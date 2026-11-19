from __future__ import annotations

import hashlib
import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import chargepath.demo.services as demo_services
from chargepath.alternatives import CompetitiveRoutePlanner
from chargepath.demo import (
    DEFAULT_TILE_URL,
    FixturePlanningService,
    IntegrationPlanningService,
    PlanRequest,
)
from chargepath.demo.responses import ConfigPayload, PlanPayload
from chargepath.demo.server import create_server
from chargepath.fixtures import load_synthetic_scenario
from chargepath.models import GeoJsonLineString, NoFeasibleRouteError
from chargepath.providers.base import CoordinateLike
from chargepath.providers.osrm import OsrmResponseError, OsrmTableCost, OsrmTableResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FIXTURE = REPOSITORY_ROOT / "data/sample/synthetic_corridor.json"
EPDK_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/epdk/observed_v1/synthetic_response.json"
LOCAL_OSRM = "http://127.0.0.1:5000"


def _service() -> FixturePlanningService:
    return FixturePlanningService(
        load_synthetic_scenario(SYNTHETIC_FIXTURE), tile_url=DEFAULT_TILE_URL
    )


def _payload() -> dict[str, Any]:
    config = _service().public_config()
    endpoints = config["endpoints"]
    assert isinstance(endpoints, list)
    return {"origin": endpoints[0], "destination": endpoints[1], "vehicle": config["vehicle"]}


@pytest.fixture
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server(_service(), port=0)
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post_plan(host: str, port: int, body: bytes, content_type: str) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(host, port, timeout=3)
    connection.request("POST", "/api/plan", body=body, headers={"Content-Type": content_type})
    response = connection.getresponse()
    result = (response.status, json.loads(response.read()))
    connection.close()
    return result


def test_fixture_service_returns_explainable_verified_route_options() -> None:
    config = _service().public_config()
    result = _service().plan(PlanRequest.from_payload(_payload()))

    assert config["mode"] == "fixture"
    assert config["data_label"] == "Synthetic fixture — freshness not applicable"
    assert config["tile_attribution"] == "© OpenStreetMap contributors"
    assert result["estimate_label"] == "SOC and charging times are model estimates"
    assert result["model_resolution"] == {
        "soc_step_pct": 5,
        "refined_after_coarse_infeasible": False,
    }
    options = result["options"]
    assert isinstance(options, list) and len(options) == 3
    fastest = options[0]
    assert fastest["strategies"] == ["fastest", "fewest_charging_stops"]
    assert fastest["nodes"] == ["origin", "fast_hub", "destination"]
    assert [(row["kind"], row["soc_pct"]) for row in fastest["soc_timeline"]] == [
        ("start", 60),
        ("station", 20),
        ("charge", 75),
        ("arrival", 10),
    ]
    assert len(fastest["geometry"]["features"]) == 2


def test_request_validation_rejects_unknown_fields_and_undeclared_points() -> None:
    payload = _payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match=r"unexpected \['unexpected'\]"):
        PlanRequest.from_payload(payload)

    payload = _payload()
    payload["origin"]["longitude"] = 30.0
    with pytest.raises(ValueError, match="declared map marker"):
        _service().plan(PlanRequest.from_payload(payload))


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


class OfflineOsrmClient:
    """Fake OSRM that enforces osrm-routed's default --max-table-size of 100.

    Every station is reachable on the three-location table of the single-site fixture; on larger
    tables every off-diagonal cell is too long to drive, so the trip stays infeasible.
    """

    largest_table = 0

    def __init__(self, *, endpoint: str, allow_remote_endpoint: bool = False) -> None:
        assert endpoint == LOCAL_OSRM and allow_remote_endpoint is False

    def table(self, coordinates: tuple[CoordinateLike, ...]) -> OsrmTableResult:
        size = len(coordinates)
        if size > 100:
            raise OsrmResponseError("OSRM TooBig: table is too large", code="TooBig")
        type(self).largest_table = max(type(self).largest_table, size)
        if size == 3:
            rows = (
                (0, 0, 120, 75, 300, 180),
                (120, 75, 0, 0, 190, 115),
                (300, 180, 190, 115, 0, 0),
            )
            cells = tuple(
                tuple(OsrmTableCost(row[i], row[i + 1]) for i in range(0, 6, 2)) for row in rows
            )
        else:
            far = OsrmTableCost(5_000, 5_000)
            cells = tuple(
                tuple(OsrmTableCost(0, 0) if r == c else far for c in range(size))
                for r in range(size)
            )
        return OsrmTableResult(cells=cells, data_version="offline-test")

    def route_geometry(
        self, origin: CoordinateLike, destination: CoordinateLike
    ) -> GeoJsonLineString:
        return GeoJsonLineString(
            ((origin.longitude, origin.latitude), (destination.longitude, destination.latitude))
        )


def _snapshot(
    tmp_path: Path, *, site_count: int = 0, reuse_status: str = "pending_verification"
) -> tuple[Path, Path]:
    """Write an EPDK snapshot and its manifest; ``site_count`` > 0 copies the first site."""
    snapshot_bytes = EPDK_FIXTURE.read_bytes()
    if site_count:
        payload = json.loads(snapshot_bytes)
        template = payload["data"][0]
        payload["data"] = [
            {
                **template,
                "sarjIstasyonuNo": f"SYN-MANY-{i:03d}",
                "enlem": 39.0,
                "boylam": 31.0 + 2.0 * i / site_count,
            }
            for i in range(site_count)
        ]
        payload["numRows"] = site_count
        snapshot_bytes = json.dumps(payload).encode("utf-8")
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
                "reuse_status": reuse_status,
                "source_freshness": "unknown",
            }
        ),
        encoding="utf-8",
    )
    return snapshot, manifest


