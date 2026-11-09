# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Reorganized documentation: benchmark outputs live under `docs/benchmarks/` and version
  history moved here.
- Trimmed project tooling: CI runs on Ubuntu only (Python 3.11 and 3.13); the release-file
  allowlist audit, wheel smoke check, constraints file, release guide, data dictionary, and
  EPDK access report were removed. The EPDK findings now live in `docs/data_strategy.md`, and a
  test checks that relative Markdown links resolve.
- Narrowed the package exports: `chargepath` re-exports only the planner, model, and replay
  names, and `chargepath.providers` only `StaticRoadNetwork`; import other names from their
  modules. Removed the unused `.env.example`.
- Removed the Windows launchers (`ChargePath.cmd`, `scripts/open_chargepath.ps1`,
  `scripts/start_custom_map.ps1`); the live demo covers the no-install case, and integration mode
  starts with `python -m chargepath.demo --mode integration`.
- The demo and the real-route collector load EPDK snapshots through one
  `station_data.load_snapshot_options` function, and both EPDK scripts share
  `station_data.inspect_snapshot` instead of running a subprocess.
- `docs/architecture.md` is now a module table that covers every module; `docs/source_registry.md`
  was folded into `docs/research.md`.
- Renamed benchmark scripts after what they do (`run_synthetic_benchmark.py`,
  `build_route_manifest.py`, `collect_routes.py`, `evaluate_routes.py`,
  `run_route_followup.py`).
- Shortened the README.
- Split `chargepath/demo.py` into a `chargepath.demo` package (`schema`, `services`, `server`);
  `python -m chargepath.demo` and the `chargepath-demo` entry point are unchanged.
- Moved repeated input validation into `chargepath.validation`. Some error messages changed wording,
  for example unexpected request fields are now reported as `fields are invalid: unexpected [...]`.
- Corrected the Sweda, Dolinskaya, and Klabjan (2017) author list and cited the refereed Schoenberg
  and Dressler (2023) paper.
- `verify_route_plan` returns the battery level after each replayed action, and the demo builds its
  SOC timeline from it instead of recomputing leg energy.
- The optimizer, greedy heuristic, and road-only baseline share `RoutePlan.assemble`,
  `RoutePlan.stationary`, and one shortest-path search. Outputs are byte-identical.
- Rewrote the README around results, design decisions, and how AI was used. Replaced the unreachable
  Artmeier et al. (2010) link with its DOI.

### Added

- Differential, property-based, and metamorphic tests (`tests/test_properties.py`, Hypothesis)
  against an exact-rational reference solver.
- Pluggable consumption models (`ConsumptionModel`, `LegEnergy`) with signed per-leg energy, a peak
  check inside each leg, and a full-battery cap, plus an optional piecewise-linear
  `VehicleProfile.charging_curve`. Defaults reproduce earlier results byte for byte.
- Per-stop structural fallback note for the fastest plan (`chargepath.redundancy`), labeled as
  structure only.
- A static browser build of the fixture demo that runs the planner in Pyodide
  (`scripts/build_static_demo.py`), published to GitHub Pages by the `Demo site` workflow.
- An experimental terrain consumption model (`chargepath.terrain`) and its pre-registered held-out
  evaluation on the BMW i3 pilot data, which it did not pass; it is not the default.
- Literature additions in `docs/research.md`.
- Tests that the replay checker rejects tampered plans, that the fastest objective breaks time ties
  on charging stops before distance, and that the README headline follows from the committed
  real-route outcomes.
- `docs/demo.md` with captured output of the main commands.

## [0.2.0] - 2026-11-07

### Added

- Related-work review in `docs/research.md` and new entries in the source registry.

### Changed

- Documentation only; optimizer code and benchmark results are identical to 0.2.0rc1.
  Vehicle-specific charging curves were investigated and left as future work because
  no suitable public data was found.

## [0.2.0rc1] - 2026-11-07

### Added

- Real-route benchmark: 24 frozen routes on Türkiye's road network, three vehicle
  profiles, separate OSRM collection and offline evaluation, and replay of every
  returned plan.

### Fixed

- The OSRM adapter now tolerates sub-meter negative distances returned for some legs.

## [0.1.1] - 2026-11-05

### Changed

- The Windows launcher works from a clean clone and asks before sending coordinates
  to the public OSRM server.
- Candidate widening stays within the default OSRM Table limit.
- The UI labels how many distinct plans the strategies produced.
- CI also runs on CPython 3.13.5.

### Added

- `scripts/fetch_epdk_snapshot.py` to download a station snapshot in one request.

## [0.1.0] - 2026-08-12

First public release.

### Added

- Exact SOC-bucketed Dijkstra planner with fastest-time, shortest-distance and
  fewest-stop objectives, plus a fixed-80% greedy baseline.
- Independent replay that checks every returned plan against the reserve constraint.
- OSRM route/table adapter with recorded fixtures.
- EPDK station-data normalizer and CCS2 DC corridor selection.
- Synthetic benchmark suite with correctness and runtime results.
- Offline Leaflet map demo served from loopback.

[Unreleased]: https://github.com/EfeErim/chargepath-ev-route-optimizer/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/EfeErim/chargepath-ev-route-optimizer/compare/v0.2.0rc1...v0.2.0
[0.2.0rc1]: https://github.com/EfeErim/chargepath-ev-route-optimizer/compare/v0.1.1...v0.2.0rc1
[0.1.1]: https://github.com/EfeErim/chargepath-ev-route-optimizer/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/EfeErim/chargepath-ev-route-optimizer/releases/tag/v0.1.0
