"""The Agent query-plan/v1 loop on LangGraph: its steps, its context and its treatment arm's plan through the KGE tools
its turn is given (the KgeTools port: the in-process tools with none, a person's MCP tools otherwise), its tokens, and
the labels on its synthetic recordings.

The frozen answer seals were retired (owner 2026-11-16): figures, plans and refusals are tested directly, against the
recorded model replies the replay tests use.
HTTP people credentials are compared over ASGI as well as the manager's live
socket test. Opaque history fingerprints are normalized only for comparing
people and their sole effective role; authorization uses the actual principal.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from api import runtrace
from api.agent import Agent
from pipeline.llm import LLM
from tests.scratch import BUILD, DEMO, profile_of

FIXTURES = DEMO / "tests" / "fixtures"
SETS = ("golden", "heldout", "golden_wealth_v1")
ARMS = ("control", "treatment")
ADVERSARIAL = ("golden:G001:control", "golden:G012:control", "golden:G023:control", "golden:G034:control",
               "golden:G045:control", "golden_wealth_v1:W06:control", "golden_wealth_v1:W17:control", "heldout:H011:control")


def modes() -> tuple[str, ...]:
    from pipeline.mockllm import MODES
    return MODES


def llm(mode: str | None):
    """None for rules; the mock in mode otherwise."""
    return None if mode is None else LLM("mock", "mock", mode=mode)


def questions():
    """(<set>:<id>:<arm>, question, principal) for every question of the three sets on each arm, as its principal, else the
    deployment's asker."""
    from eval import sets
    asker = profile_of().asking(None)
    for name in SETS:
        for it in sets.items(BUILD / f"{name}.json"):
            for arm in ARMS:
                yield f"{name}:{it['id']}:{arm}", it["question"], it.get("principal") or asker, arm
    for it in sets.items(BUILD / 'golden_figures_v1.json'):
        if it['id'] == 'F04':
            for arm in ARMS:
                yield f"golden_figures_v1:F04:{arm}", it['question'], it.get('principal') or asker, arm


def sha(text: str) -> str:
    """text's sha256 in base32: no hex digest, which the disclosure guard reads as an account number."""
    return base64.b32encode(hashlib.sha256(text.encode()).digest()).decode().rstrip("=").lower()


def held() -> dict[str, list[str]]:
    """The questions each mode is asked, by mode: rules and careful every question on both arms, each adversary eight."""
    keys = [key for key, *_ in questions()]
    return {"rules": keys, **{mode: list(keys) if mode == "careful" else list(ADVERSARIAL) for mode in modes()}}


def prepare(agent, question, who, arm):
    if agent.mode == 'mock':
        from tests.agent_recordings import for_question
        mode = agent.llm.chat.mode
        agent.llm = for_question(agent.e, question, who, arm, mode)


def asked(agent: Agent, question: str, who: str, arm: str) -> dict:
    prepare(agent, question, who, arm)
    return runtrace.run("agent", lambda: agent.ask(question, who, arm=arm), graph=agent.g)


