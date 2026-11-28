"""The co-deployed portal's authenticated, governed chat transport."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from kge.adapters.model import model_for
from kge.pipeline.settings import settings
from kge.ports import StoreError
from kge.services.http import gate, may, principal, served, trusted_portal
from kge.services.identity import person, session_key
from kge.services.modelgateway import service
from kge.services.modelgateway.models import ChatBody, FinishBody, QuestionBody, QuestionReply, ReservationReply, UsageReply

router = APIRouter()

@dataclass(frozen=True)
class Actor:
    who: str
    kind: str
    roles: tuple[str, ...]
    client: str


def portal(request: Request):
    trusted_portal(request, "The model gateway is only available to the portal.")


def actor(request: Request, who: str = Depends(principal)) -> Actor:
    """People pass the Ask matrix; a configured machine is granted only model chat, never Ask or query."""
    header = request.headers.get("x-kge-authorization") or request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer ") or not header[7:].strip():
        raise HTTPException(401, "A caller bearer is required.")
    graph = served.engine().g
    roles = tuple(sorted(graph.acting_roles(who)))
    if who not in graph.people.people():
        raise HTTPException(403, "The model gateway needs a registered caller.")
    if person(graph.processes(who)) or graph.people.is_admin(who):
        gate(request, who, "ask", lambda name: may(name, "querying"))
        kind = "person"
    else:
        enabled = {name.strip() for name in (settings().MODEL_GATEWAY_SERVICE_PRINCIPALS or "").split(",") if name.strip()}
        if who not in enabled:
            raise HTTPException(403, "This service is not allowed to call the model gateway.")
        kind = "service"
    return Actor(who, kind, roles, served.PROFILE.client)


def correlation(actor: Actor, purpose: str):
    """Use the deployment's shared session key; never trust token contents as identity."""
    from kge.adapters.modelusage import Actor as UsageActor, CorrelationTokens
    current = settings()
    if current.K_SERVICE and not (current.SESSION_SECRET or "").strip():
        raise HTTPException(503, "The model question service needs a shared session key.")
    codec = CorrelationTokens((session_key(served.PROFILE.run_dir()),))
    scope = UsageActor(actor.client, actor.kind, actor.who, "cerebro" if purpose == "agent" else "discover")
    return codec, scope


@contextmanager
def model_scope(caller: Actor, body: ChatBody, model, ledger, invocation_id: str):
    """Bind quota and encrypted continuation to authenticated server state for the whole stream."""
    from kge.adapters.model import Caller, ContinuationBinding, Continuations, QuotaExceeded, QuotaUnavailable, continuation_scope, quota_caller
    from kge.adapters.modelusage import UsageConflict, UsageUnavailable, usage_scope
    from kge.pipeline.trace import telemetry_scope
    scope = Caller(caller.client, caller.kind, caller.who, caller.roles)
    binding = ContinuationBinding(tenant=caller.client, principal=caller.who,
                                  app="cerebro" if body.purpose == "agent" else "discover",
                                  conversation=str(body.conversation_id), purpose=body.purpose,
                                  provider=model.provider, model=model.model, prefix="0" * 64)
    codec = Continuations(session_key(served.PROFILE.run_dir()), max_age_seconds=30 * 24 * 60 * 60)
    try:
        with quota_caller(scope), continuation_scope(codec, binding), usage_scope(ledger, str(body.question_id), invocation_id), telemetry_scope(caller.client, "cerebro" if body.purpose == "agent" else "discover_ask", str(body.conversation_id)):
            yield
    except (QuotaExceeded, QuotaUnavailable) as exc:
        try:
            reason = "quota_refused" if isinstance(exc, QuotaExceeded) else "quota_unavailable"
            ledger.finish_invocation_no_inference(str(body.question_id), invocation_id, reason=reason)
        except UsageConflict:
            ledger.finish_invocation(str(body.question_id), invocation_id)
        raise
    except BaseException:
        try:
            ledger.finish_invocation(str(body.question_id), invocation_id)
        except (UsageConflict, UsageUnavailable, StoreError, OSError):
            pass
        raise
    else:
        ledger.finish_invocation(str(body.question_id), invocation_id)


