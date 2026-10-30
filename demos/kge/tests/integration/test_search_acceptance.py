"""Meridian acceptance for the knowledge search API on both transports."""
from types import SimpleNamespace

import anyio
import pytest
from fastapi.testclient import TestClient

from kge.services.identity import token_for
from kge.services.search.models import Search
from kge.services.search.service import search
from tests.scratch import profile_of, scratch_build


@pytest.fixture
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app, base_url="http://127.0.0.1:8000") as http:
        http.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield http, appmod


def test_revenue_finds_retail_product_and_never_another_unit(client):
    http, appmod = client
    headers = http.as_("analyst_retail")
    body = {"query": "revenue", "limit": 100}
    response = http.post("/search", json=body, headers=headers)
    assert response.status_code == 200, response.text
    hits = response.json()["hits"]
    assert any(hit["kind"] == "product" and hit["id"] == "retail.monthly_revenue" for hit in hits)
    assert {hit["unit"] for hit in hits} == {"retail"}
    assert "wealth" not in response.text.casefold()
    assert response.json() == http.post("/search", json=body, headers=headers).json()
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))
    result = anyio.run(appmod.search_capability_mcp._tool_manager.call_tool, "search", body, ctx)
    assert response.json() == result.model_dump(exclude_unset=True)
    assert {hit.kind for hit in search(appmod.engine(), "analyst_retail", Search(query="revenue", limit=100)).hits} >= {
        "product", "column", "term", "metric"}
    documents = http.post("/search", json={"query": "Retail Banking Data Dictionary", "kinds": ["document"]}, headers=headers)
    assert documents.status_code == 200
    assert any(hit["id"] == "DD-RETAIL" for hit in documents.json()["hits"])
    assert all(hit["unit"] == "retail" for hit in documents.json()["hits"])
    wealth = http.post("/search", json={"query": "net new assets"}, headers=headers)
    assert wealth.status_code == 200 and all(hit["unit"] == "retail" for hit in wealth.json()["hits"])
    assert not any(hit["kind"] == "metric" for hit in wealth.json()["hits"])


def test_mounted_mcp_and_http_reject_bad_identity(client):
    http, _ = client
    for headers in ({}, {"authorization": "Bearer invalid"}):
        assert http.post("/search", json={"query": "revenue"}, headers=headers).status_code == 401
        assert http.post("/mcp/search", json={}, headers=headers).status_code == 401
    denied = http.post("/search", json={"query": "revenue"}, headers=http.as_("pipeline_tagger"))
    assert denied.status_code == 403


def test_wire_mcp_search_matches_http(client):
    http, _ = client
    headers = {**http.as_("analyst_retail"), "accept": "application/json, text/event-stream"}
    body = {"query": "revenue", "kinds": ["product"], "limit": 5}
    response = http.post("/mcp/search", headers=headers, json={"jsonrpc": "2.0", "id": 1,
        "method": "tools/call", "params": {"name": "search", "arguments": body}})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["structuredContent"] == http.post("/search", headers=headers, json=body).json()


def test_search_on_the_bigquery_twin_matches_ladybug(tmp_path):
    import json
    from tests.integration.test_graphstore_twin import direct, pointer, twin

    build = scratch_build(tmp_path / "build")
    local, _ = pointer(build)
    script = tmp_path / "search.py"
    script.write_text('''
import json
from kge.pipeline.profile import resolve
from kge.services.ask import Engine
from kge.services.search.models import Search
from kge.services.search.service import search
engine = Engine.serving(resolve())
try:
    print(json.dumps([search(engine, "analyst_retail", Search(query=query, limit=100)).model_dump()
                      for query in ("revenue", "Retail Banking Data Dictionary", "net new assets")], sort_keys=True))
finally:
    engine.close()
''')
    expected = json.loads(direct(build, str(script)).stdout)
    actual = json.loads(twin(build, local, "-", str(script)).stdout)
    assert actual == expected


def test_search_explains_metadata_restrictions_and_rejects_noise(client):
    http, _ = client
    headers = http.as_("analyst_enterprise")
    noise = http.post("/search", json={"query": "zzqx blorf", "kinds": ["document"]}, headers=headers)
    assert noise.status_code == 200
    assert noise.json()["hits"] == []
    response = http.post("/search", json={"query": "date of birth", "kinds": ["column"]}, headers=headers)
    assert response.status_code == 200
    hit = next(hit for hit in response.json()["hits"] if hit["id"] == "retail.customer.date_of_birth")
    assert hit["restricted"] is True
    assert hit["why"]["kind"] in {"name", "description"}
    assert "date of birth" in hit["why"]["text"].lower().replace("_", " ")
    assert hit["why"]["term"] is None
