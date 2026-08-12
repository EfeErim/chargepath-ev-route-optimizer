# Project State

Last updated: 2026-08-12

## Current milestone

M0 — Research and foundation: **complete**

M0.1 — Foundation hardening: **complete**

M0.2 — Competitive route options: **complete**

M1 — OSRM road adapter: **complete**

M2 — EPDK station data: **complete**

M3 — Planner evaluation: **complete**

M4 — Local map demo: **complete**

M5 — Portfolio release: **release candidate locally complete; remote CI and publication approval pending**

## Confirmed decisions

- Repository: `D:\kişisel projeler\03-ev-route-optimizer`
- Product: local portfolio project, not a commercial navigation service.
- Problem class: single-EV resource-constrained shortest path with charging nodes.
- Road boundary: OSRM supplies road-network legs; ChargePath selects charging sequence and amount.
- First-release charging boundary: compatible CCS2 DC charging only.
- Data boundary: bundled sample is synthetic and schema-versioned; real EPDK data requires provenance,
  quality, freshness, and reuse validation.
- Runtime boundary: core checks and the default future fixture flow are offline and deterministic.
- Solver boundary: exact Dijkstra label-setting remains authoritative within the supplied candidate
  graph and discrete SOC model. It exposes fastest-time, shortest-distance, and fewest-session
  preferences alongside a separate greedy fixed-80 competitor; OR-Tools is not required for the
  single-trip first release.

## M0 through M0.2 evidence

Verified on 2026-08-05 with Python 3.11.9:

- `.\scripts\check.ps1`: 27 tests passed, byte-compilation passed, and the offline demo completed.
- `ruff 0.16.1`: lint passed and 28 files were already formatted.
- `mypy 1.20.2 --strict`: no issues in 15 source files.
- `pip check`: no broken requirements.
- Demo result: three distinct verified options. Fastest/fewest-session uses
  `origin -> fast_hub -> destination` at 205.2 modeled minutes; shortest-distance uses
  `origin -> slow_hub -> destination` at 280 km; greedy fixed-80 uses the fast corridor at
  206.4 modeled minutes and 15% arrival SOC.
- The demo and fixture tests load the same schema-version-2
  `data/sample/synthetic_corridor.json`; values are no longer duplicated in demo code.
- Every plan returned by a successful optimizer test invocation passes through the shared independent
  replay helper. Replay verifies charging compatibility, charge energy/time, every driven leg,
  reserve, and arrival SOC; the demo is replayed independently as well.
- `CompetitiveRoutePlanner` independently replays every candidate before exposing it, deduplicates
  identical actionable plans under strategy aliases, and reports unavailable strategies separately.
- A controlled trade-off graph produces four distinct verified strategy results; tests also cover
  deterministic repetition, direct-plan deduplication, greedy-only failure, and all-strategy
  infeasibility.
- Every bundled synthetic road leg carries validated GeoJSON LineString geometry for the future
  offline map flow.
- Boundary coverage includes exact reserve, non-aligned reserve, incompatible connector, multi-stop,
  tampered result, unlabeled fixture, duplicate station, invalid provider output, competitive
  objective separation, option deduplication, and heuristic failure.
- The canonical verification path requires no network access.
- `.github/workflows/ci.yml` defines the same canonical gate for Windows and Ubuntu; it has not run on
  GitHub because no commit or remote publication was authorized.

## Next milestone

M5 finalization — configure an approved remote, push the exact allowlisted candidate, obtain green
`windows-2025` and `ubuntu-24.04` CI results, record the immutable commit/run evidence, and only then
mark M5 complete. Tagging and a GitHub release remain separately approval-gated.

## M1 evidence

Verified on 2026-08-09 with Python 3.11.9.

Commands and results:

