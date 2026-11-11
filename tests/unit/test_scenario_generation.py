"""
Scenario assessment generation, with the model mocked: domain assignment,
prompt rendering, batching, top-up, rubric weight repair, the blueprint and
planned parallel generation.
"""

import asyncio
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


class RoutedLLM:
    """
    Stands in for `call_llm` where batches run in parallel and the call order
    is not fixed: `route(prompt, schema)` picks each response. Records how many
    calls were in flight at once.
    """

    def __init__(self, route, delay=0.0):
        self.route = route
        self.delay = delay
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, prompt, *, schema=None, use_kcm_cache=True):
        self.calls.append({"prompt": prompt, "schema": schema, "use_kcm_cache": use_kcm_cache})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return self.route(prompt, schema), {"total_token_count": 10}
        finally:
            self.in_flight -= 1


def plan_response(slots, overrides=None):
    """
    A planning response with a seed for each slot number in `slots`;
    `overrides` maps a slot number to fields that replace its seed's.
    """
    entries = [{"slot": n, "learner": f"Learner {n}", "setting": f"Setting {n}",
                "trigger": f"Trigger {n}", "roles": f"Roles {n}"} for n in slots]
    for entry in entries:
        entry.update((overrides or {}).get(entry["slot"], {}))
    return {"plan": entries}


def is_plan_call(schema):
    return schema is scenario._PLAN_SCHEMA


# The per-type line a batch prompt carries for the count it asks for.
MCQ_X2 = "- 2 Scenario-based MCQs (SCENARIO_MCQ), with"
OPEN_X1 = "- 1 Open Text Questions (OPEN_TEXT), with"


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
    monkeypatch.setattr(scenario, "SCENARIO_PLANNER_ENABLED", False)
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


# --------------------------------------------------------------------------
# Planned parallel generation
# --------------------------------------------------------------------------
# request() asks for 2 scenario MCQs and 1 open text over two domains. With
# SCENARIO_BATCH_SIZE = 2 that is two batches — the MCQs, then the open text —
# and three plan slots: 1 MCQ/Team conflict, 2 MCQ/Ethical decisions,
# 3 open text/Team conflict.

