"""The authoring skills against the code (issue #530, B4.2 of #452): author-pack writes question shapes tests first,
names the platform's closed lists as the code holds them, and its worked shape passes the shape checks and F12's set
rules on the platform's own preload; build-and-gate reports each shape's lifecycle and blocks below the bar; and
land-and-draft proposes candidate phrases only.

    .venv/bin/python -m pytest tests/unit/test_authoring_skills.py -q

Small synthetic data only: the worked shape is the skill's own snippet, on invented ledger names, built into a pack in
memory and a set file in a temporary folder. No question set, fixture or sample client is read.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy

import pytest
import yaml

from kge.compiler import checks, standards
from kge.compiler.pack import Pack
from kge.eval import sets
from kge.pipeline.settings import ROOT
from kge.semantics import blocks, coverage

SKILLS = ROOT / "skills"
HEADING = "Question shapes: tests first"
SHAPE_SECTIONS = ("phrases", "shapes", "recipes", "shape_tests", "examples")
SECTIONS = {"measures": "measures", "dimensions": "dimensions", "filters": "filters", "time": "time",
            "comparisons": "comparisons", "derived": "derived"}
LOCK = {"sources": {"ledger": {"tables": [{"name": "ledger", "columns": [
    {"name": "amount", "type": "DOUBLE"}, {"name": "status", "type": "VARCHAR"}, {"name": "booked", "type": "DATE"}]}]}}}


def skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def section(text: str, heading: str) -> str:
    """The body under one level-two heading, up to the next."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, heading
    return match.group(1)


