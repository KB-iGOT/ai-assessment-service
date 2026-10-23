"""
Client-side logic for the assessment editing workspace.

Everything in this module used to be served by the API and is now the client's
job. It lives in one file, with no Streamlit imports, so a production frontend
can port it directly rather than reverse-engineering it out of the UI.

Four things moved, and each moved for the same reason: the server had no
information the client lacked, and producing them server-side either cost a
round trip or hard-coded English into a service that generates assessments in
twelve languages.

  * `editor_list`        — the flat, position-annotated question list with
                           per-question affordance state. Derived from
                           `GET /status`, which already returns the whole
                           assessment. Replaces `GET /questions/list/{job_id}`.
  * `impact_of`          — the pre-save warnings for a pending edit. A pure
                           function of before/after, both of which the client
                           holds. Replaces `?dry_run=true`.
  * `error_text`         — human-readable copy for a validation error, built
                           from its `code` and `params`. Replaces the `message`
                           the API used to embed.
  * `announce_*`         — screen-reader / toast copy for a completed action.
                           Replaces the `announcement` field.

The one piece of this that is genuinely subtle is `classify_option_change`:
reordering a question's options renumbers them, so `correct_option_index`
changes on every reorder even though the option it points at does not. Reported
naively that reads "the correct answer will change", which is wrong, and it is
the one thing a reviewer must be able to trust. The server still makes this
distinction for the AUDIT TRAIL; the client has to make it again for the
warning, because the warning is shown before anything is sent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Vocabulary — mirrors src/assessment/questions.py
# --------------------------------------------------------------------------

BUCKET_MCQ = "Multiple Choice Question"
BUCKET_FTB = "FTB Question"
BUCKET_MTF = "MTF Question"
BUCKET_MULTICHOICE = "Multi-Choice Question"
BUCKET_TRUEFALSE = "True/False Question"

TYPE_KEY_BY_BUCKET = {
    BUCKET_MCQ: "mcq",
    BUCKET_FTB: "ftb",
    BUCKET_MTF: "mtf",
    BUCKET_MULTICHOICE: "multichoice",
    BUCKET_TRUEFALSE: "truefalse",
}

# Buckets whose answer options are editable as options. An FTB qualifies only
# in the option shape — see `is_legacy_ftb`.
OPTION_BUCKETS = (BUCKET_MCQ, BUCKET_MULTICHOICE, BUCKET_FTB)


def is_legacy_ftb(bucket: str, question: Dict[str, Any]) -> bool:
    """
    An FTB stored before prompt version 6: a free-text `correct_answer` and no
    `options`. It is edited through its text answer and can never gain options.
    Every other FTB is answered like a single-select MCQ. Mirrors
    `questions.is_legacy_ftb` on the server.
    """
    return bucket == BUCKET_FTB and not question.get("options")

# Both the floor and the ceiling apply to every save — add and edit alike —
# mirroring the server.
MIN_OPTION_COUNT = 2
MAX_OPTION_COUNT = 5

BLOOMS_LEVELS = ["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]


# --------------------------------------------------------------------------
# 1. The editor list — replaces GET /questions/list/{job_id}
# --------------------------------------------------------------------------

def editor_list(assessment_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Flat, 1-based, position-annotated view of the assessment.

    Walks `question_order` — the authoritative sequence — and annotates each
    question with its position, bucket, short type key and the affordance state
    the editor uses to disable controls rather than discovering a rule from a
    rejected save.

    Anything present in the buckets but missing from `question_order` is
    appended rather than dropped, mirroring the server's own iteration so a
    question can never vanish from the editor.
    """
    data = assessment_data or {}
    buckets = data.get("questions") or {}

    by_id: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    bucket_sequence: List[str] = []
    for bucket, questions in buckets.items():
        if not isinstance(questions, list):
            continue
        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "")
            if qid and qid not in by_id:
                by_id[qid] = (bucket, question)
                bucket_sequence.append(qid)

    order: List[str] = []
    for qid in data.get("question_order") or []:
        qid = str(qid)
        if qid in by_id and qid not in order:
            order.append(qid)
    for qid in bucket_sequence:
        if qid not in order:
            order.append(qid)

    total = len(order)
    out: List[Dict[str, Any]] = []
    for position, qid in enumerate(order, start=1):
        bucket, question = by_id[qid]
        item = dict(question)
        item["position"] = position
        item["question_bucket"] = bucket
        item["question_type_key"] = TYPE_KEY_BY_BUCKET.get(bucket, bucket)

        options = question.get("options")
        option_count = len(options) if isinstance(options, list) else None
        legacy_ftb = is_legacy_ftb(bucket, question)
        has_options = bucket in OPTION_BUCKETS and not legacy_ftb and option_count is not None
        item["legacy_ftb"] = legacy_ftb
        item["option_count"] = option_count
        # Add turns off at the ceiling, remove at the floor.
        item["can_add_option"] = bool(has_options and option_count < MAX_OPTION_COUNT)
        item["can_remove_option"] = bool(has_options and option_count > MIN_OPTION_COUNT)
        item["can_delete"] = total > 1
        out.append(item)
    return out


