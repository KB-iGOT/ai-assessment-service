"""
`worker_service.process_job` — one Kafka job from payload to saved result.

The database, storage, course fetcher, Kafka producer and the generator are
all faked. What is real is the worker's own logic: mapping the payload onto
`generate_assessment`, retrieving uploads, the ingest-time repairs of the LLM
output (`normalize_assessment` with the option-index rebase, then answer
balancing), the save, the cleanup and the completion event.
"""

import pytest

from assessment import worker_service as ws
from assessment.questions import BUCKET_MCQ
from factories import make_options, mcq

JOB = "do_1_abcd1234_user-1"


class FakeStorage:
    def __init__(self):
        self.read = []
        self.deleted = []

    def read_file(self, stored_path, local_dest):
        self.read.append((stored_path, local_dest))

    def delete_file(self, stored_path):
        self.deleted.append(stored_path)


@pytest.fixture
def worker(monkeypatch, tmp_path):
    """Patch every collaborator of `process_job` and record what it was asked to do."""
    calls = {"status": [], "fetched": [], "generate": None, "saved": None, "events": []}
    storage = FakeStorage()
    llm_output = {"value": None, "error": None}

    async def update_job_status(job_id, status, error=None):
        calls["status"].append((job_id, status, error))

    async def fetch_course_data(course_id, base_path):
        calls["fetched"].append(course_id)
        return course_id != "do_unreachable"

    async def generate_assessment(**kwargs):
        calls["generate"] = kwargs
        if llm_output["error"]:
            raise llm_output["error"]
        return {"courses": []}, llm_output["value"], {"total_token_count": 42}

    async def save_assessment_result(job_id, metadata, assessment, usage):
        calls["saved"] = {"job_id": job_id, "metadata": metadata,
                          "assessment": assessment, "usage": usage}

    async def send_completion_event(job_id, user_id, status, summary=None):
        calls["events"].append((job_id, user_id, status, summary))

    monkeypatch.setattr(ws, "update_job_status", update_job_status)
    monkeypatch.setattr(ws, "fetch_course_data", fetch_course_data)
    monkeypatch.setattr(ws, "generate_assessment", generate_assessment)
    monkeypatch.setattr(ws, "save_assessment_result", save_assessment_result)
    monkeypatch.setattr(ws, "send_completion_event", send_completion_event)
    monkeypatch.setattr(ws, "get_storage_service", lambda: storage)
    monkeypatch.setattr(ws, "INTERACTIVE_COURSES_PATH", str(tmp_path))

    llm_output["value"] = one_based_llm_output()
    return {"calls": calls, "storage": storage, "llm": llm_output, "root": tmp_path}


def one_based_llm_output():
    """What a model sometimes returns: options numbered 1..n, no ids or order."""
    questions = []
    for i in range(4):
        q = mcq(f"q_{i}", texts=(f"right-{i}", "w1", "w2", "w3"))
        q["options"] = make_options([f"right-{i}", "w1", "w2", "w3"], start=1)
        q["correct_option_index"] = 1
        del q["question_id"], q["provenance"]
        questions.append(q)
    return {"blueprint": {"title": "bp"}, "questions": {BUCKET_MCQ: questions}}


def payload(**overrides):
    data = {
        "job_id": JOB,
        "user_id": "user-1",
        "course_ids": ["do_1"],
        "course_names": ["Course One"],
        "assessment_type": "final",
        "difficulty": "intermediate",
        "total_questions": 4,
        "question_type_counts": {"mcq": 4},
        "language": "english",
        "topic_names": ["t1"],
        "blooms_distribution": {"Remember": 100},
        "enable_blooms": True,
        "course_weightage": None,
        "time_limit": 30,
        "additional_instructions": "be precise",
        "competency_area": None,
        "competency_themes": [],
        "competency_sub_themes": [],
        "extra_files": ["uploads/job/guide.pdf"],
        "transcript_files": ["uploads/job/talk.vtt"],
        "config": {"assessment_type": "final", "enable_blooms": True},
    }
    data.update(overrides)
    return data


