"""The reference agent: one agent, two contexts, the same seam after them.

    .venv/bin/python -m pytest tests/integration/test_agent.py -q

Graph off (control) and graph on (treatment) run the same agent with the same
model, prompt templates and seed; only the prompt's context block differs.
After it, everything is shared: the reply is checked against the contract,
every query goes through the gate with one retry, runs, is bound to a
citation, and the answer is rendered with the number check on any wording.

Offline: no model is called. Where a model is needed, the real client runs
with its HTTP post replaced by a script, so what is tested is what the agent
does with a model's reply. Nothing here is a model result. With no model the
agent runs on rules, and says so.

TWO is golden G029 (two measures, in two units) and KEYS is G032 (a no-join fact
resting on two dictionaries). NOT_FIGURES and ROW_SETS each passed the gate once
and were the answer: figures the data did not give, and the right aggregate over
rows the query changed.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import CONTEXT_CLOSE, CONTEXT_OPEN, PROMPTS, Agent, ContractError, bind, carried, read_reply, supported, wording
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, STAGES, WORDS, RunConfig, parse, question_words
from kge.eval import sets
from kge.adapters.model import LLM
from tests.scratch import BUILD, profile_of, scratch_build

FIG = "Total retail revenue for June 2025"
FIG_SQL = "SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue WHERE monthly_revenue.month_start = DATE '2025-06-01'"
CROSS = "Combined Retail and SME revenue for June 2025"
SME_SQL = "SELECT round(sum(monthly_client_revenue.revenue_usd), 2) FROM monthly_client_revenue WHERE monthly_client_revenue.month_start = DATE '2025-06-01'"
DEFINE = "What is the definition of an active customer?"
EMAIL_SQL = "SELECT count(customer.email) FROM customer"
BAD_JOIN_SQL = "SELECT count(*) FROM complaint JOIN monthly_revenue ON complaint.customer_id = monthly_revenue.account_id"
ACTIVE = "How many retail customers were active in June 2025?"
ACTIVE_SQL = ("SELECT count(*) FROM customer_activity WHERE customer_activity.is_active_flag "
              "AND customer_activity.month_start = DATE '2025-06-01'")
TWO = "Total assets under advice plus corporate credit exposure"
KEYS = "Can retail customers be joined to corporate contacts on customer_id?"
GROUP = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
GROUP_SME = GROUP.replace("Retail", "SME")
GROUP_SAYS = ("Not answered: the question asks about one Retail Banking customer group: how many meet Active Customer, "
              "how many of those had a Complaint, and their Average Balance and Fee Income.")
GROUP_SME_SAYS = "Not certified in SME Banking: Average Balance, Complaint. The answer would rest on a guess."
CE_SQL = "SELECT round(sum(exposure.ead_eur), 2) FROM exposure WHERE exposure.as_of = DATE '2025-06-30'"
NOT_FIGURES = ["SELECT 13287.67", "SELECT 99999.0 AS total FROM monthly_revenue LIMIT 1",
               FIG_SQL.replace("SELECT round(sum(monthly_revenue.total_revenue_usd), 2)", "SELECT round(sum(monthly_revenue.total_revenue_usd), 2) * 2"),
               FIG_SQL.replace("round(sum(monthly_revenue.total_revenue_usd), 2)", "round(sum(monthly_revenue.total_revenue_usd), -3)")]


@pytest.fixture(scope="module")
def golden() -> list[dict]:
    return sets.items(BUILD / "golden.json")


def scripted(monkeypatch, plan=(), phrase=()) -> LLM:
    """The real model client with its HTTP post replaced: each task's replies in turn.
    A reply is a dict (sent as JSON), a string (sent as is) or a function of the prompt."""
    llm = LLM("openai", "gpt-test", "sk-test-secret")
    plans, phrases, prompts = list(plan), list(phrase), []

    def post(url, headers, body):
        prompt = body["messages"][0]["content"]
        prompts.append(prompt)
        queue = plans if CONTEXT_OPEN in prompt else phrases
        reply = queue.pop(0) if queue else {"behaviour": "abstain", "sql": {}, "citations": [], "answer_text": ""}
        reply = reply(prompt) if callable(reply) else reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {"choices": [{"message": {"content": content}}], "model": "gpt-test-2026",
                "usage": {"prompt_tokens": len(prompt.split()), "completion_tokens": len(content.split())}}
    monkeypatch.setattr(llm, "_post", post)
    llm.prompts = prompts
    return llm


def plan(behaviour="figure", sql=None, citations=(), **more) -> dict:
    return {"behaviour": behaviour, "sql": sql or {}, "citations": list(citations), "answer_text": "", **more}


def context_of(prompt: str) -> str:
    return prompt.split(CONTEXT_OPEN, 1)[1].split(CONTEXT_CLOSE, 1)[0].strip("\n")


def without_context(prompt: str) -> str:
    head, rest = prompt.split(CONTEXT_OPEN, 1)
    return head + CONTEXT_OPEN + CONTEXT_CLOSE + rest.split(CONTEXT_CLOSE, 1)[1]


def test_the_agent_arms_are_run_configs_and_the_presets_are_untouched():
    assert AGENT_ARMS == {
        "control": RunConfig("control", resolution="words", context="schema", sql_source="planner", citation_rule="retrieval", renderer="template"),
        "treatment": RunConfig("treatment", resolution="glossary", context="graph", sql_source="planner", citation_rule="tiers", renderer="template"),
    }
    assert list(PRESETS) == ["schema", "catalog", "graph"]
    assert "words" in STAGES["resolution"] and "model" in STAGES["sql_source"]
    for cfg in AGENT_ARMS.values():
        assert cfg.label == cfg.arm and cfg.problems("agent", has_model=False) == []
    for cfg in PRESETS.values():
        assert any("arm must be one of control, treatment" in p for p in cfg.problems("agent", has_model=True))


def test_only_the_agent_writes_every_query_with_the_model():
    cfg = PRESETS["graph"].with_stages(sql_source="model")
    for path in ("answer", "ask"):
        assert any("reference agent" in p for p in cfg.problems(path, has_model=True))
    model = AGENT_ARMS["treatment"].with_stages(sql_source="model", renderer="template+model")
    assert model.problems("agent", has_model=True) == []
    assert any("no model is configured" in p for p in model.problems("agent", has_model=False))
    assert model.label == "treatment[sql_source=model,renderer=template+model]"


def test_words_resolution_maps_no_term(eng):
    assert question_words("How many retail customers were active in June 2025?", eng) == ["customer", "active"]
    assert question_words("Total retail revenue for June 2025", eng) == ["total", "revenue"]
    req = parse("Total assets under advice plus corporate credit exposure", [], eng, aliases=False)
    assert req.terms_found == [] and req.term is None and req.special is None


def test_the_control_guesses_a_column_from_the_schema_by_the_questions_words(eng):
    a = eng.answer(FIG, "analyst_enterprise", config=AGENT_ARMS["control"])
    assert a["arm"] == "control" and a["behaviour"] == "figure"
    assert a["column"] == "monthly_revenue.total_revenue_usd" and a["figure"] == 13287.67
    step = next(t for t in a["trace"] if t["step"] == "map")
    assert step["term"] == WORDS and step["words"] == ["total", "revenue"] and step["certified"] is False


def test_the_control_uses_no_glossary_anywhere(eng):
    """The schema preset's alias list turns these phrases into glossary terms; the control has none."""
    a = eng.answer("Total assets under advice plus corporate credit exposure", "analyst_enterprise", config=AGENT_ARMS["control"])
    assert a["behaviour"] != "refuse" or "Two different measures" not in a.get("reason", "")
    step = next(t for t in a["trace"] if t["step"] == "parse")
    assert step["term"] in (None, WORDS) and step["special"] is None


