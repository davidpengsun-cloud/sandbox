"""The roles table in the README and in the install guide, held to the routes' checks (issue #105).

    .venv/bin/python -m pytest tests/unit/test_roles_docs.py -q

Each document's section "Roles in onboarding and improvement" has a table whose rows name a step, the persona who
does it, the role the page checks and the route (with the /run step) that checks it. The routes' role is read from
the source with ast, never by importing the app: an onboarding route's role or process dependency,
the /run route's named check for each step, the steps the onboard service runs
(COMMANDS), and the ask routes' needs("querying"). The test fails if a route's dependency or the /run rule changes and
a table does not, if a table names another role, or if an onboarding route or a /run step is missing from a table.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2]
ONBOARD = DEMO / "kge" / "services" / "onboard" / "routes.py"
SERVICE = DEMO / "kge" / "services" / "onboard" / "service.py"
ASK = DEMO / "kge" / "services" / "ask" / "routes.py"
DOCS = [DEMO / "README.md", DEMO / "kit" / "INSTALL.md"]
HEADING = "### Roles in onboarding and improvement"
PERSONAS = {"Knowledge engineer", "Business team", "Steward", "End user"}
ROLES = {"business", "steward", "participant", "drafting", "building", "improving"}


@pytest.mark.parametrize("doc", DOCS, ids=lambda doc: doc.name)
def test_job_operator_power_and_platform_install_are_explicit(doc):
    text = doc.read_text()
    for phrase in ("Whoever runs the KGE job", "is a knowledge engineer", "docker compose run job",
                   "sign-off PRINCIPAL=<steward>", "break-glass power", "Cloud Run's execution log",
                   "page remains the governed route", "later hardening", "holds no KGE role by default"):
        assert phrase in text
    assert "business team reads, accepts and answers requests" in text


def _prefix(tree: ast.Module) -> str:
    """The prefix the module's APIRouter is made with, or none."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "APIRouter":
            for kw in node.keywords:
                if kw.arg == "prefix":
                    return kw.value.value
    return ""


def _route(fn: ast.FunctionDef) -> tuple[str, str] | None:
    """The method and path of a function's router decorator."""
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router" and dec.args and isinstance(dec.args[0], ast.Constant)):
            return dec.func.attr.upper(), dec.args[0].value
    return None


def _depends(fn: ast.FunctionDef) -> list[str]:
    """Each Depends(...) among a function's defaults, as its source text."""
    defaults = [*fn.args.defaults, *(d for d in fn.args.kw_defaults if d is not None)]
    return [ast.unparse(d.args[0]) for d in defaults
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Depends" and d.args]


def _commands() -> set[str]:
    """The keys of the onboard service's COMMANDS, the steps /run accepts."""
    tree = ast.parse(SERVICE.read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "COMMANDS" for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            return {k.value for k in node.value.keys}
    raise AssertionError("service.py defines no COMMANDS dict")


def _run_rule(fn: ast.FunctionDef) -> dict[str, str]:
    """The run adapter dispatches each step through the same named check as MCP."""
    assert "RUN_CHECKS[body.step](who_)" in ast.unparse(fn)
    tree = ast.parse(ONBOARD.read_text())
    rule = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "RUN_CHECKS" for t in n.targets))
    checks = {key.value: value.id for key, value in zip(rule.keys, rule.values)}
    assert set(checks) == _commands()
    return checks


def code_roles() -> dict[tuple[str, str, str | None], str]:
    """(method, path, /run step or None) to the role the code checks, for every onboarding route and the ask routes."""
    roles: dict[tuple[str, str, str | None], str] = {}
    tree = ast.parse(ONBOARD.read_text())
    prefix = _prefix(tree)
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or not (route := _route(fn)):
            continue
        method, path = route[0], prefix + route[1]
        if fn.name == "run":
            assert _depends(fn) == ["signed_in"], _depends(fn)
            for step, role in _run_rule(fn).items():
                roles[(method, path, step)] = role
        else:
            deps = [d for d in _depends(fn) if d in ROLES]
            assert len(deps) == 1, f"{method} {path} depends on {_depends(fn)}"
            roles[(method, path, None)] = deps[0]
    tree = ast.parse(ASK.read_text())
    prefix = _prefix(tree)
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and (route := _route(fn)):
            for dep in _depends(fn):
                if dep.startswith("needs("):
                    roles[(route[0], prefix + route[1], None)] = dep.replace("'", '"')
    return roles


def table(doc: Path) -> list[dict[str, str]]:
    """The rows of the roles section's table, keyed by its header."""
    text = doc.read_text()
    assert HEADING in text, f"{doc.name} has no section {HEADING!r}"
    section = text.split(HEADING, 1)[1].split("\n#", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    assert len(lines) > 2, f"{doc.name}'s roles section has no table"
    cells = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
    header = cells[0]
    return [dict(zip(header, row)) for row in cells[2:]]


def doc_roles(doc: Path) -> dict[tuple[str, str, str | None], str]:
    """(method, path, /run step or None) to the role each table row states for it."""
    stated: dict[tuple[str, str, str | None], str] = {}
    for row in table(doc):
        assert row["Who does it"] in PERSONAS, row
        role = row["Role the page checks"].strip("`")
        route = row["Page route"]
        steps = re.findall(r"`([\w-]+)`", route.split(", step", 1)[1]) if ", step" in route else [None]
        paths = re.findall(r"`(GET|POST) (/[^`\s]+)`", route)
        assert paths, row
        for method, path in paths:
            for step in steps:
                key = (method, path, step)
                assert key not in stated, f"{doc.name} names {key} twice"
                stated[key] = role
    return stated


def test_the_code_has_the_roles_the_tables_are_read_against():
    roles = code_roles()
    assert roles[("POST", "/onboard/certify", None)] == "steward"
    assert {k[2] for k in roles if k[1] == "/onboard/run"} == {"checks", "improve", "sign-off", "release"}
    assert roles[("POST", "/ask", None)] == 'needs("querying")'


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_each_table_states_the_role_the_code_checks(doc):
    roles, stated = code_roles(), doc_roles(doc)
    wrong = {k: (v, roles.get(k)) for k, v in stated.items() if roles.get(k) != v}
    assert not wrong, f"{doc.name} states (table, code): {wrong}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_each_table_has_every_onboarding_write_and_every_run_step(doc):
    stated = doc_roles(doc)
    missing = [k for k in code_roles() if k[1].startswith("/onboard") and k[0] == "POST" and k not in stated]
    assert not missing, f"{doc.name}'s table leaves out {missing}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_each_table_has_the_four_personas(doc):
    assert {row["Who does it"] for row in table(doc)} == PERSONAS
