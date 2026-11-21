"""S37, business terms' structure (meta-model 1.6.0; issue #512): what a glossary's is_a, broader and codes declare, held
against the glossary, the mappings, the joins, the vocabulary and the lock (kge/compiler/checks.py term_structure).

    .venv/bin/python -m pytest tests/unit/test_term_structure.py -q

Small synthetic packs only, built in memory: two invented classes, two invented members and one invented source, no
question set, nothing tied to a sample client's data. Each planted-invalid case is refused with exactly the finding
named: a reference that does not resolve, a cycle of is_a or broader, a second broader term in one classification
(the broader term's is_a class, or none: EM decision (c)), a coded column that maps to neither the term's class nor a
broader ancestor's, a term and its broader term coded on two tables no hierarchy or key join links, a member's name that
is a vocabulary value or a predicate phrase (F12), and a code that contradicts a code/name pair (F2, names compared
ignoring case, against the term's name or an alias of it: EM decision (b)) or another term's.
"""
from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from kge.compiler import checks
from kge.compiler import pack as packs
from kge.compiler.compile import VERSION
from kge.compiler.emit import FILLS
from kge.compiler.pack import Pack

DEMO = Path(__file__).resolve().parents[2]
LEVEL_2, LEVEL_3, LEVEL_3_NAME, UNIT = "u.lvl.level_2", "u.lvl.level_3", "u.lvl.level_3_name", "u.unit.unit_code"

LOCK = {"sources": {"u": {"tables": [
    {"name": "lvl", "columns": [{"name": "level_2", "type": "VARCHAR"}, {"name": "level_3", "type": "VARCHAR"},
                                {"name": "level_3_name", "type": "VARCHAR"}]},
    {"name": "unit", "columns": [{"name": "unit_code", "type": "VARCHAR"}]}]}}}


def term(i: str, name: str, **fields) -> dict:
    return {"id": i, "name": name, "domain": "Example", "steward": "Example Steward", "definition": f"{name}, invented.",
            "status": "certified", **fields}


def base() -> dict:
    """Two classes (line, area); north is a line coded on level 2, north-east an area under north coded on level 3, each
    level mapped to its class."""
    return {
        "manifest": {"meta_model_version": "1.6.0"},
        "glossary": [term("line", "Line"), term("area", "Area"),
                     term("north", "North (line)", is_a=["line"], codes=[{"column": LEVEL_2, "code": "N1"}]),
                     term("north-east", "North East (area)", is_a=["area"], broader=["north"],
                          codes=[{"column": LEVEL_3, "code": "N1E"}])],
        "mappings": [{"column": LEVEL_2, "term": "line", "certified": True},
                     {"column": LEVEL_3, "term": "area", "certified": True}],
        "joins": [],
        "terminology": {"aliases": []},
    }


def found(tmp_path, data: dict) -> list[tuple[str, str, str]]:
    p = Pack("example", tmp_path, data=data, lock=deepcopy(LOCK))
    return [(f.rule, f.path.split(":", 1)[1], f.message) for f in checks.term_structure(p)]


def test_a_member_under_a_member_of_another_class_on_one_table_passes(tmp_path):
    assert found(tmp_path, base()) == []


def test_an_earlier_pack_with_no_term_fields_has_nothing_to_report(tmp_path):
    d = base()
    d["manifest"]["meta_model_version"] = "1.5.0"
    for g in d["glossary"]:
        for field in ("is_a", "broader", "codes"):
            g.pop(field, None)
    assert found(tmp_path, d) == []


@pytest.mark.parametrize("field, at", [("is_a", "glossary[3].is_a[0]"), ("broader", "glossary[3].broader[0]")])
def test_a_reference_to_an_undeclared_term_is_refused(tmp_path, field, at):
    d = base()
    d["glossary"][3][field] = ["no-such-term"]
    assert ("S37", at, "no-such-term is not a term the glossary declares") in found(tmp_path, d)


@pytest.mark.parametrize("field", ["is_a", "broader"])
def test_a_term_that_names_itself_is_refused(tmp_path, field):
    d = base()
    d["glossary"][2][field] = ["north"]
    assert ("S37", f"glossary[2].{field}[0]", f"names north, the term itself: a term's {field} names another term") \
        in found(tmp_path, d)


