"""Deterministic road-only baseline used by the synthetic benchmark."""

from __future__ import annotations

import heapq
import itertools
import math

from chargepath import validation
from chargepath.energy import (
    DEFAULT_CONSUMPTION,
    ConsumptionModel,
    SocGrid,
    leg_buckets,
)
from chargepath.models import NoFeasibleRouteError, RoadLeg, RoutePlan, VehicleProfile
from chargepath.providers.base import RoadNetwork


class ShortestDrivingTimeBaseline:
    """Select the minimum-driving-time path and evaluate it without charging."""

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
        road_network: RoadNetwork,
    ) -> RoutePlan:
        """Return the road-only path when it preserves reserve on every leg."""

        validation.trip_endpoints(origin_id, destination_id)
        grid = SocGrid.for_vehicle(vehicle, self.soc_step_pct)
        if origin_id == destination_id:
            return RoutePlan.stationary(origin_id, arrival_soc_pct=grid.soc_pct(grid.initial))

        legs = self.shortest_path(origin_id, destination_id, road_network)
        if legs is None:
            raise NoFeasibleRouteError(
                f"shortest-driving-time baseline found no road path from {origin_id!r} "
                f"to {destination_id!r}"
            )

        energy_bucket = grid.initial
        for leg in legs:
            remaining = grid.after_leg(
                energy_bucket,
                leg_buckets(self.consumption_model.leg_energy(leg, vehicle), grid.bucket_kwh),
            )
            if remaining is None:
                raise NoFeasibleRouteError(
                    "shortest-driving-time baseline path violates the reserve-SOC invariant"
                )
            energy_bucket = remaining

        driving_minutes = sum(leg.duration_minutes for leg in legs)
        return RoutePlan(
            node_ids=(origin_id, *(leg.destination_id for leg in legs)),
            legs=legs,
            charging_stops=(),
            total_distance_km=sum(leg.distance_km for leg in legs),
            driving_minutes=driving_minutes,
            charging_minutes=0.0,
            arrival_soc_pct=grid.soc_pct(energy_bucket),
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
