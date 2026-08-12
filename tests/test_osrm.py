from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest

from chargepath.models import GeoJsonLineString, RoadLeg, VehicleProfile
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
    OsrmResponseError,
    OsrmTableCost,
    OsrmTableResult,
    OsrmTimeoutError,
    OsrmTransportError,
    fetch_selected_plan_geometry,
    fetch_selected_plans_geometry,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "osrm" / "v26.7.3"


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


def test_coordinate_uses_explicit_longitude_latitude_serialization() -> None:
    assert Coordinate(longitude=29.123456789, latitude=41.000000001).serialize() == (
        "29.12345679,41"
    )
    assert Coordinate(longitude=-0.0, latitude=0.0).serialize() == "0,0"

    with pytest.raises(ValueError, match="longitude"):
        Coordinate(longitude=181, latitude=0)
    with pytest.raises(ValueError, match="latitude"):
        Coordinate(longitude=0, latitude=math.nan)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": math.nan},
        {"profile": 1},
        {"endpoint": 1},
        {"allow_remote_endpoint": "yes"},
        {"endpoint": "https://user:secret@router.project-osrm.org", "allow_remote_endpoint": True},
        {"endpoint": "http://127.0.0.1:bad"},
    ],
)
def test_osrm_client_rejects_invalid_direct_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OsrmHttpClient(**cast(Any, kwargs))


def test_direct_provider_results_reject_invalid_transport_values() -> None:
    with pytest.raises(ValueError, match="status_code"):
        HttpResponse(True, b"{}")
    with pytest.raises(ValueError, match="body"):
        HttpResponse(200, cast(Any, "{}"))
    with pytest.raises(ValueError, match="data_version"):
        OsrmTableResult(cells=(), data_version=" ")


def test_table_request_covers_order_encoding_profile_timeout_and_explicit_endpoint() -> None:
    transport = FakeTransport(
        {
            "code": "Ok",
            "durations": [[0, 60], [90, 0]],
            "distances": [[0, 1000], [1200, 0]],
        }
    )
    client = OsrmHttpClient(
        endpoint="http://localhost:5999/osrm-root",
        profile="driving car",
        timeout_seconds=3.5,
        transport=transport,
    )

    client.table((Coordinate(29.1, 41.2), Coordinate(30.3, 42.4)))

    assert transport.calls == [
        (
            "http://localhost:5999/osrm-root/table/v1/driving%20car/"
            "29.1,41.2;30.3,42.4?annotations=duration%2Cdistance&sources=all&destinations=all",
            3.5,
        )
    ]


def test_endpoint_defaults_to_loopback_and_remote_requires_explicit_opt_in() -> None:
    transport = FakeTransport(
        {
            "code": "Ok",
            "durations": [[0, 1], [1, 0]],
            "distances": [[0, 1], [1, 0]],
        }
    )
    OsrmHttpClient(transport=transport).table((Coordinate(0, 0), Coordinate(0.1, 0.1)))
    assert transport.calls[0][0].startswith(f"{DEFAULT_OSRM_ENDPOINT}/table/v1/driving/")

    with pytest.raises(ValueError, match="allow_remote_endpoint"):
        OsrmHttpClient(endpoint="https://router.project-osrm.org")
    OsrmHttpClient(
        endpoint="https://router.project-osrm.org",
        allow_remote_endpoint=True,
        transport=transport,
    )


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
    assert graph.neighbors("a")[0].destination_id == "b"
    assert all(leg.destination_id != "c" for leg in graph.neighbors("a"))


def test_table_clamps_only_osrm_single_decimal_negative_zero() -> None:
    rounded_negative = FakeTransport(
        {
            "code": "Ok",
            "durations": [[0, -0.1], [1, 0]],
            "distances": [[0, -0.1], [1, 0]],
        }
    )
    result = OsrmHttpClient(transport=rounded_negative).table(
        (Coordinate(0, 0), Coordinate(0.1, 0.1))
    )

    assert result.cells[0][1] == OsrmTableCost(distance_km=0.0, duration_minutes=0.0)

    material_negative = FakeTransport(
        {
            "code": "Ok",
            "durations": [[0, -0.2], [1, 0]],
            "distances": [[0, 1], [1, 0]],
        }
    )
    with pytest.raises(OsrmResponseError, match="must not be negative"):
        OsrmHttpClient(transport=material_negative).table((Coordinate(0, 0), Coordinate(0.1, 0.1)))


