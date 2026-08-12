import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import chargepath.corridor as corridor_module
from chargepath import GeoJsonLineString, Station
from chargepath.corridor import (
    DEFAULT_CANDIDATE_SELECTION_CONFIG,
    RANKING_KEYS,
    CandidateSelectionConfig,
    select_corridor_candidates,
)
from chargepath.station_data import (
    EPDK_SOURCE_NAME,
    EPDK_SOURCE_URL,
    CompatibleStationOption,
    EpdkSchemaDriftError,
    ReconciliationCounts,
    SnapshotProvenance,
    ValidationIssue,
    build_snapshot_manifest,
    normalize_epdk_response,
    project_ccs2_dc_options,
    request_fingerprint,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_EPDK_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
EPDK_METADATA_EVIDENCE = REPOSITORY_ROOT / "docs/evidence/epdk_snapshot_manifest_2026-08-09.json"


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
    assert [socket.stable_id for socket in result.sockets] == [
        "SYN-SITE-001:AC-1",
        "SYN-SITE-001:DC-1",
        "SYN-SITE-001:DC-2",
        "SYN-SITE-002:DC-1",
    ]
    assert result.report.sites.input == 4
    assert result.report.sites.accepted == 2
    assert result.report.sites.rejected == 1
    assert result.report.sites.duplicates == 1
    assert result.report.sockets.input == 8
    assert result.report.sockets.accepted == 4
    assert result.report.sockets.rejected == 2
    assert result.report.sockets.duplicates == 2
    assert {issue.reason for issue in result.report.issues} >= {
        "invalid_coordinates",
        "invalid_socket_power",
        "duplicate_site_source_id",
        "duplicate_socket_source_id",
    }
    assert all(site.provenance == provenance() for site in result.sites)
    assert all(socket.provenance == provenance() for socket in result.sockets)


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
    assert manifest.reuse_status == "pending_verification"
    assert manifest.source_freshness == "unknown_not_supplied_by_response"
    assert manifest.quota_status == "unknown_not_documented"
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


def test_snapshot_manifest_rejects_provenance_checksum_mismatch() -> None:
    raw = SYNTHETIC_EPDK_FIXTURE.read_bytes()
    payload = fixture_payload()
    result = normalize_epdk_response(payload, provenance())
    with pytest.raises(ValueError, match="checksum does not match"):
        build_snapshot_manifest(
            raw_response=raw,
            payload=payload,
            provenance=provenance(),
            report=result.report,
            response_status=200,
        )


def test_snapshot_manifest_rejects_cross_snapshot_report() -> None:
    raw = SYNTHETIC_EPDK_FIXTURE.read_bytes()
    fixture_provenance = provenance(response_sha256=hashlib.sha256(raw).hexdigest())
    result = normalize_epdk_response(fixture_payload(), fixture_provenance)
    with pytest.raises(ValueError, match="report snapshot does not match"):
        build_snapshot_manifest(
            raw_response=raw,
            payload=fixture_payload(),
            provenance=fixture_provenance,
            report=replace(result.report, snapshot_id="other"),
            response_status=200,
        )


def test_snapshot_manifest_rejects_payload_report_and_status_mismatches() -> None:
    raw = SYNTHETIC_EPDK_FIXTURE.read_bytes()
    payload = fixture_payload()
    fixture_provenance = provenance(response_sha256=hashlib.sha256(raw).hexdigest())
    result = normalize_epdk_response(payload, fixture_provenance)

    changed_payload = fixture_payload()
    changed_payload["message"] = "different"
    with pytest.raises(ValueError, match="content does not match"):
        build_snapshot_manifest(
            raw_response=raw,
            payload=changed_payload,
            provenance=fixture_provenance,
            report=result.report,
            response_status=200,
        )

    bad_report = replace(
        result.report,
        sites=ReconciliationCounts(5, 2, 2, 1),
        issues=(
            *result.report.issues,
            ValidationIssue("site", "extra", "synthetic_mismatch"),
        ),
    )
    with pytest.raises(ValueError, match="input counts do not match"):
        build_snapshot_manifest(
            raw_response=raw,
            payload=payload,
            provenance=fixture_provenance,
            report=bad_report,
            response_status=200,
        )

    with pytest.raises(ValueError, match="status does not match"):
        build_snapshot_manifest(
            raw_response=raw,
            payload=payload,
            provenance=fixture_provenance,
            report=result.report,
            response_status=201,
        )


@pytest.mark.parametrize(
    "retrieved_at",
    ["not-a-dateZ", "2026-99-99T14:13:33Z", "2026-08-09T14:13:33+03:00"],
)
def test_snapshot_provenance_rejects_invalid_utc_timestamp(retrieved_at: str) -> None:
    with pytest.raises(ValueError, match="UTC timestamp"):
        SnapshotProvenance(
            snapshot_id="test",
            source_name=EPDK_SOURCE_NAME,
            source_url=EPDK_SOURCE_URL,
            retrieved_at=retrieved_at,
            response_sha256="a" * 64,
            reuse_status="pending_verification",
        )


def test_ccs2_projection_rejects_orphan_socket_and_mixed_provenance() -> None:
    result = normalize_epdk_response(fixture_payload(), provenance())
    orphan = replace(result.sockets[1], site_source_id="missing")
    with pytest.raises(ValueError, match="unknown normalized site"):
        project_ccs2_dc_options(replace(result, sockets=(orphan,)))

    other_provenance = replace(provenance(), snapshot_id="other")
    mixed = replace(result.sockets[1], provenance=other_provenance)
    with pytest.raises(ValueError, match="provenance must match"):
        project_ccs2_dc_options(replace(result, sockets=(mixed,)))


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


def make_option(
    station_id: str, *, longitude: float, latitude: float, power_kw: float = 150
) -> CompatibleStationOption:
    source_id = station_id.removeprefix("epdk:")
    return CompatibleStationOption(
        station=Station(
            id=station_id,
            name=f"Synthetic {source_id}",
            latitude=latitude,
            longitude=longitude,
            max_power_kw=power_kw,
        ),
        site_source_id=source_id,
        socket_source_ids=("DC-1",),
        provenance=provenance(),
    )


def test_corridor_selection_is_capped_stable_and_manifested() -> None:
    route = GeoJsonLineString(((32.0, 39.0), (33.0, 39.0)))
    options = (
        make_option("epdk:b", longitude=32.5, latitude=39.01, power_kw=200),
        make_option("epdk:a", longitude=32.5, latitude=39.01),
        make_option("epdk:early", longitude=32.2, latitude=39.02, power_kw=350),
        make_option("epdk:outside", longitude=32.5, latitude=40.0),
    )
    config = CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=2)

    first = select_corridor_candidates(route_geometry=route, options=options, config=config)
    second = select_corridor_candidates(
        route_geometry=route, options=tuple(reversed(options)), config=config
    )

    assert first.station_ids == ("epdk:early", "epdk:b")
    assert second.station_ids == first.station_ids
    assert first.eligible_count == 3
    assert first.config.to_manifest() == {
        "algorithm": "equirectangular-segment-farthest-progress-v3",
        "corridor_width_km": 5.0,
        "candidate_cap": 2,
        "ranking_keys": list(RANKING_KEYS),
        "final_tie_break": "stable_station_id_asc",
        "coverage_policy": "prefix-monotonic-largest-progress-gap-then-quality",
        "progress_gap_tolerance_fraction": 0.02,
    }
    assert DEFAULT_CANDIDATE_SELECTION_CONFIG.to_manifest() == {
        "algorithm": "equirectangular-segment-farthest-progress-v3",
        "corridor_width_km": 25.0,
        "candidate_cap": 50,
        "ranking_keys": list(RANKING_KEYS),
        "final_tie_break": "stable_station_id_asc",
        "coverage_policy": "prefix-monotonic-largest-progress-gap-then-quality",
        "progress_gap_tolerance_fraction": 0.02,
    }


