"""Create metadata-only M2 evidence from an ignored raw EPDK response."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from chargepath.station_data import (
    EPDK_SOURCE_NAME,
    EPDK_SOURCE_URL,
    SnapshotProvenance,
    build_snapshot_manifest,
    normalize_epdk_response,
    project_ccs2_dc_options,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect an already retrieved EPDK response without making a network call."
    )
    parser.add_argument("raw_response", type=Path)
    parser.add_argument("--retrieved-at", required=True, help="UTC ISO-8601 timestamp ending in Z")
    parser.add_argument("--response-status", type=int, default=200)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--report-out", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = args.raw_response.read_bytes()
    payload: Any = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("EPDK response root must be an object")
    checksum = hashlib.sha256(raw).hexdigest()
    date = args.retrieved_at[:10].replace("-", "")
    provenance = SnapshotProvenance(
        snapshot_id=f"epdk-{date}-{checksum[:12]}",
        source_name=EPDK_SOURCE_NAME,
        source_url=EPDK_SOURCE_URL,
        retrieved_at=args.retrieved_at,
        response_sha256=checksum,
        reuse_status="pending_verification",
    )
    normalized = normalize_epdk_response(payload, provenance)
    options = project_ccs2_dc_options(normalized)
    manifest = build_snapshot_manifest(
        raw_response=raw,
        payload=payload,
        provenance=provenance,
        report=normalized.report,
        response_status=args.response_status,
    )

    if args.manifest_out is not None:
        _write_json(args.manifest_out, manifest.to_dict())
    if args.report_out is not None:
        _write_json(args.report_out, normalized.report.to_dict())

    print(f"snapshot_id={manifest.snapshot_id}")
    print(f"sites={manifest.source_record_count}")
    print(f"sockets={manifest.source_socket_count}")
    print(f"compatible_ccs2_sites={len(options)}")
    print(f"site_reconciliation={manifest.reconciliation['sites']}")
    print(f"socket_reconciliation={manifest.reconciliation['sockets']}")
    print(f"reuse_status={manifest.reuse_status}")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
