"""JSON shapes of the demo API and the functions that build them.

Everything here formats results that ``services`` has already computed. Nothing in this module plans
a route, replays a plan, or calls a road provider.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

from chargepath.alternatives import RouteOption, RouteStrategy
from chargepath.demo.schema import VEHICLE_INPUT_BOUNDS, PointInput, VehicleInputs
from chargepath.models import RoadLeg, Station
from chargepath.redundancy import StopRedundancy
from chargepath.verification import ReplayEvent

TILE_ATTRIBUTION = "© OpenStreetMap contributors"
ESTIMATE_LABEL = "SOC and charging times are model estimates"


class ErrorPayload(TypedDict):
    error: str


class EndpointPayload(TypedDict):
    id: str
    longitude: float
    latitude: float


class ConfigPayload(TypedDict):
    mode: Literal["fixture", "integration"]
    data_label: str
    tile_url: str
    tile_attribution: str
    endpoints: list[EndpointPayload]
    vehicle: dict[str, float]
    input_bounds: dict[str, list[float]]
    integration: dict[str, str | int | bool]


class RedundancyPayload(TypedDict):
    alternative_count: int
    alternatives_checked: int
    without_stop_added_minutes: float | None


class StopPayload(TypedDict):
    station_id: str
    name: str
    longitude: float
    latitude: float
    arrival_soc_pct: float
    departure_soc_pct: float
    energy_added_kwh: float
    charging_minutes: float
    redundancy: RedundancyPayload | None


class TimelineEntry(TypedDict):
    label: str
    kind: Literal["start", "charge", "station", "arrival"]
    soc_pct: float


class LineStringPayload(TypedDict):
    type: str
    coordinates: list[list[float]]


class FeaturePayload(TypedDict):
    type: Literal["Feature"]
    properties: dict[str, str]
    geometry: LineStringPayload


class FeatureCollectionPayload(TypedDict):
    type: Literal["FeatureCollection"]
    features: list[FeaturePayload]


class OptionPayload(TypedDict):
    id: str
    strategies: list[str]
    nodes: list[str]
    total_distance_km: float
    driving_minutes: float
    charging_minutes: float
    total_minutes: float
    arrival_soc_pct: float
    charging_stops: list[StopPayload]
    soc_timeline: list[TimelineEntry]
    geometry: FeatureCollectionPayload


class PlanPayload(TypedDict):
    data_label: str
    estimate_label: str
    model_resolution: dict[str, int | bool]
    candidate_selection: dict[str, int | bool]
    options: list[OptionPayload]
    unavailable_strategies: list[str]


@dataclass(frozen=True, slots=True)
class AnalyzedOption:
    """A returned option plus what the demo shows about it, all computed before formatting."""

    option: RouteOption
    # Battery level after each action, from the independent replay.
    replay: tuple[ReplayEvent, ...]
    # Structural fallbacks per charging station; measured for the fastest plan only.
    redundancy: Mapping[str, StopRedundancy] | None


@dataclass(frozen=True, slots=True)
class AnalyzedTrip:
    options: tuple[AnalyzedOption, ...]
    unavailable_strategies: tuple[RouteStrategy, ...]
    stations: Mapping[str, Station]
    soc_step_pct: int
    refined_after_coarse_infeasible: bool


def config_payload(
    *,
    mode: Literal["fixture", "integration"],
    data_label: str,
    tile_url: str,
    vehicle: VehicleInputs,
    endpoints: Sequence[PointInput],
    integration_summary: Mapping[str, str | int | bool] | None = None,
) -> ConfigPayload:
    return {
        "mode": mode,
        "data_label": data_label,
        "tile_url": tile_url,
        "tile_attribution": TILE_ATTRIBUTION,
        "endpoints": [
            {"id": point.id, "longitude": point.longitude, "latitude": point.latitude}
            for point in endpoints
        ],
        "vehicle": vehicle.to_dict(),
        "input_bounds": {
            field: [minimum, maximum] for field, (minimum, maximum) in VEHICLE_INPUT_BOUNDS.items()
        },
        "integration": dict(integration_summary or {}),
    }


def plan_payload(
    trip: AnalyzedTrip,
    *,
    data_label: str,
    candidate_selection: Mapping[str, int | bool] | None = None,
) -> PlanPayload:
    return {
        "data_label": data_label,
        "estimate_label": ESTIMATE_LABEL,
        "model_resolution": {
            "soc_step_pct": trip.soc_step_pct,
            "refined_after_coarse_infeasible": trip.refined_after_coarse_infeasible,
        },
        "candidate_selection": dict(candidate_selection or {}),
        "options": [
            _option_payload(index, analyzed, trip.stations)
            for index, analyzed in enumerate(trip.options, start=1)
        ],
        "unavailable_strategies": [strategy.value for strategy in trip.unavailable_strategies],
    }


def _option_payload(
    index: int, analyzed: AnalyzedOption, stations: Mapping[str, Station]
) -> OptionPayload:
    plan = analyzed.option.plan
    redundancy = analyzed.redundancy or {}
    return {
        "id": f"option-{index}",
        "strategies": [strategy.value for strategy in analyzed.option.strategies],
        "nodes": list(plan.node_ids),
        "total_distance_km": plan.total_distance_km,
        "driving_minutes": plan.driving_minutes,
        "charging_minutes": plan.charging_minutes,
        "total_minutes": plan.total_minutes,
        "arrival_soc_pct": plan.arrival_soc_pct,
        "charging_stops": [
            {
                "station_id": stop.station_id,
                "name": stations[stop.station_id].name,
                "longitude": stations[stop.station_id].longitude,
                "latitude": stations[stop.station_id].latitude,
                "arrival_soc_pct": stop.arrival_soc_pct,
                "departure_soc_pct": stop.departure_soc_pct,
                "energy_added_kwh": stop.energy_added_kwh,
                "charging_minutes": stop.charging_minutes,
                "redundancy": _redundancy_payload(redundancy.get(stop.station_id)),
            }
            for stop in plan.charging_stops
        ],
        "soc_timeline": _timeline(analyzed.replay, stations, arrival_id=plan.node_ids[-1]),
        "geometry": _feature_collection(plan.legs),
    }


def _redundancy_payload(item: StopRedundancy | None) -> RedundancyPayload | None:
    if item is None:
        return None
    return {
        "alternative_count": item.alternative_count,
        "alternatives_checked": item.alternatives_checked,
        "without_stop_added_minutes": item.without_stop_added_minutes,
    }


def _timeline(
    replay: Sequence[ReplayEvent], stations: Mapping[str, Station], *, arrival_id: str
) -> list[TimelineEntry]:
    timeline: list[TimelineEntry] = []
    for event in replay:
        if event.kind == "start":
            timeline.append({"label": "Start", "kind": "start", "soc_pct": event.soc_pct})
        elif event.kind == "charge":
            label = f"Charge · {stations[event.node_id].name}"
            timeline.append({"label": label, "kind": "charge", "soc_pct": event.soc_pct})
        elif event.node_id == arrival_id:
            timeline.append({"label": "Arrival", "kind": "arrival", "soc_pct": event.soc_pct})
        else:
            label = stations[event.node_id].name
            timeline.append({"label": label, "kind": "station", "soc_pct": event.soc_pct})
    return timeline


def _feature_collection(legs: Sequence[RoadLeg]) -> FeatureCollectionPayload:
    features: list[FeaturePayload] = []
    for leg in legs:
        if leg.geometry is None:
            raise ValueError("selected route leg is missing geometry")
        features.append(
            {
                "type": "Feature",
                "properties": {"origin_id": leg.origin_id, "destination_id": leg.destination_id},
                "geometry": {
                    "type": leg.geometry.type,
                    "coordinates": [list(position) for position in leg.geometry.coordinates],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
