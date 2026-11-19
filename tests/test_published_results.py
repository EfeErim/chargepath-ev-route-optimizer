"""Recompute the README's headline numbers from the committed per-scenario outcomes.

The road graphs behind the real-route benchmark are not in Git, so a reader cannot rerun the
planner on them. They can check that the published numbers follow from the committed outcome file.
"""

from __future__ import annotations

import importlib.util
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from chargepath.evidence import nearest_rank

ROOT = Path(__file__).resolve().parents[1]
TRIPS = ROOT / "docs/benchmarks/real-routes/trips_v1.jsonl"
FOLLOWUP = ROOT / "docs/benchmarks/real-routes/followup_v1.json"
# Whitespace is collapsed so line wrapping in the README does not matter.
README = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())


def _rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in TRIPS.read_text(encoding="utf-8").splitlines()]


def test_e1_savings_match_the_readme() -> None:
    e1 = [row for row in _rows() if row["experiment"] == "E1"]
    savings: list[float] = []
    percent: list[float] = []
    greedy_failures = 0
    for row in e1:
        fastest = row["results"]["fastest"]
        greedy = row["results"]["greedy_fixed_80"]
        assert fastest["outcome"] == "plan"
        if greedy["outcome"] != "plan":
            greedy_failures += 1
            continue
        saved = greedy["total_minutes"] - fastest["total_minutes"]
        assert saved >= -1e-9, "exact fastest must never be slower than greedy"
        savings.append(saved)
        percent.append(100 * saved / greedy["total_minutes"])

    assert len(e1) == 72
    assert len(savings) == 71
    assert greedy_failures == 1
    assert sum(1 for saved in savings if abs(saved) < 1e-9) == 6
    assert round(statistics.median(savings), 1) == 24.7
    assert round(statistics.median(percent), 1) == 4.2
    assert round(max(savings), 1) == 98.5
    assert "median **24.7 minutes (4.2%) faster**" in README
    assert "71 of 72 scenarios" in README


def test_charging_time_share_matches_the_readme() -> None:
    driving_share: list[float] = []
    fastest_charging: list[float] = []
    greedy_charging: list[float] = []
    charging_cut: list[float] = []
    for row in _rows():
        if row["experiment"] != "E1":
            continue
        fastest = row["results"]["fastest"]
        greedy = row["results"]["greedy_fixed_80"]
        if greedy["outcome"] != "plan":
            continue
        driving_share.append(100 * greedy["driving_minutes"] / greedy["total_minutes"])
        fastest_charging.append(fastest["charging_minutes"])
        greedy_charging.append(greedy["charging_minutes"])
        if greedy["charging_minutes"] > 0:
            cut = greedy["charging_minutes"] - fastest["charging_minutes"]
            charging_cut.append(100 * cut / greedy["charging_minutes"])

    assert len(charging_cut) == 66
    assert round(statistics.median(driving_share), 1) == 87.5
    assert round(statistics.median(greedy_charging), 1) == 70.7
    assert round(statistics.median(fastest_charging), 1) == 53.0
    assert round(statistics.median(charging_cut), 1) == 28.7
    assert "median 87.5% of the trip" in README
    assert "from 70.7 to 53.0 minutes, a median cut of 28.7% in the 66 scenarios" in README


def test_every_returned_plan_was_replayed_without_violations() -> None:
    results = [result for row in _rows() for result in row["results"].values()]
    plans = [result for result in results if result["outcome"] == "plan"]
    assert len(plans) == 2525
    assert all(result["reserve_violations"] == 0 for result in plans)
    assert "All 2,525 plans" in README


def test_committed_savings_chart_matches_the_outcomes() -> None:
    spec = importlib.util.spec_from_file_location(
        "plot_e1_savings", ROOT / "scripts/plot_e1_savings.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    savings = script.paired_savings(TRIPS)
    assert sum(len(values) for values in savings.values()) == 71
    committed = (ROOT / "docs/images/e1-savings.svg").read_bytes().decode("utf-8")
    assert committed == script.render(savings), "rerun scripts/plot_e1_savings.py"
    assert "docs/images/e1-savings.svg" in README


def test_lookahead_baseline_matches_the_readme() -> None:
    artifact = json.loads(
        (ROOT / "docs/benchmarks/real-routes/lookahead_baseline_v1.json").read_text(
            encoding="utf-8"
        )
    )
    savings: list[float] = []
    percent: list[float] = []
    for row in artifact["scenarios"]:
        exact, lookahead = row["exact_fastest"], row["lookahead"]
        assert lookahead is not None, "the lookahead heuristic solved every E1 scenario"
        saved = lookahead["total_minutes"] - exact["total_minutes"]
        assert saved >= -1e-9, "exact fastest must never be slower than the heuristic"
        savings.append(saved)
        percent.append(100 * saved / lookahead["total_minutes"])

    assert len(savings) == 72
    assert sum(1 for saved in savings if abs(saved) <= 1e-6) == 22
    assert round(statistics.median(savings), 1) == 2.6
    assert round(statistics.median(percent), 1) == 0.5
    assert round(nearest_rank(savings, 0.9), 1) == 12.0
    assert round(max(savings), 1) == 26.5
    # Exact fastest and the lookahead heuristic planned all 72 scenarios, fixed-80 planned 71.
    assert artifact["replayed_plans"] == 72 + 72 + 71 == 215
    assert artifact["reserve_violations_on_returned_plans"] == 0
    assert "median **2.6 minutes (0.5%) faster**, tied in 22 of 72 scenarios" in README
    assert "at most 26.5 minutes faster" in README
    assert "p90 12.0 min" in README


def test_followup_decomposition_and_candidate_cap_match_the_readme() -> None:
    scenarios = json.loads(FOLLOWUP.read_text(encoding="utf-8"))["scenarios"]
    assert len(scenarios) == 72

    paired = [row for row in scenarios if row.get("greedy_min") is not None]
    assert len(paired) == 71
    charge_amounts = [row["greedy_min"] - row["fixed_path_min"] for row in paired]
    free_path = [row["fixed_path_min"] - row["nominal_min"] for row in paired]
    assert round(statistics.median(charge_amounts), 1) == 9.6
    assert round(statistics.median(free_path), 1) == 10.5
    assert "medians of 9.6 min and 10.5 min" in README

    sparse = [
        row["sparse_candidates_min"] - row["nominal_min"]
        for row in scenarios
        if row.get("sparse_candidates_min") is not None
    ]
    assert len(scenarios) - len(sparse) == 4
    assert round(statistics.median(sparse), 1) == 3.5
    assert round(max(sparse)) == 91
    assert (
        "lowering it from 50 to 12 cost a median 3.5 minutes, up to 91 minutes, "
        "and made 4 of 72 scenarios infeasible"
    ) in README


def test_soc_grid_cost_matches_the_readme() -> None:
    minutes: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for row in _rows():
        if row["experiment"] == "E2":
            fastest = row["results"]["fastest"]
            assert fastest["outcome"] == "plan"
            key = (row["route_id"], row["group"])
            minutes[key][row["soc_step_pct"]] = fastest["total_minutes"]

    medians = {
        group: statistics.median(
            steps[5] - steps[2] for (_, row_group), steps in minutes.items() if row_group == group
        )
        for group in ("set-40kwh", "set-60kwh", "set-80kwh")
    }
    assert (round(min(medians.values()), 1), round(max(medians.values()), 1)) == (0.8, 3.3)
    assert "the 5% grid added a median 0.8 to 3.3 minutes against a 2% grid" in README
