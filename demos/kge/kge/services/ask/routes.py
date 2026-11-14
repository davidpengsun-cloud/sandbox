"""The ask service's routes: /answer and /ask, the reference agent (/agent), its context package (/context),
/retrieve and a term's map (/map/{term})."""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from kge.ports import StateConflict, StateUnavailable, StoreError
from kge.pipeline import questionrun
from kge.services.ask import service
from kge.services.ask.models import (AgentAsk, AgentResponse, AnswerResponse, Ask, AskResponse, ContextAsk, ContextResponse, Problems,
                                     QuestionRunResponse, Retrieve, RetrieveResponse, RuntimeInfo, RuntimesResponse, Stages, TermMap)
from kge.services.http import needs, served, tool_limits

__all__ = ["AgentAsk", "AgentResponse", "AnswerResponse", "Ask", "AskResponse", "ContextResponse", "Retrieve", "RetrieveResponse",
           "RuntimeInfo", "RuntimesResponse", "Stages", "TermMap", "router"]

router = APIRouter()
tool_limits("answer", ask=Ask)
tool_limits("context", get_context=ContextAsk, retrieve=Retrieve)
BAD_CONFIG = {400: {"model": Problems, "description": "A run config or an arm that cannot run here"}}
SERVICE_FUNCTIONS = {"ask": service.ask, "list_runtimes": service.list_runtimes}


def run_config(a: Ask, path: str) -> service.RunConfig | JSONResponse:
    """The config a request runs on (service.run_config), or a 400 listing the problems if it cannot run here."""
    cfg = service.run_config(a, path, lambda: served.combined())
    return JSONResponse(cfg, status_code=400) if isinstance(cfg, dict) else cfg


def _wants_stream(request: Request) -> bool:
    return "application/x-ndjson" in (request.headers.get("accept") or "").lower()


