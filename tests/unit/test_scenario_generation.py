"""
Scenario assessment generation, with the model mocked: domain assignment,
prompt rendering, batching, top-up, rubric weight repair and the blueprint.
"""

import json
import re

import pytest

from assessment import generator as gen
from assessment import scenario
from assessment.questions import (
    BUCKET_OPEN_TEXT,
    BUCKET_RANKING,
    BUCKET_SCENARIO_MCQ,
    SCENARIO_BUCKETS,
)
from factories import make_rubric, open_text, ranking, scenario_mcq


def request(**overrides):
    fields = dict(
        assessment_type="scenario",
        question_type_counts={"scenario_mcq": 2, "open_text": 1},
        difficulty_level="intermediate",
        input_language="english",
        assessment_topic="Leadership and conflict management",
        scenario_domains=["Team conflict", "Ethical decisions"],
        target_audience="New district officers",
    )
    fields.update(overrides)
    return gen.GenerationRequest(**fields)


def response(**by_bucket):
    questions = {bucket: [] for bucket in SCENARIO_BUCKETS}
    questions.update(by_bucket)
    return json.dumps({"questions": questions})


class FakeLLM:
    """Stands in for `call_llm`: records each call, returns canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, prompt, *, schema=None, use_kcm_cache=True):
        self.calls.append({"prompt": prompt, "schema": schema, "use_kcm_cache": use_kcm_cache})
        return self.responses.pop(0), {"total_token_count": 10}


# --------------------------------------------------------------------------
# Domains
# --------------------------------------------------------------------------

def test_domains_are_assigned_round_robin_across_the_whole_assessment():
    out = scenario.assign_domains({"scenario_mcq": 3, "open_text": 2}, ["A", "B"])
    assert out == {"scenario_mcq": ["A", "B", "A"], "open_text": ["B", "A"]}


def test_remaining_domains_skip_the_ones_already_used():
    planned = {"scenario_mcq": ["A", "B", "A"]}
    kept = {BUCKET_SCENARIO_MCQ: [scenario_mcq(scenario_domain="a")]}
    assert scenario.remaining_domains(planned, kept, {"scenario_mcq": 2}) == {"scenario_mcq": ["B", "A"]}


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

def test_prompt_lists_each_questions_domain_and_marks_unrequested_types():
    text = scenario.format_scenario_type_instructions(
        {"scenario_mcq": 2}, {"scenario_mcq": ["Team conflict", "Ethical decisions"]})
    assert "2 Scenario-based MCQs (SCENARIO_MCQ), with these scenario domains in order:" in text
    assert "1. Team conflict" in text and "2. Ethical decisions" in text
    assert "0 Open Text Questions (OPEN_TEXT) [DO NOT GENERATE]" in text


def test_every_placeholder_in_the_scenario_prompt_is_filled():
    prompt = scenario.build_scenario_prompt(
        {"assessment_topic": "T", "scenario_domains": "A; B", "target_audience": "Officers",
         "learning_areas": "L", "difficulty_level": "advanced", "input_language": "hindi"},
        type_counts={"open_text": 1}, domains_by_type={"open_text": ["A"]},
    )
    assert not re.findall(r"\{[a-z_]+\}", prompt)
    assert "Assessment Topic: T" in prompt and "Target Audience: Officers" in prompt
    assert "Difficulty Level: advanced" in prompt
    # No uploads — the model is told to generate from the inputs alone.
    assert "None provided. Generate from the topic" in prompt


def test_user_text_that_looks_like_a_placeholder_is_not_substituted():
    prompt = scenario.build_scenario_prompt(
        {"assessment_topic": "{difficulty_level}", "difficulty_level": "advanced"},
        type_counts={"open_text": 1}, domains_by_type={},
    )
    assert "Assessment Topic: {difficulty_level}" in prompt


# --------------------------------------------------------------------------
# Rubric weights
# --------------------------------------------------------------------------

@pytest.mark.parametrize("weights, expected", [
    ((30, 30, 30), [34, 33, 33]),
    ((2, 1, 1), [50, 25, 25]),
    ((40.0, 30.0, 30.0), [40, 30, 30]),
])
def test_rubric_weights_are_rescaled_to_100(weights, expected):
    rubric = make_rubric(weights)
    assert scenario.rebalance_rubric_weights(rubric) is True
    assert [c["weight_percent"] for c in rubric] == expected


@pytest.mark.parametrize("weights", [(40, 30, 30), (40, None, 30), (40, 0, 30), (40, "x", 30)])
def test_rubric_weights_are_left_alone_when_correct_or_unusable(weights):
    rubric = make_rubric(weights)
    assert scenario.rebalance_rubric_weights(rubric) is False
    assert [c["weight_percent"] for c in rubric] == list(weights)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

async def test_generate_assessment_dispatches_scenario_requests(monkeypatch):
    llm = FakeLLM([response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b", scenario_domain="Ethical decisions")],
        BUCKET_OPEN_TEXT: [open_text("c")],
    })])
    monkeypatch.setattr(gen, "call_llm", llm)

    metadata, assessment, usage = await gen.generate_assessment(request())

    assert [len(assessment["questions"][b]) for b in SCENARIO_BUCKETS] == [2, 0, 1, 0, 0, 0]
    assert list(assessment["questions"]) == SCENARIO_BUCKETS
    # One call, without the KCM context cache, against the scenario schema.
    assert len(llm.calls) == 1
    assert llm.calls[0]["use_kcm_cache"] is False
    assert llm.calls[0]["schema"] is scenario.SCENARIO_SCHEMA
    assert metadata == {"courses": [], "content_availability": {}}
    assert usage["total_token_count"] == 10


async def test_blueprint_is_computed_from_the_request_and_the_questions(monkeypatch):
    monkeypatch.setattr(gen, "call_llm", FakeLLM([response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b", scenario_domain="Ethical decisions")],
        BUCKET_OPEN_TEXT: [open_text("c")],
    })]))
    _, assessment, _ = await gen.generate_assessment(request(time_limit=30))
    blueprint = assessment["blueprint"]

    assert blueprint["assessment_topic"] == "Leadership and conflict management"
    assert blueprint["target_audience"] == "New district officers"
    assert blueprint["domain_distribution"] == {"Team conflict": 2, "Ethical decisions": 1}
    assert blueprint["question_type_distribution"] == {"scenario_mcq": 2, "open_text": 1}
    assert blueprint["time_appropriateness_validation"].startswith("30 minute(s) for 3 question(s)")
    assert blueprint["prompt_version"].startswith("scenario-")
    assert "no reference documents" in blueprint["assessment_scope_summary"]


async def test_large_requests_are_batched_and_later_batches_see_earlier_questions(monkeypatch):
    monkeypatch.setattr(scenario, "SCENARIO_BATCH_SIZE", 2)
    llm = FakeLLM([
        response(**{BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b")]}),
        response(**{BUCKET_OPEN_TEXT: [open_text("c")]}),
    ])
    monkeypatch.setattr(gen, "call_llm", llm)

    _, assessment, usage = await gen.generate_assessment(request())

    assert len(llm.calls) == 2
    assert "ALREADY GENERATED" not in llm.calls[0]["prompt"]
    assert "ALREADY GENERATED" in llm.calls[1]["prompt"]
    assert "[Disagreement Over Training Data]" in llm.calls[1]["prompt"]
    assert usage["batch_count"] == 2
    ids = [q["question_id"] for qs in assessment["questions"].values() for q in qs]
    assert len(set(ids)) == 3


async def test_a_short_response_is_topped_up_with_the_unused_domains(monkeypatch):
    monkeypatch.setattr(scenario, "TOPUP_MAX_ROUNDS", 1)
    llm = FakeLLM([
        response(**{BUCKET_SCENARIO_MCQ: [scenario_mcq("a")], BUCKET_OPEN_TEXT: [open_text("c")]}),
        response(**{BUCKET_SCENARIO_MCQ: [scenario_mcq("b", scenario_domain="Ethical decisions")]}),
    ])
    monkeypatch.setattr(gen, "call_llm", llm)

    _, assessment, _ = await gen.generate_assessment(request())

    assert len(assessment["questions"][BUCKET_SCENARIO_MCQ]) == 2
    topup_prompt = llm.calls[1]["prompt"]
    assert "1 Scenario-based MCQs (SCENARIO_MCQ), with these scenario domains in order:" in topup_prompt
    assert "1. Ethical decisions" in topup_prompt


async def test_extra_questions_are_trimmed(monkeypatch):
    monkeypatch.setattr(gen, "call_llm", FakeLLM([response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b"), scenario_mcq("x")],
        BUCKET_OPEN_TEXT: [open_text("c")],
        BUCKET_RANKING: [ranking("unrequested")],
    })]))
    _, assessment, _ = await gen.generate_assessment(request())
    assert len(assessment["questions"][BUCKET_SCENARIO_MCQ]) == 2
    assert assessment["questions"][BUCKET_RANKING] == []


async def test_generated_rubric_weights_are_repaired_and_difficulty_set_from_the_request(monkeypatch):
    question = open_text("c", difficulty_level="advanced", rubric=make_rubric((30, 30, 30)))
    monkeypatch.setattr(gen, "call_llm", FakeLLM([response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b")],
        BUCKET_OPEN_TEXT: [question],
    })]))
    _, assessment, _ = await gen.generate_assessment(request())
    stored = assessment["questions"][BUCKET_OPEN_TEXT][0]
    assert [c["weight_percent"] for c in stored["rubric"]] == [34, 33, 33]
    assert stored["difficulty_level"] == "intermediate"


async def test_a_question_with_a_broken_rubric_is_kept_not_retried(monkeypatch):
    broken = open_text("c", rubric=make_rubric((100,)))  # missing anchor below
    del broken["rubric"][0]["score_anchors"]["3"]
    llm = FakeLLM([response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b")],
        BUCKET_OPEN_TEXT: [broken],
    })])
    monkeypatch.setattr(gen, "call_llm", llm)
    _, assessment, _ = await gen.generate_assessment(request())
    assert len(llm.calls) == 1
    assert len(assessment["questions"][BUCKET_OPEN_TEXT][0]["rubric"]) == 1


async def test_an_unusable_response_is_retried_as_on_the_standard_path(monkeypatch):
    llm = FakeLLM(["not json", response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b")],
        BUCKET_OPEN_TEXT: [open_text("c")],
    })])
    monkeypatch.setattr(gen, "call_llm", llm)
    _, assessment, _ = await gen.generate_assessment(request())
    assert len(llm.calls) == 2
    assert len(assessment["questions"][BUCKET_SCENARIO_MCQ]) == 2


async def test_every_batch_failing_fails_the_job(monkeypatch):
    monkeypatch.setattr(gen, "call_llm", FakeLLM(["nope", "still nope"]))
    with pytest.raises(RuntimeError, match="Every scenario question batch failed"):
        await gen.generate_assessment(request())


@pytest.mark.parametrize("overrides", [
    {"assessment_topic": " "},
    {"target_audience": None},
    {"scenario_domains": []},
    {"question_type_counts": {"mcq": 3}},
])
async def test_missing_scenario_inputs_fail_before_any_call(monkeypatch, overrides):
    llm = FakeLLM([])
    monkeypatch.setattr(gen, "call_llm", llm)
    with pytest.raises(ValueError):
        await gen.generate_assessment(request(**overrides))
    assert llm.calls == []


async def test_uploaded_documents_become_the_reference_content(monkeypatch, tmp_path):
    doc = tmp_path / "policy.txt"
    doc.write_text("Grievances must be acknowledged within 3 days.", encoding="utf-8")
    llm = FakeLLM([response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b")],
        BUCKET_OPEN_TEXT: [open_text("c")],
    })])
    monkeypatch.setattr(gen, "call_llm", llm)

    metadata, assessment, _ = await gen.generate_assessment(request(extra_files=[doc]))

    assert "--- UPLOADED FILE: policy.txt ---" in llm.calls[0]["prompt"]
    assert "acknowledged within 3 days" in llm.calls[0]["prompt"]
    assert metadata["content_availability"]["uploaded_files"]["has_pdf"] is True
    assert "uploaded reference documents" in assessment["blueprint"]["assessment_scope_summary"]
