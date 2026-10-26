"""
Answer-position balancing (`balance_mcq_answer_positions`).

Models tend to put the correct option in the same slot. Balancing spreads it
evenly across positions without ever changing which option is correct.
"""

import copy
import random
from collections import Counter

import pytest

from assessment.questions import (
    BUCKET_FTB,
    BUCKET_MCQ,
    BUCKET_MULTICHOICE,
    balance_mcq_answer_positions,
)
from factories import legacy_ftb, make_options, mcq, multichoice, option_ftb


def correct_text(question):
    key = question["correct_option_index"]
    return next(o["text"] for o in question["options"] if o["index"] == key)


def mcqs(n, option_count=4, correct=0):
    return [
        mcq(f"m{i}", correct=correct, texts=tuple(f"q{i}-opt{j}" for j in range(option_count)))
        for i in range(n)
    ]


@pytest.mark.parametrize("n,option_count", [(8, 4), (10, 4), (7, 3), (3, 4), (1, 4)])
def test_correct_positions_are_spread_evenly(n, option_count):
    data = {"questions": {BUCKET_MCQ: mcqs(n, option_count)}}  # model put every answer first
    balance_mcq_answer_positions(data, rng=random.Random(0))
    positions = Counter(q["correct_option_index"] for q in data["questions"][BUCKET_MCQ])
    low, high = n // option_count, -(-n // option_count)
    for position in range(option_count):
        assert low <= positions.get(position, 0) <= high


def test_same_option_stays_correct_and_no_option_is_lost():
    questions = mcqs(12)
    before = {q["question_id"]: (correct_text(q), sorted(o["text"] for o in q["options"]))
              for q in questions}
    data = {"questions": {BUCKET_MCQ: questions}}
    balance_mcq_answer_positions(data, rng=random.Random(1))
    for q in data["questions"][BUCKET_MCQ]:
        answer, texts = before[q["question_id"]]
        assert correct_text(q) == answer
        assert sorted(o["text"] for o in q["options"]) == texts
        assert [o["index"] for o in q["options"]] == list(range(len(q["options"])))
        assert q["options"][q["correct_option_index"]]["text"] == answer


def test_option_ftbs_are_balanced_too():
    ftbs = [option_ftb(f"f{i}", correct=0) for i in range(6)]
    data = {"questions": {BUCKET_FTB: ftbs}}
    balance_mcq_answer_positions(data, rng=random.Random(2))
    positions = Counter(q["correct_option_index"] for q in data["questions"][BUCKET_FTB])
    assert set(positions.values()) == {2}
    assert all(correct_text(q) == "red" for q in data["questions"][BUCKET_FTB])


def test_legacy_ftb_and_multichoice_are_untouched():
    data = {"questions": {
        BUCKET_FTB: [legacy_ftb("l1")],
        BUCKET_MULTICHOICE: [multichoice("mc1"), multichoice("mc2")],
    }}
    before = copy.deepcopy(data)
    balance_mcq_answer_positions(data, rng=random.Random(3))
    assert data == before


def _damaged(kind):
    q = mcq("bad", texts=("a", "b", "c", "d"))
    if kind == "gap":
        q["options"] = [{"index": i, "text": t} for i, t in zip((0, 1, 3, 4), "abcd")]
    elif kind == "string_answer":
        q["correct_option_index"] = "2"
    elif kind == "answer_out_of_range":
        q["correct_option_index"] = 9
    elif kind == "single_option":
        q["options"] = make_options(["only"])
    elif kind == "list_answer":
        q["correct_option_index"] = [0]
    elif kind == "one_based":
        q["options"] = make_options(["a", "b", "c", "d"], start=1)
        q["correct_option_index"] = 1
    return q


@pytest.mark.parametrize("kind", ["gap", "string_answer", "answer_out_of_range",
                                  "single_option", "list_answer", "one_based"])
def test_damaged_questions_are_left_as_generated(kind):
    damaged = _damaged(kind)
    data = {"questions": {BUCKET_MCQ: [damaged] + mcqs(4)}}
    before = copy.deepcopy(damaged)
    balance_mcq_answer_positions(data, rng=random.Random(4))
    assert data["questions"][BUCKET_MCQ][0] == before


def test_groups_by_option_count():
    three = mcqs(6, option_count=3)
    four = [mcq(f"f{i}", texts=("w", "x", "y", "z")) for i in range(8)]
    data = {"questions": {BUCKET_MCQ: three + four}}
    balance_mcq_answer_positions(data, rng=random.Random(5))
    got3 = Counter(q["correct_option_index"] for q in data["questions"][BUCKET_MCQ][:6])
    got4 = Counter(q["correct_option_index"] for q in data["questions"][BUCKET_MCQ][6:])
    assert got3 == {0: 2, 1: 2, 2: 2}
    assert got4 == {0: 2, 1: 2, 2: 2, 3: 2}


def test_missing_or_non_list_buckets_do_not_crash():
    balance_mcq_answer_positions({}, rng=random.Random(0))
    balance_mcq_answer_positions({"questions": {BUCKET_MCQ: None}}, rng=random.Random(0))
    balance_mcq_answer_positions({"questions": {BUCKET_MCQ: ["junk", 1]}}, rng=random.Random(0))
