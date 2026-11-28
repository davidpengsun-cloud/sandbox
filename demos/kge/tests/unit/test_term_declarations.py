"""A glossary term's is_a, broader and codes (meta-model 1.6.0; issue #511): S01's field table and the rules it cannot
state (kge/compiler/checks.py term_fields).

    .venv/bin/python -m pytest tests/unit/test_term_declarations.py -q

Small synthetic packs only, built in memory: three invented terms and one invented source, no question set, nothing tied
to a sample client's data. S01 checks the fields' shape: is_a and broader are non-empty lists of term ids, each id once;
codes a non-empty list of {column, code} with the code quoted text, each pair once (several codes on one column are
alternatives, F7); a member with is_a and no codes is legal (F8); and a pack written for a meta-model before 1.6.0 may
not carry them. Whether a reference resolves is S37's (#512), not S01's. The one read of committed packs is a regression
check that every pack under clients/ still passes every check, with nothing added to it.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from kge.compiler import checks
from kge.compiler import pack as packs
from kge.compiler.compile import VERSION
from kge.compiler.emit import FILLS
from kge.compiler.pack import Pack

DEMO = Path(__file__).resolve().parents[2]

LOCK = {"sources": {"u": {"tables": [{"name": "t", "columns": [{"name": "code", "type": "VARCHAR"},
                                                                  {"name": "label", "type": "VARCHAR"}]}]}}}


def term(i: str, name: str, **fields) -> dict:
    return {"id": i, "name": name, "domain": "Example", "steward": "Example Steward", "definition": f"{name}, invented.",
            "status": "certified", **fields}


def pack(tmp_path, version: str = "1.6.0", **member) -> Pack:
    """A synthetic pack whose glossary holds a class, a member of it and another member; member's fields replace the
    first member's is_a, broader and codes (None drops one)."""
    fields = {"is_a": ["class"], "broader": ["other"],
              "codes": [{"column": "u.t.code", "code": "A"}, {"column": "u.t.code", "code": "B"}]}
    for key, value in member.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = deepcopy(value)
    data = {"manifest": {"meta_model_version": version},
            "glossary": [term("class", "Class"), term("member", "Member (class)", **fields),
                         term("other", "Other (class)", is_a=["class"])]}
    return Pack("example", tmp_path, data=data, lock=deepcopy(LOCK))


def glossary_findings(p: Pack) -> list[tuple[str, str, str]]:
    """S01's findings in the glossary, as (rule, location, message)."""
    return [(f.rule, f.path.split(":", 1)[1], f.message) for f in checks.sections(p) if ":glossary" in f.path]


def test_a_member_with_classes_a_broader_term_and_two_codes_on_one_column_passes(tmp_path):
    """F7: two codes on one column are alternatives; F8: other is a member with is_a and no codes."""
    assert glossary_findings(pack(tmp_path)) == []


def test_a_term_with_none_of_the_fields_passes_on_an_earlier_pack(tmp_path):
    p = pack(tmp_path, "1.5.0", is_a=None, broader=None, codes=None)
    p.data["glossary"][2].pop("is_a")
    assert glossary_findings(p) == []


@pytest.mark.parametrize("changes, at, message", [
    ({"version": "1.5.0", "broader": None, "codes": None}, "glossary[1].is_a", "needs meta-model 1.6.0 or later"),
    ({"version": "1.5.0", "is_a": None, "codes": None}, "glossary[1].broader", "needs meta-model 1.6.0 or later"),
    ({"version": "1.5.0", "is_a": None, "broader": None}, "glossary[1].codes", "needs meta-model 1.6.0 or later"),
    ({"is_a": "class"}, "glossary[1].is_a", "is a list"),
    ({"is_a": []}, "glossary[1].is_a", "is a non-empty list of term ids (glossary ids)"),
    ({"broader": []}, "glossary[1].broader", "is a non-empty list of term ids (glossary ids)"),
    ({"is_a": ["class", "class"]}, "glossary[1].is_a[1]", "names class once"),
    ({"broader": ["other", "other"]}, "glossary[1].broader[1]", "names other once"),
    ({"is_a": [5]}, "glossary[1].is_a[0]", "is a text, not 5"),
    ({"codes": [{"column": "u.t.code"}]}, "glossary[1].codes[0].code", "is required"),
    ({"codes": [{"column": "u.t.code", "code": 5100}]}, "glossary[1].codes[0].code", "is a text, not 5100"),
    ({"codes": [{"column": "u.t.code", "code": "A"}, {"column": "u.t.code", "code": "A"}]}, "glossary[1].codes[1]",
     "gives u.t.code A once: several codes on one column are alternatives, each given once"),
    ({"codes": [{"column": "u.t.code", "code": "A", "valid_to": "2026-01-01"}]}, "glossary[1].codes[0].valid_to",
     "is not a field here: the fields are column, code"),
    ({"codes": []}, "glossary[1].codes", "is a non-empty list of {column, code}"),
    ({"codes": {"column": "u.t.code", "code": "A"}}, "glossary[1].codes", "is a list"),
])
def test_a_malformed_term_field_is_exactly_one_s01_finding(tmp_path, changes, at, message):
    changes = dict(changes)
    version = changes.pop("version", "1.6.0")
    p = pack(tmp_path, version, **changes)
    if version != "1.6.0":
        p.data["glossary"][2].pop("is_a")
    assert glossary_findings(p) == [("S01", at, message)]


def test_an_earlier_pack_is_told_once_per_field_and_nothing_raises_on_a_malformed_one(tmp_path):
    p = pack(tmp_path, "1.5.0", is_a="class", codes=[["not", "a", "mapping"]])
    got = glossary_findings(p)
    assert ("S01", "glossary[1].is_a", "needs meta-model 1.6.0 or later") in got
    assert ("S01", "glossary[1].codes", "needs meta-model 1.6.0 or later") in got
    assert ("S01", "glossary[1].codes[0]", "is a mapping with column, code") in got


def test_an_unresolved_reference_is_not_s01_s(tmp_path):
    """References resolve under S37 (#512): S01 checks shape only."""
    p = pack(tmp_path, is_a=["no-such-term"], codes=[{"column": "u.no.such", "code": "Z"}])
    assert glossary_findings(p) == []


@pytest.mark.parametrize("client", ["meridian", "thornbury", "fellmoor"])
def test_every_committed_pack_still_passes_every_check(client):
    p = packs.read(DEMO / "clients", client)
    assert not p.findings, p.findings
    assert checks.check(p, packs.meta_model(DEMO / "meta-model.yaml"), VERSION, FILLS) == []
