from __future__ import annotations

import hashlib
import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import chargepath.demo as demo_module
import chargepath.demo.services as demo_services
from chargepath.alternatives import CompetitiveRoutePlanner
from chargepath.corridor import CandidateSelectionConfig, CandidateSelectionResult
from chargepath.demo import (
    DEFAULT_PORT,
    DEFAULT_TILE_URL,
    DemoServerConfig,
    FixturePlanningService,
    IntegrationPlanningService,
    PlanRequest,
    create_server,
)
from chargepath.fixtures import load_synthetic_scenario
from chargepath.models import GeoJsonLineString, NoFeasibleRouteError
from chargepath.providers.base import CoordinateLike
from chargepath.providers.osrm import OsrmResponseError, OsrmTableCost, OsrmTableResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FIXTURE = REPOSITORY_ROOT / "data/sample/synthetic_corridor.json"
WEB_ROOT = REPOSITORY_ROOT / "src/chargepath/web"


def _service() -> FixturePlanningService:
    return FixturePlanningService(
        load_synthetic_scenario(SYNTHETIC_FIXTURE),
        tile_url=DEFAULT_TILE_URL,
    )


def _payload() -> dict[str, object]:
    config = _service().public_config()
    endpoints = config["endpoints"]
    assert isinstance(endpoints, list)
    return {
        "origin": endpoints[0],
        "destination": endpoints[1],
        "vehicle": config["vehicle"],
    }


@pytest.fixture
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server(DemoServerConfig(service=_service(), port=0))
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fixture_service_returns_explainable_verified_route_options() -> None:
    service = _service()
    config = service.public_config()
    result = service.plan(PlanRequest.from_payload(_payload()))

    assert config["mode"] == "fixture"
    assert DEFAULT_PORT == 8743
    assert config["data_label"] == "Synthetic fixture — freshness not applicable"
    assert config["tile_url"] == DEFAULT_TILE_URL
    assert config["tile_attribution"] == "© OpenStreetMap contributors"
    result_options = result["options"]
    assert isinstance(result_options, list)
    assert len(result_options) == 3
    assert result["estimate_label"] == "SOC and charging times are model estimates"
    assert result["model_resolution"] == {
        "soc_step_pct": 5,
        "refined_after_coarse_infeasible": False,
    }
    assert result["candidate_selection"] == {}

    options = result["options"]
    assert isinstance(options, list)
    fastest = options[0]
    assert fastest["strategies"] == ["fastest", "fewest_charging_stops"]
    assert fastest["nodes"] == ["origin", "fast_hub", "destination"]
    assert fastest["charging_stops"][0]["name"] == "Fast Hub"
    assert [(row["kind"], row["soc_pct"]) for row in fastest["soc_timeline"]] == [
        ("start", 60),
        ("station", 20),
        ("charge", 75),
        ("arrival", 10),
    ]
    assert fastest["soc_timeline"][2]["label"] == "Charge · Fast Hub"
    assert fastest["soc_timeline"][-1]["soc_pct"] == fastest["arrival_soc_pct"]
    assert fastest["geometry"]["type"] == "FeatureCollection"
    assert len(fastest["geometry"]["features"]) == 2


def test_request_validation_is_exact_and_bounded() -> None:
    payload = _payload()
    vehicle = payload["vehicle"]
    assert isinstance(vehicle, dict)
    vehicle["initial_soc_pct"] = 4
    with pytest.raises(ValueError, match="between 5 and 100"):
        PlanRequest.from_payload(payload)

    payload = _payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match=r"unexpected \['unexpected'\]"):
        PlanRequest.from_payload(payload)


def test_direct_demo_models_reject_boolean_numeric_configuration() -> None:
    with pytest.raises(ValueError, match="point.longitude"):
        demo_module.PointInput("origin", True, 41)
    with pytest.raises(ValueError, match="vehicle.usable_battery_kwh"):
        demo_module.VehicleInputs(True, 60, 20, 150, 10, 1.1)
    with pytest.raises(ValueError, match="port"):
        DemoServerConfig(service=_service(), port=True)
    with pytest.raises(ValueError, match="tile URL"):
        FixturePlanningService(
            load_synthetic_scenario(SYNTHETIC_FIXTURE),
            tile_url="https://user:secret@example.invalid/{z}/{x}/{y}.png",
        )


def test_fixture_rejects_coordinates_not_selected_from_declared_markers() -> None:
    service = _service()
    payload = _payload()
    origin = payload["origin"]
    assert isinstance(origin, dict)
    origin["longitude"] = 30.0

    with pytest.raises(ValueError, match="declared map marker"):
        service.plan(PlanRequest.from_payload(payload))


