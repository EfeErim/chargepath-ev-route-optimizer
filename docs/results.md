# Results

## Real-route benchmark

The 2026-11-05 run used 24 routes across four distance bands, three declared vehicle parameter
sets, and a single frozen road graph and station snapshot. Routes were selected before running the
planner, using only province metadata and road distances. Road distances derive from OpenStreetMap
data, © OpenStreetMap contributors, ODbL. Results are modeled minutes on that graph and a static
EPDK snapshot. SOC, arrival time, and charger availability require separate field evidence.

The [frozen manifest](../data/benchmarks/m6/v1/manifest.json) defines E1–E6. The
[normalized summary](benchmarks/real-routes/summary_v1.json), [scenario outcomes](benchmarks/real-routes/trips_v1.jsonl),
and [machine timings](benchmarks/real-routes/runtime_2026-11-05.json) are the sources for these tables.

| Strategy | Plan outcomes | Infeasible | Provider failures |
|---|---:|---:|---:|
| Exact fastest | 624 | 0 | 0 |
| Exact shortest distance | 624 | 0 | 0 |
| Exact fewest charging sessions | 624 | 0 | 0 |
| Greedy fixed-80 | 616 | 8 | 0 |
| Road-only shortest driving time | 37 | 587 | 0 |

The 624 cases include multiple configurations of the same 24 routes. All 2,525 returned plan
outcomes passed independent replay with zero reserve violations. Shared inputs were computed and
replayed once. Exact fastest was at least as fast as every other returned plan on the same graph and
SOC grid. Road-only infeasibility means insufficient modeled battery for that path at the declared
starting SOC. The shortest-distance objective compares the distances of the legs supplied by OSRM.

| Experiment | Observed result |
|---|---|
| E1 · Strategy comparison | On 71 paired plans, median savings versus fixed-80 greedy were 24.7 min (4.2%), p90 60.9 min (9.5%), and maximum 98.5 min (16.0%). Six ties and one greedy failure out of 72 scenarios. |
| E2 · SOC grid | Versus 2%, the 5% grid added a median 3.3, 1.0, and 0.8 min for the 40, 60, and 80 kWh sets. The 10% grid added 8.0, 2.5, and 2.6 min. Exact feasibility stayed the same. The 5% and 2% grids are non-nested, and the minimum 5% gap was −0.5 min. |
| E3 · Candidate cap | Versus cap 200, median gaps for caps 12, 24, 50, and 98 were 2.7, 0.9, 0, and 0 min. Maxima were 92.0, 18.9, 5.8, and 1.3 min. Cap 200 is a finite reference. Greedy missed four plans at cap 12. |
| E4 · Corridor width | With cap 50, widths 10 and 25 km both had a median gap of 0 versus 50 km. Gaps ranged from −5.1 to 2.7 min. A wider corridor can change which stations survive the cap. |
| E5 · Vehicle inputs | On eight routes, exact search stayed feasible at every tested value. Median total times at the 50, 150, and 250 kW vehicle limits were 617.9, 551.0, and 542.5 min. |
| E6 · Runtime | Over 72 scenarios per pass, median/p95 were 21.3/27.5 s for exact fastest, 3.9/4.8 s for greedy, and 0.32/0.56 s for road-only. OSRM was timed separately over 24 requests per pass, with medians of 0.65 s for Route and 1.15 s for Table. These are machine-specific batch timings. |

Driving was a median 87.5% of the greedy trip time, and that share is fixed by the road legs, so the
percentage saving understates the effect on the part a charging planner controls. Median charging
time, including the 2-minute session overhead, was 70.7 minutes for greedy and 53.0 for exact
fastest. In the 66 paired scenarios where greedy charged, exact fastest cut charging time by a
median 28.7% (range 0–100%). The total saving also includes path changes, so it is not only charging
time.

Median E1 savings by distance band were 10.3, 19.8, 28.7, and 40.0 minutes, from the shortest to the
longest band. The paired comparison measures the effect of replacing one specified heuristic and
combines changes in stop selection and charging amounts.