def test_words_is_a_stage_value_the_baseline_takes_too(eng):
    a = eng.answer(FIG, "analyst_enterprise", config=PRESETS["schema"].with_stages(resolution="words"))
    assert a["arm"] == "schema[resolution=words]" and a["column"] == "monthly_revenue.total_revenue_usd"


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_without_a_model_the_agent_is_the_rule_based_planner_on_its_arm(eng, arm):
    agent = Agent(eng, llm=None)
    a = runtrace.run("agent", lambda: agent.ask(FIG, "analyst_enterprise", arm=arm), graph=eng.g)
    rt = a.pop("run_trace")
    assert a.pop("agent") == {"arm": arm, "mode": "rules", "config": arm, "prompt_versions": None,
                              "sql_source": "planner", "renderer": "template"}
    base = eng.answer(FIG, "analyst_enterprise", config=AGENT_ARMS[arm])
    assert {k: a[k] for k in base if k != "trace"} == {k: v for k, v in base.items() if k != "trace"}, "the planner's answer, unchanged"
    assert a["trace"][:len(base["trace"])] == base["trace"]
    assert set(a) - set(base) == {"figures", "citations", "cite", "citations_rejected", "references"}, "and the evidence step's"
    assert rt["agent"] == {"arm": arm, "mode": "rules", "prompt_versions": None}
    assert rt["llm"] is None and rt["llm_calls"] == [] and rt["config"]["sql_source"] == "planner" and rt["config"]["renderer"] == "template"


