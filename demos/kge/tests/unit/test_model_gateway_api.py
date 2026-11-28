"""The model gateway is person-scoped and keeps provider details out of failures."""
import asyncio
import json
import sys
import threading
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kge.pipeline.trace import check_cancelled
from kge.ports import ChatEvent, ChatPart
from kge.services import http
from kge.services.identity import Identities
from kge.services.modelgateway import models, routes, service


class Model:
    model = "gemini-3-flash"
    provider = "vertex-gemini"

    def __init__(self):
        self.requests = []

    def stream_chat(self, request):
        self.requests.append(request)
        yield ChatEvent(part=ChatPart("text", text="Hello."))
        yield ChatEvent(part=ChatPart("call", call_id="c1", name="search", value={"query": "NNA"}, signature="opaque"))
        yield ChatEvent(model="served", tokens_in=10, tokens_out=3, tokens_thinking=1, finish="STOP")


@pytest.fixture
def client(monkeypatch):
    model = Model()
    identities = SimpleNamespace(resolve=lambda header, cookie: {"Bearer alice": "alice", "Bearer denied": "denied", "Bearer service": "service", "Bearer role": "role"}.get(header))
    graph = SimpleNamespace(processes=lambda who: {"tagging"} if who == "service" else {"querying"},
                            acting_roles=lambda who: {"pipeline"} if who == "service" else {"analyst"},
                            people=SimpleNamespace(is_admin=lambda who: False, people=lambda: {"alice", "denied", "service"}))
    state = SimpleNamespace(ids=lambda: identities, COOKIE="session", SIGN_IN="Sign in", UNRELEASED_MESSAGE=None,
                            engine=lambda: SimpleNamespace(g=graph), PROFILE=SimpleNamespace(client="test-client"))
    monkeypatch.setattr(http, "served", state)
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(http, "settings", lambda: SimpleNamespace(TRUSTED_CALLER_AUDIENCE="", PORTAL_SYNC_TOKEN="portal-secret"))
    monkeypatch.setattr(routes, "settings", lambda: SimpleNamespace(MODEL_GATEWAY_SERVICE_PRINCIPALS="service"))
    class CorrelationInvalid(Exception):
        pass

    class Codec:
        def issue(self, scope, question_id, purpose, conversation_id, *, model_binding=None):
            return f"{scope.who}:{purpose}:{conversation_id}:{question_id}"

        def verify(self, token, scope, purpose, conversation_id):
            prefix = f"{scope.who}:{purpose}:{conversation_id}:"
            if not token.startswith(prefix):
                raise CorrelationInvalid()
            return token[len(prefix):]

        def verify_bound(self, token, scope, purpose, conversation_id):
            return self.verify(token, scope, purpose, conversation_id), None

    class UsageConflict(Exception):
        pass

    class UsageUnavailable(Exception):
        pass

    monkeypatch.setitem(sys.modules, "kge.adapters.modelusage", SimpleNamespace(CorrelationInvalid=CorrelationInvalid,
                                                                                 UsageConflict=UsageConflict,
                                                                                 UsageUnavailable=UsageUnavailable))
    monkeypatch.setattr(routes, "correlation", lambda actor, purpose: (Codec(), actor))
    class Ledger:
        def __init__(self):
            self.opened = set()
            self.closed = set()

        def open_question(self, question_id, **kwargs):
            self.opened.add(question_id)

        def bind_question(self, question_id, binding, *, legacy_binding):
            pass

        def report(self, question_id):
            return {"calls": 1, "input_tokens": 10 if question_id in self.closed else None,
                    "output_tokens": None, "thinking_tokens": None, "cost_usd": None,
                    "complete": question_id in self.closed}

        def finish_question(self, question_id):
            self.closed.add(question_id)

        def start_invocation(self, question_id, invocation_id):
            return True

    ledgers = {}
    monkeypatch.setattr(routes, "usage_ledger", lambda actor, purpose, conversation: ledgers.setdefault((actor.client, actor.who, purpose, conversation), Ledger()))
    calls = []

    def gate(request, who, row, today, state=None):
        calls.append((who, row, request.url.path))
        if who != "alice":
            raise HTTPException(403, "Not allowed")
        return who

    monkeypatch.setattr(http, "gate", gate)
    monkeypatch.setattr(routes, "gate", gate)
    monkeypatch.setattr(routes, "model_for", lambda purpose, **kwargs: model)
    monkeypatch.setattr(routes, "model_binding", lambda model: (model.provider, model.model) if model else ("none", "none"))
    monkeypatch.setattr(routes, "model_scope", lambda caller, body, model, ledger, invocation_id: nullcontext())
    monkeypatch.setattr(routes, "model_error", lambda exc: None)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as test:
        yield SimpleNamespace(http=test, model=model, checks=calls, ledgers=ledgers)


