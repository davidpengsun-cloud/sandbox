"""The glossary classifier and the answer's wording as structured calls (#561): Combined.understand and Combined.phrase.

    .venv/bin/python -m pytest tests/unit/test_combined_contracts.py -q

Each goes through the structured-call helper on the real model adapter, here a vertex-gemini Gemini 3 model whose
transport is stood in, so the request body is the one the pilot would send and nothing leaves the process: the system
instruction, the data with the question last, the contract's schema, no temperature, and the classify and wording
budgets (4,096 reply tokens, thinking capped at 512). A term outside the glossary is refused and corrected in the same
conversation; on the pilot's cloud path a reply that never meets the contract stops the run (NoFallback). Small synthetic
glossaries and facts only.
"""
from __future__ import annotations

import json

import pytest

from kge.adapters.model import NoFallback, providers
from kge.services.ask.engine import combined as combined_module
from kge.services.ask.engine.combined import (GLOSSARY_INSTRUCTIONS, WORDING_INSTRUCTIONS, Combined, glossary_prompt,
                                              wording_prompt)

BACKENDS = ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE")
STEERING = ("MODEL_MAX_TOKENS", "MODEL_HIGH_MAX_TOKENS", "MODEL_MEDIUM_MAX_TOKENS", "MODEL_LOW_MAX_TOKENS",
            "ASK_PLAN_MAX_TOKENS", "ASK_CLASSIFY_MAX_TOKENS", "ASK_WORDING_MAX_TOKENS",
            "ASK_PLAN_THINKING_TOKENS", "ASK_CLASSIFY_THINKING_TOKENS", "ASK_WORDING_THINKING_TOKENS",
            "ASK_PLAN_THINKING_LEVEL", "ASK_CLASSIFY_THINKING_LEVEL", "ASK_WORDING_THINKING_LEVEL",
            "MODEL_THINKING_TOKENS", "MODEL_HIGH_THINKING_TOKENS", "MODEL_MEDIUM_THINKING_TOKENS", "MODEL_LOW_THINKING_TOKENS",
            "MODEL_THINKING_LEVEL", "MODEL_HIGH_THINKING_LEVEL", "MODEL_MEDIUM_THINKING_LEVEL", "MODEL_LOW_THINKING_LEVEL",
            "ASK_MODEL_CORRECTIONS", "ASK_UNKNOWN_WORD", "MODEL_SEED")
QUESTION = "What does deposits mean for June 2025?"
CALENDARS = {"fiscal": {"starts": "04-01"}}


@pytest.fixture(autouse=True)
def plain(monkeypatch):
    """No budget, thinking or backend setting from the caller's environment steers a test; the local stack."""
    for name in (*STEERING, *BACKENDS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(providers.Chat, "token", lambda self: ("stand-in", "your-project-id"))


@pytest.fixture
def cloud(monkeypatch):
    for name in BACKENDS:
        monkeypatch.setenv(name, "bigquery")
    return monkeypatch


class Engine:
    term_names = ["revenue", "deposits"]
    CALENDARS = CALENDARS


class Config:
    def uses_model(self, stage):
        return True


def gemini(*replies):
    """A vertex-gemini Gemini 3 model on its high tier whose transport answers each call with the next reply, as
    Gemini's JSON response; the bodies it was sent are its .sent."""
    llm = providers.LLM("vertex-gemini", "gemini-3.1-pro-preview", project="your-project-id", region="global", tier="high")
    queue, sent = list(replies), []

    def post(url, headers, body):
        sent.append(json.loads(json.dumps(body)))
        text = json.dumps(queue.pop(0))
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 20, "thoughtsTokenCount": 100},
                "modelVersion": "gemini-3.1-pro-preview"}

    llm._post = post
    llm.sent = sent
    return llm


def combined(llm) -> Combined:
    c = Combined.__new__(Combined)
    c.llm, c.e = llm, Engine()
    c.template = lambda r: "the template's answer"
    return c


ANSWER = {"question": "How much did Retail hold in June 2025?", "unit_label": "Retail", "period": "June 2025",
          "figures": [], "citations": [], "not_cited": [], "withheld": []}


def text_of(content: dict) -> str:
    return "".join(p.get("text") or "" for p in content["parts"])


def test_the_glossary_call_is_structured_with_its_own_budget_and_no_temperature():
    llm = gemini({"terms": ["deposits", "revenue"], "period": {"kind": "month", "value": "2025-06"}})
    assert combined(llm).understand(QUESTION, ["deposits"]) == (["revenue"], {"kind": "month", "value": "2025-06"})
    body = llm.sent[0]
    assert body["systemInstruction"]["parts"][0]["text"] == GLOSSARY_INSTRUCTIONS
    content = text_of(body["contents"][0])
    assert content.startswith("## Glossary terms\n") and content.endswith("## Question\n" + QUESTION)
    assert content.index("## Calendars") < content.index("## Question")
    config = body["generationConfig"]
    assert "temperature" not in config
    assert config["maxOutputTokens"] == 4096 and config["thinkingConfig"] == {"thinkingBudget": 512}
    assert config["responseMimeType"] == "application/json"
    assert '"deposits"' not in json.dumps(config["responseJsonSchema"]) and "deposits" in content


def test_the_system_instruction_holds_no_term_calendar_or_question():
    for word in ("revenue", "deposits", "04-01", QUESTION):
        assert word not in GLOSSARY_INSTRUCTIONS


