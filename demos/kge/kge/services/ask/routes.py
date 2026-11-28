"""The ask service's routes: /answer and /ask, the reference agent (/agent), its context package (/context),
/retrieve and a term's map (/map/{term})."""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from kge.adapters.kgetools import ToolRefused, kge_tools
from kge.ports import StateConflict, StateUnavailable, StoreError
from kge.pipeline import questionrun
from kge.services.ask import service
from kge.services.ask.models import (AgentAsk, AgentResponse, AnswerResponse, Ask, AskResponse, ContextAsk, ContextResponse,
                                     Feedback, FeedbackAccepted, Problems, QuestionRunResponse, Retrieve, RetrieveResponse,
                                     RuntimeInfo, RuntimesResponse, Stages, TermMap)
from kge.services.http import engineer_view, gate, in_process, matrix, may, person_bearer, principal, question_run_usage, served, tool_limits

__all__ = ["AgentAsk", "AgentResponse", "AnswerResponse", "Ask", "AskResponse", "ContextResponse", "Feedback",
           "FeedbackAccepted", "Retrieve", "RetrieveResponse", "RuntimeInfo", "RuntimesResponse", "Stages", "TermMap", "router"]

router = APIRouter()
tool_limits("answer", ask=Ask, feedback=Feedback)
tool_limits("context", get_context=ContextAsk, retrieve=Retrieve)
BAD_CONFIG = {400: {"model": Problems, "description": "A run config or an arm that cannot run here"}}
SERVICE_FUNCTIONS = {"ask": service.ask, "list_runtimes": service.list_runtimes, "feedback": service.feedback,
                     "question_run": service.get_run, "stop_question_run": service.stop_run}
FEEDBACK_UNAVAILABLE = "The feedback log is unavailable; try again shortly."
IN_PROCESS_URL = "http://kge"
AGENT_THREADS = anyio.CapacityLimiter(40)


def run_config(a: Ask, path: str) -> service.RunConfig | JSONResponse:
    """The config a request runs on (service.run_config), or a 400 listing the problems if it cannot run here."""
    cfg = service.run_config(a, path, lambda: served.combined())
    return JSONResponse(cfg, status_code=400) if isinstance(cfg, dict) else cfg


def _wants_stream(request: Request) -> bool:
    return "application/x-ndjson" in (request.headers.get("accept") or "").lower()


