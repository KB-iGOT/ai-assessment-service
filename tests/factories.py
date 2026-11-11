"""Builders for valid questions and assessments, shared by every test module."""

from assessment.questions import (
    BUCKET_ACTION_PLANNING,
    BUCKET_FTB,
    BUCKET_MCQ,
    BUCKET_MTF,
    BUCKET_MULTICHOICE,
    BUCKET_OPEN_TEXT,
    BUCKET_RANKING,
    BUCKET_SCENARIO_MCQ,
    BUCKET_SCENARIO_MULTISELECT,
    BUCKET_SHORT_ANSWER,
    BUCKET_TRUEFALSE,
)

USER_ID = "user-1"
OTHER_USER_ID = "user-2"


# Each builder returns a question that passes `validate_question`.

def _common(qid, defaults, extra):
    """Shared fields, then the type's own `defaults`, then caller overrides."""
    q = {
        "question_id": qid,
        "provenance": "ai_generated",
        "blooms_level": "Remember",
        "relevance_percentage": 90,
        "difficulty_level": "medium",
        "course_name": "Course A",
        "answer_rationale": {"correct_answer_explanation": "Because."},
    }
    q.update(defaults)
    q.update(extra)
    return q


def make_options(texts, start=0):
    return [{"index": start + i, "text": t} for i, t in enumerate(texts)]


def mcq(qid="mcq_1", correct=0, texts=("A", "B", "C", "D"), **extra):
    return _common(qid, {"question_text": f"Question {qid}?", "options": make_options(texts),
                         "correct_option_index": correct}, extra)


def option_ftb(qid="ftb_1", correct=1, texts=("red", "blue", "green"), **extra):
    return _common(qid, {"question_text": "The sky is ____ .", "options": make_options(texts),
                         "correct_option_index": correct}, extra)


def legacy_ftb(qid="ftb_legacy", **extra):
    return _common(qid, {"question_text": "The sky is ____ .", "correct_answer": "blue"}, extra)


def mtf(qid="mtf_1", **extra):
    return _common(qid, {"matching_context": "Match capitals",
                         "pairs": [{"left": "India", "right": "Delhi"},
                                   {"left": "France", "right": "Paris"}]}, extra)


def multichoice(qid="mc_1", correct=(0, 2), **extra):
    return _common(qid, {"question_text": "Pick all primes",
                         "options": make_options(["2", "4", "3", "6"]),
                         "correct_option_index": list(correct)}, extra)


def truefalse(qid="tf_1", answer="True", **extra):
    return _common(qid, {"question_text": "Water is wet.", "correct_answer": answer}, extra)


def make_assessment():
    return {
        "blueprint": {"title": "Test blueprint"},
        "questions": {
            BUCKET_MCQ: [mcq("mcq_1"), mcq("mcq_2", correct=2)],
            BUCKET_FTB: [option_ftb("ftb_1")],
            BUCKET_MTF: [mtf("mtf_1")],
            BUCKET_MULTICHOICE: [multichoice("mc_1")],
            BUCKET_TRUEFALSE: [truefalse("tf_1")],
        },
        "question_order": ["mcq_1", "mcq_2", "ftb_1", "mtf_1", "mc_1", "tf_1"],
    }


# --------------------------------------------------------------------------
# Scenario assessment — each builder returns a question that passes
# `validate_question`.
# --------------------------------------------------------------------------

def make_rubric(weights=(40, 30, 30)):
    return [
        {
            "criterion": f"Criterion {i + 1}",
            "description": f"What criterion {i + 1} looks for.",
            "weight_percent": weight,
            "score_anchors": {level: f"Level {level} for criterion {i + 1}"
                              for level in ("0", "1", "2", "3")},
            "evidence_indicators": [f"Evidence for criterion {i + 1}"],
        }
        for i, weight in enumerate(weights)
    ]


def _scenario_common(qid, defaults, extra):
    q = {
        "question_id": qid,
        "provenance": "ai_generated",
        "scenario_domain": "Team conflict",
        "difficulty_level": "intermediate",
        "scenario": {
            "title": "Disagreement Over Training Data",
            "description": "Two team members disagree about delayed participant data.",
            "constraints": ["Three-day deadline", "Conflicting accounts"],
        },
        "question_text": f"Question {qid}?",
        "answer_rationale": {"correct_answer_explanation": "Because."},
    }
    q.update(defaults)
    q.update(extra)
    return q


def scenario_mcq(qid="smcq_1", correct=0, **extra):
    return _scenario_common(qid, {"options": make_options(["A", "B", "C", "D"]),
                                  "correct_option_index": correct}, extra)


def scenario_multiselect(qid="sms_1", correct=(0, 2), **extra):
    return _scenario_common(qid, {"options": make_options(["A", "B", "C", "D", "E"]),
                                  "correct_option_index": list(correct)}, extra)


def ranking(qid="rank_1", order=(2, 0, 3, 1), **extra):
    return _scenario_common(qid, {"options": make_options(["W", "X", "Y", "Z"]),
                                  "correct_ranking": list(order)}, extra)


def open_text(qid="open_1", **extra):
    return _scenario_common(qid, {"reference_answer": "Establish the facts first.",
                                  "rubric": make_rubric()}, extra)


def short_answer(qid="sa_1", **extra):
    return _scenario_common(qid, {"expected_answer": "Check the shared records.",
                                  "rubric": make_rubric((60, 40))}, extra)


def action_planning(qid="plan_1", **extra):
    return _scenario_common(qid, {"expected_components": ["Verify", "Assign", "Follow up"],
                                  "rubric": make_rubric((30, 40, 30))}, extra)


def make_scenario_assessment():
    return {
        "blueprint": {"assessment_scope_summary": "Scenario test blueprint"},
        "questions": {
            BUCKET_SCENARIO_MCQ: [scenario_mcq("smcq_1")],
            BUCKET_SCENARIO_MULTISELECT: [scenario_multiselect("sms_1")],
            BUCKET_OPEN_TEXT: [open_text("open_1")],
            BUCKET_RANKING: [ranking("rank_1")],
            BUCKET_ACTION_PLANNING: [action_planning("plan_1")],
            BUCKET_SHORT_ANSWER: [short_answer("sa_1")],
        },
        "question_order": ["smcq_1", "sms_1", "open_1", "rank_1", "plan_1", "sa_1"],
    }