def test_a_term_outside_the_glossary_is_refused_and_corrected_in_the_same_conversation():
    llm = gemini({"terms": ["profit"]}, {"terms": ["revenue"]})
    assert combined(llm).understand(QUESTION, []) == (["revenue"], None)
    assert len(llm.sent) == 2
    contents = llm.sent[1]["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert text_of(contents[0]) == text_of(llm.sent[0]["contents"][0])
    assert json.loads(text_of(contents[1])) == {"terms": ["profit"]}
    assert "- /terms/0: " in text_of(contents[2]) and "Allowed: deposits, revenue" in text_of(contents[2])


def test_on_the_local_stack_a_reply_that_never_meets_the_contract_reads_nothing_after_three_calls():
    llm = gemini(*[{"terms": ["profit"]}] * 3)
    assert combined(llm).understand(QUESTION, []) == ([], None)
    assert len(llm.sent) == 3 and not llm.single_text


def test_on_the_cloud_path_a_reply_that_never_meets_the_contract_stops_the_run(cloud):
    llm = gemini(*[{"terms": ["profit"]}] * 3)
    with pytest.raises(NoFallback, match=r"reading the question: the model call failed \(ValidationError\)"):
        combined(llm).understand(QUESTION, [])
    assert len(llm.sent) == 3


def test_ask_model_corrections_zero_makes_one_call(cloud):
    cloud.setenv("ASK_MODEL_CORRECTIONS", "0")
    llm = gemini({"terms": ["profit"]})
    with pytest.raises(NoFallback, match="reading the question"):
        combined(llm).understand(QUESTION, [])
    assert len(llm.sent) == 1


def test_a_json_only_model_is_sent_today_s_glossary_prompt(monkeypatch):
    prompts = []

    class JsonOnly:
        name = "stub:json"

        def json(self, prompt):
            prompts.append(prompt)
            return {"terms": ["revenue"], "period": None}

    assert combined(JsonOnly()).understand(QUESTION, []) == (["revenue"], None)
    assert prompts == [glossary_prompt(Engine.term_names, CALENDARS, QUESTION)]
    assert prompts[0].startswith("Glossary terms: revenue; deposits\n") and prompts[0].endswith("\n\nQuestion: " + QUESTION)


def test_the_wording_call_is_structured_with_its_own_budget_and_no_temperature():
    llm = gemini({"answer": "Retail held nothing to report for June 2025."})
    said, by = combined(llm).phrase(dict(ANSWER), Config())
    assert (said, by) == ("Retail held nothing to report for June 2025.", "model:vertex-gemini:gemini-3.1-pro-preview")
    body = llm.sent[0]
    assert body["systemInstruction"]["parts"][0]["text"] == WORDING_INSTRUCTIONS
    content = text_of(body["contents"][0])
    assert content.startswith("## Facts\n{") and content.endswith("## Question\n" + ANSWER["question"])
    facts = json.loads(content.split("\n", 1)[1].split("\n\n## Question", 1)[0])
    assert facts["unit_label"] == "Retail" and facts["figures"] == []
    config = body["generationConfig"]
    assert "temperature" not in config
    assert config["maxOutputTokens"] == 4096 and config["thinkingConfig"] == {"thinkingBudget": 512}


def test_the_wording_number_check_still_holds_a_corrected_reply_to_the_result():
    llm = gemini({"wording": "x"}, {"answer": "Retail had 9,999 customers."})
    said, by = combined(llm).phrase(dict(ANSWER), Config())
    assert said == "the template's answer" and by.startswith("template (model text carried numbers")
    assert len(llm.sent) == 2


def test_on_the_cloud_path_wording_that_never_meets_the_contract_stops_the_run(cloud):
    llm = gemini(*[{"text": "no answer field"}] * 3)
    with pytest.raises(NoFallback, match="phrasing the answer"):
        combined(llm).phrase(dict(ANSWER), Config())
    assert len(llm.sent) == 3


def test_on_the_local_stack_wording_that_never_meets_the_contract_takes_the_template():
    llm = gemini(*[{"text": "no answer field"}] * 3)
    assert combined(llm).phrase(dict(ANSWER), Config()) == ("the template's answer", "template (model call failed)")


def test_a_json_only_model_is_sent_today_s_wording_prompt():
    prompts = []

    class JsonOnly:
        name = "stub:json"

        def json(self, prompt):
            prompts.append(prompt)
            return {"answer": "Retail: nothing to report."}

    assert combined(JsonOnly()).phrase(dict(ANSWER), Config()) == ("Retail: nothing to report.", "model:stub:json")
    facts = {k: ANSWER[k] for k in ("unit_label", "period", "figures", "citations", "not_cited", "withheld")}
    assert prompts == [wording_prompt(facts)] and prompts[0].startswith("Rewrite these facts")


def test_the_mock_provider_still_declines_both_calls_as_it_does_today(monkeypatch):
    llm = providers.LLM("mock", "mock")
    c = combined(llm)
    assert c.understand(QUESTION, []) == ([], None)
    assert c.phrase(dict(ANSWER), Config()) == ("the template's answer", "template (model call failed)")


def test_the_module_keeps_today_s_prompts_for_the_legacy_path():
    assert combined_module.GLOSSARY_VERSION == "glossary-v2" and combined_module.WORDING_VERSION == "wording-v2"
