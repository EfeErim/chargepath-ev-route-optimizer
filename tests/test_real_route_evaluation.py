from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from chargepath.models import GeoJsonLineString, Station, VehicleProfile
from chargepath.providers.base import CoordinateLike
from chargepath.providers.osrm import (
    OsrmResponseError,
    OsrmTableCost,
    OsrmTableResult,
)
from chargepath.real_route_benchmark import Province, SelectedRoute, build_manifest
from chargepath.real_route_collection import (
    GraphKey,
    collect_graphs,
    load_frozen_options,
    load_stored_graph,
    measure_osrm_runtime,
    required_graph_keys,
)
from chargepath.real_route_evaluation import (
    EvaluationDefectError,
    assert_no_station_data,
    check_gates,
    enumerate_trips,
    evaluate,
    measure_planner_runtime,
    run_trip_on_graph,
    summary_bytes,
    trips_bytes,
)
from chargepath.station_data import CompatibleStationOption, SnapshotProvenance

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY_ROOT / "tests/fixtures/real-routes/synthetic_graph.json"
ALGORITHMS = (
    "shortest_driving_time",
    "fastest",
    "shortest_distance",
    "fewest_charging_stops",
    "greedy_fixed_80",
)
DATA_VERSION = "synthetic-data-version"
SNAPSHOT_ID = "synthetic-snapshot"
PROVENANCE = SnapshotProvenance(
    snapshot_id=SNAPSHOT_ID,
    source_name="synthetic fixture",
    source_url="https://example.invalid/synthetic",
    retrieved_at="2026-11-05T00:00:00Z",
    response_sha256="0" * 64,
    reuse_status="pending_verification",
    source_freshness="unknown",
)


def _province(plate: int, longitude: float) -> Province:
    return Province(plate, f"P{plate}", "Marmara", 39.0, longitude)


def _synthetic_manifest() -> dict[str, Any]:
    routes = (
        SelectedRoute("m6-r01", "150-300", 0, _province(1, 30.0), _province(2, 33.0), 300.0),
        SelectedRoute("m6-r02", "300-600", 2, _province(3, 30.0), _province(4, 36.0), 600.0),
        SelectedRoute("m6-r03", "600-1000", 1, _province(5, 30.0), _province(6, 32.0), 200.0),
    )
    return build_manifest(
        routes=routes,
        frozen_inputs={
            "road_graph": {"data_version": DATA_VERSION},
            "station_snapshot": {"snapshot_id": SNAPSHOT_ID},
        },
        distances_data_version=DATA_VERSION,
    )


def _synthetic_options(count: int = 12) -> tuple[CompatibleStationOption, ...]:
    return tuple(
        CompatibleStationOption(
            station=Station(
                id=f"epdk:SYN-{index:03d}",
                name=f"Synthetic Site {index:03d}",
                latitude=39.01,
                longitude=30.0 + 6.0 * index / count,
                max_power_kw=(50, 150, 250)[index % 3],
            ),
            site_source_id=f"SYN-{index:03d}",
            socket_source_ids=(f"SYN-{index:03d}:1",),
            provenance=PROVENANCE,
        )
        for index in range(count)
    )


class FakeClient:
    """Deterministic offline stand-in for osrm-routed; road km are 1.2 times straight-line km."""

    def __init__(self, *, data_version: str = DATA_VERSION, failing_route: float | None = None):
        self.data_version = data_version
        self.failing_route = failing_route
        self.table_sizes: list[int] = []
        self.route_calls = 0

    @staticmethod
    def _km(start: CoordinateLike, end: CoordinateLike) -> float:
        return (
            1.2
            * 111.0
            * math.hypot(
                (start.longitude - end.longitude) * math.cos(math.radians(39)),
                start.latitude - end.latitude,
            )
        )

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        self.table_sizes.append(len(coordinates))
        cells = tuple(
            tuple(OsrmTableCost(self._km(a, b), self._km(a, b) * 0.8) for b in coordinates)
            for a in coordinates
        )
        return OsrmTableResult(cells=cells, data_version=self.data_version)

    def route_geometry(
        self, origin: CoordinateLike, destination: CoordinateLike
    ) -> GeoJsonLineString:
        self.route_calls += 1
        if destination.longitude == self.failing_route:
            raise OsrmResponseError("OSRM NoRoute: synthetic failure", code="NoRoute")
        return GeoJsonLineString(
            ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
        )


