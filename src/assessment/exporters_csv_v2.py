
import csv
import json
import re
from typing import Dict, List, Any
from pathlib import Path

from .questions import (
    BUCKET_FTB,
    BUCKET_SCENARIO_MCQ,
    BUCKET_SCENARIO_MULTISELECT,
    RUBRIC_BUCKETS,
    is_legacy_ftb,
    iter_questions_in_order,
)

# Internal bucket name -> the type code used in the iGot import schema.
# Scenario MCQ and multi-select reuse the MCQ codes — they are answered the
# same way; the scenario is carried in the Question cell.
CSV_TYPE_BY_BUCKET = {
    "Multiple Choice Question": "MCQ-SCA",
    "Multi-Choice Question": "MCQ-MCA",
    "True/False Question": "T/F",
    "MTF Question": "MTF",
    "FTB Question": "FTB",
    "Scenario MCQ Question": "MCQ-SCA",
    "Scenario Multi-Select Question": "MCQ-MCA",
    "Ranking Question": "RANK",
    "Open Text Question": "OPEN",
    "Short Answer Question": "SA",
    "Action Planning Question": "PLAN",
}

# Header of the column carrying a written-answer question's rubric as JSON.
# Present only when the assessment has such a question, so the CSV of every
# other assessment keeps exactly the columns it always had.
RUBRIC_COLUMN = "Rubric"


def scenario_question_text(q: Dict[str, Any]) -> str:
    """
    A scenario question's Question cell: the scenario (title, description and
    constraints) followed by the question itself. A question with no scenario
    returns its text unchanged.
    """
    text = str(q.get("question_text") or "")
    scenario = q.get("scenario")
    if not isinstance(scenario, dict):
        return text
    parts = [str(scenario.get(k) or "").strip() for k in ("title", "description")]
    constraints = [str(c).strip() for c in scenario.get("constraints") or [] if str(c).strip()]
    if constraints:
        parts.append("Constraints: " + "; ".join(constraints))
    head = "\n".join(p for p in parts if p)
    return f"{head}\n\n{text}" if head else text


def _answer_rows(q: Dict[str, Any], q_type: str) -> List[tuple]:
    """
    The (Option, isOptionCorrect) pairs of a written-answer scenario question.

    OPEN / SA: the reference (or expected) answer tagged `Answer`. PLAN: each
    expected component tagged `Component`.
    """
    if q_type == "PLAN":
        return [(c, "Component") for c in q.get("expected_components") or []]
    answer = q.get("reference_answer") if q_type == "OPEN" else q.get("expected_answer")
    return [(answer, "Answer")] if answer else []


def _has_option_rows(q: Dict[str, Any], q_type: str) -> bool:
    """
    True when the row is written as options with Yes/No correctness: MCQs, and
    option FTBs, which keep type FTB but are filled exactly like MCQ-SCA. A
    legacy FTB keeps its answer-text / BlankN row.
    """
    return q_type in ("MCQ-SCA", "MCQ-MCA") or (
        q_type == "FTB" and not is_legacy_ftb(BUCKET_FTB, q)
    )


# --------------------------------------------------------------------------
# Option slots
# --------------------------------------------------------------------------
# Each question fills the OptionN / isOptionNCorrect columns from a list of
# (Option, isOptionCorrect) pairs, one pair per slot, built per type below.

# Every CSV row has at least this many option slots.
MIN_OPTION_SLOTS = 7

DEFAULT_MTF_CONTEXT = "Match the following items appropriately:"

# Request / model difficulty -> the QuestionTagging value. Anything else is Medium.
DIFFICULTY_TAGS = {
    "beginner": "Easy", "easy": "Easy",
    "medium": "Medium", "intermediate": "Medium",
    "hard": "Difficult", "difficult": "Difficult", "advanced": "Difficult",
}


def _option_index(option: Dict[str, Any], position: int) -> int:
    """
    The option's own `index`. `normalize_assessment` guarantees it is present;
    the fallback is zero-based to match the documented convention and the
    PDF/DOCX exporters — it used to be one-based here, which could mark a
    different option correct in CSV than in the other formats.
    """
    return int(option["index"]) if option.get("index") is not None else position