def test_table_rejects_mismatched_duration_distance_nullability() -> None:
    transport = FakeTransport(
        {
            "code": "Ok",
            "durations": [[0, None], [1, 0]],
            "distances": [[0, 1], [1, 0]],
        }
    )
    with pytest.raises(OsrmResponseError, match="nullability must match"):
        OsrmHttpClient(transport=transport).table((Coordinate(0, 0), Coordinate(0.1, 0.1)))


def test_candidate_graph_direct_construction_rejects_unknown_leg_nodes() -> None:
    nodes = (
        CandidateNode("origin", Coordinate(29.0, 41.0)),
        CandidateNode("destination", Coordinate(29.2, 41.2)),
    )
    with pytest.raises(ValueError, match="legs must reference declared nodes"):
        CandidateGraph(
            nodes=nodes,
            legs=(RoadLeg("origin", "ghost", distance_km=1, duration_minutes=1),),
        )


def test_candidate_graph_skips_rounded_zero_length_non_diagonal_cells() -> None:
    costs = OsrmTableResult(
        cells=(
            (OsrmTableCost(0, 0), OsrmTableCost(0, 0), OsrmTableCost(1, 2)),
            (OsrmTableCost(0, 0), OsrmTableCost(0, 0), OsrmTableCost(1, 2)),
            (OsrmTableCost(1, 2), OsrmTableCost(1, 2), OsrmTableCost(0, 0)),
        ),
        data_version="rounded-zero-test",
    )
    graph = CandidateGraphBuilder(StubTableClient(costs)).build(
        (
            CandidateNode("a", Coordinate(29.0, 41.0)),
            CandidateNode("near-a", Coordinate(29.000001, 41.000001)),
            CandidateNode("b", Coordinate(29.1, 41.1)),
        )
    )

    assert all(leg.distance_km > 0 for leg in graph.legs)
    assert all(leg.duration_minutes > 0 for leg in graph.legs)
    assert {leg.destination_id for leg in graph.neighbors("a")} == {"b"}


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -1.0])
def test_table_cost_rejects_invalid_values(invalid: float) -> None:
    with pytest.raises(ValueError, match="costs must be finite and non-negative"):
        OsrmTableCost(invalid, 1)


def test_route_parses_geojson_geometry() -> None:
    transport = FakeTransport(
        {
            "code": "Ok",
            "routes": [
                {
                    "distance": 1234.5,
                    "duration": 67.8,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[29.0, 41.0], [29.05, 41.02], [29.1, 41.1]],
                    },
                }
            ],
        }
    )

    geometry = OsrmHttpClient(transport=transport).route_geometry(
        Coordinate(29.0, 41.0), Coordinate(29.1, 41.1)
    )

    assert geometry == GeoJsonLineString(((29.0, 41.0), (29.05, 41.02), (29.1, 41.1)))
    assert "geometries=geojson" in transport.calls[0][0]
    assert "overview=full" in transport.calls[0][0]


@dataclass
class StubTableClient:
    result: OsrmTableResult
    calls: int = 0

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        self.calls += 1
        return self.result


class RouteSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[CoordinateLike, CoordinateLike]] = []

    def route_geometry(
        self, origin: CoordinateLike, destination: CoordinateLike
    ) -> GeoJsonLineString:
        self.calls.append((origin, destination))
        return GeoJsonLineString(
            ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
        )


