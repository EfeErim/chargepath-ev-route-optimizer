"""Loopback-only fixture-first web demo for ChargePath.

``schema`` validates requests, ``services`` plans them without any HTTP dependency, and ``server``
exposes them on IPv4 loopback.
"""

from chargepath.demo.schema import VEHICLE_INPUT_BOUNDS, PlanRequest, PointInput, VehicleInputs
from chargepath.demo.server import (
    DEFAULT_PORT,
    HEALTH_API_VERSION,
    MAX_REQUEST_BYTES,
    DemoHttpServer,
    DemoRequestHandler,
    DemoServerConfig,
    create_server,
    main,
)
from chargepath.demo.services import (
    DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT,
    DEFAULT_TILE_URL,
    REFINED_SOC_STEP_PCT,
    SOC_STEP_PCT,
    TILE_ATTRIBUTION,
    FixturePlanningService,
    IntegrationPlanningService,
    PlanningService,
)

__all__ = [
    "DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT",
    "DEFAULT_PORT",
    "DEFAULT_TILE_URL",
    "HEALTH_API_VERSION",
    "MAX_REQUEST_BYTES",
    "REFINED_SOC_STEP_PCT",
    "SOC_STEP_PCT",
    "TILE_ATTRIBUTION",
    "VEHICLE_INPUT_BOUNDS",
    "DemoHttpServer",
    "DemoRequestHandler",
    "DemoServerConfig",
    "FixturePlanningService",
    "IntegrationPlanningService",
    "PlanRequest",
    "PlanningService",
    "PointInput",
    "VehicleInputs",
    "create_server",
    "main",
]