- `.\.venv\Scripts\python.exe -m pytest -q tests\test_osrm.py`: 15 passed in 0.08 seconds; every
  test uses an injected offline transport or in-memory protocol stub.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1`: 42 tests passed,
  byte-compilation passed, the offline synthetic demo completed, Ruff lint passed, all 31 files were
  formatted, strict mypy reported no issues in 17 source files, `pip check` found no broken
  requirements, and the canonical gate printed `All project checks passed.`
- Direct `.\scripts\check.ps1` invocation was blocked before execution by this machine's PowerShell
  script policy; `-ExecutionPolicy Bypass -File` ran the unchanged repository script.

Named M1 acceptance evidence:

- **Offline unit boundary — passed.** `tests/test_osrm.py` injects `FakeTransport`,
  `TimeoutTransport`, and protocol stubs. Neither the targeted command nor the canonical gate needs
  an OSRM process or internet access.
- **Request contract — passed.** Tests assert WGS84 `longitude,latitude` serialization, encoded
  profile and query values, profile selection, exact timeout forwarding, explicit endpoint
  configuration, loopback default `http://127.0.0.1:5000`, and remote-endpoint opt-in.
- **Response and unit conversion — passed.** Tests parse asymmetric Table matrices, convert meters to
  kilometers and seconds to minutes, preserve `data_version`, treat `null` cells as unreachable, and
  construct typed `GeoJsonLineString` geometry from Route output.
- **Provider errors — passed.** `NoSegment`, `NoRoute`, `TooBig`, `NoTable`, generic non-`Ok` shapes,
  malformed JSON, invalid response shapes, HTTP failures, and timeouts remain explicit OSRM provider
  errors; unreachable matrix cells never become zero-cost legs.
- **Selected geometry boundary — passed.** A real optimizer test first builds the candidate graph and
  selects `origin -> hub -> destination` without any Route request. Geometry is then requested for
  only those two selected directed endpoint pairs, while Table-derived plan costs remain unchanged.
- **Recorded fixture provenance — passed.** `tests/fixtures/osrm/v26.7.3/manifest.json` records
  request targets and exact responses produced on a project-authored synthetic OSM ring by backend
  `v26.7.3` (`0844e3af77896d11998ef6db356a553056652c8e`), `car.lua`, CH, and HTTP `v1`. It includes the
  backend asset/profile hashes, supplied data version `synthetic-m1-2026-08-09`, retrieval time, and
  SHA-256 checksums for every recorded file; tests recalculate every checksum.
- **Local/self-hosted boundary — passed.** `docs/osrm_setup.md` independently pins backend `v26.7.3`
  from protocol path `v1`, documents a loopback-only Docker bind and adapter configuration, keeps
  `.osm.pbf`/`.osrm*` artifacts outside Git, and states that a public demo endpoint requires explicit
  opt-in and is not a production dependency.

## M2 evidence

Verified on 2026-08-09 with Python 3.11.9.

Commands and results:

- Official access request: `curl.exe -X GET -H "Accept: application/json" -H
  "Content-Type: application/json" --data "{}" -o
  data/raw/epdk/2026-08-09/response.json
  https://apigateway.epdk.gov.tr/sarjIstasyonlari` returned HTTP 200 at
  `2026-08-09T14:13:33Z`, `application/json;charset=utf-8`, 15,444,078 bytes, and SHA-256
  `2850e5e2082751ffcc955a11ad217671c98b292ca665f87d2966fb42c90ae005`.
- `.\.venv\Scripts\python.exe -m pytest -q tests\test_station_data.py`: 10 passed in 0.08
  seconds with no network access.
