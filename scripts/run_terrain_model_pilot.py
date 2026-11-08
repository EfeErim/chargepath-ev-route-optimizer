"""Evaluate the planner's experimental terrain model on held-out BMW i3 winter drives.

Pre-registered before the first run (2026-11-08):

- data, screening, and the trip-level holdout split are those of ``run_grade_pilot.py``;
- the model is ``chargepath.terrain.GradeAwareConsumption`` with fixed, uncalibrated constants:
  mass 1,400 kg (declared vehicle plus occupant), drive efficiency 0.85, regen efficiency 0.65;
- only the flat rate is fitted, on calibration data, after removing the modeled climb and regen
  energy; the model sees distance, ascent, and descent only, never recorded speed;
- the comparison baseline is the fitted constant model from the same split;
- the model may become a planner default only if, on held-out data, it lowers hillier-section MAE
  and does not raise whole-trip MAE or all-section MAE. Otherwise it stays experimental.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_grade_pilot as pilot  # noqa: E402

from chargepath.real_route_benchmark import canonical_bytes  # noqa: E402
from chargepath.terrain import GradeAwareConsumption, LegElevation  # noqa: E402

MASS_KG = 1_400.0
DRIVE_EFFICIENCY = 0.85
REGEN_EFFICIENCY = 0.65
HILL_THRESHOLD_M_PER_KM = 12.0


def elevation_of(drive: pilot.Drive) -> LegElevation:
    net_m = drive.grade_pct * 10 * drive.distance_km
    total_m = drive.vertical_m_per_km * drive.distance_km
    return LegElevation(
        ascent_m=max(0.0, (total_m + net_m) / 2),
        descent_m=max(0.0, (total_m - net_m) / 2),
    )


def terrain_kwh(model: GradeAwareConsumption, drive: pilot.Drive) -> float:
    climb, regen = model.climb_and_regen_kwh(elevation_of(drive))
    return climb - regen


def compare(training: list[pilot.Drive], held_out: list[pilot.Drive]) -> dict[str, object]:
    model = GradeAwareConsumption(
        elevations=(),
        mass_kg=MASS_KG,
        drive_efficiency=DRIVE_EFFICIENCY,
        regen_efficiency=REGEN_EFFICIENCY,
    )
    constant_rate = mean(drive.consumption for drive in training)
    flat_rate = mean(
        100 * (drive.energy_kwh - terrain_kwh(model, drive)) / drive.distance_km
        for drive in training
    )
    result: dict[str, object] = {
        "calibration_count": len(training),
        "holdout_count": len(held_out),
        "holdout_hillier_count": sum(
            drive.vertical_m_per_km >= HILL_THRESHOLD_M_PER_KM for drive in held_out
        ),
        "fitted_constant_rate": constant_rate,
        "terrain_flat_rate": flat_rate,
    }
    predictions = {
        "constant": [constant_rate * drive.distance_km / 100 for drive in held_out],
        "terrain": [
            flat_rate * drive.distance_km / 100 + terrain_kwh(model, drive) for drive in held_out
        ],
    }
    for name, predicted in predictions.items():
        errors = [
            value - drive.energy_kwh for value, drive in zip(predicted, held_out, strict=True)
        ]
        metrics: dict[str, float | int] = {
            "mae_kwh": mean(map(abs, errors)),
            "bias_kwh": mean(errors),
            "energy_underestimated": sum(error < 0 for error in errors),
            "max_underestimate_kwh": max(0.0, -min(errors)),
        }
        hillier = [
            abs(error)
            for error, drive in zip(errors, held_out, strict=True)
            if drive.vertical_m_per_km >= HILL_THRESHOLD_M_PER_KM
        ]
        if hillier:
            metrics["hillier_mae_kwh"] = mean(hillier)
        result[name] = metrics
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != pilot.SOURCE_SHA256:
            raise ValueError("input does not match the BMW driving archive")
    drives: list[pilot.Drive] = []
    grouped_sections: list[list[pilot.Drive]] = []
    with zipfile.ZipFile(args.archive) as archive:
        members = sorted(
            name for name in archive.namelist() if re.fullmatch(r"TripB\d+\.csv", name)
        )
        for name in members:
            with archive.open(name) as raw, io.TextIOWrapper(raw, encoding="latin-1") as source:
                try:
                    samples = pilot.read_samples(source)
                    trip = pilot.summarize(samples, min_km=5)
                except ValueError:
                    trip = None
            if trip is not None and trip.energy_kwh > 0:
                drives.append(trip)
                grouped_sections.append(pilot.sections(samples))
    training = [index for index in range(len(drives)) if index % 3 != 2]
    holdout = list(range(2, len(drives), 3))
    whole = compare([drives[i] for i in training], [drives[i] for i in holdout])
    sections = compare(
        [section for i in training for section in grouped_sections[i]],
        [section for i in holdout for section in grouped_sections[i]],
    )

    def mae(block: dict[str, object], model: str, key: str = "mae_kwh") -> float:
        metrics = block[model]
        assert isinstance(metrics, dict)
        return float(metrics[key])

    checks = {
        "hillier_sections_improve": mae(sections, "terrain", "hillier_mae_kwh")
        < mae(sections, "constant", "hillier_mae_kwh"),
        "whole_trip_not_worse": mae(whole, "terrain") <= mae(whole, "constant"),
        "all_sections_not_worse": mae(sections, "terrain") <= mae(sections, "constant"),
    }
    result = {
        "source_sha256": pilot.SOURCE_SHA256,
        "accepted_trips": len(drives),
        "declared_constants": {
            "mass_kg": MASS_KG,
            "drive_efficiency": DRIVE_EFFICIENCY,
            "regen_efficiency": REGEN_EFFICIENCY,
            "hillier_threshold_m_per_km": HILL_THRESHOLD_M_PER_KM,
        },
        "whole_trip": whole,
        "one_km_sections": sections,
        "decision_checks": checks,
        "eligible_as_default": all(checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(result))
    print(f"Eligible as planner default: {result['eligible_as_default']} {checks}")


if __name__ == "__main__":
    main()
