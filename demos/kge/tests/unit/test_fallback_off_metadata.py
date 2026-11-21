"""With ASK_AI_FALLBACK=off (#464), or with no model, Ask makes no model call at all: not even the metadata question's
glossary classification, which with the fallback on reads the question's terms through the model."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine import combined as combinedmod
from kge.services.ask.engine.combined import Combined


class Scope:
    products = None

    def portal_only(self):
        return False

    def check(self, *a):
        return None

    def documents(self, out):
        return out


def governed(monkeypatch, llm):
    req = SimpleNamespace(special=None, definitional=True, term="NNA", counted=None, agg="sum", period=None,
                          period_by="rules", restricted=False, problem=None, terms_found=["NNA"], units=["wealth"])
    monkeypatch.setattr(combinedmod, "parse", lambda *a: req)
    monkeypatch.setattr(combinedmod, "definition_request", lambda r: r)
    monkeypatch.setattr(combinedmod, "catalogue_question", lambda *a: False)
    cfg = SimpleNamespace(graph=True, arm="graph", label="graph", problems=lambda *a, **k: [],
                          uses_model=lambda stage: True, model_stages=lambda: ["resolution"])
    c = Combined.__new__(Combined)
    c.e = SimpleNamespace(stamped=lambda x: x, term_names=["NNA"],
                          definition_answer=lambda *a: {"behaviour": "glossary_definition", "definition": "Net new assets"})
    c.g = SimpleNamespace(may=lambda *a: True, embedder=SimpleNamespace(name="hash"))
    c.llm = llm
    return c._governed("What does NNA mean in wealth?", "who", "analysis", cfg, Scope())


class Counting:
    name = "stub:counting"

    def __init__(self):
        self.prompts = []

    def json(self, prompt, max_tokens=800):
        self.prompts.append(prompt)
        return {"terms": [], "period": None}


def test_with_the_fallback_on_the_glossary_classification_asks_the_model(monkeypatch):
    monkeypatch.setenv("ASK_AI_FALLBACK", "on")
    llm = Counting()
    out = governed(monkeypatch, llm)
    assert len(llm.prompts) == 1 and out["trace"][0]["resolved_by"] == "rules+model"
    assert out["versions"]["model"] == llm.name


@pytest.mark.parametrize("fallback", ["off", "OFF"])
def test_with_the_fallback_off_no_model_call_is_made_at_all(monkeypatch, fallback):
    monkeypatch.setenv("ASK_AI_FALLBACK", fallback)
    llm = Counting()
    out = governed(monkeypatch, llm)
    assert llm.prompts == []
    assert out["behaviour"] == "glossary_definition"
    assert out["trace"][0]["resolved_by"] == "rules" and out["versions"]["model"] == "none"
