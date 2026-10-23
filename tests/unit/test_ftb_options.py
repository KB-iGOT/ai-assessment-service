"""
FTB with options (option FTB) alongside the legacy free-text FTB.

Covers the shape helpers, the editable-field allowlist, validation, the edit
path's shape lock and the CSV export.
"""

import csv

import pytest

from assessment.editing import apply_question_add, apply_question_edit, diff_assessments
from assessment.exporters_csv_v2 import _has_option_rows, generate_csv_v2
from assessment.questions import (
    BUCKET_FTB,
    BUCKET_MCQ,
    BUCKET_MULTICHOICE,
    answers_by_single_option,
    is_legacy_ftb,
)
from assessment.validation import (
    LEGACY_FTB_EDITABLE,
    ValidationError,
    editable_fields_for,
    validate_editable_fields,
    validate_question,
)
from factories import legacy_ftb, make_options, mcq, multichoice, option_ftb


def codes(errors):
    return {e["code"] for e in errors}


def one_ftb(question):
    return {"questions": {BUCKET_FTB: [question]}, "question_order": [question["question_id"]]}


# --------------------------------------------------------------------------
# Shape helpers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    (option_ftb(), False),
    (legacy_ftb(), True),
    ({**option_ftb(), "options": None}, True),
    ({**option_ftb(), "options": []}, True),
])
def test_is_legacy_ftb(question, expected):
    assert is_legacy_ftb(BUCKET_FTB, question) is expected


def test_is_legacy_ftb_is_false_for_other_buckets():
    assert is_legacy_ftb(BUCKET_MCQ, {"question_text": "x"}) is False


@pytest.mark.parametrize("bucket,question,expected", [
    (BUCKET_MCQ, mcq(), True),
    (BUCKET_FTB, option_ftb(), True),
    (BUCKET_FTB, legacy_ftb(), False),
    (BUCKET_MULTICHOICE, multichoice(), False),
])
def test_answers_by_single_option(bucket, question, expected):
    assert answers_by_single_option(bucket, question) is expected


# --------------------------------------------------------------------------
# Editable fields
# --------------------------------------------------------------------------

def test_legacy_ftb_gets_legacy_allowlist():
    fields = editable_fields_for(BUCKET_FTB, legacy_ftb())
    assert fields == LEGACY_FTB_EDITABLE
    assert "options" not in fields and "correct_answer" in fields


def test_option_ftb_and_add_path_get_option_allowlist():
    for fields in (editable_fields_for(BUCKET_FTB, option_ftb()), editable_fields_for(BUCKET_FTB)):
        assert {"options", "correct_option_index"} <= fields
        assert "correct_answer" not in fields


def test_option_ftb_rejects_correct_answer_and_legacy_rejects_options():
    assert codes(validate_editable_fields(BUCKET_FTB, {"correct_answer": "x"}, option_ftb())) == {"field_not_editable"}
    assert codes(validate_editable_fields(BUCKET_FTB, {"options": []}, legacy_ftb())) == {"field_not_editable"}


def test_server_owned_fields_are_ignored_not_rejected():
    assert validate_editable_fields(BUCKET_FTB, {"question_id": "x", "provenance": "y"}, option_ftb()) == []


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_valid_option_ftb_passes():
    assert validate_question(BUCKET_FTB, option_ftb()) == []


def test_option_ftb_needs_exactly_one_blank():
    none = option_ftb(question_text="No blank here.")
    two = option_ftb(question_text="__ and ____ .")
    assert any(e["code"] == "ftb_blank_count_invalid" and e["params"]["found"] == 0
               for e in validate_question(BUCKET_FTB, none))
    assert any(e["code"] == "ftb_blank_count_invalid" and e["params"]["found"] == 2
               for e in validate_question(BUCKET_FTB, two))


