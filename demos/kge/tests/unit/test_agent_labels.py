"""An /agent answer is labelled as /ask's is (#464): every figure begins with its answer_mode's label, Certified or
AI-Generated, once; a decline carries none."""
from types import SimpleNamespace

import pytest

from kge.services.ask import service
from kge.services.ask.engine.wording import AI_GENERATED_LABEL, CERTIFIED_LABEL, labelled


@pytest.mark.parametrize("mode, label", [("ai_generated", AI_GENERATED_LABEL), ("certified", CERTIFIED_LABEL)])
@pytest.mark.parametrize("behaviour", ["figure", "combined", "sum_with_verdict"])
def test_a_figure_is_labelled_once_per_its_answer_mode(mode, label, behaviour):
    out = labelled({"answer_text": "NNA: 15.", "behaviour": behaviour, "answer_mode": mode})
    assert out["answer_text"] == label + "NNA: 15."
    assert labelled(out)["answer_text"] == out["answer_text"]


@pytest.mark.parametrize("mode", ["ai_generated", "certified", "not_sure"])
@pytest.mark.parametrize("behaviour", ["refuse", "abstain", "clarify", "abstain_with_reason"])
def test_a_decline_carries_no_label(mode, behaviour):
    assert labelled({"answer_text": "Not answered.", "behaviour": behaviour, "answer_mode": mode})["answer_text"] == "Not answered."


@pytest.mark.parametrize("mode, label", [("ai_generated", AI_GENERATED_LABEL), ("certified", CERTIFIED_LABEL), ("not_sure", "")])
def test_the_agent_route_labels_its_first_turn_figure(monkeypatch, mode, label):
    answer = {"question": "q", "behaviour": "figure", "answer_mode": mode, "answer_text": "NNA: 15.", "trace": []}
    monkeypatch.setattr(service.runtrace, "run", lambda path, fn, **kw: fn())
    monkeypatch.setattr(service, "log_unmatched", lambda *a, **k: None)
    monkeypatch.setattr(service, "build_path", lambda e, out: {})
    monkeypatch.setattr(service, "build_graph_path", lambda e, out: {})
    ag = SimpleNamespace(ask=lambda *a, **k: dict(answer), g=None, e=None)
    a = SimpleNamespace(question="q", purpose="analysis", arm="treatment", products=None, history=(), runtimes=None)
    assert service.agent(ag, None, a, "who")["answer_text"] == label + "NNA: 15."
