"""A readable answer for every behaviour (#102 §1): /ask and /answer return answer_text and phrased_by on each answer.

    .venv/bin/python -m pytest tests/integration/test_answer_text.py -q

Every golden, held-out and golden_wealth_v1 question, asked through POST /ask as the A/B harness asks it (the item's own
principal, else the deployment's asker) and as two principals who read one unit each: answer_text is never empty, every
number in it is one the answer holds, every [n] names one of its citations, and no refusal or abstention names a unit
the caller may not read, nor a table or column only such a unit holds. The model path, with a stub that misbehaves as
the mock's adversarial modes do: the template stands on the local stack, and the cloud path is a 503.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import re
import anyio
import pytest
from fastapi.testclient import TestClient

from kge.adapters.model import NotJSON
from kge.eval import sets
from kge.eval.run_golden import asked_as
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.wording import (ABSTENTIONS, BEHAVIOURS, REFUSALS, Hidden, bad_markers, protected_values,
                                             stray_numbers)
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.models import AnswerResponse, AskResponse
from tests.integration.test_combined import Q, Stub
from tests.scratch import BUILD, profile_of, scratch_build

SETS = ("golden", "heldout", "golden_wealth_v1")
NARROW = ("analyst_retail", "advisor_wealth")
GOLDEN = "Total retail revenue for June 2025"
REFUSAL = "Combined Retail and SME revenue for June 2025"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import kge.app as appmod
    build = scratch_build(tmp_path_factory.mktemp("answer_text") / "build")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MODEL_PROVIDER", "none")
        for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None),
                            ("_ids", None)):
            mp.setattr(appmod, name, value)
        with TestClient(appmod.app) as c:
            from kge.services.identity.service import token_for
            prof = profile_of(build)
            c.as_ = lambda who: {"authorization": f"Bearer {token_for(prof.run_dir(), who)}"}
            c.asker = prof.asking()
            yield c
        appmod._engine.close() if appmod._engine else None


@pytest.fixture(scope="module")
def answers(client):
    """(set, id, principal) -> the /ask answer, for every question of the three sets, as the harness asks it and as
    each narrow principal; and the combined answer's worked example (Q), which no set holds."""
    out = {}
    for s in SETS:
        for item in sets.items(BUILD / f"{s}.json"):
            for who in dict.fromkeys([asked_as(item, client.asker), *NARROW]):
                r = client.post("/ask", json={"question": item["question"]}, headers=client.as_(who))
                assert r.status_code == 200, (s, item["id"], who, r.text)
                out[(s, item["id"], who)] = r.json()
    for who in NARROW:
        r = client.post("/ask", json={"question": Q}, headers=client.as_(who))
        assert r.status_code == 200, (Q, who, r.text)
        out[("combined", "Q", who)] = r.json()
    return out


