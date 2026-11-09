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
README = (ROOT / "README.md").read_text(encoding="utf-8")


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


def test_every_returned_plan_was_replayed_without_violations() -> None:
    results = [result for row in _rows() for result in row["results"].values()]
    plans = [result for result in results if result["outcome"] == "plan"]
    assert len(plans) == 2525
    assert all(result["reserve_violations"] == 0 for result in plans)
    assert "All 2,525 plans" in README
