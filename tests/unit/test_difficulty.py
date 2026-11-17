"""
Difficulty allocation for scenario assessments: the even split, explicit
overall and per-type counts, validation, and lining levels up with domains.
"""

from fractions import Fraction
from math import ceil, floor

import pytest

from assessment.difficulty import (
    describe_levels,
    even_split,
    order_levels,
    plan_difficulty,
    plan_levels,
    plan_totals,
)


def assert_balanced(plan, counts, totals):
    """Level totals exact; each type's share of a level its proportion rounded down or up."""
    assert plan_totals(plan) == {level: n for level, n in totals.items() if n}
    total = sum(counts.values())
    for type_key, count in counts.items():
        assert sum(plan[type_key].values()) == count
        for level, level_total in totals.items():
            ideal = Fraction(count * level_total, total)
            assert floor(ideal) <= plan[type_key].get(level, 0) <= ceil(ideal)


# --------------------------------------------------------------------------
# Even split
# --------------------------------------------------------------------------

def test_one_level_puts_every_question_at_it():
    plan = plan_difficulty({"scenario_mcq": 2, "open_text": 1}, levels=["advanced"])
    assert plan == {"scenario_mcq": {"advanced": 2}, "open_text": {"advanced": 1}}


@pytest.mark.parametrize("total, levels, expected", [
    (10, ["beginner", "intermediate", "advanced"], {"beginner": 4, "intermediate": 3, "advanced": 3}),
    (5, ["beginner", "advanced"], {"beginner": 3, "advanced": 2}),
    (6, ["beginner", "intermediate", "advanced"], {"beginner": 2, "intermediate": 2, "advanced": 2}),
])
def test_even_split_gives_the_remainder_to_the_earlier_levels(total, levels, expected):
    assert even_split(total, levels) == expected


@pytest.mark.parametrize("counts, levels", [
    ({"scenario_mcq": 4, "open_text": 3, "sjt_ranking": 3}, ["beginner", "intermediate", "advanced"]),
    ({"scenario_mcq": 2, "open_text": 1}, ["beginner", "intermediate", "advanced"]),
    ({"scenario_mcq": 7, "short_answer": 5, "action_planning": 1}, ["beginner", "advanced"]),
    ({"scenario_mcq": 40, "scenario_multiselect": 40, "open_text": 30, "sjt_ranking": 30,
      "action_planning": 30, "short_answer": 30}, ["beginner", "intermediate", "advanced"]),
    ({"open_text": 1}, ["intermediate", "advanced"]),
])
def test_levels_split_evenly_over_the_total_then_within_each_type(counts, levels):
    plan = plan_difficulty(counts, levels=levels)
    assert_balanced(plan, counts, even_split(sum(counts.values()), levels))


def test_levels_are_case_insensitive_and_put_in_canonical_order():
    plan = plan_difficulty({"scenario_mcq": 3}, levels=["Advanced", " beginner ", "advanced"])
    assert plan == {"scenario_mcq": {"beginner": 2, "advanced": 1}}


# --------------------------------------------------------------------------
# Explicit counts
# --------------------------------------------------------------------------

def test_overall_counts_fix_each_levels_total():
    counts = {"scenario_mcq": 4, "open_text": 2}
    plan = plan_difficulty(counts, distribution={"beginner": 1, "advanced": 5})
    assert_balanced(plan, counts, {"beginner": 1, "advanced": 5})


def test_overall_counts_take_precedence_over_levels():
    plan = plan_difficulty({"scenario_mcq": 2}, levels=["intermediate"],
                           distribution={"advanced": 2})
    assert plan == {"scenario_mcq": {"advanced": 2}}


