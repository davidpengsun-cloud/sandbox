"""Feedback on an answer (#464, requirements 5, 7 and 10): POST /ask/feedback and the feedback tool on /mcp/answer, as
two people with the querying role; a bad receipt or verdict is a 400 on both, a repeat is a record of its own with the
same receipt id, and the log keeps no principal or question. The answer's new fields are declared and kept by compact."""
from __future__ import annotations

import json
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from kge.services import http
from kge.services.ask import mcp, routes, service
from kge.services.ask.models import Answer, AnswerResponse, Ask, AskResponse, Feedback
from kge.services.ask.receipt import Receipts
from kge.services.identity import session_key

QUERYING = {"ana", "ben"}
QUESTION = "certified NNA minus current NNA for March 2025"
ANSWER = {"answer_id": "turn-1", "answer_mode": "not_sure", "answered_by": "rules",
          "plan": {"measure": "nna", "filters": [{"column": "client_id", "value": "<client_id>"}]}}
NEW_FIELDS = {"answer_mode": "ai_generated", "answered_by": "model:mock:planner", "answer_id": "turn-1",
              "receipt": "sealed", "ai_fallback": "on", "model": "mock:planner"}


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    engine = SimpleNamespace(identity={"release": "20261115T101500Z-abc123", "hash": "f" * 64},
                             g=SimpleNamespace(may=lambda who, process: process == "querying" and who in QUERYING,
                                               acting_roles=lambda who: {"analyst_retail"} if who in QUERYING else set(),
                                               people=SimpleNamespace(is_admin=lambda who: False),
                                               role_templates=lambda: {"analyst_retail": {"processes": ["querying"]}}))
    profile = SimpleNamespace(run_dir=lambda: tmp_path, client="meridian")
    state = SimpleNamespace(engine=lambda: engine, PROFILE=profile, SIGN_IN="sign in", COOKIE="kge_session",
                            ids=lambda: SimpleNamespace(resolve=lambda header, cookie: header.removeprefix("Bearer ") if header else None))
    for module in (http, routes, mcp):
        monkeypatch.setattr(module, "served", state)
    return SimpleNamespace(engine=engine, profile=profile, log=tmp_path / "feedback" / "20261115T101500Z-abc123.jsonl")


@pytest.fixture
def client(served):
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, http.request_validation_failed)
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        yield test_client


def receipt(served, answer=ANSWER) -> str:
    return service.receipts(served.engine, served.profile).seal(answer)


def records(served) -> list[dict]:
    return [json.loads(line) for line in served.log.read_text().splitlines()]


def post(client, who, body):
    return client.post("/ask/feedback", json=body, headers={"Authorization": f"Bearer {who}"} if who else {})


def call(who, body):
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(
        headers={"authorization": f"Bearer {who}"} if who else {})))
    return anyio.run(mcp.answer_mcp_server()._tool_manager.call_tool, "feedback", body, ctx)


def test_two_people_give_feedback_over_http_and_a_repeat_is_its_own_record(served, client):
    first, second = receipt(served), receipt(served)
    for who, token, verdict in (("ana", first, "confirmed"), ("ben", second, "not_what_i_meant"),
                                ("ana", first, "not_what_i_meant")):
        response = post(client, who, {"receipt": token, "verdict": verdict})
        assert (response.status_code, response.json()) == (202, {})
    kept = records(served)
    assert [r["verdict"] for r in kept] == ["confirmed", "not_what_i_meant", "not_what_i_meant"]
    assert kept[0]["receipt_id"] == kept[2]["receipt_id"] != kept[1]["receipt_id"]
    assert {r["release"] for r in kept} == {"20261115T101500Z-abc123"}
    assert all(r["plan"] == ANSWER["plan"] and r["answer_mode"] == "not_sure" for r in kept)
    text = served.log.read_text()
    assert "ana" not in text and "ben" not in text and QUESTION not in text and first not in text


def test_two_people_give_feedback_through_the_mcp_tool(served):
    for who in ("ana", "ben"):
        out = call(who, {"receipt": receipt(served), "verdict": "confirmed"})
        assert out == {}
    assert [r["verdict"] for r in records(served)] == ["confirmed", "confirmed"]
    assert "ana" not in served.log.read_text() and "ben" not in served.log.read_text()


def tampered(token: str) -> str:
    return token[:20] + ("A" if token[20] != "A" else "B") + token[21:]


@pytest.mark.parametrize("bad", ["tampered", "other release", "rotated key", "not a receipt", "bad verdict"])
def test_a_bad_receipt_or_verdict_is_a_400_on_both_transports(served, client, monkeypatch, bad):
    token, verdict = receipt(served), "confirmed"
    if bad == "tampered":
        token = tampered(token)
    elif bad == "other release":
        token = Receipts(session_key(served.profile.run_dir()), "20261114T090000Z-def456").seal(ANSWER)
    elif bad == "rotated key":
        monkeypatch.setenv("SESSION_SECRET", "a-rotated-session-secret-of-32-characters-or-more")
    elif bad == "not a receipt":
        token = "not-a-receipt"
    else:
        verdict = "maybe"
    response = post(client, "ana", {"receipt": token, "verdict": verdict})
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    with pytest.raises(ToolError) as tool:
        call("ben", {"receipt": token, "verdict": verdict})
    assert str(tool.value).endswith(f"400: {detail}")
    assert not served.log.exists()


