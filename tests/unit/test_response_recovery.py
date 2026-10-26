"""
Count recovery for one LLM response (`response_recovery.py`).

The model does not always honour the per-type counts it is asked for — it
returns fewer, more, types it was not asked for, and bucket names of its own.
"""

from assessment.batching import _unwrap_questions
from assessment.questions import (
    BUCKET_FTB,
    BUCKET_MCQ,
    BUCKET_TRUEFALSE,
    CANONICAL_BUCKETS,
)
from assessment.response_recovery import (
    count_by_type,
    extract_buckets,
    remaining_blooms,
    remaining_course_counts,
    requested_counts,
    shortfall,
    trim_to_counts,
)


def q(name, blooms=None, course=None):
    out = {"question_text": name}
    if blooms is not None:
        out["blooms_level"] = blooms
    if course is not None:
        out["course_name"] = course
    return out


def texts(questions):
    return [x["question_text"] for x in questions]


def empty_buckets(**by_bucket):
    buckets = {b: [] for b in CANONICAL_BUCKETS}
    buckets.update(by_bucket)
    return buckets


COURSE_META = {"courses": [
    {"name": "Course A", "identifier": "do_A"},
    {"name": "Course B", "identifier": "do_B"},
]}


# --------------------------------------------------------------------------
# requested_counts
# --------------------------------------------------------------------------

def test_requested_counts_keeps_known_positive_types_as_ints():
    assert requested_counts({"mcq": "3", "ftb": 2}) == {"mcq": 3, "ftb": 2}


def test_requested_counts_drops_zero_none_and_unknown_types():
    assert requested_counts({"mcq": 0, "ftb": None, "essay": 4, "truefalse": 1}) == {"truefalse": 1}


def test_requested_counts_handles_none():
    assert requested_counts(None) == {}


# --------------------------------------------------------------------------
# Response shape: unwrapping and bucket names
# --------------------------------------------------------------------------

def test_extract_buckets_reads_wrapped_response():
    result = {"blueprint": {"x": 1}, "questions": {BUCKET_MCQ: [q("a"), q("b")]}}
    buckets = extract_buckets(result)
    assert texts(buckets[BUCKET_MCQ]) == ["a", "b"]
    assert set(buckets) == set(CANONICAL_BUCKETS)


def test_extract_buckets_reads_unwrapped_top_level_buckets_and_ignores_blueprint():
    # Model left out the {"questions": ...} wrapper.
    result = {"blueprint": {"title": "t"}, BUCKET_MCQ: [q("a")]}
    buckets = extract_buckets(result)
    assert texts(buckets[BUCKET_MCQ]) == ["a"]
    assert all(not v for k, v in buckets.items() if k != BUCKET_MCQ)


def test_extract_buckets_accepts_short_type_keys_and_type_consts():
    result = {"questions": {"mcq": [q("a")], "TRUEFALSE": [q("t")], " FTB ": [q("f")]}}
    buckets = extract_buckets(result)
    assert texts(buckets[BUCKET_MCQ]) == ["a"]
    assert texts(buckets[BUCKET_TRUEFALSE]) == ["t"]
    assert texts(buckets[BUCKET_FTB]) == ["f"]


def test_extract_buckets_merges_two_spellings_of_the_same_bucket_in_order():
    result = {"questions": {BUCKET_MCQ: [q("a")], "mcq": [q("b")]}}
    assert texts(extract_buckets(result)[BUCKET_MCQ]) == ["a", "b"]


def test_extract_buckets_drops_invented_bucket_names():
    result = {"questions": {"Essay Question": [q("e")], BUCKET_MCQ: [q("a")]}}
    buckets = extract_buckets(result)
    assert "Essay Question" not in buckets
    assert count_by_type(buckets) == {"mcq": 1}


def test_extract_buckets_skips_non_dict_items_and_non_list_buckets():
    result = {"questions": {BUCKET_MCQ: [q("a"), "junk", None, 3], BUCKET_FTB: "not a list"}}
    buckets = extract_buckets(result)
    assert texts(buckets[BUCKET_MCQ]) == ["a"]
    assert buckets[BUCKET_FTB] == []


def test_extract_buckets_of_non_dict_response_is_empty_not_a_crash():
    for bad in (None, [], "text", 42):
        assert count_by_type(extract_buckets(bad)) == {}


