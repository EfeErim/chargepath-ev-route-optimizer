"""Loopback-only fixture-first web demo for ChargePath."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from chargepath.alternatives import CompetitiveRoutePlanner, RouteOption, RouteOptionSet
from chargepath.corridor import (
    DEFAULT_CANDIDATE_SELECTION_CONFIG,
    CandidateSelectionConfig,
    CandidateSelectionResult,
    select_corridor_candidates,
)
from chargepath.energy import energy_for_distance, required_energy_buckets
from chargepath.fixtures import SyntheticScenario, load_synthetic_scenario
from chargepath.models import GeoJsonLineString, NoFeasibleRouteError, Station, VehicleProfile
from chargepath.providers.base import RoadNetwork
from chargepath.providers.osrm import (
    DEFAULT_OSRM_ENDPOINT,
    CandidateGraph,
    CandidateGraphBuilder,
    CandidateNode,
    Coordinate,
    OsrmHttpClient,
    OsrmProviderError,
    fetch_selected_plans_geometry,
)
from chargepath.providers.static import StaticRoadNetwork
from chargepath.station_data import (
    SnapshotProvenance,
    normalize_epdk_response,
    project_ccs2_dc_options,
)

MAX_REQUEST_BYTES = 32_768
DEFAULT_PORT = 8743
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ATTRIBUTION = "© OpenStreetMap contributors"
SOC_STEP_PCT = 5
REFINED_SOC_STEP_PCT = 2
DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT = 200
HEALTH_API_VERSION = 3


@dataclass(frozen=True, slots=True)
class PointInput:
    id: str
    longitude: float
    latitude: float

    def __post_init__(self) -> None:
        _text(self.id, field="point.id")
        _bounded_number(self.longitude, field="point.longitude", minimum=-180, maximum=180)
        _bounded_number(self.latitude, field="point.latitude", minimum=-90, maximum=90)

    @classmethod
    def from_value(cls, value: object, *, field: str) -> PointInput:
        row = _exact_object(value, field=field, expected={"id", "longitude", "latitude"})
        identifier = _text(row["id"], field=f"{field}.id")
        return cls(
            id=identifier,
            longitude=_bounded_number(
                row["longitude"], field=f"{field}.longitude", minimum=-180, maximum=180
            ),
            latitude=_bounded_number(
                row["latitude"], field=f"{field}.latitude", minimum=-90, maximum=90
            ),
        )


@dataclass(frozen=True, slots=True)
class VehicleInputs:
    usable_battery_kwh: float
    initial_soc_pct: float
    consumption_kwh_per_100km: float
    max_dc_power_kw: float
    reserve_soc_pct: float
    energy_safety_factor: float

    def __post_init__(self) -> None:
        for field, value, minimum, maximum in (
            ("usable_battery_kwh", self.usable_battery_kwh, 20, 200),
            ("initial_soc_pct", self.initial_soc_pct, 5, 100),
            ("consumption_kwh_per_100km", self.consumption_kwh_per_100km, 8, 40),
            ("max_dc_power_kw", self.max_dc_power_kw, 20, 400),
            ("reserve_soc_pct", self.reserve_soc_pct, 5, 40),
            ("energy_safety_factor", self.energy_safety_factor, 1, 1.5),
        ):
            _bounded_number(value, field=f"vehicle.{field}", minimum=minimum, maximum=maximum)
        if self.initial_soc_pct < self.reserve_soc_pct:
            raise ValueError("initial SOC must be at least the reserve SOC")

    @classmethod
    def from_value(cls, value: object) -> VehicleInputs:
        expected = {
            "usable_battery_kwh",
            "initial_soc_pct",
            "consumption_kwh_per_100km",
            "max_dc_power_kw",
            "reserve_soc_pct",
            "energy_safety_factor",
        }
        row = _exact_object(value, field="vehicle", expected=expected)
        result = cls(
            usable_battery_kwh=_bounded_number(
                row["usable_battery_kwh"],
                field="vehicle.usable_battery_kwh",
                minimum=20,
                maximum=200,
            ),
            initial_soc_pct=_bounded_number(
                row["initial_soc_pct"],
                field="vehicle.initial_soc_pct",
                minimum=5,
                maximum=100,
            ),
            consumption_kwh_per_100km=_bounded_number(
                row["consumption_kwh_per_100km"],
                field="vehicle.consumption_kwh_per_100km",
                minimum=8,
                maximum=40,
            ),
            max_dc_power_kw=_bounded_number(
                row["max_dc_power_kw"],
                field="vehicle.max_dc_power_kw",
                minimum=20,
                maximum=400,
            ),
            reserve_soc_pct=_bounded_number(
                row["reserve_soc_pct"],
                field="vehicle.reserve_soc_pct",
                minimum=5,
                maximum=40,
            ),
            energy_safety_factor=_bounded_number(
                row["energy_safety_factor"],
                field="vehicle.energy_safety_factor",
                minimum=1,
                maximum=1.5,
            ),
        )
        if result.initial_soc_pct < result.reserve_soc_pct:
            raise ValueError("initial SOC must be at least the reserve SOC")
        return result

    @classmethod
    def from_profile(cls, vehicle: VehicleProfile) -> VehicleInputs:
        return cls(
            usable_battery_kwh=vehicle.usable_battery_kwh,
            initial_soc_pct=vehicle.initial_soc_pct,
            consumption_kwh_per_100km=vehicle.consumption_kwh_per_100km,
            max_dc_power_kw=vehicle.max_dc_power_kw,
            reserve_soc_pct=vehicle.reserve_soc_pct,
            energy_safety_factor=vehicle.energy_safety_factor,
        )

    def to_profile(self) -> VehicleProfile:
        return VehicleProfile(
            name="Demo EV",
            usable_battery_kwh=self.usable_battery_kwh,
            initial_soc_pct=self.initial_soc_pct,
            consumption_kwh_per_100km=self.consumption_kwh_per_100km,
            max_dc_power_kw=self.max_dc_power_kw,
            reserve_soc_pct=self.reserve_soc_pct,
            energy_safety_factor=self.energy_safety_factor,
            supported_dc_connectors=("CCS2",),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "usable_battery_kwh": self.usable_battery_kwh,
            "initial_soc_pct": self.initial_soc_pct,
            "consumption_kwh_per_100km": self.consumption_kwh_per_100km,
            "max_dc_power_kw": self.max_dc_power_kw,
            "reserve_soc_pct": self.reserve_soc_pct,
            "energy_safety_factor": self.energy_safety_factor,
        }


@dataclass(frozen=True, slots=True)
class PlanRequest:
    origin: PointInput
    destination: PointInput
    vehicle: VehicleInputs

    def __post_init__(self) -> None:
        if not isinstance(self.origin, PointInput) or not isinstance(self.destination, PointInput):
            raise ValueError("plan endpoints must be PointInput values")
        if not isinstance(self.vehicle, VehicleInputs):
            raise ValueError("plan vehicle must be VehicleInputs")
        if self.origin.id == self.destination.id:
            raise ValueError("origin and destination must be different")

    @classmethod
    def from_payload(cls, payload: object) -> PlanRequest:
        row = _exact_object(payload, field="request", expected={"origin", "destination", "vehicle"})
        origin = PointInput.from_value(row["origin"], field="origin")
        destination = PointInput.from_value(row["destination"], field="destination")
        if origin.id == destination.id:
            raise ValueError("origin and destination must be different")
        return cls(
            origin=origin,
            destination=destination,
            vehicle=VehicleInputs.from_value(row["vehicle"]),
        )


class PlanningService(Protocol):
    def public_config(self) -> dict[str, object]: ...

    def plan(self, request: PlanRequest) -> dict[str, object]: ...


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
        result, soc_step_pct = _plan_with_soc_refinement(
            origin_id=self._scenario.origin_id,
            destination_id=self._scenario.destination_id,
            vehicle=vehicle,
            stations=stations,
            road_network=StaticRoadNetwork(self._scenario.legs),
        )
        return _serialize_result(
            result,
            vehicle=vehicle,
            stations=stations,
            soc_step_pct=soc_step_pct,
            data_label="Synthetic fixture — freshness not applicable",
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
            graph = CandidateGraphBuilder(self._client).build(nodes)
            stations = {option.station.id: option.station for option in candidate_options}
            try:
                result, soc_step_pct = _plan_with_soc_refinement(
                    origin_id="origin",
                    destination_id="destination",
                    vehicle=vehicle,
                    stations=stations,
                    road_network=graph,
                )
            except NoFeasibleRouteError:
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


@dataclass(frozen=True, slots=True)
class DemoServerConfig:
    service: PlanningService
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 0 <= self.port <= 65_535
        ):
            raise ValueError("port must be between 0 and 65535")


class DemoHttpServer(ThreadingHTTPServer):
    service: PlanningService


class DemoRequestHandler(BaseHTTPRequestHandler):
    server_version = "ChargePathDemo/0.1"

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", maxsplit=1)[0]
        if path == "/api/config":
            self._send_json(HTTPStatus.OK, self._demo_server.service.public_config())
            return
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "chargepath-demo",
                    "api_version": HEALTH_API_VERSION,
                },
            )
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/leaflet.css": ("leaflet.css", "text/css; charset=utf-8"),
            "/leaflet.js": ("leaflet.js", "text/javascript; charset=utf-8"),
        }
        asset = assets.get(path)
        if asset is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        name, content_type = asset
        payload = files("chargepath.web").joinpath(name).read_bytes()
        self._send_bytes(HTTPStatus.OK, payload, content_type=content_type)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", maxsplit=1)[0] != "/api/plan":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Content-Type must be application/json"},
            )
            return
        length = self.headers.get("Content-Length")
        if length is None:
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length is required"})
            return
        try:
            byte_count = int(length)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return
        if not 0 < byte_count <= MAX_REQUEST_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"Request body must be between 1 and {MAX_REQUEST_BYTES} bytes"},
            )
            return
        try:
            payload = json.loads(self.rfile.read(byte_count))
            request = PlanRequest.from_payload(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error) or "Invalid request"})
            return
        try:
            result = self._demo_server.service.plan(request)
        except NoFeasibleRouteError as error:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
            return
        except OsrmProviderError as error:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
            return
        except (KeyError, ValueError):
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal planning error"})
            return
        self._send_json(HTTPStatus.OK, result)

    @property
    def _demo_server(self) -> DemoHttpServer:
        return cast(DemoHttpServer, self.server)

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, content_type="application/json; charset=utf-8")

    def _send_bytes(self, status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(config: DemoServerConfig) -> DemoHttpServer:
    """Create a server that can only listen on IPv4 loopback."""
    server = DemoHttpServer(("127.0.0.1", config.port), DemoRequestHandler)
    server.service = config.service
    return server


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
            "usable_battery_kwh": [20, 200],
            "initial_soc_pct": [5, 100],
            "consumption_kwh_per_100km": [8, 40],
            "max_dc_power_kw": [20, 400],
            "reserve_soc_pct": [5, 40],
            "energy_safety_factor": [1, 1.5],
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
    candidate_selection: Mapping[str, object] | None = None,
) -> dict[str, object]:
    options: list[dict[str, object]] = []
    for index, option in enumerate(result.options, start=1):
        plan = option.plan
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


def _exact_object(value: object, *, field: str, expected: set[str]) -> dict[str, object]:
    row = _object(value, field=field)
    actual = set(row)
    if actual != expected:
        raise ValueError(
            f"{field} fields changed; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return row


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _bounded_number(value: object, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    return number


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


def _default_fixture_path() -> Path:
    repository_fixture = (
        Path(__file__).resolve().parents[2] / "data" / "sample" / "synthetic_corridor.json"
    )
    if repository_fixture.is_file():
        return repository_fixture
    return Path(sys.prefix) / "share" / "chargepath" / "synthetic_corridor.json"


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "integration"), default="fixture")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tile-url", default=DEFAULT_TILE_URL)
    parser.add_argument("--fixture", type=Path, default=_default_fixture_path())
    parser.add_argument("--osrm-endpoint", default=DEFAULT_OSRM_ENDPOINT)
    parser.add_argument(
        "--allow-remote-osrm",
        action="store_true",
        help="Allow the explicitly configured non-loopback OSRM endpoint",
    )
    parser.add_argument(
        "--candidate-cap",
        type=int,
        default=DEFAULT_CANDIDATE_SELECTION_CONFIG.candidate_cap,
        help="Maximum compatible charging sites included in the OSRM table",
    )
    parser.add_argument(
        "--adaptive-candidate-cap-limit",
        type=int,
        default=DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT,
        help="Local-only maximum candidate cap after a capped search is infeasible",
    )
    parser.add_argument("--station-snapshot", type=Path)
    parser.add_argument("--station-manifest", type=Path)
    return parser


def _service_from_args(args: argparse.Namespace) -> PlanningService:
    if args.mode == "fixture":
        return FixturePlanningService(
            load_synthetic_scenario(cast(Path, args.fixture)),
            tile_url=cast(str, args.tile_url),
        )
    snapshot = cast(Path | None, args.station_snapshot)
    manifest = cast(Path | None, args.station_manifest)
    if snapshot is None or manifest is None:
        raise ValueError("integration mode requires --station-snapshot and --station-manifest")
    return IntegrationPlanningService(
        snapshot_path=snapshot,
        manifest_path=manifest,
        osrm_endpoint=cast(str, args.osrm_endpoint),
        tile_url=cast(str, args.tile_url),
        allow_remote_osrm=cast(bool, args.allow_remote_osrm),
        candidate_cap=cast(int, args.candidate_cap),
        adaptive_candidate_cap_limit=cast(int, args.adaptive_candidate_cap_limit),
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        service = _service_from_args(args)
        server = create_server(DemoServerConfig(service=service, port=cast(int, args.port)))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    host = cast(str, server.server_address[0])
    port = server.server_address[1]
    print(f"ChargePath demo: http://{host}:{port}")
    print(f"Mode: {service.public_config()['mode']}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
