"""The structured-call helper every Ask model call goes through (#561): kge/services/ask/engine/modelcall.py.

    .venv/bin/python -m pytest tests/unit/test_model_call.py -q

A model with structured() is sent a ModelRequest: the system instruction, the data as titled sections with the question
last, and the contract's schema, its ids one enum. A model with json() alone is sent the request's legacy single-text
form, as one argument. A reply that fails the contract is corrected in the same conversation: the previous contract as
the model's turn, then the errors with the choices allowed as the user's. A reply cut off at its budget adds no model
turn and is retried only while thinking less leaves the contract more room. At most 1 + ASK_MODEL_CORRECTIONS calls.
Small synthetic contracts and stand-in models only; nothing leaves the process.
"""
from __future__ import annotations

import json
from typing import Literal

import pytest

from kge.adapters.model import NotJSON, Truncated
from kge.ports import ModelReply, ModelRequest
from kge.services.ask.engine import modelcall, runtrace
from kge.adapters.model.providers import provider_schema
from kge.services.ask.engine.modelcall import (FILLER, Contract, ContractFailure, Correction, FieldError, ReadingId, VocabId,
                                                allowed_for, contract_schema, correction_text, render, structured_call,
                                                structured_retry, turns)

IDS = ("u.revenue", "u.deposits", "cal.fiscal")


class Choice(Contract):
    kind: Literal["sum", "avg"]
    target: VocabId


class Picks(Contract):
    picks: tuple[VocabId, ...] = ()
    reading: ReadingId | None = None
    choice: Choice | None = None


class JsonOnly:
    """A test double that offers json(self, prompt) alone, as many of the suite's doubles do."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def json(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, BaseException):
            raise reply
        return reply


class Structured:
    """A stand-in with structured(): records each request and answers with the next scripted reply or exception."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def structured(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return ModelReply(data=reply, text=json.dumps(reply), replay={"parts": [{"text": json.dumps(reply)}]})


def call(model, **over):
    args = dict(version="test-v1", instructions="Pick the ids.", data={"Vocabulary": list(IDS), "Empty": []},
                question="Total revenue?", contract=Picks, ids=IDS, legacy_prompt="test-v1\nthe legacy text")
    return {**args, **over}


def test_render_puts_the_question_last_after_every_data_section_and_skips_empty_ones():
    text = render({"Vocabulary": ["a", "b"], "Notes": "plain text", "Nothing": [], "None": None, "Facts": {"x": 1}}, "Why?")
    assert text == '## Vocabulary\n["a","b"]\n\n## Notes\nplain text\n\n## Facts\n{"x":1}\n\n## Question\nWhy?'
    assert text.rindex("## Question") > text.rindex("## Facts")


def test_render_writes_values_json_cannot_hold_as_text_not_python_repr():
    import datetime
    assert render({"Facts": {"when": datetime.date(2025, 6, 30)}}, "Q") == '## Facts\n{"when":"2025-06-30"}\n\n## Question\nQ'


def test_the_contract_schema_sends_each_id_as_a_plain_string_with_no_enum_and_no_titles():
    """#561: Vertex AI inlines every $ref, so an id enum named in every id field multiplied the schema; the ids stay in
    the data, and validation enforces them."""
    schema = contract_schema(Picks, IDS)
    assert schema["properties"]["picks"]["items"] == {"type": "string", "minLength": 1, "x-ids": "vocabulary"}
    assert "VocabularyId" not in schema.get("$defs", {}) and "ReadingId" not in schema.get("$defs", {})
    assert "enum" not in json.dumps(schema["properties"]["picks"]) and "u.revenue" not in json.dumps(schema)
    assert schema["properties"]["picks"]["maxItems"] == modelcall.MAX_CONTRACT_ITEMS
    assert schema["additionalProperties"] is False
    assert '"title"' not in json.dumps(schema)
    assert "x-ids" not in json.dumps(provider_schema(schema, "vertex-gemini"))


def test_with_or_without_ids_the_schema_is_the_same():
    assert contract_schema(Picks) == contract_schema(Picks, IDS)


def test_a_property_named_title_is_kept_while_schema_titles_are_stripped():
    class Titled(Contract):
        title: str

    schema = contract_schema(Titled)
    assert "title" in schema["properties"] and "title" not in schema["properties"]["title"] and "title" not in schema


