"""Retrieve one EPDK station response into the ignored data directories, then inspect it."""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chargepath.station_data import EPDK_SOURCE_URL, inspect_snapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
TIMEOUT_SECONDS = 120


def fetch_response(
    url: str = EPDK_SOURCE_URL,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[int, bytes]:
    """Make exactly one request; the service expects GET with an empty JSON body."""

    request = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ChargePath",
        },
        method="GET",
    )
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = int(response.status)
    except urllib.error.HTTPError as error:
        return error.code, b""
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("EPDK response exceeds the configured size limit")
    return status, body


def store_snapshot(
    *,
    root: Path,
    status: int,
    body: bytes,
    retrieved_at: datetime,
) -> tuple[Path, Path]:
    """Write the raw response and its manifest, refusing to replace an existing snapshot."""

    date = retrieved_at.strftime("%Y-%m-%d")
    raw_path = root / "data/raw/epdk" / date / "response.json"
    processed = root / "data/processed/epdk" / date
    manifest_path = processed / "manifest.json"
    if raw_path.exists():
        raise FileExistsError(f"snapshot already exists: {raw_path}")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(body)

    inspect_snapshot(
        raw_path,
        retrieved_at=retrieved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        response_status=status,
        manifest_out=manifest_path,
        report_out=processed / "validation_report.json",
    )
    return raw_path, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Make one request to the official EPDK service and store the response under the "
            "Git-ignored data/raw and data/processed directories. No retry or polling is performed."
        )
    )
    parser.parse_args()
    status, body = fetch_response()
    if status == 429:
        raise SystemExit(
            "EPDK returned HTTP 429 (rate limited); nothing was stored. "
            "Its quota is undocumented, so try again later rather than repeatedly."
        )
    if status != 200:
        raise SystemExit(f"EPDK returned HTTP {status}; nothing was stored")
    raw_path, manifest_path = store_snapshot(
        root=REPOSITORY_ROOT,
        status=status,
        body=body,
        retrieved_at=datetime.now(UTC),
    )
    print(f"raw_response={raw_path}")
    print(f"manifest={manifest_path}")
    print("EPDK reuse conditions remain unverified: keep this snapshot local and out of Git.")


if __name__ == "__main__":
    main()
