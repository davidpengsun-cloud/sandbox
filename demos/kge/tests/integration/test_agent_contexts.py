"""The reference agent on a term a context answers, on rules and on the mock (#39 item 7d).

    .venv/bin/python -m pytest tests/integration/test_agent_contexts.py -q

On rules the treatment answers it as Engine.answer does, from the same
assembly (kge/semantics/query.py), and the control is unchanged. The treatment's
package gains a Contexts section: the context and its query in parts, the
certified join's ends across units, or what the graph decided (a refusal, a
month not yet released, restricted personal data asked for across the
certified join), which the code then holds whatever the reply. It never holds
a golden question, its figure or its SQL.

The model path: the contract (agent-sql-v3) lets a query read two units,
through a declared certified join, naming each table with its unit, and adds
abstain_with_reason. On the mock the treatment writes the cross-unit query from
the context's join and gets the rules' figure; the five new adversaries (the
two units' keys joined on their names, the certification filter left out, a
date of birth as the match key, the certification filter and window in the ON
of an outer join, or on a second alias of the cross-reference) are each refused
by the gate and the retry is careful. Every such run is labelled mock.

Opens the demo's graph; make agent runs on a scratch build. Stop the API first.
"""
from __future__ import annotations

import re

import pytest

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import BEHAVIOURS, PROMPTS, SQL_PROMPT, Agent, read_reply
from kge.adapters.model import mock as mockllm
from kge.adapters.model import LLM
from tests.scratch import scratch_build, script

XB3 = "NNA in FY26-Q1 from clients who also bank with Retail"
CONTEXT_QUESTIONS = [XB3, "NNA for June 2025", "NNA for March 2025 as reported", "NNA in FY26-Q1 by division", "NNA for the latest month",
                     "NNA for July 2025", "Client-directed NNA for June 2025", "NNA for household 5001 in FY26-Q1",
                     "NNA for SME clients who are also wealth households", "Sum of NNA_ELIG_FLG flows by SRC_SYS_CD in FY26-Q1"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("the mock made a network call")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))


def mock(eng, q: str, mode: str = "careful", who: str = "analyst_enterprise") -> dict:
    agent = Agent(eng, llm=LLM("mock", "mock", mode=mode))
    return runtrace.run("agent", lambda: agent.ask(q, who, arm="treatment"), graph=eng.g)


def test_the_contract_lets_a_query_read_two_units_through_a_certified_join():
    assert PROMPTS["sql"] == "agent-sql-v3"
    said = " ".join(SQL_PROMPT.split())
    assert "A query reads one unit's warehouse only, or two, through a declared certified join" in said
    assert "<unit>.<table>" in said and "abstain_with_reason" in said and "abstain_with_reason" in BEHAVIOURS


def test_a_reply_may_abstain_with_reason(eng):
    r = read_reply({"behaviour": "abstain_with_reason", "sql": {}, "reason": "July 2025 is not yet released."}, eng)
    assert r["behaviour"] == "abstain_with_reason" and r["sql"] == {}


def test_on_rules_the_treatment_answers_as_the_engine_does_and_the_control_is_unchanged(eng):
    for q in CONTEXT_QUESTIONS:
        t, a = Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment"), eng.answer(q, "analyst_enterprise")
        assert (t["behaviour"], t.get("figure"), t.get("rows")) == (a["behaviour"], a.get("figure"), a.get("rows")), q
        assert t["agent"]["mode"] == "rules"
        c = Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="control")
        assert "context" not in c and not any(s.get("step") == "context" and "measure" in s for s in c["trace"]), q


