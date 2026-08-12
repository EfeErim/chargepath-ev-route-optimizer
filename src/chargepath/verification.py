"""Independent replay checks for optimizer output."""

from __future__ import annotations

import math

from chargepath.energy import charging_minutes, energy_for_distance, required_energy_buckets
from chargepath.models import RoutePlan, Station, VehicleProfile


class PlanVerificationError(ValueError):
    """Raised when a returned plan violates its modeled contracts."""


def _close(actual: float, expected: float, *, field: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
        raise PlanVerificationError(f"{field} mismatch: expected {expected}, got {actual}")


def verify_route_plan(
    plan: RoutePlan,
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    soc_step_pct: int = 5,
    taper_start_pct: float = 80.0,
    taper_factor: float = 0.45,
    session_setup_minutes: float = 2.0,
) -> None:
    """Replay charge and drive actions without relying on optimizer predecessor state."""

    if (
        isinstance(soc_step_pct, bool)
        or not isinstance(soc_step_pct, int)
        or soc_step_pct <= 0
        or 100 % soc_step_pct != 0
    ):
        raise ValueError("soc_step_pct must be a positive divisor of 100")
    for value, field in (
        (taper_start_pct, "taper_start_pct"),
        (taper_factor, "taper_factor"),
        (session_setup_minutes, "session_setup_minutes"),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{field} must be finite")
    if not 0 < taper_start_pct < 100:
        raise ValueError("taper_start_pct must be between 0 and 100")
    if not 0 < taper_factor <= 1:
        raise ValueError("taper_factor must be between 0 and 1")
    if session_setup_minutes < 0:
        raise ValueError("session_setup_minutes must not be negative")
    bucket_kwh = vehicle.usable_battery_kwh * soc_step_pct / 100
    energy_bucket = math.floor(vehicle.initial_soc_pct / soc_step_pct)
    reserve_bucket = math.ceil(vehicle.reserve_soc_pct / soc_step_pct)
    maximum_bucket = 100 // soc_step_pct
    stop_index = 0

    for leg_index, leg in enumerate(plan.legs):
        current_node = plan.node_ids[leg_index]
        if stop_index < len(plan.charging_stops):
            stop = plan.charging_stops[stop_index]
            if stop.station_id == current_node:
                station = stations.get(current_node)
                if station is None or station.id != current_node:
                    raise PlanVerificationError("charging stop has no matching station")
                if station.connector_type not in vehicle.supported_dc_connectors:
                    raise PlanVerificationError("charging stop connector is incompatible")
                arrival_soc = energy_bucket * soc_step_pct
                _close(stop.arrival_soc_pct, arrival_soc, field="charging arrival SOC")
                departure_bucket = round(stop.departure_soc_pct / soc_step_pct)
                if not energy_bucket < departure_bucket <= maximum_bucket:
                    raise PlanVerificationError("charging departure bucket is invalid")
                _close(
                    stop.departure_soc_pct,
                    departure_bucket * soc_step_pct,
                    field="charging departure SOC",
                )
                _close(
                    stop.energy_added_kwh,
                    (departure_bucket - energy_bucket) * bucket_kwh,
                    field="charging energy",
                )
                expected_minutes = charging_minutes(
                    vehicle,
                    station.max_power_kw,
                    arrival_soc,
                    departure_bucket * soc_step_pct,
                    taper_start_pct=taper_start_pct,
                    taper_factor=taper_factor,
                    setup_minutes=session_setup_minutes,
                )
                _close(stop.charging_minutes, expected_minutes, field="charging minutes")
                energy_bucket = departure_bucket
                stop_index += 1

        if (leg.origin_id, leg.destination_id) != (
            current_node,
            plan.node_ids[leg_index + 1],
        ):
            raise PlanVerificationError("road leg does not match plan node order")
        required_kwh = energy_for_distance(
            leg.distance_km,
            vehicle.consumption_kwh_per_100km,
            vehicle.energy_safety_factor,
        )
        energy_bucket -= required_energy_buckets(required_kwh, bucket_kwh)
        if energy_bucket < reserve_bucket:
            raise PlanVerificationError("road leg violates reserve SOC")

    if stop_index != len(plan.charging_stops):
        raise PlanVerificationError("charging stops are not in drive order")
    _close(plan.arrival_soc_pct, energy_bucket * soc_step_pct, field="arrival SOC")
