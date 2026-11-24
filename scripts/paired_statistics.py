"""Write route-clustered intervals and signed-rank tests for the published real-route savings.

Reads only committed outcome files, so it needs neither the frozen graphs nor a planner run.
``tests/test_published_results.py`` checks that the committed output matches a fresh run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from chargepath.benchmarks.evidence import canonical_json_bytes
from chargepath.benchmarks.paired import build_paired_statistics

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REAL_ROUTES = REPOSITORY_ROOT / "docs" / "benchmarks" / "real-routes"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REAL_ROUTES / "paired_stats_v1.json")
    args = parser.parse_args()
    payload = build_paired_statistics(
        trips=REAL_ROUTES / "trips_v1.jsonl",
        baseline=REAL_ROUTES / "lookahead_baseline_v1.json",
    )
    args.output.write_bytes(canonical_json_bytes(payload))
    print(f"Paired statistics written to {args.output}")


if __name__ == "__main__":
    main()
