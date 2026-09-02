from datetime import date, timedelta
from types import SimpleNamespace

from srl.commands import add, recall
from srl.scheduling import due_candidates


def _due_entry(rating=4, interval=28):
    return {
        "history": [
            {
                "rating": rating,
                "date": (date.today() - timedelta(days=interval)).isoformat(),
                "interval_days": interval,
                "due_date": date.today().isoformat(),
            }
        ],
        "interval_days": interval,
        "due_date": date.today().isoformat(),
    }


def test_recall_pass_preserves_submission_rating_and_interval(
    mock_data, dump_json, load_json, console
):
    problem = "Quick Recall"
    dump_json(mock_data.PROGRESS_FILE, {problem: _due_entry(rating=4, interval=28)})

    recall.handle(
        SimpleNamespace(result="pass", name=problem, number=None), console
    )

    entry = load_json(mock_data.PROGRESS_FILE)[problem]
    assert len(entry["history"]) == 1
    assert entry["history"][-1]["rating"] == 4
    assert entry["interval_days"] == 28
    assert entry["due_date"] == (date.today() + timedelta(days=28)).isoformat()
    assert entry["full_solve_required"] is True
    assert entry["recall_history"][-1]["result"] == "pass"
    assert "Recall alone cannot grant mastery" in console.export_text()
    future_due = due_candidates(
        {problem: entry}, date.today() + timedelta(days=28)
    )
    assert future_due[0].review_mode == "full solve"


def test_recall_fail_schedules_full_solve_without_rating(
    mock_data, dump_json, load_json, console
):
    problem = "Failed Recall"
    dump_json(mock_data.PROGRESS_FILE, {problem: _due_entry(rating=5, interval=30)})

    recall.handle(
        SimpleNamespace(result="fail", name=problem, number=None), console
    )

    entry = load_json(mock_data.PROGRESS_FILE)[problem]
    assert len(entry["history"]) == 1
    assert entry["history"][-1]["rating"] == 5
    assert entry["due_date"] == (date.today() + timedelta(days=1)).isoformat()
    assert entry["recall_failed_since_last_submission"] is True
    assert entry["full_solve_required"] is True
    assert entry["recall_history"][-1]["result"] == "fail"


def test_submission_after_failed_recall_cannot_master_and_resets_interval(
    mock_data, dump_json, load_json, console
):
    problem = "Blocked Mastery"
    entry = _due_entry(rating=5, interval=30)
    entry["history"][0]["date"] = (date.today() - timedelta(days=31)).isoformat()
    dump_json(mock_data.PROGRESS_FILE, {problem: entry})

    recall.handle(
        SimpleNamespace(result="fail", name=problem, number=None), console
    )
    add.handle(SimpleNamespace(name=problem, rating=5), console)

    progress = load_json(mock_data.PROGRESS_FILE)
    mastered = load_json(mock_data.MASTERED_FILE)
    assert problem in progress
    assert problem not in mastered
    assert progress[problem]["interval_days"] == 30
    assert progress[problem]["history"][-1]["mastery_blocked"] is True
    assert "recall_failed_since_last_submission" not in progress[problem]
    assert "full_solve_required" not in progress[problem]


def test_recall_rejects_low_rated_problem(mock_data, dump_json, console):
    problem = "Needs Full Solve"
    dump_json(mock_data.PROGRESS_FILE, {problem: _due_entry(rating=3, interval=7)})

    recall.handle(
        SimpleNamespace(result="pass", name=problem, number=None), console
    )

    assert "requires a submitted solution" in console.export_text()


def test_recall_number_matches_daily_list_order(
    mock_data, dump_json, load_json, console
):
    progress = {
        "Weak": _due_entry(rating=1, interval=1),
        "Recall Target": _due_entry(rating=4, interval=14),
    }
    dump_json(mock_data.PROGRESS_FILE, progress)

    recall.handle(SimpleNamespace(result="pass", name=None, number=2), console)

    updated = load_json(mock_data.PROGRESS_FILE)
    assert updated["Recall Target"]["recall_history"][-1]["result"] == "pass"
    assert "recall_history" not in updated["Weak"]


def test_recall_pass_cannot_clear_prior_recall_failure(
    mock_data, dump_json, console
):
    problem = "Must Submit"
    entry = _due_entry(rating=5, interval=30)
    entry["recall_failed_since_last_submission"] = True
    dump_json(mock_data.PROGRESS_FILE, {problem: entry})

    recall.handle(
        SimpleNamespace(result="pass", name=problem, number=None), console
    )

    assert "requires a full solve" in console.export_text()


def test_amend_does_not_consume_pending_recall_failure(
    mock_data, dump_json, load_json, console
):
    problem = "Amend After Recall"
    entry = _due_entry(rating=5, interval=30)
    entry["recall_failed_since_last_submission"] = True
    entry["full_solve_required"] = True
    dump_json(mock_data.PROGRESS_FILE, {problem: entry})

    add.handle(
        SimpleNamespace(name=problem, rating=4, amend=True), console
    )

    updated = load_json(mock_data.PROGRESS_FILE)[problem]
    assert updated["history"][-1]["rating"] == 4
    assert updated["recall_failed_since_last_submission"] is True
    assert updated["full_solve_required"] is True
    assert "mastery_blocked" not in updated["history"][-1]
