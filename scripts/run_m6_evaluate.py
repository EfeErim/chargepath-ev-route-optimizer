"""Evaluate the stored M6 graphs offline and write normalized evidence with no station data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from chargepath.models import Station
from chargepath.real_route_collection import load_stored_graph
from chargepath.real_route_evaluation import (
    assert_no_station_data,
    enumerate_trips,
    evaluate,
    measure_planner_runtime,
    summary_bytes,
    trips_bytes,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "data/benchmarks/m6/v1/manifest.json"
DEFAULT_GRAPHS = REPOSITORY_ROOT / "data/processed/m6/v1/graphs"
DEFAULT_EVIDENCE = REPOSITORY_ROOT / "docs/evidence/m6"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--graphs-dir", type=Path, default=DEFAULT_GRAPHS)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--osrm-runtime", type=Path, help="Provider timing from run_m6_collect.py")
    parser.add_argument("--runtime-out", type=Path, help="Write the machine-specific E6 artifact")
    args = parser.parse_args()

    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    summary, trips = evaluate(
        manifest=manifest,
        graphs_dir=args.graphs_dir,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )

    stations: dict[str, Station] = {}
    for key in sorted({trip.graph for trip in enumerate_trips(manifest)}):
        stations.update(load_stored_graph(args.graphs_dir / key.file_name).stations)
    assert_no_station_data({"summary": summary, "trips": trips}, stations)

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    (args.evidence_dir / "summary_v1.json").write_bytes(summary_bytes(summary))
    (args.evidence_dir / "trips_v1.jsonl").write_bytes(trips_bytes(trips))
    print(json.dumps(summary["reconciliation"], indent=2))

    if args.runtime_out is not None:
        runtime = {
            "benchmark_id": manifest["benchmark_id"],
            "machine_specific": True,
            "planner": measure_planner_runtime(manifest=manifest, graphs_dir=args.graphs_dir),
        }
        if args.osrm_runtime is not None:
            runtime["osrm"] = json.loads(args.osrm_runtime.read_text(encoding="utf-8"))
        args.runtime_out.parent.mkdir(parents=True, exist_ok=True)
        args.runtime_out.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
