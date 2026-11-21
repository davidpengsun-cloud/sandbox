"""A1's acceptance (#464, J6) on Meridian's demo, with a stand-in model that replays a fixed plan: never a live model.

Rules first: a full match is served Certified with no model call. With ASK_AI_FALLBACK=on, where the rules do not fully
match, the model's plan is checked and served labelled AI-Generated with its readings, on POST /ask, the MCP ask tool
and a background question run; POST /answer is the rules' answer alone, so it is Certified or not sure there. With
ASK_AI_FALLBACK=off, or with no model, the model is never asked and a rules miss is a clean not sure, with no label.

The 40 rewordings of the 20 golden questions (tests/fixtures/a1_rewordings.json, two per item S06 to S25) are asked with
the fallback on, the stand-in replying with the item's verified example plan (clients/meridian/baselines/examples.json):
each comes back Certified by the rules, its figures the golden baseline's, or labelled AI-Generated; none unlabelled.
With the fallback off each is Certified by the rules or a clean not sure.
The rewordings that do not hold yet are strict expected failures, each with its reason (EXPECTED): one that starts
holding fails until it is taken out.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import json
import time

import pytest

from kge.estate import golden20
from kge.estate.golden_sage import GOLDEN_20
from kge.eval import sets
from kge.eval.run_golden import score
from kge.pipeline.settings import ROOT
from kge.services.ask import service
from kge.services.ask.models import MAX_RECEIPT_CHARS
from kge.services.ask.engine.wording import AI_GENERATED_LABEL, CERTIFIED_LABEL
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Stub
from tests.scratch import BUILD

WHO = "analyst_enterprise.meridian@example.com"
BY_REGION = "NNA for Q1 2025 by region"
TAKINGS = "Retail takings for June 2025"
MINUS = "certified NNA minus current NNA for March 2025"
FIELDS = ("answer_mode", "answered_by", "answer_id", "ai_fallback", "model")
ANSWERS = ("figure", "combined")
REWORDINGS = json.loads((ROOT / "tests/fixtures/a1_rewordings.json").read_text())
EXAMPLES = {e["question"]: e["plan"] for e in json.loads((ROOT / "clients/meridian/baselines/examples.json").read_text())}


class Reading(Stub):
    """The stand-in model: the rules' plan of another question, sent with the readings given."""
    name = "stub:reading"

    def __init__(self, engine=None, question=None, readings=(), proposal=None):
        super().__init__(engine, question=question, proposal=proposal)
        self.readings = list(readings)

    def json(self, prompt, max_tokens=800):
        reply = super().json(prompt, max_tokens)
        if prompt.startswith("query-plan-on-v1\n"):
            return {"reply": reply, "readings": self.readings}
        return reply


@pytest.fixture
def served(client, monkeypatch):
    import kge.app as appmod

    def use(model):
        monkeypatch.setattr(appmod.combined(), "llm", model)
        return model
    return appmod, use


def takings_model(appmod):
    model = Reading(appmod.engine(), question="Total retail revenue for June 2025")
    measure = model.proposal["plan"]["measures"][0]["measure"]["id"]
    model.readings = [{"phrase": "takings", "id": measure}]
    return model


def background(client, question):
    started = client.post("/ask?background=true", json={"question": question}, headers=client.as_(WHO))
    assert started.status_code == 202, started.text
    run = started.json()
    for _ in range(500):
        if run.get("state") != "running":
            break
        time.sleep(0.02)
        run = client.get(f"/ask/runs/{run['id']}", headers=client.as_(WHO)).json()
    assert run.get("answer"), run
    return run["answer"]


def ask(client, path, question):
    if path == "/ask":
        response = client.post("/ask", json={"question": question}, headers=client.as_(WHO))
        assert response.status_code == 200, response.text
        return response.json()
    if path == "mcp ask":
        return mcp_wire(client, "/mcp/answer", WHO, "ask", {"question": question})["structuredContent"]
    return background(client, question)


def labelled(out):
    return out["answer_text"].startswith((CERTIFIED_LABEL, AI_GENERATED_LABEL))


