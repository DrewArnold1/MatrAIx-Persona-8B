"""Tests for the margin of error on categorical answer distributions.

A share with no interval invites reading a 3-point gap between two options as
real. These pin the arithmetic and, more importantly, the denominator: for
select-all-that-apply the counts sum past the number of respondents, and
dividing by their sum would silently shrink every interval.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (
    REPO_ROOT / "application" / "playground",
    REPO_ROOT / "packages" / "playground" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from backend.service.job_aggregation import (  # noqa: E402
    _aggregate_categorical,
    wilson_interval,
)


def _entries(values: list[object]) -> list[dict[str, object]]:
    return [{"value": value} for value in values]


class TestWilsonInterval:
    def test_matches_a_known_value(self) -> None:
        # 50/100 at 95% is (0.40383, 0.59617). Cross-checked against a second
        # algebraic arrangement of the same formula, which agrees to 16 digits.
        low, high = wilson_interval(50, 100)
        assert low == pytest.approx(0.403832, abs=1e-5)
        assert high == pytest.approx(0.596168, abs=1e-5)

    def test_is_symmetric_at_one_half(self) -> None:
        low, high = wilson_interval(50, 100)
        assert (low + high) / 2 == pytest.approx(0.5, abs=1e-9)

    def test_stays_inside_the_scale_at_the_extremes(self) -> None:
        """The reason for Wilson over the normal approximation.

        At 3/1000 the normal interval runs below zero, which reads as a
        negative share of the population.
        """
        low, high = wilson_interval(3, 1000)
        assert low >= 0.0
        assert high <= 1.0
        assert low > 0.0

    def test_zero_successes_gives_a_one_sided_interval(self) -> None:
        low, high = wilson_interval(0, 500)
        # Floating-point noise leaves a value ~1e-19 rather than exactly 0;
        # the aggregation rounds to 6 decimals, so it reaches the UI as 0.
        assert low == pytest.approx(0.0, abs=1e-12)
        assert 0.0 < high < 0.02

    def test_all_successes_reaches_the_top(self) -> None:
        low, high = wilson_interval(500, 500)
        assert high == 1.0
        assert 0.98 < low < 1.0

    def test_narrows_as_the_sample_grows(self) -> None:
        """n=1000 is the whole point of the cohort; it should show."""
        narrow = wilson_interval(500, 1000)
        wide = wilson_interval(50, 100)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])

    def test_no_interval_without_respondents(self) -> None:
        assert wilson_interval(0, 0) is None

    def test_no_interval_when_successes_exceed_the_base(self) -> None:
        # Would mean selections were counted against the wrong denominator.
        assert wilson_interval(11, 10) is None

    def test_no_interval_for_negative_counts(self) -> None:
        assert wilson_interval(-1, 10) is None


class TestAggregateCategorical:
    def test_single_choice_shares_sum_to_one(self) -> None:
        result = _aggregate_categorical(_entries(["yes"] * 60 + ["no"] * 40))
        assert result["respondentCount"] == 100
        shares = {row["value"]: row["share"] for row in result["counts"]}
        assert shares["yes"] == pytest.approx(0.6)
        assert shares["no"] == pytest.approx(0.4)
        assert sum(shares.values()) == pytest.approx(1.0)

    def test_every_row_carries_an_interval_around_its_share(self) -> None:
        result = _aggregate_categorical(_entries(["yes"] * 60 + ["no"] * 40))
        for row in result["counts"]:
            assert row["ciLow"] <= row["share"] <= row["ciHigh"]

    def test_multi_choice_uses_respondents_not_selections(self) -> None:
        """The denominator bug this test exists to prevent.

        Two respondents picking two options each give four selections. A share
        of 1.0 for an option everyone picked is correct; 0.5 would not be.
        """
        result = _aggregate_categorical(
            _entries([["a", "b"], ["a", "b"]])
        )
        assert result["count"] == 4  # selections
        assert result["respondentCount"] == 2  # respondents
        shares = {row["value"]: row["share"] for row in result["counts"]}
        assert shares["a"] == pytest.approx(1.0)
        assert shares["b"] == pytest.approx(1.0)

    def test_multi_choice_shares_may_exceed_one_in_total(self) -> None:
        result = _aggregate_categorical(_entries([["a", "b"], ["a"], ["b"]]))
        assert result["respondentCount"] == 3
        total = sum(row["share"] for row in result["counts"])
        assert total > 1.0

    def test_no_entries_yields_no_intervals(self) -> None:
        result = _aggregate_categorical([])
        assert result["respondentCount"] == 0
        assert result["counts"] == []

    def test_counts_are_unchanged_for_existing_consumers(self) -> None:
        """Adding fields must not move the ones already rendered."""
        result = _aggregate_categorical(_entries(["yes", "yes", "no"]))
        assert result["count"] == 3
        assert result["distinctCount"] == 2
        assert [row["count"] for row in result["counts"]] == [2, 1]
        assert [row["value"] for row in result["counts"]] == ["yes", "no"]
