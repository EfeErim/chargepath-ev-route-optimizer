"""Strict loaders for small, explicitly synthetic repository fixtures."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chargepath.models import GeoJsonLineString, RoadLeg, Station, VehicleProfile


@dataclass(frozen=True, slots=True)
class SyntheticScenario:
    origin_id: str
    destination_id: str
    vehicle: VehicleProfile
    stations: tuple[Station, ...]
    legs: tuple[RoadLeg, ...]

    def station_map(self) -> dict[str, Station]:
        return {station.id: station for station in self.stations}


def _object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _array(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _exact_keys(value: dict[str, Any], *, field: str, expected: set[str]) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {unexpected}")
        raise ValueError(f"{field} fields are invalid: {', '.join(details)}")


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _geojson_line_string(value: Any, *, field: str) -> GeoJsonLineString:
    geometry = _object(value, field=field)
    _exact_keys(geometry, field=field, expected={"type", "coordinates"})
    if geometry.get("type") != "LineString":
        raise ValueError(f"{field}.type must be LineString")
    coordinate_rows = _array(geometry.get("coordinates"), field=f"{field}.coordinates")
    coordinates: list[tuple[float, float]] = []
    for index, coordinate_value in enumerate(coordinate_rows):
        coordinate = _array(coordinate_value, field=f"{field}.coordinates[{index}]")
        if len(coordinate) != 2:
            raise ValueError(f"{field}.coordinates[{index}] must contain longitude and latitude")
        coordinates.append(
            (
                _number(coordinate[0], field=f"{field}.coordinates[{index}][0]"),
                _number(coordinate[1], field=f"{field}.coordinates[{index}][1]"),
            )
        )
    return GeoJsonLineString(tuple(coordinates))


def load_synthetic_scenario(path: str | Path) -> SyntheticScenario:
    """Load a repository fixture and reject unlabeled or structurally invalid data."""

    fixture_path = Path(path)
    try:
        payload = _object(json.loads(fixture_path.read_text(encoding="utf-8")), field="root")
        _exact_keys(
            payload,
            field="root",
            expected={"metadata", "trip", "vehicle", "stations", "legs"},
        )
        metadata = _object(payload["metadata"], field="metadata")
        _exact_keys(
            metadata,
            field="metadata",
            expected={"name", "schema_version", "synthetic", "purpose"},
        )
        if metadata.get("synthetic") is not True:
            raise ValueError("fixture metadata.synthetic must be true")
        if metadata.get("schema_version") != 2:
            raise ValueError("unsupported synthetic fixture schema_version")

        trip = _object(payload["trip"], field="trip")
        _exact_keys(trip, field="trip", expected={"origin_id", "destination_id"})
        vehicle_data = _object(payload["vehicle"], field="vehicle")
        _exact_keys(
            vehicle_data,
            field="vehicle",
            expected={
                "name",
                "usable_battery_kwh",
                "initial_soc_pct",
                "consumption_kwh_per_100km",
                "max_dc_power_kw",
                "reserve_soc_pct",
                "energy_safety_factor",
                "supported_dc_connectors",
            },
        )
        supported_connectors = _array(
            vehicle_data["supported_dc_connectors"],
            field="vehicle.supported_dc_connectors",
        )
        station_rows = _array(payload["stations"], field="stations")
        leg_rows = _array(payload["legs"], field="legs")

        vehicle = VehicleProfile(
            name=vehicle_data["name"],
            usable_battery_kwh=vehicle_data["usable_battery_kwh"],
            initial_soc_pct=vehicle_data["initial_soc_pct"],
            consumption_kwh_per_100km=vehicle_data["consumption_kwh_per_100km"],
            max_dc_power_kw=vehicle_data["max_dc_power_kw"],
            reserve_soc_pct=vehicle_data["reserve_soc_pct"],
            energy_safety_factor=vehicle_data["energy_safety_factor"],
            supported_dc_connectors=tuple(supported_connectors),
        )
        station_objects = tuple(
            _object(item, field=f"stations[{index}]") for index, item in enumerate(station_rows)
        )
        for index, row in enumerate(station_objects):
            _exact_keys(
                row,
                field=f"stations[{index}]",
                expected={
                    "id",
                    "name",
                    "latitude",
                    "longitude",
                    "max_power_kw",
                    "connector_type",
                },
            )
        stations = tuple(
            Station(
                id=row["id"],
                name=row["name"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                max_power_kw=row["max_power_kw"],
                connector_type=row["connector_type"],
            )
            for row in station_objects
        )
        leg_objects = tuple(
            _object(item, field=f"legs[{index}]") for index, item in enumerate(leg_rows)
        )
        for index, row in enumerate(leg_objects):
            _exact_keys(
                row,
                field=f"legs[{index}]",
                expected={
                    "origin_id",
                    "destination_id",
                    "distance_km",
                    "duration_minutes",
                    "geometry",
                },
            )
        legs = tuple(
            RoadLeg(
                origin_id=row["origin_id"],
                destination_id=row["destination_id"],
                distance_km=row["distance_km"],
                duration_minutes=row["duration_minutes"],
                geometry=_geojson_line_string(row["geometry"], field=f"legs[{index}].geometry"),
            )
            for index, row in enumerate(leg_objects)
        )
        scenario = SyntheticScenario(
            origin_id=_text(trip["origin_id"], field="trip.origin_id"),
            destination_id=_text(trip["destination_id"], field="trip.destination_id"),
            vehicle=vehicle,
            stations=stations,
            legs=legs,
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid synthetic fixture {fixture_path}: {exc}") from exc

    station_ids = [station.id for station in scenario.stations]
    if len(set(station_ids)) != len(station_ids):
        raise ValueError("synthetic fixture station ids must be unique")
    if scenario.origin_id == scenario.destination_id:
        raise ValueError("synthetic fixture trip endpoints must be different")
    node_ids = {scenario.origin_id, scenario.destination_id, *station_ids}
    directed_edges: set[tuple[str, str]] = set()
    for leg in scenario.legs:
        edge = (leg.origin_id, leg.destination_id)
        if leg.origin_id not in node_ids or leg.destination_id not in node_ids:
            raise ValueError("synthetic fixture legs must reference declared trip or station nodes")
        if edge in directed_edges:
            raise ValueError("synthetic fixture directed road legs must be unique")
        directed_edges.add(edge)
    return scenario
