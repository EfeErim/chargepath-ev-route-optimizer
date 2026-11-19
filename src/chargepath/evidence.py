"""Serialization and summary statistics shared by every committed evidence file."""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable
from typing import Any


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize normalized evidence deterministically for byte-for-byte comparisons."""

    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def nearest_rank(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def time_passes(run_pass: Callable[[], None], *, warmups: int, repetitions: int) -> list[float]:
    """Run the warm-up passes, then return each measured pass in milliseconds."""

    for _ in range(warmups):
        run_pass()
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        run_pass()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return samples


def timing_summary(samples: list[float]) -> dict[str, Any]:
    return {
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(nearest_rank(samples, 0.95), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
    }


def summary_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p90": None, "minimum": None, "maximum": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median": round(statistics.median(ordered), 3),
        "p90": round(nearest_rank(ordered, 0.9), 3),
        "minimum": round(ordered[0], 3),
        "maximum": round(ordered[-1], 3),
    }
