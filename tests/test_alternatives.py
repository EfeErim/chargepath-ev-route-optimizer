import pytest

from chargepath import (
    CompetitiveRoutePlanner,
    GreedyFixedTargetOptimizer,
    NoFeasibleRouteError,
    RoadLeg,
    RouteOptionSet,
    RouteStrategy,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.providers import StaticRoadNetwork


def make_vehicle(*, initial_soc_pct: float = 60) -> VehicleProfile:
    return VehicleProfile(
        name="Options EV",
        usable_battery_kwh=60,
        initial_soc_pct=initial_soc_pct,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
        reserve_soc_pct=10,
        energy_safety_factor=1.0,
    )


def plan(
    legs: list[RoadLeg], *, vehicle: VehicleProfile, stations: dict[str, Station] | None = None
) -> RouteOptionSet:
    return CompetitiveRoutePlanner().plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations or {},
        road_network=StaticRoadNetwork(legs),
    )


def test_competitive_planner_returns_distinct_verified_deterministic_options() -> None:
    stations = {
        "fast_1": Station("fast_1", "Fast 1", 39.0, 31.0, 250),
        "fast_2": Station("fast_2", "Fast 2", 39.1, 31.1, 250),
        "short_1": Station("short_1", "Short 1", 39.2, 31.2, 50),
        "short_2": Station("short_2", "Short 2", 39.3, 31.3, 50),
        "one_stop": Station("one_stop", "One Stop", 39.4, 31.4, 100),
    }
    legs = [
        RoadLeg("origin", "fast_1", 150, 35),
        RoadLeg("fast_1", "fast_2", 150, 35),
        RoadLeg("fast_2", "destination", 150, 35),
        RoadLeg("origin", "short_1", 80, 80),
        RoadLeg("short_1", "short_2", 80, 80),
        RoadLeg("short_2", "destination", 80, 80),
        RoadLeg("origin", "one_stop", 140, 100),
        RoadLeg("one_stop", "destination", 140, 100),
    ]
    vehicle = make_vehicle()
    result = plan(legs, vehicle=vehicle, stations=stations)

    by_strategy = {s: option.plan.node_ids for option in result.options for s in option.strategies}
    assert by_strategy[RouteStrategy.FASTEST] == ("origin", "fast_1", "fast_2", "destination")
    assert by_strategy[RouteStrategy.SHORTEST_DISTANCE] == (
        "origin",
        "short_1",
        "short_2",
        "destination",
    )
    assert by_strategy[RouteStrategy.FEWEST_CHARGING_STOPS] == ("origin", "one_stop", "destination")
    assert set(by_strategy) == set(RouteStrategy)
    assert len(result.options) == 4
    for option in result.options:
        verify_route_plan(option.plan, vehicle=vehicle, stations=stations)
    assert plan(legs, vehicle=vehicle, stations=stations) == result


def test_identical_actionable_plans_are_deduplicated_with_strategy_aliases() -> None:
    result = plan(
        [RoadLeg("origin", "destination", 100, 60)], vehicle=make_vehicle(initial_soc_pct=80)
    )
    assert len(result.options) == 1
    assert result.options[0].strategies == tuple(RouteStrategy)


def test_greedy_failure_is_visible_when_exact_search_finds_a_plan() -> None:
    result = plan(
        [RoadLeg("origin", "hub", 100, 60), RoadLeg("hub", "destination", 230, 120)],
        vehicle=make_vehicle(),
        stations={"hub": Station("hub", "Hub", 39, 32, 150)},
    )
    assert result.unavailable_strategies == (RouteStrategy.GREEDY_FIXED_80,)
    assert len(result.options) == 1
    assert result.options[0].strategies == (
        RouteStrategy.FASTEST,
        RouteStrategy.SHORTEST_DISTANCE,
        RouteStrategy.FEWEST_CHARGING_STOPS,
    )


def test_all_infeasible_strategies_raise_explicit_error() -> None:
    with pytest.raises(NoFeasibleRouteError, match="no route-option strategy"):
        plan([RoadLeg("origin", "destination", 300, 180)], vehicle=make_vehicle())


def test_greedy_does_not_shuttle_back_to_a_visited_station() -> None:
    # Blocked at "a", greedy detours to "s"; the shortest path from "s" leads back through "a".
    # Without the unvisited-station rule it went a -> s -> a -> s, emptied the battery, and only
    # then charged at the second visit of "s".
    vehicle = VehicleProfile(
        name="Small EV",
        usable_battery_kwh=20,
        initial_soc_pct=40,
        consumption_kwh_per_100km=10,
        max_dc_power_kw=50,
        reserve_soc_pct=0,
        energy_safety_factor=1.0,
    )
    legs = [
        RoadLeg("origin", "a", 5, 4),
        RoadLeg("a", "s", 5, 4),
        RoadLeg("s", "a", 5, 4),
        RoadLeg("a", "destination", 80, 4),
    ]
    stations = {"s": Station("s", "Hub", 39, 32, 22)}
    with pytest.raises(NoFeasibleRouteError, match="greedy fixed-target strategy found no route"):
        GreedyFixedTargetOptimizer(soc_step_pct=10).optimize(
            origin_id="origin",
            destination_id="destination",
            vehicle=vehicle,
            stations=stations,
            road_network=StaticRoadNetwork(legs),
        )
    result = CompetitiveRoutePlanner(soc_step_pct=10).plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=StaticRoadNetwork(legs),
    )
    assert result.unavailable_strategies == (RouteStrategy.GREEDY_FIXED_80,)
    fastest = result.options[0].plan
    assert fastest.node_ids == ("origin", "a", "s", "a", "destination")
    assert [stop.node_index for stop in fastest.charging_stops] == [2]


def test_greedy_does_not_return_to_the_origin_to_charge() -> None:
    # Shrunk from a randomized property run. Greedy drove n0 -> n1 -> n2, was blocked, returned to
    # the origin station n0 and charged there; the replay then tied that stop to the first visit.
    legs = [
        RoadLeg(origin, destination, distance, minutes)
        for origin, destination, distance, minutes in (
            ("n0", "n1", 5.0, 4.0),
            ("n1", "n2", 5.0, 4.0),
            ("n2", "n0", 5.0, 4.0),
            ("n2", "n3", 10.1, 4.0),
            ("n2", "n4", 5.0, 8.0),
            ("n3", "n0", 5.0, 4.0),
            ("n3", "n1", 5.0, 4.0),
            ("n3", "n4", 5.0, 4.0),
            ("n4", "n5", 5.0, 4.0),
        )
    ]
    vehicle = VehicleProfile(
        name="Property EV",
        usable_battery_kwh=20.0,
        initial_soc_pct=15.0,
        consumption_kwh_per_100km=10.0,
        max_dc_power_kw=40.0,
        reserve_soc_pct=0.0,
        energy_safety_factor=1.0,
    )
    result = CompetitiveRoutePlanner(soc_step_pct=5).plan(
        origin_id="n0",
        destination_id="n5",
        vehicle=vehicle,
        stations={"n0": Station("n0", "Station n0", 39.0, 32.0, 22.0)},
        road_network=StaticRoadNetwork(legs),
    )
    assert RouteStrategy.GREEDY_FIXED_80 in result.unavailable_strategies
    assert all(
        len(set(option.plan.node_ids)) == len(option.plan.node_ids) for option in result.options
    )
