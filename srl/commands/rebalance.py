from datetime import date, timedelta

from rich.console import Console

from srl import storage
from srl.commands.backup.utils import create_backup
from srl.commands.config import Config
from srl.scheduling import (
    due_candidates,
    entry_due_date,
    merge_histories,
    select_balanced_reviews,
)
from srl.utils import today


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("daily cap must be positive")
    return parsed


def add_subparser(subparsers):
    parser = subparsers.add_parser(
        "rebalance",
        help="Spread the overdue backlog across sustainable daily sessions",
    )
    parser.add_argument(
        "--daily-cap",
        type=positive_int,
        default=None,
        help="Maximum overdue reviews scheduled per day (defaults to config)",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=None,
        help="First schedule date in YYYY-MM-DD format (defaults to today)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the automatic pre-rebalance backup",
    )
    parser.set_defaults(handler=handle)
    return parser


def _merge_progress_mastered_duplicates(progress: dict, mastered: dict) -> list[str]:
    mastered_by_lower = {name.lower(): name for name in mastered}
    merged_names = []

    for progress_name in list(progress):
        mastered_name = mastered_by_lower.get(progress_name.lower())
        if mastered_name is None:
            continue

        progress_entry = progress[progress_name]
        mastered_entry = mastered.pop(mastered_name)
        progress_entry["history"] = merge_histories(
            mastered_entry.get("history", []), progress_entry.get("history", [])
        )
        if not progress_entry.get("url") and mastered_entry.get("url"):
            progress_entry["url"] = mastered_entry["url"]
        merged_names.append(progress_name)

    return merged_names


def handle(args, console: Console):
    config = Config.load()
    cap = getattr(args, "daily_cap", None) or config.daily_review_limit
    start = getattr(args, "start_date", None) or today()

    progress = storage.load_json(storage.PROGRESS_FILE)
    mastered = storage.load_json(storage.MASTERED_FILE)
    duplicate_names = _merge_progress_mastered_duplicates(progress, mastered)
    candidates = due_candidates(progress, today())

    if not candidates and not duplicate_names:
        console.print("[green]No overdue problems to rebalance.[/green]")
        return

    if not getattr(args, "no_backup", False):
        create_backup(console)

    ordered = select_balanced_reviews(candidates, None)
    for index, candidate in enumerate(ordered):
        scheduled = start + timedelta(days=index // cap)
        entry = progress[candidate.name]
        old_due = entry_due_date(entry)
        if old_due and old_due.isoformat() != scheduled.isoformat():
            entry["rebalanced_from_due_date"] = old_due.isoformat()
        entry["due_date"] = scheduled.isoformat()
        entry["last_rebalanced_at"] = today().isoformat()

    storage.save_json(storage.PROGRESS_FILE, progress)
    storage.save_json(storage.MASTERED_FILE, mastered)

    config.daily_review_limit = cap
    config.save()

    days = max(1, (len(ordered) + cap - 1) // cap)
    console.print(
        f"[green]Rebalanced {len(ordered)} overdue problems across {days} day(s) "
        f"with a daily cap of {cap}.[/green]"
    )
    if duplicate_names:
        console.print(
            f"[cyan]Merged {len(duplicate_names)} mastered/in-progress duplicate(s): "
            f"{', '.join(duplicate_names)}[/cyan]"
        )