def _correct_indexes(correct_idx: Any) -> set:
    """`correct_option_index` — one int (SCA) or a list (MCA) — as a set."""
    if isinstance(correct_idx, list):
        return {int(x) for x in correct_idx}
    if correct_idx is not None:
        return {int(correct_idx)}
    return set()


def _choice_slots(q: Dict[str, Any]) -> List[tuple]:
    """MCQs and option FTBs: each option, `Yes` when it is correct, else `No`."""
    correct = _correct_indexes(q.get("correct_option_index"))
    return [
        (opt.get("text", ""), "Yes" if _option_index(opt, i) in correct else "No")
        for i, opt in enumerate(q.get("options", []))
    ]


def _ranking_slots(q: Dict[str, Any]) -> List[tuple]:
    """Each action, with its place in the correct ranking (1 = most effective)."""
    rank_of = {}
    for position, index in enumerate(q.get("correct_ranking") or [], start=1):
        try:
            rank_of[int(index)] = position
        except (TypeError, ValueError):
            continue
    return [
        (opt.get("text", ""), rank_of.get(_option_index(opt, i), ""))
        for i, opt in enumerate(q.get("options") or [])
    ]


def _true_false_slots(q: Dict[str, Any]) -> List[tuple]:
    """Fixed options TRUE / FALSE."""
    answer = str(q.get("correct_answer", "")).lower()
    return [
        ("TRUE", "Yes" if answer == "true" else "No"),
        ("FALSE", "Yes" if answer == "false" else "No"),
    ]


def _matching_slots(q: Dict[str, Any]) -> List[tuple]:
    """Each pair: left in OptionN, right in isOptionNCorrect."""
    return [(pair.get("left", ""), pair.get("right", "")) for pair in q.get("pairs", [])]


def _legacy_blank_slots(q: Dict[str, Any]) -> List[tuple]:
    """
    A legacy FTB's answers, tagged BlankN. `correct_answer` is one string, a
    list, or a {"blank1": ..., "blank2": ...} dict.
    """
    answer = q.get("correct_answer")
    if isinstance(answer, dict):
        answer = list(answer.values())
    if not isinstance(answer, list):
        return [(str(answer), "Blank1")]
    return [(value, f"Blank{n}") for n, value in enumerate(answer, start=1)]


def _option_slots(q: Dict[str, Any], q_type: str) -> List[tuple]:
    """The (Option, isOptionCorrect) pair for each slot this question fills."""
    if _has_option_rows(q, q_type):
        return _choice_slots(q)
    if q_type == "RANK":
        return _ranking_slots(q)
    if q_type in ("OPEN", "SA", "PLAN"):
        return _answer_rows(q, q_type)
    if q_type == "T/F":
        return _true_false_slots(q)
    if q_type == "MTF":
        return _matching_slots(q)
    if q_type == "FTB":
        return _legacy_blank_slots(q)
    return []


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def _question_cell(q: Dict[str, Any], q_type: str) -> Any:
    """
    The Question cell. An MTF has no question text of its own, so it carries
    the matching context; an FTB's blanks are written `<blank>`; a scenario
    question starts with its scenario.
    """
    if q_type == "MTF":
        return q.get("matching_context", DEFAULT_MTF_CONTEXT)
    if isinstance(q.get("scenario"), dict):
        return scenario_question_text(q)
    text = q.get("question_text", "")
    if q_type == "FTB":
        text = re.sub(r'_{2,}', '<blank>', text)
    return text


def _question_tagging(q: Dict[str, Any]) -> str:
    return DIFFICULTY_TAGS.get(str(q.get("difficulty_level", "")).lower(), "Medium")


def _csv_v2_row(
    number: int,
    bucket: str,
    q: Dict[str, Any],
    q_type: str,
    slots: List[tuple],
    max_slots: int,
    has_rubrics: bool,
) -> Dict[str, Any]:
    row = {
        "QuestionNo": number,
        "QuestionType": q_type,
        "Question": _question_cell(q, q_type),
        "QuestionTagging": _question_tagging(q),
    }
    padded = slots[:max_slots] + [("", "")] * (max_slots - len(slots))
    for i, (option, correct) in enumerate(padded, start=1):
        row[f"Option{i}"] = option
        row[f"isOption{i}Correct"] = correct
    if has_rubrics:
        rubric = q.get("rubric") if bucket in RUBRIC_BUCKETS else None
        row[RUBRIC_COLUMN] = json.dumps(rubric, ensure_ascii=False) if rubric else ""
    return row