def payload(**overrides):
    body = {"purpose": "agent", "question_id": "c356a749-c495-42a5-b6a3-88ecb57eb464", "conversation_id": "31d4cdf6-9de0-451c-877a-d155b8f23180", "instructions": "Help safely.", "messages": [{"role": "user", "parts": [{"kind": "text", "text": "What is NNA?"}]}],
            "tools": [{"name": "search", "description": "Search", "parameters": {"type": "object"}}]}
    return {**body, **overrides}


QUESTION = "c356a749-c495-42a5-b6a3-88ecb57eb464"
CONVERSATION = "31d4cdf6-9de0-451c-877a-d155b8f23180"
HEADERS = {"x-portal-sync-token": "portal-secret", "x-kge-authorization": "Bearer alice",
           "x-kge-question-token": f"alice:agent:{CONVERSATION}:{QUESTION}"}


def events(response):
    return [(chunk.split("\n", 1)[0][7:], json.loads(chunk.split("\ndata: ", 1)[1]))
            for chunk in response.text.strip().split("\n\n")]


def test_trusted_portal_and_signed_person_are_both_required(client):
    assert client.http.post("/model/chat", json=payload()).status_code == 403
    assert client.http.post("/model/chat", json=payload(), headers={"x-portal-sync-token": "portal-secret"}).status_code == 401
    denied = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer denied"})
    assert denied.status_code == 403
    assert not client.model.requests
    assert client.checks == [("denied", "ask", "/model/chat")]


def test_chat_streams_parts_and_final_usage_for_authorized_caller(client):
    response = client.http.post("/model/chat", json=payload(), headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    parts = events(response)
    assert [name for name, _ in parts] == ["part", "part", "final"]
    assert parts[1][1]["signature"] == "opaque"
    assert (parts[-1][1]["tokens_in"], parts[-1][1]["tokens_out"], parts[-1][1]["finish"]) == (10, 3, "STOP")
    assert client.checks == [("alice", "ask", "/model/chat")]
    assert client.model.requests[0].purpose == "agent"
    assert client.model.requests[0].messages[0].parts[0].text == "What is NNA?"


def test_registered_service_bearer_is_attributed_from_credential(client):
    response = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer service",
                                                                      "x-kge-question-token": f"service:agent:{CONVERSATION}:{QUESTION}"})
    assert response.status_code == 200
    assert client.checks == [], "a machine receives only the configured model capability, not Ask"


def test_registered_machine_uses_real_identity_token(client, monkeypatch, tmp_path):
    identities = Identities(tmp_path / "tokens", ["alice", "service"])
    monkeypatch.setattr(http.served, "ids", lambda: identities)
    token = identities.token_for("service")
    response = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": f"Bearer {token}",
                                                                      "x-kge-question-token": f"service:agent:{CONVERSATION}:{QUESTION}"})
    assert response.status_code == 200
    assert client.checks == []
    denied = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer forged"})
    assert denied.status_code == 401


def test_machine_is_refused_without_explicit_model_capability(client, monkeypatch):
    monkeypatch.setattr(routes, "settings", lambda: SimpleNamespace(MODEL_GATEWAY_SERVICE_PRINCIPALS=""))
    response = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer service"})
    assert response.status_code == 403
    assert not client.model.requests


