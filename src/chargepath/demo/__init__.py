"""Loopback-only fixture-first web demo for ChargePath.

``schema`` validates requests, ``services`` plans and analyzes them, ``responses`` formats the
result as JSON, ``server`` exposes the services on IPv4 loopback, and ``browser`` answers the same
API inside Pyodide. Importing this package does not import the HTTP server; ``python -m
chargepath.demo`` and the ``chargepath-demo`` command start it.
"""

from chargepath.demo.responses import TILE_ATTRIBUTION
from chargepath.demo.schema import (
    MAX_REQUEST_BYTES,
    VEHICLE_INPUT_BOUNDS,
    PlanRequest,
    PointInput,
    VehicleInputs,
)
from chargepath.demo.services import (
    DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT,
    DEFAULT_TILE_URL,
    REFINED_SOC_STEP_PCT,
    SOC_STEP_PCT,
    FixturePlanningService,
    IntegrationPlanningService,
    PlanningService,
)

__all__ = [
    "DEFAULT_ADAPTIVE_CANDIDATE_CAP_LIMIT",
    "DEFAULT_TILE_URL",
    "MAX_REQUEST_BYTES",
    "REFINED_SOC_STEP_PCT",
    "SOC_STEP_PCT",
    "TILE_ATTRIBUTION",
    "VEHICLE_INPUT_BOUNDS",
    "FixturePlanningService",
    "IntegrationPlanningService",
    "PlanRequest",
    "PlanningService",
    "PointInput",
    "VehicleInputs",
]
