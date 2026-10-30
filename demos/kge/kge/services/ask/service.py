"""The ask service's logic, one function a capability, whatever the transport (the HTTP routes, the MCP tools): an answer
(ask, answer), each with its readable text; a term's map; and the unmatched questions the improvement job reads."""
from __future__ import annotations

import json
from pathlib import Path

from kge.adapters.model import request_budget
from kge.pipeline.profile import Profile
from kge.pipeline.settings import ROOT
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import Engine, RunConfig
from kge.services.ask.engine.wording import worded

_Q_PREFIXES = ("what", "was", "were", "is", "are", "the", "show", "give", "me", "tell", "how", "much", "please")
MAX_UNMATCHED_CHARS = 500


def _norm_question(q: str) -> str:
    words = [w.strip("?.!,;:\"'") for w in str(q).strip().lower().split()]
    words = [w for w in words if w]
    while words and words[0] in _Q_PREFIXES:
        words.pop(0)
    return " ".join(words)


def _is_heldout_question(question: str, profile: Profile) -> bool:
    norm = _norm_question(question)
    if not norm:
        return False
    candidates: list[Path] = []
    estate_dir = profile.estate()
    if estate_dir.is_dir():
        candidates.extend(sorted(estate_dir.glob("heldout*.json")))
        if estate_dir.parent.is_dir():
            candidates.extend(sorted(estate_dir.parent.glob("heldout*.json")))
    client_dir = profile.clients / profile.client
    if client_dir.is_dir():
        candidates.extend(sorted(client_dir.glob("**/heldout*.json")))
    default_heldout = ROOT / "kge" / "eval" / "heldout.json"
    if default_heldout.is_file():
        candidates.append(default_heldout)
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        items = (data.get("items") or []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for item in items:
            if isinstance(item, dict) and _norm_question(str(item.get("question") or "")) == norm:
                return True
    return False


@request_budget
def ask(c: Combined, profile: Profile, question: str, who: str, purpose: str, arm: str, cfg: RunConfig) -> dict:
    """POST /ask: the graph arm answered whole (Combined.ask) as who, on cfg, with its run trace and its answer_text.
    A combined answer's text is its own (the template, or a model's that passed the checks); any other is the template's.
    On the cloud path a model step that fails stops it (NoFallback, a 503), never a silent template."""
    out = runtrace.run("ask", lambda: c.ask(question, who, purpose, config=cfg), graph=c.g)
    log_unmatched(profile, "/ask", arm, cfg.graph, who, question, out, c.e)
    return worded(out, c.e)


def answer(e: Engine, profile: Profile, question: str, who: str, purpose: str, arm: str, cfg: RunConfig) -> dict:
    """POST /answer: the rule-based answer (Engine.answer) as who, on cfg, with its run trace and the template's
    answer_text."""
    out = runtrace.run("answer", lambda: e.answer(question, who, purpose, config=cfg), graph=e.g)
    log_unmatched(profile, "/answer", arm, cfg.graph, who, question, out, e)
    return worded(out, e)


def log_unmatched(profile: Profile, path: str, arm: str, graph_on: bool, who: str, question: str, out: dict, eng: Engine) -> None:
    """A graph-on abstention the glossary or the context could not place, logged to the run state's unmatched.jsonl for the
    improvement job: never a question that names private data, and never a held-out one."""
    if not graph_on or out.get("behaviour") != "abstain":
        return
    reason = str(out.get("reason") or "")
    if not reason.startswith("No glossary term recognised"):
        from kge.services.ask.engine.core import parse
        req = parse(question, eng.term_names, eng)
        if req.term is not None or req.definitional or req.special or reason != "The context names no column for this question.":
            return
    from kge.pipeline.privacy import DETECTORS
    if any(rx.search(question) for rx, _ in DETECTORS):
        return
    if _is_heldout_question(question, profile):
        return
    from datetime import datetime, timezone
    runtrace.append_jsonl(profile.run_dir() / "unmatched.jsonl", {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "path": path,
        "arm": arm,
        "principal": who,
        "question": question[:MAX_UNMATCHED_CHARS],
        "reason": reason,
    })


def term_map(e: Engine, term: str, who: str) -> dict:
    """A term's map, as who may read it: its certified columns, the documents that define it, its contexts, the no-join
    facts and certified joins among their units, and the industry standards it is mapped to."""
    g = e.g
    impls = g.implementations(term)
    readable = g.readable_units(who)
    currencies = {r["id"]: r["currency"] for r in g.q("MATCH (m:Measure) RETURN m.id AS id, m.currency AS currency")}
    contexts = [{**context_map(c, e.UNIT_ORDER), "currency": currencies.get(c.measure) or ""}
                for c in e.semantics.contexts_of(term) if c.unit in readable]
    for context in contexts:
        context["joins"] = [j for j in context["joins"]
                            if all(j[key].split(".", 1)[0] in readable for key in ("from", "to"))]
        context["units"] = [unit for unit in context["units"] if unit in readable]
        for key in ("filters", "row_selectors"):
            context[key] = [item for item in context[key] if item["table"].split(".", 1)[0] in readable]
    units = sorted({i["unit"] for i in impls} | {u for c in contexts for u in c["units"]}, key=e.UNIT_ORDER.index)
    pairs = [(units[a], units[b]) for a in range(len(units)) for b in range(a + 1, len(units))]
    facts = [{"pair": f"{a}-{b}", **f} for a, b in pairs if (f := g.no_join(a, b))]
    certified = [{"pair": f"{a}-{b}", **c} for a, b in pairs if (c := g.certified_join(a, b))]
    return {"term": term, "principal": who, "implementations": [{**i, "readable": i["unit"] in readable} for i in impls],
            "defined_by": g.defining_docs(term), "contexts": contexts, "no_join": facts, "certified_joins": certified,
            "uncertified_mentions": g.uncertified_mentions(term), "standards": industry_standards(g, term)}


def industry_standards(g, term: str) -> list[dict]:
    """The term's certified mappings to the preloaded industry standards, each with its standard's name from the
    platform's preload (standards/preload.yaml), which the image ships beside the code."""
    from kge.compiler.standards import load
    names = {s["id"]: s["name"] for s in load().standards.values()}
    return [{**s, "standard_name": names.get(s["standard"], s["standard"])} for s in g.standards(term)]


def context_map(c, order: list[str]) -> dict:
    """A context for the map: its measure and fact, its filters and row selectors by label, its declared joins (required:
    always made; the rest only when a question reaches a dimension, an entity or another unit through them), and the
    units its fact and joins are in."""
    units = sorted({t.split(".", 1)[0] for j in c.joins for t in (j.source, j.target)} & set(order) | {c.unit}, key=order.index)
    return {"id": c.id, "label": c.label, "description": c.description, "measure": c.measure, "expression": c.expression,
            "fact": c.fact, "unit": c.unit,
            "filters": [{"id": f.id, "label": f.label, "table": f.table} for f in c.filters],
            "row_selectors": [{"id": s.id, "label": s.label, "table": s.table, "mode": s.mode} for s in c.selectors],
            "joins": [{"id": j.id, "kind": j.kind, "from": j.source, "to": j.target, "required": j.required} for j in c.joins],
            "units": units}
