"""Measure province road distances on local OSRM and write the real-route benchmark manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from chargepath.providers.osrm import Coordinate, OsrmHttpClient
from chargepath.real_route_benchmark import (
    build_manifest,
    canonical_bytes,
    distance_key,
    load_provinces,
    select_routes,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPOSITORY_ROOT / "data/benchmarks/real-routes/v1"
DATA_VERSION = "geofabrik-turkey-20261104-794874509924"
FROZEN_INPUTS = {
    "road_graph": {
        "source": "Geofabrik turkey-latest.osm.pbf",
        "bytes": 652_637_317,
        "last_modified": "2026-11-04T11:15:16Z",
        "sha256": "794874509924396187de1c9f1f63ce4fb4a306a4e09d089786db79d7e7bffc11",
        "data_version": DATA_VERSION,
        "osrm_backend": "v26.7.3",
        "osrm_commit": "0844e3af77896d11998ef6db356a553056652c8e",
        "osrm_algorithm": "CH",
        "profile": "car.lua",
        "profile_sha256_lf": "48bbb716c2b68ce6803a11a4151fcac050c6ea8240295e7dde111f15f8bd3984",
    },
    "station_snapshot": {
        "snapshot_id": "epdk-20261105-bb01ff406b86",
        "sha256": "bb01ff406b8636b98e634a071761fff440c12ea310678d8a3d8efef69baa7986",
        "retrieved_at": "2026-11-05T13:43:42Z",
        "reuse_status": "pending_verification",
        "stored_in_repository": False,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osrm-endpoint", default="http://127.0.0.1:5000")
    parser.add_argument("--road-metadata", type=Path, help="JSON road_graph provenance object")
    parser.add_argument("--station-manifest", type=Path, help="Reviewed EPDK snapshot manifest")
    parser.add_argument("--output-dir", type=Path, help="New directory for an independent run")
    args = parser.parse_args()

    custom_run = any((args.road_metadata, args.station_manifest, args.output_dir))
    if custom_run and not all((args.road_metadata, args.station_manifest, args.output_dir)):
        parser.error(
            "an independent run requires --road-metadata, --station-manifest and --output-dir"
        )
    frozen_inputs = FROZEN_INPUTS
    output_dir = BENCHMARK_DIR
    if custom_run:
        output_dir = args.output_dir
        if output_dir.exists():
            parser.error("--output-dir must be a new directory")
        road_metadata = json.loads(args.road_metadata.read_text(encoding="utf-8"))
        station_manifest = json.loads(args.station_manifest.read_text(encoding="utf-8"))
        frozen_inputs = {
            "road_graph": road_metadata,
            "station_snapshot": {
                "snapshot_id": station_manifest["snapshot_id"],
                "sha256": station_manifest["response_sha256"],
                "retrieved_at": station_manifest["retrieved_at"],
                "reuse_status": station_manifest["reuse_status"],
                "stored_in_repository": False,
            },
        }
    data_version = frozen_inputs["road_graph"]["data_version"]

    provinces_path = BENCHMARK_DIR / "provinces.json"
    provinces = load_provinces(provinces_path)
    client = OsrmHttpClient(endpoint=args.osrm_endpoint, timeout_seconds=120)
    table = client.table(
        tuple(Coordinate(province.longitude, province.latitude) for province in provinces)
    )
    if table.data_version != data_version:
        raise SystemExit(f"OSRM reports data_version {table.data_version!r}, not {data_version!r}")

    road_km: dict[str, float] = {}
    for row, origin in enumerate(provinces):
        for column in range(row + 1, len(provinces)):
            cell = table.cells[row][column]
            if cell is not None:
                road_km[distance_key(origin.plate, provinces[column].plate)] = round(
                    cell.distance_km, 1
                )

    distances_bytes = canonical_bytes(
        {
            "schema_version": 1,
            "description": (
                "Road km from the lower to the higher plate number; used only to assign bands."
            ),
            "data_version": data_version,
            "attribution": "Derived from OpenStreetMap data, © OpenStreetMap contributors, ODbL.",
            "unreachable_pairs_omitted": len(provinces) * (len(provinces) - 1) // 2 - len(road_km),
            "road_km": road_km,
        }
    )
    routes = select_routes(provinces, road_km)
    manifest = build_manifest(
        routes=routes,
        frozen_inputs={
            **frozen_inputs,
            "provinces_sha256": hashlib.sha256(provinces_path.read_bytes()).hexdigest(),
            "selection_distances_sha256": hashlib.sha256(distances_bytes).hexdigest(),
        },
        distances_data_version=data_version,
    )
    if custom_run:
        output_dir.mkdir(parents=True)
        (output_dir / "provinces.json").write_bytes(provinces_path.read_bytes())
    (output_dir / "selection_distances.json").write_bytes(distances_bytes)
    (output_dir / "manifest.json").write_bytes(canonical_bytes(manifest))
    for route in routes:
        print(
            f"{route.route_id} {route.band:>9} slot={route.slot} "
            f"{route.origin.name} -> {route.destination.name} {route.selection_road_km} km"
        )


if __name__ == "__main__":
    main()
