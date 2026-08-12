"""Build, inspect, install, and smoke-test the ChargePath release wheel offline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WHEEL_PREFIX = "chargepath_ev_route_optimizer-0.1.0-"


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _build_wheel(output_directory: Path) -> Path:
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(output_directory),
            ".",
        ],
        cwd=REPOSITORY_ROOT,
    )
    wheels = sorted(output_directory.glob(f"{WHEEL_PREFIX}*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected one ChargePath wheel, found: {wheels}")
    return wheels[0]


def _inspect_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        fixture_name = (
            "chargepath_ev_route_optimizer-0.1.0.data/data/share/chargepath/synthetic_corridor.json"
        )
        required_exact = {
            "chargepath/web/app.css",
            "chargepath/web/app.js",
            "chargepath/web/index.html",
            "chargepath/web/leaflet.css",
            "chargepath/web/leaflet.js",
            fixture_name,
        }
        missing = sorted(required_exact - names)
        if missing:
            raise ValueError(f"wheel is missing runtime files: {missing}")
        license_names = {
            name.rsplit("/", 1)[-1] for name in names if ".dist-info/licenses/" in name
        }
        if license_names != {
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "leaflet-BSD-2-Clause.txt",
        }:
            raise ValueError(f"unexpected wheel license set: {sorted(license_names)}")
        source_fixture = REPOSITORY_ROOT / "data/sample/synthetic_corridor.json"
        if archive.read(fixture_name) != source_fixture.read_bytes():
            raise ValueError("wheel fixture bytes differ from the canonical source fixture")


def _install_and_smoke(wheel: Path, temporary_root: Path) -> None:
    environment = temporary_root / "installed"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = _venv_python(environment)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            str(wheel),
        ],
        cwd=temporary_root,
    )
    smoke = _run(
        [
            str(python),
            "-c",
            (
                "import json; "
                "from chargepath.demo import DEFAULT_TILE_URL, FixturePlanningService, "
                "_default_fixture_path; "
                "from chargepath.fixtures import load_synthetic_scenario; "
                "path=_default_fixture_path(); "
                "service=FixturePlanningService(load_synthetic_scenario(path), "
                "tile_url=DEFAULT_TILE_URL); "
                "print(json.dumps({'fixture': str(path), "
                "'config': service.public_config()}, ensure_ascii=False))"
            ),
        ],
        cwd=temporary_root,
    )
    result = json.loads(smoke.stdout)
    fixture_path = Path(result["fixture"])
    config = result["config"]
    if REPOSITORY_ROOT in fixture_path.parents:
        raise ValueError("installed wheel smoke test leaked the repository fixture")
    if config.get("mode") != "fixture" or len(config.get("endpoints", [])) != 2:
        raise ValueError(f"installed wheel returned invalid fixture config: {config}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="chargepath-wheel-") as temporary:
        temporary_root = Path(temporary)
        wheel = _build_wheel(temporary_root)
        _inspect_wheel(wheel)
        _install_and_smoke(wheel, temporary_root)
        print(
            "Wheel check passed: build, runtime data/web assets, license notices, "
            "non-editable install, and repo-independent fixture smoke."
        )


if __name__ == "__main__":
    main()
