"""Bounded improvement edits (#532, after #452 requirement 4): kge/improve/packtext.py adds entries to a pack's text
without rewriting it, and bounded() names every change a candidate pack makes outside the improvement allowlist.

    .venv/bin/python -m pytest tests/unit/test_improve_packtext.py -q

In-memory YAML text and parsed packs only, invented for these tests: no pack file, no set file, nothing tied to a
sample client's data.
"""
from __future__ import annotations

import copy
import difflib

import pytest
import yaml

from kge.improve.packtext import (
    Unplaceable,
    add_input_set,
    append_entries,
    append_test_set,
    bounded,
    flow,
)

BLOCK = """manifest:
  id: sample
  version: 1.2.0
  inputs:
    estate: {fixture: fixture}
    sets:
      - golden
      - heldout
  owners: [steward]

# phrases the steward keeps
phrases:
  - {phrase: per head, operation: average}
  - {phrase: in total, operation: sum}
  # more to come

# the tested sets
tests:
  sets:
    - {name: golden, version: v1}
glossary:
  - {term: policy}
"""

FLOW = """manifest:
  inputs:
    sets: [golden]
  version: 1.2.0
phrases: [{phrase: per head, operation: average}]  # one so far
shapes: []  # none yet
tests:
  sets: [{name: golden, version: v1}]
"""

NEW_PHRASE = {"phrase": "on average", "operation": "average"}


def additions_only(before: str, after: str, replaced: int = 0) -> None:
    """Every original line survives in order; the diff adds lines and removes at most `replaced` of them."""
    diff = list(difflib.ndiff(before.splitlines(), after.splitlines()))
    assert sum(1 for d in diff if d.startswith("- ")) == replaced
    kept = [d[2:] for d in diff if d.startswith("  ")]
    removed = [d[2:] for d in diff if d.startswith("- ")]
    assert [line for line in before.splitlines() if line not in removed] == kept


def with_added(text: str, section: str, entries: list) -> dict:
    """The parsed text with entries appended to one top-level list."""
    data = yaml.safe_load(text)
    data[section] = (data.get(section) or []) + entries
    return data


def test_flow_is_one_line_and_drops_the_document_end() -> None:
    assert flow({"phrase": "a b", "operation": "sum"}) == "{phrase: a b, operation: sum}"
    assert flow("golden_v1") == "golden_v1"
    assert flow(["x", "y"]) == "[x, y]"


def test_append_entries_creates_an_absent_section_at_the_end() -> None:
    out = append_entries(BLOCK, "shapes", [{"id": "s1", "stage": "specify"}])
    additions_only(BLOCK, out)
    assert out.endswith("\nshapes:\n  - {id: s1, stage: specify}\n")
    assert yaml.safe_load(out) == with_added(BLOCK, "shapes", [{"id": "s1", "stage": "specify"}])


def test_append_entries_adds_a_trailing_newline_first() -> None:
    out = append_entries("glossary: []", "phrases", [NEW_PHRASE])
    assert yaml.safe_load(out) == {"glossary": [], "phrases": [NEW_PHRASE]}


def test_append_entries_turns_an_empty_list_into_a_block() -> None:
    out = append_entries(FLOW, "shapes", [{"id": "s1"}])
    additions_only(FLOW, out, replaced=1)
    assert "shapes:  # none yet\n  - {id: s1}\n" in out
    assert yaml.safe_load(out) == with_added(FLOW, "shapes", [{"id": "s1"}])


def test_append_entries_goes_after_the_last_block_item_and_leaves_comments() -> None:
    out = append_entries(BLOCK, "phrases", [NEW_PHRASE])
    additions_only(BLOCK, out)
    assert "  - {phrase: in total, operation: sum}\n  - {phrase: on average, operation: average}\n  # more to come\n" in out
    assert yaml.safe_load(out) == with_added(BLOCK, "phrases", [NEW_PHRASE])


def test_append_entries_extends_a_one_line_flow_list_and_keeps_its_comment() -> None:
    out = append_entries(FLOW, "phrases", [NEW_PHRASE])
    additions_only(FLOW, out, replaced=1)
    assert "phrases: [{phrase: per head, operation: average}, {phrase: on average, operation: average}]  # one so far\n" in out
    assert yaml.safe_load(out) == with_added(FLOW, "phrases", [NEW_PHRASE])


def test_append_entries_refuses_a_multi_line_flow_list() -> None:
    text = "phrases: [{phrase: a, operation: sum},\n  {phrase: b, operation: sum}]\n"
    with pytest.raises(Unplaceable):
        append_entries(text, "phrases", [NEW_PHRASE])


def test_append_entries_refuses_a_section_outside_the_allowlist() -> None:
    with pytest.raises(ValueError):
        append_entries(BLOCK, "glossary", [{"term": "claim"}])


