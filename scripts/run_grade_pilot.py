"""Test a speed-and-grade energy model on held-out BMW i3 winter drives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import mean, variance

from chargepath.real_route_benchmark import canonical_bytes

SOURCE_SHA256 = "fb59fa697344794321319c7e9d73968d563ee78500f2b173d445f78ff3cc40f5"
COLUMNS = (
    "Time [s]",
    "Velocity [km/h]",
    "Elevation [m]",
    "Battery Voltage [V]",
    "Battery Current [A]",
)


@dataclass(frozen=True)
class Drive:
    distance_km: float
    energy_kwh: float
    speed_feature: float
    grade_pct: float
    vertical_m_per_km: float

    @property
    def consumption(self) -> float:
        return 100 * self.energy_kwh / self.distance_km


Sample = tuple[float, ...]


def read_samples(source: io.TextIOBase) -> list[Sample]:
    reader = csv.DictReader(source, delimiter=";")
    if reader.fieldnames:
        reader.fieldnames = [
            "Velocity [km/h]" if name == "Velocity [km/h]]]" else name for name in reader.fieldnames
        ]
    return [tuple(float(row[name]) for name in COLUMNS) for row in reader]


def summarize(samples: list[Sample], *, min_km: float) -> Drive | None:
    if len(samples) < 2 or not all(math.isfinite(value) for row in samples for value in row):
        return None
    distance_km = energy_kwh = speed_square_km = 0.0
    elevations = [samples[0][2]]
    next_sample_km = 0.25
    for before, after in pairwise(samples):
        time0, speed0, elevation0, voltage0, current0 = before
        time1, speed1, elevation1, voltage1, current1 = after
        dt = time1 - time0
        if dt < 0 or dt > 2:
            return None
        leg_km = (speed0 + speed1) * dt / 7200
        while leg_km > 0 and distance_km + leg_km >= next_sample_km:
            fraction = (next_sample_km - distance_km) / leg_km
            elevations.append(elevation0 + fraction * (elevation1 - elevation0))
            next_sample_km += 0.25
        distance_km += leg_km
        speed_square_km += leg_km * ((speed0 + speed1) / 200) ** 2
        # Negative current in the source denotes energy leaving the pack.
        energy_kwh -= (voltage0 * current0 + voltage1 * current1) * dt / 7_200_000
    if distance_km < min_km:
        return None
    elevations.append(samples[-1][2])
    return Drive(
        distance_km,
        energy_kwh,
        speed_square_km / distance_km,
        (samples[-1][2] - samples[0][2]) / (10 * distance_km),
        sum(abs(after - before) for before, after in pairwise(elevations)) / distance_km,
    )


def slope(x: list[float], y: list[float]) -> float:
    x_mean, y_mean = mean(x), mean(y)
    return sum((left - x_mean) * (right - y_mean) for left, right in zip(x, y, strict=True)) / (
        (len(x) - 1) * variance(x)
    )


def evaluate(training: list[Drive], held_out: list[Drive]) -> dict[str, object]:
    if len(training) < 6 or len(held_out) < 3:
        raise ValueError("insufficient valid BMW trips")
    speeds = [drive.speed_feature for drive in training]
    grades = [drive.grade_pct for drive in training]
    vertical = [drive.vertical_m_per_km for drive in training]
    rates = [drive.consumption for drive in training]
    speed_center, grade_center, constant = mean(speeds), mean(grades), mean(rates)
    speed_only = slope(speeds, rates)
    grade_on_speed = slope(speeds, grades)
    residual_grades = [
        grade - grade_center - grade_on_speed * (speed - speed_center)
        for speed, grade in zip(speeds, grades, strict=True)
    ]
    grade_coefficient = slope(residual_grades, rates)
    adjusted_speed = speed_only - grade_coefficient * grade_on_speed
    vertical_center = mean(vertical)
    vertical_on_speed = slope(speeds, vertical)
    vertical_on_grade = slope(residual_grades, vertical)
    residual_vertical = [
        rise
        - vertical_center
        - vertical_on_speed * (speed - speed_center)
        - vertical_on_grade * grade
        for rise, speed, grade in zip(vertical, speeds, residual_grades, strict=True)
    ]
    vertical_coefficient = slope(residual_vertical, rates)
    full_grade = grade_coefficient - vertical_coefficient * vertical_on_grade
    full_speed = speed_only - full_grade * grade_on_speed - vertical_coefficient * vertical_on_speed
    hill_threshold = 12.0
    hill_count = sum(drive.vertical_m_per_km >= hill_threshold for drive in held_out)
    result: dict[str, object] = {
        "calibration_count": len(training),
        "holdout_count": len(held_out),
        "holdout_grade_pct_min": min(drive.grade_pct for drive in held_out),
        "holdout_grade_pct_max": max(drive.grade_pct for drive in held_out),
        "fitted_constant_rate": constant,
        "speed_center": speed_center,
        "grade_center": grade_center,
        "speed_only_coefficient": speed_only,
        "speed_and_grade_speed_coefficient": adjusted_speed,
        "speed_and_grade_grade_coefficient": grade_coefficient,
        "full_speed_coefficient": full_speed,
        "full_grade_coefficient": full_grade,
        "full_vertical_coefficient": vertical_coefficient,
        "vertical_center": vertical_center,
        "holdout_vertical_m_per_km_min": min(drive.vertical_m_per_km for drive in held_out),
        "holdout_vertical_m_per_km_max": max(drive.vertical_m_per_km for drive in held_out),
        "hillier_threshold_m_per_km": hill_threshold,
        "holdout_hillier_count": hill_count,
    }
    for name, speed_coef, grade_coef, vertical_coef in (
        ("constant", 0.0, 0.0, 0.0),
        ("speed", speed_only, 0.0, 0.0),
        ("speed_and_grade", adjusted_speed, grade_coefficient, 0.0),
        ("speed_and_terrain", full_speed, full_grade, vertical_coefficient),
    ):
        errors = [
            (
                constant
                + speed_coef * (drive.speed_feature - speed_center)
                + grade_coef * (drive.grade_pct - grade_center)
                + vertical_coef * (drive.vertical_m_per_km - vertical_center)
            )
            * drive.distance_km
            / 100
            - drive.energy_kwh
            for drive in held_out
        ]
        model_metrics = {
            "mae_kwh": mean(map(abs, errors)),
            "bias_kwh": mean(errors),
            "energy_underestimated": sum(error < 0 for error in errors),
            "max_underestimate_kwh": max(0.0, -min(errors)),
        }
        for label, hillier in (("flatter", False), ("hillier", True)):
            subset = [
                abs(error)
                for error, drive in zip(errors, held_out, strict=True)
                if (drive.vertical_m_per_km >= hill_threshold) == hillier
            ]
            if subset:
                model_metrics[f"{label}_mae_kwh"] = mean(subset)
        result[name] = model_metrics
    return result


def sections(samples: list[Sample]) -> list[Drive]:
    """Group each trip into non-overlapping windows of approximately one kilometer."""
    start = 0
    distance_km = 0.0
    result = []
    for end in range(1, len(samples)):
        before, after = samples[end - 1], samples[end]
        distance_km += (before[1] + after[1]) * (after[0] - before[0]) / 7200
        if distance_km >= 1:
            section = summarize(samples[start : end + 1], min_km=0.99)
            if section is not None:
                result.append(section)
            start = end
            distance_km = 0.0
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("input does not match the BMW driving archive")
    drives = []
    grouped_sections = []
    with zipfile.ZipFile(args.archive) as archive:
        members = sorted(
            name for name in archive.namelist() if re.fullmatch(r"TripB\d+\.csv", name)
        )
        for name in members:
            with archive.open(name) as raw, io.TextIOWrapper(raw, encoding="latin-1") as source:
                try:
                    samples = read_samples(source)
                    trip = summarize(samples, min_km=5)
                except ValueError:
                    trip = None
            if trip is not None and trip.energy_kwh > 0:
                drives.append(trip)
                grouped_sections.append(sections(samples))
    # Hold out complete trips, including all their sections.
    training_indices = [index for index in range(len(drives)) if index % 3 != 2]
    holdout_indices = list(range(2, len(drives), 3))
    result = {
        "source_sha256": SOURCE_SHA256,
        "winter_trips": len(members),
        "accepted_trips": len(drives),
        "whole_trip": evaluate(
            [drives[index] for index in training_indices],
            [drives[index] for index in holdout_indices],
        ),
        "one_km_sections": evaluate(
            [section for index in training_indices for section in grouped_sections[index]],
            [section for index in holdout_indices for section in grouped_sections[index]],
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(result))
    print(f"BMW winter trips accepted: {len(drives)}/{len(members)}")


if __name__ == "__main__":
    main()
