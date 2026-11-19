"""JSON entry point for running the synthetic fixture demo in a browser with Pyodide.

The static build loads this module instead of the HTTP server. Requests and responses use the same
paths, payloads, status codes, and error messages as the loopback server's fixture mode.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from chargepath.demo.services import (
    DEFAULT_TILE_URL,
    FixturePlanningService,
    PlanningService,
    api_response,
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

    payload: Mapping[str, object]
    if _service is None:
        status, payload = 503, {"error": "The in-browser planner has not started"}
    else:
        status, payload = api_response(
            _service, method, path.split("?", maxsplit=1)[0], (body or "").encode("utf-8")
        )
    return json.dumps({"status": status, "payload": payload}, ensure_ascii=False)
