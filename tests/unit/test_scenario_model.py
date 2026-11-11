"""
Scenario assessment — the question model, the validation gate (including
rubrics) and the editing operations.
"""

import copy
import random

import pytest

from assessment.editing import apply_question_add, apply_question_edit, diff_assessments
from assessment.questions import (
    BUCKET_ACTION_PLANNING,
    BUCKET_MCQ,
    BUCKET_OPEN_TEXT,
    BUCKET_RANKING,
    BUCKET_SCENARIO_MCQ,
    BUCKET_SHORT_ANSWER,
    CANONICAL_BUCKETS,
    SCENARIO_BUCKETS,
    balance_mcq_answer_positions,
    is_scenario_assessment,
    normalize_assessment,
)
from assessment.validation import ValidationError, validate_question, validate_rubric
from factories import (
    action_planning,
    make_assessment,
    make_rubric,
    make_scenario_assessment,
    mcq,
    open_text,
    ranking,
    scenario_mcq,
    scenario_multiselect,
    short_answer,
)


def codes(errors):
    return [e["code"] for e in errors]


# --------------------------------------------------------------------------
# Bucket layout
# --------------------------------------------------------------------------

def test_scenario_assessment_normalizes_to_the_six_scenario_buckets_only():
    data = normalize_assessment(make_scenario_assessment())
    assert list(data["questions"]) == SCENARIO_BUCKETS
    assert is_scenario_assessment(data)
    assert data["question_order"] == make_scenario_assessment()["question_order"]
    assert data["questions"][BUCKET_RANKING][0]["question_type"] == "SJT_RANKING"


def test_standard_assessment_keeps_the_five_standard_buckets():
    data = normalize_assessment(make_assessment())
    assert list(data["questions"]) == CANONICAL_BUCKETS
    assert not is_scenario_assessment(data)


def test_scenario_layout_survives_every_bucket_being_empty():
    data = make_scenario_assessment()
    data["questions"] = {bucket: [] for bucket in SCENARIO_BUCKETS}
    assert list(normalize_assessment(data)["questions"]) == SCENARIO_BUCKETS


def test_one_based_ranking_is_rebased_with_its_answer_key():
    question = ranking("r1", order=(3, 1, 4, 2))
    for i, option in enumerate(question["options"]):
        option["index"] = i + 1
    data = make_scenario_assessment()
    data["questions"][BUCKET_RANKING] = [question]

    out = normalize_assessment(data, rebase_option_indexes=True)
    rebased = out["questions"][BUCKET_RANKING][0]
    assert [o["index"] for o in rebased["options"]] == [0, 1, 2, 3]
    assert rebased["correct_ranking"] == [2, 0, 3, 1]


# --------------------------------------------------------------------------
# Answer-position balancing
# --------------------------------------------------------------------------

def test_ranking_options_are_shuffled_but_the_ranked_actions_stay_the_same():
    data = make_scenario_assessment()
    question = data["questions"][BUCKET_RANKING][0]
    question["correct_ranking"] = [0, 1, 2, 3]  # listed best-first, as a model tends to
    texts_by_index = {o["index"]: o["text"] for o in question["options"]}
    best_first = [texts_by_index[i] for i in question["correct_ranking"]]

    balance_mcq_answer_positions(data, rng=random.Random(7))

    shuffled = data["questions"][BUCKET_RANKING][0]
    after = {o["index"]: o["text"] for o in shuffled["options"]}
    assert [o["index"] for o in shuffled["options"]] == [0, 1, 2, 3]
    assert [after[i] for i in shuffled["correct_ranking"]] == best_first
    assert [o["text"] for o in shuffled["options"]] != best_first


def test_scenario_mcq_answer_positions_are_balanced():
    data = make_scenario_assessment()
    data["questions"][BUCKET_SCENARIO_MCQ] = [scenario_mcq(f"m{i}", correct=0) for i in range(8)]
    balance_mcq_answer_positions(data, rng=random.Random(1))
    positions = [q["correct_option_index"] for q in data["questions"][BUCKET_SCENARIO_MCQ]]
    assert sorted(positions) == [0, 0, 1, 1, 2, 2, 3, 3]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bucket, question", [
    (BUCKET_SCENARIO_MCQ, scenario_mcq()),
    ("Scenario Multi-Select Question", scenario_multiselect()),
    (BUCKET_RANKING, ranking()),
    (BUCKET_OPEN_TEXT, open_text()),
    (BUCKET_SHORT_ANSWER, short_answer()),
    (BUCKET_ACTION_PLANNING, action_planning()),
])
def test_every_scenario_factory_question_is_valid(bucket, question):
    assert validate_question(bucket, question) == []