def mcp_wire(client, path: str, who: str, name: str, arguments: dict) -> dict:
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    headers = {**client.as_(who), "accept": "application/json, text/event-stream", "host": "localhost:18930"}
    response = client.post(path, json=request, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def without_run_details(answer: dict) -> dict:
    return {key: value for key, value in answer.items() if key not in {"run_trace", "trace"}}


@pytest.mark.parametrize("who,question", [
    ("analyst_enterprise", GOLDEN),
    ("analyst_retail", REFUSAL),
])
def test_mcp_answer_is_post_ask_as_the_same_principal(client, who, question):
    body = {"question": question}
    http = client.post("/ask", json=body, headers=client.as_(who))
    assert http.status_code == 200, http.text
    over_mcp = mcp_wire(client, "/mcp/answer", who, "ask", body)["structuredContent"]
    assert without_run_details(over_mcp) == without_run_details(http.json())


def test_every_mcp_server_refuses_unknown_arguments_over_the_transport(client):
    from kge.services.ask.mcp import answer_mcp_server
    listed = anyio.run(answer_mcp_server().list_tools)
    tool_schema = next(item.inputSchema for item in listed if item.name == "ask")
    assert "principal" not in tool_schema.get("properties", {})
    calls = [
        ("/mcp", "analyst_retail", "get_context", {"question": GOLDEN, "who": "analyst_enterprise"}),
        ("/mcp/answer", "analyst_retail", "ask", {"question": GOLDEN, "principal": "analyst_enterprise"}),
        ("/mcp/onboard", "business_team", "onboard_status", {"principal": "analyst_enterprise"}),
    ]
    for path, who, name, arguments in calls:
        result = mcp_wire(client, path, who, name, arguments)
        assert result["isError"] is True and "422: " in result["content"][0]["text"], (path, result)


def test_mcp_wrong_type_has_the_http_validation_detail_over_the_transport(client):
    http = client.post("/ask", json={"question": 123}, headers=client.as_("analyst_retail"))
    assert http.status_code == 422
    result = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", {"question": 123})
    assert result["isError"] is True
    assert result["content"][0]["text"] == f"422: {http.json()['detail']}"


def test_mcp_answer_with_no_token_is_refused(client):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    response = client.post("/mcp/answer", json=init, headers={"accept": "application/json, text/event-stream"})
    assert response.status_code == 401


def test_every_answer_has_readable_text_that_holds_only_its_own_numbers_and_markers(answers):
    assert len(answers) > 250
    seen = set()
    for key, a in answers.items():
        AskResponse.model_validate(a)
        seen.add(a["behaviour"])
        assert a["answer_text"].strip(), key
        assert a["phrased_by"] == "template", (key, a["phrased_by"])
        assert not stray_numbers(a), (key, a["answer_text"], stray_numbers(a))
        assert not bad_markers(a), (key, a["answer_text"], bad_markers(a))
    assert {"combined", "figure", "refuse", "blocked_access", "abstain", "cite_certified"} <= seen, seen
    assert seen <= set(BEHAVIOURS), seen - set(BEHAVIOURS)


def test_a_figure_says_its_value_column_and_period(answers):
    for key, a in answers.items():
        if a["behaviour"] == "figure" and a.get("figure") is not None and a.get("column"):
            assert a["column"] in a["answer_text"], (key, a["answer_text"])
            value = a["figure"]
            shown = f"{value:,.0f}" if float(value).is_integer() else f"{value:,}"
            assert shown in a["answer_text"], (key, a["answer_text"])
    combined = [a for a in answers.values() if a["behaviour"] == "combined"]
    assert combined and all(re.search(r"\[\d+\]", a["answer_text"]) for a in combined)


def test_allowed_words_are_not_withheld(client, answers):
    assert "Client-Directed NNA" in answers[("golden_wealth_v1", "W02", "advisor_wealth")]["answer_text"]
    g043 = answers[("golden", "G043", "advisor_wealth")]
    assert "Customer Attrition" in g043["answer_text"], (g043.get("term"), g043.get("definition"), g043.get("reason"), list(g043))
    aum = client.post("/ask", json={"question": "What is AUM?"}, headers=client.as_("analyst_retail")).json()
    assert "market value" in aum["answer_text"].lower() and "(withheld)" not in aum["answer_text"]
    for key, word in {
        ("golden_wealth_v1", "W19", "advisor_wealth"): "customer",
        ("golden_wealth_v1", "XB6", "advisor_wealth"): "customer",
        ("golden", "G028", "advisor_wealth"): "customer",
        ("golden", "G024", "analyst_retail"): "contact",
        ("golden", "G032", "analyst_retail"): "contact",
        ("heldout", "H009", "analyst_retail"): "household",
    }.items():
        answer = answers[key]
        assert word in answer["answer_text"].lower(), (key, answer["answer_text"])
    for key, answer in answers.items():
        if "(withheld)" in answer["answer_text"]:
            assert answer.get("denied_warehouses") or "(withheld)" in (answer.get("reason") or ""), (
                key, answer["answer_text"])


def test_no_text_names_what_the_caller_may_not_read_and_a_refusal_says_why_and_whom_to_ask_by_role(client, answers):
    """No answer_text names a unit the caller may not read, a table or column only such units hold, or a document of
    one; a refusal or an abstention says why, and names whom to ask by role, never the caller or anyone by name."""
    import kge.app as appmod
    e = appmod.engine()
    hidden = {}
    checked = 0
    for key, a in answers.items():
        who, text = key[2], a["answer_text"]
        said = hidden.setdefault(who, Hidden.of(e, who)).leaks(text, protected_values(a))
        assert not said, (key, text, said)
        if a["behaviour"] not in REFUSALS + ABSTENTIONS:
            continue
        assert text.startswith("Refused: " if a["behaviour"] in REFUSALS else "No answer: "), (key, text)
        assert re.search(r"data steward|business team", text), (key, text)
        assert who not in text, (key, text)
        checked += 1
    assert checked > 50 and any(h.names for h in hidden.values()) and any(h.docs for h in hidden.values())


def test_no_text_names_a_unit_the_caller_may_not_read_by_its_id_or_label(client, answers):
    """Each unit the caller may not read, by its id and label, is absent from both the readable text and its reason."""
    import kge.app as appmod
    e = appmod.engine()
    for key, a in answers.items():
        who = key[2]
        readable = e.g.readable_units(who)
        hidden = [u for u in e.UNIT_LABEL if u not in readable]
        names = {n for u in hidden for n in (u, e.UNIT_LABEL[u])} - {n for u in readable for n in (u, e.UNIT_LABEL[u])}
        said = [n for n in names if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", a["answer_text"], re.I)]
        assert not said, (key, a["answer_text"], said)
        question_names = {n for n in names if re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])",
                                                        a.get("question") or "", re.I)}
        reason = [n for n in names - question_names if re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])",
                                                                 a.get("reason") or "", re.I)]
        assert not reason, (key, a.get("reason"), reason)


