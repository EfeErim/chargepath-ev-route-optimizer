"""JSON entry point for running the synthetic fixture demo in a browser with Pyodide.

The static build loads this module instead of the HTTP server. Requests and responses use the same
paths, payloads, status codes, and error messages as the loopback server's fixture mode.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from chargepath.demo.schema import MAX_REQUEST_BYTES
from chargepath.demo.services import (
    DEFAULT_TILE_URL,
    FixturePlanningService,
    PlanningService,
    plan_response,
)
from chargepath.fixtures import load_synthetic_scenario

_service: PlanningService | None = None


def start(fixture_path: str, tile_url: str = DEFAULT_TILE_URL) -> None:
    """Load the synthetic fixture. Only fixture mode exists in the browser build."""

    global _service
    scenario = load_synthetic_scenario(Path(fixture_path))
    _service = FixturePlanningService(scenario, tile_url=tile_url)


def handle(method: str, path: str, body: str | None = None) -> str:
    """Answer one API request and return ``{"status": int, "payload": object}`` as JSON text."""

    status, payload = _dispatch(method, path.split("?", maxsplit=1)[0], body)
    return json.dumps({"status": status, "payload": payload}, ensure_ascii=False)


def _dispatch(method: str, path: str, body: str | None) -> tuple[int, Mapping[str, object]]:
    if _service is None:
        return 503, {"error": "The in-browser planner has not started"}
    if method == "GET" and path == "/api/config":
        return 200, _service.public_config()
    if method != "POST" or path != "/api/plan":
        return 404, {"error": "Not found"}
    encoded = (body or "").encode("utf-8")
    if not 0 < len(encoded) <= MAX_REQUEST_BYTES:
        return 413, {"error": f"Request body must be between 1 and {MAX_REQUEST_BYTES} bytes"}
    try:
        payload = json.loads(encoded)
    except ValueError as error:
        return 400, {"error": str(error) or "Invalid request"}
    return plan_response(_service, payload)