def test_scenario_questions_need_no_blooms_relevance_or_kcm():
    question = open_text()
    assert "blooms_level" not in question and "relevance_percentage" not in question
    assert validate_question(BUCKET_OPEN_TEXT, question, enable_blooms=True) == []


@pytest.mark.parametrize("field, code", [
    ("question_text", "question_text_required"),
    ("scenario_domain", "scenario_domain_required"),
])
def test_scenario_required_fields(field, code):
    question = scenario_mcq(**{field: " "})
    assert code in codes(validate_question(BUCKET_SCENARIO_MCQ, question))


@pytest.mark.parametrize("constraints", [[], None])
def test_scenario_constraints_are_optional(constraints):
    question = scenario_mcq()
    if constraints is None:
        del question["scenario"]["constraints"]
    else:
        question["scenario"]["constraints"] = constraints
    assert validate_question(BUCKET_SCENARIO_MCQ, question) == []


def test_scenario_constraints_must_be_non_blank_lines_when_given():
    question = scenario_mcq()
    question["scenario"]["constraints"] = ["Deadline in two days", "  "]
    assert "scenario_constraints_invalid" in codes(validate_question(BUCKET_SCENARIO_MCQ, question))


def test_scenario_title_and_description_are_required():
    question = scenario_mcq(scenario={"title": "", "description": None, "constraints": []})
    assert {"scenario_title_required", "scenario_description_required"} <= set(
        codes(validate_question(BUCKET_SCENARIO_MCQ, question)))


@pytest.mark.parametrize("order", [(0, 1, 2), (0, 1, 2, 2), (0, 1, 2, 9), "first"])
def test_ranking_must_order_every_option_exactly_once(order):
    question = ranking(order=order if isinstance(order, tuple) else ())
    if isinstance(order, str):
        question["correct_ranking"] = order
    errs = codes(validate_question(BUCKET_RANKING, question))
    assert "correct_ranking_invalid" in errs or "correct_ranking_required" in errs


def test_ranking_accepts_two_actions():
    question = ranking(order=(1, 0))
    question["options"] = question["options"][:2]
    assert validate_question(BUCKET_RANKING, question) == []


@pytest.mark.parametrize("count", [1, 7])
def test_ranking_action_count_is_two_to_six(count):
    question = ranking(order=tuple(range(count)))
    question["options"] = [{"index": i, "text": f"Action {i}"} for i in range(count)]
    errors = [e for e in validate_question(BUCKET_RANKING, question)
              if e["code"] == "option_count_invalid"]
    assert errors and errors[0]["params"]["minimum"] == 2 and errors[0]["params"]["maximum"] == 6


@pytest.mark.parametrize("bucket, factory, field, code", [
    (BUCKET_OPEN_TEXT, open_text, "reference_answer", "reference_answer_required"),
    (BUCKET_SHORT_ANSWER, short_answer, "expected_answer", "expected_answer_required"),
    (BUCKET_ACTION_PLANNING, action_planning, "expected_components", "expected_components_required"),
])
def test_written_answer_keys_are_required(bucket, factory, field, code):
    question = factory()
    question[field] = [] if field == "expected_components" else ""
    assert code in codes(validate_question(bucket, question))


def test_written_answer_types_require_a_rubric():
    question = open_text(rubric=[])
    assert "rubric_required" in codes(validate_question(BUCKET_OPEN_TEXT, question))


# --------------------------------------------------------------------------
# Rubrics
# --------------------------------------------------------------------------

def test_rubric_weights_must_total_100():
    errors = validate_rubric(make_rubric((40, 30, 20)), "q")
    assert codes(errors) == ["rubric_weight_total_invalid"]
    assert errors[0]["params"] == {"expected": 100, "found": 90}


def test_rubric_needs_all_four_anchors():
    rubric = make_rubric()
    del rubric[1]["score_anchors"]["2"]
    rubric[2]["score_anchors"]["3"] = "  "
    errors = [e for e in validate_rubric(rubric, "q")
              if e["code"] == "rubric_anchor_required"]
    assert [e["params"]["missing"] for e in errors] == [["2"], ["3"]]


@pytest.mark.parametrize("weight", [0, -5, 101, 33.5, "40", None, True])
def test_rubric_weight_must_be_a_whole_percentage(weight):
    rubric = make_rubric()
    rubric[0]["weight_percent"] = weight
    errs = codes(validate_rubric(rubric, "q"))
    assert "rubric_weight_invalid" in errs
    # The total is not re-reported once a weight is itself invalid.
    assert "rubric_weight_total_invalid" not in errs