def test_allowlisted_bearer_for_unregistered_role_is_refused(client, monkeypatch):
    monkeypatch.setattr(routes.served.engine().g.people, "people", lambda: {"alice", "denied"})
    response = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer service"})
    assert response.status_code == 403
    assert not client.model.requests


def test_bearer_for_querying_role_is_not_a_registered_person(client):
    response = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer role"})
    assert response.status_code == 403
    assert not client.model.requests


@pytest.mark.parametrize("extra", [{"principal": "alice"}, {"model": "override"}, {"output_tokens": 9}, {"purpose": "other"},
                                   {"question_id": "not-a-uuid"},
                                   {"response_schema": {"type": "object"}},
                                   {"response_format": "json", "response_schema": {"type": "invalid"}},
                                   {"response_format": "json", "response_schema": {"$ref": "https://example.org/schema"}},
                                   {"response_format": "json", "response_schema": {"$id": "https://example.org/schema"}},
                                   {"response_format": "json", "response_schema": {"properties": {"$ref": {"$ref": "https://example.org/schema"}}}}])
def test_callers_cannot_override_identity_model_or_budgets(client, extra):
    assert client.http.post("/model/chat", json=payload(**extra), headers=HEADERS).status_code == 422
    assert not client.model.requests


def test_tool_round_trip_preserves_signature_and_private_replay(client):
    messages = [{"role": "user", "parts": [{"kind": "text", "text": "Find it"}]},
                {"role": "assistant", "parts": [{"kind": "call", "call_id": "c1", "name": "search", "value": {"q": "NNA"},
                                                "signature": "sig", "metadata": {"claude_continuation": "opaque-ciphertext"}}]},
                {"role": "tool", "parts": [{"kind": "result", "call_id": "c1", "name": "search", "value": {"hits": []}}]}]
    assert client.http.post("/model/chat", json=payload(messages=messages), headers=HEADERS).status_code == 200
    part = client.model.requests[-1].messages[1].parts[0]
    assert part.signature == "sig" and part.metadata == {"claude_continuation": "opaque-ciphertext"}


def test_plain_private_replay_metadata_is_never_sent(client, monkeypatch):
    def unsafe(_request):
        yield ChatEvent(part=ChatPart("call", call_id="c1", name="search", value={}, metadata={"claude_replay": "private thinking"}))
        yield ChatEvent(finish="STOP")
    monkeypatch.setattr(client.model, "stream_chat", unsafe)
    response = client.http.post("/model/chat", json=payload(), headers=HEADERS)
    assert [kind for kind, _ in events(response)] == ["error"]
    assert "private thinking" not in response.text


def test_continuation_transport_length_boundary():
    data = {"kind": "call", "call_id": "c1", "name": "search", "value": {},
            "metadata": {"claude_continuation": "x" * models.MAX_CONTINUATION_CHARS}}
    assert models.Part.model_validate(data).metadata == data["metadata"]
    event = service._event(ChatEvent(part=ChatPart(**data)))
    assert len(event) > models.MAX_CONTINUATION_CHARS
    data["metadata"]["claude_continuation"] += "x"
    with pytest.raises(ValueError):
        models.Part.model_validate(data)
    with pytest.raises(ValueError):
        service._event(ChatEvent(part=ChatPart(**data)))


def test_real_maximum_continuation_and_reduction_survive_gateway():
    from kge.adapters.model.continuation import (CONTINUATION_MAX_PAYLOAD_BYTES, ContinuationBinding, Continuations)
    binding = ContinuationBinding(tenant="client", principal="alice", app="cerebro", conversation="c", purpose="agent",
                                  provider="vertex-claude", model="claude", prefix="0" * 64)
    cipher = Continuations(b"x" * 32, max_age_seconds=60, max_payload_bytes=CONTINUATION_MAX_PAYLOAD_BYTES)
    token = cipher.seal(b"x" * CONTINUATION_MAX_PAYLOAD_BYTES, binding)
    recipe = {"drop": 1, "omit": [{"turn": 1, "message": 0, "part": 0, "call_id": "call"}]}
    data = {"kind": "call", "call_id": "c1", "name": "search", "value": {},
            "metadata": {"claude_continuation": token, "reduction": recipe}}
    assert len(token) == models.MAX_CONTINUATION_CHARS
    assert models.Part.model_validate(data).metadata == data["metadata"]
    assert b'"reduction"' in service._event(ChatEvent(part=ChatPart(**data)))
    data["metadata"]["reduction"]["omit"][0]["turn"] = True
    with pytest.raises(ValueError):
        models.Part.model_validate(data)
    with pytest.raises(ValueError):
        service._event(ChatEvent(part=ChatPart(**data)))


