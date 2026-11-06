"""Exact glossary mentions survive model omissions without changing other proposal families."""
import json

import pytest

from kge.pipeline.tagger import ModelTagger, Proposal, RuleTagger


class Reply:
    """An offline model response, with calls recorded to check the privacy gate."""

    model = "tagger-stand-in"

    def __init__(self, mentions=(), candidates=()):
        self.response = json.dumps({"mentions": list(mentions), "candidates": list(candidates)})
        self.calls = []

    def complete(self, prompt, max_tokens=None):
        self.calls.append((prompt, max_tokens))
        return self.response


@pytest.mark.parametrize("term,text,evidence", [
    ("Revenue", "Revenue increased this year.", "Revenue"),
    ("Fiscal Year", "The fiscal year ends in December.", "fiscal year"),
    ("Active Customer", "Active Customers are counted once.", "Active Customers"),
])
def test_model_omission_keeps_exact_rule_mention(term, text, evidence):
    model = Reply()
    proposals = ModelTagger.configured([term], model).propose(text, "certified", "policy")
    assert [p for p in proposals if p.family == "business_meaning"] == [
        Proposal("business_meaning", term, 0.85, evidence),
    ]
    assert len(model.calls) == 1


def test_rule_and_model_mentions_are_kept_once_with_original_provenance():
    model = Reply(mentions=[
        {"term": "Revenue", "confidence": 0.99, "evidence": "model interpretation"},
        {"term": "Profit", "confidence": 0.75, "evidence": "earnings after costs"},
        {"term": "Revenue", "confidence": 0.8, "evidence": "repeated mention"},
        {"term": "Profit", "confidence": 0.6, "evidence": "repeated semantic mention"},
        {"term": "Unknown", "confidence": 0.9, "evidence": "outside glossary"},
    ])
    proposals = ModelTagger.configured(["Revenue", "Profit"], model).propose(
        "Revenue increased. Revenue less costs gives earnings.", "certified", "policy")
    mentions = [p for p in proposals if p.family == "business_meaning"]
    assert mentions == [
        Proposal("business_meaning", "Revenue", 0.85, "Revenue"),
        Proposal("business_meaning", "Profit", 0.75, "earnings after costs", "auto", model.model),
    ]


def test_rule_candidates_are_not_added_to_model_candidates():
    text = "Draft note\nRetention Score: a new measure. Revenue funds investment."
    rules = RuleTagger(["Revenue"]).propose(text, "uncertified", "memo")
    assert any(p.family == "candidate_term" and p.value == "Retention Score" for p in rules)
    model = Reply(candidates=[{"phrase": "Engagement Score", "definition": "Share engaged", "confidence": 0.65}])
    proposals = ModelTagger.configured(["Revenue"], model).propose(text, "uncertified", "memo")
    assert [p for p in proposals if p.family == "candidate_term"] == [
        Proposal("candidate_term", "Engagement Score", 0.65, "Share engaged", "auto", model.model),
    ]
    assert [p for p in proposals if p.family in {"privacy", "classification"}] == [
        p for p in rules if p.family in {"privacy", "classification"}
    ]


def test_privacy_withheld_passage_keeps_rules_without_a_model_call():
    text = "Revenue contact: alice@example.com"
    model = Reply(mentions=[{"term": "Profit"}])
    glossary = ["Revenue", "Profit"]
    rules = RuleTagger(glossary).propose(text, "certified", "policy")
    assert any(p.family == "privacy" for p in rules)
    tagger = ModelTagger.configured(glossary, model)
    assert tagger.propose(text, "certified", "policy") == rules
    assert tagger.withheld == 1
    assert model.calls == []


def test_mentions_remain_per_passage_and_do_not_match_substrings():
    model = Reply()
    tagger = ModelTagger.configured(["Revenue"], model)
    for text in ("Revenue rose.", "Revenue fell."):
        assert [p.value for p in tagger.propose(text, "certified", "policy")
                if p.family == "business_meaning"] == ["Revenue"]
    assert not [p for p in tagger.propose("RevenueGrowth is an identifier.", "certified", "policy")
                if p.family == "business_meaning"]
