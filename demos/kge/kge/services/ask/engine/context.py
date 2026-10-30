"""The context workflow: the package the reference agent's prompt carries, built by a LangGraph StateGraph.

    Agent.context(question, principal, purpose, arm)   # kge/services/ask/engine/agent.py: the package, for one arm
    python3 -m kge.services.ask.engine.context [--profile <id>] [--as <principal>] [--arm treatment] [--via mcp] "<question>"

The package is the only thing the A/B test's two arms do not share (kge/services/ask/engine/agent.py): control, graph off, and treatment,
graph on. This builds it as a StateGraph with fixed edges, one node per step of the build, each a method of the agent
holding the code for that step, which takes the workflow's state and returns what it adds: a section of the block, and
what the steps after it read. The arm picks the branch at START, control for control and treatment for any other arm,
as Agent.context always has; after that no node picks the next.

  control    schema (the warehouse schema of the units the principal may read) -> search (plain vector search)
  treatment  scope (the question parsed: the units it may read and needs, a context's query) -> define (the terms)
             -> route (phrases not in the glossary) -> customer_group -> linked (across units, through the certified
             join) -> columns (by role) -> contexts -> joins (declared) -> across (no-join pairs, adding parts)
             -> withhold (do not use) -> tiers (the sources by tier, the references) -> provenance

The last node of each branch gives the package, the same dict, field for field, the build gave before it ran on
LangGraph (tests/test_context.py holds every question of the demo's sets to that, on both arms). The workflow makes no
model call: the model, where there is one, is asked after it, with what it built.

POST /context and the MCP tool get_context (kge/app.py) return the package as JSON, as_json. The reference agent is a
client of the service as a downstream app is (post): on both arms it gets its package by POST /context with the asking
principal's own token, at CONTEXT_URL, or with none set, in its own process, through the same route, token check and JSON
(kge/app.py, serving), and reads the JSON back into the package its code reads (from_json). Run as a script, this
asks the API serving the profile for one, as a principal: through its MCP server, with the mcp SDK's own client (--via mcp,
the default), or POST /context (--via api), with the principal's token from the profile's run state. It prints the tools
the server offers, the units the package lets the principal read, and the block (--json: the package whole).
"""
from __future__ import annotations

import argparse
import json
import operator
import sys
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from kge.pipeline.settings import settings

CONTROL = ("schema", "search")
TREATMENT = ("scope", "define", "route", "customer_group", "linked", "columns", "contexts", "joins", "across", "withhold", "tiers",
             "provenance")


class State(TypedDict, total=False):
    """What the steps pass on: the question asked, then what each step adds for the ones after it. Three fields add up
    across the steps: lines (the block), named (documents it names besides its numbered sources) and withheld (those
    it says not to use); units is graph off's, req (the question as the parser read it), context and plan graph on's."""
    question: str
    principal: str
    purpose: str
    arm: str
    lines: Annotated[list[str], operator.add]
    named: Annotated[list[str], operator.add]
    withheld: Annotated[list[str], operator.add]
    units: list[str]
    req: Any
    readable: list[str]
    terms: list[str]
    impls: dict
    everywhere: dict
    asked: dict
    two: bool
    scope_of: dict
    in_play: list[str]
    needed: list[str]
    context: Any
    plan: Any
    denied: list[str]
    undefined: dict | None
    group: dict | None
    held: dict | None
    certified: dict
    defines: dict
    meta: dict
    rests_on: list[str]
    cited: dict
    sources: list[dict]
    package: dict


def branch(s: State) -> str:
    return "control" if s["arm"] == "control" else "treatment"


def workflow(agent) -> CompiledStateGraph:
    """The context workflow for agent (kge/services/ask/engine/agent.py's Agent): each node the agent's method of its name."""
    g = StateGraph(State)
    for name in CONTROL + TREATMENT:
        g.add_node(name, getattr(agent, name))
    g.add_conditional_edges(START, branch, {"control": CONTROL[0], "treatment": TREATMENT[0]})
    for steps in (CONTROL, TREATMENT):
        for a, b in zip(steps, steps[1:]):
            g.add_edge(a, b)
        g.add_edge(steps[-1], END)
    return g.compile()


def as_json(pkg: dict) -> dict:
    """The package as JSON, as POST /context and the MCP tool get_context return it: each field as the package gives it,
    but certified, each unit's columns as [table, column]; defines and meta, keyed by unit and then table.column; and the
    plan, by its ids (Plan.about), which a plan's query parts in the block spell out."""
    def by_unit(d: dict) -> dict:
        out: dict[str, dict] = {}
        for (unit, column), v in d.items():
            out.setdefault(unit, {})[column] = v
        return out
    out = dict(pkg)
    out["certified"] = {u: [list(c) for c in cols] for u, cols in pkg["certified"].items()}
    out["defines"], out["meta"] = by_unit(pkg["defines"]), by_unit(pkg["meta"])
    if "plan" in pkg:
        out["plan"] = pkg["plan"].about() if pkg["plan"] is not None else None
    return out


class ContextError(RuntimeError):
    """The context service gave no package: it refused the call, did not answer, or gave one this graph does not plan."""


def service_url() -> str | None:
    """CONTEXT_URL, the context service the reference agent asks (the environment, else .env); None, unset or empty: the
    agent's own process."""
    from kge.pipeline.envfile import load_env
    load_env()
    return (settings().CONTEXT_URL or "").strip().rstrip("/") or None


