"""Competitive route strategies over the shared deterministic energy model."""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from enum import StrEnum

from chargepath.energy import charging_minutes, energy_for_distance, required_energy_buckets
from chargepath.models import (
    ChargingStop,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
)
from chargepath.optimizer import EVRouteOptimizer, LegEnergyKey, RoutePreference
from chargepath.providers.base import RoadNetwork
from chargepath.verification import verify_route_plan


class RouteStrategy(StrEnum):
    """Stable public identifiers for the route-option algorithms."""

    FASTEST = "fastest"
    SHORTEST_DISTANCE = "shortest_distance"
    FEWEST_CHARGING_STOPS = "fewest_charging_stops"
    GREEDY_FIXED_80 = "greedy_fixed_80"


@dataclass(frozen=True, slots=True)
class RouteOption:
    """One unique actionable plan and every strategy that selected it."""

    strategies: tuple[RouteStrategy, ...]
    plan: RoutePlan

    def __post_init__(self) -> None:
        if not isinstance(self.strategies, tuple) or not self.strategies:
            raise ValueError("route option must name at least one strategy")
        if any(not isinstance(strategy, RouteStrategy) for strategy in self.strategies):
            raise ValueError("route option strategies must be RouteStrategy values")
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("route option strategies must not contain duplicates")
        if not isinstance(self.plan, RoutePlan):
            raise ValueError("route option plan must be a RoutePlan")


@dataclass(frozen=True, slots=True)
class RouteOptionSet:
    """Unique feasible options plus strategies that could not produce a plan."""

    options: tuple[RouteOption, ...]
    unavailable_strategies: tuple[RouteStrategy, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.options, tuple) or not self.options:
            raise ValueError("route option set must contain at least one option")
        if any(not isinstance(option, RouteOption) for option in self.options):
            raise ValueError("route option set options must be RouteOption values")
        if not isinstance(self.unavailable_strategies, tuple) or any(
            not isinstance(strategy, RouteStrategy) for strategy in self.unavailable_strategies
        ):
            raise ValueError("unavailable strategies must be RouteStrategy values")
        available = [strategy for option in self.options for strategy in option.strategies]
        if len(set(available)) != len(available):
            raise ValueError("a route strategy must not appear in multiple options")
        if len(set(self.unavailable_strategies)) != len(self.unavailable_strategies):
            raise ValueError("unavailable strategies must not contain duplicates")
        if set(available) & set(self.unavailable_strategies):
            raise ValueError("available and unavailable route strategies must be disjoint")
        if set(available) | set(self.unavailable_strategies) != set(RouteStrategy):
            raise ValueError("every route strategy must be available or explicitly unavailable")


