"""The opt-in citation passage contract; legacy retrieval remains unchanged."""
from types import SimpleNamespace

from kge.services.ask.models import Retrieve
from kge.services.ask.service import retrieve


class Graph:
    def __init__(self):
        self.rows = [
            {"doc": "gold", "chunk": "gold#0", "excerpt": "opening"},
            {"doc": "silver", "chunk": "silver#0", "excerpt": "opening"},
            {"doc": "old", "chunk": "old#0", "excerpt": "opening"},
            {"doc": "other", "chunk": "other#0", "excerpt": "opening"},
        ]
        self.docs = {name: {"doc": name, "title": name, "owner": "owner", "version": "v1", "ai_ready": True,
                            "unit": "other" if name == "other" else "retail", "tier": "silver" if name == "silver" else "gold",
                            "cert_from": "2020-01-01", "cert_to": "2022-12-31" if name == "old" else ""}
                     for name in ("gold", "silver", "old", "other")}
        self.calls = []

    def retrieve(self, query, who, purpose, k, shelf, tiers):
        self.calls.append((query, who, purpose, k, shelf, tiers))
        return {"exact": self.rows, "index_returned": 4, "post_filter_lost": 0, "post_filtered": ["legacy"]}

    def certification(self, ids):
        return {key: self.docs[key] for key in ids}

    def readable_units(self, who):
        return {"retail"}

    def q(self, query, params):
        assert "left(c.text, 4001)" in query
        assert params["purpose"] == "analysis"
        assert params["units"] == ["retail"]
        return [{"doc": key.split("#")[0], "id": key, "text": "X" * (4001 if key == "gold#0" else 50)} for key in params["ids"]]


def engine():
    return SimpleNamespace(g=Graph(), term_names=[])


def test_legacy_retrieval_keeps_response_and_parameters():
    e = engine()
    result = retrieve(e, Retrieve(query="q", tiers=["bronze"]), "analyst")
    assert result["post_filtered"] == ["legacy"]
    assert e.g.calls[0][-1] == ["bronze"]


def test_passages_are_bounded_citable_and_caller_scoped(monkeypatch):
    from kge.services.ask.engine import question
    monkeypatch.setattr(question, "parse", lambda *args: None)
    monkeypatch.setattr(question, "asked_window", lambda *args: None)
    e = engine()
    result = retrieve(e, Retrieve(query="q", purpose="analysis", include_passages=True), "analyst")
    assert [row["doc"] for row in result["exact"]] == ["gold", "silver"]
    gold, silver = result["exact"]
    assert gold["passage"] == {"text": "X" * 4000, "truncated": True}
    assert silver["passage"]["truncated"] is False
    assert silver["attribution"] == "owner, version v1"
    assert e.g.calls[0][-1] == ["gold", "silver"]


def test_passages_can_include_historical_source(monkeypatch):
    from kge.services.ask.engine import question
    monkeypatch.setattr(question, "parse", lambda *args: None)
    monkeypatch.setattr(question, "asked_window", lambda *args: ("2021-01-01", "2021-12-31"))
    result = retrieve(engine(), Retrieve(query="q", purpose="analysis", include_passages=True), "analyst")
    assert "old" in [row["doc"] for row in result["exact"]]


def test_requesting_only_non_citable_tiers_returns_no_passages():
    e = engine()
    assert retrieve(e, Retrieve(query="q", tiers=["bronze"], include_passages=True), "analyst")["exact"] == []
    assert not e.g.calls


def test_total_limit_reports_omitted_eligible_chunks(monkeypatch):
    from kge.services.ask.engine import question
    monkeypatch.setattr(question, "parse", lambda *args: None)
    monkeypatch.setattr(question, "asked_window", lambda *args: None)
    e = engine()
    e.g.rows = [{"doc": "gold", "chunk": f"gold#{i}", "excerpt": "opening"} for i in range(6)]
    e.g.q = lambda query, params: [{"doc": "gold", "id": key, "text": "X" * 4000} for key in params["ids"]]
    result = retrieve(e, Retrieve(query="q", purpose="analysis", include_passages=True), "analyst")
    assert len(result["exact"]) == 5
    assert result["truncated"] is True
    assert "index_returned" not in result and "post_filter_lost" not in result


def test_chunk_text_is_bound_to_its_document(monkeypatch):
    from kge.services.ask.engine import question
    monkeypatch.setattr(question, "parse", lambda *args: None)
    monkeypatch.setattr(question, "asked_window", lambda *args: None)
    e = engine()
    e.g.q = lambda query, params: [{"doc": "silver", "id": "gold#0", "text": "wrong document"}]
    result = retrieve(e, Retrieve(query="q", purpose="analysis", include_passages=True), "analyst")
    assert result["exact"] == []


def test_candidate_limit_is_applied_before_fetching_chunk_text(monkeypatch):
    from kge.services.ask.engine import question
    monkeypatch.setattr(question, "parse", lambda *args: None)
    monkeypatch.setattr(question, "asked_window", lambda *args: None)
    e = engine()
    e.g.rows = [{"doc": "gold", "chunk": f"gold#{i}", "excerpt": "opening"} for i in range(21)]
    fetched = []
    def query(text, params):
        fetched.extend(params["ids"])
        return [{"doc": "gold", "id": key, "text": "short"} for key in params["ids"]]
    e.g.q = query
    result = retrieve(e, Retrieve(query="q", purpose="analysis", k=21, include_passages=True), "analyst")
    assert len(fetched) == len(result["exact"]) == 20
    assert result["truncated"] is True


def test_unknown_tier_is_refused_in_passage_mode():
    import pytest
    with pytest.raises(ValueError, match="tiers must name"):
        retrieve(engine(), Retrieve(query="q", tiers=["gøld"], include_passages=True), "analyst")
