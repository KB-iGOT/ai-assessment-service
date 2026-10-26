"""
The editing workspace: POST /questions/{update,create,delete,order}/{job_id}.
"""

import pytest

from assessment.questions import BUCKET_MCQ
from factories import OTHER_USER_ID, USER_ID, make_assessment, make_options, mcq

BASE = "/ai-assessments/v1"
JOB = "do_1_abcd1234_user-1"

UPDATE = f"{BASE}/questions/update/{JOB}"
CREATE = f"{BASE}/questions/create/{JOB}"
DELETE = f"{BASE}/questions/delete/{JOB}"
ORDER = f"{BASE}/questions/order/{JOB}"

ORIGINAL_ORDER = ["mcq_1", "mcq_2", "ftb_1", "mtf_1", "mc_1", "tf_1"]


@pytest.fixture
def row(fake_db):
    return fake_db.add_row(JOB, ai_original=make_assessment())


def codes(response):
    return [e["code"] for e in response.json()["errors"]]


def new_mcq_payload(**overrides):
    question = {k: v for k, v in mcq("ignored").items() if k not in ("question_id", "provenance")}
    question["question_text"] = "Brand new?"
    question.update(overrides)
    return question


# --------------------------------------------------------------------------
# Envelope and identifiers (all POST endpoints)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [UPDATE, CREATE, DELETE, ORDER])
@pytest.mark.parametrize("body", [{}, {"request": None}])
def test_missing_envelope_is_400_not_422(client, row, url, body):
    response = client.post(url, json=body)
    assert response.status_code == 400
    assert response.json()["detail"] == "request_required"


@pytest.mark.parametrize("url", [UPDATE, DELETE])
@pytest.mark.parametrize("question_id,code", [
    (None, "question_id_required"),
    ("   ", "question_id_invalid"),
    (123, "question_id_invalid"),
])
def test_question_id_is_validated(client, row, url, question_id, code):
    body = {"request": {"questionId": question_id, "updates": {}}}
    if question_id is None:
        del body["request"]["questionId"]
    response = client.post(url, json=body)
    assert response.status_code == 400
    assert codes(response) == [code]


def test_snake_case_field_names_are_accepted(client, row):
    response = client.post(UPDATE, json={"request": {"question_id": "mcq_1",
                                                     "updates": {"question_text": "Snake?"}}})
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Loading the assessment: existence, ownership, status
# --------------------------------------------------------------------------

def test_unknown_assessment_is_404(client):
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": {}}})
    assert response.status_code == 404
    assert codes(response) == ["assessment_not_found"]


def test_another_users_assessment_is_403(client, fake_db):
    fake_db.add_row(JOB, user_id=OTHER_USER_ID)
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": {}}})
    assert response.status_code == 403
    assert codes(response) == ["assessment_access_denied"]


@pytest.mark.parametrize("status", ["PENDING", "IN_PROGRESS", "FAILED"])
def test_unfinished_assessment_is_not_editable(client, fake_db, status):
    fake_db.add_row(JOB, status=status)
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": {}}})
    assert response.status_code == 409
    assert response.json()["errors"][0]["params"] == {"status": status}


def test_legacy_row_is_owned_by_the_user_its_id_ends_with(client, fake_db):
    fake_db.add_row(JOB, user_id=None)  # JOB ends with _user-1
    ok = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": {"question_text": "x?"}}})
    assert ok.status_code == 200

    fake_db.add_row("do_1_abcd1234_user-2", user_id=None)
    denied = client.post(f"{BASE}/questions/update/do_1_abcd1234_user-2",
                         json={"request": {"questionId": "mcq_1", "updates": {}}})
    assert denied.status_code == 403


def test_legacy_row_suffix_match_is_not_fooled_by_a_longer_user_id(client, fake_db, current_user):
    fake_db.add_row("do_1_abcd1234_xuser-1", user_id=None)
    response = client.post(f"{BASE}/questions/update/do_1_abcd1234_xuser-1",
                           json={"request": {"questionId": "mcq_1", "updates": {}}})
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Edit
# --------------------------------------------------------------------------

def test_edit_saves_and_bumps_version(client, fake_db, row):
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1",
                                                     "updates": {"question_text": "Reworded?"}}})
    body = response.json()
    assert response.status_code == 200
    assert body["code"] == "saved" and body["version"] == 2
    assert body["question"]["question_text"] == "Reworded?"
    assert body["question"]["provenance"] == "ai_assisted"
    assert body["question_order"] == ORIGINAL_ORDER and body["total_questions"] == 6

    save = fake_db.saves[0]
    assert save["expected_version"] == 1 and save["user_id"] == USER_ID
    [audit] = save["audit_rows"]
    assert audit["event_code"] == "question_edit"
    assert audit["changed_fields"] == [{"field": "question_text",
                                        "previous_value": "Question mcq_1?",
                                        "new_value": "Reworded?"}]
    assert audit["original_question"]["question_text"] == "Question mcq_1?"


