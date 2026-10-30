"""Build, test, keep, and judge steps for the improvement job.

Runs kge/compiler/compile.py, kge/graph/load.py, kge/pipeline/run.py, kge/graph/release.py --mark-loaded, and kge/eval/margin.py as subprocesses,
as entrypoint.sh does, and never touches the served release (current) or the store's pack.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from kge.adapters import sync_step
from kge.pipeline.settings import ROOT
from kge.pipeline.trace import read_jsonl
from kge.eval import sets as setfiles
from kge.graph.release import GATE, LOADED, held, marked, store_of, write_marker
from kge.improve import CannotImprove
from kge.improve.edits import write_candidate_pack
from kge.pipeline.profile import Profile
from kge import packstore

DEMO = ROOT


RULES_ONLY = {"MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules"}
TIER_MODELS = tuple(f"MODEL_{tier.upper()}_{part}" for tier in ("high", "medium", "low") for part in ("PROVIDER", "NAME"))


def _sub_env(prof: Profile, force_rules: bool = False) -> dict[str, str]:
    """The environment of every step that builds and checks a candidate: the profile's, with the adapters' candidate
    settings (candidate_settings), so its graph and chunks go where nothing serves and the improvement job may write,
    never to the tables a release serves. force_rules makes the step an intentional rules run: no model for any agent
    (MODEL_PROVIDER none, no tier's provider or model, no mock) and the rule tagger named outright (TAGGER_PROVIDER=rules),
    so the cloud path's refusal of TAGGER_PROVIDER=model without a model never fires in a run that asked for rules. The
    embedder stays the configured one: the candidate's chunks are embedded and searched with it, as the served graph's
    are, so its margin measures the pack's edit, not a change of embedder."""
    from kge.adapters import candidate_settings
    env = {**os.environ, **candidate_settings()}
    if prof.registry:
        env["PROFILES"] = str(prof.registry)
    env["PROFILE"] = prof.id
    if force_rules:
        env.update(RULES_ONLY)
        for name in (*TIER_MODELS, "MOCK_MODE"):
            env.pop(name, None)
    return env


def _run_cmd(args: list[str], prof: Profile, force_rules: bool = False, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, "-m", *args],
        cwd=str(DEMO),
        env=_sub_env(prof, force_rules=force_rules),
        capture_output=True,
        text=True,
    )
    if proc.returncode not in ok_codes:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise CannotImprove(f"command {' '.join(args)} failed (exit {proc.returncode}): {detail}")
    return proc


def proposal_dir(prof: Profile, proposal_id: str) -> Path:
    return prof.clients / prof.client / "proposals" / proposal_id


def write_proposal_record(prof: Profile, proposal_id: str, record: dict[str, Any]) -> Path:
    """Write proposal.yaml both to local clients/<client>/proposals/<id>/ and through packstore."""
    pdir = proposal_dir(prof, proposal_id)
    pdir.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_dump(record, sort_keys=False, allow_unicode=True).encode()
    path = pdir / "proposal.yaml"
    path.write_bytes(data)
    store = packstore.for_profile(prof)
    store.write(f"proposals/{proposal_id}/proposal.yaml", data)
    return path


def sync_proposal_pack_to_store(prof: Profile, proposal_id: str) -> None:
    """Ensure the candidate pack files under proposals/<id>/<client>/ are persisted in packstore."""
    store = packstore.for_profile(prof)
    pdir = proposal_dir(prof, proposal_id)
    pack_root = pdir / prof.client
    if not pack_root.is_dir():
        return
    for file_path in sorted(pack_root.rglob("*")):
        if file_path.is_file():
            rel = file_path.relative_to(pdir).as_posix()
            store.write(f"proposals/{proposal_id}/{rel}", file_path.read_bytes())


