# Reproducing the results

## Synthetic benchmark

The checksum-pinned fixture and manifest are under `data/benchmarks/synthetic/v1/`, the outputs
under `docs/benchmarks/synthetic/`. `tests/test_evaluation.py` regenerates the correctness artifact
and compares it byte for byte. Runtime is machine-specific.

```powershell
python scripts/run_synthetic_benchmark.py --warmups 3 --repetitions 20
```

## Local OSRM

ChargePath defaults to `http://127.0.0.1:5000` and refuses a non-loopback endpoint unless it is
passed with `allow_remote_endpoint=True` (`--allow-remote-osrm` in the demo). The adapter was
fixture-tested against OSRM backend `v26.7.3` at commit `0844e3af77896d11998ef6db356a553056652c8e`;
`tests/fixtures/osrm/v26.7.3/manifest.json` records the asset, profile checksum, synthetic input
map and response checksums. The `/v1/` in request paths is the HTTP protocol version, not the
backend release.

Native Windows setup, all outside Git. The release asset has no Lua profile, so `profiles/` comes
from the source archive of the pinned commit. The manifest's `car.lua` checksum is for CRLF line
endings; GitHub serves LF.

```powershell
$Commit = "0844e3af77896d11998ef6db356a553056652c8e"
$Asset = "node_osrm-v26.7.3-8-win32-x64-Release.tar.gz"
New-Item -ItemType Directory -Force D:\osrm-data\bin, D:\osrm-data\profiles, D:\osrm-data\extract

curl.exe -fsSL -o D:\osrm-data\$Asset "https://github.com/Project-OSRM/osrm-backend/releases/download/v26.7.3/$Asset"
tar.exe -xzf D:\osrm-data\$Asset -C D:\osrm-data\bin
curl.exe -fsSL -o D:\osrm-data\src.tar.gz "https://github.com/Project-OSRM/osrm-backend/archive/$Commit.tar.gz"
tar.exe -xzf D:\osrm-data\src.tar.gz -C D:\osrm-data\profiles --strip-components=2 "osrm-backend-$Commit/profiles"
curl.exe -fsSL -o D:\osrm-data\extract\turkey-latest.osm.pbf https://download.geofabrik.de/europe/turkey-latest.osm.pbf

$Bin = "D:\osrm-data\bin\binding_napi_v8"
& $Bin\osrm-extract.exe -p D:\osrm-data\profiles\car.lua -d <data-version> -t 8 D:\osrm-data\extract\turkey-latest.osm.pbf
& $Bin\osrm-contract.exe -t 8 D:\osrm-data\extract\turkey-latest.osrm
& $Bin\osrm-routed.exe --algorithm CH --ip 127.0.0.1 --port 5000 --max-table-size 100 D:\osrm-data\extract\turkey-latest.osrm
```

Pass `--ip 127.0.0.1`; `osrm-routed` binds `0.0.0.0` by default. `osrm-extract` can use nearly all
free memory of a desktop machine on the Turkey extract.

`osrm-routed` rejects Table requests above 100 locations unless started with a higher
`--max-table-size`. Each request also carries the origin and destination, so the demo's automatic
candidate widening stops at 98 stations. To search further, raise both, for example
`--max-table-size 202` with `python -m chargepath.demo --adaptive-candidate-cap-limit 200`. If a
widened request is still refused with `TooBig`, the demo reports the infeasible result of the last
search it completed.

## Real-route rerun

