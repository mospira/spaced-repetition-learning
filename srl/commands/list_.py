from rich.console import Console
from rich.panel import Panel
from srl.utils import today, format_problem
from srl.commands.audit.utils import (
    get_current_audit,
    random_audit,
    get_last_audit_date,
)
import random
from srl.storage import (
    load_json,
    NEXT_UP_FILE,
    PROGRESS_FILE,
)
from srl.commands.config import Config
from srl import storage
from srl.scheduling import (
    due_candidates,
    new_problem_allowance,
    select_balanced_reviews,
)


def add_subparser(subparsers):
    parser = subparsers.add_parser(
        "list",
        help="List due problems, supplementing from the Nextup Queue as needed",
    )

    parser.add_argument(
        "-n",
        "--num",
        type=int,
        default=None,
        dest="n",
        help="Target number of problems to list, prioritizing due problems then Nextup Queue",
    )

    parser.set_defaults(handler=handle)

    return parser


def handle(args, console: Console):
    if should_audit() and not get_current_audit():
        problem, problem_url = random_audit()
        if problem:
            console.print("[bold red]You have been randomly audited![/bold red]")
            display = format_problem(problem, problem_url)
            console.print(f"[yellow]Audit problem:[/yellow] [cyan]{display}[/cyan]")
            console.print(
                "Run [green]srl audit pass[/green] or [red]fail[/red] when done"
            )
            return

    cfg = Config.load()
    requested: int | None = getattr(args, "n", None)
    review_limit = requested if requested is not None else cfg.daily_review_limit

    progress = load_json(PROGRESS_FILE)
    all_due = due_candidates(progress, today())
    selected = select_balanced_reviews(all_due, review_limit)

    plan = [
        (candidate.name, candidate.url, candidate.review_mode, "review")
        for candidate in selected
    ]

    allowance = new_problem_allowance(len(all_due), cfg)
    if requested is not None:
        allowance = min(allowance, max(0, requested - len(plan)))

    next_up = load_json(NEXT_UP_FILE)
    for name, info in list(next_up.items())[:allowance]:
        plan.append((name, info.get("url", ""), "new problem", "new"))

    if not plan:
        console.print("[bold green]No problems due today or in Next Up.[/bold green]")
        return

    masters = mastery_candidates()
    lines = []
    for i, (name, url, mode, source) in enumerate(plan, start=1):
        mark = " [magenta]*[/magenta]" if name in masters else ""
        display = format_problem(name, url)
        style = "cyan" if source == "new" else "dim"
        lines.append(f"{i}. {display}{mark} [{style}]{mode}[/{style}]")

    review_count = sum(1 for *_, source in plan if source == "review")
    new_count = len(plan) - review_count
    status = f"{review_count} review(s), {new_count} new; {len(all_due)} overdue"

    console.print(
        Panel.fit(
            "\n".join(lines),
            title=(
                f"[bold blue]Problems to Practice [{today().isoformat()}] "
                f"({len(plan)}) - {status}[/bold blue]"
            ),
            border_style="blue",
            title_align="left",
        )
    )


def should_audit():
    cfg = Config.load()

    if cfg.suppress_audits_when_overdue:
        overdue_count = len(
            due_candidates(storage.load_json(storage.PROGRESS_FILE), today())
        )
        if overdue_count > cfg.daily_review_limit:
            return False

    # Check max days without audit first
    if cfg.max_days_without_audit and cfg.max_days_without_audit > 0:
        last_audit_date = get_last_audit_date()
        if last_audit_date:
            days_since_last = (today() - last_audit_date).days
            if days_since_last >= cfg.max_days_without_audit:
                return True

    # Fall back to probability-based audit
    probability = cfg.audit_probability
    try:
        probability = float(probability)
    except (ValueError, TypeError):
        probability = 0.1
    return random.random() < probability


def get_due_problems(limit: int | None = None) -> list[tuple[str, str]]:
    """Return problems as ``(name, url)`` tuples.

    Due problems are returned first, sorted by oldest attempt then lowest rating.
    Remaining slots are filled from the Nextup Queue.

    Args:
        limit: Maximum number of problems to return. If ``None``, returns all due
            problems, or all Nextup Queue problems if no due problems exist.
    """
    cfg = Config.load()
    candidates = due_candidates(load_json(PROGRESS_FILE), today())
    selected = select_balanced_reviews(candidates, limit)
    result = [(candidate.name, candidate.url) for candidate in selected]

    if limit is None:
        if result:
            return result

        next_up = load_json(NEXT_UP_FILE)

        return [(prob, info.get("url", "")) for prob, info in next_up.items()]

    if len(result) >= limit:
        return result

    next_up = load_json(NEXT_UP_FILE)

    remaining = min(
        limit - len(result), new_problem_allowance(len(candidates), cfg)
    )

    supplement = [
        (prob, info.get("url", "")) for prob, info in list(next_up.items())[:remaining]
    ]

    return result + supplement


def mastery_candidates() -> set[str]:
    """Return names of problems whose *last* rating was 5."""
    data = load_json(PROGRESS_FILE)
    out = set()
    for name, info in data.items():
        hist = info.get("history", [])
        if hist and hist[-1].get("rating") == 5:
            out.add(name)
    return out
