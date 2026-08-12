"""Deterministic energy and charging-time calculations."""

from __future__ import annotations

import math

from chargepath.models import VehicleProfile


def _require_finite(value: float, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")


def energy_for_distance(
    distance_km: float,
    consumption_kwh_per_100km: float,
    safety_factor: float = 1.0,
) -> float:
    _require_finite(distance_km, field="distance_km")
    _require_finite(consumption_kwh_per_100km, field="consumption")
    _require_finite(safety_factor, field="safety_factor")
    if distance_km < 0:
        raise ValueError("distance_km must not be negative")
    if consumption_kwh_per_100km <= 0:
        raise ValueError("consumption must be positive")
    if safety_factor < 1:
        raise ValueError("safety_factor must be at least 1")
    return distance_km * consumption_kwh_per_100km / 100 * safety_factor


def soc_to_kwh(soc_pct: float, usable_battery_kwh: float) -> float:
    _require_finite(soc_pct, field="soc_pct")
    _require_finite(usable_battery_kwh, field="usable_battery_kwh")
    if not 0 <= soc_pct <= 100:
        raise ValueError("soc_pct must be between 0 and 100")
    if usable_battery_kwh <= 0:
        raise ValueError("usable_battery_kwh must be positive")
    return usable_battery_kwh * soc_pct / 100


def kwh_to_soc(energy_kwh: float, usable_battery_kwh: float) -> float:
    _require_finite(energy_kwh, field="energy_kwh")
    _require_finite(usable_battery_kwh, field="usable_battery_kwh")
    if energy_kwh < 0:
        raise ValueError("energy_kwh must not be negative")
    if usable_battery_kwh <= 0:
        raise ValueError("usable_battery_kwh must be positive")
    return energy_kwh / usable_battery_kwh * 100


def required_energy_buckets(energy_kwh: float, bucket_kwh: float) -> int:
    """Round required energy upward so discretization never invents range."""
    _require_finite(energy_kwh, field="energy_kwh")
    _require_finite(bucket_kwh, field="bucket_kwh")
    if energy_kwh < 0:
        raise ValueError("energy_kwh must not be negative")
    if bucket_kwh <= 0:
        raise ValueError("bucket_kwh must be positive")
    return math.ceil(energy_kwh / bucket_kwh)


def charging_minutes(
    vehicle: VehicleProfile,
    station_power_kw: float,
    from_soc_pct: float,
    to_soc_pct: float,
    *,
    taper_start_pct: float = 80.0,
    taper_factor: float = 0.45,
    setup_minutes: float = 2.0,
) -> float:
    """Approximate a two-segment charging curve plus a session overhead."""
    for value, field in (
        (station_power_kw, "station_power_kw"),
        (from_soc_pct, "from_soc_pct"),
        (to_soc_pct, "to_soc_pct"),
        (taper_start_pct, "taper_start_pct"),
        (taper_factor, "taper_factor"),
        (setup_minutes, "setup_minutes"),
    ):
        _require_finite(value, field=field)
    if station_power_kw <= 0:
        raise ValueError("station_power_kw must be positive")
    if not 0 <= from_soc_pct <= to_soc_pct <= 100:
        raise ValueError("charge SOC values must satisfy 0 <= from <= to <= 100")
    if not 0 < taper_start_pct < 100:
        raise ValueError("taper_start_pct must be between 0 and 100")
    if not 0 < taper_factor <= 1:
        raise ValueError("taper_factor must be between 0 and 1")
    if setup_minutes < 0:
        raise ValueError("setup_minutes must not be negative")
    if from_soc_pct == to_soc_pct:
        return 0.0

    effective_power_kw = min(vehicle.max_dc_power_kw, station_power_kw)
    full_speed_pct = max(
        0.0,
        min(to_soc_pct, taper_start_pct) - min(from_soc_pct, taper_start_pct),
    )
    tapered_pct = max(0.0, to_soc_pct - max(from_soc_pct, taper_start_pct))
    full_speed_kwh = soc_to_kwh(full_speed_pct, vehicle.usable_battery_kwh)
    tapered_kwh = soc_to_kwh(tapered_pct, vehicle.usable_battery_kwh)
    energy_minutes = 60 * (
        full_speed_kwh / effective_power_kw + tapered_kwh / (effective_power_kw * taper_factor)
    )
    return setup_minutes + energy_minutes
