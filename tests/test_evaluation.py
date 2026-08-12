import json
import shutil
from pathlib import Path

import pytest

from chargepath import RoadLeg, ShortestDrivingTimeBaseline, VehicleProfile
from chargepath.evaluation import (
    PLAN_ALGORITHMS,
    REQUIRED_TOPOLOGY_CLASSES,
    build_correctness_artifact,
    build_runtime_artifact,
    canonical_json_bytes,
    load_benchmark_manifest,
)
from chargepath.providers import StaticRoadNetwork

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
M3_MANIFEST = REPOSITORY_ROOT / "data/benchmarks/m3/v1/manifest.json"
M3_CORRECTNESS = REPOSITORY_ROOT / "docs/evidence/m3/correctness_v1.json"
M3_RUNTIME = REPOSITORY_ROOT / "docs/evidence/m3/runtime_2026-08-09.json"


def test_manifest_pins_fixture_and_covers_every_required_class() -> None:
    manifest = load_benchmark_manifest(M3_MANIFEST)

    assert manifest.benchmark_id == "chargepath-m3-synthetic-v1"
    assert len(manifest.fixture_sha256) == 64
    assert len(manifest.cases) == len(REQUIRED_TOPOLOGY_CLASSES) == 8
    assert {case.topology_class for case in manifest.cases} == REQUIRED_TOPOLOGY_CLASSES
    assert {case.expected_feasibility_source["kind"] for case in manifest.cases} == {
        "hand_audited_witness",
        "hand_audited_exhaustion",
    }


def test_manifest_rejects_fixture_checksum_drift(tmp_path: Path) -> None:
    shutil.copy(M3_MANIFEST, tmp_path / "manifest.json")
    fixture_source = M3_MANIFEST.with_name("cases.json")
    fixture_payload = json.loads(fixture_source.read_text(encoding="utf-8"))
    fixture_payload["description"] = "tampered"
    (tmp_path / "cases.json").write_text(json.dumps(fixture_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="fixture checksum mismatch"):
        load_benchmark_manifest(tmp_path / "manifest.json")


def test_shortest_driving_time_baseline_uses_full_node_path_tie_break() -> None:
    vehicle = VehicleProfile(
        name="Tie EV",
        usable_battery_kwh=80,
        initial_soc_pct=100,
        consumption_kwh_per_100km=15,
        max_dc_power_kw=150,
        reserve_soc_pct=10,
        energy_safety_factor=1,
    )
    network = StaticRoadNetwork(
        [
            RoadLeg("origin", "charlie", 40, 20),
            RoadLeg("charlie", "destination", 40, 20),
            RoadLeg("origin", "bravo", 40, 20),
            RoadLeg("bravo", "destination", 40, 20),
        ]
    )

    plan = ShortestDrivingTimeBaseline().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        road_network=network,
    )

    assert plan.node_ids == ("origin", "bravo", "destination")


def test_correctness_artifact_reconciles_and_passes_reference_gates() -> None:
    manifest = load_benchmark_manifest(M3_MANIFEST)
    artifact = build_correctness_artifact(manifest)

    assert artifact["case_count"] == 8
    assert artifact["result_count"] == 8 * len(PLAN_ALGORITHMS)
    assert artifact["correctness_gates"] == {
        "all_returned_plans_verified": True,
        "reserve_violations": 0,
        "false_feasible_count": 0,
        "exact_algorithms_match_reference": True,
        "input_result_counts_reconcile": True,
    }
    for summary in artifact["algorithm_summaries"].values():
        assert summary["returned_plans"] == summary["verified_plans"]
        assert summary["reference_infeasible_returned"] == 0


def test_normalized_correctness_serialization_is_byte_stable() -> None:
    manifest = load_benchmark_manifest(M3_MANIFEST)

    first = canonical_json_bytes(build_correctness_artifact(manifest))
    second = canonical_json_bytes(build_correctness_artifact(manifest))

    assert first == second


def test_committed_correctness_evidence_matches_regenerated_bytes() -> None:
    manifest = load_benchmark_manifest(M3_MANIFEST)

    regenerated = canonical_json_bytes(build_correctness_artifact(manifest))

    assert M3_CORRECTNESS.read_bytes() == regenerated


def test_committed_runtime_evidence_is_bound_to_manifest_and_protocol() -> None:
    manifest = load_benchmark_manifest(M3_MANIFEST)
    runtime = json.loads(M3_RUNTIME.read_text(encoding="utf-8"))

    assert runtime["manifest_sha256"] == manifest.manifest_sha256
    assert runtime["fixture_sha256"] == manifest.fixture_sha256
    assert runtime["case_count_per_sample"] == len(manifest.cases)
    assert runtime["protocol"]["warmup_count"] == 3
    assert runtime["protocol"]["measured_repetitions"] == 20
    assert runtime["protocol"]["network_access"] == "none"


def test_soc_grid_sensitivity_changes_only_resolution_and_exposes_boundary() -> None:
    artifact = build_correctness_artifact(load_benchmark_manifest(M3_MANIFEST))
    sensitivity = artifact["soc_grid_sensitivity"][0]
    runs = sensitivity["runs"]

    assert sensitivity["factor"] == "soc_step_pct"
    assert [run["soc_step_pct"] for run in runs] == [10, 5, 2]
    assert [run["feasible"] for run in runs] == [False, False, True]
    assert runs[2]["plan"]["arrival_soc_pct"] == 12


def test_pruning_audit_compares_against_unpruned_small_graph() -> None:
    artifact = build_correctness_artifact(load_benchmark_manifest(M3_MANIFEST))
    audit = artifact["pruning_audits"][0]

    assert audit["unpruned_candidate_ids"] == ["fast_near", "slow_far"]
    assert audit["pruned_candidate_ids"] == ["fast_near"]
    assert audit["unpruned"]["feasible"] is True
    assert audit["pruned"]["feasible"] is True
    assert audit["same_plan"] is True
    assert audit["pruning_time_gap_minutes"] == 0


def test_runtime_artifact_declares_protocol_environment_median_and_p95() -> None:
    artifact = build_runtime_artifact(
        load_benchmark_manifest(M3_MANIFEST),
        warmup_count=0,
        measured_repetitions=2,
        command="test protocol",
        generated_at_utc="2026-08-09T00:00:00Z",
    )

    assert artifact["protocol"]["warmup_count"] == 0
    assert artifact["protocol"]["measured_repetitions"] == 2
    assert artifact["protocol"]["clock"] == "time.perf_counter_ns"
    assert artifact["environment"]["python_version"]
    assert artifact["environment"]["platform"]
    for result in artifact["algorithm_results"]:
        assert result["median_ms"] >= 0
        assert result["p95_ms"] >= result["median_ms"]
        assert len(result["measured_samples_ms"]) == 2
