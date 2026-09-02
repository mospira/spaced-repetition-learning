from datetime import timedelta

from rich.console import Console

from srl.commands.config import Config
from srl.commands.list_ import get_due_problems
from srl.scheduling import (
    INITIAL_INTERVALS,
    due_candidates,
    entry_due_date,
    previous_interval_days,
    select_balanced_reviews,
)
from srl.storage import PROGRESS_FILE, load_json, save_json
from srl.utils import today


def add_subparser(subparsers):
    parser = subparsers.add_parser(
        "recall",
        help="Record a lightweight conceptual recall without a submission rating",
    )
    parser.add_argument(
        "result",
        choices=("pass", "fail"),
        help="Whether the approach, invariant, complexity, and edge cases were recalled",
    )

    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "-n",
        "--number",
        type=int,
        help="Problem number from the default 'srl list' plan",
    )
    target.add_argument(
        "-p",
        "--problem",
        dest="name",
        help="Problem name",
    )
    parser.set_defaults(handler=handle)
    return parser


def _canonical_name(data: dict, name: str) -> str | None:
    lowered = name.lower()
    for key in data:
        if key.lower() == lowered:
            return key
    return None


def _resolve_name(args, progress: dict) -> tuple[str | None, str | None]:
    number = getattr(args, "number", None)
    if number is not None:
        plan = get_due_problems(Config.load().daily_review_limit)
        if number <= 0 or number > len(plan):
            return None, f"[red]Invalid problem number:[/red] {number}"
        name = _canonical_name(progress, plan[number - 1][0])
        if name is None:
            return None, "[red]That list item is a new problem, not a recall.[/red]"
        return name, None

    requested = getattr(args, "name", None)
    if requested:
        name = _canonical_name(progress, requested)
        if name is None:
            return None, f"[red]Problem '{requested}' is not in progress.[/red]"
        return name, None

    candidates = select_balanced_reviews(
        due_candidates(progress, today()), Config.load().daily_review_limit
    )
    for candidate in candidates:
        if candidate.rating >= 4:
            return candidate.name, None

    return None, "[yellow]No quick-recall problem is due in today's plan.[/yellow]"


def handle(args, console: Console):
    progress = load_json(PROGRESS_FILE)
    name, error = _resolve_name(args, progress)
    if error:
        return console.print(error)

    entry = progress[name]
    history = entry.get("history", [])
    if not history:
        return console.print(f"[red]No submission history found for '{name}'.[/red]")

    last_rating = int(history[-1]["rating"])
    if last_rating < 4:
        return console.print(
            f"[yellow]{name} requires a submitted solution; its last rating was "
            f"{last_rating}.[/yellow]"
        )

    due = entry_due_date(entry)
    if due is None or due > today():
        return console.print(f"[yellow]{name} is not due for recall yet.[/yellow]")

    result = args.result
    if entry.get("full_solve_required") or entry.get(
        "recall_failed_since_last_submission"
    ):
        return console.print(
            f"[yellow]{name} requires a full solve. Complete and submit it "
            "before recording another recall.[/yellow]"
        )

    current_interval = previous_interval_days(entry) or INITIAL_INTERVALS[last_rating]
    next_interval = current_interval if result == "pass" else 1
    next_due = today() + timedelta(days=next_interval)

    recall = {
        "date": today().isoformat(),
        "result": result,
        "last_submission_rating": last_rating,
        "scheduled_due": due.isoformat(),
        "days_late": max(0, (today() - due).days),
        "next_full_solve_due": next_due.isoformat(),
    }
    entry.setdefault("recall_history", []).append(recall)
    entry["due_date"] = next_due.isoformat()
    entry["full_solve_required"] = True

    if result == "fail":
        entry["recall_failed_since_last_submission"] = True

    save_json(PROGRESS_FILE, progress)

    if result == "pass":
        console.print(
            f"[green]Recall passed for {name}.[/green] Submission rating remains "
            f"{last_rating}; full solve due in {next_interval} day(s) on {next_due}. "
            "Recall alone cannot grant mastery."
        )
    else:
        console.print(
            f"[red]Recall failed for {name}.[/red] No submission rating was recorded. "
            f"Complete a full solve by {next_due}."
        )
