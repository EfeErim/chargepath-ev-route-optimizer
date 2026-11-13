"""Real-route collection stage: the only stage that talks to OSRM.

Candidate graphs are stored under the Git-ignored ``data/processed/real-routes/`` directory
because they contain real station rows. A provider failure is stored as an outcome, never
dropped.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from chargepath.corridor import (
    FARTHEST_PROGRESS_ALGORITHM,
    CandidateSelectionConfig,
    select_corridor_candidates,
)
from chargepath.evaluation import _nearest_rank_p95
from chargepath.models import GeoJsonLineString, RoadLeg, Station
from chargepath.providers.osrm import (
    CandidateGraphBuilder,
    CandidateNode,
    Coordinate,
    OsrmProviderError,
    OsrmResponseError,
    RoadRouteClient,
    RoadTableClient,
)
from chargepath.station_data import CompatibleStationOption, load_snapshot_options

GRAPH_SCHEMA_VERSION = 1
GRAPH_EXPERIMENTS = ("E1", "E2", "E3", "E4", "E5")


class CollectionClient(RoadTableClient, RoadRouteClient, Protocol):
    """Both OSRM services; tests substitute an offline fake with a configurable data version."""


@dataclass(frozen=True, slots=True, order=True)
class GraphKey:
    route_id: str
    corridor_width_km: int
    candidate_cap: int

    @property
    def file_name(self) -> str:
        return f"{self.route_id}-w{self.corridor_width_km}-c{self.candidate_cap}.json"


@dataclass(frozen=True, slots=True)
class StoredGraph:
    key: GraphKey
    outcome: str
    eligible_count: int
    stations: Mapping[str, Station]
    legs: tuple[RoadLeg, ...]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def required_graph_keys(manifest: Mapping[str, Any]) -> tuple[GraphKey, ...]:
    """Return every graph the declared experiments need, once each."""

    keys: set[GraphKey] = set()
    experiments = manifest["experiments"]
    for name in GRAPH_EXPERIMENTS:
        experiment = experiments[name]
        for route_id in experiment["routes"]:
            for width in _as_list(experiment["corridor_width_km"]):
                for cap in _as_list(experiment["candidate_cap"]):
                    keys.add(GraphKey(route_id, width, cap))
    return tuple(sorted(keys))


def route_endpoints(route: Mapping[str, Any]) -> tuple[Coordinate, Coordinate]:
    origin, destination = route["origin"], route["destination"]
    return (
        Coordinate(origin["longitude"], origin["latitude"]),
        Coordinate(destination["longitude"], destination["latitude"]),
    )


def _graph_bytes(document: Mapping[str, Any]) -> bytes:
    text = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return (text + "\n").encode("utf-8")


def _failure_document(key: GraphKey, error: OsrmProviderError) -> dict[str, Any]:
    # The message can quote a request URL, so only the type and provider code are kept.
    code = error.code if isinstance(error, OsrmResponseError) else None
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "route_id": key.route_id,
        "corridor_width_km": key.corridor_width_km,
        "candidate_cap": key.candidate_cap,
        "outcome": "provider_failure",
        "error_type": type(error).__name__,
        "error_code": code,
    }


def collect_graphs(
    *,
    manifest: Mapping[str, Any],
    options: tuple[CompatibleStationOption, ...],
    client: CollectionClient,
    output_dir: Path,
    keys: Iterable[GraphKey] | None = None,
) -> tuple[GraphKey, ...]:
    """Build and store each required candidate graph, recording provider failures as outcomes."""

    frozen = manifest["frozen_inputs"]
    corridor_algorithm = manifest["shared_configuration"]["corridor_algorithm"]
    if corridor_algorithm != FARTHEST_PROGRESS_ALGORITHM:
        raise ValueError(f"unsupported corridor algorithm: {corridor_algorithm}")
    routes = {route["id"]: route for route in manifest["routes"]}
    selected_keys = tuple(sorted(keys)) if keys is not None else required_graph_keys(manifest)
    data_version = frozen["road_graph"]["data_version"]
    first_origin, first_destination = route_endpoints(routes[selected_keys[0].route_id])
    observed = client.table((first_origin, first_destination)).data_version
    if observed != data_version:
        raise ValueError(f"OSRM data_version {observed!r} is not the frozen {data_version!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    geometries: dict[str, GeoJsonLineString | OsrmProviderError] = {}
    builder = CandidateGraphBuilder(client)
    for key in selected_keys:
        origin, destination = route_endpoints(routes[key.route_id])
        if key.route_id not in geometries:
            try:
                geometries[key.route_id] = client.route_geometry(origin, destination)
            except OsrmProviderError as error:
                geometries[key.route_id] = error
        geometry = geometries[key.route_id]
        document: dict[str, Any]
        if isinstance(geometry, OsrmProviderError):
            document = _failure_document(key, geometry)
        else:
            selection = select_corridor_candidates(
                route_geometry=geometry,
                options=options,
                config=CandidateSelectionConfig(
                    corridor_width_km=float(key.corridor_width_km),
                    candidate_cap=key.candidate_cap,
                ),
            )
            stations = [candidate.option.station for candidate in selection.candidates]
            nodes = (
                CandidateNode("origin", origin),
                *(
                    CandidateNode(station.id, Coordinate(station.longitude, station.latitude))
                    for station in stations
                ),
                CandidateNode("destination", destination),
            )
            try:
                graph = builder.build(nodes)
            except OsrmProviderError as error:
                document = _failure_document(key, error)
            else:
                document = {
                    "schema_version": GRAPH_SCHEMA_VERSION,
                    "route_id": key.route_id,
                    "corridor_width_km": key.corridor_width_km,
                    "candidate_cap": key.candidate_cap,
                    "outcome": "graph",
                    "corridor_algorithm": corridor_algorithm,
                    "data_version": data_version,
                    "snapshot_id": frozen["station_snapshot"]["snapshot_id"],
                    "eligible_count": selection.eligible_count,
                    "stations": [
                        {
                            "id": station.id,
                            "name": station.name,
                            "latitude": station.latitude,
                            "longitude": station.longitude,
                            "max_power_kw": station.max_power_kw,
                            "connector_type": station.connector_type,
                        }
                        for station in stations
                    ],
                    "legs": [
                        [leg.origin_id, leg.destination_id, leg.distance_km, leg.duration_minutes]
                        for leg in graph.legs
                    ],
                }
        (output_dir / key.file_name).write_bytes(_graph_bytes(document))
    return selected_keys


def load_stored_graph(path: Path) -> StoredGraph:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["schema_version"] != GRAPH_SCHEMA_VERSION:
        raise ValueError(f"unsupported stored graph schema: {document['schema_version']}")
    key = GraphKey(document["route_id"], document["corridor_width_km"], document["candidate_cap"])
    if document["outcome"] != "graph":
        return StoredGraph(key, document["outcome"], 0, {}, ())
    stations = {row["id"]: Station(**row) for row in document["stations"]}
    legs = tuple(
        RoadLeg(origin_id=origin, destination_id=destination, distance_km=km, duration_minutes=mins)
        for origin, destination, km, mins in document["legs"]
    )
    return StoredGraph(key, "graph", document["eligible_count"], stations, legs)


def load_verified_graph(path: Path, *, expected_sha256: str) -> StoredGraph:
    """Load a stored graph whose bytes match a published checksum and that holds a graph."""

    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError(f"graph checksum mismatch for {path.name}")
    graph = load_stored_graph(path)
    if graph.outcome != "graph":
        raise ValueError(f"missing graph for {path.name}")
    return graph


def load_frozen_options(
    *,
    snapshot_path: Path,
    station_manifest_path: Path,
    frozen_snapshot: Mapping[str, Any],
) -> tuple[CompatibleStationOption, ...]:
    """Load the checksum-pinned snapshot and project it to CCS2 DC options, or fail closed."""

    provenance, options = load_snapshot_options(
        snapshot_path, station_manifest_path, frozen_sha256=frozen_snapshot["sha256"]
    )
    if provenance.snapshot_id != frozen_snapshot["snapshot_id"]:
        raise ValueError("station manifest does not describe the frozen snapshot")
    return options


def measure_osrm_runtime(
    *,
    manifest: Mapping[str, Any],
    client: CollectionClient,
    graphs_dir: Path,
) -> dict[str, Any]:
    """Time the E1 provider requests apart from the planner, under the declared E6 protocol."""

    protocol = manifest["experiments"]["E6"]
    experiment = manifest["experiments"]["E1"]
    routes = {route["id"]: route for route in manifest["routes"]}
    requests: list[tuple[Coordinate, Coordinate, tuple[Coordinate, ...]]] = []
    for route_id in experiment["routes"]:
        key = GraphKey(route_id, experiment["corridor_width_km"], experiment["candidate_cap"])
        graph = load_stored_graph(graphs_dir / key.file_name)
        if graph.outcome != "graph":
            continue
        origin, destination = route_endpoints(routes[route_id])
        stations = tuple(
            Coordinate(station.longitude, station.latitude) for station in graph.stations.values()
        )
        requests.append((origin, destination, (origin, *stations, destination)))

    def run_route_pass() -> None:
        for origin, destination, _ in requests:
            client.route_geometry(origin, destination)

    def run_table_pass() -> None:
        for _, _, coordinates in requests:
            client.table(coordinates)

    results: dict[str, Any] = {"requests_per_pass": len(requests)}
    for name, run_pass in (("route", run_route_pass), ("table", run_table_pass)):
        for _ in range(protocol["warmups"]):
            run_pass()
        samples: list[float] = []
        for _ in range(protocol["repetitions"]):
            started = time.perf_counter_ns()
            run_pass()
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
        results[name] = {
            "median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(_nearest_rank_p95(samples), 3),
            "samples_ms": [round(sample, 3) for sample in samples],
        }
    return results