def test_edit_with_no_real_change_writes_nothing(client, fake_db, row):
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1",
                                                     "updates": {"question_text": "Question mcq_1?"}}})
    assert response.json()["code"] == "no_changes"
    assert response.json()["version"] == 1
    assert fake_db.saves == []


def test_edit_echoing_server_owned_fields_is_ignored(client, fake_db, row):
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": {
        "provenance": "human_authored", "question_id": "hijack", "question_text": "New?"}}})
    assert response.status_code == 200
    assert response.json()["question"]["provenance"] == "ai_assisted"
    assert response.json()["question"]["question_id"] == "mcq_1"


def test_edit_unknown_question_is_404(client, row):
    response = client.post(UPDATE, json={"request": {"questionId": "nope", "updates": {"question_text": "x"}}})
    assert response.status_code == 404
    assert codes(response) == ["question_not_found"]


def test_edit_non_editable_field_is_400(client, row):
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1",
                                                     "updates": {"question_type": "TRUEFALSE", "pairs": []}}})
    assert response.status_code == 400
    assert codes(response) == ["field_not_editable"]


@pytest.mark.parametrize("updates,code", [
    ({"correct_option_index": 9}, "correct_option_index_out_of_range"),
    ({"question_text": "  "}, "question_text_required"),
    ({"options": make_options(["only"])}, "option_count_invalid"),
    ({"blooms_level": "Memorise"}, "blooms_level_invalid"),
    ({"relevance_percentage": 140}, "relevance_invalid"),
    ({"reasoning.competency_alignment.kcm.competency_area": "Behavioural"}, "competency_mapping_incomplete"),
])
def test_edit_that_would_make_the_question_invalid_is_blocked(client, fake_db, row, updates, code):
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": updates}})
    assert response.status_code == 400
    assert code in codes(response)
    assert fake_db.saves == []


def test_blooms_is_optional_when_disabled_for_the_assessment(client, fake_db):
    fake_db.add_row(JOB, metadata={"config": {"enable_blooms": False}})
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1",
                                                     "updates": {"blooms_level": None}}})
    assert response.status_code == 200


def test_stale_body_version_is_409(client, fake_db):
    fake_db.add_row(JOB, version=3)
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "version": 2,
                                                     "updates": {"question_text": "x?"}}})
    assert response.status_code == 409
    assert response.json()["current_version"] == 3
    assert response.json()["detail"] == "version_conflict"


def test_if_match_header_is_honoured(client, fake_db, row):
    stale = client.post(UPDATE, headers={"If-Match": '"7"'},
                        json={"request": {"questionId": "mcq_1", "updates": {"question_text": "x?"}}})
    assert stale.status_code == 409

    fresh = client.post(UPDATE, headers={"If-Match": '"1"'},
                        json={"request": {"questionId": "mcq_1", "updates": {"question_text": "x?"}}})
    assert fresh.status_code == 200


def test_non_numeric_if_match_is_400(client, row):
    response = client.post(UPDATE, headers={"If-Match": "abc"},
                           json={"request": {"questionId": "mcq_1", "updates": {"question_text": "x?"}}})
    assert response.status_code == 400
    assert codes(response) == ["if_match_invalid"]


def test_concurrent_write_detected_by_the_database_is_409(client, fake_db, row):
    fake_db.force_conflict = True
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1",
                                                     "updates": {"question_text": "x?"}}})
    assert response.status_code == 409
    assert response.json()["current_version"] == 1


def test_double_submit_of_the_same_version_saves_once(client, fake_db, row):
    body = {"request": {"questionId": "mcq_1", "version": 1, "updates": {"question_text": "x?"}}}
    assert client.post(UPDATE, json=body).status_code == 200
    assert client.post(UPDATE, json=body).status_code == 409
    assert len(fake_db.saves) == 1


def test_reordering_options_is_audited_as_reindex_not_answer_change(client, fake_db, row):
    # mcq_1: options A,B,C,D, correct 0 ("A"). Move "A" to the end.
    reordered = [{"index": i, "text": t} for i, t in enumerate(["B", "C", "D", "A"])]
    response = client.post(UPDATE, json={"request": {"questionId": "mcq_1", "updates": {
        "options": reordered, "correct_option_index": 3}}})
    details = fake_db.saves[0]["audit_rows"][0]["details"]
    assert response.status_code == 200
    assert details["options_reordered"] is True and details["reindexed_only"] is True


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------