def test_unwrap_questions_falls_back_to_top_level_when_questions_is_not_a_dict():
    assert _unwrap_questions({"questions": ["x"], BUCKET_MCQ: []}) == {"questions": ["x"], BUCKET_MCQ: []}


# --------------------------------------------------------------------------
# count_by_type / shortfall
# --------------------------------------------------------------------------

def test_count_by_type_omits_empty_buckets():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a"), q("b")]})
    assert count_by_type(buckets) == {"mcq": 2}


def test_shortfall_reports_only_types_under_their_count():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a")], BUCKET_FTB: [q("f1"), q("f2"), q("f3")]})
    assert shortfall({"mcq": 3, "ftb": 2, "truefalse": 1}, buckets) == {"mcq": 2, "truefalse": 1}


def test_shortfall_is_empty_when_counts_are_met():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a")]})
    assert shortfall({"mcq": 1}, buckets) == {}


# --------------------------------------------------------------------------
# trim_to_counts
# --------------------------------------------------------------------------

def test_trim_drops_every_question_of_an_unrequested_type():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a")], BUCKET_TRUEFALSE: [q("t1"), q("t2")]})
    kept, dropped = trim_to_counts(buckets, {"mcq": 1})
    assert texts(kept[BUCKET_MCQ]) == ["a"]
    assert kept[BUCKET_TRUEFALSE] == []
    assert dropped == {"truefalse": 2}


def test_trim_leaves_types_at_or_under_their_count_untouched():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a")], BUCKET_FTB: [q("f")]})
    kept, dropped = trim_to_counts(buckets, {"mcq": 3, "ftb": 1})
    assert dropped == {}
    assert texts(kept[BUCKET_MCQ]) == ["a"]


def test_trim_without_distributions_drops_the_latest_returned():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a"), q("b"), q("c"), q("d")]})
    kept, dropped = trim_to_counts(buckets, {"mcq": 2})
    assert texts(kept[BUCKET_MCQ]) == ["a", "b"]
    assert dropped == {"mcq": 2}


def test_trim_prefers_dropping_a_surplus_blooms_level():
    # Assigned [Apply, Remember]; the model wrote two Remember and one Apply.
    buckets = empty_buckets(**{BUCKET_MCQ: [
        q("r1", "Remember"), q("r2", "Remember"), q("ap", "Apply"),
    ]})
    kept, _ = trim_to_counts(buckets, {"mcq": 2}, blooms_by_type={"mcq": ["Apply", "Remember"]})
    # The later Remember is the surplus, even though Apply came last.
    assert texts(kept[BUCKET_MCQ]) == ["r1", "ap"]


def test_trim_blooms_matching_ignores_case_and_whitespace():
    buckets = empty_buckets(**{BUCKET_MCQ: [
        q("a", " apply "), q("b", "REMEMBER"), q("c", "Remember"),
    ]})
    kept, _ = trim_to_counts(buckets, {"mcq": 2}, blooms_by_type={"mcq": ["Apply", "Remember"]})
    assert texts(kept[BUCKET_MCQ]) == ["a", "b"]


def test_trim_then_prefers_dropping_a_course_over_its_quota():
    buckets = empty_buckets(**{BUCKET_MCQ: [
        q("a1", course="Course A"), q("a2", course="Course A"), q("b1", course="Course B"),
    ]})
    kept, _ = trim_to_counts(
        buckets, {"mcq": 2},
        course_counts={"do_A": 1, "do_B": 1}, aggregated_metadata=COURSE_META,
    )
    assert texts(kept[BUCKET_MCQ]) == ["a1", "b1"]


def test_trim_treats_unknown_course_names_as_over_quota():
    buckets = empty_buckets(**{BUCKET_MCQ: [
        q("x", course="Hallucinated Course"), q("a", course="course  a"),
    ]})
    kept, _ = trim_to_counts(
        buckets, {"mcq": 1},
        course_counts={"do_A": 1}, aggregated_metadata=COURSE_META,
    )
    assert texts(kept[BUCKET_MCQ]) == ["a"]


