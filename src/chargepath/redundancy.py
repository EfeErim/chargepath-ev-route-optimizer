"""Structural redundancy of a plan's charging stops.

For each stop this answers two questions on the same candidate graph and model:

- how many other compatible chargers could be driven to instead, directly from where the plan was
  before that stop, while keeping the reserve and still finishing the trip; and
- how much slower the best trip is when that stop's charger is removed before planning.

These are properties of the graph and the static station list only. They carry no information about
occupancy, outages, or reliability, and a count of alternatives is not a probability of success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from chargepath.energy import (
    DEFAULT_CONSUMPTION,
    ConsumptionModel,
    drive_bucket,
    leg_buckets,
)
from chargepath.models import NoFeasibleRouteError, RoutePlan, Station, VehicleProfile
from chargepath.optimizer import EVRouteOptimizer
from chargepath.providers.base import RoadNetwork

DEFAULT_ALTERNATIVE_LIMIT = 12


@dataclass(frozen=True, slots=True)
class StopRedundancy:
    station_id: str
    # Compatible chargers, other than this one, reachable directly from the previous plan node with
    # the reserve kept and from which the destination stays reachable.
    alternative_count: int
    # How many candidate chargers were examined (nearest by driving time, up to the limit).
    alternatives_checked: int
    # Added minutes when this charger is excluded before planning; None when that is infeasible.
    without_stop_added_minutes: float | None


def stop_redundancy(
    plan: RoutePlan,
    *,
    vehicle: VehicleProfile,
    stations: dict[str, Station],
    road_network: RoadNetwork,
    soc_step_pct: int = 5,
    consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    alternative_limit: int = DEFAULT_ALTERNATIVE_LIMIT,
) -> tuple[StopRedundancy, ...]:
    """Measure, per charging stop of an exact fastest plan, the structural fallback options."""

    if isinstance(alternative_limit, bool) or not isinstance(alternative_limit, int):
        raise ValueError("alternative_limit must be an integer")
    if alternative_limit < 0:
        raise ValueError("alternative_limit must not be negative")
    optimizer = EVRouteOptimizer(soc_step_pct=soc_step_pct, consumption_model=consumption_model)
    origin_id, destination_id = plan.node_ids[0], plan.node_ids[-1]
    departure_buckets = _departure_buckets(
        plan, vehicle=vehicle, soc_step_pct=soc_step_pct, model=consumption_model
    )
    reserve_bucket = math.ceil(vehicle.reserve_soc_pct / soc_step_pct)
    maximum_bucket = 100 // soc_step_pct
    bucket_kwh = vehicle.usable_battery_kwh * soc_step_pct / 100

    results: list[StopRedundancy] = []
    for stop in plan.charging_stops:
        stop_index = plan.node_ids.index(stop.station_id)
        previous_node = plan.node_ids[stop_index - 1] if stop_index > 0 else None

        without = {key: value for key, value in stations.items() if key != stop.station_id}
        try:
            fallback = optimizer.optimize(
                origin_id=origin_id,
                destination_id=destination_id,
                vehicle=vehicle,
                stations=without,
                road_network=road_network,
            )
            added: float | None = fallback.total_minutes - plan.total_minutes
        except NoFeasibleRouteError:
            added = None

        alternatives = 0
        checked = 0
        if previous_node is not None:
            candidates = sorted(
                (
                    leg
                    for leg in road_network.neighbors(previous_node)
                    if leg.destination_id != stop.station_id
                    and leg.destination_id in stations
                    and stations[leg.destination_id].connector_type
                    in vehicle.supported_dc_connectors
                ),
                key=lambda leg: (leg.duration_minutes, leg.destination_id),
            )[:alternative_limit]
            for leg in candidates:
                checked += 1
                arrival = drive_bucket(
                    departure_buckets[stop_index - 1],
                    leg_buckets(consumption_model.leg_energy(leg, vehicle), bucket_kwh),
                    reserve_bucket=reserve_bucket,
                    maximum_bucket=maximum_bucket,
                )
                if arrival is None:
                    continue
                try:
                    optimizer.optimize(
                        origin_id=leg.destination_id,
                        destination_id=destination_id,
                        vehicle=replace(vehicle, initial_soc_pct=float(arrival * soc_step_pct)),
                        stations=stations,
                        road_network=road_network,
                    )
                except NoFeasibleRouteError:
                    continue
                alternatives += 1

        results.append(
            StopRedundancy(
                station_id=stop.station_id,
                alternative_count=alternatives,
                alternatives_checked=checked,
                without_stop_added_minutes=added,
            )
        )
    return tuple(results)


def _departure_buckets(
    plan: RoutePlan,
    *,
    vehicle: VehicleProfile,
    soc_step_pct: int,
    model: ConsumptionModel,
) -> list[int]:
    """Bucket held when leaving each plan node, after any charge there."""

    bucket_kwh = vehicle.usable_battery_kwh * soc_step_pct / 100
    maximum_bucket = 100 // soc_step_pct
    stops = {stop.station_id: stop for stop in plan.charging_stops}
    bucket = math.floor(vehicle.initial_soc_pct / soc_step_pct)
    departures: list[int] = []
    for index, node in enumerate(plan.node_ids):
        stop = stops.get(node)
        if stop is not None:
            bucket = round(stop.departure_soc_pct / soc_step_pct)
        departures.append(bucket)
        if index < len(plan.legs):
            net, _ = leg_buckets(model.leg_energy(plan.legs[index], vehicle), bucket_kwh)
            bucket = min(maximum_bucket, bucket - net)
    return departures