def test_on_rules_each_figure_is_bound_by_the_evidence_step_the_model_path_shares(eng):
    agent = Agent(eng, llm=None)
    t = runtrace.run("agent", lambda: agent.ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    gold = t["citations"][0]
    assert t["cite"] == ["MD-REVENUE"] and gold["tier"] == "gold" and gold["cited_as"] == "authority" and gold["chunk"] == "MD-REVENUE#0"
    assert t["figures"] == [{"id": "f1", "unit": "retail", "value": 13287.67, "column": "monthly_revenue.total_revenue_usd",
                             "sql": t["sql"], "join_path": [], "cite": gold["n"]}]
    rt = t["run_trace"]
    assert rt["bound"] == [{"figure": "f1", "doc": "MD-REVENUE", "chunk": "MD-REVENUE#0"}]
    assert {(b["doc"], b["chunk"]) for b in rt["bound"]} <= {(r["doc"], r["chunk"]) for r in rt["retrieved"]}
    assert [c["doc"] for c in rt["citations"]] == ["MD-REVENUE"]
    c = runtrace.run("agent", lambda: agent.ask(FIG, "analyst_enterprise", arm="control"), graph=eng.g)
    hits = [h["doc"] for h in eng.g.retrieve(FIG, "analyst_enterprise", "analysis", k=5)["exact"]]
    assert c["figure"] == 13287.67 and len(c["cite"]) == 1 and c["cite"][0] in hits
    bound = c["run_trace"]["bound"][0]
    assert bound["doc"] == c["cite"][0] and next(r for r in c["run_trace"]["retrieved"] if r["chunk"] == bound["chunk"])["via"] == "vector"
    x = agent.ask(CROSS, "analyst_enterprise", arm="treatment")
    assert x["behaviour"] == "sum_with_verdict" and [f["unit"] for f in x["figures"]] == ["retail", "sme"]
    assert x["cite"] == ["MD-REVENUE"] and all(f["cite"] == x["citations"][0]["n"] for f in x["figures"])
    d = agent.ask("What is the email address of customer 118?", "analyst_enterprise", arm="treatment")
    assert d["behaviour"] == "refuse" and "figures" not in d


def test_the_rule_based_treatment_cites_by_tier(eng):
    a = Agent(eng, llm=None).ask("Which calendar does Wealth Management report on?", "analyst_enterprise", arm="treatment")
    cal = next(c for c in a["citations"] if c["doc"] == "STD-FISCAL-CAL")
    assert a["behaviour"] == "cite_certified" and cal["tier"] == "silver" and cal["attribution"] == "Group Finance, version 2.1"
    assert {"WIKI-002", "EM-005"} <= {r["doc"] for r in a["references"]}


def test_the_rule_based_arms_score_what_the_readme_says(eng, golden):
    from pathlib import Path

    from kge.eval.run_golden import score, uncertified_ids
    unc = uncertified_ids(BUILD)
    heldout = sets.items(BUILD / "heldout.json")
    agent = Agent(eng, llm=None)
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text().split("## The reference agent", 1)[1]
    for arm in AGENT_ARMS:
        ok = {i["id"]: score(i, agent.ask(i["question"], "analyst_enterprise", arm=arm), unc)[0] for i in golden + heldout}
        cells = [f"{sum(ok[i['id']] for i in golden if i['kind'] == k)} of {sum(1 for i in golden if i['kind'] == k)}"
                 for k in ("within", "cross", "shelf", "abstain")] + [f"{sum(ok[i['id']] for i in heldout)} of {len(heldout)}"]
        assert f"| {arm.capitalize()} | " + " | ".join(cells) + " |" in readme, (arm, cells)
        if arm == "treatment":
            assert all(ok[i["id"]] for i in golden)


def test_an_unknown_arm_is_an_error(eng):
    with pytest.raises(ValueError, match="arm must be one of control, treatment"):
        Agent(eng, llm=None).ask(FIG, "analyst_enterprise", arm="schema")


@pytest.mark.parametrize("reply, says", [
    ([], "not a JSON object"),
    ({"behaviour": "guess", "sql": {}, "citations": []}, "behaviour must be one of"),
    ({"behaviour": "figure", "sql": {"retail": FIG_SQL, "sme": SME_SQL}, "citations": []}, "a figure runs one unit's query"),
    ({"behaviour": "sum_with_verdict", "sql": {"retail": FIG_SQL}, "citations": []}, "runs two or more units' queries"),
    ({"behaviour": "refuse", "sql": {"retail": FIG_SQL}, "citations": []}, "a refuse reply runs no query"),
    ({"behaviour": "figure", "sql": {"treasury": FIG_SQL}, "citations": []}, "no unit treasury"),
    ({"behaviour": "figure", "sql": {"retail": ""}, "citations": []}, "sql must map a unit to one query"),
    ({"behaviour": "figure", "sql": {"retail": FIG_SQL}, "citations": ["1"]}, "citations must be a list of source numbers"),
    ({"behaviour": "figure", "sql": {"retail": FIG_SQL}, "citations": [], "joins_refused": ["retail+sme"]}, "joins_refused"),
    ({"behaviour": "figure", "sql": {"retail": FIG_SQL}, "citations": [], "answer_text": 7}, "answer_text must be text"),
])
def test_a_reply_that_breaks_the_contract_names_why(eng, reply, says):
    with pytest.raises(ContractError) as exc:
        read_reply(reply, eng)
    assert any(says in p for p in exc.value.problems), exc.value.problems


def test_a_reply_that_keeps_the_contract_is_normalised(eng):
    r = read_reply({"behaviour": "sum_with_verdict", "sql": {"sme": SME_SQL + ";", "retail": FIG_SQL}, "citations": [2, 1, 2],
                    "joins_refused": ["sme-retail"], "answer_text": " x "}, eng)
    assert list(r["sql"]) == ["retail", "sme"] and r["sql"]["sme"] == SME_SQL
    assert r["citations"] == [2, 1] and r["joins_refused"] == ["retail-sme"] and r["answer_text"] == "x"


@pytest.fixture(scope="module")
def both(eng):
    """The plan prompt each arm sends for the cross question, and each arm's context."""
    out = {}
    for arm in ("control", "treatment"):
        agent = Agent(eng, llm=None)
        out[arm] = agent.context(CROSS, "analyst_enterprise", "analysis", arm)
        out[arm]["prompt"] = agent.prompt("sql", CROSS, out[arm]["block"])
    return out


def test_the_prompt_is_the_same_for_both_arms_but_the_context_block(both):
    c, t = both["control"]["prompt"], both["treatment"]["prompt"]
    assert without_context(c) == without_context(t)
    assert context_of(c) == both["control"]["block"] and context_of(t) == both["treatment"]["block"]
    assert context_of(c) != context_of(t)


def test_the_control_context_is_the_schema_and_plain_search(both, eng):
    ctx = both["control"]
    block = ctx["block"]
    for words in [s["excerpt"] for s in ctx["sources"]] + [s["title"] for s in ctx["sources"]]:
        block = block.replace(words, "")
    assert "monthly_revenue: " in block and "total_revenue_usd DECIMAL" in block and "customer.email" not in block
    assert "account.customer_id -> customer.customer_id" in block or "customer_id -> customer.customer_id" in block
    graph_only = ["gold", "silver", "bronze", "certified", "Data Architecture Board", "Separate customer populations",
                  "Group Finance Data Steward", "Income earned from customers", "No join", "Do not use", "may be added"]
    assert not [w for w in graph_only if w.lower() in block.lower()]
    assert ctx["sources"] and all(set(s) == {"n", "doc", "chunk", "title", "excerpt", "unit", "citable"} for s in ctx["sources"])
    assert all(s["citable"] for s in ctx["sources"])


def test_the_treatment_context_is_the_graph_package(both):
    block = both["treatment"]["block"]
    for part in ["Scope", "Terms", "Revenue: Income earned from customers", "Columns by role", "measure", "period", "keys",
                 "No join between retail and sme", "Data Architecture Board", "may be added", "Do not use",
                 "customer.email", "References, for reasoning only, never cited", "Provenance", "Group Finance Data Steward", "gold"]:
        assert part in block, part
    cited = [s for s in both["treatment"]["sources"] if s["citable"]]
    assert cited and all(s["tier"] in ("gold", "silver") for s in cited)


def test_the_treatment_package_lists_the_declared_joins_of_the_units_in_play(both):
    joins = both["treatment"]["block"].split("Declared joins", 1)[1].split("\n\n", 1)[0]
    assert "  retail: " in joins and "monthly_revenue.account_id = account.account_id" in joins and "daily_balance.account_id = account.account_id" in joins
    assert "  sme: " in joins and "corporate" not in joins and "wealth" not in joins
    assert "Declared joins" not in both["control"]["block"], "the control has its catalogue's references instead"


def test_two_measures_bring_both_their_units_into_the_package(eng):
    ctx = Agent(eng, llm=None).context(TWO, "analyst_enterprise", "analysis", "treatment")
    block = ctx["block"]
    assert "never added together" in block and "No join between corporate and wealth" in block
    roles = block.split("Columns by role", 1)[1].split("\n\n", 1)[0]
    assert "corporate, Credit Exposure:" in roles and "wealth, Assets Under Advice:" in roles
    assert "not certified in" not in block, "the question names corporate for credit exposure, not for assets under advice"
    assert ctx["certified"] == {"corporate": [("exposure", "ead_eur")], "wealth": [("AUA_FACT_1", "AUA_AM")]}


@pytest.mark.parametrize("question, says", [
    ("Total retail credit exposure at 30 June 2025", "not certified in retail"),
    ("Retail revenue for June 2025 from customers whose home branch is in region NW", None),
])
def test_a_term_is_not_certified_only_in_a_unit_the_question_names_for_it(eng, question, says):
    block = Agent(eng, llm=None).context(question, "analyst_enterprise", "analysis", "treatment")["block"]
    assert (says in block) if says else ("not certified in" not in block), block


def test_the_package_says_where_each_term_the_question_names_is_not_certified(eng):
    block = Agent(eng, llm=None).context(GROUP_SME, "analyst_enterprise", "analysis", "treatment")["block"]
    terms = block.split("Terms:\n", 1)[1].split("\n\n", 1)[0]
    said: dict[str, str] = {}
    term = None
    for line in terms.splitlines():
        if line.startswith("    "):
            assert term is not None, terms
            said[term] += line + "\n"
        else:
            term = line.strip().split(":", 1)[0]
            said[term] = ""
    assert list(said) == ["Active Customer", "Average Balance", "Fee Income", "Complaint"]
    assert [t for t in said if "not certified in sme" in said[t]] == ["Average Balance", "Complaint"], terms


@pytest.mark.parametrize("question, says", [(GROUP, GROUP_SAYS), (GROUP_SME, GROUP_SME_SAYS)])
def test_the_package_tells_a_customer_group_from_a_figure(eng, question, says):
    block = Agent(eng, llm=None).context(question, "analyst_enterprise", "analysis", "treatment")["block"]
    group = block.split("One customer group:\n", 1)[1].split("\n\n", 1)[0]
    assert group.startswith("  Reply abstain: " + says), group


@pytest.mark.parametrize("gid", ["G004", "G015", "G019", "G029", "G034"])
def test_a_golden_question_naming_several_terms_is_not_a_customer_group(eng, golden, gid):
    q = next(i for i in golden if i["id"] == gid)
    block = Agent(eng, llm=None).context(q["question"], "analyst_enterprise", "analysis", "treatment")["block"]
    assert "One customer group" not in block
    if "figure" in q["expect"]:
        a = Agent(eng, llm=None).ask(q["question"], "analyst_enterprise", arm="treatment")
        assert a["behaviour"] == "figure" and a["figure"] == float(q["expect"]["figure"])


@pytest.mark.parametrize("question, says", [(GROUP, GROUP_SAYS), (GROUP_SME, GROUP_SME_SAYS),
                                            ("How many active customers in Retail in June 2025 raised a complaint that month?",
                                             "Not answered: the question asks about one Retail Banking customer group: how many meet "
                                             "Active Customer and how many of those had a Complaint.")])
def test_on_rules_the_treatment_gives_no_one_figure_for_a_customer_group(eng, question, says):
    """Graph on, the agent sees the group and decides as /ask does; graph off it cannot see the group and gives one
    figure, which is the gap the A/B measures."""
    agent = Agent(eng, llm=None)
    t = runtrace.run("agent", lambda: agent.ask(question, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert t["behaviour"] == "abstain" and t["reason"].startswith(says), t["reason"]
    assert not {"figure", "figures", "sql", "parts"} & set(t) and not t.get("cite")
    assert t["arm"] == "treatment" and t["agent"]["mode"] == "rules" and t["run_trace"]["config"]["arm"] == "treatment"
    assert t["trace"][0]["term"] == "Active Customer"
    if "SME" in question:
        from kge.services.ask.engine.combined import Combined
        assert Combined(eng, llm=None).ask(question, "analyst_enterprise")["reason"] == t["reason"], "as the graph's /ask says"
    assert agent.ask(question, "analyst_enterprise", arm="control")["behaviour"] == "figure"


def test_a_customer_group_across_retail_and_wealth_is_decided_as_ask_decides_it(eng):
    """Pack 1.1.0 replaced retail-wealth's no-join fact with a certified join, which the runtime reads (#39 item 7d); a
    customer group is built within one unit, so the treatment abstains in /ask's words and refuses no join."""
    from kge.services.ask.engine.combined import Combined
    q = "How many active customers in Retail and Wealth in June 2025 raised a complaint that month?"
    t, a = Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment"), Combined(eng, llm=None).ask(q, "analyst_enterprise")
    assert t["behaviour"] == a["behaviour"] == "abstain" and "joins_refused" not in t and a["joins_refused"] == []
    assert t["reason"] == a["reason"] and "no certified join" not in t["reason"] and "certified join client_xref" in t["reason"]


def test_a_customer_group_the_principal_cannot_read_is_refused(eng):
    a = Agent(eng, llm=None).ask(GROUP, "advisor_wealth", arm="treatment")
    assert a["behaviour"] == "refuse" and "retail" in a["reason"] and "figure" not in a


def test_a_models_figure_for_a_customer_group_is_not_run(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": ACTIVE_SQL}, citations=[1], columns={"retail": "customer_activity.is_active_flag"})])
    a = Agent(eng, llm=llm).ask(GROUP, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "abstain" and a["reason"].startswith(GROUP_SAYS) and a["attempts"] == [] and "figure" not in a
    assert any(t["step"] == "group" and t["overruled"] == "figure" for t in a["trace"])


@pytest.mark.parametrize("who", ["analyst_retail", "advisor_wealth"])
def test_the_package_holds_no_document_of_a_unit_the_principal_cannot_read(eng, monkeypatch, who):
    """The no-join fact and "never added together" are the graph's and stay; the data dictionaries they rest on are
    documents, offered only from readable units."""
    readable = eng.g.readable_units(who)
    ctx = Agent(eng, llm=None).context(TWO, who, "analysis", "treatment")
    assert "never added together" in ctx["block"]
    assert all(s["unit"] in readable or s["unit"] == "" for s in ctx["sources"]), [(s["doc"], s["unit"]) for s in ctx["sources"]]
    for doc, unit in (("DD-CORPORATE", "corporate"), ("DD-WEALTH", "wealth"), ("DD-SME", "sme")):
        assert unit in readable or doc not in ctx["block"], doc
    assert "counterparty_ref" not in ctx["block"], "the corporate dictionary's text"
    llm = scripted(monkeypatch, plan=[plan("cite_certified", citations=list(range(1, 15)), answer_text="See the sources.")])
    a = Agent(eng, llm=llm).ask(TWO, who, arm="treatment")
    assert a["citations"] and all(c["unit"] in readable or c["unit"] == "" for c in a["citations"])


@pytest.mark.parametrize("who", ["analyst_retail", "advisor_wealth"])
def test_on_rules_the_tier_rule_cites_no_dictionary_of_a_unit_the_principal_cannot_read(eng, who):
    readable = eng.g.readable_units(who)
    unit_of = {"DD-RETAIL": "retail", "DD-CORPORATE": "corporate"}
    for a in (Agent(eng, llm=None).ask(KEYS, who, arm="treatment"), eng.answer(KEYS, who, config=AGENT_ARMS["treatment"])):
        assert a["answer"] == "no" and a["fact"]["note"] in a["reason"]
        assert a["cite"] == [d for d in ("DD-CORPORATE", "DD-RETAIL") if unit_of[d] in readable] == [c["doc"] for c in a["citations"]]
        assert [(x["doc"], x["unit"]) for x in a["not_readable"]] == [(d, u) for d, u in sorted(unit_of.items()) if u not in readable]
        assert all(set(x) == {"doc", "unit", "why"} and "may not read" in x["why"] for x in a["not_readable"]), "no title, owner or version"
    full = Agent(eng, llm=None).ask(KEYS, "analyst_enterprise", arm="treatment")
    assert full["cite"] == ["DD-CORPORATE", "DD-RETAIL"] and full["not_readable"] == []


def test_every_chunk_the_package_shows_and_every_bound_chunk_was_retrieved(eng, monkeypatch):
    agent = Agent(eng, llm=None)
    with runtrace.start("agent", eng.g) as t:
        ctx = agent.context(TWO, "analyst_enterprise", "analysis", "treatment")
        rows = {(r["doc"], r["chunk"]) for r in t.rows}
    shown = {(s["doc"], s["chunk"]) for s in ctx["sources"] if s["chunk"]}
    assert ("DD-CORPORATE", "DD-CORPORATE#0") in shown and shown <= rows, shown - rows
    dd = next(s["n"] for s in ctx["sources"] if s["doc"] == "DD-CORPORATE")
    llm = scripted(monkeypatch, plan=[plan(sql={"corporate": CE_SQL}, citations=[dd])])
    rt = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(TWO, "analyst_enterprise", arm="treatment"), graph=eng.g)["run_trace"]
    retrieved = {(r["doc"], r["chunk"]) for r in rt["retrieved"]}
    assert rt["bound"] == [{"figure": "f1", "doc": "DD-CORPORATE", "chunk": "DD-CORPORATE#0"}]
    assert {(b["doc"], b["chunk"]) for b in rt["bound"]} <= retrieved
    assert next(r for r in rt["retrieved"] if r["chunk"] == "DD-CORPORATE#0")["via"] == "rests_on"


def test_the_restricted_columns_are_named_to_the_treatment_only(eng, both):
    restricted = [f"{t}.{c}" for u, t, c in eng.RESTRICTED_COLUMNS if u == "retail"]
    assert all(r in both["treatment"]["block"] for r in restricted)


def test_a_figure_runs_through_the_gate_and_is_bound_to_its_citation(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[lambda p: plan(sql={"retail": FIG_SQL}, citations=[1], columns={"retail": "monthly_revenue.total_revenue_usd"})],
                   phrase=[{"answer_text": "Retail Banking took 13,287.67 in June 2025 [1]."}])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67 and a["unit"] == "retail"
    assert a["column"] == "monthly_revenue.total_revenue_usd" and a["join_path"] == []
    assert a["attempts"][0]["passed"] and a["agent"]["mode"] == "model" and a["agent"]["sql_source"] == "model:openai:gpt-test"
    assert a["answer_text"] == "Retail Banking took 13,287.67 in June 2025 [1]." and a["phrased_by"] == "model:openai:gpt-test"
    rt = a["run_trace"]
    assert rt["bound"] == [{"figure": "f1", "doc": a["citations"][0]["doc"], "chunk": a["citations"][0]["chunk"]}]
    assert rt["number_check"] == {"passed": True, "stray_numbers": 0, "bad_markers": 0, "bad_documents": 0}


def test_a_figure_is_bound_to_the_definition_of_the_measure_it_measures(eng, monkeypatch):
    ctx = Agent(eng, llm=None).context(TWO, "analyst_enterprise", "analysis", "treatment")
    n = {s["doc"]: s["n"] for s in ctx["sources"]}
    aua, ce = n["MD-ASSETS-UNDER-ADVICE"], n["MD-CREDIT-EXPOSURE"]
    assert aua < ce
    llm = scripted(monkeypatch, plan=[plan(sql={"corporate": CE_SQL}, citations=[aua, ce])])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(TWO, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["behaviour"] == "figure" and a["column"] == "exposure.ead_eur" and a["figures"][0]["cite"] == ce
    assert a["run_trace"]["bound"] == [{"figure": "f1", "doc": "MD-CREDIT-EXPOSURE", "chunk": "MD-CREDIT-EXPOSURE#0"}]
    assert a["answer_text"].endswith(f"[{ce}].") and {c["doc"]: c["terms"] for c in a["citations"]}["MD-CREDIT-EXPOSURE"] == ["Credit Exposure"]


def test_a_figure_binds_by_term_then_unit_then_no_unit():
    f = {"unit": "retail", "column": "monthly_revenue.total_revenue_usd"}
    defines = {("retail", "monthly_revenue.total_revenue_usd"): ["Revenue"]}

    def c(n, unit="", terms=()):
        return {"n": n, "unit": unit, "terms": list(terms)}
    assert bind(f, [c(1, terms=["Fee Income"]), c(2, "retail"), c(3, terms=["Revenue"])], defines) == 3, "the term it measures"
    assert bind(f, [c(1, terms=["Fee Income"]), c(2), c(3, "retail")], defines) == 3, "then its unit"
    assert bind(f, [c(1, terms=["Fee Income"]), c(2)], defines) == 2, "then no unit, and no other term"
    assert bind(f, [c(1, "sme"), c(2, terms=["Fee Income"])], defines) == 2
    assert bind(f, [c(1, "sme")], defines) == 1 and bind(f, [], defines) is None
    two = [c(1, terms=["Revenue"]), c(2, terms=["Revenue"])]
    assert bind(f, two, defines) == 1 and bind(f, two, defines, in_window={2}) == 2 and bind(f, two[::-1], defines, in_window={1}) == 1
    assert bind(f, [c(1, "retail"), c(2, terms=["Revenue"])], defines, in_window={1}) == 2, "the term first, in force or not"


def test_graph_on_a_figure_rests_only_on_what_defines_its_term_or_is_about_its_unit():
    """A document that defines another term, or is about no unit or another one, is not what the figure rests on however
    it was retrieved; the control's search has no term to bind by."""
    f = {"unit": "retail", "column": "monthly_revenue.total_revenue_usd"}
    defines = {("retail", "monthly_revenue.total_revenue_usd"): ["Revenue"]}

    def c(n, unit="", terms=()):
        return {"n": n, "unit": unit, "terms": list(terms)}
    assert bind(f, [c(1, terms=["Fee Income"]), c(2, "retail"), c(3, terms=["Revenue"])], defines, own=True) == 3
    assert bind(f, [c(1, terms=["Fee Income"]), c(2), c(3, "retail")], defines, own=True) == 3, "then its unit"
    assert bind(f, [c(1, terms=["Fee Income"]), c(2)], defines, own=True) is None, "a document of no unit is not its own"
    assert bind(f, [c(1, "sme"), c(2, terms=["Fee Income"])], defines, own=True) is None, "nor one of another unit or term"
    two = [c(1, terms=["Revenue"]), c(2, terms=["Revenue"])]
    assert bind(f, two, defines, in_window={2}, own=True) == 2, "of two that define it, the one in force"
    assert bind({"unit": "wealth", "column": "flow.amount_usd"}, [c(1, terms=["Revenue"]), c(2), c(3, "wealth")], defines, own=True) == 3, \
        "a column certified for no term rests on a document about its unit"
    assert bind({"unit": "wealth", "column": "flow.amount_usd"}, [c(1, terms=["Revenue"]), c(2)], defines, own=True) is None


DECEMBER = "How many retail customers were active in December 2024?"
DECEMBER_SQL = ("SELECT count(*) FROM customer_activity WHERE customer_activity.is_active_flag "
                "AND customer_activity.month_start = DATE '2024-12-01'")


def test_a_figure_is_bound_to_the_definition_in_force_for_its_period(eng, monkeypatch):
    """For December 2024 both versions of Active Customer may be cited: V1, in force to 31 December 2024, and the
    current one, in force from 1 January 2025; the figure rests on V1 whatever the order."""
    ctx = Agent(eng, llm=None).context(DECEMBER, "analyst_enterprise", "analysis", "treatment")
    n = {s["doc"]: s["n"] for s in ctx["sources"] if s["citable"]}
    v1, v2 = n["MD-ACTIVE-CUSTOMER-V1"], n["MD-ACTIVE-CUSTOMER"]
    for order in ([v1, v2], [v2, v1]):
        llm = scripted(monkeypatch, plan=[plan(sql={"retail": DECEMBER_SQL}, citations=order)])
        a = Agent(eng, llm=llm).ask(DECEMBER, "analyst_enterprise", arm="treatment")
        assert a["behaviour"] == "figure" and a["figures"][0]["cite"] == v1, order
        assert a["answer_text"].endswith(f"[{v1}]."), a["answer_text"]
    rules = Agent(eng, llm=None).ask(DECEMBER, "analyst_enterprise", arm="treatment")
    assert rules["cite"] == ["MD-ACTIVE-CUSTOMER-V1"]


def test_a_restricted_column_is_refused_by_the_gate_and_the_retry_is_told_why(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": EMAIL_SQL}), plan(sql={"retail": FIG_SQL})])
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="control")
    assert [(t["try"], t["passed"]) for t in a["attempts"]] == [(1, False), (2, True)]
    assert any("customer.email is restricted personal data" in p for p in a["attempts"][0]["problems"])
    assert "customer.email is restricted personal data" in llm.prompts[1] and a["behaviour"] == "figure"
    assert without_context(llm.prompts[0]) in without_context(llm.prompts[1]), "the retry is the same prompt, plus the reasons"


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_restricted_personal_data_is_refused_in_both_arms(eng, monkeypatch, arm):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": EMAIL_SQL}), plan("refuse", reason="Restricted personal data.")])
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm=arm)
    assert not a["attempts"][0]["passed"] and a["behaviour"] == "refuse" and "figures" not in a


def test_the_gate_is_the_same_code_but_admits_each_arms_context(eng, monkeypatch):
    logins = "SELECT sum(customer_activity.login_count) FROM customer_activity"
    got = {}
    for arm in ("control", "treatment"):
        llm = scripted(monkeypatch, plan=[plan(sql={"retail": logins}), plan(sql={"retail": logins})])
        got[arm] = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm=arm)
    assert got["control"]["attempts"][0]["passed"] and got["control"]["behaviour"] == "figure"
    assert not got["treatment"]["attempts"][0]["passed"] and got["treatment"]["behaviour"] == "abstain"
    assert any("customer_activity.login_count is not certified" in p for p in got["treatment"]["attempts"][0]["problems"])


@pytest.mark.parametrize("arm", ["control", "treatment"])
@pytest.mark.parametrize("bad", NOT_FIGURES)
def test_a_figure_the_data_did_not_give_is_refused_and_the_retry_is_told_why(eng, monkeypatch, arm, bad):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": bad}, citations=[1]), plan(sql={"retail": FIG_SQL}, citations=[1])])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm=arm), graph=eng.g)
    assert [(t["try"], t["passed"]) for t in a["attempts"]] == [(1, False), (2, True)]
    assert any("aggregate" in p for p in a["attempts"][0]["problems"]), a["attempts"][0]["problems"]
    assert "aggregate" in llm.prompts[1].split(CONTEXT_CLOSE, 1)[1], "the retry carries the gate's reasons"
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67 and a["column"] == "monthly_revenue.total_revenue_usd"
    assert [f["value"] for f in a["figures"]] == [13287.67]


@pytest.mark.parametrize("arm", ["control", "treatment"])
@pytest.mark.parametrize("bad", NOT_FIGURES)
def test_a_figure_the_data_did_not_give_twice_is_an_abstention(eng, monkeypatch, arm, bad):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": bad}, citations=[1]), plan(sql={"retail": bad}, citations=[1])],
                   phrase=[{"answer_text": "Retail Banking took 99,999.00 in June 2025 [1]."}])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm=arm), graph=eng.g)
    assert a["behaviour"] == "abstain" and "No query passed the gate" in a["reason"]
    assert "figure" not in a and "figures" not in a and a["run_trace"]["bound"] == []
    assert [c["stage"] for c in a["run_trace"]["llm_calls"]] == ["plan", "plan"], "nothing was phrased"


