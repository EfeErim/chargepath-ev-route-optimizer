# Changelog

The project uses [Semantic Versioning](https://semver.org/).

## Unreleased

- Exact search keeps each state's priority with its label, turns a node's legs into bucket counts
  once per search, and memoizes charge minutes per station power and bucket pair. Relaxation
  order and arithmetic are unchanged, so every plan and evidence file is identical.
  `soc_to_kwh` is gone; `charging_minutes` computes the energy inline. New dated runtime files
  `runtime_2026-11-24.json` (synthetic and real-route) sit beside the earlier ones; one E1 pass
  of exact fastest takes a median 5.98 s, against 21.3 s on 2026-11-05. `evaluate_routes.py` and
  `collect_routes.py` write their timing JSON with LF line endings on Windows too.
- `scripts/paired_statistics.py` writes `paired_stats_v1.json` from the committed outcomes: 95%
  intervals for the median E1 savings with whole routes resampled, and an exact Wilcoxon
  signed-rank test on per-route mean savings. `tests/test_published_results.py` checks the file and
  the README intervals.
- Benchmark and evidence code moved to `chargepath.benchmarks` and is no longer shipped in the
  static demo's source archive. The synthetic corridor ships as package data at
  `chargepath/data/synthetic_corridor.json`, and `load_synthetic_scenario()` loads it by default;
  the command-line example is `scripts/plan_synthetic_trip.py`.
- `RoutePlan` derives its distance and time totals from its legs and stops;
  `RoutePlan.assemble` and `RoutePlan.stationary` are gone. A plan without charging now records
  `charging_minutes` as `0` in every strategy, so the evidence files were rewritten with the same
  values.
- `charging_minutes` no longer re-validates station power and model constants on every search
  edge.
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