The exact v1 output requires its original frozen inputs. The repository distributes derived results
and provenance metadata. See the [rerun procedure](reproducing.md#real-route-rerun-procedure).

### Follow-up · Fixed-path charging and stress cases

The [follow-up artifact](benchmarks/real-routes/followup_v1.json) uses the E1 scenarios and verifies each
loaded graph against the recorded benchmark checksum. The intermediate strategy keeps every greedy road
leg in order and optimizes how much to charge at the visits where greedy charged. The solver may
skip a charging session there. Every returned plan is replayed, and for paired cases the script
checks the modeled time ordering from greedy through fixed-path to joint optimization.

| Comparison on the 71 paired scenarios | Median saving | Maximum saving |
|---|---:|---:|
| Fixed-80 to optimized charging on the same path | 9.6 min | 47.2 min |
| Fixed-path optimization to joint optimization | 10.5 min | 92.5 min |
| Fixed-80 to joint optimization | 24.7 min | 98.5 min |

The two gains add per scenario. Their separate medians need not add to the median joint gain.
The second row measures the value of freeing the path and charging-site choices together.

| Stress case | Feasible | Median added time among feasible plans | Maximum added time |
|---|---:|---:|---:|
| Initial SOC lowered from 80% to 30% | 72/72 | 16.3 min | 29.4 min |
| Candidate cap lowered from 50 to 12 | 68/72 | 3.5 min | 91.1 min |
| First selected charger disabled before planning | 65/66 | 1.0 min | 5.2 min |

The outage scenario keeps road legs and removes charging at the first selected site. The six
plans without charging are outside its denominator. This tests replanning with advance notice
and retains the original candidate set. It does not simulate discovering the outage on arrival.
A single pass across 72 nominal inputs measured median/p95 of 165/268 ms per exact search with
replay on the recording machine, excluding graph loading, OSRM, and browser work.

```powershell
python scripts/run_route_followup.py --output data/processed/m6/v1/followup-rerun.json
```

### Follow-up · Speed choice

The [speed-choice artifact](benchmarks/real-routes/speed_choice_v1.json) reruns the 72 E1 scenarios
on the checksum-verified graphs with the opt-in `SpeedAwareConsumption` model. It scales each
vehicle's constant rate by `1 − q + q × (v / v_ref)²`, with `q = 0.149` and `v_ref = 86.8 km/h` from
the ID.3 pilot's squared-speed fit. Both runs use that model. The second may also drive any leg at 90%
or 80% of its OSRM speed, and exact search picks the fastest combination.

Speed choice improved none of the 72 scenarios; no plan slowed a single leg. Every plan replayed
under the same model. This is the expected result under this model, not a search failure.
Minimizing `d / v + d × (a + b v²) / P` per kilometre gives an optimal speed of `v³ = P / (2b)`.
With the pilot's quadratic term and the vehicle sets' rates, that is 179–193 km/h at a 50 kW
charger and still about 137 km/h at a 50 kW charger in the taper (22.5 kW), above OSRM's leg speeds. Slowing down can still matter when it avoids a stop entirely or
when the next charger is weak, which these scenarios did not require. The result depends on one
vehicle's fit, which ignores temperature, wind, and grade.

```powershell
python scripts/run_speed_choice.py --output docs/benchmarks/real-routes/speed_choice_v1.json
```

## Field pilot · Volkswagen ID.3

The pilot uses one Volkswagen ID.3 Pro Performance from the [TUM usage dataset](https://github.com/TUMFTM/electric-vehicle-uds-dataset)
by Schreiber et al. ([eTransportation paper](https://doi.org/10.1016/j.etran.2025.100518)).
The [aggregate artifact](benchmarks/field-pilot/tum_id1_v1.json) records a chronological split.
There are 240 calibration and 121 held-out driving windows, and 16 calibration and nine held-out
likely DC charging windows. These are separately screened windows from the same vehicle.

| Driving model | Energy proxy MAE | Arrival SOC MAE | Arrival SOC overestimated |
|---|---:|---:|---:|
| 18 kWh/100 km, raw | 1.27 kWh | 2.19 pp | 83/121 |
| 18 kWh/100 km, 10% margin | 1.05 kWh | 1.80 pp | 69/121 |
| Fitted constant rate | 1.09 kWh | 1.88 pp | 72/121 |
| Fitted speed model | 0.87 kWh | 1.49 pp | 70/121 |

The largest SOC overestimate fell from 11.46 pp with the nominal margin to 7.18 pp with the
speed model. The speed model still overestimated SOC in 70 held-out windows.

| Charging model | Duration MAE | Mean signed error | Duration underestimated |
|---|---:|---:|---:|
| Generic taper with 100 kW assumed cap | 8.62 min | −8.61 min | 8/9 |
| Fitted effective cap and taper | 2.72 min | −2.25 min | 5/9 |

Observed kWh is a proxy computed from the measured SOC change and nominal 58 kWh usable capacity.
Distance comes from integrated recorded speed. The speed model therefore has access to actual
driving speed during evaluation. Route elevation and charger-type labels are absent. Charging
windows are screened from stationary SOC rises, and the inferred DC classification may be wrong.
The fitted effective cap of 75 kW and taper at 65% can reflect the sampled chargers and battery
conditions. Timing excludes setup and queueing. The results motivate testing on additional vehicles,
planned rather than recorded speeds, elevation, and labeled DC sessions before changing planner
defaults. See [pilot procedure](reproducing.md#field-pilot-procedure).

### BMW i3 · Speed and elevation

The [BMW pilot artifact](benchmarks/field-pilot/bmw_grade_v1.json) uses the winter portion of
Steinstraeter et al.'s [Battery and Heating Data in Real Driving Cycles](https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles).
It compares predictions with net battery energy integrated from recorded voltage and current.
Of 38 winter trips, 35 passed screening. Eleven complete trips were held out from fitting.
The held-out trips also provided 195 approximately one-kilometer sections. Three sections
exceeded the declared hillier threshold of 12 m of vertical change per km.

| Model | Whole-trip MAE, 11 trips | Section MAE, 195 sections | Hillier MAE, 3 sections |
|---|---:|---:|---:|
| Fitted constant | 0.282 kWh | 0.0715 kWh | 0.225 kWh |
| Speed | 0.281 kWh | 0.0710 kWh | 0.234 kWh |
| Speed and net grade | 0.338 kWh | 0.0722 kWh | 0.225 kWh |
| Speed, net grade, and total vertical change | 0.319 kWh | 0.0702 kWh | 0.148 kWh |

The full terrain model lowered error on the three hillier sections, but raised whole-trip error
versus the constant model. It underestimated energy on 86/195 sections versus 78/195 for the
constant model. The comparison holds out trips from one car and uses observed speed and elevation.
The hillier sample is too small to generalize the observed improvement to other routes.

### BMW i3 · Planner terrain model

The [terrain-model artifact](benchmarks/field-pilot/bmw_terrain_model_v1.json) tests the planner's
opt-in `GradeAwareConsumption` model (`src/chargepath/terrain.py`) on the same data, screening, and
held-out trips as the pilot above. Unlike the pilot's regression models, it sees only what a planner
could know before driving: distance, ascent, and descent. Its physical constants were declared
before the first run and not fitted: 1,400 kg, drive efficiency 0.85, and regen efficiency 0.65.
Only the flat rate is fitted on calibration data. The decision rule was also fixed in advance: the
model may become a planner default only if it lowers hillier-section error without raising
whole-trip or all-section error.

| Model | Whole-trip MAE, 11 trips | Section MAE, 195 sections | Hillier MAE, 3 sections |
|---|---:|---:|---:|
| Fitted constant | 0.282 kWh | 0.0715 kWh | 0.225 kWh |
| Terrain model | 0.367 kWh | 0.0719 kWh | 0.194 kWh |

The terrain model lowered error on the three hillier sections but raised whole-trip error, so it
failed the rule and stays experimental. The constant model remains the planner default. As in the
pilot above, three hillier sections from one car in winter are too few to generalize either way.

```powershell
python scripts/run_terrain_model_pilot.py data/raw/field-pilot/bmw-i3/drives.zip --output data/processed/field-pilot/bmw-terrain-model.json
```

A second run on 2026-11-08 reproduced the artifact byte for byte (SHA-256
`aef6dd92256aa3ec51b017f0ebf505fc32bdc725213ff46d612b905aa6d73129`).

## Differential and property checks

`tests/test_properties.py` generates small trips with Hypothesis: two to six nodes, forward,
backward, and cyclic legs, mixed charger powers, 5–25% SOC grids, and decimal vehicle inputs. A
reference solver written from [the algorithm description](algorithm.md) recomputes every energy
bucket and charge duration with exact rational arithmetic and shares no code with
`chargepath.energy`. The checks are:

- **differential:** feasibility and the lexicographic optimum of all three exact objectives match the
  reference, and every returned plan passes both the library replay and an exact-arithmetic replay;
- **dominance:** exact fastest is never slower than shortest distance, fewest sessions, greedy
  fixed-80, or the road-only baseline when those return a plan;
- **metamorphic:** a higher starting SOC, a lower reserve, an extra charger, doubled charger and
  vehicle power, or an extra direct leg never makes a feasible trip infeasible or slower; relabeling
  nodes leaves every objective unchanged; repeated runs return identical plans.

The gate runs a fixed derandomized sample of 150 cases per property. A deeper randomized run on
2026-11-08 used 4,000 cases per property (20,000 in total), about half of them feasible and about 8%
with two or more charging stops. It found no disagreement, including between floating-point and exact
bucket rounding:

```powershell
$env:CHARGEPATH_PROPERTY_EXAMPLES = "4000"; python -m pytest -q tests/test_properties.py
```

These checks cover the modeled problem only. They say nothing about the accuracy of the energy or
charging model.

## Synthetic benchmark

The synthetic benchmark was evaluated on 2026-08-09 with Python 3.11.9 using the offline, project-authored
`chargepath-m3-synthetic-v1` suite. The fixture contains eight hand-audited cases covering every
declared topology and boundary class. These results validate software behavior under the stated
model; they do not establish real-world SOC accuracy or charger availability.

Reproduction command:

```powershell
python scripts/run_synthetic_benchmark.py --warmups 3 --repetitions 20
```

Evidence identity:

- fixture SHA-256: `b805fe43dc89d3129e1126d1d5529b9bf5058ab0b4e8a86362caeccd472b980d`;
- manifest SHA-256: `8744707588e3afbd45d676678fd1b42c1da56c783ce72fed3266887c4457f9e7`;
- correctness artifact: `docs/benchmarks/synthetic/correctness_v1.json`, SHA-256
  `db55067b92a5904624dcf9fe90c5861561cc092b0b7754673ee101ef849572b6`;
- runtime artifact: `docs/benchmarks/synthetic/runtime_2026-08-09.json`, SHA-256
  `04bc47528c72c3facca037403f243e3234f7d7bc14752f64869d82ad73704386`.

### Correctness

The suite produced 40 algorithm/case outcomes. All 22 returned plans passed independent replay; no
reserve violation or false-feasible result was observed. Each exact objective matched all five
feasible and all three infeasible reference labels. The road-only baseline returned only the two
feasible cases that required no charging, which is expected because it never inserts charge actions.

| Algorithm | Feasible references solved | Returned plans verified | False feasible |
|---|---:|---:|---:|
| Shortest driving time, no charging | 2 / 5 | 2 / 2 | 0 |
| Exact fastest time | 5 / 5 | 5 / 5 | 0 |
| Exact shortest distance | 5 / 5 | 5 / 5 | 0 |
| Exact fewest sessions | 5 / 5 | 5 / 5 | 0 |
| Greedy fixed-80 | 5 / 5 | 5 / 5 | 0 |

### Charging-case comparison

On all three feasible cases that required charging, exact fastest-time used partial charging and was
4.8 modeled minutes faster than greedy fixed-80. This supports the narrow synthetic-suite hypothesis;
it is not a general performance claim. The road-only baseline was infeasible on these cases, so no
trip-time comparison against it is reported.

| Case | Exact fastest | Greedy fixed-80 | Difference | Exact charge time | Greedy charge time |
|---|---:|---:|---:|---:|---:|
| One stop | 174.0 min | 178.8 min | 4.8 min | 14.0 min | 18.8 min |
| Multi-stop | 268.0 min | 272.8 min | 4.8 min | 28.0 min | 32.8 min |
| Detour choice | 134.0 min | 138.8 min | 4.8 min | 14.0 min | 18.8 min |

The detour case also demonstrates objective separation: exact fastest selected the 280 km fast
corridor at 134.0 minutes, while exact shortest-distance selected the 270 km slow corridor at 175.8
minutes. Different strategies still deduplicate when their complete actionable plans are identical.

### SOC-grid sensitivity

The fixed 140 km boundary case changes only `soc_step_pct`. It is rejected at 10% and 5% resolution
but feasible at 2%, arriving at the 12% reserve. This confirms the documented risk that conservative
discretization can reject a narrowly feasible route.

| SOC step | Feasible | Total minutes | Arrival SOC |
|---:|---|---:|---:|
| 10% | No | — | — |
| 5% | No | — | — |
| 2% | Yes | 84.0 | 12% |

### Candidate-pruning audit

On the declared small detour graph, the corridor selector retained `fast_near` and excluded
`slow_far`. Pruned and unpruned fastest-time searches returned the same verified 134.0-minute plan,
so the observed pruning gap was 0.0 minutes. This one audit does not prove that pruning preserves the
best plan on other graphs or configurations.

### Runtime protocol

Runtime was measured with `time.perf_counter_ns`, three warm-ups, and 20 complete eight-case suite
passes per algorithm. The machine ran CPython 3.11.9 on Windows, AMD64, with 12 logical CPUs. Median
and nearest-rank p95 are reported; individual samples remain in the runtime artifact.

| Algorithm | Median | p95 |
|---|---:|---:|
| Shortest driving time, no charging | 0.0619 ms | 0.0642 ms |
| Exact fastest time | 2.9196 ms | 2.9344 ms |
| Exact shortest distance | 3.0779 ms | 3.1058 ms |
| Exact fewest sessions | 3.0874 ms | 3.1381 ms |
| Greedy fixed-80 | 0.1823 ms | 0.2114 ms |
| Composed competitive option planner | 9.1702 ms | 9.2808 ms |

Runtime values describe this tiny synthetic suite on one machine. They are regression evidence, not
production latency or scaling evidence.
