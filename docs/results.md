# Results

All numbers below are modeled minutes or kWh and come from the committed files linked in each
section.

## Real-route benchmark

The 2026-11-05 run used 24 routes in four distance bands, three vehicle parameter sets, one frozen
road graph and one static EPDK snapshot. Routes were chosen from province metadata and road
distances before the planner ran. Road distances derive from OpenStreetMap data, © OpenStreetMap
contributors, ODbL.

The [manifest](../data/benchmarks/real-routes/v1/manifest.json) defines experiments E1–E6. The
[summary](benchmarks/real-routes/summary_v1.json), [scenario outcomes](benchmarks/real-routes/trips_v1.jsonl)
and timings ([2026-11-24](benchmarks/real-routes/runtime_2026-11-24.json), earlier
[2026-11-05](benchmarks/real-routes/runtime_2026-11-05.json)) are the sources.

| Strategy | Plans | Infeasible | Provider failures |
|---|---:|---:|---:|
| Exact fastest | 624 | 0 | 0 |
| Exact shortest distance | 624 | 0 | 0 |
| Exact fewest charging sessions | 624 | 0 | 0 |
| Greedy fixed-80 | 616 | 8 | 0 |
| Road-only shortest driving time | 37 | 587 | 0 |

The 624 cases repeat the 24 routes under different settings. All 2,525 returned plans passed
replay with zero reserve violations, and exact fastest was never slower than another returned plan
on the same graph and SOC grid. Road-only infeasibility means the battery cannot cover that path
from the declared start SOC.

| Experiment | Result |
|---|---|
| E1 · Strategy comparison | On 71 paired scenarios exact fastest saved a median 24.7 min (4.2%) over fixed-80, p90 60.9 min (9.5%), maximum 98.5 min (16.0%). 6 ties; fixed-80 failed 1 of 72. |
| E2 · SOC grid | Against a 2% grid, the 5% grid added a median 3.3, 1.0 and 0.8 min for the 40, 60 and 80 kWh sets; 10% added 8.0, 2.5 and 2.6 min. Feasibility did not change. The grids are not nested, so the smallest 5% gap was −0.5 min. |
| E3 · Candidate cap | Against cap 200, caps 12, 24, 50 and 98 had median gaps of 2.7, 0.9, 0 and 0 min and maxima of 92.0, 18.9, 5.8 and 1.3 min. Greedy missed four plans at cap 12. |
| E4 · Corridor width | At cap 50, widths 10 and 25 km had a median gap of 0 against 50 km, ranging from −5.1 to 2.7 min, because width changes which stations survive the cap. |
| E5 · Vehicle inputs | On eight routes exact search stayed feasible at every tested value. Median total times at 50, 150 and 250 kW vehicle limits were 617.9, 551.0 and 542.5 min. |
| E6 · Runtime | Measured on 2026-11-24 after the search bookkeeping change, which returns the same plans. Wall time of one pass over 72 scenarios, median/p95: 5.98/6.41 s for exact fastest (about 0.08 s per scenario), 6.41/6.76 s for shortest distance, 6.92/7.72 s for fewest sessions, 5.57/5.64 s for greedy, 0.22/0.24 s for road-only. The 2026-11-05 run measured 21.3/27.5 s for exact fastest, 3.9/4.8 s for greedy and 0.32/0.56 s for road-only; greedy's revisit rule changed between the runs. OSRM was timed only on 2026-11-05: medians over 24 requests per pass of 0.65 s Route, 1.15 s Table. |

Driving is a median 87.5% of the greedy trip and is fixed by the road legs, so the percentage saving
understates the effect on the part a charging planner controls. Median charging time, including
the 2-minute session overhead, was 70.7 min for greedy and 53.0 for exact fastest; in the 66 paired
scenarios where greedy charged, the cut was a median 28.7% (range 0–100%). Median E1 savings by
distance band were 10.3, 19.8, 28.7 and 40.0 min, shortest band first.

