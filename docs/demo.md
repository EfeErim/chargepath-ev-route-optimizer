# Demo run

Every block below was produced by running the command on 2026-11-09 (Python 3.11.9, Windows). The output is pasted unedited, except that test progress dots are trimmed and temporary paths are shortened to `<tmp>`.

Plan the bundled synthetic trip from the command line.

```text
$ python examples/run_synthetic_demo.py
ChargePath synthetic route options

Option 1: fastest, fewest_charging_stops
Nodes: origin -> fast_hub -> destination
Charge at Fast Hub: 20% -> 75% (15.2 min)
Distance: 310.0 km
Driving: 190.0 min
Charging: 15.2 min
Total: 205.2 min
Arrival SOC: 10%

Option 2: shortest_distance
Nodes: origin -> slow_hub -> destination
Charge at Slow Hub: 25% -> 70% (34.4 min)
Distance: 280.0 km
Driving: 205.0 min
Charging: 34.4 min
Total: 239.4 min
Arrival SOC: 10%

Option 3: greedy_fixed_80
Nodes: origin -> fast_hub -> destination
Charge at Fast Hub: 20% -> 80% (16.4 min)
Distance: 310.0 km
Driving: 190.0 min
Charging: 16.4 min
Total: 206.4 min
Arrival SOC: 15%
```

Run the test suite.

```text
$ python -m pytest -q -p no:cacheprovider
217 passed in 38.02s
```

Recompute the README headline from the committed real-route outcomes.

```text
$ python -m pytest -v -p no:cacheprovider --no-header tests/test_published_results.py
tests/test_published_results.py::test_e1_savings_match_the_readme PASSED [ 50%]
tests/test_published_results.py::test_every_returned_plan_was_replayed_without_violations PASSED [100%]
============================== 2 passed in 0.61s ==============================
```

Regenerate the synthetic benchmark. The correctness artifact hash matches the committed file.

```text
$ python scripts/run_synthetic_benchmark.py --correctness-out <tmp>/correctness.json --runtime-out <tmp>/runtime.json --warmups 1 --repetitions 3
Correctness results: <tmp>\correctness.json (sha256=db55067b92a5904624dcf9fe90c5861561cc092b0b7754673ee101ef849572b6)
Runtime results: <tmp>\runtime.json (sha256=490b3930d33aacd713dfb30be2c1daad4782d2cdae777f86eae54c65cce27675)
```

Rerun the real-route follow-up on the frozen road graphs. Those graphs are kept outside Git (see
[reproducing](reproducing.md#real-route-rerun-procedure)), so this block was run on the author's
machine and a clone cannot repeat it. The rerun output is byte-identical to the committed artifact.

```text
$ python scripts/run_route_followup.py --output <tmp>/followup.json
Scenarios: 72. Exact + replay median/p95: 161.6/226.2 ms
$ cmp <tmp>/followup.json docs/benchmarks/real-routes/followup_v1.json && echo "files are identical"
files are identical
```
