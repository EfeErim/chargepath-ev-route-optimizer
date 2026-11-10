"""Measure what opt-in speed choice changes on the frozen real-route E1 scenarios.

Both runs use the same speed-aware consumption model, so the difference is only the freedom to drive
a leg at 90% or 80% of its OSRM speed. Every plan is replayed under that model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

from chargepath.models import RoutePlan, Station
from chargepath.optimizer import EVRouteOptimizer
from chargepath.providers.static import StaticRoadNetwork
from chargepath.real_route_benchmark import canonical_bytes
from chargepath.real_route_collection import load_stored_graph
from chargepath.real_route_evaluation import assert_no_station_data, enumerate_trips
from chargepath.speed import (
    DEFAULT_SPEED_FACTORS,
    PILOT_QUADRATIC_SHARE,
    PILOT_REFERENCE_SPEED_KMH,
    SpeedAwareConsumption,
    SpeedChoiceNetwork,
)
from chargepath.verification import verify_route_plan

ROOT = Path(__file__).resolve().parents[1]
V1_GRAPHS = ROOT / "data/processed/m6/v1/graphs"
MODEL = SpeedAwareConsumption()


def _round(value: float) -> float:
    return round(value, 3)


def _metrics(plan: RoutePlan) -> dict[str, float | int]:
    return {
        "total_minutes": _round(plan.total_minutes),
        "driving_minutes": _round(plan.driving_minutes),
        "charging_minutes": _round(plan.charging_minutes),
        "charging_stops": len(plan.charging_stops),
    }


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_bytes = (ROOT / "data/benchmarks/m6/v1/manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    reference = json.loads((ROOT / "docs/benchmarks/real-routes/summary_v1.json").read_bytes())

    rows = []
    savings: list[float] = []
    all_stations: dict[str, Station] = {}
    for trip in enumerate_trips(manifest):
        if trip.experiment != "E1":
            continue
        path = V1_GRAPHS / trip.graph.file_name
        expected = reference["stored_graphs"][trip.graph.file_name]["sha256"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"graph checksum mismatch for {trip.graph.file_name}")
        graph = load_stored_graph(path)
        if graph.outcome != "graph":
            raise ValueError(f"missing graph for {trip.graph.file_name}")
        stations = dict(graph.stations)
        all_stations.update(stations)
        base = StaticRoadNetwork(graph.legs)
        choice = SpeedChoiceNetwork(base)
        optimizer = EVRouteOptimizer(soc_step_pct=trip.soc_step_pct, consumption_model=MODEL)
        plans = {}
        for name, network in (("fixed_speed", base), ("speed_choice", choice)):
            plan = optimizer.optimize(
                origin_id="origin",
                destination_id="destination",
                vehicle=trip.vehicle,
                stations=stations,
                road_network=network,
            )
            verify_route_plan(
                plan,
                vehicle=trip.vehicle,
                stations=stations,
                soc_step_pct=trip.soc_step_pct,
                consumption_model=MODEL,
            )
            plans[name] = plan
        if plans["speed_choice"].total_minutes > plans["fixed_speed"].total_minutes + 1e-8:
            raise ValueError("speed choice was slower than fixed speed")
        slowed_km = {
            str(factor): _round(
                sum(
                    leg.distance_km
                    for leg in plans["speed_choice"].legs
                    if choice.speed_factor(leg) == factor
                )
            )
            for factor in DEFAULT_SPEED_FACTORS[1:]
        }
        savings.append(plans["fixed_speed"].total_minutes - plans["speed_choice"].total_minutes)
        rows.append(
            {
                "route": trip.route_id,
                "profile": trip.group,
                "fixed_speed": _metrics(plans["fixed_speed"]),
                "speed_choice": {**_metrics(plans["speed_choice"]), "slowed_km": slowed_km},
            }
        )

    evidence = {
        "input_benchmark": manifest["benchmark_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "consumption_model": {
            "kind": "speed_aware",
            "quadratic_share": _round(PILOT_QUADRATIC_SHARE),
            "reference_speed_kmh": _round(PILOT_REFERENCE_SPEED_KMH),
            "source": "docs/benchmarks/field-pilot/tum_id1_v1.json",
        },
        "speed_factors": list(DEFAULT_SPEED_FACTORS),
        "reserve_violations_on_returned_plans": 0,
        "summary": {
            "scenarios": len(rows),
            "improved": sum(1 for saving in savings if saving > 1e-6),
            "median_saving_minutes": _round(median(savings)),
            "max_saving_minutes": _round(max(savings)),
        },
        "scenarios": rows,
    }
    assert_no_station_data(evidence, all_stations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(evidence))
    print(json.dumps(evidence["summary"]))


if __name__ == "__main__":
    run()
