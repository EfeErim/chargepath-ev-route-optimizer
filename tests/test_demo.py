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
from chargepath.providers.osrm import OsrmTableCost, OsrmTableResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FIXTURE = REPOSITORY_ROOT / "data/sample/synthetic_corridor.json"
WEB_ROOT = REPOSITORY_ROOT / "src/chargepath/web"
OPEN_SCRIPT = REPOSITORY_ROOT / "scripts/open_chargepath.ps1"
START_SCRIPT = REPOSITORY_ROOT / "scripts/start_custom_map.ps1"


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
    assert fastest["soc_timeline"][0] == {"label": "Start", "kind": "start", "soc_pct": 60}
    assert fastest["soc_timeline"][-1]["label"] == "Arrival"
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
    with pytest.raises(ValueError, match=r"extra=\['unexpected'\]"):
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

    monkeypatch.setattr(demo_module, "CompetitiveRoutePlanner", CoarseGridFails)
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
        demo_module._next_candidate_cap(
            selected=selected,
            cap_limit=4,
            enabled=True,
        )
        == 4
    )
    assert (
        demo_module._next_candidate_cap(
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

    monkeypatch.setattr(demo_module, "OsrmHttpClient", OfflineOsrmClient)
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
        "adaptive_candidate_cap_limit": 200,
        "adaptive_expansion_enabled": True,
    }
    result_options = result["options"]
    assert isinstance(result_options, list)
    assert result_options[0]["nodes"] == ["origin", "epdk:SYN-SITE-001", "destination"]
    assert all(
        feature["geometry"]["type"] == "LineString"
        for feature in result_options[0]["geometry"]["features"]
    )


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
    assert payload["error"].startswith("request fields changed")

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


def test_ui_contract_keeps_basemap_optional_and_avoids_public_geocoding() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    stylesheet = (WEB_ROOT / "app.css").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    leaflet_css = (WEB_ROOT / "leaflet.css").read_bytes()
    leaflet_js = (WEB_ROOT / "leaflet.js").read_bytes()
    combined = f"{html}\n{script}".lower()

    assert 'href="/leaflet.css"' in html
    assert 'src="/leaflet.js"' in html
    assert "unpkg.com" not in html
    assert "openstreetmap contributors" in combined
    assert "tileerror" in script
    assert ".leaflet-pane" in stylesheet
    assert hashlib.sha256(leaflet_css).hexdigest() == (
        "5f236f11b6ca29a549c06be1c1c786ec53523fb39a1bae2f2ba61f6fef889edb"
    )
    assert hashlib.sha256(leaflet_js).hexdigest() == (
        "4f1ac3296897403e0a84881c974dbdf36d9a8488f0ef4b5626a952b1b6190d80"
    )
    assert "basemap unavailable" in combined
    assert "route explanation remains usable" in combined
    assert "nominatim" not in combined
    assert "prefetch" not in combined
    assert 'id="planButton" type="button" disabled' in html
    assert 'id="fitButton" type="button"' in html
    assert 'title="Fit route to view" disabled' in html
    assert "Enter your vehicle details" in html
    assert 'aria-label="Vehicle details"' in html
    assert "Vehicle presets" not in html
    assert "vehicle profile" not in html.lower()
    assert "preset-chip" not in html
    assert "vehicle-details" not in html
    assert "vehiclePresets" not in script
    assert "applyVehiclePreset" not in script
    assert "vehicleSummary" not in script
    for input_id in ("battery", "initialSoc", "consumption", "maxPower", "reserveSoc", "safety"):
        assert f'<label for="{input_id}">' in html
        assert f'<input id="{input_id}"' in html
    assert "resetPlanForChangedInputs();" in script
    assert "requestRevision !== state.inputRevision" in script
    assert "new AbortController()" in script
    assert "The route provider did not respond within 45 seconds" in script
    assert "Local OSRM is not running" in script
    assert "const strategyOrder = Object.keys(strategyNames);" in script
    assert "refined_after_coarse_infeasible" in script
    assert "expanded_after_infeasible" in script
    assert "function strategyTabEntries()" in script
    assert "${tabEntries.length} route strategies ready" in script
    assert 'button.addEventListener("keydown"' in script
    assert "button.tabIndex = index === state.selectedOption ? 0 : -1" in script
    assert ".map {\n  position: absolute;" in stylesheet
    assert "height: 100%;\n  overflow: hidden;" in stylesheet


def test_windows_launcher_contract_matches_current_health_and_public_cap() -> None:
    open_script = OPEN_SCRIPT.read_text(encoding="utf-8")
    start_script = START_SCRIPT.read_text(encoding="utf-8")

    assert '$Payload.service -eq "chargepath-demo"' in open_script
    assert "$Payload.api_version -eq 3" in open_script
    assert "[int]$PublicCandidateCap = 24" in start_script
