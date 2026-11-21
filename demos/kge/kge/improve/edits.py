"""Propose step, allowlist check, and candidate pack writer for the improvement job.

The bound (#452 requirement 4): a proposal retains add-only aliases and may add phrases, plus new shapes, recipes,
shape tests and worked examples at specify, one stem in manifest.inputs.sets, one entry in tests.sets and one new set
file. It changes nothing else. In detail:
- terminology.aliases: add-only {phrase, term} for an existing certified term, backed by one matching column comment;
- phrases: add-only {phrase, operation}, the phrase a span the miss's ledger left unread, the operation a platform
  operation; a filler phrase stays an author-pack edit;
- at most one template bundle a proposal: one shape at stage specify, with no replaces and no position constraints,
  its recipe, one shape_tests entry, one drafted worked example per candidate test, one stem in manifest.inputs.sets,
  one entry in tests.sets, and one new set file, shapes_<shape>_v1;
- and a minor bump of manifest.version (e.g. 1.2.0 -> 1.3.0).
kge/improve/packtext.py writes each addition into the pack's text without rewriting it, and its bounded() refuses a
candidate pack that differs from the source in anything else.

A template's candidate tests come only from repeated learnable A/B misses that share a ledger signature (the sorted
operations and markers the question's ledger read, and the count of each slot kind): each miss becomes a test item,
with ids <shape>-NN, and the others become its alternate phrasings, each scored against the item's own expected
answer (owner question 1, as written; drafted_from records where every item came from). Its worked examples are
<shape>-example-NN. Held-out records and page questions never supply a test or a phrasing. Nothing here approves:
the drafted reference SQL and worked examples carry no approval field, and count towards nothing until a person
approves them.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from kge.pipeline.settings import ROOT
from kge.compiler import checks, emit, pack as packs, standards
from kge.compiler.checks import shape_condition
from kge.compiler.compile import VERSION as COMPILER_VERSION
from kge.eval import sets as setfiles
from kge.improve import packtext
from kge.improve.rules import _matching_comments
from kge.semantics.blocks import CATALOGUE
from kge.adapters.model import ExternalCallError
from kge.adapters.model import LLM, NotJSON, Refused
from kge.adapters.model import IMPROVE_TASK, MISS_CLOSE, MISS_OPEN

PROMPTS = {"propose": "improve-propose-v1", "phrase": "improve-phrase-v1", "template": "improve-template-v1"}
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ALLOWED_EDIT_KEYS = {"section", "phrase", "term"}
PHRASE_EDIT_KEYS = {"section", "phrase", "operation"}
TEMPLATE_REPLY_KEYS = {"kind", "shape", "recipe", "plans", "why"}
SHAPE_KEYS = {"id", "label", "slots", "requires", "forbids", "recipe", "stage"}
OUTSIDE = ("refused: section {section!r} is outside the allowlist (an edit adds terminology.aliases or phrases; shapes "
           "come only as a template proposal)")
ONE_TEMPLATE = "one template and one new set file per proposal"
PHRASE_KEY = object()
Signature = tuple[tuple[str, ...], tuple[tuple[str, int], ...]]


def bump_minor(version: str) -> str:
    """Increment the minor component of a semver string and reset patch to 0 (e.g. 1.2.3 -> 1.3.0)."""
    m = SEMVER.match(str(version).strip())
    if not m:
        raise ValueError(f"version {version!r} is not semver")
    return f"{int(m.group(1))}.{int(m.group(2)) + 1}.0"


def build_prompt(diag: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Build the improve-propose-v1 prompt for one diagnosed miss."""
    payload = {
        "prompt_version": PROMPTS["propose"],
        "id": diag.get("id"),
        "question": diag.get("question"),
        "diagnosis": diag.get("diagnosis"),
        "phrase": diag.get("phrase"),
        "matching_comments": diag.get("matching_comments") or [],
        "certified_terms": sorted((evidence.get("certified_terms") or {}).keys()),
        "existing_aliases": sorted((evidence.get("existing_aliases") or {}).keys()),
        "why": diag.get("why"),
    }
    return (
        f"{IMPROVE_TASK} ({PROMPTS['propose']}).\n"
        "Given the diagnosed miss below, return JSON only: {\"kind\": \"proposal\"|\"note\"|\"request\", "
        "\"edit\": {\"section\": \"terminology.aliases\", \"phrase\": str, \"term\": str} | null, \"why\": str}.\n"
        f"{MISS_OPEN}\n{json.dumps(payload, sort_keys=True)}\n{MISS_CLOSE}"
    )


def build_phrase_prompt(diag: dict[str, Any], preload: standards.Preload) -> str:
    """Build the improve-phrase-v1 prompt for one missing_phrase miss: the spans its ledger left unread, and the
    platform's operations a phrase may mark."""
    ledger = diag.get("ledger") if isinstance(diag.get("ledger"), dict) else {}
    payload = {
        "prompt_version": PROMPTS["phrase"],
        "id": diag.get("id"),
        "question": diag.get("question"),
        "diagnosis": diag.get("diagnosis"),
        "unapplied": [str(s.get("text")) for s in _spans(ledger)],
        "operations": sorted(preload.operations),
        "why": diag.get("why"),
    }
    return (
        f"{IMPROVE_TASK} ({PROMPTS['phrase']}).\n"
        "Given the diagnosed miss below, propose at most one phrase: one of its unapplied spans, word for word, that "
        "marks one of the operations. Never a filler phrase. Return JSON only: {\"kind\": \"proposal\"|\"note\"|\"request\", "
        "\"edit\": {\"section\": \"phrases\", \"phrase\": str, \"operation\": str} | null, \"why\": str}.\n"
        f"{MISS_OPEN}\n{json.dumps(payload, sort_keys=True)}\n{MISS_CLOSE}"
    )


