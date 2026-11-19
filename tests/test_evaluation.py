import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chargepath import RoadLeg, RoutePlan, ShortestDrivingTimeBaseline, VehicleProfile, evaluation
from chargepath.evaluation import (
    PLAN_ALGORITHMS,
    REQUIRED_TOPOLOGY_CLASSES,
    build_correctness_artifact,
    evaluate_plan,
    load_benchmark_manifest,
)
from chargepath.evidence import canonical_json_bytes
from chargepath.providers import StaticRoadNetwork

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_MANIFEST = REPOSITORY_ROOT / "data/benchmarks/synthetic/v1/manifest.json"
SYNTHETIC_CORRECTNESS = REPOSITORY_ROOT / "docs/benchmarks/synthetic/correctness_v1.json"
SYNTHETIC_RUNTIME = REPOSITORY_ROOT / "docs/benchmarks/synthetic/runtime_2026-11-18.json"


def test_manifest_pins_fixture_and_covers_every_required_class() -> None:
    manifest = load_benchmark_manifest(SYNTHETIC_MANIFEST)

    assert len(manifest.cases) == len(REQUIRED_TOPOLOGY_CLASSES) == 8
    assert {case.topology_class for case in manifest.cases} == REQUIRED_TOPOLOGY_CLASSES
    assert {case.expected_feasibility_source["kind"] for case in manifest.cases} == {
        "hand_audited_witness",
        "hand_audited_exhaustion",
    }


def test_manifest_rejects_fixture_checksum_drift(tmp_path: Path) -> None:
    shutil.copy(SYNTHETIC_MANIFEST, tmp_path / "manifest.json")
    fixture_source = SYNTHETIC_MANIFEST.with_name("cases.json")
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


def test_committed_correctness_evidence_matches_regenerated_bytes() -> None:
    artifact = build_correctness_artifact(load_benchmark_manifest(SYNTHETIC_MANIFEST))

    assert SYNTHETIC_CORRECTNESS.read_bytes() == canonical_json_bytes(artifact)
    assert artifact["result_count"] == 8 * len(PLAN_ALGORITHMS)
    assert artifact["correctness_gates"] == {
        "all_returned_plans_verified": True,
        "reserve_violations": 0,
        "false_feasible_count": 0,
        "exact_algorithms_match_reference": True,
        "input_result_counts_reconcile": True,
    }


def test_committed_runtime_evidence_is_bound_to_manifest_and_protocol() -> None:
    manifest = load_benchmark_manifest(SYNTHETIC_MANIFEST)
    runtime = json.loads(SYNTHETIC_RUNTIME.read_text(encoding="utf-8"))

    assert runtime["manifest_sha256"] == manifest.manifest_sha256
    assert runtime["fixture_sha256"] == manifest.fixture_sha256
    assert runtime["case_count_per_sample"] == len(manifest.cases)
    assert runtime["protocol"]["warmup_count"] == 3
    assert runtime["protocol"]["measured_repetitions"] == 20
    assert runtime["protocol"]["network_access"] == "none"


def test_reserve_violations_are_counted_by_the_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    # A planner that ignores the reserve returns a plan arriving at 5%; the replay must count the
    # leg that ends below the 10% reserve rather than the evaluation writing a fixed zero.
    vehicle = VehicleProfile(
        name="Reserve EV",
        usable_battery_kwh=50,
        initial_soc_pct=90,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=100,
        reserve_soc_pct=10,
        energy_safety_factor=1.0,
    )
    network = StaticRoadNetwork([RoadLeg("origin", "destination", 212.5, 120)])

    def reserve_blind_planner(algorithm: str, **arguments: Any) -> RoutePlan:
        return ShortestDrivingTimeBaseline(soc_step_pct=arguments["soc_step_pct"]).optimize(
            origin_id=arguments["origin_id"],
            destination_id=arguments["destination_id"],
            vehicle=replace(arguments["vehicle"], reserve_soc_pct=0),
            road_network=arguments["road_network"],
        )

    monkeypatch.setattr(evaluation, "plan_route", reserve_blind_planner)
    plan, violations = evaluate_plan(
        "shortest_driving_time",
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations={},
        road_network=network,
        soc_step_pct=5,
    )
    assert plan.arrival_soc_pct == 5
    assert violations == 1