- `.\.venv\Scripts\python.exe scripts\inspect_epdk_snapshot.py
  data\raw\epdk\2026-08-09\response.json --retrieved-at 2026-08-09T14:13:33Z
  --response-status 200 --manifest-out data\processed\epdk\2026-08-09\manifest.json
  --report-out data\processed\epdk\2026-08-09\validation_report.json`: 16,539 sites,
  46,955 sockets, and 7,640 public compatible CCS2 DC sites; the same snapshot ID
  `epdk-20260809-2850e5e20827` and reconciliation were reproduced offline.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1`: 52 tests passed,
  byte-compilation and the offline synthetic demo passed, Ruff lint passed, all 37 Python files were
  formatted, strict mypy reported no issues in 20 source files, `pip check` found no broken
  requirements, and the canonical gate printed `All project checks passed.`
- `git check-ignore -v` matched the real response to `data/raw/` and generated manifest/report to
  `data/processed/`; `git ls-files data/raw data/processed` returned no tracked paths.

Named M2 acceptance evidence:

- **Access/request spike — passed with unresolved service policy documented.** The official Swagger
  and station endpoint both returned HTTP 200. The reproducible request is `GET` plus body `{}`. The
  Swagger still declares no 200 response model, pagination is not declared, and quota/retry behavior
  was not found; no automatic polling or retry loop was added.
- **Observed schema and fixture boundary — passed.** The checksum-pinned response exposed the exact
  station and `soketNo/soketTipi/soketTuru/soketGucu` socket fields recorded in
  `docs/epdk_access_spike.md`. A small project-authored synthetic fixture covers the same container
  shape. Tests fail closed on unreviewed root, station, or socket fields.
- **Source volume — passed.** The offline production normalizer loaded all 16,539 source rows, well
  above the conditional 500-row threshold. This count is recorded as snapshot evidence, not treated
  as a continuing quality or availability claim.
- **Provenance and manifest — passed.** Every accepted normalized site and socket retains its source
  identifier and immutable `SnapshotProvenance`, including snapshot ID, official source URL,
  retrieval time, checksum, unknown freshness, and pending reuse status. The committed metadata-only
  manifest records the canonical request fingerprint, response checksum/size, record/socket counts,
  transformations, and reconciliation; raw and normalized real rows remain ignored.
- **Validation reconciliation — passed.** The real checksum-pinned response reconciled 16,539 site
  inputs to 16,539 accepted/0 rejected/0 duplicates and 46,955 socket inputs to 46,955 accepted/0
  rejected/0 duplicates. The synthetic fixture independently exercises explicit invalid-coordinate,
  invalid-power, duplicate-site, duplicate-socket, and rejected-parent reporting while enforcing
  `input = accepted + rejected + duplicates` for both grains.
- **Static compatibility projection — passed.** Multiple sockets remain separate normalized records.
  Only public `DC_CCS` rows map to canonical CCS2 options; one optimizer station per site retains all
  contributing socket IDs and uses their maximum compatible power. AC, CHAdeMO, private sites,
  status, price, reservation, and availability are excluded from planning.
- **Deterministic corridor cap — passed.** The default configuration explicitly records
  `equirectangular-segment-v1`, 25 km width, 50 candidates, corridor distance, route progress,
  descending power, and final stable-station-ID tie-break. Tests reproduce the same capped IDs from
  reversed input and separately verify the final identifier tie.
- **Reuse/freshness boundary — passed.** Official public access was observed, but an explicit
  redistribution licence, source update timestamp, quota contract, and cross-snapshot deletion/update
  semantics were not found. The manifest therefore remains `pending_verification`, source freshness
  remains unknown, and only metadata/code plus synthetic fixtures are repository material.

## M3 evidence

Verified on 2026-08-09 with Python 3.11.9.

Commands and results:

- `.\.venv\Scripts\python.exe -m pytest -q tests\test_evaluation.py`: 10 passed in 0.23 seconds.
- `.\.venv\Scripts\python.exe scripts\run_m3_benchmark.py --warmups 3 --repetitions 20` generated
  the reviewed correctness/comparison/SOC-grid/pruning artifact and a separate machine-specific
  runtime artifact with no network access.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1`: 62 tests passed,
  byte-compilation and the offline synthetic demo passed, Ruff lint passed, all 41 Python files were
  formatted, strict mypy reported no issues in 23 source files, `pip check` found no broken
  requirements, and the canonical gate printed `All project checks passed.`

Evidence identity:

- Benchmark `chargepath-m3-synthetic-v1`; fixture SHA-256
  `b805fe43dc89d3129e1126d1d5529b9bf5058ab0b4e8a86362caeccd472b980d`; manifest SHA-256
  `8744707588e3afbd45d676678fd1b42c1da56c783ce72fed3266887c4457f9e7`.
- `docs/evidence/m3/correctness_v1.json`: SHA-256
  `db55067b92a5904624dcf9fe90c5861561cc092b0b7754673ee101ef849572b6`.
- `docs/evidence/m3/runtime_2026-08-09.json`: SHA-256
  `04bc47528c72c3facca037403f243e3234f7d7bc14752f64869d82ad73704386`.

Named M3 acceptance evidence:

