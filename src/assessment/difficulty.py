"""
Difficulty allocation for scenario assessments.

A scenario assessment can mix difficulty levels. Every question slot is given
its level in Python, before the first call, the same way it is given its
scenario domain — so a batch can hold questions at several levels, and the
whole assessment matches the request by construction rather than by asking the
model to balance it.

What a request can say, all optional and combinable:

  * **levels** — one or more levels. With no counts, the questions are split
    evenly across them.
  * **overall counts** — `{"beginner": 4, "advanced": 6}`: how many questions at
    each level across the whole assessment. They must add up to the total.
  * **per-type counts** — `{"sjt_ranking": {"advanced": 2, "beginner": 1}}`: the
    exact split for one question type. They must add up to that type's count.
    Types left out are allocated from the levels or overall counts.

Overall and per-type counts arrive together in one `distribution` mapping: a
level key is an overall count, a question-type key is a per-type split.

The split is decided in two steps, in priority order:

  1. **Across the whole assessment.** Each level's total is fixed first — the
     overall counts, or an even split of the total over the levels, with any
     remainder going to the earlier levels in `DIFFICULTY_LEVELS` order (10
     questions over three levels is 4/3/3).
  2. **Within each type.** Those totals are then shared out across the types
     in proportion to each type's count, each type's share of a level being
     its exact proportion rounded down or up — never further off — while the
     level totals from step 1 are kept exactly.

`order_levels` then lines the levels up with the type's domain slots, so a
domain is not always paired with the same level.

Everything here is pure — no I/O, no LLM, no DB.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .questions import SCENARIO_TYPE_KEYS

DIFFICULTY_LEVELS: tuple = ("beginner", "intermediate", "advanced")

# {type_key: {level: count}} — every requested type, levels with a zero count
# left out, both in canonical order.
DifficultyPlan = Dict[str, Dict[str, int]]


def normalise_level(value: Any) -> str:
    """A level in its canonical lower-case form. Raises ValueError if unknown."""
    level = " ".join(str(value or "").split()).casefold()
    if level not in DIFFICULTY_LEVELS:
        raise ValueError(
            f"Unknown difficulty level {value!r} — use {', '.join(DIFFICULTY_LEVELS)}."
        )
    return level


def normalise_levels(values: Optional[Iterable[Any]]) -> List[str]:
    """Distinct levels in canonical order, blanks ignored."""
    chosen = {normalise_level(v) for v in values or [] if str(v or "").strip()}
    return [level for level in DIFFICULTY_LEVELS if level in chosen]


def _count(value: Any, where: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{where} must be a non-negative whole number, got {value!r}.")
    return value


def _level_counts(raw: Any, where: str) -> Dict[str, int]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} must map difficulty levels to question counts.")
    out: Dict[str, int] = {}
    for key, value in raw.items():
        level = normalise_level(key)
        out[level] = out.get(level, 0) + _count(value, f"{where} {level}")
    return out


def split_distribution(
    distribution: Optional[Mapping[str, Any]],
) -> tuple:
    """
    `(overall, per_type)` from a distribution mapping: level keys are overall
    counts, question-type keys are per-type splits. Raises ValueError on any
    other key or value.
    """
    overall: Dict[str, int] = {}
    per_type: Dict[str, Dict[str, int]] = {}
    if distribution is None:
        return overall, per_type
    if not isinstance(distribution, Mapping):
        raise ValueError("difficulty_distribution must be a JSON object.")
    for key, value in distribution.items():
        name = " ".join(str(key or "").split()).casefold()
        if name in DIFFICULTY_LEVELS:
            overall[name] = overall.get(name, 0) + _count(value, f"difficulty_distribution {name}")
        elif name in SCENARIO_TYPE_KEYS:
            per_type[name] = _level_counts(value, f"difficulty_distribution {name}")
        else:
            raise ValueError(
                f"difficulty_distribution key {key!r} is neither a difficulty level "
                f"({', '.join(DIFFICULTY_LEVELS)}) nor a scenario question type."
            )
    return overall, per_type


def even_split(total: int, levels: List[str]) -> Dict[str, int]:
    """`total` over `levels` as evenly as possible, extras to the earlier levels."""
    if not levels or total <= 0:
        return {}
    base, extra = divmod(int(total), len(levels))
    return {level: base + (1 if i < extra else 0) for i, level in enumerate(levels)}


def _round_table(
    rows: Dict[str, int],
    cols: Dict[str, int],
    row_order: List[str],
    col_order: List[str],
) -> Dict[str, Dict[str, int]]:
    """
    An integer table with exactly these row and column totals (which must sum
    to the same amount), each cell its proportional share
    `row * col / total` rounded down or up.

    Every cell starts at its share rounded down; the units still owed are then
    placed one at a time on cells with a fractional part, largest fraction
    first, re-routing earlier placements along an alternating path when a
    column is full. A placement that meets every total always exists — the
    fractional parts are one — so this never fails on valid totals.
    """
    total = sum(rows.values())
    table = {r: {c: 0 for c in col_order} for r in row_order}
    if total <= 0:
        return table

    exact = {(r, c): Fraction(rows[r] * cols[c], total) for r in row_order for c in col_order}
    for (r, c), share in exact.items():
        table[r][c] = int(share)

    row_need = {r: rows[r] - sum(table[r].values()) for r in row_order}
    col_need = {c: cols[c] - sum(table[r][c] for r in row_order) for c in col_order}
    options = {
        r: sorted(
            (c for c in col_order if exact[(r, c)] != table[r][c]),
            key=lambda c, r=r: (-(exact[(r, c)] - table[r][c]), col_order.index(c)),
        )
        for r in row_order
    }
    rounded_up: Dict[str, List[str]] = {c: [] for c in col_order}

    def place(r: str, seen: set) -> bool:
        for c in options[r]:
            if c in seen or r in rounded_up[c]:
                continue
            seen.add(c)
            if len(rounded_up[c]) < col_need[c]:
                rounded_up[c].append(r)
                return True
            for other in list(rounded_up[c]):
                if place(other, seen):
                    rounded_up[c].remove(other)
                    rounded_up[c].append(r)
                    return True
        return False

    progress = True
    while progress and any(row_need.values()):
        progress = False
        for r in row_order:
            while row_need[r] > 0 and place(r, set()):
                row_need[r] -= 1
                progress = True
    if any(row_need.values()):  # pragma: no cover — unreachable for valid totals
        raise RuntimeError("Difficulty allocation could not meet its totals.")

    for c, up in rounded_up.items():
        for r in up:
            table[r][c] += 1
    return table


def plan_difficulty(
    type_counts: Mapping[str, int],
    *,
    levels: Optional[Iterable[Any]] = None,
    distribution: Optional[Mapping[str, Any]] = None,
) -> DifficultyPlan:
    """
    How many questions of each type are written at each level.

    `type_counts` is the requested count per scenario type. `levels` and
    `distribution` are described in the module docstring; overall counts in
    `distribution` take precedence over `levels`.

    Raises ValueError when the request cannot be met: an unknown level or key,
    counts that do not add up, a per-type split for a type not requested, or
    no level at all for some questions.
    """
    wanted = {t: int(type_counts.get(t) or 0) for t in SCENARIO_TYPE_KEYS
              if int(type_counts.get(t) or 0) > 0}
    total = sum(wanted.values())
    chosen = normalise_levels(levels)
    overall, per_type = split_distribution(distribution)

    fixed: DifficultyPlan = {}
    for type_key, counts in per_type.items():
        assigned = sum(counts.values())
        if assigned != wanted.get(type_key, 0):
            raise ValueError(
                f"difficulty_distribution {type_key} adds up to {assigned}, but "
                f"{wanted.get(type_key, 0)} {type_key} question(s) were requested."
            )
        if assigned:
            fixed[type_key] = counts
    fixed_by_level: Counter = Counter()
    for counts in fixed.values():
        fixed_by_level.update(counts)

    free = {t: n for t, n in wanted.items() if t not in fixed}
    free_total = sum(free.values())

    if overall:
        if sum(overall.values()) != total:
            raise ValueError(
                f"difficulty_distribution levels add up to {sum(overall.values())}, "
                f"but {total} question(s) were requested."
            )
        targets = {}
        for level in DIFFICULTY_LEVELS:
            left = overall.get(level, 0) - fixed_by_level.get(level, 0)
            if left < 0:
                raise ValueError(
                    f"The per-type difficulty counts use {fixed_by_level[level]} {level} "
                    f"question(s), more than the {overall.get(level, 0)} asked for overall."
                )
            if left:
                targets[level] = left
    elif free_total:
        if not chosen:
            raise ValueError("No difficulty level was given for the questions.")
        # Even across the whole assessment first; the per-type splits already
        # fixed use some of each level, and the free types make up the rest.
        desired = even_split(total, chosen)
        targets = {level: max(0, desired[level] - fixed_by_level.get(level, 0)) for level in chosen}
        short = free_total - sum(targets.values())
        if short > 0:
            for level, extra in even_split(short, chosen).items():
                targets[level] += extra
        elif short < 0:
            targets = _shrink(targets, free_total, chosen)
        targets = {level: n for level, n in targets.items() if n}
    else:
        targets = {}

    free_order = [t for t in SCENARIO_TYPE_KEYS if t in free]
    level_order = [level for level in DIFFICULTY_LEVELS if level in targets]
    table = _round_table(free, targets, free_order, level_order) if free_total else {}

    plan: DifficultyPlan = {}
    for type_key in SCENARIO_TYPE_KEYS:
        if type_key not in wanted:
            continue
        counts = fixed.get(type_key) or table.get(type_key) or {}
        plan[type_key] = {level: counts[level] for level in DIFFICULTY_LEVELS if counts.get(level)}
    return plan


def _shrink(targets: Dict[str, int], amount: int, order: List[str]) -> Dict[str, int]:
    """`targets` scaled down to sum to `amount`, keeping proportions."""
    out = {level: 0 for level in targets}
    pool = sum(targets.values())
    exact = {level: Fraction(n * amount, pool) for level, n in targets.items()}
    for level, share in exact.items():
        out[level] = int(share)
    by_fraction = sorted(targets, key=lambda lv: (-(exact[lv] - out[lv]), order.index(lv)))
    for level in by_fraction[:amount - sum(out.values())]:
        out[level] += 1
    return out


def plan_levels(plan: DifficultyPlan) -> List[str]:
    """The levels a plan uses anywhere, in canonical order."""
    used = {level for counts in plan.values() for level, n in counts.items() if n}
    return [level for level in DIFFICULTY_LEVELS if level in used]


def plan_totals(plan: DifficultyPlan) -> Dict[str, int]:
    """Questions per level across the whole plan, in canonical order."""
    totals: Counter = Counter()
    for counts in plan.values():
        totals.update(counts)
    return {level: totals[level] for level in DIFFICULTY_LEVELS if totals[level]}


def describe_levels(counts: Mapping[str, int]) -> str:
    """`advanced`, or `mixed (beginner: 2, advanced: 1)`."""
    used = [(level, counts[level]) for level in DIFFICULTY_LEVELS if counts.get(level)]
    if not used:
        return "unspecified"
    if len(used) == 1:
        return used[0][0]
    return "mixed (" + ", ".join(f"{level}: {n}" for level, n in used) + ")"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def order_levels(
    plan: DifficultyPlan,
    domains_by_type: Mapping[str, List[str]],
) -> Dict[str, List[str]]:
    """
    The level of every question slot, per type, in slot order — lined up with
    `domains_by_type` position for position.

    Each slot takes, from the levels its type still has left, the one least
    often paired with the slot's domain so far across the whole assessment;
    ties go to the level with most left, then canonical order. So a domain is
    spread over the levels, and within a type the levels interleave.
    """
    pairs: Counter = Counter()
    out: Dict[str, List[str]] = {}
    for type_key in SCENARIO_TYPE_KEYS:
        left = dict((plan.get(type_key) or {}))
        count = sum(left.values())
        if count <= 0:
            continue
        domains = list(domains_by_type.get(type_key) or [])
        levels: List[str] = []
        for i in range(count):
            domain = _norm(domains[i]) if i < len(domains) else ""
            level = min(
                (lv for lv in DIFFICULTY_LEVELS if left.get(lv, 0) > 0),
                key=lambda lv: (pairs[(domain, lv)], -left[lv], DIFFICULTY_LEVELS.index(lv)),
            )
            left[level] -= 1
            pairs[(domain, level)] += 1
            levels.append(level)
        out[type_key] = levels
    return out
