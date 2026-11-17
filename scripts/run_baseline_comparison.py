"""Compare exact fastest with the lookahead heuristic on the frozen real-route E1 scenarios.

Fixed-80 greedy is rerun alongside, and both it and exact fastest must reproduce the committed
follow-up artifact scenario by scenario. Every returned plan is replayed against its road network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from chargepath.alternatives import GreedyFixedTargetOptimizer
from chargepath.evaluation import canonical_json_bytes
from chargepath.lookahead import LookaheadGreedyOptimizer
from chargepath.models import NoFeasibleRouteError, RoutePlan, Station
from chargepath.optimizer import EVRouteOptimizer
from chargepath.providers.static import StaticRoadNetwork
from chargepath.real_route_evaluation import (
    Trip,
    assert_no_station_data,
    summary_stats,
    verified_e1_graphs,
)
from chargepath.verification import verify_route_plan

ROOT = Path(__file__).resolve().parents[1]
V1_GRAPHS = ROOT / "data/processed/real-routes/v1/graphs"
FOLLOWUP = ROOT / "docs/benchmarks/real-routes/followup_v1.json"
TIE_MINUTES = 1e-6
Planner = EVRouteOptimizer | GreedyFixedTargetOptimizer | LookaheadGreedyOptimizer


def _round(value: float) -> float:
    return round(value, 3)


def _metrics(plan: RoutePlan | None) -> dict[str, float | int] | None:
    if plan is None:
        return None
    return {
        "total_minutes": _round(plan.total_minutes),
        "charging_minutes": _round(plan.charging_minutes),
        "charging_stops": len(plan.charging_stops),
        "arrival_soc_pct": plan.arrival_soc_pct,
    }


def _solve(
    planner: Planner, trip: Trip, stations: dict[str, Station], network: StaticRoadNetwork
) -> RoutePlan | None:
    try:
        plan = planner.optimize(
            origin_id="origin",
            destination_id="destination",
            road_network=network,
            vehicle=trip.vehicle,
            stations=stations,
        )
    except NoFeasibleRouteError:
        return None
    verify_route_plan(
        plan,
        vehicle=trip.vehicle,
        stations=stations,
        soc_step_pct=trip.soc_step_pct,
        road_network=network,
        origin_id="origin",
        destination_id="destination",
    )
    return plan


def _paired(rows: list[dict[str, Any]], heuristic: str) -> dict[str, Any]:
    minutes: list[float] = []
    percent: list[float] = []
    for row in rows:
        exact, other = row["exact_fastest"], row[heuristic]
        if exact is None or other is None:
            continue
        saved = other["total_minutes"] - exact["total_minutes"]
        minutes.append(saved)
        percent.append(100 * saved / other["total_minutes"])
    charging = [row[heuristic]["charging_minutes"] for row in rows if row[heuristic] is not None]
    return {
        "paired": len(minutes),
        "heuristic_failures": sum(row[heuristic] is None for row in rows),
        "ties": sum(abs(value) <= TIE_MINUTES for value in minutes),
        "saving_minutes": summary_stats(minutes),
        "saving_percent": summary_stats(percent),
        "median_heuristic_charging_minutes": _round(median(charging)),
    }


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_bytes = (ROOT / "data/benchmarks/real-routes/v1/manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    reference = json.loads((ROOT / "docs/benchmarks/real-routes/summary_v1.json").read_bytes())
    followup = json.loads(FOLLOWUP.read_bytes())
    published = {(row["route"], row["profile"]): row for row in followup["scenarios"]}

    rows: list[dict[str, Any]] = []
    all_stations: dict[str, Station] = {}
    for trip, graph in verified_e1_graphs(manifest, graphs_dir=V1_GRAPHS, summary=reference):
        stations = dict(graph.stations)
        all_stations.update(stations)
        network = StaticRoadNetwork(graph.legs)
        step = trip.soc_step_pct
        exact = _solve(EVRouteOptimizer(soc_step_pct=step), trip, stations, network)
        greedy = _solve(GreedyFixedTargetOptimizer(soc_step_pct=step), trip, stations, network)
        lookahead = _solve(LookaheadGreedyOptimizer(soc_step_pct=step), trip, stations, network)
        if exact is None:
            raise ValueError(f"expected a feasible exact plan for {trip.route_id}")

        expected = published[(trip.route_id, trip.group)]
        if not math.isclose(exact.total_minutes, expected["nominal_min"], abs_tol=TIE_MINUTES):
            raise ValueError(f"exact fastest no longer reproduces {trip.route_id}")
        if (greedy is None) != ("greedy_min" not in expected) or (
            greedy is not None
            and not math.isclose(greedy.total_minutes, expected["greedy_min"], abs_tol=TIE_MINUTES)
        ):
            raise ValueError(f"fixed-80 greedy no longer reproduces {trip.route_id}")
        for plan in (greedy, lookahead):
            if plan is not None and plan.total_minutes < exact.total_minutes - TIE_MINUTES:
                raise ValueError(f"a heuristic beat exact fastest on {trip.route_id}")

        rows.append(
            {
                "route": trip.route_id,
                "profile": trip.group,
                "exact_fastest": _metrics(exact),
                "greedy_fixed_80": _metrics(greedy),
                "lookahead": _metrics(lookahead),
            }
        )

    evidence = {
        "input_benchmark": manifest["benchmark_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "preregistration": "PROJECT_PLAN.md, M9",
        "reserve_violations_on_returned_plans": 0,
        "summary": {
            "scenarios": len(rows),
            "exact_vs_greedy_fixed_80": _paired(rows, "greedy_fixed_80"),
            "exact_vs_lookahead": _paired(rows, "lookahead"),
            "median_exact_charging_minutes": _round(
                median(row["exact_fastest"]["charging_minutes"] for row in rows)
            ),
        },
        "scenarios": rows,
    }
    assert_no_station_data(evidence, all_stations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence))
    print(json.dumps(evidence["summary"], indent=2))


if __name__ == "__main__":
    run()
