"""The approved Agent query-plan/v1 answer and trace seal on LangGraph.

The historical and step-seven seals are immutable. The readable-vocabulary seal
links its metadata changes to them. The #402 manifest records the current release seals,
including owner-approved answer moves and retirements, while every model reply remains identical.
Default context serialization remains held by test_context.py.
HTTP people credentials are compared over ASGI as well as the manager's live
socket test. Opaque history fingerprints are normalized only for comparing
people and their sole effective role; authorization uses the actual principal.
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

HISTORICAL = DEMO / "tests" / "fixtures" / "agent_f75605a.json"
PRE_READABLE_VOCABULARY = DEMO / "tests" / "fixtures" / "agent_query_plan_v1.json"
SNAPSHOT = DEMO / "tests" / "fixtures" / "agent_query_plan_readable_vocabulary.json"
SETS = ("golden", "heldout", "golden_wealth_v1")
ARMS = ("control", "treatment")
SPREAD = 8
REASON_DIAGNOSTIC = 'golden:G024:control:careful'
REASON_396 = {'golden:G003:control:careful', 'golden:G009:control:careful', 'heldout:H003:control:careful'}
HERE: dict[str, dict] = {}


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


VOLATILE = ("run_id", "started_at", "total_ms", "graph")


def record(answer: dict) -> dict:
    """The answer and its run trace, less what a clock or a run id puts in the trace (and the graph's fingerprint, which is
    the build's, not the agent's): each stage by name, with its model calls and tokens; each model call less its time.
    Graph path display metadata is checked separately from these frozen answer seals."""
    rt = {k: v for k, v in answer["run_trace"].items() if k not in VOLATILE}
    rt["stages"] = [{k: v for k, v in s.items() if k != "ms"} for s in rt["stages"]]
    rt["llm_calls"] = [{k: v for k, v in c.items() if k != "ms"} for c in rt["llm_calls"]]
    return {"answer": {k: v for k, v in answer.items() if k not in ("run_trace", "graph_path")}, "run_trace": rt}


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
    return sha("query-plan-v1\n" + json.dumps(normalise(held), sort_keys=True, default=str))


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


def preserved_answer_sha(answer, diagnostic=False):
    held = json.loads(json.dumps(record(answer)['answer'], default=str))
    for step in held.get('trace', []):
        if step.get('step') == 'context' and 'hash' in step:
            step['hash'] = '(readable-vocabulary-hash)'
    if diagnostic:
        hashes = re.findall(r'[0-9a-f]{64}', held['reason'])
        assert len(hashes) == 2
        held['reason'] = held['reason'].replace(hashes[0], '(vocabulary-fingerprint)', 1)
    return sha(json.dumps(held, sort_keys=True, default=str))


def snapshot() -> dict:
    """Write only the owner-approved new contract seal; the historical seal is immutable."""
    eng = open_engine()
    asks = {key: (q, who, arm) for key, q, who, arm in questions()}
    agents, answers, records = {}, {}, {}
    try:
        keys = set(json.loads(HISTORICAL.read_text())['answers'])
        keys.update(key + ':' + mode for key in asks for mode in ('rules', 'careful'))
        for key in sorted(keys):
            base, mode = key.rsplit(':', 1)
            if mode not in agents:
                agents[mode] = Agent(eng, llm=llm(None if mode == 'rules' else mode))
            agent = agents[mode]
            answer = asked(agent, *asks[base])
            answers[key] = digest(answer)
            records[key] = record(answer)
    finally:
        eng.close()
    SNAPSHOT.write_text(json.dumps({'about': '#316 9f readable-vocabulary correction. '
        'Historical and step-seven seals are preserved; agent_query_plan_readable_vocabulary_manifest.json links the metadata changes. '
        'Digests are SHA256 in base32, domain-separated by query-plan-v1 and a newline.',
        'answers': answers}, indent=1, sort_keys=True) + '\n')
    return records


def test_the_agent_answers_as_it_did_before_the_loop(eng, monkeypatch):
    """Every approved plan-contract answer and trace, without rewriting the historical seal."""
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    frozen = json.loads(SNAPSHOT.read_text())["answers"]
    release = json.loads((SNAPSHOT.parent / 'agent_query_plan_402_vocabulary_manifest.json').read_text())
    changed_release = {row['key']: row for row in release['answers']}
    frozen = {key: changed_release.get(key, {}).get('after_seal', seal) for key, seal in frozen.items()}
    frozen = {key: seal for key, seal in frozen.items() if seal is not None}
    migration = json.loads((SNAPSHOT.parent / 'agent_query_plan_readable_vocabulary_manifest.json').read_text())
    preserved = {row['key']: row for row in migration['answers']}
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
            changed = preserved.get(f'{key}:{mode}')
            if changed and f'{key}:{mode}' not in changed_release:
                assert preserved_answer_sha(a, changed['key'] == REASON_DIAGNOSTIC) == changed['answer_sha']
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


def test_the_agent_migration_manifest_accounts_for_each_key_and_preserves_the_old_seal():
    assert sha(HISTORICAL.read_text()) == 'sitwfzncgqqj726za3sjab7nybnm44awpgdri2v7fdqkuvhw4aqq'
    historical = json.loads(HISTORICAL.read_text())['answers']
    current = json.loads(PRE_READABLE_VOCABULARY.read_text())['answers']
    manifest = [json.loads(line) for line in (SNAPSHOT.parent / 'agent_query_plan_v1_manifest.jsonl').read_text().splitlines()]
    assert len(manifest) == len(current) == 464
    assert len(historical) == 460 and set(historical) <= set(current)
    assert len({r['key'] for r in manifest}) == len(manifest)
    for row in manifest:
        assert row['before_seal'] == historical.get(row['key'])
        assert row['after_seal'] == current[row['key']]
        assert row['decision'] and row['after_step7']['behaviour']


def test_readable_vocabulary_migration_preserves_the_seals_and_recorded_answers():
    from tests.agent_recordings import VOCABULARY_MIGRATION, before_402_recordings, recordings
    before = json.loads(PRE_READABLE_VOCABULARY.read_text())['answers']
    after = json.loads(SNAPSHOT.read_text())['answers']
    migration = json.loads(VOCABULARY_MIGRATION.read_text())
    assert sha(PRE_READABLE_VOCABULARY.read_text()) == migration['baseline_sha']
    assert sha(json.dumps(before_402_recordings(), indent=1, sort_keys=True) + '\n') == migration['recordings_sha']
    assert set(before) == set(after) and len(before) == 464
    assert {row['key'] for row in migration['answers']} == {key for key in before if before[key] != after[key]}
    permitted = re.compile(
        r'^/(?:answer/trace/\d+/hash|answer/history/0/(?:binding/release_identity|turn_id|plan/obligations/\d+/spans/\d+/turn)|'
        r'run_trace/llm_calls/\d+/(?:context_hash|prompt_hash))$'
    )
    for row in migration['answers']:
        assert row['before_seal'] == before[row['key']] and row['after_seal'] == after[row['key']]
        assert row['answer_sha'] and row['decision']
        assert all(permitted.fullmatch(path) or (row['key'] in {REASON_DIAGNOSTIC, *REASON_396} and path == '/answer/reason')
                   for path in row['paths'])
    replay = recordings()
    for row in migration['recordings']:
        assert replay[row['after']] == replay[row['before']]
    public = SNAPSHOT.parent / 'query-plan-v1'
    original = json.loads((public / 'public-ask.json').read_text())
    updated = json.loads((public / 'public-ask-readable-vocabulary.json').read_text())
    assert [row['reply'] for row in original] == [row['reply'] for row in updated]


def test_402_vocabulary_migration_preserves_the_seals_and_recorded_answers(eng):
    """Account for every narrowed-vocabulary key and moved seal without editing a recorded reply."""
    from kge.services.ask.engine import planmodel, planrules
    from kge.services.ask.engine.core import parse
    from kge.services.ask.engine.scope import ProductScope
    from tests.agent_recordings import PATH, VOCABULARY_402_MIGRATION, before_402_recordings, key_of, recordings
    from tests.plan_recordings import recorded

    migration = json.loads(VOCABULARY_402_MIGRATION.read_text())
    assert sha(SNAPSHOT.read_text()) == migration['baseline_sha'] == 'xpf4v7kuj4jwrosezgj7qwhrgi4djhkf5ejbntvf6vgxqjg2fj5a'
    assert sha(PATH.read_text()) == migration['recordings_sha']
    old = before_402_recordings()
    assert sha(json.dumps(old, indent=1, sort_keys=True) + '\n') == migration['prior_recordings_sha'] == (
        'hxojzbmmdjj4riq55sfhgi5zvt6wkqb2uhxwklvwxyd52d3kanaa')
    current = json.loads(PATH.read_text())
    assert len(old) == len(current) == len({row['case'] for row in current}) == len({row['prompt_hash'] for row in current}) == 184
    moved = {row['case']: row for row in migration['recordings']}
    assert len(moved) == len(migration['recordings'])
    assert set(moved) == {a['case'] for a, b in zip(old, current) if a['prompt_hash'] != b['prompt_hash']}
    replay = recordings()
    asks = {key: (q, who, arm) for key, q, who, arm in questions()}
    retired = {row['case'] for row in current} - set(asks)
    removed = ('W08', 'XB3', 'XB3-W')
    assert retired == {f'golden_wealth_v1:{item}:{arm}' for item in removed for arm in ARMS}
    assert json.loads((BUILD / 'golden_wealth_v1.json').read_text())['changes_from']['removed'] == list(removed)
    assert {row['case'] for row in current} == set(asks) | retired
    assert [row['case'] for row in old] == [row['case'] for row in current]
    assert re.sub(r'(?m)^  "prompt_hash": "[a-z2-7]+",$', '', PATH.read_text()).encode() == re.sub(
        r'(?m)^  "prompt_hash": "[a-z2-7]+",$', '', json.dumps(old, indent=1, sort_keys=True) + '\n').encode()
    agent = Agent(eng, llm=recorded({}))
    for a, b in zip(old, current):
        assert a['reply'] == b['reply']
        assert replay[b['prompt_hash']] == a['reply']
        assert json.dumps(a['reply'], indent=1, sort_keys=True).encode() == json.dumps(b['reply'], indent=1, sort_keys=True).encode()
        assert {k: v for k, v in a.items() if k != 'prompt_hash'} == {k: v for k, v in b.items() if k != 'prompt_hash'}
        if b['case'] in moved:
            row = moved[b['case']]
            assert row['before'] == a['prompt_hash'] and row['after'] == b['prompt_hash']
            assert replay[row['before']] == replay[row['after']] == a['reply']
            assert row['reply_sha'] == sha(json.dumps(a['reply'], sort_keys=True))
            assert '#402-6/7' in row['decision']
        retired_principal = 'advisor_wealth' if b['case'].split(':')[1] == 'XB3-W' else eng.ASKER
        question, who, arm = asks.get(b['case'], (b['question'], retired_principal, b['arm']))
        assert (b['question'], b['arm']) == (question, arm)
        cfg = agent.config(arm)
        request = parse(question, eng.term_names if cfg.glossary else [], eng, aliases=cfg.resolution not in ('none', 'words'))
        scope = ProductScope(eng, None)
        vocab = (planrules.vocabulary(eng, who, 'analysis', scope, cfg, request) if cfg.graph else
                 planrules.control_vocabulary(eng, who, 'analysis', scope, request)[0])
        assert b['prompt_hash'] == key_of(planmodel.payload(question, vocab)['recording_key'])
    frozen = json.loads(SNAPSHOT.read_text())['answers']
    assert len({row['key'] for row in migration['answers']}) == len(migration['answers'])
    observed, agents = {}, {}
    for full in frozen:
        key, mode = full.rsplit(':', 1)
        if key in retired:
            observed[full] = None, None
            continue
        if mode not in agents:
            agents[mode] = Agent(eng, llm=llm(None if mode == 'rules' else mode))
        agent = agents[mode]
        answer = asked(agent, *asks[key])
        question, who, arm = asks[key]
        block = agent.context(question, who, 'analysis', arm)['block']
        seal = digest(answer, block)
        if seal != frozen[full]:
            observed[full] = seal, sha(json.dumps(answer_of(answer), sort_keys=True, default=str))
    assert {row['key'] for row in migration['answers']} == set(observed)
    for row in migration['answers']:
        assert row['before_seal'] == frozen[row['key']] and row['before_seal'] != row['after_seal']
        assert (row['after_seal'], row['answer_sha']) == observed[row['key']]
        if row['key'].rsplit(':', 1)[0] in retired:
            assert row['paths'] == ['/retired'] and '#402 restraint requirement 2' in row['decision']
        else:
            assert re.fullmatch(r'[a-z2-7]{52}', row['after_seal'])
            assert row['paths'] and row['answer_sha']
        assert re.search(r'#(?:398|402)(?:-\d+| restraint requirement \d+)', row['decision'])




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
        monkeypatch.setattr(agent, "context", lambda *a, **kw: served.append(a) or built(*a, **kw))
        monkeypatch.setattr(agent, "fetch", lambda *a, **kw: got.append(fetch(*a, **kw)) or got[-1])
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
    Agent(eng, llm=None).identities
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
    release = json.loads((SNAPSHOT.parent / 'agent_query_plan_402_vocabulary_manifest.json').read_text())
    changed_release = {row['key']: row for row in release['answers']}
    frozen = {key: changed_release.get(key, {}).get('after_seal', seal) for key, seal in frozen.items()}
    frozen = {key: seal for key, seal in frozen.items() if seal is not None}
    asks = {key: (q, f"{who}.meridian@example.com", arm) for key, q, who, arm in questions()}
    by_mode: dict[str, list[str]] = {}
    for key in frozen:
        by_mode.setdefault(key.rsplit(":", 1)[1], []).append(key.rsplit(":", 1)[0])
    by_mode["careful"] = by_mode["careful"][::4]
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
