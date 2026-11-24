from __future__ import annotations

import itertools
from fractions import Fraction

import pytest

from chargepath.benchmarks.paired import cluster_bootstrap_median, wilcoxon_signed_rank


def _brute_force_p(differences: list[float]) -> float:
    """Two-sided p-value by flipping every sign over the observed average ranks."""

    nonzero = [value for value in differences if value != 0]
    magnitudes = sorted(abs(value) for value in nonzero)
    ranks = {
        magnitude: Fraction(
            sum(index + 1 for index, other in enumerate(magnitudes) if other == magnitude),
            magnitudes.count(magnitude),
        )
        for magnitude in magnitudes
    }
    signed = [ranks[abs(value)] for value in nonzero]
    observed = sum(
        (rank for rank, value in zip(signed, nonzero, strict=True) if value > 0), Fraction(0)
    )
    mean = sum(signed, Fraction(0)) / 2
    extreme = 0
    for signs in itertools.product((False, True), repeat=len(signed)):
        total = sum(
            (rank for rank, positive in zip(signed, signs, strict=True) if positive), Fraction(0)
        )
        extreme += abs(total - mean) >= abs(observed - mean)
    return float(Fraction(extreme, 2 ** len(signed)))


@pytest.mark.parametrize(
    "differences",
    [
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [1.5, -2.0, 3.0, 0.0, 4.25, -0.5, 6.0],
        [2.0, 2.0, -2.0, 1.0, 1.0, 3.0, -3.0, 0.0, 5.0],
        [-1.0, -2.0, 0.5, -4.0, -4.0, -6.0, 7.0, 8.0],
    ],
)
def test_exact_signed_rank_matches_enumeration(differences: list[float]) -> None:
    result = wilcoxon_signed_rank(differences)
    assert result["p_value"] == pytest.approx(_brute_force_p(differences), abs=1e-15)
    assert result["nonzero_pairs"] == result["positive"] + result["negative"]


def test_all_positive_signed_rank_has_the_smallest_two_sided_p() -> None:
    result = wilcoxon_signed_rank([1.0, 2.0, 3.0, 4.0, 5.0])
    assert result["p_value"] == 2 / 2**5
    assert result["w_plus"] == 15


def test_cluster_bootstrap_is_deterministic_and_brackets_the_median() -> None:
    savings = {"a": [1.0, 2.0, 3.0], "b": [10.0, 11.0], "c": [5.0], "d": [0.0, 7.0]}
    first = cluster_bootstrap_median(savings, resamples=2000, seed=7)
    assert first == cluster_bootstrap_median(savings, resamples=2000, seed=7)
    low, high = first
    assert 0.0 <= low <= 4.0 <= high <= 11.0
