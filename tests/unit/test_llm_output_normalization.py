"""
Repairs applied to raw LLM output by `normalize_assessment` (questions.py).

These are the model quirks the ingest path absorbs: one-based option indexes,
missing indexes, booleans for True/False answers, duplicate or missing ids,
`options: null` on an FTB, and a stray `correct_answer` on an option FTB.
"""

import copy

import pytest

from assessment import questions as qmod
from assessment.questions import (
    BUCKET_FTB,
    BUCKET_MCQ,
    BUCKET_MULTICHOICE,
    BUCKET_TRUEFALSE,
    CANONICAL_BUCKETS,
    PROV_AI_GENERATED,
    _normalize_option_indexes,
    _rebase_option_indexes,
    normalize_assessment,
)
from factories import make_options, mcq, multichoice, option_ftb, truefalse


def correct_text(question):
    key = question["correct_option_index"]
    return next(o["text"] for o in question["options"] if o["index"] == key)


# --------------------------------------------------------------------------
# One-based option indexes (rebase)
# --------------------------------------------------------------------------

def test_rebase_moves_one_based_mcq_to_zero_based_keeping_the_same_correct_option():
    question = mcq(texts=("A", "B", "C", "D"))
    question["options"] = make_options(["A", "B", "C", "D"], start=1)
    question["correct_option_index"] = 3  # "C"
    _rebase_option_indexes(question)
    assert [o["index"] for o in question["options"]] == [0, 1, 2, 3]
    assert question["correct_option_index"] == 2
    assert correct_text(question) == "C"


def test_rebase_moves_one_based_multichoice_answer_list():
    question = multichoice()
    question["options"] = make_options(["2", "4", "3", "6"], start=1)
    question["correct_option_index"] = [1, 3]
    _rebase_option_indexes(question)
    assert question["correct_option_index"] == [0, 2]


def test_rebase_is_idempotent():
    question = mcq()
    question["options"] = make_options(["A", "B", "C"], start=1)
    question["correct_option_index"] = 1
    _rebase_option_indexes(question)
    once = copy.deepcopy(question)
    _rebase_option_indexes(question)
    assert question == once


@pytest.mark.parametrize("indexes,correct", [
    ([1, 2, 4], 1),          # gap in the run
    ([0, 1, 2], 1),          # already zero-based
    ([1, 2, 3], 0),          # answer key outside the run — already broken
    ([1, 2, 3], "x"),        # non-numeric answer key
])
def test_rebase_leaves_ambiguous_questions_untouched(indexes, correct):
    question = mcq()
    question["options"] = [{"index": i, "text": f"o{i}"} for i in indexes]
    question["correct_option_index"] = correct
    before = copy.deepcopy(question)
    _rebase_option_indexes(question)
    assert question == before


def test_rebase_skips_options_with_non_int_index():
    question = mcq()
    question["options"] = [{"index": "1", "text": "a"}, {"index": 2, "text": "b"}]
    question["correct_option_index"] = 1
    before = copy.deepcopy(question)
    _rebase_option_indexes(question)
    assert question == before


def test_rebase_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(qmod, "NORMALIZE_OPTION_INDEX_BASE", False)
    question = mcq()
    question["options"] = make_options(["A", "B"], start=1)
    question["correct_option_index"] = 2
    _rebase_option_indexes(question)
    assert question["correct_option_index"] == 2


def test_normalize_rebases_only_when_asked():
    data = {"questions": {BUCKET_MCQ: [mcq()]}}
    data["questions"][BUCKET_MCQ][0]["options"] = make_options(["A", "B", "C"], start=1)
    data["questions"][BUCKET_MCQ][0]["correct_option_index"] = 2

    stored = normalize_assessment(data)  # stored assessments: never rebased
    assert stored["questions"][BUCKET_MCQ][0]["correct_option_index"] == 2

    fresh = normalize_assessment(data, rebase_option_indexes=True)  # ingest path
    assert fresh["questions"][BUCKET_MCQ][0]["correct_option_index"] == 1
    assert correct_text(fresh["questions"][BUCKET_MCQ][0]) == "B"


def test_normalize_rebases_option_ftb_too():
    ftb = option_ftb()
    ftb["options"] = make_options(["red", "blue", "green"], start=1)
    ftb["correct_option_index"] = 2
    out = normalize_assessment({"questions": {BUCKET_FTB: [ftb]}}, rebase_option_indexes=True)
    assert correct_text(out["questions"][BUCKET_FTB][0]) == "blue"
    assert out["questions"][BUCKET_FTB][0]["correct_option_index"] == 1


# --------------------------------------------------------------------------
# Missing / malformed option indexes
# --------------------------------------------------------------------------

def test_missing_indexes_are_filled_positionally():
    question = {"options": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}
    _normalize_option_indexes(question)
    assert [o["index"] for o in question["options"]] == [0, 1, 2]