def test_create_appends_a_human_authored_question(client, fake_db, row):
    response = client.post(CREATE, json={"request": {"questionType": "mcq", "question": new_mcq_payload()}})
    body = response.json()
    assert response.status_code == 201
    assert body["question"]["provenance"] == "human_authored"
    assert body["question_id"].startswith("q_")
    assert body["question_order"][-1] == body["question_id"]
    assert body["total_questions"] == 7
    # Additions are not written to the audit table.
    assert fake_db.saves[0]["audit_rows"] == []


def test_create_at_position_and_clamps_out_of_range(client, row, fake_db):
    first = client.post(CREATE, json={"request": {"questionType": "mcq", "question": new_mcq_payload(),
                                                  "position": 1}}).json()
    assert first["question_order"][0] == first["question_id"]

    far = client.post(CREATE, json={"request": {"questionType": "truefalse", "position": 999, "question": {
        "question_text": "Sky is blue.", "correct_answer": "true", "blooms_level": "Remember",
        "relevance_percentage": 50, "answer_rationale": {"correct_answer_explanation": "Yes."}}}}).json()
    assert far["question_order"][-1] == far["question_id"]
    assert far["question"]["correct_answer"] == "True"


def test_create_ignores_client_supplied_id_and_provenance(client, row):
    body = client.post(CREATE, json={"request": {"questionType": "mcq", "question": new_mcq_payload(
        question_id="mine", provenance="ai_generated")}}).json()
    assert body["question_id"] != "mine"
    assert body["question"]["provenance"] == "human_authored"


@pytest.mark.parametrize("question_type,question,code", [
    ("essay", {}, "question_type_invalid"),
    ("mcq", {"foo": "bar"}, "field_not_editable"),
    ("mcq", {"question_text": "Incomplete"}, "options_required"),
])
def test_create_rejects_invalid_input(client, fake_db, row, question_type, question, code):
    response = client.post(CREATE, json={"request": {"questionType": question_type, "question": question}})
    assert response.status_code == 400
    assert code in codes(response)
    assert fake_db.saves == []


def test_create_missing_required_body_fields_is_422(client, row):
    assert client.post(CREATE, json={"request": {"question": {}}}).status_code == 422


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------

def test_delete_removes_question_and_writes_audit(client, fake_db, row):
    response = client.post(DELETE, json={"request": {"questionId": "ftb_1"}})
    body = response.json()
    assert response.status_code == 200
    assert body["deleted_question_id"] == "ftb_1"
    assert "ftb_1" not in body["question_order"] and body["total_questions"] == 5
    [audit] = fake_db.saves[0]["audit_rows"]
    assert audit["event_code"] == "question_delete" and audit["previous_position"] == 3


def test_delete_unknown_question_is_404(client, row):
    assert client.post(DELETE, json={"request": {"questionId": "nope"}}).status_code == 404


def test_last_question_cannot_be_deleted(client, fake_db):
    fake_db.add_row(JOB, data={"questions": {BUCKET_MCQ: [mcq("only")]}, "question_order": ["only"]})
    response = client.post(DELETE, json={"request": {"questionId": "only"}})
    assert response.status_code == 400
    assert codes(response) == ["last_question_cannot_be_deleted"]


# --------------------------------------------------------------------------
# Reorder
# --------------------------------------------------------------------------

def test_reorder_saves_new_sequence_without_audit_rows(client, fake_db, row):
    new_order = list(reversed(ORIGINAL_ORDER))
    response = client.post(ORDER, json={"request": {"questionOrder": new_order}})
    assert response.status_code == 200
    assert response.json()["question_order"] == new_order and response.json()["version"] == 2
    assert fake_db.saves[0]["audit_rows"] == []


def test_reorder_to_same_order_is_a_no_op(client, fake_db, row):
    response = client.post(ORDER, json={"request": {"questionOrder": ORIGINAL_ORDER}})
    assert response.json()["code"] == "order_unchanged"
    assert fake_db.saves == []


@pytest.mark.parametrize("order", [
    ORIGINAL_ORDER[:-1],                     # dropped one (stale client)
    ORIGINAL_ORDER + ["ghost"],              # unknown id
    ORIGINAL_ORDER[:-1] + ["mcq_1"],         # duplicate replacing one
])
def test_reorder_must_be_an_exact_permutation(client, fake_db, row, order):
    response = client.post(ORDER, json={"request": {"questionOrder": order}})
    assert response.status_code == 400
    assert codes(response) == ["question_order_invalid"]
    assert fake_db.saves == []


def test_reorder_requires_question_order(client, row):
    response = client.post(ORDER, json={"request": {}})
    assert response.status_code == 400
    assert codes(response) == ["question_order_required"]
