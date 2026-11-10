"""Opt-in speed choice: drive a leg more slowly to use less energy.

Driving is most of a long EV trip, and a charging planner cannot shorten it. It can, however, trade
a few minutes of slower driving for less energy and therefore less charging. Two pieces make that
trade available to the unchanged exact search:

- :class:`SpeedChoiceNetwork` offers every provider leg at declared fractions of its provider speed;
- :class:`SpeedAwareConsumption` makes a leg's energy depend on its mean speed.

The search picks a slower variant only when the charging time it saves exceeds the driving time it
adds. A slower variant scales the whole leg uniformly; real traffic, speed limits, and minimum
speeds are not modeled, so a chosen speed is a modeled trade-off, not a driving instruction.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from chargepath.energy import LegEnergy, energy_for_distance
from chargepath.models import RoadLeg, VehicleProfile
from chargepath.providers.base import RoadNetwork
from chargepath.validation import is_finite_number

# Squared-speed fit from the TUM ID.3 field pilot (docs/benchmarks/field-pilot/tum_id1_v1.json):
# rate(v) = constant + slope × ((v / 100)² − feature_mean), in kWh/100 km. Rewritten relative to the
# rate at the reference speed, where the fit equals its constant rate.
_PILOT_CONSTANT_RATE = 19.38787270528953
_PILOT_SLOPE = 3.8366453526851596
_PILOT_FEATURE_MEAN = 0.7529254392590666
PILOT_REFERENCE_SPEED_KMH = 100 * math.sqrt(_PILOT_FEATURE_MEAN)
PILOT_QUADRATIC_SHARE = _PILOT_SLOPE * _PILOT_FEATURE_MEAN / _PILOT_CONSTANT_RATE

DEFAULT_SPEED_FACTORS = (1.0, 0.9, 0.8)
MINIMUM_SPEED_FACTOR = 0.5


@dataclass(frozen=True, slots=True)
class SpeedAwareConsumption:
    """Scale the vehicle's constant rate with the square of the leg's mean speed.

    ``rate(v) = vehicle rate × (1 − q + q × (v / v_ref)²)``. At ``v_ref`` the model equals the
    vehicle's constant rate. ``q`` is the speed-dependent share of consumption at ``v_ref``,
    mostly aerodynamic drag. The defaults come from one vehicle's public telemetry and are an
    example, not a calibration for other cars.
    """

    quadratic_share: float = PILOT_QUADRATIC_SHARE
    reference_speed_kmh: float = PILOT_REFERENCE_SPEED_KMH

    def __post_init__(self) -> None:
        if not is_finite_number(self.quadratic_share) or not 0 <= self.quadratic_share < 1:
            raise ValueError("quadratic_share must be in [0, 1)")
        if not is_finite_number(self.reference_speed_kmh) or self.reference_speed_kmh <= 0:
            raise ValueError("reference_speed_kmh must be positive")

    def rate_factor(self, speed_kmh: float) -> float:
        ratio = speed_kmh / self.reference_speed_kmh
        return 1 - self.quadratic_share + self.quadratic_share * ratio * ratio

    def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
        speed_kmh = leg.distance_km * 60 / leg.duration_minutes
        kwh = energy_for_distance(
            leg.distance_km,
            vehicle.consumption_kwh_per_100km * self.rate_factor(speed_kmh),
            vehicle.energy_safety_factor,
        )
        return LegEnergy(net_kwh=kwh, peak_kwh=kwh)


def _validated_factors(factors: Sequence[float]) -> tuple[float, ...]:
    values = tuple(factors)
    if not values or values[0] != 1.0:
        raise ValueError("speed_factors must start with 1.0, the provider speed")
    if not all(is_finite_number(value) for value in values):
        raise ValueError("speed_factors must be finite numbers")
    if any(later >= earlier for earlier, later in zip(values, values[1:], strict=False)):
        raise ValueError("speed_factors must decrease strictly")
    if values[-1] < MINIMUM_SPEED_FACTOR:
        raise ValueError(f"speed_factors must be at least {MINIMUM_SPEED_FACTOR}")
    return tuple(float(value) for value in values)


class SpeedChoiceNetwork:
    """Offer each leg of ``base`` at every declared fraction of its provider speed.

    Variants share endpoints, distance, and geometry; only the duration grows by ``1 / factor``.
    Use it with a speed-dependent consumption model; under a constant rate a slower variant is
    never useful.
    """

    def __init__(
        self, base: RoadNetwork, speed_factors: Sequence[float] = DEFAULT_SPEED_FACTORS
    ) -> None:
        self._base = base
        self.speed_factors = _validated_factors(speed_factors)
        self._cache: dict[str, tuple[RoadLeg, ...]] = {}
        self._factor_by_leg: dict[RoadLeg, float] = {}

    def neighbors(self, node_id: str) -> tuple[RoadLeg, ...]:
        cached = self._cache.get(node_id)
        if cached is not None:
            return cached
        legs: list[RoadLeg] = []
        for leg in self._base.neighbors(node_id):
            for factor in self.speed_factors:
                variant = (
                    leg
                    if factor == 1.0
                    else replace(leg, duration_minutes=leg.duration_minutes / factor)
                )
                self._factor_by_leg.setdefault(variant, factor)
                legs.append(variant)
        result = tuple(legs)
        self._cache[node_id] = result
        return result

    def speed_factor(self, leg: RoadLeg) -> float:
        """Return the factor a leg returned by :meth:`neighbors` was offered at."""

        try:
            return self._factor_by_leg[leg]
        except KeyError:
            raise ValueError("leg was not offered by this network") from None
