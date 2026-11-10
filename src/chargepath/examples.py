"""Example vehicle inputs derived from the project's public-telemetry field pilot.

These are examples for opt-in models, not calibrations. They come from one Volkswagen ID.3 Pro
Performance in the TUM usage dataset (Schreiber et al., https://doi.org/10.1016/j.etran.2025.100518,
https://github.com/TUMFTM/electric-vehicle-uds-dataset at commit
6bac2936e280b2d7f45607e5cb624a18b41b3faa, CC BY-NC 4.0). Only aggregate fitted parameters are
used; they are recorded in ``docs/benchmarks/field-pilot/tum_id1_v1.json``. The pilot's charging
windows are inferred from SOC rises, the charger types are unknown, and the fit ignores temperature,
preconditioning, and power sharing.
"""

from __future__ import annotations

from chargepath.models import VehicleProfile

# Fitted on training windows: 75 kW effective cap, half power from 65% SOC. The drop starts at
# 64.9% so the curve never exceeds the fitted step, which keeps modeled charge times conservative.
ID3_PILOT_CHARGING_CURVE: tuple[tuple[float, float], ...] = (
    (0.0, 75.0),
    (64.9, 75.0),
    (65.0, 37.5),
    (100.0, 37.5),
)

ID3_PILOT_PROFILE = VehicleProfile(
    name="ID.3 pilot example",
    usable_battery_kwh=58.0,
    initial_soc_pct=80.0,
    # Fitted constant driving rate on the pilot's training windows, before the safety factor.
    consumption_kwh_per_100km=19.39,
    max_dc_power_kw=100.0,
    charging_curve=ID3_PILOT_CHARGING_CURVE,
)
