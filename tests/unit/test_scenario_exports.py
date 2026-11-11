"""
Scenario assessment exports — CSV v2, basic CSV, PDF and DOCX.

The PDF is checked through `generate_html_content`, the HTML WeasyPrint turns
into the PDF, so these tests do not need WeasyPrint's system libraries.
"""

import csv
import io
import json

import pytest
from docx import Document

from assessment.exporters import (
    PROVENANCE_LABELS,
    RUBRIC_ANCHOR_LEVELS,
    generate_docx,
    generate_html_content,
)
from assessment.exporters_csv_v2 import RUBRIC_COLUMN, generate_csv_basic, generate_csv_v2
from assessment.questions import (
    BUCKET_OPEN_TEXT,
    BUCKET_RANKING,
    BUCKET_SCENARIO_MCQ,
    normalize_assessment,
)
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


# --------------------------------------------------------------------------
# PDF and DOCX
# --------------------------------------------------------------------------
# make_scenario_assessment() holds one question of each type, in the order
# smcq_1, sms_1, open_1, rank_1, plan_1, sa_1.

# The answer key each type's question shows, from the factory defaults.
ANSWER_KEYS = [
    "Correct Answer: A",                                     # correct_option_index 0
    "Correct Options: A, C",                                 # (0, 2)
    "Reference Answer: Establish the facts first.",
    "Correct Ranking (most to least effective): C &gt; A &gt; D &gt; B",  # (2, 0, 3, 1)
    "Expected Components",
    "Expected Answer: Check the shared records.",
]


def question_blocks(data):
    """The PDF's HTML, split into one chunk per scenario question, in order."""
    html = generate_html_content(normalize_assessment(data))
    return html.split("<div class='question-block'>")[1:]


def docx_of(data, tmp_path):
    path = tmp_path / "out.docx"
    generate_docx(normalize_assessment(data), path)
    return Document(str(path))


def docx_text(doc):
    return "\n".join(p.text for p in doc.paragraphs)


def test_pdf_renders_every_scenario_question_in_order_with_its_scenario():
    blocks = question_blocks(make_scenario_assessment())

    assert len(blocks) == 6
    for number, (block, qid) in enumerate(
            zip(blocks, ["smcq_1", "sms_1", "open_1", "rank_1", "plan_1", "sa_1"]), start=1):
        assert f"Q{number}: Question {qid}?" in block
        assert "<b>Scenario: Disagreement Over Training Data</b>" in block
        assert "Two team members disagree about delayed participant data." in block
        assert "<li>Three-day deadline</li><li>Conflicting accounts</li>" in block
        assert "<b>Scenario Domain:</b> Team conflict" in block
        assert f"<b>Provenance:</b> {PROVENANCE_LABELS['ai_generated']}" in block
        # Standard-only fields have no place on a scenario question.
        assert "Source Course" not in block and "Bloom's Level" not in block


@pytest.mark.parametrize("position, key", list(enumerate(ANSWER_KEYS)))
def test_pdf_shows_each_types_answer_key(position, key):
    assert key in question_blocks(make_scenario_assessment())[position]


def test_pdf_lists_the_expected_components_of_an_action_planning_question():
    plan = question_blocks(make_scenario_assessment())[4]
    assert "<li>Verify</li><li>Assign</li><li>Follow up</li>" in plan


def test_pdf_rubric_table_shows_criteria_weights_anchors_and_evidence():
    blocks = question_blocks(make_scenario_assessment())
    open_text = blocks[2]

    header = "".join(f"<th>{level}</th>" for level in RUBRIC_ANCHOR_LEVELS)
    assert f"<tr><th>Criterion</th><th>Weight</th>{header}<th>Evidence</th></tr>" in open_text
    assert "<b>Criterion 1</b><br/>What criterion 1 looks for." in open_text
    assert [w in open_text for w in ("<td>40%</td>", "<td>30%</td>")] == [True, True]
    assert all(f"<td>Level {level} for criterion 1</td>" in open_text
               for level in RUBRIC_ANCHOR_LEVELS)
    assert "• Evidence for criterion 1" in open_text
    # Only the written types carry a rubric.
    assert [("rubric-table" in b) for b in blocks] == [False, False, True, False, True, True]


