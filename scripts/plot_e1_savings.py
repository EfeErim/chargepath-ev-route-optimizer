"""Draw the README's E1 savings chart from the committed real-route outcomes.

The chart shows, for every scenario where both planners found a plan, how many modeled minutes the
exact fastest plan saved against charging to 80% at each stop. One panel per vehicle set, scenarios
sorted by saving, with that panel's median. The output is plain SVG with no dependencies, and is
byte-identical for identical inputs; ``tests/test_published_results.py`` checks the committed copy.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TRIPS = REPOSITORY_ROOT / "docs" / "benchmarks" / "real-routes" / "trips_v1.jsonl"
OUTPUT = REPOSITORY_ROOT / "docs" / "images" / "e1-savings.svg"
GROUPS = (("set-40kwh", "40 kWh set"), ("set-60kwh", "60 kWh set"), ("set-80kwh", "80 kWh set"))

WIDTH = 960
HEIGHT = 360
TOP = 92
BOTTOM = 300
LEFT = 56
PANEL_GAP = 28
Y_MAX = 100.0
Y_TICKS = (0, 25, 50, 75, 100)

STYLE = """
  .surface { fill: #fcfcfb; }
  .title { fill: #0b0b0b; font: 600 17px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .subtitle { fill: #52514e; font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .panel { fill: #0b0b0b; font: 600 13px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .tick { fill: #898781; font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .note { fill: #52514e; font: 12px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .median-label { fill: #0b0b0b; font: 600 12px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .grid { stroke: #e1e0d9; stroke-width: 1; }
  .axis { stroke: #c3c2b7; stroke-width: 1; }
  .bar { fill: #2a78d6; }
  .median { stroke: #0b0b0b; stroke-width: 1.5; stroke-dasharray: 5 4; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .title, .panel, .median-label { fill: #f0efec; }
    .subtitle, .note { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; }
    .axis { stroke: #383835; }
    .bar { fill: #3987e5; }
    .median { stroke: #f0efec; }
  }
"""


def paired_savings(trips: Path = TRIPS) -> dict[str, list[float]]:
    """Minutes saved per E1 scenario where greedy found a plan, grouped by vehicle set."""

    savings: dict[str, list[float]] = {group: [] for group, _ in GROUPS}
    for line in trips.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["experiment"] != "E1":
            continue
        greedy = row["results"]["greedy_fixed_80"]
        if greedy["outcome"] != "plan":
            continue
        fastest = row["results"]["fastest"]
        savings[row["group"]].append(greedy["total_minutes"] - fastest["total_minutes"])
    return savings


def _y(minutes: float) -> float:
    return BOTTOM - (BOTTOM - TOP) * minutes / Y_MAX


def render(savings: dict[str, list[float]]) -> str:
    every = [value for values in savings.values() for value in values]
    if max(every) > Y_MAX:
        raise ValueError("a saving exceeds the chart's fixed y range")
    panel_width = (WIDTH - LEFT - 24 - PANEL_GAP * (len(GROUPS) - 1)) / len(GROUPS)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-labelledby="title desc">',
        '<title id="title">Modeled minutes saved by the exact planner, per scenario</title>',
        f'<desc id="desc">{len(every)} real-route scenarios where both the exact fastest planner '
        "and the charge-to-80% heuristic found a plan, split by vehicle set and sorted by saving. "
        f"Overall median {statistics.median(every):.1f} minutes, maximum {max(every):.1f}."
        "</desc>",
        f"<style>{STYLE}</style>",
        f'<rect class="surface" width="{WIDTH}" height="{HEIGHT}" rx="8"/>',
        '<text class="title" x="24" y="34">Minutes saved per trip against charging to 80% '
        "at every stop</text>",
        f'<text class="subtitle" x="24" y="56">{len(every)} real-route scenarios, sorted within '
        "each vehicle set. Dashed line: that set's median. Modeled minutes, not measured "
        "drives.</text>",
    ]
    for tick in Y_TICKS:
        y = _y(tick)
        parts.append(
            f'<line class="grid" x1="{LEFT}" x2="{WIDTH - 24}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{tick}</text>'
        )
    parts.append(
        f'<text class="tick" x="16" y="{(TOP + BOTTOM) / 2:.1f}" text-anchor="middle" '
        f'transform="rotate(-90 16 {(TOP + BOTTOM) / 2:.1f})">minutes saved</text>'
    )
    for index, (group, label) in enumerate(GROUPS):
        values = sorted(savings[group], reverse=True)
        x0 = LEFT + index * (panel_width + PANEL_GAP)
        slot = panel_width / len(values)
        bar = max(1.0, slot - 2)
        parts.append(
            f'<text class="panel" x="{x0:.1f}" y="{TOP - 12}">{label} · {len(values)} '
            "scenarios</text>"
        )
        for position, value in enumerate(values):
            x = x0 + position * slot + (slot - bar) / 2
            height = BOTTOM - _y(value)
            parts.append(
                f'<rect class="bar" x="{x:.1f}" y="{_y(value):.1f}" width="{bar:.1f}" '
                f'height="{height:.1f}"><title>{value:.1f} min</title></rect>'
            )
        median = statistics.median(values)
        y = _y(median)
        parts.append(
            f'<line class="median" x1="{x0:.1f}" x2="{x0 + panel_width:.1f}" '
            f'y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="median-label" x="{x0 + panel_width:.1f}" y="{y - 6:.1f}" '
            f'text-anchor="end">median {median:.1f} min</text>'
        )
        parts.append(
            f'<line class="axis" x1="{x0:.1f}" x2="{x0 + panel_width:.1f}" '
            f'y1="{BOTTOM}" y2="{BOTTOM}"/>'
        )
    parts.append(
        f'<text class="note" x="24" y="{HEIGHT - 22}">Ties show as empty slots at zero. The one '
        "scenario where the heuristic found no plan is left out. Source: "
        "docs/benchmarks/real-routes/trips_v1.jsonl.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.write_bytes(render(paired_savings()).encode("utf-8"))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
