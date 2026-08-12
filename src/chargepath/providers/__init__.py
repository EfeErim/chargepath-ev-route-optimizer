"""Road-network provider interfaces and implementations."""

from chargepath.providers.base import RoadNetwork, RoadRouteClient, RoadTableClient
from chargepath.providers.osrm import (
    DEFAULT_OSRM_ENDPOINT,
    CandidateGraph,
    CandidateGraphBuilder,
    CandidateNode,
    Coordinate,
    OsrmHttpClient,
    OsrmProviderError,
    OsrmResponseError,
    OsrmTableCost,
    OsrmTableResult,
    OsrmTimeoutError,
    OsrmTransportError,
    fetch_selected_plan_geometry,
    fetch_selected_plans_geometry,
)
from chargepath.providers.static import StaticRoadNetwork

__all__ = [
    "DEFAULT_OSRM_ENDPOINT",
    "CandidateGraph",
    "CandidateGraphBuilder",
    "CandidateNode",
    "Coordinate",
    "OsrmHttpClient",
    "OsrmProviderError",
    "OsrmResponseError",
    "OsrmTableCost",
    "OsrmTableResult",
    "OsrmTimeoutError",
    "OsrmTransportError",
    "RoadNetwork",
    "RoadRouteClient",
    "RoadTableClient",
    "StaticRoadNetwork",
    "fetch_selected_plan_geometry",
    "fetch_selected_plans_geometry",
]
