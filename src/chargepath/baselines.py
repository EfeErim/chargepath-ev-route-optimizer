"""Deterministic road-only baseline used by the M3 evaluation suite."""

from __future__ import annotations

import heapq
import itertools
import math

from chargepath.energy import energy_for_distance, required_energy_buckets
from chargepath.models import NoFeasibleRouteError, RoadLeg, RoutePlan, VehicleProfile
from chargepath.providers.base import RoadNetwork


class ShortestDrivingTimeBaseline:
    """Select the minimum-driving-time path and evaluate it without charging."""

    def __init__(self, *, soc_step_pct: int = 5) -> None:
        if (
            isinstance(soc_step_pct, bool)
            or not isinstance(soc_step_pct, int)
            or soc_step_pct <= 0
            or 100 % soc_step_pct != 0
        ):
            raise ValueError("soc_step_pct must be a positive divisor of 100")
        self.soc_step_pct = soc_step_pct

    def optimize(
        self,
        *,
        origin_id: str,
        destination_id: str,
        vehicle: VehicleProfile,
        road_network: RoadNetwork,
    ) -> RoutePlan:
        """Return the road-only path when it preserves reserve on every leg."""

        if (
            not isinstance(origin_id, str)
            or not isinstance(destination_id, str)
            or not origin_id.strip()
            or not destination_id.strip()
        ):
            raise ValueError("origin_id and destination_id must not be empty")
        initial_bucket = math.floor(vehicle.initial_soc_pct / self.soc_step_pct)
        if origin_id == destination_id:
            return RoutePlan(
                node_ids=(origin_id,),
                legs=(),
                charging_stops=(),
                total_distance_km=0.0,
                driving_minutes=0.0,
                charging_minutes=0.0,
                arrival_soc_pct=float(initial_bucket * self.soc_step_pct),
                total_minutes=0.0,
            )

        legs = self.shortest_path(origin_id, destination_id, road_network)
        if legs is None:
            raise NoFeasibleRouteError(
                f"shortest-driving-time baseline found no road path from {origin_id!r} "
                f"to {destination_id!r}"
            )

        reserve_bucket = math.ceil(vehicle.reserve_soc_pct / self.soc_step_pct)
        bucket_kwh = vehicle.usable_battery_kwh * self.soc_step_pct / 100
        energy_bucket = initial_bucket
        for leg in legs:
            required_kwh = energy_for_distance(
                leg.distance_km,
                vehicle.consumption_kwh_per_100km,
                vehicle.energy_safety_factor,
            )
            energy_bucket -= required_energy_buckets(required_kwh, bucket_kwh)
            if energy_bucket < reserve_bucket:
                raise NoFeasibleRouteError(
                    "shortest-driving-time baseline path violates the reserve-SOC invariant"
                )

        node_ids = (origin_id, *(leg.destination_id for leg in legs))
        total_distance = sum(leg.distance_km for leg in legs)
        driving_minutes = sum(leg.duration_minutes for leg in legs)
        return RoutePlan(
            node_ids=node_ids,
            legs=legs,
            charging_stops=(),
            total_distance_km=total_distance,
            driving_minutes=driving_minutes,
            charging_minutes=0.0,
            arrival_soc_pct=float(energy_bucket * self.soc_step_pct),
            total_minutes=driving_minutes,
        )

    @staticmethod
    def shortest_path(
        origin_id: str,
        destination_id: str,
        road_network: RoadNetwork,
    ) -> tuple[RoadLeg, ...] | None:
        """Use full node sequence as the stable tie-break after driving duration."""

        if origin_id == destination_id:
            return ()
        sequence = itertools.count()
        start_path = (origin_id,)
        queue: list[tuple[float, tuple[str, ...], int, str, tuple[RoadLeg, ...]]] = [
            (0.0, start_path, next(sequence), origin_id, ())
        ]
        best: dict[str, tuple[float, tuple[str, ...]]] = {origin_id: (0.0, start_path)}
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
