"""Calls to outside services fail in one line (kge/adapters/model/errors.py): a model provider's, Vertex AI's embeddings. The line
names the service and quotes its error message, never the request's key or token; the API answers it with a 503, and the
improvement job records it as a failed edit. KEY and TOKEN are deliberately fake: the tests prove no key reaches the
line."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from kge.improve import edits
from kge.adapters.model import providers as llm_mod
from kge.adapters.model import VertexEmbedder
from kge.adapters.model import ExternalCallError, post_json, service_of
from kge.adapters.model import LLM
from tests.scratch import profile_of, scratch_build

KEY = "AIzaFAKE-not-a-real-key-abcdefghij"  # gitleaks:allow
TOKEN = "ya29.fake-bearer-token-abcdefghij"  # gitleaks:allow


def refusing(code: int, body: str | bytes, calls: list | None = None):
    """A fake urlopen that answers every request with this HTTP error, recording each request it was given."""
    raw = body.encode() if isinstance(body, str) else body

    def urlopen(request, timeout=None):
        if calls is not None:
            calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "refused", {}, io.BytesIO(raw))
    return urlopen


def google_error(message: str, code: int = 403) -> str:
    return json.dumps({"error": {"code": code, "message": message, "status": "PERMISSION_DENIED"}})


class Credentials:
    valid = True
    token = TOKEN


def vertex_embedder() -> VertexEmbedder:
    embedder = VertexEmbedder(project="demo-project", region="europe-west2")
    embedder._account = (Credentials(), "demo-project")
    return embedder


def test_vertex_embedding_failure_is_one_line_with_the_service_s_message(monkeypatch):
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", refusing(403, google_error("Permission 'aiplatform.endpoints.predict' denied\non resource"), calls))
    with pytest.raises(ExternalCallError) as caught:
        vertex_embedder().embed("active customer")
    line = str(caught.value)
    assert line == "Vertex AI error (HTTP 403): Permission 'aiplatform.endpoints.predict' denied on resource"
    assert "\n" not in line and TOKEN not in line and calls, "the fake answered the embedder's request"
    assert isinstance(caught.value, RuntimeError) and not isinstance(caught.value, ValueError), "a 503, never /retrieve's 400"


def test_a_provider_that_echoes_the_key_has_it_redacted(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", refusing(400, google_error(f"API key {KEY} not valid. Please pass a valid API key.", 400)))
    chat = LLM("gemini", "gemini-test", KEY)
    with pytest.raises(ExternalCallError) as caught:
        chat.complete("hello")
    line = str(caught.value)
    assert line.startswith("Gemini API error (HTTP 400): API key [redacted] not valid")
    assert KEY not in line and "?key=" not in line


@pytest.mark.parametrize("url,headers,body,expected", [
    ("https://api.openai.com/v1/chat/completions", {"authorization": f"Bearer {TOKEN}"},
     json.dumps({"error": {"message": "Rate limit reached", "type": "requests"}}), "OpenAI API error (HTTP 429): Rate limit reached"),
    ("https://api.anthropic.com/v1/messages", {"x-api-key": KEY},
     json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}), "Anthropic API error (HTTP 429): Overloaded"),
    ("http://localhost:11434/v1/chat/completions", {"authorization": "Bearer ollama"},
     json.dumps({"error": "model 'x' not found"}), "localhost error (HTTP 429): model 'x' not found"),
    ("https://europe-west2-aiplatform.googleapis.com/v1/projects/p/x:generateContent", {"authorization": f"Bearer {TOKEN}"},
     "<html>\n<body>Too many requests</body>\n</html>", "Vertex AI error (HTTP 429): <html> <body>Too many requests</body> </html>"),
])
def test_each_provider_s_error_body_gives_its_message(monkeypatch, url, headers, body, expected):
    monkeypatch.setattr(urllib.request, "urlopen", refusing(429, body))
    with pytest.raises(ExternalCallError) as caught:
        llm_mod.post(url, headers, {"x": 1})
    assert str(caught.value) == expected


def test_no_answer_at_all_is_one_line_too(monkeypatch):
    def down(request, timeout=None):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(ExternalCallError, match=r"^Vertex AI unreachable: no route to host$"):
        post_json("https://aiplatform.googleapis.com/v1/x", {"authorization": f"Bearer {TOKEN}"}, {}, service="Vertex AI")


def test_service_names_come_from_the_host_alone():
    assert service_of(f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={KEY}") == "Gemini API"
    assert service_of("https://us-central1-aiplatform.googleapis.com/v1/projects/p") == "Vertex AI"
    assert service_of(f"https://models.example.com:8443/v1?token={KEY}") == "models.example.com"


def test_a_failed_model_call_while_proposing_is_a_failed_edit(monkeypatch):
    """The improvement job asks the model about each miss; a failed call is that miss's refused edit, and the job goes on."""
    monkeypatch.setattr(urllib.request, "urlopen", refusing(503, google_error("The service is currently unavailable.", 503)))
    model = LLM("openai", "gpt-test", "sk-test-key")
    diagnoses = [{"id": "TEAM-01", "question": "Lease income for June 2025", "outcome": "edit", "why": "", "edit": None}]
    out = edits.propose(diagnoses, [], {"certified_terms": {}, "existing_aliases": {}, "candidate_phrases": []}, llm=model)
    assert out["edits"] == [] and len(out["refused"]) == 1
    assert out["refused"][0]["reason"] == "refused: OpenAI API error (HTTP 503): The service is currently unavailable."


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        c.engine = appmod.engine
        yield c
    appmod._engine.close() if appmod._engine else None


