"""KGE's MCP server instructions (#707 requirement 5): the mounts that carry get_context, ask and search send them, no
other mount does, and they and the two tool descriptions they point at stay within their token caps. A token is counted
as characters divided by 4, rounded up: the image has no tokeniser. The characters are counted with a docstring's
indentation removed, as Python 3.13 sends it and as the caps were measured; Python 3.12 keeps the indentation, so
without this its count is larger and the caps would mean a different text on each Python.

    .venv/bin/python -m pytest tests/unit/test_mcp_instructions.py -q
"""
from __future__ import annotations

import inspect
import math

import pytest

INSTRUCTIONS_CAP = 160
DESCRIPTION_GROWTH = 15
DESCRIPTIONS_AT_6B5ECD58 = {"get_context": 68, "ask": 340}
"""Each tool description's tokens at origin/develop 6b5ecd58, before #707 added its line."""
SENDING = {"context_mcp", "answer_mcp", "search_capability_mcp"}
MOUNTS = ("context_mcp", "onboarding_mcp", "answer_mcp", "identity_capability_mcp", "catalogue_capability_mcp",
          "data_capability_mcp", "assist_capability_mcp", "search_capability_mcp", "access_capability_mcp",
          "graph_capability_mcp")


def tokens(text: str) -> int:
    return math.ceil(len(inspect.cleandoc(text)) / 4)


@pytest.fixture(scope="module")
def app():
    import kge.app as app
    return app


def test_the_instructions_are_at_most_160_tokens():
    from kge.services.http import AGENT_INSTRUCTIONS
    assert 0 < tokens(AGENT_INSTRUCTIONS) <= INSTRUCTIONS_CAP, tokens(AGENT_INSTRUCTIONS)
    for said in ("get_context", "open_items: true", "ask", "history", "refusal", "no data"):
        assert said in AGENT_INSTRUCTIONS, said


def test_only_the_context_answer_and_search_mounts_send_them(app):
    from kge.services.http import AGENT_INSTRUCTIONS
    assert set(MOUNTS) == {name for name in vars(app) if name.endswith("_mcp") and hasattr(getattr(app, name), "instructions")}
    for name in MOUNTS:
        sent = getattr(app, name).instructions
        assert sent == (AGENT_INSTRUCTIONS if name in SENDING else None), name


def test_get_context_and_ask_each_gain_one_line_within_15_tokens(app):
    for server, name, line in ((app.context_mcp, "get_context", "Lists a question's open items."),
                               (app.answer_mcp, "ask", "Check get_context's open items first.")):
        description = server._tool_manager.get_tool(name).description
        assert " ".join(line.split()) in " ".join(description.split()), name
        assert tokens(description) <= DESCRIPTIONS_AT_6B5ECD58[name] + DESCRIPTION_GROWTH, (name, tokens(description))


def test_get_context_takes_open_items_off_by_default(app):
    schema = app.context_mcp._tool_manager.get_tool("get_context").parameters
    assert schema["properties"]["open_items"]["default"] is False
    output = app.context_mcp._tool_manager.get_tool("get_context").output_schema
    assert {"bindings", "open_items"} <= set(output["properties"])