def test_allowed_for_walks_items_refs_and_union_branches_and_lists_the_ids_shown_at_an_id_field():
    schema = contract_schema(Picks, IDS)
    assert allowed_for(schema, "/picks/0", IDS) == tuple(sorted(IDS))
    assert allowed_for(schema, "/choice/target", IDS) == tuple(sorted(IDS))
    assert allowed_for(schema, "/choice/kind", IDS) == ("sum", "avg")
    assert allowed_for(schema, "/reading", IDS) == (*sorted(IDS), FILLER)
    assert allowed_for(schema, "/nowhere", IDS) == ()
    assert allowed_for(schema, "/picks/0") == ()


def test_a_json_only_double_is_sent_the_legacy_prompt_as_its_one_argument():
    model = JsonOnly({"picks": ["u.revenue"]})
    outcome = structured_call(model, "plan", **call(model))
    assert model.prompts == ["test-v1\nthe legacy text"]
    assert outcome.value == Picks(picks=("u.revenue",)) and outcome.errors == ()


def test_a_structured_model_is_sent_a_request_with_the_question_last_and_no_id_enum():
    model = Structured({"picks": ["u.revenue"]})
    structured_call(model, "plan", **call(model))
    request = model.requests[0]
    assert request.call == "plan" and request.version == "test-v1" and request.instructions == "Pick the ids."
    assert request.content.endswith("## Question\nTotal revenue?") and "## Empty" not in request.content
    assert request.schema["properties"]["picks"]["items"]["type"] == "string" and "enum" not in json.dumps(request.schema["properties"]["picks"])
    assert request.json_mode is False
    assert request.contract == "Picks" and request.turns == () and request.attempt == 0 and request.less_thinking == 0
    assert request.legacy_prompt == "test-v1\nthe legacy text"


def test_an_id_outside_those_shown_is_a_field_error_listing_the_allowed_ids():
    outcome = structured_call(JsonOnly({"picks": ["u.revenue", "u.profit"]}), "plan", **call(None))
    assert outcome.value is None
    assert outcome.errors[0].field == "/picks/1" and "not a declared id" in outcome.errors[0].error
    assert outcome.errors[0].allowed == tuple(sorted(IDS))
    assert outcome.problem.startswith("/picks/1: ")


def test_a_reading_may_be_filler_but_not_an_undeclared_id():
    assert structured_call(JsonOnly({"reading": FILLER}), "plan", **call(None)).value == Picks(reading=FILLER)
    assert structured_call(JsonOnly({"reading": "u.other"}), "plan", **call(None)).errors[0].field == "/reading"


def test_a_literal_error_lists_its_choices_and_an_extra_field_is_refused():
    outcome = structured_call(JsonOnly({"choice": {"kind": "max", "target": "u.revenue"}, "sql": "x"}), "plan", **call(None))
    fields = {e.field: e for e in outcome.errors}
    assert fields["/choice/kind"].allowed == ("sum", "avg") and "/sql" in fields


def test_a_reply_that_is_not_json_is_an_outcome_not_an_exception():
    outcome = structured_call(JsonOnly(NotJSON("model reply held no JSON object")), "plan", **call(None))
    assert outcome.value is None and outcome.errors == (FieldError("", "The reply is not one JSON object."),)
    assert isinstance(outcome.cause, NotJSON)


def test_a_truncated_reply_is_an_outcome_with_its_room():
    exc = Truncated(16384, "ASK_PLAN_MAX_TOKENS")
    exc.more_room = True
    outcome = structured_call(Structured(exc), "plan", **call(None))
    assert outcome.truncated and outcome.more_room and outcome.value is None and "16384-token budget" in outcome.problem


def test_any_other_failure_propagates():
    with pytest.raises(RuntimeError, match="boom"):
        structured_call(Structured(RuntimeError("boom")), "plan", **call(None))


def test_prepare_runs_before_validation_and_a_legacy_reply_is_returned_raw():
    class Legacy(Picks):
        @classmethod
        def legacy(cls, raw):
            return "kind" in raw

    prepared = structured_call(JsonOnly({"picks": ["revenue"]}), "plan",
                               **call(None, prepare=lambda raw: {"picks": ["u." + p for p in raw["picks"]]}))
    assert prepared.value == Picks(picks=("u.revenue",))
    raw = {"kind": "abstain", "spans": []}
    assert structured_call(JsonOnly(raw), "plan", **call(None, contract=Legacy)).value == raw
    assert structured_call(JsonOnly({"anything": 1}), "plan", **call(None, validate=False)).value == {"anything": 1}


