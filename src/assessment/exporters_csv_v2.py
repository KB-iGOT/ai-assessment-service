
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


def _slot_count_v2(q: Dict[str, Any], q_type: str) -> int:
    """How many Option/isOptionCorrect columns this question needs."""
    if _has_option_rows(q, q_type) or q_type == "RANK":
        return len(q.get("options", []) or [])
    if q_type in ("OPEN", "SA", "PLAN"):
        return len(_answer_rows(q, q_type))
    if q_type == "MTF":
        return len(q.get("pairs", []) or [])
    if q_type == "FTB":
        correct_ans = q.get("correct_answer")
        if isinstance(correct_ans, (dict, list)):
            return len(correct_ans)
        return 1
    return 0  # T/F is fixed at 2, already covered by the floor below


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
    all_questions = [
        {
            "raw": q,
            "bucket": bucket,
            "type": CSV_TYPE_BY_BUCKET.get(bucket, bucket),
            "complexity": q.get('reasoning', {}).get('complexity_level', 'Easy'),
        }
        for bucket, q in iter_questions_in_order(assessment_data)
    ]

    max_slots = max([7] + [_slot_count_v2(item["raw"], item["type"]) for item in all_questions])
    has_rubrics = any(item["bucket"] in RUBRIC_BUCKETS for item in all_questions)

    # Define Header
    headers = ["QuestionNo", "QuestionType", "Question", "QuestionTagging"]
    for i in range(1, max_slots + 1):
        headers.extend([f"Option{i}", f"isOption{i}Correct"])
    if has_rubrics:
        headers.append(RUBRIC_COLUMN)

    rows = []
    q_counter = 1

    for item in all_questions:
        q = item["raw"]
        q_type = item["type"]
        difficulty_map = {"beginner": "Easy", "easy": "Easy", "medium": "Medium", "intermediate": "Medium", "hard": "Difficult", "difficult": "Difficult", "advanced": "Difficult"}
        raw_diff = str(q.get("difficulty_level", "")).lower()
        tagging = difficulty_map.get(raw_diff, "Medium")
        
        default_q_txt = "" 
        if q_type == "MTF":
            # Extract Matching Context and Prepend it
            context = q.get("matching_context", "Match the following items appropriately:")
            default_q_txt = f"{context}\n\n" if context else "Match the following items appropriately:\n\n"
        
        q_text = q.get("question_text", default_q_txt)
        if q_type == "FTB":
            q_text = re.sub(r'_{2,}', '<blank>', q_text)
        if isinstance(q.get("scenario"), dict):
            q_text = scenario_question_text(q)

        row = {
            "QuestionNo": q_counter,
            "QuestionType": q_type,
            "Question": q_text,
            "QuestionTagging": tagging,
        }
        
        # Override the MTF string to only contain the context since it lacks a QuestionText itself
        if q_type == "MTF":
           row["Question"] = q.get("matching_context", "Match the following items appropriately:")
        
        # Populate Options columns (Default empty)
        for i in range(1, max_slots + 1):
            row[f"Option{i}"] = ""
            row[f"isOption{i}Correct"] = ""
        if has_rubrics:
            rubric = q.get("rubric") if item["bucket"] in RUBRIC_BUCKETS else None
            row[RUBRIC_COLUMN] = json.dumps(rubric, ensure_ascii=False) if rubric else ""

        # Logic per Type
        if _has_option_rows(q, q_type):
            options = q.get("options", [])
            correct_idx = q.get("correct_option_index")
            # correct_idx could be int (SCA) or list of ints (MCA) return 1-based index usually? 
            # Internal schema is 0-indexed or 1-indexed? Usually 0-indexed lists.
            # Example provided: Option1..Option7.
            
            # Normalize correct_idx to set
            correct_set = set()
            if isinstance(correct_idx, list):
                correct_set = {int(x) for x in correct_idx}
            elif correct_idx is not None:
                correct_set = {int(correct_idx)}
                
            for i, opt in enumerate(options[:max_slots]):
                col_idx = i + 1
                row[f"Option{col_idx}"] = opt.get("text", "")
                # Match using the option's own index field. `normalize_assessment`
                # guarantees it is present; the fallback is zero-based to match
                # the documented convention and the PDF/DOCX exporters — it used
                # to be one-based here, which could mark a different option
                # correct in CSV than in the other formats.
                opt_index = int(opt["index"]) if opt.get("index") is not None else i
                is_correct = "Yes" if opt_index in correct_set else "No"
                row[f"isOption{col_idx}Correct"] = is_correct

        elif q_type == "RANK":
            # Each action, with its place in the correct ranking (1 = most effective).
            rank_of = {}
            for position, index in enumerate(q.get("correct_ranking") or [], start=1):
                try:
                    rank_of[int(index)] = position
                except (TypeError, ValueError):
                    continue
            for i, opt in enumerate((q.get("options") or [])[:max_slots]):
                col_idx = i + 1
                opt_index = int(opt["index"]) if opt.get("index") is not None else i
                row[f"Option{col_idx}"] = opt.get("text", "")
                row[f"isOption{col_idx}Correct"] = rank_of.get(opt_index, "")

        elif q_type in ("OPEN", "SA", "PLAN"):
            for i, (text, tag) in enumerate(_answer_rows(q, q_type)[:max_slots]):
                col_idx = i + 1
                row[f"Option{col_idx}"] = text
                row[f"isOption{col_idx}Correct"] = tag

        elif q_type == "T/F":
            # Fixed Options: TRUE / FALSE
            row["Option1"] = "TRUE"
            row["Option2"] = "FALSE"
            
            correct_ans = str(q.get("correct_answer", "")).lower()
            row["isOption1Correct"] = "Yes" if correct_ans == "true" else "No"
            row["isOption2Correct"] = "Yes" if correct_ans == "false" else "No"

        elif q_type == "MTF":
            # Map Pairs: Left -> OptionN, Right -> isOptionNCorrect
            pairs = q.get("pairs", [])
            for i, pair in enumerate(pairs[:max_slots]):
                col_idx = i + 1
                row[f"Option{col_idx}"] = pair.get("left", "")
                row[f"isOption{col_idx}Correct"] = pair.get("right", "")

        elif q_type == "FTB":
            correct_ans = q.get("correct_answer") # Could be string or list/dict
            if isinstance(correct_ans, dict):
                 # {"blank1": "val", "blank2": "val"}
                 for i, (k, v) in enumerate(correct_ans.items()):
                     if i >= max_slots: break
                     col_idx = i + 1
                     row[f"Option{col_idx}"] = v
                     row[f"isOption{col_idx}Correct"] = f"Blank{col_idx}"
            elif isinstance(correct_ans, list):
                for i, v in enumerate(correct_ans):
                    if i >= max_slots: break
                    col_idx = i + 1
                    row[f"Option{col_idx}"] = v
                    row[f"isOption{col_idx}Correct"] = f"Blank{col_idx}"
            else:
                 # Single string?
                 row["Option1"] = str(correct_ans)
                 row["isOption1Correct"] = "Blank1"

        rows.append(row)
        q_counter += 1

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

    max_options = max([7] + [len(item["raw"].get("options", []) or []) for item in all_questions])

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
        correct_idx = q.get("correct_option_index")
        correct_set = set()
        if isinstance(correct_idx, list):
            correct_set = {int(x) for x in correct_idx}
        elif correct_idx is not None:
            correct_set = {int(correct_idx)}

        for i, opt in enumerate(options[:max_options]):
            col_idx = i + 1
            row[f"Option{col_idx}"] = opt.get("text", "")
            opt_index = int(opt["index"]) if opt.get("index") is not None else i
            row[f"IsOption{col_idx}Correct"] = "TRUE" if opt_index in correct_set else "FALSE"

        rows.append(row)
        q_counter += 1

    # utf-8-sig for Excel, as in generate_csv_v2.
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
