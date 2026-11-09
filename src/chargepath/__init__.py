"""ChargePath EV route-planning core."""

from chargepath.alternatives import (
    CompetitiveRoutePlanner,
    GreedyFixedTargetOptimizer,
    RouteOption,
    RouteOptionSet,
    RouteStrategy,
)
from chargepath.baselines import ShortestDrivingTimeBaseline
from chargepath.fixtures import load_synthetic_scenario
from chargepath.models import (
    GeoJsonLineString,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
)
from chargepath.optimizer import EVRouteOptimizer, RoutePreference
from chargepath.verification import PlanVerificationError, verify_route_plan

__all__ = [
    "CompetitiveRoutePlanner",
    "EVRouteOptimizer",
    "GeoJsonLineString",
    "GreedyFixedTargetOptimizer",
    "NoFeasibleRouteError",
    "PlanVerificationError",
    "RoadLeg",
    "RouteOption",
    "RouteOptionSet",
    "RoutePlan",
    "RoutePreference",
    "RouteStrategy",
    "ShortestDrivingTimeBaseline",
    "Station",
    "VehicleProfile",
    "load_synthetic_scenario",
    "verify_route_plan",
]