def test_missing_index_in_one_based_run_is_filled_one_based_without_collision():
    question = {"options": [{"index": 1, "text": "a"}, {"text": "b"}, {"index": 3, "text": "c"}]}
    _normalize_option_indexes(question)
    assert [o["index"] for o in question["options"]] == [1, 2, 3]


def test_string_indexes_are_coerced_to_int():
    question = {"options": [{"index": "0", "text": "a"}, {"index": "1", "text": "b"}]}
    _normalize_option_indexes(question)
    assert [o["index"] for o in question["options"]] == [0, 1]


def test_non_list_options_are_left_alone():
    question = {"options": None}
    _normalize_option_indexes(question)
    assert question == {"options": None}


# --------------------------------------------------------------------------
# True/False answer spelling
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (True, "True"), (False, "False"),
    ("true", "True"), ("FALSE", "False"),
    (" yes ", "True"), ("n", "False"), ("T", "True"), ("f", "False"),
])
def test_true_false_answer_is_canonicalised(raw, expected):
    out = normalize_assessment({"questions": {BUCKET_TRUEFALSE: [truefalse(answer=raw)]}})
    assert out["questions"][BUCKET_TRUEFALSE][0]["correct_answer"] == expected


def test_unrecognised_true_false_answer_is_left_for_validation():
    out = normalize_assessment({"questions": {BUCKET_TRUEFALSE: [truefalse(answer="maybe")]}})
    assert out["questions"][BUCKET_TRUEFALSE][0]["correct_answer"] == "maybe"


# --------------------------------------------------------------------------
# FTB shape
# --------------------------------------------------------------------------

def test_option_ftb_loses_a_stray_correct_answer():
    ftb = option_ftb()
    ftb["correct_answer"] = "blue"
    out = normalize_assessment({"questions": {BUCKET_FTB: [ftb]}})
    assert "correct_answer" not in out["questions"][BUCKET_FTB][0]


@pytest.mark.parametrize("options", [None, []])
def test_ftb_with_null_or_empty_options_is_kept_as_legacy(options):
    ftb = option_ftb()
    ftb["options"] = options
    ftb["correct_answer"] = "blue"
    out = normalize_assessment({"questions": {BUCKET_FTB: [ftb]}})
    assert out["questions"][BUCKET_FTB][0]["correct_answer"] == "blue"


# --------------------------------------------------------------------------
# Ids, provenance, type, order
# --------------------------------------------------------------------------

def test_every_batch_numbering_from_q_001_is_deduplicated():
    data = {"questions": {
        BUCKET_MCQ: [mcq("q_001"), mcq("q_001")],
        BUCKET_FTB: [option_ftb("q_001")],
    }}
    out = normalize_assessment(data)
    ids = [q["question_id"] for b in out["questions"].values() for q in b]
    assert len(ids) == len(set(ids)) == 3
    assert ids[0] == "q_001"


def test_missing_ids_are_backfilled_by_type_and_position():
    data = {"questions": {BUCKET_MCQ: [{"question_text": "x"}, {"question_text": "y", "question_id": ""}]}}
    out = normalize_assessment(data)
    assert [q["question_id"] for q in out["questions"][BUCKET_MCQ]] == ["mcq_001", "mcq_002"]


def test_invalid_provenance_and_wrong_question_type_are_repaired():
    question = mcq(provenance="robot", question_type="ESSAY")
    out = normalize_assessment({"questions": {BUCKET_MCQ: [question]}})
    q = out["questions"][BUCKET_MCQ][0]
    assert q["provenance"] == PROV_AI_GENERATED
    assert q["question_type"] == "MCQ"


def test_all_canonical_buckets_present_and_unknown_buckets_kept():
    out = normalize_assessment({"questions": {"Essay": [{"question_text": "x"}]}})
    assert list(out["questions"])[:5] == CANONICAL_BUCKETS
    assert len(out["questions"]["Essay"]) == 1


def test_question_order_drops_stale_ids_and_appends_new_ones():
    data = {
        "questions": {BUCKET_MCQ: [mcq("a"), mcq("b")], BUCKET_MULTICHOICE: [multichoice("c")]},
        "question_order": ["b", "ghost", "b"],
    }
    out = normalize_assessment(data)
    assert out["question_order"] == ["b", "a", "c"]


def test_legacy_order_follows_stored_bucket_order_not_canonical():
    # jsonb returns keys in its own order; backfilled numbering must follow it.
    data = {"questions": {BUCKET_TRUEFALSE: [truefalse("t")], BUCKET_MCQ: [mcq("m")]}}
    assert normalize_assessment(data)["question_order"] == ["t", "m"]


@pytest.mark.parametrize("bad", [None, "text", [1, 2]])
def test_non_dict_payload_normalises_to_empty_assessment(bad):
    out = normalize_assessment(bad)
    assert out["question_order"] == []
    assert all(v == [] for v in out["questions"].values())


def test_normalize_is_idempotent_and_does_not_mutate_input(assessment):
    original = copy.deepcopy(assessment)
    once = normalize_assessment(assessment)
    assert assessment == original
    assert normalize_assessment(once) == once
