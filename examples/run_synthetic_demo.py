"""Run a deterministic example without external APIs or live data."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from chargepath import (  # noqa: E402
    CompetitiveRoutePlanner,
    load_synthetic_scenario,
    verify_route_plan,
)
from chargepath.providers import StaticRoadNetwork  # noqa: E402


def main() -> None:
    scenario = load_synthetic_scenario(REPOSITORY_ROOT / "data/sample/synthetic_corridor.json")
    stations = scenario.station_map()
    network = StaticRoadNetwork(scenario.legs)
    result = CompetitiveRoutePlanner(soc_step_pct=5).plan(
        origin_id=scenario.origin_id,
        destination_id=scenario.destination_id,
        vehicle=scenario.vehicle,
        stations=stations,
        road_network=network,
    )

    print("ChargePath synthetic route options")
    for index, option in enumerate(result.options, start=1):
        plan = option.plan
        verify_route_plan(plan, vehicle=scenario.vehicle, stations=stations)
        strategies = ", ".join(strategy.value for strategy in option.strategies)
        print(f"\nOption {index}: {strategies}")
        print(f"Nodes: {' -> '.join(plan.node_ids)}")
        for stop in plan.charging_stops:
            print(
                f"Charge at {stations[stop.station_id].name}: "
                f"{stop.arrival_soc_pct:.0f}% -> {stop.departure_soc_pct:.0f}% "
                f"({stop.charging_minutes:.1f} min)"
            )
        print(f"Distance: {plan.total_distance_km:.1f} km")
        print(f"Driving: {plan.driving_minutes:.1f} min")
        print(f"Charging: {plan.charging_minutes:.1f} min")
        print(f"Total: {plan.total_minutes:.1f} min")
        print(f"Arrival SOC: {plan.arrival_soc_pct:.0f}%")

    if result.unavailable_strategies:
        unavailable = ", ".join(strategy.value for strategy in result.unavailable_strategies)
        print(f"\nUnavailable strategies: {unavailable}")


if __name__ == "__main__":
    main()
