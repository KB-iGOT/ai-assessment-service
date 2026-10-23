"""Builders for valid questions and assessments, shared by every test module."""

from assessment.questions import (
    BUCKET_FTB,
    BUCKET_MCQ,
    BUCKET_MTF,
    BUCKET_MULTICHOICE,
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