def test_a_code_on_a_column_the_lock_does_not_hold_is_refused(tmp_path):
    d = base()
    d["glossary"][3]["codes"] = [{"column": "u.lvl.no_such", "code": "N1E"}]
    assert found(tmp_path, d) == [("S37", "glossary[3].codes[0].column",
                                   "u.lvl.no_such is not a column in pack.lock (source.table.column)")]


def test_a_code_on_a_restricted_column_is_refused(tmp_path):
    d = base()
    d["policies"] = {"restricted_attributes": [{"id": "r", "action": "refuse", "cite": "X-1", "columns": [LEVEL_3]}]}
    assert found(tmp_path, d) == [("S37", "glossary[3].codes[0].column", f"{LEVEL_3} is restricted personal data: no term "
                                   "is coded on it")]


def test_a_cycle_of_is_a_is_refused_once(tmp_path):
    d = base()
    d["glossary"][0]["is_a"] = ["area"]
    d["glossary"][1]["is_a"] = ["line"]
    got = [f for f in found(tmp_path, d) if "cycle" in f[2]]
    assert got == [("S37", "glossary[0].is_a[0]", "closes a cycle of is_a: line -> area -> line; is_a runs one way only")]


def test_a_cycle_of_broader_is_refused_once(tmp_path):
    d = base()
    d["glossary"].append(term("north-east-2", "North East Two (area)", is_a=["area"], broader=["north-east"]))
    d["glossary"][3]["broader"] = ["north-east-2"]
    got = [f for f in found(tmp_path, d) if "cycle" in f[2]]
    assert got == [("S37", "glossary[3].broader[0]", "closes a cycle of broader: north-east -> north-east-2 -> north-east; "
                    "broader runs one way only")]


def test_a_second_broader_term_in_one_classification_is_refused(tmp_path):
    d = base()
    d["glossary"].append(term("south", "South (line)", is_a=["line"]))
    d["glossary"][3]["broader"] = ["north", "south"]
    assert found(tmp_path, d) == [("S37", "glossary[3].broader[1]", "south is a second broader term for north-east in "
                                   "line's classification: north is already; a term has one broader term in a classification")]


def test_two_broader_terms_that_both_lack_is_a_are_refused(tmp_path):
    d = base()
    d["glossary"] += [term("p", "P"), term("q", "Q")]
    d["glossary"][3]["broader"] = ["p", "q"]
    d["glossary"][3].pop("codes")
    assert found(tmp_path, d) == [("S37", "glossary[3].broader[1]", "q is a second broader term for north-east in no "
                                   "classification (neither has is_a): p is already; a term has one broader term in a "
                                   "classification")]


def test_broader_terms_in_different_classifications_pass(tmp_path):
    """A term may sit in several classifications, one broader term in each."""
    d = base()
    d["glossary"] += [term("zone", "Zone"), term("east-zone", "East (zone)", is_a=["zone"])]
    d["glossary"][3]["broader"] = ["north", "east-zone"]
    assert found(tmp_path, d) == []


def test_a_repeated_id_in_a_list_is_s01_s_and_not_reported_again(tmp_path):
    d = base()
    d["glossary"][3]["broader"] = ["north", "north"]
    d["glossary"][3]["is_a"] = ["area", "area"]
    assert found(tmp_path, d) == []


def test_a_coded_column_that_maps_to_another_class_is_refused(tmp_path):
    d = base()
    d["mappings"][1]["term"] = "line"
    d["glossary"][3].pop("broader")
    assert found(tmp_path, d) == [("S37", "glossary[3].codes[0].column", f"{LEVEL_3} maps to line, not to north-east's "
                                   "class or a broader term's (area): a coded column maps to the class its codes are "
                                   "members of")]


def test_a_coded_column_that_maps_to_no_class_of_the_term_is_refused(tmp_path):
    d = base()
    d["mappings"] = [d["mappings"][0]]
    assert found(tmp_path, d) == [("S37", "glossary[3].codes[0].column", f"{LEVEL_3} maps to no term, not to north-east's "
                                   "class or a broader term's (area, line): a coded column maps to the class its codes are "
                                   "members of")]


