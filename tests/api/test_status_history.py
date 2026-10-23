"""GET /status/{job_id} and GET /history."""

import json
from datetime import datetime

from factories import OTHER_USER_ID, make_assessment

BASE = "/ai-assessments/v1"


def test_status_unknown_job_is_404(client):
    response = client.get(f"{BASE}/status/missing")
    assert response.status_code == 404
    assert response.json() == {"status": "NOT_FOUND"}


def test_status_of_another_users_job_is_403(client, fake_db):
    fake_db.add_row("job", user_id=OTHER_USER_ID)
    assert client.get(f"{BASE}/status/job").status_code == 403


def test_status_of_legacy_row_without_owner_is_readable(client, fake_db):
    fake_db.add_row("job", user_id=None)
    assert client.get(f"{BASE}/status/job").status_code == 200


def test_status_lowercases_blooms_keys_and_normalises_payload(client, fake_db):
    data = make_assessment()
    for questions in data["questions"].values():
        for q in questions:
            q.pop("question_id")
    data.pop("question_order")
    fake_db.add_row("job", data=data, metadata={"config": {"blooms_config": {"Remember": 40, "Apply": 60}}})

    body = client.get(f"{BASE}/status/job").json()
    assert body["metadata"]["config"]["blooms_config"] == {"remember": 40, "apply": 60}
    order = body["assessment_data"]["question_order"]
    assert len(order) == 6 and all(order)
    assert all(q["question_id"] for b in body["assessment_data"]["questions"].values() for q in b)


def test_status_parses_metadata_stored_as_json_string(client, fake_db):
    fake_db.add_row("job", metadata=json.dumps({"config": {"blooms_config": {"Apply": 1}}}))
    assert client.get(f"{BASE}/status/job").json()["metadata"]["config"]["blooms_config"] == {"apply": 1}


def test_status_tolerates_unparseable_metadata_string(client, fake_db):
    fake_db.add_row("job", metadata="{broken")
    assert client.get(f"{BASE}/status/job").json()["metadata"] == {}


def test_status_of_pending_job_has_no_assessment_data(client, fake_db):
    fake_db.add_row("job", status="PENDING")
    fake_db.rows["job"]["assessment_data"] = None
    body = client.get(f"{BASE}/status/job").json()
    assert body["status"] == "PENDING" and body["assessment_data"] is None


def test_history_is_formatted(client, fake_db):
    created = datetime(2026, 1, 2, 3, 4, 5)
    fake_db.history = [
        {"job_id": "j1", "status": "COMPLETED", "created_at": created, "updated_at": created,
         "metadata": {"course_ids": ["do_1"], "course_names": ["C1"], "config": {"x": 1}},
         "version": 3, "edited_at": created, "error_message": None},
        {"job_id": "j2", "status": "FAILED", "created_at": None, "updated_at": None,
         "metadata": "{not json", "version": None, "edited_at": None, "error_message": "boom"},
    ]
    first, second = client.get(f"{BASE}/history").json()

    assert first == {
        "job_id": "j1", "status": "COMPLETED",
        "created_at": "2026-01-02T03:04:05", "updated_at": "2026-01-02T03:04:05",
        "course_ids": ["do_1"], "course_names": ["C1"], "config": {"x": 1},
        "error_message": None, "version": 3, "edited": True, "edited_at": "2026-01-02T03:04:05",
    }
    assert second["course_ids"] == [] and second["config"] == {}
    assert second["version"] == 1 and second["edited"] is False
    assert second["error_message"] == "boom"


def test_history_empty(client):
    assert client.get(f"{BASE}/history").json() == []
