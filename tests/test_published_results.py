"""Recompute the README's headline numbers from the committed per-scenario outcomes.

The road graphs behind the real-route benchmark are not in Git, so a reader cannot rerun the
planner on them. They can check that the published numbers follow from the committed outcome file.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRIPS = ROOT / "docs/benchmarks/real-routes/trips_v1.jsonl"
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