def _collected(tmp_path: Path, **client_options: Any) -> tuple[dict[str, Any], Path, FakeClient]:
    manifest = _synthetic_manifest()
    client = FakeClient(**client_options)
    graphs_dir = tmp_path / "graphs"
    collect_graphs(
        manifest=manifest,
        options=_synthetic_options(),
        client=client,
        output_dir=graphs_dir,
    )
    return manifest, graphs_dir, client


def _vehicle() -> VehicleProfile:
    return VehicleProfile(
        name="synthetic",
        usable_battery_kwh=60,
        initial_soc_pct=80,
        consumption_kwh_per_100km=18,
        max_dc_power_kw=150,
        reserve_soc_pct=10,
        energy_safety_factor=1.1,
    )


def test_fixture_graph_matches_hand_audited_outcomes() -> None:
    graph = load_stored_graph(FIXTURE)
    outcome = run_trip_on_graph(graph, vehicle=_vehicle(), soc_step_pct=5, algorithms=ALGORITHMS)
    results = outcome["results"]
    # The fastest road path runs 290 km via the slow hub (185 minutes against 190 direct). It needs
    # 57.42 kWh, or about 96% SOC: beyond the 70% available above reserve from 80%.
    assert results["shortest_driving_time"] == {"outcome": "infeasible"}
    assert results["fastest"]["outcome"] == "plan"
    assert results["fastest"]["charging_stops"] == 1
    assert results["fastest"]["reserve_violations"] == 0
    assert results["fastest"]["arrival_soc_pct"] >= 10
    assert results["shortest_distance"]["distance_km"] == pytest.approx(290.0)
    assert results["fastest"]["detour_km"] == pytest.approx(
        results["fastest"]["distance_km"] - 290.0
    )
    assert results["fastest"]["total_minutes"] <= results["greedy_fixed_80"]["total_minutes"]


def test_required_graphs_cover_every_declared_combination() -> None:
    manifest = _synthetic_manifest()
    keys = required_graph_keys(manifest)
    assert len(keys) == len(set(keys))
    # Three routes at caps 12/24/50/98/200 and widths 10/25/50, sharing width 25 with cap 50.
    assert len(keys) == 3 * (5 + 2)
    assert {trip.graph for trip in enumerate_trips(manifest)} == set(keys)


def test_trip_expansion_changes_only_the_named_factor() -> None:
    manifest = _synthetic_manifest()
    trips = enumerate_trips(manifest)
    e5_routes = len(manifest["experiments"]["E5"]["routes"])
    assert Counter(trip.experiment for trip in trips) == {
        "E1": 3 * 3,
        "E2": 3 * 3 * 3,
        "E3": 3 * 5,
        "E4": 3 * 3,
        "E5": e5_routes * 18,
    }
    base = manifest["parameter_sets"]["set-60kwh"]
    for trip in trips:
        if trip.experiment != "E5":
            continue
        changed = [
            field for field, expected in base.items() if getattr(trip.vehicle, field) != expected
        ]
        assert changed in ([], [trip.group])


def test_collection_stores_graphs_and_checks_the_frozen_data_version(tmp_path: Path) -> None:
    manifest, graphs_dir, client = _collected(tmp_path)
    stored = load_stored_graph(graphs_dir / GraphKey("m6-r01", 25, 12).file_name)
    assert stored.outcome == "graph"
    assert len(stored.stations) <= 12
    assert stored.eligible_count >= len(stored.stations)
    assert client.route_calls == len(manifest["routes"])

    with pytest.raises(ValueError, match="data_version"):
        collect_graphs(
            manifest=manifest,
            options=_synthetic_options(),
            client=FakeClient(data_version="another-graph"),
            output_dir=tmp_path / "other",
        )