- **Manifest and reference labels — passed.** The version-1 manifest pins one schema-versioned,
  explicitly synthetic fixture by SHA-256 and records every input, case SOC step, topology class,
  algorithm/result version, hand-audited witness or exhaustion reason, baseline tie-break contract,
  one-factor sensitivity run, and pruning-audit configuration. All eight required classes are
  present: direct feasible, one-stop, multi-stop, detour choice, connector incompatible,
  exact-reserve boundary, discretization-rejected, and infeasible.
- **Deterministic baselines — passed.** The road-only baseline minimizes summed driving duration then
  full lexicographic node sequence and never inserts charging. Greedy fixed-80 retains the documented
  remaining-duration/current-leg/stable-ID station order and fixed 80% target. A dedicated tie case
  verifies road-only ordering, while existing option tests retain greedy unavailable-state coverage.
- **Correctness and replay — passed.** The suite reconciled eight inputs to 40 algorithm/case outcomes.
  It returned 22 plans, all 22 passed independent replay, and recorded zero reserve violations and
  zero false-feasible results. Each exact objective solved all five feasible references and rejected
  all three infeasible references; infeasible outcomes carry only explicit errors, never partial
  plans.
- **Stable normalized evidence — passed.** Correctness output omits runtime/timestamp fields and
  serializes with sorted keys and normalized numeric precision. Two repeated in-memory executions are
  byte-for-byte identical, and a repository test regenerates the artifact and compares its exact
  bytes with the committed evidence file.
- **Comparison claim — passed with narrow support.** On the one-stop, multi-stop, and detour charging
  cases, exact fastest-time was 4.8 modeled minutes faster than greedy fixed-80 by charging less. The
  road-only baseline was infeasible on those cases, so no road-only time comparison is claimed. The
  detour fixture also separates fastest (280 km, 134.0 min) from shortest-distance (270 km, 175.8
  min). README and `docs/results.md` state the synthetic-only boundary.
- **SOC-grid sensitivity — passed.** The same checksum-pinned 140 km case changes only
  `soc_step_pct`: it is infeasible at 10% and 5% but feasible at 2%, arriving at the 12% reserve.
- **Candidate-pruning audit — passed within the declared small graph.** The actual corridor selector
  retained `fast_near` and removed `slow_far`; pruned and unpruned fastest-time searches returned the
  same independently verified 134.0-minute plan, for an observed gap of 0.0 minutes. The evidence and
  README explicitly avoid a general pruning-preservation claim.
- **Runtime protocol — passed.** `time.perf_counter_ns` measured 20 complete eight-case suite passes
  after three warm-ups on CPython 3.11.9, Windows AMD64, 12 logical CPUs. The artifact retains every
  sample plus median/nearest-rank p95. Median/p95 were 2.9196/2.9344 ms for exact fastest and
  9.1702/9.2808 ms for the composed competitive planner; these are regression measurements for one
  tiny synthetic suite, not production latency claims.

## M4 evidence

Verified on 2026-08-10 with Python 3.11.9 and the Codex in-app Chromium browser.

Commands and results:

- `.\.venv\Scripts\python.exe -m pytest -q tests\test_demo.py`: 7 passed in 1.40 seconds with
  no live provider calls. This includes the fixture service, bounded/exact request validation,
  loopback HTTP behavior, packaged UI contract, checksum failure boundary, and an offline-injected
  OSRM integration orchestration test.
- `.\.venv\Scripts\python.exe -m chargepath.demo`: the default command bound to
  `127.0.0.1:8743`; `GET /api/config` returned fixture mode, the declared synthetic endpoints,
  bounded vehicle inputs, the configurable tile template, and visible attribution metadata.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1`: 69 tests passed in
  1.85 seconds,
  byte-compilation and the offline synthetic CLI demo passed, Ruff lint passed, all 45 Python files
  were formatted, strict mypy reported no issues in 26 source files, `pip check` found no broken
  requirements, and the canonical gate printed `All project checks passed.`
- Browser primary flow on explicit test port `http://127.0.0.1:8765`: three option tabs were
  selectable. Fastest/fewest
  charging used `origin -> fast_hub -> destination` at 205.2 modeled minutes; shortest-distance used
  `origin -> slow_hub -> destination` at 280 km and 239.4 modeled minutes; greedy fixed-80 used the
  fast corridor at 206.4 modeled minutes and 15% arrival SOC.
