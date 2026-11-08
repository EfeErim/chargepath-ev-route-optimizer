"""Planning services behind the demo API: fixture mode and OSRM integration mode.

This module has no HTTP dependency, so the same services can run in a browser build.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from chargepath import validation
from chargepath.alternatives import (
    CompetitiveRoutePlanner,
    RouteOption,
    RouteOptionSet,
    RouteStrategy,
)
from chargepath.corridor import (
    DEFAULT_CANDIDATE_SELECTION_CONFIG,
    CandidateSelectionConfig,
    CandidateSelectionResult,
    select_corridor_candidates,
)
from chargepath.demo.schema import VEHICLE_INPUT_BOUNDS, PlanRequest, PointInput, VehicleInputs
from chargepath.energy import energy_for_distance, required_energy_buckets
from chargepath.fixtures import SyntheticScenario
from chargepath.models import GeoJsonLineString, NoFeasibleRouteError, Station, VehicleProfile
from chargepath.providers.base import RoadNetwork
from chargepath.providers.osrm import (
    CandidateGraph,
    CandidateGraphBuilder,
    CandidateNode,
    Coordinate,
    OsrmHttpClient,
    OsrmProviderError,
    OsrmResponseError,
    fetch_selected_plans_geometry,
)
from chargepath.providers.static import StaticRoadNetwork
from chargepath.redundancy import stop_redundancy
from chargepath.station_data import (
    SnapshotProvenance,
    normalize_epdk_response,
    project_ccs2_dc_options,
)

DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ATTRIBUTION = "© OpenStreetMap contributors"
SOC_STEP_PCT = 5
REFINED_SOC_STEP_PCT = 2
# osrm-routed rejects larger Table requests unless it is started with a higher --max-table-size.
OSRM_DEFAULT_MAX_TABLE_LOCATIONS = 100
# The Table request also carries the origin and destination.
DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT = OSRM_DEFAULT_MAX_TABLE_LOCATIONS - 2


def _object(value: object, *, field: str) -> dict[str, object]:
    return validation.json_object(value, field=field)


def _text(value: object, *, field: str) -> str:
    return validation.text(value, field=field).strip()


class PlanningService(Protocol):
    def public_config(self) -> dict[str, object]: ...

    def plan(self, request: PlanRequest) -> dict[str, object]: ...


def plan_response(service: PlanningService, payload: object) -> tuple[int, dict[str, object]]:
    """Validate a decoded plan payload and map planning outcomes to HTTP-style status codes.

    The loopback server and the in-browser build share this so both report the same errors.
    """

    try:
        request = PlanRequest.from_payload(payload)
    except ValueError as error:
        return 400, {"error": str(error) or "Invalid request"}
    try:
        return 200, service.plan(request)
    except NoFeasibleRouteError as error:
        return 422, {"error": str(error)}
    except OsrmProviderError as error:
        return 502, {"error": str(error)}
    except (KeyError, ValueError):
        return 500, {"error": "Internal planning error"}


class FixturePlanningService:
    """Deterministic planning against the bundled synthetic scenario."""

    def __init__(self, scenario: SyntheticScenario, *, tile_url: str) -> None:
        self._scenario = scenario
        self._tile_url = _validate_tile_url(tile_url)
        self._points = _scenario_points(scenario)

    def public_config(self) -> dict[str, object]:
        return _public_config(
            mode="fixture",
            data_label="Synthetic fixture — freshness not applicable",
            tile_url=self._tile_url,
            vehicle=VehicleInputs.from_profile(self._scenario.vehicle),
            endpoints=self._points,
        )

    def plan(self, request: PlanRequest) -> dict[str, object]:
        expected_origin, expected_destination = self._points
        _require_fixture_point(request.origin, expected_origin, role="origin")
        _require_fixture_point(request.destination, expected_destination, role="destination")
        vehicle = request.vehicle.to_profile()
        stations = self._scenario.station_map()
        network = StaticRoadNetwork(self._scenario.legs)
        result, soc_step_pct = _plan_with_soc_refinement(
            origin_id=self._scenario.origin_id,
            destination_id=self._scenario.destination_id,
            vehicle=vehicle,
            stations=stations,
            road_network=network,
        )
        return _serialize_result(
            result,
            vehicle=vehicle,
            stations=stations,
            soc_step_pct=soc_step_pct,
            data_label="Synthetic fixture — freshness not applicable",
            road_network=network,
        )


class IntegrationPlanningService:
    """Explicit OSRM plus checksum-pinned static EPDK snapshot mode."""

    def __init__(
        self,
        *,
        snapshot_path: Path,
        manifest_path: Path,
        osrm_endpoint: str,
        tile_url: str,
        allow_remote_osrm: bool = False,
        candidate_cap: int = DEFAULT_CANDIDATE_SELECTION_CONFIG.candidate_cap,
        adaptive_candidate_cap_limit: int = DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT,
    ) -> None:
        if not isinstance(snapshot_path, Path) or not isinstance(manifest_path, Path):
            raise ValueError("station snapshot and manifest paths must be Path values")
        if not isinstance(allow_remote_osrm, bool):
            raise ValueError("allow_remote_osrm must be boolean")
        if (
            isinstance(adaptive_candidate_cap_limit, bool)
            or not isinstance(adaptive_candidate_cap_limit, int)
            or adaptive_candidate_cap_limit < candidate_cap
        ):
            raise ValueError(
                "adaptive candidate cap limit must be an integer at least candidate_cap"
            )
        raw_bytes = snapshot_path.read_bytes()
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = _object(manifest_value, field="station manifest")
        expected_sha = _text(manifest.get("response_sha256"), field="response_sha256").lower()
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha != expected_sha:
            raise ValueError("station snapshot checksum does not match its manifest")
        reuse_status = _text(manifest.get("reuse_status"), field="reuse_status")
        if reuse_status not in {"pending_verification", "approved", "prohibited"}:
            raise ValueError("station manifest reuse_status is unsupported")
        if reuse_status == "prohibited":
            raise ValueError("station manifest prohibits use of this snapshot")
        freshness_value = manifest.get("source_freshness")
        source_freshness = (
            None if freshness_value is None else _text(freshness_value, field="source_freshness")
        )
        provenance = SnapshotProvenance(
            snapshot_id=_text(manifest.get("snapshot_id"), field="snapshot_id"),
            source_name=_text(manifest.get("source"), field="source"),
            source_url=_text(manifest.get("source_url"), field="source_url"),
            retrieved_at=_text(manifest.get("retrieved_at"), field="retrieved_at"),
            response_sha256=actual_sha,
            reuse_status=cast(
                Literal["pending_verification", "approved", "prohibited"], reuse_status
            ),
            source_freshness=source_freshness,
        )
        payload = _object(json.loads(raw_bytes), field="station snapshot")
        normalized = normalize_epdk_response(payload, provenance)
        self._options = project_ccs2_dc_options(normalized)
        self._client = OsrmHttpClient(
            endpoint=osrm_endpoint,
            allow_remote_endpoint=allow_remote_osrm,
        )
        self._osrm_endpoint = osrm_endpoint.strip().rstrip("/")
        self._coordinates_leave_device = allow_remote_osrm
        self._candidate_config = CandidateSelectionConfig(
            corridor_width_km=DEFAULT_CANDIDATE_SELECTION_CONFIG.corridor_width_km,
            candidate_cap=candidate_cap,
        )
        self._adaptive_candidate_cap_limit = adaptive_candidate_cap_limit
        self._tile_url = _validate_tile_url(tile_url)
        self._snapshot_id = provenance.snapshot_id
        self._data_label = (
            f"Static EPDK snapshot {provenance.snapshot_id} — retrieved "
            f"{provenance.retrieved_at}; source freshness "
            f"{provenance.source_freshness or 'unknown'}"
        )

    def public_config(self) -> dict[str, object]:
        return _public_config(
            mode="integration",
            data_label=self._data_label,
            tile_url=self._tile_url,
            vehicle=VehicleInputs(60, 60, 20, 150, 10, 1.1),
            endpoints=(),
            integration_summary={
                "snapshot_id": self._snapshot_id,
                "compatible_site_count": len(self._options),
                "osrm_endpoint": self._osrm_endpoint,
                "coordinates_leave_device": self._coordinates_leave_device,
                "candidate_cap": self._candidate_config.candidate_cap,
                "adaptive_candidate_cap_limit": self._adaptive_candidate_cap_limit,
                "adaptive_expansion_enabled": not self._coordinates_leave_device,
            },
        )

    def plan(self, request: PlanRequest) -> dict[str, object]:
        origin = Coordinate(request.origin.longitude, request.origin.latitude)
        destination = Coordinate(request.destination.longitude, request.destination.latitude)
        corridor_geometry = self._client.route_geometry(origin, destination)
        selected = select_corridor_candidates(
            route_geometry=corridor_geometry,
            options=self._options,
            config=self._candidate_config,
        )
        vehicle = request.vehicle.to_profile()
        selected, graph, stations, result, soc_step_pct = self._plan_with_adaptive_candidates(
            origin=origin,
            destination=destination,
            vehicle=vehicle,
            corridor_geometry=corridor_geometry,
            initial_selection=selected,
        )
        geometric_plans = fetch_selected_plans_geometry(
            tuple(option.plan for option in result.options),
            graph=graph,
            route_client=self._client,
            known_geometries={("origin", "destination"): corridor_geometry},
        )
        geometric_options = tuple(
            RouteOption(
                strategies=option.strategies,
                plan=geometric_plan,
            )
            for option, geometric_plan in zip(result.options, geometric_plans, strict=True)
        )
        geometric_result = RouteOptionSet(
            options=geometric_options,
            unavailable_strategies=result.unavailable_strategies,
        )
        return _serialize_result(
            geometric_result,
            vehicle=vehicle,
            stations=stations,
            data_label=self._data_label,
            soc_step_pct=soc_step_pct,
            road_network=graph,
            candidate_selection={
                "eligible_count": selected.eligible_count,
                "selected_count": len(selected.candidates),
                "initial_cap": self._candidate_config.candidate_cap,
                "expanded_after_infeasible": len(selected.candidates)
                > self._candidate_config.candidate_cap,
            },
        )

    def _plan_with_adaptive_candidates(
        self,
        *,
        origin: Coordinate,
        destination: Coordinate,
        vehicle: VehicleProfile,
        corridor_geometry: GeoJsonLineString,
        initial_selection: CandidateSelectionResult,
    ) -> tuple[
        CandidateSelectionResult,
        CandidateGraph,
        dict[str, Station],
        RouteOptionSet,
        int,
    ]:
        selected = initial_selection
        last_infeasible: NoFeasibleRouteError | None = None
        while True:
            candidate_options = tuple(candidate.option for candidate in selected.candidates)
            nodes = (
                CandidateNode("origin", origin),
                *(
                    CandidateNode(
                        option.station.id,
                        Coordinate(option.station.longitude, option.station.latitude),
                    )
                    for option in candidate_options
                ),
                CandidateNode("destination", destination),
            )
            try:
                graph = CandidateGraphBuilder(self._client).build(nodes)
            except OsrmResponseError as error:
                # A widened table the provider refuses leaves the last search's result standing.
                if error.code != "TooBig" or last_infeasible is None:
                    raise
                raise last_infeasible from error
            stations = {option.station.id: option.station for option in candidate_options}
            try:
                result, soc_step_pct = _plan_with_soc_refinement(
                    origin_id="origin",
                    destination_id="destination",
                    vehicle=vehicle,
                    stations=stations,
                    road_network=graph,
                )
            except NoFeasibleRouteError as error:
                last_infeasible = error
                next_cap = _next_candidate_cap(
                    selected=selected,
                    cap_limit=self._adaptive_candidate_cap_limit,
                    enabled=not self._coordinates_leave_device,
                )
                if next_cap is None:
                    raise
                selected = select_corridor_candidates(
                    route_geometry=corridor_geometry,
                    options=self._options,
                    config=replace(self._candidate_config, candidate_cap=next_cap),
                )
                continue
            return selected, graph, stations, result, soc_step_pct


def _plan_with_soc_refinement(
    *,
    origin_id: str,
    destination_id: str,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    road_network: RoadNetwork,
) -> tuple[RouteOptionSet, int]:
    """Retry only a coarse infeasible result with the documented finer SOC grid."""

    try:
        return (
            CompetitiveRoutePlanner(soc_step_pct=SOC_STEP_PCT).plan(
                origin_id=origin_id,
                destination_id=destination_id,
                vehicle=vehicle,
                stations=stations,
                road_network=road_network,
            ),
            SOC_STEP_PCT,
        )
    except NoFeasibleRouteError as coarse_error:
        try:
            return (
                CompetitiveRoutePlanner(soc_step_pct=REFINED_SOC_STEP_PCT).plan(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    vehicle=vehicle,
                    stations=stations,
                    road_network=road_network,
                ),
                REFINED_SOC_STEP_PCT,
            )
        except NoFeasibleRouteError as refined_error:
            raise coarse_error from refined_error


def _next_candidate_cap(
    *,
    selected: CandidateSelectionResult,
    cap_limit: int,
    enabled: bool,
) -> int | None:
    """Return the next bounded local-table size after an infeasible capped search."""

    selected_count = len(selected.candidates)
    if not enabled or selected_count >= selected.eligible_count or selected_count >= cap_limit:
        return None
    return min(selected.eligible_count, cap_limit, max(selected_count + 1, selected_count * 2))


def _public_config(
    *,
    mode: Literal["fixture", "integration"],
    data_label: str,
    tile_url: str,
    vehicle: VehicleInputs,
    endpoints: Sequence[PointInput],
    integration_summary: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "mode": mode,
        "data_label": data_label,
        "tile_url": tile_url,
        "tile_attribution": TILE_ATTRIBUTION,
        "endpoints": [
            {"id": point.id, "longitude": point.longitude, "latitude": point.latitude}
            for point in endpoints
        ],
        "vehicle": vehicle.to_dict(),
        "input_bounds": {
            field: [minimum, maximum] for field, (minimum, maximum) in VEHICLE_INPUT_BOUNDS.items()
        },
        "integration": dict(integration_summary or {}),
    }


def _serialize_result(
    result: RouteOptionSet,
    *,
    vehicle: VehicleProfile,
    stations: Mapping[str, Station],
    data_label: str,
    soc_step_pct: int,
    road_network: RoadNetwork,
    candidate_selection: Mapping[str, object] | None = None,
) -> dict[str, object]:
    options: list[dict[str, object]] = []
    for index, option in enumerate(result.options, start=1):
        plan = option.plan
        # Structural fallbacks are measured for the fastest plan only, which bounds the extra
        # searches; other options report null.
        redundancy: dict[str, dict[str, object]] = {}
        if RouteStrategy.FASTEST in option.strategies:
            redundancy = {
                item.station_id: {
                    "alternative_count": item.alternative_count,
                    "alternatives_checked": item.alternatives_checked,
                    "without_stop_added_minutes": item.without_stop_added_minutes,
                }
                for item in stop_redundancy(
                    plan,
                    vehicle=vehicle,
                    stations=dict(stations),
                    road_network=road_network,
                    soc_step_pct=soc_step_pct,
                )
            }
        features: list[dict[str, object]] = []
        for leg in plan.legs:
            if leg.geometry is None:
                raise ValueError("selected route leg is missing geometry")
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "origin_id": leg.origin_id,
                        "destination_id": leg.destination_id,
                    },
                    "geometry": {
                        "type": leg.geometry.type,
                        "coordinates": [list(position) for position in leg.geometry.coordinates],
                    },
                }
            )
        stops = [
            {
                "station_id": stop.station_id,
                "name": stations[stop.station_id].name,
                "longitude": stations[stop.station_id].longitude,
                "latitude": stations[stop.station_id].latitude,
                "arrival_soc_pct": stop.arrival_soc_pct,
                "departure_soc_pct": stop.departure_soc_pct,
                "energy_added_kwh": stop.energy_added_kwh,
                "charging_minutes": stop.charging_minutes,
                "redundancy": redundancy.get(stop.station_id),
            }
            for stop in plan.charging_stops
        ]
        options.append(
            {
                "id": f"option-{index}",
                "strategies": [strategy.value for strategy in option.strategies],
                "nodes": list(plan.node_ids),
                "total_distance_km": plan.total_distance_km,
                "driving_minutes": plan.driving_minutes,
                "charging_minutes": plan.charging_minutes,
                "total_minutes": plan.total_minutes,
                "arrival_soc_pct": plan.arrival_soc_pct,
                "charging_stops": stops,
                "soc_timeline": _soc_timeline(
                    option,
                    vehicle=vehicle,
                    stations=stations,
                    soc_step_pct=soc_step_pct,
                ),
                "geometry": {"type": "FeatureCollection", "features": features},
            }
        )
    return {
        "data_label": data_label,
        "estimate_label": "SOC and charging times are model estimates",
        "model_resolution": {
            "soc_step_pct": soc_step_pct,
            "refined_after_coarse_infeasible": soc_step_pct != SOC_STEP_PCT,
        },
        "candidate_selection": dict(candidate_selection or {}),
        "options": options,
        "unavailable_strategies": [strategy.value for strategy in result.unavailable_strategies],
    }


def _soc_timeline(
    option: RouteOption,
    *,
    vehicle: VehicleProfile,
    stations: Mapping[str, Station],
    soc_step_pct: int,
) -> list[dict[str, object]]:
    plan = option.plan
    bucket_kwh = vehicle.usable_battery_kwh * soc_step_pct / 100
    current_bucket = math.floor(vehicle.initial_soc_pct / soc_step_pct)
    timeline: list[dict[str, object]] = [
        {"label": "Start", "kind": "start", "soc_pct": current_bucket * soc_step_pct}
    ]
    stop_by_station = {stop.station_id: stop for stop in plan.charging_stops}
    for leg in plan.legs:
        stop = stop_by_station.get(leg.origin_id)
        if stop is not None:
            current_bucket = round(stop.departure_soc_pct / soc_step_pct)
            timeline.append(
                {
                    "label": f"Charge · {stations[stop.station_id].name}",
                    "kind": "charge",
                    "soc_pct": stop.departure_soc_pct,
                }
            )
        required = required_energy_buckets(
            energy_for_distance(
                leg.distance_km,
                vehicle.consumption_kwh_per_100km,
                vehicle.energy_safety_factor,
            ),
            bucket_kwh,
        )
        current_bucket -= required
        timeline.append(
            {
                "label": "Arrival"
                if leg.destination_id == plan.node_ids[-1]
                else (stations[leg.destination_id].name),
                "kind": "arrival" if leg.destination_id == plan.node_ids[-1] else "station",
                "soc_pct": current_bucket * soc_step_pct,
            }
        )
    return timeline


def _scenario_points(scenario: SyntheticScenario) -> tuple[PointInput, PointInput]:
    origin_position: tuple[float, float] | None = None
    destination_position: tuple[float, float] | None = None
    for leg in scenario.legs:
        if leg.geometry is None:
            continue
        if leg.origin_id == scenario.origin_id:
            origin_position = leg.geometry.coordinates[0]
        if leg.destination_id == scenario.destination_id:
            destination_position = leg.geometry.coordinates[-1]
    if origin_position is None or destination_position is None:
        raise ValueError("fixture must provide geometry for both trip endpoints")
    return (
        PointInput(scenario.origin_id, origin_position[0], origin_position[1]),
        PointInput(scenario.destination_id, destination_position[0], destination_position[1]),
    )


def _require_fixture_point(actual: PointInput, expected: PointInput, *, role: str) -> None:
    if actual.id != expected.id or not (
        math.isclose(actual.longitude, expected.longitude, abs_tol=1e-8)
        and math.isclose(actual.latitude, expected.latitude, abs_tol=1e-8)
    ):
        raise ValueError(f"fixture {role} must be selected from its declared map marker")


def _validate_tile_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("tile URL must be text")
    url = value.strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not all(token in url for token in ("{z}", "{x}", "{y}"))
    ):
        raise ValueError("tile URL must be HTTPS and contain {z}, {x}, and {y}")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("tile URL port is invalid") from error
    return url
