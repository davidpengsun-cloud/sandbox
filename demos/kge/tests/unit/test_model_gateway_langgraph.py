"""One offline loopback through LangGraph, the gateway, MCP accounting, and HITL resume."""
import os
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException, Request

from kge.adapters.modelusage import current_usage
from kge.ports import ChatEvent, ChatPart
from kge.services import http
from kge.services.identity import Identities
from kge.services.modelgateway import routes

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.environ.get("KGE_HELPER_SOCKETS") != "1", reason="KGE_HELPER_SOCKETS=1 enables loopback acceptance")
def test_langgraph_hitl_gateway_and_mcp_usage(tmp_path, monkeypatch):
    if not (ROOT / "portal/server/node_modules").exists():
        pytest.skip("portal server dependencies are required")
    monkeypatch.setenv("PORTAL_SYNC_TOKEN", "s" * 32)
    for key in ("K_SERVICE", "STORE_URL", "MODEL_USAGE_KEY", "SESSION_SECRET", "TRUSTED_CALLER_AUDIENCE"):
        monkeypatch.delenv(key, raising=False)
    ids = Identities(tmp_path / "ids", ["alice"])
    graph = SimpleNamespace(processes=lambda who: {"querying"}, acting_roles=lambda who: {"analyst"},
                            people=SimpleNamespace(is_admin=lambda who: False, people=lambda: {"alice"}))
    state = SimpleNamespace(ids=lambda: ids, COOKIE="session", SIGN_IN="Sign in", UNRELEASED_MESSAGE=None,
                            engine=lambda: SimpleNamespace(g=graph), PROFILE=SimpleNamespace(client="test-client", run_dir=lambda: tmp_path))
    monkeypatch.setattr(http, "served", state)
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(routes, "gate", lambda req, who, row, today: who)

    def account():
        ledger, question, invocation = current_usage()
        attempt = str(uuid.uuid4())
        ledger.start_attempt(question, attempt, invocation)
        ledger.finish_attempt(question, attempt, {"outcome": "ok", "input_tokens": 10, "output_tokens": 4, "thinking_tokens": 1,
            "cache_read_tokens": None, "cache_write_tokens": None, "input_price": 2, "output_price": 4,
            "cache_read_price": None, "cache_write_price": None, "cache_mode": "none"})

    class Model:
        model = "mock-model"
        provider = "mock"

        def stream_chat(self, request):
            account()
            results = [part.name for message in request.messages for part in message.parts if part.kind == "result"]
            if "choose" not in results:
                yield ChatEvent(part=ChatPart("call", call_id="choose-1", name="choose", value={"question": "Which book?", "options": ["Use Wealth.", "Use Retail."]}))
            elif "ask" not in results:
                yield ChatEvent(part=ChatPart("call", call_id="ask-1", name="ask", value={"question": "NNA?"}))
            else:
                yield ChatEvent(part=ChatPart("text", text="Finished."))
            yield ChatEvent(model="mock-model", tokens_in=10, tokens_out=4, finish="STOP")

    monkeypatch.setattr(routes, "model_for", lambda purpose, **kwargs: Model())
    app = FastAPI()
    app.include_router(routes.router)

    @app.get("/whoami")
    def whoami():
        return {"id": "alice", "cerebro_roles": ["business"]}

    @app.get("/readable-units")
    def units():
        return {"units": []}

    @app.get("/golden")
    def golden():
        return {"sets": []}

    @app.post("/session/bearer")
    def bearer(request: Request):
        if request.headers.get("x-portal-sync-token") != "s" * 32 or request.cookies.get("kge_session") != "alice":
            raise HTTPException(403)
        return {"access_token": ids.token_for("alice")}

    @app.post("/mcp/answer")
    async def mcp(request: Request):
        if request.headers.get("x-kge-authorization") != "Bearer " + ids.token_for("alice"):
            raise HTTPException(401)
        body = await request.json()
        with http.mcp_usage_context(request, "alice", body["params"]["name"]):
            account()
        return {"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": {"answer": "ok"}}}

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        try:
            process = subprocess.run(["node", "--experimental-sqlite", str(ROOT / "tests/fixtures/model_gateway/hitl.mjs")],
                                     env={**os.environ, "ENDPOINT_PORT": str(sock.getsockname()[1]), "PORTAL_KEY": "s" * 32},
                                     text=True, capture_output=True, timeout=45, check=False)
            assert process.returncode == 0, process.stdout + process.stderr
        finally:
            server.should_exit = True
            thread.join(5)