ROW_SETS = [FIG_SQL.replace(" WHERE", ", (VALUES (1), (2)) AS v WHERE"),
            FIG_SQL.replace(" WHERE", ", unnest(range(1000000)) WHERE"),
            "WITH c AS (SELECT complaint.customer_id FROM complaint) " + FIG_SQL.replace(
                " WHERE", " JOIN c ON monthly_revenue.account_id = c.customer_id WHERE"),
            FIG_SQL + " AND monthly_revenue.account_id IN (SELECT complaint.customer_id FROM complaint)",
            FIG_SQL + " AND random() < 0.5"]


@pytest.mark.parametrize("arm", ["control", "treatment"])
@pytest.mark.parametrize("bad", ROW_SETS)
def test_a_figure_over_rows_the_query_changed_is_refused_and_the_retry_is_told_why(eng, monkeypatch, arm, bad):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": bad}, citations=[1]), plan(sql={"retail": FIG_SQL}, citations=[1])])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm=arm), graph=eng.g)
    assert [(t["try"], t["passed"]) for t in a["attempts"]] == [(1, False), (2, True)], a["attempts"]
    assert a["attempts"][0]["failed_at"] == "static", "refused by rule, before the warehouse saw it"
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67


@pytest.mark.parametrize("arm", ["control", "treatment"])
@pytest.mark.parametrize("question, sql, claimed, column", [
    (FIG, FIG_SQL, None, "monthly_revenue.total_revenue_usd"),
    (FIG, FIG_SQL, "customer.email", "monthly_revenue.total_revenue_usd"),
    (ACTIVE, ACTIVE_SQL, None, "customer_activity.is_active_flag"),
])
def test_a_figure_that_passed_names_the_column_it_measures(eng, monkeypatch, arm, question, sql, claimed, column):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": sql}, citations=[1], **({"columns": {"retail": claimed}} if claimed else {}))])
    a = Agent(eng, llm=llm).ask(question, "analyst_enterprise", arm=arm)
    assert a["behaviour"] == "figure" and a["column"] == column and a["attempts"][0]["measured"] == [column]


