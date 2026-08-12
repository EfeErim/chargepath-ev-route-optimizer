import math
import random
from dataclasses import replace
from typing import Any, cast

import pytest

from chargepath import (
    EVRouteOptimizer,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    RoutePreference,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.energy import charging_minutes, energy_for_distance, required_energy_buckets
from chargepath.providers import StaticRoadNetwork
from chargepath.providers.base import RoadNetwork


def make_vehicle(*, initial_soc_pct: float = 60) -> VehicleProfile:
    return VehicleProfile(
        name="Test EV",
        usable_battery_kwh=60,
        initial_soc_pct=initial_soc_pct,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
        reserve_soc_pct=10,
        energy_safety_factor=1.0,
    )


def reference_priority(
    metrics: tuple[float, float, int], preference: RoutePreference
) -> tuple[float, float, float]:
    minutes, distance, stops = metrics
    if preference is RoutePreference.FASTEST:
        return minutes, float(stops), distance
    if preference is RoutePreference.SHORTEST_DISTANCE:
        return distance, minutes, float(stops)
    return float(stops), minutes, distance


def reference_optimum(
    *,
    origin_id: str,
    destination_id: str,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    network: RoadNetwork,
    preference: RoutePreference,
    soc_step_pct: int,
) -> tuple[float, float, float] | None:
    """Small-graph Bellman-Ford reference, independent of optimizer queue/predecessors."""

    initial_bucket = math.floor(vehicle.initial_soc_pct / soc_step_pct)
    reserve_bucket = math.ceil(vehicle.reserve_soc_pct / soc_step_pct)
    maximum_bucket = 100 // soc_step_pct
    bucket_kwh = vehicle.usable_battery_kwh * soc_step_pct / 100
    best: dict[tuple[str, int], tuple[float, float, int]] = {
        (origin_id, initial_bucket): (0.0, 0.0, 0)
    }

    changed = True
    while changed:
        changed = False
        for (node_id, energy_bucket), metrics in tuple(best.items()):
            minutes, distance, stops = metrics
            transitions: list[tuple[tuple[str, int], tuple[float, float, int]]] = []
            for leg in network.neighbors(node_id):
                required_kwh = energy_for_distance(
                    leg.distance_km,
                    vehicle.consumption_kwh_per_100km,
                    vehicle.energy_safety_factor,
                )
                remaining = energy_bucket - required_energy_buckets(required_kwh, bucket_kwh)
                if remaining >= reserve_bucket:
                    transitions.append(
                        (
                            (leg.destination_id, remaining),
                            (
                                minutes + leg.duration_minutes,
                                distance + leg.distance_km,
                                stops,
                            ),
                        )
                    )
            station = stations.get(node_id)
            if station is not None and station.connector_type in vehicle.supported_dc_connectors:
                for target_bucket in range(energy_bucket + 1, maximum_bucket + 1):
                    charge_time = charging_minutes(
                        vehicle,
                        station.max_power_kw,
                        energy_bucket * soc_step_pct,
                        target_bucket * soc_step_pct,
                    )
                    transitions.append(
                        (
                            (node_id, target_bucket),
                            (minutes + charge_time, distance, stops + 1),
                        )
                    )
            for state, candidate in transitions:
                current = best.get(state)
                if current is not None and reference_priority(
                    candidate, preference
                ) >= reference_priority(current, preference):
                    continue
                best[state] = candidate
                changed = True

    destinations = [
        reference_priority(metrics, preference)
        for (node_id, _), metrics in best.items()
        if node_id == destination_id
    ]
    return min(destinations) if destinations else None


def optimize_and_verify(
    *,
    origin_id: str,
    destination_id: str,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    road_network: RoadNetwork,
) -> RoutePlan:
    """Return a plan only after the independent verifier accepts it."""

    plan = EVRouteOptimizer().optimize(
        origin_id=origin_id,
        destination_id=destination_id,
        vehicle=vehicle,
        stations=stations,
        road_network=road_network,
    )
    verify_route_plan(plan, vehicle=vehicle, stations=stations)
    return plan


def test_direct_feasible_route_needs_no_charging() -> None:
    network = StaticRoadNetwork([RoadLeg("a", "b", 100, 60)])
    vehicle = make_vehicle(initial_soc_pct=80)
    plan = optimize_and_verify(
        origin_id="a",
        destination_id="b",
        vehicle=vehicle,
        stations={},
        road_network=network,
    )
    assert plan.node_ids == ("a", "b")
    assert plan.charging_stops == ()
    assert plan.arrival_soc_pct == 45


def test_optimizer_selects_faster_charging_corridor() -> None:
    stations = {
        "fast": Station("fast", "Fast", 39, 32, 150),
        "slow": Station("slow", "Slow", 39, 31, 50),
    }
    network = StaticRoadNetwork(
        [
            RoadLeg("origin", "destination", 300, 180),
            RoadLeg("origin", "fast", 120, 75),
            RoadLeg("fast", "destination", 190, 115),
            RoadLeg("origin", "slow", 100, 65),
            RoadLeg("slow", "destination", 210, 130),
        ]
    )
    vehicle = make_vehicle()
    plan = optimize_and_verify(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=network,
    )
    assert plan.node_ids == ("origin", "fast", "destination")
    assert [stop.station_id for stop in plan.charging_stops] == ["fast"]
    assert plan.arrival_soc_pct >= 10


def test_infeasible_route_raises_explicit_error() -> None:
    network = StaticRoadNetwork([RoadLeg("a", "b", 300, 180)])
    with pytest.raises(NoFeasibleRouteError, match="no energy-feasible route"):
        EVRouteOptimizer().optimize(
            origin_id="a",
            destination_id="b",
            vehicle=make_vehicle(),
            stations={},
            road_network=network,
        )


def test_plan_is_deterministic() -> None:
    network = StaticRoadNetwork([RoadLeg("a", "c", 100, 60), RoadLeg("a", "b", 100, 60)])
    vehicle = make_vehicle(initial_soc_pct=80)
    first = optimize_and_verify(
        origin_id="a",
        destination_id="b",
        vehicle=vehicle,
        stations={},
        road_network=network,
    )
    second = optimize_and_verify(
        origin_id="a",
        destination_id="b",
        vehicle=vehicle,
        stations={},
        road_network=network,
    )
    assert first == second


def test_zero_length_trip_returns_conservative_initial_soc() -> None:
    vehicle = make_vehicle(initial_soc_pct=63)
    plan = optimize_and_verify(
        origin_id="a",
        destination_id="a",
        vehicle=vehicle,
        stations={},
        road_network=StaticRoadNetwork([]),
    )
    assert plan.node_ids == ("a",)
    assert plan.total_minutes == 0
    assert plan.arrival_soc_pct == 60


def test_exact_reserve_boundary_is_feasible() -> None:
    network = StaticRoadNetwork([RoadLeg("a", "b", 150, 90)])
    vehicle = make_vehicle(initial_soc_pct=60)
    plan = optimize_and_verify(
        origin_id="a",
        destination_id="b",
        vehicle=vehicle,
        stations={},
        road_network=network,
    )
    assert plan.arrival_soc_pct == 10


def test_non_aligned_reserve_is_rounded_up_conservatively() -> None:
    vehicle = replace(make_vehicle(initial_soc_pct=60), reserve_soc_pct=12)
    network = StaticRoadNetwork([RoadLeg("a", "b", 150, 90)])
    with pytest.raises(NoFeasibleRouteError):
        EVRouteOptimizer().optimize(
            origin_id="a",
            destination_id="b",
            vehicle=vehicle,
            stations={},
            road_network=network,
        )


def test_incompatible_charger_is_not_used() -> None:
    network = StaticRoadNetwork(
        [RoadLeg("origin", "hub", 100, 60), RoadLeg("hub", "destination", 100, 60)]
    )
    stations = {"hub": Station("hub", "Hub", 39, 32, 100, "CHADEMO")}
    with pytest.raises(NoFeasibleRouteError):
        EVRouteOptimizer().optimize(
            origin_id="origin",
            destination_id="destination",
            vehicle=make_vehicle(),
            stations=stations,
            road_network=network,
        )


def test_multi_stop_plan_replays_through_independent_verifier() -> None:
    vehicle = make_vehicle()
    stations = {
        "first": Station("first", "First", 39, 32, 150),
        "second": Station("second", "Second", 40, 33, 150),
    }
    network = StaticRoadNetwork(
        [
            RoadLeg("origin", "first", 140, 80),
            RoadLeg("first", "second", 140, 80),
            RoadLeg("second", "destination", 140, 80),
        ]
    )
    plan = optimize_and_verify(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=network,
    )
    assert [stop.station_id for stop in plan.charging_stops] == ["first", "second"]


def test_verifier_rejects_tampered_arrival_soc() -> None:
    vehicle = make_vehicle(initial_soc_pct=80)
    plan = optimize_and_verify(
        origin_id="a",
        destination_id="b",
        vehicle=vehicle,
        stations={},
        road_network=StaticRoadNetwork([RoadLeg("a", "b", 100, 60)]),
    )
    with pytest.raises(ValueError, match="arrival SOC mismatch"):
        verify_route_plan(replace(plan, arrival_soc_pct=50), vehicle=vehicle, stations={})


def test_route_plan_rejects_inconsistent_total() -> None:
    vehicle = make_vehicle(initial_soc_pct=80)
    plan = optimize_and_verify(
        origin_id="a",
        destination_id="b",
        vehicle=vehicle,
        stations={},
        road_network=StaticRoadNetwork([RoadLeg("a", "b", 100, 60)]),
    )
    with pytest.raises(ValueError, match="total_minutes must equal"):
        replace(plan, total_minutes=plan.total_minutes + 1)


def test_optimizer_rejects_invalid_provider_neighbor_contract() -> None:
    class InvalidRoadNetwork:
        def neighbors(self, node_id: str) -> tuple[RoadLeg, ...]:
            return (RoadLeg("wrong", "b", 10, 10),)

    with pytest.raises(ValueError, match="must start at the requested node"):
        EVRouteOptimizer().optimize(
            origin_id="a",
            destination_id="b",
            vehicle=make_vehicle(),
            stations={},
            road_network=InvalidRoadNetwork(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("soc_step_pct", True),
        ("taper_start_pct", math.nan),
        ("taper_factor", math.inf),
        ("session_setup_minutes", math.nan),
    ],
)
def test_optimizer_rejects_nonfinite_or_boolean_configuration(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        EVRouteOptimizer(**cast(Any, {field: value}))


def test_seeded_small_graph_fuzz_is_deterministic_and_replay_safe() -> None:
    rng = random.Random(20260810)
    multi_stop_count = 0
    for case_index in range(30):
        nodes = tuple(f"n{index}" for index in range(6))
        legs: list[RoadLeg] = []
        for origin_index in range(len(nodes) - 1):
            for destination_index in range(origin_index + 1, len(nodes)):
                if destination_index != origin_index + 1 and rng.random() >= 0.4:
                    continue
                distance = rng.randint(55, 155)
                legs.append(
                    RoadLeg(
                        nodes[origin_index],
                        nodes[destination_index],
                        distance,
                        rng.randint(35, 120),
                    )
                )
        stations = {
            node_id: Station(
                node_id,
                f"Case {case_index} {node_id}",
                39 + index * 0.1,
                29 + index * 0.1,
                rng.choice((50, 100, 150)),
            )
            for index, node_id in enumerate(nodes[1:-1], start=1)
        }
        network = StaticRoadNetwork(legs)
        vehicle = make_vehicle(initial_soc_pct=60)
        for preference in RoutePreference:
            expected = reference_optimum(
                origin_id=nodes[0],
                destination_id=nodes[-1],
                vehicle=vehicle,
                stations=stations,
                network=network,
                preference=preference,
                soc_step_pct=5,
            )
            optimizer = EVRouteOptimizer(preference=preference, soc_step_pct=5)
            try:
                first = optimizer.optimize(
                    origin_id=nodes[0],
                    destination_id=nodes[-1],
                    vehicle=vehicle,
                    stations=stations,
                    road_network=network,
                )
            except NoFeasibleRouteError:
                assert expected is None
                with pytest.raises(NoFeasibleRouteError):
                    optimizer.optimize(
                        origin_id=nodes[0],
                        destination_id=nodes[-1],
                        vehicle=vehicle,
                        stations=stations,
                        road_network=network,
                    )
                continue
            second = optimizer.optimize(
                origin_id=nodes[0],
                destination_id=nodes[-1],
                vehicle=vehicle,
                stations=stations,
                road_network=network,
            )
            assert second == first
            verify_route_plan(first, vehicle=vehicle, stations=stations)
            actual = reference_priority(
                (first.total_minutes, first.total_distance_km, len(first.charging_stops)),
                preference,
            )
            assert expected is not None
            assert actual == pytest.approx(expected)
            multi_stop_count += len(first.charging_stops) >= 2

    assert multi_stop_count >= 10
