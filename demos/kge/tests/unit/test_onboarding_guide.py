"""Role docs share shipped files and honour the live identity reader."""
from pathlib import Path
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.services.tools.markdown import render_markdown
from kge.services.tools import routes, service
from kge.pipeline.access_table import Access
from kge.adapters.appstate import access_state
from kge.services.identity import Identities
from tests.scratch import write_registry, profile_of

DEMO = Path(__file__).resolve().parents[2]
GUIDES = DEMO / "ui/guides"


def test_every_audience_has_a_page_guide():
    assert {p.stem for p in GUIDES.glob("*.md")} == set(service.GUIDES)
    for path in GUIDES.glob("*.md"):
        assert "## 1." in path.read_text()


def test_the_renderer_escapes_html_and_renders_commands_and_expectations():
    source = '# <script>x</script>\n\nA **bold** and `code` <img src=x>.\n\n- one\n\n| A | B |\n|---|---|\n| <b>x</b> | y |\n\n```kge\nkge grant "<bad>"\n```\n```expect\n<img src=x>\n```'
    rendered = render_markdown(source)
    assert "<h1>&lt;script&gt;x&lt;/script&gt;</h1>" in rendered
    assert "<strong>bold</strong>" in rendered and "<code>code</code>" in rendered
    assert "<ul>\n<li>one</li>" in rendered and "<table>" in rendered
    assert "&lt;img src=x&gt;" in rendered and "&lt;b&gt;x&lt;/b&gt;" in rendered
    assert "data-copy" in rendered and "You should see" in rendered
    assert "<script>" not in rendered and "<img" not in rendered and "<b>" not in rendered


@pytest.fixture
def docs(tmp_path, monkeypatch):
    write_registry(tmp_path, store=tmp_path / "store")
    prof = profile_of(tmp_path)
    state = access_state(prof)
    now = [0.0]
    reader = Access(state, clock=lambda: now[0])
    roles = {"operator": {}, "engineer": {"processes": ["building", "improving"]},
             "business": {"processes": ["accepting"]}, "steward": {"processes": ["certification"]}}
    monkeypatch.setattr(routes, "authorization_roles", lambda _: roles)
    ids = Identities(tmp_path / "identities", reader.people, access=reader)
    app = FastAPI()
    app.include_router(routes.router)
    from types import SimpleNamespace
    monkeypatch.setattr(routes, "served", SimpleNamespace(ids=lambda: ids, PROFILE=prof))
    def identity(request):
        who = ids.resolve(request.headers.get("authorization"), None)
        if who is None:
            raise routes.HTTPException(401)
        return who
    monkeypatch.setattr(routes, "principal", identity)
    with TestClient(app) as client:
        yield client, state, now, ids


def test_signed_out_gets_only_connect_and_not_role_content(docs):
    client, _, _, _ = docs
    for url in ("/onboarding-guide", "/onboarding-guide?role=steward"):
        response = client.get(url)
        assert response.status_code == 200
        assert "Sign in and connect" in response.text
        assert "kge sign-off" not in response.text
        assert response.headers["cache-control"] == "no-store"


def test_roles_union_revocation_and_admin_rows_follow_table_ttl(docs):
    client, state, now, ids = docs
    who = "business_team.meridian@example.com"
    state.access_put({"username": who, "roles": ["business", "steward"], "name": "Example"}, by=who, via="job")
    headers = {"Authorization": "Bearer " + ids.token_for(who)}
    first = client.get("/onboarding-guide", headers=headers)
    assert 'role=business' in first.text and 'role=steward' in first.text
    assert 'role=operator' not in first.text and 'role=access-admin' not in first.text
    assert client.get("/onboarding-guide?role=operator", headers=headers).status_code == 403
    state.access_put({"username": who, "roles": ["access_admin"], "name": "Example"}, by=who, via="job")
    now[0] = 60
    second = client.get("/onboarding-guide", headers=headers)
    assert 'role=developer' in second.text and 'role=business' not in second.text
    assert 'role=access-admin' not in second.text
    assert client.get("/onboarding-guide?role=steward", headers=headers).status_code == 403
    state.access_delete(who, by=who, via="job")
    now[0] = 120
    assert "Sign in and connect" in client.get("/onboarding-guide", headers=headers).text


