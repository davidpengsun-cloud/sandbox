"""Propose step, allowlist check, and candidate pack writer for the improvement job.

Allowlist:
- Only `terminology.aliases` add-only `{phrase, term}` for an existing certified term
- And a minor bump of `manifest.version` (e.g. 1.2.0 -> 1.3.0)
Nothing else may be modified.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from kge.pipeline.settings import ROOT
from kge.compiler import checks, emit, pack as packs
from kge.compiler.compile import VERSION as COMPILER_VERSION
from kge.improve.rules import _matching_comments
from kge.adapters.model import ExternalCallError
from kge.adapters.model import LLM, NotJSON, Refused
from kge.adapters.model import IMPROVE_TASK, MISS_CLOSE, MISS_OPEN

PROMPTS = {"propose": "improve-propose-v1"}
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ALLOWED_EDIT_KEYS = {"section", "phrase", "term"}


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


def validate_edit(edit: Any, evidence: dict[str, Any]) -> tuple[bool, str]:
    """Check a proposed edit against the allowlist, pack.lock, and the pack's certified terms."""
    if not isinstance(edit, dict):
        return False, "refused: edit must be a JSON object"
    section = edit.get("section")
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


def propose(
    ab_diagnoses: list[dict[str, Any]],
    page_diagnoses: list[dict[str, Any]],
    evidence: dict[str, Any],
    llm: LLM | None = None,
) -> dict[str, Any]:
    """Produce allowlisted edits, steward notes, platform requests, and refused edits.

    Follows MODEL_PROVIDER: when llm is None, rules propose ('rules'); with the mock ('mock') or a model ('model'),
    the model is asked about every miss with prompt improve-propose-v1, and code checks every returned edit. A page
    question with no term is attached to an edit whose proposed alias would also answer it, or else noted for a steward,
    never made a proposal of its own.

    """

    if llm is None:
        proposer = "rules"
    elif llm.provider == "mock":
        proposer = "mock"
    else:
        proposer = "model"

    edits_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    notes: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    for diag in ab_diagnoses:
        cand_edit = diag.get("edit")
        why = diag.get("why") or ""
        outcome = diag.get("outcome") or "request"

        if llm is not None:
            prompt = build_prompt(diag, evidence)
            try:
                reply = llm.json(prompt)
            except (NotJSON, Refused, ExternalCallError) as exc:
                refused.append({
                    "id": diag.get("id"),
                    "question": diag.get("question"),
                    "proposer": proposer,
                    "edit": None,
                    "reason": f"refused: {exc}",
                })
                continue
            cand_edit = reply.get("edit")
            why = str(reply.get("why") or why)
            outcome = str(reply.get("kind") or outcome)

        if cand_edit is not None:
            ok, reason = validate_edit(cand_edit, evidence)
            if not ok:
                refused.append({
                    "id": diag.get("id"),
                    "question": diag.get("question"),
                    "proposer": proposer,
                    "edit": cand_edit,
                    "reason": reason,
                })
                notes.append({
                    "id": diag.get("id"),
                    "set": diag.get("set"),
                    "question": diag.get("question"),
                    "diagnosis": diag.get("diagnosis"),
                    "why": reason,
                    "proposer": proposer,
                })
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
                    "accepted_by": None,
                    "accepted_on": None,
                    "certified_by": None,
                    "certified_on": None,
                }
            else:
                if diag.get("id") not in edits_by_key[key]["items"]:
                    edits_by_key[key]["items"].append(diag.get("id"))
                if diag.get("question") not in edits_by_key[key]["questions"]:
                    edits_by_key[key]["questions"].append(diag.get("question"))
        elif outcome == "note":
            notes.append({
                "id": diag.get("id"),
                "set": diag.get("set"),
                "question": diag.get("question"),
                "diagnosis": diag.get("diagnosis"),
                "why": why,
                "proposer": proposer,
            })
        elif outcome == "request":
            requests.append({
                "id": diag.get("id"),
                "set": diag.get("set"),
                "question": diag.get("question"),
                "diagnosis": diag.get("diagnosis"),
                "why": why,
                "proposer": proposer,
            })

    for pdiag in page_diagnoses:
        p_edit = pdiag.get("edit")
        if p_edit:
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
        "edits": list(edits_by_key.values()),
        "notes": notes,
        "requests": requests,
        "refused": refused,
    }


def _flow_scalar(val: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./-]+(?: [A-Za-z0-9_./-]+)*", val) and yaml.safe_load(val) == val:
        return val
    return json.dumps(val)


def apply_edits_to_pack_text(text: str, next_version: str, edits: list[dict[str, Any]]) -> str:
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


def write_candidate_pack(
    clients_dir: Path,
    client: str,
    dest_clients_dir: Path,
    edits: list[dict[str, Any]],
    identities: Path | None = None,
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

    for ed in edits:
        trial_edits = [*kept_edits, ed]
        pack_path.write_text(apply_edits_to_pack_text(orig_text, next_version, trial_edits))
        p = packs.read(dest_clients_dir, client, lock=True, identities=identities)
        found = checks.check(p, meta, COMPILER_VERSION, emit.FILLS)
        if found:
            compiler_refused.append({
                "edit": {"section": ed["section"], "phrase": ed["phrase"], "term": ed["term"]},
                "proposer": ed.get("proposer", "rules"),
                "reason": "; ".join(str(f) for f in found),
            })
            pack_path.write_text(apply_edits_to_pack_text(orig_text, next_version, kept_edits))
        else:
            kept_edits.append(ed)

    pack_path.write_text(apply_edits_to_pack_text(orig_text, next_version, kept_edits))
    return next_version, kept_edits, compiler_refused
