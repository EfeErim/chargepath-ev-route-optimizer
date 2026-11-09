# Local map demo

## Fixture-first command

From the repository root, after the editable development install:

```powershell
python -m chargepath.demo
```

Open `http://127.0.0.1:8743`. The server has no non-loopback bind option. Fixture mode is the
default and loads `data/sample/synthetic_corridor.json`; route planning, charging decisions, SOC
replay, and route geometry do not call OSRM or EPDK.

The two declared synthetic endpoint markers are selected by default. They can be reselected on the
Leaflet map. The primary fixture deliberately does not pretend that arbitrary clicked coordinates
have matching roads. Integration mode accepts arbitrary origin/destination clicks because OSRM owns
those road legs.

## What the UI shows

- one tab per available strategy, the number of distinct plans, and every strategy alias that
  selected the shown plan;
- unavailable strategies without manufacturing a partial result;
- selected route geometry and charging-stop markers;
- driving, charging, total-time, and arrival-SOC estimates;
- an estimated SOC timeline and estimated charging actions;
- for each stop of the fastest plan, a structural fallback note: how many other modeled chargers are
  in direct reach and how much slower the best plan is without that charger (graph structure only,
  not availability);
- the exact fixture or static-snapshot freshness label; and
- validation, loading, infeasible/provider failure, empty-result, and unavailable-basemap states.

The bundled flow always displays `Synthetic fixture — freshness not applicable`. SOC and charging
time are explicitly estimates. Static station records are never described as live availability,
price, or reservation evidence.

## Static browser build

The same fixture flow can run entirely in a browser, with no Python server:

```powershell
python scripts/build_static_demo.py
python -m http.server 8790 --bind 127.0.0.1 --directory outputs/static-demo
```

The script writes `outputs/static-demo/`: the packaged HTML, CSS, JavaScript, and Leaflet files with
relative paths, the synthetic fixture, a deterministic zip of the `chargepath` sources, and a small
bridge (`static-api.js`). The bridge loads a pinned Pyodide release (0.27.7, CPython 3.12) from
jsDelivr and answers `/api/config` and `/api/plan` through `chargepath.demo.browser`. That module
reuses the server's request schema, fixture service, and status mapping, so both builds return the
same plans and errors. Integration mode, OSRM, and EPDK data are not included.

The output is a plain static site. The `Demo site` workflow
(`.github/workflows/pages.yml`) rebuilds it on every push to `main` and publishes it to
[GitHub Pages](https://efeerim.github.io/chargepath-ev-route-optimizer/). The first load downloads
Pyodide (about 10 MB) from the CDN; map tiles still come from the configured tile server under its
usage policy.

## Explicit integration mode

Integration is never selected implicitly. It requires a local OSRM endpoint, a static EPDK response,
and its metadata manifest:

```powershell
python -m chargepath.demo `
  --mode integration `
  --osrm-endpoint http://127.0.0.1:5000 `
  --station-snapshot data/raw/epdk/<date>/response.json `
  --station-manifest data/processed/epdk/<date>/manifest.json
```

Startup recalculates the response SHA-256 and rejects a mismatch. It then applies the reviewed EPDK
normalizer and public CCS2 DC projection. For each submitted trip, the integration flow asks OSRM for
the direct corridor geometry, applies deterministic station pruning, builds the candidate Table
graph, runs the competitive planner, and requests geometry only for selected route legs. The OSRM
adapter deduplicates shared directed legs across options and uses at most four concurrent geometry
requests. It still rejects non-loopback endpoints unless the caller explicitly opts in.

With the public OSRM server, use `--candidate-cap 24` to bound the remote Table
request. The optimizer has no one-stop or fixed stop-count rule, but this pool necessarily bounds a
plan to those 24 distinct station nodes. Candidate selection is prefix-monotonic and spreads that set
across route progress while letting corridor quality resolve close coverage choices. In loopback OSRM
mode, an infeasible capped search widens the same deterministic selection up to
`--adaptive-candidate-cap-limit` (default: 98, which fits `osrm-routed`'s default 100-location Table
limit once the origin and destination are included) before reporting no feasible route. See
[local OSRM setup](osrm_setup.md) to raise both limits together. The service never performs that
automatic widening against a remote OSRM endpoint.

The planner first evaluates the configured 5% SOC grid. If every route strategy is infeasible at that
coarse resolution, it retries once at 2% and returns a `model_resolution` label with the response.
The UI shows that refinement rather than presenting it as a continuous-model result.

The real response and processed rows remain outside Git while redistribution status is unresolved.
The browser receives manifest freshness context and only the selected plan's station presentation;
it does not receive the source snapshot or the full normalized station set.

A snapshot is retrieved once, on request, with `python scripts/fetch_epdk_snapshot.py`. The script
performs no retry or polling and refuses to replace a snapshot from the same day. The service's quota
is undocumented and it has returned HTTP 429 to a repeat request, so retrieve snapshots sparingly.

With a snapshot and a local OSRM server, start integration mode directly (replace the date with
your snapshot's):

```powershell
python -m chargepath.demo --mode integration `
  --station-snapshot data/raw/epdk/2026-08-09/response.json `
  --station-manifest data/processed/epdk/2026-08-09/manifest.json `
  --osrm-endpoint http://127.0.0.1:5000
```

Without a local OSRM instance, the public OSRM demo server can stand in. Selected origin,
destination, and candidate station coordinates then leave the machine, so the server refuses a
non-loopback endpoint unless `--allow-remote-osrm` is given, and that server has no project-owned
availability guarantee:

```powershell
python -m chargepath.demo --mode integration `
  --station-snapshot data/raw/epdk/2026-08-09/response.json `
  --station-manifest data/processed/epdk/2026-08-09/manifest.json `
  --osrm-endpoint https://router.project-osrm.org --allow-remote-osrm --candidate-cap 24
```

## Map and network boundary

The default tile template is:

```text
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

It can be changed with `--tile-url`; the value must be HTTPS and retain `{z}`, `{x}`, and `{y}`.
The app creates one normal Leaflet tile layer with idle updates and a one-tile buffer. It contains no
prefetch or bulk-download path. OpenStreetMap attribution remains visible. Public Nominatim search or
autocomplete is not implemented.

Leaflet 1.9.4 CSS and JavaScript are packaged with the application, so map layout does not depend on
the unpkg CDN at runtime. If tiles fail, the app raises a basemap warning but keeps route GeoJSON, endpoint/station markers,
option tabs, charging details, and the SOC timeline. If Leaflet itself cannot load, the non-map trip
setup and route explanation remain usable.

## Input and HTTP boundary

The JSON planning endpoint accepts only the documented origin, destination, and vehicle fields. It
caps request bodies at 32 KiB and validates finite bounds for battery, SOC, consumption, charging
power, reserve, coordinates, and safety factor. Initial SOC cannot be below reserve SOC. Provider,
infeasible, validation, and missing-resource outcomes remain distinct HTTP states without returning
internal tracebacks.
