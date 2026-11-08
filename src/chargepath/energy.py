"""Deterministic energy and charging-time calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from chargepath.models import RoadLeg, VehicleProfile
from chargepath.validation import require_finite as _require_finite


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


@dataclass(frozen=True, slots=True)
class LegEnergy:
    """Battery energy for one driven leg.

    ``net_kwh`` is the change over the whole leg and is negative when recuperation recovers more
    than the leg uses. ``peak_kwh`` is the largest cumulative draw at any point along the leg, so a
    climb followed by a descent cannot hide a reserve violation in the middle of the leg.
    """

    net_kwh: float
    peak_kwh: float

    def __post_init__(self) -> None:
        _require_finite(self.net_kwh, field="net_kwh")
        _require_finite(self.peak_kwh, field="peak_kwh")
        if self.peak_kwh < 0 or self.peak_kwh < self.net_kwh:
            raise ValueError("peak_kwh must be non-negative and at least net_kwh")


class ConsumptionModel(Protocol):
    """Energy model for driven legs. Implementations must be hashable and deterministic."""

    def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy: ...


@dataclass(frozen=True, slots=True)
class ConstantConsumption:
    """The default model: distance times the vehicle's constant rate and safety factor."""

    def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
        kwh = energy_for_distance(
            leg.distance_km,
            vehicle.consumption_kwh_per_100km,
            vehicle.energy_safety_factor,
        )
        return LegEnergy(net_kwh=kwh, peak_kwh=kwh)


DEFAULT_CONSUMPTION = ConstantConsumption()

# Per-leg (net, peak) bucket counts, cached per vehicle, model, and bucket size.
LegBuckets = tuple[int, int]
LegBucketCache = dict[tuple[VehicleProfile, ConsumptionModel, float], dict[RoadLeg, LegBuckets]]


def leg_buckets(energy: LegEnergy, bucket_kwh: float) -> LegBuckets:
    """Convert leg energy to whole buckets, conservatively in both directions.

    ``ceil`` rounds a draw up and a recovered amount down, so discretization never adds range.
    """

    _require_finite(bucket_kwh, field="bucket_kwh")
    if bucket_kwh <= 0:
        raise ValueError("bucket_kwh must be positive")
    return math.ceil(energy.net_kwh / bucket_kwh), math.ceil(energy.peak_kwh / bucket_kwh)


def drive_bucket(
    energy_bucket: int,
    buckets: LegBuckets,
    *,
    reserve_bucket: int,
    maximum_bucket: int,
) -> int | None:
    """Return the bucket after a leg, or ``None`` when any point on it would break the reserve.

    Recovered energy cannot raise the battery above full.
    """

    net, peak = buckets
    if energy_bucket - peak < reserve_bucket:
        return None
    remaining = min(maximum_bucket, energy_bucket - net)
    return remaining if remaining >= reserve_bucket else None


def leg_bucket_lookup(
    cache: LegBucketCache,
    *,
    vehicle: VehicleProfile,
    model: ConsumptionModel,
    bucket_kwh: float,
) -> dict[RoadLeg, LegBuckets]:
    """Return the per-leg cache for one vehicle, model, and bucket size."""

    return cache.setdefault((vehicle, model, bucket_kwh), {})


def buckets_for_leg(
    lookup: dict[RoadLeg, LegBuckets],
    leg: RoadLeg,
    *,
    vehicle: VehicleProfile,
    model: ConsumptionModel,
    bucket_kwh: float,
) -> LegBuckets:
    buckets = lookup.get(leg)
    if buckets is None:
        buckets = leg_buckets(model.leg_energy(leg, vehicle), bucket_kwh)
        lookup[leg] = buckets
    return buckets


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
    """Estimate charging minutes plus a session overhead.

    With ``vehicle.charging_curve`` the accepted power follows that piecewise-linear curve, capped
    by the station and the vehicle. Without it, a generic two-segment taper applies.
    """
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
    if vehicle.charging_curve is not None:
        return setup_minutes + _curve_minutes(
            vehicle.charging_curve,
            vehicle.usable_battery_kwh,
            effective_power_kw,
            from_soc_pct,
            to_soc_pct,
        )
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


def _curve_power(curve: tuple[tuple[float, float], ...], soc_pct: float) -> float:
    for (soc_a, power_a), (soc_b, power_b) in pairwise(curve):
        if soc_a <= soc_pct <= soc_b:
            return power_a + (power_b - power_a) * (soc_pct - soc_a) / (soc_b - soc_a)
    raise ValueError("SOC is outside the charging curve")


def _curve_minutes(
    curve: tuple[tuple[float, float], ...],
    usable_battery_kwh: float,
    cap_kw: float,
    from_soc_pct: float,
    to_soc_pct: float,
) -> float:
    """Integrate time over SOC with power ``min(curve(soc), cap)``, which is linear between cuts."""

    cuts = {from_soc_pct, to_soc_pct}
    for (soc_a, power_a), (soc_b, power_b) in pairwise(curve):
        if from_soc_pct < soc_a < to_soc_pct:
            cuts.add(soc_a)
        if (power_a - cap_kw) * (power_b - cap_kw) < 0:
            crossing = soc_a + (cap_kw - power_a) * (soc_b - soc_a) / (power_b - power_a)
            if from_soc_pct < crossing < to_soc_pct:
                cuts.add(crossing)
    kwh_per_pct = usable_battery_kwh / 100
    hours = 0.0
    for start, end in pairwise(sorted(cuts)):
        power_start = min(_curve_power(curve, start), cap_kw)
        power_end = min(_curve_power(curve, end), cap_kw)
        if math.isclose(power_start, power_end, rel_tol=1e-12, abs_tol=0.0):
            hours += kwh_per_pct * (end - start) / power_start
        else:
            # Power is linear in SOC on this interval: integral of dSOC / P(SOC).
            hours += (
                kwh_per_pct
                * (end - start)
                * math.log(power_end / power_start)
                / (power_end - power_start)
            )
    return 60 * hours