def test_a_question_s_unreadable_unit_name_survives_in_its_reason(answers):
    cases = {"advisor_wealth": ("G001", "G002", "G003", "G004", "G022"), "analyst_retail": ("G025",)}
    for principal, item_ids in cases.items():
        for item_id in item_ids:
            answer = answers[("golden", item_id, principal)]
            named = [unit.lower() for unit in re.findall(r"[\w-]+", answer["question"])
                     if unit.lower() in ("retail", "sme", "corporate", "wealth")]
            assert all(unit in answer["reason"].split("may not read ", 1)[1].split(".", 1)[0].lower()
                       for unit in named), (item_id, answer["reason"])


def test_a_named_back_unit_is_protected_only_in_the_may_not_read_span():
    answer = {"question": "Show retail customers", "term": None, "definition": None,
              "reason": "advisor_wealth may not read retail. retail.customer.email and retail.customer.date_of_birth are restricted."}
    hidden = Hidden(["retail.customer.date_of_birth", "retail.customer.email", "retail"], set(), [])
    assert hidden.withheld(answer["reason"], protected_values(answer)) == \
        "advisor_wealth may not read retail. (withheld) and (withheld) are restricted."
    answer["reason"] = "advisor_wealth may not read retail; retail.customer.email stays restricted."
    assert hidden.withheld(answer["reason"], protected_values(answer)) == \
        "advisor_wealth may not read retail; (withheld) stays restricted."


def test_answer_on_every_preset_and_the_tier_rule_words_every_golden_answer(client):
    """/answer (internal) on each preset of the control, and on the tier rule, whose citations are numbered: the same
    contract, with each [n] one of its citations."""
    seen, marked = set(), 0
    arms = [*PRESETS, "graph[citation_rule=tiers]"]
    for item in sets.items(BUILD / "golden.json"):
        for arm in arms:
            body = {"question": item["question"], "arm": arm.split("[")[0]}
            if "[" in arm:
                body["stages"] = {"citation_rule": "tiers"}
            r = client.post("/answer", json=body, headers=client.as_(asked_as(item, client.asker)))
            assert r.status_code == 200, (item["id"], arm, r.text)
            a = r.json()
            AnswerResponse.model_validate(a)
            assert "combined" not in a and a["answer_text"].strip() and a["phrased_by"] == "template", (item["id"], arm)
            assert not stray_numbers(a) and not bad_markers(a), (item["id"], arm, a["answer_text"])
            seen.add(a["behaviour"])
            marked += bool(re.search(r"\[\d+\]", a["answer_text"]))
    assert {"cite", "cite_certified", "sum_with_verdict", "parts_with_reason"} <= seen, seen
    assert marked > 5


class Adversary(Stub):
    """A stub model in one of the mock's adversarial modes, for the phrasing step: a number the result does not hold, a
    marker that names no citation, or a reply that is not JSON."""

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    def json(self, prompt, max_tokens=800):
        if not prompt.startswith("Rewrite"):
            return super().json(prompt, max_tokens)
        if self.mode == "not_json":
            raise NotJSON("model reply held no JSON object")
        if self.mode == "invented_number":
            return {"answer": "176 customers [1], and 9,999 more the month before."}
        return {"answer": "176 customers [1], see also [99]."}


@pytest.fixture()
def adversarial(client, monkeypatch):
    import kge.app as appmod

    def use(mode: str):
        c = Combined(appmod.engine(), llm=Adversary(mode))
        monkeypatch.setattr(appmod, "_combined", c)
        return c
    yield use


RENDERED = {"question": Q, "stages": {"renderer": "template+model"}}


@pytest.mark.parametrize("mode", ["invented_number", "bad_marker", "not_json"])
def test_a_misbehaving_model_leaves_the_template_on_the_local_stack(client, adversarial, mode):
    adversarial(mode)
    r = client.post("/ask", json=RENDERED, headers=client.as_("analyst_retail"))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["behaviour"] == "combined" and a["phrased_by"].startswith("template ("), a["phrased_by"]
    assert not stray_numbers(a) and not bad_markers(a)


@pytest.mark.parametrize("mode", ["invented_number", "bad_marker", "not_json"])
def test_on_the_cloud_path_a_reply_that_is_not_json_is_a_503_and_wording_that_fails_the_checks_is_named(
        client, adversarial, monkeypatch, mode):
    """A failed or non-JSON call stops the answer (NoFallback, a 503), never a silent template; wording the checks
    reject is a call that answered, so the template stands and phrased_by names why."""
    import kge.pipeline.settings as settings_module
    adversarial(mode)
    monkeypatch.setattr(settings_module, "gcp_native", lambda: True)
    r = client.post("/ask", json=RENDERED, headers=client.as_("analyst_retail"))
    if mode == "not_json":
        assert r.status_code == 503, r.text
        assert "phrasing the answer" in r.json()["detail"] and "Traceback" not in r.text
    else:
        assert r.status_code == 200 and r.json()["phrased_by"].startswith("template (model text carried"), r.text
