"""Independent replay checks for optimizer output."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from chargepath import validation
from chargepath.energy import (
    DEFAULT_CONSUMPTION,
    ConsumptionModel,
    SocGrid,
    charging_minutes,
    leg_buckets,
)
from chargepath.models import RoadLeg, RoutePlan, Station, VehicleProfile
from chargepath.providers.base import RoadNetwork


class PlanVerificationError(ValueError):
    """Raised when a returned plan violates its modeled contracts."""


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """Battery level after one replayed action, as the replay computed it."""

    kind: Literal["start", "charge", "arrive"]
    node_id: str
    soc_pct: float


@dataclass(frozen=True, slots=True)
class Replay:
    """Replayed battery levels and the number of driven legs that went below the reserve."""

    events: tuple[ReplayEvent, ...]
    reserve_violations: int


def _close(actual: float, expected: float, *, field: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
        raise PlanVerificationError(f"{field} mismatch: expected {expected}, got {actual}")


def _offered_by(road_network: RoadNetwork, leg: RoadLeg) -> bool:
    """Compare costs, not geometry: geometry is attached only after a plan is chosen."""

    return any(
        (offered.destination_id, offered.distance_km, offered.duration_minutes)
        == (leg.destination_id, leg.distance_km, leg.duration_minutes)
        for offered in road_network.neighbors(leg.origin_id)
    )


def verify_route_plan(
    plan: RoutePlan,
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    soc_step_pct: int = 5,
    consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    road_network: RoadNetwork | None = None,
    origin_id: str | None = None,
    destination_id: str | None = None,
) -> tuple[ReplayEvent, ...]:
    """Replay a plan and raise on any broken contract, including a reserve violation.

    See :func:`replay_route_plan` for what is checked.
    """

    return _replay(
        plan,
        vehicle=vehicle,
        stations=stations,
        soc_step_pct=soc_step_pct,
        consumption_model=consumption_model,
        road_network=road_network,
        origin_id=origin_id,
        destination_id=destination_id,
        raise_on_reserve_violation=True,
    ).events


def replay_route_plan(
    plan: RoutePlan,
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    soc_step_pct: int = 5,
    consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    road_network: RoadNetwork | None = None,
    origin_id: str | None = None,
    destination_id: str | None = None,
) -> Replay:
    """Replay charge and drive actions without relying on optimizer predecessor state.

    Driven legs that go below the reserve are counted, not raised, so evidence reports a measured
    count. Every other broken contract raises :class:`PlanVerificationError`; a plan whose stated
    SOC values assume a leg it cannot drive usually fails one of those checks further on.

    The replay shares the energy and charging-time functions with the optimizer, so it catches
    search and reconstruction bugs, not model bugs. ``tests/test_properties.py`` checks the model
    against a separate exact-arithmetic reference.

    With ``road_network``, every driven leg must be one that network offers, with the same
    distance and duration, so a plan cannot shorten or invent a road. With ``origin_id`` and
    ``destination_id``, the plan must start and end at those nodes.
    """

    return _replay(
        plan,
        vehicle=vehicle,
        stations=stations,
        soc_step_pct=soc_step_pct,
        consumption_model=consumption_model,
        road_network=road_network,
        origin_id=origin_id,
        destination_id=destination_id,
        raise_on_reserve_violation=False,
    )


def _replay(
    plan: RoutePlan,
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    soc_step_pct: int,
    consumption_model: ConsumptionModel,
    road_network: RoadNetwork | None,
    origin_id: str | None,
    destination_id: str | None,
    raise_on_reserve_violation: bool,
) -> Replay:
    validation.soc_step(soc_step_pct)
    grid = SocGrid.for_vehicle(vehicle, soc_step_pct)
    energy_bucket = grid.initial
    if origin_id is not None and plan.node_ids[0] != origin_id:
        raise PlanVerificationError("plan does not start at the requested origin")
    if destination_id is not None and plan.node_ids[-1] != destination_id:
        raise PlanVerificationError("plan does not end at the requested destination")
    stop_index = 0
    violations = 0
    events = [ReplayEvent("start", plan.node_ids[0], energy_bucket * soc_step_pct)]

    for leg_index, leg in enumerate(plan.legs):
        current_node = plan.node_ids[leg_index]
        if stop_index < len(plan.charging_stops):
            stop = plan.charging_stops[stop_index]
            if stop.node_index == leg_index:
                if stop.station_id != current_node:
                    raise PlanVerificationError("charging stop is not at its plan node")
                station = stations.get(current_node)
                if station is None or station.id != current_node:
                    raise PlanVerificationError("charging stop has no matching station")
                if station.connector_type not in vehicle.supported_dc_connectors:
                    raise PlanVerificationError("charging stop connector is incompatible")
                arrival_soc = energy_bucket * soc_step_pct
                _close(stop.arrival_soc_pct, arrival_soc, field="charging arrival SOC")
                departure_bucket = round(stop.departure_soc_pct / soc_step_pct)
                if not energy_bucket < departure_bucket <= grid.maximum:
                    raise PlanVerificationError("charging departure bucket is invalid")
                _close(
                    stop.departure_soc_pct,
                    departure_bucket * soc_step_pct,
                    field="charging departure SOC",
                )
                _close(
                    stop.energy_added_kwh,
                    (departure_bucket - energy_bucket) * grid.bucket_kwh,
                    field="charging energy",
                )
                expected_minutes = charging_minutes(
                    vehicle, station.max_power_kw, arrival_soc, departure_bucket * soc_step_pct
                )
                _close(stop.charging_minutes, expected_minutes, field="charging minutes")
                energy_bucket = departure_bucket
                stop_index += 1
                events.append(ReplayEvent("charge", current_node, energy_bucket * soc_step_pct))

        if (leg.origin_id, leg.destination_id) != (
            current_node,
            plan.node_ids[leg_index + 1],
        ):
            raise PlanVerificationError("road leg does not match plan node order")
        if road_network is not None and not _offered_by(road_network, leg):
            raise PlanVerificationError("road leg is not offered by the road network")
        net, peak = leg_buckets(consumption_model.leg_energy(leg, vehicle), grid.bucket_kwh)
        lowest = energy_bucket - peak
        energy_bucket = min(grid.maximum, energy_bucket - net)
        if min(lowest, energy_bucket) < grid.reserve:
            if raise_on_reserve_violation:
                raise PlanVerificationError("road leg violates reserve SOC")
            violations += 1
        events.append(ReplayEvent("arrive", leg.destination_id, energy_bucket * soc_step_pct))

    if stop_index != len(plan.charging_stops):
        raise PlanVerificationError("charging stops are not in drive order")
    _close(plan.arrival_soc_pct, energy_bucket * soc_step_pct, field="arrival SOC")
    return Replay(events=tuple(events), reserve_violations=violations)