def test_trim_blooms_rule_outranks_course_rule():
    # q1 is course-surplus, q2 is blooms-surplus: the blooms one goes first.
    buckets = empty_buckets(**{BUCKET_MCQ: [
        q("q0", "Apply", "Course A"),
        q("q1", "Remember", "Course A"),
        q("q2", "Apply", "Course B"),
    ]})
    kept, _ = trim_to_counts(
        buckets, {"mcq": 2},
        blooms_by_type={"mcq": ["Apply", "Remember"]},
        course_counts={"do_A": 1, "do_B": 1}, aggregated_metadata=COURSE_META,
    )
    assert texts(kept[BUCKET_MCQ]) == ["q0", "q1"]


def test_trim_never_drops_for_a_wrong_level_when_the_type_is_not_over():
    buckets = empty_buckets(**{BUCKET_MCQ: [q("a", "Create"), q("b", "Create")]})
    kept, dropped = trim_to_counts(buckets, {"mcq": 2}, blooms_by_type={"mcq": ["Remember", "Apply"]})
    assert dropped == {}
    assert texts(kept[BUCKET_MCQ]) == ["a", "b"]


def test_trim_unrequested_types_do_not_use_up_course_quota():
    # The True/False question is from Course A and comes first, but is dropped
    # anyway; it must not eat Course A's single slot.
    buckets = {
        BUCKET_TRUEFALSE: [q("tf", course="Course A")],
        BUCKET_MCQ: [q("a1", course="Course A"), q("b1", course="Course B"), q("a2", course="Course A")],
    }
    kept, dropped = trim_to_counts(
        buckets, {"mcq": 2},
        course_counts={"do_A": 1, "do_B": 1}, aggregated_metadata=COURSE_META,
    )
    assert texts(kept[BUCKET_MCQ]) == ["a1", "b1"]
    assert dropped == {"truefalse": 1, "mcq": 1}


# --------------------------------------------------------------------------
# remaining_blooms
# --------------------------------------------------------------------------

def test_remaining_blooms_matches_by_label_not_position():
    # Assigned [Remember, Apply, Analyze]; the short response wrote only Analyze.
    kept = empty_buckets(**{BUCKET_MCQ: [q("x", "analyze")]})
    out = remaining_blooms({"mcq": ["Remember", "Apply", "Analyze"]}, kept, {"mcq": 2})
    assert out == {"mcq": ["Remember", "Apply"]}


def test_remaining_blooms_truncates_to_the_number_missing():
    kept = empty_buckets()
    out = remaining_blooms({"mcq": ["Remember", "Apply", "Analyze"]}, kept, {"mcq": 1})
    assert out == {"mcq": ["Remember"]}


def test_remaining_blooms_skips_types_with_no_assignment():
    assert remaining_blooms(None, empty_buckets(), {"mcq": 2}) == {}
    assert remaining_blooms({"ftb": ["Apply"]}, empty_buckets(), {"mcq": 2}) == {}


# --------------------------------------------------------------------------
# remaining_course_counts
# --------------------------------------------------------------------------

def test_remaining_course_counts_subtracts_what_is_already_kept():
    kept = empty_buckets(**{BUCKET_MCQ: [q("a", course="Course A"), q("b", course="Course A")]})
    out = remaining_course_counts({"do_A": 2, "do_B": 2}, kept, 2, COURSE_META)
    assert out == {"do_B": 2}


def test_remaining_course_counts_falls_back_to_original_quota_when_kept_is_over_quota():
    # Course A is already over its quota, so the remaining quota (B=1) cannot
    # cover the 2 missing; the original quota is apportioned instead.
    kept = empty_buckets(**{BUCKET_MCQ: [q("a", course="Course A"), q("b", course="Course A"),
                                          q("c", course="Course A")]})
    out = remaining_course_counts({"do_A": 1, "do_B": 1}, kept, 2, COURSE_META)
    assert out == {"do_A": 1, "do_B": 1}


def test_remaining_course_counts_always_sums_to_missing_total():
    kept = empty_buckets(**{BUCKET_MCQ: [q("a", course="Nope")]})
    out = remaining_course_counts({"do_A": 3, "do_B": 2}, kept, 4, COURSE_META)
    assert sum(out.values()) == 4


def test_remaining_course_counts_empty_without_weightage_or_shortfall():
    assert remaining_course_counts(None, empty_buckets(), 3, COURSE_META) == {}
    assert remaining_course_counts({"do_A": 1}, empty_buckets(), 0, COURSE_META) == {}
