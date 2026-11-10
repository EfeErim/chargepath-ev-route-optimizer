# Reproducing the results

## Synthetic benchmark

Completed on 2026-08-09 against the checksum-pinned, project-authored synthetic v1 fixture. The
versioned manifest, normalized correctness/comparison/SOC-grid/pruning artifact, and machine-specific
runtime artifact are under `data/benchmarks/synthetic/v1/` and `docs/benchmarks/synthetic/`. The reviewed tables and
claim boundaries are in [results](results.md).

Reproduction command:

```powershell
python scripts/run_synthetic_benchmark.py --warmups 3 --repetitions 20
```

## Real-route rerun procedure

The [real-route results](results.md#real-route-benchmark) use the inputs pinned in the
[v1 manifest](../data/benchmarks/real-routes/v1/manifest.json). Collection uses local OSRM. Evaluation
reads the stored graphs offline. Keep graphs and real EPDK input under the ignored data directories
because the graphs contain source station fields. To rerun the published design on a new snapshot
or road extract, create an independent manifest and write its results outside the v1 evidence path.

1. Prepare a local OSRM instance using the [setup guide](osrm_setup.md), with
   `--max-table-size 202` and the extract's explicit `data_version`. Save its road provenance
   in `data/processed/real-routes/road.json`, using the v1 manifest's `frozen_inputs.road_graph` object
   as the schema. Record the actual extract checksum, data version, backend, and profile.
2. Obtain a local EPDK response with `scripts/fetch_epdk_snapshot.py` and generate its reviewed
   station manifest with `scripts/inspect_epdk_snapshot.py`. Keep both local and substitute their
   paths below.
3. Run the builder, collection, and offline evaluation from the repository root.

```powershell
$run = "data/processed/real-routes/independent"
$stationManifest = "data/processed/epdk/DATE/manifest.json"
$snapshot = "data/raw/epdk/DATE/response.json"
python scripts/build_route_manifest.py --road-metadata data/processed/real-routes/road.json --station-manifest $stationManifest --output-dir $run
python scripts/collect_routes.py --manifest "$run/manifest.json" --station-manifest $stationManifest --snapshot $snapshot --graphs-dir "$run/graphs" --osrm-runtime-out "$run/osrm_runtime.json"
python scripts/evaluate_routes.py --manifest "$run/manifest.json" --graphs-dir "$run/graphs" --evidence-dir "$run/results" --osrm-runtime "$run/osrm_runtime.json" --runtime-out "$run/results/runtime.json"
```

The builder requires a fresh output directory. It records new road-distance and manifest checksums.
Collection verifies the station-response hash and the OSRM data version. An independent run can
select different routes if the road distances change. The manifest hash identifies the specific
run within the v1 real-route design. Byte-for-byte reproduction of v1 requires its original
road graph and station snapshot, which are kept outside Git. Machine timings vary across runs.

For an end-to-end check without a real station snapshot or OSRM instance, run the test module
below. Its synthetic fixture and fake road provider exercise collection and offline evaluation.

```powershell
python -m pytest -q tests/test_real_route_evaluation.py
```

## Field pilot procedure

The [TUM source](https://github.com/TUMFTM/electric-vehicle-uds-dataset/tree/6bac2936e280b2d7f45607e5cb624a18b41b3faa)
publishes ID1 telemetry under CC BY-NC 4.0. Download the Git LFS object for
`data/uds_data/ID1.parquet` into `data/raw/field-pilot/tum-id1/`. The script verifies the original
object SHA-256 and reads it offline using optional PyArrow 21.0.0. Install that reader in a separate
analysis environment and install ChargePath there before running the command.

```powershell
python scripts/run_field_pilot.py data/raw/field-pilot/tum-id1/ID1.parquet --output data/processed/field-pilot/id1-results.json
```

Use the last reading per second and split SOC observations at gaps over 30 seconds. Integrate
speed across gaps up to three seconds. A drive window must have at least 10 km, 90% speed
coverage, and three percentage points of SOC loss. A likely DC window must be stationary, have
at least eight percentage points of SOC gain, and average 20–110 kW implied battery power during
the SOC rise. These thresholds screen telemetry for analysis.

Use the earliest two thirds of each set to fit the models and reserve the latest third for error
measurement. Compare an 18 kWh/100 km baseline, with and without the 10% margin, against a fitted
constant rate and a fitted rate depending on squared mean speed. Compare the generic charging
model against a taper and effective cap fitted on the training windows. Keep the source records
local. The [reported results](results.md#field-pilot--volkswagen-id3) include error direction and
limits of the telemetry.

For the BMW follow-up, obtain the [driving-cycle ZIP mirror](https://www.kaggle.com/datasets/atechnohazard/battery-and-heating-data-in-real-driving-cycles)
of Steinstraeter, Buberger, and Trifonov's data and keep it under `data/raw/field-pilot/bmw-i3/`.
The script pins the downloaded archive hash and processes only winter trips. It integrates
measured pack voltage × current and recorded speed, rejects invalid traces or trips below 5 km,
and holds out every third accepted trip with all its sections. The models use distance-weighted
squared speed, signed net grade, and total absolute elevation change sampled every 250 m. Roughly
1 km sections are scored separately so a trip's net elevation does not mask short climbs.

```powershell
python scripts/run_grade_pilot.py data/raw/field-pilot/bmw-i3/drives.zip --output data/processed/field-pilot/bmw-results.json
python scripts/run_terrain_model_pilot.py data/raw/field-pilot/bmw-i3/drives.zip --output data/processed/field-pilot/bmw-terrain-model.json
```
