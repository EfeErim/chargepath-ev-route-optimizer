# Third-party notices

ChargePath has no third-party Python runtime package dependency. The standard-library server and
the static demo load the following optional browser or provider components at runtime; none of
their source or data is copied into the release.

| Component | Version or boundary | License / terms | Project use |
|---|---|---|---|
| [Leaflet](https://github.com/Leaflet/Leaflet/tree/v1.9.4) | 1.9.4, vendored distribution CSS and JavaScript | [BSD 2-Clause](licenses/leaflet-BSD-2-Clause.txt) | Local map rendering without a runtime CDN dependency. |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Provider-selected snapshot | ODbL 1.0 data; separate tile usage policy | Road/map data only through configured providers. Attribution is visible in the UI. No OSM extract is distributed. |
| [OSRM backend](https://github.com/Project-OSRM/osrm-backend) | Optional; M1 fixture pins 26.7.3 | [BSD 2-Clause](https://github.com/Project-OSRM/osrm-backend/blob/master/LICENSE.TXT) | Optional local road provider. The backend is not bundled. |
| [Pyodide](https://github.com/pyodide/pyodide/tree/0.27.7) | 0.27.7, loaded from the jsDelivr CDN by the static demo only, with a subresource-integrity hash on `pyodide.js` | [MPL-2.0](https://github.com/pyodide/pyodide/blob/0.27.7/LICENSE) | Runs the planner in the browser for the GitHub Pages demo. Not bundled and not used by the package or the local server. |
| EPDK charging-station service | Optional static snapshot | Redistribution status unresolved | Real response rows are excluded from Git and the release. Only metadata and project-authored synthetic fixtures are included. |

The development tools are pinned in the `dev` extra of [`pyproject.toml`](pyproject.toml), and the
build backend in its `[build-system]` table. These packages, listed with the versions in use for
0.3.0, are development or build tools, not application runtime dependencies.

| Package / tool | Version | Declared license |
|---|---:|---|
| setuptools (build backend) | 84.2.1 | MIT |
| pytest | 9.2.0 | MIT |
| Hypothesis | 6.170.15 | MPL-2.0 |
| mypy | 2.4.1 | MIT |
| Ruff | 0.17.4 | MIT |
| ast_serialize | 0.13.1 | MIT |
| colorama | 0.4.6 | BSD |
| iniconfig | 2.3.0 | MIT |
| librt | 0.16.0 | MIT |
| mypy_extensions | 1.1.0 | MIT |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pathspec | 1.1.1 | MPL-2.0 |
| pluggy | 1.6.0 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| sortedcontainers | 2.4.0 | Apache-2.0 |
| typing_extensions | 4.16.0 | PSF-2.0 |

CI uses `actions/checkout` 7.0.1 and `actions/setup-python` 7.1.0; the Pages workflow also uses
`actions/upload-pages-artifact` 5.0.0 and `actions/deploy-pages` 5.0.1. All are MIT-licensed and
pinned to full commit SHAs. Their code is executed by GitHub Actions and is not distributed in the
ChargePath package.

Files under `data/sample/`, `data/benchmarks/synthetic/`, and `tests/fixtures/` are project-authored
synthetic fixtures. The recorded OSRM fixture was produced from the included synthetic OSM ring.
The M6 selection distances and model results derive from OpenStreetMap data, © OpenStreetMap
contributors, ODbL. Source extracts and station rows stay outside Git.

## Field-pilot data

The research pilot uses the [TUM Electric Vehicle Usage Data Set](https://github.com/TUMFTM/electric-vehicle-uds-dataset)
by Schreiber et al. ([paper](https://doi.org/10.1016/j.etran.2025.100518)). The data source is
licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). The raw source stays
outside the release. The repository carries the method and aggregate pilot statistics.

The BMW grade pilot uses winter recordings from Steinstraeter, Buberger, and Trifonov,
[Battery and Heating Data in Real Driving Cycles](https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles),
from a [Kaggle mirror](https://www.kaggle.com/datasets/atechnohazard/battery-and-heating-data-in-real-driving-cycles).
The pinned ZIP stays local. The release carries aggregate statistics and analysis code.
