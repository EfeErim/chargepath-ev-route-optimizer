from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from chargepath.demo import browser

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY_ROOT / "data/sample/synthetic_corridor.json"


def _load_build_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_static_demo", REPOSITORY_ROOT / "scripts/build_static_demo.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _call(method: str, path: str, body: object = None) -> tuple[int, Any]:
    text = None if body is None else body if isinstance(body, str) else json.dumps(body)
    answer = json.loads(browser.handle(method, path, text))
    return answer["status"], answer["payload"]


@pytest.fixture(autouse=True)
def started_browser_planner() -> None:
    browser.start(str(FIXTURE))


def _plan_payload() -> dict[str, object]:
    status, config = _call("GET", "/api/config")
    assert status == 200
    return {
        "origin": config["endpoints"][0],
        "destination": config["endpoints"][1],
        "vehicle": config["vehicle"],
    }


def test_browser_entry_point_serves_fixture_config_and_plans() -> None:
    status, config = _call("GET", "/api/config")
    assert status == 200
    assert config["mode"] == "fixture"
    assert "Synthetic fixture" in config["data_label"]

    status, result = _call("POST", "/api/plan", _plan_payload())
    assert status == 200
    assert result["options"]
    assert result["estimate_label"] == "SOC and charging times are model estimates"


def test_browser_entry_point_reports_the_server_error_codes() -> None:
    assert _call("GET", "/missing")[0] == 404
    assert _call("POST", "/api/plan", "{not json")[0] == 400
    assert _call("POST", "/api/plan", "")[0] == 413
    payload = _plan_payload()
    payload["unexpected"] = True
    status, error = _call("POST", "/api/plan", payload)
    assert status == 400
    assert "unexpected" in error["error"]


def test_static_build_is_relative_and_self_contained(tmp_path: Path) -> None:
    script = _load_build_script()
    output = tmp_path / "site"
    written = {path.name for path in script.build(output, pyodide_version="0.27.7")}
    assert written == {
        ".nojekyll",
        "app.css",
        "app.js",
        "chargepath-src.zip",
        "index.html",
        "leaflet.css",
        "leaflet.js",
        "static-api.js",
        "synthetic_corridor.json",
    }
    html = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="/' not in html and 'src="/' not in html
    assert html.index("pyodide/v0.27.7/full/pyodide.js") < html.index('src="static-api.js"')
    assert f'integrity="{script.PYODIDE_INTEGRITY["0.27.7"]}" crossorigin="anonymous"' in html
    assert html.index('src="static-api.js"') < html.index('src="app.js"')
    assert "__PYODIDE_VERSION__" not in (output / "static-api.js").read_text(encoding="utf-8")
    with zipfile.ZipFile(output / "chargepath-src.zip") as archive:
        names = set(archive.namelist())
    assert {"chargepath/demo/browser.py", "chargepath/optimizer.py"} <= names
    assert not any(name.endswith((".json", ".pyc")) for name in names)

    first = (output / "chargepath-src.zip").read_bytes()
    script.build(output, pyodide_version="0.27.7")
    assert (output / "chargepath-src.zip").read_bytes() == first


def test_static_build_rejects_a_floating_pyodide_version(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact"):
        _load_build_script().build(tmp_path / "site", pyodide_version="latest")


def test_static_build_rejects_a_pyodide_version_without_a_hash(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="integrity hash"):
        _load_build_script().build(tmp_path / "site", pyodide_version="0.0.1")