### Where the saving comes from

The [follow-up](benchmarks/real-routes/followup_v1.json) keeps every greedy leg and optimizes only
how much to charge at greedy's stops (a stop may be skipped), then compares with joint
optimization. Each loaded graph is checked against the benchmark checksum.

| On the 71 paired scenarios | Median saving | Maximum saving |
|---|---:|---:|
| Fixed-80 to optimized charging on the same path | 9.6 min | 47.2 min |
| Same path to joint optimization | 10.5 min | 92.5 min |
| Fixed-80 to joint optimization | 24.7 min | 98.5 min |

The two gains add per scenario, not as medians.

| Stress case | Feasible | Median added time | Maximum added time |
|---|---:|---:|---:|
| Initial SOC 80% → 30% | 72/72 | 16.3 min | 29.4 min |
| Candidate cap 50 → 12 | 68/72 | 3.5 min | 91.1 min |
| First selected charger disabled before planning | 65/66 | 1.0 min | 5.2 min |

The outage case removes charging at the first selected site, keeps the candidate set and excludes
the six plans without charging. It models advance notice, not finding the charger down on arrival.
The artifact also records how many plans were replayed (419) and the replay's reserve-violation
count (0).

```powershell
python scripts/run_route_followup.py --output docs/benchmarks/real-routes/followup_v1.json
```

### Stronger heuristic baseline