def correct_option_texts(question: Dict[str, Any]) -> List[str]:
    """
    The text of every correct option.

    `correct_option_index` names an option's own `index` VALUE, not its array
    position — assessments generated before prompt v4.3 may be one-based, which
    is harmless only as long as every reader matches on the value. Never index
    into `options` with it.
    """
    correct = question.get("correct_option_index")
    if correct is None:
        return []
    wanted = correct if isinstance(correct, list) else [correct]
    try:
        wanted = {int(v) for v in wanted}
    except (TypeError, ValueError):
        return []
    out = []
    for option in question.get("options") or []:
        if not isinstance(option, dict):
            continue
        try:
            if int(option.get("index")) in wanted:
                out.append(str(option.get("text") or ""))
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------
# 2. Pre-save impact warnings — replaces ?dry_run=true
# --------------------------------------------------------------------------

_FIELD_IMPACT = {
    "correct_option_index": ("answer_key_changed", "high",
                             "The correct answer for this question will change."),
    "correct_answer": ("answer_key_changed", "high",
                       "The correct answer for this question will change."),
    "pairs": ("answer_key_changed", "high",
              "The matching pairs — and therefore the answer key — will change."),
    "options": ("options_changed", "high",
                "The answer options for this question will change."),
    "question_text": ("question_text_changed", "medium", "The question text will change."),
    "matching_context": ("question_text_changed", "medium",
                         "The matching context will change."),
    "course_name": ("course_mapping_changed", "medium",
                    "The course this question is mapped to will change."),
    "blooms_level": ("blooms_level_changed", "low",
                     "The Bloom's level for this question will change."),
    "relevance_percentage": ("relevance_changed", "low",
                             "The relevance percentage for this question will change."),
}

# Used instead of the entries above when the options were only re-sequenced.
_REORDER_IMPACT = {
    "options": ("options_reordered", "medium",
                "The answer options will be shown in a new order. Their wording "
                "is unchanged."),
    "correct_option_index": ("answer_key_reindexed", "medium",
                             "The correct option itself is unchanged; its index "
                             "moves to follow the new option order."),
}