def test_bootstrap_admin_gets_integration_guide_without_product_actions(docs, monkeypatch):
    client, _, _, ids = docs
    who = "access_admin.meridian@example.com"
    monkeypatch.setenv("ACCESS_ADMINS", who)
    response = client.get("/onboarding-guide", headers={"Authorization": "Bearer " + ids.token_for(who)})
    assert 'role=developer' in response.text
    assert 'role=access-admin' not in response.text
    assert 'role=operator' not in response.text
    assert '}, 20000);' in response.text
    assert 'role=steward' not in response.text


@pytest.mark.parametrize("role", service.GUIDES)
def test_rendered_role_pages_hold_only_product_content(docs, role):
    client, state, _, ids = docs
    who = "reader@example.com"
    state.access_put({"username": who, "roles": ["engineer", "business", "steward"], "name": "Reader"},
                     by=who, via="job")
    response = client.get(f"/onboarding-guide?role={role}", headers={"Authorization": "Bearer " + ids.token_for(who)})
    assert response.status_code == 200
    assert not re.search(r"\b(KGE|gcloud|Cloud Run|Compose|deployment|hosting|proxy|make tokens)\b|example\.com|```expect|--dry-run|kge connect", response.text, re.I)
    assert "Knowledge docs" in response.text
    assert "<meta name=\"viewport\"" in response.text


@pytest.mark.parametrize("assigned,expected", [(["engineer"], {"knowledge-engineer", "developer"}),
    (["business"], {"business", "developer"}), (["steward"], {"steward", "developer"}),
    (["operator"], {"developer"})])
def test_audience_filter_uses_permissions_without_granting_a_new_role(docs, assigned, expected):
    client, state, _, ids = docs
    who = "reader@example.com"
    state.access_put({"username": who, "roles": assigned, "name": "Reader"}, by=who, via="job")
    headers = {"Authorization": "Bearer " + ids.token_for(who)}
    response = client.get("/onboarding-guide", headers=headers)
    assert set(re.findall(r'href="/onboarding-guide\?role=([^"&]+)"', response.text)) == expected
    for role in set(service.GUIDES) - expected:
        assert client.get(f"/onboarding-guide?role={role}", headers=headers).status_code == 403


def test_integration_guide_has_real_addresses_and_token_exchange(docs):
    client, state, _, ids = docs
    who = "reader@example.com"
    state.access_put({"username": who, "roles": ["business"], "name": "Reader"}, by=who, via="job")
    response = client.get("/onboarding-guide?role=developer", headers={"Authorization": "Bearer " + ids.token_for(who)})
    assert '<code id="connection-api">http://testserver</code>' in response.text
    assert '<code id="connection-mcp">http://testserver/mcp</code>' in response.text
    assert all(word in response.text for word in ("/session/token", "expires_in", "access_token", "Authorization", "username", "password"))
    assert "no more than the person" in response.text


def test_signed_out_page_keeps_ask_and_uses_the_guide_index():
    page = (DEMO / "ui/index.html").read_text()
    assert 'if (!ME) return "ask";' in page and 'if (!ME) return view === "ask";' in page
    assert 'id="role-docs"' in page and '$("#role-docs").src = "/onboarding-guide";' in page
    assert 'if (!ME) return view === "ask"' in page


def test_e2e_signoff_hint_uses_an_email_placeholder():
    script = (DEMO / "tests/e2e/demo_script.cjs").read_text()
    assert "make sign-off PRINCIPAL=<steward>" not in script
    assert "make sign-off PRINCIPAL=<steward-email>" in script
