"""Explicit OSRM HTTP adapter and candidate-graph construction."""

from __future__ import annotations

import ipaddress
import json
import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from chargepath.models import GeoJsonLineString, RoadLeg, RoutePlan
from chargepath.providers.base import CoordinateLike, RoadRouteClient, RoadTableClient
from chargepath.providers.static import StaticRoadNetwork

DEFAULT_OSRM_ENDPOINT = "http://127.0.0.1:5000"
DEFAULT_OSRM_PROFILE = "driving"
DEFAULT_OSRM_TIMEOUT_SECONDS = 10.0
OSRM_DECIMAL_ROUNDING_TOLERANCE = 0.1
MAX_GEOMETRY_WORKERS = 4


class OsrmProviderError(RuntimeError):
    """Base error for an explicit OSRM provider failure."""


class OsrmTimeoutError(OsrmProviderError):
    """Raised when an OSRM request exceeds its configured timeout."""


class OsrmTransportError(OsrmProviderError):
    """Raised when OSRM cannot be reached or returns an HTTP failure."""


class OsrmResponseError(OsrmProviderError):
    """Raised for malformed JSON, a non-Ok code, or an invalid OSRM shape."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Coordinate:
    """WGS84 coordinate serialized for OSRM as ``longitude,latitude``."""

    longitude: float
    latitude: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.longitude, bool)
            or not isinstance(self.longitude, (int, float))
            or not math.isfinite(self.longitude)
            or not -180 <= self.longitude <= 180
        ):
            raise ValueError("longitude must be finite and between -180 and 180")
        if (
            isinstance(self.latitude, bool)
            or not isinstance(self.latitude, (int, float))
            or not math.isfinite(self.latitude)
            or not -90 <= self.latitude <= 90
        ):
            raise ValueError("latitude must be finite and between -90 and 90")

    def serialize(self) -> str:
        """Use deterministic decimal degrees without changing axis order."""
        return f"{_format_coordinate(self.longitude)},{_format_coordinate(self.latitude)}"


@dataclass(frozen=True, slots=True)
class CandidateNode:
    id: str
    coordinate: Coordinate

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("candidate node id must not be empty")
        if not isinstance(self.coordinate, Coordinate):
            raise ValueError("candidate node coordinate must be a Coordinate")


@dataclass(frozen=True, slots=True)
class OsrmTableCost:
    distance_km: float
    duration_minutes: float

    def __post_init__(self) -> None:
        for value in (self.distance_km, self.duration_minutes):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("OSRM table costs must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class OsrmTableResult:
    cells: tuple[tuple[OsrmTableCost | None, ...], ...]
    data_version: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.cells, tuple) or any(
            not isinstance(row, tuple)
            or any(cell is not None and not isinstance(cell, OsrmTableCost) for cell in row)
            for row in self.cells
        ):
            raise ValueError("OSRM table cells must be tuples of costs or nulls")
        if self.data_version is not None and (
            not isinstance(self.data_version, str) or not self.data_version.strip()
        ):
            raise ValueError("OSRM data_version must be non-empty text when supplied")


@dataclass(frozen=True, slots=True)
class CandidateGraph:
    """Optimizer-facing graph plus the coordinates used to create it."""

    nodes: tuple[CandidateNode, ...]
    legs: tuple[RoadLeg, ...]
    _network: StaticRoadNetwork = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple) or any(
            not isinstance(node, CandidateNode) for node in self.nodes
        ):
            raise ValueError("candidate graph nodes must be CandidateNode values")
        if not isinstance(self.legs, tuple) or any(
            not isinstance(leg, RoadLeg) for leg in self.legs
        ):
            raise ValueError("candidate graph legs must be RoadLeg values")
        ids = tuple(node.id for node in self.nodes)
        if len(ids) < 2:
            raise ValueError("candidate graph requires at least two nodes")
        if len(set(ids)) != len(ids):
            raise ValueError("candidate node ids must be unique")
        declared_ids = set(ids)
        if any(
            leg.origin_id not in declared_ids or leg.destination_id not in declared_ids
            for leg in self.legs
        ):
            raise ValueError("candidate graph legs must reference declared nodes")
        object.__setattr__(self, "_network", StaticRoadNetwork(self.legs))

    def neighbors(self, node_id: str) -> tuple[RoadLeg, ...]:
        return self._network.neighbors(node_id)

    def coordinate_for(self, node_id: str) -> Coordinate:
        for node in self.nodes:
            if node.id == node_id:
                return node.coordinate
        raise KeyError(f"unknown candidate node: {node_id}")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("HTTP response status_code must be a valid status code")
        if not isinstance(self.body, bytes):
            raise ValueError("HTTP response body must be bytes")


class HttpTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        """Perform one HTTP GET without provider-specific parsing."""
        ...


class UrllibHttpTransport:
    """Small standard-library transport; tests inject an offline fake."""

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "ChargePath/0.1"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                status = response.status
                body = response.read()
        except HTTPError as error:
            status = error.code
            body = error.read()
        except TimeoutError as error:
            raise OsrmTimeoutError(f"OSRM request timed out after {timeout_seconds:g}s") from error
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise OsrmTimeoutError(
                    f"OSRM request timed out after {timeout_seconds:g}s"
                ) from error
            raise OsrmTransportError(f"OSRM request failed: {error.reason}") from error
        return HttpResponse(status_code=status, body=body)


class OsrmHttpClient(RoadTableClient, RoadRouteClient):
    """OSRM Table and Route services behind separate client protocols."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_OSRM_ENDPOINT,
        profile: str = DEFAULT_OSRM_PROFILE,
        timeout_seconds: float = DEFAULT_OSRM_TIMEOUT_SECONDS,
        allow_remote_endpoint: bool = False,
        transport: HttpTransport | None = None,
    ) -> None:
        if not isinstance(allow_remote_endpoint, bool):
            raise ValueError("allow_remote_endpoint must be boolean")
        self._endpoint = _validate_endpoint(endpoint, allow_remote=allow_remote_endpoint)
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("OSRM profile must not be empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("OSRM timeout_seconds must be positive and finite")
        self._profile = profile.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibHttpTransport()

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        if len(coordinates) < 2:
            raise ValueError("OSRM table requires at least two coordinates")
        url = self._service_url(
            "table",
            coordinates,
            {"annotations": "duration,distance", "sources": "all", "destinations": "all"},
        )
        payload = self._request_payload(url)
        size = len(coordinates)
        durations = _matrix(payload.get("durations"), field="durations", size=size)
        distances = _matrix(payload.get("distances"), field="distances", size=size)

        rows: list[tuple[OsrmTableCost | None, ...]] = []
        for row_index in range(size):
            row: list[OsrmTableCost | None] = []
            for column_index in range(size):
                duration = durations[row_index][column_index]
                distance = distances[row_index][column_index]
                if (duration is None) != (distance is None):
                    raise OsrmResponseError(
                        "OSRM table duration and distance nullability must match"
                    )
                if duration is None:
                    assert distance is None
                    row.append(None)
                    continue
                assert distance is not None
                if (
                    duration < -OSRM_DECIMAL_ROUNDING_TOLERANCE
                    or distance < -OSRM_DECIMAL_ROUNDING_TOLERANCE
                ):
                    raise OsrmResponseError("OSRM table values must not be negative")
                row.append(
                    OsrmTableCost(
                        distance_km=max(0.0, distance) / 1000.0,
                        duration_minutes=max(0.0, duration) / 60.0,
                    )
                )
            rows.append(tuple(row))
        return OsrmTableResult(cells=tuple(rows), data_version=_data_version(payload))

    def route_geometry(
        self,
        origin: CoordinateLike,
        destination: CoordinateLike,
    ) -> GeoJsonLineString:
        url = self._service_url(
            "route",
            (origin, destination),
            {
                "alternatives": "false",
                "steps": "false",
                "geometries": "geojson",
                "overview": "full",
            },
        )
        payload = self._request_payload(url)
        routes = _array(payload.get("routes"), field="routes")
        if not routes:
            raise OsrmResponseError("OSRM Ok response did not contain a route")
        route = _object(routes[0], field="routes[0]")
        geometry = _object(route.get("geometry"), field="routes[0].geometry")
        if geometry.get("type") != "LineString":
            raise OsrmResponseError("OSRM route geometry must be a GeoJSON LineString")
        coordinate_rows = _array(geometry.get("coordinates"), field="geometry.coordinates")
        positions: list[tuple[float, float]] = []
        for index, value in enumerate(coordinate_rows):
            position = _array(value, field=f"geometry.coordinates[{index}]")
            if len(position) != 2:
                raise OsrmResponseError("OSRM GeoJSON positions must have two values")
            positions.append(
                (
                    _number(position[0], field="geometry longitude"),
                    _number(position[1], field="geometry latitude"),
                )
            )
        try:
            return GeoJsonLineString(tuple(positions))
        except ValueError as error:
            raise OsrmResponseError(f"invalid OSRM route geometry: {error}") from error

    def _service_url(
        self,
        service: str,
        coordinates: Sequence[CoordinateLike],
        query: Mapping[str, str],
    ) -> str:
        coordinate_path = ";".join(
            Coordinate(item.longitude, item.latitude).serialize() for item in coordinates
        )
        path = (
            f"{self._endpoint}/{service}/v1/{quote(self._profile, safe='')}/"
            f"{quote(coordinate_path, safe=',;')}"
        )
        return f"{path}?{urlencode(query)}"

    def _request_payload(self, url: str) -> dict[str, object]:
        try:
            response = self._transport.get(url, timeout_seconds=self._timeout_seconds)
        except OsrmProviderError:
            raise
        except TimeoutError as error:
            raise OsrmTimeoutError(
                f"OSRM request timed out after {self._timeout_seconds:g}s"
            ) from error
        except OSError as error:
            raise OsrmTransportError(f"OSRM request failed: {error}") from error

        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OsrmResponseError("OSRM returned malformed JSON") from error
        payload = _object(decoded, field="response")
        code = payload.get("code")
        if not isinstance(code, str):
            raise OsrmResponseError("OSRM response code must be a string")
        if code != "Ok":
            message = payload.get("message")
            detail = message if isinstance(message, str) and message else "request was rejected"
            raise OsrmResponseError(f"OSRM {code}: {detail}", code=code)
        if response.status_code != 200:
            raise OsrmTransportError(f"OSRM returned HTTP {response.status_code} with code Ok")
        return payload


class CandidateGraphBuilder:
    """Convert an OSRM Table result into deterministic optimizer road legs."""

    def __init__(self, table_client: RoadTableClient) -> None:
        self._table_client = table_client

    def build(self, nodes: Sequence[CandidateNode]) -> CandidateGraph:
        ordered_nodes = tuple(nodes)
        if len(ordered_nodes) < 2:
            raise ValueError("candidate graph requires at least two nodes")
        ids = tuple(node.id for node in ordered_nodes)
        if len(set(ids)) != len(ids):
            raise ValueError("candidate node ids must be unique")
        table = self._table_client.table(tuple(node.coordinate for node in ordered_nodes))
        if len(table.cells) != len(ordered_nodes) or any(
            len(row) != len(ordered_nodes) for row in table.cells
        ):
            raise OsrmResponseError("road table dimensions do not match candidate nodes")

        legs: list[RoadLeg] = []
        for source_index, source in enumerate(ordered_nodes):
            for destination_index, destination in enumerate(ordered_nodes):
                if source_index == destination_index:
                    continue
                cell = table.cells[source_index][destination_index]
                if cell is None:
                    continue
                if not math.isfinite(cell.distance_km) or not math.isfinite(cell.duration_minutes):
                    raise OsrmResponseError("road table costs must be finite")
                if cell.distance_km < 0 or cell.duration_minutes < 0:
                    raise OsrmResponseError("road table costs must not be negative")
                if cell.distance_km <= 0 or cell.duration_minutes <= 0:
                    continue
                legs.append(
                    RoadLeg(
                        origin_id=source.id,
                        destination_id=destination.id,
                        distance_km=cell.distance_km,
                        duration_minutes=cell.duration_minutes,
                    )
                )
        return CandidateGraph(nodes=ordered_nodes, legs=tuple(legs))


def fetch_selected_plan_geometry(
    plan: RoutePlan,
    *,
    graph: CandidateGraph,
    route_client: RoadRouteClient,
) -> RoutePlan:
    """Fetch geometry only for the already optimized plan's selected legs."""
    geometric_legs: list[RoadLeg] = []
    for leg in plan.legs:
        geometry = route_client.route_geometry(
            graph.coordinate_for(leg.origin_id),
            graph.coordinate_for(leg.destination_id),
        )
        geometric_legs.append(replace(leg, geometry=geometry))
    return replace(plan, legs=tuple(geometric_legs))


def fetch_selected_plans_geometry(
    plans: Sequence[RoutePlan],
    *,
    graph: CandidateGraph,
    route_client: RoadRouteClient,
    known_geometries: Mapping[tuple[str, str], GeoJsonLineString] | None = None,
) -> tuple[RoutePlan, ...]:
    """Fetch each distinct selected directed leg once using bounded concurrency."""
    geometry_by_edge = dict(known_geometries or {})
    missing_edges: list[tuple[str, str]] = []
    missing_edge_set: set[tuple[str, str]] = set()
    for plan in plans:
        for leg in plan.legs:
            edge = (leg.origin_id, leg.destination_id)
            if edge not in geometry_by_edge and edge not in missing_edge_set:
                missing_edges.append(edge)
                missing_edge_set.add(edge)

    def fetch(edge: tuple[str, str]) -> GeoJsonLineString:
        origin_id, destination_id = edge
        return route_client.route_geometry(
            graph.coordinate_for(origin_id),
            graph.coordinate_for(destination_id),
        )

    if missing_edges:
        worker_count = min(MAX_GEOMETRY_WORKERS, len(missing_edges))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            geometries = executor.map(fetch, missing_edges)
            geometry_by_edge.update(zip(missing_edges, geometries, strict=True))
    return tuple(
        replace(
            plan,
            legs=tuple(
                replace(leg, geometry=geometry_by_edge[(leg.origin_id, leg.destination_id)])
                for leg in plan.legs
            ),
        )
        for plan in plans
    )


def _format_coordinate(value: float) -> str:
    if value == 0:
        value = 0.0
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"


def _validate_endpoint(endpoint: str, *, allow_remote: bool) -> str:
    if not isinstance(endpoint, str):
        raise ValueError("OSRM endpoint must be text")
    normalized = endpoint.strip().rstrip("/")
    if any(character.isspace() for character in normalized):
        raise ValueError("OSRM endpoint must not contain whitespace")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("OSRM endpoint must be an explicit http(s) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("OSRM endpoint must not contain a query or fragment")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("OSRM endpoint port is invalid") from error
    if not allow_remote and not _is_loopback(parsed.hostname):
        raise ValueError("remote OSRM endpoints require allow_remote_endpoint=True")
    return normalized


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise OsrmResponseError(f"OSRM {field} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise OsrmResponseError(f"OSRM {field} must be an array")
    return cast(list[object], value)


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OsrmResponseError(f"OSRM {field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise OsrmResponseError(f"OSRM {field} must be finite")
    return number


def _matrix(value: object, *, field: str, size: int) -> tuple[tuple[float | None, ...], ...]:
    rows = _array(value, field=field)
    if len(rows) != size:
        raise OsrmResponseError(f"OSRM {field} row count does not match request")
    parsed_rows: list[tuple[float | None, ...]] = []
    for row_index, value_row in enumerate(rows):
        cells = _array(value_row, field=f"{field}[{row_index}]")
        if len(cells) != size:
            raise OsrmResponseError(f"OSRM {field} column count does not match request")
        parsed_rows.append(
            tuple(None if cell is None else _number(cell, field=f"{field} cell") for cell in cells)
        )
    return tuple(parsed_rows)


def _data_version(payload: Mapping[str, object]) -> str | None:
    value = payload.get("data_version")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OsrmResponseError("OSRM data_version must be a non-empty string when supplied")
    return value