def test_a_per_type_split_is_kept_and_the_rest_keeps_the_overall_split_even():
    plan = plan_difficulty(
        {"scenario_mcq": 3, "sjt_ranking": 3},
        levels=["beginner", "advanced"],
        distribution={"sjt_ranking": {"advanced": 3}},
    )
    assert plan == {"scenario_mcq": {"beginner": 3}, "sjt_ranking": {"advanced": 3}}
    assert plan_totals(plan) == {"beginner": 3, "advanced": 3}


def test_per_type_splits_combine_with_overall_counts():
    plan = plan_difficulty(
        {"scenario_mcq": 3, "sjt_ranking": 3},
        distribution={"beginner": 2, "advanced": 4, "sjt_ranking": {"advanced": 3}},
    )
    assert plan == {"scenario_mcq": {"beginner": 2, "advanced": 1}, "sjt_ranking": {"advanced": 3}}


def test_every_type_split_needs_no_levels():
    plan = plan_difficulty(
        {"scenario_mcq": 2, "open_text": 1},
        distribution={"scenario_mcq": {"beginner": 1, "advanced": 1}, "open_text": {"intermediate": 1}},
    )
    assert plan == {"scenario_mcq": {"beginner": 1, "advanced": 1}, "open_text": {"intermediate": 1}}
    assert plan_levels(plan) == ["beginner", "intermediate", "advanced"]


@pytest.mark.parametrize("levels, distribution, message", [
    (["expert"], None, "Unknown difficulty level"),
    (None, None, "No difficulty level"),
    (None, {"beginner": 2}, "add up to 2, but 3"),
    (None, {"beginner": -1, "advanced": 4}, "non-negative whole number"),
    (None, {"beginner": 1.5}, "non-negative whole number"),
    (["beginner"], {"scenario_mcq": {"advanced": 1}}, "adds up to 1, but 2"),
    (["beginner"], {"sjt_ranking": {"advanced": 1}}, "adds up to 1, but 0"),
    (None, {"beginner": 3, "scenario_mcq": {"advanced": 2}}, "more than the 0 asked for overall"),
    (["beginner"], {"hard": 3}, "neither a difficulty level"),
    (["beginner"], {"scenario_mcq": 2}, "must map difficulty levels"),
    (["beginner"], ["advanced"], "must be a JSON object"),
])
def test_requests_that_cannot_be_met_are_rejected(levels, distribution, message):
    with pytest.raises(ValueError, match=message):
        plan_difficulty({"scenario_mcq": 2, "open_text": 1}, levels=levels, distribution=distribution)


# --------------------------------------------------------------------------
# Slot order
# --------------------------------------------------------------------------

def test_levels_line_up_with_the_slots_and_spread_over_the_domains():
    plan = {"scenario_mcq": {"beginner": 3, "advanced": 3}}
    domains = {"scenario_mcq": ["A", "B", "A", "B", "A", "B"]}
    levels = order_levels(plan, domains)["scenario_mcq"]
    assert sorted(levels) == ["advanced"] * 3 + ["beginner"] * 3
    by_domain = {d: {lv for dd, lv in zip(domains["scenario_mcq"], levels) if dd == d} for d in "AB"}
    assert by_domain == {"A": {"beginner", "advanced"}, "B": {"beginner", "advanced"}}


def test_slot_order_continues_the_domain_spread_across_types():
    plan = {"scenario_mcq": {"beginner": 1, "advanced": 1}, "open_text": {"beginner": 1, "advanced": 1}}
    domains = {"scenario_mcq": ["A", "B"], "open_text": ["A", "B"]}
    out = order_levels(plan, domains)
    # A was beginner for the MCQ, so it is advanced for the open text.
    assert out == {"scenario_mcq": ["beginner", "advanced"], "open_text": ["advanced", "beginner"]}


@pytest.mark.parametrize("counts, expected", [
    ({"advanced": 3}, "advanced"),
    ({"advanced": 1, "beginner": 2}, "mixed (beginner: 2, advanced: 1)"),
    ({}, "unspecified"),
])
def test_describe_levels(counts, expected):
    assert describe_levels(counts) == expected
