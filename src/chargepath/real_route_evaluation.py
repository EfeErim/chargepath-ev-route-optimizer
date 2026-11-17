"""Real-route evaluation stage: offline strategy runs over stored candidate graphs.

This stage makes no network call. Committed evidence holds metrics, frozen-input identifiers, and
checksums, never a station name, coordinate, source identifier, or per-trip station sequence.
"""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Any

from chargepath.baselines import ShortestDrivingTimeBaseline
from chargepath.evaluation import (
    OPTION_ALGORITHMS,
    evaluate_plan,
    nearest_rank,
    plan_route,
    time_passes,
)
from chargepath.models import NoFeasibleRouteError, RoutePlan, Station, VehicleProfile
from chargepath.providers.static import StaticRoadNetwork
from chargepath.real_route_collection import (
    GraphKey,
    StoredGraph,
    load_stored_graph,
    load_verified_graph,
    timing_summary,
)

FASTEST = "fastest"
GREEDY = "greedy_fixed_80"
GAP_REFERENCES = {"E2": 2, "E3": 200, "E4": 50}
MINUTE_TOLERANCE = 1e-6
FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "latitude",
        "longitude",
        "name",
        "node_ids",
        "site_source_id",
        "socket_source_ids",
        "station_id",
        "stations",
    }
)
_VEHICLE_FIELDS = (
    "usable_battery_kwh",
    "initial_soc_pct",
    "consumption_kwh_per_100km",
    "max_dc_power_kw",
    "reserve_soc_pct",
    "energy_safety_factor",
)


class EvaluationDefectError(RuntimeError):
    """Raised when results break a gate the benchmark treats as a defect rather than a finding."""


@dataclass(frozen=True, slots=True)
class Trip:
    experiment: str
    group: str
    variant: str
    value: float | str
    route_id: str
    band: str
    graph: GraphKey
    soc_step_pct: int
    vehicle: VehicleProfile


def _vehicle(name: str, values: Mapping[str, float]) -> VehicleProfile:
    return VehicleProfile(
        name=name,
        usable_battery_kwh=values["usable_battery_kwh"],
        initial_soc_pct=values["initial_soc_pct"],
        consumption_kwh_per_100km=values["consumption_kwh_per_100km"],
        max_dc_power_kw=values["max_dc_power_kw"],
        reserve_soc_pct=values["reserve_soc_pct"],
        energy_safety_factor=values["energy_safety_factor"],
    )


def enumerate_trips(manifest: Mapping[str, Any]) -> tuple[Trip, ...]:
    """Expand the declared experiments into their exact, ordered trip list."""

    bands = {route["id"]: route["band"] for route in manifest["routes"]}
    sets = manifest["parameter_sets"]
    experiments = manifest["experiments"]
    trips: list[Trip] = []

    def add(
        name: str,
        settings: Mapping[str, Any],
        group: str,
        variant: str,
        value: float | str,
        vehicle: VehicleProfile,
    ) -> None:
        trips.extend(
            Trip(
                experiment=name,
                group=group,
                variant=variant,
                value=value,
                route_id=route_id,
                band=bands[route_id],
                graph=GraphKey(route_id, settings["corridor_width_km"], settings["candidate_cap"]),
                soc_step_pct=settings["soc_step_pct"],
                vehicle=vehicle,
            )
            for route_id in experiments[name]["routes"]
        )

    e1 = experiments["E1"]
    for set_id in e1["parameter_sets"]:
        add("E1", e1, set_id, set_id, set_id, _vehicle(set_id, sets[set_id]))
    # E2-E4 each sweep one setting over the same parameter sets.
    for name, swept, label in (
        ("E2", "soc_step_pct", "soc"),
        ("E3", "candidate_cap", "cap"),
        ("E4", "corridor_width_km", "width"),
    ):
        experiment = experiments[name]
        for set_id in experiment["parameter_sets"]:
            for value in experiment[swept]:
                settings = {**experiment, swept: value}
                vehicle = _vehicle(set_id, sets[set_id])
                add(name, settings, set_id, f"{set_id}|{label}={value}", value, vehicle)
    e5 = experiments["E5"]
    base = sets[e5["base_parameter_set"]]
    for factor, values in e5["factors"].items():
        for value in values:
            variant = f"{factor}={value}"
            add("E5", e5, factor, variant, value, _vehicle(variant, {**base, factor: value}))
    return tuple(trips)


def verified_e1_graphs(
    manifest: Mapping[str, Any], *, graphs_dir: Path, summary: Mapping[str, Any]
) -> Iterator[tuple[Trip, StoredGraph]]:
    """Yield each E1 trip with its stored graph, checked against the published checksum."""

    for trip in enumerate_trips(manifest):
        if trip.experiment == "E1":
            name = trip.graph.file_name
            sha256 = summary["stored_graphs"][name]["sha256"]
            yield trip, load_verified_graph(graphs_dir / name, expected_sha256=sha256)


