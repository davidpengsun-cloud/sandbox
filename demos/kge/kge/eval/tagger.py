"""Evaluate passage tags, separately from the graph's question-answering gates.

Mock runs prove the scorer and model seam, never model quality. The pilot run
requires a frozen target agreement and explicit --live. Nothing retags a graph,
changes a default, releases a candidate or writes a release gate marker.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sys
from uuid import uuid4

from kge.adapters.objstore import open_store
from kge.adapters.model import model_for
from kge.adapters.model.errors import ExternalCallError
from kge.adapters.model.providers import ModelSettingError
from kge.eval import harness, margin, report, sets as setfiles
from kge.pipeline.envfile import load_env
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT, settings
from kge.pipeline.tagger import ModelTagger, RuleTagger, PROMPT_VERSION
from kge.services.ask import Engine

DATA = ROOT / "examples" / "tagger-eval"
TIERS = ("low", "medium", "high")
FAMILIES = {"candidates": "candidate_term", "mentions": "business_meaning"}


class EvaluationError(ValueError):
    """An evaluator refusal whose message is safe to show in job diagnostics."""


def failure_line(exc: Exception, context: str) -> str:
    detail = str(exc) if isinstance(exc, (ExternalCallError, ModelSettingError, EvaluationError)) else type(exc).__name__
    return f"{context} could not complete ({detail}); no quality verdict"


def normal(value: str) -> str:
    """Only case and whitespace are ignored; no fuzzy matching or inferred synonyms."""
    return " ".join(value.split()).casefold()


def read_set(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not data.get("version") or not data.get("glossary") or not data.get("passages"):
        raise EvaluationError("evaluation set needs version, glossary and passages")
    ids = [p["id"] for p in data["passages"]]
    if len(ids) != len(set(ids)):
        raise EvaluationError("duplicate passage id")
    glossary = {normal(term) for term in data["glossary"]}
    for p in data["passages"]:
        if not p["text"].strip() or not p["document"] or p["shelf"] not in {"certified", "uncertified"}:
            raise EvaluationError("passage needs text, document and a valid shelf")
        for family in FAMILIES:
            labels = p[family]
            if not isinstance(labels, list) or any(not isinstance(t, str) or not normal(t) for t in labels):
                raise EvaluationError("expected labels must be nonempty strings")
            if len(labels) != len({normal(t) for t in labels}):
                raise EvaluationError("duplicate expected label in a passage")
        if not {normal(t) for t in p["mentions"]} <= glossary:
            raise EvaluationError("expected mentions must be glossary terms")
        if {normal(t) for t in p["candidates"]} & glossary:
            raise EvaluationError("a candidate must not already be in the glossary")
    return data


def score(expected: list[str], found: list[str]) -> dict:
    wanted, got = {normal(t) for t in expected}, {normal(t) for t in found}
    return {"expected": len(wanted), "found": len(wanted & got), "wrong": len(got - wanted),
            "missed": len(wanted - got), "matched": sorted(wanted & got),
            "false_positives": sorted(got - wanted), "false_negatives": sorted(wanted - got)}


def evaluate(data: dict, tagger, repeats: int) -> dict:
    rows = []
    for repeat in range(1, repeats + 1):
        for passage in data["passages"]:
            proposals = tagger.propose(passage["text"], passage["shelf"], passage["kind"])
            if any(p.value == "__model_error__" for p in proposals):
                raise EvaluationError("tagger model failed; this is not a quality result")
            mentions = [p.value for p in proposals if p.family == FAMILIES["mentions"]]
            surfaces = [t for t in passage["mentions"]
                        if re.search(r"\b" + re.escape(t) + r"\b", passage["text"], re.IGNORECASE)]
            identifiers = {normal(t) for t in passage.get("identifiers", [])}
            wrong_identifiers = sorted({normal(p.value) for p in proposals
                                        if p.family == FAMILIES["candidates"]
                                        and (normal(p.value) in identifiers
                                             or re.fullmatch(r"\w+_\w+", p.value))})
            rows.append({"surface_mentions": score(surfaces, [t for t in mentions if normal(t) in
                                                             {normal(s) for s in surfaces}]),
                         "candidate_identifiers": wrong_identifiers,
                         "passage": passage["id"], "document": passage["document"], "repeat": repeat,
                         "proposals": [asdict(p) for p in proposals],
                         **{name: score(passage[name], [p.value for p in proposals if p.family == family])
                            for name, family in FAMILIES.items()}})
    totals = {name: {field: sum(row[name][field] for row in rows)
                     for field in ("expected", "found", "wrong", "missed")} for name in (*FAMILIES, "surface_mentions")}
    return {"totals": totals, "candidate_identifiers": sum(len(r["candidate_identifiers"]) for r in rows),
            "rows": rows, "withheld": getattr(tagger, "withheld", 0)}


@contextmanager
def environment(values: dict):
    before = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def configuration(live: bool) -> dict:
    if live:
        return {}
    return {"MODEL_PROVIDER": "mock", "MODEL_NAME": "mock", "TAGGER_PROVIDER": "model",
            "MOCK_MODE": "careful", "ANSWER_MODEL_TIER": "medium", "CONTEXT_MODEL_TIER": "low",
            **{f"MODEL_{tier.upper()}_{field}": "mock" for tier in TIERS for field in ("PROVIDER", "NAME")}}


def agreement(path: Path | None, data: dict, repeats: int) -> dict | None:
    if path is None:
        return None
    value = json.loads(path.read_text())
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    if not value.get("agreed_by") or not value.get("agreed_at") or value.get("set_sha256") != digest:
        raise EvaluationError("targets need agreed_by, agreed_at and this set's set_sha256")
    if value.get("repeats") != repeats:
        raise EvaluationError("targets must agree this repeat count")
    for family in FAMILIES:
        total = sum(len(p[family]) for p in data["passages"]) * repeats
        limits = value[family]
        if any(type(limits.get(k)) is not int or limits[k] < 0 for k in ("min_found", "max_wrong")):
            raise EvaluationError("targets must be nonnegative integer counts")
        if limits["min_found"] > total:
            raise EvaluationError("min_found exceeds the expected count across repeats")
    return value


def meets(totals: dict, targets: dict) -> bool:
    return all(totals[name]["found"] >= targets[name]["min_found"]
               and totals[name]["wrong"] <= targets[name]["max_wrong"] for name in FAMILIES)


def verdict(row: dict, targets: dict) -> dict:
    """Apply the owner's baseline or model bar; an empty family has perfect recall."""
    totals = row["totals"]
    def recall(name):
        counts = totals[name]
        return counts["found"] / counts["expected"] if counts["expected"] else 1.0
    def wrong_rate(name):
        counts = totals[name]
        proposed = counts["found"] + counts["wrong"]
        return counts["wrong"] / proposed if proposed else 0.0
    checks = {
        "mention_recall": recall("mentions") >= targets["mention_recall"],
        "surface_recall": recall("surface_mentions") >= targets["surface_recall"],
        "candidate_recall": recall("candidates") >= targets["candidate_recall"],
        "mention_wrong_rate": wrong_rate("mentions") <= targets["mention_wrong_rate"],
        "candidate_wrong_rate": wrong_rate("candidates") <= targets["candidate_wrong_rate"],
    }
    if "max_wrong_candidates" in targets:
        checks["max_wrong_candidates"] = totals["candidates"]["wrong"] <= targets["max_wrong_candidates"]
    if targets.get("forbid_identifiers"):
        checks["candidate_identifiers"] = row.get("candidate_identifiers", 0) == 0
    return checks


