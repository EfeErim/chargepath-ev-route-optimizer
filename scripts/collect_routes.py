"""Collect real-route candidate graphs from local OSRM into the Git-ignored processed directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from chargepath.providers.osrm import OsrmHttpClient
from chargepath.real_route_collection import (
    collect_graphs,
    load_frozen_options,
    measure_osrm_runtime,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "data/benchmarks/m6/v1/manifest.json"
DEFAULT_GRAPHS = REPOSITORY_ROOT / "data/processed/m6/v1/graphs"
DEFAULT_SNAPSHOT = REPOSITORY_ROOT / "data/raw/epdk/2026-11-05/response.json"
DEFAULT_STATION_MANIFEST = REPOSITORY_ROOT / "data/processed/epdk/2026-11-05/manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--graphs-dir", type=Path, default=DEFAULT_GRAPHS)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--station-manifest", type=Path, default=DEFAULT_STATION_MANIFEST)
    parser.add_argument("--osrm-endpoint", default="http://127.0.0.1:5000")
    parser.add_argument(
        "--osrm-runtime-out",
        type=Path,
        help="Also time provider requests under the E6 protocol and write them to this file",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    options = load_frozen_options(
        snapshot_path=args.snapshot,
        station_manifest_path=args.station_manifest,
        frozen_snapshot=manifest["frozen_inputs"]["station_snapshot"],
    )
    client = OsrmHttpClient(endpoint=args.osrm_endpoint, timeout_seconds=300)
    keys = collect_graphs(
        manifest=manifest,
        options=options,
        client=client,
        output_dir=args.graphs_dir,
    )
    outcomes = [
        json.loads((args.graphs_dir / key.file_name).read_text(encoding="utf-8"))["outcome"]
        for key in keys
    ]
    print(
        f"graphs={len(keys)} stored={outcomes.count('graph')} "
        f"provider_failures={outcomes.count('provider_failure')}"
    )
    if args.osrm_runtime_out is not None:
        runtime = measure_osrm_runtime(manifest=manifest, client=client, graphs_dir=args.graphs_dir)
        args.osrm_runtime_out.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
        print(f"osrm_runtime={args.osrm_runtime_out}")


if __name__ == "__main__":
    main()
