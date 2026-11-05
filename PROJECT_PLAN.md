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

### M6 — Real-route evaluation

Status: planned on 2026-11-05; M6.0, M6.1, and M6.2 complete on 2026-11-05 (evidence in
`PROJECT_STATE.md`); M6.3 not started.

M3 validates the software on eight small synthetic cases. M6 measures what the planner does on real
Turkish road legs and real static station locations, on inputs frozen before the first result exists.

Decision question: under one frozen road graph and station snapshot, how much modeled trip time does
exact optimization save over the fixed-80 greedy strategy, and how much do that result and feasibility
depend on the SOC grid, the candidate cap, and the vehicle inputs?

M6 measures the model on real geography. It does not establish real-world SOC accuracy, arrival time,
or charger availability, because no trip is driven and no live data is used.

#### Boundaries

- No optimizer, energy-model, or charging-model change. A defect found during M6 is fixed separately,
  and the affected results are regenerated.
- No elevation, weather, traffic, AC charging, or new vehicle catalogue.
- The public OSRM server is not used for M6 evidence: its graph is not pinned, its use has no quota
  guarantee, and it cannot serve the full candidate range.
- Real station rows stay out of Git while EPDK reuse status is unresolved. Committed evidence contains
  metrics, identifiers of frozen inputs, and checksums, but no station names, coordinates, source
  identifiers, or per-trip station sequences.

#### M6.0 — Infrastructure spike

Deliverables:

- local OSRM from the same native `node_osrm-v26.7.3-8-win32-x64-Release` asset, checksum, and `car.lua`
  pinned by the M1 fixture, so no container runtime is required;
- one dated Geofabrik Turkey extract (652,637,317 bytes when checked on 2026-11-05) outside Git, with
  its SHA-256 and OSRM `data_version` recorded;
- one EPDK snapshot retrieved once with `scripts/fetch_epdk_snapshot.py`, then frozen for all of M6;
- `docs/osrm_setup.md` updated with the verified native commands, including peak memory, disk use, and
  processing time on the recording machine.

Acceptance:

- the extract is processed on the recording machine (15.8 GB RAM), or the failure and its peak memory
  are recorded and M6 stops for a decision before any fallback is chosen;
- `osrm-routed` answers Route and Table requests on loopback only;
- a real `--max-table-size 100` server returns `TooBig` above 100 locations and ChargePath handles it as
  v0.1.1 claims, closing that unverified item;
- `scripts/fetch_epdk_snapshot.py` completes one live download, closing that unverified item; an HTTP
  429 is waited out rather than retried in a loop;
- one browser plan runs through local OSRM and the frozen snapshot, recorded with its result.

#### M6.1 — Frozen benchmark manifest

Deliverables:

- `data/benchmarks/m6/v1/manifest.json`, committed before the first evaluation run;
- 24 origin/destination pairs using general province-center coordinates, never a person's location: six
  each in the 150–300, 300–600, 600–1000, and over 1000 km bands, with the selection rule recorded and
  the eastern, lower-density regions represented;
- three declared parameter sets of 40 kWh/50 kW/16, 60 kWh/150 kW/18, and 80 kWh/250 kW/20 kWh per
  100 km, all at 80% initial SOC, 10% reserve, and a 1.1 safety factor. These are parameter sets, not
  claims about any real car;
- the experiment list below, with every value and the route subset fixed.

Acceptance:

- the manifest commit precedes every result commit in Git history;
- routes are not added, removed, or replaced after the first run; a snap, routing, or infeasibility
  failure is a reported outcome, and any correction creates a new manifest version with the old results
  kept.

#### M6.2 — Collection and evaluation tooling

Deliverables:

- a collection stage that uses the existing corridor selector and `CandidateGraphBuilder` against local
  OSRM, and stores each candidate graph under the Git-ignored `data/processed/m6/`;
- an offline evaluation stage that needs no network, runs every strategy and the road-only baseline
  from those graphs, independently replays every plan, and writes normalized evidence;
