"""A one-way breakdown reads in its ranking's direction: the leader first."""
from kge.services.ask.engine.reading import grouped


def answer(direction):
    order = [{"output": "m0", "direction": direction, "nulls": "last", "evidence": []}] if direction else []
    return {"group_by": "BRCH_DIM_1.RGN_CD", "rows": {"NER": 20.0, "MISSING": None, "MIDWEST": 30.0, "CEO": 40.0},
            "history": [{"plan": {"order": order}}]}


def test_least_question_reads_smallest_first():
    assert [k for k, _ in grouped(answer("asc"))] == ["NER", "MIDWEST", "CEO", "MISSING"]


def test_most_question_reads_largest_first():
    assert [k for k, _ in grouped(answer("desc"))] == ["CEO", "MIDWEST", "NER", "MISSING"]


def test_unranked_breakdown_reads_largest_first():
    assert [k for k, _ in grouped(answer(None))] == ["CEO", "MIDWEST", "NER", "MISSING"]
    assert [k for k, _ in grouped({"group_by": "BRCH_DIM_1.RGN_CD", "rows": {"A": 1, "B": 2}})] == ["B", "A"]