@pytest.fixture
def planned(monkeypatch):
    """The planned path on, two batches per request(), no hidden env overrides."""
    monkeypatch.setattr(scenario, "SCENARIO_PLANNER_ENABLED", True)
    monkeypatch.setattr(scenario, "SCENARIO_BATCH_SIZE", 2)
    monkeypatch.setattr(scenario, "SCENARIO_BATCH_CONCURRENCY", 6)
    monkeypatch.setattr(scenario, "BATCH_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(gen, "BATCH_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(scenario, "TOPUP_MAX_ROUNDS", 1)


def default_route(prompt, schema):
    if is_plan_call(schema):
        return json.dumps(plan_response([1, 2, 3]))
    if MCQ_X2 in prompt:
        return response(**{BUCKET_SCENARIO_MCQ: [
            scenario_mcq("a"), scenario_mcq("b", scenario_domain="Ethical decisions")]})
    if OPEN_X1 in prompt:
        return response(**{BUCKET_OPEN_TEXT: [open_text("c")]})
    raise AssertionError(f"Unexpected prompt:\n{prompt}")


def test_plan_slots_number_every_question_in_type_order():
    domains = scenario.assign_domains({"scenario_mcq": 2, "open_text": 1}, ["A", "B"])
    assert scenario.plan_slots(domains) == [
        {"slot": 1, "type": "scenario_mcq", "domain": "A"},
        {"slot": 2, "type": "scenario_mcq", "domain": "B"},
        {"slot": 3, "type": "open_text", "domain": "A"},
    ]


def test_parse_plan_merges_seeds_onto_their_slots_in_slot_order():
    slots = scenario.plan_slots({"scenario_mcq": ["A", "B"]})
    payload = plan_response([2, 1], {1: {"setting": "  Ward   office "}})
    plan = scenario.parse_plan(payload, slots)
    assert [p["slot"] for p in plan] == [1, 2]
    assert plan[0] == {"slot": 1, "type": "scenario_mcq", "domain": "A", "learner": "Learner 1",
                       "setting": "Ward office", "trigger": "Trigger 1", "roles": "Roles 1"}


@pytest.mark.parametrize("payload, reason", [
    ({}, "no `plan` list"),
    ({"plan": "x"}, "no `plan` list"),
    (plan_response([1]), "not planned"),
    ({"plan": plan_response([1, 2])["plan"] + plan_response([2])["plan"]}, "planned twice"),
    (plan_response([1, 2, 9]), "unknown slot 9"),
    (plan_response([1, 2], {2: {"trigger": " "}}), "slot 2 has no trigger"),
    (plan_response([1, 2], {2: {"slot": "two"}}), "is not a number"),
])
def test_parse_plan_rejects_a_plan_that_cannot_be_used(payload, reason):
    slots = scenario.plan_slots({"scenario_mcq": ["A", "B"]})
    with pytest.raises(ValueError, match=re.escape(reason)):
        scenario.parse_plan(payload, slots)


def test_slice_plan_lines_each_batchs_seeds_up_with_its_domains():
    counts = {"scenario_mcq": 3, "open_text": 1}
    domains = scenario.assign_domains(counts, ["A", "B"])
    batches = scenario.plan_batches(counts, domains, batch_size=2)
    plan = scenario.parse_plan(plan_response([1, 2, 3, 4]), scenario.plan_slots(domains))

    sliced = scenario.slice_plan(batches, plan)

    assert len(sliced) == len(batches) == 2
    for batch, seeds in zip(batches, sliced):
        for type_key, type_domains in batch.blooms_by_type.items():
            assert [s["domain"] for s in seeds[type_key]] == type_domains
    assert [s["slot"] for s in sliced[0]["scenario_mcq"]] == [1, 2]
    assert [s["slot"] for s in sliced[1]["scenario_mcq"]] == [3]
    assert [s["slot"] for s in sliced[1]["open_text"]] == [4]


def test_seeds_appear_next_to_their_domains_in_the_prompt():
    seeds = {"scenario_mcq": [{"learner": "L", "setting": "S", "trigger": "T", "roles": "R"}]}
    text = scenario.format_scenario_type_instructions(
        {"scenario_mcq": 1}, {"scenario_mcq": ["Team conflict"]}, seeds)
    assert "with these scenario domains and seeds in order:" in text
    assert "1. Team conflict — you: L; setting: S; trigger: T; roles: R" in text
    assert "Each seed fixes only who the learner is" in text


async def test_large_requests_plan_once_then_run_batches_with_their_own_seeds(monkeypatch, planned):
    llm = RoutedLLM(default_route)
    monkeypatch.setattr(gen, "call_llm", llm)

    metadata, assessment, usage = await gen.generate_assessment(request())

    # One planning call first, then one call per batch.
    assert len(llm.calls) == 3
    assert is_plan_call(llm.calls[0]["schema"])
    assert llm.calls[0]["use_kcm_cache"] is False
    assert "Leadership and conflict management" in llm.calls[0]["prompt"]
    batch_calls = llm.calls[1:]
    assert all(c["schema"] is scenario.SCENARIO_SCHEMA for c in batch_calls)

    # Each batch sees only its own seeds and no "already generated" list.
    mcq_prompt = next(c["prompt"] for c in batch_calls if MCQ_X2 in c["prompt"])
    open_prompt = next(c["prompt"] for c in batch_calls if OPEN_X1 in c["prompt"])
    assert "1. Team conflict — you: Learner 1; setting: Setting 1" in mcq_prompt
    assert "2. Ethical decisions — you: Learner 2; setting: Setting 2" in mcq_prompt
    assert "Setting 3" not in mcq_prompt
    assert "1. Team conflict — you: Learner 3; setting: Setting 3" in open_prompt
    assert "Setting 1" not in open_prompt
    assert all("ALREADY GENERATED" not in c["prompt"] for c in batch_calls)

    assert [len(assessment["questions"][b]) for b in SCENARIO_BUCKETS] == [2, 0, 1, 0, 0, 0]
    ids = [q["question_id"] for qs in assessment["questions"].values() for q in qs]
    assert len(set(ids)) == 3
    # The plan is kept in the metadata, and its call counted in the usage.
    assert [p["slot"] for p in metadata["scenario_plan"]] == [1, 2, 3]
    assert metadata["scenario_plan"][0]["setting"] == "Setting 1"
    assert usage["batch_count"] == 3


async def test_planned_batches_run_in_parallel_up_to_the_concurrency_limit(monkeypatch, planned):
    monkeypatch.setattr(scenario, "SCENARIO_BATCH_SIZE", 1)
    monkeypatch.setattr(scenario, "SCENARIO_BATCH_CONCURRENCY", 2)
    numbers = iter(range(100))

    def route(prompt, schema):
        if is_plan_call(schema):
            return json.dumps(plan_response([1, 2, 3, 4]))
        return response(**{BUCKET_SCENARIO_MCQ: [scenario_mcq(f"q{next(numbers)}")]})

    llm = RoutedLLM(route, delay=0.01)
    monkeypatch.setattr(gen, "call_llm", llm)

    _, assessment, _ = await gen.generate_assessment(
        request(question_type_counts={"scenario_mcq": 4}))

    assert len(llm.calls) == 5  # the plan, then four one-question batches
    assert llm.max_in_flight == 2
    assert len(assessment["questions"][BUCKET_SCENARIO_MCQ]) == 4


async def test_a_single_batch_request_is_not_planned(monkeypatch, planned):
    monkeypatch.setattr(scenario, "SCENARIO_BATCH_SIZE", 25)
    llm = RoutedLLM(lambda prompt, schema: response(**{
        BUCKET_SCENARIO_MCQ: [scenario_mcq("a"), scenario_mcq("b", scenario_domain="Ethical decisions")],
        BUCKET_OPEN_TEXT: [open_text("c")],
    }))
    monkeypatch.setattr(gen, "call_llm", llm)

    metadata, _, _ = await gen.generate_assessment(request())

    assert len(llm.calls) == 1
    assert not is_plan_call(llm.calls[0]["schema"])
    assert "scenario_plan" not in metadata


async def test_an_unusable_plan_is_retried(monkeypatch, planned):
    plans = iter([plan_response([1, 2]), plan_response([1, 2, 3])])  # first is a slot short

    def route(prompt, schema):
        if is_plan_call(schema):
            return json.dumps(next(plans))
        return default_route(prompt, schema)

    llm = RoutedLLM(route)
    monkeypatch.setattr(gen, "call_llm", llm)

    metadata, assessment, _ = await gen.generate_assessment(request())

    assert sum(is_plan_call(c["schema"]) for c in llm.calls) == 2
    assert len(metadata["scenario_plan"]) == 3
    assert sum(len(qs) for qs in assessment["questions"].values()) == 3


async def test_failed_planning_falls_back_to_sequential_batches(monkeypatch, planned):
    def route(prompt, schema):
        if is_plan_call(schema):
            return "not json"
        return default_route(prompt, schema)

    llm = RoutedLLM(route)
    monkeypatch.setattr(gen, "call_llm", llm)

    metadata, assessment, _ = await gen.generate_assessment(request())

    # Both planning attempts, then the two batches in order, the second shown the first.
    assert [is_plan_call(c["schema"]) for c in llm.calls] == [True, True, False, False]
    assert "ALREADY GENERATED" not in llm.calls[2]["prompt"]
    assert "ALREADY GENERATED" in llm.calls[3]["prompt"]
    assert "seeds in order" not in llm.calls[2]["prompt"]
    assert "scenario_plan" not in metadata
    assert [len(assessment["questions"][b]) for b in SCENARIO_BUCKETS] == [2, 0, 1, 0, 0, 0]


async def test_a_failed_planned_batch_does_not_stop_the_others_and_is_topped_up(monkeypatch, planned):
    def route(prompt, schema):
        if OPEN_X1 in prompt and "seeds in order" in prompt:
            return "nope"  # the planned open-text batch fails every attempt
        return default_route(prompt, schema)

    llm = RoutedLLM(route)
    monkeypatch.setattr(gen, "call_llm", llm)

    _, assessment, _ = await gen.generate_assessment(request())

    open_calls = [c["prompt"] for c in llm.calls if OPEN_X1 in c["prompt"]]
    assert len(open_calls) == 3  # two planned attempts, then the top-up
    topup = open_calls[-1]
    assert "seeds in order" not in topup
    assert "ALREADY GENERATED" in topup  # the top-up sees the MCQs that succeeded
    assert "1. Team conflict" in topup
    assert [len(assessment["questions"][b]) for b in SCENARIO_BUCKETS] == [2, 0, 1, 0, 0, 0]


async def test_every_planned_batch_failing_fails_the_job(monkeypatch, planned):
    def route(prompt, schema):
        if is_plan_call(schema):
            return json.dumps(plan_response([1, 2, 3]))
        return "nope"

    monkeypatch.setattr(gen, "call_llm", RoutedLLM(route))
    with pytest.raises(RuntimeError, match="Every scenario question batch failed"):
        await gen.generate_assessment(request())