def test_a_correction_carries_the_previous_contract_then_the_errors_with_their_choices():
    bad = {"picks": ["u.profit"]}
    model = Structured(bad, {"picks": ["u.revenue"]})
    first = structured_call(model, "plan", **call(None))
    correction = Correction(previous=first.text, replay=first.replay, errors=first.errors)
    second = structured_call(model, "plan", **call(None, corrections=(correction,)))
    assert second.value == Picks(picks=("u.revenue",))
    request = model.requests[1]
    assert request.attempt == 1 and len(request.turns) == 2
    previous, errors = request.turns
    assert previous.role == "model" and previous.text == json.dumps(bad) and previous.replay == {"parts": [{"text": json.dumps(bad)}]}
    assert errors.role == "user" and errors.text.startswith("Your previous reply did not pass these checks.")
    assert "- /picks/0: Value error, not a declared id Allowed: cal.fiscal, u.deposits, u.revenue" in errors.text
    assert request.content == model.requests[0].content


def test_a_truncated_correction_adds_no_model_turn_and_appends_its_note():
    model = Structured({"picks": []})
    structured_call(model, "plan", **call(None, corrections=(Correction(truncated=True),)))
    request = model.requests[0]
    assert request.turns == () and request.less_thinking == 1
    assert request.content.endswith("## Question\nTotal revenue?\n\n" + modelcall.TRUNCATED_NOTE)
    later = turns((Correction(previous="{}", errors=(FieldError("", "bad"),)), Correction(truncated=True)))
    assert [t.role for t in later] == ["model", "user"] and later[1].text.endswith(modelcall.TRUNCATED_NOTE)


def test_correction_text_without_a_field_or_choices():
    text = correction_text(Correction(errors=(FieldError("", "The reply is not one JSON object."),)))
    assert text.splitlines()[1] == "- The reply is not one JSON object."


def test_field_errors_round_trip():
    e = FieldError("/picks/0", "not a declared id", ("a", "b"))
    assert FieldError.load(e.dump()) == e


@pytest.fixture
def attempts(monkeypatch):
    seen = []
    monkeypatch.setattr(runtrace, "model_attempt", lambda *a, **k: seen.append(a))
    monkeypatch.delenv("ASK_MODEL_CORRECTIONS", raising=False)
    return seen


def test_structured_retry_makes_at_most_three_calls(attempts):
    model = Structured(*[{"picks": ["u.profit"]}] * 5)
    with pytest.raises(ContractFailure, match="/picks/0") as failure:
        structured_retry(model, "plan", **call(None))
    assert len(model.requests) == 3 and [r.attempt for r in model.requests] == [0, 1, 2]
    assert len(model.requests[2].turns) == 4
    assert [a[:3] for a in attempts] == [("plan", 0, "contract"), ("plan", 1, "contract"), ("plan", 2, "contract")]
    assert failure.value.outcome.errors[0].field == "/picks/0"


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_structured_retry_makes_one_plus_limit_calls(attempts, limit):
    model = Structured(*[{"picks": ["u.profit"]}] * 5)
    with pytest.raises(ContractFailure):
        structured_retry(model, "plan", limit=limit, **call(None))
    assert len(model.requests) == 1 + limit


@pytest.mark.parametrize("setting, calls", [("0", 1), ("1", 2), ("2", 3)])
def test_the_setting_sets_the_corrections(attempts, monkeypatch, setting, calls):
    monkeypatch.setenv("ASK_MODEL_CORRECTIONS", setting)
    model = Structured(*[{"picks": ["u.profit"]}] * 5)
    with pytest.raises(ContractFailure):
        structured_retry(model, "plan", **call(None))
    assert len(model.requests) == calls


def test_structured_retry_returns_the_corrected_contract(attempts):
    model = Structured({"picks": ["u.profit"]}, {"picks": ["u.revenue"]})
    assert structured_retry(model, "plan", **call(None)) == Picks(picks=("u.revenue",))
    assert len(model.requests) == 2 and len(attempts) == 1


def test_a_truncation_with_room_is_retried_with_less_thinking(attempts):
    exc = Truncated(16384, "ASK_PLAN_MAX_TOKENS")
    exc.more_room = True
    model = Structured(exc, {"picks": ["u.revenue"]})
    assert structured_retry(model, "plan", **call(None)) == Picks(picks=("u.revenue",))
    assert [r.less_thinking for r in model.requests] == [0, 1] and attempts[0][:3] == ("plan", 0, "truncated")