def _plan_fingerprint(plan: RoutePlan) -> tuple[object, ...]:
    return (
        plan.node_ids,
        tuple(
            (stop.station_id, stop.arrival_soc_pct, stop.departure_soc_pct)
            for stop in plan.charging_stops
        ),
    )


def _plan_metrics(plan: RoutePlan, *, reference_km: float | None) -> dict[str, Any]:
    return {
        "outcome": "plan",
        "total_minutes": plan.total_minutes,
        "driving_minutes": plan.driving_minutes,
        "charging_minutes": plan.charging_minutes,
        "distance_km": plan.total_distance_km,
        "charging_stops": len(plan.charging_stops),
        "arrival_soc_pct": plan.arrival_soc_pct,
        "detour_km": None if reference_km is None else plan.total_distance_km - reference_km,
        "reserve_violations": 0,
    }


def run_trip_on_graph(
    graph: StoredGraph,
    *,
    vehicle: VehicleProfile,
    soc_step_pct: int,
    algorithms: Sequence[str],
) -> dict[str, Any]:
    """Run every algorithm on one graph and independently replay every returned plan."""

    if graph.outcome != "graph":
        return {
            "selected_count": 0,
            "eligible_count": 0,
            "distinct_options": 0,
            "results": {algorithm: {"outcome": "provider_failure"} for algorithm in algorithms},
        }
    network = StaticRoadNetwork(graph.legs)
    stations: dict[str, Station] = dict(graph.stations)
    reference = ShortestDrivingTimeBaseline.shortest_path("origin", "destination", network)
    reference_km = None if reference is None else sum(leg.distance_km for leg in reference)
    results: dict[str, Any] = {}
    fingerprints: set[tuple[object, ...]] = set()
    for algorithm in algorithms:
        try:
            plan = evaluate_plan(
                algorithm,
                origin_id="origin",
                destination_id="destination",
                vehicle=vehicle,
                stations=stations,
                road_network=network,
                soc_step_pct=soc_step_pct,
            )
        except NoFeasibleRouteError:
            results[algorithm] = {"outcome": "infeasible"}
            continue
        # Replay failures propagate as defects, not reportable trip outcomes.
        results[algorithm] = _plan_metrics(plan, reference_km=reference_km)
        if algorithm in OPTION_ALGORITHMS:
            fingerprints.add(_plan_fingerprint(plan))
    return {
        "selected_count": len(graph.stations),
        "eligible_count": graph.eligible_count,
        "distinct_options": len(fingerprints),
        "results": results,
    }


def _round_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {key: _round_value(item) for key, item in value.items()}
    return value


def check_gates(trips: Sequence[Mapping[str, Any]]) -> None:
    """Raise on any result the benchmark treats as a defect: exact must dominate greedy."""

    for trip in trips:
        results = trip["results"]
        fastest = results.get(FASTEST)
        if fastest is None:
            continue
        label = f"{trip['experiment']}/{trip['variant']}/{trip['route_id']}"
        for algorithm, result in results.items():
            if result["outcome"] != "plan":
                continue
            if fastest["outcome"] != "plan":
                raise EvaluationDefectError(f"{label}: {algorithm} planned but fastest did not")
            if fastest["total_minutes"] > result["total_minutes"] + MINUTE_TOLERANCE:
                raise EvaluationDefectError(f"{label}: fastest is slower than {algorithm}")


def summary_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p90": None, "minimum": None, "maximum": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median": round(statistics.median(ordered), 3),
        "p90": round(nearest_rank(ordered, 0.9), 3),
        "minimum": round(ordered[0], 3),
        "maximum": round(ordered[-1], 3),
    }