- Browser unavailable-basemap flow used an intentionally unreachable HTTPS tile template. The
  visible `Basemap unavailable` state retained four rendered route-path elements, all three
  endpoint/stop markers, selectable options, charging detail, and the replay-verified SOC timeline.
- `docs/evidence/m4/fixture-primary.png`: 90,455 bytes; SHA-256
  `c69518c007d1b111701871828821ca3d457154f5515e351e1c1fa8933ff0e534`.

Named M4 acceptance evidence:

- **Fixture-first command — passed.** `python -m chargepath.demo` starts the documented default flow
  at `http://127.0.0.1:8743`. Planning, charging decisions, SOC replay, and versioned synthetic
  GeoJSON come from `data/sample/synthetic_corridor.json` without OSRM or EPDK requests.
- **Loopback and input boundary — passed.** The server has no external-bind argument and constructs
  `ThreadingHTTPServer` on `127.0.0.1` only. Tests assert the resolved address. The API caps JSON at
  32 KiB, rejects unknown/missing fields and non-JSON content, validates finite coordinate/vehicle
  bounds, and prevents initial SOC below reserve SOC.
- **Leaflet selection and explanation — passed.** The default map exposes the two declared fixture
  endpoints for explicit marker selection; integration mode accepts arbitrary clicked endpoints.
  The browser verified route lines, charging markers, option switching, strategy aliases, drive and
  charging breakdowns, estimated SOC timeline, and independently replay-verified option badges.
- **Static and estimate truth labels — passed.** Fixture mode visibly states
  `Sentetik demo verisi · canlı değil`. Explicit integration mode exposes snapshot ID,
  retrieval timestamp, and source freshness from the checksum-matched manifest. Both modes label SOC
  and charging times as estimates and state that static rows do not imply live availability, price,
  or reservation.
- **Explicit integration mode — passed offline and live against the opted-in public demo.**
  `--mode integration`
  requires both `--station-snapshot` and `--station-manifest`, recalculates the snapshot SHA-256,
  applies the reviewed EPDK normalizer/CCS2 projection and deterministic corridor cap, builds the
  OSRM Table graph, and fetches geometry only for selected legs. A fake injected OSRM client proves
  orchestration without network access. With explicit coordinate-transfer approval, the browser also
  selected arbitrary points `39.8254, 31.6626` and `40.1117, 32.8052` against
  `router.project-osrm.org`; the 12-candidate public-demo profile returned three rendered alternatives,
  a 177 km fastest route, and one static-snapshot charging stop. The public launcher keeps the local
  50-candidate default unchanged and exposes its smaller cap only to keep the shared demo service
  responsive; this is verification evidence, not a service-level guarantee.
- **Loading, empty, failure, and unavailable states — passed.** Packaged UI sections cover initial
  empty, loading, validation/provider/infeasible failure, unavailable strategies, missing Leaflet,
  and tile failure. HTTP tests distinguish 400 validation, 415 content type, 422 infeasible, 502
  provider, and 404 resource boundaries without internal tracebacks.
- **Map policy boundary — passed.** OpenStreetMap attribution appears both on the map and in a fixed
  fallback label. The HTTPS tile template is configurable, while the client contains no prefetch or
  bulk-download path and no public Nominatim search/autocomplete. The browser forced tile errors and
  confirmed that the fixture route and explanation stayed inspectable.
- **Captured primary-flow evidence — passed.** The reviewed 1280×720 screenshot is stored at
  `docs/evidence/m4/fixture-primary.png`; it shows the redesigned map-first planner, data/estimate
  boundaries, three selectable verified options, SOC timeline, and charging plan.

## 2026-08-10 integration performance remediation

- The corridor selector now prepares invariant segment lengths and conservative per-segment search
  bounds once per request. Its distance/progress math and deterministic ranking contract are unchanged.
- A 7,640-option, 500-point deterministic comparison measured the former calculation at 6,877.0 ms
  and the optimized calculation at 274.2 ms (`25.1x`); both produced 2,232 eligible sites and the
  same ordered 12 candidate identifiers.
