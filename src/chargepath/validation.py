"""Shared input validation for configuration values and parsed JSON documents.

Every planner, replay helper, and loader validates the same kinds of values. Keeping the checks here
means a bound is stated once and every caller rejects the same inputs with the same message.
"""

from __future__ import annotations

import math
from typing import Any, TypeGuard


def is_finite_number(value: object) -> TypeGuard[int | float]:
    """Return whether ``value`` is a finite int or float that is not a boolean."""

    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def trip_endpoints(origin_id: object, destination_id: object) -> None:
    if (
        not isinstance(origin_id, str)
        or not isinstance(destination_id, str)
        or not origin_id.strip()
        or not destination_id.strip()
    ):
        raise ValueError("origin_id and destination_id must not be empty")


def require_finite(value: object, *, field: str) -> float:
    if not is_finite_number(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def bounded_number(value: object, *, field: str, minimum: float, maximum: float) -> float:
    """Return a finite number within an inclusive range."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    return number


def soc_step(value: object) -> int:
    """Validate a SOC grid step: a positive integer that divides 100."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or 100 % value != 0:
        raise ValueError("soc_step_pct must be a positive divisor of 100")
    return value


def charge_model(
    *,
    taper_start_pct: object,
    taper_factor: object,
    session_setup_minutes: object,
) -> tuple[float, float, float]:
    """Validate the generic charging-curve parameters shared by every strategy and replay."""

    if not is_finite_number(taper_start_pct) or not 0 < float(taper_start_pct) < 100:
        raise ValueError("taper_start_pct must be between 0 and 100")
    if not is_finite_number(taper_factor) or not 0 < float(taper_factor) <= 1:
        raise ValueError("taper_factor must be between 0 and 1")
    if not is_finite_number(session_setup_minutes) or float(session_setup_minutes) < 0:
        raise ValueError("session_setup_minutes must not be negative")
    return float(taper_start_pct), float(taper_factor), float(session_setup_minutes)


def json_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object")
    return value


def json_array(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def exact_fields(row: dict[str, Any], *, field: str, expected: set[str]) -> dict[str, Any]:
    """Require exactly the expected keys, naming any missing or unexpected ones."""

    actual = set(row)
    if actual != expected:
        details: list[str] = []
        if missing := sorted(expected - actual):
            details.append(f"missing {missing}")
        if unexpected := sorted(actual - expected):
            details.append(f"unexpected {unexpected}")
        raise ValueError(f"{field} fields are invalid: {', '.join(details)}")
    return row


def exact_object(value: Any, *, field: str, expected: set[str]) -> dict[str, Any]:
    return exact_fields(json_object(value, field=field), field=field, expected=expected)


def text(value: Any, *, field: str) -> str:
    """Return non-blank text unchanged; callers that normalize whitespace strip it themselves."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return int(value)


def boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value