def test_selected_geometry_is_fetched_only_after_optimization_for_chosen_endpoints() -> None:
    costs = OsrmTableResult(
        cells=(
            (
                OsrmTableCost(0, 0),
                OsrmTableCost(10, 10),
                OsrmTableCost(30, 60),
            ),
            (OsrmTableCost(10, 10), OsrmTableCost(0, 0), OsrmTableCost(10, 10)),
            (OsrmTableCost(30, 60), OsrmTableCost(10, 10), OsrmTableCost(0, 0)),
        ),
        data_version=None,
    )
    nodes = (
        CandidateNode("origin", Coordinate(29.0, 41.0)),
        CandidateNode("hub", Coordinate(29.1, 41.1)),
        CandidateNode("destination", Coordinate(29.2, 41.2)),
    )
    table_client = StubTableClient(costs)
    graph = CandidateGraphBuilder(table_client).build(nodes)
    route_client = RouteSpy()

    assert route_client.calls == []
    plan = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=VehicleProfile(
            name="geometry-test",
            usable_battery_kwh=100,
            initial_soc_pct=100,
            reserve_soc_pct=0,
            consumption_kwh_per_100km=10,
            max_dc_power_kw=100,
            energy_safety_factor=1,
        ),
        stations={},
        road_network=graph,
    )
    assert plan.node_ids == ("origin", "hub", "destination")
    assert route_client.calls == []

    enriched = fetch_selected_plan_geometry(plan, graph=graph, route_client=route_client)

    assert sorted(route_client.calls, key=lambda call: call[0].longitude) == [
        (nodes[0].coordinate, nodes[1].coordinate),
        (nodes[1].coordinate, nodes[2].coordinate),
    ]
    assert all(leg.geometry is not None for leg in enriched.legs)
    assert enriched.total_minutes == plan.total_minutes


def test_selected_option_geometries_are_deduplicated_and_can_reuse_corridor() -> None:
    costs = OsrmTableResult(
        cells=(
            (OsrmTableCost(0, 0), OsrmTableCost(10, 10), OsrmTableCost(30, 60)),
            (OsrmTableCost(10, 10), OsrmTableCost(0, 0), OsrmTableCost(10, 10)),
            (OsrmTableCost(30, 60), OsrmTableCost(10, 10), OsrmTableCost(0, 0)),
        ),
        data_version=None,
    )
    nodes = (
        CandidateNode("origin", Coordinate(29.0, 41.0)),
        CandidateNode("hub", Coordinate(29.1, 41.1)),
        CandidateNode("destination", Coordinate(29.2, 41.2)),
    )
    graph = CandidateGraphBuilder(StubTableClient(costs)).build(nodes)
    route_client = RouteSpy()
    vehicle = VehicleProfile(
        name="geometry-test",
        usable_battery_kwh=100,
        initial_soc_pct=100,
        reserve_soc_pct=0,
        consumption_kwh_per_100km=10,
        max_dc_power_kw=100,
        energy_safety_factor=1,
    )
    via_hub = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations={},
        road_network=graph,
    )
    direct = replace(
        via_hub,
        node_ids=("origin", "destination"),
        legs=(
            next(leg for leg in graph.neighbors("origin") if leg.destination_id == "destination"),
        ),
        total_distance_km=30,
        driving_minutes=60,
        total_minutes=60,
    )
    corridor = GeoJsonLineString(((29.0, 41.0), (29.2, 41.2)))

    enriched = fetch_selected_plans_geometry(
        (via_hub, via_hub, direct),
        graph=graph,
        route_client=route_client,
        known_geometries={("origin", "destination"): corridor},
    )

    assert sorted(route_client.calls, key=lambda call: call[0].longitude) == [
        (nodes[0].coordinate, nodes[1].coordinate),
        (nodes[1].coordinate, nodes[2].coordinate),
    ]
    assert enriched[0].legs == enriched[1].legs
    assert enriched[2].legs[0].geometry == corridor


