# Changelog

The project uses [Semantic Versioning](https://semver.org/).

## Unreleased

- Added `LookaheadGreedyOptimizer`, a one-step lookahead evaluation baseline, and
  `scripts/run_baseline_comparison.py`: exact fastest is a median 2.6 min (0.5%) faster on the 72
  E1 scenarios, maximum 26.5, with 22 ties.
- Exact search breaks full ties toward the higher arrival SOC, compares minutes and kilometres at
  nine decimal places, and skips states dominated by a higher bucket at the same node.
  `summary_v1.json` and `trips_v1.jsonl` were regenerated; feasibility and plan validity counts
  did not change.
- The Pages deploy is a job in `ci.yml` that needs the checks and the new Chromium smoke test
  (`browser_tests/`) of the same commit.
- Demo responses are typed and formatted after planning and analysis; `chargepath.demo.browser` no
  longer imports the HTTP server, and `chargepath-demo` points at `chargepath.demo.server:main`.
- Removed `chargepath.examples`, Dependabot, charging-model arguments that never changed on the
  planners and `verify_route_plan`, `OsrmHttpClient(profile=...)`, `TableCost`, `RoadTable` and
  `fetch_selected_plan_geometry`.
- Merged the architecture notes into `docs/algorithm.md` and the OSRM setup into
  `docs/reproducing.md`, and shortened the documentation and tests.

## 0.3.0 - 2026-11-12

- `verify_route_plan` can check a plan against the road network and the requested endpoints, and
  returns the battery level after each action.
- Pluggable consumption models with signed per-leg energy and an in-leg peak check, optional
  piecewise-linear charging curves, opt-in speed-dependent consumption with per-leg speed choice,
  and an experimental terrain model that failed its held-out BMW i3 check.
- Property, differential and metamorphic tests against an exact-rational reference solver.
- Charging-time framing and a per-scenario savings chart for the real-route headline, both checked
  against the committed outcomes.
- Static browser build of the fixture demo on Pyodide, published to GitHub Pages.
- Per-stop structural fallback note in the demo.
- Split `chargepath/demo.py` into a package, shared validation in `chargepath.validation`, narrowed
  package exports, and removed the Windows launchers.

## 0.2.0 - 2026-11-07

- Related-work review in `docs/research.md`. Optimizer and results are identical to 0.2.0rc1.

## 0.2.0rc1 - 2026-11-07

- Real-route benchmark: 24 frozen routes in Türkiye, three vehicle profiles, separate OSRM
  collection and offline evaluation with replay of every plan.
- The OSRM adapter tolerates sub-meter negative distances.

## 0.1.1 - 2026-11-05

- Candidate widening stays within the default OSRM Table limit.
- The UI shows how many distinct plans the strategies produced.
- `scripts/fetch_epdk_snapshot.py` downloads a station snapshot in one request.

## 0.1.0 - 2026-08-12

First public release: exact SOC-bucketed Dijkstra with fastest, shortest-distance and fewest-stop
objectives, a fixed-80% greedy baseline, independent replay, an OSRM adapter with recorded
fixtures, EPDK normalization and corridor selection, the synthetic benchmark and a loopback Leaflet
demo.
