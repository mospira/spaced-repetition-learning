"""Adaptive scheduling helpers for SRL.

Legacy entries only contain a history of ``date`` and ``rating`` values.  Those
entries keep their original 1-5 day due-date behavior until the next review.
Once reviewed, explicit interval and due-date metadata is stored on the entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from collections import Counter
import json


INITIAL_INTERVALS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}


def parse_stored_date(value: str) -> date:
    """Accept both legacy datetime strings and current ISO date strings."""
    return datetime.fromisoformat(value).date()


@dataclass(frozen=True)
class DueCandidate:
    name: str
    url: str
    rating: int
    attempts: int
    due_date: date
    overdue_days: int
    full_solve_required: bool = False

    @property
    def review_mode(self) -> str:
        if self.full_solve_required:
            return "full solve"
        return review_mode(self.rating)


def next_interval_days(rating: int, previous_interval: int | None = None) -> int:
    """Return the next interval using the user's 1-5 performance scale."""
    if rating not in INITIAL_INTERVALS:
        raise ValueError("rating must be between 1 and 5")

    if rating <= 2 or previous_interval is None:
        return INITIAL_INTERVALS[rating]

    if rating == 3:
        return min(21, max(7, round(previous_interval * 1.5)))
    if rating == 4:
        return min(60, max(14, round(previous_interval * 2)))
    return min(120, max(30, round(previous_interval * 2.5)))


def entry_due_date(entry: dict) -> date | None:
    """Return an entry's due date while retaining legacy schedule semantics."""
    explicit = entry.get("due_date")
    if explicit:
        return date.fromisoformat(explicit)

    history = entry.get("history", [])
    if not history:
        return None

    last = history[-1]
    if not last.get("date"):
        return None
    reviewed = parse_stored_date(last["date"])
    return reviewed + timedelta(days=int(last["rating"]))


def previous_interval_days(entry: dict) -> int | None:
    value = entry.get("interval_days")
    if value is None:
        return None
    return int(value)


def make_attempt(entry: dict, rating: int, reviewed_on: date) -> dict:
    """Build a history record and schedule metadata for a completed review."""
    old_due = entry_due_date(entry)
    recall_failed = bool(entry.get("recall_failed_since_last_submission"))
    previous_interval = None if recall_failed else previous_interval_days(entry)
    interval = next_interval_days(rating, previous_interval)
    due = reviewed_on + timedelta(days=interval)

    attempt = {
        "rating": rating,
        "date": reviewed_on.isoformat(),
        "interval_days": interval,
        "due_date": due.isoformat(),
    }

    if previous_interval is not None:
        attempt["previous_interval_days"] = previous_interval
    if recall_failed:
        attempt["mastery_blocked"] = True
    if old_due is not None:
        attempt["scheduled_due"] = old_due.isoformat()
        attempt["days_late"] = max(0, (reviewed_on - old_due).days)

    return attempt


def apply_attempt_schedule(entry: dict, attempt: dict) -> None:
    entry["interval_days"] = int(attempt["interval_days"])
    entry["due_date"] = attempt["due_date"]
    entry.pop("recall_failed_since_last_submission", None)
    entry.pop("full_solve_required", None)


def qualifies_for_mastery(history: list[dict]) -> bool:
    """Master only a rating-5 recall demonstrated after a mature interval."""
    if len(history) < 2 or int(history[-1].get("rating", 0)) != 5:
        return False

    last = history[-1]
    if last.get("mastery_blocked"):
        return False
    current_date = parse_stored_date(last["date"])
    previous_date = parse_stored_date(history[-2]["date"])
    actual_gap = (current_date - previous_date).days
    completed_interval = last.get("previous_interval_days")
    if completed_interval is not None:
        return int(completed_interval) >= 30 and actual_gap >= 30

    # Legacy history has no interval metadata.  Its actual review gap is the
    # best evidence available and avoids rewriting old records.
    return actual_gap >= 30


def review_mode(rating: int) -> str:
    if rating == 1:
        return "full solve"
    if rating == 2:
        return "full solve: target weak spot"
    if rating == 3:
        return "full solve: plan first"
    return "quick recall"


def due_candidates(data: dict, on_date: date) -> list[DueCandidate]:
    candidates = []
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        history = entry.get("history", [])
        due = entry_due_date(entry)
        if not history or due is None or due > on_date:
            continue

        candidates.append(
            DueCandidate(
                name=name,
                url=entry.get("url", ""),
                rating=int(history[-1]["rating"]),
                attempts=len(history),
                due_date=due,
                overdue_days=(on_date - due).days,
                full_solve_required=bool(entry.get("full_solve_required")),
            )
        )
    return candidates


def _oldest_first(candidate: DueCandidate):
    return (candidate.due_date, candidate.rating)


def select_balanced_reviews(
    candidates: list[DueCandidate], limit: int | None
) -> list[DueCandidate]:
    """Return a stable weak/mixed/near-mastery rotation.

    The order is independent of ``limit`` so the numbers shown by ``list`` are
    also safe to use with ``take`` and ``add -n``.
    """
    if not candidates:
        return []

    weak = sorted((c for c in candidates if c.rating <= 2), key=_oldest_first)
    mixed = sorted((c for c in candidates if c.rating in (3, 4)), key=_oldest_first)
    mastery = sorted((c for c in candidates if c.rating == 5), key=_oldest_first)

    queues = {"weak": weak, "mixed": mixed, "mastery": mastery}
    pattern = (
        "weak",
        "mixed",
        "mastery",
        "weak",
        "mixed",
        "weak",
        "mixed",
        "mastery",
    )
    selected = []

    while any(queues.values()):
        added_this_round = False
        for bucket in pattern:
            if queues[bucket]:
                selected.append(queues[bucket].pop(0))
                added_this_round = True
        if not added_this_round:
            break

    return selected if limit is None else selected[:limit]


def new_problem_allowance(overdue_count: int, config) -> int:
    if overdue_count > config.overdue_pause_new_threshold:
        return 0
    if overdue_count > config.overdue_reduce_new_threshold:
        return min(1, config.new_problem_limit)
    return config.new_problem_limit


def merge_histories(*histories: list[dict]) -> list[dict]:
    """Merge histories using the maximum occurrence count of each attempt."""
    max_counts = Counter()
    attempts_by_key = {}
    for history in histories:
        counts = Counter()
        for attempt in history:
            key = json.dumps(attempt, sort_keys=True, separators=(",", ":"))
            counts[key] += 1
            attempts_by_key[key] = dict(attempt)
        for key, count in counts.items():
            max_counts[key] = max(max_counts[key], count)

    merged = []
    for key, count in max_counts.items():
        merged.extend(dict(attempts_by_key[key]) for _ in range(count))
    merged.sort(key=lambda attempt: attempt.get("date", ""))
    return merged
