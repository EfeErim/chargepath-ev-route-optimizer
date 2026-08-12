import json
from pathlib import Path

import pytest

from chargepath import GeoJsonLineString, load_synthetic_scenario

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FIXTURE = REPOSITORY_ROOT / "data/sample/synthetic_corridor.json"


def test_bundled_synthetic_fixture_is_directly_loadable() -> None:
    scenario = load_synthetic_scenario(SYNTHETIC_FIXTURE)
    assert scenario.origin_id == "origin"
    assert scenario.destination_id == "destination"
    assert scenario.vehicle.name == "Synthetic EV"
    assert scenario.vehicle.supported_dc_connectors == ("CCS2",)
    assert set(scenario.station_map()) == {"fast_hub", "slow_hub"}
    assert len(scenario.legs) == 5
    assert all(isinstance(leg.geometry, GeoJsonLineString) for leg in scenario.legs)
    assert scenario.legs[1].geometry == GeoJsonLineString(
        ((31.0, 38.9), (31.5, 38.95), (32.0, 39.0))
    )


def test_loader_rejects_fixture_without_synthetic_label(tmp_path: Path) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    payload["metadata"]["synthetic"] = False
    fixture_path = tmp_path / "unlabeled.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata.synthetic must be true"):
        load_synthetic_scenario(fixture_path)


def test_loader_rejects_duplicate_station_ids(tmp_path: Path) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    payload["stations"].append(dict(payload["stations"][0]))
    fixture_path = tmp_path / "duplicate.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="station ids must be unique"):
        load_synthetic_scenario(fixture_path)


def test_loader_rejects_invalid_geojson_geometry(tmp_path: Path) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    payload["legs"][0]["geometry"] = {
        "type": "LineString",
        "coordinates": [[31.0, 38.9]],
    }
    fixture_path = tmp_path / "invalid-geometry.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="at least two positions"):
        load_synthetic_scenario(fixture_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"surprise": True}), "root fields are invalid"),
        (
            lambda payload: payload["stations"][0].update({"live": True}),
            r"stations\[0\] fields are invalid",
        ),
        (
            lambda payload: payload["legs"][0].update({"traffic": "live"}),
            r"legs\[0\] fields are invalid",
        ),
    ],
)
def test_loader_rejects_fields_outside_schema_v2(
    tmp_path: Path, mutation: object, message: str
) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    assert callable(mutation)
    mutation(payload)
    fixture_path = tmp_path / "extra-field.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_synthetic_scenario(fixture_path)


def test_loader_rejects_undeclared_leg_node(tmp_path: Path) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    payload["legs"][0]["destination_id"] = "ghost"
    fixture_path = tmp_path / "ghost-node.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="legs must reference declared"):
        load_synthetic_scenario(fixture_path)


def test_loader_rejects_duplicate_directed_leg(tmp_path: Path) -> None:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    payload["legs"].append(dict(payload["legs"][0]))
    fixture_path = tmp_path / "duplicate-leg.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="directed road legs must be unique"):
        load_synthetic_scenario(fixture_path)