def test_corridor_stable_identifier_breaks_exact_final_tie() -> None:
    route = GeoJsonLineString(((32.0, 39.0), (33.0, 39.0)))
    options = (
        make_option("epdk:b", longitude=32.5, latitude=39.01),
        make_option("epdk:a", longitude=32.5, latitude=39.01),
    )
    result = select_corridor_candidates(
        route_geometry=route,
        options=options,
        config=CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=2),
    )
    assert result.station_ids == ("epdk:a", "epdk:b")


def test_corridor_cap_covers_route_progress_instead_of_clustering() -> None:
    route = GeoJsonLineString(((0.0, 39.0), (10.0, 39.0)))
    options = (
        make_option("epdk:progress-20", longitude=2.0, latitude=39.01),
        make_option("epdk:progress-40", longitude=4.0, latitude=39.01),
        make_option("epdk:progress-60", longitude=6.0, latitude=39.01),
        make_option("epdk:cluster-91", longitude=9.1, latitude=39.0),
        make_option("epdk:cluster-92", longitude=9.2, latitude=39.0),
        make_option("epdk:cluster-93", longitude=9.3, latitude=39.0),
        make_option("epdk:cluster-94", longitude=9.4, latitude=39.0),
    )
    config = CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=4)

    first = select_corridor_candidates(route_geometry=route, options=options, config=config)
    second = select_corridor_candidates(
        route_geometry=route,
        options=tuple(reversed(options)),
        config=config,
    )

    assert set(first.station_ids) >= {
        "epdk:progress-20",
        "epdk:progress-40",
        "epdk:progress-60",
    }
    assert sum(station_id.startswith("epdk:cluster-") for station_id in first.station_ids) == 1
    assert second.station_ids == first.station_ids
    assert {
        corridor_module._progress_stratum(candidate.route_progress_fraction, 4)
        for candidate in first.candidates
    } == {0, 1, 2, 3}


