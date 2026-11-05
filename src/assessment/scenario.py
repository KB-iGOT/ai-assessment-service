"""
Scenario assessment generation (`assessment_type = scenario`).

A scenario assessment is generated from an assessment topic, one or more
scenario domains and a target audience — never from courses — optionally
grounded in uploaded reference documents. It uses six question types of its
own (see `questions.SCENARIO_BUCKETS`), and every written-answer question
carries a criterion rubric whose weights are percentages, so marks can be set
later without regenerating anything.

The flow mirrors the batched path in generator.py and reuses its pure
helpers — batch planning, merging, trimming, the "already generated" digest and
the per-batch retry — with three differences:

  * **Its own prompt and response schema.** `scenario_prompt_template` in
    resources/prompts.yaml, and `SCENARIO_SCHEMA` below.
  * **Scenario domains are assigned before the first call.** Each question
    slot is given its domain round-robin across the whole assessment, and each
    batch is told the domain of every question it writes — the same way the
    standard path hands each batch its slice of the Bloom's assignment.
  * **The blueprint is computed, not generated.** Every field is a count or a
    property of the request, so no call is spent authoring it.

Nothing here fails a job over question content. A response that cannot be
used at all is retried exactly as on the standard path; a rubric whose weights
do not add up to 100 is rescaled; any other problem is logged and the question
kept for a reviewer to fix through the editing endpoints.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from . import generator as gen
from .batching import Batch, merge_batches, plan_batches, summarize_for_dedup
from .config import SCENARIO_BATCH_SIZE, TOPUP_MAX_ROUNDS
from .questions import (
    BUCKET_ACTION_PLANNING,
    BUCKET_BY_TYPE_KEY,
    BUCKET_OPEN_TEXT,
    BUCKET_RANKING,
    BUCKET_SCENARIO_MCQ,
    BUCKET_SCENARIO_MULTISELECT,
    BUCKET_SHORT_ANSWER,
    QUESTION_TYPE_CONST_BY_BUCKET,
    RUBRIC_BUCKETS,
    SCENARIO_BUCKETS,
    SCENARIO_TYPE_KEYS,
    TYPE_KEY_BY_BUCKET,
    new_question_id,
)
from .response_recovery import (
    Buckets,
    count_by_type,
    extract_buckets,
    requested_counts,
    shortfall,
    trim_to_counts,
)
from .validation import validate_scenario_question

logger = logging.getLogger(__name__)

SCENARIO_PROMPT_VERSION = f"scenario-{gen.ASSESSMENT_PROMPTS.get('scenario_version', 'Unknown')}"

# Prompt-facing label for each scenario type key, in prompt order.
_TYPE_LABELS: Dict[str, str] = {
    "scenario_mcq": "Scenario-based MCQs (SCENARIO_MCQ)",
    "scenario_multiselect": "Scenario-based Multi-Select Questions (SCENARIO_MULTISELECT)",
    "open_text": "Open Text Questions (OPEN_TEXT)",
    "sjt_ranking": "Situational Judgment Ranking Questions (SJT_RANKING)",
    "action_planning": "Action and Decision Planning Questions (ACTION_PLANNING)",
    "short_answer": "Scenario-based Short Answer Questions (SHORT_ANSWER)",
}

_NO_CONTENT = (
    "None provided. Generate from the topic, scenario domains and target "
    "audience alone, following the Facts rule under SCENARIO RULES."
)


# --------------------------------------------------------------------------
# Response schema
# --------------------------------------------------------------------------
# Built here rather than written out in resources/schemas.json: the six types
# share most of their fields, and spelling each one out in full would be six
# near-identical copies that could drift apart.

_STRING = {"type": "string"}
_STRING_LIST = {"type": "array", "items": {"type": "string"}}

_OPTIONS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": _STRING,
            "index": {
                "type": "integer",
                "description": "ZERO-BASED position of this option. The first option MUST be 0, "
                               "the second 1, and so on. Never start at 1.",
            },
        },
        "required": ["text", "index"],
    },
}

_RUBRIC = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "criterion": _STRING,
            "description": _STRING,
            "weight_percent": {
                "type": "integer",
                "description": "This criterion's share of the question, as a whole-number "
                               "percentage. A rubric's weights add up to exactly 100.",
            },
            "score_anchors": {
                "type": "object",
                "properties": {level: _STRING for level in ("0", "1", "2", "3")},
                "required": ["0", "1", "2", "3"],
            },
            "evidence_indicators": _STRING_LIST,
        },
        "required": ["criterion", "description", "weight_percent", "score_anchors",
                     "evidence_indicators"],
    },
}


def _question_schema(bucket: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    properties = {
        "question_id": _STRING,
        "question_type": {"type": "string", "const": QUESTION_TYPE_CONST_BY_BUCKET[bucket]},
        "scenario_domain": _STRING,
        "scenario": {
            "type": "object",
            "properties": {"title": _STRING, "description": _STRING, "constraints": _STRING_LIST},
            "required": ["title", "description", "constraints"],
        },
        "question_text": _STRING,
        **extra,
        "answer_rationale": {
            "type": "object",
            "properties": {"correct_answer_explanation": _STRING},
            "required": ["correct_answer_explanation"],
        },
    }
    return {"type": "array", "items": {
        "type": "object", "properties": properties, "required": list(properties),
    }}


_SINGLE_INDEX = {
    "type": "integer",
    "description": "ZERO-BASED index of the single correct option. MUST equal that option's `index`.",
}
_INDEX_ARRAY = {
    "type": "array", "items": {"type": "integer"},
    "description": "ZERO-BASED indexes of every correct option. Each MUST equal a correct option's `index`.",
}
_RANKING = {
    "type": "array", "items": {"type": "integer"},
    "description": "Option `index` values ordered from MOST to LEAST effective. Every option exactly once.",
}

SCENARIO_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "object",
            "properties": {
                BUCKET_SCENARIO_MCQ: _question_schema(
                    BUCKET_SCENARIO_MCQ,
                    {"options": _OPTIONS, "correct_option_index": _SINGLE_INDEX},
                ),
                BUCKET_SCENARIO_MULTISELECT: _question_schema(
                    BUCKET_SCENARIO_MULTISELECT,
                    {"options": _OPTIONS, "correct_option_index": _INDEX_ARRAY},
                ),
                BUCKET_OPEN_TEXT: _question_schema(
                    BUCKET_OPEN_TEXT,
                    {"reference_answer": _STRING, "rubric": _RUBRIC},
                ),
                BUCKET_RANKING: _question_schema(
                    BUCKET_RANKING,
                    {"options": _OPTIONS, "correct_ranking": _RANKING},
                ),
                BUCKET_ACTION_PLANNING: _question_schema(
                    BUCKET_ACTION_PLANNING,
                    {"expected_components": _STRING_LIST, "rubric": _RUBRIC},
                ),
                BUCKET_SHORT_ANSWER: _question_schema(
                    BUCKET_SHORT_ANSWER,
                    {"expected_answer": _STRING, "rubric": _RUBRIC},
                ),
            },
            "required": list(SCENARIO_BUCKETS),
        },
    },
    "required": ["questions"],
}


# --------------------------------------------------------------------------
# Scenario domains
# --------------------------------------------------------------------------

def assign_domains(type_counts: Dict[str, int], domains: List[str]) -> Dict[str, List[str]]:
    """
    The scenario domain of every question slot, per type, in order.

    Assigned round-robin across the whole assessment, continuing from one type
    to the next, so each domain gets an equal share of the questions (within
    one) and each type spreads across the domains as evenly as its count allows.
    """
    out: Dict[str, List[str]] = {}
    if not domains:
        return out
    cursor = 0
    for type_key in SCENARIO_TYPE_KEYS:
        count = int(type_counts.get(type_key) or 0)
        if count <= 0:
            continue
        out[type_key] = [domains[(cursor + i) % len(domains)] for i in range(count)]
        cursor += count
    return out


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def remaining_domains(
    domains_by_type: Dict[str, List[str]],
    kept: Buckets,
    missing: Dict[str, int],
) -> Dict[str, List[str]]:
    """
    The domains a top-up should assign, per missing type: the type's planned
    domains not yet used by the questions kept, truncated to the number missing.
    """
    out: Dict[str, List[str]] = {}
    for type_key, count in missing.items():
        used = Counter(_norm(q.get("scenario_domain"))
                       for q in kept.get(BUCKET_BY_TYPE_KEY[type_key], []))
        left: List[str] = []
        for domain in domains_by_type.get(type_key) or []:
            if used.get(_norm(domain), 0) > 0:
                used[_norm(domain)] -= 1
            else:
                left.append(domain)
        if left[:count]:
            out[type_key] = left[:count]
    return out


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

def format_scenario_type_instructions(
    type_counts: Dict[str, int],
    domains_by_type: Dict[str, List[str]],
) -> str:
    """
    The per-type count block, with the domain of each question listed in
    order. Types with a zero count are listed as DO NOT GENERATE, for the same
    reason as on the standard path: naming every type stops the model
    volunteering one that was not asked for.
    """
    lines: List[str] = []
    for type_key in SCENARIO_TYPE_KEYS:
        count = int(type_counts.get(type_key) or 0)
        label = _TYPE_LABELS[type_key]
        if count <= 0:
            lines.append(f"     - 0 {label} [DO NOT GENERATE]")
            continue
        domains = domains_by_type.get(type_key) or []
        if not domains:
            lines.append(f"     - {count} {label}")
            continue
        lines.append(f"     - {count} {label}, with these scenario domains in order:")
        lines.extend(f"         {i}. {domain}" for i, domain in enumerate(domains, start=1))
    return "\n" + "\n".join(lines)


def format_scenario_previously_generated(digest: List[Dict[str, str]]) -> str:
    """The "already generated" block a batch after the first is shown."""
    if not digest:
        return ""

    grouped: Dict[str, List[Dict[str, str]]] = {}
    for entry in digest:
        grouped.setdefault(entry.get("type", ""), []).append(entry)

    lines: List[str] = []
    number = 0
    for type_key in [t for t in SCENARIO_TYPE_KEYS if t in grouped] + [
        t for t in grouped if t not in SCENARIO_TYPE_KEYS
    ]:
        lines.append(f"  {_TYPE_LABELS.get(type_key, type_key)}")
        for entry in grouped[type_key]:
            number += 1
            domain = entry.get("scenario_domain")
            tag = f"[{domain}] " if domain else ""
            lines.append(f"   {number:>3}. {tag}{entry['stem']}")

    return (
        "  ------------------------------------------------------------\n"
        "  ALREADY GENERATED IN EARLIER PARTS — DO NOT REPEAT\n"
        "  ------------------------------------------------------------\n"
        f"  The following {number} question(s) have already been generated for this\n"
        "  assessment, each shown with its domain and [scenario title]. They are NOT\n"
        "  part of your count.\n\n"
        + "\n".join(lines)
        + "\n\n"
        "  - Every question you write MUST differ from all of the above in its\n"
        "    situation AND in the judgment it tests — not merely in names, places or\n"
        "    wording. Re-using a situation with different details is a duplicate.\n"
        "  - This does NOT change your counts or domain assignments in section 9."
    )


def build_scenario_prompt(
    inputs: Dict[str, Any],
    *,
    type_counts: Dict[str, int],
    domains_by_type: Dict[str, List[str]],
    previously_generated: str = "",
) -> str:
    """
    Render `scenario_prompt_template`.

    Placeholders whose values come from the user or the model are replaced
    last, so text inside them that happens to look like a placeholder is never
    substituted.
    """
    prompt = gen.ASSESSMENT_PROMPTS.get("scenario_prompt_template", "")
    structural = [
        ("{question_type_instructions}", format_scenario_type_instructions(type_counts, domains_by_type)),
        ("{total_questions}", str(sum(int(v or 0) for v in type_counts.values()))),
        ("{difficulty_level}", inputs.get("difficulty_level") or "intermediate"),
        ("{time_to_complete}", inputs.get("time_to_complete") or "Not provided (use standard pacing)"),
        ("{input_language}", inputs.get("input_language") or "English"),
    ]
    supplied = [
        ("{previously_generated}", previously_generated),
        ("{assessment_topic}", inputs.get("assessment_topic") or ""),
        ("{scenario_domains}", inputs.get("scenario_domains") or ""),
        ("{target_audience}", inputs.get("target_audience") or ""),
        ("{learning_areas}", inputs.get("learning_areas") or ""),
        ("{additional_instructions}", inputs.get("additional_instructions") or "None provided"),
        ("{content_context}", inputs.get("content_context") or _NO_CONTENT),
    ]
    for placeholder, value in structural + supplied:
        prompt = prompt.replace(placeholder, value)
    return prompt


# --------------------------------------------------------------------------
# Rubric weights
# --------------------------------------------------------------------------

def rebalance_rubric_weights(rubric: Any) -> bool:
    """
    Rescale a rubric's `weight_percent` values so they add up to exactly 100,
    keeping their proportions (largest remainder, ties to the earlier
    criterion — 30/30/30 becomes 34/33/33).

    Applied to fresh LLM output only. Returns True when the weights were
    changed. A rubric with any missing, non-numeric or non-positive weight is
    left as generated: there is no proportion to keep, so it is logged and left
    for a reviewer instead.
    """
    if not isinstance(rubric, list) or not rubric:
        return False
    if not all(isinstance(c, dict) for c in rubric):
        return False
    weights = [c.get("weight_percent") for c in rubric]
    if not all(isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 for w in weights):
        return False
    if all(type(w) is int for w in weights) and sum(weights) == 100:
        return False

    # Largest remainder. Not `batching.apportion`, which never hands out more
    # than the input total and so cannot scale weights summing under 100 up.
    total = float(sum(weights))
    exact = [100 * w / total for w in weights]
    scaled = [int(x) for x in exact]
    by_remainder = sorted(range(len(exact)), key=lambda i: (-(exact[i] - scaled[i]), i))
    for i in by_remainder[:100 - sum(scaled)]:
        scaled[i] += 1
    for criterion, weight in zip(rubric, scaled):
        criterion["weight_percent"] = weight
    return True


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def _clean_list(values: Optional[List[str]]) -> List[str]:
    """Stripped, non-empty values with duplicates removed, order kept."""
    out: List[str] = []
    for value in values or []:
        text = " ".join(str(value or "").split())
        if text and text.casefold() not in {v.casefold() for v in out}:
            out.append(text)
    return out


async def _content_context(request: "gen.GenerationRequest") -> Tuple[str, Dict[str, bool]]:
    """The uploaded reference content as one prompt section, and its availability."""
    if not (request.extra_files or request.transcript_files):
        return "", {}
    transcripts, documents, availability = await gen._collect_uploads(
        request.extra_files, request.transcript_files,
    )
    sections: List[str] = []
    if transcripts:
        sections.append("TRANSCRIPTS:\n" + "\n\n".join(transcripts))
    if documents:
        sections.append("DOCUMENTS:\n" + "\n\n".join(documents))
    return "\n\n".join(sections), availability


async def _run_scenario_batches(
    batches: List[Batch],
    *,
    digest: List[Dict[str, str]],
    inputs: Dict[str, Any],
    job_id: str,
    label: str = "",
) -> Tuple[List[Any], List[Any]]:
    """
    Generate `batches` one after another, each shown the questions already
    generated. Stops at the first batch that fails after its retries, as the
    standard path does. `Batch.blooms_by_type` carries each batch's slice of the
    domain assignment — `plan_batches` slices any per-type list the same way.
    """
    payloads: List[Any] = []
    usages: List[Any] = []

    for batch in batches:
        prompt = build_scenario_prompt(
            inputs,
            type_counts=batch.type_counts,
            domains_by_type=batch.blooms_by_type,
            previously_generated=format_scenario_previously_generated(digest),
        )
        try:
            payload, usage = await gen._generate_question_batch(
                prompt=prompt, schema=SCENARIO_SCHEMA, batch=batch, job_id=job_id,
                llm_kwargs={"use_kcm_cache": False},
            )
        except Exception as exc:  # noqa: BLE001 — already retried inside
            logger.error(
                f"[{job_id}] {label}{batch.describe()} failed after retries — keeping "
                f"the {len(payloads)} batch(es) already generated | {exc}"
            )
            break

        payloads.append(payload)
        usages.append(usage)
        digest.extend(summarize_for_dedup(payload))
        logger.info(f"[{job_id}] {label}{batch.describe()} complete | {len(digest)} questions so far")

    return payloads, usages


async def _reconcile(
    pool: Buckets,
    wanted: Dict[str, int],
    domains_by_type: Dict[str, List[str]],
    inputs: Dict[str, Any],
    job_id: str,
) -> Tuple[Buckets, List[Any]]:
    """
    Bring the generated questions to the requested per-type counts: trim any
    extras, then generate what is missing in up to TOPUP_MAX_ROUNDS rounds,
    each asked for the domains the kept questions have not used.
    """
    if count_by_type(pool) == wanted:
        return pool, []

    logger.warning(
        f"[{job_id}] Generated counts {count_by_type(pool)} differ from the "
        f"requested {wanted} — reconciling"
    )
    kept, dropped = trim_to_counts(pool, wanted, layout=SCENARIO_BUCKETS)
    if dropped:
        logger.info(f"[{job_id}] Trimmed extras by type: {dropped}")

    usages: List[Any] = []
    for round_no in range(1, max(0, TOPUP_MAX_ROUNDS) + 1):
        missing = shortfall(wanted, kept)
        if not missing:
            break
        domains = remaining_domains(domains_by_type, kept, missing)
        logger.warning(
            f"[{job_id}] Top-up round {round_no}/{TOPUP_MAX_ROUNDS} | missing {missing}"
            f" | domains {domains}"
        )
        batches = plan_batches(missing, domains, batch_size=SCENARIO_BATCH_SIZE)
        payloads, round_usages = await _run_scenario_batches(
            batches,
            digest=summarize_for_dedup({"questions": kept}),
            inputs=inputs,
            job_id=job_id,
            label=f"top-up {round_no} ",
        )
        usages.extend(round_usages)
        for payload in payloads:
            for bucket, questions in extract_buckets(payload, layout=SCENARIO_BUCKETS).items():
                for question in questions:
                    question["question_id"] = new_question_id()
                kept[bucket].extend(questions)
        kept, _ = trim_to_counts(kept, wanted, layout=SCENARIO_BUCKETS)

    missing = shortfall(wanted, kept)
    if missing:
        logger.error(
            f"[{job_id}] Still short of the request after top-up — missing {missing}; "
            f"returning {sum(len(v) for v in kept.values())}/{sum(wanted.values())} questions"
        )
    else:
        logger.info(f"[{job_id}] Counts reconciled to the request {wanted}")
    return kept, usages


def _finalise_questions(
    questions: Buckets,
    *,
    domains: List[str],
    difficulty_level: str,
    job_id: str,
) -> None:
    """
    The fixes applied to every generated question, and the log of anything
    left for a reviewer.

      * `difficulty_level` is set to the request's on every question — a
        scenario assessment has one difficulty, so it is not asked of the model.
      * Rubric weights that do not add up to 100 are rescaled.
      * Every other problem the validation gate would report is logged and the
        question kept as generated. `provenance` is not set until the worker
        normalizes the assessment, so that rule is skipped here.
    """
    known_domains = {_norm(d) for d in domains}
    for bucket, bucket_questions in questions.items():
        for question in bucket_questions:
            question["difficulty_level"] = difficulty_level
            if bucket in RUBRIC_BUCKETS and rebalance_rubric_weights(question.get("rubric")):
                logger.info(
                    f"[{job_id}] Rubric weights rescaled to 100 | question={question.get('question_id')}"
                )
            if _norm(question.get("scenario_domain")) not in known_domains:
                logger.warning(
                    f"[{job_id}] Question outside the requested domains | "
                    f"question={question.get('question_id')} | domain={question.get('scenario_domain')!r}"
                )
            issues = [
                e["code"] for e in validate_scenario_question(bucket, question)
                if e["code"] != "provenance_invalid"
            ]
            if issues:
                logger.warning(
                    f"[{job_id}] Generated question has validation issues, kept for review | "
                    f"question={question.get('question_id')} | type={TYPE_KEY_BY_BUCKET.get(bucket)} | "
                    f"issues={issues}"
                )


def build_scenario_blueprint(
    questions: Buckets,
    *,
    assessment_topic: str,
    target_audience: str,
    domains: List[str],
    difficulty_level: str,
    input_language: str,
    time_limit: Optional[int],
    has_reference_content: bool,
) -> Dict[str, Any]:
    """The blueprint, computed entirely from the request and the questions kept."""
    all_questions = [q for qs in questions.values() for q in qs]
    produced = len(all_questions)

    domain_counts: Dict[str, int] = {domain: 0 for domain in domains}
    for question in all_questions:
        domain = str(question.get("scenario_domain") or "").strip() or "Unassigned"
        match = next((d for d in domain_counts if _norm(d) == _norm(domain)), domain)
        domain_counts[match] = domain_counts.get(match, 0) + 1

    difficulty = Counter(
        str(q.get("difficulty_level") or "").strip() for q in all_questions
        if str(q.get("difficulty_level") or "").strip()
    )

    source = ("the uploaded reference documents" if has_reference_content
              else "the topic and scenario domains (no reference documents)")
    limit = int(time_limit) if time_limit else 0
    if limit > 0 and produced > 0:
        timing = (f"{limit} minute(s) for {produced} question(s) is "
                  f"{round(limit / produced, 2)} minute(s) per question at "
                  f"{difficulty_level} difficulty.")
    else:
        timing = (f"No time limit was configured for this assessment ({produced} "
                  f"question(s), {difficulty_level} difficulty); standard pacing applies.")

    return {
        "assessment_scope_summary": (
            f"Scenario assessment of {produced} question(s) on {assessment_topic} for "
            f"{target_audience}, at {difficulty_level} difficulty, authored in "
            f"{input_language}, drawn from {source}."
        ),
        "assessment_topic": assessment_topic,
        "target_audience": target_audience,
        "scenario_domains": list(domains),
        "domain_distribution": domain_counts,
        "question_type_distribution": {
            TYPE_KEY_BY_BUCKET[bucket]: len(questions.get(bucket) or [])
            for bucket in SCENARIO_BUCKETS if questions.get(bucket)
        },
        "difficulty_distribution": ", ".join(f"{k}: {v}" for k, v in difficulty.items()),
        "time_appropriateness_validation": timing,
        "prompt_version": SCENARIO_PROMPT_VERSION,
        "api_version": "api/v1",
    }


async def generate_scenario_assessment(
    request: "gen.GenerationRequest",
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Generate a scenario assessment. Returns (metadata, assessment, usage), the
    same shape `generator.generate_assessment` returns.
    """
    job_id = "scenario"
    topic = " ".join(str(request.assessment_topic or "").split())
    audience = " ".join(str(request.target_audience or "").split())
    domains = _clean_list(request.scenario_domains)
    if not topic or not audience or not domains:
        raise ValueError(
            "A scenario assessment needs an assessment topic, a target audience "
            "and at least one scenario domain."
        )

    wanted = {
        k: v for k, v in requested_counts(request.question_type_counts).items()
        if k in SCENARIO_TYPE_KEYS
    }
    if not wanted:
        raise ValueError("No scenario questions were requested — nothing to generate.")
    requested_total = sum(wanted.values())
    difficulty_level = str(request.difficulty_level or "intermediate")
    input_language = str(request.input_language or "English")
    learning_areas = _clean_list(request.topic_names)

    content_context, availability = await _content_context(request)
    metadata: Dict[str, Any] = {"courses": [], "content_availability": {}}
    if availability:
        metadata["content_availability"]["uploaded_files"] = availability

    time_to_complete: Optional[str] = None
    if request.time_limit:
        time_to_complete = f"{request.time_limit} minutes"
        if requested_total > max(1, SCENARIO_BATCH_SIZE):
            time_to_complete = gen._format_batch_time_limit(time_to_complete, requested_total)

    inputs = {
        "assessment_topic": topic,
        "scenario_domains": "; ".join(domains),
        "target_audience": audience,
        "learning_areas": (
            "; ".join(learning_areas) if learning_areas
            else "None specified — target the skills most relevant to the topic and domains."
        ),
        "difficulty_level": difficulty_level,
        "time_to_complete": time_to_complete,
        "input_language": input_language,
        "additional_instructions": request.additional_instructions,
        "content_context": content_context,
    }

    domains_by_type = assign_domains(wanted, domains)
    batches = plan_batches(wanted, domains_by_type, batch_size=SCENARIO_BATCH_SIZE)
    logger.info(
        f"[{job_id}] Scenario generation | topic={topic!r} | {len(batches)} batch(es) | "
        f"{requested_total} questions | domains={domains}"
    )

    payloads, usages = await _run_scenario_batches(
        batches, digest=[], inputs=inputs, job_id=job_id,
    )
    if not payloads:
        raise RuntimeError("Every scenario question batch failed")

    pool = merge_batches(payloads, layout=SCENARIO_BUCKETS)
    try:
        pool, topup_usages = await _reconcile(pool, wanted, domains_by_type, inputs, job_id)
        usages.extend(topup_usages)
    except Exception:
        logger.exception(f"[{job_id}] Count reconciliation failed — returning the batches as generated")

    _finalise_questions(pool, domains=domains, difficulty_level=difficulty_level, job_id=job_id)

    assessment = {
        "blueprint": build_scenario_blueprint(
            pool,
            assessment_topic=topic,
            target_audience=audience,
            domains=domains,
            difficulty_level=difficulty_level,
            input_language=input_language,
            time_limit=request.time_limit,
            has_reference_content=bool(content_context),
        ),
        "questions": pool,
    }
    usage = gen._merge_usage(usages)
    logger.info(
        f"[{job_id}] Scenario generation complete | "
        f"{sum(len(v) for v in pool.values())}/{requested_total} questions | "
        f"{len(usages)} calls | total_tokens={usage.get('total_token_count', 'N/A')}"
    )
    return metadata, assessment, usage
