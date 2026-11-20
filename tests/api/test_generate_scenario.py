"""POST /ai-assessments/v1/generate with assessment_type=scenario."""

import json

import pytest

from factories import USER_ID

URL = "/ai-assessments/v1/generate"


def form(**overrides):
    data = {
        "assessment_type": "scenario",
        "difficulty": "intermediate",
        "question_type_counts": json.dumps({"scenario_mcq": 2, "open_text": 1}),
        "assessment_topic": "Leadership and conflict management",
        "scenario_domains": "Team conflict, Ethical decisions",
        "target_audience": "New district officers",
        "enable_blooms": "true",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def job_id_for(client, data):
    return client.post(URL, data=data).json()["job_id"]


@pytest.mark.parametrize("missing", ["assessment_topic", "scenario_domains", "target_audience"])
def test_topic_domains_and_audience_are_required(client, missing):
    response = client.post(URL, data=form(**{missing: None}))
    assert response.status_code == 400
    assert missing in response.json()["detail"]


def test_swagger_placeholders_count_as_missing(client):
    response = client.post(URL, data=form(assessment_topic="string", scenario_domains="string"))
    assert response.status_code == 400
    assert "assessment_topic" in response.json()["detail"]
    assert "scenario_domains" in response.json()["detail"]


def test_standard_question_types_are_rejected(client):
    response = client.post(URL, data=form(question_type_counts=json.dumps({"mcq": 2})))
    assert response.status_code == 400
    assert "mcq" in response.json()["detail"]


def test_scenario_question_types_are_rejected_for_other_assessment_types(client):
    response = client.post(URL, data=form(assessment_type="final", course_ids="do_1"))
    assert response.status_code == 400
    assert "scenario_mcq" in response.json()["detail"]


@pytest.mark.parametrize("counts", [{"scenario_mcq": -1}, {"scenario_mcq": 0}, {"open_text": 1.5}])
def test_counts_must_be_whole_and_request_at_least_one_question(client, counts):
    assert client.post(URL, data=form(question_type_counts=json.dumps(counts))).status_code == 400


def test_new_scenario_job_is_queued_without_courses_or_files(client, fake_db, sent_events):
    response = client.post(URL, data=form(topic_names="Mediation, Escalation", time_limit="40"))
    body = response.json()
    assert response.status_code == 200 and body["status"] == "PENDING"
    assert body["job_id"].startswith("scenario_") and body["job_id"].endswith(f"_{USER_ID}")

    payload = sent_events[0]
    assert payload["assessment_type"] == "scenario"
    assert payload["assessment_topic"] == "Leadership and conflict management"
    assert payload["scenario_domains"] == ["Team conflict", "Ethical decisions"]
    assert payload["target_audience"] == "New district officers"
    assert payload["topic_names"] == ["Mediation", "Escalation"]
    assert payload["question_type_counts"] == {"scenario_mcq": 2, "open_text": 1}
    assert payload["total_questions"] == 3
    # Bloom's never applies to a scenario assessment, whatever was sent.
    assert payload["enable_blooms"] is False and payload["blooms_distribution"] is None

    config = fake_db.created_jobs[0]["metadata"]["config"]
    assert config == payload["config"]
    assert config["scenario_domains"] == ["Team conflict", "Ethical decisions"]
    assert config["enable_blooms"] is False


def test_domains_accept_repeated_fields_and_a_json_array(client, sent_events):
    client.post(URL, data={**form(), "scenario_domains": ["Team conflict", "Ethical decisions"]})
    client.post(URL, data=form(scenario_domains=json.dumps(["Team conflict", "Ethical decisions"])))
    assert sent_events[0]["scenario_domains"] == sent_events[1]["scenario_domains"] == [
        "Team conflict", "Ethical decisions",
    ]


def test_uploaded_files_are_passed_to_the_worker(client, sent_events):
    files = [("files", ("policy.pdf", b"%PDF-1.4", "application/pdf"))]
    response = client.post(URL, data=form(), files=files)
    assert response.status_code == 200
    assert [p.rsplit("/", 1)[-1] for p in sent_events[0]["extra_files"]] == ["policy.pdf"]


@pytest.mark.parametrize("change", [
    {"assessment_topic": "Public grievance handling"},
    {"scenario_domains": "Team conflict"},
    {"target_audience": "University learners"},
    {"difficulty": "advanced"},
])
def test_topic_domains_and_audience_are_part_of_the_cache_key(client, change):
    assert job_id_for(client, form()) != job_id_for(client, form(**change))


# --------------------------------------------------------------------------
# Difficulty
# --------------------------------------------------------------------------

def test_several_levels_reach_the_worker_without_a_single_difficulty(client, fake_db, sent_events):
    response = client.post(URL, data=form(difficulty=None, difficulty_levels="advanced, beginner"))
    assert response.status_code == 200

    payload = sent_events[0]
    assert payload["difficulty"] is None
    assert payload["difficulty_levels"] == ["beginner", "advanced"]
    assert payload["difficulty_distribution"] is None
    config = fake_db.created_jobs[0]["metadata"]["config"]
    assert config["difficulty"] is None and config["difficulty_levels"] == ["beginner", "advanced"]


def test_a_distribution_is_validated_and_passed_on(client, sent_events):
    dist = {"beginner": 1, "advanced": 2, "open_text": {"advanced": 1}}
    response = client.post(URL, data=form(difficulty=None, difficulty_distribution=json.dumps(dist)))
    assert response.status_code == 200
    assert sent_events[0]["difficulty_distribution"] == dist


@pytest.mark.parametrize("overrides, message", [
    ({"difficulty": None}, "difficulty, difficulty_levels or difficulty_distribution is required"),
    ({"difficulty_levels": "beginner, expert"}, "Unknown difficulty level"),
    ({"difficulty_distribution": "{not json"}, "Invalid JSON for difficulty_distribution"),
    ({"difficulty_distribution": json.dumps({"beginner": 1, "advanced": 1})}, "add up to 2, but 3"),
    ({"difficulty_distribution": json.dumps({"open_text": {"advanced": 2}})}, "adds up to 2, but 1"),
])
def test_difficulty_requests_that_cannot_be_met_are_rejected(client, overrides, message):
    response = client.post(URL, data=form(**overrides))
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_one_level_however_it_is_sent_is_the_plain_single_difficulty_request(client, sent_events):
    a = job_id_for(client, form(difficulty="advanced"))
    b = job_id_for(client, form(difficulty=None, difficulty_levels="advanced"))
    c = job_id_for(client, form(difficulty=None, difficulty_distribution=json.dumps({"advanced": 3})))
    assert a == b == c
    assert all(e["difficulty"] == "advanced" and "difficulty_levels" not in e for e in sent_events)


def test_the_difficulty_split_is_part_of_the_cache_key(client):
    even = job_id_for(client, form(difficulty=None, difficulty_levels="beginner, advanced"))
    same = job_id_for(client, form(difficulty=None,
                                   difficulty_distribution=json.dumps({"beginner": 2, "advanced": 1})))
    other = job_id_for(client, form(difficulty=None,
                                    difficulty_distribution=json.dumps({"beginner": 1, "advanced": 2})))
    assert even == same  # the even split of 3 over two levels is 2/1
    assert even != other
    assert even != job_id_for(client, form())


def test_other_assessment_types_still_need_a_difficulty(client):
    response = client.post(URL, data=form(assessment_type="final", course_ids="do_1", difficulty=None,
                                          question_type_counts=json.dumps({"mcq": 2})))
    assert response.status_code == 400
    assert "difficulty is required" in response.json()["detail"]


def test_domain_order_and_blooms_settings_do_not_change_the_cache_key(client):
    a = job_id_for(client, form(scenario_domains="Team conflict, Ethical decisions"))
    b = job_id_for(client, form(scenario_domains="Ethical decisions, Team conflict",
                                enable_blooms="false", blooms_config=json.dumps({"apply": 100})))
    assert a == b