def test_feedback_is_signed_in_and_needs_querying_on_both_transports(served, client):
    body = {"receipt": receipt(served), "verdict": "confirmed"}
    assert post(client, None, body).status_code == 401
    assert post(client, "carl", body).status_code == 403
    with pytest.raises(ToolError, match="^401: "):
        call(None, body)
    with pytest.raises(ToolError, match="^403: "):
        call("carl", body)
    assert not served.log.exists()


def test_feedback_takes_a_receipt_and_a_verdict_and_nothing_else(served, client):
    response = post(client, "ana", {"receipt": receipt(served), "verdict": "confirmed", "question": QUESTION})
    assert response.status_code == 422
    with pytest.raises(ToolError, match="^422: "):
        call("ana", {"receipt": receipt(served), "verdict": "confirmed", "principal": "ben"})
    assert set(Feedback.model_fields) == {"receipt", "verdict"}
    assert not served.log.exists()


def test_the_answer_declares_its_new_fields_and_ask_takes_no_ai_fallback():
    for model in (AnswerResponse, AskResponse):
        assert set(NEW_FIELDS) <= set(model.model_fields)
        assert all(not model.model_fields[name].is_required() for name in NEW_FIELDS)
    assert set(NEW_FIELDS) <= set(Answer.model_json_schema()["properties"])
    assert Answer.model_fields["answer_mode"].annotation.__args__[0].__args__ == ("certified", "ai_generated", "not_sure")
    assert Answer.model_fields["ai_fallback"].annotation.__args__[0].__args__ == ("on", "off")
    assert "ai_fallback" not in Ask.model_fields
    with pytest.raises(ValidationError):
        Ask(question="q", ai_fallback="on")


def test_compact_keeps_the_new_fields():
    out = {"question": "q", "behaviour": "figure", "answer_text": "Certified. 12", "sql": "SELECT 1", "trace": [],
           **NEW_FIELDS}
    view = service.response_view(out, compact=True)
    assert {name: view[name] for name in NEW_FIELDS} == NEW_FIELDS
    assert (view["sql"], view["sql_certified"]) == ("SELECT 1", False)
    assert "trace" not in view


def test_the_tools_describe_the_label_the_fallback_and_feedback():
    tools = {item.name: item for item in anyio.run(mcp.answer_mcp_server().list_tools)}
    ask = tools["ask"].description
    for words in ("Show answer_text to the person as given", "Certified", "AI-Generated", "ai_fallback",
                  "ASK_AI_FALLBACK", "Never compute or change a figure"):
        assert words in ask, words
    assert " ".join(tools["feedback"].description.split()) == ("Record whether an answer was what the person meant: verdict "
                                                             "confirmed or not_what_i_meant, with the answer's receipt as "
                                                             "given. Call it only when the person says so.")
    assert tools["feedback"].meta == {"route": "POST /ask/feedback", "tags": ["external: query"], "role": "querying"}
    assert "ai_fallback" not in tools["ask"].inputSchema["properties"], "the fallback is the deployment's, not a request's"


@pytest.mark.parametrize("decline", [
    {"behaviour": "abstain", "reason": "No glossary term recognised in the question."},
    {"behaviour": "refuse", "reason": "ana is not a querying identity."}])
def test_a_decline_made_before_the_planner_carries_a_not_sure_receipt_feedback_accepts(served, client, decline):
    out = service.with_receipt(dict(decline), served.engine, served.profile)
    assert (out["answer_mode"], out["answered_by"]) == ("not_sure", "rules") and out["answer_id"]
    opened = service.receipts(served.engine, served.profile).open(out["receipt"])
    assert (opened["answer_id"], opened["answer_mode"], opened["plan"]) == (out["answer_id"], "not_sure", None)
    assert post(client, "ana", {"receipt": out["receipt"], "verdict": "not_what_i_meant"}).status_code == 202
    assert "receipt" not in service.with_receipt({"behaviour": "definition", "answer_text": "A term."}, served.engine, served.profile)


@pytest.mark.parametrize("behaviour", ["clarify", "refuse", "blocked_access", "abstain", "abstain_route_steward",
                                       "abstain_with_reason"])