def build_template_prompt(group: dict[str, Any], item_ids: list[str], evidence: dict[str, Any],
                          preload: standards.Preload) -> str:
    """Build the improve-template-v1 prompt for one signature group: the signature, the candidate tests, the closed
    lists a shape and a recipe draw from, the pack's declared ids a worked example's plan may name, and the rules."""
    payload = {
        "prompt_version": PROMPTS["template"],
        "signature": {"names": list(group["names"]), "slots": dict(group["slots"])},
        "items": [{"id": i, "question": d.get("question")} for i, d in zip(item_ids, group["diags"])],
        "slot_kinds": list(standards.SLOT_KINDS),
        "markers": list(standards.MARKERS),
        "operations": sorted(preload.operations),
        "blocks": sorted(CATALOGUE),
        "declared": evidence.get("declared") or {},
        "rules": [
            "the shape's stage is specify",
            "the shape names no replaces and no constraints",
            "the shape matches the signature: it requires and forbids only names, and counts only slot kinds, the "
            "signature agrees with",
            "the recipe's blocks come from blocks only",
            "never approve: no approved_by, approved_on or reference_sql_approved_* field anywhere",
            "one plan per item id, each a query-plan/v1 naming only ids from declared",
        ],
    }
    return (
        f"{IMPROVE_TASK} ({PROMPTS['template']}).\n"
        "Given the repeated misses below, which share one ledger signature, draft one question shape, its recipe and "
        "one worked example plan per item. Return JSON only: {\"kind\": \"template\"|\"note\"|\"request\", "
        "\"shape\": {\"id\", \"label\", \"slots\", \"requires\", \"forbids\", \"recipe\", \"stage\"}, "
        "\"recipe\": {\"id\", \"blocks\"}, \"plans\": {item id: query-plan/v1}, \"why\": str}.\n"
        f"{MISS_OPEN}\n{json.dumps(payload, sort_keys=True)}\n{MISS_CLOSE}"
    )


def validate_edit(edit: Any, evidence: dict[str, Any]) -> tuple[bool, str]:
    """Check a proposed edit against the allowlist, pack.lock, and the pack's certified terms."""
    if not isinstance(edit, dict):
        return False, "refused: edit must be a JSON object"
    section = edit.get("section")
    if section == "phrases":
        return False, "refused: a phrase edit needs its miss's ledger"
    if section != "terminology.aliases":
        return False, f"refused: section {section!r} is outside the allowlist (only 'terminology.aliases' is allowed)"
    if edit.get("certified") is not None:
        return False, "refused: edit may not set certified: true"
    if "column" in edit:
        col = edit.get("column")
        if col not in (evidence.get("columns") or {}):
            return False, f"refused: column {col!r} does not exist in pack.lock"
        return False, f"refused: column {col!r} is not an allowed field on terminology.aliases"
    extra = set(edit.keys()) - ALLOWED_EDIT_KEYS
    if extra:
        return False, f"refused: disallowed fields {', '.join(sorted(extra))} in terminology.aliases edit"
    phrase = edit.get("phrase")
    term = edit.get("term")
    if not isinstance(phrase, str) or not phrase.strip():
        return False, "refused: phrase must be a non-empty string"
    if not isinstance(term, str) or not term.strip():
        return False, "refused: term must be a non-empty string"
    phrase_norm = " ".join(phrase.strip().lower().split())
    if term not in (evidence.get("certified_terms") or {}):
        return False, f"refused: term {term!r} is not a certified term in the pack"
    if phrase_norm in (evidence.get("existing_aliases") or {}):
        return False, f"refused: phrase {phrase_norm!r} is already an alias in the pack"
    if phrase_norm in set(evidence.get("candidate_phrases") or []):
        return False, f"refused: phrase {phrase_norm!r} is already held in the steward queue or open drafts"
    matches = _matching_comments(phrase_norm, evidence)
    matched_terms = {m["term"] for m in matches}
    if matched_terms != {term}:
        return False, f"refused: phrase {phrase_norm!r} is not backed by a single matching comment for term {term!r}"
    return True, "ok"


def _norm(text: Any) -> str:
    """A phrase or question as two are compared: lower case, its runs of white space one space."""
    return " ".join(str(text or "").strip().lower().split())


def signature(diag: dict[str, Any]) -> Signature | None:
    """A learnable miss's ledger signature: the sorted operations and markers its ledger read, and the count of each
    slot kind above zero. None where the ledger is missing or malformed, holds an unknown piece, or counts a slot kind
    the platform does not declare: such a miss needs a phrase or an alias, not a template.

    The ledger is the diagnosis's {names, slots, unknown, unapplied} (#531 emits it); this is the one place that reads
    its names and slots."""
    ledger = diag.get("ledger")
    if not isinstance(ledger, dict) or ledger.get("unknown"):
        return None
    names, slots = ledger.get("names"), ledger.get("slots")
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names) or not isinstance(slots, dict):
        return None
    counts: list[tuple[str, int]] = []
    for kind, n in slots.items():
        if kind not in standards.SLOT_KINDS or not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return None
        if n > 0:
            counts.append((kind, n))
    return tuple(sorted(set(names))), tuple(sorted(counts))


def describe(sig: Signature) -> str:
    """A signature in words, for a note: its names, then each slot kind with its count."""
    names = ", ".join(sig[0]) or "no operation or marker"
    slots = ", ".join(f"{kind} x{n}" for kind, n in sig[1]) or "no slot"
    return f"[{names}; {slots}]"


def _learnable_template_miss(d: dict[str, Any]) -> bool:
    """Whether a diagnosis may supply a template's candidate test: a missing_template A/B miss, never held out or from
    the page, with a signature, its kind and its expected answer."""
    return (d.get("diagnosis") == "missing_template" and (d.get("source") or "ab") == "ab"
            and not setfiles.is_heldout(str(d.get("set") or "")) and signature(d) is not None
            and bool(d.get("kind")) and isinstance(d.get("expect"), dict) and bool(d.get("expect"))
            and bool(setfiles._phrasing(d.get("question") or "")))


