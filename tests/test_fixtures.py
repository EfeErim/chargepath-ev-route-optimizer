import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from chargepath import GeoJsonLineString, load_synthetic_scenario
from chargepath.fixtures import SYNTHETIC_SCENARIO_PATH

SYNTHETIC_FIXTURE = SYNTHETIC_SCENARIO_PATH


def test_bundled_synthetic_fixture_is_directly_loadable() -> None:
    scenario = load_synthetic_scenario(SYNTHETIC_FIXTURE)
    assert (scenario.origin_id, scenario.destination_id) == ("origin", "destination")
    assert set(scenario.station_map()) == {"fast_hub", "slow_hub"}
    assert len(scenario.legs) == 5
    assert all(isinstance(leg.geometry, GeoJsonLineString) for leg in scenario.legs)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["metadata"].update({"synthetic": False}), "metadata.synthetic must be true"),
        (lambda p: p["stations"].append(dict(p["stations"][0])), "station ids must be unique"),
        (lambda p: p["legs"].append(dict(p["legs"][0])), "directed road legs must be unique"),
        (
            lambda p: p["legs"][0].update({"destination_id": "ghost"}),
            "legs must reference declared",
        ),
        (lambda p: p["legs"][0].update({"traffic": "live"}), r"legs\[0\] fields are invalid"),
    ],
)
def test_loader_fails_closed_on_invalid_fixtures(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    mutate(payload)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_synthetic_scenario(fixture_path)
