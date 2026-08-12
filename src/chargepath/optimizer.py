"""Energy-aware shortest-path search over road and charging transitions."""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from enum import StrEnum

from chargepath.energy import (
    charging_minutes,
    energy_for_distance,
    required_energy_buckets,
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
Priority = tuple[float, float, float]
LegEnergyKey = tuple[RoadLeg, float, float, float]


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
        taper_start_pct: float = 80.0,
        taper_factor: float = 0.45,
        session_setup_minutes: float = 2.0,
    ) -> None:
        if (
            isinstance(soc_step_pct, bool)
            or not isinstance(soc_step_pct, int)
            or soc_step_pct <= 0
            or 100 % soc_step_pct != 0
        ):
            raise ValueError("soc_step_pct must be a positive divisor of 100")
        if (
            isinstance(taper_start_pct, bool)
            or not isinstance(taper_start_pct, (int, float))
            or not math.isfinite(taper_start_pct)
            or not 0 < taper_start_pct < 100
        ):
            raise ValueError("taper_start_pct must be between 0 and 100")
        if (
            isinstance(taper_factor, bool)
            or not isinstance(taper_factor, (int, float))
            or not math.isfinite(taper_factor)
            or not 0 < taper_factor <= 1
        ):
            raise ValueError("taper_factor must be between 0 and 1")
        if (
            isinstance(session_setup_minutes, bool)
            or not isinstance(session_setup_minutes, (int, float))
            or not math.isfinite(session_setup_minutes)
            or session_setup_minutes < 0
        ):
            raise ValueError("session_setup_minutes must not be negative")
        self.preference = RoutePreference(preference)
        self.soc_step_pct = soc_step_pct
        self.taper_start_pct = taper_start_pct
        self.taper_factor = taper_factor
        self.session_setup_minutes = session_setup_minutes

    def optimize(
        self,
        *,
        origin_id: str,
        destination_id: str,
        vehicle: VehicleProfile,
        stations: dict[str, Station],
        road_network: RoadNetwork,
    ) -> RoutePlan:
        return self._optimize_with_cache(
            origin_id=origin_id,
            destination_id=destination_id,
            vehicle=vehicle,
            stations=stations,
            road_network=road_network,
            required_buckets_cache={},
        )

    def _optimize_with_cache(
        self,
        *,
        origin_id: str,
        destination_id: str,
        vehicle: VehicleProfile,
        stations: dict[str, Station],
        road_network: RoadNetwork,
        required_buckets_cache: dict[LegEnergyKey, int],
    ) -> RoutePlan:
        if (
            not isinstance(origin_id, str)
            or not isinstance(destination_id, str)
            or not origin_id.strip()
            or not destination_id.strip()
        ):
            raise ValueError("origin_id and destination_id must not be empty")
        if origin_id == destination_id:
            initial_bucket = math.floor(vehicle.initial_soc_pct / self.soc_step_pct)
            arrival_soc = initial_bucket * self.soc_step_pct
            return RoutePlan(
                node_ids=(origin_id,),
                legs=(),
                charging_stops=(),
                total_distance_km=0.0,
                driving_minutes=0.0,
                charging_minutes=0.0,
                arrival_soc_pct=float(arrival_soc),
                total_minutes=0.0,
            )

        maximum_bucket = 100 // self.soc_step_pct
        initial_bucket = math.floor(vehicle.initial_soc_pct / self.soc_step_pct)
        reserve_bucket = math.ceil(vehicle.reserve_soc_pct / self.soc_step_pct)
        bucket_kwh = vehicle.usable_battery_kwh * self.soc_step_pct / 100
        leg_energy_cache = required_buckets_cache
        start: State = (origin_id, initial_bucket)

        best_metrics: dict[State, _SearchMetrics] = {start: _SearchMetrics()}
        predecessors: dict[State, tuple[State, Action]] = {}
        queue: list[tuple[Priority, int, State]] = []
        sequence = itertools.count()
        heapq.heappush(queue, (self._priority(_SearchMetrics()), next(sequence), start))
        destination_state: State | None = None

        while queue:
            current_priority, _, current = heapq.heappop(queue)
            current_metrics = best_metrics[current]
            if current_priority != self._priority(current_metrics):
                continue
            node_id, energy_bucket = current
            if node_id == destination_id:
                destination_state = current
                break

            for leg in road_network.neighbors(node_id):
                if leg.origin_id != node_id:
                    raise ValueError("road-network neighbor leg must start at the requested node")
                energy_key = (
                    leg,
                    vehicle.consumption_kwh_per_100km,
                    vehicle.energy_safety_factor,
                    bucket_kwh,
                )
                required_buckets = leg_energy_cache.get(energy_key)
                if required_buckets is None:
                    required_kwh = energy_for_distance(
                        leg.distance_km,
                        vehicle.consumption_kwh_per_100km,
                        vehicle.energy_safety_factor,
                    )
                    required_buckets = required_energy_buckets(required_kwh, bucket_kwh)
                    leg_energy_cache[energy_key] = required_buckets
                remaining_bucket = energy_bucket - required_buckets
                if remaining_bucket < reserve_bucket:
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
            if station is None:
                continue
            if station.id != node_id:
                raise ValueError("station mapping key must match station.id")
            if energy_bucket >= maximum_bucket:
                continue
            if station.connector_type not in vehicle.supported_dc_connectors:
                continue
            for target_bucket in range(energy_bucket + 1, maximum_bucket + 1):
                from_soc = energy_bucket * self.soc_step_pct
                to_soc = target_bucket * self.soc_step_pct
                duration = charging_minutes(
                    vehicle,
                    station.max_power_kw,
                    from_soc,
                    to_soc,
                    taper_start_pct=self.taper_start_pct,
                    taper_factor=self.taper_factor,
                    setup_minutes=self.session_setup_minutes,
                )
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
        next_priority = self._priority(next_metrics)
        existing = best_metrics.get(next_state)
        if existing is not None and next_priority >= self._priority(existing):
            return
        best_metrics[next_state] = next_metrics
        predecessors[next_state] = (current, action)
        heapq.heappush(queue, (next_priority, next(sequence), next_state))

    def _priority(self, metrics: _SearchMetrics) -> Priority:
        if self.preference is RoutePreference.FASTEST:
            return (
                metrics.total_minutes,
                float(metrics.charging_stops),
                metrics.distance_km,
            )
        if self.preference is RoutePreference.SHORTEST_DISTANCE:
            return (
                metrics.distance_km,
                metrics.total_minutes,
                float(metrics.charging_stops),
            )
        return (
            float(metrics.charging_stops),
            metrics.total_minutes,
            metrics.distance_km,
        )

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
                    arrival_soc_pct=float(from_soc),
                    departure_soc_pct=float(to_soc),
                    energy_added_kwh=(to_soc - from_soc) * vehicle.usable_battery_kwh / 100,
                    charging_minutes=action.minutes,
                )
            )

        total_distance = sum(leg.distance_km for leg in legs)
        driving_time = sum(leg.duration_minutes for leg in legs)
        charging_time = sum(stop.charging_minutes for stop in stops)
        return RoutePlan(
            node_ids=tuple(node_ids),
            legs=tuple(legs),
            charging_stops=tuple(stops),
            total_distance_km=total_distance,
            driving_minutes=driving_time,
            charging_minutes=charging_time,
            arrival_soc_pct=float(destination[1] * self.soc_step_pct),
            total_minutes=driving_time + charging_time,
        )
