"""Loopback-only fixture-first web demo for ChargePath."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import cast

from chargepath.corridor import DEFAULT_CANDIDATE_SELECTION_CONFIG
from chargepath.demo.services import (
    DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT,
    DEFAULT_TILE_URL,
    FixturePlanningService,
    IntegrationPlanningService,
    PlanningService,
    plan_response,
)
from chargepath.fixtures import load_synthetic_scenario
from chargepath.providers.osrm import DEFAULT_OSRM_ENDPOINT

MAX_REQUEST_BYTES = 32_768
DEFAULT_PORT = 8743
HEALTH_API_VERSION = 3


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
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error) or "Invalid request"})
            return
        status, result = plan_response(self._demo_server.service, payload)
        self._send_json(HTTPStatus(status), result)

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


def _default_fixture_path() -> Path:
    repository_fixture = (
        Path(__file__).resolve().parents[3] / "data" / "sample" / "synthetic_corridor.json"
    )
    if repository_fixture.is_file():
        return repository_fixture
    return Path(sys.prefix) / "share" / "chargepath" / "synthetic_corridor.json"


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chargepath-demo", description=__doc__)
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
