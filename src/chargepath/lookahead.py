"""One-step lookahead charging heuristic, a stronger competitor than the fixed-80 greedy rule.

At each node the heuristic picks the next node and how much to charge here by minimizing the time it
spends now plus an optimistic estimate of what is left: the remaining shortest driving time and the
energy still missing on arrival, charged at the next station's power. It commits to that choice and
never backtracks, so it can fail where the exact search succeeds.
"""

from __future__ import annotations

import math

from chargepath import validation
from chargepath.baselines import ShortestDrivingTimeBaseline
from chargepath.energy import (
    DEFAULT_CONSUMPTION,
    SESSION_SETUP_MINUTES,
    ConsumptionModel,
    LegBuckets,
    SocGrid,
    buckets_for_leg,
    charging_minutes,
    leg_bucket_lookup,
)
from chargepath.models import (
    ChargingStop,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
)
from chargepath.providers.base import RoadNetwork

_SCORE_DECIMALS = 9


class LookaheadGreedyOptimizer:
    """Choose each next stop and charge amount by a one-step lookahead time estimate."""

    def __init__(
        self,
        *,
        soc_step_pct: int = 5,
        consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    ) -> None:
        validation.soc_step(soc_step_pct)
        self.soc_step_pct = soc_step_pct
        self.consumption_model = consumption_model

    def optimize(
        self,
        *,
        origin_id: str,
        destination_id: str,
        vehicle: VehicleProfile,
        stations: dict[str, Station],
        road_network: RoadNetwork,
    ) -> RoutePlan:
        validation.trip_endpoints(origin_id, destination_id)
        step = self.soc_step_pct
        grid = SocGrid.for_vehicle(vehicle, step)
        energy_bucket = grid.initial
        if origin_id == destination_id:
            return RoutePlan(
                node_ids=(origin_id,),
                legs=(),
                charging_stops=(),
                arrival_soc_pct=grid.soc_pct(energy_bucket),
            )

        leg_lookup = leg_bucket_lookup(
            {}, vehicle=vehicle, model=self.consumption_model, bucket_kwh=grid.bucket_kwh
        )

        def buckets(leg: RoadLeg) -> LegBuckets:
            return buckets_for_leg(
                leg_lookup,
                leg,
                vehicle=vehicle,
                model=self.consumption_model,
                bucket_kwh=grid.bucket_kwh,
            )

        remaining_paths: dict[str, tuple[RoadLeg, ...] | None] = {}

        def remaining_path(node_id: str) -> tuple[RoadLeg, ...] | None:
            if node_id not in remaining_paths:
                remaining_paths[node_id] = ShortestDrivingTimeBaseline.shortest_path(
                    node_id, destination_id, road_network
                )
            return remaining_paths[node_id]

        def remaining_minutes(node_id: str) -> float:
            path = remaining_path(node_id)
            return math.inf if path is None else sum(leg.duration_minutes for leg in path)

        def bucket_needed_to_finish(node_id: str) -> int | None:
            """Smallest bucket that drives the remaining shortest path without charging."""

            path = remaining_path(node_id)
            if path is None:
                return None
            needed = grid.reserve
            for leg in reversed(path):
                net, peak = buckets(leg)
                needed = max(needed + net, grid.reserve + peak)
            return needed

        def charge_minutes(station: Station, from_bucket: int, to_bucket: int) -> float:
            return charging_minutes(
                vehicle, station.max_power_kw, from_bucket * step, to_bucket * step
            )

        def later_charging_estimate(station: Station, missing_buckets: int) -> float:
            if missing_buckets <= 0:
                return 0.0
            power_kw = min(station.max_power_kw, vehicle.max_dc_power_kw)
            return SESSION_SETUP_MINUTES + 60 * missing_buckets * grid.bucket_kwh / power_kw

        def usable(station: Station | None) -> bool:
            return station is not None and station.connector_type in vehicle.supported_dc_connectors

        node_ids = [origin_id]
        legs: list[RoadLeg] = []
        stops: list[ChargingStop] = []
        visited = {origin_id}
        current = origin_id

        while current != destination_id:
            here = stations.get(current)
            highest_departure = grid.maximum if usable(here) else energy_bucket
            current_remaining = remaining_minutes(current)

            best: tuple[tuple[float, int, str], RoadLeg, int] | None = None
            for leg in road_network.neighbors(current):
                next_id = leg.destination_id
                next_station = stations.get(next_id)
                if next_id != destination_id and (next_id in visited or not usable(next_station)):
                    continue
                if not remaining_minutes(next_id) < current_remaining:
                    continue
                needed_after = 0 if next_id == destination_id else bucket_needed_to_finish(next_id)
                if needed_after is None:
                    continue
                leg_buckets = buckets(leg)
                for departure in range(energy_bucket, highest_departure + 1):
                    arrival = grid.after_leg(departure, leg_buckets)
                    if arrival is None:
                        continue
                    if next_id == destination_id:
                        later = 0.0
                    else:
                        later = later_charging_estimate(
                            _require(next_station), needed_after - arrival
                        )
                    charge_now = (
                        0.0
                        if departure == energy_bucket
                        else charge_minutes(_require(here), energy_bucket, departure)
                    )
                    score = charge_now + leg.duration_minutes + remaining_minutes(next_id) + later
                    key = (round(score, _SCORE_DECIMALS), departure, next_id)
                    if best is None or key < best[0]:
                        best = (key, leg, departure)

            if best is None:
                raise NoFeasibleRouteError(
                    f"lookahead heuristic found no progressing move from {current!r}"
                )
            _, chosen_leg, departure = best
            if departure > energy_bucket:
                station = _require(here)
                stops.append(
                    ChargingStop(
                        station_id=station.id,
                        node_index=len(node_ids) - 1,
                        arrival_soc_pct=float(energy_bucket * step),
                        departure_soc_pct=float(departure * step),
                        energy_added_kwh=(departure - energy_bucket) * grid.bucket_kwh,
                        charging_minutes=charge_minutes(station, energy_bucket, departure),
                    )
                )
                energy_bucket = departure
            arrival = grid.after_leg(energy_bucket, buckets(chosen_leg))
            if arrival is None:
                raise AssertionError("the chosen leg was scored as drivable")
            energy_bucket = arrival
            current = chosen_leg.destination_id
            legs.append(chosen_leg)
            node_ids.append(current)
            visited.add(current)

        return RoutePlan(
            node_ids=tuple(node_ids),
            legs=tuple(legs),
            charging_stops=tuple(stops),
            arrival_soc_pct=grid.soc_pct(energy_bucket),
        )


def _require(station: Station | None) -> Station:
    if station is None:
        raise AssertionError("charging requires a station")
    return station