@router.post("/answer", tags=["internal: platform"], response_model=AnswerResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def answer(a: Ask, request: Request, who: str = Depends(matrix("ask")), display: bool = Query(False)):
    """Internal: the rule-based answer on one of the control's presets, for the A/B harness and the control. Callers
    use /ask. Plan and trace are the engineer's (the role matrix's row 7): anyone else's answer is compact, as JSON
    and streamed, as /ask's is, and keeps the answered query's SQL and sql_certified."""
    cfg = run_config(a, "answer")
    if isinstance(cfg, JSONResponse):
        return cfg
    eng = served.engine()
    prof = served.PROFILE
    compact = as_compact_unless_engineer(a, who).compact

    def answered(on_stage=None) -> dict:
        out = service.answer(eng, prof, a.question, who, a.purpose, a.arm, cfg, products=a.products,
                             history=a.history, runtimes=a.runtimes, on_stage=on_stage)
        return service.shown(service.response_view(out, True) if compact else out, display and not compact, engine)

    if _wants_stream(request):
        return _stream_response(request, a.question, answered, compact=compact)
    return answered()


def as_compact_unless_engineer(a: Ask, who: str) -> Ask:
    """The request as it runs: compact for anyone the role matrix does not give plan and trace (row 7)."""
    return a if a.compact or engineer_view(who) else a.model_copy(update={"compact": True})


def engine():
    """The served engine, read when a display block is built."""
    return served.engine()


@router.post("/ask", tags=["external: query"], response_model=AskResponse, response_model_exclude_unset=True, responses={**BAD_CONFIG, 202: {"model": QuestionRunResponse, "description": "A background question run"}})
def ask(a: Ask, request: Request, who: str = Depends(matrix("ask")), display: bool = Query(False), background: bool = Query(False)):
    """The graph arm, answered whole: figures from the warehouse with the documents that back them.

    A question with one customer group, optional narrowing and measures over it
    is planned, gated and cited here (kge/services/ask/engine/combined.py); any other question gets
    exactly what /answer returns, with combined: false. With no stages override
    the model takes part wherever it can if one is configured, as before. Every answer carries answer_text, worded
    by the template, or by the model where it phrases a combined answer and its wording passes the checks.
    Set background=true in the query string to return a durable run id (202) and follow /ask/runs/{id}.
    Set compact=true to omit traces, plan internals and duplicate candidates, keeping answer content, the answered
    query's SQL and sql_certified (false unless the answer is certified), runtime labels and the history needed for
    follow-up turns. Omitted or false leaves the full response unchanged. Plan and trace are the engineer's (the role
    matrix's row 7): anyone else's answer is compact, and every role who may ask gets the SQL."""
    a = as_compact_unless_engineer(a, who)
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


@router.post("/ask/feedback", tags=["external: query"], status_code=202, response_model=FeedbackAccepted,
             response_model_exclude_unset=True, responses={400: {"description": "A receipt that does not open, or an unknown verdict"}})
def ask_feedback(f: Feedback, who: str = Depends(matrix("feedback"))):
    """Record whether an answer was what the person meant: the answer's receipt, as given, and verdict confirmed or
    not_what_i_meant, on any answer, declines included. No question, history or free text is sent, and the log keeps no
    principal. A receipt that does not open (changed, of another release, or sealed before the session key rotated) or an
    unknown verdict is a 400. A receipt may be posted again: each post is a record of its own carrying the receipt's id,
    and readers count the last verdict per receipt id. Replies 202 with nothing read back."""
    try:
        return service.feedback(served.engine(), served.PROFILE, f.receipt, f.verdict)
    except service.BadFeedback as exc:
        raise HTTPException(400, str(exc)) from None
    except (StateConflict, StateUnavailable, StoreError, OSError):
        raise HTTPException(503, FEEDBACK_UNAVAILABLE) from None


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


RUN_NOT_FOUND = "Question run not found."


def held_run(call) -> dict:
    """call()'s question run: a store error is the question state's 503, and an unknown run, or another person's, the
    404 RUN_NOT_FOUND. The routes and the tools over a question run share it, so they refuse alike."""
    try:
        out = call()
    except (StateConflict, StateUnavailable, StoreError):
        raise HTTPException(503, service.STATE_UNAVAILABLE) from None
    except ValueError:
        out = None
    if out is None:
        raise HTTPException(404, RUN_NOT_FOUND)
    return out


def followed_run(run_id: str, who: str, after: int = 0, *, loaded=None) -> dict:
    """The signed-in person's question run by id (service.get_run), its events after `after`, as GET /ask/runs/{run_id}
    and the question_run tool give it."""
    out = held_run(lambda: service.get_run(served.combined(), served.PROFILE, run_id, who, loaded=loaded))
    out["events"] = out["events"][after:]
    return out


def stopped_run(run_id: str, who: str) -> dict:
    """The signed-in person's question run stopped (service.stop_run), as DELETE /ask/runs/{run_id} and the
    stop_question_run tool give it."""
    return held_run(lambda: service.stop_run(served.combined(), served.PROFILE, run_id, who))


@router.get("/ask/runs/{run_id}", tags=["external: query"], response_model=QuestionRunResponse, response_model_exclude_unset=True,
            responses={202: {"model": QuestionRunResponse, "description": "The background question is still running"}})
def question_run(run_id: str, request: Request, who: str = Depends(matrix("ask")), after: int = Query(0, ge=0)):
    """Resume the signed-in person's question by id, as JSON or reconnectable live events."""
    if _wants_stream(request):
        comb = served.combined()
        out = held_run(lambda: _live_or_done(request, comb, run_id, who, after))
        if isinstance(out, LiveResponse):
            return out
        out["events"] = out["events"][after:]
        return _run_response(request, out)
    with question_run_usage(who, run_id, advance=True) as loaded:
        return _run_response(request, followed_run(run_id, who, after, loaded=loaded))


def _live_or_done(request: Request, comb, run_id: str, who: str, after: int):
    def advance(on_start, on_event, stop):
        with question_run_usage(who, run_id, advance=True) as loaded:
            return service.get_run(comb, served.PROFILE, run_id, who, on_start=on_start, on_event=on_event, stop=stop, loaded=loaded)
    out, live, finished = service.live_run(
        advance,
        engine, lambda: request.is_disconnected(), after=after)
    return LiveResponse(live, finished) if live is not None else out


@router.delete("/ask/runs/{run_id}", tags=["external: query"], response_model=QuestionRunResponse, response_model_exclude_unset=True)
def stop_question_run(run_id: str, request: Request, who: str = Depends(matrix("ask"))):
    """Stop the signed-in person's background question by id: its unfinished warehouse jobs are cancelled and the run
    ends failed, with reason stopped and error "Stopped.". A later GET /ask/runs/{run_id} gives it stopped; it never
    resumes. A run that has already ended is returned unchanged. Another person's run, or an unknown id, is a 404."""
    with question_run_usage(who, run_id, advance=False):
        return _run_response(request, stopped_run(run_id, who))


@router.get("/runtimes", tags=["external: query"], response_model=RuntimesResponse, response_model_exclude_unset=True)
def list_runtimes(who: str = Depends(matrix("ask"))):
    """The runtimes the caller may use, and which reference tables each holds."""
    return service.list_runtimes(served.engine(), who)


@router.post("/agent", tags=["internal: platform"], response_model=AgentResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
async def ask_agent(a: AgentAsk, request: Request, who: str = Depends(matrix("ask"))):
    """Internal: the reference agent on one arm: control (graph off) or treatment (graph on). The same model,
    prompts and seed in both; only the context differs. With no model configured it runs on rules,
    and its answer and run trace say so (agent.mode). Plan and trace are the engineer's (the role matrix's row 7):
    anyone else's answer is compact, as JSON and streamed, as /ask's is, with the answered query's SQL. The agent reaches KGE as the signed-in person,
    through the API's own MCP tools with the person's own bearer (person_tools). Its work runs on threads of its own
    (AGENT_THREADS), never the ones its nested tool calls run on, so however many run at once they cannot starve them."""
    loop = anyio.lowlevel.current_token()
    return await anyio.to_thread.run_sync(lambda: agent_reply(a, request, who, loop), limiter=AGENT_THREADS)


def agent_reply(a: AgentAsk, request: Request, who: str, loop):
    """/agent's reply, on a thread: a 400 for a bad arm, the stream, or the JSON answer; a tool's refusal of the person
    mid-turn (a bearer expired or signed out) is its own status and detail."""
    if (bad := bad_arm(a.arm)) is not None:
        return bad
    if _wants_stream(request):
        return agent_stream(a, request, who=who, loop=loop)
    try:
        return service.agent(served.agent(), served.PROFILE, a, who, compact=not engineer_view(who),
                             tools=person_tools(request, loop))
    except ToolRefused as exc:
        if exc.status is None:
            raise
        raise HTTPException(exc.status, exc.detail) from None


def person_tools(request: Request, loop):
    """The KGE tools one /agent request reaches KGE through: the MCP transport to this API's own app, in this process, as
    the signed-in person with the person's own bearer (person_bearer), each call run on loop, the event loop serving the
    app, where its MCP session managers run, from whichever thread the agent runs on. Built for the request and handed to
    its turn alone, never held on the shared agent. Each call is an in-process hop (in_process): this request holds the
    person's slot."""
    return kge_tools("mcp", url=IN_PROCESS_URL, bearer=person_bearer(request), app=in_process(served.app), loop=loop)


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
    if isinstance(exc, ToolRefused) and exc.status is not None:
        return {"event": "error", "error": exc.detail, "status": exc.status}
    return None


def agent_stream(a: AgentAsk, request: Request, *, who: str, loop):
    ag = served.agent()
    prof = served.PROFILE
    compact = not engineer_view(who)
    tools = person_tools(request, loop)
    return _stream_response(request, a.question,
                            lambda on_stage: service.agent(ag, prof, a, who, on_stage=on_stage, compact=compact, tools=tools),
                            compact=compact)


def bad_arm(arm: str) -> JSONResponse | None:
    """A 400 for an arm the reference agent does not run on; None for one it does."""
    bad = service.agent_arm_problems(arm)
    return None if bad is None else JSONResponse(bad, status_code=400)


@router.post("/context", tags=["external: knowledge"], response_model=ContextResponse, response_model_exclude_unset=True, responses=BAD_CONFIG)
def context_package(a: ContextAsk, who: str = Depends(matrix("ask"))):
    """The context package the reference agent's prompt carries on one arm, control (graph off) or treatment (graph on),
    built by the context workflow as the caller, exactly as /agent's context step gets it, as JSON. /agent's
    sign-in rules and errors: a bad arm is a 400. Set compact=true for the structured context, sources, definitions
    and decisions, with only definitions in the prompt block and no SQL, traces, duplicate candidates or plan internals.
    Omitted or false is unchanged."""
    if (bad := bad_arm(a.arm)) is not None:
        return bad
    combined = served.combined() if a.open_items else None
    return service.response_view(served.as_json(service.context(served.agent(), a, who, combined)), a.compact)


def retrieve_access(r: Retrieve, request: Request = None, who: str = Depends(principal)) -> str:
    """Check the appropriate row before executing either retrieval contract."""
    return gate(request, who, "ask" if r.include_passages else "retrieval", lambda person: may(person, "querying"))


@router.post("/retrieve", tags=["external: knowledge"], response_model=RetrieveResponse, response_model_exclude_unset=True)
def retrieve(r: Retrieve, who: str = Depends(retrieve_access)):
    """Vector search over the chunks the caller may read for a purpose; tiers: only AI-ready documents at these tiers.
    A bad shelf or tier is a 400."""
    try:
        return service.retrieve(served.engine(), r, who)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/map/{term:path}", tags=["external: knowledge"], response_model=TermMap, response_model_exclude_unset=True)
def resolved_map(term: str, who: str = Depends(matrix("ask"))):
    """A term's map, for anyone who may ask: its definition and steward, the data sets that hold it, the documents that
    define it, its contexts and the industry standards it is mapped to, in the units the caller may read. An engineer's
    is full: its certified columns, each context as the graph declares it (Term -RESOLVES_TO-> Context), and the no-join
    facts and certified joins among the units these are in, or the context's joins reach. Anyone else's is compact
    (the role matrix's row 7), in business names: no physical column, join condition, no-join fact or certified join.
    Both give each of the term's contexts explained as /graph/explore explains it (#628), its join hops in business
    words for anyone but an engineer."""
    return service.term_map(served.engine(), term, who, compact=not engineer_view(who))
