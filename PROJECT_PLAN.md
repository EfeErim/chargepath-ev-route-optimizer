# Project Plan

## Objective

Build an evidence-backed GitHub portfolio project that demonstrates road-data
integration, energy modeling, and constrained route optimization for one electric
vehicle trip.

## Scope contract

### In scope

- one passenger EV, one origin, one destination;
- provider-computed road legs from a pinned OSRM/OpenStreetMap graph and profile;
- static, versioned charging-station data;
- CCS2 DC connector and power compatibility for the first release;
- discrete-SOC route and partial-charging optimization;
- deduplicated route options for fastest time, shortest distance, fewest charging sessions, and a
  deterministic fixed-target greedy strategy;
- deterministic baselines, independent plan replay, tests, and benchmark evidence;
- a local, fixture-backed web demo with an explainable plan;
- an explicitly enabled integration mode for a user-configured OSRM endpoint and snapshot.

### Out of scope

- fleet dispatch and vehicle-routing problems;
- AC trip-charging optimization or non-CCS2 connectors in the first release;
- proprietary turn-by-turn navigation;
- accounts, payments, reservations, or vehicle login;
- guaranteed/live station status;
- commercial-scale hosting or service-level objectives;
- claims of real-world safety without field calibration.

## Evidence rules

- A milestone is complete only after every named acceptance check is recorded in
  `PROJECT_STATE.md` with its command, date, and result.
- Generated benchmark claims require a machine-readable artifact, fixture/version identifiers,
  and the command that produced it.
- Live integration observations never replace deterministic fixtures or offline unit tests.
- A public API response may be inspected, but no real dataset is committed until provenance and
  reuse status are documented.

## Milestones

### M0 — Research and foundation

Deliverables:

- research review and source registry;
- scope, architecture, data, algorithm, experiment, and limitation documents;
- typed domain model;
- deterministic energy and charging functions;
- state-space optimizer over supplied road legs;
- synthetic demo and unit tests.

Acceptance:

- `python -m pytest -q` passes;
- `python -m compileall -q src tests examples` passes;
- demo returns a feasible plan with at least one charging stop;
- infeasible and direct-route cases are tested;
- no network access is required for checks.

### M0.1 — Foundation hardening

Deliverables:

- one schema-versioned synthetic fixture used by both demo and tests;
- first-release CCS2 DC compatibility contract;
- independent route-plan replay verifier;
- validation for returned plan totals and action boundaries;
- exact-reserve, non-aligned-reserve, incompatible-connector, multi-stop, tamper, and invalid-provider
  tests;
- one repository check command that uses the project environment and includes test, compile, demo,
  lint, format, strict type, and dependency checks.

Acceptance:

- the bundled fixture loads through the production loader and is visibly marked synthetic;
- every returned drive leg replays without a reserve violation;
- an incompatible charger cannot make an otherwise infeasible route feasible;
- the canonical check command passes without any network access;
- the CI workflow invokes the same canonical gate for Windows and Ubuntu; successful remote runs
  remain an M5 release requirement.

### M0.2 — Competitive route options

Deliverables:

- one exact label-setting implementation with lexicographic fastest-time, shortest-distance, and
  fewest-charging-session preferences;
- a separate deterministic greedy algorithm that charges once toward a fixed 80% target;
- a route-option planner that independently verifies every returned plan, merges identical actionable
  plans under strategy aliases, and reports strategies that could not produce a plan;
- a synthetic demo that displays the distinct available options rather than only the fastest plan.

Acceptance:

- the default `EVRouteOptimizer` remains backward-compatible and time-optimal under its existing
  modeled objective;
- a controlled trade-off graph produces distinct fastest, shortest-distance, fewest-session, and
  greedy options;
- every returned option passes `verify_route_plan` and repeated option generation is deterministic;
- identical actionable plans are deduplicated without losing the strategies that selected them;
- a greedy failure remains visible when exact search succeeds, and all-strategy infeasibility raises
  `NoFeasibleRouteError` rather than returning a partial option set;
- the canonical repository gate passes without network access.

### M1 — OSRM road adapter

Deliverables:

- a coordinate value object with an explicit WGS84 `longitude,latitude` serialization contract;
- separate route/table client protocol and candidate-graph builder;
- table parsing for fastest-route duration and distance, including unit conversion;
- route parsing into the typed `GeoJsonLineString` contract for the selected plan;
- recorded request/response fixtures and a fixture manifest containing OSRM API version, profile,
  request options, data version when supplied, retrieval time, and checksums;
- local/self-hosted OSRM setup notes with the tested backend release pinned independently from the
  HTTP protocol path (`v1`).

Acceptance:

- unit tests make no live network calls;
- request tests cover coordinate order, URL encoding, profile, timeout, and explicit endpoint
  configuration;
- response tests cover meters-to-kilometers, seconds-to-minutes, asymmetric matrices, and GeoJSON
  geometry;