def test_a_truncation_with_no_room_stops_without_a_retry(attempts):
    exc = Truncated(16384, "ASK_PLAN_MAX_TOKENS")
    exc.more_room = False
    model = Structured(exc, {"picks": ["u.revenue"]})
    with pytest.raises(ContractFailure, match="16384-token budget") as failure:
        structured_retry(model, "plan", **call(None))
    assert len(model.requests) == 1 and failure.value.__cause__ is exc
    assert modelcall.failure_of(failure.value) is exc


def test_a_json_only_double_gets_one_call_as_a_retry_would_repeat_its_prompt(attempts):
    model = JsonOnly(*[NotJSON("model reply held no JSON object")] * 3)
    with pytest.raises(ContractFailure) as failure:
        structured_retry(model, "classify", **call(None))
    assert len(model.prompts) == 1 and type(modelcall.failure_of(failure.value)) is NotJSON


def test_a_model_that_takes_the_single_text_form_alone_gets_one_call(attempts):
    model = Structured(*[{"picks": ["u.profit"]}] * 3)
    model.single_text = True
    with pytest.raises(ContractFailure):
        structured_retry(model, "plan", **call(None))
    assert len(model.requests) == 1


def test_the_attempts_are_traced_in_an_open_trace(monkeypatch, tmp_path):
    monkeypatch.delenv("ASK_MODEL_CORRECTIONS", raising=False)
    model = Structured({"picks": ["u.profit"]}, {"picks": ["u.revenue"]})
    with runtrace.start(str(tmp_path / "trace")) as trace:
        structured_retry(model, "plan", **call(None))
        answer = trace.finish({})
    assert answer["model_attempts"][0]["call"] == "plan" and answer["model_attempts"][0]["outcome"] == "contract"


def test_a_prose_reply_goes_back_as_the_models_own_turn_never_an_empty_one():
    prose = NotJSON("model reply held no JSON object", reply_text="It is revenue.", replay={"parts": [{"text": "It is revenue."}]})
    model = Structured(prose, {"picks": ["u.revenue"]})
    first = structured_call(model, "plan", **call(None))
    assert (first.text, first.replay) == ("It is revenue.", {"parts": [{"text": "It is revenue."}]})
    correction = Correction(previous=first.text, replay=first.replay, errors=first.errors)
    structured_call(model, "plan", **call(None, corrections=(correction,)))
    previous, errors = model.requests[1].turns
    assert (previous.role, previous.text) == ("model", "It is revenue.")
    assert errors.text.endswith("- The reply is not one JSON object.")


def test_a_failed_reply_with_no_text_adds_no_model_turn_and_its_note_joins_the_users():
    model = Structured({"picks": []})
    empty = Correction(previous="", errors=(FieldError("", modelcall.NOT_ONE_OBJECT),))
    structured_call(model, "plan", **call(None, corrections=(empty,)))
    request = model.requests[0]
    assert request.turns == ()
    assert request.content.endswith("## Question\nTotal revenue?\n\n" + correction_text(empty))
    later = turns((Correction(previous="{}", errors=(FieldError("", "bad"),)), empty))
    assert [t.role for t in later] == ["model", "user"] and all(t.text.strip() for t in later)
    assert later[1].text.endswith(correction_text(empty))


def test_with_more_ids_than_a_correction_lists_it_offers_those_nearest_the_reply_and_says_how_many_there_are():
    """#561: the schema no longer lists the ids, so a correction is what steers the model back; with 400 ids it names the
    50 spelled most like the id the model gave, not the first 50 in alphabetical order."""
    ids = tuple(f"u.metric_{i:03d}" for i in range(400)) + ("wealth.net_new_assets",)
    outcome = structured_call(JsonOnly({"picks": ["wealth.nna"]}), "plan", **call(None, ids=ids))
    error = outcome.errors[0]
    assert error.field == "/picks/0" and error.of == len(ids) and len(error.allowed) == modelcall.MAX_ALLOWED
    assert error.allowed[0] == "wealth.net_new_assets"
    text = correction_text(Correction(previous="{}", errors=(error,)))
    assert f"Allowed (the {modelcall.MAX_ALLOWED} of {len(ids)} nearest your reply; every one is in the data shown): " \
           "wealth.net_new_assets" in text
    assert FieldError.load(error.dump()) == error


def test_a_short_list_of_choices_is_given_whole_and_says_nothing_of_a_count():
    error = structured_call(JsonOnly({"picks": ["u.profit"]}), "plan", **call(None)).errors[0]
    assert error.of == 0 and "of" not in error.dump()
    assert "Allowed: " + ", ".join(sorted(IDS)) in correction_text(Correction(previous="{}", errors=(error,)))