- A larger local stress sample with 7,640 options and 2,500 route points fell from 32,664.4 ms to
  1,305.7 ms while preserving the existing selection contract.
- Live, explicitly opted-in public-OSRM validation used general Istanbul and Izmit center coordinates,
  not a user's location. The complete integration plan returned two route options in 3,421.8 ms,
  below the UI's 45-second request boundary. After restarting the loopback app with the remediated
  code, the same API flow returned HTTP 200 with two options in 4,149 ms. These are observed demo
  results, not an SLA.
- The same live provider exposed a documented parsing edge case: one Table distance arrived as
  `-0.1` meter for near-coincident candidates. The adapter now treats only OSRM's single-decimal
  negative-zero artifact as zero, continues to reject material negative values, and omits rounded
  zero-length non-diagonal drive legs from the optimizer graph.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1` passed after the
  remediation: 72 tests, byte-compilation, offline demo, Ruff lint/format, strict mypy, `pip check`,
  wheel build/install smoke, and the 82-file release audit all passed.

## 2026-08-10 long-route and deep-boundary remediation

- Root cause: the public launcher candidate cap of 12 bounded OSRM Table size but the former global
  distance ranking could spend nearly the whole cap on stations clustered near one end of a long
  route. The optimizer itself already supported repeated charging transitions; the cap was never a
  separate charging-stop rule, although it necessarily bounded the distinct station nodes available
  to a plan. `equirectangular-segment-progress-strata-v2` now preserves route-progress
  coverage first and then fills spare capacity with globally strong candidates. Legacy v1 remains
  available only to replay the frozen M3 evidence.
- Selected route geometry is now deduplicated across returned options. A directed leg shared by two
  strategies is requested from OSRM once, and a previously fetched direct corridor geometry is reused
  when an option selects that edge.
- Deep input/provider hardening now rejects non-finite and boolean numeric values, malformed UTC
  provenance, orphan socket/site projections, mismatched OSRM Table nullability, invalid direct
  candidate graphs, schema-v2 fixture extensions, undeclared fixture nodes, and duplicate directed
  fixture legs. The provider package export list no longer gets overwritten by a second `__all__`.
- The loopback health response now includes `service=chargepath-demo` and `api_version=2`; the Windows
  launcher validates both instead of accepting any HTTP 200 or an outdated ChargePath health shape on
  the configured port.
- A seeded 30-graph regression sweep runs every exact preference twice, verifies deterministic
  equality, independently replays every feasible plan, and exercises repeated charging stops.
- Live public-OSRM validation used general Istanbul and Marmaris center coordinates with the public
  launcher's 12-candidate cap and the default 60 kWh/60% SOC vehicle. The endpoint returned three
  options in 12,594 ms: fastest/fewest-stops used 3 charging stops, shortest-distance used 4, and the
  greedy competitor used 5. A repeat returned the same stop counts and modeled totals. This is an
  observed portfolio-demo result, not an SLA or live charging guarantee.
- The final canonical gate passed with 97 tests plus byte-compilation, the offline demo, Ruff
  lint/format, strict mypy, `pip check`, wheel build/install smoke, and the 82-file release audit.

## 2026-08-10 suboptimality, latency, and boundary audit

- The public launcher's candidate cap is now 24. This remains a remote OSRM Table-size/latency bound,
  not a one-stop or fixed-stop-count optimizer rule; exact plans may use any number of charging
  transitions supported by the selected candidate graph.
- Candidate selection moved to `equirectangular-segment-farthest-progress-v3`. Every larger-cap result
  retains the smaller-cap prefix, each next station closes the largest remaining route-progress gap,
  and a two-percent tolerance lets corridor distance and charger power resolve practically equivalent
  coverage choices. Tests cover 4/8/12 prefix retention, quality selection, duplicate identifiers,
  and the frozen v1 replay boundary.
- The live Istanbul-center to Marmaris-center comparison with the default vehicle returned four
  options at both caps. Cap 12 completed in 17,913 ms with 4/4/3/4 stops and modeled totals
  538.8/541.5/590.6/546.7 minutes; cap 24 completed in 19,369 ms with 4/4/3/4 stops and improved
  totals 534.7/537.7/566.2/546.6 minutes. These are observed public-demo results, not an SLA or live
  station guarantee.
- Selected-plan geometry now deduplicates directed legs across all options and fetches independent
  missing geometries with bounded four-way concurrency. Public response ordering and optimizer
  determinism remain unchanged; an offline concurrency regression proves distinct edges overlap.
- Exact and greedy strategies now share immutable per-leg energy-bucket calculations. A 52-node
  complete synthetic graph retained identical output while the ten-run composed mean fell from
  941.38 ms to 605.68 ms (about 36%). Exact search state and independent verification are not shared.
- Conservative energy rounding no longer subtracts an epsilon before `ceil`; every positive energy
  requirement consumes at least one bucket. A separate small-graph Bellman-Ford oracle now checks all
  three exact lexicographic objectives across 30 seeded graphs, including infeasible and multi-stop
  cases.
- Direct domain, route-option, provider, station-normalization, snapshot-manifest, URL, path, and
  server configurations now fail closed on malformed types, non-finite values, credentials in URLs,
  inconsistent provenance/counts/content, prohibited snapshot reuse, or invalid status values.
  Internal service `ValueError`/`KeyError` failures return a generic HTTP 500 instead of being
  misclassified as client errors or leaking details. Health identity is now API v3 and the Windows
  launcher requires that exact contract.
- A final public-OSRM rerun from this sandbox was unavailable because child-process outbound sockets
  were denied (`WinError 10013`). The earlier live A/B values above were completed before that policy
  boundary; all final behavior remains covered offline and by provider fakes.
- The canonical repository gate passed with 120 tests, byte-compilation, the deterministic offline
  demo, Ruff lint/format, strict mypy, `pip check`, real-wheel build/install smoke, and the 82-file
  fail-closed release audit.

## M5 local release-candidate evidence

Prepared and verified on 2026-08-12 with CPython 3.11.9 on Windows x64. M5 is not marked complete
because the local candidate is committed but no remote is configured, so the required Windows/Ubuntu
GitHub Actions jobs cannot run until the exact candidate is pushed with explicit approval.

2026-08-12 remediation evidence:

- The full canonical gate passed with 122 tests, byte-compilation, the offline synthetic demo, Ruff
  lint/format, strict mypy, `pip check`, wheel build/install smoke, and the 83-file fail-closed
  release audit.
- The fixture browser smoke at `127.0.0.1:8765` selected all four route strategies, verified
  keyboard tab selection, and recorded no browser console errors. Its checked-in acceptance record is
  `docs/evidence/m5/browser-smoke-2026-08-12.md`.
- A coarse 5% SOC infeasibility now retries once at 2% and exposes the returned resolution. Local
  OSRM integration also widens only an infeasible capped candidate pool, up to its explicit local
  limit; remote OSRM never auto-expands.

Commands and results:

- Official tag resolution pinned `actions/checkout` 6.0.2 to
  `de0fac2e4500dabe0009e67214ff5f5447ce83dd` and `actions/setup-python` 6.2.0 to
  `a309ff8b426b58ec0e2a45f0f869d46889d02405`; the workflow uses the full immutable SHAs.
- A new isolated `%TEMP%\chargepath-m5-clean-20260810` environment was created from CPython 3.11.9.
  `pip==26.2`, `setuptools==83.0.0`, and the exact constrained development graph installed from the
  local candidate successfully.
- With `CHARGEPATH_PYTHON` selecting that isolated interpreter,
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1` passed: 69 tests,
  byte-compilation, the offline synthetic CLI demo, Ruff lint, formatting for 49 Python
  files, strict mypy for 26 source files, `pip check`, the real-wheel build/install smoke, and the
  fail-closed release audit. The audit reconciled exactly 76 allowlisted files and reported no prohibited paths, oversized
  files, or high-confidence secret patterns.
