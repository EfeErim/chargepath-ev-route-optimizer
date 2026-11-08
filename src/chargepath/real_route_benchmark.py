"""Deterministic route selection and manifest content for the real-route benchmark.

Selection reads only province metadata and road distances. It never reads station data or planner
output, so the benchmark cannot be tuned toward a favorable result.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BENCHMARK_ID = "chargepath-m6-real-route-v1"
SELECTION_SEED = "chargepath-m6-v1"
EASTERN_REGIONS = frozenset({"Doğu Anadolu", "Güneydoğu Anadolu"})
ROUTES_PER_BAND = 6
EASTERN_SLOTS_PER_BAND = 2
MAX_ROUTES_PER_PROVINCE = 2
# The first eastern slot and the first open slot of each band form the E5 subset.
E5_SLOTS = (0, EASTERN_SLOTS_PER_BAND)


@dataclass(frozen=True, slots=True)
class DistanceBand:
    name: str
    minimum_km: float
    maximum_km: float | None

    def contains(self, distance_km: float) -> bool:
        if distance_km < self.minimum_km:
            return False
        return self.maximum_km is None or distance_km < self.maximum_km


DISTANCE_BANDS = (
    DistanceBand("150-300", 150.0, 300.0),
    DistanceBand("300-600", 300.0, 600.0),
    DistanceBand("600-1000", 600.0, 1000.0),
    DistanceBand("over-1000", 1000.0, None),
)


@dataclass(frozen=True, slots=True)
class Province:
    plate: int
    name: str
    region: str
    latitude: float
    longitude: float

    @property
    def is_eastern(self) -> bool:
        return self.region in EASTERN_REGIONS


@dataclass(frozen=True, slots=True)
class SelectedRoute:
    route_id: str
    band: str
    slot: int
    origin: Province
    destination: Province
    selection_road_km: float

    @property
    def is_eastern(self) -> bool:
        return self.origin.is_eastern or self.destination.is_eastern


def load_provinces(path: str | Path) -> tuple[Province, ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    provinces = tuple(
        Province(
            plate=row["plate"],
            name=row["name"],
            region=row["region"],
            latitude=row["latitude"],
            longitude=row["longitude"],
        )
        for row in document["provinces"]
    )
    if [province.plate for province in provinces] != list(range(1, len(provinces) + 1)):
        raise ValueError("provinces must be ordered by consecutive plate number")
    return provinces


def distance_key(origin_plate: int, destination_plate: int) -> str:
    return f"{origin_plate}-{destination_plate}"


def _rank(origin_plate: int, destination_plate: int) -> str:
    token = f"{SELECTION_SEED}|{origin_plate}|{destination_plate}"
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def select_routes(
    provinces: Sequence[Province],
    road_km: Mapping[str, float],
) -> tuple[SelectedRoute, ...]:
    """Select six routes per band: two touching an eastern region, then four unrestricted.

    Each pair runs from its lower to its higher plate number and is ranked by a fixed SHA-256 token.
    Bands are filled in ascending order. A province is used at most twice across the benchmark.
    A pair with no stored distance was unreachable and is not a candidate.
    """

    pairs = [
        (origin, destination)
        for index, origin in enumerate(provinces)
        for destination in provinces[index + 1 :]
        if distance_key(origin.plate, destination.plate) in road_km
    ]
    pairs.sort(key=lambda pair: _rank(pair[0].plate, pair[1].plate))
    uses: Counter[int] = Counter()
    routes: list[SelectedRoute] = []

    for band in DISTANCE_BANDS:
        chosen: list[tuple[Province, Province]] = []
        for eastern_only, target in ((True, EASTERN_SLOTS_PER_BAND), (False, ROUTES_PER_BAND)):
            for origin, destination in pairs:
                if len(chosen) == target:
                    break
                distance = road_km[distance_key(origin.plate, destination.plate)]
                if (
                    not band.contains(distance)
                    or (origin, destination) in chosen
                    or uses[origin.plate] >= MAX_ROUTES_PER_PROVINCE
                    or uses[destination.plate] >= MAX_ROUTES_PER_PROVINCE
                    or (eastern_only and not (origin.is_eastern or destination.is_eastern))
                ):
                    continue
                chosen.append((origin, destination))
                uses[origin.plate] += 1
                uses[destination.plate] += 1
        if len(chosen) != ROUTES_PER_BAND:
            raise ValueError(f"band {band.name} produced {len(chosen)} routes")
        for slot, (origin, destination) in enumerate(chosen):
            routes.append(
                SelectedRoute(
                    route_id=f"m6-r{len(routes) + 1:02d}",
                    band=band.name,
                    slot=slot,
                    origin=origin,
                    destination=destination,
                    selection_road_km=road_km[distance_key(origin.plate, destination.plate)],
                )
            )
    return tuple(routes)


def _endpoint(province: Province) -> dict[str, Any]:
    return {
        "plate": province.plate,
        "name": province.name,
        "region": province.region,
        "latitude": province.latitude,
        "longitude": province.longitude,
    }


def _vehicle(
    *, battery_kwh: float, max_dc_kw: float, consumption: float, initial_soc: float = 80
) -> dict[str, float]:
    return {
        "usable_battery_kwh": battery_kwh,
        "initial_soc_pct": initial_soc,
        "consumption_kwh_per_100km": consumption,
        "max_dc_power_kw": max_dc_kw,
        "reserve_soc_pct": 10,
        "energy_safety_factor": 1.1,
    }


def build_manifest(
    *,
    routes: Sequence[SelectedRoute],
    frozen_inputs: Mapping[str, Any],
    distances_data_version: str,
) -> dict[str, Any]:
    all_routes = [route.route_id for route in routes]
    e5_routes = [route.route_id for route in routes if route.slot in E5_SLOTS]
    set_ids = ["set-40kwh", "set-60kwh", "set-80kwh"]
    return {
        "manifest_version": 1,
        "benchmark_id": BENCHMARK_ID,
        "decision_question": (
            "Under one frozen road graph and station snapshot, how much modeled trip time does "
            "exact optimization save over the fixed-80 greedy strategy, and how much do that "
            "result and feasibility depend on the SOC grid, the candidate cap, and the vehicle "
            "inputs?"
        ),
        "freeze_policy": (
            "Committed before the first evaluation run. Routes are not added, removed, or "
            "replaced after that run. A snap, routing, or infeasibility failure is a reported "
            "outcome. Any correction creates a new manifest version and keeps the old results."
        ),
        "frozen_inputs": dict(frozen_inputs),
        "selection_rule": {
            "seed": SELECTION_SEED,
            "rank_key": "sha256(seed|origin_plate|destination_plate) ascending",
            "orientation": "lower plate number is the origin",
            "band_distance": (
                "OSRM Table road distance from origin to destination, rounded to 0.1 km, on the "
                f"graph with data_version {distances_data_version}"
            ),
            "bands_km": [
                {
                    "name": band.name,
                    "minimum_inclusive": band.minimum_km,
                    "maximum_exclusive": band.maximum_km,
                }
                for band in DISTANCE_BANDS
            ],
            "routes_per_band": ROUTES_PER_BAND,
            "eastern_slots_per_band": EASTERN_SLOTS_PER_BAND,
            "eastern_regions": sorted(EASTERN_REGIONS),
            "max_routes_per_province": MAX_ROUTES_PER_PROVINCE,
            "e5_subset": "slot 0 and slot 2 of each band",
            "inputs_not_read": ["station data", "planner output"],
        },
        "routes": [
            {
                "id": route.route_id,
                "band": route.band,
                "slot": route.slot,
                "eastern": route.is_eastern,
                "selection_road_km": route.selection_road_km,
                "origin": _endpoint(route.origin),
                "destination": _endpoint(route.destination),
            }
            for route in routes
        ],
        "parameter_sets": {
            "note": "Declared parameter sets, not claims about any real car.",
            "set-40kwh": _vehicle(battery_kwh=40, max_dc_kw=50, consumption=16),
            "set-60kwh": _vehicle(battery_kwh=60, max_dc_kw=150, consumption=18),
            "set-80kwh": _vehicle(battery_kwh=80, max_dc_kw=250, consumption=20),
        },
        "shared_configuration": {
            "corridor_algorithm": "equirectangular-segment-farthest-progress-v3",
            "planner_retries": (
                "None. The demo's SOC-grid refinement and adaptive candidate widening are not "
                "used, so each trip has exactly its declared grid, width, and cap."
            ),
        },
        "experiments": {
            "E1": {
                "name": "Strategy comparison",
                "algorithms": [
                    "shortest_driving_time",
                    "fastest",
                    "shortest_distance",
                    "fewest_charging_stops",
                    "greedy_fixed_80",
                ],
                "soc_step_pct": 5,
                "corridor_width_km": 25,
                "candidate_cap": 50,
                "parameter_sets": set_ids,
                "routes": all_routes,
            },
            "E2": {
                "name": "SOC grid",
                "soc_step_pct": [10, 5, 2],
                "corridor_width_km": 25,
                "candidate_cap": 50,
                "parameter_sets": set_ids,
                "routes": all_routes,
            },
            "E3": {
                "name": "Candidate cap",
                "candidate_cap": [12, 24, 50, 98, 200],
                "soc_step_pct": 5,
                "corridor_width_km": 25,
                "parameter_sets": ["set-60kwh"],
                "routes": all_routes,
                "server_requirement": "cap 200 needs osrm-routed --max-table-size 202",
                "reference": (
                    "Cap 200 is the reference for the cap gap; it is not a global optimum."
                ),
            },
            "E4": {
                "name": "Corridor width",
                "corridor_width_km": [10, 25, 50],
                "soc_step_pct": 5,
                "candidate_cap": 50,
                "parameter_sets": ["set-60kwh"],
                "routes": all_routes,
            },
            "E5": {
                "name": "Vehicle inputs, one factor at a time",
                "base_parameter_set": "set-60kwh",
                "factors": {
                    "usable_battery_kwh": [40, 60, 80],
                    "initial_soc_pct": [50, 80, 100],
                    "consumption_kwh_per_100km": [14.4, 18, 21.6],
                    "reserve_soc_pct": [5, 10, 20],
                    "energy_safety_factor": [1.0, 1.1, 1.25],
                    "max_dc_power_kw": [50, 150, 250],
                },
                "soc_step_pct": 5,
                "corridor_width_km": 25,
                "candidate_cap": 50,
                "routes": e5_routes,
            },
            "E6": {
                "name": "Runtime",
                "inputs": "E1",
                "warmups": 3,
                "repetitions": 20,
                "timing": "planner and OSRM timed separately",
                "summary": ["median", "p95"],
            },
        },
        "metrics": [
            "feasibility_rate",
            "valid_plan_rate",
            "reserve_violations",
            "total_time_minutes",
            "charging_time_minutes",
            "charging_stops",
            "detour_distance_km",
            "distinct_options",
            "greedy_failure_rate",
            "soc_grid_gap",
            "candidate_cap_gap",
            "exact_vs_greedy_savings_minutes_and_percent",
        ],
        "savings_summary": {
            "statistics": ["median", "p90", "minimum", "maximum"],
            "groups": ["overall", "per_distance_band"],
            "population": "trips where both strategies return a plan",
        },
        "outcome_classes": ["plan", "infeasible", "provider_failure"],
        "attribution": (
            "Road distances derive from OpenStreetMap data, © OpenStreetMap contributors, ODbL."
        ),
        "result_boundary": (
            "Modeled minutes on one OSM extract, one static snapshot, and declared parameter sets. "
            "Not real-world SOC accuracy, arrival time, or charger availability."
        ),
    }


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")
