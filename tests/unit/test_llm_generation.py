"""
The generation flow around the LLM call, with the model mocked.

Covers what happens when the model's response is unusable (bad JSON, a list
instead of an object), when a batch fails part-way, and the top-up loop that
fills a short response (`_reconcile_counts`).
"""

import json

import pytest

from assessment import generator as gen
from assessment.batching import Batch
from assessment.questions import BUCKET_FTB, BUCKET_MCQ, BUCKET_TRUEFALSE, CANONICAL_BUCKETS


def q(text, blooms=None, course=None):
    out = {"question_text": text, "question_id": "q_001"}
    if blooms:
        out["blooms_level"] = blooms
    if course:
        out["course_name"] = course
    return out


def buckets(**by_bucket):
    out = {b: [] for b in CANONICAL_BUCKETS}
    out.update(by_bucket)
    return out


def texts(questions):
    return [x["question_text"] for x in questions]


SHARED_INPUTS = dict(
    course_context="{}",
    learning_objectives_str="- LO",
    transcript="N/A",
    pdf_snippets="N/A",
    assessment_type="final",
    difficulty_level="Intermediate",
    time_to_complete=None,
    additional_instructions=None,
    input_language="English",
    topic_names="All",
    competency_focus_instruction="Not applicable for this assessment type.",
    case_study_levels="None",
)