def test_distinct_selected_geometries_are_fetched_concurrently() -> None:
    costs = OsrmTableResult(
        cells=(
            (OsrmTableCost(0, 0), OsrmTableCost(10, 10), OsrmTableCost(30, 60)),
            (OsrmTableCost(10, 10), OsrmTableCost(0, 0), OsrmTableCost(10, 10)),
            (OsrmTableCost(30, 60), OsrmTableCost(10, 10), OsrmTableCost(0, 0)),
        ),
        data_version=None,
    )
    nodes = (
        CandidateNode("origin", Coordinate(29.0, 41.0)),
        CandidateNode("hub", Coordinate(29.1, 41.1)),
        CandidateNode("destination", Coordinate(29.2, 41.2)),
    )
    graph = CandidateGraphBuilder(StubTableClient(costs)).build(nodes)
    vehicle = VehicleProfile(
        name="concurrency-test",
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

    class ConcurrentRouteSpy:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0
            self.lock = threading.Lock()

        def route_geometry(
            self, origin: CoordinateLike, destination: CoordinateLike
        ) -> GeoJsonLineString:
            with self.lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            time.sleep(0.02)
            with self.lock:
                self.active -= 1
            return GeoJsonLineString(
                ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
            )

    route_client = ConcurrentRouteSpy()
    enriched = fetch_selected_plans_geometry((plan,), graph=graph, route_client=route_client)

    assert route_client.maximum_active == 2
    assert all(leg.geometry is not None for leg in enriched[0].legs)


@pytest.mark.parametrize("code", ["NoSegment", "NoRoute", "TooBig", "NoTable", "InvalidQuery"])
def test_non_ok_codes_remain_explicit_provider_errors(code: str) -> None:
    client = OsrmHttpClient(
        transport=FakeTransport({"code": code, "message": "blocked"}, status_code=400)
    )

    with pytest.raises(OsrmResponseError, match=code) as raised:
        client.table((Coordinate(0, 0), Coordinate(1, 1)))

    assert raised.value.code == code


def test_invalid_table_shape_and_http_failure_remain_explicit_provider_errors() -> None:
    malformed_shape = FakeTransport(
        {
            "code": "Ok",
            "durations": [[0, 1]],
            "distances": [[0, 1], [1, 0]],
        }
    )
    with pytest.raises(OsrmResponseError, match="row count"):
        OsrmHttpClient(transport=malformed_shape).table((Coordinate(0, 0), Coordinate(1, 1)))

    with pytest.raises(OsrmTransportError, match="HTTP 503"):
        OsrmHttpClient(
            transport=FakeTransport(
                {
                    "code": "Ok",
                    "durations": [[0, 1], [1, 0]],
                    "distances": [[0, 1], [1, 0]],
                },
                status_code=503,
            )
        ).table((Coordinate(0, 0), Coordinate(1, 1)))


def test_malformed_json_and_timeout_remain_explicit_provider_errors() -> None:
    with pytest.raises(OsrmResponseError, match="malformed JSON"):
        OsrmHttpClient(transport=FakeTransport(b"not-json")).table(
            (Coordinate(0, 0), Coordinate(1, 1))
        )

    with pytest.raises(OsrmTimeoutError, match="10s"):
        OsrmHttpClient(transport=TimeoutTransport()).table((Coordinate(0, 0), Coordinate(1, 1)))


def test_recorded_osrm_fixture_manifest_and_checksums() -> None:
    manifest_path = FIXTURE_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["osrm_backend_release"] == "v26.7.3"
    assert manifest["osrm_backend_commit"] == "0844e3af77896d11998ef6db356a553056652c8e"
    assert manifest["http_api_version"] == "v1"
    assert manifest["profile"] == "car.lua"
    assert manifest["retrieved_at"].endswith("Z")
    assert manifest["data_version"] == "synthetic-m1-2026-08-09"
    assert manifest["synthetic"] is True
    assert manifest["requests"]["table"]["options"] == {
        "annotations": "duration,distance",
        "destinations": "all",
        "sources": "all",
    }
    assert manifest["requests"]["route"]["options"] == {
        "alternatives": "false",
        "geometries": "geojson",
        "overview": "full",
        "steps": "false",
    }
    for filename, expected_checksum in manifest["sha256"].items():
        payload = (FIXTURE_ROOT / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_checksum


def test_recorded_table_and_route_responses_parse_offline() -> None:
    table_payload = (FIXTURE_ROOT / "table_response.json").read_bytes()
    route_payload = (FIXTURE_ROOT / "route_response.json").read_bytes()
    coordinates = (Coordinate(13, 52), Coordinate(13.01, 52), Coordinate(13.005, 52.01))

    table = OsrmHttpClient(transport=FakeTransport(table_payload)).table(coordinates)
    geometry = OsrmHttpClient(transport=FakeTransport(route_payload)).route_geometry(
        coordinates[0], coordinates[1]
    )

    assert table.cells[0][1] is not None
    assert table.cells[0][1] != table.cells[1][0]
    assert geometry.type == "LineString"
    assert len(geometry.coordinates) >= 2
