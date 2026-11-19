from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from chargepath.models import GeoJsonLineString, RoutePlan, VehicleProfile
from chargepath.optimizer import EVRouteOptimizer
from chargepath.providers.base import CoordinateLike
from chargepath.providers.osrm import (
    DEFAULT_OSRM_ENDPOINT,
    CandidateGraph,
    CandidateGraphBuilder,
    CandidateNode,
    Coordinate,
    HttpResponse,
    OsrmHttpClient,
    OsrmProviderError,
    OsrmResponseError,
    OsrmTableCost,
    OsrmTableResult,
    OsrmTimeoutError,
    OsrmTransportError,
    fetch_selected_plans_geometry,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "osrm" / "v26.7.3"
TWO_POINTS = (Coordinate(0, 0), Coordinate(0.1, 0.1))


class FakeTransport:
    def __init__(self, payload: Mapping[str, object] | bytes, *, status_code: int = 200) -> None:
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status_code = status_code
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        self.calls.append((url, timeout_seconds))
        return HttpResponse(self.status_code, self.body)


class TimeoutTransport:
    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        raise TimeoutError


def _table(
    durations: list[list[float | None]], distances: list[list[float | None]]
) -> FakeTransport:
    return FakeTransport({"code": "Ok", "durations": durations, "distances": distances})


def test_table_request_encoding_and_loopback_default() -> None:
    assert Coordinate(longitude=29.123456789, latitude=41.000000001).serialize() == "29.12345679,41"
    transport = _table([[0, 60], [90, 0]], [[0, 1000], [1200, 0]])
    OsrmHttpClient(
        endpoint="http://localhost:5999/osrm-root", timeout_seconds=3.5, transport=transport
    ).table((Coordinate(29.1, 41.2), Coordinate(30.3, 42.4)))
    assert transport.calls == [
        (
            "http://localhost:5999/osrm-root/table/v1/driving/"
            "29.1,41.2;30.3,42.4?annotations=duration%2Cdistance&sources=all&destinations=all",
            3.5,
        )
    ]

    OsrmHttpClient(transport=transport).table(TWO_POINTS)
    assert transport.calls[1][0].startswith(f"{DEFAULT_OSRM_ENDPOINT}/table/v1/driving/")
    with pytest.raises(ValueError, match="allow_remote_endpoint"):
        OsrmHttpClient(endpoint="https://router.project-osrm.org")
    OsrmHttpClient(endpoint="https://router.project-osrm.org", allow_remote_endpoint=True)


def test_table_parses_units_asymmetry_and_null_as_unreachable_leg() -> None:
    transport = FakeTransport(
        {
            "code": "Ok",
            "data_version": "2026-08-01T00:00:00Z",
            "durations": [[0, 60, None], [120, 0, 180], [240, 300, 0]],
            "distances": [[0, 1000, None], [2500, 0, 3000], [4000, 5000, 0]],
        }
    )
    client = OsrmHttpClient(transport=transport)
    nodes = (
        CandidateNode("a", Coordinate(13, 52)),
        CandidateNode("b", Coordinate(13.1, 52.1)),
        CandidateNode("c", Coordinate(13.2, 52.2)),
    )

    result = client.table(tuple(node.coordinate for node in nodes))
    graph = CandidateGraphBuilder(client).build(nodes)

    assert result.data_version == "2026-08-01T00:00:00Z"
    assert result.cells[0][1] == OsrmTableCost(distance_km=1.0, duration_minutes=1.0)
    assert result.cells[1][0] == OsrmTableCost(distance_km=2.5, duration_minutes=2.0)
    assert result.cells[0][2] is None
    assert [leg.destination_id for leg in graph.neighbors("a")] == ["b"]


@pytest.mark.parametrize(
    ("duration", "distance"),
    # OSRM rounds to one decimal, and a local Turkey graph returned distances down to -0.5 m
    # with zero duration between points about 5 m apart.
    [(-0.1, -0.1), (0, -0.5)],
)
def test_table_clamps_sub_meter_negative_cells_to_zero(duration: float, distance: float) -> None:
    transport = _table([[0, duration], [1, 0]], [[0, distance], [1, 0]])
    result = OsrmHttpClient(transport=transport).table(TWO_POINTS)
    assert result.cells[0][1] == OsrmTableCost(distance_km=0.0, duration_minutes=0.0)


@pytest.mark.parametrize(
    ("transport", "error", "match"),
    [
        (_table([[0, -0.2], [1, 0]], [[0, 1], [1, 0]]), OsrmResponseError, "must not be negative"),
        (_table([[0, 0], [1, 0]], [[0, -1.1], [1, 0]]), OsrmResponseError, "must not be negative"),
        (
            _table([[0, None], [1, 0]], [[0, 1], [1, 0]]),
            OsrmResponseError,
            "nullability must match",
        ),
        (_table([[0, 1]], [[0, 1], [1, 0]]), OsrmResponseError, "row count"),
        (
            FakeTransport({"code": "TooBig", "message": "x"}, status_code=400),
            OsrmResponseError,
            "TooBig",
        ),
        (FakeTransport({"code": "Ok"}, status_code=503), OsrmTransportError, "HTTP 503"),
        (FakeTransport(b"not-json"), OsrmResponseError, "malformed JSON"),
        (
            FakeTransport(b"<html>Bad Gateway</html>", status_code=502),
            OsrmTransportError,
            "HTTP 502",
        ),
        (TimeoutTransport(), OsrmTimeoutError, "10s"),
    ],
)
def test_bad_responses_remain_explicit_provider_errors(
    transport: FakeTransport | TimeoutTransport, error: type[OsrmProviderError], match: str
) -> None:
    with pytest.raises(error, match=match):
        OsrmHttpClient(transport=transport).table(TWO_POINTS)


@dataclass
class StubTableClient:
    result: OsrmTableResult

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        return self.result


def _costs(*rows: tuple[tuple[float, float], ...]) -> OsrmTableResult:
    cells = tuple(tuple(OsrmTableCost(*cell) for cell in row) for row in rows)
    return OsrmTableResult(cells=cells, data_version=None)


def test_candidate_graph_skips_rounded_zero_length_non_diagonal_cells() -> None:
    costs = _costs(((0, 0), (0, 0), (1, 2)), ((0, 0), (0, 0), (1, 2)), ((1, 2), (1, 2), (0, 0)))
    graph = CandidateGraphBuilder(StubTableClient(costs)).build(
        (
            CandidateNode("a", Coordinate(29.0, 41.0)),
            CandidateNode("near-a", Coordinate(29.000001, 41.000001)),
            CandidateNode("b", Coordinate(29.1, 41.1)),
        )
    )
    assert all(leg.distance_km > 0 and leg.duration_minutes > 0 for leg in graph.legs)
    assert {leg.destination_id for leg in graph.neighbors("a")} == {"b"}


def test_route_parses_geojson_geometry() -> None:
    coordinates = [[29.0, 41.0], [29.05, 41.02], [29.1, 41.1]]
    route = {
        "distance": 1234.5,
        "duration": 67.8,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }
    transport = FakeTransport({"code": "Ok", "routes": [route]})

    geometry = OsrmHttpClient(transport=transport).route_geometry(
        Coordinate(29.0, 41.0), Coordinate(29.1, 41.1)
    )

    assert geometry == GeoJsonLineString(((29.0, 41.0), (29.05, 41.02), (29.1, 41.1)))
    assert "geometries=geojson" in transport.calls[0][0]
    assert "overview=full" in transport.calls[0][0]


class RouteSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[CoordinateLike, CoordinateLike]] = []
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def route_geometry(
        self, origin: CoordinateLike, destination: CoordinateLike
    ) -> GeoJsonLineString:
        with self.lock:
            self.calls.append((origin, destination))
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1
        return GeoJsonLineString(
            ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
        )


