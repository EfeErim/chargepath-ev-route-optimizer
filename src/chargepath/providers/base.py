"""Provider boundary between the planner and road data."""

from __future__ import annotations

from typing import Protocol

from chargepath.models import RoadLeg


class CoordinateLike(Protocol):
    """Structural coordinate contract; provider clients accept any object with these fields."""

    @property
    def longitude(self) -> float: ...

    @property
    def latitude(self) -> float: ...


class RoadNetwork(Protocol):
    """What the planner reads. Implemented by ``StaticRoadNetwork`` and ``SpeedChoiceNetwork``."""

    def neighbors(self, node_id: str) -> tuple[RoadLeg, ...]:
        """Return directed road legs leaving a candidate node."""
        ...
