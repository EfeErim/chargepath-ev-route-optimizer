"""Planning services behind the demo API: fixture mode and OSRM integration mode.

Each service plans a request, then analyzes the returned options (replay for the battery timeline
and, for the fastest plan, structural redundancy). ``responses`` only formats that analysis. This
module has no HTTP dependency, so the same services run in the browser build.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from chargepath.alternatives import CompetitiveRoutePlanner, RouteOptionSet, RouteStrategy
from chargepath.corridor import (
    DEFAULT_CANDIDATE_SELECTION_CONFIG,
    CandidateSelectionConfig,
    CandidateSelectionResult,
    select_corridor_candidates,
)
from chargepath.demo.responses import (
    AnalyzedOption,
    AnalyzedTrip,
    ConfigPayload,
    ErrorPayload,
    PlanPayload,
    config_payload,
    plan_payload,
)
from chargepath.demo.schema import PlanRequest, PointInput, VehicleInputs
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
from chargepath.station_data import load_snapshot_options
from chargepath.verification import verify_route_plan

DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
SOC_STEP_PCT = 5
REFINED_SOC_STEP_PCT = 2
# osrm-routed rejects larger Table requests unless it is started with a higher --max-table-size.
OSRM_DEFAULT_MAX_TABLE_LOCATIONS = 100
# The Table request also carries the origin and destination.
DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT = OSRM_DEFAULT_MAX_TABLE_LOCATIONS - 2
FIXTURE_DATA_LABEL = "Synthetic fixture — freshness not applicable"


class PlanningService(Protocol):
    """Implemented by the fixture and integration services; the server and browser use either."""

    def public_config(self) -> ConfigPayload: ...

    def plan(self, request: PlanRequest) -> PlanPayload: ...


def plan_response(
    service: PlanningService, payload: object
) -> tuple[int, PlanPayload | ErrorPayload]:
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

    def public_config(self) -> ConfigPayload:
        return config_payload(
            mode="fixture",
            data_label=FIXTURE_DATA_LABEL,
            tile_url=self._tile_url,
            vehicle=VehicleInputs.from_profile(self._scenario.vehicle),
            endpoints=self._points,
        )

    def plan(self, request: PlanRequest) -> PlanPayload:
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
        trip = analyze_options(
            result,
            vehicle=vehicle,
            stations=stations,
            road_network=network,
            soc_step_pct=soc_step_pct,
        )
        return plan_payload(trip, data_label=FIXTURE_DATA_LABEL)


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
        if (
            isinstance(adaptive_candidate_cap_limit, bool)
            or not isinstance(adaptive_candidate_cap_limit, int)
            or adaptive_candidate_cap_limit < candidate_cap
        ):
            raise ValueError(
                "adaptive candidate cap limit must be an integer at least candidate_cap"
            )
        provenance, self._options = load_snapshot_options(snapshot_path, manifest_path)
        if provenance.reuse_status == "prohibited":
            raise ValueError("station manifest prohibits use of this snapshot")
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

    def public_config(self) -> ConfigPayload:
        return config_payload(
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

    def plan(self, request: PlanRequest) -> PlanPayload:
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
        geometric_result = replace(
            result,
            options=tuple(
                replace(option, plan=plan)
                for option, plan in zip(result.options, geometric_plans, strict=True)
            ),
        )
        trip = analyze_options(
            geometric_result,
            vehicle=vehicle,
            stations=stations,
            road_network=graph,
            soc_step_pct=soc_step_pct,
        )
        return plan_payload(
            trip,
            data_label=self._data_label,
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
    """Retry only a coarse infeasible result with the documented finer SOC grid.

    If both fail, the coarse error is raised, chained to the refined one.
    """

    errors: list[NoFeasibleRouteError] = []
    for soc_step_pct in (SOC_STEP_PCT, REFINED_SOC_STEP_PCT):
        try:
            result = CompetitiveRoutePlanner(soc_step_pct=soc_step_pct).plan(
                origin_id=origin_id,
                destination_id=destination_id,
                vehicle=vehicle,
                stations=stations,
                road_network=road_network,
            )
        except NoFeasibleRouteError as error:
            errors.append(error)
            continue
        return result, soc_step_pct
    coarse_error, refined_error = errors
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


def analyze_options(
    result: RouteOptionSet,
    *,
    vehicle: VehicleProfile,
    stations: Mapping[str, Station],
    road_network: RoadNetwork,
    soc_step_pct: int,
) -> AnalyzedTrip:
    """Compute everything the response shows beyond the plans themselves.

    Each option is replayed once for its battery timeline. Structural fallbacks are measured for
    the fastest plan only, which bounds the extra searches; other options report none.
    """

    analyzed: list[AnalyzedOption] = []
    for option in result.options:
        replay = verify_route_plan(
            option.plan,
            vehicle=vehicle,
            stations=dict(stations),
            soc_step_pct=soc_step_pct,
            road_network=road_network,
        )
        redundancy = None
        if RouteStrategy.FASTEST in option.strategies:
            redundancy = {
                item.station_id: item
                for item in stop_redundancy(
                    option.plan,
                    vehicle=vehicle,
                    stations=dict(stations),
                    road_network=road_network,
                    soc_step_pct=soc_step_pct,
                )
            }
        analyzed.append(AnalyzedOption(option=option, replay=replay, redundancy=redundancy))
    return AnalyzedTrip(
        options=tuple(analyzed),
        unavailable_strategies=result.unavailable_strategies,
        stations=stations,
        soc_step_pct=soc_step_pct,
        refined_after_coarse_infeasible=soc_step_pct != SOC_STEP_PCT,
    )


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
