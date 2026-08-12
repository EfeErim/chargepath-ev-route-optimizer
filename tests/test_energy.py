import math

import pytest

from chargepath.energy import (
    charging_minutes,
    energy_for_distance,
    required_energy_buckets,
)
from chargepath.models import ChargingStop, VehicleProfile


@pytest.fixture
def vehicle() -> VehicleProfile:
    return VehicleProfile(
        name="Test EV",
        usable_battery_kwh=60,
        initial_soc_pct=80,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
    )


def test_energy_model_applies_safety_factor() -> None:
    assert energy_for_distance(100, 20, 1.1) == pytest.approx(22)


def test_required_buckets_rounds_up_conservatively() -> None:
    assert required_energy_buckets(6.01, 3) == 3
    assert required_energy_buckets(6, 3) == 2


def test_any_positive_energy_requires_at_least_one_bucket() -> None:
    assert required_energy_buckets(1e-15, 3) == 1


def test_charging_above_taper_threshold_is_slower(vehicle: VehicleProfile) -> None:
    below_taper = charging_minutes(vehicle, 150, 60, 80, setup_minutes=0)
    above_taper = charging_minutes(vehicle, 150, 80, 100, setup_minutes=0)
    assert above_taper > below_taper


def test_zero_energy_charge_has_no_session_overhead(vehicle: VehicleProfile) -> None:
    assert charging_minutes(vehicle, 150, 50, 50, setup_minutes=2) == 0


def test_vehicle_connectors_are_normalized() -> None:
    profile = VehicleProfile(
        name="EV",
        usable_battery_kwh=60,
        initial_soc_pct=80,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
        supported_dc_connectors=(" ccs2 ",),
    )
    assert profile.supported_dc_connectors == ("CCS2",)


def test_invalid_charging_stop_is_rejected() -> None:
    with pytest.raises(ValueError, match="arrival < departure"):
        ChargingStop("hub", 80, 70, 1, 1)


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, True])
def test_domain_and_energy_boundaries_reject_non_finite_numbers(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        VehicleProfile(
            name="Invalid EV",
            usable_battery_kwh=invalid,
            initial_soc_pct=80,
            consumption_kwh_per_100km=20,
            max_dc_power_kw=150,
        )
    with pytest.raises(ValueError, match="finite"):
        energy_for_distance(invalid, 20)
    with pytest.raises(ValueError, match="finite"):
        required_energy_buckets(1, invalid)
