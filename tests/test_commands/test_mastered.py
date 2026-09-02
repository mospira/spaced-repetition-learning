from srl.commands import mastered, add
from types import SimpleNamespace


def test_mastered_count(console, mature_problem):
    problem = "Counting Test"
    rating = 5
    mature_problem(problem)

    args = SimpleNamespace(count=True)
    mastered.handle(args=args, console=console)

    output = console.export_text()
    assert "Mastered Count:" in output
    assert "1" in output


def test_mastered_list_with_items(console, today_string, mock_data, dump_json):
    problem_a = "Problem A"
    problem_b = "Problem B"

    dump_json(
        mock_data.MASTERED_FILE,
        {
            problem_a: {
                "history": [
                    {"rating": 5, "date": "2026-07-01"},
                    {"rating": 5, "date": today_string},
                ]
            },
            problem_b: {
                "history": [
                    {"rating": 5, "date": "2026-05-01"},
                    {"rating": 1, "date": "2026-06-01"},
                    {"rating": 5, "date": "2026-07-01"},
                    {"rating": 5, "date": today_string},
                ]
            },
        },
    )

    args = SimpleNamespace(c=False)
    mastered.handle(args=args, console=console)

    output = console.export_text()
    assert "Mastered Problems (2)" in output
    assert "Problem A" in output
    assert "2" in output
    assert "Problem B" in output
    assert "4" in output
    assert today_string in output


def test_mastered_list_empty(console):
    args = SimpleNamespace(c=False)
    mastered.handle(args=args, console=console)

    output = console.export_text()
    assert "No mastered problems yet" in output


def test_mastered_fuzzy_filter(console, mature_problem):
    problem_a = "Problem A"
    problem_b = "Other B"

    mature_problem(problem_a)
    mature_problem(problem_b)

    console.clear()
    args = SimpleNamespace(query="Prob")
    mastered.handle(args=args, console=console)

    output = console.export_text()
    assert "Problem A" in output
    assert "Other B" not in output


def test_get_mastered_problems_filters_empty_history(
    console, today_string, mature_problem
):
    problem_a = "Problem A"

    mature_problem(problem_a)

    result = mastered.get_mastered_problems()
    assert len(result) == 1
    assert (problem_a, 2, today_string) in result