Fixed-80 is a deliberately simple rule. The [baseline artifact](benchmarks/real-routes/lookahead_baseline_v1.json)
compares exact fastest with the [lookahead heuristic](algorithm.md#heuristics-and-baselines) on the
same 72 scenarios. Fixed-80 and exact fastest were rerun alongside and matched the committed
follow-up totals scenario by scenario.

| Exact fastest versus | Paired | Ties | Heuristic failures | Median saving | p90 | Maximum | Median heuristic charging |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fixed-80 | 71 | 6 | 1 | 24.7 min (4.2%) | 60.9 min (9.5%) | 98.5 min (16.0%) | 70.7 min |
| Lookahead | 72 | 22 | 0 | 2.6 min (0.5%) | 12.0 min (1.5%) | 26.5 min (2.7%) | 54.6 min |

Exact fastest charged a median 53.0 min. The heuristic closes about nine tenths of the median
fixed-80 gap and solved the scenario fixed-80 could not, so in the typical case exact search adds
little here; its value is the tail and the optimality guarantee on the graph and grid. All 215
returned plans replayed against their graphs with zero reserve violations.

```powershell
python scripts/run_baseline_comparison.py --output docs/benchmarks/real-routes/lookahead_baseline_v1.json
```

### Uncertainty of the median savings

The 72 scenarios are 24 routes driven by three vehicle sets, so scenarios on one route are not
independent. The [paired statistics](benchmarks/real-routes/paired_stats_v1.json) resample whole
routes (10,000 resamples, fixed seed) for a percentile interval of the pooled median, and run an
exact two-sided Wilcoxon signed-rank test on each route's mean saving. They read only the committed
outcome files.

| Exact fastest versus | Scenarios | Median saving | 95% interval, routes resampled | Routes with a nonzero mean saving | Signed-rank p |
|---|---:|---:|---:|---:|---:|
| Fixed-80 | 71 | 24.7 min | 16.1–29.5 min | 23 of 24, all positive | 2.4 × 10⁻⁷ |
| Lookahead | 72 | 2.6 min | 0.9–4.8 min | 21 of 24, all positive | 9.5 × 10⁻⁷ |

Exact fastest is never slower than a heuristic on the same graph and grid, so the test only says the
gain is not zero; the interval is the useful part. It covers these 24 routes, one road extract and
one charger snapshot, not other networks.

```powershell
python scripts/paired_statistics.py
```

### Speed choice

The [speed-choice artifact](benchmarks/real-routes/speed_choice_v1.json) reruns the 72 scenarios with
the opt-in `SpeedAwareConsumption`, which scales the constant rate by `1 − q + q × (v / v_ref)²`
with `q = 0.149` and `v_ref = 86.8 km/h` from the ID.3 pilot. The second run may also drive any leg
at 90% or 80% of its OSRM speed.

No scenario improved and no plan slowed a leg. Minimizing `d / v + d × (a + b v²) / P` per kilometre
gives `v³ = P / (2b)`: 179–193 km/h at a 50 kW charger for these vehicle sets, and still about
137 km/h at 22.5 kW in the taper, above OSRM's leg speeds. Slowing down can still help when it
avoids a stop or the next charger is weak, which these scenarios did not need. The fit is from one
car and ignores temperature, wind and grade.

```powershell
python scripts/run_speed_choice.py --output docs/benchmarks/real-routes/speed_choice_v1.json
```

## Field pilot · Volkswagen ID.3

One ID.3 Pro Performance from the [TUM usage dataset](https://github.com/TUMFTM/electric-vehicle-uds-dataset)
(Schreiber et al., [paper](https://doi.org/10.1016/j.etran.2025.100518)). The
[artifact](benchmarks/field-pilot/tum_id1_v1.json) splits chronologically: 240 calibration and 121
held-out driving windows, 16 and 9 likely DC charging windows.

| Driving model | Energy MAE | Arrival SOC MAE | SOC overestimated |
|---|---:|---:|---:|
| 18 kWh/100 km, raw | 1.27 kWh | 2.19 pp | 83/121 |
| 18 kWh/100 km, 10% margin | 1.05 kWh | 1.80 pp | 69/121 |
| Fitted constant rate | 1.09 kWh | 1.88 pp | 72/121 |
| Fitted speed model | 0.87 kWh | 1.49 pp | 70/121 |

The largest SOC overestimate fell from 11.46 pp with the nominal margin to 7.18 pp with the speed
model.

| Charging model | Duration MAE | Mean signed error | Underestimated |
|---|---:|---:|---:|
| Generic taper, 100 kW cap | 8.62 min | −8.61 min | 8/9 |
| Fitted 75 kW cap, taper at 65% | 2.72 min | −2.25 min | 5/9 |

Energy is a proxy from SOC change and the nominal 58 kWh capacity, and distance comes from recorded
speed, so the speed model sees the actual speed. There is no elevation or charger-type label, DC
windows are inferred, and timing excludes setup and queueing. Screening is in
[reproducing](reproducing.md#field-pilots).

### BMW i3 · Speed and elevation

Winter trips from Steinstraeter et al., [Battery and Heating Data in Real Driving Cycles](https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles),
compared with net battery energy from voltage and current
([artifact](benchmarks/field-pilot/bmw_grade_v1.json)). 35 of 38 trips passed screening; 11 were
held out, giving 195 sections of about 1 km, of which 3 exceed 12 m of vertical change per km.

The [terrain-model artifact](benchmarks/field-pilot/bmw_terrain_model_v1.json) tests the planner's
opt-in `GradeAwareConsumption` on the same split. It sees only distance, ascent and descent, with
1,400 kg, drive efficiency 0.85 and regen efficiency 0.65 set before the run; only the flat rate is
fitted. Its rule: become the default only if hillier-section error falls without raising whole-trip
or all-section error.

| Model | Whole-trip MAE (11) | Section MAE (195) | Hillier MAE (3) |
|---|---:|---:|---:|
| Fitted constant | 0.282 kWh | 0.0715 kWh | 0.225 kWh |
| Speed | 0.281 kWh | 0.0710 kWh | 0.234 kWh |
| Speed and net grade | 0.338 kWh | 0.0722 kWh | 0.225 kWh |
| Speed, net grade and total vertical change | 0.319 kWh | 0.0702 kWh | 0.148 kWh |
| Planner terrain model | 0.367 kWh | 0.0719 kWh | 0.194 kWh |

The terrain model lowered hillier-section error but raised whole-trip error, so it failed and stays
opt-in. Three hillier sections from one car in winter are too few to generalize either way. A second
run on 2026-11-08 reproduced the terrain artifact byte for byte.

## Property checks

`tests/test_properties.py` generates trips of two to six nodes with forward, backward and cyclic
legs, mixed charger powers, 5–25% SOC grids and decimal vehicle inputs. A reference solver written
from [the algorithm notes](algorithm.md) recomputes every bucket and charge time in exact rational
arithmetic. The checks:

- **differential:** feasibility and the lexicographic optimum of all three objectives match the
  reference, and every plan passes both the library replay and an exact replay;
- **dominance:** exact fastest is never slower than any other strategy that returns a plan;
- **metamorphic:** more start SOC, a lower reserve, an extra charger, doubled power or an extra
  direct leg never makes a trip infeasible or slower; relabeling nodes changes no objective;
  repeated runs give identical plans.

The gate runs a fixed sample of 150 cases per property. A randomized run on 2026-11-19 with 4,000
per property (20,000 in total) found no disagreement:

```powershell
$env:CHARGEPATH_PROPERTY_EXAMPLES = "4000"; python -m pytest -q tests/test_properties.py
```

## Synthetic benchmark

Eight hand-audited cases (`chargepath-m3-synthetic-v1`) covering every declared topology and
boundary class, run with Python 3.11.9; the timings below are from 2026-11-24. The
[correctness artifact](benchmarks/synthetic/correctness_v1.json) is regenerated byte for byte by
`tests/test_evaluation.py`; the [runtime artifact](benchmarks/synthetic/runtime_2026-11-24.json)
holds every sample. The [2026-11-18 run](benchmarks/synthetic/runtime_2026-11-18.json), before the
search bookkeeping change, is kept for comparison.

All 22 returned plans out of 40 outcomes passed replay. Each exact objective solved all 5 feasible
cases and rejected all 3 infeasible ones; greedy fixed-80 solved all 5; the road-only baseline
solved only the 2 that need no charging.

| Case | Exact fastest | Greedy fixed-80 | Exact charging | Greedy charging |
|---|---:|---:|---:|---:|
| One stop | 174.0 min | 178.8 min | 14.0 min | 18.8 min |
| Multi-stop | 268.0 min | 272.8 min | 28.0 min | 32.8 min |
| Detour choice | 134.0 min | 138.8 min | 14.0 min | 18.8 min |

In the detour case exact fastest takes the 280 km fast corridor (134.0 min) and shortest distance
the 270 km slow one (175.8 min). A 140 km boundary case is infeasible at 10% and 5% SOC steps and
feasible at 2%, arriving at the 12% reserve. On the small detour graph the corridor selector kept
`fast_near`, dropped `slow_far`, and pruned and unpruned searches returned the same plan.

| Algorithm (20 eight-case passes, 3 warm-ups) | Median | p95 | Median on 2026-11-18 |
|---|---:|---:|---:|
| Shortest driving time, no charging | 0.1240 ms | 0.1541 ms | 0.1163 ms |
| Exact fastest time | 2.1463 ms | 2.6677 ms | 6.5737 ms |
| Exact shortest distance | 2.2382 ms | 2.3234 ms | 6.7200 ms |
| Exact fewest sessions | 2.2437 ms | 2.3444 ms | 6.7192 ms |
| Greedy fixed-80 | 0.3075 ms | 0.5721 ms | 0.3266 ms |
| Composed option planner | 6.6037 ms | 6.6651 ms | 20.0380 ms |

These timings are from one Windows machine with 12 logical CPUs and are regression evidence only.

```powershell
python scripts/run_synthetic_benchmark.py --warmups 3 --repetitions 20
```