def test_option_ftb_answer_key_must_be_a_single_valid_index():
    assert "correct_option_index_out_of_range" in codes(validate_question(BUCKET_FTB, option_ftb(correct=7)))
    assert "correct_option_index_invalid" in codes(validate_question(BUCKET_FTB, option_ftb(correct=[0])))
    assert "correct_option_index_required" in codes(validate_question(BUCKET_FTB, option_ftb(correct=None)))


def test_option_ftb_option_ceiling():
    q = option_ftb()
    q["options"] = make_options(list("abcdef"))
    assert "option_count_invalid" in codes(validate_question(BUCKET_FTB, q))


def test_legacy_ftb_still_validates_on_correct_answer():
    assert validate_question(BUCKET_FTB, legacy_ftb()) == []
    assert "correct_answer_required" in codes(validate_question(BUCKET_FTB, legacy_ftb(correct_answer=" ")))


def test_new_ftb_must_have_options():
    assert "options_required" in codes(validate_question(BUCKET_FTB, legacy_ftb(), is_new_question=True))


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------

def test_edit_option_ftb_options_and_answer():
    data = one_ftb(option_ftb("f1"))
    result = apply_question_edit(data, "f1", {
        "options": make_options(["cyan", "blue", "navy"]), "correct_option_index": 2,
    }, editor_id="u")
    q = result.question
    assert q["options"][2]["text"] == "navy" and q["correct_option_index"] == 2
    assert q["provenance"] == "ai_assisted"
    assert result.events[0]["details"]["answer_key_changed"] is True


def test_emptying_an_option_ftb_cannot_turn_it_legacy():
    data = one_ftb(option_ftb("f1"))
    with pytest.raises(ValidationError) as exc:
        apply_question_edit(data, "f1", {"options": []}, editor_id="u")
    assert "ftb_format_change_not_allowed" in codes(exc.value.errors)


def test_legacy_ftb_edit_of_answer_text():
    data = one_ftb(legacy_ftb("l1"))
    result = apply_question_edit(data, "l1", {"correct_answer": "azure"}, editor_id="u")
    assert result.question["correct_answer"] == "azure"
    assert "options" not in result.question


def test_bulk_update_cannot_switch_ftb_shape():
    before = one_ftb(legacy_ftb("l1"))
    after = one_ftb({**legacy_ftb("l1"), "options": make_options(["a", "b"]), "correct_option_index": 0})
    with pytest.raises(ValidationError) as exc:
        diff_assessments(before, after, editor_id="u")
    assert "ftb_format_change_not_allowed" in codes(exc.value.errors)


def test_adding_a_legacy_shaped_ftb_is_rejected():
    data = one_ftb(option_ftb("f1"))
    payload = {k: v for k, v in legacy_ftb().items() if k not in ("question_id", "provenance")}
    payload.pop("correct_answer")  # not an addable FTB field any more
    with pytest.raises(ValidationError) as exc:
        apply_question_add(data, "ftb", payload, editor_id="u")
    assert "options_required" in codes(exc.value.errors)


# --------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------

def test_has_option_rows():
    assert _has_option_rows(option_ftb(), "FTB") is True
    assert _has_option_rows(legacy_ftb(), "FTB") is False
    assert _has_option_rows(mcq(), "MCQ-SCA") is True
    assert _has_option_rows({}, "T/F") is False


def test_csv_writes_option_ftb_as_options_and_legacy_ftb_as_blank(tmp_path):
    data = {
        "questions": {BUCKET_FTB: [option_ftb("f1", correct=1), legacy_ftb("l1")]},
        "question_order": ["f1", "l1"],
    }
    path = tmp_path / "out.csv"
    generate_csv_v2(data, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))

    opt, legacy = rows
    assert opt["QuestionType"] == "FTB"
    assert "<blank>" in opt["Question"]
    assert [opt[f"Option{i}"] for i in (1, 2, 3)] == ["red", "blue", "green"]
    assert [opt[f"isOption{i}Correct"] for i in (1, 2, 3)] == ["No", "Yes", "No"]

    assert legacy["Option1"] == "blue"
    assert legacy["isOption1Correct"] == "Blank1"
