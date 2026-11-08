# Local Map Demo

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
- the exact fixture or static-snapshot freshness label; and
- validation, loading, infeasible/provider failure, empty-result, and unavailable-basemap states.

The bundled flow always displays `Synthetic fixture — freshness not applicable`. SOC and charging
time are explicitly estimates. Static station records are never described as live availability,
price, or reservation evidence.

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

The public-OSRM convenience launcher defaults to 24 candidate stations to bound the remote Table
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

The convenience launcher uses the newest complete local snapshot to enable arbitrary map clicks. It
uses loopback OSRM by default:

```powershell
.\scripts\start_custom_map.ps1
```

For a short-lived portfolio demo without a local OSRM instance, remote OSRM must be an explicit
choice:

```powershell
.\scripts\start_custom_map.ps1 -UsePublicOsrm
```

The remote form warns that selected origin/destination coordinates leave the machine. It is a public
demo dependency without a project-owned availability guarantee, not the default release path.

## Double-click launcher

`ChargePath.cmd` runs the server in the background and opens the browser. It uses `.venv` when present
and otherwise any Python 3.11+ directly from `src/`. Without a local snapshot it opens the synthetic
demo. With one, it asks in its console window before using the public OSRM server, and any answer other
than yes opens the synthetic demo. If startup fails, it prints the server's error. The background
server keeps running after the window closes; stop it with:

```powershell
Get-NetTCPConnection -LocalPort 8743 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess }
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

## Captured evidence

The verified fixture flow is captured at
[`docs/images/map-demo.png`](images/map-demo.png), recaptured from the current
interface for 0.1.1. An earlier browser check separately forced tile failure, confirming that route
paths, three markers, and the replay-verified explanation remained visible.
