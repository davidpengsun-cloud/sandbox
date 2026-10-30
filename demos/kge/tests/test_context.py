"""The context service (#48 item 1): the reference agent's context package, built on LangGraph, served as POST /context
and as an MCP server at /mcp.

    .venv/bin/python -m pytest tests/test_context.py -q

The package is what Agent.context(question, principal, purpose, arm) builds for the two arms of the A/B test, control
(graph off) and treatment (graph on). The context workflow (api/context.py) builds it as a LangGraph StateGraph with
fixed edges, one node per step of that build, each calling the agent's code for it, and no model call. Its package is
the one built before it: tests/fixtures/context_f75605a.json holds a digest of each package develop built at f75605a,
for every golden, held-out and golden_wealth_v1 question on both arms, asked as the question's principal (else the
deployment's asker) and as analyst_retail, with the run-trace stages it entered and the chunks it read
(python3 -m tests.test_context --snapshot wrote it, on that commit), and the first test builds each one again.

POST /context returns the package as JSON (api/context.py, as_json) under the caller's own identity, as /agent does,
with /agent's errors. /mcp is an MCP server, the official mcp SDK over streamable HTTP mounted in the app, with three
tools, each its endpoint: get_context (POST /context), retrieve (POST /retrieve) and map_term (GET /map/{term}), each run
as the principal whose bearer token the MCP request carries. The last tests serve the API on a scratch copy of the build,
on TEST_PORT (else a free port), and call it with the SDK's own client.

Opens the demo's graph, and scratch copies of it; stop the API first.
"""
from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import runtrace
from api.agent import Agent
from tests.scratch import BUILD, DEMO, open_engine, profile_of, registry, run_of, scratch_build

SNAPSHOT = DEMO / "tests" / "fixtures" / "context_f75605a.json"
SETS = ("golden", "heldout", "golden_wealth_v1")
ARMS = ("control", "treatment")
CROSS = "Combined Retail and SME revenue for June 2025"
SIGN_IN = "sign in: POST /session with a token, or send Authorization: Bearer <token>"


# -- the package as it was: a canonical form, and its digest -------------------------------------------------------------

SKIP = {"sem", "g", "e", "graph", "engine"}   # what a plan holds of the engine: not the package's