def test_provider_failure_is_an_outcome_and_counts_reconcile(tmp_path: Path) -> None:
    manifest, graphs_dir, _ = _collected(tmp_path, failing_route=36.0)
    stored = json.loads(
        (graphs_dir / GraphKey("m6-r02", 25, 50).file_name).read_text(encoding="utf-8")
    )
    assert stored["outcome"] == "provider_failure"
    assert stored["error_code"] == "NoRoute"
    assert "url" not in json.dumps(stored).lower()

    summary, trips = evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)
    reconciliation = summary["reconciliation"]
    assert reconciliation["evaluated_trips"] == reconciliation["expected_trips"] == len(trips)
    for algorithm in ALGORITHMS:
        counts = reconciliation["outcomes_per_algorithm"][algorithm]
        assert sum(counts.values()) == len(trips)
        assert counts["provider_failure"] == sum(trip["route_id"] == "m6-r02" for trip in trips)


def test_repeated_evaluation_is_byte_for_byte_stable(tmp_path: Path) -> None:
    manifest, graphs_dir, _ = _collected(tmp_path)
    first = evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)
    second = evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)
    assert summary_bytes(first[0]) == summary_bytes(second[0])
    assert trips_bytes(first[1]) == trips_bytes(second[1])


def test_summary_records_the_checksum_of_every_stored_graph(tmp_path: Path) -> None:
    manifest, graphs_dir, _ = _collected(tmp_path)
    summary, _ = evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)
    assert set(summary["stored_graphs"]) == {key.file_name for key in required_graph_keys(manifest)}
    for name, record in summary["stored_graphs"].items():
        assert record["sha256"] == hashlib.sha256((graphs_dir / name).read_bytes()).hexdigest()


def test_committed_evidence_shape_contains_no_station_data(tmp_path: Path) -> None:
    manifest, graphs_dir, _ = _collected(tmp_path)
    summary, trips = evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)
    stations = {option.station.id: option.station for option in _synthetic_options()}
    assert any(trip["distinct_options"] > 0 for trip in trips)
    assert_no_station_data({"summary": summary, "trips": trips}, stations)
    for payload in (summary_bytes(summary), trips_bytes(trips)):
        text = payload.decode("utf-8")
        assert "epdk:" not in text
        assert "Synthetic Site" not in text


@pytest.mark.parametrize(
    "evidence",
    [
        {"node_ids": ["origin", "destination"]},
        {"nested": [{"latitude": 39.0}]},
        {"note": "synthetic:fast"},
        {"note": "Synthetic Fast Hub"},
        {"note": 39.123456},
    ],
)
def test_station_leak_guard_rejects_each_kind_of_station_value(evidence: dict[str, Any]) -> None:
    stations = dict(load_stored_graph(FIXTURE).stations)
    with pytest.raises(EvaluationDefectError):
        assert_no_station_data(evidence, stations)


def _gate_trip() -> dict[str, Any]:
    return {
        "experiment": "E1",
        "variant": "set",
        "route_id": "m6-r01",
        "results": {
            "fastest": {"outcome": "plan", "total_minutes": 100.0},
            "greedy_fixed_80": {"outcome": "plan", "total_minutes": 100.0},
        },
    }


def test_gate_accepts_exact_no_slower_than_greedy() -> None:
    check_gates([_gate_trip()])


def test_gate_treats_exact_slower_than_greedy_as_a_defect() -> None:
    trip = _gate_trip()
    trip["results"]["fastest"]["total_minutes"] = 100.1
    with pytest.raises(EvaluationDefectError, match="slower"):
        check_gates([trip])


def test_gate_treats_a_plan_without_an_exact_plan_as_a_defect() -> None:
    trip = copy.deepcopy(_gate_trip())
    trip["results"]["fastest"] = {"outcome": "infeasible"}
    with pytest.raises(EvaluationDefectError, match="fastest did not"):
        check_gates([trip])


