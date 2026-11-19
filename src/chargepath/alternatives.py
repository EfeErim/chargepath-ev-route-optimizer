"""Competitive route strategies over the shared deterministic energy model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from chargepath import validation
from chargepath.baselines import ShortestDrivingTimeBaseline
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
from chargepath.optimizer import EVRouteOptimizer, RoutePreference
from chargepath.providers.base import RoadNetwork
from chargepath.verification import verify_route_plan

GREEDY_TARGET_SOC_PCT = 80


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


@dataclass(frozen=True, slots=True)
class RouteOptionSet:
    """Unique feasible options plus strategies that could not produce a plan."""

    options: tuple[RouteOption, ...]
    unavailable_strategies: tuple[RouteStrategy, ...] = ()

    def __post_init__(self) -> None:
        if not self.options:
            raise ValueError("route option set must contain at least one option")
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
        energy_bucket = grid.initial
        if origin_id == destination_id:
            return RoutePlan.stationary(origin_id, arrival_soc_pct=grid.soc_pct(energy_bucket))

        target_bucket = math.ceil(GREEDY_TARGET_SOC_PCT / self.soc_step_pct)
        leg_lookup = leg_bucket_lookup(
            required_buckets_cache,
            vehicle=vehicle,
            model=self.consumption_model,
            bucket_kwh=grid.bucket_kwh,
        )

        def after_leg(bucket: int, leg: RoadLeg) -> int | None:
            return grid.after_leg(
                bucket,
                buckets_for_leg(
                    leg_lookup,
                    leg,
                    vehicle=vehicle,
                    model=self.consumption_model,
                    bucket_kwh=grid.bucket_kwh,
                ),
            )

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

            shortest_path = ShortestDrivingTimeBaseline.shortest_path(
                current_node,
                destination_id,
                road_network,
            )
            if shortest_path:
                next_leg = shortest_path[0]
                remaining_bucket = after_leg(energy_bucket, next_leg)
                if remaining_bucket is not None:
                    legs.append(next_leg)
                    node_ids.append(next_leg.destination_id)
                    energy_bucket = remaining_bucket
                    current_node = next_leg.destination_id
                    continue

            current_station = stations.get(current_node)
            if (
                current_station is not None
                and current_station.connector_type in vehicle.supported_dc_connectors
                and current_node not in used_stations
                and energy_bucket < target_bucket
            ):
                stops.append(
                    self._charge_to_target(
                        station=current_station,
                        node_index=len(node_ids) - 1,
                        energy_bucket=energy_bucket,
                        target_bucket=target_bucket,
                        bucket_kwh=grid.bucket_kwh,
                        vehicle=vehicle,
                    )
                )
                energy_bucket = target_bucket
                used_stations.add(current_node)
                continue

            # Detours go only to stations not yet on the route. Returning to a visited node would
            # let the heuristic shuttle between two nodes, draining the battery on every pass.
            visited = set(node_ids)
            candidates: list[tuple[float, float, str, RoadLeg, int]] = []
            for leg in road_network.neighbors(current_node):
                station = stations.get(leg.destination_id)
                if station is None or station.id in visited:
                    continue
                if station.connector_type not in vehicle.supported_dc_connectors:
                    continue
                arrival_bucket = after_leg(energy_bucket, leg)
                if arrival_bucket is None or arrival_bucket >= target_bucket:
                    continue
                remaining_path = ShortestDrivingTimeBaseline.shortest_path(
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

        return RoutePlan.assemble(
            node_ids=tuple(node_ids),
            legs=tuple(legs),
            charging_stops=tuple(stops),
            arrival_soc_pct=grid.soc_pct(energy_bucket),
        )

    def _charge_to_target(
        self,
        *,
        station: Station,
        node_index: int,
        energy_bucket: int,
        target_bucket: int,
        bucket_kwh: float,
        vehicle: VehicleProfile,
    ) -> ChargingStop:
        arrival_soc = energy_bucket * self.soc_step_pct
        departure_soc = target_bucket * self.soc_step_pct
        minutes = charging_minutes(vehicle, station.max_power_kw, arrival_soc, departure_soc)
        return ChargingStop(
            station_id=station.id,
            node_index=node_index,
            arrival_soc_pct=float(arrival_soc),
            departure_soc_pct=float(departure_soc),
            energy_added_kwh=(target_bucket - energy_bucket) * bucket_kwh,
            charging_minutes=minutes,
        )


class CompetitiveRoutePlanner:
    """Run exact objective variants and a greedy heuristic, then deduplicate plans."""

    def __init__(
        self,
        *,
        soc_step_pct: int = 5,
        consumption_model: ConsumptionModel = DEFAULT_CONSUMPTION,
    ) -> None:
        validation.soc_step(soc_step_pct)
        self.soc_step_pct = soc_step_pct
        self.consumption_model = consumption_model

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
        required_buckets_cache: LegBucketCache = {}
        exact_strategies = (
            (RouteStrategy.FASTEST, RoutePreference.FASTEST),
            (RouteStrategy.SHORTEST_DISTANCE, RoutePreference.SHORTEST_DISTANCE),
            (RouteStrategy.FEWEST_CHARGING_STOPS, RoutePreference.FEWEST_CHARGING_STOPS),
        )
        for strategy, preference in exact_strategies:
            optimizer = EVRouteOptimizer(
                preference=preference,
                soc_step_pct=self.soc_step_pct,
                consumption_model=self.consumption_model,
            )
            try:
                plan = optimizer.optimize_with_cache(
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
            soc_step_pct=self.soc_step_pct, consumption_model=self.consumption_model
        )
        try:
            greedy_plan = greedy.optimize_with_cache(
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
                consumption_model=self.consumption_model,
                road_network=road_network,
                origin_id=origin_id,
                destination_id=destination_id,
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
