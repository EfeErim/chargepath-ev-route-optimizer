"""Install a built wheel into a fresh virtual environment and check it works outside the repository.

An editable install can hide packaging mistakes: a module, web asset, or data file that is missing
from the wheel still imports from the source tree. This check installs the wheel non-editably,
runs from a temporary directory with ``PYTHONPATH`` removed and Python's isolated mode, and then
checks that ``chargepath`` comes from the environment, the packaged fixture matches the repository
copy byte for byte, the web assets load, the demo command is installed, and the default fixture
plan replays.

Building the wheel needs the pinned build backend from PyPI, so it is a separate step:

    python -m pip wheel . --no-deps --wheel-dir dist
    python scripts/check_wheel.py dist/chargepath_ev_route_optimizer-*.whl

The check itself installs with ``--no-index`` and needs no network; the package has no runtime
dependencies.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY_ROOT / "data" / "sample" / "synthetic_corridor.json"

# Runs inside the fresh environment; every failure is an assertion with a message.
PROBE = """
import sys
from importlib.resources import files
from pathlib import Path

import chargepath
from chargepath.demo import FixturePlanningService, PlanRequest
from chargepath.demo.server import _default_fixture_path
from chargepath.fixtures import load_synthetic_scenario
from chargepath.providers.static import StaticRoadNetwork
from chargepath.verification import verify_route_plan
from chargepath.alternatives import CompetitiveRoutePlanner

repository, expected_fixture = Path(sys.argv[1]), Path(sys.argv[2])
package = Path(chargepath.__file__).resolve()
prefix = Path(sys.prefix).resolve()
assert prefix in package.parents, f"chargepath imported from {package}, not {prefix}"
assert repository not in package.parents, f"chargepath imported from the repository: {package}"

fixture = _default_fixture_path()
assert fixture.resolve() == prefix / "share" / "chargepath" / "synthetic_corridor.json", fixture
assert fixture.read_bytes() == expected_fixture.read_bytes(), "packaged fixture differs"

web = files("chargepath.web")
for name in ("index.html", "app.js", "app.css", "leaflet.js", "leaflet.css"):
    assert web.joinpath(name).read_bytes(), f"web asset {name} is empty or missing"

scenario = load_synthetic_scenario(fixture)
service = FixturePlanningService(scenario, tile_url="https://tile.openstreetmap.org/{z}/{x}/{y}.png")
config = service.public_config()
request = PlanRequest.from_payload(
    {
        "origin": config["endpoints"][0],
        "destination": config["endpoints"][1],
        "vehicle": config["vehicle"],
    }
)
payload = service.plan(request)
assert payload["options"], "the fixture produced no route options"

network = StaticRoadNetwork(scenario.legs)
result = CompetitiveRoutePlanner().plan(
    origin_id=scenario.origin_id,
    destination_id=scenario.destination_id,
    vehicle=scenario.vehicle,
    stations=scenario.station_map(),
    road_network=network,
)
for option in result.options:
    verify_route_plan(
        option.plan,
        vehicle=scenario.vehicle,
        stations=scenario.station_map(),
        road_network=network,
        origin_id=scenario.origin_id,
        destination_id=scenario.destination_id,
    )
fastest = min(option.plan.total_minutes for option in result.options)
print(f"chargepath {package.parent}")
print(f"{len(result.options)} replayed options, fastest {fastest:.1f} min")
"""


def check(wheel: Path) -> None:
    if wheel.suffix != ".whl" or not wheel.is_file():
        raise SystemExit(f"not a wheel file: {wheel}")
    with tempfile.TemporaryDirectory(prefix="chargepath-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "env"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts" if os.name == "nt" else "bin") / "python"
        clean = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel.resolve())],
            check=True,
            cwd=root,
            env=clean,
        )
        demo = environment / ("Scripts" if os.name == "nt" else "bin") / "chargepath-demo"
        if not (demo.exists() or demo.with_suffix(".exe").exists()):
            raise SystemExit("the chargepath-demo command was not installed")
        subprocess.run(
            [str(python), "-I", "-c", PROBE, str(REPOSITORY_ROOT.resolve()), str(FIXTURE)],
            check=True,
            cwd=root,
            env=clean,
        )
        subprocess.run(
            [str(python), "-I", "-m", "chargepath.demo", "--help"],
            check=True,
            cwd=root,
            env=clean,
            stdout=subprocess.DEVNULL,
        )
    print("Wheel check passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    check(args.wheel)


if __name__ == "__main__":
    sys.exit(main())