def _integration(snapshot: Path, manifest: Path, **kwargs: Any) -> IntegrationPlanningService:
    return IntegrationPlanningService(
        snapshot_path=snapshot,
        manifest_path=manifest,
        osrm_endpoint=LOCAL_OSRM,
        tile_url=DEFAULT_TILE_URL,
        **kwargs,
    )


def _trip(origin: tuple[float, float], destination: tuple[float, float]) -> PlanRequest:
    return PlanRequest.from_payload(
        {
            "origin": {"id": "origin", "longitude": origin[0], "latitude": origin[1]},
            "destination": {
                "id": "destination",
                "longitude": destination[0],
                "latitude": destination[1],
            },
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


@pytest.fixture(autouse=True)
def offline_osrm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OfflineOsrmClient, "largest_table", 0)
    monkeypatch.setattr(demo_services, "OsrmHttpClient", OfflineOsrmClient)


def test_integration_mode_plans_through_a_checksum_verified_snapshot(tmp_path: Path) -> None:
    snapshot, manifest = _snapshot(tmp_path)
    service = _integration(snapshot, manifest)
    config = service.public_config()
    result = service.plan(_trip((31.0, 38.9), (33.0, 39.2)))

    assert "source freshness unknown" in str(config["data_label"])
    assert config["integration"] == {
        "snapshot_id": "synthetic-integration-test",
        "compatible_site_count": 1,
        "osrm_endpoint": LOCAL_OSRM,
        "coordinates_leave_device": False,
        "candidate_cap": 50,
        "adaptive_candidate_cap_limit": 98,
        "adaptive_expansion_enabled": True,
    }
    options = result["options"]
    assert isinstance(options, list)
    assert options[0]["nodes"] == ["origin", "epdk:SYN-SITE-001", "destination"]

    tampered = {**json.loads(manifest.read_text(encoding="utf-8")), "response_sha256": "0" * 64}
    manifest.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum does not match"):
        _integration(snapshot, manifest)


def test_integration_mode_rejects_snapshot_marked_prohibited(tmp_path: Path) -> None:
    snapshot, manifest = _snapshot(tmp_path, reuse_status="prohibited")
    with pytest.raises(ValueError, match="prohibits use"):
        _integration(snapshot, manifest)


def test_default_widening_stays_within_default_osrm_table_limit(tmp_path: Path) -> None:
    service = _integration(*_snapshot(tmp_path, site_count=150))
    with pytest.raises(NoFeasibleRouteError):
        service.plan(_trip((31.0, 39.0), (33.0, 39.0)))
    assert OfflineOsrmClient.largest_table == 100


def test_widening_past_the_table_limit_reports_the_last_infeasible_result(tmp_path: Path) -> None:
    service = _integration(*_snapshot(tmp_path, site_count=150), adaptive_candidate_cap_limit=140)
    with pytest.raises(NoFeasibleRouteError):
        service.plan(_trip((31.0, 39.0), (33.0, 39.0)))


def test_initial_table_rejection_remains_a_provider_error(tmp_path: Path) -> None:
    service = _integration(
        *_snapshot(tmp_path, site_count=150), candidate_cap=120, adaptive_candidate_cap_limit=120
    )
    with pytest.raises(OsrmResponseError, match="TooBig"):
        service.plan(_trip((31.0, 39.0), (33.0, 39.0)))


def test_loopback_server_serves_assets_config_and_plan(running_server: tuple[str, int]) -> None:
    host, port = running_server
    connection = http.client.HTTPConnection(host, port, timeout=3)
    for path, marker in (("/", b"Leaflet route selection map"), ("/leaflet.js", b"1.9.4")):
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert marker in response.read()
    connection.request("GET", "/api/config")
    response = connection.getresponse()
    assert json.loads(response.read())["mode"] == "fixture"
    connection.close()

    status, result = _post_plan(host, port, json.dumps(_payload()).encode(), "application/json")
    assert status == 200 and result["options"]


def test_server_reports_client_errors_without_tracebacks(running_server: tuple[str, int]) -> None:
    status, payload = _post_plan(*running_server, b"{}", "application/json")
    assert status == 400 and payload["error"].startswith("request fields are invalid")
    status, payload = _post_plan(*running_server, b"{}", "text/plain")
    assert (status, payload) == (415, {"error": "Content-Type must be application/json"})


def test_server_does_not_leak_internal_service_errors(caplog: pytest.LogCaptureFixture) -> None:
    class FailingService:
        def public_config(self) -> ConfigPayload:
            return _service().public_config()

        def plan(self, request: PlanRequest) -> PlanPayload:
            raise KeyError("secret")

    server = create_server(FailingService(), port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        status, payload = _post_plan(
            str(host), int(port), json.dumps(_payload()).encode(), "application/json"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert (status, payload) == (500, {"error": "Internal planning error"})
    assert "secret" in caplog.text, "the operator log keeps the cause"