def test_every_decline_carries_a_not_sure_receipt_its_feedback_records(served, behaviour):
    """Every decline wording names (#553): an access block, a question routed to the steward and an abstention with its
    reason as well as abstain, refuse and clarify get a not-sure receipt, and feedback on it is recorded."""
    from kge.services.ask.engine.wording import DECLINES
    assert behaviour in DECLINES and len(DECLINES) == 6
    out = service.with_receipt({"behaviour": behaviour, "reason": "Not answered."}, served.engine, served.profile)
    assert (out["answer_mode"], out["answered_by"]) == ("not_sure", "rules") and out["answer_id"] and out["receipt"]
    assert service.feedback(served.engine, served.profile, out["receipt"], "not_what_i_meant") == {}
    [kept] = records(served)
    assert (kept["answer_mode"], kept["answered_by"], kept["plan"], kept["verdict"]) == (
        "not_sure", "rules", None, "not_what_i_meant")


def in_filter(n, column, values):
    return {"kind": "in", "id": f"n{n}", "left": {"kind": "column", "name": {"id": column}}, "values": values}


def long_plan(values, filters=2, measures=1):
    """A receipt's normalised plan (planpublic.receipt_plan) whose IN filters list many values: entity keys, kept by
    their type alone, or declared members, kept whole."""
    return {"schema": "query-plan/v1",
            "measures": [{"kind": "measure", "id": f"n{filters + m + 1}", "name": {"id": "measure:nna"}} for m in range(measures)],
            "filters": [in_filter(n, f"column:retail.accounts.c{n}", values) for n in range(1, filters + 1)]}


INTEGERS = [{"kind": "integer"}] * 256
MEMBERS = [{"kind": "text", "value": f"declared-member-{n:03d}"} for n in range(256)]


@pytest.mark.parametrize("values, filters", [(INTEGERS, 2), (MEMBERS, 1), (MEMBERS, 2)])
def test_a_receipt_whose_plan_lists_many_values_can_be_posted_unchanged(served, client, values, filters):
    """A plan whose IN filters list hundreds of values (#553): the receipt Ask issues stays within the cap a feedback call
    takes, which stays 8,192 characters, so the receipt posts unchanged over HTTP and MCP; the log keeps the plan with
    each IN filter's values counted by kind, the rest of it as it was."""
    from kge.services.ask.engine.planpublic import RECEIPT_PLAN
    from kge.services.ask.models import MAX_RECEIPT_CHARS
    plan = long_plan(values, filters)
    whole = receipt(served, {"answer_id": "turn-9", "answer_mode": "certified", "answered_by": "rules", "plan": plan})
    assert MAX_RECEIPT_CHARS == 8192 and len(whole) > MAX_RECEIPT_CHARS
    out = service.with_receipt({"behaviour": "figure", "answer_id": "turn-9", "answer_mode": "certified",
                                "answered_by": "rules", RECEIPT_PLAN: plan}, served.engine, served.profile)
    assert len(out["receipt"]) <= MAX_RECEIPT_CHARS and RECEIPT_PLAN not in out
    assert Feedback(receipt=out["receipt"], verdict="confirmed").receipt == out["receipt"]
    assert post(client, "ana", {"receipt": out["receipt"], "verdict": "confirmed"}).status_code == 202
    assert call("ben", {"receipt": out["receipt"], "verdict": "not_what_i_meant"}) == {}
    kept = records(served)
    assert [(r["answer_mode"], r["verdict"]) for r in kept] == [("certified", "confirmed"), ("certified", "not_what_i_meant")]
    held = kept[0]["plan"]
    assert held["measures"] == plan["measures"] and held["schema"] == plan["schema"]
    assert [(f["id"], f["left"], f["values"]) for f in held["filters"]] == [
        (f["id"], f["left"], [{"kind": values[0]["kind"], "count": 256}]) for f in plan["filters"]]


def test_a_receipt_whose_plan_is_too_large_even_counted_keeps_no_plan_and_can_be_posted(served, client):
    """A plan too large even with its values counted: the receipt keeps the answer's id, mode and who answered, with no
    plan, rather than one a feedback call refuses (#553)."""
    from kge.services.ask.engine.planpublic import RECEIPT_PLAN
    from kge.services.ask.models import MAX_RECEIPT_CHARS
    plan = long_plan(INTEGERS, filters=2, measures=200)
    out = service.with_receipt({"behaviour": "figure", "answer_id": "turn-9", "answer_mode": "ai_generated",
                                "answered_by": "model", RECEIPT_PLAN: plan}, served.engine, served.profile)
    assert len(out["receipt"]) <= MAX_RECEIPT_CHARS
    assert post(client, "ana", {"receipt": out["receipt"], "verdict": "confirmed"}).status_code == 202
    [kept] = records(served)
    assert (kept["answer_mode"], kept["answered_by"], kept["plan"]) == ("ai_generated", "model", None)


def test_a_receipt_keeps_its_whole_plan_where_it_fits(served):
    from kge.services.ask.engine.planpublic import RECEIPT_PLAN
    plan = long_plan(MEMBERS[:8], filters=2)
    out = service.with_receipt({"behaviour": "figure", "answer_id": "turn-9", "answer_mode": "certified",
                                "answered_by": "rules", RECEIPT_PLAN: plan}, served.engine, served.profile)
    assert service.receipts(served.engine, served.profile).open(out["receipt"])["plan"] == plan
