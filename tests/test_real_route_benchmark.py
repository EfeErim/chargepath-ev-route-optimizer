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

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "data/benchmarks/real-routes/v1"


def _manifest() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (BENCHMARK_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    return payload


def _stored_distances() -> dict[str, float]:
    payload = json.loads((BENCHMARK_DIR / "selection_distances.json").read_text(encoding="utf-8"))
    road_km: dict[str, float] = payload["road_km"]
    return road_km


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


def test_a_band_that_cannot_be_filled_fails_instead_of_shrinking() -> None:
    provinces = _synthetic_provinces(40)
    distances = {key: 200.0 for key in _synthetic_distances(provinces)}
    with pytest.raises(ValueError, match="band 300-600"):
        select_routes(provinces, distances)
