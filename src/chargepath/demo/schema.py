"""Request schema for the demo API: map points, vehicle inputs, and plan requests."""

from __future__ import annotations

from dataclasses import dataclass

from chargepath import validation
from chargepath.models import VehicleProfile

# One table drives request validation and the bounds the browser form receives.
VEHICLE_INPUT_BOUNDS: dict[str, tuple[float, float]] = {
    "usable_battery_kwh": (20, 200),
    "initial_soc_pct": (5, 100),
    "consumption_kwh_per_100km": (8, 40),
    "max_dc_power_kw": (20, 400),
    "reserve_soc_pct": (5, 40),
    "energy_safety_factor": (1, 1.5),
}


def _text(value: object, *, field: str) -> str:
    return validation.text(value, field=field).strip()


@dataclass(frozen=True, slots=True)
class PointInput:
    id: str
    longitude: float
    latitude: float

    def __post_init__(self) -> None:
        _text(self.id, field="point.id")
        validation.bounded_number(
            self.longitude, field="point.longitude", minimum=-180, maximum=180
        )
        validation.bounded_number(self.latitude, field="point.latitude", minimum=-90, maximum=90)

    @classmethod
    def from_value(cls, value: object, *, field: str) -> PointInput:
        row = validation.exact_object(value, field=field, expected={"id", "longitude", "latitude"})
        return cls(
            id=_text(row["id"], field=f"{field}.id"),
            longitude=validation.bounded_number(
                row["longitude"], field=f"{field}.longitude", minimum=-180, maximum=180
            ),
            latitude=validation.bounded_number(
                row["latitude"], field=f"{field}.latitude", minimum=-90, maximum=90
            ),
        )


@dataclass(frozen=True, slots=True)
class VehicleInputs:
    usable_battery_kwh: float
    initial_soc_pct: float
    consumption_kwh_per_100km: float
    max_dc_power_kw: float
    reserve_soc_pct: float
    energy_safety_factor: float

    def __post_init__(self) -> None:
        for field, (minimum, maximum) in VEHICLE_INPUT_BOUNDS.items():
            validation.bounded_number(
                getattr(self, field), field=f"vehicle.{field}", minimum=minimum, maximum=maximum
            )
        if self.initial_soc_pct < self.reserve_soc_pct:
            raise ValueError("initial SOC must be at least the reserve SOC")

    @classmethod
    def from_value(cls, value: object) -> VehicleInputs:
        row = validation.exact_object(value, field="vehicle", expected=set(VEHICLE_INPUT_BOUNDS))
        return cls(
            **{
                field: validation.bounded_number(
                    row[field], field=f"vehicle.{field}", minimum=minimum, maximum=maximum
                )
                for field, (minimum, maximum) in VEHICLE_INPUT_BOUNDS.items()
            }
        )

    @classmethod
    def from_profile(cls, vehicle: VehicleProfile) -> VehicleInputs:
        return cls(**{field: getattr(vehicle, field) for field in VEHICLE_INPUT_BOUNDS})

    def to_profile(self) -> VehicleProfile:
        return VehicleProfile(
            name="Demo EV",
            usable_battery_kwh=self.usable_battery_kwh,
            initial_soc_pct=self.initial_soc_pct,
            consumption_kwh_per_100km=self.consumption_kwh_per_100km,
            max_dc_power_kw=self.max_dc_power_kw,
            reserve_soc_pct=self.reserve_soc_pct,
            energy_safety_factor=self.energy_safety_factor,
            supported_dc_connectors=("CCS2",),
        )

    def to_dict(self) -> dict[str, float]:
        return {field: getattr(self, field) for field in VEHICLE_INPUT_BOUNDS}


@dataclass(frozen=True, slots=True)
class PlanRequest:
    origin: PointInput
    destination: PointInput
    vehicle: VehicleInputs

    def __post_init__(self) -> None:
        if not isinstance(self.origin, PointInput) or not isinstance(self.destination, PointInput):
            raise ValueError("plan endpoints must be PointInput values")
        if not isinstance(self.vehicle, VehicleInputs):
            raise ValueError("plan vehicle must be VehicleInputs")
        if self.origin.id == self.destination.id:
            raise ValueError("origin and destination must be different")

    @classmethod
    def from_payload(cls, payload: object) -> PlanRequest:
        row = validation.exact_object(
            payload, field="request", expected={"origin", "destination", "vehicle"}
        )
        origin = PointInput.from_value(row["origin"], field="origin")
        destination = PointInput.from_value(row["destination"], field="destination")
        if origin.id == destination.id:
            raise ValueError("origin and destination must be different")
        return cls(
            origin=origin,
            destination=destination,
            vehicle=VehicleInputs.from_value(row["vehicle"]),
        )
