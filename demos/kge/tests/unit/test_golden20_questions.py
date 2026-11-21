"""GET /golden names each set's golden-20 questions, the items make golden20 asks, so the portal's "Try one of these"
list is the release's golden 20 alone (issue #590)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from kge.estate.golden_sage import GOLDEN_20
from kge.eval import sets
from kge.services.quality import routes, service


def item(sid: str, question: str, **extra) -> dict:
    return {"id": sid, "kind": "within", "question": question, "expect": {"unit": "wealth"}, **extra}


def estate(tmp_path):
    (tmp_path / "golden.json").write_text(json.dumps([item("G001", "older golden?")]))
    (tmp_path / "sage_v1.json").write_text(json.dumps({"version": "v1", "items": [
        item("S01", "sage, not golden 20?", category="Other"),
        item("S06", "golden 20 first?", category=GOLDEN_20),
        item("S06-a", "golden 20 first, reworded?", category=GOLDEN_20, variant_of="S06"),
        item("S07", "golden 20 second?", category=GOLDEN_20),
    ]}))
    return tmp_path


def test_a_set_names_the_questions_of_its_items_in_a_category_that_are_no_other_item_s_phrasing(tmp_path):
    shown = {s["name"]: s for s in sets.golden(estate(tmp_path), category=GOLDEN_20)}
    assert shown["sage"]["category_questions"] == ["golden 20 first?", "golden 20 second?"]
    assert shown["golden"]["category_questions"] == []
    assert all("category_questions" not in s for s in sets.golden(tmp_path)), "the existing shape without a category"


def test_the_quality_service_names_the_golden_20_by_the_category_make_golden20_reads(tmp_path):
    profile = SimpleNamespace(baseline=None, id="p", estate=lambda: estate(tmp_path))
    shown = {s["name"]: s for s in service.golden(profile, SimpleNamespace(UNIT_ORDER=["wealth"]), "graph")}
    assert shown["sage"]["golden20"] == ["golden 20 first?", "golden 20 second?"]
    assert shown["golden"]["golden20"] == [] and all("category_questions" not in s for s in shown.values())


def test_get_golden_leaves_a_question_on_a_unit_the_caller_may_not_read_out_of_golden20(monkeypatch, tmp_path):
    profile = SimpleNamespace(baseline=None, id="p", estate=lambda: estate(tmp_path))
    monkeypatch.setattr(routes.served, "engine", lambda: SimpleNamespace(UNIT_ORDER=["wealth"]), raising=False)
    monkeypatch.setattr(routes.served, "PROFILE", profile, raising=False)
    monkeypatch.setattr(routes.served, "DEFAULT_ARM", "graph", raising=False)
    monkeypatch.setattr(routes.served, "SERVE_HASH", None, raising=False)
    monkeypatch.setattr(routes.identity, "readable_units", lambda eng, who: [])
    monkeypatch.setattr(routes.service, "suggestions_meta", lambda *a: {"unreadable_questions": {"golden 20 second?"}})
    sage = next(s for s in routes.golden(who="analyst")["sets"] if s["name"] == "sage")
    assert sage["golden20"] == ["golden 20 first?"] and "golden 20 second?" not in sage["questions"]
