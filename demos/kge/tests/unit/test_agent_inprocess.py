"""The reference agent behind the KgeTools port (#497): its treatment arm's plan comes from the turn's tools' ask, its
control arm's from this process; its context from the tools' get_context, read back from JSON; with no tools, the
in-process tools, which are POST /context's chain without the hop and the agent's own coordinator or rules; and the
evidence step reads a compact answer (no SQL, plan or trace). Stubs only: no built graph is read."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine import agent as agentmod
from kge.services.ask.engine.agent import ASK_ARM, MODEL_STAGES, RULES_STAGES, Agent, planned_figures
from kge.services.ask.engine.inprocess import InProcessTools

PLANNED = {"question": "q", "behaviour": "abstain", "reason": "No figure.", "answer_text": "No figure.", "phrased_by": "template"}


class Tools:
    """KgeTools that record each call and give fixed JSON."""

    def __init__(self, package=None, answer=None):
        self.calls = []
        self.package = package
        self.answer = answer if answer is not None else dict(PLANNED)

    def get_context(self, question, **kw):
        self.calls.append(("get_context", question, kw))
        return self.package

    def ask(self, question, **kw):
        self.calls.append(("ask", question, kw))
        return self.answer


class Engine:
    def __init__(self):
        self.g = SimpleNamespace()
        self.ruled_calls = []

    def ruled(self, question, principal, purpose, **kw):
        self.ruled_calls.append((question, principal, purpose, kw))
        return dict(PLANNED)

    def stamped(self, cfg):
        return cfg


def turn(arm, tools, on_event=None):
    return {"question": "q", "principal": "ann", "purpose": "analysis", "arm": arm, "cfg": "cfg", "products": ["retail.t"],
            "history": ("h",), "runtimes": None, "tools": tools, "on_event": on_event}


@pytest.fixture
def agent():
    return Agent(Engine(), llm=None)


def test_the_treatment_arm_plans_through_the_turn_s_tools_on_ask_s_graph_arm(agent):
    def relay(event):
        return None
    for step, stages in ((agent.rules_step, RULES_STAGES), (agent.plan_step, MODEL_STAGES)):
        tools = Tools()
        assert step(turn("treatment", tools, relay)) == {"planned": PLANNED}
        assert tools.calls == [("ask", "q", {"purpose": "analysis", "arm": ASK_ARM, "stages": stages, "products": ["retail.t"],
                                             "history": ("h",), "runtimes": None, "on_event": relay})]
    assert ASK_ARM == "graph" and RULES_STAGES == {"resolution": "glossary", "context": "graph", "sql_source": "planner",
                                                   "citation_rule": "shelves", "renderer": "template"}
    assert MODEL_STAGES == {"renderer": "template"}
    assert agent.e.ruled_calls == []


def test_the_control_arm_plans_in_this_process_and_never_asks_the_tools(agent):
    tools = Tools()
    assert agent.rules_step(turn("control", tools)) == {"planned": PLANNED}
    assert tools.calls == []
    assert agent.e.ruled_calls == [("q", "ann", "analysis", {"config": "cfg", "products": ["retail.t"], "history": ("h",), "runtimes": None})]


def test_fetch_reads_the_tools_json_back_into_the_package(agent):
    wire = {"arm": "treatment", "block": "b", "sources": [], "units": ["retail"], "certified": {"retail": [["t", "c"]]},
            "in_window": [], "defines": {"retail": {"t.c": ["Revenue"]}}, "meta": {"retail": {"t.c": {"grain": "month"}}},
            "named": [], "withheld": [], "needed": [], "plan": None, "decided": None}
    tools = Tools(package=wire)
    pkg = agent.fetch("q", "ann", "analysis", "treatment", plan_vocabulary=True, products=["retail.t"], history=("h",), tools=tools)
    assert pkg["certified"] == {"retail": [("t", "c")]}
    assert pkg["defines"] == {("retail", "t.c"): ["Revenue"]} and pkg["meta"] == {("retail", "t.c"): {"grain": "month"}}
    assert tools.calls == [("get_context", "q", {"purpose": "analysis", "arm": "treatment", "products": ["retail.t"],
                                                 "plan_vocabulary": True, "history": ("h",), "runtimes": None})]


def package(**more):
    return {"arm": "treatment", "block": "b", "sources": [{"n": 1, "doc": "D", "citable": True}], "units": ("retail",),
            "certified": {"retail": [("t", "c")]}, "in_window": (1,), "defines": {("retail", "t.c"): ("Revenue",)},
            "meta": {}, "group": None, "named": [], "withheld": [], "needed": [], "decided": None, **more}


def test_the_in_process_context_is_the_route_s_chain_as_json(agent):
    asked = []

    def context(*args, **kw):
        asked.append((args, kw))
        return package(plan_vocabulary={"names": [{"id": "ctx", "kinds": ("context",)}]}) if kw.get("plan_vocabulary") else package()
    agent.context = context
    tools = InProcessTools(agent, "ann")
    plain = tools.get_context("q", purpose="analysis", arm="treatment", products=["retail.t"], plan_vocabulary=False,
                              history=("h",), runtimes=["reference"])
    assert asked == [(("q", "ann", "analysis", "treatment"), {"runtimes": ["reference"]})]
    assert plain["units"] == ["retail"] and plain["in_window"] == [1] and plain["certified"] == {"retail": [["t", "c"]]}
    assert plain["defines"] == {"retail": {"t.c": ["Revenue"]}} and tools.plan_vocabulary is None
    vocabulary = tools.get_context("q", purpose="analysis", arm="treatment", products=["retail.t"], plan_vocabulary=True,
                                   history=("h",), runtimes=None)
    assert asked[1] == (("q", "ann", "analysis", "treatment"),
                        {"products": ["retail.t"], "plan_vocabulary": True, "history": ("h",), "runtimes": None})
    assert vocabulary["plan_vocabulary"] == tools.plan_vocabulary == {"names": [{"id": "ctx", "kinds": ["context"]}]}


def test_the_in_process_ask_is_the_agent_s_rules_or_its_coordinator_on_the_turn_s_config(agent, monkeypatch):
    tools = InProcessTools(agent, "ann", "cfg")
    assert tools.ask("q", purpose="analysis", arm=ASK_ARM, stages=RULES_STAGES, products=None, history=(), runtimes=None) == PLANNED
    assert agent.e.ruled_calls == [("q", "ann", "analysis", {"config": "cfg", "products": None, "history": (), "runtimes": None})]
    made = []

    class Combined:
        def __init__(self, engine, llm):
            made.append(self)
            self.engine, self.llm, self.plan_vocabulary = engine, llm, "unset"

        def governed(self, question, principal, purpose, **kw):
            self.asked = (question, principal, purpose, kw)
            return {"governed": True}
    import kge.services.ask.engine.combined as combined
    monkeypatch.setattr(combined, "Combined", Combined)
    agent.llm = model = SimpleNamespace(provider="mock")
    tools.plan_vocabulary = {"names": []}
    assert tools.ask("q", purpose="analysis", arm=ASK_ARM, stages=MODEL_STAGES, products=["retail.t"], history=("h",),
                     runtimes=None, on_event=print) == {"governed": True}
    assert made[0].llm is model and made[0].plan_vocabulary == {"names": []} and made[0].engine is agent.e
    assert made[0].asked == ("q", "ann", "analysis", {"config": "cfg", "products": ["retail.t"], "history": ("h",), "runtimes": None})


def test_a_turn_with_no_tools_gets_in_process_tools_for_its_principal_and_config(agent, monkeypatch):
    monkeypatch.setattr(agentmod.runtrace, "agent", lambda *a, **kw: None)
    t = agent.turn("q", "ann", arm="treatment")
    assert isinstance(t["tools"], InProcessTools) and t["tools"].principal == "ann" and t["tools"].cfg is t["cfg"]
    assert t["tools"].agent is agent and t["on_event"] is None
    given = Tools()
    t = agent.turn("q", "ann", arm="control", tools=given, on_event=print)
    assert t["tools"] is given and t["on_event"] is print


class Context:
    def __init__(self, id, unit, column):
        self.id, self.unit, self.aggregation, self.values = id, unit, "sum", [f"{unit}.{column}"]


def test_a_compact_answer_s_figures_are_read_from_its_parts_and_result(monkeypatch):
    engine = SimpleNamespace()
    contexts = {"retail_revenue": Context("retail_revenue", "retail", "monthly.rev"), "sme_revenue": Context("sme_revenue", "sme", "client.rev")}
    monkeypatch.setattr(agentmod.planrules, "snapshot", lambda e: SimpleNamespace(output_metadata={}, contexts=contexts))

    def measure(ctx):
        return {"id": ctx, "kind": "measure", "lineage": [ctx, ctx.replace("_", "."), "2025-06-01", "2025-07-01"]}
    compact = {"behaviour": "parts_with_reason", "parts": {"retail": 10.0, "sme": 20.5, "corporate": None},
               "result": {"columns": [{"id": "d0", "kind": "dimension", "lineage": []}, measure("retail_revenue"), measure("sme_revenue")]}}
    assert planned_figures(compact, engine) == [
        {"id": "f1", "unit": "retail", "value": 10.0, "column": "monthly.rev", "sql": None, "join_path": []},
        {"id": "f2", "unit": "sme", "value": 20.5, "column": "client.rev", "sql": None, "join_path": []}]
    assert planned_figures(compact) == []
    single = {"behaviour": "figure", "figure": 3.0, "unit": "retail", "column": "monthly.rev"}
    assert planned_figures(single, engine) == [{"id": "f1", "unit": "retail", "value": 3.0, "column": "monthly.rev", "sql": None, "join_path": []}]
    whole = {"behaviour": "sum_with_verdict", "parts": {"retail": 10.0}, "plan": [
        {"unit": "retail", "value": 10.0, "column": "monthly.rev", "sql": "SELECT 1", "status": "ok"}]}
    assert planned_figures(whole, engine) == [{"id": "f1", "unit": "retail", "value": 10.0, "column": "monthly.rev", "sql": "SELECT 1", "join_path": []}]
    engine.BUSINESS = {"monthly.rev": "monthly_revenue.revenue_usd"}
    named = {**single, "column": "monthly_revenue.revenue_usd"}
    assert planned_figures(named, engine)[0]["column"] == "monthly.rev"
    assert planned_figures(named)[0]["column"] == "monthly_revenue.revenue_usd"
    keys = {**single, "column": "monthly_revenue.revenue_usd, account.source_system"}
    assert planned_figures(keys, engine)[0]["column"] == "monthly.rev, account.source_system"


def test_the_evidence_step_passes_on_a_compact_answer_with_no_trace(agent, monkeypatch):
    monkeypatch.setattr(agentmod.runtrace, "stage", lambda name: None)
    t = {**turn("treatment", Tools()), "planned": {**PLANNED, "graph_path": {"nodes": [], "edges": []}},
         "ctx": {"sources": [{"n": 1, "doc": "D", "citable": True}], "named": [], "block": "b"},
         "info": {"arm": "treatment", "mode": "rules"}}
    answer = agent.evidence_step(t)["answer"]
    assert answer["graph_path"] == {"nodes": [], "edges": []} and answer["answer_text"] == "No figure."
    assert answer["trace"][-1]["step"] == "context" and answer["trace"][-1]["sources"] == [[1, "D", True]]
