"""Baseline and default-context contracts retained across the typed-plan Agent migration.

Model-plan safety and history live in test_shared_plan_contract.py and the shared
plan/check/compiler suites; no model may propose SQL through Agent.ask.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent, bind, carried, supported, wording
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, STAGES, WORDS, RunConfig, parse, question_words
from kge.eval import sets
from tests.scratch import BUILD, profile_of, scratch_build
from tests.integration.test_combined import Stub

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
                              "sql_source": "planner", "renderer": "template", "planner": "query-plan-v1"}
    base = eng.answer(FIG, "analyst_enterprise", config=AGENT_ARMS[arm])
    assert (a['behaviour'], a['figure'], a['result'], a['history']) == (base['behaviour'], base['figure'], base['result'], base['history'])
    assert a['trace'][:len(base['trace'])] == base['trace']
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


@pytest.fixture(scope="module")
def both(eng):
    """The plan prompt each arm sends for the cross question, and each arm's context."""
    out = {}
    for arm in ("control", "treatment"):
        agent = Agent(eng, llm=None)
        out[arm] = agent.context(CROSS, "analyst_enterprise", "analysis", arm)
    return out


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


def test_a_customer_group_across_retail_and_wealth_is_decided_as_ask_decides_it(eng):
    """Pack 1.1.0 replaced retail-wealth's no-join fact with a certified join, which the runtime reads (#39 item 7d); a
    customer group is built within one unit, so the treatment abstains in /ask's words and refuses no join."""
    from kge.services.ask.engine.combined import Combined
    q = "How many active customers in Retail and Wealth in June 2025 raised a complaint that month?"
    t, a = Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment"), Combined(eng, llm=None).ask(q, "analyst_enterprise")
    assert t["behaviour"] == a["behaviour"] == "abstain" and t["joins_refused"] == a["joins_refused"] == []
    assert t["reason"] == a["reason"] and "no certified join" not in t["reason"] and "certified join client_xref" in t["reason"]


def test_a_customer_group_the_principal_cannot_read_is_refused(eng):
    a = Agent(eng, llm=None).ask(GROUP, "advisor_wealth", arm="treatment")
    assert a["behaviour"] == "refuse" and "retail" in a["reason"] and "figure" not in a


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
    llm = Stub(eng, proposal={'kind': 'abstain', 'spans': [], 'missing': ['No complete declaration.']})
    a = Agent(eng, llm=llm).ask(TWO, who, arm="treatment")
    assert all(c["unit"] in readable or c["unit"] == "" for c in a.get("citations", []))


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
    question = 'Total corporate credit exposure for June 2025'
    llm = Stub(eng, question=question)
    rt = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(question, "analyst_enterprise", arm="treatment"), graph=eng.g)["run_trace"]
    retrieved = {(r["doc"], r["chunk"]) for r in rt["retrieved"]}
    assert rt["bound"] == [{"figure": "f1", "doc": "MD-CREDIT-EXPOSURE", "chunk": "MD-CREDIT-EXPOSURE#0"}]
    assert {(b["doc"], b["chunk"]) for b in rt["bound"]} <= retrieved
    assert next(r for r in rt["retrieved"] if r["chunk"] == "MD-CREDIT-EXPOSURE#0")["via"] == "definition"


def test_the_restricted_columns_are_named_to_the_treatment_only(eng, both):
    restricted = [f"{t}.{c}" for u, t, c in eng.RESTRICTED_COLUMNS if u == "retail"]
    assert all(r in both["treatment"]["block"] for r in restricted)


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


ROW_SETS = [FIG_SQL.replace(" WHERE", ", (VALUES (1), (2)) AS v WHERE"),
            FIG_SQL.replace(" WHERE", ", unnest(range(1000000)) WHERE"),
            "WITH c AS (SELECT complaint.customer_id FROM complaint) " + FIG_SQL.replace(
                " WHERE", " JOIN c ON monthly_revenue.account_id = c.customer_id WHERE"),
            FIG_SQL + " AND monthly_revenue.account_id IN (SELECT complaint.customer_id FROM complaint)",
            FIG_SQL + " AND random() < 0.5"]


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


KEY = "sk-SECRET-KEY-123"  # gitleaks:allow


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
    q = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
    a = c.ask(q, "analyst_retail")
    assert a['behaviour'] == 'abstain' and 'configured model could not provide a valid query plan' in a['reason']
    assert KEY not in json.dumps(a, default=str)
    t = ModelTagger(["Revenue"], "openai", "stub", KEY + "\r")
    monkeypatch.setattr(t.llm, "_post", lambda *a: (_ for _ in ()).throw(ValueError(f"Invalid header value b'Bearer {KEY}\\r'")))
    tags = t.propose("Revenue is booked monthly.", "certified", "policy")
    err = next(p for p in tags if p.value == "__model_error__")
    assert err.evidence == "model call failed (ValueError)" and KEY not in json.dumps([vars(p) for p in tags])


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
    who = client.as_("analyst_enterprise.meridian@example.com")
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
    assert client.get("/ab").json() == {"runs": [], "baseline": appmod.PROFILE.baseline.name}
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
