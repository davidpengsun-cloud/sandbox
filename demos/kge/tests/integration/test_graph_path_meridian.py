"""The context graph's path (graph_path, #403) on the built Meridian graph: W01 asked as the deployment's asker names the
nodes and edges its answer used by graph id, its unused rules and joins, its SCD2 tables; the same question asked by a
principal who may not read wealth shows the refused read and nothing after; /ask returns it, compact leaves it out,
and the stream adds it before the answer.

Reads the demo's build (make graph pipeline); the HTTP tests run on a scratch copy. Stop the API first."""
from __future__ import annotations

import json

import pytest

from kge.services.ask import service
from kge.services.ask.engine.core import PRESETS
from kge.pipeline.settings import ROOT
from tests.integration.test_answer_text import client as client
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.scratch import open_engine, profile_of

W01 = "NNA for FY26-Q1 by division"
RETAIL_ONLY = "analyst_retail.meridian@example.com"
USED = {
    "Words:NNA", "Words:FY26-Q1", "Words:division", "Acronym:NNA", "Term:net-new-assets", "Term:division",
    "Calendar:fiscal_april", "Principal:analyst_enterprise", "Unit:wealth", "Golden:golden_wealth_v3:W01", "Context:nna",
    "Measure:wealth.net-new-assets", "Filter:eligible", "Filter:released", "RowSelector:not_test_account",
    "Table:wealth.TRAN_FACT_1", "Table:wealth.MONTH_DIM_1", "Table:wealth.ACCT_DIM2_1", "Table:wealth.BRCH_DIM_1",
    "Entity:wealth_branch_division", "Runtime:reference",
}
JOINS = {("wealth.TRAN_FACT_1", "wealth.MONTH_DIM_1", "flow_month_end"), ("wealth.TRAN_FACT_1", "wealth.ACCT_DIM2_1", "flow_account"),
         ("wealth.ACCT_DIM2_1", "wealth.BRCH_DIM_1", "account_current_office")}
SCD2 = {"Table:wealth.TRAN_FACT_1": "scd2-wealth-flow", "Table:wealth.ACCT_DIM2_1": "scd2-wealth-account",
        "Table:wealth.BRCH_DIM_1": "scd2-wealth-office"}


@pytest.fixture(scope="module")
def eng():
    e = open_engine()
    yield e
    e.close()


def asked(eng, who: str, question: str = W01) -> dict:
    return service.answer(eng, profile_of(), question, who, "analysis", "treatment", PRESETS["graph"])


def test_w01_names_the_context_graph_it_used_by_graph_id(eng):
    out = asked(eng, profile_of().asking(None))
    assert out["behaviour"] == "figure"
    path = out["graph_path"]
    assert path == json.loads((ROOT / 'portal/ui/src/pages/Context/fixtures/w01_graph_path.json').read_text())
    assert "error" not in path
    assert {n["id"] for n in path["nodes"] if n["used"]} == USED
    joins = {(e["from"].removeprefix("Table:"), e["to"].removeprefix("Table:"), e["label"])
             for e in path["edges"] if e["type"] == "JoinRule" and e["used"]}
    assert joins == JOINS
    badges = {n["id"]: [b["id"] for b in n["badges"] if b["kind"] == "SCD2"] for n in path["nodes"] if n["badges"]}
    assert {k: v for k, v in badges.items() if k in USED} == {k: [v] for k, v in SCD2.items()}
    assert path["unused"] == 3
    unused = {n["id"] for n in path["nodes"] if not n["used"]}
    assert not unused & USED
    assert {e["label"] for e in path["edges"] if e["type"] == "JoinRule" and not e["used"]} == {
        "account_client_code", "flow_date", "flow_source_system"}
    edges = {(e["from"], e["to"], e["type"]) for e in path["edges"]}
    assert {("Words:NNA", "Acronym:NNA", "matches"), ("Term:net-new-assets", "Acronym:NNA", "ABBREVIATED_AS"),
            ("Words:NNA", "Context:nna", "matches"),
            ("Term:net-new-assets", "Context:nna", "RESOLVES_TO"), ("Context:nna", "Golden:golden_wealth_v3:W01", "CERTIFIED_BY"),
            ("Measure:wealth.net-new-assets", "Calendar:fiscal_april", "IN_CALENDAR"),
            ("Words:division", "Term:division", "matches"),
            ("Term:division", "Entity:wealth_branch_division", "matches"),
            ("Table:wealth.BRCH_DIM_1", "Entity:wealth_branch_division", "REPRESENTS"),
            ("Principal:analyst_enterprise", "Unit:wealth", "CAN_READ")} <= edges
    assert [(n['id'], n['state'], n['label']) for n in out['path']['nodes']] == [
        ('question', 'done', W01), ('terms', 'done', 'Net New Assets · FY26-Q1'),
        ('measure', 'done', 'flow.amount_usd'), ('tables', 'done', 'wealth.TRAN_FACT_1'),
        ('access', 'done', 'Access check (analyst_enterprise)'), ('runtime', 'done', 'Reference warehouse'),
        ('query', 'done', 'Query (duckdb)'), ('figure', 'done', '2 grouped rows')]


