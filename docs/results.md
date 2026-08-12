# Results

## M3 synthetic benchmark

M3 was evaluated on 2026-08-09 with Python 3.11.9 using the offline, project-authored
`chargepath-m3-synthetic-v1` suite. The fixture contains eight hand-audited cases covering every
declared topology and boundary class. These results validate software behavior under the stated
model; they do not establish real-world SOC accuracy or charger availability.

Reproduction command:

```powershell
python scripts/run_m3_benchmark.py --warmups 3 --repetitions 20
```

Evidence identity:

- fixture SHA-256: `b805fe43dc89d3129e1126d1d5529b9bf5058ab0b4e8a86362caeccd472b980d`;
- manifest SHA-256: `8744707588e3afbd45d676678fd1b42c1da56c783ce72fed3266887c4457f9e7`;
- correctness artifact: `docs/evidence/m3/correctness_v1.json`, SHA-256
  `db55067b92a5904624dcf9fe90c5861561cc092b0b7754673ee101ef849572b6`;
- runtime artifact: `docs/evidence/m3/runtime_2026-08-09.json`, SHA-256
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

## Earlier foundation verification

M0-M0.2 established the typed model, exact objective variants, greedy competitor, independent replay,
deduplicated route options, and the shared synthetic demo. Those unit and demo results remain
functional evidence; M3 adds the versioned benchmark and measured artifacts above.
