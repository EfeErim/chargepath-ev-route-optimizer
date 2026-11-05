from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from chargepath.real_route_benchmark import (
    DISTANCE_BANDS,
    Province,
    distance_key,
    load_provinces,
    select_routes,
)

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "data/benchmarks/m6/v1"


def _manifest() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (BENCHMARK_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    return payload


def _stored_distances() -> dict[str, float]:
    payload = json.loads((BENCHMARK_DIR / "selection_distances.json").read_text(encoding="utf-8"))
    road_km: dict[str, float] = payload["road_km"]
    return road_km


def test_provinces_cover_every_plate_inside_turkey() -> None:
    provinces = load_provinces(BENCHMARK_DIR / "provinces.json")
    assert len(provinces) == 81
    assert len({province.name for province in provinces}) == 81
    for province in provinces:
        assert 35.8 <= province.latitude <= 42.2
        assert 25.6 <= province.longitude <= 44.9


def test_committed_routes_are_reproduced_from_stored_distances() -> None:
    provinces = load_provinces(BENCHMARK_DIR / "provinces.json")
    routes = select_routes(provinces, _stored_distances())
    assert [
        (route["id"], route["origin"]["plate"], route["destination"]["plate"], route["band"])
        for route in _manifest()["routes"]
    ] == [
        (route.route_id, route.origin.plate, route.destination.plate, route.band)
        for route in routes
    ]


def test_manifest_meets_the_selection_contract() -> None:
    routes = _manifest()["routes"]
    assert len(routes) == 24
    assert Counter(route["band"] for route in routes) == {band.name: 6 for band in DISTANCE_BANDS}
    assert all(
        sum(route["eastern"] for route in routes if route["band"] == band.name) >= 2
        for band in DISTANCE_BANDS
    )
    for route in routes:
        band = next(band for band in DISTANCE_BANDS if band.name == route["band"])
        assert band.contains(route["selection_road_km"])
        assert route["origin"]["plate"] < route["destination"]["plate"]
    usage = Counter(
        plate
        for route in routes
        for plate in (route["origin"]["plate"], route["destination"]["plate"])
    )
    assert max(usage.values()) <= 2


def test_manifest_pins_its_committed_inputs_by_checksum() -> None:
    frozen = _manifest()["frozen_inputs"]
    for name, field in (
        ("provinces.json", "provinces_sha256"),
        ("selection_distances.json", "selection_distances_sha256"),
    ):
        assert hashlib.sha256((BENCHMARK_DIR / name).read_bytes()).hexdigest() == frozen[field]


def test_experiments_match_the_project_plan() -> None:
    manifest = _manifest()
    route_ids = [route["id"] for route in manifest["routes"]]
    experiments = manifest["experiments"]
    assert experiments["E2"]["soc_step_pct"] == [10, 5, 2]
    assert experiments["E3"]["candidate_cap"] == [12, 24, 50, 98, 200]
    assert experiments["E4"]["corridor_width_km"] == [10, 25, 50]
    assert (experiments["E6"]["warmups"], experiments["E6"]["repetitions"]) == (3, 20)
    for name in ("E1", "E2", "E3", "E4"):
        assert experiments[name]["routes"] == route_ids
    e5_routes = experiments["E5"]["routes"]
    assert len(e5_routes) == 8
    assert set(e5_routes) <= set(route_ids)
    assert Counter(route["band"] for route in manifest["routes"] if route["id"] in e5_routes) == {
        band.name: 2 for band in DISTANCE_BANDS
    }
    sets = manifest["parameter_sets"]
    assert [
        (
            sets[name]["usable_battery_kwh"],
            sets[name]["max_dc_power_kw"],
            sets[name]["consumption_kwh_per_100km"],
        )
        for name in ("set-40kwh", "set-60kwh", "set-80kwh")
    ] == [(40, 50, 16), (60, 150, 18), (80, 250, 20)]
    assert all(
        (
            sets[name]["initial_soc_pct"],
            sets[name]["reserve_soc_pct"],
            sets[name]["energy_safety_factor"],
        )
        == (80, 10, 1.1)
        for name in ("set-40kwh", "set-60kwh", "set-80kwh")
    )


def test_manifest_contains_no_station_rows_or_identifiers() -> None:
    text = (BENCHMARK_DIR / "manifest.json").read_text(encoding="utf-8")
    for forbidden in ("sarjIstasyonu", "soketNo", "epdk:", "ŞRJ/"):
        assert forbidden not in text


def _synthetic_provinces(count: int) -> tuple[Province, ...]:
    return tuple(
        Province(
            plate=plate,
            name=f"P{plate}",
            region="Doğu Anadolu" if plate % 2 else "Marmara",
            latitude=39.0,
            longitude=33.0,
        )
        for plate in range(1, count + 1)
    )


def _synthetic_distances(provinces: tuple[Province, ...]) -> dict[str, float]:
    midpoints = (200.0, 450.0, 800.0, 1200.0)
    return {
        distance_key(origin.plate, destination.plate): midpoints[
            (origin.plate + destination.plate) % 4
        ]
        for index, origin in enumerate(provinces)
        for destination in provinces[index + 1 :]
    }


def test_selection_does_not_depend_on_distance_mapping_order() -> None:
    provinces = _synthetic_provinces(40)
    distances = _synthetic_distances(provinces)
    assert select_routes(provinces, distances) == select_routes(
        provinces, dict(reversed(list(distances.items())))
    )


def test_selection_fills_eastern_slots_first() -> None:
    provinces = _synthetic_provinces(40)
    routes = select_routes(provinces, _synthetic_distances(provinces))
    assert all(route.is_eastern for route in routes if route.slot < 2)


def test_unreachable_pairs_are_never_selected() -> None:
    provinces = _synthetic_provinces(40)
    distances = _synthetic_distances(provinces)
    first = select_routes(provinces, distances)[0]
    del distances[distance_key(first.origin.plate, first.destination.plate)]
    assert (first.origin, first.destination) not in {
        (route.origin, route.destination) for route in select_routes(provinces, distances)
    }


def test_a_band_that_cannot_be_filled_fails_instead_of_shrinking() -> None:
    provinces = _synthetic_provinces(40)
    distances = {key: 200.0 for key in _synthetic_distances(provinces)}
    with pytest.raises(ValueError, match="band 300-600"):
        select_routes(provinces, distances)
