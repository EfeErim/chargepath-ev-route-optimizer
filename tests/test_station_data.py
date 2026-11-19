import hashlib
import importlib.util
import json
import urllib.error
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, cast

import pytest

from chargepath import Station
from chargepath.corridor import DEFAULT_CANDIDATE_SELECTION_CONFIG
from chargepath.station_data import (
    EPDK_SOURCE_NAME,
    EPDK_SOURCE_URL,
    EpdkSchemaDriftError,
    SnapshotProvenance,
    build_snapshot_manifest,
    normalize_epdk_response,
    project_ccs2_dc_options,
    request_fingerprint,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_EPDK_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
EPDK_METADATA_EVIDENCE = REPOSITORY_ROOT / "docs/benchmarks/epdk_snapshot_manifest_2026-08-09.json"


def provenance(*, response_sha256: str = "a" * 64) -> SnapshotProvenance:
    return SnapshotProvenance(
        snapshot_id="epdk-synthetic-test-001",
        source_name=EPDK_SOURCE_NAME,
        source_url=EPDK_SOURCE_URL,
        retrieved_at="2026-08-09T14:13:33Z",
        response_sha256=response_sha256,
        reuse_status="pending_verification",
    )


def fixture_payload() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(SYNTHETIC_EPDK_FIXTURE.read_text(encoding="utf-8")),
    )


def test_observed_schema_normalizes_with_reconciled_rejections_and_duplicates() -> None:
    result = normalize_epdk_response(fixture_payload(), provenance())

    assert [site.source_id for site in result.sites] == ["SYN-SITE-001", "SYN-SITE-002"]
    assert [f"{socket.site_source_id}:{socket.source_id}" for socket in result.sockets] == [
        "SYN-SITE-001:AC-1",
        "SYN-SITE-001:DC-1",
        "SYN-SITE-001:DC-2",
        "SYN-SITE-002:DC-1",
    ]
    sites, sockets = result.report.sites, result.report.sockets
    assert (sites.input, sites.accepted, sites.rejected, sites.duplicates) == (4, 2, 1, 1)
    assert (sockets.input, sockets.accepted, sockets.rejected, sockets.duplicates) == (8, 4, 2, 2)
    assert {issue.reason for issue in result.report.issues} >= {
        "invalid_coordinates",
        "invalid_socket_power",
        "duplicate_site_source_id",
        "duplicate_socket_source_id",
    }


def test_ccs2_projection_keeps_socket_provenance_and_uses_site_max_power() -> None:
    result = normalize_epdk_response(fixture_payload(), provenance())
    options = project_ccs2_dc_options(result)

    assert len(options) == 1
    assert options[0].station == Station(
        id="epdk:SYN-SITE-001",
        name="Synthetic North Hub",
        latitude=39.0,
        longitude=32.0,
        max_power_kw=180.0,
        connector_type="CCS2",
    )
    assert options[0].socket_source_ids == ("DC-1", "DC-2")
    assert options[0].provenance.snapshot_id == "epdk-synthetic-test-001"
    assert result.sites[1].access == "private"
    assert result.sockets[-1].connector_type == "CCS2"


@pytest.mark.parametrize("location", ["root", "site", "socket"])
def test_unreviewed_schema_fields_fail_closed(location: str) -> None:
    payload = fixture_payload()
    if location == "root":
        payload["unexpected"] = True
    elif location == "site":
        payload["data"][0]["unexpected"] = True  # type: ignore[index]
    else:
        payload["data"][0]["soketler"][0]["unexpected"] = True  # type: ignore[index]

    with pytest.raises(EpdkSchemaDriftError, match="unreviewed|fields changed"):
        normalize_epdk_response(payload, provenance())


def test_snapshot_manifest_records_request_checksum_counts_and_reconciliation() -> None:
    raw = SYNTHETIC_EPDK_FIXTURE.read_bytes()
    payload = fixture_payload()
    fixture_provenance = provenance(response_sha256=hashlib.sha256(raw).hexdigest())
    result = normalize_epdk_response(payload, fixture_provenance)
    manifest = build_snapshot_manifest(
        raw_response=raw,
        payload=payload,
        provenance=fixture_provenance,
        report=result.report,
        response_status=200,
    )

    assert manifest.source_record_count == 4
    assert manifest.source_socket_count == 8
    assert manifest.response_bytes == len(raw)
    assert manifest.source_freshness == "unknown_not_supplied_by_response"
    assert manifest.reconciliation["sites"] == {
        "input": 4,
        "accepted": 2,
        "rejected": 1,
        "duplicates": 1,
    }
    assert manifest.request_fingerprint_sha256 == request_fingerprint(
        method="GET",
        url=EPDK_SOURCE_URL,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        body="{}",
    )


def test_committed_epdk_evidence_is_metadata_only_and_matches_default_contract() -> None:
    evidence = json.loads(EPDK_METADATA_EVIDENCE.read_text(encoding="utf-8"))
    assert "data" not in evidence
    assert evidence["repository_decision"] == "metadata_and_synthetic_fixture_only"
    assert evidence["reuse_status"] == "pending_verification"
    assert evidence["source_record_count"] >= 500
    assert (
        evidence["response_sha256"]
        == "2850e5e2082751ffcc955a11ad217671c98b292ca665f87d2966fb42c90ae005"
    )
    assert evidence["request_fingerprint_sha256"] == request_fingerprint(
        method="GET",
        url=EPDK_SOURCE_URL,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        body="{}",
    )
    assert evidence["candidate_selection_default"] == (
        DEFAULT_CANDIDATE_SELECTION_CONFIG.to_manifest()
    )


def _fetch_script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts/fetch_epdk_snapshot.py"
    spec = importlib.util.spec_from_file_location("fetch_epdk_snapshot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


def test_fetch_makes_one_get_request_and_never_retries_a_rate_limit() -> None:
    requests: list[Any] = []

    def opener(request: Any, *, timeout: int) -> _FakeResponse:
        requests.append(request)
        if len(requests) > 1:
            raise urllib.error.HTTPError(EPDK_SOURCE_URL, 429, "Too Many Requests", Message(), None)
        return _FakeResponse(b"{}")

    script = _fetch_script()
    assert script.fetch_response(opener=opener) == (200, b"{}")
    assert (requests[0].get_method(), requests[0].data) == ("GET", b"{}")
    assert requests[0].full_url == EPDK_SOURCE_URL
    assert script.fetch_response(opener=opener) == (429, b"")
    assert len(requests) == 2


def test_stored_snapshot_gets_a_matching_manifest_and_is_never_replaced(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures/epdk/observed_v1/synthetic_response.json"
    body = source.read_bytes()
    retrieved_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    script = _fetch_script()

    raw_path, manifest_path = script.store_snapshot(
        root=tmp_path, status=200, body=body, retrieved_at=retrieved_at
    )

    assert raw_path == tmp_path / "data/raw/epdk/2026-11-05/response.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert manifest["reuse_status"] == "pending_verification"
    with pytest.raises(FileExistsError):
        script.store_snapshot(root=tmp_path, status=200, body=body, retrieved_at=retrieved_at)