def test_a_coded_column_may_map_to_a_broader_ancestor_s_class(tmp_path):
    """F3: the member's is_a class or a BROADER ancestor's class."""
    d = base()
    d["glossary"][3]["codes"] = [{"column": LEVEL_2, "code": "N1E"}]
    assert found(tmp_path, d) == []


def test_a_coded_term_of_no_class_is_no_member_and_its_mapping_is_not_checked(tmp_path):
    """F3 holds a member's coded column: a term with no class of its own or a broader ancestor's is no member."""
    d = base()
    d["glossary"].append(term("loose", "Loose", codes=[{"column": LEVEL_3, "code": "L"}]))
    assert found(tmp_path, d) == []


def test_an_unresolved_link_is_reported_alone_and_not_again_as_a_mapping(tmp_path):
    d = base()
    d["glossary"][3]["is_a"] = ["area", "no-such-term"]
    d["mappings"][1]["term"] = "line"
    d["glossary"][3].pop("broader")
    assert found(tmp_path, d) == [("S37", "glossary[3].is_a[1]", "no-such-term is not a term the glossary declares")]


def test_a_cycle_of_three_terms_is_refused_once(tmp_path):
    d = base()
    d["glossary"] += [term("x", "X", broader=["y"]), term("y", "Y", broader=["z"]), term("z", "Z", broader=["x"])]
    assert found(tmp_path, d) == [("S37", "glossary[4].broader[0]", "closes a cycle of broader: x -> y -> z -> x; broader "
                                   "runs one way only")]


def test_a_second_broader_term_sharing_two_classes_is_refused_once(tmp_path):
    d = base()
    d["glossary"] += [term("zone", "Zone"), term("south", "South (line)", is_a=["line", "zone"])]
    d["glossary"][2]["is_a"] = ["line", "zone"]
    d["glossary"][3]["broader"] = ["north", "south"]
    assert found(tmp_path, d) == [("S37", "glossary[3].broader[1]", "south is a second broader term for north-east in "
                                   "line's classification: north is already; a term has one broader term in a classification")]


def test_unusual_vocabulary_that_s01_accepts_is_read_without_error(tmp_path):
    d = base()
    d["terminology"] = {"aliases": [{"phrase": "northern", "source": "u"}],
                        "value_vocabularies": [{"column": LEVEL_2, "pattern": "quarter"}]}
    d["glossary"].append(term("north", "North again (line)", broader=["north-east"]))
    assert all(f[0] == "S37" for f in found(tmp_path, d))


def test_a_member_with_is_a_and_no_code_passes(tmp_path):
    """F8."""
    d = base()
    d["glossary"].append(term("west", "West (line)", is_a=["line"]))
    assert found(tmp_path, d) == []


@pytest.mark.parametrize("joins, refused", [
    ([], True),
    ([{"id": "j", "kind": "calendar_rollup", "from": "u.unit", "to": "u.lvl"}], True),
    ([{"id": "j", "kind": "key", "from": "u.unit", "to": "u.lvl"}], False),
    ([{"id": "j", "kind": "hierarchy", "from": "u.lvl", "to": "u.unit"}], False),
])
def test_a_term_and_its_broader_term_on_two_tables_need_a_hierarchy_or_key_join(tmp_path, joins, refused):
    """F4: on different tables, the row test goes through a declared hierarchy or key join, or S37 refuses."""
    d = base()
    d["glossary"][3]["codes"] = [{"column": UNIT, "code": "N1E"}]
    d["mappings"].append({"column": UNIT, "term": "area", "certified": True})
    d["joins"] = joins
    finding = ("S37", "glossary[3].broader[0]", "north-east is coded on u.unit and north on u.lvl, with no declared "
               "hierarchy or key join between them: the rows cannot show which is under which")
    assert found(tmp_path, d) == ([finding] if refused else [])


@pytest.mark.parametrize("where, said", [
    ({"value_vocabularies": [{"column": LEVEL_2, "values": ["NORTH (LINE)"]}]},
     "a value of terminology.value_vocabularies[0] (NORTH (LINE))"),
    ({"predicates": [{"phrases": ["north (line)"], "column": LEVEL_2, "op": "is_not_null"}]},
     "a phrase of terminology.predicates[0] (north (line))"),
])
def test_a_member_named_as_a_vocabulary_value_or_a_predicate_phrase_is_refused(tmp_path, where, said):
    """F12, ignoring case."""
    d = base()
    d["terminology"].update(where)
    assert found(tmp_path, d) == [("S37", "glossary[2].name", f"North (line) is a member of a class and also {said}: a "
                                   "member's name is the term's alone")]


