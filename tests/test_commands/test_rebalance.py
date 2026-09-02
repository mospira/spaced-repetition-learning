from datetime import date, timedelta
from types import SimpleNamespace

from srl.commands import rebalance


def _legacy_entry(rating, reviewed_on):
    return {"history": [{"rating": rating, "date": reviewed_on.isoformat()}]}


def test_rebalance_spreads_overdue_and_merges_duplicates(
    mock_data, dump_json, load_json, console
):
    old = date.today() - timedelta(days=40)
    progress = {
        f"Problem {index}": _legacy_entry((index % 5) + 1, old)
        for index in range(10)
    }
    progress["Duplicate"] = _legacy_entry(1, old)
    mastered = {
        "duplicate": {
            "history": [{"rating": 5, "date": (old - timedelta(days=31)).isoformat()}]
        }
    }
    dump_json(mock_data.PROGRESS_FILE, progress)
    dump_json(mock_data.MASTERED_FILE, mastered)

    args = SimpleNamespace(daily_cap=4, start_date=date.today(), no_backup=False)
    rebalance.handle(args, console)

    updated = load_json(mock_data.PROGRESS_FILE)
    updated_mastered = load_json(mock_data.MASTERED_FILE)
    config = load_json(mock_data.CONFIG_FILE)
    dates = [entry["due_date"] for entry in updated.values()]

    assert len([value for value in dates if value == date.today().isoformat()]) == 4
    assert len(set(dates)) == 3
    assert "duplicate" not in updated_mastered
    assert len(updated["Duplicate"]["history"]) == 2
    assert config["daily_review_limit"] == 4
    assert list(mock_data.BACKUP_DIR.glob("backup-*.tar.gz"))


def test_rebalance_does_not_create_attempts(mock_data, dump_json, load_json, console):
    old = date.today() - timedelta(days=20)
    dump_json(mock_data.PROGRESS_FILE, {"Problem": _legacy_entry(1, old)})

    rebalance.handle(
        SimpleNamespace(daily_cap=8, start_date=date.today(), no_backup=True), console
    )

    entry = load_json(mock_data.PROGRESS_FILE)["Problem"]
    assert len(entry["history"]) == 1
    assert entry["due_date"] == date.today().isoformat()
