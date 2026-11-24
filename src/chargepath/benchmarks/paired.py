"""Paired statistics over the committed real-route outcomes, computed without rerunning a planner.

The 72 E1 scenarios are 24 routes driven by three vehicle sets, so scenarios on the same route are
not independent. Both statistics here treat the route as the unit:

- the confidence interval for the median saving resamples whole routes (cluster bootstrap) and
  takes the median of the pooled scenario savings in each resample;
- the signed-rank test runs on each route's mean saving, with the exact permutation distribution
  of the observed ranks instead of a normal approximation.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261122
CONFIDENCE = 0.95


def cluster_bootstrap_median(
    savings_by_route: Mapping[str, Sequence[float]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = CONFIDENCE,
) -> tuple[float, float]:
    """Percentile interval for the median saving, resampling routes with replacement."""

    routes = sorted(savings_by_route)
    rng = random.Random(seed)
    medians = sorted(
        statistics.median(
            value
            for route in rng.choices(routes, k=len(routes))
            for value in savings_by_route[route]
        )
        for _ in range(resamples)
    )
    tail = (1 - confidence) / 2
    low = medians[math.floor(tail * resamples)]
    high = medians[math.ceil((1 - tail) * resamples) - 1]
    return low, high


def wilcoxon_signed_rank(differences: Sequence[float]) -> dict[str, Any]:
    """Two-sided exact Wilcoxon signed-rank test; zero differences are dropped.

    Tied magnitudes get average ranks, and the null distribution is enumerated over those ranks,
    so the p-value stays exact with ties.
    """

    nonzero = [value for value in differences if value != 0]
    count = len(nonzero)
    order = sorted(range(count), key=lambda index: abs(nonzero[index]))
    # Doubled ranks keep average ranks integral.
    doubled = [0] * count
    position = 0
    while position < count:
        end = position
        while end + 1 < count and abs(nonzero[order[end + 1]]) == abs(nonzero[order[position]]):
            end += 1
        for index in order[position : end + 1]:
            doubled[index] = position + end + 2
        position = end + 1
    w_plus = sum(rank for rank, value in zip(doubled, nonzero, strict=True) if value > 0)

    # Number of sign assignments reaching each doubled positive-rank sum.
    counts = {0: 1}
    for rank in doubled:
        shifted = dict(counts)
        for total, ways in counts.items():
            shifted[total + rank] = shifted.get(total + rank, 0) + ways
        counts = shifted
    total_ways = 2**count
    mean = sum(doubled) / 2
    distance = abs(w_plus - mean)
    extreme = sum(ways for total, ways in counts.items() if abs(total - mean) >= distance)
    return {
        "nonzero_pairs": count,
        "positive": sum(1 for value in nonzero if value > 0),
        "negative": sum(1 for value in nonzero if value < 0),
        "w_plus": w_plus / 2,
        "p_value": float(Fraction(extreme, total_ways)),
    }


def _comparison(savings: Sequence[tuple[str, float]]) -> dict[str, Any]:
    by_route: dict[str, list[float]] = defaultdict(list)
    for route, saving in savings:
        by_route[route].append(saving)
    route_means = [statistics.fmean(by_route[route]) for route in sorted(by_route)]
    low, high = cluster_bootstrap_median(by_route)
    test = wilcoxon_signed_rank(route_means)
    return {
        "scenarios": len(savings),
        "routes": len(by_route),
        "median_saving_min": round(statistics.median(value for _, value in savings), 3),
        "median_ci95_min": [round(low, 3), round(high, 3)],
        "route_mean_signed_rank": {
            **{key: value for key, value in test.items() if key != "p_value"},
            "w_plus": round(test["w_plus"], 3),
            "p_value": float(f"{test['p_value']:.3g}"),
        },
    }


def fixed80_savings(trips: Path) -> list[tuple[str, float]]:
    """(route, minutes saved by exact fastest over fixed-80) per E1 scenario with both plans."""

    savings = []
    for line in trips.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["experiment"] != "E1":
            continue
        greedy, fastest = row["results"]["greedy_fixed_80"], row["results"]["fastest"]
        if greedy["outcome"] == "plan" and fastest["outcome"] == "plan":
            savings.append((row["route_id"], greedy["total_minutes"] - fastest["total_minutes"]))
    return savings


def lookahead_savings(baseline: Path) -> list[tuple[str, float]]:
    """(route, minutes saved by exact fastest over the lookahead heuristic) per E1 scenario."""

    artifact = json.loads(baseline.read_text(encoding="utf-8"))
    return [
        (row["route"], row["lookahead"]["total_minutes"] - row["exact_fastest"]["total_minutes"])
        for row in artifact["scenarios"]
        if row["lookahead"] is not None and row["exact_fastest"] is not None
    ]


def build_paired_statistics(*, trips: Path, baseline: Path) -> dict[str, Any]:
    return {
        "inputs": {
            "fixed_80": "docs/benchmarks/real-routes/trips_v1.jsonl",
            "lookahead": "docs/benchmarks/real-routes/lookahead_baseline_v1.json",
        },
        "method": {
            "saving": "other strategy's modeled total minutes minus exact fastest's, per scenario",
            "interval": (
                "percentile interval of the pooled median over route-level resamples with "
                "replacement"
            ),
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "confidence": CONFIDENCE,
            "test": (
                "two-sided exact Wilcoxon signed-rank test on per-route mean savings, zero "
                "differences dropped, average ranks for ties"
            ),
        },
        "exact_vs_fixed_80": _comparison(fixed80_savings(trips)),
        "exact_vs_lookahead": _comparison(lookahead_savings(baseline)),
    }