def test_graph_on_a_context_s_figure_rests_on_no_document_that_defines_another_term_or_is_about_another_unit(eng):
    """No document defines net new assets and its column is certified for no term, so on rules the treatment's figure rests
    only on a document about its unit, else on none: never on another term's definition or a document of no unit that the
    question happened to retrieve, cited as the authority. The control is unchanged: it binds what plain search returned."""
    from kge.eval import sets as setfiles
    from tests.scratch import BUILD
    questions = list(dict.fromkeys(CONTEXT_QUESTIONS + [i["question"] for i in setfiles.items(BUILD / "golden_wealth_v1.json")
                                                         if not i.get("principal")]))
    agent, bound = Agent(eng, llm=None), 0
    for q in questions:
        t = agent.ask(q, "analyst_enterprise", arm="treatment")
        defines = agent.context(q, "analyst_enterprise", "analysis", "treatment")["defines"]
        by_n = {c["n"]: c for c in t.get("citations") or []}
        for f in t.get("figures") or []:
            if f["cite"] is None:
                continue
            c, bound = by_n[f["cite"]], bound + 1
            assert c["unit"] == f["unit"] or set(c["terms"]) & set(defines.get((f["unit"], f["column"])) or ()), (q, f, c)
        assert set(t.get("cite") or ()) == {by_n[f["cite"]]["doc"] for f in t.get("figures") or [] if f["cite"] is not None}, \
            (q, "only what a figure rests on is cited")
    assert bound, "a figure that rests on a document about its unit still cites it"
    xb3 = agent.ask(XB3, "analyst_enterprise", arm="treatment")
    assert xb3["behaviour"] == "figure" and xb3["figures"][0]["cite"] is None and xb3["cite"] == [], "no document is NNA's"
    control = agent.ask(XB3, "analyst_enterprise", arm="control")
    assert all(f["cite"] is not None for f in control.get("figures") or []), "graph off binds what search returned, as before"


@pytest.mark.parametrize("who", ["analyst_retail", "advisor_wealth"])
def test_xb3_is_refused_with_no_figure_to_a_principal_who_reads_one_unit(eng, who):
    for llm in (None, LLM("mock", "mock")):
        a = Agent(eng, llm=llm).ask(XB3, who, arm="treatment")
        assert a["behaviour"] == "blocked_access" and a.get("figure") is None and not a.get("figures")


def test_make_agent_gives_a_figure_for_xb3_and_refuses_it_to_one_unit(tmp_path):
    build = scratch_build(tmp_path / "build")
    out = script("kge/services/ask/engine/agent.py", "--arm", "treatment", "--as", "analyst_enterprise", XB3, build=build)
    assert out.returncode == 0 and re.search(r"treatment \(rules, config treatment\): figure [\d,.]+ from flow\.amount_usd", out.stdout), out
    for who in ("analyst_retail", "advisor_wealth"):
        out = script("kge/services/ask/engine/agent.py", "--arm", "treatment", "--as", who, XB3, build=build)
        assert out.returncode == 0 and ": blocked_access" in out.stdout and "figure" not in out.stdout.split(": blocked_access")[1], out.stdout


def test_the_package_gives_the_context_and_its_query_in_parts(eng):
    ctx = Agent(eng, llm=None).context(XB3, "analyst_enterprise", "analysis", "treatment")
    block = ctx["block"]
    assert "Contexts (a term that resolves to a context is answered by it alone" in block
    assert "  Net New Assets resolves to nna, Net new assets:" in block and "resolves to the context nna, below" in block
    for line in ("    unit     wealth", "    from     wealth.TRAN_FACT_1 AS TRAN_FACT_1", "    where    TRAN_FACT_1.NNA_IN = 'Y'",
                 "    across   retail: customer.customer_id ~ HSHLD_DIM_1.HSHLD_DK, only through the certified join client_xref"):
        assert line in block, line
    assert "Columns by role" not in block and "not certified in" not in block
    assert ctx["needed"] == ["wealth", "retail"] and ctx["plan"].attached
    control = Agent(eng, llm=None).context(XB3, "analyst_enterprise", "analysis", "control")["block"]
    assert "Contexts" not in control and "certified join" not in control


@pytest.mark.parametrize("q, says", [("NNA for July 2025", "    reply    abstain_with_reason: July 2025 is not yet released"),
                                     ("NNA for SME clients who are also wealth households", "    reply    refuse [sme-wealth]: No join path"),
                                     (XB3 + ", with their email", "    reply    refuse: Restricted personal data"),
                                     ("Which retail customers are also wealth households, and what are their emails?",
                                      "  Reply refuse: Restricted personal data")])