def _savings(trips: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    minutes: list[float] = []
    percent: list[float] = []
    for trip in trips:
        fastest, greedy = trip["results"][FASTEST], trip["results"][GREEDY]
        if fastest["outcome"] == greedy["outcome"] == "plan":
            saved = greedy["total_minutes"] - fastest["total_minutes"]
            minutes.append(saved)
            percent.append(100 * saved / greedy["total_minutes"])
    return {"minutes": summary_stats(minutes), "percent": summary_stats(percent)}


def _variant_summary(
    trips: Sequence[Mapping[str, Any]], algorithms: Sequence[str]
) -> dict[str, Any]:
    summary: dict[str, Any] = {"trips": len(trips), "algorithms": {}}
    for algorithm in algorithms:
        results = [trip["results"][algorithm] for trip in trips]
        plans = [result for result in results if result["outcome"] == "plan"]
        summary["algorithms"][algorithm] = {
            "plan": len(plans),
            "infeasible": sum(result["outcome"] == "infeasible" for result in results),
            "provider_failure": sum(result["outcome"] == "provider_failure" for result in results),
            "feasibility_rate": round(len(plans) / len(results), 4) if results else None,
            "valid_plan_rate": 1.0 if plans else None,
            "reserve_violations": sum(result["reserve_violations"] for result in plans),
            "total_minutes": summary_stats([result["total_minutes"] for result in plans]),
            "charging_minutes": summary_stats([result["charging_minutes"] for result in plans]),
            "charging_stops": summary_stats([float(result["charging_stops"]) for result in plans]),
            "detour_km": summary_stats(
                [result["detour_km"] for result in plans if result["detour_km"] is not None]
            ),
        }
    summary["distinct_options"] = summary_stats([float(trip["distinct_options"]) for trip in trips])
    return summary


def _reference_gap(trips: Sequence[Mapping[str, Any]], reference_value: float) -> dict[str, Any]:
    """Compare each variant's exact fastest result with the same route at the reference value."""

    references = {
        (trip["group"], trip["route_id"]): trip["results"][FASTEST]
        for trip in trips
        if trip["value"] == reference_value
    }
    report: dict[str, Any] = {"reference_value": reference_value, "variants": {}}
    for variant in sorted({trip["variant"] for trip in trips}):
        gaps: list[float] = []
        lost = gained = 0
        for trip in trips:
            if trip["variant"] != variant or trip["value"] == reference_value:
                continue
            result = trip["results"][FASTEST]
            reference = references[(trip["group"], trip["route_id"])]
            if result["outcome"] == reference["outcome"] == "plan":
                gaps.append(result["total_minutes"] - reference["total_minutes"])
            elif reference["outcome"] == "plan":
                lost += 1
            elif result["outcome"] == "plan":
                gained += 1
        report["variants"][variant] = {
            "gap_minutes": summary_stats(gaps),
            "feasible_only_at_reference": lost,
            "feasible_only_at_variant": gained,
        }
    return report


def build_summary(
    *,
    manifest: Mapping[str, Any],
    trips: Sequence[Mapping[str, Any]],
    manifest_sha256: str,
    graph_checksums: Mapping[str, str],
    graph_outcomes: Mapping[str, str],
) -> dict[str, Any]:
    algorithms = manifest["experiments"]["E1"]["algorithms"]
    experiments: dict[str, Any] = {}
    for name in sorted({trip["experiment"] for trip in trips}):
        selected = [trip for trip in trips if trip["experiment"] == name]
        report: dict[str, Any] = {
            "title": manifest["experiments"][name]["name"],
            "trips": len(selected),
            "variants": {
                variant: _variant_summary(
                    [trip for trip in selected if trip["variant"] == variant], algorithms
                )
                for variant in sorted({trip["variant"] for trip in selected})
            },
        }
        if name == "E1":
            report["exact_vs_greedy_savings"] = {
                "overall": _savings(selected),
                "per_distance_band": {
                    band: _savings([trip for trip in selected if trip["band"] == band])
                    for band in sorted({trip["band"] for trip in selected})
                },
                "per_parameter_set": {
                    group: _savings([trip for trip in selected if trip["group"] == group])
                    for group in sorted({trip["group"] for trip in selected})
                },
                "population": manifest["savings_summary"]["population"],
            }
            attempted = len(selected)
            failed = sum(trip["results"][GREEDY]["outcome"] != "plan" for trip in selected)
            report["greedy_failure_rate"] = round(failed / attempted, 4) if attempted else None
        if name in GAP_REFERENCES:
            report["reference_gap"] = _reference_gap(selected, GAP_REFERENCES[name])
        experiments[name] = report

    outcome_counts: dict[str, dict[str, int]] = {}
    for algorithm in algorithms:
        counts = {"plan": 0, "infeasible": 0, "provider_failure": 0}
        for trip in trips:
            counts[trip["results"][algorithm]["outcome"]] += 1
        outcome_counts[algorithm] = counts
    frozen = manifest["frozen_inputs"]
    return {
        "artifact_version": 1,
        "benchmark_id": manifest["benchmark_id"],
        "manifest_sha256": manifest_sha256,
        "road_graph_data_version": frozen["road_graph"]["data_version"],
        "station_snapshot_id": frozen["station_snapshot"]["snapshot_id"],
        "stored_graphs": {
            name: {"sha256": graph_checksums[name], "outcome": graph_outcomes[name]}
            for name in sorted(graph_checksums)
        },
        "reconciliation": {
            "expected_trips": len(enumerate_trips(manifest)),
            "evaluated_trips": len(trips),
            "outcomes_per_algorithm": outcome_counts,
        },
        "experiments": experiments,
        "attribution": manifest["attribution"],
        "result_boundary": manifest["result_boundary"],
    }


def evaluate(
    *, manifest: Mapping[str, Any], graphs_dir: Path, manifest_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate every declared trip from stored graphs and return summary plus trip records."""

    algorithms = tuple(manifest["experiments"]["E1"]["algorithms"])
    planned = enumerate_trips(manifest)
    keys = sorted({trip.graph for trip in planned})
    checksums: dict[str, str] = {}
    outcomes: dict[str, str] = {}
    for key in keys:
        path = graphs_dir / key.file_name
        checksums[key.file_name] = hashlib.sha256(path.read_bytes()).hexdigest()
        outcomes[key.file_name] = json.loads(path.read_text(encoding="utf-8"))["outcome"]

    @lru_cache(maxsize=4)
    def graph_for(key: GraphKey) -> StoredGraph:
        return load_stored_graph(graphs_dir / key.file_name)

    # Experiments share trips with identical inputs, so each distinct input runs once.
    memo: dict[tuple[object, ...], dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for trip in planned:
        vehicle_values = tuple(getattr(trip.vehicle, field) for field in _VEHICLE_FIELDS)
        memo_key = (trip.graph, trip.soc_step_pct, vehicle_values)
        if memo_key not in memo:
            memo[memo_key] = run_trip_on_graph(
                graph_for(trip.graph),
                vehicle=trip.vehicle,
                soc_step_pct=trip.soc_step_pct,
                algorithms=algorithms,
            )
        records.append(
            {
                "experiment": trip.experiment,
                "group": trip.group,
                "variant": trip.variant,
                "value": trip.value,
                "route_id": trip.route_id,
                "band": trip.band,
                "corridor_width_km": trip.graph.corridor_width_km,
                "candidate_cap": trip.graph.candidate_cap,
                "soc_step_pct": trip.soc_step_pct,
                **memo[memo_key],
            }
        )
    check_gates(records)
    summary = build_summary(
        manifest=manifest,
        trips=records,
        manifest_sha256=manifest_sha256,
        graph_checksums=checksums,
        graph_outcomes=outcomes,
    )
    return summary, records


def trips_bytes(trips: Sequence[Mapping[str, Any]]) -> bytes:
    """Serialize one rounded trip per line, keeping the file reviewable and diffable."""

    lines = [
        json.dumps(
            _round_value(dict(trip)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        for trip in trips
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*map(_walk_keys, value.values()))
    if isinstance(value, list):
        return set().union(*map(_walk_keys, value))
    return set()


def assert_no_station_data(evidence: Any, stations: Mapping[str, Station]) -> None:
    """Fail closed if evidence carries a station field, identifier, name, or precise coordinate."""

    leaked_keys = _walk_keys(evidence) & FORBIDDEN_EVIDENCE_KEYS
    if leaked_keys:
        raise EvaluationDefectError(f"evidence contains station fields: {sorted(leaked_keys)}")
    text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    for station in stations.values():
        tokens = [station.id]
        if len(station.name) >= 4:
            tokens.append(station.name)
        # Metrics carry at most three decimals, so a longer decimal can only be a coordinate.
        for coordinate in (station.latitude, station.longitude):
            rendered = repr(coordinate)
            if len(rendered.partition(".")[2]) >= 4:
                tokens.append(rendered)
        for token in tokens:
            if token in text:
                raise EvaluationDefectError("evidence contains a station value")


def measure_planner_runtime(*, manifest: Mapping[str, Any], graphs_dir: Path) -> dict[str, Any]:
    """Time each algorithm over the E1 inputs under the declared E6 protocol."""

    protocol = manifest["experiments"]["E6"]
    algorithms = manifest["experiments"]["E1"]["algorithms"]
    trips = [trip for trip in enumerate_trips(manifest) if trip.experiment == "E1"]
    graphs = {
        key: load_stored_graph(graphs_dir / key.file_name) for key in {trip.graph for trip in trips}
    }

    def run_pass(algorithm: str) -> None:
        for trip in trips:
            graph = graphs[trip.graph]
            if graph.outcome != "graph":
                continue
            try:
                plan_route(
                    algorithm,
                    origin_id="origin",
                    destination_id="destination",
                    vehicle=trip.vehicle,
                    stations=dict(graph.stations),
                    road_network=StaticRoadNetwork(graph.legs),
                    soc_step_pct=trip.soc_step_pct,
                )
            except NoFeasibleRouteError:
                pass

    results = {
        algorithm: timing_summary(
            time_passes(
                partial(run_pass, algorithm),
                warmups=protocol["warmups"],
                repetitions=protocol["repetitions"],
            )
        )
        for algorithm in algorithms
    }
    return {
        "clock": "time.perf_counter_ns",
        "warmups": protocol["warmups"],
        "repetitions": protocol["repetitions"],
        "trips_per_pass": len(trips),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "algorithms": results,
    }
