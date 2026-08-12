"""ChargePath EV route-planning core."""

from chargepath.alternatives import (
    CompetitiveRoutePlanner,
    GreedyFixedTargetOptimizer,
    RouteOption,
    RouteOptionSet,
    RouteStrategy,
)
from chargepath.baselines import ShortestDrivingTimeBaseline
from chargepath.corridor import (
    DEFAULT_CANDIDATE_SELECTION_CONFIG,
    CandidateSelectionConfig,
    CandidateSelectionResult,
    CorridorCandidate,
    select_corridor_candidates,
)
from chargepath.evaluation import (
    BenchmarkCase,
    BenchmarkManifest,
    build_correctness_artifact,
    build_runtime_artifact,
    canonical_json_bytes,
    load_benchmark_manifest,
)
from chargepath.fixtures import SyntheticScenario, load_synthetic_scenario
from chargepath.models import (
    ChargingStop,
    GeoJsonLineString,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
)
from chargepath.optimizer import EVRouteOptimizer, RoutePreference
from chargepath.station_data import (
    CompatibleStationOption,
    EpdkSchemaDriftError,
    NormalizationResult,
    NormalizedSite,
    NormalizedSocket,
    SnapshotManifest,
    SnapshotProvenance,
    StationValidationReport,
    normalize_epdk_response,
    project_ccs2_dc_options,
)
from chargepath.verification import PlanVerificationError, verify_route_plan

__all__ = [
    "ChargingStop",
    "BenchmarkCase",
    "BenchmarkManifest",
    "CandidateSelectionConfig",
    "CandidateSelectionResult",
    "CompatibleStationOption",
    "CompetitiveRoutePlanner",
    "EVRouteOptimizer",
    "EpdkSchemaDriftError",
    "GeoJsonLineString",
    "GreedyFixedTargetOptimizer",
    "NoFeasibleRouteError",
    "NormalizationResult",
    "NormalizedSite",
    "NormalizedSocket",
    "RoadLeg",
    "RouteOption",
    "RouteOptionSet",
    "RoutePlan",
    "RoutePreference",
    "RouteStrategy",
    "SnapshotManifest",
    "SnapshotProvenance",
    "ShortestDrivingTimeBaseline",
    "Station",
    "StationValidationReport",
    "SyntheticScenario",
    "VehicleProfile",
    "PlanVerificationError",
    "load_synthetic_scenario",
    "load_benchmark_manifest",
    "build_correctness_artifact",
    "build_runtime_artifact",
    "canonical_json_bytes",
    "verify_route_plan",
    "CorridorCandidate",
    "DEFAULT_CANDIDATE_SELECTION_CONFIG",
    "normalize_epdk_response",
    "project_ccs2_dc_options",
    "select_corridor_candidates",
]
