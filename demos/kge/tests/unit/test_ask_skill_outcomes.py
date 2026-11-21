"""One outcome per diagnosis (#531): the ask skill's outcome table lists improve's outcomes in the order rules.py
declares them, each with the route rules.py gives it, and the retired needs_code outcome appears in neither."""
import inspect

from kge.improve import rules
from kge.pipeline.settings import ROOT

SKILL = ROOT / "skills" / "ask" / "SKILL.md"
HEADER = ("Outcome", "Route", "Evidence", "Handed to")


def cells(line):
    """A Markdown table line's cells, stripped, whatever its indent."""
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def outcome_table(text):
    """The contiguous rows under the Outcome | Route | Evidence | Handed to header, as (outcome, route) pairs."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip().startswith("|") and cells(line) == HEADER)
    table = []
    for line in lines[start + 2:]:
        if not line.strip().startswith("|"):
            break
        table.append(cells(line)[:2])
    return table


def test_outcome_column_lists_every_outcome_in_order():
    table = outcome_table(SKILL.read_text())
    assert tuple(outcome for outcome, _ in table) == rules.OUTCOMES


def test_each_route_matches_the_route_rules_gives():
    for outcome, route in outcome_table(SKILL.read_text()):
        assert route == rules.ROUTES[outcome], outcome


def test_the_table_is_parsed_from_the_header_only():
    text = "| a | b | c | d |\n|---|---|---|---|\n| x | y | z | w |\n\n" \
           "   | Outcome | Route | Evidence | Handed to |\n   |---|---|---|---|\n   | one | pack | e | h |\n\n" \
           "| p | q | r | s |\n"
    assert outcome_table(text) == [("one", "pack")]


def test_needs_code_is_retired_from_the_skill_and_the_rules():
    assert "needs_code" not in SKILL.read_text()
    assert "needs_code" not in inspect.getsource(rules)
