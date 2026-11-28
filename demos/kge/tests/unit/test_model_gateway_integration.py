"""Exercise authentication, correlation, a model attempt and durable usage across the HTTP route."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters.modelusage import current_usage
from kge.adapters.model.continuation import current_continuation
from kge.adapters.model.quota import current_quota_caller
from kge.ports import ChatEvent, ChatPart
from kge.services import http
from kge.services.identity import Identities
from kge.services.modelgateway import routes


QUESTION = "e3c514f3-58f6-4d8f-a3c0-6d69ce763a31"
CONVERSATION = "9583c497-b81d-4e33-9f57-c1550f16f184"
ATTEMPT = "69a01e71-11ed-4633-8509-71005eb869f9"


def test_real_identity_correlation_and_usage_across_gateway_requests(tmp_path, monkeypatch):
    for name in ("K_SERVICE", "STORE_URL", "MODEL_USAGE_KEY", "SESSION_SECRET", "TRUSTED_CALLER_AUDIENCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORTAL_SYNC_TOKEN", "x" * 32)
    identities = Identities(tmp_path / "identities", ["alice", "bob"])
    graph = SimpleNamespace(processes=lambda who: {"querying"}, acting_roles=lambda who: {"analyst"},
                            people=SimpleNamespace(is_admin=lambda who: False, people=lambda: {"alice", "bob"}))
    profile = SimpleNamespace(client="test-client", run_dir=lambda: tmp_path)
    state = SimpleNamespace(ids=lambda: identities, COOKIE="session", SIGN_IN="Sign in", UNRELEASED_MESSAGE=None,
                            engine=lambda: SimpleNamespace(g=graph), PROFILE=profile)
    monkeypatch.setattr(http, "served", state)
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(routes, "gate", lambda request, who, row, today: who)

    class Instrumented:
        model, provider = "mock-model", "mock"

        def stream_chat(self, request):
            ledger, question_id, invocation_id = current_usage()
            caller = current_quota_caller()
            assert (caller.client, caller.kind, caller.identifier, caller.roles) == ("test-client", "person", "alice", ("analyst",))
            codec, binding = current_continuation()
            assert (binding.tenant, binding.principal, binding.app, binding.conversation) == ("test-client", "alice", "cerebro", CONVERSATION)
            assert codec.open(codec.seal(b"private", binding), binding) == b"private"
            assert ledger.start_attempt(question_id, ATTEMPT, invocation_id)
            yield ChatEvent(part=ChatPart("text", text="Hello."))
            ledger.finish_attempt(question_id, ATTEMPT, {"outcome": "ok", "input_tokens": 10, "output_tokens": 4,
                "thinking_tokens": 1, "cache_read_tokens": None, "cache_write_tokens": None,
                "input_price": 2, "output_price": 4, "cache_read_price": None, "cache_write_price": None, "cache_mode": "none"})
            yield ChatEvent(model="mock-model", tokens_in=10, tokens_out=4, tokens_thinking=1, finish="STOP")

    monkeypatch.setattr(routes, "model_for", lambda purpose, **kwargs: Instrumented())
    app = FastAPI()
    app.include_router(routes.router)
    body = {"question_id": QUESTION, "conversation_id": CONVERSATION, "purpose": "agent"}
    alice = {"x-portal-sync-token": "x" * 32, "x-kge-authorization": f"Bearer {identities.token_for('alice')}"}
    with TestClient(app) as client:
        issued = client.post("/model/question", json=body, headers=alice)
        assert issued.status_code == 200
        headers = {**alice, "x-kge-question-token": issued.json()["correlation_token"]}
        asked = client.post("/model/chat", json={**body, "instructions": "Help", "messages": [
            {"role": "user", "parts": [{"kind": "text", "text": "Hello"}]}], "tools": []}, headers=headers)
        assert asked.status_code == 200 and "event: final" in asked.text
        closed = client.post("/model/usage/finish", json=body, headers=headers)
        assert closed.status_code == 200
        assert closed.json() == {"calls": 1, "input_tokens": 10, "output_tokens": 4, "thinking_tokens": 1,
                                 "cost_usd": 0.000036, "complete": True}
        bob = {**headers, "x-kge-authorization": f"Bearer {identities.token_for('bob')}"}
        assert client.post("/model/usage", json=body, headers=bob).status_code == 403

        other = {**body, "question_id": "95f7d16e-f703-4523-bb1a-0a21fb62444a"}
        issued_other = client.post("/model/question", json=other, headers=alice)
        other_headers = {**alice, "x-kge-question-token": issued_other.json()["correlation_token"]}
        cancelled = client.post("/model/usage/finish", json={**other, "no_inference": "deterministic_cancelled"}, headers=other_headers)
        assert cancelled.status_code == 200
        assert cancelled.json() == {"calls": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
                                    "cost_usd": 0.0, "complete": True}
        repeated = client.post("/model/usage/finish", json={**other, "no_inference": "deterministic_cancelled"}, headers=other_headers)
        assert repeated.json() == cancelled.json()

        cannot_rewrite = client.post("/model/usage/finish", json={**body, "no_inference": "deterministic_cancelled"}, headers=headers)
        assert cannot_rewrite.status_code == 409
        assert client.post("/model/usage", json=body, headers=headers).json() == closed.json()

        reserved_body = {**body, "question_id": "5aed5563-0948-45a4-bb0b-c4d54fb89892"}
        reserved_issued = client.post("/model/question", json=reserved_body, headers=alice)
        reserved_headers = {**alice, "x-kge-question-token": reserved_issued.json()["correlation_token"]}
        reserved = client.post("/model/usage/reserve", json=reserved_body, headers=reserved_headers)
        assert reserved.status_code == 200 and reserved.json()["invocation_id"]
        assert client.post("/model/usage/finish", json=reserved_body, headers=reserved_headers).status_code == 409
        assert client.post("/model/usage", json=reserved_body, headers=reserved_headers).json()["complete"] is False