def model_error(exc):
    """Quota errors are distinct from provider busy responses and never include internal exception text."""
    from kge.adapters.model import QuotaExceeded, QuotaUnavailable
    from kge.adapters.modelusage import UsageConflict, UsageUnavailable
    if isinstance(exc, QuotaExceeded):
        return {"code": "model_quota_exceeded", "message": "You have reached the model request limit. Please try again later.",
                "retry_after": exc.retry_after}
    if isinstance(exc, (QuotaUnavailable, UsageConflict, UsageUnavailable)):
        return {"code": "model_limit_unavailable", "message": "The model request limit is temporarily unavailable. Please try again."}
    return None


def usage_ledger(caller: Actor, purpose: str, conversation_id: str):
    """Open the durable numeric-only ledger for the verified actor, never for request body identity."""
    from kge.adapters.modelusage import Actor as UsageActor, for_profile
    scope = UsageActor(caller.client, caller.kind, caller.who, "cerebro" if purpose == "agent" else "discover")
    return for_profile(served.PROFILE, scope, conversation_id)


def verified_question(body: QuestionBody, request: Request, caller: Actor):
    from kge.adapters.modelusage import CorrelationInvalid
    codec, scope = correlation(caller, body.purpose)
    try:
        question_id = codec.verify(request.headers.get("x-kge-question-token", ""), scope, body.purpose, str(body.conversation_id))
    except CorrelationInvalid:
        raise HTTPException(403, "The model question token is invalid or expired.") from None
    if question_id != str(body.question_id):
        raise HTTPException(403, "The model question token does not match this question.")
    return question_id


def usage_report(ledger, question_id: str) -> UsageReply:
    """Incomplete or malformed measurements are unknown, never a zero or a successful report."""
    try:
        result = ledger.report(question_id)
        if not isinstance(result, dict) or set(result) != set(UsageReply.model_fields):
            return UsageReply()
        if result.get("calls") == 0 and result.get("complete") is not True:
            return UsageReply()
        return UsageReply.model_validate(result)
    except (ValueError, TypeError, ValidationError):
        return UsageReply()


def ledger_call(action):
    """A missing ledger fails closed; an in-progress attempt remains open for a later finish."""
    from kge.adapters.modelusage import UsageConflict, UsageUnavailable
    try:
        return action()
    except UsageConflict:
        raise HTTPException(409, "This question has unfinished model work. Try again shortly.") from None
    except (UsageUnavailable, StoreError, OSError, ValueError):
        raise HTTPException(503, "Model usage is temporarily unavailable.") from None


def check_model(body: QuestionBody, configured):
    """Refuse a portal expectation that does not match KGE's configured selection."""
    if body.expected_model and (configured is None or configured.model != body.expected_model):
        raise HTTPException(409, "The portal's previous model does not match the KGE model. Configure the KGE model or the corresponding Cerebro or catalogue model tier to use the same model, then try again.")


def model_binding(model):
    """Bind the effective connector without putting its endpoint or credentials in the ledger or token."""
    if model is None:
        return ("none", "none")
    identity = [model.model, getattr(model, "base_url", None), getattr(model, "api_key", ""),
                getattr(model, "prices", None), getattr(model, "region", None), getattr(getattr(model, "chat", None), "project", None)]
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha256(b"kge-question-connector-v1\0" + encoded).hexdigest()
    return (model.provider, digest)


@router.post("/model/question", tags=["internal: platform"], response_model=QuestionReply)
def question(body: QuestionBody, request: Request, _trusted: None = Depends(portal), caller: Actor = Depends(actor)):
    """Internal: issue an opaque accounting token for the authenticated caller and question."""
    configured = model_for("cerebro" if body.purpose == "agent" else "catalogue", expected_model=body.expected_model)
    check_model(body, configured)
    codec, scope = correlation(caller, body.purpose)
    binding = model_binding(configured)
    def legacy_binding():
        legacy = model_for("cerebro" if body.purpose == "agent" else "catalogue")
        return model_binding(legacy)
    ledger = ledger_call(lambda: usage_ledger(caller, body.purpose, str(body.conversation_id)))
    ledger_call(lambda: ledger.bind_question(str(body.question_id), binding, legacy_binding=legacy_binding))
    ledger_call(lambda: ledger.open_question(str(body.question_id)))
    return {"question_id": body.question_id, "correlation_token": codec.issue(scope, str(body.question_id), body.purpose, str(body.conversation_id))}