@router.post("/answer", tags=["internal: platform"], response_model=AnswerResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def answer(a: Ask, request: Request, who: str = Depends(needs("querying")), display: bool = Query(False)):
    """Internal: the rule-based answer on one of the control's presets, for the A/B harness and the control. Callers
    use /ask."""
    cfg = run_config(a, "answer")
    if isinstance(cfg, JSONResponse):
        return cfg
    eng = served.engine()
    prof = served.PROFILE
    if _wants_stream(request):
        return _stream_response(
            request,
            a.question,
            lambda on_stage: service.shown(
                service.answer(eng, prof, a.question, who, a.purpose, a.arm, cfg, products=a.products, history=a.history, runtimes=a.runtimes, on_stage=on_stage),
                display,
                engine,
            ),
        )
    return service.shown(service.answer(eng, prof, a.question, who, a.purpose, a.arm, cfg, products=a.products, history=a.history, runtimes=a.runtimes), display, engine)


def engine():
    """The served engine, read when a display block is built."""
    return served.engine()


@router.post("/ask", tags=["external: query"], response_model=AskResponse, response_model_exclude_unset=True, responses={**BAD_CONFIG, 202: {"model": QuestionRunResponse, "description": "A background question run"}})
def ask(a: Ask, request: Request, who: str = Depends(needs("querying")), display: bool = Query(False), background: bool = Query(False)):
    """The graph arm, answered whole: figures from the warehouse with the documents that back them.

    A question with one customer group, optional narrowing and measures over it
    is planned, gated and cited here (kge/services/ask/engine/combined.py); any other question gets
    exactly what /answer returns, with combined: false. With no stages override
    the model takes part wherever it can if one is configured, as before. Every answer carries answer_text, worded
    by the template, or by the model where it phrases a combined answer and its wording passes the checks.
    Set background=true in the query string to return a durable run id (202) and follow /ask/runs/{id}.
    Set compact=true to omit SQL, traces, plan internals and duplicate candidates, keeping answer content,
    runtime labels and the history needed for follow-up turns. Omitted or false leaves the full response unchanged."""
    cfg = run_config(a, "ask")
    if isinstance(cfg, JSONResponse):
        return cfg
    comb = served.combined()
    prof = served.PROFILE
    if background:
        wait = questionrun.start_wait()
        try:
            if _wants_stream(request):
                out, live, finished = service.live_run(
                    lambda on_start, on_event, stop: service.start_run(comb, prof, a, who, cfg, presentation=display, wait=wait,
                                                                       on_start=on_start, on_event=on_event, stop=stop),
                    engine, lambda: request.is_disconnected(), starting=True)
                if live is not None:
                    return LiveResponse(live, finished)
            else:
                out = service.start_run(comb, prof, a, who, cfg, presentation=display, wait=wait)
        except (StateConflict, StateUnavailable, StoreError):
            raise HTTPException(503, service.STATE_UNAVAILABLE) from None
        return _run_response(request, out, starting=True)
    if _wants_stream(request):
        return _stream_response(
            request,
            a.question,
            lambda on_stage: service.shown(
                service.ask(comb, prof, a.question, who, a.purpose, a.arm, cfg, products=a.products, history=a.history, compact=a.compact, runtimes=a.runtimes, on_stage=on_stage),
                display and not a.compact,
                engine,
            ),
            compact=a.compact,
        )
    return service.shown(service.ask(comb, prof, a.question, who, a.purpose, a.arm, cfg, products=a.products, history=a.history, compact=a.compact, runtimes=a.runtimes), display and not a.compact, engine)


class LiveResponse(StreamingResponse):
    """A question run's live NDJSON reply. The request is let go, with its engine pin and its person's slot, only once the
    work behind the reply has ended, even where the reader left first."""

    def __init__(self, lines, finished):
        super().__init__(lines, media_type="application/x-ndjson", status_code=202,
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        self.finished = finished

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.finished.wait)


def _run_response(request, out, *, starting=False):
    out, status = service.run_reply(out, engine, starting=starting)
    if _wants_stream(request):
        return StreamingResponse(service.ndjson(service.run_events(out)), media_type="application/x-ndjson", status_code=status,
                                 headers={"Cache-Control": "no-store"})
    return JSONResponse(out, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/ask/runs/{run_id}", tags=["external: query"], response_model=QuestionRunResponse, response_model_exclude_unset=True,
            responses={202: {"model": QuestionRunResponse, "description": "The background question is still running"}})
def question_run(run_id: str, request: Request, who: str = Depends(needs("querying")), after: int = Query(0, ge=0)):
    """Resume the signed-in person's question by id, as JSON or reconnectable live events."""
    try:
        if _wants_stream(request):
            comb = served.combined()
            out, live, finished = service.live_run(
                lambda on_start, on_event, stop: service.get_run(comb, served.PROFILE, run_id, who, on_start=on_start,
                                                                 on_event=on_event, stop=stop),
                engine, lambda: request.is_disconnected(), after=after)
            if live is not None:
                return LiveResponse(live, finished)
        else:
            out = service.get_run(served.combined(), served.PROFILE, run_id, who)
    except (StateConflict, StateUnavailable, StoreError):
        raise HTTPException(503, service.STATE_UNAVAILABLE) from None
    except ValueError:
        out = None
    if out is None:
        raise HTTPException(404, "Question run not found.")
    out["events"] = out["events"][after:]
    return _run_response(request, out)


@router.get("/runtimes", tags=["external: query"], response_model=RuntimesResponse, response_model_exclude_unset=True)
def list_runtimes(who: str = Depends(needs("querying"))):
    """The runtimes the caller may use, and which reference tables each holds."""
    return service.list_runtimes(served.engine(), who)


@router.post("/agent", tags=["internal: platform"], response_model=AgentResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def ask_agent(a: AgentAsk, request: Request, who: str = Depends(needs("querying"))):
    """Internal: the reference agent on one arm: control (graph off) or treatment (graph on). The same model,
    prompts and seed in both; only the context differs. With no model configured it runs on rules,
    and its answer and run trace say so (agent.mode)."""
    if (bad := bad_arm(a.arm)) is not None:
        return bad
    if _wants_stream(request):
        return agent_stream(a, request, who=who)
    return service.agent(served.agent(), served.PROFILE, a, who)


def _stream_response(request: Request, question: str, compute_fn, *, compact: bool = False) -> StreamingResponse:
    return StreamingResponse(
        service.stream(question, compute_fn, request.is_disconnected, compact=compact, refused=refusal),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def refusal(exc: Exception) -> dict | None:
    """An HTTP refusal raised while a stream runs, as its error event; None for any other error."""
    if isinstance(exc, HTTPException):
        return {"event": "error", "error": exc.detail, "status": exc.status_code}
    return None


def agent_stream(a: AgentAsk, request: Request, *, who: str):
    if (bad := bad_arm(a.arm)) is not None:
        return bad
    ag = served.agent()
    prof = served.PROFILE
    return _stream_response(request, a.question, lambda on_stage: service.agent(ag, prof, a, who, on_stage=on_stage))


def bad_arm(arm: str) -> JSONResponse | None:
    """A 400 for an arm the reference agent does not run on; None for one it does."""
    bad = service.agent_arm_problems(arm)
    return None if bad is None else JSONResponse(bad, status_code=400)


@router.post("/context", tags=["external: knowledge"], response_model=ContextResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def context_package(a: ContextAsk, who: str = Depends(needs("querying"))):
    """The context package the reference agent's prompt carries on one arm, control (graph off) or treatment (graph on),
    built by the context workflow (kge/services/ask/engine/context.py) as the caller, exactly as /agent builds it, as JSON (as_json). /agent's
    sign-in rules and errors: a bad arm is a 400. Set compact=true for the structured context, sources, definitions
    and decisions, with only definitions in the prompt block and no SQL, traces, duplicate candidates or plan internals.
    Omitted or false is unchanged."""
    if (bad := bad_arm(a.arm)) is not None:
        return bad
    return service.response_view(served.as_json(service.context(served.agent(), a, who)), a.compact)


@router.post("/retrieve", tags=["external: knowledge"], response_model=RetrieveResponse, response_model_exclude_unset=True)
def retrieve(r: Retrieve, who: str = Depends(needs("querying"))):
    """Vector search over the chunks the caller may read for a purpose; tiers: only AI-ready documents at these tiers.
    A bad shelf or tier is a 400."""
    try:
        return service.retrieve(served.engine(), r, who)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/map/{term:path}", tags=["external: knowledge"], response_model=TermMap, response_model_exclude_unset=True)
def resolved_map(term: str, who: str = Depends(needs("querying"))):
    """A term's map: its certified columns, the documents that define it, and, for a term certified through its contexts
    (Term -RESOLVES_TO-> Context), each context as the graph declares it; then the no-join facts and certified joins among
    the units these are in, or the context's joins reach; and the industry standards it is mapped to."""
    return service.term_map(served.engine(), term, who)
