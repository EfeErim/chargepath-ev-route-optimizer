from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from chargepath.benchmarks.evidence import canonical_json_bytes
from chargepath.benchmarks.real_route_benchmark import Province, SelectedRoute, build_manifest
from chargepath.benchmarks.real_route_collection import (
    GraphKey,
    collect_graphs,
    load_frozen_options,
    load_stored_graph,
    load_verified_graph,
    required_graph_keys,
)
from chargepath.benchmarks.real_route_evaluation import (
    EvaluationDefectError,
    assert_no_station_data,
    check_gates,
    enumerate_trips,
    evaluate,
    run_trip_on_graph,
    trips_bytes,
)
from chargepath.models import GeoJsonLineString, Station, VehicleProfile
from chargepath.providers.base import CoordinateLike
from chargepath.providers.osrm import (
    OsrmResponseError,
    OsrmTableCost,
    OsrmTableResult,
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


# Route m6-r02 fails at the provider, which also keeps the long route's planning out of these two
# evaluations; the fixture graph test above plans a full graph.
@pytest.fixture(scope="module")
def failing_collection(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    manifest, graphs_dir, _ = _collected(tmp_path_factory.mktemp("failing"), failing_route=36.0)
    return manifest, graphs_dir


@pytest.fixture(scope="module")
def failing_evaluation(
    failing_collection: tuple[dict[str, Any], Path],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, graphs_dir = failing_collection
    return evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)


def test_provider_failure_is_an_outcome_and_counts_reconcile(
    failing_collection: tuple[dict[str, Any], Path],
    failing_evaluation: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    graphs_dir = failing_collection[1]
    stored = json.loads(
        (graphs_dir / GraphKey("m6-r02", 25, 50).file_name).read_text(encoding="utf-8")
    )
    assert stored["outcome"] == "provider_failure"
    assert stored["error_code"] == "NoRoute"
    assert "url" not in json.dumps(stored).lower()

    summary, trips = failing_evaluation
    reconciliation = summary["reconciliation"]
    assert reconciliation["evaluated_trips"] == reconciliation["expected_trips"] == len(trips)
    for algorithm in ALGORITHMS:
        counts = reconciliation["outcomes_per_algorithm"][algorithm]
        assert sum(counts.values()) == len(trips)
        assert counts["provider_failure"] == sum(trip["route_id"] == "m6-r02" for trip in trips)


def test_evaluation_is_byte_stable_and_records_every_graph_checksum(
    failing_collection: tuple[dict[str, Any], Path],
    failing_evaluation: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    manifest, graphs_dir = failing_collection
    first = failing_evaluation
    second = evaluate(manifest=manifest, graphs_dir=graphs_dir, manifest_sha256="0" * 64)
    assert canonical_json_bytes(first[0]) == canonical_json_bytes(second[0])
    assert trips_bytes(first[1]) == trips_bytes(second[1])
    stored = first[0]["stored_graphs"]
    assert set(stored) == {key.file_name for key in required_graph_keys(manifest)}
    for name, record in stored.items():
        assert record["sha256"] == hashlib.sha256((graphs_dir / name).read_bytes()).hexdigest()


def test_station_leak_guard_rejects_ids_names_and_coordinates() -> None:
    stations = dict(load_stored_graph(FIXTURE).stations)
    for evidence in (
        {"node_ids": ["origin", "destination"]},
        {"note": "Synthetic Fast Hub"},
        {"note": 39.123456},
    ):
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


def test_gates_reject_an_exact_plan_slower_than_greedy_or_missing() -> None:
    check_gates([_gate_trip()])
    slower = _gate_trip()
    slower["results"]["fastest"]["total_minutes"] = 100.1
    with pytest.raises(EvaluationDefectError, match="slower"):
        check_gates([slower])
    missing = _gate_trip()
    missing["results"]["fastest"] = {"outcome": "infeasible"}
    with pytest.raises(EvaluationDefectError, match="fastest did not"):
        check_gates([missing])


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


def _committed_evidence() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evidence_dir = REPOSITORY_ROOT / "docs/benchmarks/real-routes"
    summary: dict[str, Any] = json.loads(
        (evidence_dir / "summary_v1.json").read_text(encoding="utf-8")
    )
    lines = (evidence_dir / "trips_v1.jsonl").read_text(encoding="utf-8").splitlines()
    return summary, [json.loads(line) for line in lines]


def test_committed_evidence_belongs_to_the_frozen_manifest_and_passes_every_gate() -> None:
    summary, trips = _committed_evidence()
    manifest_bytes = (REPOSITORY_ROOT / "data/benchmarks/real-routes/v1/manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert summary["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert [(t["experiment"], t["variant"], t["route_id"]) for t in trips] == [
        (trip.experiment, trip.variant, trip.route_id) for trip in enumerate_trips(manifest)
    ]
    assert len(summary["stored_graphs"]) == len(required_graph_keys(manifest))
    check_gates(trips)
    for algorithm in ALGORITHMS:
        counts = Counter(trip["results"][algorithm]["outcome"] for trip in trips)
        declared = summary["reconciliation"]["outcomes_per_algorithm"][algorithm]
        assert dict(counts) == {key: value for key, value in declared.items() if value}


def test_committed_evidence_contains_no_station_data() -> None:
    summary, trips = _committed_evidence()
    assert_no_station_data({"summary": summary, "trips": trips}, {})
    real_routes = REPOSITORY_ROOT / "docs/benchmarks/real-routes"
    runtimes = sorted(real_routes.glob("runtime_*.json"))
    assert len(runtimes) >= 2
    for path in [real_routes / "summary_v1.json", real_routes / "trips_v1.jsonl", *runtimes]:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("epdk:", "ŞRJ/", "sarjIstasyonu", "latitude", "longitude"):
            assert forbidden not in text


def test_verified_graph_loading_checks_bytes_and_outcome(tmp_path: Path) -> None:
    expected = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert load_verified_graph(FIXTURE, expected_sha256=expected) == load_stored_graph(FIXTURE)
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_verified_graph(FIXTURE, expected_sha256="0" * 64)
    failed = tmp_path / "failed.json"
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["outcome"] = "provider_failure"
    failed.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="missing graph"):
        load_verified_graph(failed, expected_sha256=hashlib.sha256(failed.read_bytes()).hexdigest())