def canon(v):
    """v in a canonical JSON form: a tuple key joined with |, a set sorted, a date as ISO, a plan or a dataclass by its fields."""
    if isinstance(v, dict):
        return {("|".join(map(str, k)) if isinstance(k, tuple) else str(k)): canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [canon(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted((canon(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if dataclasses.is_dataclass(v):
        return {"__type": type(v).__name__, **{f.name: canon(getattr(v, f.name)) for f in dataclasses.fields(v) if f.name not in SKIP}}
    if hasattr(v, "__dict__"):
        return {"__type": type(v).__name__, **{k: canon(x) for k, x in vars(v).items() if k not in SKIP and not k.startswith("_")}}
    return repr(v)


def sha(text: str) -> str:
    """text's sha256 in base32, as the estate snapshot's checksums (estate/check_estate.py): no hex digest, which the
    disclosure guard reads as an account number."""
    return base64.b32encode(hashlib.sha256(text.encode()).digest()).decode().rstrip("=").lower()


def without_graph_model_provenance(value):
    """Normalise only the package block's graph-model identity to the frozen snapshot's fixed token."""
    if isinstance(value, dict):
        return {k: without_graph_model_provenance(v) for k, v in value.items()}
    if isinstance(value, list):
        return [without_graph_model_provenance(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?<=; graph model )[0-9a-f]{12}(?=\.)", "c414dc395140", value)
    return value


def digest(v) -> str:
    return sha(json.dumps(canon(without_graph_model_provenance(v)), sort_keys=True))


def questions():
    """(key, question, principal) for every question of the three sets, as its principal (else the deployment's asker) and
    as analyst_retail, on each arm: key is <set>:<id>:<arm>:<principal>."""
    from eval import sets
    asker = profile_of().asking(None)
    for name in SETS:
        for it in sets.items(BUILD / f"{name}.json"):
            for who in dict.fromkeys([it.get("principal") or asker, "analyst_retail"]):
                for arm in ARMS:
                    yield f"{name}:{it['id']}:{arm}:{who}", it["question"], who, arm


def built(agent: Agent, question: str, who: str, arm: str) -> tuple[dict, dict]:
    """(the package, the record its digest is of: the package, the run-trace stages it entered and the chunks it read)."""
    r = runtrace.run("agent", lambda: {"package": agent.context(question, who, "analysis", arm)}, graph=agent.g)
    trace = r["run_trace"]
    return r["package"], {"package": r["package"], "stages": [s["stage"] for s in trace["stages"]], "retrieved": trace["retrieved"]}


def snapshot() -> None:
    """Write SNAPSHOT from the code checked out, on the demo's build: run once, at f75605a, before the workflow."""
    eng = open_engine()
    try:
        agent = Agent(eng, llm=None)
        packages = {}
        for key, q, who, arm in questions():
            pkg, record = built(agent, q, who, arm)
            packages[key] = {"package": digest(record), "block": sha(pkg["block"])}
    finally:
        eng.close()
    SNAPSHOT.write_text(json.dumps({
        "about": "The reference agent's context package (Agent.context) on rules, for every golden, held-out and golden_wealth_v1 "
                 "question on both arms, asked as the question's principal (else the deployment's asker) and as analyst_retail: "
                 "per <set>:<id>:<arm>:<principal>, a sha256 of the package with the run-trace stages it entered and the chunks it "
                 "read, in tests/test_context.py's canonical form, and one of its block alone, each in base32. Digests only: no "
                 "question, figure or SQL. Frozen before the context workflow (#48 item 1); never edited.",
        "taken_on": "f75605a, develop before #48, on Ladybug", "packages": packages}, indent=1, sort_keys=True) + "\n")


def test_the_package_is_the_one_built_before_the_workflow(eng):
    """Every package the snapshot holds, built again by the context workflow: the same package, block, run-trace stages and
    chunks read, on both arms, for each question and principal. The graph-model provenance identity alone is normalised
    before hashing, so the frozen file still checks every behavioural field. And each is JSON once as_json has given it."""
    from api.context import as_json
    frozen = json.loads(SNAPSHOT.read_text())["packages"]
    agent = Agent(eng, llm=None)
    moved, seen = [], set()
    for key, q, who, arm in questions():
        seen.add(key)
        pkg, record = built(agent, q, who, arm)
        now = {"package": digest(record), "block": sha(without_graph_model_provenance(pkg["block"]))}
        if now != frozen.get(key):
            moved.append(key + (" (its block)" if now["block"] != frozen.get(key, {}).get("block") else ""))
        out = as_json(pkg)
        assert list(out) == list(pkg), key
        assert json.loads(json.dumps(out)) == out, key   # JSON as it is: no tuple, set, date or object in it
    assert moved == []
    assert seen == set(frozen), "the snapshot's questions are the ones asked"


def test_the_snapshot_holds_no_hex_digest_and_passes_the_disclosure_guard():
    text = SNAPSHOT.read_text()
    assert not re.search(r"[0-9a-f]{12,}", text), "no hex digest: the disclosure guard flags them"
    assert all(re.fullmatch(r"[a-z2-7]{52}", d) for p in json.loads(text)["packages"].values() for d in p.values()), "sha256s in base32"
    repo = DEMO.parents[1]
    r = subprocess.run([sys.executable, "tools/check_disclosure.py", SNAPSHOT.relative_to(repo).as_posix()], cwd=repo, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# -- the workflow --------------------------------------------------------------------------------------------------------

def test_the_workflow_is_a_state_graph_with_fixed_edges_one_node_per_step(eng):
    from langgraph.graph.state import CompiledStateGraph

    from api.context import CONTROL, TREATMENT
    agent = Agent(eng, llm=None)
    assert isinstance(agent.workflow, CompiledStateGraph)
    graph = agent.workflow.get_graph()
    assert set(graph.nodes) == {"__start__", "__end__", *CONTROL, *TREATMENT}
    edges = {(e.source, e.target, e.conditional) for e in graph.edges}
    chain = lambda steps: {(a, b, False) for a, b in zip(steps, steps[1:])} | {(steps[-1], "__end__", False)}  # noqa: E731
    assert edges == {("__start__", CONTROL[0], True), ("__start__", TREATMENT[0], True)} | chain(CONTROL) | chain(TREATMENT)
    for arm, steps in (("control", CONTROL), ("treatment", TREATMENT)):
        ran = [next(iter(u)) for u in agent.workflow.stream({"question": CROSS, "principal": "analyst_enterprise", "purpose": "analysis",
                                                             "arm": arm}, stream_mode="updates")]
        assert ran == list(steps), arm


def test_the_workflow_makes_no_model_call(eng):
    class NoModel:
        name, provider = "none:none", "none"

        def json(self, *a, **k):
            raise AssertionError("the context workflow called a model")
    for arm in ARMS:
        with_model = runtrace.run("agent", lambda: {"package": Agent(eng, llm=NoModel()).context(CROSS, "analyst_enterprise", "analysis", arm)},
                                  graph=eng.g)
        assert with_model["run_trace"]["llm_calls"] == []
        assert digest(with_model["package"]) == digest(Agent(eng, llm=None).context(CROSS, "analyst_enterprise", "analysis", arm))


# What LangGraph and mcp pull in that had a release in the two weeks before they were added (to 17 Oct 2026): requirements.txt
# pins each at its last release before them, beside the packages it names.
PULLED_IN = {"langsmith", "ormsgpack", "pydantic-settings", "tenacity", "jsonpatch", "jsonpointer", "truststore", "pycparser", "rpds-py"}


def test_the_service_runs_on_the_versions_requirements_txt_pins():
    from importlib.metadata import version

    from packaging.requirements import Requirement
    pins = [Requirement(line.split("#")[0]) for line in (DEMO / "requirements.txt").read_text().splitlines() if "==" in line.split("#")[0]]
    assert PULLED_IN <= {r.name for r in pins}
    held = {r.name: next(iter(r.specifier)).version for r in pins if r.marker is None or r.marker.evaluate()}
    assert {name: version(name) for name in held} == held, "the venv is not what requirements.txt pins: pip install -r requirements.txt"


# -- POST /context ------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    return scratch_build(tmp_path_factory.mktemp("context") / "build")


@pytest.fixture()
def client(scratch, monkeypatch):
    import api.app as appmod
    from fastapi.testclient import TestClient
    from api.identity import token_for
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(scratch)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(run_of(scratch), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_context_returns_the_package_as_json_as_the_caller(client):
    import api.app as appmod
    from api.context import as_json
    for who in ("analyst_enterprise", "analyst_retail"):
        for arm in ARMS:
            r = client.post("/context", json={"question": CROSS, "arm": arm}, headers=client.as_(who))
            assert r.status_code == 200, r.text
            assert r.json() == json.loads(json.dumps(as_json(appmod.agent().context(CROSS, who, "analysis", arm)))), (who, arm)
    retail = client.post("/context", json={"question": CROSS}, headers=client.as_("analyst_retail")).json()
    every = client.post("/context", json={"question": CROSS}, headers=client.as_("analyst_enterprise")).json()
    assert retail["arm"] == "treatment" and retail["units"] == ["retail"] and every["units"] == ["retail", "sme", "corporate", "wealth"]
    assert set(retail["certified"]) == {"retail"} and {s["unit"] for s in retail["sources"]} <= {"retail", ""}
    assert "You may not read sme" in retail["block"] and "You may not read" not in every["block"]


def test_context_s_sign_in_rules_and_errors_are_agent_s(client):
    body = {"question": CROSS, "arm": "treatment"}
    for path in ("/context", "/agent"):
        assert client.post(path, json=body).status_code == 401, path
        assert client.post(path, json=body).json()["detail"].startswith(SIGN_IN), path
        assert client.post(path, json=body, headers={"authorization": "Bearer nope"}).status_code == 401, path
        r = client.post(path, json=body, headers=client.as_("pipeline_tagger"))
        assert (r.status_code, r.json()["detail"]) == (403, "pipeline_tagger is not a querying identity"), path
        assert client.post(path, json={**body, "principal": "admin"}, headers=client.as_("analyst_retail")).status_code == 422, path
        assert client.post(path, json={"arm": "treatment"}, headers=client.as_("analyst_retail")).status_code == 422, path
    bad = {path: client.post(path, json={**body, "arm": "graph"}, headers=client.as_("analyst_retail")) for path in ("/context", "/agent")}
    assert bad["/context"].status_code == bad["/agent"].status_code == 400
    assert bad["/context"].json() == bad["/agent"].json() == {"error": "arm must be one of control, treatment, not 'graph'",
                                                                "problems": ["arm must be one of control, treatment, not 'graph'"]}


def test_context_signed_in_by_session_is_the_session_s_principal(client, scratch):
    from api.identity import token_for
    assert client.post("/session", json={"token": token_for(run_of(scratch), "analyst_retail")}).status_code == 200
    assert client.post("/context", json={"question": CROSS}).json()["units"] == ["retail"]
    client.delete("/session")
    assert client.post("/context", json={"question": CROSS}).status_code == 401


# -- /mcp, served live, called with the SDK's own client ---------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """The API on a scratch copy of the build, served by uvicorn in a process of its own on TEST_PORT (else a free port), with
    what the in-process agent builds for CROSS, as analyst_retail and as analyst_enterprise on each arm, taken before it starts
    (the graph store lets one process open the graph). Stopped by its pid."""
    from api.context import as_json
    from pipeline.state import graph_store
    build = scratch_build(tmp_path_factory.mktemp("live") / "build")
    eng = open_engine(build)
    try:
        agent = Agent(eng, llm=None)
        expected = {(who, arm): json.loads(json.dumps(as_json(agent.context(CROSS, who, "analysis", arm))))
                    for who in ("analyst_retail", "analyst_enterprise") for arm in ARMS}
    finally:
        eng.close()
    port = int(os.environ.get("TEST_PORT") or free_port())
    log = build / "api.log"
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--app-dir", str(DEMO), "--host", "127.0.0.1",
                                 "--port", str(port)], cwd=DEMO, stdout=out, stderr=subprocess.STDOUT,
                                env={**os.environ, "PROFILES": str(registry(build)), "PROFILE": "", "GRAPH_STORE": graph_store(),
                                     "MODEL_PROVIDER": "none", "DEMO_MODE": "", "API_URL": ""})
    import httpx
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
        from api.identity import token_for
        yield SimpleNamespace(url=url, build=build, expected=expected, token=lambda who: token_for(run_of(build), who))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def mcp_call(url: str, token: str | None, tool: str | None = None, **args):
    """With the SDK's own client over streamable HTTP, as token: (the tools listed, the tool's result), or the exception it raised."""
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run():
        headers = {"authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(headers=headers, timeout=60) as http:
            async with streamable_http_client(f"{url}/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = sorted(t.name for t in (await session.list_tools()).tools)
                    return tools, (await session.call_tool(tool, args) if tool else None)
    try:
        return anyio.run(run)
    except BaseException as exc:  # noqa: BLE001  the refusal, raised through the client's task groups
        return exc


def refused(exc) -> list[int]:
    """The HTTP statuses the client's exception carries, through any exception group."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        return [exc.response.status_code]
    return [s for e in getattr(exc, "exceptions", ()) for s in refused(e)]


def test_a_second_mcp_client_gets_from_get_context_only_what_analyst_retail_may_read(live):
    for arm in ARMS:
        tools, result = mcp_call(live.url, live.token("analyst_retail"), "get_context", question=CROSS, arm=arm)
        assert tools == ["get_context", "map_term", "retrieve"]
        assert not result.isError, result.content
        pkg = result.structuredContent
        assert pkg == live.expected["analyst_retail", arm], arm
        assert pkg != live.expected["analyst_enterprise", arm], arm
        assert pkg["units"] == ["retail"] and set(pkg["certified"]) <= {"retail"}, arm
        assert {s["unit"] for s in pkg["sources"]} <= {"retail", ""}, arm
    _, every = mcp_call(live.url, live.token("analyst_enterprise"), "get_context", question=CROSS)
    assert every.structuredContent == live.expected["analyst_enterprise", "treatment"]


def test_each_mcp_tool_is_its_endpoint_as_the_same_caller(live):
    import httpx
    who = {"authorization": f"Bearer {live.token('analyst_retail')}"}
    ask, search = {"question": CROSS, "arm": "control"}, {"query": "revenue definition", "k": 3}
    pairs = [("get_context", ask, httpx.post(f"{live.url}/context", json=ask, headers=who)),
             ("retrieve", search, httpx.post(f"{live.url}/retrieve", json=search, headers=who)),
             ("map_term", {"term": "Revenue"}, httpx.get(f"{live.url}/map/Revenue", headers=who))]
    for tool, args, rest in pairs:
        assert rest.status_code == 200, (tool, rest.text)
        _, result = mcp_call(live.url, live.token("analyst_retail"), tool, **args)
        assert not result.isError, (tool, result.content)
        assert result.structuredContent == rest.json(), tool


def test_mcp_with_no_token_or_a_bad_one_is_refused_as_the_api_refuses_it(live):
    import httpx
    assert refused(mcp_call(live.url, None)) == [401]
    assert refused(mcp_call(live.url, "nope", "get_context", question=CROSS)) == [401]
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    accept = {"accept": "application/json, text/event-stream"}
    for headers in ({}, {"authorization": "Bearer nope"}):
        mcp = httpx.post(f"{live.url}/mcp", json=init, headers={**accept, **headers})
        api = httpx.post(f"{live.url}/context", json={"question": CROSS}, headers=headers)
        assert (mcp.status_code, mcp.json()) == (api.status_code, api.json()) == (401, api.json()), headers
        assert api.json()["detail"].startswith(SIGN_IN)


def test_an_mcp_tool_refuses_what_its_endpoint_refuses(live):
    _, result = mcp_call(live.url, live.token("pipeline_tagger"), "get_context", question=CROSS)
    assert result.isError and "403: pipeline_tagger is not a querying identity" in result.content[0].text
    _, result = mcp_call(live.url, live.token("analyst_retail"), "get_context", question=CROSS, arm="graph")
    assert result.isError and "400: arm must be one of control, treatment, not 'graph'" in result.content[0].text
    _, result = mcp_call(live.url, live.token("analyst_retail"), "retrieve", query="revenue", tiers=["platinum"])
    assert result.isError and "400: tiers must name one or more of" in result.content[0].text


def test_python3_api_context_py_asks_the_api_serving_the_profile_as_a_principal(live):
    """The README's step: the package of a question as a principal, from the API serving the profile (its address and the
    principal's token from the run state), through /mcp with the SDK's own client, or POST /context."""
    def run(*args):
        return subprocess.run([sys.executable, str(DEMO / "api" / "context.py"), *args, CROSS], capture_output=True, text=True, cwd=DEMO,
                              timeout=120, env={**os.environ, "PROFILES": str(registry(live.build)), "PROFILE": ""})
    block = live.expected["analyst_retail", "treatment"]["block"]
    for via in ("mcp", "api"):
        r = run("--as", "analyst_retail", "--via", via)
        assert r.returncode == 0, r.stderr
        assert "as analyst_retail, treatment: the units it may read, retail;" in r.stdout and block in r.stdout, via
        assert (f"MCP server at {live.url}/mcp: tools get_context, retrieve, map_term" in r.stdout) == (via == "mcp")
    r = run("--as", "pipeline_tagger")
    assert r.returncode == 1 and "refused" in r.stdout and "403: pipeline_tagger is not a querying identity" in r.stdout
    r = run("--as", "analyst_retail", "--arm", "control", "--json")
    assert r.returncode == 0 and json.loads(r.stdout) == live.expected["analyst_retail", "control"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--snapshot"]:
        raise SystemExit("python3 -m tests.test_context --snapshot: write tests/fixtures/context_f75605a.json from the code checked out")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    snapshot()