def test_add_input_set_extends_the_flow_form() -> None:
    out = add_input_set(FLOW, "new_v1")
    additions_only(FLOW, out, replaced=1)
    assert "    sets: [golden, new_v1]\n" in out
    assert yaml.safe_load(out)["manifest"]["inputs"]["sets"] == ["golden", "new_v1"]


def test_add_input_set_extends_the_block_form() -> None:
    out = add_input_set(BLOCK, "new_v1")
    additions_only(BLOCK, out)
    assert "      - heldout\n      - new_v1\n" in out
    expected = yaml.safe_load(BLOCK)
    expected["manifest"]["inputs"]["sets"].append("new_v1")
    assert yaml.safe_load(out) == expected


def test_add_input_set_refuses_a_pack_without_inputs() -> None:
    with pytest.raises(Unplaceable, match="declares no manifest.inputs.sets"):
        add_input_set("manifest:\n  version: 1.0.0\n", "new_v1")


def test_add_input_set_refuses_inputs_written_as_a_flow_mapping() -> None:
    with pytest.raises(Unplaceable):
        add_input_set("manifest:\n  inputs: {sets: [golden]}\n", "new_v1")


def test_add_input_set_refuses_a_stem_already_listed() -> None:
    with pytest.raises(Unplaceable, match="already lists"):
        add_input_set(BLOCK, "heldout")


def test_append_test_set_extends_the_block_form() -> None:
    entry = {"name": "new", "version": "v1", "items": ["q1", "q2"]}
    out = append_test_set(BLOCK, entry)
    additions_only(BLOCK, out)
    assert "    - {name: golden, version: v1}\n    - {name: new, version: v1, items: [q1, q2]}\n" in out
    assert yaml.safe_load(out)["tests"]["sets"][-1] == entry


def test_append_test_set_extends_the_flow_form() -> None:
    entry = {"name": "new", "version": "v1", "items": ["q1"]}
    out = append_test_set(FLOW, entry)
    additions_only(FLOW, out, replaced=1)
    assert yaml.safe_load(out)["tests"]["sets"] == [{"name": "golden", "version": "v1"}, entry]


def test_append_test_set_refuses_a_pack_without_tests_sets() -> None:
    with pytest.raises(Unplaceable, match="declared"):
        append_test_set("manifest:\n  version: 1.0.0\n", {"name": "new", "version": "v1", "items": ["q1"]})


def test_append_test_set_refuses_a_name_already_used() -> None:
    with pytest.raises(Unplaceable, match="already names"):
        append_test_set(BLOCK, {"name": "golden", "version": "v1", "items": ["q1"]})


BEFORE = {
    "manifest": {"id": "sample", "version": "1.2.0", "owners": ["steward"],
                 "inputs": {"estate": {"fixture": "fixture"}, "sets": ["golden"]}},
    "glossary": [{"term": "policy"}],
    "terminology": {"aliases": [{"phrase": "cover", "term": "policy"}], "stopwords": ["the"]},
    "phrases": [{"phrase": "per head", "operation": "average"}],
    "shapes": [{"id": "old_shape", "stage": "shadow", "recipe": "old_recipe"}],
    "recipes": [{"id": "old_recipe"}],
    "examples": [{"id": "ex_old", "shape": "old_shape", "question": "q", "plan": {}}],
    "tests": {"sets": [{"name": "golden", "version": "v1"}]},
}
ITEMS = ["n1", "n2"]


def improved() -> dict:
    """BEFORE with a full bundle, a phrase and an alias added: within the bound."""
    after = copy.deepcopy(BEFORE)
    after["manifest"]["version"] = "1.3.0"
    after["manifest"]["inputs"]["sets"].append("shapes_new_shape_v1")
    after["terminology"]["aliases"].append({"phrase": "insured", "term": "policy"})
    after["phrases"].append({"phrase": "on average", "operation": "average"})
    after["shapes"].append({"id": "new_shape", "stage": "specify", "recipe": "new_recipe"})
    after["recipes"].append({"id": "new_recipe"})
    after["shape_tests"] = [{"shape": "new_shape", "tests": {"set": "shapes_new_shape", "items": list(ITEMS)}}]
    after["examples"].append({"id": "ex_new", "shape": "new_shape", "question": "q", "plan": {}})
    after["tests"]["sets"].append({"name": "shapes_new_shape", "version": "v1", "items": list(ITEMS)})
    return after


def test_bounded_accepts_a_full_bundle_a_phrase_and_an_alias() -> None:
    assert bounded(BEFORE, improved(), "1.3.0") == []


def test_bounded_accepts_phrases_and_aliases_alone() -> None:
    after = copy.deepcopy(BEFORE)
    after["manifest"]["version"] = "1.3.0"
    after["phrases"].append({"phrase": "on average", "operation": "average"})
    after["terminology"]["aliases"].append({"phrase": "insured", "term": "policy"})
    assert bounded(BEFORE, after, "1.3.0") == []