def test_rubric_criterion_names_are_unique():
    rubric = make_rubric()
    rubric[1]["criterion"] = " criterion 1 "
    assert codes(validate_rubric(rubric, "q")) == ["rubric_criterion_duplicate"]


def test_rubric_description_and_evidence_are_optional():
    rubric = make_rubric()
    del rubric[0]["description"]
    del rubric[0]["evidence_indicators"]
    rubric[1]["description"] = ""
    rubric[1]["evidence_indicators"] = []
    assert validate_rubric(rubric, "q") == []


def test_rubric_evidence_must_be_non_blank_lines_when_given():
    rubric = make_rubric()
    rubric[0]["evidence_indicators"] = ["Checks records", "  "]
    assert codes(validate_rubric(rubric, "q")) == ["rubric_evidence_invalid"]


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------

def test_rubric_edit_is_saved_and_audited_as_an_edit():
    data = make_scenario_assessment()
    rubric = make_rubric((50, 25, 25))
    result = apply_question_edit(data, "open_1", {"rubric": rubric}, editor_id="u")
    assert result.question["rubric"] == rubric
    assert result.question["provenance"] == "ai_assisted"
    assert [c["field"] for c in result.events[0]["changed_fields"]] == ["rubric"]


def test_rubric_edit_that_breaks_the_weights_is_blocked():
    data = make_scenario_assessment()
    with pytest.raises(ValidationError) as exc:
        apply_question_edit(data, "open_1", {"rubric": make_rubric((50, 50, 50))}, editor_id="u")
    assert "rubric_weight_total_invalid" in codes(exc.value.errors)


def test_scenario_fields_are_editable_by_dotted_path():
    data = make_scenario_assessment()
    result = apply_question_edit(
        data, "rank_1", {"scenario.title": "New title", "correct_ranking": [0, 1, 2, 3]},
        editor_id="u",
    )
    assert result.question["scenario"]["title"] == "New title"
    assert result.events[0]["details"]["answer_key_changed"] is True


def test_standard_only_fields_are_not_editable_on_a_scenario_question():
    data = make_scenario_assessment()
    with pytest.raises(ValidationError) as exc:
        apply_question_edit(data, "smcq_1", {"blooms_level": "Apply"}, editor_id="u")
    assert codes(exc.value.errors) == ["field_not_editable"]


def test_scenario_question_can_be_added_to_a_scenario_assessment():
    data = make_scenario_assessment()
    payload = {k: v for k, v in short_answer().items()
               if k not in ("question_id", "provenance")}
    result = apply_question_add(data, "short_answer", payload, editor_id="u", position=1)
    assert result.question["provenance"] == "human_authored"
    assert result.question["question_type"] == "SHORT_ANSWER"
    assert result.assessment_data["question_order"][0] == result.question["question_id"]


def test_standard_type_cannot_be_added_to_a_scenario_assessment():
    with pytest.raises(ValidationError) as exc:
        apply_question_add(make_scenario_assessment(), "mcq", {"question_text": "x"}, editor_id="u")
    error = exc.value.errors[0]
    assert error["code"] == "question_type_invalid"
    assert "scenario_mcq" in error["params"]["expected"] and "mcq" not in error["params"]["expected"]


def test_scenario_type_cannot_be_added_to_a_standard_assessment():
    with pytest.raises(ValidationError) as exc:
        apply_question_add(make_assessment(), "open_text", {"question_text": "x"}, editor_id="u")
    assert exc.value.errors[0]["code"] == "question_type_invalid"
    assert "mcq" in exc.value.errors[0]["params"]["expected"]


def test_whole_blob_update_rejects_a_question_of_the_other_set():
    before = make_scenario_assessment()
    after = copy.deepcopy(before)
    after["questions"][BUCKET_MCQ] = [mcq("intruder")]
    after["question_order"].append("intruder")
    with pytest.raises(ValidationError) as exc:
        diff_assessments(before, after, editor_id="u")
    assert exc.value.errors[0]["code"] == "question_type_invalid"
    assert exc.value.errors[0]["question_id"] == "intruder"


def test_whole_blob_update_of_a_scenario_assessment_records_the_edit():
    before = make_scenario_assessment()
    after = copy.deepcopy(before)
    after["questions"][BUCKET_OPEN_TEXT][0]["reference_answer"] = "A revised model answer."
    _, events = diff_assessments(before, after, editor_id="u")
    assert [e["event_code"] for e in events] == ["question_edit"]
    assert events[0]["changed_fields"][0]["field"] == "reference_answer"