class FakeRunBatches:
    """Stands in for `_run_batches`: records each top-up round, returns canned payloads."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []

    async def __call__(self, batches, *, digest, **kwargs):
        self.calls.append({"batches": batches, "digest": list(digest), **kwargs})
        payloads = self.rounds.pop(0) if self.rounds else []
        return payloads, [{"total_token_count": 1}] * len(payloads)


async def reconcile(pool, counts, *, blueprint=None, blooms_by_type=None,
                    course_weightage=None, aggregated_metadata=None):
    return await gen._reconcile_counts(
        pool, blueprint if blueprint is not None else {"title": "bp"},
        question_type_counts=counts,
        requested_total=sum(counts.values()),
        blooms_by_type=blooms_by_type or {},
        blooms_str="",
        course_weightage=course_weightage,
        aggregated_metadata=aggregated_metadata or {"courses": []},
        shared_prompt_inputs=SHARED_INPUTS,
        job_id="job",
    )


# --------------------------------------------------------------------------
# _reconcile_counts — top-up for a short or over-long response
# --------------------------------------------------------------------------

async def test_counts_already_match_returns_none_without_calling_llm(monkeypatch):
    fake = FakeRunBatches([])
    monkeypatch.setattr(gen, "_run_batches", fake)
    pool = buckets(**{BUCKET_MCQ: [q("a"), q("b")]})
    assert await reconcile(pool, {"mcq": 2}) is None
    assert fake.calls == []


async def test_extras_are_trimmed_without_any_llm_call(monkeypatch):
    fake = FakeRunBatches([])
    monkeypatch.setattr(gen, "_run_batches", fake)
    pool = buckets(**{BUCKET_MCQ: [q("a"), q("b"), q("c")], BUCKET_TRUEFALSE: [q("t")]})
    kept, blueprint, usages = await reconcile(pool, {"mcq": 2})
    assert texts(kept[BUCKET_MCQ]) == ["a", "b"]
    assert kept[BUCKET_TRUEFALSE] == []
    assert fake.calls == [] and usages == []
    assert blueprint == {"title": "bp"}


async def test_short_response_triggers_one_top_up_for_exactly_the_shortfall(monkeypatch):
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 1)
    fake = FakeRunBatches([[{"questions": {BUCKET_MCQ: [q("c", "Analyze"), q("d", "Apply")]}}]])
    monkeypatch.setattr(gen, "_run_batches", fake)
    pool = buckets(**{BUCKET_MCQ: [q("a", "Remember")]})

    kept, _, usages = await reconcile(
        pool, {"mcq": 3},
        blooms_by_type={"mcq": ["Remember", "Analyze", "Apply"]},
    )

    batch = fake.calls[0]["batches"][0]
    assert batch.type_counts == {"mcq": 2}
    assert batch.blooms_by_type == {"mcq": ["Analyze", "Apply"]}
    # The top-up is shown the kept question so it does not repeat it.
    assert [d["stem"] for d in fake.calls[0]["digest"]] == ["a"]
    assert texts(kept[BUCKET_MCQ]) == ["a", "c", "d"]
    assert len(usages) == 1


async def test_top_up_questions_get_fresh_ids(monkeypatch):
    # Every call numbers its questions from q_001; the top-up's must not collide.
    fake = FakeRunBatches([[{"questions": {BUCKET_MCQ: [q("b")]}}]])
    monkeypatch.setattr(gen, "_run_batches", fake)
    kept, _, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")]}), {"mcq": 2})
    ids = [x["question_id"] for x in kept[BUCKET_MCQ]]
    assert ids[0] == "q_001" and ids[1] != "q_001"


async def test_top_up_that_over_generates_or_adds_wrong_types_is_trimmed(monkeypatch):
    fake = FakeRunBatches([[{"questions": {
        BUCKET_MCQ: [q("b"), q("c"), q("d")],
        BUCKET_FTB: [q("f")],
    }}]])
    monkeypatch.setattr(gen, "_run_batches", fake)
    kept, _, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")]}), {"mcq": 2})
    assert texts(kept[BUCKET_MCQ]) == ["a", "b"]
    assert kept[BUCKET_FTB] == []


async def test_top_up_that_returns_nothing_stops_after_max_rounds(monkeypatch):
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 3)
    fake = FakeRunBatches([[], [], [], []])
    monkeypatch.setattr(gen, "_run_batches", fake)
    kept, _, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")]}), {"mcq": 4})
    assert len(fake.calls) == 3
    assert texts(kept[BUCKET_MCQ]) == ["a"]


async def test_second_round_fills_what_the_first_left_short(monkeypatch):
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 2)
    fake = FakeRunBatches([
        [{"questions": {BUCKET_MCQ: [q("b")]}}],
        [{"questions": {BUCKET_MCQ: [q("c")]}}],
    ])
    monkeypatch.setattr(gen, "_run_batches", fake)
    kept, _, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")]}), {"mcq": 3})
    assert [c["batches"][0].type_counts for c in fake.calls] == [{"mcq": 2}, {"mcq": 1}]
    assert texts(kept[BUCKET_MCQ]) == ["a", "b", "c"]


async def test_zero_rounds_still_trims_but_never_tops_up(monkeypatch):
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 0)
    fake = FakeRunBatches([])
    monkeypatch.setattr(gen, "_run_batches", fake)
    kept, _, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")], BUCKET_FTB: [q("x")]}), {"mcq": 2})
    assert fake.calls == []
    assert kept[BUCKET_FTB] == []


async def test_missing_blueprint_is_authored_by_the_last_top_up_call(monkeypatch):
    fake = FakeRunBatches([[{"blueprint": {"title": "from top-up"},
                             "questions": {BUCKET_MCQ: [q("b")]}}]])
    monkeypatch.setattr(gen, "_run_batches", fake)
    _, blueprint, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")]}), {"mcq": 2}, blueprint={})
    assert fake.calls[0]["batches"][-1].is_final is True
    assert blueprint == {"title": "from top-up"}


async def test_existing_blueprint_is_kept_and_top_up_is_not_final(monkeypatch):
    fake = FakeRunBatches([[{"blueprint": {"title": "other"}, "questions": {BUCKET_MCQ: [q("b")]}}]])
    monkeypatch.setattr(gen, "_run_batches", fake)
    _, blueprint, _ = await reconcile(buckets(**{BUCKET_MCQ: [q("a")]}), {"mcq": 2})
    assert all(not b.is_final for b in fake.calls[0]["batches"])
    assert blueprint == {"title": "bp"}


async def test_top_up_asks_for_the_courses_still_owed(monkeypatch):
    fake = FakeRunBatches([[{"questions": {BUCKET_MCQ: [q("b", course="Course B"),
                                                         q("c", course="Course B")]}}]])
    monkeypatch.setattr(gen, "_run_batches", fake)
    meta = {"courses": [{"name": "Course A", "identifier": "do_A"},
                        {"name": "Course B", "identifier": "do_B"}]}
    await reconcile(
        buckets(**{BUCKET_MCQ: [q("a1", course="Course A"), q("a2", course="Course A")]}),
        {"mcq": 4},
        course_weightage=json.dumps({"do_A": 50, "do_B": 50}),
        aggregated_metadata=meta,
    )
    assert fake.calls[0]["batches"][0].course_counts == {"do_B": 2}


# --------------------------------------------------------------------------
# _generate_question_batch — unusable responses are retried
# --------------------------------------------------------------------------

class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def __call__(self, prompt, *, schema=None):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, {"total_token_count": 10}


async def test_invalid_json_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(gen, "BATCH_MAX_ATTEMPTS", 2)
    llm = FakeLLM(['{"questions": ', json.dumps({"questions": {}})])
    monkeypatch.setattr(gen, "call_llm", llm)
    payload, usage = await gen._generate_question_batch(
        prompt="p", schema={}, batch=Batch(index=0, type_counts={"mcq": 1}), job_id="j")
    assert payload == {"questions": {}}
    assert len(llm.prompts) == 2


@pytest.mark.parametrize("bad", ["[1, 2]", '"just a string"', "null"])
async def test_json_that_is_not_an_object_fails_after_retries(monkeypatch, bad):
    monkeypatch.setattr(gen, "BATCH_MAX_ATTEMPTS", 2)
    llm = FakeLLM([bad, bad])
    monkeypatch.setattr(gen, "call_llm", llm)
    with pytest.raises(ValueError):
        await gen._generate_question_batch(
            prompt="p", schema={}, batch=Batch(index=0, type_counts={"mcq": 1}), job_id="j")
    assert len(llm.prompts) == 2


async def test_llm_exception_is_reraised_after_retries(monkeypatch):
    monkeypatch.setattr(gen, "BATCH_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(gen, "call_llm", FakeLLM([RuntimeError("boom"), RuntimeError("boom 2")]))
    with pytest.raises(RuntimeError, match="boom 2"):
        await gen._generate_question_batch(
            prompt="p", schema={}, batch=Batch(index=0, type_counts={"mcq": 1}), job_id="j")


# --------------------------------------------------------------------------
# _run_batches — stop at the first batch that fails
# --------------------------------------------------------------------------

async def test_run_batches_stops_at_first_failure_and_keeps_earlier_batches(monkeypatch):
    responses = [
        ({"questions": {BUCKET_MCQ: [q("STEM-FROM-BATCH-ONE")]}}, {"t": 1}),
        RuntimeError("batch 2 failed"),
        ({"questions": {BUCKET_MCQ: [q("never")]}}, {"t": 3}),
    ]
    seen_prompts = []

    async def fake_batch(*, prompt, schema, batch, job_id):
        seen_prompts.append(prompt)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(gen, "_generate_question_batch", fake_batch)
    batches = [Batch(index=i, type_counts={"mcq": 1}, is_final=(i == 2)) for i in range(3)]
    digest = []
    payloads, usages = await gen._run_batches(
        batches, digest=digest, question_type_counts={"mcq": 3}, requested_total=3,
        blooms_str="", course_weightage=None, batch_prompt_inputs=SHARED_INPUTS, job_id="j",
    )
    assert len(payloads) == 1 and len(seen_prompts) == 2
    assert [d["stem"] for d in digest] == ["STEM-FROM-BATCH-ONE"]
    # The second batch was shown what the first produced.
    assert "STEM-FROM-BATCH-ONE" in seen_prompts[1]
    assert "STEM-FROM-BATCH-ONE" not in seen_prompts[0]


# --------------------------------------------------------------------------
# generate_assessment single-call path, LLM mocked end to end
# --------------------------------------------------------------------------

def _full_response(mcq_texts):
    return json.dumps({
        "blueprint": {"title": "bp"},
        "questions": {
            BUCKET_MCQ: [q(t, "Remember") for t in mcq_texts],
            BUCKET_FTB: [], "MTF Question": [], "Multi-Choice Question": [], BUCKET_TRUEFALSE: [],
        },
    })


async def test_generate_assessment_tops_up_a_short_single_call_response(monkeypatch):
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 1)
    llm = FakeLLM([_full_response(["a"]), _full_response(["b", "c"])])
    monkeypatch.setattr(gen, "call_llm", llm)

    _, result, _ = await gen.generate_assessment({"mcq": 3}, enable_blooms=False)

    assert texts(result["questions"][BUCKET_MCQ]) == ["a", "b", "c"]
    assert len(llm.prompts) == 2
    assert "3 question(s)" in result["blueprint"]["assessment_scope_summary"]


async def test_generate_assessment_rejects_non_json_response(monkeypatch):
    monkeypatch.setattr(gen, "call_llm", FakeLLM(["Sorry, I can't help with that."]))
    with pytest.raises(ValueError, match="not valid JSON"):
        await gen.generate_assessment({"mcq": 1}, enable_blooms=False)


async def test_reconcile_failure_returns_the_response_as_generated(monkeypatch):
    monkeypatch.setattr(gen, "call_llm", FakeLLM([_full_response(["a"])]))

    async def explode(*a, **k):
        raise RuntimeError("reconcile bug")

    monkeypatch.setattr(gen, "_reconcile_counts", explode)
    _, result, _ = await gen.generate_assessment({"mcq": 3}, enable_blooms=False)
    assert texts(result["questions"][BUCKET_MCQ]) == ["a"]


async def test_uploaded_transcript_and_document_land_in_their_own_sections(monkeypatch, tmp_path):
    transcript = tmp_path / "talk.txt"
    transcript.write_text("TRANSCRIPT BODY", encoding="utf-8")
    document = tmp_path / "notes.txt"
    document.write_text("DOCUMENT BODY", encoding="utf-8")
    llm = FakeLLM([_full_response(["a"])])
    monkeypatch.setattr(gen, "call_llm", llm)

    meta, _, _ = await gen.generate_assessment(
        {"mcq": 1}, enable_blooms=False,
        extra_files=[document], transcript_files=[transcript],
    )

    prompt = llm.prompts[0]
    transcripts_part, pdf_part = prompt.split("PDF CONTENT:", 1)
    assert "UPLOADED TRANSCRIPT: talk.txt" in transcripts_part and "TRANSCRIPT BODY" in transcripts_part
    assert "UPLOADED FILE: notes.txt" in pdf_part and "DOCUMENT BODY" in pdf_part
    assert meta["content_availability"]["uploaded_files"] == {"has_vtt": True, "has_pdf": True}


# --------------------------------------------------------------------------
# generate_assessment batched path (_generate_in_batches), LLM mocked
# --------------------------------------------------------------------------

def _batch_response(mcq_texts, blueprint=True):
    body = {"questions": {BUCKET_MCQ: [q(t, "Remember") for t in mcq_texts]}}
    if blueprint:
        body["blueprint"] = {"title": "bp"}
    return json.dumps(body)


@pytest.fixture
def batch_size_two(monkeypatch):
    """Force batching for a 3-question request: two calls of 2 + 1."""
    from assessment import batching

    monkeypatch.setattr(gen, "QUESTION_BATCH_SIZE", 2)
    monkeypatch.setattr(batching, "QUESTION_BATCH_SIZE", 2)
    monkeypatch.setattr(gen, "ENABLE_QUESTION_BATCHING", True)
    monkeypatch.setattr(gen, "BATCH_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 1)


class UsageLLM(FakeLLM):
    """FakeLLM whose every call reports 10 tokens, so summing can be checked."""

    async def __call__(self, prompt, *, schema=None):
        text, _ = await super().__call__(prompt, schema=schema)
        return text, {"total_token_count": 10, "prompt_token_count": 7}


async def test_batched_run_merges_batches_and_sums_usage(monkeypatch, batch_size_two):
    llm = UsageLLM([_batch_response(["STEM-ALPHA", "b"], blueprint=False), _batch_response(["c"])])
    monkeypatch.setattr(gen, "call_llm", llm)

    _, result, usage = await gen.generate_assessment({"mcq": 3}, enable_blooms=False, time_limit=30)

    assert len(llm.prompts) == 2
    assert texts(result["questions"][BUCKET_MCQ]) == ["STEM-ALPHA", "b", "c"]
    # Batch 2 is shown what batch 1 produced.
    assert "STEM-ALPHA" in llm.prompts[1] and "STEM-ALPHA" not in llm.prompts[0]
    # Fresh, unique ids although every batch numbered its questions q_001.
    ids = [x["question_id"] for x in result["questions"][BUCKET_MCQ]]
    assert len(set(ids)) == 3
    # The final batch's blueprint is kept and its counted fields recomputed.
    assert result["blueprint"]["title"] == "bp"
    assert "3 question(s)" in result["blueprint"]["assessment_scope_summary"]
    assert usage["total_token_count"] == 20 and usage["prompt_token_count"] == 14


async def test_batched_run_cut_short_is_topped_up_and_top_up_writes_blueprint(monkeypatch, batch_size_two):
    llm = UsageLLM([
        _batch_response(["a", "b"], blueprint=False),
        RuntimeError("batch 2 failed"),
        _batch_response(["c"]),            # top-up call, marked final
    ])
    monkeypatch.setattr(gen, "call_llm", llm)

    _, result, usage = await gen.generate_assessment({"mcq": 3}, enable_blooms=False)

    assert len(llm.prompts) == 3
    assert texts(result["questions"][BUCKET_MCQ]) == ["a", "b", "c"]
    assert result["blueprint"]["title"] == "bp"
    assert usage["total_token_count"] == 20  # the failed call reported nothing


async def test_batched_run_with_no_blueprint_still_reports_counted_fields(monkeypatch, batch_size_two):
    monkeypatch.setattr(gen, "TOPUP_MAX_ROUNDS", 0)
    llm = UsageLLM([_batch_response(["a", "b"], blueprint=False), RuntimeError("batch 2 failed")])
    monkeypatch.setattr(gen, "call_llm", llm)

    _, result, _ = await gen.generate_assessment({"mcq": 3}, enable_blooms=False)

    assert texts(result["questions"][BUCKET_MCQ]) == ["a", "b"]
    assert "title" not in result["blueprint"]
    assert "2 question(s)" in result["blueprint"]["assessment_scope_summary"]


async def test_batched_run_where_every_batch_fails_raises(monkeypatch, batch_size_two):
    monkeypatch.setattr(gen, "call_llm", UsageLLM([RuntimeError("down")]))
    with pytest.raises(RuntimeError, match="Every question batch failed"):
        await gen.generate_assessment({"mcq": 3}, enable_blooms=False)


async def test_batched_run_survives_a_reconcile_failure(monkeypatch, batch_size_two):
    llm = UsageLLM([_batch_response(["a", "b"], blueprint=False), _batch_response(["c", "d"])])
    monkeypatch.setattr(gen, "call_llm", llm)

    async def explode(*a, **k):
        raise RuntimeError("reconcile bug")

    monkeypatch.setattr(gen, "_reconcile_counts", explode)
    _, result, _ = await gen.generate_assessment({"mcq": 3}, enable_blooms=False)
    # Returned as generated (4, one over the request) rather than failing the job.
    assert texts(result["questions"][BUCKET_MCQ]) == ["a", "b", "c", "d"]
