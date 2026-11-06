"""Evaluate assistance directly or with --api, using the served profile's golden, catalogue and held-out routing questions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import urllib.request

from kge.eval import page_scope, sets
from kge.eval.run_golden import API_IN_FLIGHT, asked_as
from kge.pipeline.profile import resolve
from kge.services import ask as governed
from kge.services import catalogue
from kge.services.assist import assist
from kge.services.identity import token_for

EXTRA = {"source", "sources", "assistant_available", "notice", "run_trace", "trace"}
HELDOUT_THRESHOLD = 0.9
HELD_OUT = json.loads(Path(__file__).with_name("assist_heldout.json").read_text())


def comparable(answer):
    return {key: value for key, value in answer.items() if key not in EXTRA}


def numeric_text(text):
    """Standalone numeric literals, excluding digits embedded in source identifiers."""
    return re.findall(r"(?<![\w])\d+(?:[.,:-]\d+)*(?![\w])", re.sub(r"\b[\w.-]*[a-zA-Z_][\w.-]*\b", "", text))


def numeric_evidence(question, sources, products):
    """Only a requested numeric catalogue attribute can back metadata prose."""
    ids = {source["id"].casefold() for source in sources}
    selected = [p for p in products if f"{p['unit_id']}.{p['table']}".casefold() in ids]
    q = question.casefold()
    evidence = []
    for p in selected:
        if "quality" in q and "score" in q:
            evidence.append(str(p["quality_score"]))
        if re.search(r"\b(?:refresh|refreshed|cadence|frequency|updated|up to date)\b", q):
            evidence.append(p.get("last_refresh") or "")
        if "how many" in q and re.search(r"\b(?:fields|columns|attributes)\b", q):
            evidence.append(str(len(p["fields"])))
        if re.search(r"\bdata types?\b", q):
            evidence.extend(f["data_type"] for f in p["fields"])
    if "how many" in q and re.search(r"\b(?:products|tables)\b", q):
        evidence.append(str(len(selected)))
    return set(numeric_text(" ".join(evidence)))


def evaluate_suite(golden, metadata, ask, assistance, products=(), heldout=HELD_OUT):
    """Pass marks across golden, C01-C20 catalogue and held-out routing questions, suitable for tests and the command line."""
    batch1 = [item for item in heldout if item.get("batch", 1) == 1]
    batch2 = [item for item in heldout if item.get("batch") == 2]
    report = {
        "catalogue_answered": 0,
        "catalogue_figures": 0,
        "golden_unchanged": 0,
        "heldout_routed": 0,
        "heldout_total": len(batch1),
        "heldout_accuracy": 1.0,
        "heldout_batch2_routed": 0,
        "heldout_batch2_total": len(batch2),
        "heldout_batch2_accuracy": 1.0,
        "unbacked_numbers": 0,
        "failures": [],
    }
    for item in golden:
        baseline, out = ask(item), assistance(item)
        if comparable(baseline) == comparable(out):
            report["golden_unchanged"] += 1
        else:
            report["failures"].append(item["id"] + ": governed result changed")
        if out.get("behaviour") in ("figure", "parts", "sum", "cite_certified", "cite_uncertified") and not out.get("sources"):
            report["failures"].append(item["id"] + ": missing ask sources")
        report["unbacked_numbers"] += len(set(numeric_text(out.get("answer_text", ""))) - set(numeric_text(baseline.get("answer_text", ""))))
    for item in metadata:
        out = assistance(item)
        ok = out.get("source") in ("catalogue", "search") and out.get("behaviour") in ("catalogue", "search") and bool(out.get("sources"))
        report["catalogue_answered"] += int(ok)
        if not ok:
            report["failures"].append(item["id"] + ": no metadata answer")
        report["catalogue_figures"] += int(out.get("figure") is not None or bool(out.get("figures") or out.get("parts") or out.get("rows")))
        permitted = " ".join(s.get("id", "") + " " + s.get("title", "") for s in out.get("sources", []))
        q = item["question"].lower()
        allowed = set(numeric_text(permitted)) | numeric_evidence(q, out.get("sources", []), products)
        report["unbacked_numbers"] += len(set(numeric_text(out.get("answer_text", ""))) - allowed)
    model_active = False
    for item in heldout:
        out = assistance(item)
        model_active = model_active or bool(out.get("assistant_available"))
        expected_source = item["source"]
        if expected_source == "ask":
            routed = out.get("source") == "ask"
        else:
            expected_behaviour = item.get("behaviour", expected_source)
            routed = (
                out.get("source") == expected_source
                and out.get("behaviour") == expected_behaviour
                and (not out.get("sources") if expected_behaviour in ("abstain", "refuse") else bool(out.get("sources")))
            )
            has_figure = out.get("figure") is not None or bool(out.get("figures") or out.get("parts") or out.get("rows"))
            if expected_source != "ask" and (item.get("batch", 1) == 1 or model_active or out.get("source") in ("catalogue", "search")):
                report["catalogue_figures"] += int(has_figure)
                if has_figure:
                    report["failures"].append(item["id"] + ": catalogue question returned a figure")
            if out.get("source") in ("catalogue", "search") and expected_behaviour not in ("abstain", "refuse"):
                permitted = " ".join(s.get("id", "") + " " + s.get("title", "") for s in out.get("sources", []))
                q = item["question"].lower()
                allowed = set(numeric_text(permitted)) | numeric_evidence(q, out.get("sources", []), products)
                report["unbacked_numbers"] += len(set(numeric_text(out.get("answer_text", ""))) - allowed)
        if item.get("batch") == 2:
            report["heldout_batch2_routed"] += int(routed)
        else:
            report["heldout_routed"] += int(routed)
    if batch1:
        report["heldout_accuracy"] = round(report["heldout_routed"] / len(batch1), 4)
        if report["heldout_accuracy"] < HELDOUT_THRESHOLD:
            report["failures"].append(f"heldout: routing accuracy {report['heldout_routed']}/{len(batch1)} below {HELDOUT_THRESHOLD:.0%}")
    if batch2:
        report["heldout_batch2_accuracy"] = round(report["heldout_batch2_routed"] / len(batch2), 4)
        if model_active and report["heldout_batch2_accuracy"] < HELDOUT_THRESHOLD:
            report["failures"].append(f"heldout_batch2: routing accuracy {report['heldout_batch2_routed']}/{len(batch2)} below {HELDOUT_THRESHOLD:.0%}")
    report["passed"] = (
        report["catalogue_answered"] == len(metadata)
        and report["catalogue_figures"] == 0
        and report["golden_unchanged"] == len(golden)
        and report["heldout_accuracy"] >= HELDOUT_THRESHOLD
        and (not model_active or report["heldout_batch2_accuracy"] >= HELDOUT_THRESHOLD)
        and report["unbacked_numbers"] == 0
        and not report["failures"]
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--api")
    parser.add_argument("--as", dest="principal")
    args = parser.parse_args()
    profile = resolve(args.profile).serve()
    principal = profile.asking(args.principal)
    path = profile.estate() / "golden.json"
    golden, metadata = sets.items(path), sets.catalogue(path)
    engine = None
    if args.api:
        request = urllib.request.Request(args.api.rstrip("/") + "/catalogue",
                  headers={"authorization": "Bearer " + token_for(profile.run_dir(), principal)})
        with urllib.request.urlopen(request, timeout=290) as response:
            products = json.load(response)["products"]
        def call(route, item):
            who = asked_as(item, principal)
            if "@" not in who and "." in principal:
                who = f"{who}.{principal.split('.', 1)[1]}"
            request = urllib.request.Request(args.api.rstrip("/") + route,
                       data=json.dumps({"question": item["question"], **({"products": item["products"]} if "products" in item else {})}).encode(),
                       headers={"content-type": "application/json", "authorization": "Bearer " + token_for(profile.run_dir(), who)})
            with urllib.request.urlopen(request, timeout=290) as response:
                return json.load(response)
    else:
        engine = governed.Engine.serving(profile)
        combined = governed.Combined(engine)
        cfg = combined.default_config("graph")
        products = [p.model_dump() for p in catalogue.get(profile, sorted(engine.g.readable_units(principal))).products]
        def call(route, item):
            function = assist if route == "/assist" else governed.ask
            return function(combined, profile, item["question"], asked_as(item, principal), "analysis", "graph", cfg,
                            products=item.get("products"))
    try:
        report = evaluate_suite(golden, metadata, lambda item: call("/ask", item), lambda item: call("/assist", item), products=products)
        generated = page_scope.questions(profile.artifacts / profile.client / profile.model_hash())
        report["page_scope"] = page_scope.evaluate(generated, lambda item: call("/ask", item), lambda item: call("/assist", item),
                                                  workers=API_IN_FLIGHT if args.api else None)
        report["passed"] = report["passed"] and report["page_scope"]["passed"]
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    finally:
        if engine is not None:
            engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
