"""Gaps people hit: the steward's read of the unmatched-question log, grouped and windowed, with who asked kept private."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.services import http
from kge.services.steward import routes, service

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
PRIVATE = {"principal", "arm", "path", "reason"}


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(question, at, who="alice@example.test"):
    return {"at": at, "path": "/ask", "arm": "treatment", "principal": who, "question": question,
            "reason": "No glossary term recognised"}


@pytest.fixture
def profile(tmp_path):
    return SimpleNamespace(run_dir=lambda: tmp_path, client="meridian")


def write(profile, rows):
    lines = [r if isinstance(r, str) else json.dumps(r) for r in rows]
    (profile.run_dir() / "unmatched.jsonl").write_text("\n".join(lines) + "\n")


def keys(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys(v)}
    return set()


def test_missing_log_is_empty(profile):
    assert service.gaps(profile, now=NOW) == {"since": "2026-11-08T12:00:00Z", "days": 7, "total": 0, "gaps": []}


def test_window_excludes_rows_older_than_seven_days(profile):
    write(profile, [row("churn by region", ago(days=8)), row("churn by region", ago(days=6))])
    reply = service.gaps(profile, now=NOW)
    assert reply["total"] == 1
    assert reply["gaps"] == [{"text": "churn by region", "count": 1, "last_at": ago(days=6)}]


def test_groups_by_normalised_text_and_orders_by_count_then_recency(profile):
    write(profile, [
        row("Churn  by Region", ago(days=3)),
        row("churn by region", ago(days=1)),
        row("  CHURN BY\tREGION ", ago(days=2)),
        row("net flows", ago(hours=1)),
        row("fee drift", ago(hours=5)),
        row("Fee drift", ago(hours=4)),
        row("older single", ago(days=5)),
    ])
    reply = service.gaps(profile, now=NOW)
    assert reply["total"] == 7
    assert reply["gaps"] == [
        {"text": "churn by region", "count": 3, "last_at": ago(days=1)},
        {"text": "Fee drift", "count": 2, "last_at": ago(hours=4)},
        {"text": "net flows", "count": 1, "last_at": ago(hours=1)},
        {"text": "older single", "count": 1, "last_at": ago(days=5)},
    ]


def test_cap_limits_groups_but_not_total(profile):
    write(profile, [row(f"question {i}", ago(hours=i)) for i in range(30)])
    reply = service.gaps(profile, now=NOW)
    assert reply["total"] == 30
    assert len(reply["gaps"]) == 20
    assert reply["gaps"][0]["text"] == "question 0"
    assert len(service.gaps(profile, now=NOW, limit=3)["gaps"]) == 3


def test_malformed_rows_and_bad_times_are_skipped(profile):
    write(profile, ["not json", "[1, 2]", json.dumps({"question": "no time"}),
                    row("bad time", "yesterday"), row("", ago(hours=1)), {"at": ago(hours=1), "question": 7},
                    row("kept", ago(hours=2))])
    reply = service.gaps(profile, now=NOW)
    assert reply["total"] == 1
    assert [g["text"] for g in reply["gaps"]] == ["kept"]


def test_reply_never_carries_who_asked(profile):
    write(profile, [row("churn by region", ago(hours=1), who="secret@example.test")])
    reply = service.gaps(profile, now=NOW)
    assert not keys(reply) & PRIVATE
    assert "secret@example.test" not in json.dumps(reply)


@pytest.fixture
def client(monkeypatch, profile):
    write(profile, [row("churn by region", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        who="secret@example.test")])
    graph = SimpleNamespace(may=lambda who, process: who == "steward" and process == "certification",
                            acting_roles=lambda who: {who}, people=SimpleNamespace(is_admin=lambda who: False),
                            role_templates=lambda: {"steward": {"processes": ["certification", "querying"]}})
    fake = SimpleNamespace(engine=lambda: SimpleNamespace(g=graph), PROFILE=profile)
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(routes, "served", fake)
    app = FastAPI()
    app.include_router(routes.router)
    def principal(request: Request):
        return request.headers.get("who", "steward")

    app.dependency_overrides[http.principal] = principal
    with TestClient(app) as test_client:
        yield test_client


def test_route_serves_a_steward(client):
    response = client.get("/steward/gaps")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"since", "days", "total", "gaps"}
    assert body["days"] == 7 and body["total"] == 1
    assert body["gaps"][0]["text"] == "churn by region" and body["gaps"][0]["count"] == 1
    assert not keys(body) & PRIVATE
    assert "secret@example.test" not in response.text


def test_route_refuses_one_without_certification(client):
    assert client.get("/steward/gaps", headers={"who": "reader"}).status_code == 403