class GreedyFixedTargetOptimizer:
    """Deterministic reachable-station heuristic with one fixed 80% charge target."""

    def __init__(
        self,
        *,
        soc_step_pct: int = 5,
        target_soc_pct: float = 80.0,
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
            isinstance(target_soc_pct, bool)
            or not isinstance(target_soc_pct, (int, float))
            or not math.isfinite(target_soc_pct)
            or not 0 < target_soc_pct <= 100
        ):
            raise ValueError("target_soc_pct must be between 0 and 100")
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
        self.soc_step_pct = soc_step_pct
        self.target_soc_pct = target_soc_pct
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

        energy_bucket = math.floor(vehicle.initial_soc_pct / self.soc_step_pct)
        if origin_id == destination_id:
            return RoutePlan(
                node_ids=(origin_id,),
                legs=(),
                charging_stops=(),
                total_distance_km=0.0,
                driving_minutes=0.0,
                charging_minutes=0.0,
                arrival_soc_pct=float(energy_bucket * self.soc_step_pct),
                total_minutes=0.0,
            )

        reserve_bucket = math.ceil(vehicle.reserve_soc_pct / self.soc_step_pct)
        bucket_kwh = vehicle.usable_battery_kwh * self.soc_step_pct / 100
        leg_energy_cache = required_buckets_cache
        target_bucket = math.ceil(self.target_soc_pct / self.soc_step_pct)
        maximum_bucket = 100 // self.soc_step_pct
        target_bucket = min(target_bucket, maximum_bucket)

        node_ids = [origin_id]
        legs: list[RoadLeg] = []
        stops: list[ChargingStop] = []
        used_stations: set[str] = set()
        seen_states: set[tuple[str, int, tuple[str, ...]]] = set()
        current_node = origin_id

        while current_node != destination_id:
            state = (current_node, energy_bucket, tuple(sorted(used_stations)))
            if state in seen_states:
                raise NoFeasibleRouteError("greedy fixed-target strategy entered a repeated state")
            seen_states.add(state)

            shortest_path = self._shortest_driving_path(
                current_node,
                destination_id,
                road_network,
            )
            if shortest_path:
                next_leg = shortest_path[0]
                remaining_bucket = self._remaining_bucket(
                    energy_bucket,
                    next_leg,
                    vehicle,
                    bucket_kwh,
                    leg_energy_cache,
                )
                if remaining_bucket >= reserve_bucket:
                    legs.append(next_leg)
                    node_ids.append(next_leg.destination_id)
                    energy_bucket = remaining_bucket
                    current_node = next_leg.destination_id
                    continue

            current_station = stations.get(current_node)
            if (
                current_station is not None
                and current_station.id == current_node
                and current_station.connector_type in vehicle.supported_dc_connectors
                and current_node not in used_stations
                and energy_bucket < target_bucket
            ):
                stops.append(
                    self._charge_to_target(
                        station=current_station,
                        energy_bucket=energy_bucket,
                        target_bucket=target_bucket,
                        bucket_kwh=bucket_kwh,
                        vehicle=vehicle,
                    )
                )
                energy_bucket = target_bucket
                used_stations.add(current_node)
                continue

            candidates: list[tuple[float, float, str, RoadLeg, int]] = []
            for leg in road_network.neighbors(current_node):
                if leg.origin_id != current_node:
                    raise ValueError("road-network neighbor leg must start at the requested node")
                station = stations.get(leg.destination_id)
                if station is None or station.id in used_stations:
                    continue
                if station.id != leg.destination_id:
                    raise ValueError("station mapping key must match station.id")
                if station.connector_type not in vehicle.supported_dc_connectors:
                    continue
                arrival_bucket = self._remaining_bucket(
                    energy_bucket,
                    leg,
                    vehicle,
                    bucket_kwh,
                    leg_energy_cache,
                )
                if arrival_bucket < reserve_bucket or arrival_bucket >= target_bucket:
                    continue
                remaining_path = self._shortest_driving_path(
                    station.id,
                    destination_id,
                    road_network,
                )
                if remaining_path is None:
                    continue
                remaining_duration = sum(item.duration_minutes for item in remaining_path)
                candidates.append(
                    (
                        remaining_duration,
                        leg.duration_minutes,
                        station.id,
                        leg,
                        arrival_bucket,
                    )
                )

            if not candidates:
                raise NoFeasibleRouteError(
                    f"greedy fixed-target strategy found no route from {current_node!r}"
                )
            _, _, station_id, selected_leg, arrival_bucket = min(
                candidates,
                key=lambda item: (item[0], item[1], item[2]),
            )
            legs.append(selected_leg)
            node_ids.append(station_id)
            energy_bucket = arrival_bucket
            current_node = station_id

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
            arrival_soc_pct=float(energy_bucket * self.soc_step_pct),
            total_minutes=driving_time + charging_time,
        )

    def _remaining_bucket(
        self,
        energy_bucket: int,
        leg: RoadLeg,
        vehicle: VehicleProfile,
        bucket_kwh: float,
        required_buckets_cache: dict[LegEnergyKey, int],
    ) -> int:
        energy_key = (
            leg,
            vehicle.consumption_kwh_per_100km,
            vehicle.energy_safety_factor,
            bucket_kwh,
        )
        required_buckets = required_buckets_cache.get(energy_key)
        if required_buckets is None:
            required_kwh = energy_for_distance(
                leg.distance_km,
                vehicle.consumption_kwh_per_100km,
                vehicle.energy_safety_factor,
            )
            required_buckets = required_energy_buckets(required_kwh, bucket_kwh)
            required_buckets_cache[energy_key] = required_buckets
        return energy_bucket - required_buckets

    def _charge_to_target(
        self,
        *,
        station: Station,
        energy_bucket: int,
        target_bucket: int,
        bucket_kwh: float,
        vehicle: VehicleProfile,
    ) -> ChargingStop:
        arrival_soc = energy_bucket * self.soc_step_pct
        departure_soc = target_bucket * self.soc_step_pct
        minutes = charging_minutes(
            vehicle,
            station.max_power_kw,
            arrival_soc,
            departure_soc,
            taper_start_pct=self.taper_start_pct,
            taper_factor=self.taper_factor,
            setup_minutes=self.session_setup_minutes,
        )
        return ChargingStop(
            station_id=station.id,
            arrival_soc_pct=float(arrival_soc),
            departure_soc_pct=float(departure_soc),
            energy_added_kwh=(target_bucket - energy_bucket) * bucket_kwh,
            charging_minutes=minutes,
        )

    @staticmethod
    def _shortest_driving_path(
        origin_id: str,
        destination_id: str,
        road_network: RoadNetwork,
    ) -> tuple[RoadLeg, ...] | None:
        if origin_id == destination_id:
            return ()
        sequence = itertools.count()
        start_nodes = (origin_id,)
        queue: list[tuple[float, tuple[str, ...], int, str, tuple[RoadLeg, ...]]] = [
            (0.0, start_nodes, next(sequence), origin_id, ())
        ]
        best: dict[str, tuple[float, tuple[str, ...]]] = {origin_id: (0.0, start_nodes)}
        while queue:
            duration, node_path, _, node_id, legs = heapq.heappop(queue)
            if (duration, node_path) != best.get(node_id):
                continue
            if node_id == destination_id:
                return legs
            for leg in road_network.neighbors(node_id):
                if leg.origin_id != node_id:
                    raise ValueError("road-network neighbor leg must start at the requested node")
                next_duration = duration + leg.duration_minutes
                next_nodes = (*node_path, leg.destination_id)
                next_best = (next_duration, next_nodes)
                if next_best >= best.get(leg.destination_id, (math.inf, ())):
                    continue
                best[leg.destination_id] = next_best
                heapq.heappush(
                    queue,
                    (
                        next_duration,
                        next_nodes,
                        next(sequence),
                        leg.destination_id,
                        (*legs, leg),
                    ),
                )
        return None