HUB_NODES = (
    CandidateNode("origin", Coordinate(29.0, 41.0)),
    CandidateNode("hub", Coordinate(29.1, 41.1)),
    CandidateNode("destination", Coordinate(29.2, 41.2)),
)


def _hub_graph_and_plan() -> tuple[CandidateGraph, RoutePlan]:
    """A three-node graph where the fastest plan drives origin -> hub -> destination."""
    costs = _costs(
        ((0, 0), (10, 10), (30, 60)), ((10, 10), (0, 0), (10, 10)), ((30, 60), (10, 10), (0, 0))
    )
    graph = CandidateGraphBuilder(StubTableClient(costs)).build(HUB_NODES)
    vehicle = VehicleProfile(
        name="geometry-test",
        usable_battery_kwh=100,
        initial_soc_pct=100,
        reserve_soc_pct=0,
        consumption_kwh_per_100km=10,
        max_dc_power_kw=100,
        energy_safety_factor=1,
    )
    plan = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations={},
        road_network=graph,
    )
    assert plan.node_ids == ("origin", "hub", "destination")
    return graph, plan


def test_selected_geometries_are_fetched_once_per_leg_and_concurrently() -> None:
    route_client = RouteSpy()
    graph, via_hub = _hub_graph_and_plan()
    direct_leg = next(
        leg for leg in graph.neighbors("origin") if leg.destination_id == "destination"
    )
    direct = replace(
        via_hub,
        node_ids=("origin", "destination"),
        legs=(direct_leg,),
    )
    corridor = GeoJsonLineString(((29.0, 41.0), (29.2, 41.2)))

    enriched = fetch_selected_plans_geometry(
        (via_hub, via_hub, direct),
        graph=graph,
        route_client=route_client,
        known_geometries={("origin", "destination"): corridor},
    )

    assert sorted(route_client.calls, key=lambda call: call[0].longitude) == [
        (HUB_NODES[0].coordinate, HUB_NODES[1].coordinate),
        (HUB_NODES[1].coordinate, HUB_NODES[2].coordinate),
    ]
    assert route_client.maximum_active == 2
    assert enriched[0].legs == enriched[1].legs
    assert enriched[0].total_minutes == via_hub.total_minutes
    assert enriched[2].legs[0].geometry == corridor


def test_recorded_osrm_fixture_matches_its_checksums_and_parses_offline() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["osrm_backend_release"] == "v26.7.3"
    for filename, expected_checksum in manifest["sha256"].items():
        assert (
            hashlib.sha256((FIXTURE_ROOT / filename).read_bytes()).hexdigest() == expected_checksum
        )

    coordinates = (Coordinate(13, 52), Coordinate(13.01, 52), Coordinate(13.005, 52.01))
    table_payload = (FIXTURE_ROOT / "table_response.json").read_bytes()
    route_payload = (FIXTURE_ROOT / "route_response.json").read_bytes()
    table = OsrmHttpClient(transport=FakeTransport(table_payload)).table(coordinates)
    geometry = OsrmHttpClient(transport=FakeTransport(route_payload)).route_geometry(
        coordinates[0], coordinates[1]
    )
    assert table.cells[0][1] is not None and table.cells[0][1] != table.cells[1][0]
    assert len(geometry.coordinates) >= 2
