"""Versioned, offline synthetic benchmark loading and result generation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from chargepath import validation
from chargepath.alternatives import (
    CompetitiveRoutePlanner,
    GreedyFixedTargetOptimizer,
)
from chargepath.baselines import ShortestDrivingTimeBaseline
from chargepath.benchmarks.evidence import nearest_rank, time_passes
from chargepath.corridor import CandidateSelectionConfig, select_corridor_candidates
from chargepath.fixtures import leg_from_json, station_from_json, vehicle_from_json
from chargepath.models import (
    GeoJsonLineString,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
)
from chargepath.optimizer import EVRouteOptimizer, RoutePreference
from chargepath.providers import StaticRoadNetwork
from chargepath.station_data import CompatibleStationOption, SnapshotProvenance
from chargepath.verification import replay_route_plan

REQUIRED_TOPOLOGY_CLASSES = frozenset(
    {
        "direct_feasible",
        "one_stop",
        "multi_stop",
        "detour_choice",
        "connector_incompatible",
        "exact_reserve_boundary",
        "discretization_rejected",
        "infeasible",
    }
)
EXACT_ALGORITHMS = ("fastest", "shortest_distance", "fewest_charging_stops")
# Strategies that can return a charging plan; the road-only baseline cannot.
OPTION_ALGORITHMS = (*EXACT_ALGORITHMS, "greedy_fixed_80")
PLAN_ALGORITHMS = ("shortest_driving_time", *OPTION_ALGORITHMS)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    topology_class: str
    soc_step_pct: int
    expected_feasible: bool
    expected_feasibility_source: dict[str, Any]
    origin_id: str
    destination_id: str
    vehicle: VehicleProfile
    stations: tuple[Station, ...]
    legs: tuple[RoadLeg, ...]

    def station_map(self) -> dict[str, Station]:
        return {station.id: station for station in self.stations}


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    benchmark_id: str
    algorithm_version: str
    result_serialization_version: int
    manifest_sha256: str
    fixture_sha256: str
    cases: tuple[BenchmarkCase, ...]
    sensitivity_runs: tuple[dict[str, Any], ...]
    pruning_audits: tuple[dict[str, Any], ...]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_benchmark_manifest(path: str | Path) -> BenchmarkManifest:
    """Load the benchmark manifest, verify fixture identity, and construct typed cases."""

    manifest_path = Path(path).resolve()
    payload = validation.json_object(
        json.loads(manifest_path.read_text(encoding="utf-8")), field="manifest"
    )
    if payload.get("manifest_version") != 1:
        raise ValueError("unsupported benchmark manifest_version")
    fixture = validation.json_object(payload.get("fixture"), field="fixture")
    fixture_path = (
        manifest_path.parent / validation.text(fixture.get("path"), field="fixture.path")
    ).resolve()
    if fixture_path.parent != manifest_path.parent:
        raise ValueError("benchmark fixture must be adjacent to its manifest")
    expected_fixture_sha = validation.text(fixture.get("sha256"), field="fixture.sha256").lower()
    actual_fixture_sha = _sha256(fixture_path)
    if actual_fixture_sha != expected_fixture_sha:
        raise ValueError(
            f"benchmark fixture checksum mismatch: expected {expected_fixture_sha}, "
            f"got {actual_fixture_sha}"
        )
    if fixture.get("schema_version") != 1 or fixture.get("synthetic") is not True:
        raise ValueError(
            "benchmark fixture metadata must declare schema version 1 and synthetic=true"
        )

    fixture_payload = validation.json_object(
        json.loads(fixture_path.read_text(encoding="utf-8")), field="fixture root"
    )
    if fixture_payload.get("schema_version") != 1 or fixture_payload.get("synthetic") is not True:
        raise ValueError("benchmark case fixture must declare schema version 1 and synthetic=true")
    fixture_cases = {
        validation.text(case_row.get("id"), field="fixture cases[].id"): case_row
        for case_row in (
            validation.json_object(value, field="fixture cases[]")
            for value in validation.json_array(fixture_payload.get("cases"), field="fixture cases")
        )
    }
    manifest_rows = [
        validation.json_object(value, field="manifest cases[]")
        for value in validation.json_array(payload.get("cases"), field="manifest cases")
    ]
    manifest_ids = [
        validation.text(row.get("id"), field="manifest cases[].id") for row in manifest_rows
    ]
    if len(set(manifest_ids)) != len(manifest_ids):
        raise ValueError("benchmark manifest case ids must be unique")
    if set(manifest_ids) != set(fixture_cases):
        raise ValueError("benchmark manifest and fixture case ids must match exactly")

    cases = tuple(
        _load_case(row, fixture_cases[row_id])
        for row, row_id in zip(manifest_rows, manifest_ids, strict=True)
    )
    topology_classes = {case.topology_class for case in cases}
    missing_topologies = sorted(REQUIRED_TOPOLOGY_CLASSES - topology_classes)
    if missing_topologies:
        raise ValueError(f"benchmark manifest is missing topology classes: {missing_topologies}")

    # The algorithm list, greedy contract and grid values the manifest records are fixed by the
    # committed file, whose checksum the correctness artifact pins; they are not re-checked here.
    sensitivity_runs = tuple(
        validation.json_object(value, field="sensitivity_runs[]")
        for value in validation.json_array(
            payload.get("sensitivity_runs"), field="sensitivity_runs"
        )
    )
    for run in sensitivity_runs:
        if run.get("fixed_input_fixture_sha256") != actual_fixture_sha:
            raise ValueError("sensitivity run fixture checksum must match the fixed case fixture")
        if run.get("case_id") not in fixture_cases:
            raise ValueError("sensitivity run references an unknown case")

    pruning_audits = tuple(
        validation.json_object(value, field="pruning_audits[]")
        for value in validation.json_array(payload.get("pruning_audits"), field="pruning_audits")
    )
    if not pruning_audits:
        raise ValueError("benchmark manifest must define at least one pruning audit")
    for audit in pruning_audits:
        if audit.get("case_id") not in fixture_cases:
            raise ValueError("pruning audit references an unknown case")

    return BenchmarkManifest(
        benchmark_id=validation.text(payload.get("benchmark_id"), field="benchmark_id"),
        algorithm_version=validation.text(
            payload.get("algorithm_version"), field="algorithm_version"
        ),
        result_serialization_version=validation.integer(
            payload.get("result_serialization_version"), field="result_serialization_version"
        ),
        manifest_sha256=_sha256(manifest_path),
        fixture_sha256=actual_fixture_sha,
        cases=cases,
        sensitivity_runs=sensitivity_runs,
        pruning_audits=pruning_audits,
    )


def _load_case(metadata: dict[str, Any], inputs: dict[str, Any]) -> BenchmarkCase:
    case_id = validation.text(metadata.get("id"), field="case.id")
    if inputs.get("id") != case_id:
        raise ValueError("benchmark case metadata and fixture id differ")
    vehicle = vehicle_from_json(inputs.get("vehicle"), field=f"{case_id}.vehicle")
    stations = tuple(
        station_from_json(value, field=f"{case_id}.stations[]")
        for value in validation.json_array(inputs.get("stations"), field=f"{case_id}.stations")
    )
    if len({station.id for station in stations}) != len(stations):
        raise ValueError(f"{case_id} station ids must be unique")
    legs = tuple(
        leg_from_json(value, field=f"{case_id}.legs[]", with_geometry=False)
        for value in validation.json_array(inputs.get("legs"), field=f"{case_id}.legs")
    )
    source = validation.json_object(
        metadata.get("expected_feasibility_source"),
        field=f"{case_id}.expected_feasibility_source",
    )
    source_kind = source.get("kind")
    expected_feasible = validation.boolean(
        metadata.get("expected_feasible"), field="expected_feasible"
    )
    if expected_feasible and source_kind != "hand_audited_witness":
        raise ValueError(f"{case_id} feasible reference must have a hand-audited witness")
    if not expected_feasible and source_kind != "hand_audited_exhaustion":
        raise ValueError(f"{case_id} infeasible reference must have a hand-audited exhaustion")
    validation.text(source.get("reason"), field=f"{case_id}.expected_feasibility_source.reason")
    if expected_feasible:
        witness = tuple(
            validation.text(value, field=f"{case_id}.expected_feasibility_source.node_ids[]")
            for value in validation.json_array(
                source.get("node_ids"),
                field=f"{case_id}.expected_feasibility_source.node_ids",
            )
        )
        if witness[0] != inputs.get("origin_id") or witness[-1] != inputs.get("destination_id"):
            raise ValueError(f"{case_id} witness endpoints do not match the trip")
    return BenchmarkCase(
        id=case_id,
        topology_class=validation.text(metadata.get("topology_class"), field="topology_class"),
        soc_step_pct=validation.integer(metadata.get("soc_step_pct"), field="soc_step_pct"),
        expected_feasible=expected_feasible,
        expected_feasibility_source=source,
        origin_id=validation.text(inputs.get("origin_id"), field=f"{case_id}.origin_id"),
        destination_id=validation.text(
            inputs.get("destination_id"), field=f"{case_id}.destination_id"
        ),
        vehicle=vehicle,
        stations=stations,
        legs=legs,
    )


def _rounded(value: float) -> float:
    return round(value, 6)


def _serialize_plan(plan: RoutePlan) -> dict[str, Any]:
    return {
        "node_ids": list(plan.node_ids),
        "total_distance_km": _rounded(plan.total_distance_km),
        "driving_minutes": _rounded(plan.driving_minutes),
        "charging_minutes": _rounded(plan.charging_minutes),
        "total_minutes": _rounded(plan.total_minutes),
        "arrival_soc_pct": _rounded(plan.arrival_soc_pct),
        "charging_stops": [
            {
                "station_id": stop.station_id,
                "arrival_soc_pct": _rounded(stop.arrival_soc_pct),
                "departure_soc_pct": _rounded(stop.departure_soc_pct),
                "energy_added_kwh": _rounded(stop.energy_added_kwh),
                "charging_minutes": _rounded(stop.charging_minutes),
            }
            for stop in plan.charging_stops
        ],
    }


def plan_route(
    algorithm: str,
    *,
    origin_id: str,
    destination_id: str,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    road_network: StaticRoadNetwork,
    soc_step_pct: int,
) -> RoutePlan:
    """Run one benchmark algorithm; the road-only baseline ignores the stations."""

    if algorithm == "shortest_driving_time":
        return ShortestDrivingTimeBaseline(soc_step_pct=soc_step_pct).optimize(
            origin_id=origin_id,
            destination_id=destination_id,
            vehicle=vehicle,
            road_network=road_network,
        )
    optimizer: EVRouteOptimizer | GreedyFixedTargetOptimizer
    if algorithm == "greedy_fixed_80":
        optimizer = GreedyFixedTargetOptimizer(soc_step_pct=soc_step_pct)
    elif algorithm in EXACT_ALGORITHMS:
        optimizer = EVRouteOptimizer(
            preference=RoutePreference(algorithm), soc_step_pct=soc_step_pct
        )
    else:
        raise ValueError(f"unsupported benchmark algorithm: {algorithm}")
    return optimizer.optimize(
        origin_id=origin_id,
        destination_id=destination_id,
        vehicle=vehicle,
        stations=stations,
        road_network=road_network,
    )


def evaluate_plan(
    algorithm: str,
    *,
    origin_id: str,
    destination_id: str,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    road_network: StaticRoadNetwork,
    soc_step_pct: int,
) -> tuple[RoutePlan, int]:
    """Plan one synthetic or frozen real-route case and replay it independently.

    Returns the plan and the replay's count of legs that went below the reserve. Infeasibility and
    other replay defects propagate so each report keeps its own outcome format.
    """
    plan = plan_route(
        algorithm,
        origin_id=origin_id,
        destination_id=destination_id,
        vehicle=vehicle,
        stations=stations,
        road_network=road_network,
        soc_step_pct=soc_step_pct,
    )
    replay = replay_route_plan(
        plan,
        vehicle=vehicle,
        stations=stations,
        soc_step_pct=soc_step_pct,
        road_network=road_network,
        origin_id=origin_id,
        destination_id=destination_id,
    )
    return plan, replay.reserve_violations


def _run_algorithm(
    case: BenchmarkCase, algorithm: str, *, soc_step_pct: int | None = None
) -> dict[str, Any]:
    step = case.soc_step_pct if soc_step_pct is None else soc_step_pct
    try:
        plan, reserve_violations = evaluate_plan(
            algorithm,
            origin_id=case.origin_id,
            destination_id=case.destination_id,
            vehicle=case.vehicle,
            stations=case.station_map(),
            road_network=StaticRoadNetwork(case.legs),
            soc_step_pct=step,
        )
    except NoFeasibleRouteError as exc:
        return {"algorithm": algorithm, "feasible": False, "reason": str(exc)}
    return {
        "algorithm": algorithm,
        "feasible": True,
        "verified": True,
        "reserve_violations": reserve_violations,
        "plan": _serialize_plan(plan),
    }


def _option_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {}
    plans: dict[str, dict[str, Any]] = {}
    unavailable: list[str] = []
    for run in runs:
        algorithm = str(run["algorithm"])
        if algorithm not in OPTION_ALGORITHMS:
            continue
        if not run["feasible"]:
            unavailable.append(algorithm)
            continue
        plan = validation.json_object(run["plan"], field="run.plan")
        key = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        grouped.setdefault(key, []).append(algorithm)
        plans[key] = plan
    return {
        "distinct_option_count": len(grouped),
        "options": [{"strategy_aliases": grouped[key], "plan": plans[key]} for key in grouped],
        "unavailable_strategies": unavailable,
    }


def build_correctness_artifact(manifest: BenchmarkManifest) -> dict[str, Any]:
    """Run deterministic comparison, SOC-grid sensitivity, and pruning evidence."""

    case_results: list[dict[str, Any]] = []
    for case in manifest.cases:
        runs = [_run_algorithm(case, algorithm) for algorithm in PLAN_ALGORITHMS]
        case_results.append(
            {
                "case_id": case.id,
                "topology_class": case.topology_class,
                "soc_step_pct": case.soc_step_pct,
                "expected_feasible": case.expected_feasible,
                "expected_feasibility_source": case.expected_feasibility_source,
                "runs": runs,
                "route_options": _option_summary(runs),
            }
        )

    summaries: dict[str, dict[str, int]] = {}
    for algorithm in PLAN_ALGORITHMS:
        algorithm_runs = [
            next(run for run in case_result["runs"] if run["algorithm"] == algorithm)
            for case_result in case_results
        ]
        summaries[algorithm] = {
            "returned_plans": sum(bool(run["feasible"]) for run in algorithm_runs),
            "verified_plans": sum(bool(run.get("verified")) for run in algorithm_runs),
            "reference_feasible_returned": sum(
                bool(run["feasible"]) and case.expected_feasible
                for run, case in zip(algorithm_runs, manifest.cases, strict=True)
            ),
            "reference_infeasible_returned": sum(
                bool(run["feasible"]) and not case.expected_feasible
                for run, case in zip(algorithm_runs, manifest.cases, strict=True)
            ),
        }

    sensitivity_results = []
    cases_by_id = {case.id: case for case in manifest.cases}
    for sensitivity in manifest.sensitivity_runs:
        case = cases_by_id[str(sensitivity["case_id"])]
        sensitivity_results.append(
            {
                "id": sensitivity["id"],
                "case_id": case.id,
                "factor": sensitivity["factor"],
                "fixed_input_fixture_sha256": sensitivity["fixed_input_fixture_sha256"],
                "runs": [
                    {
                        "soc_step_pct": step,
                        **_run_algorithm(case, "fastest", soc_step_pct=step),
                    }
                    for step in sensitivity["values"]
                ],
            }
        )

    pruning_results = [
        _run_pruning_audit(audit, cases_by_id[str(audit["case_id"])])
        for audit in manifest.pruning_audits
    ]
    artifact = {
        "artifact_version": 1,
        "benchmark_id": manifest.benchmark_id,
        "algorithm_version": manifest.algorithm_version,
        "result_serialization_version": manifest.result_serialization_version,
        "manifest_sha256": manifest.manifest_sha256,
        "fixture_sha256": manifest.fixture_sha256,
        "synthetic": True,
        "case_count": len(manifest.cases),
        "result_count": sum(len(case["runs"]) for case in case_results),
        "case_results": case_results,
        "algorithm_summaries": summaries,
        "soc_grid_sensitivity": sensitivity_results,
        "pruning_audits": pruning_results,
        "correctness_gates": {
            "all_returned_plans_verified": all(
                not run["feasible"] or run.get("verified") is True
                for case in case_results
                for run in case["runs"]
            ),
            "reserve_violations": sum(
                int(run.get("reserve_violations", 0))
                for case in case_results
                for run in case["runs"]
            ),
            "false_feasible_count": sum(
                bool(run["feasible"]) and not case.expected_feasible
                for case_result, case in zip(case_results, manifest.cases, strict=True)
                for run in case_result["runs"]
            ),
            "exact_algorithms_match_reference": all(
                bool(run["feasible"]) == case.expected_feasible
                for case_result, case in zip(case_results, manifest.cases, strict=True)
                for run in case_result["runs"]
                if run["algorithm"] in EXACT_ALGORITHMS
            ),
            "input_result_counts_reconcile": (
                len(manifest.cases) * len(PLAN_ALGORITHMS)
                == sum(len(case["runs"]) for case in case_results)
            ),
        },
    }
    assert_correctness_gates(artifact)
    return artifact


def _run_pruning_audit(audit: dict[str, Any], case: BenchmarkCase) -> dict[str, Any]:
    geometry_row = validation.json_object(
        audit.get("route_geometry"), field="pruning route_geometry"
    )
    positions = (
        validation.json_array(value, field="route coordinate")
        for value in validation.json_array(
            geometry_row.get("coordinates"), field="route coordinates"
        )
    )
    geometry = GeoJsonLineString(tuple((float(lon), float(lat)) for lon, lat in positions))
    config_row = validation.json_object(audit.get("selection_config"), field="selection_config")
    config = CandidateSelectionConfig(
        corridor_width_km=float(config_row["corridor_width_km"]),
        candidate_cap=validation.integer(config_row["candidate_cap"], field="candidate_cap"),
        algorithm=validation.text(config_row["algorithm"], field="selection algorithm"),
        ranking_keys=tuple(
            validation.text(value, field="ranking_keys[]")
            for value in validation.json_array(config_row.get("ranking_keys"), field="ranking_keys")
        ),
    )
    provenance = SnapshotProvenance(
        snapshot_id="synthetic-m3-pruning-v1",
        source_name="ChargePath synthetic benchmark",
        source_url="https://example.invalid/chargepath-synthetic-m3",
        retrieved_at="2026-08-09T00:00:00Z",
        response_sha256="0" * 64,
        reuse_status="approved",
        source_freshness="synthetic_not_applicable",
    )
    options = tuple(
        CompatibleStationOption(
            station=station,
            site_source_id=station.id,
            socket_source_ids=(f"{station.id}:synthetic-ccs2",),
            provenance=provenance,
        )
        for station in case.stations
    )
    selection = select_corridor_candidates(
        route_geometry=geometry,
        options=options,
        config=config,
    )
    selected_ids = set(selection.station_ids)
    pruned_legs = tuple(
        leg
        for leg in case.legs
        if (leg.origin_id not in case.station_map() or leg.origin_id in selected_ids)
        and (leg.destination_id not in case.station_map() or leg.destination_id in selected_ids)
    )
    unpruned = _run_algorithm(case, "fastest")
    pruned_case = replace(
        case,
        stations=tuple(station for station in case.stations if station.id in selected_ids),
        legs=pruned_legs,
    )
    pruned = _run_algorithm(pruned_case, "fastest")
    time_gap: float | None = None
    same_plan = False
    if unpruned["feasible"] and pruned["feasible"]:
        unpruned_plan = validation.json_object(unpruned["plan"], field="unpruned plan")
        pruned_plan = validation.json_object(pruned["plan"], field="pruned plan")
        time_gap = _rounded(
            float(pruned_plan["total_minutes"]) - float(unpruned_plan["total_minutes"])
        )
        same_plan = unpruned_plan == pruned_plan
    return {
        "id": audit["id"],
        "case_id": case.id,
        "unpruned_candidate_ids": [station.id for station in case.stations],
        "eligible_candidate_count": selection.eligible_count,
        "pruned_candidate_ids": list(selection.station_ids),
        "selection_config": config.to_manifest(),
        "unpruned": unpruned,
        "pruned": pruned,
        "pruning_time_gap_minutes": time_gap,
        "same_plan": same_plan,
        "scope": "small synthetic candidate graph; no general preservation claim",
    }


def assert_correctness_gates(artifact: dict[str, Any]) -> None:
    gates = validation.json_object(artifact.get("correctness_gates"), field="correctness_gates")
    failed = [
        name
        for name, value in gates.items()
        if (isinstance(value, bool) and not value)
        or (isinstance(value, int) and not isinstance(value, bool) and value != 0)
    ]
    if failed:
        raise RuntimeError(f"Synthetic benchmark correctness gates failed: {failed}")


def _run_suite_once(manifest: BenchmarkManifest, algorithm: str) -> None:
    if algorithm == "competitive_options":
        for case in manifest.cases:
            try:
                CompetitiveRoutePlanner(soc_step_pct=case.soc_step_pct).plan(
                    origin_id=case.origin_id,
                    destination_id=case.destination_id,
                    vehicle=case.vehicle,
                    stations=case.station_map(),
                    road_network=StaticRoadNetwork(case.legs),
                )
            except NoFeasibleRouteError:
                pass
        return
    for case in manifest.cases:
        _run_algorithm(case, algorithm)


def build_runtime_artifact(
    manifest: BenchmarkManifest,
    *,
    warmup_count: int,
    measured_repetitions: int,
    command: str,
) -> dict[str, Any]:
    """Measure complete offline benchmark passes with a declared warm-up protocol."""

    algorithm_results: list[dict[str, Any]] = []
    for algorithm in (*PLAN_ALGORITHMS, "competitive_options"):
        durations_ms = time_passes(
            partial(_run_suite_once, manifest, algorithm),
            warmups=warmup_count,
            repetitions=measured_repetitions,
        )
        algorithm_results.append(
            {
                "algorithm": algorithm,
                "median_ms": _rounded(statistics.median(durations_ms)),
                "p95_ms": _rounded(nearest_rank(durations_ms, 0.95)),
                "measured_samples_ms": [_rounded(value) for value in durations_ms],
            }
        )
    return {
        "artifact_version": 1,
        "benchmark_id": manifest.benchmark_id,
        "algorithm_version": manifest.algorithm_version,
        "manifest_sha256": manifest.manifest_sha256,
        "fixture_sha256": manifest.fixture_sha256,
        "synthetic": True,
        "protocol": {
            "command": command,
            "clock": "time.perf_counter_ns",
            "warmup_count": warmup_count,
            "measured_repetitions": measured_repetitions,
            "aggregation": "median and nearest-rank p95 over complete eight-case suite passes",
            "network_access": "none",
        },
        "environment": {
            "generated_at_utc": datetime.now(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine() or "unknown",
            "processor": platform.processor() or "unknown",
            "logical_cpu_count": os.cpu_count(),
        },
        "case_count_per_sample": len(manifest.cases),
        "algorithm_results": algorithm_results,
    }