def test_fixture_refines_only_after_the_default_soc_grid_is_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_planner = CompetitiveRoutePlanner

    class CoarseGridFails:
        def __init__(self, *, soc_step_pct: int) -> None:
            self._soc_step_pct = soc_step_pct

        def plan(self, **kwargs: Any) -> Any:
            if self._soc_step_pct == 5:
                raise NoFeasibleRouteError("coarse grid rejected this route")
            return original_planner(soc_step_pct=self._soc_step_pct).plan(**kwargs)

    monkeypatch.setattr(demo_services, "CompetitiveRoutePlanner", CoarseGridFails)
    result = _service().plan(PlanRequest.from_payload(_payload()))

    assert result["model_resolution"] == {
        "soc_step_pct": 2,
        "refined_after_coarse_infeasible": True,
    }


def test_local_candidate_cap_expands_only_after_an_infeasible_capped_search() -> None:
    selected = CandidateSelectionResult(
        candidates=cast(Any, (object(), object())),
        eligible_count=5,
        config=CandidateSelectionConfig(corridor_width_km=25, candidate_cap=2),
    )

    assert (
        demo_services._next_candidate_cap(
            selected=selected,
            cap_limit=4,
            enabled=True,
        )
        == 4
    )
    assert (
        demo_services._next_candidate_cap(
            selected=selected,
            cap_limit=4,
            enabled=False,
        )
        is None
    )


class OfflineOsrmClient:
    def __init__(self, *, endpoint: str, allow_remote_endpoint: bool = False) -> None:
        assert endpoint == "http://127.0.0.1:5000"
        assert allow_remote_endpoint is False

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        assert len(coordinates) == 3
        cells = (
            (OsrmTableCost(0, 0), OsrmTableCost(120, 75), OsrmTableCost(300, 180)),
            (OsrmTableCost(120, 75), OsrmTableCost(0, 0), OsrmTableCost(190, 115)),
            (OsrmTableCost(300, 180), OsrmTableCost(190, 115), OsrmTableCost(0, 0)),
        )
        return OsrmTableResult(cells=cells, data_version="offline-test")

    def route_geometry(
        self, origin: CoordinateLike, destination: CoordinateLike
    ) -> GeoJsonLineString:
        return GeoJsonLineString(
            ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
        )


def test_explicit_integration_mode_uses_checksum_snapshot_and_injected_osrm_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
    snapshot_bytes = source.read_bytes()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(snapshot_bytes)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "snapshot_id": "synthetic-integration-test",
                "source": "EPDK fixture",
                "source_url": "https://example.invalid/epdk-fixture",
                "retrieved_at": "2026-08-10T00:00:00Z",
                "response_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                "reuse_status": "pending_verification",
                "source_freshness": "unknown",
            }
        ),
        encoding="utf-8",
    )
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["response_sha256"] = "0" * 64
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum does not match"):
        IntegrationPlanningService(
            snapshot_path=snapshot,
            manifest_path=manifest,
            osrm_endpoint="http://127.0.0.1:5000",
            tile_url=DEFAULT_TILE_URL,
        )
    manifest_payload["response_sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    monkeypatch.setattr(demo_services, "OsrmHttpClient", OfflineOsrmClient)
    service = IntegrationPlanningService(
        snapshot_path=snapshot,
        manifest_path=manifest,
        osrm_endpoint="http://127.0.0.1:5000",
        tile_url=DEFAULT_TILE_URL,
    )
    request = PlanRequest.from_payload(
        {
            "origin": {"id": "origin", "longitude": 31.0, "latitude": 38.9},
            "destination": {"id": "destination", "longitude": 33.0, "latitude": 39.2},
            "vehicle": {
                "usable_battery_kwh": 60,
                "initial_soc_pct": 60,
                "consumption_kwh_per_100km": 20,
                "max_dc_power_kw": 150,
                "reserve_soc_pct": 10,
                "energy_safety_factor": 1,
            },
        }
    )

    config = service.public_config()
    result = service.plan(request)

    assert config["mode"] == "integration"
    assert "source freshness unknown" in str(config["data_label"])
    assert config["integration"] == {
        "snapshot_id": "synthetic-integration-test",
        "compatible_site_count": 1,
        "osrm_endpoint": "http://127.0.0.1:5000",
        "coordinates_leave_device": False,
        "candidate_cap": 50,
        "adaptive_candidate_cap_limit": 98,
        "adaptive_expansion_enabled": True,
    }
    result_options = result["options"]
    assert isinstance(result_options, list)
    assert result_options[0]["nodes"] == ["origin", "epdk:SYN-SITE-001", "destination"]
    assert all(
        feature["geometry"]["type"] == "LineString"
        for feature in result_options[0]["geometry"]["features"]
    )


class TableLimitedOsrmClient:
    """Offline stand-in enforcing osrm-routed's default --max-table-size of 100."""

    largest_table = 0

    def __init__(self, *, endpoint: str, allow_remote_endpoint: bool = False) -> None:
        assert allow_remote_endpoint is False

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        size = len(coordinates)
        if size > 100:
            raise OsrmResponseError("OSRM TooBig: table is too large", code="TooBig")
        type(self).largest_table = max(type(self).largest_table, size)
        unreachable = OsrmTableCost(5_000, 5_000)
        cells = tuple(
            tuple(OsrmTableCost(0, 0) if row == col else unreachable for col in range(size))
            for row in range(size)
        )
        return OsrmTableResult(cells=cells, data_version="offline-test")

    def route_geometry(
        self, origin: CoordinateLike, destination: CoordinateLike
    ) -> GeoJsonLineString:
        return GeoJsonLineString(
            ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
        )


def _many_site_snapshot(tmp_path: Path, *, site_count: int) -> tuple[Path, Path]:
    source = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    template = payload["data"][0]
    rows = []
    for index in range(site_count):
        row = json.loads(json.dumps(template))
        row["sarjIstasyonuNo"] = f"SYN-MANY-{index:03d}"
        row["enlem"] = 39.0
        row["boylam"] = 31.0 + 2.0 * index / site_count
        rows.append(row)
    payload["data"] = rows
    payload["numRows"] = site_count
    snapshot_bytes = json.dumps(payload).encode("utf-8")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(snapshot_bytes)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "snapshot_id": "synthetic-table-limit-test",
                "source": "EPDK fixture",
                "source_url": "https://example.invalid/epdk-fixture",
                "retrieved_at": "2026-11-05T00:00:00Z",
                "response_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                "reuse_status": "pending_verification",
                "source_freshness": "unknown",
            }
        ),
        encoding="utf-8",
    )
    return snapshot, manifest


