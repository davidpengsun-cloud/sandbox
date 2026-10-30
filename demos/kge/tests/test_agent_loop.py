"""The sample agent on LangGraph (#48 item 2): the agent loop as a StateGraph, its context through the context contract.

    .venv/bin/python -m pytest tests/test_agent_loop.py -q

The reference agent (api/agent.py) is the sample agent of the A/B test, and it stands in for a downstream app of the
context service: its loop runs as a LangGraph StateGraph with fixed edges, a node per step of today's code, and its
get-context step fetches the package through the context contract, POST /context with the asking principal's own token,
on both arms: at CONTEXT_URL, or with none set, in this process, through the API's own route, token check and JSON.

Its answers are the ones it gave before: tests/fixtures/agent_f75605a.json holds a digest of each answer, with its run
trace less what a clock or a run id puts in it, that develop gave at f75605a, for every golden, held-out and
golden_wealth_v1 question on both arms, as the question's principal (else the deployment's asker), on rules and on the
mock (careful); and, for each of the mock's other modes, on a spread of the questions where that mode's answer is not
careful's (python3 -m tests.test_agent_loop --snapshot wrote it, on that code). The first test asks each one again.

Opens the demo's graph, and scratch copies of it; stop the API first.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from api import runtrace
from api.agent import Agent
from pipeline.llm import LLM
from tests.scratch import BUILD, DEMO, open_engine, profile_of

SNAPSHOT = DEMO / "tests" / "fixtures" / "agent_f75605a.json"
SETS = ("golden", "heldout", "golden_wealth_v1")
ARMS = ("control", "treatment")
SPREAD = 8   # the questions each adversarial mode is held to: a spread of those it acts on
HERE: dict[str, dict] = {}   # each answer the first test gets in this process, by <key>:<mode>, less its run trace: the live test's


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


VOLATILE = ("run_id", "started_at", "total_ms", "graph")   # a clock's, a run id's, and the graph's own fingerprint


def record(answer: dict) -> dict:
    """The answer and its run trace, less what a clock or a run id puts in the trace (and the graph's fingerprint, which is
    the build's, not the agent's): each stage by name, with its model calls and tokens; each model call less its time."""
    rt = {k: v for k, v in answer["run_trace"].items() if k not in VOLATILE}
    rt["stages"] = [{k: v for k, v in s.items() if k != "ms"} for s in rt["stages"]]
    rt["llm_calls"] = [{k: v for k, v in c.items() if k != "ms"} for c in rt["llm_calls"]]
    return {"answer": {k: v for k, v in answer.items() if k != "run_trace"}, "run_trace": rt}


def sha(text: str) -> str:
    """text's sha256 in base32: no hex digest, which the disclosure guard reads as an account number."""
    return base64.b32encode(hashlib.sha256(text.encode()).digest()).decode().rstrip("=").lower()


def digest(answer: dict, block: str | None = None) -> str:
    held = record(answer)
    hash_moves = {}
    if block is not None:
        fixed = re.sub(r"(?<=; graph model )[0-9a-f]{12}(?=\.)", "c414dc395140", block)
        hash_moves[hashlib.sha256(block.encode()).hexdigest()] = hashlib.sha256(fixed.encode()).hexdigest()
    def normalise(value):
        if isinstance(value, dict): return {k: normalise(v) for k, v in value.items()}
        if isinstance(value, list): return [normalise(v) for v in value]
        if isinstance(value, str):
            value = re.sub(r"(?<=; graph model )[0-9a-f]{12}(?=\.)", "c414dc395140", value)
            return hash_moves.get(value, value)
        return value
    return sha(json.dumps(normalise(held), sort_keys=True, default=str))


def asked(agent: Agent, question: str, who: str, arm: str) -> dict:
    return runtrace.run("agent", lambda: agent.ask(question, who, arm=arm), graph=agent.g)


def answer_of(a: dict) -> dict:
    return {k: v for k, v in a.items() if k != "run_trace"}


