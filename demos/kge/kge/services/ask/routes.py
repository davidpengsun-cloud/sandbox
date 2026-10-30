"""The ask service's routes: /answer and /ask, the reference agent (/agent), its context package (/context),
/retrieve and a term's map (/map/{term})."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, RunConfig
from kge.services.ask.engine.present import present
from kge.services.ask import service
from kge.services.ask.models import (AgentAsk, AgentResponse, AnswerResponse, Ask, AskResponse, ContextResponse, Problems,
                                     Retrieve, RetrieveResponse, Stages, TermMap)
from kge.services.http import needs, served, tool_limits

__all__ = ["AgentAsk", "AgentResponse", "AnswerResponse", "Ask", "AskResponse", "ContextResponse", "Retrieve", "RetrieveResponse",
           "Stages", "TermMap", "router"]

router = APIRouter()
tool_limits("answer", ask=Ask)
tool_limits("context", get_context=AgentAsk, retrieve=Retrieve)
BAD_CONFIG = {400: {"model": Problems, "description": "A run config or an arm that cannot run here"}}
SERVICE_FUNCTIONS = {"ask": service.ask}


def has_model() -> bool:
    return served.combined().llm is not None


def run_config(a: Ask, path: str) -> RunConfig | JSONResponse:
    """The config a request runs on: its arm's default for this path, with its stages override.
    A 400 listing the problems if it cannot run here."""
    if a.arm not in PRESETS:
        bad = [f"arm must be one of {', '.join(PRESETS)}"]
        return JSONResponse({"error": bad[0], "problems": bad}, status_code=400)
    base = served.combined().default_config(a.arm) if path == "ask" else PRESETS[a.arm]
    try:
        cfg = base.with_stages(**(a.stages.model_dump(exclude_unset=True) if a.stages else {}))
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "problems": [str(exc)]}, status_code=400)
    bad = cfg.problems(path, has_model())
    if bad:
        return JSONResponse({"error": "; ".join(bad), "problems": bad, "config": cfg.stages()}, status_code=400)
    return cfg


@router.post("/answer", tags=["internal: platform"], response_model=AnswerResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def answer(a: Ask, who: str = Depends(needs("querying")), display: bool = Query(False)):
    """Internal: the rule-based answer on one of the control's presets, for the A/B harness and the control. Callers
    use /ask."""
    cfg = run_config(a, "answer")
    if isinstance(cfg, JSONResponse):
        return cfg
    return shown(service.answer(served.engine(), served.PROFILE, a.question, who, a.purpose, a.arm, cfg), display)


def shown(out: dict, display: bool) -> dict:
    """The rich answer's display block (kge/services/ask/engine/present.py), added only when the page asks for it (?display=1, its Rich answers
    switch, off by default). The A/B harness, eval, margin and parity never ask; the answer is the same either way."""
    if display:
        out["display"] = present(out, served.engine())
    return out


@router.post("/ask", tags=["external: query"], response_model=AskResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def ask(a: Ask, who: str = Depends(needs("querying")), display: bool = Query(False)):
    """The graph arm, answered whole: figures from the warehouse with the documents that back them.

    A question with one customer group, optional narrowing and measures over it
    is planned, gated and cited here (kge/services/ask/engine/combined.py); any other question gets
    exactly what /answer returns, with combined: false. With no stages override
    the model takes part wherever it can if one is configured, as before. Every answer carries answer_text, worded
    by the template, or by the model where it phrases a combined answer and its wording passes the checks."""
    cfg = run_config(a, "ask")
    if isinstance(cfg, JSONResponse):
        return cfg
    return shown(service.ask(served.combined(), served.PROFILE, a.question, who, a.purpose, a.arm, cfg), display)


@router.post("/agent", tags=["internal: platform"], response_model=AgentResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def ask_agent(a: AgentAsk, who: str = Depends(needs("querying"))):
    """Internal: the reference agent on one arm: control (graph off) or treatment (graph on). The same model,
    prompts and seed in both; only the context differs. With no model configured it runs on rules,
    and its answer and run trace say so (agent.mode)."""
    if a.arm not in AGENT_ARMS:
        return bad_arm(a.arm)
    ag = served.agent()
    out = runtrace.run("agent", lambda: ag.ask(a.question, who, a.purpose, arm=a.arm), graph=ag.g)
    service.log_unmatched(served.PROFILE, "/agent", a.arm, a.arm == "treatment", who, a.question, out, ag.e)
    return out


def bad_arm(arm: str) -> JSONResponse:
    bad = f"arm must be one of {', '.join(AGENT_ARMS)}, not {arm!r}"
    return JSONResponse({"error": bad, "problems": [bad]}, status_code=400)


@router.post("/context", tags=["external: knowledge"], response_model=ContextResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def context_package(a: AgentAsk, who: str = Depends(needs("querying"))):
    """The context package the reference agent's prompt carries on one arm, control (graph off) or treatment (graph on),
    built by the context workflow (kge/services/ask/engine/context.py) as the caller, exactly as /agent builds it, as JSON (as_json). /agent's
    sign-in rules and errors: a bad arm is a 400."""
    if a.arm not in AGENT_ARMS:
        return bad_arm(a.arm)
    return served.as_json(served.agent().context(a.question, who, a.purpose, a.arm))


@router.post("/retrieve", tags=["external: knowledge"], response_model=RetrieveResponse, response_model_exclude_unset=True)
def retrieve(r: Retrieve, who: str = Depends(needs("querying"))):
    """Vector search over the chunks the caller may read for a purpose; tiers: only AI-ready documents at these tiers.
    A bad shelf or tier is a 400."""
    try:
        return served.engine().g.retrieve(r.query, who, r.purpose, r.k, r.shelf, r.tiers)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/map/{term:path}", tags=["external: knowledge"], response_model=TermMap, response_model_exclude_unset=True)
def resolved_map(term: str, who: str = Depends(needs("querying"))):
    """A term's map: its certified columns, the documents that define it, and, for a term certified through its contexts
    (Term -RESOLVES_TO-> Context), each context as the graph declares it; then the no-join facts and certified joins among
    the units these are in, or the context's joins reach; and the industry standards it is mapped to."""
    return service.term_map(served.engine(), term, who)
