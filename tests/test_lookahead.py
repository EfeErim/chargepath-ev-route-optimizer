import random
from pathlib import Path

import pytest

from chargepath import (
    EVRouteOptimizer,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
    load_synthetic_scenario,
    verify_route_plan,
)
from chargepath.lookahead import LookaheadGreedyOptimizer
from chargepath.providers import StaticRoadNetwork


def make_vehicle(*, initial_soc_pct: float = 30) -> VehicleProfile:
    # 60 kWh at 20 kWh/100 km: one 5% bucket is 3 kWh, or 15 km.
    return VehicleProfile(
        name="Test EV",
        usable_battery_kwh=60,
        initial_soc_pct=initial_soc_pct,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
        reserve_soc_pct=10,
        energy_safety_factor=1.0,
    )


def plan_and_verify(
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    legs: list[RoadLeg],
    origin_id: str = "a",
    destination_id: str = "d",
) -> RoutePlan:
    network = StaticRoadNetwork(legs)
    plan = LookaheadGreedyOptimizer().optimize(
        origin_id=origin_id,
        destination_id=destination_id,
        vehicle=vehicle,
        stations=stations,
        road_network=network,
    )
    verify_route_plan(
        plan,
        vehicle=vehicle,
        stations=stations,
        road_network=network,
        origin_id=origin_id,
        destination_id=destination_id,
    )
    return plan


def test_direct_trip_needs_no_charging() -> None:
    plan = plan_and_verify(
        vehicle=make_vehicle(initial_soc_pct=80), stations={}, legs=[RoadLeg("a", "d", 90, 60)]
    )
    assert plan.node_ids == ("a", "d")
    assert plan.charging_stops == ()
    assert plan.arrival_soc_pct == 50


def test_charges_only_as_much_as_the_rest_of_the_trip_needs() -> None:
    stations = {"s": Station("s", "Hub", 39, 32, 150)}
    legs = [RoadLeg("a", "s", 60, 40), RoadLeg("s", "d", 150, 100), RoadLeg("a", "d", 210, 140)]
    plan = plan_and_verify(vehicle=make_vehicle(), stations=stations, legs=legs)
    assert plan.node_ids == ("a", "s", "d")
    (stop,) = plan.charging_stops
    # Arrive with the 10% reserve and leave with exactly 10 buckets more; fixed-80 would go to 80%.
    assert (stop.arrival_soc_pct, stop.departure_soc_pct) == (10, 60)
    assert plan.arrival_soc_pct == 10


def test_prefers_the_faster_charger_when_roads_are_equal() -> None:
    stations = {
        "fast": Station("fast", "Fast", 39, 32, 150),
        "slow": Station("slow", "Slow", 39, 31, 50),
    }
    legs = [
        RoadLeg("a", "fast", 60, 40),
        RoadLeg("a", "slow", 60, 40),
        RoadLeg("fast", "d", 150, 100),
        RoadLeg("slow", "d", 150, 100),
    ]
    plan = plan_and_verify(vehicle=make_vehicle(), stations=stations, legs=legs)
    assert plan.node_ids == ("a", "fast", "d")


def test_reports_failure_instead_of_a_partial_plan() -> None:
    with pytest.raises(NoFeasibleRouteError):
        plan_and_verify(vehicle=make_vehicle(), stations={}, legs=[RoadLeg("a", "d", 300, 200)])


def test_never_moves_away_from_the_destination() -> None:
    # The only station is farther from the destination than the origin, so it is never a move.
    stations = {"back": Station("back", "Behind", 39, 30, 150)}
    legs = [
        RoadLeg("a", "back", 15, 10),
        RoadLeg("back", "d", 300, 200),
        RoadLeg("a", "d", 210, 140),
    ]
    with pytest.raises(NoFeasibleRouteError):
        plan_and_verify(vehicle=make_vehicle(), stations=stations, legs=legs)


def test_synthetic_fixture_plan_replays_and_is_deterministic() -> None:
    scenario = load_synthetic_scenario(
        Path(__file__).resolve().parents[1] / "data/sample/synthetic_corridor.json"
    )
    stations = scenario.station_map()
    plans = [
        LookaheadGreedyOptimizer().optimize(
            origin_id=scenario.origin_id,
            destination_id=scenario.destination_id,
            vehicle=scenario.vehicle,
            stations=stations,
            road_network=StaticRoadNetwork(scenario.legs),
        )
        for _ in range(2)
    ]
    assert plans[0] == plans[1]
    verify_route_plan(plans[0], vehicle=scenario.vehicle, stations=stations)


def test_random_graphs_replay_and_are_never_faster_than_exact() -> None:
    rng = random.Random(20261116)
    compared = 0
    for _ in range(300):
        names = ["a", "d", *(f"s{index}" for index in range(rng.randint(1, 5)))]
        stations = {
            name: Station(name, name, 39, 32, rng.choice([50, 100, 150])) for name in names[2:]
        }
        legs = [
            RoadLeg(origin, destination, km := rng.randint(20, 200), km * rng.uniform(0.5, 1.2))
            for origin in names
            for destination in names
            if origin != destination and origin != "d" and rng.random() < 0.8
        ]
        vehicle = make_vehicle(initial_soc_pct=rng.choice([30, 50, 80]))
        network = StaticRoadNetwork(legs)
        try:
            exact = EVRouteOptimizer().optimize(
                origin_id="a",
                destination_id="d",
                vehicle=vehicle,
                stations=stations,
                road_network=network,
            )
        except NoFeasibleRouteError:
            exact = None
        try:
            heuristic = plan_and_verify(vehicle=vehicle, stations=stations, legs=legs)
        except NoFeasibleRouteError:
            continue
        assert exact is not None, "the heuristic cannot solve a trip exact search cannot"
        assert heuristic.total_minutes >= exact.total_minutes - 1e-9
        compared += 1
    assert compared > 50
