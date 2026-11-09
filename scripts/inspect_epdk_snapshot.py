"""Create metadata-only snapshot evidence from an ignored raw EPDK response."""

from __future__ import annotations

import argparse
from pathlib import Path

from chargepath.station_data import inspect_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect an already retrieved EPDK response without making a network call."
    )
    parser.add_argument("raw_response", type=Path)
    parser.add_argument("--retrieved-at", required=True, help="UTC ISO-8601 timestamp ending in Z")
    parser.add_argument("--response-status", type=int, default=200)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args()

    manifest, options = inspect_snapshot(
        args.raw_response,
        retrieved_at=args.retrieved_at,
        response_status=args.response_status,
        manifest_out=args.manifest_out,
        report_out=args.report_out,
    )
    print(f"snapshot_id={manifest.snapshot_id}")
    print(f"sites={manifest.source_record_count}")
    print(f"sockets={manifest.source_socket_count}")
    print(f"compatible_ccs2_sites={len(options)}")
    print(f"site_reconciliation={manifest.reconciliation['sites']}")
    print(f"socket_reconciliation={manifest.reconciliation['sockets']}")
    print(f"reuse_status={manifest.reuse_status}")


if __name__ == "__main__":
    main()
