"""Hand-checked optimizer cases. Random graphs against a reference solver are in test_properties."""

from dataclasses import dataclass, replace

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
from chargepath.energy import ConstantConsumption, ConsumptionModel, LegEnergy
from chargepath.providers import StaticRoadNetwork


def make_vehicle(*, initial_soc_pct: float = 60, reserve_soc_pct: float = 10) -> VehicleProfile:
    return VehicleProfile(
        name="Test EV",
        usable_battery_kwh=60,
        initial_soc_pct=initial_soc_pct,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
        reserve_soc_pct=reserve_soc_pct,
        energy_safety_factor=1.0,
    )


def optimize(
    legs: list[RoadLeg],
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station] | None = None,
    origin_id: str = "a",
    destination_id: str = "b",
    preference: RoutePreference = RoutePreference.FASTEST,
    consumption_model: ConsumptionModel | None = None,
) -> RoutePlan:
    """Plan, then return the plan only after the independent replay accepts it."""
    stations = stations or {}
    network = StaticRoadNetwork(legs)
    plan = EVRouteOptimizer(
        preference=preference, consumption_model=consumption_model or ConstantConsumption()
    ).optimize(
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
        consumption_model=consumption_model or ConstantConsumption(),
    )
    return plan


def test_direct_feasible_route_needs_no_charging() -> None:
    plan = optimize([RoadLeg("a", "b", 100, 60)], vehicle=make_vehicle(initial_soc_pct=80))
    assert plan.node_ids == ("a", "b")
    assert plan.charging_stops == ()
    assert plan.arrival_soc_pct == 45


def test_optimizer_selects_faster_charging_corridor() -> None:
    stations = {
        "fast": Station("fast", "Fast", 39, 32, 150),
        "slow": Station("slow", "Slow", 39, 31, 50),
    }
    legs = [
        RoadLeg("a", "b", 300, 180),
        RoadLeg("a", "fast", 120, 75),
        RoadLeg("fast", "b", 190, 115),
        RoadLeg("a", "slow", 100, 65),
        RoadLeg("slow", "b", 210, 130),
    ]
    plan = optimize(legs, vehicle=make_vehicle(), stations=stations)
    assert plan.node_ids == ("a", "fast", "b")
    assert [stop.station_id for stop in plan.charging_stops] == ["fast"]


def test_multi_stop_plan_replays() -> None:
    stations = {
        "first": Station("first", "First", 39, 32, 150),
        "second": Station("second", "Second", 40, 33, 150),
    }
    legs = [
        RoadLeg("a", "first", 140, 80),
        RoadLeg("first", "second", 140, 80),
        RoadLeg("second", "b", 140, 80),
    ]
    plan = optimize(legs, vehicle=make_vehicle(), stations=stations)
    assert [stop.station_id for stop in plan.charging_stops] == ["first", "second"]


def test_zero_length_trip_returns_conservative_initial_soc() -> None:
    plan = optimize([], vehicle=make_vehicle(initial_soc_pct=63), destination_id="a")
    assert plan.node_ids == ("a",)
    assert plan.total_minutes == 0
    assert plan.arrival_soc_pct == 60


def test_exact_reserve_boundary_is_feasible_and_a_non_aligned_reserve_rounds_up() -> None:
    legs = [RoadLeg("a", "b", 150, 90)]
    assert optimize(legs, vehicle=make_vehicle()).arrival_soc_pct == 10
    with pytest.raises(NoFeasibleRouteError):
        optimize(legs, vehicle=make_vehicle(reserve_soc_pct=12))


@pytest.mark.parametrize(
    ("legs", "stations"),
    [
        ([RoadLeg("a", "b", 300, 180)], {}),
        (
            [RoadLeg("a", "hub", 100, 60), RoadLeg("hub", "b", 100, 60)],
            {"hub": Station("hub", "Hub", 39, 32, 100, "CHADEMO")},
        ),
    ],
    ids=["no-charger", "incompatible-charger"],
)
def test_infeasible_trip_raises_instead_of_a_partial_plan(
    legs: list[RoadLeg], stations: dict[str, Station]
) -> None:
    with pytest.raises(NoFeasibleRouteError, match="no energy-feasible route"):
        optimize(legs, vehicle=make_vehicle(), stations=stations)


def test_infeasibility_names_a_start_that_rounds_below_the_reserve() -> None:
    legs = [RoadLeg("a", "b", 1, 1)]
    with pytest.raises(NoFeasibleRouteError, match="rounds down to 10%, below the 15% reserve"):
        optimize(legs, vehicle=make_vehicle(initial_soc_pct=12, reserve_soc_pct=11))


def test_fastest_breaks_time_ties_on_charging_stops_before_distance() -> None:
    @dataclass(frozen=True, slots=True)
    class EfficientDirectLeg:
        def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
            if (leg.origin_id, leg.destination_id) == ("a", "b"):
                return LegEnergy(net_kwh=6.0, peak_kwh=6.0)
            return ConstantConsumption().leg_energy(leg, vehicle)

    # Both routes take exactly 100 minutes. The direct one is longer but needs no charging;
    # the one via s is shorter and needs 3 buckets (9 kWh at 180 kW = 3 min, plus 2 min setup).
    plan = optimize(
        [RoadLeg("a", "b", 200, 100), RoadLeg("a", "s", 30, 40), RoadLeg("s", "b", 75, 55)],
        vehicle=replace(make_vehicle(initial_soc_pct=30), max_dc_power_kw=180),
        stations={"s": Station("s", "Hub", 39.0, 32.0, 180)},
        consumption_model=EfficientDirectLeg(),
    )
    assert plan.total_minutes == 100
    assert plan.node_ids == ("a", "b")


@pytest.mark.parametrize("preference", list(RoutePreference))
def test_full_ties_prefer_the_higher_arrival_soc(preference: RoutePreference) -> None:
    @dataclass(frozen=True, slots=True)
    class CheaperViaC:
        def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
            if leg.origin_id == "c":
                return LegEnergy(net_kwh=3.0, peak_kwh=3.0)
            return ConstantConsumption().leg_energy(leg, vehicle)

    # Both paths take 100 minutes over 100 km with no charging; the one via c uses less energy.
    legs = [
        RoadLeg("a", "c", 50, 50),
        RoadLeg("c", "b", 50, 50),
        RoadLeg("a", "d", 50, 50),
        RoadLeg("d", "b", 50, 50),
    ]
    plan = optimize(
        legs,
        vehicle=make_vehicle(initial_soc_pct=80),
        preference=preference,
        consumption_model=CheaperViaC(),
    )
    assert plan.node_ids == ("a", "c", "b")
    assert plan.arrival_soc_pct == 55