def test_unmatched_tool_result_is_rejected_before_provider(client):
    messages = [{"role": "user", "parts": [{"kind": "text", "text": "Question"}]},
                {"role": "tool", "parts": [{"kind": "result", "call_id": "x", "name": "search", "value": {}}]}]
    assert client.http.post("/model/chat", json=payload(messages=messages), headers=HEADERS).status_code == 422
    assert not client.model.requests


def test_gateway_does_not_leak_provider_exception_or_commit_partial_tool_calls(client, monkeypatch):
    def broken(_request):
        yield ChatEvent(part=ChatPart("call", call_id="c1", name="search", value={}))
        raise RuntimeError("secret internal provider detail")
    monkeypatch.setattr(client.model, "stream_chat", broken)
    response = client.http.post("/model/chat", json=payload(), headers=HEADERS)
    received = events(response)
    assert [name for name, _ in received] == ["part", "error"]
    assert "secret" not in response.text
    assert not any(name == "final" for name, _ in received)


def test_issue_and_verify_are_bound_to_authenticated_identity_purpose_and_question(client):
    created = client.http.post("/model/question", json={"question_id": QUESTION, "conversation_id": CONVERSATION, "purpose": "agent"}, headers=HEADERS)
    assert created.status_code == 200
    token = created.json()["correlation_token"]
    assert created.json()["question_id"] == QUESTION
    mismatch = client.http.post("/model/chat", json=payload(question_id="f4a9743e-dc83-4d10-860e-983dd7f72000"),
                                headers={**HEADERS, "x-kge-question-token": token})
    assert mismatch.status_code == 403
    wrong_actor = client.http.post("/model/chat", json=payload(), headers={**HEADERS, "x-kge-authorization": "Bearer service",
                                                                         "x-kge-question-token": token})
    assert wrong_actor.status_code == 403
    wrong_purpose = client.http.post("/model/chat", json=payload(purpose="catalogue"), headers=HEADERS)
    assert wrong_purpose.status_code == 403
    wrong_conversation = client.http.post("/model/chat", json=payload(conversation_id="f4a9743e-dc83-4d10-860e-983dd7f72000"), headers=HEADERS)
    assert wrong_conversation.status_code == 403
    assert not client.model.requests


def test_prior_model_is_an_assertion_and_never_changes_kge_selection(client):
    body = {"question_id": QUESTION, "conversation_id": CONVERSATION, "purpose": "agent"}
    wrong = client.http.post("/model/question", json={**body, "expected_model": "other-model"}, headers=HEADERS)
    assert wrong.status_code == 409 and "Configure the KGE model" in wrong.json()["detail"]
    assert not client.ledgers, "a mismatch must not create or spend an accounting record"
    correct = client.http.post("/model/question", json={**body, "expected_model": "gemini-3-flash"}, headers=HEADERS)
    assert correct.status_code == 200
    chat = client.http.post("/model/chat", json=payload(expected_model="other-model"), headers=HEADERS)
    assert chat.status_code == 409
    assert client.model.requests == []


def test_numeric_usage_stays_open_until_finished_and_is_actor_bound(client):
    body = {"question_id": QUESTION, "conversation_id": CONVERSATION, "purpose": "agent"}
    assert client.http.post("/model/question", json=body, headers=HEADERS).status_code == 200
    opened = client.http.post("/model/usage", json=body, headers=HEADERS)
    assert opened.json() == {"calls": 1, "input_tokens": None, "output_tokens": None,
                             "thinking_tokens": None, "cost_usd": None, "complete": False}
    wrong = client.http.post("/model/usage", json=body, headers={**HEADERS, "x-kge-authorization": "Bearer service"})
    assert wrong.status_code == 403
    finished = client.http.post("/model/usage/finish", json=body, headers=HEADERS)
    assert finished.json()["input_tokens"] == 10
    assert finished.json()["complete"] is True
    assert client.http.post("/model/usage/finish", json=body, headers=HEADERS).json() == finished.json()