@pytest.mark.parametrize("path,body", [
    ("/retrieve", {"query": "active customer", "purpose": "analysis"}),
    ("/ask", {"question": "Which calendar does Wealth Management report on?"}),
    ("/answer", {"question": "Which calendar does Wealth Management report on?"}),
    ("/context", {"question": "Which calendar does Wealth Management report on?", "arm": "treatment"}),
    ("/agent", {"question": "Which calendar does Wealth Management report on?", "arm": "treatment"}),
])
def test_an_embedding_failure_reaching_the_api_is_a_503(client, monkeypatch, path, body):
    who = client.as_("analyst_enterprise")
    client.engine().g.embedder = vertex_embedder()
    monkeypatch.setattr(urllib.request, "urlopen", refusing(403, google_error("Vertex AI API has not been used in project demo-project")))
    r = client.post(path, json=body, headers=who)
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": "Vertex AI error (HTTP 403): Vertex AI API has not been used in project demo-project"}


def test_an_embedding_failure_through_mcp_is_the_tool_s_503(client, monkeypatch):
    """/mcp bypasses the app's handlers: its tool wrapper turns the failure into the tool's error, with the same line."""
    import anyio
    from types import SimpleNamespace

    from mcp.server.fastmcp.exceptions import ToolError

    from kge.services.ask import mcp, routes
    who = client.as_("analyst_enterprise")
    client.engine().g.embedder = vertex_embedder()
    monkeypatch.setattr(urllib.request, "urlopen", refusing(403, google_error("Vertex AI API has not been used in project demo-project")))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=who)))
    query = routes.Retrieve(query="active customer", purpose="analysis")
    with pytest.raises(ToolError) as caught:
        anyio.run(mcp.tool, ctx, lambda principal: routes.retrieve(query, principal))
    assert str(caught.value) == "503: Vertex AI error (HTTP 403): Vertex AI API has not been used in project demo-project"


@pytest.mark.parametrize("region, host", [("global", "aiplatform.googleapis.com"), ("europe-west2", "europe-west2-aiplatform.googleapis.com")])
def test_vertex_calls_use_the_global_host_for_global_and_the_regions_host_otherwise(monkeypatch, region, host):
    sent = []
    embedder = VertexEmbedder(project="demo-project", region=region)
    monkeypatch.setattr(embedder, "token", lambda: (TOKEN, "demo-project"))
    monkeypatch.setattr(embedder, "_post", lambda url, headers, body: sent.append(url) or {"predictions": [{"embeddings": {"values": [0.0] * embedder.dim}}]})
    embedder.embed("text")
    assert sent[0].startswith(f"https://{host}/v1/projects/demo-project/locations/{region}/")
    assert llm_mod.vertex_url(region) == f"https://{host}/v1"