@router.post("/model/usage", tags=["internal: platform"], response_model=UsageReply)
def usage(body: QuestionBody, request: Request, _trusted: None = Depends(portal), caller: Actor = Depends(actor)):
    """Internal: read the caller's numeric model usage for a verified question."""
    question_id = verified_question(body, request, caller)
    return ledger_call(lambda: usage_report(usage_ledger(caller, body.purpose, str(body.conversation_id)), question_id))


@router.post("/model/usage/reserve", tags=["internal: platform"], response_model=ReservationReply)
def reserve(body: QuestionBody, request: Request, _trusted: None = Depends(portal), caller: Actor = Depends(actor)):
    """Internal: reserve a durable marker before the trusted portal dispatches an MCP question."""
    question_id = verified_question(body, request, caller)
    invocation_id = str(uuid4())
    ledger = ledger_call(lambda: usage_ledger(caller, body.purpose, str(body.conversation_id)))
    if not ledger_call(lambda: ledger.start_invocation(question_id, invocation_id)):
        raise HTTPException(409, "The model invocation was already started.")
    return {"invocation_id": invocation_id}


@router.post("/model/usage/finish", tags=["internal: platform"], response_model=UsageReply)
def finish(body: FinishBody, request: Request, _trusted: None = Depends(portal), caller: Actor = Depends(actor)):
    """Internal: close a completed question after its background work and model attempts have ended."""
    question_id = verified_question(body, request, caller)
    def close():
        ledger = usage_ledger(caller, body.purpose, str(body.conversation_id))
        if body.no_inference:
            ledger.finish_question_no_inference(question_id, question_id, reason=body.no_inference)
            return usage_report(ledger, question_id)
        ledger.finish_question(question_id)
        return usage_report(ledger, question_id)
    return ledger_call(close)


@router.post("/model/chat", tags=["internal: platform"], response_class=StreamingResponse)
def chat(body: ChatBody, request: Request, _trusted: None = Depends(portal), caller: Actor = Depends(actor)):
    """Internal: stream a chat for a signed-in person or registered machine, through the trusted portal alone.

    Each SSE frame names `part`, `final` or `error`. Tool calls are provisional until a final STOP frame; an error or
    disconnect has no final frame. KGE selects the configured model and applies the purpose's budget.
    """
    verified_question(body, request, caller)
    from kge.pipeline.trace import register_telemetry
    from kge.services.http import REQUEST_SPAN
    held = request.scope.get(REQUEST_SPAN)
    if held is not None:
        register_telemetry(held.span, caller.client, "cerebro" if body.purpose == "agent" else "discover_ask", str(body.conversation_id))
    agent = "cerebro" if body.purpose == "agent" else "catalogue"
    model = model_for(agent, expected_model=body.expected_model)
    check_model(body, model)
    actual = model_binding(model)
    if model is None:
        raise HTTPException(503, "The model is not configured.")
    ledger = ledger_call(lambda: usage_ledger(caller, body.purpose, str(body.conversation_id)))
    ledger_call(lambda: ledger.bind_question(str(body.question_id), actual, legacy_binding=lambda: model_binding(model_for(agent))))
    try:
        chat_request = body.request()
    except TypeError:
        raise HTTPException(503, "The model does not support this response format.") from None
    invocation_id = str(uuid4())
    if not ledger_call(lambda: ledger.start_invocation(str(body.question_id), invocation_id)):
        raise HTTPException(409, "The model invocation was already started.")
    return StreamingResponse(service.stream(model, chat_request, request.is_disconnected,
                                             scope_factory=lambda: model_scope(caller, body, model, ledger, invocation_id), error_of=model_error), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
