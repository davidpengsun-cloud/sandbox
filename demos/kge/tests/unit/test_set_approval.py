"""A set item's reference SQL approval and its alternate phrasings, as kge/eval/sets.py reads them for F12 (issue #509).

    .venv/bin/python -m pytest tests/unit/test_set_approval.py -q

A person approves a figure item's reference SQL: reference_sql_approved_by, who, and reference_sql_approved_on, the date.
Both or neither; scoring never reads them, and reading a set copies neither into an item's expect, so no item's content
hash moves. An item's alternates are its also_asked_as and added_phrasings, expanded in the object form only. The sets
here are synthetic; the golden 20 are read, never changed, to show a shape may cite them as they are.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kge.compiler import standards
from kge.eval import sets

DEMO = Path(__file__).resolve().parents[2]
GOLDEN_20 = [f"S{n:02d}" for n in range(6, 26)]
APPROVED = {"reference_sql_approved_by": "steward", "reference_sql_approved_on": "2026-11-18"}


def written(tmp_path: Path, value, name: str = "golden.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value, indent=2))
    return path


def test_reference_sql_is_the_item_s_own_then_its_expect_s():
    assert sets.reference_sql({"reference_sql": "SELECT 1", "expect": {"reference_sql": "SELECT 2"}}) == "SELECT 1"
    assert sets.reference_sql({"reference_sql": " ", "expect": {"reference_sql": "SELECT 2"}}) == "SELECT 2"
    assert sets.reference_sql({"expect": {"reference_sql": "SELECT 2"}}) == "SELECT 2"
    assert sets.reference_sql({"expect": {"reference_sql": ""}}) is None
    assert sets.reference_sql({"expect": "SELECT 2"}) is None
    assert sets.reference_sql({}) is None


@pytest.mark.parametrize("item,problems", [
    ({}, []),
    (APPROVED, []),
    ({"reference_sql_approved_by": "steward"},
     ["names reference_sql_approved_by without reference_sql_approved_on"]),
    ({"reference_sql_approved_on": "2026-11-18"},
     ["names reference_sql_approved_on without reference_sql_approved_by"]),
    ({**APPROVED, "reference_sql_approved_by": ""},
     ["reference_sql_approved_by is a non-empty text: the person who approved the reference SQL"]),
    ({**APPROVED, "reference_sql_approved_by": 7},
     ["reference_sql_approved_by is a non-empty text: the person who approved the reference SQL"]),
    ({**APPROVED, "reference_sql_approved_on": "18/11/2026"},
     ["reference_sql_approved_on is a date, YYYY-MM-DD, not '18/11/2026'"]),
    ({**APPROVED, "reference_sql_approved_on": "2026-02-30"},
     ["reference_sql_approved_on is a date, YYYY-MM-DD, not '2026-02-30'"]),
    ({**APPROVED, "reference_sql_approved_on": 20261118},
     ["reference_sql_approved_on is a date, YYYY-MM-DD, not 20261118"]),
    ({"reference_sql_approved_by": None, "reference_sql_approved_on": None},
     ["reference_sql_approved_by is a non-empty text: the person who approved the reference SQL",
      "reference_sql_approved_on is a date, YYYY-MM-DD, not None"]),
], ids=["neither", "both", "by-only", "on-only", "by-blank", "by-number", "on-day-first", "on-no-such-day",
        "on-number", "both-null"])
def test_an_approval_is_both_fields_well_formed_or_neither(item, problems):
    assert sets.approval_problems(item) == problems
    assert sets.approved(item) == (bool(item) and not problems)


def test_alternates_count_also_asked_as_and_added_phrasings_in_the_object_form_only(tmp_path):
    item = {"id": "a", "question": "A?", "kind": "figure", "expect": {}, "reference_sql": "SELECT 1",
            "also_asked_as": ["A, again?"], "added_phrasings": ["A once more?", "And A?"]}
    other = {"id": "b", "question": "B?", "kind": "figure", "expect": {}}
    obj = sets.read(written(tmp_path, {"version": "v1", "items": [item, other]}))
    assert sets.alternates(obj["items"]) == {"a": 3}
    assert [i["id"] for i in obj["items"]] == ["a", "a_alt1", "a_alt2", "a_alt3", "b"]
    listed = sets.read(written(tmp_path, [item, other], "listed.json"))
    assert sets.alternates(listed["items"]) == {}


def test_alternates_count_a_phrasing_once_and_never_the_question_itself(tmp_path):
    item = {"id": "a", "question": "A?", "kind": "figure", "expect": {},
            "also_asked_as": ["a?", "A, again?", "a,  AGAIN?"], "added_phrasings": ["And A?"]}
    obj = sets.read(written(tmp_path, {"version": "v1", "items": [item]}))
    assert sets.alternates(obj["items"]) == {"a": 2}
    listed = sets.read(written(tmp_path, [{"id": "a", "question": "A?"}, {"id": "b", "question": "And A?",
                                                                         "variant_of": "a"}], "listed.json"))
    assert sets.alternates(listed["items"]) == {"a": 1}


def test_locations_name_each_written_item_where_its_file_holds_it(tmp_path):
    assert sets.locations(written(tmp_path, [{"id": "a"}, {"id": "b"}])) == {"a": "[0]", "b": "[1]"}
    path = written(tmp_path, {
        "version": "v2",
        "items": [{"id": "a"}, {"id": "a"}],
        "questions": [{"id": "q"}, {"id": "a"}],
        "templates": [{"template_id": "T", "matrix": [{"x": 1}, {"id": "own", "x": 2}]},
                      {"id_prefix": "P", "matrix": [{"x": 3}]}, {"matrix": "not a list"}]})
    assert sets.locations(path) == {"a": "items[0]", "q": "questions[0]", "T_001": "templates[0].matrix[0]",
                                    "own": "templates[0].matrix[1]", "P_001": "templates[1].matrix[0]"}
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    with pytest.raises(ValueError):
        sets.locations(broken)
    with pytest.raises(OSError):
        sets.locations(tmp_path / "absent.json")


def test_an_approval_passes_through_to_the_item_and_its_alternates_and_never_into_expect(tmp_path):
    path = written(tmp_path, {
        "suite_id": "synthetic", "suite_version": "1", "defaults": {"tolerance": 0},
        "questions": [{"id": "a", "question": "A?", "reference_sql": "SELECT 1", "also_asked_as": ["A, again?"],
                       **APPROVED}],
        "templates": [{"template_id": "T", "question_template": "Total for {x}?", "reference_sql": "SELECT {x}",
                       **APPROVED, "matrix": [{"x": 1}, {"id": "own", "x": 2}]}]})
    items = sets.read(path)["items"]
    assert [i["id"] for i in items] == ["a", "a_alt1", "T_001", "own"]
    for item in items:
        assert {k: item[k] for k in sets.APPROVAL} == APPROVED and sets.approved(item)
        assert not set(sets.APPROVAL) & set(item["expect"])
    assert sorted(items[2]) == sorted(["template_id", "reference_sql", *sets.APPROVAL, "id", "question", "kind",
                                       "expect", "tolerance", "x"])
    assert (items[2]["question"], items[2]["reference_sql"], items[2]["template_id"]) == ("Total for 1?", "SELECT 1", "T")
    assert items[2]["expect"] == {"tolerance": 0, "reference_sql": "SELECT 1"}
    assert items[1]["variant_of"] == "a" and items[1]["question"] == "A, again?"


def test_the_golden_20_carry_approved_reference_sql_and_enough_phrasings():
    """A shape may cite the golden 20 as they are: each is approved, carries reference SQL and has at least the
    preload's minimum of alternate phrasings."""
    data = sets.read(DEMO / "clients" / "meridian" / "fixture" / "sage_v1.json")
    counts = sets.alternates(data["items"])
    base = {i["id"]: i for i in data["items"] if "variant_of" not in i}
    least = standards.load().min_also_asked_as
    for sid in GOLDEN_20:
        assert sets.approved(base[sid]), sid
        assert sets.reference_sql(base[sid]), sid
        assert counts.get(sid, 0) >= least, sid