def _long_trip_request() -> PlanRequest:
    return PlanRequest.from_payload(
        {
            "origin": {"id": "origin", "longitude": 31.0, "latitude": 39.0},
            "destination": {"id": "destination", "longitude": 33.0, "latitude": 39.0},
            "vehicle": {
                "usable_battery_kwh": 60,
                "initial_soc_pct": 60,
                "consumption_kwh_per_100km": 20,
                "max_dc_power_kw": 150,
                "reserve_soc_pct": 10,
                "energy_safety_factor": 1,
            },
        }
    )


def test_default_widening_stays_within_default_osrm_table_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, manifest = _many_site_snapshot(tmp_path, site_count=150)
    monkeypatch.setattr(TableLimitedOsrmClient, "largest_table", 0)
    monkeypatch.setattr(demo_services, "OsrmHttpClient", TableLimitedOsrmClient)
    service = IntegrationPlanningService(
        snapshot_path=snapshot,
        manifest_path=manifest,
        osrm_endpoint="http://127.0.0.1:5000",
        tile_url=DEFAULT_TILE_URL,
    )

    with pytest.raises(NoFeasibleRouteError):
        service.plan(_long_trip_request())

    assert TableLimitedOsrmClient.largest_table == 100


def test_widening_past_the_provider_table_limit_reports_the_infeasible_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, manifest = _many_site_snapshot(tmp_path, site_count=150)
    monkeypatch.setattr(demo_services, "OsrmHttpClient", TableLimitedOsrmClient)
    service = IntegrationPlanningService(
        snapshot_path=snapshot,
        manifest_path=manifest,
        osrm_endpoint="http://127.0.0.1:5000",
        tile_url=DEFAULT_TILE_URL,
        adaptive_candidate_cap_limit=140,
    )

    with pytest.raises(NoFeasibleRouteError):
        service.plan(_long_trip_request())


def test_initial_table_rejection_remains_a_provider_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, manifest = _many_site_snapshot(tmp_path, site_count=150)
    monkeypatch.setattr(demo_services, "OsrmHttpClient", TableLimitedOsrmClient)
    service = IntegrationPlanningService(
        snapshot_path=snapshot,
        manifest_path=manifest,
        osrm_endpoint="http://127.0.0.1:5000",
        tile_url=DEFAULT_TILE_URL,
        candidate_cap=120,
        adaptive_candidate_cap_limit=120,
    )

    with pytest.raises(OsrmResponseError, match="TooBig"):
        service.plan(_long_trip_request())


