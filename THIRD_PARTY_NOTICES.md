# Third-party notices

ChargePath has no third-party Python runtime package dependency. Leaflet is the only third-party
code shipped: its distribution CSS and JavaScript are vendored in `src/chargepath/web/`, go into the
wheel and the static demo, and carry their BSD 2-Clause license text with them. The other
components below are loaded or queried at runtime and are not bundled.

| Component | Version or boundary | License / terms | Project use |
|---|---|---|---|
| [Leaflet](https://github.com/Leaflet/Leaflet/tree/v1.9.4) | 1.9.4, vendored distribution CSS and JavaScript | [BSD 2-Clause](src/chargepath/web/LICENSE-leaflet.txt) | Local map rendering without a runtime CDN dependency. |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Provider-selected snapshot | ODbL 1.0 data; separate tile usage policy | Road/map data only through configured providers. Attribution is visible in the UI. No OSM extract is distributed. |
| [OSRM backend](https://github.com/Project-OSRM/osrm-backend) | Optional; the recorded fixture pins 26.7.3 | [BSD 2-Clause](https://github.com/Project-OSRM/osrm-backend/blob/master/LICENSE.TXT) | Optional local road provider. The backend is not bundled. |
| [Pyodide](https://github.com/pyodide/pyodide/tree/0.27.7) | 0.27.7, loaded from the jsDelivr CDN by the static demo only, with a subresource-integrity hash on `pyodide.js` | [MPL-2.0](https://github.com/pyodide/pyodide/blob/0.27.7/LICENSE) | Runs the planner in the browser for the GitHub Pages demo. Not bundled and not used by the package or the local server. |
| EPDK charging-station service | Optional static snapshot | Redistribution status unresolved | Real response rows are excluded from Git and the release. Only metadata and project-authored synthetic fixtures are included. |

Files under `data/sample/`, `data/benchmarks/synthetic/`, and `tests/fixtures/` are project-authored
synthetic fixtures. The recorded OSRM fixture was produced from the included synthetic OSM ring.
The real-route selection distances and model results derive from OpenStreetMap data, © OpenStreetMap
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
