"""The rich answer (issue #52, R2): a display block the page asks for, built by code from an answer that has already run.

    .venv/bin/python -m pytest tests/integration/test_present.py -q

The owner's rule: "a toggle as add on, not used in a/b testing but definitely good in demo". The page's Rich answers
switch, off on every page load, asks POST /answer or /ask for ?display=1, and only then does kge/services/ask/engine/present.py add a
display block to the answer: a sentence, the figures table, a bar chart drawn by code from that table, and one line naming
the checked queries. Nothing else changes: the answer is the same with or without it, and the A/B harness, eval, margin
and parity never ask for it, import it or see it, so their records are the same as before the toggle's code was written.

The tests hold:
- the switch's contract and isolation: no display unless asked; the answer the same either way; nothing in kge/eval/ and no
  api module but app.py imports kge.services.ask.engine.present; harness records the same after the page asked for displays as before;
- what a display holds, over every golden, held-out and golden_wealth_v1 question on the three presets (/answer), on graph
  through /ask, and on the agent's two arms on rules (/agent): it builds; every number in its sentence and table is one
  the answer holds, and the sentence passes the agent's own number check; the chart names two of the table's columns and
  holds no values; there is no chart where parts do not add, never on a reply's claim alone; every query that ran is
  listed with the gate's verdict; every citation a figure is bound to stays; and the answer itself is not changed;
- named cases from the README's demo step and the design reviews, fixtures for negative values and many groups, and the
  mock: a chart on the treatment arm only, its text labelled mock and never model, its refused try listed.

Opens the demo's graph, and a scratch copy of it for the API; stop the API first.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent, names, supported, wording
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from tests.scratch import BUILD, DEMO, profile_of, run_of, scratch_build

SETS = ("golden", "heldout", "golden_wealth_v1")
W01, W14 = "NNA for FY26-Q1 by division", "NNA for FY26-Q1 by region"
G022 = "Combined Retail and SME revenue for June 2025"
G021 = "Total revenue across the bank for the quarter to 30 June 2025"
G026 = "Active customers across Retail and SME in June 2025"
G027 = "Net interest income across Retail and SME for calendar Q2 2025"
G030 = "Bank revenue for calendar Q1 2025"
REFUSED = "Which retail customers are also wealth households, and what are their emails?"
NO_METADATA = "no measure metadata in this arm"
LINE = re.compile(r"^gate (passed|refused \d+) · (\d+) checked quer(y|ies) · SQL by (\S+) · text by (.+)$")
SUMS = ("sum_with_verdict", "sum_without_verdict", "parts_with_reason")


def present(a, e):
    from kge.services.ask.engine.present import present as build
    return build(a, e)


def questions():
    from kge.eval import sets
    asker = profile_of().asking(None)
    for name in SETS:
        for it in sets.items(BUILD / f"{name}.json"):
            yield f"{name}:{it['id']}", it["question"], it.get("principal") or asker


@pytest.fixture(scope="module")
def answered(eng):
    """Every question's answer on each arm, on rules, as the routes give them: the presets as /answer does (Engine.answer),
    graph as /ask does (Combined.ask), the agent's control and treatment as /agent does (Agent.ask); each with its display."""
    combined, agent = Combined(eng, llm=None), Agent(eng, llm=None)
    out = {}
    for key, q, who in questions():
        for arm in ("schema", "catalog", "graph"):
            out[f"{key}:{arm}"] = runtrace.run("answer", lambda: eng.answer(q, who, config=PRESETS[arm]), graph=eng.g)
        out[f"{key}:ask"] = runtrace.run("ask", lambda: combined.ask(q, who, "analysis"), graph=eng.g)
        for arm in ("control", "treatment"):
            out[f"{key}:{arm}"] = runtrace.run("agent", lambda: agent.ask(q, who, arm=arm), graph=eng.g)
    return out


@pytest.fixture(scope="module")
def displays(eng, answered):
    out, moved = {}, []
    for key, a in answered.items():
        before = json.dumps(a, sort_keys=True, default=str)
        out[key] = present(a, eng)
        if json.dumps(a, sort_keys=True, default=str) != before:
            moved.append(key)
    assert not moved, f"present() changed the answer it was given: {moved[:5]}"
    return out


