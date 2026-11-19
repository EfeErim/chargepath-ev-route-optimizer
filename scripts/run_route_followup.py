"""Evaluate fixed-path charging and station loss on the frozen real-route graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from statistics import median

from chargepath.alternatives import GreedyFixedTargetOptimizer
from chargepath.evidence import canonical_json_bytes, nearest_rank
from chargepath.models import NoFeasibleRouteError, RoutePlan, Station
from chargepath.optimizer import EVRouteOptimizer
from chargepath.providers.static import StaticRoadNetwork
from chargepath.real_route_collection import load_verified_graph
from chargepath.real_route_evaluation import Trip, assert_no_station_data, verified_e1_graphs
from chargepath.verification import replay_route_plan

ROOT = Path(__file__).resolve().parents[1]
V1_GRAPHS = ROOT / "data/processed/real-routes/v1/graphs"


def _replayed(
    plan: RoutePlan, trip: Trip, stations: dict[str, Station], violations: list[int]
) -> RoutePlan:
    replay = replay_route_plan(
        plan, vehicle=trip.vehicle, stations=stations, soc_step_pct=trip.soc_step_pct
    )
    violations.append(replay.reserve_violations)
    return plan


def optimize_fixed_path(
    trip: Trip, greedy: RoutePlan, stations: dict[str, Station], violations: list[int]
) -> RoutePlan:
    # Label each visit separately so a repeated station cannot introduce a shortcut.
    visits = tuple(f"visit-{index}" for index in range(len(greedy.node_ids)))
    fixed_stations = {
        visits[stop.node_index]: replace(stations[stop.station_id], id=visits[stop.node_index])
        for stop in greedy.charging_stops
    }
    legs = [
        replace(leg, origin_id=visits[index], destination_id=visits[index + 1])
        for index, leg in enumerate(greedy.legs)
    ]
    plan = EVRouteOptimizer(soc_step_pct=trip.soc_step_pct).optimize(
        origin_id=visits[0],
        destination_id=visits[-1],
        vehicle=trip.vehicle,
        stations=fixed_stations,
        road_network=StaticRoadNetwork(legs),
    )
    return _replayed(plan, trip, fixed_stations, violations)


def exact(
    trip: Trip, stations: dict[str, Station], network: StaticRoadNetwork, violations: list[int]
) -> RoutePlan | None:
    try:
        plan = EVRouteOptimizer(soc_step_pct=trip.soc_step_pct).optimize(
            origin_id="origin",
            destination_id="destination",
            road_network=network,
            vehicle=trip.vehicle,
            stations=stations,
        )
    except NoFeasibleRouteError:
        return None
    return _replayed(plan, trip, stations, violations)


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_bytes = (ROOT / "data/benchmarks/real-routes/v1/manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    reference = json.loads((ROOT / "docs/benchmarks/real-routes/summary_v1.json").read_bytes())

    rows = []
    timings = []
    violations: list[int] = []
    all_stations: dict[str, Station] = {}
    for trip, graph in verified_e1_graphs(manifest, graphs_dir=V1_GRAPHS, summary=reference):
        stations = dict(graph.stations)
        all_stations.update(stations)
        network = StaticRoadNetwork(graph.legs)
        start = time.perf_counter()
        nominal = exact(trip, stations, network, violations)
        timings.append((time.perf_counter() - start) * 1000)
        if nominal is None:
            raise ValueError(f"expected feasible E1 reference for {trip.route_id}")
        try:
            greedy = GreedyFixedTargetOptimizer(soc_step_pct=trip.soc_step_pct).optimize(
                origin_id="origin",
                destination_id="destination",
                road_network=network,
                vehicle=trip.vehicle,
                stations=stations,
            )
        except NoFeasibleRouteError:
            greedy = None
        row = {"route": trip.route_id, "profile": trip.group, "nominal_min": nominal.total_minutes}
        if greedy is not None:
            _replayed(greedy, trip, stations, violations)
            fixed = optimize_fixed_path(trip, greedy, stations, violations)
            if (
                not nominal.total_minutes - 1e-8
                <= fixed.total_minutes
                <= greedy.total_minutes + 1e-8
            ):
                raise ValueError("fixed-path charge optimization broke the comparison ordering")
            row.update(greedy_min=greedy.total_minutes, fixed_path_min=fixed.total_minutes)
        low = replace(trip, vehicle=replace(trip.vehicle, initial_soc_pct=30.0))
        low_plan = exact(low, stations, network, violations)
        row["low_soc_min"] = None if low_plan is None else low_plan.total_minutes
        sparse_name = replace(trip.graph, candidate_cap=12).file_name
        sparse = load_verified_graph(
            V1_GRAPHS / sparse_name,
            expected_sha256=reference["stored_graphs"][sparse_name]["sha256"],
        )
        sparse_plan = exact(trip, dict(sparse.stations), StaticRoadNetwork(sparse.legs), violations)
        row["sparse_candidates_min"] = None if sparse_plan is None else sparse_plan.total_minutes
        # Keep road legs through the unavailable site and remove its charging action.
        if nominal.charging_stops:
            unavailable = nominal.charging_stops[0].station_id
            outage = exact(
                trip,
                {key: site for key, site in stations.items() if key != unavailable},
                network,
                violations,
            )
            row["first_stop_outage_min"] = None if outage is None else outage.total_minutes
        rows.append(row)

    evidence = {
        "input_benchmark": manifest["benchmark_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "initial_soc_stress_pct": 30,
        "sparse_candidate_cap": 12,
        "replayed_plans": len(violations),
        "reserve_violations_on_returned_plans": sum(violations),
        "scenarios": rows,
    }
    if sum(violations):
        raise ValueError("a returned plan violates the reserve under replay")
    assert_no_station_data(evidence, all_stations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence))
    p95 = nearest_rank(timings, 0.95)
    print(f"Scenarios: {len(rows)}. Exact + replay median/p95: {median(timings):.1f}/{p95:.1f} ms")


if __name__ == "__main__":
    run()