def test_an_undeclared_join_twice_is_an_abstention_and_nothing_runs(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": BAD_JOIN_SQL}), plan(sql={"retail": BAD_JOIN_SQL})])
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "abstain" and "No query passed the gate" in a["reason"] and "figure" not in a
    assert all("is not a declared join" in " ".join(t["problems"]) or not t["passed"] for t in a["attempts"])
    assert len([p for p in llm.prompts if CONTEXT_OPEN in p]) == 2, "one retry, no more"


def test_malformed_json_is_an_abstention_with_no_retry(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=['{"behaviour": "figure", "sql": {"retail": "SELECT 1"'])
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="control")
    assert a["behaviour"] == "abstain" and "not one JSON object" in a["reason"] and a["attempts"] == []
    assert len(llm.prompts) == 1


def test_a_reply_off_the_contract_is_an_abstention(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan("figure", sql={"retail": FIG_SQL, "sme": SME_SQL})])
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="control")
    assert a["behaviour"] == "abstain" and "broke the contract" in a["reason"] and "one unit's query" in a["reason"]


def test_an_invented_number_in_the_wording_is_thrown_away(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": FIG_SQL}, citations=[1])],
                   phrase=[{"answer_text": "Retail took 13,287.67 in June 2025, up 4% on May [1]."}])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["phrased_by"].startswith("template") and "4" in a["phrased_by"] and a["answer_text"].startswith("Retail Banking")
    assert a["run_trace"]["number_check"] == {"passed": False, "stray_numbers": 1, "bad_markers": 0, "bad_documents": 0}