def _grouped(diagnoses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every learnable missing_template miss grouped by signature, a repeated question kept once (the first, in
    diagnosis order), each group {signature, names, slots, diags, members}; members holds every miss considered."""
    by_sig: dict[Signature, dict[str, Any]] = {}
    for d in diagnoses:
        if not _learnable_template_miss(d):
            continue
        sig = signature(d)
        group = by_sig.setdefault(sig, {"signature": sig, "names": list(sig[0]), "slots": dict(sig[1]), "diags": [],
                                        "members": [], "_seen": set()})
        group["members"].append(d)
        asked = setfiles._phrasing(d.get("question") or "")
        if asked and asked not in group["_seen"]:
            group["_seen"].add(asked)
            group["diags"].append(d)
    groups = [{k: v for k, v in g.items() if k != "_seen"} for g in by_sig.values()]
    return sorted(groups, key=lambda g: (-len(g["diags"]), g["signature"]))


def _note(d: dict[str, Any], why: str, proposer: str, diagnosis: str | None = None) -> dict[str, Any]:
    return {"id": d.get("id"), "set": d.get("set"), "question": d.get("question"),
            "diagnosis": diagnosis or d.get("diagnosis"), "why": why, "proposer": proposer}


def template_groups(diagnoses: list[dict[str, Any]], need: int, proposer: str = "rules") -> tuple[list[dict], list[dict]]:
    """(groups, notes): the learnable missing_template misses grouped by ledger signature, the groups of at least need
    distinct questions eligible, largest first and then by signature; each smaller group becomes a note.

    need is the preload's min_also_asked_as + 1, so every candidate test has its minimum of alternate phrasings."""
    groups, notes = [], []
    for g in _grouped(diagnoses):
        if len(g["diags"]) >= need:
            groups.append(g)
            continue
        n = len(g["diags"])
        notes.append(_note(g["diags"][0], f"{n} learnable miss{'es' if n != 1 else ''} share the ledger signature "
                                          f"{describe(g['signature'])}; a template's candidate tests need at least "
                                          f"{need}", proposer, "missing_template"))
    return groups, notes


def holds_on(shape: dict[str, Any], names: set[str] | list[str], slots: dict[str, int]) -> bool:
    """Whether a shape matches a question whose ledger read names, with slots counted so: it requires nothing the
    question lacks, meets each of its one_of groups, forbids nothing the question holds, and every slot count it sets
    holds."""
    a = shape_condition(shape)
    held = set(names)
    if not a.plain <= held or any(not (g & held) for g in a.groups) or a.forbids & held:
        return False
    return all(lo <= slots.get(kind, 0) <= hi for kind, (lo, hi) in a.ranges.items())


def _unapproved(value: Any) -> Any:
    """value with every approval field removed, at any depth."""
    if isinstance(value, dict):
        return {k: _unapproved(v) for k, v in value.items() if k not in packtext.APPROVALS}
    if isinstance(value, list):
        return [_unapproved(v) for v in value]
    return deepcopy(value)


def _approves(value: Any) -> bool:
    """Whether value names an approval field anywhere."""
    if isinstance(value, dict):
        return any(k in packtext.APPROVALS or _approves(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_approves(v) for v in value)
    return False


def set_stem(shape_id: str) -> str:
    """The new set file's stem for a shape: shapes_<shape id, its hyphens underscores>_v1."""
    return "shapes_" + shape_id.replace("-", "_") + "_v1"


def set_document(shape_id: str, diags: list[dict[str, Any]], minimum: int = 3) -> tuple[str, dict, list[str]]:
    """(stem, document, item ids): the new set file of candidate tests for a shape.

    Item k is the group's k-th miss, id <shape>-NN, with its question, kind and expected answer (any approval field
    removed, so a drafted reference SQL stays a draft), every other question of the group as its alternate phrasings,
    and drafted_from naming the set and item it came from. Raises ValueError where the document does not read as a set
    or an item has fewer than minimum alternate phrasings."""
    stem = set_stem(shape_id)
    questions = [str(d.get("question") or "") for d in diags]
    items, ids = [], []
    for k, d in enumerate(diags, 1):
        item_id = f"{shape_id}-{k:02d}"
        own = setfiles._phrasing(questions[k - 1])
        others = [q for q in questions if setfiles._phrasing(q) != own]
        items.append({"id": item_id, "question": questions[k - 1], "kind": d.get("kind"),
                      "expect": _unapproved(d.get("expect") or {}), "also_asked_as": others,
                      "drafted_from": f"{d.get('set')}:{d.get('id')}"})
        ids.append(item_id)
    document = {"version": "v1", "items": items}
    read = setfiles.expand_suite_items("candidate", deepcopy(document))
    counts = setfiles.alternates(read)
    short = [i for i in ids if counts.get(i, 0) < minimum]
    if short:
        raise ValueError(f"refused: {', '.join(short)} would have fewer than {minimum} alternate phrasings")
    return stem, document, ids


def _spans(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """The ledger's unapplied spans, each a mapping; anything else is none."""
    spans = ledger.get("unapplied")
    return [x for x in spans if isinstance(x, dict)] if isinstance(spans, list) else []


def validate_phrase(edit: Any, evidence: dict[str, Any], diag: dict[str, Any], preload: standards.Preload) -> tuple[bool, str]:
    """Check a proposed phrase edit: {section: phrases, phrase, operation}, the phrase a span the miss's ledger left
    unread, the operation a platform operation, and the phrase held nowhere already. A filler phrase is refused."""
    if not isinstance(edit, dict):
        return False, "refused: edit must be a JSON object"
    if "filler" in edit:
        return False, "refused: filler phrases stay an author-pack edit"
    if edit.get("section") != "phrases":
        return False, OUTSIDE.format(section=edit.get("section"))
    extra = set(edit) - PHRASE_EDIT_KEYS
    if extra:
        return False, f"refused: disallowed fields {', '.join(sorted(extra))} in a phrases edit"
    phrase = _norm(edit.get("phrase")) if isinstance(edit.get("phrase"), str) else ""
    if not phrase:
        return False, "refused: phrase must be a non-empty string"
    ledger = diag.get("ledger") if isinstance(diag.get("ledger"), dict) else {}
    unread = {_norm(s.get("text")) for s in _spans(ledger)}
    if phrase not in unread:
        return False, f"refused: phrase {phrase!r} is not a word the question's ledger left unread"
    operation = edit.get("operation")
    if operation not in preload.operations:
        return False, f"refused: operation {operation!r} is not a platform operation"
    if phrase in preload.phrases:
        return False, f"refused: phrase {phrase!r} is a platform phrase already"
    if phrase in (evidence.get("existing_aliases") or {}):
        return False, f"refused: phrase {phrase!r} is already an alias in the pack"
    if phrase in set(evidence.get("candidate_phrases") or []) or phrase in set(evidence.get("pack_phrases") or []):
        return False, f"refused: phrase {phrase!r} is already held in the pack, the steward queue or open drafts"
    return True, "ok"


def validate_template(reply: Any, group: dict[str, Any], item_ids: list[str], preload: standards.Preload) -> tuple[bool, str]:
    """Check a model's template draft for a signature group: a shape at specify that would match every miss of the
    group, with no replaces and no position constraints; its recipe from the block catalogue; a drafted worked example
    plan for every candidate test; and no approval anywhere. What a recipe or a plan means is the compile check's."""
    if not isinstance(reply, dict):
        return False, "refused: a template reply is a JSON object"
    extra = set(reply) - TEMPLATE_REPLY_KEYS
    if extra:
        return False, f"refused: disallowed fields {', '.join(sorted(extra))} in a template reply"
    if _approves(reply):
        return False, "refused: a proposal never approves"
    shape, recipe, plans = reply.get("shape"), reply.get("recipe"), reply.get("plans")
    if not isinstance(shape, dict):
        return False, "refused: a template names its shape, an object"
    if "replaces" in shape:
        return False, "refused: a proposed shape does not replace a coded builder"
    if "constraints" in shape:
        return False, "refused: a signature holds no positions, so a proposed shape declares no position constraints"
    extra = set(shape) - SHAPE_KEYS
    if extra:
        return False, f"refused: disallowed fields {', '.join(sorted(extra))} in a proposed shape"
    sid = shape.get("id")
    if not isinstance(sid, str) or not standards.SLUG.fullmatch(sid):
        return False, f"refused: shape id {sid!r} is a slug"
    if sid in preload.shapes:
        return False, f"refused: shape id {sid!r} is a platform shape's"
    if shape.get("stage") != "specify":
        return False, "refused: a proposed shape starts at specify"
    slots = shape.get("slots") or []
    kinds = [s.get("kind") for s in slots if isinstance(s, dict)] if isinstance(slots, list) else None
    if kinds is None or len(kinds) != len(slots) or not all(isinstance(k, str) for k in kinds) or len(set(kinds)) != len(kinds):
        return False, "refused: a proposed shape declares each slot kind once"
    if any(k not in standards.SLOT_KINDS for k in kinds):
        return False, f"refused: a slot kind is one of {', '.join(standards.SLOT_KINDS)}"
    if not isinstance(recipe, dict) or set(recipe) != {"id", "blocks"}:
        return False, "refused: a template's recipe is {id, blocks}"
    if not isinstance(recipe.get("id"), str) or recipe.get("id") != shape.get("recipe"):
        return False, "refused: the shape names its own recipe"
    if recipe.get("id") in preload.recipes:
        return False, f"refused: recipe id {recipe.get('id')!r} is a platform recipe's"
    blocks = recipe.get("blocks")
    if not isinstance(blocks, list) or not blocks or not all(isinstance(b, dict) and isinstance(b.get("block"), str) and b.get("block") in CATALOGUE
                                                                for b in blocks):
        return False, "refused: every recipe block is one of the platform's block catalogue"
    if not holds_on(shape, group["names"], group["slots"]):
        return False, "refused: the shape would not match the misses it is proposed for"
    if not isinstance(plans, dict) or not all(isinstance(plans.get(i), dict) for i in item_ids):
        return False, "refused: every candidate test needs a drafted worked example"
    return True, "ok"


def change_note(edits: list[dict[str, Any]], proposer: str, ab_summary: str) -> str:
    """The release note's line for a kept proposal: its aliases, then its phrases, then its template, joined by '; '.
    With aliases alone it reads as it always has."""
    aliases = [e for e in edits if _kind(e) == "alias"]
    phrases = [e for e in edits if _kind(e) == "phrase"]
    shapes = [e for e in edits if _kind(e) == "shape"]
    parts = []
    if aliases or not edits:
        parts.append("alias " + ", ".join(repr(e["phrase"]) + " -> " + e["term"] for e in aliases))
    if phrases:
        parts.append("phrase " + ", ".join(f"{e['phrase']!r} marks {e['operation']}" for e in phrases))
    for e in shapes:
        parts.append(f"template {e['shape']['id']} at specify with {len(e['set']['items'])} candidate tests")
    return f"Add {'; '.join(parts)} (by {proposer}; A/B: {ab_summary})"


def _kind(ed: dict[str, Any]) -> str:
    """An edit's kind by its section: phrase, shape, or (any other, as before) alias."""
    section = ed.get("section")
    if section == "phrases":
        return "phrase"
    if section == "shapes":
        return "shape"
    return "alias"


def _unit_of(diags: list[dict[str, Any]], evidence: dict[str, Any]) -> str | None:
    """The unit a template's review history is kept under: the most common unit its misses name (ties by name), else
    the client's single unit, else None."""
    named = Counter(str(d["unit"]) for d in diags if d.get("unit"))
    if named:
        return sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    units = {c.get("unit") for c in (evidence.get("columns") or {}).values() if isinstance(c, dict) and c.get("unit")}
    return next(iter(units)) if len(units) == 1 else None


def _template(group: dict[str, Any], evidence: dict[str, Any], llm: LLM, proposer: str,
              preload: standards.Preload) -> tuple[dict | None, list[dict], list[dict]]:
    """(edit, notes, refused) for one eligible signature group: the model drafts the shape, recipe and worked examples,
    and code builds and checks everything around the draft."""
    diags = group["diags"]
    first = diags[0]
    unit = _unit_of(diags, evidence)
    if unit is None:
        reason = "refused: the template names no unit for its review history"
        return None, [_note(first, reason, proposer)], [_refusal(first, proposer, None, reason)]
    provisional = [f"item-{k:02d}" for k in range(1, len(diags) + 1)]
    try:
        reply = llm.json(build_template_prompt(group, provisional, evidence, preload))
    except (NotJSON, Refused, ExternalCallError) as exc:
        return None, [], [_refusal(first, proposer, None, f"refused: {exc}")]
    why = str(reply.get("why") or "") if isinstance(reply, dict) else ""
    if not isinstance(reply, dict) or reply.get("kind") != "template":
        return None, [_note(first, why or "the model drafted no template", proposer)], []
    shape = reply.get("shape") if isinstance(reply.get("shape"), dict) else {}
    sid = shape.get("id")
    if not isinstance(sid, str) or not standards.SLUG.fullmatch(sid):
        reason = f"refused: shape id {sid!r} is a slug"
        return None, [_note(first, reason, proposer)], [_refusal(first, proposer, reply, reason)]
    try:
        stem, document, item_ids = set_document(sid, diags, preload.min_also_asked_as)
    except ValueError as exc:
        return None, [_note(first, str(exc), proposer)], [_refusal(first, proposer, reply, str(exc))]
    plans = reply.get("plans") if isinstance(reply.get("plans"), dict) else {}
    reply = {**reply, "plans": {i: plans.get(p, plans.get(i)) for p, i in zip(provisional, item_ids)}}
    ok, reason = validate_template(reply, group, item_ids, preload)
    if not ok:
        return None, [_note(first, reason, proposer)], [_refusal(first, proposer, reply, reason)]
    name = stem[: -len("_v1")]
    n = len(item_ids)
    return {
        "id": f"shape:{sid}",
        "kind": "shape",
        "unit": unit,
        "section": "shapes",
        "title": f"Template {shape.get('label') or sid} (stage specify) with {n} candidate tests",
        "signature": {"names": list(group["names"]), "slots": dict(group["slots"])},
        "shape": deepcopy(shape),
        "recipe": deepcopy(reply["recipe"]),
        "shape_test": {"shape": sid, "tests": {"set": name, "items": list(item_ids)}},
        "examples": [{"id": f"{sid}-example-{k:02d}", "shape": sid, "question": str(d.get("question") or ""),
                      "plan": deepcopy(reply["plans"][i])} for k, (i, d) in enumerate(zip(item_ids, diags), 1)],
        "set": {"stem": stem, "name": name, "version": "v1", "items": list(item_ids), "document": document},
        "why": why,
        "proposer": proposer,
        "items": [d.get("id") for d in diags],
        "questions": [d.get("question") for d in diags],
        "also_answers": [],
        "state": "drafted",
        "accepted_as": None,
        "accepted_on": None,
        "certified_as": None,
        "certified_on": None,
    }, [], []


def _ident(d: dict[str, Any]) -> tuple[str, str]:
    """A diagnosis's set and id, as text, whatever they hold."""
    return str(d.get("set")), str(d.get("id"))


def _refusal(d: dict[str, Any], proposer: str, edit: Any, reason: str) -> dict[str, Any]:
    return {"id": d.get("id"), "question": d.get("question"), "proposer": proposer, "edit": edit, "reason": reason}


def _templates(ab_diagnoses: list[dict[str, Any]], evidence: dict[str, Any], llm: LLM | None, proposer: str,
               preload: standards.Preload) -> tuple[set, list[dict], list[dict], list[dict]]:
    """(considered, edits, notes, refused) for the template branch: the misses it took out of the per-miss loop, at
    most one template edit, and its notes and refusals."""
    considered = {_ident(d) for g in _grouped(ab_diagnoses) for d in g["members"]}
    need = preload.min_also_asked_as + 1
    groups, notes = template_groups(ab_diagnoses, need, proposer)
    edits: list[dict] = []
    refused: list[dict] = []
    for k, group in enumerate(groups):
        first, n = group["diags"][0], len(group["diags"])
        if k > 0:
            notes.append(_note(first, f"{n} misses share the ledger signature {describe(group['signature'])}; held for a "
                                      f"later run: {ONE_TEMPLATE}", proposer))
        elif llm is None:
            notes.append(_note(first, f"{n} misses share the ledger signature {describe(group['signature'])}; a template "
                                      "proposal needs a model to draft its shape, recipe and worked examples (or author "
                                      "it with author-pack)", proposer))
        else:
            edit, more_notes, more_refused = _template(group, evidence, llm, proposer, preload)
            edits += [edit] if edit else []
            notes += more_notes
            refused += more_refused
    return considered, edits, notes, refused


def propose(
    ab_diagnoses: list[dict[str, Any]],
    page_diagnoses: list[dict[str, Any]],
    evidence: dict[str, Any],
    llm: LLM | None = None,
    *,
    templates: bool = False,
    preload: standards.Preload | None = None,
) -> dict[str, Any]:
    """Produce allowlisted edits, steward notes, platform requests, and refused edits.

    Follows MODEL_PROVIDER: when llm is None, rules propose ('rules'); with the mock ('mock') or a model ('model'),
    the model is asked about every miss with prompt improve-propose-v1 (improve-phrase-v1 for a missing_phrase miss),
    and code checks every returned edit. A page question with no term is attached to an edit whose proposed alias would
    also answer it, or else noted for a steward, never made a proposal of its own. Held-out misses are dropped first.

    With templates, learnable missing_template misses are grouped by ledger signature instead, and the largest group
    large enough becomes at most one template proposal, drafted by the model with improve-template-v1; without a model
    it becomes a note. The improvement flow does not pass templates until the new set file is written with the
    candidate (#533).
    """

    if llm is None:
        proposer = "rules"
    elif llm.provider == "mock":
        proposer = "mock"
    else:
        proposer = "model"

    ab_diagnoses = [d for d in ab_diagnoses if not setfiles.is_heldout(str(d.get("set") or ""))]
    edits_by_key: dict[tuple, dict[str, Any]] = {}
    notes: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    template_edits: list[dict[str, Any]] = []
    loaded: list[standards.Preload] = [preload] if preload is not None else []

    def platform() -> standards.Preload:
        if not loaded:
            loaded.append(standards.load())
        return loaded[0]

    considered: set = set()
    if templates:
        considered, template_edits, t_notes, t_refused = _templates(ab_diagnoses, evidence, llm, proposer, platform())
        notes += t_notes
        refused += t_refused

    for diag in ab_diagnoses:
        if _ident(diag) in considered:
            continue
        cand_edit = diag.get("edit")
        why = diag.get("why") or ""
        outcome = diag.get("outcome") or "request"

        if llm is not None:
            if diag.get("diagnosis") == "missing_phrase":
                prompt = build_phrase_prompt(diag, platform())
            else:
                prompt = build_prompt(diag, evidence)
            try:
                reply = llm.json(prompt)
            except (NotJSON, Refused, ExternalCallError) as exc:
                refused.append(_refusal(diag, proposer, None, f"refused: {exc}"))
                continue
            cand_edit = reply.get("edit")
            why = str(reply.get("why") or why)
            outcome = str(reply.get("kind") or outcome)

        if cand_edit is not None:
            section = cand_edit.get("section") if isinstance(cand_edit, dict) else None
            if section == "phrases":
                ok, reason = validate_phrase(cand_edit, evidence, diag, platform())
            elif section == "terminology.aliases" or not isinstance(cand_edit, dict):
                ok, reason = validate_edit(cand_edit, evidence)
            else:
                ok, reason = False, OUTSIDE.format(section=section)
            if not ok:
                refused.append(_refusal(diag, proposer, cand_edit, reason))
                notes.append(_note(diag, reason, proposer))
                continue
            if section == "phrases" and _unit_of([diag], evidence) is None:
                reason = "refused: the phrase names no unit for its review history"
                refused.append(_refusal(diag, proposer, cand_edit, reason))
                notes.append(_note(diag, reason, proposer))
                continue
            if section == "phrases":
                _add_phrase(edits_by_key, cand_edit, diag, why, proposer, evidence)
                continue
            phrase_norm = " ".join(cand_edit["phrase"].strip().lower().split())
            term_id = cand_edit["term"]
            term_name = ((evidence.get("certified_terms") or {}).get(term_id) or {}).get("name") or term_id
            matches = diag.get("matching_comments") or _matching_comments(phrase_norm, evidence)
            ev_col = matches[0]["column"] if matches else term_id
            key = (phrase_norm, term_id)
            if key not in edits_by_key:
                edits_by_key[key] = {
                    "id": f"alias:{phrase_norm}",
                    "kind": "alias",
                    "unit": (evidence.get("columns", {}).get(ev_col) or {}).get("unit") or ev_col.split(".")[0],
                    "section": "terminology.aliases",
                    "phrase": phrase_norm,
                    "term": term_id,
                    "term_name": term_name,
                    "evidence_column": ev_col,
                    "evidence": f"the comment on {ev_col}",
                    "why": why,
                    "proposer": proposer,
                    "items": [diag.get("id")],
                    "questions": [diag.get("question")],
                    "also_answers": [],
                    "state": "drafted",
                    "accepted_as": None,
                    "accepted_on": None,
                    "certified_as": None,
                    "certified_on": None,
                }
            else:
                if diag.get("id") not in edits_by_key[key]["items"]:
                    edits_by_key[key]["items"].append(diag.get("id"))
                if diag.get("question") not in edits_by_key[key]["questions"]:
                    edits_by_key[key]["questions"].append(diag.get("question"))
        elif outcome == "note":
            notes.append(_note(diag, why, proposer))
        elif outcome == "request":
            requests.append(_note(diag, why, proposer))

    _drop_ambiguous_phrases(edits_by_key, notes, refused, proposer)

    for pdiag in page_diagnoses:
        p_edit = pdiag.get("edit")
        if isinstance(p_edit, dict) and isinstance(p_edit.get("phrase"), str) and isinstance(p_edit.get("term"), str):
            key = (" ".join(p_edit["phrase"].strip().lower().split()), p_edit["term"])
            if key in edits_by_key:
                q = pdiag.get("question")
                count = int(pdiag.get("count") or 1)
                if q and q not in edits_by_key[key]["also_answers"]:
                    edits_by_key[key]["also_answers"].append(q)
                times = "once" if count == 1 else f"{count} times"
                if "asked " not in edits_by_key[key]["evidence"]:
                    edits_by_key[key]["evidence"] += f"; asked {times} on the page"
                continue
        if pdiag.get("outcome") in ("note", "proposal"):
            notes.append({
                "id": None,
                "set": "page",
                "question": pdiag.get("question"),
                "diagnosis": pdiag.get("diagnosis"),
                "why": pdiag.get("why"),
                "proposer": proposer,
            })

    return {
        "proposer": proposer,
        "edits": list(edits_by_key.values()) + template_edits,
        "notes": notes,
        "requests": requests,
        "refused": refused,
    }


def _add_phrase(edits_by_key: dict[tuple, dict[str, Any]], edit: dict[str, Any], diag: dict[str, Any], why: str,
                proposer: str, evidence: dict[str, Any]) -> None:
    """Add a checked phrase edit, merged with the same phrase and operation from another miss."""
    phrase, operation = _norm(edit["phrase"]), edit["operation"]
    key = (PHRASE_KEY, phrase, operation)
    held = edits_by_key.get(key)
    if held is None:
        unit = _unit_of([diag], evidence)
        edits_by_key[key] = held = {
            "id": f"phrase:{phrase}", "kind": "phrase", "unit": unit, "section": "phrases", "phrase": phrase,
            "operation": operation, "title": f"Phrase '{phrase}' marks {operation}", "evidence": "", "why": why,
            "proposer": proposer, "items": [], "questions": [], "also_answers": [], "state": "drafted",
            "accepted_as": None, "accepted_on": None, "certified_as": None, "certified_on": None,
        }
    if diag.get("id") not in held["items"]:
        held["items"].append(diag.get("id"))
    if diag.get("question") not in held["questions"]:
        held["questions"].append(diag.get("question"))
    n = len(held["questions"])
    held["evidence"] = f"unread in {n} question{'s' if n != 1 else ''}"


def _drop_ambiguous_phrases(edits_by_key: dict[tuple, dict[str, Any]], notes: list[dict], refused: list[dict],
                            proposer: str) -> None:
    """Refuse every phrase edit whose phrase another edit proposes for a different operation: one phrase marks one
    operation, and which is meant is a person's call."""
    ops: dict[str, set[str]] = {}
    for key in edits_by_key:
        if key[0] is PHRASE_KEY:
            ops.setdefault(key[1], set()).add(key[2])
    for phrase, held in sorted(ops.items()):
        if len(held) < 2:
            continue
        reason = f"refused: phrase {phrase!r} is ambiguous, proposed for {', '.join(sorted(held))}"
        for op in sorted(held):
            ed = edits_by_key.pop((PHRASE_KEY, phrase, op))
            for item, question in zip(ed["items"], ed["questions"]):
                refused.append({"id": item, "question": question, "proposer": proposer,
                                "edit": {"section": "phrases", "phrase": phrase, "operation": op}, "reason": reason})
        notes.append({"id": ed["items"][0], "set": None, "question": ed["questions"][0], "diagnosis": "missing_phrase",
                      "why": reason, "proposer": proposer})


def _flow_scalar(val: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./-]+(?: [A-Za-z0-9_./-]+)*", val) and yaml.safe_load(val) == val:
        return val
    return json.dumps(val)


def _apply_aliases(text: str, next_version: str, edits: list[dict[str, Any]]) -> str:
    """Surgically update manifest.version and append terminology.aliases entries in pack.yaml text without stripping comments."""
    parsed = yaml.safe_load(text) or {}
    existing_aliases = list(((parsed.get("terminology") or {}).get("aliases")) or [])
    new_entries: list[dict[str, str]] = []
    for ed in edits:
        entry = {"phrase": str(ed["phrase"]), "term": str(ed["term"])}
        if entry not in existing_aliases and entry not in new_entries:
            new_entries.append(entry)

    updated = re.sub(
        r"(^manifest:\s*\n(?:[ \t]+[^\n]*\n|[ \t]*#[^\n]*\n)*?[ \t]+version:[ \t]*)\S+",
        rf"\g<1>{next_version}",
        text,
        count=1,
        flags=re.M,
    )
    if not new_entries:
        return updated

    lines = updated.splitlines(keepends=True)
    in_terminology = False
    aliases_idx: int | None = None
    empty_flow_aliases = False
    for idx, ln in enumerate(lines):
        if re.match(r"^terminology:\s*(?:#.*)?$", ln):
            in_terminology = True
            continue
        if in_terminology:
            if re.match(r"^[A-Za-z0-9_-]+:\s*", ln):
                break
            if re.match(r"^  aliases:\s*\[\s*\]\s*(?:#.*)?$", ln):
                aliases_idx = idx
                empty_flow_aliases = True
                break
            if re.match(r"^  aliases:\s*(?:#.*)?$", ln):
                aliases_idx = idx
                break

    if aliases_idx is None:
        parsed.setdefault("manifest", {})["version"] = next_version
        aliases = parsed.setdefault("terminology", {}).setdefault("aliases", [])
        for entry in new_entries:
            if entry not in aliases:
                aliases.append(entry)
        return yaml.safe_dump(parsed, **emit.YAML)

    if empty_flow_aliases:
        added = ["  aliases:\n"] + [
            f"    - {{phrase: {_flow_scalar(e['phrase'])}, term: {_flow_scalar(e['term'])}}}\n"
            for e in new_entries
        ]
        lines[aliases_idx : aliases_idx + 1] = added
        return "".join(lines)

    last_item_idx = aliases_idx
    item_indent = "    "
    use_block = False
    for idx in range(aliases_idx + 1, len(lines)):
        ln = lines[idx]
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^  [A-Za-z0-9_-]+:\s*", ln) or re.match(r"^[A-Za-z0-9_-]+:\s*", ln):
            break
        m_item = re.match(r"^([ \t]*)-[ \t]+(.*)$", ln)
        if m_item:
            item_indent = m_item.group(1)
            use_block = not m_item.group(2).lstrip().startswith("{")
        last_item_idx = idx

    if use_block:
        added_lines = [
            f"{item_indent}- phrase: {_flow_scalar(e['phrase'])}\n{item_indent}  term: {_flow_scalar(e['term'])}\n"
            for e in new_entries
        ]
    else:
        added_lines = [
            f"{item_indent}- {{phrase: {_flow_scalar(e['phrase'])}, term: {_flow_scalar(e['term'])}}}\n"
            for e in new_entries
        ]

    if not lines[last_item_idx].endswith("\n"):
        lines[last_item_idx] += "\n"
    lines[last_item_idx + 1 : last_item_idx + 1] = added_lines
    return "".join(lines)


def apply_edits_to_pack_text(text: str, next_version: str, edits: list[dict[str, Any]]) -> str:
    """Surgically update manifest.version and add each edit to pack.yaml text without stripping comments: aliases as
    before, then phrases, then a template bundle's shape, recipe, shape test, worked examples, input stem and test set
    entry, each appended by packtext (which raises packtext.Unplaceable where the text cannot take one)."""
    out = _apply_aliases(text, next_version, [e for e in edits if _kind(e) == "alias"])
    phrases: list[dict[str, str]] = []
    for e in edits:
        entry = {"phrase": str(e["phrase"]), "operation": str(e["operation"])} if _kind(e) == "phrase" else None
        if entry and entry not in phrases:
            phrases.append(entry)
    if phrases:
        out = packtext.append_entries(out, "phrases", phrases)
    for e in edits:
        if _kind(e) != "shape":
            continue
        out = packtext.append_entries(out, "shapes", [e["shape"]])
        out = packtext.append_entries(out, "recipes", [e["recipe"]])
        out = packtext.append_entries(out, "shape_tests", [e["shape_test"]])
        out = packtext.append_entries(out, "examples", list(e["examples"]))
        out = packtext.add_input_set(out, e["set"]["stem"])
        out = packtext.append_test_set(out, {"name": e["set"]["name"], "version": "v1", "items": list(e["set"]["items"])})
    return out


def write_candidate_pack(
    clients_dir: Path,
    client: str,
    dest_clients_dir: Path,
    edits: list[dict[str, Any]],
    meta_model_path: Path | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Write a candidate copy of <clients_dir>/<client> to <dest_clients_dir>/<client> with edits applied and minor version bumped.

    Checks the candidate with kge/compiler/checks.py: any edit that causes a compiler finding is dropped and named.
    Returns (next_version, kept_edits, compiler_refused_edits).
    """
    src_folder = Path(clients_dir) / client
    dst_folder = Path(dest_clients_dir) / client
    if dst_folder.exists():
        shutil.rmtree(dst_folder)
    dst_folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src_folder,
        dst_folder,
        ignore=shutil.ignore_patterns("proposals", "versions"),
    )

    pack_path = dst_folder / "pack.yaml"
    orig_text = pack_path.read_text()
    raw = yaml.safe_load(orig_text)
    base_version = str((raw.get("manifest") or {}).get("version") or "1.0.0")
    next_version = bump_minor(base_version)

    meta_path = meta_model_path or (ROOT / "meta-model.yaml")
    meta = packs.meta_model(meta_path)

    kept_edits: list[dict[str, Any]] = []
    compiler_refused: list[dict[str, Any]] = []
    before = yaml.safe_load(orig_text) or {}

    for ed in edits:
        reason = _refusal_reason(ed, kept_edits, orig_text, before, next_version, pack_path, dest_clients_dir, client, meta)
        if reason:
            compiler_refused.append({"edit": _summary(ed), "proposer": ed.get("proposer", "rules"), "reason": reason})
        else:
            kept_edits.append(ed)

    pack_path.write_text(apply_edits_to_pack_text(orig_text, next_version, kept_edits))
    return next_version, kept_edits, compiler_refused


def _refusal_reason(ed: dict[str, Any], kept: list[dict[str, Any]], orig_text: str, before: dict, next_version: str,
                    pack_path: Path, dest_clients_dir: Path, client: str, meta: Any) -> str | None:
    """Why ed cannot join the kept edits, or None: a second template, a pack text that cannot take it, a candidate that
    leaves the bound (packtext.bounded), or a compiler finding on the candidate written with it."""
    if _kind(ed) == "shape" and any(_kind(k) == "shape" for k in kept):
        return ONE_TEMPLATE
    try:
        trial = apply_edits_to_pack_text(orig_text, next_version, [*kept, ed])
    except packtext.Unplaceable as exc:
        return str(exc)
    violations = packtext.bounded(before, yaml.safe_load(trial) or {}, next_version)
    if violations:
        return "; ".join(violations)
    pack_path.write_text(trial)
    p = packs.read(dest_clients_dir, client, lock=True)
    found = checks.check(p, meta, COMPILER_VERSION, emit.FILLS)
    return "; ".join(str(f) for f in found) if found else None


def _summary(ed: dict[str, Any]) -> dict[str, Any]:
    """A refused edit as its refusal record names it."""
    if _kind(ed) == "phrase":
        return {"section": "phrases", "phrase": ed.get("phrase"), "operation": ed.get("operation")}
    if _kind(ed) == "shape":
        return {"section": "shapes", "shape": (ed.get("shape") or {}).get("id"), "set": (ed.get("set") or {}).get("stem")}
    return {"section": ed["section"], "phrase": ed["phrase"], "term": ed["term"]}


def candidate_set(kept_edits: list[dict[str, Any]]) -> tuple[str, dict] | None:
    """The kept template's new set file, (stem, document), for the candidate's set root (#533 writes it); None without
    one."""
    for ed in kept_edits:
        if _kind(ed) == "shape":
            return ed["set"]["stem"], deepcopy(ed["set"]["document"])
    return None


def set_text(document: dict) -> str:
    """A set document as its file's text."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