- a synthetic graph fixture and fake provider, so the repository gate covers both stages with no OSRM,
  EPDK, or network access.

Acceptance:

- repeated evaluation from the same stored graphs is byte-for-byte stable;
- stored graph checksums are recorded in the committed evidence;
- a test proves committed evidence contains no station name, coordinate, or source identifier;
- input and outcome counts reconcile, and each outcome is a plan, an infeasible result, or a provider
  failure, with none dropped.

#### M6.3 — Experiments

| Experiment | Changes | Held fixed | Trips |
|---|---|---|---|
| E1 Strategy comparison | exact strategies, greedy, road-only | 5% grid, 25 km, cap 50 | 24 routes × 3 sets |
| E2 SOC grid | 10%, 5%, 2% | 25 km, cap 50 | 24 routes × 3 sets |
| E3 Candidate cap | 12, 24, 50, 98, 200 | 5% grid, 25 km, 60 kWh set | 24 routes |
| E4 Corridor width | 10, 25, 50 km | 5% grid, cap 50, 60 kWh set | 24 routes |
| E5 Vehicle inputs | one factor at a time, below | 5% grid, 25 km, cap 50 | 8 declared routes |
| E6 Runtime | planner and OSRM timed separately | E1 inputs | 3 warm-ups, 20 repetitions |

E5 varies usable battery (40/60/80 kWh), initial SOC (50/80/100%), consumption (±20%), reserve
(5/10/20%), safety factor (1.0/1.1/1.25), and vehicle maximum DC power (50/150/250 kW). It covers the
dimensions promised in `docs/experiment_plan.md`, replacing the fixed station-power dimension because
real stations supply their own power. E3 at cap 200 requires a server started with
`--max-table-size 202`; its result is the reference for the cap gap and is not a global optimum.

Reported metrics: feasibility rate, valid-plan rate, reserve violations, total and charging time, stops,
detour distance, distinct options, greedy failure rate, SOC-grid gap, and cap gap. Exact-versus-greedy
savings are reported in minutes and percent as median, p90, minimum, and maximum, overall and per
distance band, over trips where both strategies return a plan.

#### M6.4 — Results and publication

Deliverables:

- `docs/evidence/m6/` with normalized results, the machine-specific runtime artifact, and frozen-input
  checksums;
- a real-route section in `docs/results.md` generated from those artifacts, and README claims that
  match them, including results that are unflattering or show no meaningful gain;
- `docs/experiment_plan.md`, `docs/limitations.md`, and the release allowlist updated;
- a release candidate, published only after explicit approval.

Acceptance:

- every returned plan passes independent replay with zero reserve violations;
- on every trip where both return a plan at the same grid and candidate set, exact fastest is no slower
  than greedy; any violation is treated as a defect, not reported as a result;
- no savings threshold is required for M6 to pass, and the measured result is published as found;
- results state their boundary: modeled minutes on one OSM extract, one static snapshot, and declared
  parameter sets, with visible OpenStreetMap attribution;
- anyone without the frozen snapshot is told they can rerun the tooling on their own snapshot but cannot
  reproduce the exact bytes;
- every named check is recorded in `PROJECT_STATE.md`, and the canonical gate passes without network
  access.

#### Known risks

- **Memory:** processing the extract may exceed what the recording machine has free. M6.0 measures this
  first, before any later work depends on it.
- **EPDK access:** the quota is undocumented and HTTP 429 has been observed. M6 needs only one successful
  download, and the frozen snapshot removes any further dependency.
- **Road-graph meaning:** OSRM Table distance belongs to the fastest route, so "shortest distance" means
  shortest among those legs. Results must say so.
- **Reproducibility:** real inputs cannot be redistributed, so outsiders can inspect method and metrics
  but cannot verify the exact numbers. Synthetic tests cover the tooling, not the real values.
- **Reuse conditions:** publishing aggregate metrics derived from EPDK data is treated as lower risk than
  publishing rows, but it is not confirmed permission. Asking EPDK remains open and does not block
  M6.0–M6.3; its answer should be reviewed before M6.4 if one has arrived.