class CompetitiveRoutePlanner:
    """Run exact objective variants and a greedy heuristic, then deduplicate plans."""

    def __init__(
        self,
        *,
        soc_step_pct: int = 5,
        taper_start_pct: float = 80.0,
        taper_factor: float = 0.45,
        session_setup_minutes: float = 2.0,
    ) -> None:
        EVRouteOptimizer(
            soc_step_pct=soc_step_pct,
            taper_start_pct=taper_start_pct,
            taper_factor=taper_factor,
            session_setup_minutes=session_setup_minutes,
        )
        self.soc_step_pct = soc_step_pct
        self.taper_start_pct = taper_start_pct
        self.taper_factor = taper_factor
        self.session_setup_minutes = session_setup_minutes

    def plan(
        self,
        *,
        origin_id: str,
        destination_id: str,
        vehicle: VehicleProfile,
        stations: dict[str, Station],
        road_network: RoadNetwork,
    ) -> RouteOptionSet:
        candidates: list[tuple[RouteStrategy, RoutePlan]] = []
        unavailable: list[RouteStrategy] = []
        required_buckets_cache: dict[LegEnergyKey, int] = {}
        exact_strategies = (
            (RouteStrategy.FASTEST, RoutePreference.FASTEST),
            (RouteStrategy.SHORTEST_DISTANCE, RoutePreference.SHORTEST_DISTANCE),
            (RouteStrategy.FEWEST_CHARGING_STOPS, RoutePreference.FEWEST_CHARGING_STOPS),
        )
        for strategy, preference in exact_strategies:
            optimizer = EVRouteOptimizer(
                preference=preference,
                soc_step_pct=self.soc_step_pct,
                taper_start_pct=self.taper_start_pct,
                taper_factor=self.taper_factor,
                session_setup_minutes=self.session_setup_minutes,
            )
            try:
                plan = optimizer._optimize_with_cache(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    vehicle=vehicle,
                    stations=stations,
                    road_network=road_network,
                    required_buckets_cache=required_buckets_cache,
                )
            except NoFeasibleRouteError:
                unavailable.append(strategy)
                continue
            candidates.append((strategy, plan))

        greedy = GreedyFixedTargetOptimizer(
            soc_step_pct=self.soc_step_pct,
            taper_start_pct=self.taper_start_pct,
            taper_factor=self.taper_factor,
            session_setup_minutes=self.session_setup_minutes,
        )
        try:
            greedy_plan = greedy._optimize_with_cache(
                origin_id=origin_id,
                destination_id=destination_id,
                vehicle=vehicle,
                stations=stations,
                road_network=road_network,
                required_buckets_cache=required_buckets_cache,
            )
        except NoFeasibleRouteError:
            unavailable.append(RouteStrategy.GREEDY_FIXED_80)
        else:
            candidates.append((RouteStrategy.GREEDY_FIXED_80, greedy_plan))

        if not candidates:
            raise NoFeasibleRouteError(
                f"no route-option strategy found a feasible route from {origin_id!r} "
                f"to {destination_id!r}"
            )

        options: list[RouteOption] = []
        option_index: dict[RoutePlan, int] = {}
        for strategy, plan in candidates:
            verify_route_plan(
                plan,
                vehicle=vehicle,
                stations=stations,
                soc_step_pct=self.soc_step_pct,
                taper_start_pct=self.taper_start_pct,
                taper_factor=self.taper_factor,
                session_setup_minutes=self.session_setup_minutes,
            )
            existing_index = option_index.get(plan)
            if existing_index is None:
                option_index[plan] = len(options)
                options.append(RouteOption(strategies=(strategy,), plan=plan))
                continue
            existing = options[existing_index]
            options[existing_index] = RouteOption(
                strategies=(*existing.strategies, strategy),
                plan=existing.plan,
            )

        return RouteOptionSet(
            options=tuple(options),
            unavailable_strategies=tuple(unavailable),
        )
