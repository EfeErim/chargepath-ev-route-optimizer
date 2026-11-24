"""Energy-aware shortest-path search over road and charging transitions."""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass
from enum import StrEnum

from chargepath import validation
from chargepath.energy import (
    DEFAULT_CONSUMPTION,
    ConsumptionModel,
    LegBucketCache,
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

State = tuple[str, int]
Priority = tuple[float, float, float, int]
# The best known way to reach a state: its priority, total minutes, distance and charging sessions.
# The priority is stored so that each candidate is rounded once.
Label = tuple[Priority, float, float, int]

# Accumulated float sums that differ only by rounding noise are treated as ties, so the documented
# tie-breaks decide between them instead of the last bits of the arithmetic.
_PRIORITY_DECIMALS = 9


class RoutePreference(StrEnum):
    """Lexicographic objective used by the exact state-space search."""

    FASTEST = "fastest"
    SHORTEST_DISTANCE = "shortest_distance"
    FEWEST_CHARGING_STOPS = "fewest_charging_stops"


@dataclass(frozen=True, slots=True)
class _DriveAction:
    leg: RoadLeg


@dataclass(frozen=True, slots=True)
class _ChargeAction:
    station_id: str
    from_bucket: int
    to_bucket: int
    minutes: float


Action = _DriveAction | _ChargeAction


class EVRouteOptimizer:
    """Minimize modeled driving plus charging time with a reserve constraint."""

    def __init__(
        self,
        *,
        preference: RoutePreference | str = RoutePreference.FASTEST,
        soc_step_pct: int = 5,
        consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    ) -> None:
        validation.soc_step(soc_step_pct)
        self.preference = RoutePreference(preference)
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
        return self.optimize_with_cache(
            origin_id=origin_id,
            destination_id=destination_id,
            vehicle=vehicle,
            stations=stations,
            road_network=road_network,
            required_buckets_cache={},
        )

    def optimize_with_cache(
        self,
        *,
        origin_id: str,
        destination_id: str,
        vehicle: VehicleProfile,
        stations: dict[str, Station],
        road_network: RoadNetwork,
        required_buckets_cache: LegBucketCache,
    ) -> RoutePlan:
        validation.trip_endpoints(origin_id, destination_id)
        grid = SocGrid.for_vehicle(vehicle, self.soc_step_pct)
        if origin_id == destination_id:
            return RoutePlan(
                node_ids=(origin_id,),
                legs=(),
                charging_stops=(),
                arrival_soc_pct=grid.soc_pct(grid.initial),
            )

        leg_lookup = leg_bucket_lookup(
            required_buckets_cache,
            vehicle=vehicle,
            model=self.consumption_model,
            bucket_kwh=grid.bucket_kwh,
        )
        # Each node's legs with their (net, peak) bucket counts, built when the node is first
        # expanded, and charge minutes per (station power, from bucket, to bucket).
        outgoing: dict[str, list[tuple[RoadLeg, LegBuckets]]] = {}
        charge_cache: dict[tuple[float, int, int], float] = {}
        priority_of = self._priority
        after_leg = grid.after_leg
        maximum, step = grid.maximum, self.soc_step_pct

        start: State = (origin_id, grid.initial)
        start_priority = priority_of(0.0, 0.0, 0, grid.initial)
        labels: dict[State, Label] = {start: (start_priority, 0.0, 0.0, 0)}
        predecessors: dict[State, tuple[State, Action]] = {}
        queue: list[tuple[Priority, int, State]] = [(start_priority, 0, start)]
        sequence = itertools.count(1)
        destination_state: State | None = None
        # Highest bucket already settled at each node. A state settled earlier has a priority no
        # worse than any later one, and a higher bucket can replay every continuation of a lower
        # one at no greater cost, so a lower-bucket state at the same node is dominated.
        settled_bucket: dict[str, int] = {}

        while queue:
            current_priority, _, current = heapq.heappop(queue)
            priority, minutes, distance_km, sessions = labels[current]
            if current_priority != priority:
                continue
            node_id, energy_bucket = current
            if energy_bucket < settled_bucket.get(node_id, -1):
                continue
            settled_bucket[node_id] = energy_bucket
            if node_id == destination_id:
                destination_state = current
                break

            legs = outgoing.get(node_id)
            if legs is None:
                legs = [
                    (
                        leg,
                        buckets_for_leg(
                            leg_lookup,
                            leg,
                            vehicle=vehicle,
                            model=self.consumption_model,
                            bucket_kwh=grid.bucket_kwh,
                        ),
                    )
                    for leg in road_network.neighbors(node_id)
                ]
                outgoing[node_id] = legs
            for leg, buckets in legs:
                remaining_bucket = after_leg(energy_bucket, buckets)
                if remaining_bucket is None:
                    continue
                next_minutes = minutes + leg.duration_minutes
                next_distance = distance_km + leg.distance_km
                next_priority = priority_of(next_minutes, next_distance, sessions, remaining_bucket)
                next_state = (leg.destination_id, remaining_bucket)
                existing = labels.get(next_state)
                if existing is not None and next_priority >= existing[0]:
                    continue
                labels[next_state] = (next_priority, next_minutes, next_distance, sessions)
                predecessors[next_state] = (current, _DriveAction(leg))
                heapq.heappush(queue, (next_priority, next(sequence), next_state))

            station = stations.get(node_id)
            if station is None or energy_bucket >= maximum:
                continue
            if station.connector_type not in vehicle.supported_dc_connectors:
                continue
            power_kw = station.max_power_kw
            next_sessions = sessions + 1
            for target_bucket in range(energy_bucket + 1, maximum + 1):
                key = (power_kw, energy_bucket, target_bucket)
                duration = charge_cache.get(key)
                if duration is None:
                    duration = charging_minutes(
                        vehicle, power_kw, energy_bucket * step, target_bucket * step
                    )
                    charge_cache[key] = duration
                next_minutes = minutes + duration
                next_priority = priority_of(next_minutes, distance_km, next_sessions, target_bucket)
                next_state = (node_id, target_bucket)
                existing = labels.get(next_state)
                if existing is not None and next_priority >= existing[0]:
                    continue
                labels[next_state] = (next_priority, next_minutes, distance_km, next_sessions)
                predecessors[next_state] = (
                    current,
                    _ChargeAction(node_id, energy_bucket, target_bucket, duration),
                )
                heapq.heappush(queue, (next_priority, next(sequence), next_state))

        if destination_state is None:
            reason = f"no energy-feasible route from {origin_id!r} to {destination_id!r}"
            if grid.initial < grid.reserve:
                reason += (
                    f": on the {grid.step_pct}% grid the starting charge rounds down to "
                    f"{grid.soc_pct(grid.initial):g}%, below the {grid.soc_pct(grid.reserve):g}% "
                    "reserve"
                )
            raise NoFeasibleRouteError(reason)
        return self._reconstruct(
            start=start,
            destination=destination_state,
            predecessors=predecessors,
            vehicle=vehicle,
        )

    def _priority(
        self, minutes: float, distance_km: float, sessions: int, energy_bucket: int
    ) -> Priority:
        """Lexicographic priority; the last term prefers the higher battery level on full ties."""

        rounded_minutes = round(minutes, _PRIORITY_DECIMALS)
        rounded_distance = round(distance_km, _PRIORITY_DECIMALS)
        if self.preference is RoutePreference.FASTEST:
            return (rounded_minutes, float(sessions), rounded_distance, -energy_bucket)
        if self.preference is RoutePreference.SHORTEST_DISTANCE:
            return (rounded_distance, rounded_minutes, float(sessions), -energy_bucket)
        return (float(sessions), rounded_minutes, rounded_distance, -energy_bucket)

    def _reconstruct(
        self,
        *,
        start: State,
        destination: State,
        predecessors: dict[State, tuple[State, Action]],
        vehicle: VehicleProfile,
    ) -> RoutePlan:
        actions: list[Action] = []
        cursor = destination
        while cursor != start:
            previous, action = predecessors[cursor]
            actions.append(action)
            cursor = previous
        actions.reverse()

        node_ids = [start[0]]
        legs: list[RoadLeg] = []
        stops: list[ChargingStop] = []
        for action in actions:
            if isinstance(action, _DriveAction):
                legs.append(action.leg)
                node_ids.append(action.leg.destination_id)
                continue
            from_soc = action.from_bucket * self.soc_step_pct
            to_soc = action.to_bucket * self.soc_step_pct
            stops.append(
                ChargingStop(
                    station_id=action.station_id,
                    node_index=len(node_ids) - 1,
                    arrival_soc_pct=float(from_soc),
                    departure_soc_pct=float(to_soc),
                    energy_added_kwh=(to_soc - from_soc) * vehicle.usable_battery_kwh / 100,
                    charging_minutes=action.minutes,
                )
            )

        return RoutePlan(
            node_ids=tuple(node_ids),
            legs=tuple(legs),
            charging_stops=tuple(stops),
            arrival_soc_pct=float(destination[1] * self.soc_step_pct),
        )