def test_corridor_selection_is_prefix_monotonic_as_cap_grows() -> None:
    route = GeoJsonLineString(((0.0, 39.0), (10.0, 39.0)))
    options = tuple(
        make_option(
            f"epdk:{index:02d}",
            longitude=index / 2,
            latitude=39.0 + (index % 3) * 0.01,
            power_kw=50 + index * 5,
        )
        for index in range(1, 20)
    )
    selections = [
        select_corridor_candidates(
            route_geometry=route,
            options=options,
            config=CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=cap),
        ).station_ids
        for cap in (4, 8, 12)
    ]

    assert selections[1][:4] == selections[0]
    assert selections[2][:8] == selections[1]


def test_farthest_progress_uses_quality_inside_small_coverage_tolerance() -> None:
    route = GeoJsonLineString(((0.0, 39.0), (10.0, 39.0)))
    result = select_corridor_candidates(
        route_geometry=route,
        options=(
            make_option("epdk:start", longitude=0.1, latitude=39.0),
            make_option("epdk:far-off-route", longitude=9.9, latitude=39.2),
            make_option("epdk:near-route", longitude=9.75, latitude=39.0),
        ),
        config=CandidateSelectionConfig(corridor_width_km=25.0, candidate_cap=2),
    )

    assert result.station_ids == ("epdk:start", "epdk:near-route")


def test_corridor_rejects_duplicate_station_ids_before_graph_construction() -> None:
    route = GeoJsonLineString(((0.0, 39.0), (1.0, 39.0)))
    with pytest.raises(ValueError, match="station ids must be unique"):
        select_corridor_candidates(
            route_geometry=route,
            options=(
                make_option("epdk:duplicate", longitude=0.2, latitude=39.0),
                make_option("epdk:duplicate", longitude=0.8, latitude=39.0),
            ),
            config=CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=2),
        )


def test_corridor_precomputes_route_lengths_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = GeoJsonLineString(((32.0, 39.0), (32.25, 39.05), (32.5, 39.0), (33.0, 39.0)))
    options = tuple(
        make_option(f"epdk:{index}", longitude=32.1 + index * 0.15, latitude=39.01)
        for index in range(5)
    )
    calls = 0
    original = corridor_module._haversine_km

    def counted_haversine(*args: float) -> float:
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(corridor_module, "_haversine_km", counted_haversine)

    result = select_corridor_candidates(
        route_geometry=route,
        options=options,
        config=CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=5),
    )

    assert len(result.candidates) == 5
    assert calls == len(route.coordinates) - 1