_PREFIX_IMPACT = [
    ("reasoning.competency_alignment",
     ("competency_mapping_changed", "medium",
      "The competency mapping for this question will change.")),
    ("reasoning.learning_objective_alignment",
     ("learning_outcome_changed", "medium", "The learning outcome mapping will change.")),
    ("answer_rationale", ("rationale_changed", "low", "The answer rationale will change.")),
]


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def changed_fields(before: Dict[str, Any], updates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    The diff the save will produce, in the server's own shape. A field whose
    submitted value already equals the stored one is not a change.
    """
    out = []
    for path, new_value in (updates or {}).items():
        old_value = get_path(before, path)
        if old_value != new_value:
            out.append({"field": path, "previous_value": old_value, "new_value": new_value})
    return out


def _option_texts(options: Any) -> Optional[Dict[int, str]]:
    if not isinstance(options, list):
        return None
    texts: Dict[int, str] = {}
    for i, option in enumerate(options):
        if not isinstance(option, dict):
            return None
        try:
            texts[int(option.get("index", i))] = str(option.get("text") or "")
        except (TypeError, ValueError):
            return None
    return texts if len(texts) == len(options) else None


def _answer_indexes(correct: Any) -> Optional[set]:
    if correct is None:
        return None
    values = correct if isinstance(correct, list) else [correct]
    try:
        return {int(v) for v in values}
    except (TypeError, ValueError):
        return None


def classify_option_change(changes: List[Dict[str, Any]]) -> Tuple[bool, bool]:
    """
    Tell a pure re-sequencing of the options apart from a genuine content edit.

    Returns `(options_reordered, answer_reindexed)`. This mirrors
    `validation.classify_option_change` on the server, which still makes the
    same call for the audit trail — the client needs it again because the
    warning is shown before anything is sent.
    """
    change = next((c for c in changes if c["field"] == "options"), None)
    if change is None:
        return False, False

    previous = _option_texts(change["previous_value"])
    current = _option_texts(change["new_value"])
    if previous is None or current is None:
        return False, False
    # An added, removed or reworded option is a content change, not a reorder.
    if sorted(previous.values()) != sorted(current.values()):
        return False, False
    # Same texts already in the same index sequence — renumbering only.
    if ([previous[i] for i in sorted(previous)] == [current[i] for i in sorted(current)]):
        return False, False

    answer = next((c for c in changes if c["field"] == "correct_option_index"), None)
    if answer is None:
        return True, False

    before_key = _answer_indexes(answer["previous_value"])
    after_key = _answer_indexes(answer["new_value"])
    if (before_key is None or after_key is None
            or len(before_key) != len(after_key)
            or not before_key <= set(previous) or not after_key <= set(current)):
        return True, False

    reindexed = (sorted(previous[i] for i in before_key)
                 == sorted(current[i] for i in after_key))
    return True, reindexed


def impact_of(before: Dict[str, Any], updates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Warnings describing what a pending edit will do, each with a `severity` of
    high / medium / low / info. Returns [] when nothing would change.

    This is the client-side replacement for `?dry_run=true`, and needs no
    network call: `before` is what was rendered and `updates` is what the user
    typed.
    """
    changes = changed_fields(before, updates)
    if not changes:
        return []

    alerts: List[Dict[str, Any]] = []
    emitted: set = set()
    options_reordered, answer_reindexed = classify_option_change(changes)

    for change in changes:
        path = change["field"]
        label = _REORDER_IMPACT.get(path) if (
            (path == "options" and options_reordered)
            or (path == "correct_option_index" and answer_reindexed)
        ) else _FIELD_IMPACT.get(path)
        if label is None:
            for prefix, prefix_label in _PREFIX_IMPACT:
                if path == prefix or path.startswith(prefix + "."):
                    label = prefix_label
                    break
        if label is None:
            continue
        code, severity, message = label
        if code in emitted:
            continue
        emitted.add(code)
        alerts.append({"code": code, "severity": severity, "message": message,
                       "field": path})

    if before.get("provenance") == "ai_generated":
        alerts.append({
            "code": "provenance_will_change", "severity": "info",
            "message": "This AI-generated question will be recorded as AI-assisted "
                       "once saved."})

    alerts.append({
        "code": "authoritative_version", "severity": "info",
        "message": "Saving replaces the authoritative assessment version used for "
                   "all downloads and publication."})
    return alerts


def delete_impact(question: Dict[str, Any], remaining: int) -> List[Dict[str, Any]]:
    """Warnings for a pending deletion."""
    alerts = [{
        "code": "question_will_be_deleted", "severity": "high",
        "message": "This question will be permanently removed from the assessment.",
        "question_id": question.get("question_id"),
    }]
    if remaining <= 0:
        alerts.append({
            "code": "last_question", "severity": "high",
            "message": "This is the only question in the assessment and cannot be "
                       "deleted."})
    return alerts


# --------------------------------------------------------------------------
# 3. Error copy — replaces the `message` field on validation errors
# --------------------------------------------------------------------------
# A production frontend replaces this dict with its own i18n string table,
# keyed by the same codes. The API supplies `code` + `params`; the wording and
# the language are the client's.

def _fmt_list(values: Any, limit: int = 8) -> str:
    items = [str(v) for v in (values or [])]
    if len(items) > limit:
        return ", ".join(items[:limit]) + f", … (+{len(items) - limit} more)"
    return ", ".join(items)


def error_text(error: Dict[str, Any]) -> str:
    """Render one validation error into a sentence, from its code and params."""
    code = error.get("code", "")
    p = error.get("params") or {}
    field = error.get("field")

    if code == "option_count_invalid":
        low, high, found = p.get("minimum"), p.get("maximum"), p.get("found")
        expected = (f"at least {low}" if high is None
                    else f"exactly {high}" if high == low
                    else f"between {low} and {high}")
        return f"This question type must have {expected} options (found {found})."
    if code == "option_text_required":
        return f"Option {p.get('option_position')} text cannot be empty."
    if code == "option_malformed":
        return f"Option {p.get('option_position')} must be an object with text and index."
    if code == "option_index_invalid":
        return f"Option {p.get('option_position')} requires an integer index."
    if code == "option_index_duplicate":
        return "Option indexes must be unique."
    if code == "options_required":
        return "This question requires a list of answer options."
    if code == "ftb_blank_count_invalid":
        return (f"A fill-in-the-blank question must contain exactly "
                f"{p.get('expected')} blank (___) (found {p.get('found')}).")
    if code == "ftb_format_change_not_allowed":
        return ("A fill-in-the-blank question cannot switch between the text-answer "
                "and the options format.")
    if code == "correct_option_index_required":
        return ("A multiple-select question requires at least one correct option."
                if p.get("expects") == "index_array"
                else "A correct option must be selected.")
    if code == "correct_option_index_invalid":
        return ("Correct option indexes must be integers."
                if p.get("expects") == "index_array"
                else "A single-select MCQ requires exactly one correct option index.")
    if code == "correct_option_index_out_of_range":
        return (f"The correct option index does not match any option "
                f"(got {p.get('found')}; valid: {_fmt_list(p.get('valid_indexes'))}).")
    if code == "correct_option_index_duplicate":
        return "Correct option indexes must not repeat."
    if code == "correct_answer_required":
        return "This question type requires a correct answer."
    if code == "correct_answer_invalid":
        return f"The correct answer must be one of: {_fmt_list(p.get('allowed'))}."
    if code == "question_text_required":
        return "Question text cannot be empty."
    if code == "matching_context_required":
        return "Match-the-Following questions require a matching context."
    if code == "rationale_required":
        return "An answer rationale (correct answer explanation) is required."
    if code == "blooms_level_required":
        return "A Bloom's level is required while Bloom's taxonomy is enabled."
    if code == "blooms_level_invalid":
        return f"Bloom's level must be one of: {_fmt_list(p.get('allowed'))}."
    if code == "relevance_required":
        return "Relevance percentage is required."
    if code == "relevance_invalid":
        return (f"Relevance percentage must be an integer between "
                f"{p.get('minimum')} and {p.get('maximum')}.")
    if code == "provenance_invalid":
        return f"Provenance must be one of: {_fmt_list(p.get('allowed'))}."
    if code == "pairs_required":
        return "Pairs must be a list."
    if code == "pair_count_invalid":
        return (f"Match-the-Following requires at least {p.get('minimum')} pairs "
                f"(found {p.get('found')}).")
    if code == "pair_malformed":
        return f"Pair {p.get('pair_position')} must be an object."
    if code == "pair_left_required":
        return f"Pair {p.get('pair_position')} left value cannot be empty."
    if code == "pair_right_required":
        return f"Pair {p.get('pair_position')} right value cannot be empty."
    if code == "mapping_required":
        return f"'{field}' cannot be blank once it is mapped."
    if code == "competency_mapping_incomplete":
        return (f"A competency mapping requires all three of area, theme and "
                f"sub-theme (missing: {_fmt_list(p.get('missing'))}).")
    if code == "field_not_editable":
        return (f"'{field}' is not editable for {p.get('question_bucket')}. "
                f"Editable: {_fmt_list(p.get('editable_fields'))}.")
    if code == "question_type_invalid":
        return (f"Unknown question type '{p.get('found')}'. "
                f"Expected one of: {_fmt_list(p.get('expected'))}.")
    if code == "question_order_invalid":
        bits = []
        if p.get("missing"):
            bits.append(f"missing {_fmt_list(p['missing'])}")
        if p.get("unknown"):
            bits.append(f"unknown {_fmt_list(p['unknown'])}")
        if p.get("duplicated"):
            bits.append("duplicate ids were supplied")
        detail = f" ({'; '.join(bits)})" if bits else ""
        return f"The order must list every question exactly once{detail}."
    if code == "question_order_required":
        return "The complete new question order is required."
    if code == "question_not_found":
        return "That question is not part of this assessment."
    if code == "last_question_cannot_be_deleted":
        return ("An assessment must contain at least one question, so the last "
                "remaining question cannot be deleted.")
    if code == "assessment_empty":
        return "An assessment must contain at least one question."
    if code == "question_id_required":
        return "A question identifier is required."
    if code == "question_id_invalid":
        return "The question identifier must be a non-blank string."
    if code == "question_id_duplicate":
        return "Question identifiers must be unique."
    if code == "request_required":
        return 'The request body must be the Sunbird envelope: {"request": {...}}.'

    # Request-level failures. These reach the client in the same code+params
    # shape as a field-level one — the API stopped sending an English `detail`
    # sentence for them, for the reason this whole table exists.
    if code == "assessment_not_found":
        return "That assessment does not exist."
    if code == "assessment_access_denied":
        return "You do not own this assessment."
    if code == "assessment_not_editable":
        return (f"This assessment is {p.get('status')} and cannot be edited until "
                f"generation completes.")
    if code == "version_conflict":
        return ("This assessment was changed by another update since you loaded it. "
                "Reload it and re-apply your change.")
    if code == "if_match_invalid":
        return "If-Match must be the integer assessment version, e.g. 'If-Match: 3'."
    if code == "version_conflict":
        return ("This assessment was changed by another update since you loaded "
                f"it (it is now version {p.get('current_version')}). Reload and "
                "re-apply your change.")
    if code == "assessment_not_found":
        return "No such assessment."
    if code == "assessment_access_denied":
        return "You do not own this assessment."
    if code == "assessment_not_editable":
        return (f"This assessment is {p.get('status')} and cannot be edited "
                "until generation completes.")
    if code == "if_match_invalid":
        return "If-Match must be the integer assessment version, e.g. 'If-Match: 3'."

    # Unknown code — show it rather than swallowing it, so a new server-side
    # rule is visible instead of silently rendering as an empty message.
    return f"{code}{(' — ' + str(p)) if p else ''}"


# --------------------------------------------------------------------------
# 4. Action announcements — replaces the `announcement` field
# --------------------------------------------------------------------------
# Plain text for an ARIA live region or a toast. Composed from the write
# response, in the user's language.

# Outcome codes a successful write returns in `code`, mapped to copy. The API
# used to return the sentence itself in `message`; it now returns the token and
# leaves the wording here, the same rule the error table follows.
OUTCOME_TEXT = {
    "saved": "Saved successfully.",
    "no_changes": "No changes to save.",
    "order_unchanged": "Question order unchanged.",
}


def outcome_text(response: Dict[str, Any]) -> str:
    """Copy for a 2xx write response, from its `code`."""
    code = response.get("code", "")
    return OUTCOME_TEXT.get(code, code)


def announce_saved(response: Dict[str, Any]) -> str:
    return f"Saved. Version {response.get('version')}."


def announce_added(position: int, total: int) -> str:
    return f"Question added at position {position} of {total}."


def announce_deleted(position: Optional[int], remaining: int) -> str:
    where = f" from position {position}" if position else ""
    return f"Question removed{where}. {remaining} question(s) remain."


def announce_reordered(question_id: str, old_position: int, new_position: int,
                       total: int) -> str:
    return (f"Question moved from position {old_position} to position "
            f"{new_position} of {total}.")


def announce_order_updated(moved_count: int, total: int) -> str:
    return f"Question order updated. {moved_count} of {total} questions changed position."