def generate_csv_v2(assessment_data: Dict[str, Any], output_path: Path):
    """
    Generates a CSV export with the specific V2 schema required by the user.
    Schema:
    QuestionNo, QuestionType, Question, QuestionTagging, Option1, isOption1Correct, ... OptionN, isOptionNCorrect
    N is at least 7, widened to fit whichever question in this assessment carries
    the most options/pairs/blanks, so a question exceeding the old fixed 7 never
    gets truncated.

    Scenario questions put their scenario at the start of the Question cell.
    Their own types fill the slots as follows:
      * RANK — each action, with its correct rank (1 = most effective)
      * OPEN / SA — the reference / expected answer tagged `Answer`
      * PLAN — each expected component tagged `Component`
    and an assessment holding OPEN, SA or PLAN questions gets one more column,
    `Rubric`, holding each such question's rubric as JSON.
    """

    # Flatten in the assessment's authoritative sequence, so the row order here
    # matches the PDF, DOCX and JSON exports.
    questions = []
    for bucket, q in iter_questions_in_order(assessment_data):
        q_type = CSV_TYPE_BY_BUCKET.get(bucket, bucket)
        questions.append((bucket, q, q_type, _option_slots(q, q_type)))

    # Wide enough for whichever question fills the most slots.
    max_slots = max([MIN_OPTION_SLOTS] + [len(slots) for *_, slots in questions])
    has_rubrics = any(bucket in RUBRIC_BUCKETS for bucket, *_ in questions)

    headers = ["QuestionNo", "QuestionType", "Question", "QuestionTagging"]
    for i in range(1, max_slots + 1):
        headers.extend([f"Option{i}", f"isOption{i}Correct"])
    if has_rubrics:
        headers.append(RUBRIC_COLUMN)

    rows = [
        _csv_v2_row(number, bucket, q, q_type, slots, max_slots, has_rubrics)
        for number, (bucket, q, q_type, slots) in enumerate(questions, start=1)
    ]

    # Write CSV. utf-8-sig so Excel shows non-ASCII text (dashes, curly
    # quotes, Hindi) correctly instead of guessing the ANSI codepage.
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def generate_csv_basic(assessment_data: Dict[str, Any], output_path: Path):
    """
    Basic CSV export — MCQ only (SCA + MCA), no QuestionType/QuestionTagging columns.
    Columns: SR, Question, Option1..OptionN, IsOption1Correct..IsOptionNCorrect
    N is at least 7, widened to fit whichever question in this assessment carries
    the most options, so a question exceeding the floor never gets truncated.
    IsOptionNCorrect values: TRUE / FALSE
    """
    # Ordered by the assessment's authoritative sequence, filtered to the MCQ
    # types this schema supports.
    all_questions = [
        {"raw": q, "type": CSV_TYPE_BY_BUCKET[bucket]}
        for bucket, q in iter_questions_in_order(assessment_data)
        if bucket in ("Multiple Choice Question", "Multi-Choice Question",
                      BUCKET_SCENARIO_MCQ, BUCKET_SCENARIO_MULTISELECT)
    ]

    max_options = max([MIN_OPTION_SLOTS] + [len(item["raw"].get("options", []) or []) for item in all_questions])

    headers = ["SR", "Question"]
    for i in range(1, max_options + 1):
        headers.extend([f"Option{i}", f"IsOption{i}Correct"])

    rows = []
    q_counter = 1

    for item in all_questions:
        q = item["raw"]
        q_type = item["type"]

        q_text = scenario_question_text(q)

        row = {"SR": q_counter, "Question": q_text}

        for i in range(1, max_options + 1):
            row[f"Option{i}"] = ""
            row[f"IsOption{i}Correct"] = ""

        options = q.get("options", [])
        correct_set = _correct_indexes(q.get("correct_option_index"))

        for i, opt in enumerate(options[:max_options]):
            col_idx = i + 1
            row[f"Option{col_idx}"] = opt.get("text", "")
            row[f"IsOption{col_idx}Correct"] = "TRUE" if _option_index(opt, i) in correct_set else "FALSE"

        rows.append(row)
        q_counter += 1

    # utf-8-sig for Excel, as in generate_csv_v2.
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