def held(a: dict) -> set[float]:
    """Every value the answer holds: its figure, its grouped rows and the codes they are grouped by where a code is a
    number (a source system's id, 100, since pack 1.5.0), its parts and its figures."""
    vals = [a.get("figure")] + list((a.get("rows") or {}).values()) + [f.get("value") for f in a.get("figures") or []]
    vals += [float(k) for k in (a.get("rows") or {}) if isinstance(k, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", k)]
    vals += [p.get("value") for p in a.get("plan") or [] if isinstance(p, dict)]
    vals += list(a["parts"].values()) if isinstance(a.get("parts"), dict) else [p.get("value") for p in a.get("parts") or []]
    return {float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)}


def numbers_of(t: dict) -> list:
    keys = [n for n, c in enumerate(t["columns"]) if c["type"] == "number"]
    return [r[k] for r in t["rows"] for k in keys] + ([t["total"]["value"]] if t["total"] else [])


def bound(a: dict) -> set[int]:
    return {f["cite"] for f in a.get("figures") or [] if f.get("cite")}


def kept(d: dict, n: int) -> bool:
    """The citation n is on the display: a marker in the sentence, the table's caption, or its Source column."""
    t = d["table"] or {}
    src = [k for k, c in enumerate(t.get("columns") or []) if c["type"] == "cite"]
    return f"[{n}]" in d["headline"]["text"] or t.get("cite") == n or any(r[k] == n for r in t.get("rows") or [] for k in src)


def same_metadata(parts: list[dict]) -> bool:
    ok = [p for p in parts if p.get("value") is not None]
    keys = {(p.get("grain"), p.get("currency"), p.get("calendar")) for p in ok}
    return len(ok) >= 2 and len(keys) == 1 and all(next(iter(keys))) and {p.get("conversion") for p in ok} == {"none"}


def parts_of(a: dict) -> list[dict]:
    return a["plan"] if isinstance(a.get("plan"), list) else a.get("figures") or []


def test_every_display_builds_and_holds_only_the_answer_s_numbers(answered, displays):
    bad = []
    for key, a in answered.items():
        d = displays[key]
        if "error" in d or d.get("v") != 1:
            bad.append((key, d.get("error")))
            continue
        assert set(d) >= {"v", "headline", "table", "chart", "chart_note", "queries", "checked"}, key
        t, q, text = d["table"], a["question"], d["headline"]["text"]
        if not t:
            assert text == (a.get("answer_text") or a.get("reason") or ""), (key, text)
            assert d["chart"] is None, key
            continue
        stray = [v for v in numbers_of(t) if v is not None and float(v) not in held(a)]
        assert not stray, (key, stray)
        if t["total"]:
            assert t["total"]["value"] == a["figure"], key
        docs = [x for x in (a.get("citations") or []) + (a.get("references") or []) if isinstance(x, dict)]
        known = names(q, a.get("reason") or "", a.get("column") or "", *[f"{x.get('doc') or ''} {x.get('title') or ''}" for x in docs],
                      *[f.get("column") or "" for f in a.get("figures") or []], t["caption"], *[str(r[0]) for r in t["rows"]]) \
            | set(t["caption"].split(" · "))
        stray, markers = wording(text, supported(a, q) | held(a), {c["n"] for c in a.get("citations") or []}, known)
        assert not stray and not markers, (key, text, stray, markers)
    assert not bad, bad[:5]


def test_the_chart_names_the_table_s_columns_and_holds_no_values(displays):
    charted = 0
    for key, d in displays.items():
        c = d.get("chart")
        if not c:
            continue
        charted += 1
        cols = {col["key"]: col for col in d["table"]["columns"]}
        assert set(c) == {"kind", "x", "y", "sort", "max_bars", "by"}, key
        assert c["kind"] == "bar" and c["by"] == "code" and c["sort"] == "desc" and c["max_bars"] == 12, key
        assert c["x"] in cols and cols[c["y"]]["type"] == "number", key
        y = [r[list(cols).index(c["y"])] for r in d["table"]["rows"]]
        assert sum(v is not None for v in y) >= 2, key
    assert charted >= 20, charted


def test_no_chart_and_no_total_where_parts_do_not_add(answered, displays):
    for key, a in answered.items():
        d = displays[key]
        if a.get("behaviour") not in SUMS or not d["table"]:
            continue
        addable = a["behaviour"] == "sum_with_verdict" and a.get("commensurable") is True and same_metadata(parts_of(a))
        if addable:
            assert d["chart"] and d["table"]["total"], key
        else:
            assert d["chart"] is None and d["table"]["total"] is None and d["chart_note"], key


def test_every_query_that_ran_is_listed_with_the_gate_s_verdict(answered, displays):
    for key, a in answered.items():
        d = displays[key]
        sqls = list(a.get("sql_run") or []) + ([a["sql"]] if a.get("sql") else []) \
            + [p["sql"] for p in a.get("plan") or [] if isinstance(p, dict) and p.get("sql")] \
            + [t["sql"] for t in a.get("attempts") or [] if t.get("sql")]
        listed = [q["sql"] for q in d["queries"]]
        assert set(sqls) <= set(listed), (key, set(sqls) - set(listed))
        for q in d["queries"]:
            assert q["gate"] is not None or not q["ran"] or q["role"] == "release", (key, q)
            if q["role"] == "release":
                assert q["gate"] is None and "checks are not kept" in q["note"], (key, q)
        if d["table"] and any(q["ran"] for q in d["queries"]):
            m = LINE.match(d["checked"]["line"])
            assert m and d["checked"]["passed"] == (m.group(1) == "passed"), (key, d["checked"])
            assert m.group(5) == d["headline"]["by"], key


def test_every_display_keeps_the_answer_s_citations(answered, displays):
    for key, a in answered.items():
        d = displays[key]
        if not d["table"]:
            continue
        missing = [n for n in bound(a) if not kept(d, n)]
        assert not missing, (key, missing)


def test_on_rules_the_text_is_the_template_s(answered, displays):
    for key, a in answered.items():
        assert displays[key]["headline"]["by"] == "template", (key, displays[key]["headline"])


def key_of(answered, question: str, arm: str) -> str:
    found = [k for k, a in answered.items() if a["question"] == question and k.endswith(":" + arm)]
    assert found, (question, arm)
    return found[0]


def test_nna_by_division_is_a_sentence_a_table_largest_first_and_two_bars(answered, displays):
    for arm in ("graph", "ask"):
        d = displays[key_of(answered, W01, arm)]
        t = d["table"]
        assert [c["key"] for c in t["columns"]] == ["group", "value"]
        assert t["columns"][0]["label"] == "Division" and t["columns"][1]["currency"] == "USD"
        assert [r[:2] for r in t["rows"]] == [["EAST", 12371673.22], ["WEST", 8831519.54]]
        assert t["caption"] == "Net New Assets · FY26-Q1 · by division · USD" and t["total"] is None
        assert d["chart"] == {"kind": "bar", "x": "group", "y": "value", "sort": "desc", "max_bars": 12, "by": "code"}
        assert d["headline"] == {"text": "Net New Assets for FY26-Q1 by division, in USD: EAST 12,371,673.22; WEST 8,831,519.54 (wealth · flow.amount_usd).",
                                 "by": "template"}
        assert [q["role"] for q in d["queries"]] == ["release", "figure"]
        assert d["checked"]["line"] == "gate passed · 2 checked queries · SQL by planner · text by template"


def test_on_the_agent_s_graph_on_arm_the_grouped_answer_keeps_its_source(answered, displays):
    a, d = answered[key_of(answered, W01, "treatment")], displays[key_of(answered, W01, "treatment")]
    assert bound(a) == {1} and d["table"]["cite"] == 1
    assert d["headline"]["text"].endswith("WEST 8,831,519.54 [1].")


def test_nna_by_region_draws_three_bars(answered, displays):
    d = displays[key_of(answered, W14, "ask")]
    assert [r[0] for r in d["table"]["rows"]] == ["NORTHEAST", "PACIFIC", "MIDWEST"] and d["chart"]


def test_parts_that_add_are_charted_with_their_total(answered, displays):
    for arm in ("graph", "ask", "treatment"):
        d = displays[key_of(answered, G022, arm)]
        t = d["table"]
        assert [r[:2] for r in t["rows"]] == [["Retail Banking", 13287.67], ["SME Banking", 321130.65]], arm
        assert t["total"] == {"label": "Total", "value": 334418.32} and d["chart"]["x"] == "unit" and d["chart_note"] is None, arm
        assert d["checked"]["line"] == "gate passed · 2 checked queries · SQL by planner · text by template", arm


def test_with_no_measure_metadata_parts_are_not_charted_and_the_card_says_why(answered, displays):
    for q in (G022, G027):
        for arm in ("schema", "catalog", "control"):
            d = displays[key_of(answered, q, arm)]
            assert d["chart"] is None and d["table"]["total"] is None, (q, arm)
            if answered[key_of(answered, q, arm)].get("behaviour") in SUMS:
                assert NO_METADATA in d["chart_note"], (q, arm)


def test_parts_the_graph_will_not_add_get_its_own_reason(answered, displays):
    for q in (G021, G026, G030):
        a, d = answered[key_of(answered, q, "ask")], displays[key_of(answered, q, "ask")]
        assert a["behaviour"] == "parts_with_reason" and d["chart"] is None and d["chart_note"] == a["reason"], q


def test_mixed_period_parts_do_not_put_one_period_in_the_caption(answered, displays):
    d = displays[key_of(answered, G021, "ask")]
    assert d["table"]["caption"] == "Revenue · by unit"


def test_a_source_column_is_shown_only_when_a_row_has_a_source(answered, displays):
    plain = displays[key_of(answered, W01, "ask")]["table"]
    cited = displays[key_of(answered, W01, "treatment")]["table"]
    assert "source" not in [c["key"] for c in plain["columns"]]
    assert "source" in [c["key"] for c in cited["columns"]]


def test_a_count_is_never_labelled_money(answered, displays):
    d = displays[key_of(answered, G026, "ask")]
    cur = [k for k, c in enumerate(d["table"]["columns"]) if c["key"] == "currency"]
    assert cur and all(r[cur[0]] is None for r in d["table"]["rows"])
    for key, a in answered.items():
        t = displays[key]["table"]
        if not t or not isinstance(a.get("plan"), list) or "currency" not in [c["key"] for c in t["columns"]]:
            continue
        k = [c["key"] for c in t["columns"]].index("currency")
        for p, r in zip([p for p in a["plan"]], t["rows"]):
            if re.match(r"(?is)\s*select\s+count\s*\(", p.get("sql") or ""):
                assert r[k] is None, (key, p["sql"])


def test_a_refusal_has_no_table_and_says_why(eng):
    a = runtrace.run("ask", lambda: Combined(eng, llm=None).ask(REFUSED, eng.ASKER, "analysis"), graph=eng.g)
    d = present(a, eng)
    assert d["table"] is None and d["chart"] is None and d["checked"] is None
    assert d["headline"]["text"] == a["reason"]


def test_the_answer_s_notes_are_on_the_display_as_it_gives_them(eng):
    """The card without the switch shows an answer's notes (as reported, the latest released month); the rich card must too."""
    a = runtrace.run("ask", lambda: Combined(eng, llm=None).ask("NNA for March 2025 as reported", eng.ASKER, "analysis"), graph=eng.g)
    assert a["behaviour"] == "figure" and any("as reported" in n for n in a["notes"]), a.get("notes")
    assert present(a, eng)["notes"] == a["notes"]
    assert present(grouped({"N1": 1.0, "S2": 2.0}), eng)["notes"] == []


def grouped(rows: dict) -> dict:
    return {"question": "Flows for FY26-Q1 by office", "behaviour": "figure", "figure": None, "unit": "wealth", "column": "flow.amount_usd",
            "group_by": "office.office_code", "rows": rows, "sql": "SELECT office.office_code, sum(flow.amount_usd) FROM flow GROUP BY 1",
            "guard": {"passed": True, "problems": [], "failed_at": None, "checks": ["read-only"]},
            "trace": [{"step": "parse", "term": "Flows", "period": "fiscal_quarter FY26-Q1"}]}


def test_a_negative_value_is_charted_and_thirty_groups_keep_every_row(eng):
    d = present(grouped({"N1": 500.0, "S2": -125.5}), eng)
    assert d["chart"] and [r[1] for r in d["table"]["rows"]] == [500.0, -125.5]
    many = {f"OFF{n:02d}": float(n * 10 - 40) for n in range(30)}
    d = present(grouped(many), eng)
    assert len(d["table"]["rows"]) == 30 and d["chart"]["max_bars"] == 12
    text = d["headline"]["text"]
    assert "OFF29 250" in text and "OFF00 -40" in text and "OFF15" not in text, text


def test_zero_or_one_valued_group_has_no_chart_and_says_why(eng):
    for rows in ({}, {"ONLY": 4.0}, {"EMPTY": None}):
        d = present(grouped(rows), eng)
        assert d["chart"] is None and d["chart_note"] == "fewer than two groups have a value"


def test_an_unknown_shape_gives_an_error_and_never_raises(eng):
    for odd in ({"behaviour": "figure", "rows": {"A": "not a number", "B": 2}}, {"behaviour": "sum_with_verdict", "plan": "x"}, None):
        d = present(odd, eng)
        assert d["v"] == 1 and "error" in d, odd


def mock(eng, mode: str, question: str, arm: str) -> dict:
    from kge.adapters.model import LLM
    agent = Agent(eng, llm=LLM("mock", "mock", mode=mode))
    return runtrace.run("agent", lambda: agent.ask(question, eng.ASKER, arm=arm), graph=eng.g)


def test_on_the_mock_parts_are_charted_on_the_treatment_arm_only(eng):
    t = present(mock(eng, "careful", G022, "treatment"), eng)
    assert t["chart"] and t["table"]["total"]["value"] == 334418.32 and t["headline"]["by"] == "mock:mock"
    c = present(mock(eng, "careful", G022, "control"), eng)
    assert c["chart"] is None and c["table"]["total"] is None
    assert c["chart_note"] == f"the reply's total is not supported by the graph's measure metadata: {NO_METADATA}"
    assert c["checked"]["line"] == "gate passed · 2 checked queries · SQL by mock:mock · text by mock:mock"


def test_mock_text_is_labelled_mock_never_model(eng):
    a = mock(eng, "invented_number", G022, "treatment")
    assert a["phrased_by"].startswith("template (model text")
    d = present(a, eng)
    assert d["headline"]["by"].startswith("template (mock text") and "model" not in d["headline"]["by"]
    assert "model" not in d["checked"]["line"]


def test_on_the_mock_a_refused_try_is_listed_and_named_in_the_line(eng):
    d = present(mock(eng, "restricted_column", "Total retail revenue for June 2025", "treatment"), eng)
    tries = [(q["role"], q["try"], q["gate"]["passed"]) for q in d["queries"]]
    assert tries == [("try", 1, False), ("try", 2, True)]
    assert "restricted personal data" in d["queries"][0]["gate"]["problems"][0]
    assert d["checked"] == {"line": "gate refused 1 · 2 checked queries · SQL by mock:mock · text by mock:mock", "passed": False,
                            "about": d["checked"]["about"]}


def test_a_refused_release_rule_query_keeps_its_release_role(eng):
    a = grouped({"EAST": 1.0, "WEST": 2.0})
    a.update(behaviour="refuse", figure=None, rows=None, context={"context": "nna"},
             sql="SELECT month_end_date, is_released, release_date FROM release_calendar",
             guard={"passed": False, "problems": ["column refused"], "failed_at": "static", "checks": ["columns"]})
    q = present(a, eng)["queries"]
    assert len(q) == 1 and q[0]["role"] == "release" and not q[0]["ran"] and not q[0]["gate"]["passed"]


@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    return scratch_build(tmp_path_factory.mktemp("present") / "build")


@pytest.fixture(scope="module")
def client(scratch):
    import kge.app as appmod
    from fastapi.testclient import TestClient

    from kge.services.identity.service import token_for
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MODEL_PROVIDER", "none")
        for name, value in (("PROFILE", profile_of(scratch)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
            mp.setattr(appmod, name, value)
        with TestClient(appmod.app) as c:
            c.as_ = lambda who: {"authorization": f"Bearer {token_for(run_of(scratch), who)}"}
            yield c
        appmod._engine.close() if appmod._engine else None


VOLATILE = ("run_id", "started_at", "total_ms")


def steady(a: dict) -> dict:
    """The answer less its display and what a clock or a run id puts in its run trace."""
    rt = {k: v for k, v in a["run_trace"].items() if k not in VOLATILE}
    rt["stages"] = [{k: v for k, v in s.items() if k != "ms"} for s in rt["stages"]]
    rt["llm_calls"] = [{k: v for k, v in c.items() if k != "ms"} for c in rt["llm_calls"]]
    return {**{k: v for k, v in a.items() if k not in ("display", "run_trace")}, "run_trace": rt}


ROUTES = (("/answer", {"arm": "graph"}), ("/ask", {}))


def test_no_display_unless_the_page_asks_and_the_same_answer_when_it_does(client):
    who = client.as_("analyst_enterprise")
    for path, extra in ROUTES:
        for q in (W01, G022, REFUSED):
            plain = client.post(path, json={"question": q, **extra}, headers=who)
            off = client.post(path + "?display=0", json={"question": q, **extra}, headers=who)
            rich = client.post(path + "?display=1", json={"question": q, **extra}, headers=who)
            assert plain.status_code == off.status_code == rich.status_code == 200, (path, q, plain.text)
            assert "display" not in plain.json() and "display" not in off.json(), (path, q)
            assert rich.json()["display"]["v"] == 1 and "error" not in rich.json()["display"], (path, q, rich.json()["display"])
            assert steady(rich.json()) == steady(plain.json()) == steady(off.json()), (path, q)


def test_agent_answers_keep_the_existing_card_even_when_display_is_requested(client):
    who = client.as_("analyst_enterprise")
    for arm in ("treatment", "control"):
        answer = client.post("/agent?display=1", json={"question": W01, "arm": arm}, headers=who)
        assert answer.status_code == 200 and "display" not in answer.json()


def test_the_context_service_never_carries_a_display(client):
    who = client.as_("analyst_enterprise")
    for q in (W01, G022):
        r = client.post("/context?display=1", json={"question": q, "arm": "treatment"}, headers=who)
        assert r.status_code == 200 and '"display"' not in r.text


def test_harness_records_are_the_same_after_the_page_asked_for_displays(client):
    import kge.app as appmod
    from kge.eval import harness
    arms = harness.Arms(appmod.engine(), llm=None)
    asker = profile_of().asking(None)
    items = [{"id": "W01", "kind": "within", "question": W01, "figure": None}, {"id": "G022", "kind": "cross", "question": G022, "figure": None}]

    def records() -> str:
        out = []
        for arm in harness.ARM_NAMES:
            for it in items:
                ans = arms.ask(arm, it["question"], asker)
                out.append({"arm": arm, "id": it["id"], "answer": harness.compact(ans), "run_trace": steady(ans)["run_trace"]})
        return json.dumps(out, sort_keys=True, default=str)
    before = records()
    who = client.as_("analyst_enterprise")
    for path, extra in ROUTES:
        for it in items:
            assert "display" in client.post(path + "?display=1", json={"question": it["question"], **extra}, headers=who).json()
    assert records() == before


def test_a_record_keeps_what_it_kept_and_never_a_display():
    from kge.eval import harness
    assert harness.KEEP == ("behaviour", "figure", "unit", "column", "join_path", "sql", "queries", "parts", "commensurable",
                            "missing_parts", "joins_refused", "cite", "citations_rejected", "denied_warehouses", "reason",
                            "phrased_by", "answer_text")
    assert "display" not in harness.compact({"behaviour": "figure", "figure": 1.0, "display": {"v": 1}})


def imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out |= {node.module} | {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_only_the_api_s_routes_build_a_display():
    for path in sorted((DEMO / "kge" / "eval").glob("*.py")) + sorted((DEMO / "kge" / "services").rglob("*.py")) + [DEMO / "kge" / "app.py"]:
        if path.relative_to(DEMO).as_posix() in ("kge/services/ask/routes.py", "kge/services/ask/engine/present.py"):
            continue
        assert not {"kge.services.ask.engine.present", "kge.services.ask.engine.present.present"} & imports(path), f"{path.name} imports kge.services.ask.engine.present"
        assert "display=" not in path.read_text(), f"{path.name} asks for a display"
    tree = ast.parse((DEMO / "kge" / "services" / "ask" / "routes.py").read_text())
    calls = {}
    for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        calls[fn.name] = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    builders = {name for name, called in calls.items() if "present" in called}
    assert len(builders) == 1, builders
    helper = builders.pop()
    assert {name for name, called in calls.items() if helper in called} == {"answer", "ask"}


def test_present_names_no_client():
    from tests.unit.test_client_nouns import platform_files
    assert DEMO / "kge" / "services" / "ask" / "engine" / "present.py" in platform_files()
