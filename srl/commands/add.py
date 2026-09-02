from rich.console import Console
from srl.utils import today
from srl.storage import (
    load_json,
    save_json,
    PROGRESS_FILE,
    MASTERED_FILE,
    NEXT_UP_FILE,
)
from srl.commands.list_ import get_due_problems
from srl.scheduling import (
    apply_attempt_schedule,
    make_attempt,
    merge_histories,
    parse_stored_date,
    qualifies_for_mastery,
)


def add_subparser(subparsers):
    parser = subparsers.add_parser(
        "add",
        help="Add or update a problem attempt",
        description=(
            "Add or update a problem attempt. "
            "If no problem is specified, defaults to problem #1 from 'srl list'."
        ),
    )

    parser.add_argument(
        "rating",
        type=int,
        choices=range(1, 6),
        help="Rating from 1-5",
    )

    parser.add_argument(
        "-u",
        "--url",
        type=str,
        help="Problem URL",
    )

    parser.add_argument(
        "--amend",
        action="store_true",
        help="Replace the latest rating instead of adding a new attempt",
    )

    target = parser.add_mutually_exclusive_group()

    target.add_argument(
        "-n",
        "--number",
        type=int,
        help="Problem number from 'srl list'",
    )

    target.add_argument(
        "-p",
        "--problem",
        dest="name",
        type=str,
        help="Problem name",
    )

    parser.set_defaults(handler=handle)

    return parser


def handle(args, console: Console):
    name, err = _resolve_problem_name(args)
    if err:
        return console.print(err)

    rating: int = args.rating
    url: str = getattr(args, "url", "")
    progress_data = load_json(PROGRESS_FILE)
    mastered_data = load_json(MASTERED_FILE)

    target_name = _get_canonical_name(progress_data, name)
    mastered_name = _get_canonical_name(mastered_data, name)

    entry: dict = progress_data.get(target_name, {"history": []})
    if mastered_name in mastered_data:
        mastered_entry = mastered_data.pop(mastered_name)
        if target_name in progress_data:
            entry["history"] = merge_histories(
                mastered_entry.get("history", []), entry.get("history", [])
            )
            if not entry.get("url") and mastered_entry.get("url"):
                entry["url"] = mastered_entry["url"]
        else:
            target_name = mastered_name
            entry = mastered_entry
        save_json(MASTERED_FILE, mastered_data)

    if getattr(args, "amend", False):
        console.print(f"[yellow]Amending {name}[/yellow]")
        if err := _amend_problem(progress_data, entry, target_name, rating):
            return console.print(err)
    else:
        _append_problem(entry, rating)

    if url:
        entry["url"] = url

    display_text = _update_progress_data(progress_data, entry, target_name)
    console.print(display_text)

    _remove_from_nextup(progress_data, target_name)


def _resolve_problem_name(args) -> tuple[str, str]:
    """Returns tuple of (name, err)"""
    if hasattr(args, "number") and args.number is not None:
        problems = get_due_problems()
        if args.number > len(problems) or args.number <= 0:
            return None, f"[bold red]Invalid problem number: {args.number}[/bold red]"
        name = problems[args.number - 1][0]
        return name, None

    if getattr(args, "name", None):
        return args.name, None

    problems = get_due_problems()
    if len(problems) > 0:
        name = problems[0][0]
        return name, None

    return None, "[bold red]Unable to resolve problem name[/bold red]"


def _get_canonical_name(progress_data, name):
    """Returns existing problem name from progress_data case insensitive. If not found, returns name"""
    for key in progress_data:
        if key.lower() == name.lower():
            return key

    return name


def _amend_problem(progress_data, entry, name, rating) -> str | None:
    """Returns err as str or None if successful"""
    if name not in progress_data:
        return f"[bold red]Problem '{name}' not found in progress[/bold red]"
    elif entry["history"]:
        original = entry["history"][-1]
        previous_history = entry["history"][:-1]
        base_entry = dict(entry)
        base_entry["history"] = previous_history
        pending_recall_failure = bool(
            entry.get("recall_failed_since_last_submission")
        )
        pending_full_solve = bool(entry.get("full_solve_required"))
        base_entry.pop("recall_failed_since_last_submission", None)
        base_entry.pop("full_solve_required", None)

        if previous_history:
            previous = previous_history[-1]
            if previous.get("interval_days") is not None:
                base_entry["interval_days"] = previous["interval_days"]
                base_entry["due_date"] = previous.get("due_date")
            else:
                base_entry.pop("interval_days", None)
                base_entry.pop("due_date", None)
        else:
            base_entry.pop("interval_days", None)
            base_entry.pop("due_date", None)

        amended = make_attempt(
            base_entry, rating, parse_stored_date(original["date"])
        )
        entry["history"][-1] = amended
        apply_attempt_schedule(entry, amended)
        if pending_recall_failure:
            entry["recall_failed_since_last_submission"] = True
        if pending_full_solve:
            entry["full_solve_required"] = True
    else:
        return f"[bold red]No attempts found for '{name}'[/bold red]"


def _append_problem(entry, rating):
    attempt = make_attempt(entry, rating, today())
    entry["history"].append(attempt)
    apply_attempt_schedule(entry, attempt)


def _update_progress_data(progress_data, entry, name) -> str:
    """Returns display_text"""
    display_text = _check_mastery(progress_data, entry, name)
    if not display_text:
        progress_data[name] = entry
        display_text = (
            f"Added rating [yellow]{entry['history'][-1]['rating']}[/yellow] for "
            f"'[cyan]{name}[/cyan]'. Next review in "
            f"[cyan]{entry['interval_days']} day(s)[/cyan] on {entry['due_date']}"
        )

    save_json(PROGRESS_FILE, progress_data)

    return display_text


def _check_mastery(progress_data, entry, name) -> str | None:
    """Returns display_text if moved to mastered, None otherwise"""
    history = entry["history"]

    if not qualifies_for_mastery(history):
        return None

    mastered = load_json(MASTERED_FILE)
    if name in mastered:
        mastered[name]["history"] = merge_histories(
            mastered[name].get("history", []), history
        )
        if entry.get("url"):
            mastered[name]["url"] = entry["url"]
    else:
        mastered[name] = entry
    save_json(MASTERED_FILE, mastered)

    if name in progress_data:
        del progress_data[name]

    return f"[bold green]{name}[/bold green] moved to [cyan]mastered[/cyan]!"


def _remove_from_nextup(progress_data, name):
    next_up = load_json(NEXT_UP_FILE)
    if name not in next_up:
        return

    if (
        name in progress_data
        and next_up[name].get("url")
        and not progress_data[name].get("url")
    ):
        progress_data[name]["url"] = next_up[name]["url"]
        save_json(PROGRESS_FILE, progress_data)

    del next_up[name]
    save_json(NEXT_UP_FILE, next_up)
