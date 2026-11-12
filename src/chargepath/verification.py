"""Independent replay checks for optimizer output."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from chargepath import validation
from chargepath.energy import (
    DEFAULT_CONSUMPTION,
    ConsumptionModel,
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
    taper_start_pct: float = 80.0,
    taper_factor: float = 0.45,
    session_setup_minutes: float = 2.0,
    consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    road_network: RoadNetwork | None = None,
    origin_id: str | None = None,
    destination_id: str | None = None,
) -> tuple[ReplayEvent, ...]:
    """Replay charge and drive actions without relying on optimizer predecessor state.

    The replay shares the energy and charging-time functions with the optimizer, so it catches
    search and reconstruction bugs, not model bugs. ``tests/test_properties.py`` checks the model
    against a separate exact-arithmetic reference.

    With ``road_network``, every driven leg must be one that network offers, with the same
    distance and duration, so a plan cannot shorten or invent a road. With ``origin_id`` and
    ``destination_id``, the plan must start and end at those nodes.
    """

    validation.soc_step(soc_step_pct)
    validation.charge_model(
        taper_start_pct=taper_start_pct,
        taper_factor=taper_factor,
        session_setup_minutes=session_setup_minutes,
    )
    bucket_kwh = vehicle.usable_battery_kwh * soc_step_pct / 100
    energy_bucket = math.floor(vehicle.initial_soc_pct / soc_step_pct)
    reserve_bucket = math.ceil(vehicle.reserve_soc_pct / soc_step_pct)
    maximum_bucket = 100 // soc_step_pct
    if origin_id is not None and plan.node_ids[0] != origin_id:
        raise PlanVerificationError("plan does not start at the requested origin")
    if destination_id is not None and plan.node_ids[-1] != destination_id:
        raise PlanVerificationError("plan does not end at the requested destination")
    stop_index = 0
    events = [ReplayEvent("start", plan.node_ids[0], energy_bucket * soc_step_pct)]

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
                events.append(ReplayEvent("charge", current_node, energy_bucket * soc_step_pct))

        if (leg.origin_id, leg.destination_id) != (
            current_node,
            plan.node_ids[leg_index + 1],
        ):
            raise PlanVerificationError("road leg does not match plan node order")
        if road_network is not None and not _offered_by(road_network, leg):
            raise PlanVerificationError("road leg is not offered by the road network")
        net, peak = leg_buckets(consumption_model.leg_energy(leg, vehicle), bucket_kwh)
        if energy_bucket - peak < reserve_bucket:
            raise PlanVerificationError("road leg violates reserve SOC")
        energy_bucket = min(maximum_bucket, energy_bucket - net)
        if energy_bucket < reserve_bucket:
            raise PlanVerificationError("road leg violates reserve SOC")
        events.append(ReplayEvent("arrive", leg.destination_id, energy_bucket * soc_step_pct))

    if stop_index != len(plan.charging_stops):
        raise PlanVerificationError("charging stops are not in drive order")
    _close(plan.arrival_soc_pct, energy_bucket * soc_step_pct, field="arrival SOC")
    return tuple(events)
