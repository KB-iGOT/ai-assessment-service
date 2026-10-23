"""POST /ai-assessments/v1/generate — validation, cache/clone, queueing and job ids."""

import json

import pytest

from factories import USER_ID, make_assessment

URL = "/ai-assessments/v1/generate"


def form(**overrides):
    data = {
        "assessment_type": "final",
        "difficulty": "intermediate",
        "course_ids": "do_1",
        "question_type_counts": json.dumps({"mcq": 3}),
        "blooms_config": json.dumps({"remember": 50, "apply": 50}),
        # Sent explicitly: from FastAPI 0.13x an omitted bool Form field reads
        # as False regardless of its declared default.
        "enable_blooms": "true",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def competency_form(**overrides):
    base = dict(assessment_type="competency", course_ids=None,
                competency_area="Behavioural", competency_themes="Integrity,Empathy",
                competency_sub_themes="Honesty")
    base.update(overrides)
    return form(**base)


def composite_of(job_id):
    return job_id[: -len(f"_{USER_ID}")]


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

def test_requires_courses_or_files(client):
    response = client.post(URL, data=form(course_ids=None))
    assert response.status_code == 400
    assert "Course ID" in response.json()["detail"]


@pytest.mark.parametrize("missing", ["competency_area", "competency_themes", "competency_sub_themes"])
def test_competency_requires_area_themes_and_sub_themes(client, missing):
    response = client.post(URL, data=competency_form(**{missing: None}))
    assert response.status_code == 400


def test_competency_works_without_courses_or_files(client, sent_events):
    response = client.post(URL, data=competency_form())
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    assert response.json()["job_id"].startswith("custom_upload_")
    assert sent_events[0]["competency_themes"] == ["Integrity", "Empathy"]


def test_unknown_question_type_is_rejected(client):
    response = client.post(URL, data=form(question_type_counts=json.dumps({"essay": 2})))
    assert response.status_code == 400
    assert "essay" in response.json()["detail"]


def test_invalid_blooms_json_is_rejected(client):
    response = client.post(URL, data=form(blooms_config="{not json"))
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON for blooms_config"


def test_invalid_enum_value_is_422(client):
    assert client.post(URL, data=form(difficulty="impossible")).status_code == 422


# --------------------------------------------------------------------------
# New job
# --------------------------------------------------------------------------

def test_new_job_is_created_and_queued(client, fake_db, sent_events):
    response = client.post(URL, data=form(course_names="Course One",
                                          topic_names="t1, t2", time_limit="30"))
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "PENDING"
    assert body["job_id"].startswith("do_1_") and body["job_id"].endswith(f"_{USER_ID}")

    assert fake_db.created_jobs[0]["job_id"] == body["job_id"]
    payload = sent_events[0]
    assert payload["job_id"] == body["job_id"]
    assert payload["user_id"] == USER_ID
    assert payload["course_ids"] == ["do_1"]
    assert payload["course_names"] == ["Course One"]
    assert payload["question_type_counts"] == {"mcq": 3}
    assert payload["topic_names"] == ["t1", "t2"]
    # Bloom's keys are title-cased for internal use.
    assert payload["blooms_distribution"] == {"Remember": 50, "Apply": 50}
    assert payload["assessment_type"] == "final" and payload["difficulty"] == "intermediate"
    assert payload["config"] == fake_db.created_jobs[0]["metadata"]["config"]


def test_comma_separated_courses_are_split_sorted_and_prefixed(client, sent_events):
    body = client.post(URL, data=form(course_ids="do_b, do_a")).json()
    assert body["job_id"].startswith("comprehensive_do_a_do_b_")
    assert sent_events[0]["course_ids"] == ["do_b", "do_a"]


def test_swagger_placeholder_strings_are_treated_as_empty(client, sent_events):
    client.post(URL, data=form(topic_names="string", additional_instructions="string",
                               blooms_config="string"))
    payload = sent_events[0]
    assert payload["topic_names"] is None
    assert payload["additional_instructions"] is None
    assert payload["blooms_distribution"] is None


# --------------------------------------------------------------------------
# Cache and clone
# --------------------------------------------------------------------------

def test_own_completed_job_is_returned_from_cache(client, fake_db, sent_events):
    job_id = client.post(URL, data=form()).json()["job_id"]
    sent_events.clear()
    fake_db.add_row(job_id, status="COMPLETED")

    response = client.post(URL, data=form())
    assert response.json()["message"] == "Assessment retrieved from cache"
    assert response.json()["result"]["question_order"] == make_assessment()["question_order"]
    assert sent_events == []


def test_own_in_progress_job_is_not_requeued(client, fake_db, sent_events):
    job_id = client.post(URL, data=form()).json()["job_id"]
    sent_events.clear()
    fake_db.add_row(job_id, status="IN_PROGRESS")
    assert client.post(URL, data=form()).json() == {
        "message": "Assessment generation in progress", "status": "IN_PROGRESS", "job_id": job_id,
    }
    assert sent_events == []


def test_failed_own_job_is_regenerated(client, fake_db, sent_events):
    job_id = client.post(URL, data=form()).json()["job_id"]
    sent_events.clear()
    fake_db.add_row(job_id, status="FAILED")
    assert client.post(URL, data=form()).json()["status"] == "PENDING"
    assert len(sent_events) == 1


def test_another_users_template_is_cloned_from_the_ai_original(client, fake_db, sent_events):
    job_id = client.post(URL, data=form()).json()["job_id"]
    sent_events.clear()
    composite = composite_of(job_id)

    edited = make_assessment()
    edited["questions"]["Multiple Choice Question"][0]["question_text"] = "Owner's edit"
    original = make_assessment()
    fake_db.templates[f"{composite}_someone-else"] = {
        "course_id": f"{composite}_someone-else", "metadata": {"m": 1},
        "assessment_data": edited, "ai_original_data": original, "token_usage": {"t": 1},
    }

    body = client.post(URL, data=form()).json()
    assert body["message"] == "Assessment cloned from cache"
    assert body["result"] == original
    clone = fake_db.completed_jobs[0]
    assert clone["job_id"] == job_id and clone["user_id"] == USER_ID
    assert clone["data"] == original
    assert sent_events == []


def test_clone_falls_back_to_assessment_data_for_rows_without_ai_original(client, fake_db):
    composite = composite_of(client.post(URL, data=form()).json()["job_id"])
    data = make_assessment()
    fake_db.templates[f"{composite}_x"] = {
        "course_id": f"{composite}_x", "metadata": {}, "assessment_data": data,
        "ai_original_data": None, "token_usage": {},
    }
    assert client.post(URL, data=form()).json()["result"] == data


def test_force_skips_cache_and_clone(client, fake_db, sent_events):
    job_id = client.post(URL, data=form()).json()["job_id"]
    sent_events.clear()
    fake_db.add_row(job_id, status="COMPLETED")
    fake_db.templates[f"{composite_of(job_id)}_x"] = {"course_id": "x"}

    body = client.post(URL, data=form(force="true")).json()
    assert body["status"] == "PENDING"
    assert len(sent_events) == 1
    assert fake_db.completed_jobs == []


# --------------------------------------------------------------------------
# Job id / cache key
# --------------------------------------------------------------------------

def job_id_for(client, data, files=None):
    return client.post(URL, data=data, files=files).json()["job_id"]


def test_same_request_gives_same_job_id(client):
    assert job_id_for(client, form()) == job_id_for(client, form())


@pytest.mark.parametrize("change", [
    {"difficulty": "advanced"},
    {"question_type_counts": json.dumps({"mcq": 4})},
    {"language": "hindi"},
    {"enable_blooms": "false"},
    {"additional_instructions": "focus on X"},
])
def test_any_parameter_change_gives_a_new_job_id(client, change):
    assert job_id_for(client, form()) != job_id_for(client, form(**change))


@pytest.mark.parametrize("change", [
    {"competency_area": "Functional"},
    {"competency_themes": "Integrity"},
    {"competency_sub_themes": "Courage"},
])
def test_competency_area_themes_and_sub_themes_are_part_of_the_cache_key(client, change):
    # a057f1c: two different competency requests must not share a cache entry.
    assert job_id_for(client, competency_form()) != job_id_for(client, competency_form(**change))


def test_competency_theme_order_does_not_change_the_cache_key(client):
    a = job_id_for(client, competency_form(competency_themes="Integrity,Empathy",
                                           competency_sub_themes="Honesty,Courage"))
    b = job_id_for(client, competency_form(competency_themes="Empathy, Integrity",
                                           competency_sub_themes="Courage,Honesty"))
    assert a == b


# --------------------------------------------------------------------------
# Uploads (a4f5a7a)
# --------------------------------------------------------------------------

def test_transcript_only_upload_is_accepted_and_routed(client, sent_events, fake_storage):
    files = [("transcript_files", ("talk.vtt", b"WEBVTT\n\nhello", "text/vtt"))]
    response = client.post(URL, data=form(course_ids=None), files=files)
    assert response.status_code == 200
    assert response.json()["job_id"].startswith("custom_upload_")
    payload = sent_events[0]
    assert payload["transcript_files"] == [f"uploads/{payload['job_id']}/talk.vtt"]
    assert payload["extra_files"] == []
    assert fake_storage.saved[0]["content"] == b"WEBVTT\n\nhello"


def test_documents_and_transcripts_are_stored_separately(client, sent_events):
    files = [
        ("files", ("guide.pdf", b"%PDF-1.4", "application/pdf")),
        ("files", ("notes.docx", b"PK", "application/octet-stream")),
        ("transcript_files", ("talk.txt", b"hi", "text/plain")),
    ]
    client.post(URL, data=form(), files=files)
    payload = sent_events[0]
    assert [p.rsplit("/", 1)[-1] for p in payload["extra_files"]] == ["guide.pdf", "notes.docx"]
    assert [p.rsplit("/", 1)[-1] for p in payload["transcript_files"]] == ["talk.txt"]


def test_same_file_as_transcript_or_document_gives_different_job_ids(client):
    as_doc = job_id_for(client, form(course_ids=None), [("files", ("a.txt", b"x", "text/plain"))])
    as_transcript = job_id_for(client, form(course_ids=None),
                               [("transcript_files", ("a.txt", b"x", "text/plain"))])
    assert as_doc != as_transcript