def fences(body: str, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)^```$", body, re.M | re.S)


def closed(body: str) -> dict[str, list[str]]:
    """The closed-lists table as {label: [members]}."""
    lines = body.split("| List | Members |\n", 1)[1].splitlines()
    rows = {}
    for line in lines[1:]:
        if not line.startswith("| "):
            break
        label, members = [cell.strip() for cell in line.strip("|").split("|")]
        rows[label] = re.findall(r"`([^`]+)`", members)
    return rows


def platform_lists() -> dict[str, list[str]]:
    out = {"Slot kinds": list(standards.SLOT_KINDS), "Markers": list(standards.MARKERS),
           "Position constraints": list(standards.CONSTRAINTS), "Stages": list(standards.STAGES),
           "Operations": list(standards.load().operations)}
    for section_name, label in SECTIONS.items():
        out[f"Blocks: {label}"] = [n for n, b in blocks.CATALOGUE.items() if b.section == section_name]
    out["Blocks: order and limit"] = ["order", "limit"]
    return out


def same_lists(table: dict[str, list[str]]) -> None:
    expected = platform_lists()
    assert table == expected
    named = set().union(*(set(v) for k, v in table.items() if k.startswith("Blocks: ")))
    assert named == set(blocks.CATALOGUE)


def body() -> str:
    return section(skill("author-pack"), HEADING)


def test_author_pack_names_each_closed_list_as_the_platform_holds_it():
    same_lists(closed(body()))


@pytest.mark.parametrize("plant", ["drop", "extra"])
def test_a_closed_list_that_drifts_from_the_code_is_caught(plant):
    table = deepcopy(closed(body()))
    if plant == "drop":
        table["Slot kinds"].pop()
    else:
        table["Blocks: per row"] = ["per_row"]
    with pytest.raises(AssertionError):
        same_lists(table)


ORDER = ("`reference_sql_approved_by`", "`also_asked_as`", "slots: [", "in `recipes`", "approved_by, approved_on}", "in `phrases`")


def in_order(text: str) -> bool:
    places = [text.find(marker) for marker in ORDER]
    return -1 not in places and places == sorted(places)


def test_author_pack_puts_tests_before_shapes():
    text = body()
    steps = text.split("### Closed lists", 1)[0]
    assert in_order(steps)
    for needed in ("at least three", "`missing_building_block`", "`missing_join`", "do not grow",
                   "never put in a model's prompt", "Never copy a held-out question"):
        assert needed in text, needed
    first, sixth = steps.index("1. **Tests.**"), steps.index("6. **Phrases**")
    assert not in_order(steps[:first] + steps[sixth:] + steps[first:sixth])


def worked():
    text = body()
    [doc] = fences(text, "yaml")
    [item_file] = fences(text, "json")
    return yaml.safe_load(doc), json.loads(item_file)


def ledger_pack(tmp_path, excerpt: dict, **extra) -> Pack:
    data = {"manifest": {"meta_model_version": "1.6.0"},
            "glossary": [{"id": "amount", "name": "Amount"}],
            "measures": [{"id": "amount"}],
            "contexts": [{"id": "posted"}, {"id": "pending"}],
            "calendars": [{"id": "calendar"}],
            "tests": {"sets": [{"name": "shapes_ledger", "version": "v1", "items": ["L01"]}]},
            **deepcopy(excerpt), **extra}
    return Pack("ledger", tmp_path, data=data, lock=deepcopy(LOCK))


def shape_findings(p: Pack, pre: standards.Preload) -> list:
    shaped = [f for f in checks.sections(p) if checks.part(f) in SHAPE_SECTIONS]
    return (shaped + checks.ids(p, ["shapes", "recipes", "examples"]) + checks.phrases(p, pre) + checks.shapes(p, pre)
            + checks.recipes(p, pre) + checks.examples(p, pre))


def read_items(tmp_path, item_file: dict) -> list[dict]:
    path = tmp_path / "shapes_ledger_v1.json"
    path.write_text(json.dumps(item_file))
    return sets.read(path)["items"]


def test_the_worked_shape_passes_the_shape_checks_on_the_platform_preload(tmp_path):
    excerpt, item_file = worked()
    pre = standards.load()
    assert set(excerpt) == set(SHAPE_SECTIONS)
    assert shape_findings(ledger_pack(tmp_path, excerpt), pre) == []
    phrase = excerpt["phrases"][0]["phrase"]
    assert phrase not in coverage.OPERATORS and phrase not in coverage.SCAFFOLD and phrase not in coverage.SCAFFOLD_PHRASES
    assert phrase.casefold() not in {p.casefold() for p in pre.phrases}
    items = read_items(tmp_path, item_file)
    assert any(phrase in i["question"].split() for i in items if i.get("variant_of") == "L01")
    words = {w.strip(",") for i in items if i.get("variant_of") == "L01" or i["id"] == "L01" for w in i["question"].lower().split()}
    assert not {w for w in words if coverage.OPERATORS.get(w) in excerpt["shapes"][0]["forbids"]}
    [base] = [i for i in items if i["id"] == "L01"]
    assert sets.alternates(items)["L01"] >= pre.min_also_asked_as
    assert sets.approval_problems(base) == []
    assert sets.approved(base)


def planted(excerpt: dict, plant: str) -> dict:
    out = deepcopy(excerpt)
    if plant == "pattern block":
        out["recipes"][0]["blocks"][0]["block"] = "pattern"
    elif plant == "word slot":
        out["shapes"][0]["slots"][0]["kind"] = "word"
    elif plant == "shadow without approval":
        out["shapes"][0]["stage"] = "shadow"
        for key in ("approved_by", "approved_on"):
            del out["examples"][0][key]
    return out


@pytest.mark.parametrize(("plant", "rules"), [("pattern block", {"S34"}), ("word slot", {"S01", "S33"}),
                                              ("shadow without approval", {"S35"})])
def test_a_planted_fault_in_the_worked_shape_is_refused(tmp_path, plant, rules):
    excerpt, _ = worked()
    found = shape_findings(ledger_pack(tmp_path, planted(excerpt, plant)), standards.load())
    assert found and {f.rule for f in found} & rules, found


def test_the_worked_phrase_declared_as_an_alias_too_is_refused(tmp_path):
    excerpt, _ = worked()
    phrase = excerpt["phrases"][0]["phrase"]
    p = ledger_pack(tmp_path, excerpt, terminology={"aliases": [{"phrase": phrase, "term": "amount"}]})
    assert [f.rule for f in checks.phrases(p, standards.load())] == ["S32"]


@pytest.mark.parametrize("plant", ["two phrasings", "a slashed date", "no approval"])
def test_a_worked_test_item_below_the_bar_is_caught(tmp_path, plant):
    _, item_file = worked()
    item = item_file["items"][0]
    if plant == "two phrasings":
        item["also_asked_as"] = item["also_asked_as"][:2]
    elif plant == "a slashed date":
        item["reference_sql_approved_on"] = "01/07/2025"
    else:
        del item["reference_sql_approved_by"], item["reference_sql_approved_on"]
    items = read_items(tmp_path, item_file)
    [base] = [i for i in items if i["id"] == "L01"]
    if plant == "two phrasings":
        assert sets.alternates(items)["L01"] < standards.load().min_also_asked_as
    elif plant == "a slashed date":
        assert sets.approval_problems(base) and not sets.approved(base)
    else:
        assert sets.approval_problems(base) == [] and not sets.approved(base)


GATE_REPORT = ("`shapes`", "tests passed of total", "wrong figures", "worked examples replayed", "live shadow",
               "M1 to M7", "(M8)", "as `fell` does", "in the report's `gate`",
               "no `shapes` list, or an empty one, the report gives no shape evidence")


def reports_the_lifecycle(text: str) -> bool:
    return all(needed in text for needed in GATE_REPORT) and "never lower a requested stage" in text.lower()


def test_build_and_gate_reports_each_shape_and_blocks_below_the_bar():
    text = skill("build-and-gate")
    assert reports_the_lifecycle(text)
    assert "/shapes" not in text
    assert not reports_the_lifecycle(text.replace("wrong figures", "figures"))


def proposes_candidates_only(text: str) -> bool:
    sentences = re.split(r"(?<=[.:])\s+", text)
    phrases = [s for s in sentences if "candidate phrases" in s]
    never = [s for s in sentences if s.startswith("Never upload them")]
    return (bool(phrases) and all("never from `heldout`" in s for s in phrases) and "candidates only" in text
            and bool(never) and all(w in never[0] for w in ("a template", "recipe", "worked example")))


def test_land_and_draft_proposes_candidate_phrases_only():
    text = skill("land-and-draft")
    assert proposes_candidates_only(text)
    assert "shapes[]" not in text and "recipes[]" not in text
    assert not proposes_candidates_only(text.replace("candidates only", "candidates"))