def receipt_of(out):
    """The served receipt opened for the served release: exactly the answer's id, mode, who answered and the plan,
    with no evidence or obligation pointing into the question and no word of it."""
    import kge.app as appmod
    assert out.get("receipt") and len(out["receipt"]) <= MAX_RECEIPT_CHARS, out.get("receipt")
    record = service.receipts(appmod.engine(), appmod.PROFILE).open(out["receipt"])
    assert (record["answer_id"], record["answer_mode"], record["answered_by"]) == (out["answer_id"], out["answer_mode"], out["answered_by"])
    plan = json.dumps(record["plan"])
    assert '"evidence"' not in plan and '"obligations"' not in plan
    assert (record["plan"] is None) is (out["behaviour"] not in ANSWERS), record["plan"]
    return record


@pytest.mark.parametrize("path", ["/ask", "/answer", "mcp ask", "question run"])
def test_by_region_is_certified_by_the_rules_with_no_model_call(client, served, path):
    appmod, use = served
    model = use(Reading(appmod.engine(), question="Total retail revenue for June 2025"))
    if path == "/answer":
        out = client.post("/answer", json={"question": BY_REGION}, headers=client.as_(WHO)).json()
    else:
        out = ask(client, path, BY_REGION)
    assert model.prompts == []
    assert out["behaviour"] in ANSWERS, out.get("reason")
    assert (out["answer_mode"], out["answered_by"], out["model"]) == ("certified", "rules", "none")
    assert out["answer_text"].startswith(CERTIFIED_LABEL)
    assert out["answer_id"]
    receipt_of(out)
    assert out["order"] == "rules_first"


def test_the_agent_labels_its_first_turn_figure_as_ask_does(client):
    out = client.post("/agent", json={"question": BY_REGION, "arm": "treatment"}, headers=client.as_(WHO)).json()
    assert out["behaviour"] in ANSWERS, out.get("reason")
    assert out["answer_mode"] == "certified" and out["answer_text"].startswith(CERTIFIED_LABEL)
    assert not out["answer_text"].startswith(CERTIFIED_LABEL * 2)
    assert out["order"] == "rules_first"


@pytest.mark.parametrize("path", ["/ask", "mcp ask", "question run"])
def test_with_the_fallback_on_a_rules_miss_is_answered_ai_generated_with_its_readings(client, served, path):
    appmod, use = served
    model = use(takings_model(appmod))
    out = ask(client, path, TAKINGS)
    assert [p.split("\n", 1)[0] for p in model.prompts] == ["query-plan-on-v1"]
    assert out["behaviour"] in ANSWERS, out.get("reason")
    assert (out["answer_mode"], out["answered_by"], out["ai_fallback"], out["model"]) == ("ai_generated", "model", "on", model.name)
    assert out["answer_text"].startswith(AI_GENERATED_LABEL)
    assert any(n.startswith("read 'takings' as ") for n in out.get("notes") or []), out.get("notes")
    assert out["answer_id"]
    assert "takings" not in json.dumps(receipt_of(out)["plan"]).casefold()


def test_answer_is_the_rules_alone_so_a_rules_miss_is_not_sure_there_with_no_label(client, served):
    appmod, use = served
    model = use(takings_model(appmod))
    out = client.post("/answer", json={"question": TAKINGS}, headers=client.as_(WHO)).json()
    assert model.prompts == []
    assert out["answer_mode"] == "not_sure" and out["behaviour"] not in ANSWERS and not labelled(out)
    receipt_of(out)


@pytest.mark.parametrize("path", ["/ask", "mcp ask", "question run"])
@pytest.mark.parametrize("question", [TAKINGS, MINUS])
def test_with_the_fallback_off_the_model_is_never_asked_and_a_rules_miss_declines_cleanly(client, served, monkeypatch, path, question):
    appmod, use = served
    monkeypatch.setenv("ASK_AI_FALLBACK", "off")
    model = use(takings_model(appmod))
    out = ask(client, path, question)
    assert model.prompts == []
    assert (out["answer_mode"], out["answered_by"], out["ai_fallback"], out["model"]) == ("not_sure", "rules", "off", "none")
    assert out["behaviour"] not in ANSWERS and not labelled(out)
    receipt_of(out)


