"""
Count validation and repair for one generation response.

Every LLM call asks for exact per-type counts, and the model does not always
honour them. It can return fewer (occasionally one per type), more, or a type
it was told not to generate. Everything here turns such a response into the
most usable set of questions it contains:

  * `extract_buckets`   — the questions per canonical bucket, whatever the
                          model named the buckets
  * `trim_to_counts`    — drop anything beyond the requested counts, and any
                          type that was not requested, choosing which extras
                          go by the Bloom's and course distributions
  * `shortfall`         — what is still missing

The remaining helpers work out what a top-up call for the missing questions
should ask for: the Bloom's levels and course quantities not yet used.

Everything here is pure — no I/O, no LLM, no DB.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .batching import _unwrap_questions, apportion
from .questions import BUCKET_BY_TYPE_KEY, CANONICAL_BUCKETS, TYPE_KEY_BY_BUCKET, resolve_bucket

Buckets = Dict[str, List[Dict[str, Any]]]


def requested_counts(type_counts: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """The positive counts of the known question types, as ints."""
    out: Dict[str, int] = {}
    for type_key, count in (type_counts or {}).items():
        if type_key in BUCKET_BY_TYPE_KEY and int(count or 0) > 0:
            out[type_key] = int(count)
    return out


def extract_buckets(result: Any) -> Buckets:
    """
    The questions in a response, per canonical bucket, in the order returned.

    Bucket names are resolved the same way `merge_batches` resolves them. A name
    that resolves to no known type is left out: nothing downstream asked for it.
    """
    buckets: Buckets = {bucket: [] for bucket in CANONICAL_BUCKETS}
    for name, questions in _unwrap_questions(result).items():
        if not isinstance(questions, list):
            continue
        bucket = name if name in buckets else resolve_bucket(name)
        if bucket not in buckets:
            continue
        buckets[bucket].extend(q for q in questions if isinstance(q, dict))
    return buckets


def count_by_type(buckets: Buckets) -> Dict[str, int]:
    return {
        TYPE_KEY_BY_BUCKET[bucket]: len(questions)
        for bucket, questions in buckets.items()
        if bucket in TYPE_KEY_BY_BUCKET and questions
    }


def _norm(value: Any) -> str:
    """Case- and whitespace-insensitive form of a label, for matching only."""
    return " ".join(str(value or "").split()).casefold()


def _course_ids_by_name(aggregated_metadata: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Normalised course name -> course identifier, from the course metadata."""
    id_by_name: Dict[str, str] = {}
    for course in (aggregated_metadata or {}).get("courses", []) or []:
        if isinstance(course, dict) and course.get("name") and course.get("identifier"):
            id_by_name[_norm(course["name"])] = str(course["identifier"])
    return id_by_name


def _over_assignment(questions: List[Dict[str, Any]], levels: Optional[List[str]]) -> set:
    """
    Positions of the questions whose Bloom's level the type's assignment has no
    room left for, walking in order — so of two questions on a level assigned
    once, the later one is the surplus.
    """
    if not levels:
        return set()
    room = Counter(_norm(level) for level in levels)
    over = set()
    for i, question in enumerate(questions):
        level = _norm(question.get("blooms_level"))
        if room.get(level, 0) > 0:
            room[level] -= 1
        else:
            over.add(i)
    return over


def _over_quota(
    buckets: Buckets,
    course_counts: Optional[Dict[str, int]],
    aggregated_metadata: Optional[Dict[str, Any]],
) -> set:
    """
    `id()` of every question whose course is already at its quota, or is not one
    of the weighted courses, walking all buckets in order.
    """
    if not course_counts:
        return set()
    id_by_name = _course_ids_by_name(aggregated_metadata)
    used: Counter = Counter()
    over = set()
    for questions in buckets.values():
        for question in questions:
            cid = id_by_name.get(_norm(question.get("course_name")))
            if cid in course_counts and used[cid] < int(course_counts[cid]):
                used[cid] += 1
            else:
                over.add(id(question))
    return over


