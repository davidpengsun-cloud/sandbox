"""An authenticated request reaches the real Ask and Assist routes with caller-scoped model quotas."""
import json
from fastapi.testclient import TestClient

from kge.adapters.model.quota import admit
from kge.services.ask import service as ask_service
from kge.services.assist import service as assist_service
from kge.services.identity import token_for
from tests.scratch import profile_of, scratch_build


def test_authenticated_ask_and_assist_refuse_quota_without_guessing(tmp_path, monkeypatch):
    import kge.app as appmod

    build = scratch_build(tmp_path / "build")
    profile = profile_of(build)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path / "quotas"))
    for name, value in (("PROFILE", profile), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    who = "analyst_enterprise.meridian@example.com"
    headers = {"Authorization": f"Bearer {token_for(profile.run_dir(), who)}"}
    calls = []

    def quota_probe(*args, **kwargs):
        admit("test:model", "plan")
        calls.append("sent")
        admit("test:model", "wording")
        raise AssertionError("the quota should refuse before this point")

    monkeypatch.setattr(ask_service, "ask", quota_probe)
    with TestClient(appmod.app) as client:
        response = client.post("/ask", headers=headers, json={"question": "NNA for June 2025"})
        assert response.status_code == 429
        assert "request limit" in response.json()["detail"]
        assert response.headers["Retry-After"]

        class Router:
            def json(self, prompt, maximum):
                admit("test:model", "classify")
                raise AssertionError("the quota should refuse before this point")

        monkeypatch.setattr(assist_service, "routing_model", lambda c: Router())
        response = client.post("/assist", headers=headers, json={"question": "NNA for June 2025"})
        assert response.status_code == 429
        assert "request limit" in response.json()["detail"]
        assert who not in response.text

        other = "analyst_retail.meridian@example.com"
        stream_headers = {"Authorization": f"Bearer {token_for(profile.run_dir(), other)}", "Accept": "application/x-ndjson"}
        response = client.post("/ask", headers=stream_headers, json={"question": "NNA for June 2025"})
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        error = next(event for event in events if event.get("event") == "error")
        assert error["status"] == 429
        assert "request limit" in error["error"]
        assert other not in response.text

        background_user = "advisor_wealth.meridian@example.com"
        background_headers = {"Authorization": f"Bearer {token_for(profile.run_dir(), background_user)}"}
        monkeypatch.setattr(ask_service, "_run_pass", quota_probe)
        response = client.post("/ask?background=true", headers=background_headers,
                               json={"question": "NNA for June 2025"})
        assert response.status_code == 202
        assert response.json()["state"] == "failed"
        assert "request limit" in response.json()["error"]
        assert background_user not in response.text
    assert calls == ["sent", "sent", "sent"]
    if appmod._engine:
        appmod._engine.close()
