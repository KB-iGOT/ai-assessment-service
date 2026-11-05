"""Scenario assessment exports — CSV v2 and basic CSV."""

import csv
import io
import json

from assessment.exporters_csv_v2 import RUBRIC_COLUMN, generate_csv_basic, generate_csv_v2
from assessment.questions import normalize_assessment
from factories import make_assessment, make_scenario_assessment


def csv_rows(generate, data, tmp_path):
    path = tmp_path / "out.csv"
    generate(normalize_assessment(data), path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def test_csv_v2_type_codes_follow_question_order(tmp_path):
    _, rows = csv_rows(generate_csv_v2, make_scenario_assessment(), tmp_path)
    assert [r["QuestionType"] for r in rows] == ["MCQ-SCA", "MCQ-MCA", "OPEN", "RANK", "PLAN", "SA"]


def test_csv_v2_question_cell_starts_with_the_scenario(tmp_path):
    _, rows = csv_rows(generate_csv_v2, make_scenario_assessment(), tmp_path)
    assert rows[0]["Question"] == (
        "Disagreement Over Training Data\n"
        "Two team members disagree about delayed participant data.\n"
        "Constraints: Three-day deadline; Conflicting accounts\n\n"
        "Question smcq_1?"
    )


def test_csv_v2_question_cell_skips_the_constraints_line_when_there_are_none(tmp_path):
    data = make_scenario_assessment()
    data["questions"]["Scenario MCQ Question"][0]["scenario"]["constraints"] = []
    _, rows = csv_rows(generate_csv_v2, data, tmp_path)
    assert rows[0]["Question"] == (
        "Disagreement Over Training Data\n"
        "Two team members disagree about delayed participant data.\n\n"
        "Question smcq_1?"
    )


def test_csv_v2_ranking_rows_carry_each_actions_rank(tmp_path):
    _, rows = csv_rows(generate_csv_v2, make_scenario_assessment(), tmp_path)
    rank = rows[3]
    # correct_ranking (2, 0, 3, 1): Y first, then W, Z, X.
    assert [(rank[f"Option{i}"], rank[f"isOption{i}Correct"]) for i in (1, 2, 3, 4)] == [
        ("W", "2"), ("X", "4"), ("Y", "1"), ("Z", "3"),
    ]


def test_csv_v2_written_answers_fill_the_slots(tmp_path):
    _, rows = csv_rows(generate_csv_v2, make_scenario_assessment(), tmp_path)
    open_row, plan_row, sa_row = rows[2], rows[4], rows[5]
    assert [(open_row[f"Option{i}"], open_row[f"isOption{i}Correct"]) for i in (1, 2)] == [
        ("Establish the facts first.", "Answer"), ("", ""),
    ]
    assert (sa_row["Option1"], sa_row["isOption1Correct"]) == ("Check the shared records.", "Answer")
    assert sa_row["Option2"] == ""
    assert [plan_row[f"isOption{i}Correct"] for i in (1, 2, 3)] == ["Component"] * 3


def test_csv_v2_rubric_column_holds_the_rubric_as_json(tmp_path):
    data = make_scenario_assessment()
    headers, rows = csv_rows(generate_csv_v2, data, tmp_path)
    assert headers[-1] == RUBRIC_COLUMN
    assert json.loads(rows[2][RUBRIC_COLUMN]) == data["questions"]["Open Text Question"][0]["rubric"]
    assert rows[0][RUBRIC_COLUMN] == ""  # an MCQ has no rubric


def test_standard_assessment_csv_has_no_rubric_column(tmp_path):
    headers, _ = csv_rows(generate_csv_v2, make_assessment(), tmp_path)
    assert RUBRIC_COLUMN not in headers


def test_csv_basic_includes_scenario_mcqs_with_their_scenario(tmp_path):
    _, rows = csv_rows(generate_csv_basic, make_scenario_assessment(), tmp_path)
    assert len(rows) == 2
    assert rows[0]["Question"].startswith("Disagreement Over Training Data\n")
    assert rows[1]["IsOption1Correct"] == "TRUE" and rows[1]["IsOption3Correct"] == "TRUE"