def test_snapshot_loader_fails_closed_on_a_checksum_mismatch(tmp_path: Path) -> None:
    source = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
    snapshot_bytes = source.read_bytes()
    station_manifest = tmp_path / "manifest.json"
    station_manifest.write_text(
        json.dumps(
            {
                "snapshot_id": SNAPSHOT_ID,
                "source": "synthetic fixture",
                "source_url": "https://example.invalid/synthetic",
                "retrieved_at": "2026-11-05T00:00:00Z",
                "reuse_status": "pending_verification",
                "source_freshness": "unknown",
            }
        ),
        encoding="utf-8",
    )
    options = load_frozen_options(
        snapshot_path=source,
        station_manifest_path=station_manifest,
        frozen_snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        },
    )
    assert options
    with pytest.raises(ValueError, match="checksum"):
        load_frozen_options(
            snapshot_path=source,
            station_manifest_path=station_manifest,
            frozen_snapshot={"snapshot_id": SNAPSHOT_ID, "sha256": "0" * 64},
        )


def test_runtime_measurements_follow_the_declared_protocol(tmp_path: Path) -> None:
    manifest, graphs_dir, client = _collected(tmp_path)
    manifest["experiments"]["E6"]["warmups"] = 1
    manifest["experiments"]["E6"]["repetitions"] = 2

    planner = measure_planner_runtime(manifest=manifest, graphs_dir=graphs_dir)
    assert planner["trips_per_pass"] == 9
    assert set(planner["algorithms"]) == set(ALGORITHMS)
    assert all(len(row["samples_ms"]) == 2 for row in planner["algorithms"].values())

    route_calls, table_calls = client.route_calls, len(client.table_sizes)
    osrm = measure_osrm_runtime(manifest=manifest, client=client, graphs_dir=graphs_dir)
    assert osrm["requests_per_pass"] == 3
    assert len(osrm["route"]["samples_ms"]) == len(osrm["table"]["samples_ms"]) == 2
    assert client.route_calls - route_calls == 3 * 3
    assert len(client.table_sizes) - table_calls == 3 * 3


def _committed_evidence() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evidence_dir = REPOSITORY_ROOT / "docs/benchmarks/real-routes"
    summary: dict[str, Any] = json.loads(
        (evidence_dir / "summary_v1.json").read_text(encoding="utf-8")
    )
    lines = (evidence_dir / "trips_v1.jsonl").read_text(encoding="utf-8").splitlines()
    return summary, [json.loads(line) for line in lines]


def _real_manifest_bytes() -> bytes:
    return (REPOSITORY_ROOT / "data/benchmarks/real-routes/v1/manifest.json").read_bytes()


def test_committed_evidence_belongs_to_the_frozen_manifest() -> None:
    summary, trips = _committed_evidence()
    manifest_bytes = _real_manifest_bytes()
    manifest = json.loads(manifest_bytes)
    assert summary["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert summary["reconciliation"]["evaluated_trips"] == len(trips)
    assert len(trips) == len(enumerate_trips(manifest))
    assert [(t["experiment"], t["variant"], t["route_id"]) for t in trips] == [
        (trip.experiment, trip.variant, trip.route_id) for trip in enumerate_trips(manifest)
    ]
    assert len(summary["stored_graphs"]) == len(required_graph_keys(manifest))


def test_committed_evidence_reconciles_and_passes_every_gate() -> None:
    summary, trips = _committed_evidence()
    check_gates(trips)
    for algorithm in ALGORITHMS:
        counts = Counter(trip["results"][algorithm]["outcome"] for trip in trips)
        assert dict(counts) == {
            key: value
            for key, value in summary["reconciliation"]["outcomes_per_algorithm"][algorithm].items()
            if value
        }
    plans = [
        result
        for trip in trips
        for result in trip["results"].values()
        if result["outcome"] == "plan"
    ]
    assert plans
    assert all(result["reserve_violations"] == 0 for result in plans)


def test_committed_evidence_contains_no_station_data() -> None:
    summary, trips = _committed_evidence()
    assert_no_station_data({"summary": summary, "trips": trips}, {})
    for name in ("summary_v1.json", "trips_v1.jsonl", "runtime_2026-11-05.json"):
        text = (REPOSITORY_ROOT / "docs/benchmarks/real-routes" / name).read_text(encoding="utf-8")
        for forbidden in ("epdk:", "ŞRJ/", "sarjIstasyonu", "latitude", "longitude"):
            assert forbidden not in text
