"""The LangGraph improvement workflow:

    python3 -m kge.improve.flow [--profile <id>] [--id <proposal_id>] [--unit <unit>]
    make improve [PROFILE=<id>] [UNIT=<unit>]

StateGraph topology:
    START -> gather -> diagnose -> propose -+-> nothing to try -----------------> hand_over -> END
                                            +-> build -> test -> keep -+-> dropped -> hand_over -> END
                                                                       +-> judge ---> hand_over -> END
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import operator
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from kge.pipeline.trace import append_jsonl
from kge.pipeline.objstore import read_proposal_record, proposal_pending
from kge.improve import CannotImprove, abtest, edits as edits_mod, gather as gather_mod, rules as rules_mod
from kge.adapters.model import LLM, ModelSettingError, model_for
from kge.pipeline.profile import Profile, ProfileError, resolve
from kge import packstore
from kge.estate.onboard import OnboardError, require_improve_unit, require_no_pending_proposal, require_process

DEFAULT_PROPOSAL_ID = "open"


class ImproveState(TypedDict, total=False):
    proposal_id: str
    unit: str | None
    status_lines: Annotated[list[str], operator.add]
    ab_misses: list[dict[str, Any]]
    page_misses: list[dict[str, Any]]
    evidence: dict[str, Any]
    ab_diagnoses: list[dict[str, Any]]
    page_diagnoses: list[dict[str, Any]]
    proposer: str
    edits: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    refused: list[dict[str, Any]]
    next_version: str
    candidate_hash: str | None
    after_run_id: str | None
    after_run_dir: str | None
    gate_passed: bool
    gate_lines: list[str]
    after_learnable_records: list[dict[str, Any]]
    kept: bool
    gained: list[str]
    fell: list[str]
    learnable_table: list[dict[str, Any]]
    heldout_table: list[dict[str, Any]]
    heldout_fell: bool
    proposal: dict[str, Any] | None


def _after_propose(state: ImproveState) -> str:
    return "build" if state.get("edits") else "hand_over"


def _after_keep(state: ImproveState) -> str:
    return "judge" if state.get("kept") else "hand_over"


def clear_open_proposals(prof: Profile, proposal_id: str = DEFAULT_PROPOSAL_ID) -> None:
    """Remove only this run's replaceable proposal, never kept work awaiting review."""
    store = packstore.for_profile(prof)
    name = f"proposals/{proposal_id}/proposal.yaml"
    if store.exists(name):
        try:
            record = read_proposal_record(store, name)
        except ValueError:
            return
        if proposal_pending(record, prof.model_hash()):
            return
    store.delete(f"proposals/{proposal_id}/")
    folder = prof.clients / prof.client / "proposals"
    shutil.rmtree(folder / proposal_id, ignore_errors=True)
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()



