"""Page evaluation recognises genuine glossary definitions and rejects missing answers."""
from kge.eval.page_scope import evaluate


ITEM = {"id": "page:unit.asset:term:definition", "kind": "definition", "term": "Complaint"}
ANSWER = {"behaviour": "glossary_definition", "term": "Complaint", "definition": "A recorded complaint.",
          "definition_from": "the pack's glossary", "answer_text": "Complaint: A recorded complaint."}


def test_page_evaluation_accepts_quoted_glossary_authority():
    assert evaluate([ITEM], lambda item: ANSWER)["passed"]
    for changed in ({"term": "Another term"}, {"definition_from": "uncertified email"}, {"answer_text": "No definition quoted"}):
        assert not evaluate([ITEM], lambda item: {**ANSWER, **changed})["passed"]


def test_page_evaluation_requires_a_real_figure():
    item = {**ITEM, "kind": "figure"}
    assert evaluate([item], lambda item: {"behaviour": "figure", "figure": 0})["passed"]
    for answer in ({"behaviour": "abstain"}, {"behaviour": "figure", "figure": None}, {"behaviour": "refuse"}):
        assert not evaluate([item], lambda item: answer)["passed"]