@pytest.mark.parametrize("question", [TAKINGS, MINUS, BY_REGION])
def test_a_deployment_with_no_model_is_off_and_never_labels_a_decline(client, served, question):
    appmod, use = served
    use(None)
    out = ask(client, "/ask", question)
    assert out["ai_fallback"] == "off" and out["model"] == "none"
    if question == BY_REGION:
        assert out["answer_mode"] == "certified" and out["answer_text"].startswith(CERTIFIED_LABEL)
    else:
        assert out["answer_mode"] == "not_sure" and out["behaviour"] not in ANSWERS and not labelled(out)
    receipt_of(out)


@pytest.mark.parametrize("mode", ["certified", "ai_generated", "not_sure"])
def test_feedback_accepts_the_receipt_an_answer_was_served_with_and_refuses_a_changed_one(client, served, monkeypatch, mode):
    appmod, use = served
    monkeypatch.delenv("STORE_URL", raising=False)
    if mode == "not_sure":
        monkeypatch.setenv("ASK_AI_FALLBACK", "off")
    use(takings_model(appmod))
    out = ask(client, "/ask", BY_REGION if mode == "certified" else TAKINGS)
    assert out["answer_mode"] == mode
    receipt_of(out)
    accepted = client.post("/ask/feedback", json={"receipt": out["receipt"], "verdict": "confirmed"}, headers=client.as_(WHO))
    assert accepted.status_code == 202, accepted.text
    changed = out["receipt"][:-4] + ("AAAA" if not out["receipt"].endswith("AAAA") else "BBBB")
    refused = client.post("/ask/feedback", json={"receipt": changed, "verdict": "confirmed"}, headers=client.as_(WHO))
    assert refused.status_code == 400, refused.text


FIRST, FOLLOW_UP = "NNA for June 2025", "And for April 2025?"


def follow_up(client):
    first = client.post("/ask", json={"question": FIRST}, headers=client.as_(WHO)).json()
    assert first["answer_mode"] == "certified", first.get("reason")
    response = client.post("/ask", json={"question": FOLLOW_UP, "history": first["history"]}, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    return response.json()


def test_with_a_model_configured_and_the_fallback_off_a_follow_up_the_rules_answer_is_certified(client, served, monkeypatch):
    appmod, use = served
    monkeypatch.setenv("ASK_AI_FALLBACK", "off")
    model = use(takings_model(appmod))
    out = follow_up(client)
    assert model.prompts == []
    assert out["behaviour"] in ANSWERS, out.get("reason")
    assert (out["answer_mode"], out["answered_by"], out["model"]) == ("certified", "rules", "none")
    assert out["answer_text"].startswith(CERTIFIED_LABEL)


def test_with_the_fallback_on_a_follow_up_the_model_cannot_plan_is_the_rules_floor_labelled_ai_generated(client, served):
    appmod, use = served
    model = use(Reading(proposal={"kind": "abstain", "spans": [], "missing": ["No plan."]}))
    out = follow_up(client)
    assert [p.split("\n", 1)[0] for p in model.prompts] == ["query-plan-on-v1"]
    assert out["behaviour"] in ANSWERS, out.get("reason")
    assert (out["answer_mode"], out["answered_by"]) == ("ai_generated", "rules")
    assert out["answer_text"].startswith(AI_GENERATED_LABEL)


HOSTILE = "DROP TABLE x; --"
BROKERAGE = "NNA for June 2025 from BROKERAGE"


@pytest.mark.parametrize("hostile", ["unknown id", "member value"])
def test_with_the_fallback_on_a_hostile_model_plan_is_never_served(client, served, hostile):
    appmod, use = served
    model = takings_model(appmod) if hostile == "unknown id" else Reading(appmod.engine(), question=BROKERAGE)
    plan = model.proposal["plan"]
    if hostile == "unknown id":
        plan["measures"][0]["measure"]["id"] = "secret.metric"
    else:
        member = next(f for f in plan["filters"] if f["kind"] == "compare" and f["left"]["kind"] == "column")
        member["value"] = {**member["value"], "value": HOSTILE}
    use(model)
    out = ask(client, "/ask", TAKINGS)
    assert model.prompts, "the model was asked"
    assert out["answer_mode"] == "not_sure" and out["behaviour"] not in ANSWERS and not labelled(out), out.get("answer_text")
    assert HOSTILE not in json.dumps(out) and "secret.metric" not in json.dumps({k: v for k, v in out.items() if k != "trace"})
    receipt_of(out)


def golden_items():
    held = sets.items(BUILD / "sage_v1.json")
    baseline = json.loads(golden20.BASELINE.read_text())["items"]
    return {i["id"]: {**i, "expect": {**i["expect"], "table": golden20.table(baseline[i["id"]])}}
            for i in held if i.get("category") == GOLDEN_20 and not i.get("variant_of")}


ITEMS = golden_items()
CASES = [(row["id"], n, phrasing) for row in REWORDINGS for n, phrasing in enumerate(row["phrasings"])]
WRONG = "certified by the rules with figures other than the golden baseline's"
SHAPE = ("not scored as the golden item: the rewording asks for SPS's part alone, served with Retail Investor and SPS "
         "applied as USD 1,069,674.05, the baseline's SPS row; S09's golden answer is the SPS and non-SPS breakdown")
EXPECTED = {
    ("S09", 0, "on"): SHAPE,
    ("S09", 0, "off"): SHAPE,
    ("S21", 0, "on"): WRONG + ": a weekday breakdown in place of the cumulative total",
    ("S21", 0, "off"): WRONG + ": a weekday breakdown in place of the cumulative total",
}


def cases():
    out = []
    for fallback in ("on", "off"):
        for item_id, n, question in CASES:
            reason = EXPECTED.get((item_id, n, fallback))
            marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)] if reason else []
            out.append(pytest.param(fallback, item_id, question, marks=marks, id=f"{item_id}-{n}-{fallback}"))
    return out