def answer_of(a: dict) -> dict:
    held = json.loads(json.dumps({k: v for k, v in a.items() if k not in ("run_trace", "graph_path")}, default=str))
    turns = {h['turn_id']: 'turn-' + str(i) for i, h in enumerate(held.get('history', []))}
    def normalized(value):
        if isinstance(value, dict):
            return {k: '(identity-bound)' if k == 'access_fingerprint' else normalized(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalized(v) for v in value]
        return turns.get(value, value) if isinstance(value, str) else value
    return normalized(held)


SYNTHETIC_REASON = "EM-authored, replaces a stale Gemini recording until the owner's live re-record"


def test_every_synthetic_recording_carries_its_label():
    """EM decision 2026-11-16: no synthetic reply passes as a model's. Every labelled recording is listed in the re-seal
    manifest, and every listed one is labelled, with the stated reason."""
    from tests.agent_recordings import PATH, synthetic
    rows = json.loads(PATH.read_text())
    listed = synthetic()
    labelled = {row['case'] for row in rows if 'synthetic' in row or 'synthetic_reason' in row}
    assert listed and labelled == set(listed)
    for row in rows:
        if row['case'] in listed:
            assert row['synthetic'] is True and row['synthetic_reason'] == SYNTHETIC_REASON, row['case']
            assert listed[row['case']]['synthetic'] is True and listed[row['case']]['reason'] == SYNTHETIC_REASON, row['case']
            assert 'synthetic' not in json.dumps(row['reply']), row['case']
    public = synthetic('public_recording')
    rows = [row for path in sorted((FIXTURES / 'query-plan-v1').glob('*.json')) for row in json.loads(path.read_text())]
    labelled = {row['key']: row for row in rows if 'synthetic' in row or 'synthetic_reason' in row}
    assert public and {sha(key) for key in labelled} == {held['live'] for held in public.values()}
    for case, held in public.items():
        row = next(row for key, row in labelled.items() if sha(key) == held['live'])
        assert row['synthetic'] is held['synthetic'] is True and row['synthetic_reason'] == held['reason'] == SYNTHETIC_REASON, case
        assert held['case'] == 'public:' + json.loads(row['key'])[1] and held['reply_sha'] == sha(json.dumps(row['reply'], sort_keys=True))
        assert 'synthetic' not in json.dumps(row['reply']), case
        stale = next(r['reply'] for r in rows if sha(r['key']) == held['recorded'])
        assert sha(json.dumps(stale, sort_keys=True)) == held['stale_reply_sha'], case
        swapped = json.dumps(stale)
        for old, new in held['swapped'].items():
            swapped = swapped.replace(json.dumps(old), json.dumps(new))
        plan = json.loads(swapped)['plan']
        plan['measures'] = [m for m in plan['measures'] if m['id'] not in held['dropped']]
        plan['obligations'] = [dict(o, targets=[t for t in o['targets'] if t not in held['dropped']])
                               for o in plan['obligations'] if set(o['targets']) - set(held['dropped'])]
        assert row['reply'] == dict(json.loads(swapped), plan=plan), 'the stale reply, ids swapped, unrequested outputs dropped'


FIG = "Total retail revenue for June 2025"
CROSS = "Combined Retail and SME revenue for June 2025"
SIGN_IN = "sign in: POST /session with a token, or send Authorization: Bearer <token>"


def ran(agent: Agent, question: str, who: str, arm: str) -> tuple[list[str], dict]:
    """(the loop's nodes in the order they ran, the answer), under a run trace."""
    prepare(agent, question, who, arm)
    out = runtrace.run("agent", lambda: {"updates": list(agent.loop.stream(agent.turn(question, who, arm=arm), stream_mode="updates"))},
                       graph=agent.g)
    nodes = [next(iter(u)) for u in out["updates"]]
    return nodes, next(v["answer"] for u in out["updates"] for v in u.values() if v and "answer" in v)


def test_the_loop_is_a_state_graph_with_fixed_edges_one_node_per_step(eng):
    """The mode picks the branch at START, rules with no model; after that each step's edge is fixed, and every step runs:
    one that finds the answer given passes it on."""
    from langgraph.graph.state import CompiledStateGraph

    from api.agent import MODEL_STEPS, RULES_STEPS
    assert MODEL_STEPS == ("context", "plan", "answer") and RULES_STEPS == ("rules", "evidence")
    agent = Agent(eng, llm=None)
    assert isinstance(agent.loop, CompiledStateGraph)
    graph = agent.loop.get_graph()
    assert set(graph.nodes) == {"__start__", "__end__", *MODEL_STEPS, *RULES_STEPS}
    edges = {(e.source, e.target, e.conditional) for e in graph.edges}
    chain = lambda steps: {(a, b, False) for a, b in zip(steps, steps[1:])} | {(steps[-1], "__end__", False)}  # noqa: E731
    assert edges == {("__start__", MODEL_STEPS[0], True), ("__start__", RULES_STEPS[0], True)} | chain(MODEL_STEPS) | chain(RULES_STEPS)
    for mode, steps in ((None, RULES_STEPS), ("careful", MODEL_STEPS), ("malformed_json", MODEL_STEPS)):
        a = Agent(eng, llm=llm(mode))
        for arm in ARMS:
            nodes, answer = ran(a, FIG, "analyst_enterprise", arm)
            assert nodes == list(steps), (mode, arm)
            assert answer_of(answer) == answer_of(asked(a, FIG, "analyst_enterprise", arm)), (mode, arm)
    nodes, answer = ran(Agent(eng, llm=llm("careful")), FIG, "pipeline_tagger", "treatment")
    assert nodes == list(MODEL_STEPS) and answer["behaviour"] == "refuse" and answer["reason"] == "pipeline_tagger is not a querying identity."


def test_each_arm_gets_its_context_through_the_in_process_tools_as_the_principal(eng, monkeypatch):
    """With no tools given: the in-process tools, POST /context's chain without the hop, through as_json and its JSON. The
    package is built as the principal, once per answer, on each arm the same way; a field as_json gives reaches the agent
    only as JSON gives it back, a tuple as a list."""
    import kge.services.ask.engine.inprocess as inprocess
    real = inprocess.as_json
    monkeypatch.setattr(inprocess, "as_json", lambda pkg: {**real(pkg), "probe": ("through", "json")})
    for mode in (None, "careful"):
        agent = Agent(eng, llm=llm(mode))
        built, fetch, served, got = agent.context, agent.fetch, [], []
        monkeypatch.setattr(agent, "context", lambda *a, **kw: served.append(a) or built(*a, **kw))
        monkeypatch.setattr(agent, "fetch", lambda *a, **kw: got.append(fetch(*a, **kw)) or got[-1])
        for arm in ARMS:
            served.clear()
            got.clear()
            asked(agent, FIG, "analyst_retail", arm)
            assert served == [(FIG, "analyst_retail", "analysis", arm)], (mode, arm)
            assert len(got) == 1 and got[0]["probe"] == ["through", "json"] and got[0]["arm"] == arm, (mode, arm)


@pytest.fixture
def mcp_tools(eng):
    """The API's own app in this process serving eng (kge/app.py, serving), its MCP mounts running (TestClient's
    lifespan): tools(token) gives the KGE tools over MCP with that bearer, as /agent opens them for a person."""
    import anyio.lowlevel
    from fastapi.testclient import TestClient

    import kge.app as appmod
    from kge.adapters.kgetools import kge_tools
    server = Agent(eng, llm=None)
    with appmod.serving(server), TestClient(appmod.app) as client:
        loop = client.portal.call(anyio.lowlevel.current_token)
        yield SimpleNamespace(server=server, tools=lambda token: kge_tools("mcp", url="http://kge", bearer=token,
                                                                           app=appmod.app, loop=loop))


def test_the_token_check_is_the_api_s_over_a_person_s_mcp_tools(eng, mcp_tools):
    """A bad token, or none, is the MCP mount's 401 through the tools (SignInRefused), and the agent gives no answer;
    another principal's token gets that principal's package, whoever the agent asks for: the tool decides who from the
    token alone."""
    from kge.adapters.kgetools import SignInRefused
    agent = Agent(eng, llm=llm("careful"))
    for bad in ("nope", ""):
        with pytest.raises(SignInRefused) as refused:
            agent.ask(FIG, "analyst_retail", arm="treatment", tools=mcp_tools.tools(bad))
        assert refused.value.status == 401, bad
    enterprise = mcp_tools.tools(mcp_tools.server.token("analyst_enterprise"))
    assert agent.fetch(CROSS, "analyst_retail", "analysis", "treatment", tools=enterprise)["units"] == ["retail", "sme", "corporate", "wealth"]
    retail = mcp_tools.tools(mcp_tools.server.token("analyst_retail"))
    assert agent.fetch(CROSS, "analyst_enterprise", "analysis", "treatment", tools=retail)["units"] == ["retail"]


def test_agents_asked_at_once_before_any_token_exists_share_one_set_of_tokens(eng, tmp_path, monkeypatch):
    """On a run state with no tokens.json yet, the first calls of two agents made at once from many threads build each
    agent's identities once, and both hold the tokens one of them wrote: a token a thread asks with is one the route
    checks it against. (Each build is slowed, so the calls do come at once.)"""
    import dataclasses
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import api.agent as agentmod
    agents = [Agent(eng, llm=None), Agent(eng, llm=llm("careful"))]
    monkeypatch.setattr(eng, "profile", dataclasses.replace(eng.profile, store=tmp_path / "stores"))
    run = eng.profile.run_dir()
    built, real = [], agentmod.Identities

    def slow(*args, **kw):
        time.sleep(0.05)
        built.append(threading.get_ident())
        return real(*args, **kw)
    monkeypatch.setattr(agentmod, "Identities", slow)
    assert not (run / "tokens.json").exists()
    with ThreadPoolExecutor(16) as pool:
        got = list(pool.map(lambda k: (k % 2, agents[k % 2].identities), range(32)))
    assert len(built) == 2, f"{len(built)} builds for 2 agents"
    assert all(ids is agents[k].identities for k, ids in got), "each agent's threads share its one build"
    on_disk = json.loads((run / "tokens.json").read_text())
    for a in agents:
        assert a.identities.by_token == {t: p for p, t in on_disk.items()}


def gated(pkg: dict) -> dict:
    """The package as the agent reads it, its plan by what the gate reads of it: its ids, its unit and units, its allowlist."""
    plan = pkg.get("plan")
    return {**pkg, "plan": None if plan is None else {"about": plan.about(), "unit": plan.unit, "units": plan.units, "allow": plan.allow}} \
        if "plan" in pkg else pkg


def test_the_package_through_the_contract_is_the_one_the_workflow_builds(eng, monkeypatch):
    """For every question graph on, what POST /context gives the agent, read back from its JSON, is the package the context
    workflow builds: field for field, a context's plan planned again on the agent's graph to the same ids, unit and
    allowlist; and a plan the graph does not plan so is refused. (Graph off's package holds nothing JSON changes.)"""
    from api.context import ContextError, as_json, from_json
    from tests.test_context import canon
    agent = Agent(eng, llm=None)
    planned = 0
    for key, q, who, arm in questions():
        if arm != "treatment":
            continue
        built = agent.context(q, who, "analysis", arm)
        got = agent.fetch(q, who, "analysis", arm)
        assert canon(gated(got)) == canon(gated(built)), key
        planned += got.get("plan") is not None
        if got.get("plan") is not None:
            moved = json.loads(json.dumps(as_json(built)))
            moved["plan"]["units"] = moved["plan"]["units"] + ["elsewhere"]
            with pytest.raises(ContextError, match="is not one this graph plans for the question"):
                from_json(moved, eng, q)
    assert planned > 0, "a context's plan among them"




def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory, eng):
    """The API on a scratch copy of the build, served by uvicorn in a process of its own on TEST_PORT (else a free port): the
    KGE whose MCP tools the agent in this process reaches as a person, from the demo's own graph. The demo's tokens are copied
    into its run state before it starts, as one identity provider would give both the same. Stopped by its pid."""
    import os
    import shutil
    import subprocess
    import time
    from types import SimpleNamespace

    import httpx

    from pipeline.state import graph_store
    from tests.scratch import registry, run_of, scratch_build
    build = scratch_build(tmp_path_factory.mktemp("agent-live") / "build")
    Agent(eng, llm=None).identities
    run_of(build).mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_of() / "tokens.json", run_of(build) / "tokens.json")
    port = int(os.environ.get("TEST_PORT") or free_port())
    log = build / "api.log"
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--app-dir", str(DEMO), "--host", "127.0.0.1",
                                 "--port", str(port)], cwd=DEMO, stdout=out, stderr=subprocess.STDOUT,
                                env={**os.environ, "PROFILES": str(registry(build)), "PROFILE": "", "GRAPH_STORE": graph_store(),
                                     "MODEL_PROVIDER": "none", "DEMO_MODE": "", "API_URL": ""})
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            if proc.poll() is not None:
                pytest.fail(f"the API stopped: {log.read_text()[-2000:]}", pytrace=False)
            try:
                if httpx.get(f"{url}/profile", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            pytest.fail(f"the API did not start on {url}: {log.read_text()[-2000:]}", pytrace=False)
        yield SimpleNamespace(url=url, log=log, build=build)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_with_a_person_s_mcp_tools_at_a_live_service_the_answers_are_the_ones_in_this_process(live, eng):
    """A person's MCP tools at the live API, over HTTP with the person's own token: the agent gets each arm's package
    from get_context there, and its treatment arm's plan from ask there (the live API's rules, as it has no model). On
    the control arm its answer is the one with the in-process tools, whole: on rules every question of the three sets, on
    the mock careful every fourth, and each adversary on eight control questions. On the treatment arm, on rules, every
    question's behaviour, unit, figure, parts by unit (the per-part records are the compact view's to drop), result
    (as a client reads it, client_result) and history are the in-process ones, and where the evidence step bound figures, its figures (unit, value, column, citation), citations
    and references; an answer with no figure is the planner's alone (Ask's, cited by its shelves over MCP), and the
    whole answer differs by design (Ask's labels, answer mode and compact view). The mock modes' treatment arm is not
    compared: over MCP its plan is the live API's, which has no model, so the agent's mock plans nothing there."""
    from kge.adapters.kgetools import kge_tools
    from kge.services.identity import token_for
    from tests.scratch import run_of
    asks = {key: (q, f"{who}.meridian@example.com", arm) for key, q, who, arm in questions()}
    by_mode = held()
    by_mode["careful"] = by_mode["careful"][::4]

    def tools(person):
        return kge_tools("mcp", url=live.url, bearer=token_for(run_of(live.build), person))

    def bound(a, figures):
        from kge.services.ask.engine.planpublic import client_result
        held = answer_of(client_result(a))
        answer = {k: held.get(k) for k in ("behaviour", "unit", "figure", "history")}
        answer["parts"] = held["parts"] if isinstance(held.get("parts"), dict) else None
        answer["rows"] = (held.get("result") or {}).get("rows")
        if figures:
            answer.update(figures=[{k: f.get(k) for k in ("unit", "value", "column", "cite")} for f in held.get("figures") or []],
                          citations=held.get("citations"), references=held.get("references"))
        return answer
    fetched = treated = 0
    for mode, keys in by_mode.items():
        here = Agent(eng, llm=llm(None if mode == "rules" else mode))
        for key in keys:
            q, person, arm = asks[key]
            if arm == "treatment" and mode != "rules":
                continue
            prepare(here, q, person, arm)
            there = runtrace.run("agent", lambda: here.ask(q, person, arm=arm, tools=tools(person)), graph=here.g)
            mine = asked(here, q, person, arm)
            if arm == "control":
                assert answer_of(there) == answer_of(mine), f"{key}:{mode}"
            else:
                figures = any(t["step"] == "bind" for t in mine.get("trace") or [])
                assert bound(there, figures) == bound(mine, figures), f"{key}:{mode}"
                treated += 1
            fetched += any(t["step"] == "context" and "sources" in t for t in there.get("trace") or [])
    assert fetched > 0 and treated > 0
    assert live.log.read_text().count('"POST /mcp/answer HTTP/1.1" 200') > 0, "the treatment arm's plan from the live API"


def test_an_agent_whose_mcp_tools_reach_nothing_gives_no_answer(eng):
    """A person's MCP tools where nothing serves: the call is an outside call that did not answer (ExternalCallError,
    transient, the API's 503), and the agent gives no answer."""
    from kge.adapters.deadline import ExternalCallError
    from kge.adapters.kgetools import kge_tools
    agent = Agent(eng, llm=None)
    with pytest.raises(ExternalCallError) as raised:
        agent.ask(FIG, "analyst_retail.meridian@example.com", arm="treatment",
                  tools=kge_tools("mcp", url="http://127.0.0.1:0", bearer="token"))
    assert raised.value.transient
