# Data Dictionary

## VehicleProfile

| Field | Type | Unit | Meaning |
|---|---|---:|---|
| `name` | string | — | Human-readable profile name. |
| `usable_battery_kwh` | float | kWh | Energy available to the trip model. |
| `initial_soc_pct` | float | % | Battery SOC at trip start. |
| `consumption_kwh_per_100km` | float | kWh/100 km | Baseline road-energy rate. |
| `max_dc_power_kw` | float | kW | Vehicle-side DC acceptance cap. |
| `reserve_soc_pct` | float | % | Minimum allowed SOC after every leg. |
| `energy_safety_factor` | float | multiplier | Conservative adjustment applied to baseline energy. |
| `supported_dc_connectors` | list[string] | — | Canonical DC connectors accepted by the vehicle; first-release fixture uses `CCS2`. |

## Station

| Field | Type | Unit | Meaning |
|---|---|---:|---|
| `id` | string | — | Stable planner identifier. |
| `name` | string | — | Display name. |
| `latitude` | float | degrees | WGS84 latitude. |
| `longitude` | float | degrees | WGS84 longitude. |
| `max_power_kw` | float | kW | Socket/station-option DC power cap used by the model. |
| `connector_type` | string | — | Canonical connector checked against the vehicle, initially `CCS2`. |

## RoadLeg

| Field | Type | Unit | Meaning |
|---|---|---:|---|
| `origin_id` | string | — | Start node. |
| `destination_id` | string | — | End node. |
| `distance_km` | float | km | Road distance computed by the configured provider. |
| `duration_minutes` | float | min | Provider travel-time estimate or fixture value. |
| `geometry` | GeoJsonLineString/null | — | Optional typed GeoJSON route geometry. |

## GeoJsonLineString

| Field | Type | Meaning |
|---|---|---|
| `type` | literal `LineString` | GeoJSON geometry discriminator. |
| `coordinates` | list[[float, float]] | At least two WGS84 positions in explicit `longitude,latitude` order. |

## ChargingStop

| Field | Type | Unit | Meaning |
|---|---|---:|---|
| `station_id` | string | — | Selected charging station. |
| `arrival_soc_pct` | float | % | Discrete SOC before charging. |
| `departure_soc_pct` | float | % | Discrete SOC after charging. |
| `energy_added_kwh` | float | kWh | Modeled energy added. |
| `charging_minutes` | float | min | Setup plus piecewise charge duration. |

## RoutePlan

| Field | Type | Unit | Meaning |
|---|---|---:|---|
| `node_ids` | list[string] | — | Origin, selected stops, and destination in drive order. |
| `legs` | list[RoadLeg] | — | Driven road legs. |
| `charging_stops` | list[ChargingStop] | — | Charging decisions in chronological order. |
| `total_distance_km` | float | km | Sum of road-leg distances. |
| `driving_minutes` | float | min | Sum of road-leg durations. |
| `charging_minutes` | float | min | Sum of charge-action durations. |
| `arrival_soc_pct` | float | % | Discrete destination SOC. |
| `total_minutes` | float | min | Driving plus charging time. |

## RouteOption

| Field | Type | Meaning |
|---|---|---|
| `strategies` | list[RouteStrategy] | One or more algorithms/objective strategies that selected this exact actionable plan. |
| `plan` | RoutePlan | Independently verified route and charging actions. |

`RouteStrategy` has four stable values: `fastest`, `shortest_distance`,
`fewest_charging_stops`, and `greedy_fixed_80`.

## RouteOptionSet

| Field | Type | Meaning |
|---|---|---|
| `options` | list[RouteOption] | Unique feasible plans in stable strategy order. |
| `unavailable_strategies` | list[RouteStrategy] | Strategies that failed to produce a plan; not equivalent to whole-trip infeasibility. |

## SyntheticScenario fixture

| Field | Type | Meaning |
|---|---|---|
| `metadata.schema_version` | integer | Loader contract version; currently `2`. |
| `metadata.synthetic` | boolean | Must be literal `true`; otherwise the bundled loader rejects the file. |
| `trip.origin_id` | string | Synthetic trip start node. |
| `trip.destination_id` | string | Synthetic trip destination node. |
| `vehicle` | VehicleProfile object | Complete vehicle input including name and supported connector list. |
| `stations` | list[Station] | Complete synthetic station inputs including coordinates and connector type. |
| `legs` | list[RoadLeg] | Directed synthetic road graph; schema version 2 requires validated geometry on every leg. |

## M2 station-data contracts

### SnapshotProvenance

Every normalized site and socket carries the snapshot identifier, official source name/URL, UTC
retrieval time, response SHA-256, reuse status, and source freshness. Unknown source freshness remains
`null`; it is never replaced with retrieval time.

### NormalizedSite

One row holds the source site identifier, name, operator, WGS84 coordinates, normalized
`public`/`private` access classification, and full `SnapshotProvenance`.

### NormalizedSocket

One row holds the source socket identifier, parent source site identifier, `AC`/`DC` classification,
canonical `TYPE2`/`CCS2`/`CHADEMO` connector, positive kW power, and full provenance. Its stable
identity is the site/socket source-identifier pair.

### CompatibleStationOption

Only public CCS2 DC sockets are projected. One optimizer `Station` is emitted per site with the
maximum compatible socket power; the option retains the source site identifier, all contributing
socket identifiers, and provenance. Raw status, price, reservation, and availability are absent.

### CandidateSelectionConfig

The default uses `equirectangular-segment-farthest-progress-v3`, a 25 km corridor, and a 50-station
candidate cap. It builds a prefix-monotonic ordering: each added candidate closes the largest route-
progress gap, while gaps within two percent of route progress are resolved by corridor distance,
descending power, progress, and stable station identifier. Raising the cap therefore retains every
previously selected candidate. The cap limits OSRM Table size and the distinct station nodes available
to a plan; it is not a separate optimizer stop-count rule. Legacy v1/v2 remain readable for frozen or
historical evidence.

## M3 benchmark contracts

### Benchmark manifest

The version-1 manifest records the benchmark identifier, algorithm and result-serialization versions,
fixture path/schema/SHA-256, ordered algorithms, baseline tie-break contract, every case's topology,
SOC step, expected-feasibility source, one-factor sensitivity runs, and small-graph pruning audits.

### Correctness artifact

The deterministic artifact records one normalized feasible or infeasible outcome per case/algorithm,
independent replay status, plan metrics, strategy aliases after deduplication, correctness totals,
10/5/2% SOC-grid sensitivity, and pruned/unpruned comparison. It contains no timestamp or runtime
sample, so repeated serialization is byte-for-byte stable.

### Runtime artifact

The machine-specific artifact records the manifest and fixture hashes, warm-up count, measured
repetitions, monotonic clock, command, UTC timestamp, Python/platform/CPU metadata, and every measured
sample plus median and nearest-rank p95 for each algorithm and the composed option planner.
