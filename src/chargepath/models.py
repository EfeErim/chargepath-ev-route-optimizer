"""Domain models shared by providers, optimization, and presentation layers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


class NoFeasibleRouteError(RuntimeError):
    """Raised when no route satisfies the modeled energy constraints."""


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


@dataclass(frozen=True, slots=True)
class GeoJsonLineString:
    """Typed GeoJSON LineString using WGS84 ``longitude, latitude`` positions."""

    coordinates: tuple[tuple[float, float], ...]
    type: Literal["LineString"] = "LineString"

    def __post_init__(self) -> None:
        if self.type != "LineString":
            raise ValueError("GeoJSON geometry type must be LineString")
        if len(self.coordinates) < 2:
            raise ValueError("GeoJSON LineString must contain at least two positions")
        for longitude, latitude in self.coordinates:
            if not _is_finite_number(longitude) or not -180 <= longitude <= 180:
                raise ValueError("GeoJSON longitude must be finite and between -180 and 180")
            if not _is_finite_number(latitude) or not -90 <= latitude <= 90:
                raise ValueError("GeoJSON latitude must be finite and between -90 and 90")


@dataclass(frozen=True, slots=True)
class VehicleProfile:
    name: str
    usable_battery_kwh: float
    initial_soc_pct: float
    consumption_kwh_per_100km: float
    max_dc_power_kw: float
    reserve_soc_pct: float = 10.0
    energy_safety_factor: float = 1.10
    supported_dc_connectors: tuple[str, ...] = ("CCS2",)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("vehicle name must not be empty")
        numeric_values = (
            self.usable_battery_kwh,
            self.initial_soc_pct,
            self.consumption_kwh_per_100km,
            self.max_dc_power_kw,
            self.reserve_soc_pct,
            self.energy_safety_factor,
        )
        if not all(_is_finite_number(value) for value in numeric_values):
            raise ValueError("vehicle numeric values must be finite numbers")
        if self.usable_battery_kwh <= 0:
            raise ValueError("usable_battery_kwh must be positive")
        if self.consumption_kwh_per_100km <= 0:
            raise ValueError("consumption_kwh_per_100km must be positive")
        if self.max_dc_power_kw <= 0:
            raise ValueError("max_dc_power_kw must be positive")
        if not 0 <= self.initial_soc_pct <= 100:
            raise ValueError("initial_soc_pct must be between 0 and 100")
        if not 0 <= self.reserve_soc_pct < 100:
            raise ValueError("reserve_soc_pct must be between 0 and 100")
        if self.initial_soc_pct < self.reserve_soc_pct:
            raise ValueError("initial_soc_pct must not be below reserve_soc_pct")
        if self.energy_safety_factor < 1:
            raise ValueError("energy_safety_factor must be at least 1")
        if not isinstance(self.supported_dc_connectors, tuple) or any(
            not isinstance(connector, str) for connector in self.supported_dc_connectors
        ):
            raise ValueError("supported_dc_connectors must be a tuple of text values")
        normalized_connectors = tuple(
            connector.strip().upper() for connector in self.supported_dc_connectors
        )
        if not normalized_connectors or any(not connector for connector in normalized_connectors):
            raise ValueError("supported_dc_connectors must contain non-empty values")
        if len(set(normalized_connectors)) != len(normalized_connectors):
            raise ValueError("supported_dc_connectors must not contain duplicates")
        object.__setattr__(self, "supported_dc_connectors", normalized_connectors)


@dataclass(frozen=True, slots=True)
class Station:
    id: str
    name: str
    latitude: float
    longitude: float
    max_power_kw: float
    connector_type: str = "CCS2"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not isinstance(self.name, str)
            or not self.id.strip()
            or not self.name.strip()
        ):
            raise ValueError("station id and name must not be empty")
        if not _is_finite_number(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not _is_finite_number(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if not _is_finite_number(self.max_power_kw) or self.max_power_kw <= 0:
            raise ValueError("max_power_kw must be positive")
        if not isinstance(self.connector_type, str):
            raise ValueError("connector_type must be text")
        connector_type = self.connector_type.strip().upper()
        if not connector_type:
            raise ValueError("connector_type must not be empty")
        object.__setattr__(self, "connector_type", connector_type)


@dataclass(frozen=True, slots=True)
class RoadLeg:
    origin_id: str
    destination_id: str
    distance_km: float
    duration_minutes: float
    geometry: GeoJsonLineString | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.origin_id, str)
            or not isinstance(self.destination_id, str)
            or not self.origin_id.strip()
            or not self.destination_id.strip()
        ):
            raise ValueError("road-leg endpoints must not be empty")
        if self.origin_id == self.destination_id:
            raise ValueError("road-leg endpoints must be different")
        if not _is_finite_number(self.distance_km) or self.distance_km <= 0:
            raise ValueError("distance_km must be positive")
        if not _is_finite_number(self.duration_minutes) or self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")
        if self.geometry is not None and not isinstance(self.geometry, GeoJsonLineString):
            raise ValueError("road-leg geometry must be a GeoJsonLineString when supplied")


@dataclass(frozen=True, slots=True)
class ChargingStop:
    station_id: str
    arrival_soc_pct: float
    departure_soc_pct: float
    energy_added_kwh: float
    charging_minutes: float

    def __post_init__(self) -> None:
        if not isinstance(self.station_id, str) or not self.station_id.strip():
            raise ValueError("charging-stop station_id must not be empty")
        numeric_values = (
            self.arrival_soc_pct,
            self.departure_soc_pct,
            self.energy_added_kwh,
            self.charging_minutes,
        )
        if not all(_is_finite_number(value) for value in numeric_values):
            raise ValueError("charging-stop numeric values must be finite numbers")
        if not 0 <= self.arrival_soc_pct < self.departure_soc_pct <= 100:
            raise ValueError("charging-stop SOC must satisfy 0 <= arrival < departure <= 100")
        if self.energy_added_kwh <= 0:
            raise ValueError("charging-stop energy_added_kwh must be positive")
        if self.charging_minutes < 0:
            raise ValueError("charging-stop charging_minutes must not be negative")


@dataclass(frozen=True, slots=True)
class RoutePlan:
    node_ids: tuple[str, ...]
    legs: tuple[RoadLeg, ...]
    charging_stops: tuple[ChargingStop, ...]
    total_distance_km: float
    driving_minutes: float
    charging_minutes: float
    arrival_soc_pct: float
    total_minutes: float

    def __post_init__(self) -> None:
        if not self.node_ids or any(
            not isinstance(node_id, str) or not node_id.strip() for node_id in self.node_ids
        ):
            raise ValueError("route-plan node_ids must contain non-empty values")
        if len(self.legs) != len(self.node_ids) - 1:
            raise ValueError("route-plan legs must connect every consecutive node")
        for index, leg in enumerate(self.legs):
            if (leg.origin_id, leg.destination_id) != (
                self.node_ids[index],
                self.node_ids[index + 1],
            ):
                raise ValueError("route-plan leg endpoints must match node order")
        numeric_values = (
            self.total_distance_km,
            self.driving_minutes,
            self.charging_minutes,
            self.arrival_soc_pct,
            self.total_minutes,
        )
        if not all(_is_finite_number(value) for value in numeric_values):
            raise ValueError("route-plan numeric values must be finite numbers")
        if not 0 <= self.arrival_soc_pct <= 100:
            raise ValueError("route-plan arrival_soc_pct must be between 0 and 100")
        if any(
            value < 0
            for value in (
                self.total_distance_km,
                self.driving_minutes,
                self.charging_minutes,
                self.total_minutes,
            )
        ):
            raise ValueError("route-plan totals must not be negative")

        expected_distance = sum(leg.distance_km for leg in self.legs)
        expected_driving = sum(leg.duration_minutes for leg in self.legs)
        expected_charging = sum(stop.charging_minutes for stop in self.charging_stops)
        if not math.isclose(self.total_distance_km, expected_distance, abs_tol=1e-9):
            raise ValueError("route-plan total_distance_km does not match its legs")
        if not math.isclose(self.driving_minutes, expected_driving, abs_tol=1e-9):
            raise ValueError("route-plan driving_minutes does not match its legs")
        if not math.isclose(self.charging_minutes, expected_charging, abs_tol=1e-9):
            raise ValueError("route-plan charging_minutes does not match its stops")
        if not math.isclose(
            self.total_minutes,
            self.driving_minutes + self.charging_minutes,
            abs_tol=1e-9,
        ):
            raise ValueError("route-plan total_minutes must equal driving plus charging")