- `NoSegment`, `NoRoute`, `TooBig`, `null` matrix cells, non-`Ok` payloads, malformed JSON, and timeout
  paths remain explicit provider errors or unreachable legs;
- selected-plan geometry is fetched only after optimization and matches the chosen leg endpoints;
- every recorded fixture identifies the exact OSRM backend release used to create it;
- the default endpoint remains loopback; any public demo endpoint requires explicit opt-in and is not
  described as a production dependency.

### M2 — EPDK station data

Deliverables:

- access, request, schema, quota, reuse, and freshness spike before ingestion work;
- raw snapshot manifest with request fingerprint, retrieval timestamp, response checksum, record
  count, and reuse decision;
- normalized site/socket schema with stable source identifiers;
- CCS2 DC compatibility projection used by the optimizer;
- validation report for coordinates, identifiers, duplicates, connectors, socket power, and rejected
  records;
- deterministic route-corridor candidate selection with an explicit candidate cap.

Acceptance:

- a source response is reproducibly retrieved, or the access blocker is documented with request,
  response status, and date;
- response/schema assumptions are fixture-tested because the current public Swagger does not declare
  a response model;
- at least 500 source rows load when access permits, but row count alone is not a quality gate;
- every normalized site and socket retains source identifier, snapshot identifier, and provenance;
- the validation report reconciles input, accepted, rejected, and duplicate counts;
- invalid coordinates and socket records are reported rather than silently accepted;
- no real snapshot is committed while redistribution status is unresolved;
- corridor width, candidate cap, ranking keys, and final stable-identifier tie-break are explicit
  manifest or configuration fields and reproduce the same candidate set;
- status, price, and availability fields are excluded from planning unless their freshness semantics
  are separately proven; the first release still presents station data as static;
- bundled samples remain small, synthetic or explicitly license-safe.

### M3 — Planner evaluation

Deliverables:

- deterministic shortest-driving-time and greedy fixed-target charging baselines;
- benchmark comparison of the exact fastest-time, shortest-distance, fewest-session, and greedy route
  strategies without assuming they produce distinct plans on every case;
- a versioned benchmark manifest covering every declared topology and boundary class;
- independent plan replay plus small hand-audited feasible/infeasible reference cases;
- generated correctness, comparison, SOC-grid sensitivity, and runtime artifacts;
- a candidate-pruning audit against the unpruned candidate graph on small fixtures.

Acceptance:

- the benchmark manifest defines every input, expected-feasibility source, algorithm version, and
  fixture checksum;
- baseline station ordering and tie-breaking are fully specified and repeatable;
- every returned plan passes independent replay with zero reserve violations;
- infeasible cases never return partial plans;
- each declared topology and boundary class has at least one fixture, and one-factor sensitivity cases
  change only the named variable;
- repeated normalized result serialization is byte-for-byte stable;
- runtime evidence declares warm-up count, measured repetitions, machine, Python version, and reports
  median and p95 rather than a single run;
- README claims match generated tables even when a hypothesis is not supported.

### M4 — Local map demo

Deliverables:

- a loopback-only local application with Leaflet origin/destination selection;
- fixture mode as the default reproducible primary flow;
- versioned synthetic GeoJSON geometry for every leg used by the primary fixture flow;
- an explicitly selected integration mode for configured OSRM and station snapshot inputs;
- vehicle inputs, plan action, route line, charging stops, SOC timeline, and limitation labels;
- selectable route options with their strategy aliases and explicit unavailable-strategy state;
- validation, loading, failure, empty, and unavailable-basemap states.

Acceptance:

- one documented command starts the fixture demo and the primary route-planning flow does not require
  OSRM or EPDK network access;
- tile failure does not prevent the fixture route and explanation from being inspected;
- the server binds to loopback by default and validates bounded user inputs;
- real static station data is visibly labelled with snapshot freshness; fixture mode instead shows
  `Synthetic fixture — freshness not applicable`; modeled SOC and charging time are labelled as
  estimates in both modes;
- OpenStreetMap attribution is visible, the tile URL is configurable, and the app performs no tile
  prefetch or bulk download;
- public Nominatim autocomplete is not used; any submit-only search obeys identification, caching,
  attribution, and rate limits;
- a screenshot or short GIF of the verified primary flow is captured.

### M5 — Portfolio release

Deliverables:

- clean-install verification on the documented supported platforms;
- green Windows and Ubuntu CI evidence;
- final architecture, benchmark, and demo evidence;
- natural README and demo media;
- license decision and code/data attribution audit;
- secret, cache, generated-output, and large-file audit;
- exact release dependency and tool versions recorded with the evidence.

Acceptance:

- all README commands pass in fresh environments on the supported platforms;
- CI runs the same canonical repository gate used locally;
- repository contains no keys, large extracts, caches, unreviewed generated artifacts, or unlicensed
  data;
- code license, OSM attribution, fixture provenance, and third-party dependency licenses are visible;
- limitations and readiness boundary are visible;
- Git status and release contents are reviewed against an explicit allowlist;
- commit, push, tag, and GitHub release happen only after explicit approval.