def build_candidate(
    prof: Profile,
    proposal_id: str,
    edits: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Write proposals/<id>/<client>/pack.yaml (+ lock/units), compile it, and load its graph beside current. Its
    proposal.yaml names the candidate hash before the margin runs, so V05 part (v) sees the uncertified items."""
    pdir = proposal_dir(prof, proposal_id)
    next_version, kept_edits, compiler_refused = write_candidate_pack(
        prof.clients,
        prof.client,
        pdir,
        edits,
        identities=prof.identities,
    )
    if not kept_edits:
        return {
            "next_version": next_version,
            "kept_edits": [],
            "compiler_refused": compiler_refused,
            "candidate_hash": None,
        }

    sync_proposal_pack_to_store(prof, proposal_id)

    comp = _run_cmd(
        [
            "kge.compiler.compile",
            "--profile", prof.id,
            "--clients", str(pdir),
            "--client", prof.client,
            "--out", str(prof.artifacts),
        ],
        prof,
    )
    lines = [ln.strip().rstrip("/") for ln in comp.stdout.splitlines() if ln.strip()]
    if not lines:
        raise CannotImprove("kge/compiler/compile.py produced no artifact path")
    candidate_hash = Path(lines[-1]).name

    initial_record = {
        "format": 1,
        "id": proposal_id,
        "client": prof.client,
        "base_hash": evidence["base_pack_hash"],
        "served_hash": evidence["served_hash"],
        "base_version": evidence["base_version"],
        "next_version": next_version,
        "candidate_hash": candidate_hash,
        "proposer": kept_edits[0].get("proposer", "rules"),
        "state": "drafted",
        "items": kept_edits,
    }
    write_proposal_record(prof, proposal_id, initial_record)

    try:
        _run_cmd(
            ["kge.graph.load", "--profile", prof.id, "--artifact", str(prof.artifacts / prof.client / candidate_hash)],
            prof,
            force_rules=True,
        )
        _run_cmd(["kge.pipeline.run", "--profile", prof.id, "--hash", candidate_hash], prof, force_rules=True)
        step = sync_step()
        if step:
            _run_cmd([step, "--profile", prof.id, "--hash", candidate_hash], prof, force_rules=True)
        _run_cmd(["kge.graph.release", "--profile", prof.id, "--hash", candidate_hash, "--mark-loaded"], prof, force_rules=True)
    except BaseException:
        discard(prof, candidate_hash)
        raise

    return {
        "next_version": next_version,
        "kept_edits": kept_edits,
        "compiler_refused": compiler_refused,
        "candidate_hash": candidate_hash,
    }


def discard(prof: Profile, candidate_hash: str) -> list[str]:
    """Once a candidate's check has ended, passed or failed (or its build failed after its load began): its graph
    generation and its chunks table dropped through the graph store and retrieval ports, so a run whose work is not
    kept leaves nothing behind in the candidates dataset (the dataset's default table expiration is the backstop for
    a run that crashes); its LOADED mark taken off once anything was, so nothing reads it as loaded. Nothing on Ladybug
    with the graph's own vectors, whose candidate stays as it always has. What was dropped."""
    from kge.adapters.graphstore import discard as discard_graph
    from kge.adapters.retrieval import discard_chunks
    graph = prof.at(candidate_hash).graph_path()
    dropped = [*discard_chunks(graph.parent), *discard_graph(graph)]
    if dropped:
        entries = held(graph.parent, LOADED)
        if entries.pop(store_of(graph), None) is not None:
            write_marker(graph.parent, entries, LOADED)
    return dropped


def test_candidate(prof: Profile, candidate_hash: str) -> dict[str, Any]:
    """Run kge/eval/margin.py on candidate_hash with repeats=1 on rules, then drop the candidate's graph and chunks
    (discard), whatever the check's outcome: its verdict and records are files of the run state, which stay.

    Note: exit code 1 is expected when V05 part (v) holds the gate because the proposal's items are not yet accepted/certified.
    Held-out sets are stripped from the records before keep ever sees them.
    """

    args = ["kge.eval.margin", "--profile", prof.id, "--hash", candidate_hash, "--repeats", "1"]
    if prof.baseline is None or not prof.baseline.is_file():
        args += ["--baseline", ""]
    try:
        proc = _run_cmd(args, prof, force_rules=True, ok_codes=(0, 1))
    finally:
        discard(prof, candidate_hash)
    graph = prof.at(candidate_hash).graph_path()
    gate_entry = marked(graph, GATE) or {}
    after_run_id = gate_entry.get("run_id")
    if not after_run_id:
        raise CannotImprove(f"kge/eval/margin.py on {candidate_hash} wrote no GATE run_id: {proc.stderr or proc.stdout}")
    after_dir = prof.run_dir() / "runs" / after_run_id
    raw_records = read_jsonl(after_dir / "records.jsonl")
    learnable_records = [r for r in raw_records if not setfiles.is_heldout(str(r.get("set") or ""))]
    output_lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return {
        "after_run_id": after_run_id,
        "after_run_dir": str(after_dir),
        "gate_passed": bool(gate_entry.get("passed")),
        "gate_lines": output_lines,
        "after_learnable_records": learnable_records,
    }


def keep_decision(
    evidence: dict[str, Any],
    after_learnable_records: list[dict[str, Any]],
    edits: list[dict[str, Any]],
    gate_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Compare item by item on the learnable sets against the before run.

    Kept iff at least one learnable item gains (False -> True), no learnable item falls (True -> False),
    and no non-V05 gate check failed.
    """
    before_passes: dict[str, bool] = evidence.get("learnable_passes") or {}

    after_by_item: dict[tuple[str, str], list[dict]] = {}
    for r in after_learnable_records:
        if r.get("arm") == "treatment":
            after_by_item.setdefault((str(r["set"]), str(r["id"])), []).append(r)

    after_passes: dict[str, bool] = {
        f"{s}:{i}": all(bool(x.get("ok")) for x in recs)
        for (s, i), recs in after_by_item.items()
    }

    gained: list[str] = []
    fell: list[str] = []
    sets_seen: list[str] = []
    for key in sorted(set(before_passes) | set(after_passes)):
        s, item_id = key.split(":", 1)
        if s not in sets_seen:
            sets_seen.append(s)
        was = before_passes.get(key)
        now = after_passes.get(key)
        if was is False and now is True:
            gained.append(key)
        elif was is True and now is False:
            fell.append(key)

    learnable_table: list[dict[str, Any]] = []
    for s in sets_seen:
        b_keys = [k for k in before_passes if k.startswith(f"{s}:")]
        a_keys = [k for k in after_passes if k.startswith(f"{s}:")]
        of = len(set(b_keys) | set(a_keys))
        b_pass = sum(1 for k in b_keys if before_passes[k])
        a_pass = sum(1 for k in a_keys if after_passes[k])
        learnable_table.append({
            "set": s,
            "before_passed": b_pass,
            "after_passed": a_pass,
            "of": of,
            "delta": a_pass - b_pass,
            "gained": [k.split(":", 1)[1] for k in gained if k.startswith(f"{s}:")],
            "fell": [k.split(":", 1)[1] for k in fell if k.startswith(f"{s}:")],
        })

    non_v05_failures = [
        ln for ln in (gate_lines or [])
        if ln.endswith(": failed") and not ln.startswith("V05")
    ]

    kept = bool(gained) and not fell and not non_v05_failures
    dropped_notes: list[dict[str, Any]] = []
    if not kept:
        why_drop = (
            f"dropped proposal: regression on {', '.join(fell)}"
            if fell
            else f"dropped proposal: gate failed ({'; '.join(non_v05_failures)})"
            if non_v05_failures
            else "dropped proposal: no learnable item gained"
        )
        for ed in edits:
            dropped_notes.append({
                "id": ed.get("id"),
                "set": "proposal",
                "question": ", ".join(ed.get("questions") or []),
                "diagnosis": "dropped_proposal",
                "why": f"{ed.get('id')}: {why_drop}",
                "proposer": ed.get("proposer", "rules"),
            })

    return {
        "kept": kept,
        "gained": gained,
        "fell": fell,
        "learnable_table": learnable_table,
        "dropped_notes": dropped_notes,
    }


def judge_heldout(before_run_dir: Path, after_run_dir: Path) -> dict[str, Any]:
    """After keep: read held-out totals from before and after summary.json.

    Labelled 'judge only, never read by the job'. Flags heldout_fell if any held-out set dropped.
    """
    before_sum = json.loads((Path(before_run_dir) / "summary.json").read_text()) if (Path(before_run_dir) / "summary.json").is_file() else {}
    after_sum = json.loads((Path(after_run_dir) / "summary.json").read_text()) if (Path(after_run_dir) / "summary.json").is_file() else {}
    before_man = json.loads((Path(before_run_dir) / "manifest.json").read_text()) if (Path(before_run_dir) / "manifest.json").is_file() else {}
    after_man = json.loads((Path(after_run_dir) / "manifest.json").read_text()) if (Path(after_run_dir) / "manifest.json").is_file() else {}

    before_sets = before_sum.get("sets") if isinstance(before_sum.get("sets"), dict) else before_sum
    after_sets = after_sum.get("sets") if isinstance(after_sum.get("sets"), dict) else after_sum
    before_msets = before_man.get("sets") if isinstance(before_man.get("sets"), dict) else {}
    after_msets = after_man.get("sets") if isinstance(after_man.get("sets"), dict) else {}

    held_sets = [s for s in dict.fromkeys([*before_sets.keys(), *after_sets.keys()]) if setfiles.is_heldout(s)]
    table: list[dict[str, Any]] = []
    for s in held_sets:
        b_all = ((before_sets.get(s) or {}).get("treatment") or {}).get("all") or {}
        a_all = ((after_sets.get(s) or {}).get("treatment") or {}).get("all") or {}
        b_pass = int(round((b_all.get("passed") or {}).get("mean", 0)))
        a_pass = int(round((a_all.get("passed") or {}).get("mean", 0)))
        a_mset = after_msets.get(s) if isinstance(after_msets.get(s), dict) else {}
        b_mset = before_msets.get(s) if isinstance(before_msets.get(s), dict) else {}
        of = int(
            a_mset.get("items")
            or a_mset.get("size")
            or b_mset.get("items")
            or b_mset.get("size")
            or a_all.get("of")
            or b_all.get("of")
            or 0
        )
        fell = a_pass < b_pass
        table.append({
            "set": s,
            "before_passed": b_pass,
            "after_passed": a_pass,
            "of": of,
            "delta": a_pass - b_pass,
            "fell": fell,
            "label": "judge only, never read by the job",
        })
    return {
        "label": "judge only, never read by the job",
        "heldout_table": table,
        "heldout_fell": any(r["fell"] for r in table),
    }