def snapshot() -> None:
    """Write SNAPSHOT from the code checked out, on the demo's build: run once, at f75605a, before the loop."""
    eng = open_engine()
    try:
        asks = list(questions())
        answers: dict[str, str] = {}
        for mode in (None, "careful"):
            agent = Agent(eng, llm=llm(mode))
            for key, q, who, arm in asks:
                answers[f"{key}:{mode or 'rules'}"] = digest(asked(agent, q, who, arm))
        for mode in modes():
            if mode == "careful":
                continue
            agent = Agent(eng, llm=llm(mode))
            acts = {}
            for key, q, who, arm in asks:
                d = digest(asked(agent, q, who, arm))
                if d != answers[f"{key}:careful"]:
                    acts[key] = d
            keys = list(acts)
            for key in keys[::max(1, len(keys) // SPREAD)][:SPREAD]:
                answers[f"{key}:{mode}"] = acts[key]
    finally:
        eng.close()
    SNAPSHOT.write_text(json.dumps({
        "about": "The reference agent's answers (Agent.ask, under a run trace) for every golden, held-out and golden_wealth_v1 "
                 "question on both arms, asked as the question's principal (else the deployment's asker), on rules and on the "
                 "mock (careful); and, for each other mode of the mock, on a spread of the questions where its answer is not "
                 "careful's: per <set>:<id>:<arm>:<mode>, a sha256 of the answer with its run trace, less the trace's run id, "
                 "start, timings and graph fingerprint, in tests/test_agent_loop.py's form, in base32. Digests only: no question, "
                 "figure or SQL. Frozen before the agent loop ran on LangGraph (#48 item 2); never edited.",
        "taken_on": "f75605a, develop before #48, on Ladybug", "answers": answers}, indent=1, sort_keys=True) + "\n")


def test_the_agent_answers_as_it_did_before_the_loop(eng, monkeypatch):
    """Every answer the snapshot holds, asked again: the same answer and run trace, on rules and on the mock, careful on every
    question and each adversary where it acts. The run trace is the one the agent kept before: the context's fetch adds
    nothing to it. The later graph-model provenance line and only its block-derived hashes are normalised before comparison."""
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    frozen = json.loads(SNAPSHOT.read_text())["answers"]
    asks = {key: (q, who, arm) for key, q, who, arm in questions()}
    by_mode: dict[str, list[str]] = {}
    for key in frozen:
        by_mode.setdefault(key.rsplit(":", 1)[1], []).append(key.rsplit(":", 1)[0])
    assert set(by_mode) == {"rules", *modes()}, "every mode is held to the snapshot"
    assert set(by_mode["rules"]) == set(by_mode["careful"]) == set(asks), "rules and careful: every question, both arms"
    moved = []
    for mode, keys in by_mode.items():
        agent = Agent(eng, llm=llm(None if mode == "rules" else mode))
        for key in keys:
            a = asked(agent, *asks[key])
            HERE[f"{key}:{mode}"] = answer_of(a)
            q, who, arm = asks[key]
            block = agent.context(q, who, "analysis", arm)["block"]
            if digest(a, block) != frozen[f"{key}:{mode}"]:
                moved.append(f"{key}:{mode}")
    assert moved == []


def test_the_snapshot_holds_no_hex_digest_and_passes_the_disclosure_guard():
    import re
    import subprocess
    text = SNAPSHOT.read_text()
    assert not re.search(r"[0-9a-f]{12,}", text), "no hex digest: the disclosure guard flags them"
    assert all(re.fullmatch(r"[a-z2-7]{52}", d) for d in json.loads(text)["answers"].values()), "sha256s in base32"
    repo = DEMO.parents[1]
    r = subprocess.run([sys.executable, "tools/check_disclosure.py", SNAPSHOT.relative_to(repo).as_posix()], cwd=repo, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# -- the loop --------------------------------------------------------------------------------------------------------------

FIG = "Total retail revenue for June 2025"
CROSS = "Combined Retail and SME revenue for June 2025"
SIGN_IN = "sign in: POST /session with a token, or send Authorization: Bearer <token>"


def ran(agent: Agent, question: str, who: str, arm: str) -> tuple[list[str], dict]:
    """(the loop's nodes in the order they ran, the answer), under a run trace."""
    out = runtrace.run("agent", lambda: {"updates": list(agent.loop.stream(agent.turn(question, who, arm=arm), stream_mode="updates"))},
                       graph=agent.g)
    nodes = [next(iter(u)) for u in out["updates"]]
    return nodes, next(v["answer"] for u in out["updates"] for v in u.values() if v and "answer" in v)


def test_the_loop_is_a_state_graph_with_fixed_edges_one_node_per_step(eng):
    """The mode picks the branch at START, rules with no model; after that each step's edge is fixed, and every step runs:
    one that finds the answer given passes it on."""
    from langgraph.graph.state import CompiledStateGraph

    from api.agent import MODEL_STEPS, RULES_STEPS
    assert MODEL_STEPS == ("context", "plan", "check", "run", "answer") and RULES_STEPS == ("rules", "evidence")
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
            assert answer == {k: v for k, v in asked(a, FIG, "analyst_enterprise", arm).items() if k != "run_trace"}, (mode, arm)
    nodes, answer = ran(Agent(eng, llm=llm("careful")), FIG, "pipeline_tagger", "treatment")   # refused at the first step
    assert nodes == list(MODEL_STEPS) and answer["behaviour"] == "refuse" and answer["reason"] == "pipeline_tagger is not a querying identity."


# -- the context, through the context contract ---------------------------------------------------------------------------

def test_each_arm_gets_its_context_through_post_context_as_the_principal(eng, monkeypatch):
    """With no CONTEXT_URL: POST /context in this process, through the API's route and its JSON. The route builds the package
    as the principal its token names, once per answer, on each arm the same way; a field as_json gives reaches the agent only
    as JSON gives it back, a tuple as a list."""
    import api.app as appmod
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    real = appmod.as_json
    monkeypatch.setattr(appmod, "as_json", lambda pkg: {**real(pkg), "probe": ("through", "json")})
    for mode in (None, "careful"):
        agent = Agent(eng, llm=llm(mode))
        assert agent.context_url is None
        built, fetch, served, got = agent.context, agent.fetch, [], []
        monkeypatch.setattr(agent, "context", lambda *a: served.append(a) or built(*a))
        monkeypatch.setattr(agent, "fetch", lambda *a: got.append(fetch(*a)) or got[-1])
        for arm in ARMS:
            served.clear()
            got.clear()
            asked(agent, FIG, "analyst_retail", arm)
            assert served == [(FIG, "analyst_retail", "analysis", arm)], (mode, arm)
            assert len(got) == 1 and got[0]["probe"] == ["through", "json"] and got[0]["arm"] == arm, (mode, arm)


def test_the_token_check_is_the_api_s_in_this_process_too(eng, monkeypatch):
    """No token, or a bad one, is the API's 401, and the agent gives no answer; another principal's token gets that principal's
    package, whoever the agent asks for: the route decides who from the token alone."""
    from api.context import ContextError
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    agent = Agent(eng, llm=llm("careful"))
    for bad in ("nope", None):
        monkeypatch.setattr(agent, "token", lambda who, _t=bad: _t)
        with pytest.raises(ContextError, match=f"in this process refused POST /context as analyst_retail: 401 {SIGN_IN}"):
            agent.ask(FIG, "analyst_retail", arm="treatment")
    monkeypatch.setattr(agent, "token", lambda who: Agent.token(agent, "analyst_enterprise"))
    assert agent.fetch(CROSS, "analyst_retail", "analysis", "treatment")["units"] == ["retail", "sme", "corporate", "wealth"]
    monkeypatch.setattr(agent, "token", lambda who: Agent.token(agent, "analyst_retail"))
    assert agent.fetch(CROSS, "analyst_enterprise", "analysis", "treatment")["units"] == ["retail"]


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
    allowlist; and a plan the graph does not plan so is refused. (Graph off's package holds nothing JSON changes: the first
    test holds its answers.)"""
    from api.context import ContextError, as_json, from_json
    from tests.test_context import canon
    monkeypatch.delenv("CONTEXT_URL", raising=False)
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


# -- CONTEXT_URL: a live context service ------------------------------------------------------------------------------------

def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory, eng):
    """The API on a scratch copy of the build, served by uvicorn in a process of its own on TEST_PORT (else a free port): the
    context service the agent in this process asks at CONTEXT_URL, from the demo's own graph. The demo's tokens are copied
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
    Agent(eng, llm=None).identities   # the demo's tokens, made if there are none yet
    run_of(build).mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_of() / "tokens.json", run_of(build) / "tokens.json")
    port = int(os.environ.get("TEST_PORT") or free_port())
    log = build / "api.log"
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--app-dir", str(DEMO), "--host", "127.0.0.1",
                                 "--port", str(port)], cwd=DEMO, stdout=out, stderr=subprocess.STDOUT,
                                env={**os.environ, "PROFILES": str(registry(build)), "PROFILE": "", "GRAPH_STORE": graph_store(),
                                     "MODEL_PROVIDER": "none", "DEMO_MODE": "", "API_URL": "", "CONTEXT_URL": ""})
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


