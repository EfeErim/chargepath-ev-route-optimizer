# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Reorganized documentation: benchmark outputs live under `docs/benchmarks/`, the
  release procedure is in `docs/release.md`, and version history moved here.
- Renamed benchmark scripts after what they do (`run_synthetic_benchmark.py`,
  `build_route_manifest.py`, `collect_routes.py`, `evaluate_routes.py`,
  `run_route_followup.py`).
- Shortened the README.

### Added

- pre-commit hooks (Ruff, whitespace) and Dependabot for pip and GitHub Actions.

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
