"""GET /download/{job_id}."""

import csv
import io
import json

import pytest

import assessment.api as api
from factories import OTHER_USER_ID, make_assessment

BASE = "/ai-assessments/v1"
JOB = "do_1_abcd1234_user-1"


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def test_download_unsupported_format_is_400(client, fake_db):
    fake_db.add_row(JOB)
    response = client.get(f"{BASE}/download/{JOB}?format=xml")
    assert response.status_code == 400
    assert "Supported formats" in response.json()["detail"]


def test_download_requires_format(client, fake_db):
    fake_db.add_row(JOB)
    assert client.get(f"{BASE}/download/{JOB}").status_code == 422


@pytest.mark.parametrize("status", [None, "PENDING", "FAILED"])
def test_download_of_missing_or_unfinished_job_is_404(client, fake_db, status):
    if status:
        fake_db.add_row(JOB, status=status)
    assert client.get(f"{BASE}/download/{JOB}?format=json").status_code == 404


def test_download_of_another_users_job_is_403(client, fake_db):
    fake_db.add_row(JOB, user_id=OTHER_USER_ID)
    assert client.get(f"{BASE}/download/{JOB}?format=json").status_code == 403


def test_download_json_is_the_normalised_final_state(client, fake_db):
    data = make_assessment()
    data["question_order"] = ["tf_1", "mcq_1", "mcq_2", "ftb_1", "mtf_1", "mc_1"]
    fake_db.add_row(JOB, data=data)
    response = client.get(f"{BASE}/download/{JOB}?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert f'filename="{JOB}_assessment.json"' in response.headers["content-disposition"]
    assert json.loads(response.content)["question_order"][0] == "tf_1"


def test_download_csv_follows_question_order(client, fake_db):
    data = make_assessment()
    data["question_order"] = ["tf_1", "mcq_1", "mcq_2", "ftb_1", "mtf_1", "mc_1"]
    fake_db.add_row(JOB, data=data)
    response = client.get(f"{BASE}/download/{JOB}?format=csv")
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
    assert [r["QuestionType"] for r in rows] == ["T/F", "MCQ-SCA", "MCQ-SCA", "FTB", "MTF", "MCQ-MCA"]
    mcq2 = rows[2]
    assert [mcq2[f"isOption{i}Correct"] for i in (1, 2, 3, 4)] == ["No", "No", "Yes", "No"]


def test_download_csv_basic_contains_only_mcqs(client, fake_db):
    fake_db.add_row(JOB)
    response = client.get(f"{BASE}/download/{JOB}?format=csv_basic")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
    assert len(rows) == 3  # two MCQs + one Multi-Choice
    assert rows[0]["IsOption1Correct"] == "TRUE"


@pytest.mark.parametrize("fmt,exporter,media", [
    ("pdf", "generate_pdf", "application/pdf"),
    ("docx", "generate_docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
])
def test_download_pdf_and_docx(client, fake_db, monkeypatch, fmt, exporter, media):
    fake_db.add_row(JOB)
    seen = {}

    def fake_export(data, path):
        seen["order"] = data["question_order"]
        path.write_bytes(b"binary-" + fmt.encode())

    monkeypatch.setattr(api, exporter, fake_export)
    response = client.get(f"{BASE}/download/{JOB}?format={fmt}")
    assert response.status_code == 200
    assert response.headers["content-type"] == media
    assert response.content == b"binary-" + fmt.encode()
    assert seen["order"] == make_assessment()["question_order"]


def test_real_docx_export_renders(client, fake_db):
    fake_db.add_row(JOB)
    response = client.get(f"{BASE}/download/{JOB}?format=docx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"  # a docx is a zip archive