async def test_completed_job_is_generated_repaired_saved_and_announced(worker):
    calls, storage, root = worker["calls"], worker["storage"], worker["root"]

    await ws.process_job(payload())

    assert calls["status"] == [(JOB, "IN_PROGRESS", None)]
    assert calls["fetched"] == ["do_1"]

    # Payload fields reach the generator under its own parameter names.
    g = calls["generate"]
    assert g["difficulty_level"] == "intermediate"
    assert g["input_language"] == "english"
    assert g["question_type_counts"] == {"mcq": 4}
    assert g["blooms_distribution"] == {"Remember": 100}
    assert g["course_names"] == ["Course One"]
    assert g["extra_files"] == [root / "storage_downloads" / JOB / "guide.pdf"]
    assert g["transcript_files"] == [root / "storage_downloads" / JOB / "talk.vtt"]
    assert [p for p, _ in storage.read] == ["uploads/job/guide.pdf", "uploads/job/talk.vtt"]

    saved = calls["saved"]
    assert saved["job_id"] == JOB and saved["usage"] == {"total_token_count": 42}
    assert saved["metadata"]["course_ids"] == ["do_1"]
    assert saved["metadata"]["course_names"] == ["Course One"]
    assert saved["metadata"]["config"] == {"assessment_type": "final", "enable_blooms": True}

    assert storage.deleted == ["uploads/job/guide.pdf", "uploads/job/talk.vtt"]
    assert calls["events"] == [(JOB, "user-1", "COMPLETED", {"course_ids": ["do_1"]})]


async def test_llm_output_is_rebased_balanced_and_given_ids_before_saving(worker):
    await ws.process_job(payload())
    questions = worker["calls"]["saved"]["assessment"]["questions"][BUCKET_MCQ]
    order = worker["calls"]["saved"]["assessment"]["question_order"]

    assert len(order) == 4 and all(order)
    for i, q in enumerate(questions):
        # 1-based options moved to 0-based, and the same option is still correct.
        assert [o["index"] for o in q["options"]] == [0, 1, 2, 3]
        assert q["options"][q["correct_option_index"]]["text"] == f"right-{i}"
        assert q["provenance"] == "ai_generated"
    # Every answer was the first option; balancing spreads them over 4 positions.
    assert sorted(q["correct_option_index"] for q in questions) == [0, 1, 2, 3]


async def test_config_is_rebuilt_from_payload_when_not_sent(worker):
    await ws.process_job(payload(config=None))
    config = worker["calls"]["saved"]["metadata"]["config"]
    assert config["difficulty"] == "intermediate"
    assert config["blooms_config"] == {"Remember": 100}
    assert config["time_limit"] == 30


async def test_payload_without_uploads_or_transcript_key(worker):
    # Payloads queued before transcript uploads existed have no such key.
    data = payload(extra_files=[])
    del data["transcript_files"]
    await ws.process_job(data)
    assert worker["calls"]["generate"]["extra_files"] == []
    assert worker["calls"]["generate"]["transcript_files"] == []
    assert worker["storage"].read == [] and worker["storage"].deleted == []
    assert worker["calls"]["events"][0][2] == "COMPLETED"


async def test_unreachable_course_does_not_stop_generation(worker):
    await ws.process_job(payload(course_ids=["do_1", "do_unreachable"]))
    assert worker["calls"]["fetched"] == ["do_1", "do_unreachable"]
    assert worker["calls"]["saved"] is not None


async def test_generation_failure_marks_job_failed_and_keeps_uploads(worker):
    worker["llm"]["error"] = RuntimeError("model unavailable")

    await ws.process_job(payload())

    calls = worker["calls"]
    assert calls["status"][-1] == (JOB, "FAILED", "model unavailable")
    assert calls["saved"] is None
    assert worker["storage"].deleted == []
    assert calls["events"] == [(JOB, "user-1", "FAILED", {"error": "model unavailable"})]