@pytest.mark.parametrize("glued, stray", [("down -3000 on May", ["-3000"]), ("USD9999 below plan", ["9999"]),
                                          ("in a 13,287.67-20000 band", ["20000"]), ("in a 100-200 band", ["100", "200"]),
                                          ("or .5 of plan", [".5"])])
def test_a_number_glued_to_a_sign_letters_or_a_hyphen_is_a_number_all_the_same(eng, monkeypatch, glued, stray):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": FIG_SQL}, citations=[1])],
                   phrase=[{"answer_text": f"Retail took 13,287.67 [1], {glued}."}])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["phrased_by"] == f"template (model text carried numbers or markers not in the result: {', '.join(stray)})"
    assert a["answer_text"].startswith("Retail Banking") and glued not in a["answer_text"]
    assert a["run_trace"]["number_check"] == {"passed": False, "stray_numbers": len(stray), "bad_markers": 0, "bad_documents": 0}


def test_the_number_check_reads_a_name_the_answer_carries_as_a_name():
    """An id, a version or a period label is a name, and its digits are not figures; only names the answer carries
    count."""
    r = {"figure": 2506757.26, "figures": [{"value": 2506757.26, "column": "quarterly_fee_revenue.fee_revenue_usd"}],
         "citations": [{"n": 1, "doc": "EM-004", "title": "Wealth fee note", "version": "1.3"}], "references": [], "reason": ""}
    q = "Wealth revenue for fiscal quarter FY26-Q1"
    check = lambda said: wording(said, supported(r, q), {1}, carried(r, q))  # noqa: E731
    assert check("Wealth took 2,506,757.26 in FY26-Q1, as EM-004 v1.3 says [1].") == ([], [])
    assert check("Wealth took 2,506,757.26 in FY27-Q1 [1].") == (["27"], [])
    assert check("Wealth took 2,506,757.26, as EM-005 says [1].") == (["005"], [])
    assert supported(r, q) == {2506757.26, 1.3}, "a label's digits are no number the wording may carry"


def test_a_citation_that_names_no_source_is_rejected(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": FIG_SQL}, citations=[1, 99])],
                   phrase=[{"answer_text": "Retail took 13,287.67 [99]."}])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert [c["n"] for c in a["citations"]] == [1] and a["citations_rejected"] == [{"n": 99, "why": "no source 99 in the context"}]
    assert a["phrased_by"] == "template (model text carried numbers or markers not in the result: [99])"
    assert a["run_trace"]["number_check"]["bad_markers"] == 1


def test_a_reference_is_never_cited(eng, monkeypatch):
    agent = Agent(eng, llm=None)
    ctx = agent.context("Which calendar does Wealth Management report on?", "analyst_enterprise", "analysis", "treatment")
    bronze = next(s for s in ctx["sources"] if not s["citable"])
    llm = scripted(monkeypatch, plan=[plan("cite_certified", citations=[bronze["n"]], answer_text="See the wiki.")])
    a = Agent(eng, llm=llm).ask("Which calendar does Wealth Management report on?", "analyst_enterprise", arm="treatment")
    assert a["cite"] == [] and a["citations_rejected"][0]["n"] == bronze["n"] and "never cited" in a["citations_rejected"][0]["why"]
    assert a["behaviour"] == "abstain" and a["phrased_by"] == "template" and a["answer_text"] == a["reason"]
    assert "cites no source" in a["reason"] and bronze["doc"] in a["reason"] and "See the wiki" not in a["answer_text"]


@pytest.mark.parametrize("arm", ["control", "treatment"])
@pytest.mark.parametrize("cites", [[], [99]])
def test_a_reply_that_cites_with_nothing_it_may_cite_is_an_abstention(eng, monkeypatch, arm, cites):
    said = "The authority is the team wiki: a customer is active if they logged in during the month."
    llm = scripted(monkeypatch, plan=[plan("cite_certified", citations=cites, answer_text=said)])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(DEFINE, "analyst_enterprise", arm=arm), graph=eng.g)
    assert a["behaviour"] == "abstain" and a["cite"] == [] and a["phrased_by"] == "template"
    assert "cites no source" in a["reason"] and a["answer_text"] == a["reason"] and said not in a["answer_text"]


