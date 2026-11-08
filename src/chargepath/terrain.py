"""Experimental, opt-in terrain-aware consumption model.

The model keeps the vehicle's constant rate for rolling and air resistance and adds the potential
energy of each leg's climbs and descents, following the structure of power-based EV models such as
Fiori, Ahn, and Rakha (2016) in steady state:

```text
climb_kwh = mass × g × ascent_m / 3.6e6 / drive_efficiency
regen_kwh = mass × g × descent_m × regen_efficiency / 3.6e6
net_kwh   = constant_kwh + climb_kwh − regen_kwh
peak_kwh  = constant_kwh + climb_kwh            # as if every climb came before every descent
```

The peak assumption is deliberately conservative. Elevation must be supplied per leg by the caller;
the project ships no elevation data. The model is not calibrated and is not the planner default; see
``docs/results.md`` for the held-out comparison that decides that.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from chargepath.energy import LegEnergy, energy_for_distance
from chargepath.models import RoadLeg, VehicleProfile
from chargepath.validation import is_finite_number

GRAVITY_M_PER_S2 = 9.80665
JOULES_PER_KWH = 3.6e6


@dataclass(frozen=True, slots=True)
class LegElevation:
    ascent_m: float
    descent_m: float

    def __post_init__(self) -> None:
        for value in (self.ascent_m, self.descent_m):
            if not is_finite_number(value) or value < 0:
                raise ValueError("ascent_m and descent_m must be finite and non-negative")


def elevation_from_profile(elevations_m: Sequence[float], *, noise_m: float = 1.0) -> LegElevation:
    """Sum climbs and descents along a sampled profile, ignoring reversals smaller than ``noise_m``.

    The hysteresis keeps sensor or terrain-model noise from being counted as repeated small climbs.
    """

    if not is_finite_number(noise_m) or noise_m < 0:
        raise ValueError("noise_m must be finite and non-negative")
    if len(elevations_m) < 2 or not all(is_finite_number(value) for value in elevations_m):
        raise ValueError("an elevation profile needs at least two finite samples")
    ascent = descent = 0.0
    anchor = float(elevations_m[0])
    for value in elevations_m[1:]:
        change = float(value) - anchor
        if abs(change) >= noise_m:
            if change > 0:
                ascent += change
            else:
                descent -= change
            anchor = float(value)
    return LegElevation(ascent_m=ascent, descent_m=descent)


@dataclass(frozen=True, slots=True)
class GradeAwareConsumption:
    """Constant rate plus climb and recuperation terms from per-leg elevation."""

    elevations: tuple[tuple[tuple[str, str], LegElevation], ...]
    mass_kg: float
    drive_efficiency: float = 0.85
    regen_efficiency: float = 0.65
    _lookup: dict[tuple[str, str], LegElevation] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        if not is_finite_number(self.mass_kg) or self.mass_kg <= 0:
            raise ValueError("mass_kg must be positive")
        if not is_finite_number(self.drive_efficiency) or not 0 < self.drive_efficiency <= 1:
            raise ValueError("drive_efficiency must be in (0, 1]")
        if not is_finite_number(self.regen_efficiency) or not 0 <= self.regen_efficiency <= 1:
            raise ValueError("regen_efficiency must be in [0, 1]")
        lookup = dict(self.elevations)
        if len(lookup) != len(self.elevations):
            raise ValueError("each leg may have only one elevation entry")
        object.__setattr__(self, "_lookup", lookup)

    def climb_and_regen_kwh(self, elevation: LegElevation) -> tuple[float, float]:
        weight_j_per_m = self.mass_kg * GRAVITY_M_PER_S2
        climb = weight_j_per_m * elevation.ascent_m / JOULES_PER_KWH / self.drive_efficiency
        regen = weight_j_per_m * elevation.descent_m * self.regen_efficiency / JOULES_PER_KWH
        return climb, regen

    def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
        elevation = self._lookup.get((leg.origin_id, leg.destination_id))
        if elevation is None:
            raise ValueError(f"no elevation for leg {leg.origin_id!r} -> {leg.destination_id!r}")
        constant = energy_for_distance(
            leg.distance_km,
            vehicle.consumption_kwh_per_100km,
            vehicle.energy_safety_factor,
        )
        climb, regen = self.climb_and_regen_kwh(elevation)
        peak = constant + climb
        return LegEnergy(net_kwh=peak - regen, peak_kwh=peak)
