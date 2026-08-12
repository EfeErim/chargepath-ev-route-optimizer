"""In-memory road network for deterministic examples and tests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from chargepath.models import RoadLeg


class StaticRoadNetwork:
    def __init__(self, legs: Iterable[RoadLeg]) -> None:
        adjacency: dict[str, list[RoadLeg]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()
        for leg in legs:
            key = (leg.origin_id, leg.destination_id)
            if key in seen:
                raise ValueError(f"duplicate directed road leg: {key}")
            seen.add(key)
            adjacency[leg.origin_id].append(leg)
        self._adjacency = {
            node_id: tuple(sorted(node_legs, key=lambda item: item.destination_id))
            for node_id, node_legs in adjacency.items()
        }

    def neighbors(self, node_id: str) -> tuple[RoadLeg, ...]:
        return self._adjacency.get(node_id, ())
