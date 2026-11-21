"""The reference agent's in-process tools against the real route: the package its context step reads with no tools given
(InProcessTools, the route's chain without the ASGI hop) is the one POST /context gives the person, read back the same
way, for every question of the sets on both arms; a follow-up chain through the in-process agent keeps its history; and
the evidence step reads a compact answer, which is what Ask's ask tool gives anyone but an engineer, as it reads the
whole one."""
import json

import pytest

from kge.services.ask import service
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.context import from_json
from tests.integration.test_answer_text import client as client
from tests.scratch import BUILD

QUESTION_SETS = ("golden", "heldout", "golden_wealth_v1")
ARMS = ("control", "treatment")


def questions(asker: str):
    """(<set>:<id>, question, person) for every question of the three sets and golden_figures_v1's F04, as its principal's
    person, else the deployment's asker's."""
    from kge.eval import sets
    for name in (*QUESTION_SETS, "golden_figures_v1"):
        for it in sets.items(BUILD / f"{name}.json"):
            if name == "golden_figures_v1" and it["id"] != "F04":
                continue
            yield f"{name}:{it['id']}", it["question"], f"{it.get('principal') or asker}.meridian@example.com"


def gated(pkg: dict) -> dict:
    """The package as the agent reads it, its plan by what the gate reads of it: its ids, its unit and units, its allowlist."""
    plan = pkg.get("plan")
    return {**pkg, "plan": None if plan is None else {"about": plan.about(), "unit": plan.unit, "units": plan.units, "allow": plan.allow}} \
        if "plan" in pkg else pkg


@pytest.fixture(scope="module")
def agent(client):
    import kge.app as appmod
    return Agent(appmod.engine(), llm=None)


@pytest.mark.parametrize("question_set", QUESTION_SETS)
def test_the_in_process_package_is_the_route_s_for_the_person_on_both_arms(client, agent, question_set):
    included = {question_set, "golden_figures_v1"} if question_set == "golden" else {question_set}
    compared = planned = 0
    for key, question, person in questions(client.asker):
        if key.split(":", 1)[0] not in included:
            continue
        for arm in ARMS:
            for vocabulary in (False, True):
                body = {"question": question, "arm": arm, **({"plan_vocabulary": True} if vocabulary else {})}
                r = client.post("/context", json=body, headers=client.as_(person))
                assert r.status_code == 200, (key, arm, vocabulary, r.text)
                routed = from_json(r.json(), agent.e, question)
                here = agent.fetch(question, person, "analysis", arm, plan_vocabulary=vocabulary)
                assert gated(here) == gated(routed), (key, arm, vocabulary)
                compared += 1
                planned += here.get("plan") is not None
    assert compared > 0
    if question_set == "golden_wealth_v1":
        assert planned > 0, "a context's plan among them"


def test_the_in_process_agent_carries_a_person_s_history_through_ask_and_assist(client, agent):
    from kge.semantics.plan import HistoryEntry
    from kge.services.ask.engine.combined import Combined
    from kge.services.assist.service import assist
    engine = agent.e
    question = "What is Retail Banking revenue in June 2025?"
    person = "analyst_retail.meridian@example.com"
    combined = Combined(engine, llm=None)
    first = combined.ask(question, person)
    second = agent.ask(question, person, history=[HistoryEntry.model_validate(h) for h in first['history']])
    third = assist(combined, engine.profile, question, person, 'analysis', 'graph', combined.default_config(),
                   history=[HistoryEntry.model_validate(h) for h in second['history']])
    assert first['figure'] == second['figure'] == third['figure'] == 13287.67
    assert len(second['history']) == 2 and len(third['history']) == 3
    assert first['history'][0] == second['history'][0] == third['history'][0]


def test_the_evidence_step_reads_a_compact_answer_as_it_reads_the_whole_one(client, agent):
    """For every question of the sets on the treatment arm, as its person: the plan the agent's in-process tools give,
    and Ask's compact reply as the ask tool gives it (POST /ask's: business column names, no SQL, plan or trace), bound
    by the evidence step to the same figures, units, columns, values and citations; an answer with no figure is the
    planner's alone, passed on as given."""
    from kge.services.ask.engine.agent import RULES_STAGES
    from kge.services.ask.engine.inprocess import InProcessTools
    import kge.app as appmod
    combined = appmod.combined()
    cfg = combined.default_config("graph").with_stages(**RULES_STAGES)
    bound = parts = 0

    def held(a):
        return ([{k: f[k] for k in ("unit", "value", "column", "cite")} for f in a.get("figures") or []],
                a.get("citations"), a.get("citations_rejected"), a.get("references"))
    for key, question, person in questions(client.asker):
        whole = InProcessTools(agent, person, agent.e.stamped(agent.config("treatment"))).ask(
            question, purpose="analysis", arm="graph", stages=RULES_STAGES, products=None, history=(), runtimes=None)
        compact = json.loads(json.dumps(service.ask(combined, appmod.PROFILE, question, person, "analysis", "graph", cfg,
                                                    compact=True), default=str))
        assert not {"sql", "plan", "trace"} & set(compact), key
        ctx = agent.fetch(question, person, "analysis", "treatment", plan_vocabulary=True)
        full = agent.evidence(question, person, "analysis", "treatment", whole, ctx=dict(ctx))
        read = agent.evidence(question, person, "analysis", "treatment", compact, ctx=dict(ctx))
        if full is whole:
            assert read is compact, key
            continue
        assert held(read) == held(full), key
        bound += 1
        parts += len(full.get("figures") or []) > 1
    assert bound > 0 and parts > 0, "single figures and parts bound among them"
