# Map demo

## Fixture mode

```powershell
python -m chargepath.demo
```

Open `http://127.0.0.1:8743`; the server binds loopback only. Fixture mode is the default and plans
the synthetic `src/chargepath/data/synthetic_corridor.json` with no OSRM or EPDK call. Only the two declared
endpoint markers are accepted, because the fixture has no roads for arbitrary clicks.

The UI shows one tab per strategy with the aliases that chose each plan, unavailable strategies,
the route and stops, driving, charging and total time, an estimated SOC timeline, and for each stop
of the fastest plan a structural fallback note (other modeled chargers in direct reach and the added
minutes without that charger; not availability). Every plan carries a data-freshness label;
fixture mode shows `Synthetic fixture — freshness not applicable`.

The request endpoint accepts only the documented origin, destination and vehicle fields, caps bodies
at 32 KiB, and returns distinct HTTP states for validation, infeasible, provider and missing-resource
outcomes without tracebacks.

## Static browser build

```powershell
python scripts/build_static_demo.py
python -m http.server 8790 --bind 127.0.0.1 --directory outputs/static-demo
```

The build writes the packaged web files with relative paths, the fixture, a deterministic zip of
the `chargepath` sources, and `static-api.js`, which loads Pyodide 0.27.7 (CPython 3.12) from
jsDelivr with an integrity hash and answers `/api/config` and `/api/plan` through
`chargepath.demo.browser`. That module reuses the server's schema, fixture service and status
mapping, so both builds return the same plans and errors. The `deploy` job in
`.github/workflows/ci.yml` publishes it to [GitHub Pages](https://efeerim.github.io/chargepath-ev-route-optimizer/)
from `main` after the checks of the same commit pass.

`browser_tests/` loads the build in Chromium with real Pyodide, blocks map tiles and fails on
unexpected JavaScript errors. It needs network, so it runs in CI rather than in `scripts/check.ps1`:

```powershell
python -m pip install --editable ".[dev,browser]"
python -m playwright install chromium
python -m pytest -q browser_tests
```

## Integration mode

Integration needs a local OSRM server (see [reproducing](reproducing.md#local-osrm)), an EPDK
response and its manifest:

```powershell
python -m chargepath.demo --mode integration `
  --osrm-endpoint http://127.0.0.1:5000 `
  --station-snapshot data/raw/epdk/<date>/response.json `
  --station-manifest data/processed/epdk/<date>/manifest.json
```

Startup rejects a snapshot whose SHA-256 does not match its manifest. For each trip the service
gets the direct corridor from OSRM, selects candidate stations, builds the Table graph, runs the
option planner and fetches geometry only for legs the options use, with at most four concurrent
requests. Candidate selection is prefix-monotonic and spread along the route. Against loopback OSRM
an infeasible capped search widens up to `--adaptive-candidate-cap-limit` (default 98). If every
strategy is infeasible on the 5% SOC grid, the service retries once at 2% and labels the response.

The public OSRM server can stand in with `--osrm-endpoint https://router.project-osrm.org
--allow-remote-osrm --candidate-cap 24`. Coordinates then leave the machine, the server has no
availability guarantee, and the candidate set never widens. The browser receives only the selected
plans' station details and the manifest's freshness context, never the snapshot.

## Map and network use

Tiles default to `https://tile.openstreetmap.org/{z}/{x}/{y}.png`; `--tile-url` accepts another
HTTPS template with `{z}`, `{x}` and `{y}`. The app uses one normal Leaflet tile layer with no
prefetch, keeps OpenStreetMap attribution visible, and has no address search, because the public
Nominatim policy forbids client-side autocomplete. Leaflet 1.9.4 is bundled. If tiles fail, routes,
markers, options and the SOC timeline still render; if Leaflet fails, the trip form and route
explanation remain usable.