def ab(profile, out: Path, repeats: int) -> dict:
    """Rules and configured agents on the same served graph, each graph-off/on.

    This is an answering A/B, not a retagging experiment. Its V01–V04 are
    reported independently of the passage scorer and never sign off a release.
    """
    results = {}
    engine = Engine.serving(profile)
    try:
        profile = engine.profile
        gates = margin.thresholds(profile)
        if not gates or not gates.get("golden_min_pass") or not gates.get("control_margin"):
            raise EvaluationError("A/B needs the compiled pack's V01 and V02 gates")
        sets = harness.choose_sets("all", profile.estate(), setfiles.declared(profile.paths()["pack"]),
                                   profile.paths()["pack"])
        for label, llm in (("rules", None), ("tiered", model_for("answer"))):
            if label == "tiered" and llm is None:
                raise EvaluationError("tiered A/B has no answering model")
            folder = harness.run(profile, harness.Arms(engine, llm=llm), list(report.MARGIN_ARMS),
                                 sets, repeats, out=out / "ab" / label)
            manifest, records, _ = report.load(folder)
            checks = margin.post_load(gates, records, manifest)
            results[label] = {"run": folder.relative_to(out).as_posix(), "manifest": manifest, "thresholds": gates,
                              "margin": report.margins(records),
                              "gates": [c._asdict() for c in checks],
                              "passed": all(c.ok is True for c in checks if c.rule != "V03")}
    finally:
        engine.close()
    return results


def markdown(result: dict) -> str:
    lines = ["# Tagger evaluation", "", result["purpose"], "",
             f"Set: {result['version']}; SHA256: `{result['set_sha256']}`; repeats: {result['repeats']}.", "",
             "Counts are unique (passage, label) pairs per repeat; duplicates within a passage count once.", "",
             "| Arm | Family | Expected | Found | Wrong | Missed |",
             "|---|---|---:|---:|---:|---:|"]
    for arm, row in result["arms"].items():
        for family, totals in row["totals"].items():
            cells = " | ".join(str(totals[k]) for k in ("expected", "found", "wrong", "missed"))
            lines.append(f"| {arm} | {family} | {cells} |")
    lines += ["", "Targets: owner agreement of 2026-11-02, recorded in result.json", ""]
    lines += [f"{arm}: {'PASS' if row['passed'] else 'FAIL'} ({', '.join(k for k, ok in row['checks'].items() if not ok) or 'all targets met'})"
              for arm, row in result["arms"].items()]
    lines += [""]
    for label, run in result.get("ab", {}).items():
        lines += [f"A/B {label}: {run['run']}", ""] + [c["line"] for c in run["gates"]] + [""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, default=DATA / "set.json")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "tagger-eval")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--job", action="store_true")
    parser.add_argument("--ab", action="store_true")
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.live and args.targets is None:
        parser.error("--live needs --targets agreed with the owner before the run")
    if args.job:
        return job(args)
    return run(args)