def test_a_class_named_as_a_vocabulary_value_is_not_a_member(tmp_path):
    d = base()
    d["terminology"]["value_vocabularies"] = [{"column": LEVEL_2, "values": ["Line"]}]
    assert found(tmp_path, d) == []


def pair(*members: tuple[str, str]) -> dict:
    return {"code": LEVEL_3, "name": LEVEL_3_NAME, "members": [{"code": c, "name": n} for c, n in members]}


@pytest.mark.parametrize("members, aliases", [
    ((("N1E", "North East (area)"),), []),
    ((("N1E", "north east (AREA)"),), []),
    ((("N1E", "Northeast"),), [{"phrase": "northeast", "term": "north-east"}]),
    ((("S1E", "South East"),), []),
])
def test_a_code_name_pair_that_agrees_with_a_term_may_coexist(tmp_path, members, aliases):
    """F2: B1 refuses contradictions, not coexistence; names compare ignoring case, with the term's aliases."""
    d = base()
    d["terminology"] = {"aliases": aliases, "code_name_pairs": [pair(*members)]}
    assert found(tmp_path, d) == []


def test_the_same_column_and_code_with_a_different_name_is_refused(tmp_path):
    d = base()
    d["terminology"]["code_name_pairs"] = [pair(("N1E", "Northeast"))]
    assert found(tmp_path, d) == [("S37", "glossary[3].codes[0].code", f"{LEVEL_3} N1E is Northeast at "
                                   "terminology.code_name_pairs[0].members[0]: North East (area), or an alias of it, is the "
                                   "name for that code")]


def test_the_same_name_with_a_different_code_is_refused(tmp_path):
    d = base()
    d["terminology"]["code_name_pairs"] = [pair(("N2E", "North East (area)"))]
    assert found(tmp_path, d) == [("S37", "terminology.code_name_pairs[0].members[0].code", f"North East (area) is "
                                   f"north-east, which codes N1E on {LEVEL_3}, not N2E")]


def test_two_terms_with_the_same_column_and_code_are_refused(tmp_path):
    d = base()
    d["glossary"].append(term("north-east-2", "North East Two (area)", is_a=["area"], broader=["north"],
                              codes=[{"column": LEVEL_3, "code": "N1E"}]))
    assert found(tmp_path, d) == [("S37", "glossary[4].codes[0].code", f"{LEVEL_3} N1E is north-east's already "
                                   "(glossary[3].codes[0].code): one code on one column names one term")]


def scratch(tmp_path, *planted: dict) -> tuple[list[tuple[str, str, str]], int]:
    """Every finding of checks.check on a scratch copy of the Thornbury sample pack raised to 1.6.0 with planted
    glossary terms, and the place of the first planted one."""
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / "thornbury", clients / "thornbury")
    path = clients / "thornbury" / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["manifest"]["meta_model_version"] = "1.6.0"
    data["manifest"]["platform"]["meta_model"] = "^1.6"
    data["schema"]["meta_model"] = "1.6"
    at = len(data["glossary"])
    data["glossary"] += json.loads(json.dumps(list(planted)))
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    p = packs.read(clients, "thornbury")
    got = checks.check(p, packs.meta_model(DEMO / "meta-model.yaml"), VERSION, FILLS)
    return [(f.rule, f.path.split(":", 1)[1], f.message) for f in got], at


def test_the_compiler_runs_s37_on_a_pack_that_otherwise_passes(tmp_path):
    """checks.check runs S37 after S36: one planted unresolved is_a is refused by S37 alone."""
    got, at = scratch(tmp_path, term("planted", "Planted (class)", is_a=["no-such-term"]))
    assert got == [("S37", f"glossary[{at}].is_a[0]", "no-such-term is not a term the glossary declares")]


def test_an_s01_finding_in_the_glossary_stops_s37(tmp_path):
    got, at = scratch(tmp_path, term("planted", "Planted (class)", is_a=["no-such-term"], broader="planted"))
    assert [rule for rule, _, _ in got] == ["S01"]
    assert got[0][1] == f"glossary[{at}].broader"
