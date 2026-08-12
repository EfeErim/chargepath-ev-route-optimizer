"""Deterministic route-corridor selection for normalized compatible stations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from chargepath.models import GeoJsonLineString
from chargepath.station_data import CompatibleStationOption

EARTH_RADIUS_KM = 6371.0088
LEGACY_ALGORITHM = "equirectangular-segment-v1"
PROGRESS_STRATA_ALGORITHM = "equirectangular-segment-progress-strata-v2"
FARTHEST_PROGRESS_ALGORITHM = "equirectangular-segment-farthest-progress-v3"
PROGRESS_STRATA_COVERAGE_POLICY = "one-best-per-equal-progress-stratum-then-global-fill"
FARTHEST_PROGRESS_COVERAGE_POLICY = "prefix-monotonic-largest-progress-gap-then-quality"
PROGRESS_COVERAGE_TOLERANCE_FRACTION = 0.02
LEGACY_RANKING_KEYS = (
    "corridor_distance_km_asc",
    "route_progress_fraction_asc",
    "max_power_kw_desc",
    "stable_station_id_asc",
)
PROGRESS_STRATA_RANKING_KEYS = (
    "progress_stratum_asc",
    "corridor_distance_km_asc",
    "max_power_kw_desc",
    "route_progress_fraction_asc",
    "stable_station_id_asc",
)
RANKING_KEYS = (
    "route_progress_coverage_gap_desc_with_tolerance",
    "corridor_distance_km_asc",
    "max_power_kw_desc",
    "route_progress_fraction_asc",
    "stable_station_id_asc",
)


@dataclass(frozen=True, slots=True)
class CandidateSelectionConfig:
    corridor_width_km: float
    candidate_cap: int
    algorithm: str = FARTHEST_PROGRESS_ALGORITHM
    ranking_keys: tuple[str, ...] = RANKING_KEYS

    def __post_init__(self) -> None:
        if (
            isinstance(self.corridor_width_km, bool)
            or not isinstance(self.corridor_width_km, (int, float))
            or not math.isfinite(self.corridor_width_km)
            or self.corridor_width_km <= 0
        ):
            raise ValueError("corridor_width_km must be finite and positive")
        if (
            isinstance(self.candidate_cap, bool)
            or not isinstance(self.candidate_cap, int)
            or self.candidate_cap <= 0
        ):
            raise ValueError("candidate_cap must be positive")
        if not isinstance(self.algorithm, str):
            raise ValueError("corridor-selection algorithm must be text")
        expected_ranking_keys = {
            LEGACY_ALGORITHM: LEGACY_RANKING_KEYS,
            PROGRESS_STRATA_ALGORITHM: PROGRESS_STRATA_RANKING_KEYS,
            FARTHEST_PROGRESS_ALGORITHM: RANKING_KEYS,
        }.get(self.algorithm)
        if expected_ranking_keys is None:
            raise ValueError("unsupported corridor-selection algorithm")
        if self.ranking_keys != expected_ranking_keys:
            raise ValueError("ranking_keys must match the selected deterministic algorithm")

    def to_manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "algorithm": self.algorithm,
            "corridor_width_km": self.corridor_width_km,
            "candidate_cap": self.candidate_cap,
            "ranking_keys": list(self.ranking_keys),
            "final_tie_break": "stable_station_id_asc",
        }
        if self.algorithm == PROGRESS_STRATA_ALGORITHM:
            manifest["coverage_policy"] = PROGRESS_STRATA_COVERAGE_POLICY
        if self.algorithm == FARTHEST_PROGRESS_ALGORITHM:
            manifest["coverage_policy"] = FARTHEST_PROGRESS_COVERAGE_POLICY
            manifest["progress_gap_tolerance_fraction"] = PROGRESS_COVERAGE_TOLERANCE_FRACTION
        return manifest


DEFAULT_CANDIDATE_SELECTION_CONFIG = CandidateSelectionConfig(
    corridor_width_km=25.0,
    candidate_cap=50,
)


@dataclass(frozen=True, slots=True)
class CorridorCandidate:
    option: CompatibleStationOption
    corridor_distance_km: float
    route_progress_fraction: float


@dataclass(frozen=True, slots=True)
class CandidateSelectionResult:
    candidates: tuple[CorridorCandidate, ...]
    eligible_count: int
    config: CandidateSelectionConfig

    @property
    def station_ids(self) -> tuple[str, ...]:
        return tuple(candidate.option.station.id for candidate in self.candidates)


def select_corridor_candidates(
    *,
    route_geometry: GeoJsonLineString,
    options: tuple[CompatibleStationOption, ...],
    config: CandidateSelectionConfig,
) -> CandidateSelectionResult:
    """Return capped candidates using explicit stable ranking and tie-breaking."""

    prepared_route = _prepare_route(
        route_geometry.coordinates,
        corridor_width_km=config.corridor_width_km,
    )
    candidates: list[CorridorCandidate] = []
    station_ids = tuple(option.station.id for option in options)
    if len(set(station_ids)) != len(station_ids):
        raise ValueError("corridor candidate station ids must be unique")
    for option in options:
        match = _distance_and_progress_within_corridor(
            longitude=option.station.longitude,
            latitude=option.station.latitude,
            route=prepared_route,
        )
        if match is not None:
            distance, progress = match
            candidates.append(CorridorCandidate(option, distance, progress))
    if config.algorithm == LEGACY_ALGORITHM:
        selected = sorted(candidates, key=_legacy_ranking_key)[: config.candidate_cap]
    elif config.algorithm == PROGRESS_STRATA_ALGORITHM:
        selected = _select_progress_strata(candidates, candidate_cap=config.candidate_cap)
    else:
        selected = _select_farthest_progress(candidates, candidate_cap=config.candidate_cap)
    return CandidateSelectionResult(
        candidates=tuple(selected),
        eligible_count=len(candidates),
        config=config,
    )


def _legacy_ranking_key(candidate: CorridorCandidate) -> tuple[float, float, float, str]:
    return (
        candidate.corridor_distance_km,
        candidate.route_progress_fraction,
        -candidate.option.station.max_power_kw,
        candidate.option.station.id,
    )


def _progress_stratum(progress: float, candidate_cap: int) -> int:
    return min(candidate_cap - 1, math.floor(progress * candidate_cap))


def _stratum_quality_key(candidate: CorridorCandidate) -> tuple[float, float, float, str]:
    return (
        candidate.corridor_distance_km,
        -candidate.option.station.max_power_kw,
        candidate.route_progress_fraction,
        candidate.option.station.id,
    )


def _select_progress_strata(
    candidates: list[CorridorCandidate], *, candidate_cap: int
) -> list[CorridorCandidate]:
    """Cover route progress before using spare capacity for globally strong candidates."""

    quality_order = sorted(candidates, key=_stratum_quality_key)
    selected_by_stratum: dict[int, CorridorCandidate] = {}
    for candidate in quality_order:
        stratum = _progress_stratum(candidate.route_progress_fraction, candidate_cap)
        selected_by_stratum.setdefault(stratum, candidate)

    selected = list(selected_by_stratum.values())
    selected_ids = {candidate.option.station.id for candidate in selected}
    for candidate in quality_order:
        if len(selected) >= candidate_cap:
            break
        if candidate.option.station.id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.option.station.id)

    selected.sort(
        key=lambda candidate: (
            _progress_stratum(candidate.route_progress_fraction, candidate_cap),
            *_stratum_quality_key(candidate),
        )
    )
    return selected[:candidate_cap]


def _select_farthest_progress(
    candidates: list[CorridorCandidate], *, candidate_cap: int
) -> list[CorridorCandidate]:
    """Build a stable prefix whose next item closes the largest remaining progress gap."""

    if not candidates:
        return []
    remaining = sorted(candidates, key=_stratum_quality_key)
    earliest_progress = min(candidate.route_progress_fraction for candidate in remaining)
    first = min(
        (
            candidate
            for candidate in remaining
            if candidate.route_progress_fraction
            <= earliest_progress + PROGRESS_COVERAGE_TOLERANCE_FRACTION
        ),
        key=_stratum_quality_key,
    )
    selected = [first]
    remaining.remove(first)
    minimum_gap = {
        candidate.option.station.id: abs(
            candidate.route_progress_fraction - first.route_progress_fraction
        )
        for candidate in remaining
    }
    while remaining and len(selected) < candidate_cap:
        largest_gap = max(minimum_gap.values())
        next_candidate = min(
            (
                candidate
                for candidate in remaining
                if minimum_gap[candidate.option.station.id]
                >= largest_gap - PROGRESS_COVERAGE_TOLERANCE_FRACTION
            ),
            key=_stratum_quality_key,
        )
        selected.append(next_candidate)
        remaining.remove(next_candidate)
        minimum_gap.pop(next_candidate.option.station.id)
        for candidate in remaining:
            station_id = candidate.option.station.id
            minimum_gap[station_id] = min(
                minimum_gap[station_id],
                abs(candidate.route_progress_fraction - next_candidate.route_progress_fraction),
            )
    return selected


@dataclass(frozen=True, slots=True)
class _PreparedSegment:
    start_longitude: float
    start_latitude: float
    end_longitude: float
    end_latitude: float
    length_km: float
    completed_km: float
    minimum_longitude: float
    maximum_longitude: float
    minimum_latitude: float
    maximum_latitude: float


@dataclass(frozen=True, slots=True)
class _PreparedRoute:
    segments: tuple[_PreparedSegment, ...]
    total_length_km: float
    corridor_width_km: float


def _prepare_route(
    route: tuple[tuple[float, float], ...], *, corridor_width_km: float
) -> _PreparedRoute:
    """Precompute invariant route work and conservative per-segment search bounds."""

    latitude_padding = math.degrees(corridor_width_km / EARTH_RADIUS_KM)
    segments: list[_PreparedSegment] = []
    completed_km = 0.0
    for (start_lon, start_lat), (end_lon, end_lat) in zip(route, route[1:], strict=False):
        length_km = _haversine_km(start_lon, start_lat, end_lon, end_lat)
        latitude_limit = max(abs(start_lat), abs(end_lat)) + latitude_padding
        longitude_padding = (
            180.0
            if latitude_limit >= 90.0
            else math.degrees(
                corridor_width_km / (EARTH_RADIUS_KM * math.cos(math.radians(latitude_limit)))
            )
        )
        segments.append(
            _PreparedSegment(
                start_longitude=start_lon,
                start_latitude=start_lat,
                end_longitude=end_lon,
                end_latitude=end_lat,
                length_km=length_km,
                completed_km=completed_km,
                minimum_longitude=min(start_lon, end_lon) - longitude_padding,
                maximum_longitude=max(start_lon, end_lon) + longitude_padding,
                minimum_latitude=min(start_lat, end_lat) - latitude_padding,
                maximum_latitude=max(start_lat, end_lat) + latitude_padding,
            )
        )
        completed_km += length_km
    return _PreparedRoute(
        segments=tuple(segments),
        total_length_km=completed_km,
        corridor_width_km=corridor_width_km,
    )


def _distance_and_progress_within_corridor(
    *, longitude: float, latitude: float, route: _PreparedRoute
) -> tuple[float, float] | None:
    """Return the exact existing metric only when a point is inside the corridor."""

    best: tuple[float, float, int] | None = None
    for index, segment in enumerate(route.segments):
        if not (
            segment.minimum_longitude <= longitude <= segment.maximum_longitude
            and segment.minimum_latitude <= latitude <= segment.maximum_latitude
        ):
            continue
        start_lon = segment.start_longitude
        start_lat = segment.start_latitude
        end_lon = segment.end_longitude
        end_lat = segment.end_latitude
        reference_latitude = math.radians((start_lat + end_lat + latitude) / 3.0)
        x_scale = EARTH_RADIUS_KM * math.cos(reference_latitude) * math.pi / 180.0
        y_scale = EARTH_RADIUS_KM * math.pi / 180.0
        segment_x = (end_lon - start_lon) * x_scale
        segment_y = (end_lat - start_lat) * y_scale
        point_x = (longitude - start_lon) * x_scale
        point_y = (latitude - start_lat) * y_scale
        squared_length = segment_x * segment_x + segment_y * segment_y
        position = (
            0.0
            if squared_length == 0
            else ((point_x * segment_x + point_y * segment_y) / squared_length)
        )
        position = min(1.0, max(0.0, position))
        delta_x = point_x - position * segment_x
        delta_y = point_y - position * segment_y
        distance = math.hypot(delta_x, delta_y)
        progress = (
            0.0
            if route.total_length_km == 0
            else (segment.completed_km + position * segment.length_km) / route.total_length_km
        )
        ranked = (distance, progress, index)
        if best is None or ranked < best:
            best = ranked
    if best is None or best[0] > route.corridor_width_km:
        return None
    return best[0], best[1]


def _haversine_km(
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
) -> float:
    start_lat = math.radians(start_latitude)
    end_lat = math.radians(end_latitude)
    delta_lat = end_lat - start_lat
    delta_lon = math.radians(end_longitude - start_longitude)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(start_lat) * math.cos(end_lat) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(haversine)))