@pytest.mark.parametrize("named, ok", [
    ("MD-ACTIVE-CUSTOMER-V1 still applies.", False),
    ("STD-SUITABILITY agrees.", False),
    ("std-suitability agrees.", False),
    ("DRAFT-002, a reference, proposes to widen it.", True),
    ("POL-DATA-CLASS keeps personal data out of it.", True),
])
def test_wording_names_only_documents_the_context_gave_and_did_not_withhold(eng, monkeypatch, named, ok):
    ctx = Agent(eng, llm=None).context(DEFINE, "analyst_enterprise", "analysis", "treatment")
    n = next(s["n"] for s in ctx["sources"] if s["doc"] == "MD-ACTIVE-CUSTOMER")
    said = f"A customer with a qualifying transaction in the month is active [{n}]. {named}"
    llm = scripted(monkeypatch, plan=[plan("cite_certified", citations=[n], answer_text=said)])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(DEFINE, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["behaviour"] == "cite_certified" and a["cite"] == ["MD-ACTIVE-CUSTOMER"]
    if ok:
        assert a["answer_text"] == said and a["phrased_by"] == "model:openai:gpt-test"
        assert a["run_trace"]["number_check"] == {"passed": True, "stray_numbers": 0, "bad_markers": 0, "bad_documents": 0}
    else:
        assert a["answer_text"] != said and a["phrased_by"].startswith("template (model text named documents")
        assert a["run_trace"]["number_check"] == {"passed": False, "stray_numbers": 0, "bad_markers": 0, "bad_documents": 1}


def test_a_reason_that_names_a_withheld_document_is_withheld(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan("abstain", reason="MD-ACTIVE-CUSTOMER-V1 is the definition in force, so there is no answer.")])
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(DEFINE, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["behaviour"] == "abstain" and "withheld" in a["reason"] and "V1" not in a["answer_text"]
    assert a["run_trace"]["number_check"]["bad_documents"] == 1


def test_a_unit_the_principal_cannot_read_is_refused_before_any_query(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan(sql={"sme": SME_SQL})])
    a = Agent(eng, llm=llm).ask("Total SME revenue for June 2025", "analyst_retail", arm="control")
    assert a["behaviour"] == "refuse" and a["denied_warehouses"] == ["sme"] and a["attempts"] == [] and "figure" not in a


@pytest.mark.parametrize("who", ["analyst_retail", "steward"])
def test_graph_on_the_seam_blocks_a_partial_figure_whatever_the_reply(eng, monkeypatch, who):
    """Graph on, the seam checks access on every unit the question needs, not only those the reply ran; graph off
    nothing resolves the term, so a partial figure there is a graph-off failure for the A/B to count."""
    bank = "Total revenue across the bank for June 2025"
    for reply in (plan(sql={"retail": FIG_SQL}, citations=[1]), plan("abstain", reason="Not enough context.")):
        llm = scripted(monkeypatch, plan=[reply])
        a = Agent(eng, llm=llm).ask(bank, who, arm="treatment")
        assert a["behaviour"] == "blocked_access" and "sme" in a["denied_warehouses"], a
        assert a["attempts"] == [] and "figure" not in a
    llm = scripted(monkeypatch, plan=[plan(sql={"retail": FIG_SQL}, citations=[1])])
    assert Agent(eng, llm=llm).ask(bank, who, arm="control")["behaviour"] == "figure"


def test_a_sum_across_units_runs_each_query_and_adds_them(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan("sum_with_verdict", sql={"retail": FIG_SQL, "sme": SME_SQL}, joins_refused=["retail-sme"],
                                           reason="Both monthly, USD, calendar.")])
    a = Agent(eng, llm=llm).ask(CROSS, "analyst_enterprise", arm="treatment")
    g022 = round(13287.67 + a["parts"]["sme"], 2)
    assert a["behaviour"] == "sum_with_verdict" and a["commensurable"] is True and a["figure"] == g022
    assert a["joins_refused"] == ["retail-sme"] and set(a["parts"]) == {"retail", "sme"}


def test_a_sum_with_a_part_missing_is_reported_as_parts(eng, monkeypatch):
    broken = "SELECT round(sum(monthly_client_revenue.revenue_usd), 2) FROM monthly_client_revenue WHERE monthly_client_revenue.month_start = DATE '2031-01-01'"
    llm = scripted(monkeypatch, plan=[plan("sum_with_verdict", sql={"retail": FIG_SQL, "sme": broken})])
    a = Agent(eng, llm=llm).ask(CROSS, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "parts_with_reason" and a["missing_parts"] == ["sme"] and "incomplete" in a["reason"]


def test_a_reply_with_no_query_keeps_its_wording_only_if_it_passes_the_check(eng, monkeypatch):
    agent = Agent(eng, llm=None)
    ctx = agent.context(DEFINE, "analyst_enterprise", "analysis", "treatment")
    n = next(s["n"] for s in ctx["sources"] if s["doc"] == "MD-ACTIVE-CUSTOMER")
    said = f"A customer with a qualifying transaction in the month is active [{n}]."
    llm = scripted(monkeypatch, plan=[plan("cite_certified", citations=[n], answer_text=said)])
    a = Agent(eng, llm=llm).ask(DEFINE, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "cite_certified" and a["cite"] == ["MD-ACTIVE-CUSTOMER"] and a["answer_text"] == said
    llm = scripted(monkeypatch, plan=[plan("cite_certified", citations=[n], answer_text=f"Active means 3 transactions [{n}].")])
    a = Agent(eng, llm=llm).ask(DEFINE, "analyst_enterprise", arm="treatment")
    assert a["phrased_by"].startswith("template") and "3" in a["phrased_by"]


def test_the_trace_records_the_calls_tokens_prompt_version_and_seed_alike_in_both_arms(eng, monkeypatch):
    monkeypatch.delenv("MODEL_SEED", raising=False)
    runs = {}
    for arm in ("control", "treatment"):
        llm = scripted(monkeypatch, plan=[plan(sql={"retail": FIG_SQL}, citations=[1])], phrase=[{"answer_text": ""}])
        runs[arm] = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm=arm), graph=eng.g)["run_trace"]
    for arm, rt in runs.items():
        assert rt["agent"] == {"arm": arm, "mode": "model", "prompt_versions": PROMPTS}
        assert rt["llm"]["seed"] == 7 and rt["llm"]["name"] == "openai:gpt-test"
        assert [(c["stage"], c["prompt_version"]) for c in rt["llm_calls"]] == [("plan", PROMPTS["sql"]), ("render", PROMPTS["phrase"])]
        assert all(c["tokens_in"] > 0 and c["served_model"] == "gpt-test-2026" for c in rt["llm_calls"])
        assert rt["tokens"]["in"] == sum(c["tokens_in"] for c in rt["llm_calls"])
        assert "sk-test-secret" not in json.dumps(rt) and CONTEXT_OPEN not in json.dumps(rt)
    c, t = runs["control"]["llm_calls"][0], runs["treatment"]["llm_calls"][0]
    assert c["prompt_hash"] == t["prompt_hash"], "the same prompt, less the context block"
    assert c["context_hash"] != t["context_hash"]
    assert runs["control"]["llm"]["config_hash"] == runs["treatment"]["llm"]["config_hash"]
    assert runs["control"]["config"]["label"] == "control[resolution=none,sql_source=model,renderer=template+model]"
    assert runs["treatment"]["config"]["label"] == "treatment[sql_source=model,renderer=template+model]"


def test_a_failed_model_call_is_an_abstention_and_is_recorded(eng, monkeypatch):
    import urllib.error
    llm = LLM("openai", "gpt-test", "k")

    def down(url, headers, body):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(llm, "_post", down)
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["behaviour"] == "abstain" and "model call failed" in a["reason"]
    assert a["run_trace"]["llm_calls"][0]["ok"] is False and a["run_trace"]["llm_calls"][0]["prompt_version"] == PROMPTS["sql"]


KEY = "sk-SECRET-KEY-123"  # gitleaks:allow