- The isolated `python -m chargepath.demo --port 8771` returned HTTP 200 from loopback-only
  `/api/config`, reporting fixture mode, two synthetic endpoints, the synthetic freshness label, and
  visible OpenStreetMap attribution.
- The isolated M3 benchmark command completed with three warm-ups and 20 repetitions using temporary
  outputs. Regenerated normalized correctness SHA-256
  `db55067b92a5904624dcf9fe90c5861561cc092b0b7754673ee101ef849572b6` matched the committed M3
  artifact exactly; the machine-specific runtime output was not added to the candidate.
- A direct wheel audit exposed and fixed a source-only fixture path: the first non-editable wheel
  could not find `synthetic_corridor.json` outside the checkout. The final package installs the exact
  source fixture under `share/chargepath/`; `scripts/check_wheel.py` verifies web assets, fixture,
  license notices, a new non-editable environment, and repo-independent fixture-service startup.
- After the map-interaction remediation, the canonical gate passed again: 69 tests, byte-compilation,
  offline demo, Ruff/format, strict mypy, `pip check`, wheel build/install smoke, and an 82-file
  release audit. Leaflet 1.9.4 CSS/JavaScript and its BSD-2-Clause license are now packaged locally;
  browser verification loaded all 20 visible map tiles with absolute Leaflet positioning instead of
  the prior broken CDN-style layout.