def test_malformed_report_is_unknown_not_zero():
    ledger = SimpleNamespace(report=lambda question_id: {"calls": "invalid", "complete": True})
    assert routes.usage_report(ledger, QUESTION).model_dump() == {
        "calls": None, "input_tokens": None, "output_tokens": None, "thinking_tokens": None,
        "cost_usd": None, "complete": False}


def test_empty_ledger_is_not_reported_as_verified_zero(client, monkeypatch):
    def unverified(_question_id):
        raise sys.modules["kge.adapters.modelusage"].UsageConflict()
    ledger = SimpleNamespace(report=lambda question_id: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                        "thinking_tokens": 0, "cost_usd": 0, "complete": False},
                             finish_question=unverified)
    monkeypatch.setattr(routes, "usage_ledger", lambda actor, purpose, conversation: ledger)
    body = {"question_id": QUESTION, "conversation_id": CONVERSATION, "purpose": "agent"}
    opened = client.http.post("/model/usage", json=body, headers=HEADERS)
    assert opened.status_code == 200
    assert opened.json()["calls"] is None and opened.json()["complete"] is False
    assert client.http.post("/model/usage/finish", json=body, headers=HEADERS).status_code == 409


def test_disconnect_cancels_model_worker():
    started, stopped = threading.Event(), threading.Event()

    class Slow:
        def stream_chat(self, request):
            started.set()
            try:
                while True:
                    check_cancelled()
                    time.sleep(0.01)
                    if False:
                        yield ChatEvent()
            finally:
                stopped.set()

    async def exercise():
        async def disconnected():
            return started.is_set()
        body = service.stream(Slow(), payload, disconnected)
        return [event async for event in body]

    assert asyncio.run(exercise()) == []
    assert stopped.wait(1), "disconnect must stop the model worker"


def test_worker_scope_spans_entire_model_stream():
    caller = ContextVar("test_gateway_caller", default=None)
    seen = []

    @contextmanager
    def scope():
        token = caller.set("verified-alice")
        try:
            yield
        finally:
            caller.reset(token)

    class Scoped:
        def stream_chat(self, request):
            seen.append(caller.get())
            yield ChatEvent(part=ChatPart("text", text="Hello"))
            seen.append(caller.get())
            yield ChatEvent(finish="STOP")

    async def exercise():
        async def connected():
            return False
        return [item async for item in service.stream(Scoped(), payload, connected, scope_factory=scope)]

    assert len(asyncio.run(exercise())) == 2
    assert seen == ["verified-alice", "verified-alice"]


def test_limit_refusal_is_a_distinct_safe_stream_error():
    @contextmanager
    def quota_scope():
        raise RuntimeError("private quota record")
        yield

    async def exercise():
        async def connected():
            return False
        return [item async for item in service.stream(Model(), payload, connected, scope_factory=quota_scope,
            error_of=lambda exc: {"code": "model_quota_exceeded", "message": "Please try again later."})]

    frames = asyncio.run(exercise())
    assert len(frames) == 1 and b'model_quota_exceeded' in frames[0]
    assert b'private quota record' not in frames[0]


def test_final_frame_waits_for_accounting_scope_to_finish():
    @contextmanager
    def failing_accounting():
        yield
        raise RuntimeError("usage record failed")

    async def exercise():
        async def connected():
            return False
        return [item async for item in service.stream(Model(), payload, connected, scope_factory=failing_accounting)]

    frames = asyncio.run(exercise())
    assert [frame.split(b"\n", 1)[0] for frame in frames] == [b"event: part", b"event: part", b"event: error"]
    assert all(b"event: final" not in frame for frame in frames)