def test_bounded_reports_malformed_input_rather_than_raising() -> None:
    assert bounded(BEFORE, ["not", "a", "pack"], "1.3.0")
    after = improved()
    after["shapes"] = "oops"
    after["tests"] = ["x"]
    assert bounded(BEFORE, after, "1.3.0")


def plant(change) -> list[str]:
    """The violations bounded() finds after one planted change to an otherwise good candidate."""
    after = improved()
    change(after)
    return bounded(BEFORE, after, "1.3.0")


def set_key(path: tuple, value):
    """A change that sets the value at path."""
    def change(after: dict) -> None:
        node = after
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return change


@pytest.mark.parametrize("change, expect", [
    (set_key(("terminology", "aliases", 0), {"phrase": "cover", "term": "claim"}), "terminology.aliases changed"),
    (lambda a: a["terminology"]["aliases"].pop(0), "terminology.aliases changed"),
    (set_key(("shapes", 0, "stage"), "specify"), "shapes changed"),
    (set_key(("shapes", 1, "stage"), "shadow"), "at stage \"shadow\""),
    (set_key(("shapes", 1, "replaces"), "old_shape"), "carries replaces"),
    (set_key(("shapes", 1, "constraints"), []), "carries constraints"),
    (lambda a: a["shapes"].append({"id": "other", "stage": "specify", "recipe": "new_recipe"}), "adds 2 shapes"),
    (set_key(("examples", 1, "approved_by"), "someone"), "never approves"),
    (set_key(("shape_tests", 0, "tests", "items"), ["n1"]), "exactly its items"),
    (lambda a: a["manifest"]["inputs"]["sets"].append("more_v1"), "adds 2 stems"),
    (set_key(("manifest", "inputs", "sets", 1), "other_v1"), "is not \"shapes_new_shape_v1\""),
    (set_key(("glossary", 0, "term"), "claim"), "glossary changed"),
    (set_key(("manifest", "owners"), ["someone-else"]), "manifest.owners changed"),
    (lambda a: a["phrases"].append({"phrase": "please", "operation": "filler"}), "is a filler"),
    (lambda a: a["phrases"].append({"phrase": "mean", "operation": "average", "note": "x"}), "not exactly {phrase, operation}"),
])
def test_bounded_reports_each_planted_violation(change, expect: str) -> None:
    assert any(expect in v for v in plant(change)), plant(change)


def test_bounded_refuses_a_major_bump() -> None:
    after = improved()
    after["manifest"]["version"] = "2.0.0"
    assert any("not the minor bump" in v for v in bounded(BEFORE, after, "2.0.0"))


def test_bounded_refuses_a_recipe_without_a_shape() -> None:
    after = copy.deepcopy(BEFORE)
    after["manifest"]["version"] = "1.3.0"
    after["recipes"].append({"id": "stray"})
    assert any("a recipe added without a new shape" in v for v in bounded(BEFORE, after, "1.3.0"))


@pytest.mark.parametrize("change, expect", [
    (lambda a: a["tests"]["sets"].__setitem__(1, {"name": "golden", "version": "v1", "items": list(ITEMS)}),
     "reuses a name tests.sets holds"),
    (lambda a: a["recipes"].append({"id": "old_recipe"}) or a["shapes"][1].__setitem__("recipe", "old_recipe")
     or a["recipes"].pop(1), "already in recipes"),
    (lambda a: a["phrases"].append({"phrase": "per head", "operation": "difference"}), "repeats a phrase the pack holds"),
    (lambda a: a["terminology"]["aliases"].append({"phrase": "cover", "term": "claim"}),
     "repeats a phrase terminology.aliases holds"),
], ids=["set-name-reused", "recipe-id-reused", "phrase-repeated", "alias-repeated"])
def test_bounded_refuses_an_addition_that_repeats_what_the_pack_holds(change, expect) -> None:
    found = plant(change)
    assert any(expect in f for f in found), found


def test_bounded_takes_an_example_id_of_any_type_without_raising() -> None:
    found = plant(lambda a: a["examples"].append({"id": ["x"], "shape": "new_shape", "question": "q", "plan": {}}))
    assert found == []


def test_bounded_refuses_creating_the_lists_every_run_reads() -> None:
    before = copy.deepcopy(BEFORE)
    del before["manifest"]["inputs"]["sets"]
    del before["tests"]["sets"]
    found = bounded(before, improved(), "1.3.0")
    assert any("manifest.inputs.sets is created" in f for f in found), found
    assert any("tests.sets is created" in f for f in found), found


def test_bounded_refuses_a_new_set_not_named_for_the_new_shape() -> None:
    def rename(after: dict) -> None:
        after["tests"]["sets"][1]["name"] = "heldout"
        after["shape_tests"][0]["tests"]["set"] = "heldout"
        after["manifest"]["inputs"]["sets"][1] = "heldout_v1"
    assert any("is not \"shapes_new_shape\", the new shape's set" in f for f in plant(rename)), plant(rename)
