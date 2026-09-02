from datetime import date, timedelta

import pytest

from srl.scheduling import (
    DueCandidate,
    apply_attempt_schedule,
    make_attempt,
    new_problem_allowance,
    next_interval_days,
    qualifies_for_mastery,
    select_balanced_reviews,
    merge_histories,
)


@pytest.mark.parametrize(
    ("rating", "previous", "expected"),
    [
        (1, None, 1),
        (1, 60, 1),
        (2, None, 3),
        (2, 60, 3),
        (3, None, 7),
        (3, 14, 21),
        (3, 60, 21),
        (4, None, 14),
        (4, 14, 28),
        (4, 45, 60),
        (5, None, 30),
        (5, 30, 75),
        (5, 60, 120),
    ],
)
def test_interval_mapping(rating, previous, expected):
    assert next_interval_days(rating, previous) == expected


def test_missed_review_is_recorded_without_penalty():
    entry = {
        "history": [{"rating": 4, "date": "2026-08-01"}],
        "interval_days": 14,
        "due_date": "2026-08-15",
    }

    attempt = make_attempt(entry, 4, date(2026, 8, 18))
    apply_attempt_schedule(entry, attempt)

    assert attempt["days_late"] == 3
    assert attempt["scheduled_due"] == "2026-08-15"
    assert attempt["interval_days"] == 28
    assert entry["due_date"] == "2026-09-15"


def test_mastery_requires_rating_five_after_thirty_actual_days():
    early = [
        {"rating": 5, "date": "2026-08-01", "interval_days": 30},
        {
            "rating": 5,
            "date": "2026-08-02",
            "previous_interval_days": 30,
        },
    ]
    mature = [early[0], {**early[1], "date": "2026-08-31"}]

    assert qualifies_for_mastery(early) is False
    assert qualifies_for_mastery(mature) is True


def test_failed_recall_blocks_mastery():
    history = [
        {"rating": 5, "date": "2026-08-01", "interval_days": 30},
        {
            "rating": 5,
            "date": "2026-08-31",
            "previous_interval_days": 30,
            "mastery_blocked": True,
        },
    ]

    assert qualifies_for_mastery(history) is False


def test_balanced_daily_selection_uses_three_three_two_mix():
    due = date(2026, 8, 1)
    candidates = []
    for rating, count in ((1, 5), (3, 5), (5, 5)):
        for index in range(count):
            candidates.append(
                DueCandidate(
                    name=f"r{rating}-{index}",
                    url="",
                    rating=rating,
                    attempts=1,
                    due_date=due + timedelta(days=index),
                    overdue_days=30 - index,
                )
            )

    selected = select_balanced_reviews(candidates, 8)
    ratings = [candidate.rating for candidate in selected]

    assert ratings.count(1) == 3
    assert ratings.count(3) == 3
    assert ratings.count(5) == 2


def test_new_problem_admission_thresholds():
    class Config:
        new_problem_limit = 2
        overdue_reduce_new_threshold = 12
        overdue_pause_new_threshold = 24

    assert new_problem_allowance(12, Config) == 2
    assert new_problem_allowance(13, Config) == 1
    assert new_problem_allowance(25, Config) == 0


def test_merge_histories_preserves_legitimate_repeated_attempts():
    repeated = {"rating": 5, "date": "2026-08-01"}
    other = {"rating": 1, "date": "2026-09-01"}

    merged = merge_histories([repeated, repeated], [repeated, other])

    assert merged.count(repeated) == 2
    assert other in merged