def trim_to_counts(
    buckets: Buckets,
    type_counts: Dict[str, int],
    *,
    blooms_by_type: Optional[Dict[str, List[str]]] = None,
    course_counts: Optional[Dict[str, int]] = None,
    aggregated_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Buckets, Dict[str, int]]:
    """
    Keep at most the requested number of each type, and nothing of a type that
    was not requested.

    Questions are dropped only from a type that is over its count, and only as
    many as it is over by — a Bloom's level or course that does not match is
    never on its own a reason to drop. Which of the type's questions go is
    decided by the distributions, in this order:

      1. a Bloom's level the type's assignment has no room left for
      2. a course already at its quota, or not one of the weighted courses
      3. the latest returned

    The kept questions stay in the order they were returned.

    Returns the trimmed buckets and how many questions were dropped per type.
    """
    wanted = requested_counts(type_counts)
    # Unrequested types are dropped outright, so they must not use up quota.
    over_course = _over_quota(
        {b: q for b, q in buckets.items() if TYPE_KEY_BY_BUCKET.get(b) in wanted},
        course_counts, aggregated_metadata,
    )
    kept: Buckets = {bucket: [] for bucket in CANONICAL_BUCKETS}
    dropped: Dict[str, int] = {}
    for bucket, questions in buckets.items():
        type_key = TYPE_KEY_BY_BUCKET.get(bucket)
        limit = wanted.get(type_key, 0) if type_key else 0
        excess = len(questions) - limit
        if excess <= 0:
            kept[bucket] = list(questions)
            continue
        dropped[type_key or bucket] = excess
        if limit <= 0:
            continue
        over_level = _over_assignment(questions, (blooms_by_type or {}).get(type_key))
        ranked = sorted(
            range(len(questions)),
            key=lambda i: (i in over_level, id(questions[i]) in over_course, i),
            reverse=True,
        )
        drop = set(ranked[:excess])
        kept[bucket] = [q for i, q in enumerate(questions) if i not in drop]
    return kept, dropped


def shortfall(type_counts: Dict[str, int], buckets: Buckets) -> Dict[str, int]:
    """Per type, how many more questions are needed to meet the request."""
    have = count_by_type(buckets)
    return {
        type_key: count - have.get(type_key, 0)
        for type_key, count in requested_counts(type_counts).items()
        if count > have.get(type_key, 0)
    }


def remaining_blooms(
    blooms_by_type: Optional[Dict[str, List[str]]],
    kept: Buckets,
    missing: Dict[str, int],
) -> Dict[str, List[str]]:
    """
    The Bloom's levels a top-up should assign, per missing type.

    The kept questions are matched to the assignment by their `blooms_level`
    label rather than by position, because a short response is not guaranteed to
    be the leading run of the assignment. Whatever is left unmatched is what the
    missing questions still owe, truncated to the number missing.
    """
    out: Dict[str, List[str]] = {}
    for type_key, count in missing.items():
        levels = list((blooms_by_type or {}).get(type_key) or [])
        if not levels:
            continue
        used = Counter(
            _norm(q.get("blooms_level"))
            for q in kept.get(BUCKET_BY_TYPE_KEY[type_key], [])
        )
        left: List[str] = []
        for level in levels:
            if used.get(_norm(level), 0) > 0:
                used[_norm(level)] -= 1
            else:
                left.append(level)
        if left[:count]:
            out[type_key] = left[:count]
    return out


def remaining_course_counts(
    course_counts: Optional[Dict[str, int]],
    kept: Buckets,
    missing_total: int,
    aggregated_metadata: Optional[Dict[str, Any]],
) -> Dict[str, int]:
    """
    The per-course quantities a top-up should ask for, summing to `missing_total`.

    `course_counts` is keyed by course identifier, but a question carries only
    `course_name`, so the kept questions are mapped back to identifiers through
    the course metadata. If the kept questions cannot account for the quota —
    unknown names, or already over quota — the missing total is spread across the
    original quota instead, which still sums correctly.
    """
    if not course_counts or missing_total <= 0:
        return {}
    order = list(course_counts)

    id_by_name = _course_ids_by_name(aggregated_metadata)

    used: Counter = Counter()
    for questions in kept.values():
        for q in questions:
            cid = id_by_name.get(_norm(q.get("course_name")))
            if cid:
                used[cid] += 1

    remaining = {cid: max(0, int(count) - used.get(cid, 0)) for cid, count in course_counts.items()}
    if sum(remaining.values()) >= missing_total:
        return apportion(missing_total, remaining, order)
    return apportion(missing_total, dict(course_counts), order)