Named M5 acceptance evidence:

- **Clean Windows install — passed.** The exact README install graph and every offline README
  execution path were exercised in a fresh environment. The credential-free transcript is
  `docs/evidence/m5/windows-clean-install.txt`.
- **Green Windows and Ubuntu CI — pending publication approval.** CI is prepared for exact
  `windows-2025` and `ubuntu-24.04` runner labels, CPython 3.11.9, pinned Actions SHAs, pinned pip/build
  tools, constrained verification dependencies, and the same canonical gate. No remote result is
  claimed.
- **Architecture, benchmark, demo, and media — passed locally.** Final source boundaries remain in
  `docs/architecture.md` and `docs/algorithm.md`; reviewed deterministic evidence remains under
  `docs/evidence/m3/`; the verified primary screenshot remains
  `docs/evidence/m4/fixture-primary.png`; `README.md` exposes the decision, evidence, setup, and limits.
- **License and attribution — passed.** Project-authored code/documentation now use the MIT License.
  `THIRD_PARTY_NOTICES.md` records browser/provider, OSM/ODbL, exact verification-package, and CI-action
  licenses. OpenStreetMap attribution remains visible in the UI, and unresolved EPDK redistribution
  prevents real response rows from entering the candidate.
- **Release-content audit — passed.** `docs/evidence/m5/release-allowlist.txt` is an exact sorted
  83-file allowlist. `scripts/audit_release.py`, now part of the canonical gate, rejects missing or
  unexpected files, raw/processed data, caches, bytecode, OSRM build artifacts, files over 1 MiB,
  high-confidence secret patterns, missing license/OSM/synthetic contracts, or unpinned CI inputs.
  The ignored 15,444,078-byte EPDK response, `.venv`, caches, and generated package metadata are not
  release content. The separate wheel check proves built-artifact contents and installed behavior.
- **Exact versions — passed.** Release version 0.1.0, CPython 3.11.9, pip 26.2, setuptools 83.0.0,
  direct tools, transitive verification packages, Actions releases/SHAs, fixture versions, and prior
  benchmark/OSRM identities are recorded in the candidate.
- **Git/release review — locally passed, final immutable review pending.** The current 83 candidate
  files are committed on local `main`; no remote exists. Push, CI evidence, tag, and GitHub release
  remain pending explicit remote/publication approval.

## Known risks

- EPDK's endpoint and one observed schema were verified on 2026-08-09, but the public Swagger still
  has no response definition; quota, source freshness, cross-snapshot identifier behavior, and
  redistribution permission remain integration/release risks.
- A constant-consumption model can underestimate route energy under speed, grade, temperature, and
  weather variation.
- Discrete SOC buckets may reject a narrowly feasible continuous solution.
- Candidate-corridor pruning can omit a better route. M3 found no gap on one audited small synthetic
  graph; that result is not a general preservation guarantee.
- Public OpenStreetMap, tile, Nominatim, and OSRM demo services have usage policies and no project-owned
  SLA; the configured OSRM default is loopback.
- The first-release compatibility model excludes AC charging and non-CCS2 DC connectors.

## Git state

Independent repository is on local `main` with no remote. All 83 allowlisted candidate files are
committed; ignored environments, caches, raw/processed EPDK data, and generated package metadata
remain outside the candidate. Push, tag, and publication remain pending explicit remote/publication
approval.