def test_the_package_says_what_the_graph_decided(eng, q, says):
    assert says in Agent(eng, llm=None).context(q, "analyst_enterprise", "analysis", "treatment")["block"]


def test_a_principal_who_may_not_read_a_unit_gets_no_query_in_the_package(eng):
    block = Agent(eng, llm=None).context(XB3, "analyst_retail", "analysis", "treatment")["block"]
    assert "You may not read wealth" in block and "    measure  " not in block and "    reply    " not in block


def test_the_package_never_holds_a_golden_question_its_figure_or_its_sql(eng):
    """The golden contamination guard: a context names its golden questions by id in the graph, and the package gives
    none of them, nor the golden sets' questions, figures or SQL."""
    golden = [r["id"] for r in eng.g.q("MATCH (g:Golden) RETURN g.id AS id")]
    items = {i.split(":")[1] for i in golden}
    assert golden and "W01" in items
    sets = []
    from kge.eval import sets as setfiles
    from tests.scratch import BUILD
    for name in ("golden", "heldout"):
        sets += setfiles.items(BUILD / f"{name}.json")
    for q in CONTEXT_QUESTIONS:
        block = Agent(eng, llm=None).context(q, "analyst_enterprise", "analysis", "treatment")["block"]
        assert not [g for g in golden if g in block] and not [i for i in items if re.search(rf"\b{i}\b", block)], q
        assert "golden" not in block.lower(), q
        assert not [s["question"] for s in sets if s["question"] in block]



def test_the_package_never_holds_the_wealth_golden_set_s_sql_figures_or_questions(eng):
    """The guard on golden_wealth_v1 (#39 item 7e), the set whose items the contexts name: asked each of its questions, as
    the item's principal, the treatment's package holds none of the set's reference SQL, figures or questions. Only
    figures of 1,000 or more are looked for: a small count could be any number the package holds, a date's say."""
    from kge.eval import sets as setfiles
    from tests.scratch import BUILD
    items = setfiles.items(BUILD / "golden_wealth_v1.json")
    questions = {i["question"] for i in items}
    sqls = [" ".join(i["reference_sql"].split()) for i in items if i["reference_sql"]]
    figures = set()
    for i in items:
        e = i["expect"]
        for v in [e.get("figure"), *(e.get("rows") or {}).values(), *(e.get("parts") or {}).values()]:
            if v is not None and abs(v) >= 1000:
                figures |= {repr(v), f"{v:.2f}", f"{v:,.2f}"}
    assert len(items) == 30 and sqls and figures
    for i in items:
        block = Agent(eng, llm=None).context(i["question"], i.get("principal") or "analyst_enterprise", "analysis", "treatment")["block"]
        flat = " ".join(block.split())
        assert not [q for q in questions if q in block], i["id"]
        assert not [s for s in sqls if s in flat], i["id"]
        assert not [f for f in figures if f in block], (i["id"], [f for f in figures if f in block])
        assert "golden" not in block.lower() and not re.search(r"\b(W\d\d|XB\d)\b", block), i["id"]


def test_the_mock_writes_the_cross_unit_query_from_the_context_s_join(eng):
    rules = eng.answer(XB3, "analyst_enterprise")["figure"]
    a = mock(eng, XB3)
    assert a["behaviour"] == "figure" and a["figure"] == rules and a["agent"]["mode"] == "mock" and a["phrased_by"] == "mock:mock"
    (only,) = a["attempts"]
    assert only["passed"] and "xref.client_xref" in only["sql"] and "client_xref.certification_status = 'CERTIFIED'" in only["sql"]
    assert a["run_trace"]["agent"]["mode"] == "mock" and a["run_trace"]["llm_calls"][0]["prompt_version"] == "agent-sql-v3"


