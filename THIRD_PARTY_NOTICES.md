# Third-party notices

ChargePath 0.1.0 has no third-party Python runtime package dependency. The standard-library server
loads the following optional browser or provider components at runtime; none of their source or data
is copied into the release.

| Component | Version or boundary | License / terms | Project use |
|---|---|---|---|
| [Leaflet](https://github.com/Leaflet/Leaflet/tree/v1.9.4) | 1.9.4, vendored distribution CSS and JavaScript | [BSD 2-Clause](licenses/leaflet-BSD-2-Clause.txt) | Local map rendering without a runtime CDN dependency. |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Provider-selected snapshot | ODbL 1.0 data; separate tile usage policy | Road/map data only through configured providers. Attribution is visible in the UI. No OSM extract is distributed. |
| [OSRM backend](https://github.com/Project-OSRM/osrm-backend) | Optional; M1 fixture pins 26.7.3 | [BSD 2-Clause](https://github.com/Project-OSRM/osrm-backend/blob/master/LICENSE.TXT) | Optional local road provider. The backend is not bundled. |
| EPDK charging-station service | Optional static snapshot | Redistribution status unresolved | Real response rows are excluded from Git and the release. Only metadata and project-authored synthetic fixtures are included. |

The reproducible verification environment is pinned in
[`constraints/release-py311.txt`](constraints/release-py311.txt). These packages are development or
build tools, not application runtime dependencies.

| Package / tool | Release version | Declared license |
|---|---:|---|
| pip | 26.2 | MIT |
| setuptools | 83.0.0 | MIT |
| pytest | 8.4.2 | MIT |
| mypy | 1.20.2 | MIT |
| Ruff | 0.16.1 | MIT |
| colorama | 0.4.6 | BSD |
| iniconfig | 2.3.0 | MIT |
| librt | 0.13.0 | MIT |
| mypy_extensions | 1.1.0 | MIT |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pathspec | 1.1.1 | MPL-2.0 |
| pluggy | 1.6.0 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| typing_extensions | 4.16.0 | PSF-2.0 |

CI uses `actions/checkout` 6.0.2 and `actions/setup-python` 6.2.0, both MIT-licensed and pinned to
full commit SHAs in the workflow. Their code is executed by GitHub Actions and is not distributed in
the ChargePath package.

All files under `data/sample/`, `data/benchmarks/`, and `tests/fixtures/` are project-authored and
explicitly synthetic. The recorded OSRM fixture was produced from the included synthetic OSM ring;
it contains no real OpenStreetMap extract. Research papers and official service documentation are
references only; no third-party implementation code or paper text is copied into the project.
