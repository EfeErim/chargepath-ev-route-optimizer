from __future__ import annotations

import pytest

from chargepath import EVRouteOptimizer, RoadLeg, Station, VehicleProfile
from chargepath.demo import FixturePlanningService
from chargepath.demo.schema import PlanRequest
from chargepath.demo.services import DEFAULT_TILE_URL
from chargepath.fixtures import load_synthetic_scenario
from chargepath.providers import StaticRoadNetwork
from chargepath.redundancy import stop_redundancy

VEHICLE = VehicleProfile(
    name="Redundancy EV",
    usable_battery_kwh=60,
    initial_soc_pct=50,
    consumption_kwh_per_100km=20,
    max_dc_power_kw=150,
    reserve_soc_pct=10,
    energy_safety_factor=1.0,
)


def _two_hub_graph() -> tuple[dict[str, Station], StaticRoadNetwork]:
    stations = {
        "fast": Station("fast", "Fast hub", 39, 32, 150),
        "slow": Station("slow", "Slow hub", 39, 31, 50),
        "far": Station("far", "Out of reach", 39, 30, 350),
    }
    network = StaticRoadNetwork(
        [
            RoadLeg("origin", "fast", 100, 60),
            RoadLeg("origin", "slow", 100, 65),
            RoadLeg("origin", "far", 400, 240),
            RoadLeg("fast", "destination", 200, 120),
            RoadLeg("slow", "destination", 200, 125),
            RoadLeg("far", "destination", 50, 30),
        ]
    )
    return stations, network


def test_counts_reachable_alternatives_and_the_cost_of_losing_a_stop() -> None:
    stations, network = _two_hub_graph()
    plan = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=VEHICLE,
        stations=stations,
        road_network=network,
    )
    assert [stop.station_id for stop in plan.charging_stops] == ["fast"]
    (result,) = stop_redundancy(plan, vehicle=VEHICLE, stations=stations, road_network=network)
    assert result.station_id == "fast"
    # "slow" is reachable and finishes the trip; "far" is out of reach from origin.
    assert result.alternatives_checked == 2
    assert result.alternative_count == 1
    assert result.without_stop_added_minutes is not None
    assert result.without_stop_added_minutes > 0


def test_a_single_charger_has_no_structural_fallback() -> None:
    stations = {"only": Station("only", "Only hub", 39, 32, 150)}
    network = StaticRoadNetwork(
        [RoadLeg("origin", "only", 100, 60), RoadLeg("only", "destination", 200, 120)]
    )
    plan = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=VEHICLE,
        stations=stations,
        road_network=network,
    )
    (result,) = stop_redundancy(plan, vehicle=VEHICLE, stations=stations, road_network=network)
    assert result.alternative_count == 0
    assert result.alternatives_checked == 0
    assert result.without_stop_added_minutes is None


def test_alternative_limit_bounds_the_work() -> None:
    stations, network = _two_hub_graph()
    plan = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=VEHICLE,
        stations=stations,
        road_network=network,
    )
    (result,) = stop_redundancy(
        plan, vehicle=VEHICLE, stations=stations, road_network=network, alternative_limit=1
    )
    assert result.alternatives_checked == 1
    with pytest.raises(ValueError, match="alternative_limit"):
        stop_redundancy(
            plan, vehicle=VEHICLE, stations=stations, road_network=network, alternative_limit=-1
        )


def test_demo_reports_redundancy_for_the_fastest_option_only() -> None:
    service = FixturePlanningService(
        load_synthetic_scenario("data/sample/synthetic_corridor.json"), tile_url=DEFAULT_TILE_URL
    )
    config = service.public_config()
    endpoints = config["endpoints"]
    assert isinstance(endpoints, list)
    result = service.plan(
        PlanRequest.from_payload(
            {"origin": endpoints[0], "destination": endpoints[1], "vehicle": config["vehicle"]}
        )
    )
    options = result["options"]
    assert isinstance(options, list)
    for option in options:
        for stop in option["charging_stops"]:
            if "fastest" in option["strategies"]:
                assert stop["redundancy"] is not None
                assert set(stop["redundancy"]) == {
                    "alternative_count",
                    "alternatives_checked",
                    "without_stop_added_minutes",
                }
            else:
                assert stop["redundancy"] is None