def test_integration_mode_rejects_snapshot_marked_prohibited(tmp_path: Path) -> None:
    source = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
    snapshot_bytes = source.read_bytes()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(snapshot_bytes)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "snapshot_id": "synthetic-prohibited-test",
                "source": "EPDK fixture",
                "source_url": "https://example.invalid/epdk-fixture",
                "retrieved_at": "2026-08-10T00:00:00Z",
                "response_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                "reuse_status": "prohibited",
                "source_freshness": "unknown",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="prohibits use"):
        IntegrationPlanningService(
            snapshot_path=snapshot,
            manifest_path=manifest,
            osrm_endpoint="http://127.0.0.1:5000",
            tile_url=DEFAULT_TILE_URL,
        )


def test_loopback_server_serves_assets_config_and_plan(running_server: tuple[str, int]) -> None:
    host, port = running_server
    connection = http.client.HTTPConnection(host, port, timeout=3)

    connection.request("GET", "/")
    response = connection.getresponse()
    page = response.read().decode("utf-8")
    assert response.status == 200
    assert "Leaflet route selection map" in page
    assert response.getheader("X-Content-Type-Options") == "nosniff"

    connection.request("GET", "/health")
    response = connection.getresponse()
    health = json.loads(response.read())
    assert response.status == 200
    assert health == {"status": "ok", "service": "chargepath-demo", "api_version": 3}

    connection.request("GET", "/api/config")
    response = connection.getresponse()
    config = json.loads(response.read())
    assert response.status == 200
    assert config["mode"] == "fixture"

    connection.request("GET", "/leaflet.css")
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/css; charset=utf-8"
    assert b".leaflet-pane" in response.read()

    connection.request("GET", "/leaflet.js")
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert b"1.9.4" in response.read()

    body = json.dumps(_payload()).encode()
    connection.request("POST", "/api/plan", body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    result = json.loads(response.read())
    assert response.status == 200
    assert result["options"]
    connection.close()


def test_server_exposes_explicit_failure_states_without_internal_tracebacks(
    running_server: tuple[str, int],
) -> None:
    host, port = running_server
    connection = http.client.HTTPConnection(host, port, timeout=3)
    connection.request(
        "POST",
        "/api/plan",
        body=b"{}",
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    assert response.status == 400
    assert payload["error"].startswith("request fields are invalid")

    connection.request("POST", "/api/plan", body=b"{}", headers={"Content-Type": "text/plain"})
    response = connection.getresponse()
    payload = json.loads(response.read())
    assert response.status == 415
    assert payload == {"error": "Content-Type must be application/json"}
    connection.close()


@pytest.mark.parametrize("failure", [ValueError("bad internal state"), KeyError("secret")])
def test_server_does_not_misclassify_or_leak_internal_service_errors(failure: Exception) -> None:
    class FailingService:
        def public_config(self) -> dict[str, object]:
            return _service().public_config()

        def plan(self, request: PlanRequest) -> dict[str, object]:
            raise failure

    server = create_server(DemoServerConfig(service=FailingService(), port=0))
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(str(host), int(port), timeout=3)
        connection.request(
            "POST",
            "/api/plan",
            body=json.dumps(_payload()).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 500
        assert payload == {"error": "Internal planning error"}
        assert "secret" not in json.dumps(payload)
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ui_keeps_basemap_local_attributed_and_optional() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    combined = f"{html}\n{script}".lower()

    # Leaflet is vendored, not loaded from a CDN, and must stay the reviewed 1.9.4 build.
    assert 'href="/leaflet.css"' in html
    assert 'src="/leaflet.js"' in html
    assert hashlib.sha256((WEB_ROOT / "leaflet.css").read_bytes()).hexdigest() == (
        "5f236f11b6ca29a549c06be1c1c786ec53523fb39a1bae2f2ba61f6fef889edb"
    )
    assert hashlib.sha256((WEB_ROOT / "leaflet.js").read_bytes()).hexdigest() == (
        "4f1ac3296897403e0a84881c974dbdf36d9a8488f0ef4b5626a952b1b6190d80"
    )
    assert "openstreetmap contributors" in combined
    # OSM tile servers block requests without a Referer; the page itself is no-referrer.
    assert 'referrerPolicy: "strict-origin-when-cross-origin"' in script
    assert "tileerror" in script
    assert "route explanation remains usable" in combined
    # No public geocoder: map clicks never send coordinates to a search service.
    assert "nominatim" not in combined
