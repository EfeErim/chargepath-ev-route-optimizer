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
from chargepath.providers.base import CoordinateLike
from chargepath.providers.static import StaticRoadNetwork

DEFAULT_OSRM_ENDPOINT = "http://127.0.0.1:5000"
OSRM_PROFILE = "driving"
DEFAULT_OSRM_TIMEOUT_SECONDS = 10.0
OSRM_DECIMAL_ROUNDING_TOLERANCE = 0.1
# OSRM returned distances down to -0.5 m between near-coincident points on a real Turkey graph.
OSRM_NEGATIVE_DISTANCE_TOLERANCE_METERS = 1.0
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
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be finite and between -180 and 180")
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be finite and between -90 and 90")

    def serialize(self) -> str:
        """Use deterministic decimal degrees without changing axis order."""
        return f"{_format_coordinate(self.longitude)},{_format_coordinate(self.latitude)}"


@dataclass(frozen=True, slots=True)
class CandidateNode:
    id: str
    coordinate: Coordinate


@dataclass(frozen=True, slots=True)
class OsrmTableCost:
    distance_km: float
    duration_minutes: float


@dataclass(frozen=True, slots=True)
class OsrmTableResult:
    cells: tuple[tuple[OsrmTableCost | None, ...], ...]
    data_version: str | None


@dataclass(frozen=True, slots=True)
class CandidateGraph:
    """Optimizer-facing graph plus the coordinates used to create it."""

    nodes: tuple[CandidateNode, ...]
    legs: tuple[RoadLeg, ...]
    _network: StaticRoadNetwork = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
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


class RoadTableClient(Protocol):
    """Source of the pairwise cost table.

    A protocol rather than ``OsrmHttpClient`` itself so tests and the offline demo can supply a
    table without HTTP.
    """

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        """Return directed fastest-route costs for every coordinate pair."""
        ...


class RoadRouteClient(Protocol):
    """Source of geometry for selected legs; tests substitute a recording spy."""

    def route_geometry(
        self,
        origin: CoordinateLike,
        destination: CoordinateLike,
    ) -> GeoJsonLineString:
        """Return full GeoJSON geometry for one selected directed leg."""
        ...


class HttpTransport(Protocol):
    """One HTTP GET. Tests inject offline fakes, including one that times out."""

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        """Perform one HTTP GET without provider-specific parsing."""
        ...


class UrllibHttpTransport:
    """Small standard-library transport; tests inject an offline fake."""

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "ChargePath"},
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


class OsrmHttpClient:
    """OSRM Table and Route services behind separate client protocols."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_OSRM_ENDPOINT,
        timeout_seconds: float = DEFAULT_OSRM_TIMEOUT_SECONDS,
        allow_remote_endpoint: bool = False,
        transport: HttpTransport | None = None,
    ) -> None:
        if not isinstance(allow_remote_endpoint, bool):
            raise ValueError("allow_remote_endpoint must be boolean")
        self._endpoint = _validate_endpoint(endpoint, allow_remote=allow_remote_endpoint)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("OSRM timeout_seconds must be positive and finite")
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
                    or distance < -OSRM_NEGATIVE_DISTANCE_TOLERANCE_METERS
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
        path = f"{self._endpoint}/{service}/v1/{OSRM_PROFILE}/{quote(coordinate_path, safe=',;')}"
        return f"{path}?{urlencode(query)}"

    def _request_payload(self, url: str) -> dict[str, object]:
        try:
            response = self._transport.get(url, timeout_seconds=self._timeout_seconds)
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
                # OsrmTableCost already rejects negative and non-finite costs; a zero cost
                # (coincident points) is not a usable leg.
                if cell is None or cell.distance_km == 0 or cell.duration_minutes == 0:
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


def fetch_selected_plans_geometry(
    plans: Sequence[RoutePlan],
    *,
    graph: CandidateGraph,
    route_client: RoadRouteClient,
    known_geometries: Mapping[tuple[str, str], GeoJsonLineString] | None = None,
) -> tuple[RoutePlan, ...]:
    """Fetch each distinct selected directed leg once using bounded concurrency."""
    geometry_by_edge = dict(known_geometries or {})
    # A dict keeps first-seen order, so the fetch order is deterministic.
    missing_edges = list(
        dict.fromkeys(
            (leg.origin_id, leg.destination_id)
            for plan in plans
            for leg in plan.legs
            if (leg.origin_id, leg.destination_id) not in geometry_by_edge
        )
    )

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
