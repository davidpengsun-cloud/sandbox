"""Bounded improvement edits (#532): the ledger signature, template groups, the new set file, the template and phrase
checks, propose with templates, the candidate pack written without rewriting its text, and the release note line.

    .venv/bin/python -m pytest tests/unit/test_improve_bounds.py -q

Small synthetic misses, a synthetic preload and a stub model only, with no question set file. The candidate pack cases
write a scratch copy of a committed pack, raised to meta-model 1.6.0 by text so its comments stay, and draft worked
examples over that pack's own declared ids.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from kge.adapters.model import LLM, MOCK
from kge.compiler import standards
from kge.eval import sets as setfiles
from kge.improve import edits

DEMO = Path(__file__).resolve().parents[2]
SAMPLE = "thornbury"

OPERATIONS = ("asc", "comparison", "count", "desc", "difference", "ge", "gt", "le", "lt", "percent_change", "previous_period",
              "previous_year", "ratio")
CONTEXT, MEASURE, CALENDAR = "gross_written_premium", "gross-written-premium", "calendar"

QUESTIONS = [
    "written premium less written premium as reported for January 2025",
    "gap between written premium and as reported written premium in January 2025",
    "January 2025 written premium minus the as reported figure",
    "how far is written premium from the as reported number for January 2025",
    "written premium against its as reported value, difference, January 2025",
]


def preload(**over) -> standards.Preload:
    """A synthetic preload: thirteen operations, no platform phrase, shape or recipe unless over gives one."""
    return standards.Preload("1.1.0", {}, {}, {}, {}, operations=OPERATIONS, **over)


def ledger(names=("difference",), slots=None, unknown=False, unapplied=()) -> dict:
    return {"names": list(names), "slots": dict({"context": 2} if slots is None else slots), "unknown": unknown,
            "unapplied": [{"text": t, "start": 0, "end": len(t)} for t in unapplied]}


def miss(item: str, question: str, set_name: str = "team_underwriting_v1", diagnosis: str = "missing_template",
         **over) -> dict:
    d = {"source": "ab", "set": set_name, "id": item, "question": question, "diagnosis": diagnosis, "kind": "figure",
         "expect": {"value": 1250.0}, "unit": "underwriting", "why": "no shape matches the question", "ledger": ledger()}
    d.update(deepcopy(over))
    return d


def group_of(n: int = 4, **over) -> list[dict]:
    return [miss(f"T-{k:02d}", q, **over) for k, q in enumerate(QUESTIONS[:n], 1)]


def name(i: str) -> dict:
    return {"id": i, "evidence": []}


def measure(i: str, mode: str = "current") -> dict:
    return {"kind": "measure", "id": i, "measure": name(MEASURE), "context": name(CONTEXT), "mode": mode, "filters": []}


PLAN = {"schema": "query-plan/v1", "layout": "aligned", "measures": [measure("m1"), measure("m2", "as_reported")],
        "dimensions": [], "filters": [],
        "time": {"kind": "single", "period": {"kind": "month", "value": "2025-01", "calendar": name(CALENDAR)}},
        "comparisons": [{"kind": "contexts", "id": "cmp", "left": "m1", "right": "m2", "evidence": []}],
        "derived": [{"kind": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other", "evidence": []}],
        "order": [], "limit": None, "obligations": []}

RECIPE = {"id": "context-gap", "blocks": [
    {"block": "contexts", "id": "cmp", "left": "$context[0]", "right": "$context[1]"},
    {"block": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other"},
    {"block": "single", "id": "t", "period": "$period"}]}
SHAPE = {"id": "context-gap", "label": "Gap between two contexts",
         "slots": [{"name": "context", "kind": "context", "count": 2},
                   {"name": "period", "kind": "period", "count": {"min": 0, "max": 1}}],
         "requires": ["difference"], "forbids": ["comparison"], "recipe": "context-gap", "stage": "specify"}
GROUP = {"names": ["difference"], "slots": {"context": 2}}
IDS = [f"context-gap-{k:02d}" for k in range(1, 5)]


def template_reply(shape: dict | None = None, recipe: dict | None = None, n: int = 4, **over) -> dict:
    reply = {"kind": "template", "shape": deepcopy(shape or SHAPE), "recipe": deepcopy(recipe or RECIPE),
             "plans": {f"item-{k:02d}": deepcopy(PLAN) for k in range(1, n + 1)}, "why": "four misses share one signature"}
    reply.update(deepcopy(over))
    return reply


class Stub:
    """A stand-in model: provider stub, a canned JSON reply (or one made from the prompt), every prompt recorded."""

    provider = "stub"

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def json(self, prompt: str):
        self.prompts.append(prompt)
        return deepcopy(self.reply(prompt) if callable(self.reply) else self.reply)


def evidence(**over) -> dict:
    ev = {
        "certified_terms": {MEASURE: {"id": MEASURE, "name": "Gross Written Premium", "status": "certified"}},
        "existing_aliases": {"written premium": MEASURE},
        "candidate_phrases": [],
        "columns": {"underwriting.premium.written_premium_usd": {
            "column": "underwriting.premium.written_premium_usd", "unit": "underwriting", "table": "premium",
            "name": "written_premium_usd", "type": "DECIMAL(14,2)",
            "description": "Premium written, in USD; the business calls this top line", "is_key": False,
            "certified_terms": [MEASURE]}},
        "term_columns": {MEASURE: ["underwriting.premium.written_premium_usd"]},
        "declared": {"contexts": [CONTEXT], "measures": [MEASURE], "calendars": [CALENDAR]},
        "pack_phrases": [],
    }
    ev.update(deepcopy(over))
    return ev


def phrase_miss(item: str = "P-01", phrase: str = "net of", operation: str = "difference", unapplied=("net of",),
                **edit_over) -> dict:
    edit = {"section": "phrases", "phrase": phrase, "operation": operation, **edit_over}
    return miss(item, f"written premium {phrase} returns for 2025 ({item})", diagnosis="missing_phrase",
                ledger=ledger(names=[], slots={}, unapplied=unapplied), edit=edit)


def test_a_signature_is_the_sorted_names_and_the_nonzero_slot_counts():
    a = miss("a", "q", ledger=ledger(names=["difference", "from_to"], slots={"context": 2, "period": 0}))
    b = miss("b", "q", ledger=ledger(names=["from_to", "difference", "difference"], slots={"context": 2}))
    assert edits.signature(a) == edits.signature(b) == (("difference", "from_to"), (("context", 2),))
    assert edits.describe(edits.signature(a)) == "[difference, from_to; context x2]"


@pytest.mark.parametrize("bad", [
    ledger(unknown=True),
    "not a ledger",
    {"names": "difference", "slots": {"context": 2}, "unknown": False},
    {"names": ["difference"], "slots": [["context", 2]], "unknown": False},
    {"names": ["difference", ""], "slots": {"context": 2}, "unknown": False},
    ledger(slots={"galaxy": 2}),
    ledger(slots={"context": -1}),
    ledger(slots={"context": True}),
], ids=["unknown", "not-a-mapping", "names-not-a-list", "slots-not-a-mapping", "empty-name", "unknown-slot-kind",
        "negative-count", "boolean-count"])
def test_a_ledger_that_is_unknown_malformed_or_counts_an_unknown_slot_kind_has_no_signature(bad):
    assert edits.signature(miss("a", "q", ledger=bad)) is None
    assert edits.signature({"id": "a"}) is None


def test_held_out_misses_never_join_a_group_and_the_group_drops_below_need():
    held = [miss("H-01", QUESTIONS[3], set_name="heldout"), miss("H-02", QUESTIONS[4], set_name="heldout_x")]
    diags = group_of(3) + held
    groups, notes = edits.template_groups(diags, 4)
    assert groups == []
    assert len(notes) == 1 and notes[0]["why"].startswith("3 learnable misses share the ledger signature [difference; "
                                                          "context x2]")
    stub = Stub(template_reply())
    out = edits.propose(diags, [], evidence(), stub, templates=True, preload=preload())
    assert stub.prompts == []
    assert [e for e in out["edits"] if e["kind"] == "shape"] == []
    assert any("3 learnable misses" in n["why"] for n in out["notes"])
    text = json.dumps(out)
    assert QUESTIONS[3] not in text and QUESTIONS[4] not in text


def test_held_out_misses_never_supply_an_item_or_an_alternate_of_a_drafted_template():
    held = [miss("H-01", "a held out phrasing of the gap", set_name="heldout"),
            miss("H-02", "another held out phrasing of the gap", set_name="heldout_x")]
    stub = Stub(template_reply())
    out = edits.propose(group_of(4) + held, [], evidence(), stub, templates=True, preload=preload())
    (edit,) = out["edits"]
    assert "held out" not in json.dumps(edit) and "held out" not in "".join(stub.prompts)
    assert [i["drafted_from"] for i in edit["set"]["document"]["items"]] == [
        f"team_underwriting_v1:T-{k:02d}" for k in range(1, 5)]


def test_page_misses_never_join_a_group():
    page = [miss(f"PG-{k}", q, source="page") for k, q in enumerate(QUESTIONS[3:], 1)]
    groups, notes = edits.template_groups(group_of(3) + page, 4)
    assert groups == [] and len(notes) == 1 and notes[0]["why"].startswith("3 learnable misses")


def test_a_repeated_question_counts_once_in_its_group():
    again = miss("T-09", "  " + QUESTIONS[0].upper().replace(" ", "   ") + " ")
    groups, notes = edits.template_groups(group_of(3) + [again], 4)
    assert groups == [] and notes[0]["why"].startswith("3 learnable misses")
    groups, _ = edits.template_groups(group_of(4) + [again], 4)
    assert [d["id"] for d in groups[0]["diags"]] == ["T-01", "T-02", "T-03", "T-04"]
    assert [d["id"] for d in groups[0]["members"]] == ["T-01", "T-02", "T-03", "T-04", "T-09"]


def test_of_two_eligible_groups_the_larger_is_kept_and_the_other_held_for_a_later_run():
    ratio = ledger(names=["ratio"], slots={"measure": 2})
    smaller = group_of(4)
    larger = [miss(f"R-{k:02d}", f"ratio question number {k}", ledger=ratio) for k in range(1, 6)]
    groups, notes = edits.template_groups(smaller + larger, 4)
    assert [len(g["diags"]) for g in groups] == [5, 4] and notes == []
    assert groups[0]["names"] == ["ratio"]
    out = edits.propose(smaller + larger, [], evidence(), None, templates=True, preload=preload())
    whys = [n["why"] for n in out["notes"]]
    assert out["edits"] == []
    assert whys[0].startswith("5 misses share the ledger signature [ratio; measure x2]; a template proposal needs a model")
    assert whys[1] == ("4 misses share the ledger signature [difference; context x2]; held for a later run: one template "
                       "and one new set file per proposal")


def test_the_new_set_file_drafts_one_item_a_miss_with_the_others_as_its_phrasings(tmp_path):
    diags = group_of(4, expect={"value": 1250.0, "reference_sql": "select 1", "reference_sql_approved_by": "someone",
                                "reference_sql_approved_on": "2026-01-02"})
    stem, document, ids = edits.set_document("context-gap", diags)
    assert stem == edits.set_stem("context-gap") == "shapes_context_gap_v1"
    assert ids == IDS
    items = document["items"]
    assert [i["id"] for i in items] == IDS
    for k, item in enumerate(items):
        assert item["question"] == QUESTIONS[k]
        assert item["also_asked_as"] == [q for j, q in enumerate(QUESTIONS[:4]) if j != k]
        assert item["expect"] == {"value": 1250.0, "reference_sql": "select 1"}
        assert item["drafted_from"] == f"team_underwriting_v1:T-{k + 1:02d}"
        assert item["kind"] == "figure"
    assert "reference_sql_approved_by" in diags[0]["expect"]
    path = tmp_path / f"{stem}.json"
    path.write_text(edits.set_text(document))
    read = setfiles.read(path)
    counts = setfiles.alternates(read["items"])
    assert read["version"] == "v1" and {i: counts.get(i, 0) for i in IDS} == {i: 3 for i in IDS}


def test_a_set_with_too_few_questions_is_refused():
    with pytest.raises(ValueError, match="fewer than 3 alternate phrasings"):
        edits.set_document("context-gap", group_of(3))


def good_template() -> dict:
    reply = template_reply()
    reply["plans"] = {i: deepcopy(PLAN) for i in IDS}
    return reply


def changed(path: str, value) -> dict:
    reply = good_template()
    target = reply
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[key]
    if value is None:
        target.pop(keys[-1])
    else:
        target[keys[-1]] = deepcopy(value)
    return reply


def test_a_good_template_passes():
    assert edits.validate_template(good_template(), GROUP, IDS, preload()) == (True, "ok")
    assert edits.holds_on(SHAPE, ["difference"], {"context": 2})


@pytest.mark.parametrize("reply,match", [
    (changed("shape.stage", "shadow"), "starts at specify"),
    (changed("shape.replaces", "planshapes.compose:contexts"), "does not replace a coded builder"),
    (changed("shape.constraints", [{"before": ["difference", "context"]}]), "declares no position constraints"),
    (changed(f"plans.{IDS[2]}", None), "every candidate test needs a drafted worked example"),
    (changed(f"plans.{IDS[0]}.approved_by", "someone"), "never approves"),
    (changed("confidence", 0.9), "disallowed fields confidence in a template reply"),
    (changed("shape.requires", ["difference", "ratio"]), "would not match the misses"),
    (changed("shape.slots", [{"name": "context", "kind": "context", "count": 3}]), "would not match the misses"),
    (changed("recipe.blocks", [{"block": "nowhere", "id": "x"}]), "block catalogue"),
], ids=["shadow", "replaces", "constraints", "plan-missing", "approved", "extra-key", "requires-outside",
        "slot-count-outside", "block-outside"])
def test_a_template_outside_the_bound_is_refused(reply, match):
    ok, reason = edits.validate_template(reply, GROUP, IDS, preload())
    assert not ok and match in reason, reason


def test_a_template_taking_a_platform_shape_id_is_refused():
    taken = preload(shapes={"context-gap": {**SHAPE, "stage": "graph_first"}})
    ok, reason = edits.validate_template(good_template(), GROUP, IDS, taken)
    assert not ok and "is a platform shape's" in reason


def test_propose_with_templates_drafts_one_bounded_shape_edit():
    stub = Stub(template_reply())
    out = edits.propose(group_of(4), [], evidence(), stub, templates=True, preload=preload())
    assert out["proposer"] == "model" and out["refused"] == [] and out["requests"] == []
    (edit,) = out["edits"]
    assert edit["id"] == "shape:context-gap" and edit["kind"] == "shape" and edit["section"] == "shapes"
    assert edit["state"] == "drafted" and edit["unit"] == "underwriting"
    assert [e["id"] for e in edit["examples"]] == [f"context-gap-example-{k:02d}" for k in range(1, 5)]
    assert [e["question"] for e in edit["examples"]] == QUESTIONS[:4]
    assert edit["set"]["stem"] == "shapes_context_gap_v1" and edit["set"]["name"] == "shapes_context_gap"
    assert edit["shape_test"] == {"shape": "context-gap", "tests": {"set": "shapes_context_gap", "items": IDS}}
    assert not edits._approves(edit)
    assert "approv" not in json.dumps(edit)
    (prompt,) = stub.prompts
    assert "improve-template-v1" in prompt
    payload = json.loads(prompt.split("<miss>", 1)[1].split("</miss>", 1)[0])
    assert payload["declared"] == evidence()["declared"]
    assert payload["signature"] == {"names": ["difference"], "slots": {"context": 2}}


def test_propose_with_templates_and_no_model_notes_the_group():
    out = edits.propose(group_of(4), [], evidence(), None, templates=True, preload=preload())
    assert out["edits"] == [] and out["requests"] == []
    assert len(out["notes"]) == 1 and "needs a model" in out["notes"][0]["why"]


def test_without_templates_propose_is_the_per_miss_path():
    alias = {"source": "ab", "set": "team_underwriting_v1", "id": "A-01", "question": "top line for January 2025",
             "diagnosis": "missing_alias", "phrase": "top line", "why": "no term", "outcome": "proposal",
             "edit": {"section": "terminology.aliases", "phrase": "top line", "term": MEASURE}}
    diags = group_of(4) + [alias]
    off = edits.propose(deepcopy(diags), [], evidence(), None, templates=False)
    assert off == edits.propose(deepcopy(diags), [], evidence(), None)
    assert [e["id"] for e in off["edits"]] == ["alias:top line"]
    assert [r["id"] for r in off["requests"]] == ["T-01", "T-02", "T-03", "T-04"]


def test_a_careful_mock_asked_for_a_template_gives_a_note_and_no_edit():
    out = edits.propose(group_of(4), [], evidence(), LLM(MOCK, MOCK, mode="careful"), templates=True, preload=preload())
    assert out["proposer"] == "mock" and out["edits"] == [] and out["refused"] == []
    assert len(out["notes"]) == 1 and out["notes"][0]["id"] == "T-01"


def test_the_alias_prompt_is_unchanged_by_the_new_evidence_keys():
    diag = {"id": "A-01", "question": "top line for January 2025", "diagnosis": "missing_alias", "phrase": "top line",
            "matching_comments": [{"column": "underwriting.premium.written_premium_usd", "term": MEASURE}],
            "why": "no term"}
    old = {k: v for k, v in evidence().items() if k not in ("declared", "pack_phrases")}
    assert edits.build_prompt(diag, evidence()) == edits.build_prompt(diag, old)
    payload = {"certified_terms": [MEASURE], "diagnosis": "missing_alias", "existing_aliases": ["written premium"],
               "id": "A-01", "matching_comments": diag["matching_comments"], "phrase": "top line",
               "prompt_version": "improve-propose-v1", "question": "top line for January 2025", "why": "no term"}
    assert edits.build_prompt(diag, evidence()) == (
        "You are a knowledge-graph improvement proposer (improve-propose-v1).\n"
        "Given the diagnosed miss below, return JSON only: {\"kind\": \"proposal\"|\"note\"|\"request\", "
        "\"edit\": {\"section\": \"terminology.aliases\", \"phrase\": str, \"term\": str} | null, \"why\": str}.\n"
        f"<miss>\n{json.dumps(payload, sort_keys=True)}\n</miss>")


def test_a_phrase_the_ledger_left_unread_is_accepted():
    out = edits.propose([phrase_miss()], [], evidence(), None, preload=preload())
    assert out["refused"] == []
    (edit,) = out["edits"]
    assert (edit["id"], edit["kind"], edit["section"], edit["phrase"], edit["operation"], edit["state"]) == (
        "phrase:net of", "phrase", "phrases", "net of", "difference", "drafted")
    assert edit["items"] == ["P-01"] and edit["unit"] == "underwriting"


@pytest.mark.parametrize("diag,ev,match", [
    (phrase_miss(operation="sideways"), {}, "'sideways' is not a platform operation"),
    (phrase_miss(filler=True), {}, "filler phrases stay an author-pack edit"),
    (phrase_miss(operation="filler"), {}, "'filler' is not a platform operation"),
    (phrase_miss(phrase="returns"), {}, "is not a word the question's ledger left unread"),
    (phrase_miss(term=MEASURE), {}, "disallowed fields term in a phrases edit"),
    (phrase_miss(), {"candidate_phrases": ["net of"]}, "already held in the pack, the steward queue or open drafts"),
    (phrase_miss(), {"pack_phrases": ["net of"]}, "already held in the pack, the steward queue or open drafts"),
], ids=["unknown-operation", "filler-key", "filler-operation", "not-unapplied", "extra-key", "candidate", "pack-phrase"])
def test_a_phrase_outside_the_bound_is_refused(diag, ev, match):
    out = edits.propose([diag], [], evidence(**ev), None, preload=preload())
    assert out["edits"] == []
    assert len(out["refused"]) == 1 and match in out["refused"][0]["reason"], out["refused"]


def test_one_phrase_for_two_operations_is_refused_as_ambiguous():
    diags = [phrase_miss("P-01"), phrase_miss("P-02", operation="ratio")]
    out = edits.propose(diags, [], evidence(), None, preload=preload())
    assert out["edits"] == []
    assert sorted((r["id"], r["edit"]["operation"]) for r in out["refused"]) == [("P-01", "difference"), ("P-02", "ratio")]
    assert all("ambiguous" in r["reason"] for r in out["refused"])
    assert [n["why"] for n in out["notes"]] == ["refused: phrase 'net of' is ambiguous, proposed for difference, ratio"]


def test_a_model_is_asked_about_a_missing_phrase_with_the_phrase_prompt():
    reply = {"kind": "proposal", "edit": {"section": "phrases", "phrase": "net of", "operation": "difference"},
             "why": "net of marks a difference"}
    stub = Stub(reply)
    diag = phrase_miss()
    diag.pop("edit")
    out = edits.propose([diag], [], evidence(), stub, preload=preload())
    (prompt,) = stub.prompts
    assert "(improve-phrase-v1)" in prompt
    payload = json.loads(prompt.split("<miss>", 1)[1].split("</miss>", 1)[0])
    assert payload["unapplied"] == ["net of"] and payload["operations"] == sorted(OPERATIONS)
    assert [e["id"] for e in out["edits"]] == ["phrase:net of"] and out["edits"][0]["proposer"] == "model"


def test_the_improvement_flow_does_not_pass_templates_yet():
    source = (DEMO / "kge/improve/flow.py").read_text()
    calls = re.findall(r"edits_mod\.propose\((.*?)\n\s*\)", source, flags=re.S)
    assert len(calls) == 1 and "templates" not in calls[0]


def test_an_alias_only_change_note_reads_as_it_always_has():
    alias = {"section": "terminology.aliases", "phrase": "top-line turnover", "term": "revenue"}
    assert edits.change_note([alias], "rules", "team_leasing_v1 0 -> 1 of 1") == (
        "Add alias 'top-line turnover' -> revenue (by rules; A/B: team_leasing_v1 0 -> 1 of 1)")


def test_a_mixed_change_note_joins_aliases_phrases_and_the_template_in_order():
    shape = {"section": "shapes", "shape": {"id": "context-gap"}, "set": {"items": IDS}}
    phrase = {"section": "phrases", "phrase": "net of", "operation": "difference"}
    alias = {"section": "terminology.aliases", "phrase": "top line", "term": MEASURE}
    assert edits.change_note([shape, phrase, alias], "model", "team_underwriting_v1 0 -> 4 of 4") == (
        "Add alias 'top line' -> gross-written-premium; phrase 'net of' marks difference; template context-gap at "
        "specify with 4 candidate tests (by model; A/B: team_underwriting_v1 0 -> 4 of 4)")


def scratch(root: Path, version: str = "1.6.0", text_change=None) -> Path:
    """A scratch copy of the sample pack, its fixture left out, raised to version by text so every comment stays."""
    clients = root / "clients"
    shutil.copytree(DEMO / "clients" / SAMPLE, clients / SAMPLE, ignore=shutil.ignore_patterns("fixture"))
    path = clients / SAMPLE / "pack.yaml"
    text = path.read_text()
    minor = version.rsplit(".", 1)[0]
    for old, new in (("meta_model_version: 1.2.0", f"meta_model_version: {version}"),
                     ('platform: {meta_model: "^1.2"', f'platform: {{meta_model: "^{minor}"'),
                     ('schema: {meta_model: "1.2"', f'schema: {{meta_model: "{minor}"')):
        assert text.count(old) == 1
        text = text.replace(old, new)
    path.write_text(text_change(text) if text_change else text)
    return clients


def bundle(shape_id: str = "context-gap") -> dict:
    shape = {**SHAPE, "id": shape_id, "recipe": shape_id}
    recipe = {**RECIPE, "id": shape_id}
    out = edits.propose(group_of(4), [], evidence(), Stub(template_reply(shape, recipe)), templates=True,
                        preload=preload())
    (edit,) = out["edits"]
    return edit


def phrase_edit() -> dict:
    out = edits.propose([phrase_miss(phrase="less", unapplied=("less",))], [], evidence(), None, preload=preload())
    (edit,) = out["edits"]
    return edit


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    root = tmp_path_factory.mktemp("bounds")
    clients = scratch(root)
    source = (clients / SAMPLE / "pack.yaml").read_bytes()
    dest = root / "open"
    edit = bundle()
    result = edits.write_candidate_pack(clients, SAMPLE, dest, [edit, phrase_edit()])
    return {"clients": clients, "source": source, "dest": dest, "edit": edit, "result": result,
            "text": (dest / SAMPLE / "pack.yaml").read_text()}


def changes(before: str, after: str) -> list[str]:
    return [ln for ln in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


def test_a_bundle_and_a_phrase_are_kept_with_no_compiler_finding(candidate):
    version, kept, refused = candidate["result"]
    assert refused == [], "\n".join(r["reason"] for r in refused)
    assert version == "1.1.0" and [e["kind"] for e in kept] == ["shape", "phrase"]
    data = yaml.safe_load(candidate["text"])
    assert data["manifest"]["inputs"]["sets"] == ["golden", "shapes_context_gap_v1"]
    assert data["tests"]["sets"][-1] == {"name": "shapes_context_gap", "version": "v1", "items": IDS}
    assert data["phrases"] == [{"phrase": "less", "operation": "difference"}]
    assert [s["id"] for s in data["shapes"]] == ["context-gap"] and [r["id"] for r in data["recipes"]] == ["context-gap"]
    assert [e["id"] for e in data["examples"]] == [f"context-gap-example-{k:02d}" for k in range(1, 5)]


def test_the_candidate_text_only_adds_lines_and_keeps_every_comment(candidate):
    before, after = candidate["source"].decode(), candidate["text"]
    removed = [ln for ln in changes(before, after) if ln.startswith("-")]
    assert sorted(removed) == ["-    sets: [golden]", "-  version: 1.0.1"]
    added = [ln for ln in changes(before, after) if ln.startswith("+")]
    assert "+  version: 1.1.0" in added and "+    sets: [golden, shapes_context_gap_v1]" in added
    comments = [ln for ln in before.splitlines() if ln.lstrip().startswith("#")]
    assert len(comments) > 5
    assert [ln for ln in after.splitlines() if ln.lstrip().startswith("#")] == comments


def test_the_source_pack_is_untouched(candidate):
    assert (candidate["clients"] / SAMPLE / "pack.yaml").read_bytes() == candidate["source"]


def test_the_kept_template_names_its_new_set_file(candidate):
    _, kept, _ = candidate["result"]
    stem, document = edits.candidate_set(kept)
    assert stem == "shapes_context_gap_v1" and document == candidate["edit"]["set"]["document"]
    assert json.loads(edits.set_text(document)) == document
    assert edits.candidate_set([phrase_edit()]) is None


def test_a_second_template_is_refused(tmp_path):
    clients = scratch(tmp_path)
    _, kept, refused = edits.write_candidate_pack(clients, SAMPLE, tmp_path / "open", [bundle(), bundle("other-gap")])
    assert [e["id"] for e in kept] == ["shape:context-gap"]
    assert [(r["edit"]["shape"], r["reason"]) for r in refused] == [
        ("other-gap", "one template and one new set file per proposal")]


def test_a_pack_written_before_meta_model_1_6_refuses_the_template(tmp_path):
    clients = scratch(tmp_path, version="1.2.0")
    _, kept, refused = edits.write_candidate_pack(clients, SAMPLE, tmp_path / "open", [bundle()])
    assert kept == [] and len(refused) == 1
    assert "needs meta-model 1.6.0 or later" in refused[0]["reason"], refused[0]["reason"]


def test_a_pack_naming_no_test_sets_refuses_the_template_as_unplaceable(tmp_path):
    def drop_tests(text: str) -> str:
        head, tail = text.split("\ntests:\n", 1)
        assert tail == "  sets:\n    - {name: golden, version: v1}\n"
        return head + "\n"
    clients = scratch(tmp_path, text_change=drop_tests)
    _, kept, refused = edits.write_candidate_pack(clients, SAMPLE, tmp_path / "open", [bundle()])
    assert kept == [] and len(refused) == 1
    assert refused[0]["reason"].startswith("the pack declares no tests.sets"), refused[0]["reason"]


def test_an_alias_only_candidate_keeps_the_old_diff_shape(tmp_path):
    clients = scratch(tmp_path)
    before = (clients / SAMPLE / "pack.yaml").read_text()
    alias = {"section": "terminology.aliases", "phrase": "premium written", "term": MEASURE, "proposer": "rules"}
    version, kept, refused = edits.write_candidate_pack(clients, SAMPLE, tmp_path / "open", [alias])
    assert refused == [], refused
    assert version == "1.1.0" and kept == [alias]
    after = (tmp_path / "open" / SAMPLE / "pack.yaml").read_text()
    assert changes(before, after) == ["-  version: 1.0.1", "+  version: 1.1.0",
                                      "+    - {phrase: premium written, term: gross-written-premium}"]


@pytest.mark.parametrize("reply", [
    changed("shape.slots", [{"name": "context", "kind": ["context"], "count": 2}]),
    changed("shape.slots", [{"name": "a", "kind": {"a": 1}}, {"name": "b", "kind": {"b": 1}}]),
    changed("shape.recipe", ["x"]) | {"recipe": {**RECIPE, "id": ["x"]}},
    changed("recipe.blocks", [{"block": ["contexts"], "id": "x"}]),
], ids=["kind-list", "kind-dict", "recipe-id-list", "block-list"])
def test_a_template_with_unhashable_values_is_refused_not_raised(reply):
    ok, reason = edits.validate_template(reply, GROUP, IDS, preload())
    assert not ok and reason.startswith("refused:"), reason
    out = edits.propose(group_of(4), [], evidence(), Stub(reply), templates=True, preload=preload())
    assert out["edits"] == [] and out["refused"]


def test_the_candidate_writer_refuses_an_edit_that_leaves_the_bound(tmp_path):
    clients = scratch(tmp_path)
    filler = {**phrase_edit(), "operation": "filler"}
    _, kept, refused = edits.write_candidate_pack(clients, SAMPLE, tmp_path / "open", [filler])
    assert kept == []
    assert [r["reason"] for r in refused] == ['added phrase "less" is a filler: an improvement edit never adds one']


def test_a_phrase_with_no_unit_for_its_review_history_is_refused():
    two_units = evidence()
    two_units["columns"]["claims.claim.paid_usd"] = {**two_units["columns"]["underwriting.premium.written_premium_usd"],
                                                    "column": "claims.claim.paid_usd", "unit": "claims"}
    out = edits.propose([{**phrase_miss(), "unit": None}], [], two_units, None, preload=preload())
    assert out["edits"] == []
    assert ["names no unit for its review history" in r["reason"] for r in out["refused"]] == [True]


def test_an_alias_whose_phrase_is_the_word_phrases_is_kept_beside_phrase_edits():
    ev = evidence()
    ev["columns"]["underwriting.premium.written_premium_usd"]["description"] = "Premium written; the business calls this phrases"
    alias = miss("A-01", "phrases for 2025", diagnosis="missing_alias",
                 edit={"section": "terminology.aliases", "phrase": "phrases", "term": MEASURE})
    out = edits.propose([alias, phrase_miss()], [], ev, None, preload=preload())
    assert [e["kind"] for e in out["edits"]] == ["alias", "phrase"]


def test_a_template_miss_with_no_question_never_joins_a_group():
    out = edits.propose([miss("blank", "")] + group_of(4), [], evidence(), None, templates=True, preload=preload())
    assert out["edits"] == [] and all(n["id"] != "blank" for n in out["notes"])
    assert edits.template_groups([miss("blank", "")], 4) == ([], [])
