import pytest

import chargepath.alternatives as alternatives_module
import chargepath.optimizer as optimizer_module
from chargepath import (
    CompetitiveRoutePlanner,
    NoFeasibleRouteError,
    RoadLeg,
    RouteOption,
    RouteOptionSet,
    RouteStrategy,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.energy import energy_for_distance as original_energy_for_distance
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


def make_competitive_graph() -> tuple[dict[str, Station], StaticRoadNetwork]:
    stations = {
        "fast_1": Station("fast_1", "Fast 1", 39.0, 31.0, 250),
        "fast_2": Station("fast_2", "Fast 2", 39.1, 31.1, 250),
        "short_1": Station("short_1", "Short 1", 39.2, 31.2, 50),
        "short_2": Station("short_2", "Short 2", 39.3, 31.3, 50),
        "one_stop": Station("one_stop", "One Stop", 39.4, 31.4, 100),
    }
    network = StaticRoadNetwork(
        [
            RoadLeg("origin", "fast_1", 150, 35),
            RoadLeg("fast_1", "fast_2", 150, 35),
            RoadLeg("fast_2", "destination", 150, 35),
            RoadLeg("origin", "short_1", 80, 80),
            RoadLeg("short_1", "short_2", 80, 80),
            RoadLeg("short_2", "destination", 80, 80),
            RoadLeg("origin", "one_stop", 140, 100),
            RoadLeg("one_stop", "destination", 140, 100),
        ]
    )
    return stations, network


def test_competitive_planner_returns_distinct_verified_options() -> None:
    stations, network = make_competitive_graph()
    vehicle = make_vehicle()
    result = CompetitiveRoutePlanner().plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=network,
    )

    by_strategy = {strategy: option for option in result.options for strategy in option.strategies}
    assert set(by_strategy) == set(RouteStrategy)
    assert by_strategy[RouteStrategy.FASTEST].plan.node_ids == (
        "origin",
        "fast_1",
        "fast_2",
        "destination",
    )
    assert by_strategy[RouteStrategy.SHORTEST_DISTANCE].plan.node_ids == (
        "origin",
        "short_1",
        "short_2",
        "destination",
    )
    assert by_strategy[RouteStrategy.FEWEST_CHARGING_STOPS].plan.node_ids == (
        "origin",
        "one_stop",
        "destination",
    )
    assert result.unavailable_strategies == ()
    assert len(result.options) == 4
    for option in result.options:
        verify_route_plan(option.plan, vehicle=vehicle, stations=stations)


def test_competitive_options_are_deterministic() -> None:
    stations, network = make_competitive_graph()
    planner = CompetitiveRoutePlanner()
    vehicle = make_vehicle()
    first = planner.plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=network,
    )
    second = planner.plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=network,
    )
    assert first == second


def test_identical_actionable_plans_are_deduplicated_with_strategy_aliases() -> None:
    vehicle = make_vehicle(initial_soc_pct=80)
    result = CompetitiveRoutePlanner().plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations={},
        road_network=StaticRoadNetwork([RoadLeg("origin", "destination", 100, 60)]),
    )
    assert len(result.options) == 1
    assert result.options[0].strategies == tuple(RouteStrategy)


def test_greedy_failure_is_visible_when_exact_search_finds_a_plan() -> None:
    vehicle = make_vehicle()
    stations = {"hub": Station("hub", "Hub", 39, 32, 150)}
    result = CompetitiveRoutePlanner().plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=StaticRoadNetwork(
            [
                RoadLeg("origin", "hub", 100, 60),
                RoadLeg("hub", "destination", 230, 120),
            ]
        ),
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
        CompetitiveRoutePlanner().plan(
            origin_id="origin",
            destination_id="destination",
            vehicle=make_vehicle(),
            stations={},
            road_network=StaticRoadNetwork([RoadLeg("origin", "destination", 300, 180)]),
        )


def test_route_option_set_rejects_strategy_overlap() -> None:
    vehicle = make_vehicle(initial_soc_pct=80)
    plan = (
        CompetitiveRoutePlanner()
        .plan(
            origin_id="origin",
            destination_id="destination",
            vehicle=vehicle,
            stations={},
            road_network=StaticRoadNetwork([RoadLeg("origin", "destination", 100, 60)]),
        )
        .options[0]
        .plan
    )

    with pytest.raises(ValueError, match="multiple options"):
        RouteOptionSet(
            options=(
                RouteOption((RouteStrategy.FASTEST,), plan),
                RouteOption((RouteStrategy.FASTEST,), plan),
            ),
            unavailable_strategies=(
                RouteStrategy.SHORTEST_DISTANCE,
                RouteStrategy.FEWEST_CHARGING_STOPS,
                RouteStrategy.GREEDY_FIXED_80,
            ),
        )
    with pytest.raises(ValueError, match="must be disjoint"):
        RouteOptionSet(
            options=(RouteOption((RouteStrategy.FASTEST,), plan),),
            unavailable_strategies=(RouteStrategy.FASTEST,),
        )

    with pytest.raises(ValueError, match="every route strategy"):
        RouteOptionSet(options=(RouteOption((RouteStrategy.FASTEST,), plan),))


def test_competitive_planner_rejects_invalid_configuration_at_construction() -> None:
    with pytest.raises(ValueError, match="positive divisor"):
        CompetitiveRoutePlanner(soc_step_pct=True)


def test_competitive_planner_reuses_leg_energy_across_strategies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stations, network = make_competitive_graph()
    calls = 0

    def counted_energy(*args: float) -> float:
        nonlocal calls
        calls += 1
        return original_energy_for_distance(*args)

    monkeypatch.setattr(optimizer_module, "energy_for_distance", counted_energy)
    monkeypatch.setattr(alternatives_module, "energy_for_distance", counted_energy)

    CompetitiveRoutePlanner().plan(
        origin_id="origin",
        destination_id="destination",
        vehicle=make_vehicle(),
        stations=stations,
        road_network=network,
    )

    assert 0 < calls <= 8