def build_workflow(
    prof: Profile,
    llm: LLM | None = None,
    on_step: Callable[[str, str], None] | None = None,
) -> CompiledStateGraph:
    """Build the LangGraph StateGraph for the improvement job."""

    def _emit(node: str, line: str) -> list[str]:
        if on_step is not None:
            on_step(node, line)
        return [line]

    def node_gather(state: ImproveState) -> ImproveState:
        out = gather_mod.gather(prof, unit=state["unit"]) if state.get("unit") is not None else gather_mod.gather(prof)
        line = (
            f"gather: read run {out['evidence']['before_run_id']} on {out['evidence']['served_hash'][:12]} "
            f"({len(out['ab_misses'])} learnable miss(es), {len(out['page_misses'])} unmatched page question(s); held-out dropped)"
        )
        return {**out, "status_lines": _emit("gather", line)}

    def node_diagnose(state: ImproveState) -> ImproveState:
        out = rules_mod.diagnose_all(state.get("ab_misses") or [], state.get("page_misses") or [], state["evidence"])
        counts: dict[str, int] = {}
        for d in out["ab_diagnoses"]:
            k = d["diagnosis"]
            counts[k] = counts.get(k, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no misses"
        line = f"diagnose: {summary}"
        return {**out, "status_lines": _emit("diagnose", line)}

    def node_propose(state: ImproveState) -> ImproveState:
        out = edits_mod.propose(
            state.get("ab_diagnoses") or [],
            state.get("page_diagnoses") or [],
            state["evidence"],
            llm=llm,
        )
        line = (
            f"propose ({out['proposer']}): {len(out['edits'])} allowlisted edit(s), "
            f"{len(out['notes'])} steward note(s), {len(out['requests'])} platform request(s)"
            + (f", {len(out['refused'])} refused" if out["refused"] else "")
        )
        return {**out, "status_lines": _emit("propose", line)}

    def node_build(state: ImproveState) -> ImproveState:
        pid = state.get("proposal_id") or DEFAULT_PROPOSAL_ID
        require_no_pending_proposal(prof)
        clear_open_proposals(prof, pid)
        out = abtest.build_candidate(prof, pid, state.get("edits") or [], state["evidence"])
        refused = list(state.get("refused") or []) + list(out.get("compiler_refused") or [])
        notes = list(state.get("notes") or [])
        for r in out.get("compiler_refused") or []:
            notes.append({
                "id": None,
                "set": "compiler",
                "question": "",
                "diagnosis": "compiler_refused",
                "why": r["reason"],
                "proposer": r.get("proposer", state.get("proposer", "rules")),
            })
        chash = out.get("candidate_hash")
        line = (
            f"build: candidate {chash[:12]} (pack {state['evidence']['base_version']} -> {out['next_version']})"
            if chash
            else "build: no candidate built (all edits refused by compiler)"
        )
        return {
            "next_version": out["next_version"],
            "edits": out["kept_edits"],
            "refused": refused,
            "notes": notes,
            "candidate_hash": chash,
            "status_lines": _emit("build", line),
        }

    def node_test(state: ImproveState) -> ImproveState:
        chash = state.get("candidate_hash")
        if not chash:
            return {"kept": False, "status_lines": _emit("test", "test: skipped (no candidate hash)")}
        out = abtest.test_candidate(prof, chash)
        line = f"test: margin run {out['after_run_id']} on {chash[:12]}"
        return {**out, "status_lines": _emit("test", line)}

    def node_keep(state: ImproveState) -> ImproveState:
        if not state.get("candidate_hash"):
            return {"kept": False, "status_lines": _emit("keep", "keep: dropped (no candidate)")}
        out = abtest.keep_decision(
            state["evidence"],
            state.get("after_learnable_records") or [],
            state.get("edits") or [],
            gate_lines=state.get("gate_lines"),
        )
        notes = list(state.get("notes") or []) + list(out.get("dropped_notes") or [])
        if out["kept"]:
            line = f"keep: kept ({len(out['gained'])} item(s) gained, 0 fell)"
        else:
            line = f"keep: dropped ({len(out['gained'])} gained, {len(out['fell'])} fell)"
        return {
            "kept": out["kept"],
            "gained": out["gained"],
            "fell": out["fell"],
            "learnable_table": out["learnable_table"],
            "notes": notes,
            "status_lines": _emit("keep", line),
        }

    def node_judge(state: ImproveState) -> ImproveState:
        out = abtest.judge_heldout(
            Path(state["evidence"]["before_run_dir"]),
            Path(state["after_run_dir"]),
        )
        summary = ", ".join(
            f"{r['set']} {r['before_passed']} -> {r['after_passed']} of {r['of']}"
            for r in out["heldout_table"]
        ) or "no held-out sets"
        line = f"judge (held-out, never read by the job): {summary}"
        return {
            "heldout_table": out["heldout_table"],
            "heldout_fell": out["heldout_fell"],
            "status_lines": _emit("judge", line),
        }

    def node_hand_over(state: ImproveState) -> ImproveState:
        pid = state.get("proposal_id") or DEFAULT_PROPOSAL_ID
        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ev = state["evidence"]
        proposer = state.get("proposer") or "rules"
        notes = state.get("notes") or []
        requests = state.get("requests") or []
        refused = state.get("refused") or []

        req_file = prof.run_dir() / "improve" / "requests.jsonl"
        for req in requests:
            append_jsonl(req_file, {
                "at": now_iso,
                "client": prof.client,
                "served_hash": ev["served_hash"],
                "id": req.get("id"),
                "set": req.get("set"),
                "question": req.get("question"),
                "diagnosis": req.get("diagnosis"),
                "why": req.get("why"),
                "proposer": req.get("proposer", proposer),
            })

        status_lines = list(state.get("status_lines") or [])
        if state.get("kept") and state.get("candidate_hash") and not state.get("heldout_fell"):
            edits = state.get("edits") or []
            ab_summary = ", ".join(
                f"{r['set']} {r['before_passed']} -> {r['after_passed']} of {r['of']}"
                for r in (state.get("learnable_table") or [])
                if r["before_passed"] != r["after_passed"]
            ) or "unchanged"
            change_note = (
                f"Add alias {', '.join(repr(e['phrase']) + ' -> ' + e['term'] for e in edits)} "
                f"(by {proposer}; A/B: {ab_summary})"
            )
            line = f"hand_over: proposal {pid} ready for business acceptance and steward certification"
            full_lines = status_lines + _emit("hand_over", line)
            l_table = state.get("learnable_table") or []
            h_table = state.get("heldout_table") or []
            proposal_record = {
                "format": 1,
                "id": pid,
                "created_at": now_iso,
                "client": prof.client,
                "proposer": proposer,
                "from": f"improve job · {proposer}",
                "base_hash": ev["base_pack_hash"],
                "served_hash": ev["served_hash"],
                "base_version": ev["base_version"],
                "next_version": state.get("next_version"),
                "version": {"from": ev["base_version"], "to": state.get("next_version")},
                "candidate_hash": state["candidate_hash"],
                "before_run_id": ev["before_run_id"],
                "after_run_id": state.get("after_run_id"),
                "report": f"/ab/{state['after_run_id']}/report.html" if state.get("after_run_id") else None,
                "kept": True,
                "state": "drafted",
                "change_note": change_note,
                "items": edits,
                "learnable_table": l_table,
                "heldout_table": h_table,
                "ab": {
                    "learnable": {
                        "sets": {r["set"]: {"before": r["before_passed"], "after": r["after_passed"], "of": r["of"]} for r in l_table},
                    },
                    "heldout": {
                        "note": "judge only, never read by the job",
                        "sets": {r["set"]: {"before": r["before_passed"], "after": r["after_passed"], "of": r["of"]} for r in h_table},
                    },
                },
                "heldout_fell": bool(state.get("heldout_fell")),
                "gate_lines": state.get("gate_lines") or [],
                "notes": notes,
                "requests": requests,
                "refused": refused,
                "status_lines": full_lines,
            }
            require_no_pending_proposal(prof)
            abtest.write_proposal_record(prof, pid, proposal_record)
            return {"proposal": proposal_record, "status_lines": [line]}
        else:
            clear_open_proposals(prof, pid)
            fell_reason = "held-out fell; " if state.get("heldout_fell") else ""
            line = f"hand_over: {fell_reason}no kept proposal ({len(notes)} note(s), {len(requests)} request(s))"
            full_lines = status_lines + _emit("hand_over", line)
            summary_record = {
                "format": 1,
                "id": pid,
                "created_at": now_iso,
                "client": prof.client,
                "proposer": proposer,
                "from": f"improve job · {proposer}",
                "base_hash": ev["base_pack_hash"],
                "served_hash": ev["served_hash"],
                "base_version": ev["base_version"],
                "next_version": None,
                "candidate_hash": None,
                "before_run_id": ev["before_run_id"],
                "after_run_id": state.get("after_run_id"),
                "kept": False,
                "state": "none",
                "items": [],
                "learnable_table": state.get("learnable_table") or [],
                "heldout_table": state.get("heldout_table") or [],
                "heldout_fell": bool(state.get("heldout_fell")),
                "notes": notes,
                "requests": requests,
                "refused": refused,
                "status_lines": full_lines,
            }
            last_file = prof.run_dir() / "improve" / "last_run.json"
            last_file.parent.mkdir(parents=True, exist_ok=True)
            last_file.write_text(json.dumps(summary_record, indent=2) + "\n")
            return {"proposal": None, "status_lines": [line]}

    g = StateGraph(ImproveState)
    g.add_node("gather", node_gather)
    g.add_node("diagnose", node_diagnose)
    g.add_node("propose", node_propose)
    g.add_node("build", node_build)
    g.add_node("test", node_test)
    g.add_node("keep", node_keep)
    g.add_node("judge", node_judge)
    g.add_node("hand_over", node_hand_over)

    g.add_edge(START, "gather")
    g.add_edge("gather", "diagnose")
    g.add_edge("diagnose", "propose")
    g.add_conditional_edges("propose", _after_propose, {"build": "build", "hand_over": "hand_over"})
    g.add_edge("build", "test")
    g.add_edge("test", "keep")
    g.add_conditional_edges("keep", _after_keep, {"judge": "judge", "hand_over": "hand_over"})
    g.add_edge("judge", "hand_over")
    g.add_edge("hand_over", END)
    return g.compile()


def run_improve(
    prof: Profile,
    proposal_id: str = DEFAULT_PROPOSAL_ID,
    llm: LLM | None | str = "auto",
    on_step: Callable[[str, str], None] | None = None,
    unit: str | None = None,
    as_: str | None = None,
) -> ImproveState:
    """Run the improvement StateGraph end to end on prof. llm "auto" is the enhance agent's model (model_for: its
    ENHANCE_MODEL_TIER, else the high tier where one is set, else MODEL_PROVIDER's, as before tiers)."""
    if unit is not None:
        require_improve_unit(prof, unit, as_)
    elif as_ is not None:
        require_process(prof, as_, "improving")
    require_no_pending_proposal(prof)
    model = model_for("enhance") if llm == "auto" else llm
    wf = build_workflow(prof, llm=model, on_step=on_step)
    try:
        return wf.invoke({"proposal_id": proposal_id, "status_lines": [], "unit": unit})
    except BaseException:
        clear_open_proposals(prof, proposal_id)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose graph and pack store to improve (default: PROFILE, else the registry's default)")
    ap.add_argument("--id", default=DEFAULT_PROPOSAL_ID, help="proposal id (default: open)")
    ap.add_argument("--unit", help="gather and propose for this unit only (default: client-wide)")
    ap.add_argument("--as", dest="as_", help="check the selected unit against this principal's role")
    args = ap.parse_args(argv)
    try:
        prof = resolve(args.profile)
        state = run_improve(prof, proposal_id=args.id, unit=args.unit, as_=args.as_,
                            on_step=lambda _node, line: print(line, flush=True))
    except (ProfileError, CannotImprove, ModelSettingError, OnboardError) as exc:
        print(f"improve: {exc}", file=sys.stderr)
        return 2
    prop = state.get("proposal")
    if prop and prop.get("kept"):
        print(
            f"improve: proposal {prop['id']} ({prop['base_version']} -> {prop['next_version']}, "
            f"candidate {prop['candidate_hash'][:12]}, by {prop['proposer']}) written to "
            f"clients/{prof.client}/proposals/{prop['id']}/proposal.yaml"
        )
    else:
        print("improve: no proposal kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