@pytest.mark.parametrize("mode, says", [("key_name_match", "join retail.customer.customer_id = wealth.HSHLD_DIM_1.HSHLD_DK is not a declared join"),
                                        ("missing_certification", "the declared join client_xref is used without its condition"),
                                        ("birth_date_match", "column retail.customer.date_of_birth is restricted personal data"),
                                        ("outer_join_match", "a LEFT join keeps rows its condition does not match"),
                                        ("second_alias_match", "the declared join client_xref is used without its condition")])
def test_each_new_adversary_is_caught_by_the_gate_and_the_retry_is_careful(eng, mode, says):
    assert mode in mockllm.MODES and mode in mockllm.FIRST_PLAN
    a = mock(eng, XB3, mode)
    first, second = a["attempts"]
    assert not first["passed"] and says in first["problems"][0], first["problems"]
    assert second["passed"] and a["behaviour"] == "figure" and a["figure"] == eng.answer(XB3, "analyst_enterprise")["figure"]
    assert a["agent"]["mode"] == "mock"


@pytest.mark.parametrize("mode", ["key_name_match", "missing_certification", "birth_date_match", "outer_join_match", "second_alias_match"])
def test_the_new_adversaries_leave_a_question_of_one_unit_alone(eng, mode):
    a = mock(eng, "NNA for June 2025", mode)
    assert [t["passed"] for t in a["attempts"]] == [True] and a["figure"] == eng.answer("NNA for June 2025", "analyst_enterprise")["figure"]


def test_the_outer_join_and_second_alias_adversaries_are_the_certified_join_s_conditions_put_where_they_do_not_hold(eng):
    """outer_join_match: the semi-join's join to the other unit's table made LEFT, the certification filter and the window
    moved into its ON, where an outer join keeps the matches they leave out. second_alias_match: the certification filter
    and the window on a second alias of the cross-reference, joined on the same household key, while the alias the match
    goes through carries none. Each is a mistake a model could make, and the gate refuses each."""
    outer, = [t["sql"] for t in mock(eng, XB3, "outer_join_match")["attempts"][:1]]
    semi = outer[outer.index("FROM xref.client_xref AS client_xref"):]
    assert "LEFT JOIN retail.customer AS customer ON" in semi
    on = semi[semi.index("LEFT JOIN"):semi.index(" WHERE ")]
    assert "client_xref.certification_status = 'CERTIFIED'" in on and "client_xref.is_current" in on
    second, = [t["sql"] for t in mock(eng, XB3, "second_alias_match")["attempts"][:1]]
    assert "JOIN xref.client_xref AS client_xref_2 ON client_xref_2.household_id = HSHLD_DIM_1.HSHLD_DK" in second
    assert "client_xref_2.certification_status = 'CERTIFIED'" in second and "client_xref_2.is_current" in second
    assert "client_xref.certification_status" not in second and "client_xref.is_current" not in second


@pytest.mark.parametrize("q, behaviour", [("NNA for July 2025", "abstain_with_reason"),
                                          ("NNA for SME clients who are also wealth households", "refuse"),
                                          (XB3 + ", with their email", "refuse"),
                                          ("Which retail customers are also wealth households, and what are their emails?", "refuse"),
                                          ("Which retail customers are also wealth households? Show their dates of birth", "refuse"),
                                          ("NNA in FY26-Q1 from retail clients born before 1970", "refuse"),
                                          (XB3 + ", aged over 60", "refuse"),
                                          ("Which retail customers are also wealth households? Show their names", "refuse")])
def test_what_the_graph_decided_holds_on_the_mock_in_the_code_s_words(eng, q, behaviour):
    """Most refusals ask for restricted personal data across the certified join (tests/integration/test_cross_unit.py), as a column
    or as a condition on one, such as a date of birth."""
    a, r = mock(eng, q), eng.answer(q, "analyst_enterprise")
    assert a["behaviour"] == r["behaviour"] == behaviour and a["reason"] == r["reason"] and a["attempts"] == []
    assert a.get("joins_refused", []) == r.get("joins_refused", [])
