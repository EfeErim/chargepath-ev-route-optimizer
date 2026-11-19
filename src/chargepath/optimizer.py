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

# Accumulated float sums that differ only by rounding noise are treated as ties, so the documented
# tie-breaks decide between them instead of the last bits of the arithmetic.
_PRIORITY_DECIMALS = 9


class RoutePreference(StrEnum):
    """Lexicographic objective used by the exact state-space search."""

    FASTEST = "fastest"
    SHORTEST_DISTANCE = "shortest_distance"
    FEWEST_CHARGING_STOPS = "fewest_charging_stops"


@dataclass(frozen=True, slots=True)
class _SearchMetrics:
    total_minutes: float = 0.0
    distance_km: float = 0.0
    charging_stops: int = 0

    def drive(self, leg: RoadLeg) -> _SearchMetrics:
        return _SearchMetrics(
            total_minutes=self.total_minutes + leg.duration_minutes,
            distance_km=self.distance_km + leg.distance_km,
            charging_stops=self.charging_stops,
        )

    def charge(self, minutes: float) -> _SearchMetrics:
        return _SearchMetrics(
            total_minutes=self.total_minutes + minutes,
            distance_km=self.distance_km,
            charging_stops=self.charging_stops + 1,
        )


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
            return RoutePlan.stationary(origin_id, arrival_soc_pct=grid.soc_pct(grid.initial))

        leg_lookup = leg_bucket_lookup(
            required_buckets_cache,
            vehicle=vehicle,
            model=self.consumption_model,
            bucket_kwh=grid.bucket_kwh,
        )
        start: State = (origin_id, grid.initial)

        best_metrics: dict[State, _SearchMetrics] = {start: _SearchMetrics()}
        predecessors: dict[State, tuple[State, Action]] = {}
        queue: list[tuple[Priority, int, State]] = []
        sequence = itertools.count()
        heapq.heappush(
            queue, (self._priority(_SearchMetrics(), grid.initial), next(sequence), start)
        )
        destination_state: State | None = None
        # Highest bucket already settled at each node. A state settled earlier has a priority no
        # worse than any later one, and a higher bucket can replay every continuation of a lower
        # one at no greater cost, so a lower-bucket state at the same node is dominated.
        settled_bucket: dict[str, int] = {}

        while queue:
            current_priority, _, current = heapq.heappop(queue)
            current_metrics = best_metrics[current]
            node_id, energy_bucket = current
            if current_priority != self._priority(current_metrics, energy_bucket):
                continue
            if energy_bucket < settled_bucket.get(node_id, -1):
                continue
            settled_bucket[node_id] = energy_bucket
            if node_id == destination_id:
                destination_state = current
                break

            for leg in road_network.neighbors(node_id):
                remaining_bucket = grid.after_leg(
                    energy_bucket,
                    buckets_for_leg(
                        leg_lookup,
                        leg,
                        vehicle=vehicle,
                        model=self.consumption_model,
                        bucket_kwh=grid.bucket_kwh,
                    ),
                )
                if remaining_bucket is None:
                    continue
                next_state = (leg.destination_id, remaining_bucket)
                self._relax(
                    current=current,
                    next_state=next_state,
                    action=_DriveAction(leg),
                    next_metrics=current_metrics.drive(leg),
                    best_metrics=best_metrics,
                    predecessors=predecessors,
                    queue=queue,
                    sequence=sequence,
                )

            station = stations.get(node_id)
            if station is None or energy_bucket >= grid.maximum:
                continue
            if station.connector_type not in vehicle.supported_dc_connectors:
                continue
            for target_bucket in range(energy_bucket + 1, grid.maximum + 1):
                from_soc = energy_bucket * self.soc_step_pct
                to_soc = target_bucket * self.soc_step_pct
                duration = charging_minutes(vehicle, station.max_power_kw, from_soc, to_soc)
                next_state = (node_id, target_bucket)
                self._relax(
                    current=current,
                    next_state=next_state,
                    action=_ChargeAction(node_id, energy_bucket, target_bucket, duration),
                    next_metrics=current_metrics.charge(duration),
                    best_metrics=best_metrics,
                    predecessors=predecessors,
                    queue=queue,
                    sequence=sequence,
                )

        if destination_state is None:
            raise NoFeasibleRouteError(
                f"no energy-feasible route from {origin_id!r} to {destination_id!r}"
            )
        return self._reconstruct(
            start=start,
            destination=destination_state,
            predecessors=predecessors,
            vehicle=vehicle,
        )

    def _relax(
        self,
        *,
        current: State,
        next_state: State,
        action: Action,
        next_metrics: _SearchMetrics,
        best_metrics: dict[State, _SearchMetrics],
        predecessors: dict[State, tuple[State, Action]],
        queue: list[tuple[Priority, int, State]],
        sequence: itertools.count[int],
    ) -> None:
        next_priority = self._priority(next_metrics, next_state[1])
        existing = best_metrics.get(next_state)
        if existing is not None and next_priority >= self._priority(existing, next_state[1]):
            return
        best_metrics[next_state] = next_metrics
        predecessors[next_state] = (current, action)
        heapq.heappush(queue, (next_priority, next(sequence), next_state))

    def _priority(self, metrics: _SearchMetrics, energy_bucket: int) -> Priority:
        """Lexicographic priority; the last term prefers the higher battery level on full ties."""

        minutes = round(metrics.total_minutes, _PRIORITY_DECIMALS)
        distance = round(metrics.distance_km, _PRIORITY_DECIMALS)
        sessions = float(metrics.charging_stops)
        if self.preference is RoutePreference.FASTEST:
            return (minutes, sessions, distance, -energy_bucket)
        if self.preference is RoutePreference.SHORTEST_DISTANCE:
            return (distance, minutes, sessions, -energy_bucket)
        return (sessions, minutes, distance, -energy_bucket)

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

        return RoutePlan.assemble(
            node_ids=tuple(node_ids),
            legs=tuple(legs),
            charging_stops=tuple(stops),
            arrival_soc_pct=float(destination[1] * self.soc_step_pct),
        )