def test_with_context_url_at_a_live_service_the_answers_are_the_ones_in_this_process(live, eng, monkeypatch):
    """CONTEXT_URL at the live API: the agent posts each arm's /context there, over HTTP, with the principal's token, and
    answers as it does with the context from its own process. On rules every question of the three sets on both arms; on the
    mock careful, and each adversary on the questions the snapshot holds it to. The answers in this process are the first
    test's, where it ran first."""
    frozen = json.loads(SNAPSHOT.read_text())["answers"]
    asks = {key: (q, who, arm) for key, q, who, arm in questions()}
    by_mode: dict[str, list[str]] = {}
    for key in frozen:
        by_mode.setdefault(key.rsplit(":", 1)[1], []).append(key.rsplit(":", 1)[0])
    by_mode["careful"] = by_mode["careful"][::4]   # a spread of careful's: every question is held on rules
    posted = 0
    for mode, keys in by_mode.items():
        monkeypatch.delenv("CONTEXT_URL", raising=False)
        here = Agent(eng, llm=llm(None if mode == "rules" else mode))
        monkeypatch.setenv("CONTEXT_URL", live.url + "/")
        there = Agent(eng, llm=llm(None if mode == "rules" else mode))
        assert (here.context_url, there.context_url) == (None, live.url)
        for key in keys:
            a = asked(there, *asks[key])
            assert answer_of(a) == (HERE.get(f"{key}:{mode}") or answer_of(asked(here, *asks[key]))), f"{key}:{mode}"
            posted += any(t["step"] == "context" and "sources" in t for t in a["trace"])
    assert posted > 0 and live.log.read_text().count('"POST /context HTTP/1.1" 200') == posted, "each package from the live API"


def test_an_agent_whose_context_service_does_not_answer_gives_no_answer(tmp_path):
    """make agent with CONTEXT_URL where nothing serves: it says so, names the address, and exits 2."""
    from tests.scratch import scratch_build, script
    build = scratch_build(tmp_path / "build")
    r = script("api/agent.py", "--arm", "treatment", FIG, build=build, env={"CONTEXT_URL": "http://127.0.0.1:0", "MODEL_PROVIDER": "none"})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "agent: the context service at http://127.0.0.1:0 did not answer (ConnectError): serve it, or unset CONTEXT_URL" in r.stderr


def test_the_env_example_names_context_url_and_leaves_it_empty():
    from pipeline.envfile import parse
    values = parse((DEMO / ".env.example").read_text())
    assert values["CONTEXT_URL"] == "", "copied as it is, .env leaves the agent's context in its own process"


if __name__ == "__main__":
    if sys.argv[1:] != ["--snapshot"]:
        raise SystemExit("python3 -m tests.test_agent_loop --snapshot: write tests/fixtures/agent_f75605a.json from the code checked out")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    snapshot()