def leaky(monkeypatch, plans=()) -> LLM:
    """A key pasted with a stray carriage return. The HTTP layer refuses the header and quotes it in a
    ValueError, as http.client does. plans answers the sql task first; then every call fails so.
    KEY is deliberately fake: the tests prove no key reaches a trace."""
    llm = LLM("openai", "gpt-test", KEY + "\r")
    queue = list(plans)

    def post(url, headers, body):
        if queue and CONTEXT_OPEN in body["messages"][0]["content"]:
            return {"choices": [{"message": {"content": json.dumps(queue.pop(0))}}], "model": "gpt-test", "usage": {}}
        raise ValueError(f"Invalid header value b'Bearer {KEY}\\r'")
    monkeypatch.setattr(llm, "_post", post)
    return llm


def test_a_failed_call_is_named_by_its_type_and_the_key_never_reaches_an_answer_or_a_record(eng, monkeypatch):
    from kge.eval import harness, metrics
    a = Agent(eng, llm=leaky(monkeypatch)).ask(FIG, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "abstain" and a["reason"] == "No answer: the model call failed (ValueError)."
    b = Agent(eng, llm=leaky(monkeypatch, [plan(sql={"retail": FIG_SQL}, citations=[1])])).ask(FIG, "analyst_enterprise", arm="treatment")
    assert b["behaviour"] == "figure" and b["phrased_by"] == "template (the model call failed (ValueError))"
    arms = harness.Arms(eng, llm=leaky(monkeypatch))
    item = {"id": "G001", "kind": "within", "question": FIG, "expect": {"figure": "13287.67"}}
    rec = harness.one(arms, "treatment", item, "golden", 1, "r", "analyst_enterprise", arms.describe("treatment"), {}, metrics.prices(), set())
    assert rec["answer"]["behaviour"] == "abstain"
    assert rec["answer"]["reason"].endswith("the model call failed (ValueError).")
    for out in (a, b, rec):
        assert KEY not in json.dumps(out, default=str)


def test_a_reply_that_is_not_json_still_says_so(eng, monkeypatch):
    from kge.adapters.model import NotJSON
    llm = scripted(monkeypatch, plan=["no json here"])
    with pytest.raises(NotJSON):
        llm.json("<context>x</context>")
    a = Agent(eng, llm=scripted(monkeypatch, plan=['{"behaviour": "figure",'])).ask(FIG, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "abstain" and "not one JSON object" in a["reason"]


def test_make_llm_strips_whitespace_from_the_key(monkeypatch):
    from kge.adapters.model import make_llm
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_API_KEY", " sk-x\r\n")
    assert make_llm().api_key == "sk-x"


def test_the_combined_answer_and_the_tagger_name_a_failed_call_by_its_type(eng, monkeypatch):
    from kge.services.ask.engine.combined import Combined
    from kge.pipeline.tagger import ModelTagger

    class Leaky:
        name = "stub:leaky"

        def json(self, prompt, max_tokens=800):
            raise ValueError(f"Invalid header value b'Bearer {KEY}\\r'")
    c = Combined(eng, llm=Leaky())
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    q = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
    a = c.ask(q, "analyst_retail")
    tries = next(t for t in a["trace"] if t["step"] == "plan")["attempts"]
    assert tries[-1]["problems"] == ["model call failed (ValueError)"] and KEY not in json.dumps(a, default=str)
    t = ModelTagger(["Revenue"], "openai", "stub", KEY + "\r")
    monkeypatch.setattr(t.llm, "_post", lambda *a: (_ for _ in ()).throw(ValueError(f"Invalid header value b'Bearer {KEY}\\r'")))
    tags = t.propose("Revenue is booked monthly.", "certified", "policy")
    err = next(p for p in tags if p.value == "__model_error__")
    assert err.evidence == "model call failed (ValueError)" and KEY not in json.dumps([vars(p) for p in tags])


def test_the_answer_says_which_model_and_mode_ran(eng, monkeypatch):
    llm = scripted(monkeypatch, plan=[plan("abstain", reason="Not enough context.")])
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="control")
    assert a["agent"]["mode"] == "model" and a["versions"]["model"] == "openai:gpt-test" and a["versions"]["prompts"] == PROMPTS
    assert a["arm"] == "control[resolution=none,sql_source=model,renderer=template+model]"


def test_the_phrase_call_leaves_room_for_a_model_that_thinks(eng, monkeypatch):
    """Issue #69: a model that thinks (Gemini 2.5 on Vertex AI, Claude Haiku 5.5) spends part of its output budget on
    thinking, which left a 400-token phrase call too little room for its JSON. The caller asks for 800 for phrasing
    and 1200 for SQL, and the image's 8192-token default replaces both. The mock ignores it; rules make no call."""
    from kge.services.ask.engine.agent import MAX_TOKENS
    assert MAX_TOKENS == {"sql": 1200, "phrase": 800}
    llm, asked = LLM("gemini", "gemini-test", "k"), []
    replies = [plan(sql={"retail": FIG_SQL}, citations=[1]), {"answer_text": ""}]

    def post(url, headers, body):
        asked.append(body["generationConfig"]["maxOutputTokens"])
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(replies.pop(0))}]}}], "modelVersion": "gemini-test",
                "usageMetadata": {}}
    monkeypatch.setattr(llm, "_post", post)
    a = Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment")
    assert a["behaviour"] == "figure" and asked == [8192, 8192], asked


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_agent_through_the_api(client):
    who = client.as_("analyst_enterprise")
    for arm in ("control", "treatment"):
        a = client.post("/agent", json={"question": FIG, "arm": arm}, headers=who).json()
        assert a["agent"]["mode"] == "rules" and a["arm"] == arm and a["behaviour"] == "figure"
        assert a["run_trace"]["path"] == "agent" and a["run_trace"]["agent"]["arm"] == arm
    r = client.post("/agent", json={"question": FIG, "arm": "graph"}, headers=who)
    assert r.status_code == 400 and "arm must be one of control, treatment" in r.json()["error"]
    assert client.post("/agent", json={"question": FIG}).status_code == 401


def test_the_page_reaches_the_agents_arms_and_the_ab_reports(client):
    import kge.app as appmod
    from kge.eval import harness
    p = client.get("/presets").json()
    assert list(p["agent_arms"]) == ["control", "treatment"]
    assert {a: (v["side"], v["path"], v["mode"]) for a, v in p["agent_arms"].items()} == {
        "control": ("graph off", "agent", "rules"), "treatment": ("graph on", "agent", "rules")}
    assert list(p["presets"]) == ["schema", "catalog", "graph"], "the presets are as they were"
    assert client.get("/ab").json() == {"runs": [], "baseline": "issue-40.json"}
    run = harness.run(appmod.PROFILE, harness.Arms(appmod.engine(), llm=None), ["control", "treatment"], ["golden"], 1,
                      items=["G001"], out=appmod.PROFILE.run_dir() / "runs", echo=lambda *_: None)
    runs = client.get("/ab").json()["runs"]
    assert [r["run_id"] for r in runs] == [run.name] and runs[0]["report"] == f"/ab/{run.name}/report.html"
    assert [(a["arm"], a["mode"]) for a in runs[0]["arms"]] == [("control", "rules"), ("treatment", "rules")]
    page = client.get(runs[0]["report"])
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html") and f"A/B run {run.name}" in page.text
    for bad in ("/ab/nope/report.html", "/ab/..%2F..%2Ftokens.json/report.html", "/ab/.../report.html"):
        assert client.get(bad).status_code == 404, bad


def test_the_readme_says_how_to_run_the_agent():
    from pathlib import Path
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert "## The reference agent" in readme and "make agent" in readme
    assert re.search(r"no (real )?model has been run", readme, re.I)