def post(agent, url: str | None, question: str, principal: str, purpose: str, arm: str) -> dict:
    """The package for one arm, as a downstream app gets it: POST /context as principal, with its own bearer token, at url,
    or with none, in agent's process (in_process); read back into the package the agent's code reads (from_json). A
    ContextError if the service refuses the call or does not answer: the agent answers nothing without its context."""
    import httpx
    body = {"question": question, "purpose": purpose, "arm": arm}
    headers = {"authorization": f"Bearer {agent.token(principal)}"}
    try:
        r = httpx.post(f"{url}/context", json=body, headers=headers, timeout=120) if url else in_process(agent, body, headers)
    except httpx.HTTPError as exc:
        raise ContextError(f"the context service at {url} did not answer ({type(exc).__name__}): serve it, or unset CONTEXT_URL") from None
    if r.status_code != 200:
        said = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise ContextError(f"the context service {f'at {url}' if url else 'in this process'} refused POST /context as {principal}: "
                           f"{r.status_code} {said.get('detail') or said.get('error') or r.text[:200]}")
    return from_json(r.json(), agent.e, question)


def in_process(agent, body: dict, headers: dict):
    """POST /context to the API's own app (kge/app.py) in this process, over ASGI: its route, its token check and its JSON,
    serving agent's graph, the agent and its profile's identities (serving), with nothing on the network."""
    import anyio
    import httpx

    import kge.app as service

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), base_url="http://context") as client:
            return await client.post("/context", json=body, headers=headers)
    with service.serving(agent):
        return anyio.run(call)


def from_json(data: dict, engine, question: str) -> dict:
    """The package as the agent's code reads it, from the JSON as_json gave: certified, each unit's columns as (table,
    column); defines and meta, keyed by (unit, table.column); and the plan, graph on, the one this graph plans for the
    question's context (Semantics.plan), as the gate builds every allowlist from the graph, held to the ids the package
    gives (Plan.about). A ContextError for a plan this graph does not plan so."""
    def flat(d: dict) -> dict:
        return {(unit, column): v for unit, cols in d.items() for column, v in cols.items()}
    pkg = dict(data)
    pkg["certified"] = {u: [tuple(c) for c in cols] for u, cols in data["certified"].items()}
    pkg["defines"], pkg["meta"] = flat(data["defines"]), flat(data["meta"])
    if data.get("plan") is not None:
        from kge.services.ask.engine.core import parse
        from kge.semantics.query import Asked
        context = engine.semantics.contexts.get(data["plan"]["context"])
        plan = engine.semantics.plan(context, Asked.of(parse(question, engine.term_names, engine))) if context is not None else None
        if plan is None or plan.decision or plan.about() != data["plan"]:
            raise ContextError(f"the package's plan, of {data['plan']['context']}, is not one this graph plans for the question")
        pkg["plan"] = plan
    return pkg


def fetch(url: str, token: str, question: str, arm: str, via: str) -> tuple[list[str], dict | str]:
    """(the tools the MCP server offers, none through POST /context; the package, or why there is none) from the API at url."""
    import anyio
    import httpx
    headers = {"Authorization": f"Bearer {token}"}
    body = {"question": question, "arm": arm}
    if via == "api":
        r = httpx.post(f"{url}/context", json=body, headers=headers, timeout=120)
        return [], r.json() if r.status_code == 200 else f"{r.status_code}: {r.json().get('detail') or r.json().get('error')}"
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def ask() -> tuple[list[str], dict | str]:
        async with httpx.AsyncClient(headers=headers, timeout=120) as http:
            async with streamable_http_client(f"{url}/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = [t.name for t in (await session.list_tools()).tools]
                    result = await session.call_tool("get_context", body)
                    return tools, result.content[0].text if result.isError else result.structuredContent
    return anyio.run(ask)


def main() -> int:
    """Ask the API serving the profile for one package; an API that does not answer at all stopped without saying so,
    and is told to start (make serve)."""
    from kge.services.identity import token_for
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--profile", help="the profile whose API to ask (default: PROFILE, else the registry's default)")
    ap.add_argument("--as", dest="principal", help="whose token to call with (default: the one the profile's deployment names, "
                                                   "asker in its identities)")
    ap.add_argument("--arm", default="treatment", help="control (graph off) or treatment (graph on)")
    ap.add_argument("--via", choices=("mcp", "api"), default="mcp", help="the MCP server's get_context (default) or POST /context")
    ap.add_argument("--json", action="store_true", help="print the package whole")
    args = ap.parse_args()
    try:
        prof = resolve(args.profile)
        who = prof.asking(args.principal)
    except ProfileError as exc:
        print(f"context: {exc}", file=sys.stderr)
        return 2
    serving = prof.run_dir() / "api.json"
    url = json.loads(serving.read_text()).get("url") if serving.is_file() else None
    if not url:
        print(f"context: no API is serving profile {prof.id}: make serve first", file=sys.stderr)
        return 2
    try:
        token = token_for(prof.run_dir(), who)
    except KeyError:
        print(f"context: no token for {who} in profile {prof.id}'s run state: make tokens lists them", file=sys.stderr)
        return 2
    try:
        tools, pkg = fetch(url, token, args.question, args.arm, args.via)
    except Exception as exc:  # noqa: BLE001
        print(f"context: the API at {url} did not answer ({type(exc).__name__}): make serve first", file=sys.stderr)
        return 2
    if tools and not args.json:
        print(f"MCP server at {url}/mcp: tools {', '.join(tools)}")
    if isinstance(pkg, str):
        print(f"get_context as {who}: refused: {pkg}" if args.via == "mcp" else f"POST /context as {who}: refused: {pkg}")
        return 1
    if args.json:
        print(json.dumps(pkg, indent=2))
        return 0
    print(("get_context" if args.via == "mcp" else "POST /context") + f" as {who}, {pkg['arm']}: the units it may read, "
          f"{', '.join(pkg['units']) or 'none'}; {len(pkg['sources'])} sources\n\n{pkg['block']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