def job(args) -> int:
    """Keep each job's evidence in its run state and, when configured, the object store."""
    load_env()
    profile = resolve(args.profile)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    key = f"runs/{profile.client}/tagger-eval/{stamp}"
    args.out = profile.run_dir() / "tagger-eval" / stamp
    args.out.mkdir(parents=True)
    store = open_store(settings().STORE_URL) if settings().STORE_URL else None
    status = 2
    error = None
    try:
        if args.targets is not None and store is not None:
            target_key = args.targets.as_posix()
            if not target_key.startswith(f"uploads/{profile.client}/") or ".." in args.targets.parts:
                raise EvaluationError("TARGETS must name an upload under this client's uploads prefix")
            raw = store.get(target_key)
            if raw is None:
                raise EvaluationError("targets object is missing")
            args.targets = args.out / "targets.json"
            args.targets.write_bytes(raw)
        status = run(args)
    except Exception as exc:
        error = failure_line(exc, "tagger evaluation job")
        print(error, file=sys.stderr)
    finally:
        record = {"exit_code": status, "complete": status in (0, 1), "client": profile.client,
                  "results": key, "finished_at": datetime.now(timezone.utc).isoformat()}
        if status in (0, 1):
            result = json.loads((args.out / "result.json").read_text())
            record["verdict"] = "pass" if status == 0 else "fail"
            record["arms"] = {name: row["passed"] for name, row in result["arms"].items()}
            record["quality_evidence"] = result["purpose"] == "Live model quality evidence"
        else:
            record["verdict"] = "incomplete"
        if error is not None:
            record["error"] = error
        (args.out / "status.json").write_text(json.dumps(record, indent=2) + "\n")
        if store is not None:
            for path in sorted(args.out.rglob("*")):
                if path.is_file() and path.name != "status.json":
                    store.put(f"{key}/{path.relative_to(args.out).as_posix()}", path.read_bytes())
            store.put(f"{key}/status.json", (args.out / "status.json").read_bytes())
            print(f"Results: {store.url.rstrip('/')}/{key}/")
        else:
            print(f"Results: {args.out}")
    return status


def run(args) -> int:
    try:
        load_env()
        data = read_set(args.set)
        counts = agreement(args.targets, data, args.repeats)
        targets = json.loads((DATA / "targets.json").read_text())
        config = configuration(args.live)
        result = {"version": data["version"], "set": data, "started_at": datetime.now(timezone.utc).isoformat(),
                  "set_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
                  "repeats": args.repeats, "targets": targets, "count_agreement": counts, "prompt_version": PROMPT_VERSION,
                  "configuration": {key: os.environ[key] for key in (
                      "ANSWER_MODEL_TIER", "CONTEXT_MODEL_TIER", "VERTEX_PROJECT", "VERTEX_REGION")
                      if key in os.environ}, "arms": {},
                  "purpose": "Live model quality evidence" if args.live else "Mock plumbing check; not model quality evidence"}
        with environment(config):
            result["configuration"].update(config)
            result["arms"]["RuleTagger"] = evaluate(data, RuleTagger(data["glossary"]), args.repeats)
            for tier in TIERS:
                with environment({"TAGGER_MODEL_TIER": tier}):
                    llm = model_for("tagger")
                    if llm is None or llm.tier != tier:
                        raise EvaluationError(f"no model configured for tier {tier}")
                    if llm.identity()["provider"] == "mock":
                        result["purpose"] = "Mock plumbing check; not model quality evidence"
                    result["arms"][tier] = {**evaluate(data, ModelTagger.configured(data["glossary"], llm), args.repeats),
                                            "model": {**llm.identity(), "tier": llm.tier}}
            if args.ab:
                result["ab"] = ab(resolve(args.profile), args.out, args.repeats)
        for name, row in result["arms"].items():
            row["checks"] = verdict(row, targets["rules" if name == "RuleTagger" else "model"])
            if counts:
                row["checks"]["count_agreement"] = meets(row["totals"], counts)
            row["passed"] = all(row["checks"].values())
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        (args.out / "report.md").write_text(markdown(result))
        print(markdown(result))
        if not args.job:
            print(f"Records: {args.out / 'result.json'}")
        failed = any(row.get("passed") is False for row in result["arms"].values())
        failed |= any(not row["passed"] for row in result.get("ab", {}).values())
        return 1 if failed else 0
    except Exception as exc:
        if args.job:
            raise
        print(failure_line(exc, "tagger evaluation"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