def test_the_rewordings_are_two_per_golden_item_each_with_its_verified_example():
    assert len(CASES) == 40 and sorted({i for i, _, _ in CASES}) == sorted(ITEMS) == [f"S{n:02d}" for n in range(6, 26)]
    assert all(ITEMS[i]["question"] in EXAMPLES for i in ITEMS)


@pytest.mark.parametrize("fallback,item_id,question", cases())
def test_each_rewording_is_certified_or_labelled_ai_generated_and_never_unlabelled(client, served, monkeypatch, fallback, item_id, question):
    appmod, use = served
    monkeypatch.setenv("ASK_AI_FALLBACK", fallback)
    item = ITEMS[item_id]
    model = use(Reading(proposal={"kind": "propose", "base_turn": None, "plan": EXAMPLES[item["question"]]}))
    out = ask(client, "/ask", question)
    mode = out["answer_mode"]
    receipt_of(out)
    if mode == "certified":
        assert (out["answered_by"], out["model"]) == ("rules", "none") and model.prompts == []
        assert out["answer_text"].startswith(CERTIFIED_LABEL)
        assert score(item, out)[0], f"certified with figures other than the golden baseline's: {out.get('answer_text')}"
        return
    if fallback == "on":
        assert mode == "ai_generated", (mode, out.get("reason"))
        assert (out["answered_by"], out["model"]) == ("model", model.name)
        assert out["behaviour"] in ANSWERS and out["answer_text"].startswith(AI_GENERATED_LABEL)
    else:
        assert mode == "not_sure" and model.prompts == []
        assert out["behaviour"] not in ANSWERS and not labelled(out)


@pytest.mark.parametrize("path", ["/ask", "mcp ask", "question run"])
def test_a_decline_made_before_the_planner_carries_a_receipt_feedback_accepts(client, served, monkeypatch, path):
    monkeypatch.delenv("STORE_URL", raising=False)
    out = ask(client, path, "What is the weather in Lisbon tomorrow?")
    assert out["behaviour"] not in ANSWERS and out["answer_mode"] == "not_sure"
    receipt_of(out)
    accepted = client.post("/ask/feedback", json={"receipt": out["receipt"], "verdict": "not_what_i_meant"}, headers=client.as_(WHO))
    assert accepted.status_code == 202, accepted.text