def test_a_principal_who_may_not_read_wealth_sees_the_refused_read_and_nothing_after(eng):
    out = asked(eng, RETAIL_ONLY)
    assert out["behaviour"] in ("refuse", "blocked_access")
    path = out["graph_path"]
    assert "error" not in path
    assert {n["type"] for n in path["nodes"]} == {"Words", "Principal", "Unit"}
    can_read = [e for e in path["edges"] if e["type"] == "CAN_READ"]
    assert [e["state"] for e in can_read] == ["refused"] and can_read[0]["reason"]
    unit = next(n for n in path["nodes"] if n["id"] == can_read[0]["to"])
    assert unit["state"] == "refused" and unit["reason"] == can_read[0]["reason"]
    assert next(n for n in path["nodes"] if n["type"] == "Principal")["state"] == "refused", "the unit is withheld"
    text = json.dumps(path)
    assert "TRAN_FACT" not in text and "Wealth" not in text and "Unit:wealth" not in text


def test_a_wealth_only_asker_sees_no_unused_rule_or_join_on_retail(eng):
    out = asked(eng, "advisor_wealth.meridian@example.com")
    assert out["behaviour"] == "figure", out.get("reason")
    path = out["graph_path"]
    assert "error" not in path
    text = json.dumps(path)
    assert "retail" not in text.lower() and "banks_with_retail" not in text and "client_xref" not in text
    assert path["unused"] == 3
    assert {e["label"] for e in path["edges"] if e["type"] == "JoinRule" and not e["used"]} == {
        "account_client_code", "flow_date", "flow_source_system"}
    ids = {n["id"] for n in path["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in path["edges"])


def test_a_planner_answer_without_a_context_still_shows_its_context_and_measure(eng):
    """The query planner names its context in its plan (history) and lineage, not in context: on rules and on a model."""
    from kge.services.ask.engine.combined import Combined
    from tests.plan_recordings import public_recorded
    question, who = "Total retail revenue for June 2025", "analyst_enterprise.meridian@example.com"
    ruled = asked(eng, "analyst_enterprise", question)
    combined = Combined(eng, llm=public_recorded(eng, question, who))
    modelled = service.ask(combined, profile_of(), question, who, "analysis", "treatment", combined.default_config())
    assert modelled["versions"]["interpretation"] == "model:mock:mock"
    for out in (ruled, modelled):
        assert out["behaviour"] == "figure" and "context" not in out
        path = out["graph_path"]
        assert "error" not in path
        ids = {n["id"] for n in path["nodes"] if n["used"]}
        assert {"Context:retail_revenue", "Measure:retail.revenue", "Table:retail.monthly_revenue", "Runtime:reference"} <= ids
        assert ("Context:retail_revenue", "Measure:retail.revenue", "SPECIALIZES") in {
            (e["from"], e["to"], e["type"]) for e in path["edges"]}
        node_ids = {n["id"] for n in path["nodes"]}
        assert all(e["from"] in node_ids and e["to"] in node_ids for e in path["edges"])


def test_ask_returns_the_graph_path_compact_leaves_it_out_and_the_stream_adds_it_first(client):
    who = "analyst_enterprise.meridian@example.com"
    full = client.post("/ask", json={"question": W01}, headers=client.as_(who))
    compact = client.post("/ask", json={"question": W01, "compact": True}, headers=client.as_(who))
    assert full.status_code == compact.status_code == 200, (full.text, compact.text)
    graph_path = full.json()["graph_path"]
    assert "error" not in graph_path
    assert {"Context:nna", "Golden:golden_wealth_v3:W01", "Table:wealth.BRCH_DIM_1"} <= {n["id"] for n in graph_path["nodes"]}
    assert graph_path["unused"] == 3
    assert "graph_path" not in compact.json() and "path" not in compact.json()
    streamed = client.post("/ask", json={"question": W01}, headers={**client.as_(who), "accept": "application/x-ndjson"})
    assert streamed.status_code == 200, streamed.text
    events = [json.loads(line) for line in streamed.text.splitlines() if line.strip()]
    kinds = [e["event"] for e in events]
    added = [e["node"] for e in events if e["event"] in ("node_added", "state_changed")]
    joined = [e["edge"] for e in events if e["event"] in ("edge_added", "edge_changed")]
    assert kinds[-1] == "answer"
    assert list({n["id"]: n for n in added}.values()) == events[-1]["answer"]["graph_path"]["nodes"]
    assert list({(e["from"], e["to"], e["type"], e["label"]): e for e in joined}.values()) == events[-1]["answer"]["graph_path"]["edges"]
    assert kinds.index("edge_added") < max(i for i, k in enumerate(kinds) if k == "node_added")


def test_runtime_sql_and_context_detail_are_complete_without_restricted_columns(eng):
    out = asked(eng, profile_of().asking(None))
    path = out["graph_path"]
    runtime = next(n for n in path["nodes"] if n["type"] == "Runtime")
    assert runtime["sql"] and runtime["sql"] == out["sql"]
    assert runtime["certified"] is False
    context = next(n for n in path["nodes"] if n["id"] == "Context:nna")
    for line in ("Reached from:", "Certified by:", "Filter used:", "Filter not used:", "RowSelector used:",
                 "RowSelector not used:", "JoinRule used:", "JoinRule not used:", "Policies applied (SCD2):"):
        assert line in context["detail"]
    for _, _, column in eng.RESTRICTED_COLUMNS:
        assert column not in json.dumps(path)


def test_agent_returns_the_graph_path_recorded_inside_its_workflow(client):
    response = client.post("/agent", json={"question": W01}, headers=client.as_("analyst_enterprise.meridian@example.com"))
    assert response.status_code == 200, response.text
    path = response.json()["graph_path"]
    assert "error" not in path
    assert {"Context:nna", "Entity:wealth_branch_division", "Runtime:reference"} <= {n["id"] for n in path["nodes"]}


@pytest.mark.parametrize('question', [W01, 'certified NNA for March 2025'])
def test_model_and_rules_graph_paths_match_for_the_same_plan_and_job_budget(comparison_engine, question):
    from kge.semantics.plan import Propose, QueryPlan
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_plan_wording_round2 import Recording
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    caller = Combined(eng, llm=None)
    caller.ask(question, 'analyst_enterprise', config=PRESETS['graph'])
    if fake:
        fake.requests.clear()
    rules = caller.ask(question, 'analyst_enterprise', config=PRESETS['graph'])
    assert rules['behaviour'] == 'figure'
    expected_jobs = budgets(fake) if fake else None
    proposal = Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(rules['history'][-1]['plan']))
    if fake:
        fake.requests.clear()
    model = Combined(eng, llm=Recording(proposal)).ask(question, 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert model['behaviour'] == 'figure'
    assert model['result'] == rules['result']
    assert model['graph_path'] == rules['graph_path']
    assert not any(n['state'] == 'refused' for n in model['graph_path']['nodes'])
    if fake:
        assert expected_jobs == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
        assert budgets(fake) == expected_jobs


@pytest.mark.parametrize('replacement', [False, True])
def test_model_refusal_records_its_typed_reason_and_only_a_valid_replacement_continues(eng, replacement):
    from kge.semantics.plan import Abstain
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_plan_wording_round2 import Recording
    question = 'certified NNA for March 2025' if replacement else 'certified NNA for December 2026'
    reason = 'The recorded model declined this plan.'
    recorded = Recording(Abstain(kind='abstain', spans=(), missing=(reason,)))
    answer = Combined(eng, llm=recorded).ask(question, 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert answer['behaviour'] == ('figure' if replacement else 'abstain')
    refusal = next(s for s in answer['trace'] if s['step'] == 'model_plan_refused')
    assert refusal['reason'] == reason
    assert refusal['fallback'] == ('rules' if replacement else None)
    path = answer['graph_path']
    assert 'error' not in path
    edges = {(e['from'], e['to'], e['type']) for e in path['edges']}
    assert {('Words:certified NNA', 'Context:nna_certified', 'matches'),
            ('Words:certified NNA', 'Measure:wealth.certified-nna', 'matches'),
            (f"Words:{'March 2025' if replacement else 'December 2026'}", 'Calendar:calendar', 'matches')} <= edges
    refused, = [e for e in path['edges'] if e['state'] == 'refused']
    assert refused['from'] == 'Plan:model' and refused['to'] == 'Check:model/plan'
    assert refused['reason'] == refused['detail'] == refusal['reason']
    assert not any(e['from'] == refused['to'] for e in path['edges'])
    gate = next(n for n in path['nodes'] if n['id'] == refused['to'])
    assert gate['state'] == 'refused' and gate['reason'] == reason
    assert any(n['type'] == 'Runtime' for n in path['nodes']) is replacement
    if replacement:
        rules = Combined(eng, llm=None).ask(question, 'analyst_enterprise', config=PRESETS['graph'])
        model_ids = {n['id'] for n in path['nodes'] if n['type'] in ('Plan', 'Check')}
        without_model_step = {**path, 'nodes': [n for n in path['nodes'] if n['id'] not in model_ids],
                              'edges': [e for e in path['edges'] if e['from'] not in model_ids and e['to'] not in model_ids]}
        assert without_model_step == rules['graph_path']
        assert answer['result'] == rules['result']
    else:
        assert path['nodes'][-1]['id'] == gate['id']
        assert not any(e['used'] and e['type'] in ('JoinRule', 'USES_FACT', 'COMPUTED_FROM', 'IMPLEMENTS') for e in path['edges'])
    for _, _, column in eng.RESTRICTED_COLUMNS:
        assert column not in json.dumps(path)


def test_wrong_named_context_refusal_keeps_the_ledger_matches_and_no_executed_path(eng):
    from kge.semantics.plan import Propose, QueryPlan
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_plan_wording_round2 import Recording
    question = 'certified NNA for March 2025'
    baseline = Combined(eng, llm=None).ask(question, 'analyst_enterprise')
    data = baseline['history'][-1]['plan']
    data['measures'][0]['context']['id'] = 'nna'
    data['measures'][0]['measure']['id'] = 'wealth.net-new-assets'
    combined = Combined(eng, llm=Recording(Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(data))))
    combined.plan_grounding = False
    answer = combined.ask(question, 'analyst_enterprise', config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert answer['behaviour'] in ('abstain', 'clarify')
    path = answer['graph_path']
    assert 'error' not in path
    assert ('Words:certified NNA', 'Context:nna_certified') in {(e['from'], e['to']) for e in path['edges']}
    gate = next(n for n in path['nodes'] if n['type'] == 'Check')
    assert gate['state'] == 'refused' and gate['reason']
    assert not any(n['type'] == 'Runtime' for n in path['nodes'])


def test_sme_page_refusal_keeps_the_successful_retail_answer_graph_path(comparison_engine):
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_planner_request_jobs import budgets

    eng, fake = comparison_engine
    caller = Combined(eng, llm=None)
    question = 'Active customer count and revenue for June 2025'
    products = ['sme.monthly_client_revenue']
    caller.ask(question, 'analyst_enterprise', products=products)
    if fake:
        fake.requests.clear()
    answer = caller.ask(question, 'analyst_enterprise', products=products)
    assert answer['behaviour'] == 'combined' and answer['unit'] == 'retail', answer
    assert answer['products'] == products
    assert [step['outcome'] for step in answer['trace'] if step['step'] == 'plan'] == ['abstain', 'result']
    assert any(step['step'] == 'scope_fallback' and step['units'] == ['retail'] for step in answer['trace'])
    assert [cell['value']['value'] for cell in answer['result']['rows'][0]['cells']] == [176, '12341.02']
    path = answer['graph_path']
    assert 'error' not in path
    assert {'Measure:retail.revenue', 'Table:retail.monthly_revenue', 'Table:retail.customer_activity', 'Runtime:reference'} <= {
        n['id'] for n in path['nodes'] if n['used']}
    refused, = [edge for edge in path['edges'] if edge['state'] == 'refused']
    assert refused['reason'] and refused['to'] == 'Check:query/plan'
    assert not any(edge['from'] == refused['to'] for edge in path['edges'])
    assert any(edge['type'] == 'COMPUTED_FROM' and edge['used'] and edge['to'] == 'Table:retail.monthly_revenue'
               for edge in path['edges'])
    assert path['nodes'][-1]['type'] == 'Runtime'
    if fake:
        assert budgets(fake) == dict(graph=6, retrieval=2, warehouse=1, dry=1, catalogue=0)