def test_pdf_escapes_model_text():
    data = make_scenario_assessment()
    mcq = data["questions"][BUCKET_SCENARIO_MCQ][0]
    mcq["scenario"]["title"] = "<script>alert(1)</script>"
    mcq["question_text"] = "Is R&D spend < 10%?"
    mcq["options"][0]["text"] = "<b>Yes</b>"

    block = question_blocks(data)[0]

    assert "<script>" not in block and "<b>Yes</b>" not in block
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in block
    assert "Is R&amp;D spend &lt; 10%?" in block
    assert "A. &lt;b&gt;Yes&lt;/b&gt;" in block


def test_pdf_leaves_out_the_constraints_list_when_there_are_none():
    data = make_scenario_assessment()
    data["questions"][BUCKET_SCENARIO_MCQ][0]["scenario"]["constraints"] = []
    blocks = question_blocks(data)
    assert "Constraints:" not in blocks[0]
    assert "Constraints:" in blocks[1]


def test_pdf_tolerates_malformed_model_output():
    data = make_scenario_assessment()
    data["questions"][BUCKET_RANKING][0]["correct_ranking"] = [9, "x"]
    open_q = data["questions"][BUCKET_OPEN_TEXT][0]
    open_q["rubric"] = [{"criterion": "Only a name"}, "not a criterion"]
    del open_q["scenario"]

    blocks = question_blocks(data)

    assert "Correct Ranking (most to least effective): ? &gt; ?" in blocks[3]
    assert "<b>Only a name</b>" in blocks[2]
    assert "<b>Scenario: </b>" in blocks[2]


def test_docx_renders_every_scenario_question_with_its_answer_key(tmp_path):
    text = docx_text(docx_of(make_scenario_assessment(), tmp_path))

    for number, qid in enumerate(["smcq_1", "sms_1", "open_1", "rank_1", "plan_1", "sa_1"], start=1):
        assert f"{number}. Question {qid}?" in text
    assert text.count("Scenario: Disagreement Over Training Data") == 6
    assert text.count("Scenario Domain: Team conflict") == 6
    for key in ANSWER_KEYS:
        assert key.replace("&gt;", ">") in text
    assert "Verify\nAssign\nFollow up" in text
    assert "Source Course" not in text


def test_docx_has_a_rubric_table_for_each_written_question(tmp_path):
    doc = docx_of(make_scenario_assessment(), tmp_path)

    # Open text (40/30/30), action planning (30/40/30), short answer (60/40).
    assert len(doc.tables) == 3
    table = doc.tables[0]
    assert [c.text for c in table.rows[0].cells] == ["Criterion", "Weight", *RUBRIC_ANCHOR_LEVELS, "Evidence"]
    assert [c.text for c in table.rows[1].cells] == [
        "Criterion 1\nWhat criterion 1 looks for.", "40%",
        *[f"Level {level} for criterion 1" for level in RUBRIC_ANCHOR_LEVELS],
        "• Evidence for criterion 1",
    ]
    assert [[r.cells[1].text for r in t.rows[1:]] for t in doc.tables] == [
        ["40%", "30%", "30%"], ["30%", "40%", "30%"], ["60%", "40%"],
    ]


def test_docx_tolerates_malformed_model_output(tmp_path):
    data = make_scenario_assessment()
    data["questions"][BUCKET_RANKING][0]["correct_ranking"] = [9, "x"]
    open_q = data["questions"][BUCKET_OPEN_TEXT][0]
    open_q["rubric"] = [{"criterion": "Only a name"}, "not a criterion"]
    del open_q["scenario"]

    doc = docx_of(data, tmp_path)

    assert "Correct Ranking (most to least effective): ? > ?" in docx_text(doc)
    assert doc.tables[0].rows[1].cells[0].text == "Only a name"

