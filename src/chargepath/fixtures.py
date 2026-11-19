"""Strict loaders for small, explicitly synthetic repository fixtures."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chargepath import validation
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


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _geojson_line_string(value: Any, *, field: str) -> GeoJsonLineString:
    geometry = validation.json_object(value, field=field)
    validation.exact_fields(geometry, field=field, expected={"type", "coordinates"})
    if geometry.get("type") != "LineString":
        raise ValueError(f"{field}.type must be LineString")
    coordinate_rows = validation.json_array(
        geometry.get("coordinates"), field=f"{field}.coordinates"
    )
    coordinates: list[tuple[float, float]] = []
    for index, coordinate_value in enumerate(coordinate_rows):
        coordinate = validation.json_array(coordinate_value, field=f"{field}.coordinates[{index}]")
        if len(coordinate) != 2:
            raise ValueError(f"{field}.coordinates[{index}] must contain longitude and latitude")
        coordinates.append(
            (
                _number(coordinate[0], field=f"{field}.coordinates[{index}][0]"),
                _number(coordinate[1], field=f"{field}.coordinates[{index}][1]"),
            )
        )
    return GeoJsonLineString(tuple(coordinates))


VEHICLE_FIELDS = frozenset(
    {
        "name",
        "usable_battery_kwh",
        "initial_soc_pct",
        "consumption_kwh_per_100km",
        "max_dc_power_kw",
        "reserve_soc_pct",
        "energy_safety_factor",
        "supported_dc_connectors",
    }
)
STATION_FIELDS = frozenset(
    {"id", "name", "latitude", "longitude", "max_power_kw", "connector_type"}
)


def vehicle_from_json(value: Any, *, field: str) -> VehicleProfile:
    row = validation.exact_object(value, field=field, expected=set(VEHICLE_FIELDS))
    connectors = validation.json_array(
        row["supported_dc_connectors"], field=f"{field}.supported_dc_connectors"
    )
    return VehicleProfile(
        name=row["name"],
        usable_battery_kwh=row["usable_battery_kwh"],
        initial_soc_pct=row["initial_soc_pct"],
        consumption_kwh_per_100km=row["consumption_kwh_per_100km"],
        max_dc_power_kw=row["max_dc_power_kw"],
        reserve_soc_pct=row["reserve_soc_pct"],
        energy_safety_factor=row["energy_safety_factor"],
        supported_dc_connectors=tuple(
            validation.text(item, field=f"{field}.supported_dc_connectors[]") for item in connectors
        ),
    )


def station_from_json(value: Any, *, field: str) -> Station:
    row = validation.exact_object(value, field=field, expected=set(STATION_FIELDS))
    return Station(
        id=row["id"],
        name=row["name"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        max_power_kw=row["max_power_kw"],
        connector_type=row["connector_type"],
    )


def leg_from_json(value: Any, *, field: str, with_geometry: bool) -> RoadLeg:
    expected = {"origin_id", "destination_id", "distance_km", "duration_minutes"}
    row = validation.exact_object(
        value, field=field, expected=expected | {"geometry"} if with_geometry else expected
    )
    return RoadLeg(
        origin_id=row["origin_id"],
        destination_id=row["destination_id"],
        distance_km=row["distance_km"],
        duration_minutes=row["duration_minutes"],
        geometry=(
            _geojson_line_string(row["geometry"], field=f"{field}.geometry")
            if with_geometry
            else None
        ),
    )


SYNTHETIC_SCENARIO_PATH = Path(__file__).with_name("data") / "synthetic_corridor.json"


def load_synthetic_scenario(path: str | Path = SYNTHETIC_SCENARIO_PATH) -> SyntheticScenario:
    """Load a repository fixture and reject unlabeled or structurally invalid data."""

    fixture_path = Path(path)
    try:
        payload = validation.json_object(
            json.loads(fixture_path.read_text(encoding="utf-8")), field="root"
        )
        validation.exact_fields(
            payload,
            field="root",
            expected={"metadata", "trip", "vehicle", "stations", "legs"},
        )
        metadata = validation.json_object(payload["metadata"], field="metadata")
        validation.exact_fields(
            metadata,
            field="metadata",
            expected={"name", "schema_version", "synthetic", "purpose"},
        )
        if metadata.get("synthetic") is not True:
            raise ValueError("fixture metadata.synthetic must be true")
        if metadata.get("schema_version") != 2:
            raise ValueError("unsupported synthetic fixture schema_version")

        trip = validation.json_object(payload["trip"], field="trip")
        validation.exact_fields(trip, field="trip", expected={"origin_id", "destination_id"})
        scenario = SyntheticScenario(
            origin_id=validation.text(trip["origin_id"], field="trip.origin_id"),
            destination_id=validation.text(trip["destination_id"], field="trip.destination_id"),
            vehicle=vehicle_from_json(payload["vehicle"], field="vehicle"),
            stations=tuple(
                station_from_json(row, field=f"stations[{index}]")
                for index, row in enumerate(
                    validation.json_array(payload["stations"], field="stations")
                )
            ),
            legs=tuple(
                leg_from_json(row, field=f"legs[{index}]", with_geometry=True)
                for index, row in enumerate(validation.json_array(payload["legs"], field="legs"))
            ),
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