The [real-route results](results.md#real-route-benchmark) use the inputs pinned in the
[v1 manifest](../data/benchmarks/real-routes/v1/manifest.json). Collection is the only stage that
calls OSRM; evaluation reads the stored graphs offline. Graphs and EPDK input stay under the ignored
data directories because the graphs contain source station fields. A rerun on a new snapshot or
extract needs its own manifest and must be written outside the v1 evidence path.

1. Start OSRM as above with `--max-table-size 202` and the extract's explicit `data_version`. Save
   its provenance in `data/processed/real-routes/road.json`, shaped like the v1 manifest's
   `frozen_inputs.road_graph`.
2. Fetch an EPDK response with `scripts/fetch_epdk_snapshot.py` and build its station manifest with
   `scripts/fetch_epdk_snapshot.py --raw-response PATH`.
3. Build, collect and evaluate:

```powershell
$run = "data/processed/real-routes/independent"
$stationManifest = "data/processed/epdk/DATE/manifest.json"
$snapshot = "data/raw/epdk/DATE/response.json"
python scripts/build_route_manifest.py --road-metadata data/processed/real-routes/road.json --station-manifest $stationManifest --output-dir $run
python scripts/collect_routes.py --manifest "$run/manifest.json" --station-manifest $stationManifest --snapshot $snapshot --graphs-dir "$run/graphs" --osrm-runtime-out "$run/osrm_runtime.json"
python scripts/evaluate_routes.py --manifest "$run/manifest.json" --graphs-dir "$run/graphs" --evidence-dir "$run/results" --osrm-runtime "$run/osrm_runtime.json" --runtime-out "$run/results/runtime.json"
```

The builder needs a fresh output directory and records new checksums. Collection checks the
station-response hash and the OSRM data version. Different road distances can select different
routes, so byte-for-byte reproduction of v1 needs its original graph and snapshot, which are not in
Git. `tests/test_real_route_evaluation.py` runs collection and evaluation end to end on a synthetic
fixture with a fake OSRM client.

The follow-up scripts (`run_route_followup.py`, `run_baseline_comparison.py`,
`run_speed_choice.py`) verify each stored graph against the summary's checksum before running;
their commands are in [results](results.md). The savings chart is drawn from the committed outcomes
alone (`python scripts/plot_e1_savings.py`), and `tests/test_published_results.py` fails if the SVG
no longer matches.

## Field pilots

The [TUM dataset](https://github.com/TUMFTM/electric-vehicle-uds-dataset/tree/6bac2936e280b2d7f45607e5cb624a18b41b3faa)
publishes ID1 telemetry under CC BY-NC 4.0. Put the Git LFS object for `data/uds_data/ID1.parquet`
in `data/raw/field-pilot/tum-id1/`. The script checks its SHA-256 and reads it with PyArrow 21.0.0,
which the `pilot` extra pins (`pip install --editable ".[pilot]"`).

```powershell
python scripts/run_field_pilot.py data/raw/field-pilot/tum-id1/ID1.parquet --output data/processed/field-pilot/id1-results.json
```

Screening: last reading per second; SOC series split at gaps over 30 s; speed integrated across
gaps up to 3 s. A drive window needs at least 10 km, 90% speed coverage and 3 points of SOC loss.
A likely DC window must be stationary with at least 8 points of SOC gain at an average implied
20–110 kW. The earliest two thirds of each set fit the models; the latest third is held out.

For the BMW i3 pilots, keep the [driving-cycle ZIP mirror](https://www.kaggle.com/datasets/atechnohazard/battery-and-heating-data-in-real-driving-cycles)
under `data/raw/field-pilot/bmw-i3/`. The scripts pin its hash, use winter trips only, integrate
pack voltage × current, reject invalid traces and trips under 5 km, hold out every third accepted
trip, and score roughly 1 km sections with elevation sampled every 250 m.

```powershell
python scripts/run_grade_pilot.py data/raw/field-pilot/bmw-i3/drives.zip --output data/processed/field-pilot/bmw-results.json
python scripts/run_terrain_model_pilot.py data/raw/field-pilot/bmw-i3/drives.zip --output data/processed/field-pilot/bmw-terrain-model.json
```

## EPDK station data

On 2026-08-09 a `GET` with JSON headers and the required body `{}` returned HTTP 200 and 16,539
rows. It was stored once under ignored `data/raw/`; the
[metadata manifest](benchmarks/epdk_snapshot_manifest_2026-08-09.json) records the request
fingerprint, retrieval time, checksum, size, counts and reuse decision.

```powershell
python scripts/fetch_epdk_snapshot.py `
  --raw-response data/raw/epdk/2026-08-09/response.json `
  --retrieved-at 2026-08-09T14:13:33Z `
  --response-status 200 `
  --manifest-out data/processed/epdk/2026-08-09/manifest.json `
  --report-out data/processed/epdk/2026-08-09/validation_report.json
```

That command makes no network call. It reconciled 16,539 sites and 46,955 sockets with no rejected
or duplicate records and projected 7,640 public sites with at least one `DC_CCS` socket.

| Question | Observed | Decision |
|---|---|---|
| Quota | Undocumented; a second request on 2026-11-05 returned HTTP 429 | One request, no retries or polling |
| Freshness | No source timestamp | Recorded as `unknown_not_supplied_by_response`, never the retrieval time |
| Redistribution | Public access, no explicit licence found | No real row is committed |
| Identifiers | Seen in one snapshot only | Kept verbatim; stability unproven |
| Status, price, availability | Absent | Stations are presented as static |

The Swagger publishes no response model. `tests/fixtures/epdk/observed_v1/synthetic_response.json`
mirrors the observed shape with invented rows plus deliberate invalid and duplicate cases, and the
normalizer fails closed on unreviewed fields. `src/chargepath/data/synthetic_corridor.json`, used by the CLI
demo, the map demo and the fixture tests, is also synthetic.
