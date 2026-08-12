"""Provider boundary for road-network routing engines."""

from __future__ import annotations

from typing import Protocol

from chargepath.models import GeoJsonLineString, RoadLeg


class CoordinateLike(Protocol):
    """Structural coordinate contract used by road-provider clients."""

    @property
    def longitude(self) -> float: ...

    @property
    def latitude(self) -> float: ...


class TableCost(Protocol):
    """One reachable fastest-route cell returned by a road table."""

    @property
    def distance_km(self) -> float: ...

    @property
    def duration_minutes(self) -> float: ...


class RoadTable(Protocol):
    """Directed pairwise road costs in input-coordinate order."""

    @property
    def cells(self) -> tuple[tuple[TableCost | None, ...], ...]: ...


class RoadTableClient(Protocol):
    def table(self, coordinates: tuple[CoordinateLike, ...]) -> RoadTable:
        """Return directed fastest-route costs for every coordinate pair."""
        ...


class RoadRouteClient(Protocol):
    def route_geometry(
        self,
        origin: CoordinateLike,
        destination: CoordinateLike,
    ) -> GeoJsonLineString:
        """Return full GeoJSON geometry for one selected directed leg."""
        ...


class RoadNetwork(Protocol):
    def neighbors(self, node_id: str) -> tuple[RoadLeg, ...]:
        """Return directed road legs leaving a candidate node."""
        ...
