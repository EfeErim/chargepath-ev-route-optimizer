"""Compare energy and charging models against TUM's ID1 public telemetry."""

from __future__ import annotations

import argparse
import hashlib
import math
from bisect import bisect_left
from pathlib import Path
from statistics import mean

from chargepath.energy import charging_minutes, energy_for_distance
from chargepath.models import VehicleProfile
from chargepath.real_route_benchmark import canonical_bytes

SOURCE_SHA256 = "76788d07fe5bb0b0af81ef9041fd7854e05de00b9ef34f616584ce623385e6c8"
CAPACITY_KWH = 58.0
VEHICLE_MAX_KW = 100.0
SOC_ID = 900
SPEED_ID = 4


def load_signals(path: Path) -> dict[int, dict[int, float]]:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    signals: dict[int, dict[int, float]] = {SPEED_ID: {}, SOC_ID: {}}
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(columns=["time", "value_id", "value"]):
        for signal, values in signals.items():
            selected = batch.filter(pc.equal(batch["value_id"], signal))
            times = pc.cast(selected["time"], "int64").to_pylist()
            for time_ns, value in zip(times, selected["value"].to_pylist(), strict=True):
                if time_ns is not None and value is not None and math.isfinite(value):
                    values[time_ns // 1_000_000_000] = value
    return signals


def sessions(soc: dict[int, float]) -> list[list[tuple[int, float]]]:
    groups: list[list[tuple[int, float]]] = []
    for time, value in sorted(soc.items()):
        if not 0 <= value <= 100:
            continue
        if not groups or time - groups[-1][-1][0] > 30:
            groups.append([])
        groups[-1].append((time, value))
    return groups


def extract(
    signals: dict[int, dict[int, float]],
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    drives, charges = [], []
    speed = signals[SPEED_ID]
    speed_times = sorted(speed)
    for group in sessions(signals[SOC_ID]):
        start, initial = group[0]
        end, final = group[-1]
        if end - start < 120:
            continue
        speed_window = speed_times[
            bisect_left(speed_times, start) : bisect_left(speed_times, end + 1)
        ]
        distance = 0.0
        coverage = 0
        for before, after in zip(speed_window, speed_window[1:], strict=False):
            delta = after - before
            if delta <= 3 and 0 <= speed[before] <= 200 and 0 <= speed[after] <= 200:
                distance += (speed[before] + speed[after]) * delta / 7200
                coverage += delta
        if distance >= 10 and initial - final >= 3 and coverage >= 0.9 * (end - start):
            avg_speed = distance * 3600 / coverage
            drives.append((distance, avg_speed, (initial - final) * CAPACITY_KWH / 100))
        elif distance <= 0.05 and not any(speed[time] > 3 for time in speed_window):
            # Trim leading and trailing stationary SOC plateaus.
            changes = [i for i in range(1, len(group)) if group[i][1] - group[i - 1][1] >= 0.05]
            if not changes:
                continue
            charge_start, from_soc = group[changes[0] - 1]
            charge_end, to_soc = group[changes[-1]]
            minutes = (charge_end - charge_start) / 60
            # Mean implied battery power above 20 kW indicates likely DC charging.
            if (
                minutes >= 3
                and to_soc - from_soc >= 8
                and 20 <= (to_soc - from_soc) * CAPACITY_KWH * 0.6 / minutes <= 110
                and all(
                    after[1] >= before[1] - 0.2
                    for before, after in zip(group, group[1:], strict=False)
                )
                and all(
                    after[1] - before[1] < 2
                    for before, after in zip(group, group[1:], strict=False)
                )
            ):
                charges.append((from_soc, to_soc, minutes))
    return drives, charges


def drive_metrics(drives: list[tuple[float, float, float]]) -> dict[str, object]:
    split = 2 * len(drives) // 3
    training, held_out = drives[:split], drives[split:]
    rates = [energy * 100 / distance for distance, _, energy in training]
    speed_features = [(speed / 100) ** 2 for _, speed, _ in training]
    center, constant = mean(speed_features), mean(rates)
    slope = sum(
        (feature - center) * (rate - constant)
        for feature, rate in zip(speed_features, rates, strict=True)
    ) / sum((feature - center) ** 2 for feature in speed_features)
    metrics: dict[str, object] = {
        "calibration_count": split,
        "holdout_count": len(held_out),
        "constant_rate": constant,
        "speed_model": {"feature_mean": center, "slope": slope},
    }
    for name in ("nominal_18", "nominal_18_with_margin", "fitted_constant", "fitted_speed"):
        errors = []
        for distance, speed, observed in held_out:
            rate = 18.0 if name.startswith("nominal_18") else constant
            if name == "fitted_speed":
                rate = max(1.0, constant + slope * ((speed / 100) ** 2 - center))
            margin = 1.10 if name == "nominal_18_with_margin" else 1.0
            errors.append(energy_for_distance(distance, rate, margin) - observed)
        metrics[name] = {
            "mae_kwh": mean(map(abs, errors)),
            "signed_bias_kwh": mean(errors),
            "soc_mae_points": mean(map(abs, errors)) * 100 / CAPACITY_KWH,
            "arrival_soc_overestimated_count": sum(error < -1e-9 for error in errors),
            "max_arrival_soc_overestimate_points": max(0.0, -min(errors)) * 100 / CAPACITY_KWH,
        }
    return metrics


def charge_metrics(charges: list[tuple[float, float, float]]) -> dict[str, object]:
    split = 2 * len(charges) // 3
    training, held_out = charges[:split], charges[split:]
    vehicle = VehicleProfile("ID.3", CAPACITY_KWH, 80, 18, VEHICLE_MAX_KW, 10)

    def errors(
        cases: list[tuple[float, float, float]], taper: float, factor: float, cap: float
    ) -> list[float]:
        return [
            charging_minutes(
                vehicle,
                cap,
                start,
                end,
                taper_start_pct=taper,
                taper_factor=factor,
                setup_minutes=0,
            )
            - minutes
            for start, end, minutes in cases
        ]

    taper, factor, cap = min(
        (
            (float(taper), factor / 10, float(cap))
            for taper in range(30, 86, 5)
            for factor in range(2, 10)
            for cap in range(40, 101, 5)
        ),
        key=lambda params: mean(map(abs, errors(training, *params))),
    )
    result: dict[str, object] = {
        "calibration_count": split,
        "holdout_count": len(held_out),
        "fitted_taper_start_pct": taper,
        "fitted_taper_factor": factor,
        "fitted_effective_cap_kw": cap,
    }
    for name, params in (
        ("generic", (80.0, 0.45, VEHICLE_MAX_KW)),
        ("fitted", (taper, factor, cap)),
    ):
        residuals = errors(held_out, *params)
        result[name] = {
            "mae_minutes": mean(map(abs, residuals)),
            "signed_bias_minutes": mean(residuals),
            "duration_underestimated_count": sum(error < 0 for error in residuals),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parquet", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.parquet.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("input does not match the TUM ID1 release")
    drives, charges = extract(load_signals(args.parquet))
    print(f"Screened drives={len(drives)}, likely DC sessions={len(charges)}")
    if len(drives) < 6 or len(charges) < 6:
        raise ValueError("insufficient screened sessions for a chronological holdout")
    result = {
        "source_sha256": SOURCE_SHA256,
        "driving": drive_metrics(drives),
        "charging": charge_metrics(charges),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(result))


if __name__ == "__main__":
    main()
