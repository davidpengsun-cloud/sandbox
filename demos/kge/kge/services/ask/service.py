"""The ask service's logic, one function a capability, whatever the transport (the HTTP routes, the MCP tools): an answer
(ask, answer), each with its readable text; a term's map; and the unmatched questions the improvement job reads."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import queue
import threading
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded
from kge.adapters.feedbacklog import feedback_log
from kge.adapters.model import QuotaExceeded, QuotaUnavailable, request_budget
from kge.adapters.shadowlog import shadow_log
from kge.pipeline.trace import as_current, carried, failed, new_span, traceparent
from kge.pipeline.profile import Profile
from kge.pipeline.questionrun import RunPending, current_run_id
from kge.pipeline.trace import (QueryCancelled, cancellable, check_cancelled, graph_stream, stream_event, streaming,
                                trigger_cancel)
from kge.pipeline.settings import ROOT
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.openitems import report, resolved
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.contextpath import build_graph_path
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, Engine, RunConfig
from kge.services.ask.engine.path import PathStreamRecorder, build_path
from kge.services.ask.engine.planpublic import RECEIPT_PLAN, capture_shadow, client_result, collapsed_values
from kge.services.ask.engine.present import present
from kge.services.ask.engine.wording import DECLINES, labelled, worded
from kge.services.ask.models import MAX_RECEIPT_CHARS, AskResponse, QuestionRunResponse
from kge.services.ask.receipt import Receipts
from kge.services.identity import session_key

_Q_PREFIXES = ("what", "was", "were", "is", "are", "the", "show", "give", "me", "tell", "how", "much", "please")


def _record_shadow(profile, out, observations):
    """Append after the answer completes, with one id per durable observation across terminal checkpoint retries."""
    if not observations:
        return
    try:
        log = shadow_log(profile.run_dir(), profile.client)
        run_id = current_run_id()
        for index, observation in enumerate(observations):
            if run_id is not None:
                digest = hashlib.sha256(f"ask-shadow:{run_id}:{index}".encode()).hexdigest()
                observation = replace(observation, id=digest[:32])
            log.append(observation)
            runtrace.shadow_observation(out.setdefault('run_trace', {}), observation)
    except (QueryCancelled, RequestDeadlineExceeded):
        raise
    except Exception:
        logging.getLogger(__name__).warning('shadow observation could not be recorded')
MAX_UNMATCHED_CHARS = 500
COMPACT_DROPPED = frozenset(("sql", "reference_sql", "sql_run", "run_trace", "trace", "plan", "plan_vocabulary", "named",
                             "guard", "vector_hits", "candidates", "candidate_sources", "queue", "display", "join_path", "path",
                             "difference_ids"))
CONTEXT_LABELS = frozenset(("id", "context", "label", "term", "time_mode"))
COMPACT_RECORDS = frozenset(("decided", "figures", "citations", "sources", "graph_path", "nodes", "edges"))
COMPACT_SQL = frozenset(("figures",))
SQL_MODES = ("certified", "ai_generated", "not_sure")
STREAM_DROPPED = frozenset(("plan", "stage"))
STREAM_GRAPH = {"node_added": "node", "state_changed": "node", "edge_added": "edge", "edge_changed": "edge"}
CONTEXT_SECTIONS = ("Not glossary terms:", "One customer group:", "Across units:", "Across units, through the certified join:",
                    "Columns by role (", "Contexts (", "Contexts:", "Declared joins, the only ones", "Do not use:",
                    "Sources, by number", "Provenance: each certified column")


def compact_projection(value, answered: bool = False):
    """value as a compact response holds it: no plan, trace or diagnostics, records projected the same way, and a
    context by its labels alone. SQL is the asker's (the role matrix's row 7), the answered query alone: answered, the
    answer's own sql and each of its figures' (COMPACT_SQL) are kept; a decline's, and SQL anywhere else, never."""
    if isinstance(value, list):
        return [compact_projection(item, answered) for item in value]
    if not isinstance(value, dict):
        return value
    dropped = COMPACT_DROPPED - {"sql"} if answered else COMPACT_DROPPED
    return {key: ({k: v for k, v in item.items() if k in CONTEXT_LABELS} if key == "context" and isinstance(item, dict)
                  else compact_projection(item, answered and key in COMPACT_SQL) if key in COMPACT_RECORDS else item)
            for key, item in value.items() if key not in dropped
            and not (key == "parts" and isinstance(item, list))}


def with_sql_certified(out: dict) -> dict:
    """The answer with sql_certified where it carries SQL (its own, or a figure's) and an answer_mode (SQL_MODES): true
    for certified, false for ai_generated or a not_sure that keeps its query, which the card marks "SQL: not certified
    (AI-Generated)". A view with no SQL, or with no answer_mode, has none."""
    figures = out.get("figures") if isinstance(out.get("figures"), list) else []
    carries = out.get("sql") or any(isinstance(f, dict) and f.get("sql") for f in figures)
    if out.get("answer_mode") not in SQL_MODES or not carries:
        return {k: v for k, v in out.items() if k != "sql_certified"} if "sql_certified" in out else out
    certified = out.get("answer_mode") == "certified"
    return out if out.get("sql_certified") is certified else {**out, "sql_certified": certified}


def response_view(out: dict, compact: bool = False) -> dict:
    """A view of the computed response, with no reads, as a client reads it (client_result), with sql_certified where
    it carries SQL (with_sql_certified). Compact, it keeps the answered query's SQL (compact_projection) and drops plan,
    trace and diagnostics. History is the opaque, SQL-free continuation contract: its checked plan and access binding
    must round-trip unchanged for a follow-up turn."""
    out = client_result(out)
    if not compact:
        return with_sql_certified(out)
    compact_out = with_sql_certified(compact_projection(out, out.get("behaviour") not in DECLINES))
    if "block" in out:
        if vocabulary := out.get("plan_vocabulary"):
            definitions = [{key: value for key, value in context.items() if key in ("id", "label", "definition")}
                           for context in vocabulary.get("contexts", [])]
            compact_out["block"] = json.dumps({"contexts": definitions}, ensure_ascii=False, separators=(",", ":"))
        else:
            definitions = []
            for section in out["block"].split("\n\n"):
                if definitions and section.startswith(CONTEXT_SECTIONS):
                    break
                if definitions or section.startswith("Terms:\n"):
                    definitions.append(section)
            compact_out["block"] = "\n\n".join(definitions)
    return compact_out


def ordered(out: dict) -> dict:
    """The answer with order, the ASK_ORDER its planner ran (rules_first or model_first, rules_first where Ask has no
    model), as a field of its own beside answer_mode, so a compact answer, which drops the trace, keeps it too. It is
    the plan step's, as the planner recorded it; an answer with no plan step has none."""
    if "order" in out:
        return out
    step = next((s for s in reversed(out.get("trace") or []) if isinstance(s, dict) and s.get("step") == "plan" and "order" in s), None)
    return {**out, "order": step["order"]} if step else out


class BadFeedback(ValueError):
    """A feedback call whose receipt does not open for the served release, or whose verdict is not one of the two."""


def receipts(engine: Engine, profile: Profile) -> Receipts:
    """The receipts of the release the engine serves, sealed and opened under the key derived from the session key: the
    one place an answer's receipt is made and a feedback call's opened. The release id is the release record's, else the
    model hash for a graph no release recorded."""
    identity = engine.identity
    return Receipts(session_key(profile.run_dir()), identity.get("release") or identity["hash"])


def with_receipt(out: dict, engine: Engine, profile: Profile) -> dict:
    """The answer with its receipt, sealed for the served release: every answer that carries an answer mode and every
    decline (wording.DECLINES: a clarification, a refusal or an access block, an abstention). A decline made before the
    planner (an access refusal or block, a question with no glossary term, one routed to the steward) is not sure,
    answered by the rules, under an answer id of its own. The receipt holds answer_id, answer_mode, answered_by and the
    answered plan, normalised (None for a decline); the plan travels to here under a private key that never reaches the
    client. Every receipt fits a feedback call (MAX_RECEIPT_CHARS): see sealed_within."""
    plan = out.pop(RECEIPT_PLAN, None)
    if out.get("behaviour") in DECLINES and not out.get("answer_mode"):
        out = {**out, "answer_mode": "not_sure", "answered_by": "rules", "answer_id": out.get("answer_id") or uuid.uuid4().hex}
    if not all(out.get(field) for field in ("answer_id", "answer_mode", "answered_by")):
        return out
    sealed = sealed_within(receipts(engine, profile), {"answer_id": out["answer_id"], "answer_mode": out["answer_mode"],
                                                       "answered_by": out["answered_by"], "plan": plan})
    return {**out, "receipt": sealed}


def sealed_within(sealer: Receipts, answer: dict) -> str:
    """The answer's receipt, no longer than a feedback call takes (MAX_RECEIPT_CHARS), so every receipt issued can be
    posted unchanged: with its whole normalised plan where that fits; else with each IN filter's values given by kind
    and count alone (collapsed_values), since a long list of values is what grows a plan; else, a plan too large even
    then, with no plan, its answer id, mode and who answered still kept."""
    plan = answer["plan"]
    for kept in (plan, collapsed_values(plan)):
        sealed = sealer.seal({**answer, "plan": kept})
        if len(sealed) <= MAX_RECEIPT_CHARS:
            return sealed
    return sealer.seal({**answer, "plan": None})


def feedback(engine: Engine, profile: Profile, receipt: str, verdict: str) -> dict:
    """Record a person's verdict on an answer in the feedback log (<run state>/feedback/, or the object store): the
    receipt's own fields, its id, the verdict and the time, never who gave it. BadFeedback for a receipt that does not
    open for this release (changed, of another release, or sealed before the session key rotated) or for an unknown
    verdict. A receipt may be posted again: each post is its own record, carrying the receipt's id, and a reader counts
    the last verdict per receipt id."""
    sealer = receipts(engine, profile)
    try:
        record = sealer.feedback(receipt, verdict)
    except ValueError as exc:
        raise BadFeedback(str(exc)) from None
    feedback_log(profile.run_dir()).append(record)
    return {}


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
def ask(c: Combined, profile: Profile, question: str, who: str, purpose: str, arm: str, cfg: RunConfig, products: list[str] | None = None, history=(), compact: bool = False, runtimes: list[str] | None = None, on_stage=None, display_engine=None) -> dict:
    """POST /ask: the graph arm answered whole (Combined.ask) as who, on cfg, with its run trace and its answer_text.
    A combined answer's text is its own (the template, or a model's that passed the checks); any other is the template's.
    On the cloud path a model step that fails stops it (NoFallback, a 503), never a silent template."""
    from kge.services.ask.engine.modelcall import observe_model_dispatch
    with observe_model_dispatch() as witness, capture_shadow() as observations:
        out = with_receipt(runtrace.run("ask", lambda: c.ask(question, who, purpose, config=cfg, products=products, history=history, runtimes=runtimes), graph=c.g, on_stage=on_stage), c.e, profile)
        if not (out.get("run_trace") or {}).get("llm_calls"):
            _settle_rules_only(c, cfg, witness)
    _record_shadow(profile, out, observations)
    log_unmatched(profile, "/ask", arm, cfg.graph, who, question, out, c.e)
    full = ordered(worded(out, c.e))
    view = response_view(full, compact)
    if display_engine is not None:
        view["display"] = displayed(response_view(full), compact, display_engine)
    return view


def _settle_rules_only(c, cfg, witness=None, *, reason="rules_only"):
    """Settle a concrete pass only with a live, same-thread witness that saw no model dispatch."""
    if type(c) is not Combined or witness is None or not witness.no_dispatch():
        return
    from kge.adapters.modelusage import current_usage, UsageConflict
    if (usage := current_usage()) and usage[2]:
        try:
            usage[0].finish_invocation_no_inference(usage[1], usage[2], reason=reason)
        except UsageConflict:
            pass


COMPACT_DISPLAY = ("v", "headline", "table", "chart", "chart_note", "notes")


def displayed(answer: dict, compact: bool, engine) -> dict:
    """The display block of an answer, its full view (the block reads the plan), built on the engine engine() gives.
    A compact caller's block (#602) keeps COMPACT_DISPLAY alone: the card's headline, table, chart and notes. Its
    queries and checked carry each query's SQL and the gate's verdicts, the engineer's row of the matrix, as compact
    drops the plan and the gate's verdicts from the answer (the answered query's SQL it keeps), and a block present()
    could not build loses its exception text. The answer is the same either way."""
    block = present(answer, engine())
    return {k: v for k, v in block.items() if k in COMPACT_DISPLAY} if compact else block


def answer(e: Engine, profile: Profile, question: str, who: str, purpose: str, arm: str, cfg: RunConfig, products: list[str] | None = None, history=(), runtimes: list[str] | None = None, on_stage=None) -> dict:
    """POST /answer: the rule-based answer (Engine.answer) as who, on cfg, with its run trace, the template's
    answer_text and sql_certified where it carries SQL (with_sql_certified)."""
    with capture_shadow() as observations:
        out = with_receipt(runtrace.run("answer", lambda: e.answer(question, who, purpose, config=cfg, products=products, history=history, runtimes=runtimes), graph=e.g, on_stage=on_stage), e, profile)
    _record_shadow(profile, out, observations)
    log_unmatched(profile, "/answer", arm, cfg.graph, who, question, out, e)
    return with_sql_certified(client_result(ordered(worded(out, e))))


def run_config(a, path: str, combined) -> RunConfig | dict:
    """The config a request runs on: its arm's default for this path, with its stages override; combined gives the served
    Combined, asked for only once the arm is known. A problems body ({"error", "problems"}, and the config where there is
    one) if it cannot run here."""
    if a.arm not in PRESETS:
        bad = [f"arm must be one of {', '.join(PRESETS)}"]
        return {"error": bad[0], "problems": bad}
    base = combined().default_config(a.arm) if path == "ask" else PRESETS[a.arm]
    try:
        cfg = base.with_stages(**(a.stages.model_dump(exclude_unset=True) if a.stages else {}))
    except ValueError as exc:
        return {"error": str(exc), "problems": [str(exc)]}
    bad = cfg.problems(path, combined().llm is not None)
    if bad:
        return {"error": "; ".join(bad), "problems": bad, "config": cfg.stages()}
    return cfg


def agent_arm_problems(arm: str) -> dict | None:
    """The problems body for an arm the reference agent does not run on; None for one it does."""
    if arm in AGENT_ARMS:
        return None
    bad = f"arm must be one of {', '.join(AGENT_ARMS)}, not {arm!r}"
    return {"error": bad, "problems": [bad]}


def agent(ag, profile: Profile, a, who: str, on_stage=None, compact: bool = False, tools=None) -> dict:
    """POST /agent: the reference agent's answer on a's arm as who, with its run trace, its path and its graph path, its
    text labelled Certified or AI-Generated per its answer_mode as /ask's is, and a decline's with neither. Compact, it
    holds no plan, trace or path, and SQL only the answered query's (response_view), as /ask's compact answer does.
    tools: the KGE tools this request reaches KGE through (the KgeTools port), carrying who's own bearer, given to this
    turn alone and never to the shared agent; None, the agent's in-process default. Streamed, each pipeline event the
    tools relay from the ask call reaches this request's stream as it comes (relayed)."""
    def asked() -> dict:
        return ag.ask(a.question, who, a.purpose, arm=a.arm, products=a.products, history=a.history, runtimes=a.runtimes,
                      tools=tools, on_event=relayed())
    with capture_shadow() as observations:
        out = runtrace.run("agent", asked, graph=ag.g, on_stage=on_stage)
    _record_shadow(profile, out, observations)
    log_unmatched(profile, "/agent", a.arm, a.arm == "treatment", who, a.question, out, ag.e)
    out = ordered(labelled(out))
    out["path"] = build_path(ag.e, out)
    out["graph_path"] = build_graph_path(ag.e, out)
    return response_view(out, compact)


def relayed():
    """This request's stream, as a callable another thread may publish to while this one waits on it (the MCP ask tool's
    pipeline events, relayed by the tools as notifications arrive): each event published in the stream's own context, so
    the stream's recorder sends it once, in order, compact for a compact request, and a cancelled request stops it. None
    when the request does not stream."""
    if not streaming():
        return None
    held = contextvars.copy_context()
    return lambda event: held.run(stream_event, event)


def context(ag, a, who: str, combined=None):
    """POST /context: the context package the reference agent's prompt carries on a's arm, built as who: with a's
    products, history and plan vocabulary where a asks for the plan vocabulary, else from the question alone. With
    open_items, also the question's bindings and open items as ask's rules read it, with a's history, products, purpose
    and runtimes, its declines from ask run dry on combined, the served Combined (kge/services/ask/engine/openitems.py):
    facts only, no model call and no warehouse query; graph-store and retrieval reads as any context call makes."""
    if a.plan_vocabulary:
        pkg = ag.context(a.question, who, a.purpose, a.arm, products=a.products, plan_vocabulary=True, history=a.history, runtimes=a.runtimes)
    else:
        pkg = ag.context(a.question, who, a.purpose, a.arm, runtimes=a.runtimes)
    if not a.open_items:
        return pkg
    return {**pkg, **report(ag.e, a.question, who, a.purpose, a.history, a.products, a.runtimes, combined)}


def retrieve(e: Engine, r, who: str) -> dict:
    """POST /retrieve: vector search over the chunks who may read for r's purpose, at r's shelf and tiers. A bad shelf or
    tier is a ValueError."""
    if not r.include_passages:
        return e.g.retrieve(r.query, who, r.purpose, r.k, r.shelf, r.tiers)
    return citable_passages(e, r, who)


def citable_passages(e: Engine, r, who: str) -> dict:
    """Search caller-scoped passages and return bounded text only for citable documents in force for the question."""
    from kge.services.ask.engine.question import asked_window, in_force, parse

    from kge.services.ask.engine.graphview import TIERS

    if r.tiers is not None and (not r.tiers or any(tier not in TIERS for tier in r.tiers)):
        raise ValueError(f"tiers must name one or more of {', '.join(TIERS)}, not {r.tiers!r}")
    tiers = [tier for tier in (r.tiers if r.tiers is not None else ("gold", "silver")) if tier in ("gold", "silver")]
    if not tiers:
        return {"exact": [], "truncated": False}
    found = e.g.retrieve(r.query, who, r.purpose, r.k, r.shelf, tiers)
    rows = found["exact"]
    cert = e.g.certification([row["doc"] for row in rows])
    window = asked_window(parse(r.query, e.term_names, e), e)
    units = e.g.readable_units(who)
    eligible = [row for row in rows if (c := cert.get(row["doc"])) and c["ai_ready"] and c["tier"] in tiers
                and (not c["unit"] or c["unit"] in units)
                and (in_force(c, None) or (window and in_force(c, window)))]
    omitted = len(eligible) > 20
    eligible = eligible[:20]
    ids = [row["chunk"] for row in eligible if row.get("chunk")]
    text = {(row["doc"], row["id"]): row["text"] for row in e.g.q(
        "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.id IN $ids AND c.readiness <> 'embed-prohibited' "
        "AND $purpose IN c.purposes AND (d.unit = '' OR d.unit IN $units) "
        "RETURN d.id AS doc, c.id AS id, left(c.text, 4001) AS text", {"ids": ids, "purpose": r.purpose, "units": sorted(units)})} if ids else {}
    passages = []
    remaining = 20000
    truncated = omitted
    for row in eligible:
        content = text.get((row["doc"], row.get("chunk")))
        if content is None:
            continue
        if remaining <= 0:
            truncated = True
            continue
        c = cert[row["doc"]]
        size = min(4000, remaining)
        excerpt = content[:size]
        remaining -= len(excerpt)
        truncated = truncated or len(content) > len(excerpt)
        passages.append({**row, "title": c["title"], "tier": c["tier"], "owner": c["owner"], "version": c["version"],
                         "cert_from": c["cert_from"], "cert_to": c["cert_to"], "citable": True,
                         "attribution": f"{c['owner']}, version {c['version']}" if c["tier"] == "silver" else None,
                         "passage": {"text": excerpt, "truncated": len(content) > len(excerpt)}})
    return {"exact": passages, "truncated": truncated}


def list_runtimes(e: Engine, who: str) -> dict:
    """GET /runtimes: the runtimes the caller may use, and which reference tables each holds."""
    readable = e.g.readable_units(who)
    return {"runtimes": e.w.list_runtimes(readable)}


def log_unmatched(profile: Profile, path: str, arm: str, graph_on: bool, who: str, question: str, out: dict, eng: Engine) -> None:
    """A graph-on abstention the glossary or the context could not place, logged to the run state's unmatched.jsonl for the
    improvement job: never a question that names private data, and never a held-out one."""
    if not graph_on or out.get("behaviour") != "abstain":
        return
    reason = str(out.get("reason") or "")
    if not reason.startswith("No glossary term recognised"):
        from kge.services.ask.engine.core import parse
        req = parse(question, eng.term_names, eng)
        unresolved = reason == "The context names no column for this question." or (
            reason.startswith("I cannot apply '")
            and reason.endswith("': no certified measure or declared entity resolves the complete request. No figure is given."))
        if req.term is not None or req.definitional or req.special or not unresolved:
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


def term_map(e: Engine, term: str, who: str, compact: bool = False) -> dict:
    """A term's map, as who may read it: its definition and steward, the data sets that hold it, the documents that
    define it, its contexts and the industry standards it is mapped to; in full, also its certified columns, the
    no-join facts and certified joins among their units and its uncertified mentions. compact, every reply but an
    engineer's (the role matrix's row 7), keeps business names alone: no physical table or column, context expression,
    fact, join condition, no-join fact or certified join, as Ask's compact answer keeps no plan or SQL. The compact form, the data
    sets and the contexts of both forms keep only the units the caller may read. The full form's certified columns
    list every unit's, each marked readable or not; its defining documents and uncertified mentions are not filtered
    by readable unit; and its no-join facts and certified joins are among the units of every certified column and
    readable context, the unreadable ones included. Both forms carry the term's paths (#628), each of its contexts
    explained as explore_question explains it: in business words in the compact form, physical names in the full,
    each in the units the caller may read."""
    g = e.g
    readable = g.readable_units(who)
    members = g.member_terms() if hasattr(g, "member_terms") else None
    coded = []
    if members is not None and term in members.names:
        restricted = {".".join(row) for row in g.domain()["restricted"]}
        coded = [{"unit": unit, "tbl": table} for column in members.columns(term)
                 for unit, table, _ in [column.split(".", 2)] if unit in readable and column not in restricted]
        if not coded:
            empty = {"term": term, "principal": who, "compact": compact, "definition": "", "steward": "",
                     "data_sets": [], "defined_by": [], "contexts": [], "paths": [], "standards": []}
            if not compact:
                empty.update(implementations=[], no_join=[], certified_joins=[], uncertified_mentions=[])
            return empty
    impls = g.implementations(term)
    currencies = {r["id"]: r["currency"] for r in g.q("MATCH (m:Measure) RETURN m.id AS id, m.currency AS currency")}
    held = g.q("MATCH (t:Term) WHERE t.name = $t RETURN t.definition AS definition, t.steward AS steward", {"t": term})
    about = {"definition": (held[0]["definition"] or "") if held else "", "steward": (held[0]["steward"] or "") if held else ""}
    declared = [c for c in e.semantics.contexts_of(term) if c.unit in readable]
    labels = g.domain()["labels"]
    data_sets = term_data_sets(g, [*impls, *coded], declared, readable, labels, currencies)
    from kge.services.graph import term_paths
    paths = [p.model_dump() for p in term_paths(e, who, term, engineer=not compact)]
    resolves = {} if held else alias_resolution(e, term, who)
    if compact:
        return {"term": term, "principal": who, "compact": True, **about, "data_sets": data_sets,
                "defined_by": readable_documents(g, g.defining_docs(term), readable),
                "contexts": [compact_context(c, readable, labels, currencies) for c in declared],
                "paths": paths, "standards": industry_standards(g, term), **resolves}
    contexts = [{**context_map(c, e.UNIT_ORDER), "currency": currencies.get(c.measure) or ""} for c in declared]
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
    return {"term": term, "principal": who, "compact": False, **about, "data_sets": data_sets,
            "implementations": [{**i, "readable": i["unit"] in readable} for i in impls],
            "defined_by": g.defining_docs(term), "contexts": contexts, "paths": paths, "no_join": facts,
            "certified_joins": certified,
            "uncertified_mentions": g.uncertified_mentions(term), "standards": industry_standards(g, term), **resolves}


def alias_resolution(e: Engine, name: str, who: str) -> dict:
    """{resolves_to} for a name that is no term but an alias or a head phrase of the terms who may read (#707
    requirement 2): each term the pack's alias names, and each whose name or alias ends in the name's words; {} for a
    name that resolves to none, so every other map is unchanged."""
    found = resolved(e, name, who)
    return {"resolves_to": found} if found else {}


def term_data_sets(g, impls: list[dict], contexts: list, readable: set, labels: dict, currencies: dict) -> list[dict]:
    """The data sets that hold a term, in the units the caller may read, in business names: each one's unit and its
    label, the catalogue product's domain, community and business owner, its currency, and who certified a certified
    column and when. One entry per data set, its certified column's first, and one per distinct description; never a
    table or column name."""
    products = {p["id"]: p for p in g.catalogue_products().items}
    held = [(f"{i['unit']}.{i['tbl']}", i["unit"], i.get("currency") or "", i.get("certified_by") or "",
             i.get("certified_on") or "") for i in impls if i["unit"] in readable]
    held += [(c.fact, c.unit, currencies.get(c.measure) or "", "", "") for c in contexts]
    out: list[dict] = []
    seen: set[str] = set()
    for product_id, unit, currency, certified_by, certified_on in held:
        if product_id in seen:
            continue
        seen.add(product_id)
        product = products.get(product_id, {})
        entry = {"unit": unit, "unit_label": labels.get(unit, unit), "domain": product.get("domain", ""),
                 "community": product.get("community", ""), "business_owner": product.get("business_owner", ""),
                 "currency": currency, "certified_by": certified_by, "certified_on": str(certified_on)}
        entry = {key: value for key, value in entry.items() if value}
        if entry not in out:
            out.append(entry)
    return out


def compact_context(c, readable: set, labels: dict, currencies: dict) -> dict:
    """A context in the compact map: its label, the measure's business name (its term) and currency, its unit and the
    unit's label, and its filters and row selectors by label where the caller may read the table each is on. No
    expression, fact, join or table, and not its description, which a pack may write in column names."""
    return {"label": c.label, "measure": c.term, "currency": currencies.get(c.measure) or "", "unit": c.unit,
            "unit_label": labels.get(c.unit, c.unit),
            "filters": [f.label for f in c.filters if f.table.split(".", 1)[0] in readable],
            "row_selectors": [s.label for s in c.selectors if s.table.split(".", 1)[0] in readable]}


def readable_documents(g, documents: list[dict], readable: set) -> list[dict]:
    """The defining documents the caller may read: those of no unit, or of a unit the caller may read."""
    units = {doc: row.get("unit") or "" for doc, row in g.certification([d["id"] for d in documents]).items()}
    return [d for d in documents if units.get(d["id"], "") in ("", *readable)]


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


def shown(out: dict, display: bool, engine) -> dict:
    """The rich answer's display block (kge/services/ask/engine/present.py), added only when the page asks for it (the display
    query flag, its Rich answers switch, off by default), built on the engine engine() gives. The A/B harness, eval, margin and parity
    never ask; the answer is the same either way."""
    if display:
        out["display"] = present(out, engine())
    return out


def run_reply(out: dict, engine, *, starting: bool = False) -> tuple[dict, int]:
    """A question run's public body, its answer with its display block where the run asked for one, and its status: 202
    while it runs or as it starts, else 200."""
    if out.pop("_display", False) and out.get("answer"):
        shown(out["answer"], True, engine)
        out["events"] = [{**event, "answer": out["answer"]}
                         if event.get("event") == "answer" else event for event in out["events"]]
    out = QuestionRunResponse.model_validate(out).model_dump(mode="json", exclude_unset=True, by_alias=True)
    out["events"] = [{**event, "answer": AskResponse.model_validate(event["answer"]).model_dump(mode="json", by_alias=True)}
                     if event.get("event") == "answer" else event for event in out["events"]]
    return out, 202 if starting or out["state"] == "running" else 200


def run_events(out: dict) -> list[dict]:
    """A run's body as live events: the run itself at the cursor its events start from, each event at its cursor, then
    its answer and its error where the events do not hold them."""
    first = out["cursor"] - len(out["events"])
    events = [{"event": "run", **{k: v for k, v in out.items() if k not in ("events", "answer")}, "cursor": first},
              *[{**event, "cursor": first + i} for i, event in enumerate(out["events"], 1)]]
    if out.get("answer") and not any(e.get("event") == "answer" for e in out["events"]):
        events.append({"event": "answer", "answer": out["answer"]})
    if out.get("error"):
        events.append({"event": "error", "error": out["error"], **({"reason": out["reason"]} if out.get("reason") else {})})
    return events


def public_event(event: dict, compact) -> dict | None:
    """A stream's event as the request sees it: whole, or for a compact request none of the plan's or the stages'
    events, each graph event's node or link as compact graph_path holds it, and a context by its labels alone. None for
    an event a compact request does not see."""
    if compact:
        if event.get("event") in STREAM_DROPPED:
            return None
        if (part := STREAM_GRAPH.get(event.get("event"))) and isinstance(event.get(part), dict):
            event = {**event, part: compact_projection(event[part])}
        if event.get("event") == "context":
            event = {**event, "context": response_view({"context": event["context"]}, True)["context"]}
    return event


@contextmanager
def recording(question: str, emit, sent=(), parts: list | None = None):
    """The stream recorder a direct stream and a background run share: started on what the run sent before (sent) and
    its earlier passes' traces (parts), then fed the graph's and the warehouse's events while the body runs, each sent
    through emit as the recorder's rules say. A pass that waits for the warehouse pauses it; its caller finishes it with
    the answer (emit_final)."""
    recorder = PathStreamRecorder(question, emit, sent, parts)
    recorder.emit_initial()
    with graph_stream(recorder.publish):
        try:
            yield recorder
        except RunPending:
            recorder.pause()
            raise


def stream(question: str, compute, is_disconnected, *, compact: bool = False, refused=None):
    """A direct stream's NDJSON lines: compute(on_stage) runs at once on a worker thread, in a cancellation scope and the
    shared recorder, its events queued as they come; the answer last, unless it was stopped. An error is one error
    event: refused(exc) gives the transport's refusal as one, a call to an outside service is its own line, any other is
    logged and unavailable. When the reader goes before the work ends, the work is cancelled, once."""
    q: queue.Queue = queue.Queue()
    done_marker = object()
    cancel_ev = threading.Event()
    cancel_hooks: list = []
    finished = False

    def emit(ev: dict) -> None:
        check_cancelled()
        if (ev := public_event(ev, compact)) is not None:
            q.put(json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    def worker() -> None:
        nonlocal cancel_hooks, finished
        try:
            with cancellable(cancel_ev) as (_ev, hooks):
                cancel_hooks = hooks
                with recording(question, emit) as recorder:
                    out = compute(recorder.on_stage)
                if not cancel_ev.is_set():
                    recorder.emit_final(out)
                finished = True
        except QueryCancelled:
            pass
        except (QuotaExceeded, QuotaUnavailable) as exc:
            if not cancel_ev.is_set():
                emit({"event": "error", "error": str(exc), "status": 429 if isinstance(exc, QuotaExceeded) else 503,
                      **({"retry_after": exc.retry_after} if isinstance(exc, QuotaExceeded) else {})})
        except ExternalCallError as exc:
            if not cancel_ev.is_set():
                emit({"event": "error", "error": str(exc)})
        except Exception as exc:
            event = refused(exc) if refused is not None else None
            if event is None:
                logging.getLogger(__name__).exception("Unhandled error in streamed Ask request")
                event = {"event": "error", "error": "This action is unavailable."}
            if not cancel_ev.is_set():
                emit(event)
        finally:
            q.put(done_marker)

    threading.Thread(target=carried(worker), daemon=True, name="ask-stream").start()

    async def body_iter():
        try:
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    if await is_disconnected():
                        break
                    await asyncio.sleep(0.01)
                    continue
                if item is done_marker:
                    break
                yield item.encode("utf-8")
        finally:
            if not finished:
                await asyncio.to_thread(trigger_cancel, cancel_ev, cancel_hooks)

    return body_iter()


STATE_UNAVAILABLE = "Question state is temporarily unavailable. Please retry."


def run_failure(exc: BaseException) -> dict:
    """The error event for work on a question run that failed once its reply had begun, worded as its JSON reply
    would be: the question state's 503 for a state or store error, an outside call's own line, else logged and
    unavailable."""
    from kge.ports import StateConflict, StateUnavailable, StoreError
    if isinstance(exc, (QuotaExceeded, QuotaUnavailable)):
        return {"event": "error", "error": str(exc), "status": 429 if isinstance(exc, QuotaExceeded) else 503,
                **({"retry_after": exc.retry_after} if isinstance(exc, QuotaExceeded) else {})}
    if isinstance(exc, (StateConflict, StateUnavailable, StoreError)):
        logging.getLogger(__name__).warning("Question state unavailable while a live question run streamed", exc_info=exc)
        return {"event": "error", "error": STATE_UNAVAILABLE, "status": 503}
    if isinstance(exc, ExternalCallError):
        logging.getLogger(__name__).warning("Outside call failed while a live question run streamed", exc_info=exc)
        return {"event": "error", "error": str(exc)}
    logging.getLogger(__name__).error("Unhandled error in a live question run", exc_info=exc)
    return {"event": "error", "error": "This action is unavailable."}


def live_run(work, engine, is_disconnected, *, after: int = 0, starting: bool = False):
    """A question run's NDJSON reply, live: work(on_start, on_event, stop) starts or advances the run on a worker thread.
    Once it has the run, the reply starts at once with the run (its id, state and cursor) and the events after `after`
    it already holds, then each event as the run records it, at its cursor, and ends with its answer (shown as the run
    asked) or its error, or when the work stops waiting for the warehouse. Returns (body, None, finished) where the work
    ended without advancing the run, and (None, lines, finished) where it streams; finished is set once the work has
    ended, which the transport waits for before it lets the request go, so the work keeps the request's engine and
    slot for as long as it runs. A reader that leaves ends the wait, never the job."""
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    finished = threading.Event()

    def on_start(record):
        view = {**run_view(record), "events": list(record["events"][after:]), "cursor": len(record["events"])}
        q.put(("start", view))

    def on_event(cursor, event):
        q.put(("event", cursor, event))

    def worker():
        try:
            q.put(("done", work(on_start, on_event, stop)))
        except BaseException as exc:
            q.put(("failed", exc))
        finally:
            finished.set()

    threading.Thread(target=carried(worker), daemon=True, name="ask-run").start()
    first = q.get()
    if first[0] == "done":
        return first[1], None, finished
    if first[0] == "failed":
        raise first[1]
    begun, _ = run_reply(first[1], engine, starting=starting)
    opening = run_events(begun)

    def line(event):
        return (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")

    async def body_iter():
        last = begun["cursor"]
        try:
            for event in opening:
                yield line(event)
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    if await is_disconnected():
                        break
                    await asyncio.sleep(0.01)
                    continue
                if item[0] == "event":
                    _, cursor, event = item
                    if event.get("event") != "answer":
                        last = cursor
                        yield line({**event, "cursor": cursor})
                    continue
                if item[0] == "done":
                    out = {**item[1], "events": item[1]["events"][after:]}
                    ended, _ = run_reply(out, engine)
                    for event in run_events(ended)[1:]:
                        if event.get("event") == "answer" or event.get("cursor", last + 1) > last:
                            yield line(event)
                else:
                    yield line(run_failure(item[1]))
                break
        finally:
            stop.set()

    return None, body_iter(), finished


def ndjson(events):
    """Each event as one line of JSON."""
    return iter(json.dumps(event, ensure_ascii=False, default=str) + "\n" for event in events)


def run_view(record):
    """Only the signed-in person's public events and answer, never handles or checkpoints."""
    out = {key: record[key] for key in ("id", "state", "expires_at", "poll_after", "events")}
    out["cursor"] = len(record["events"])
    if "answer" in record:
        out["answer"] = record["answer"]
    if "error" in record:
        out["error"] = record["error"]
    if "reason" in record:
        out["reason"] = record["reason"]
    if record.get("display") and not record.get("request", {}).get("compact"):
        out["_display"] = True
    return out


@request_budget
def start_run(c, profile, request, who, cfg, *, state=None, clock=None, presentation=False, wait=0.0, on_start=None,
              on_event=None, stop=None, card=False):
    """Start governed work and acknowledge its id as soon as the warehouse owns the wait. on_start(record) is told the
    run as soon as it is saved, on_event(cursor, event) each event as the run records it, and a set stop ends the wait
    for the warehouse early. presentation: an engineer's answer is shown with its display block (run_reply); card: a
    compact answer keeps its card's part of the block, as the ask tool's display gives it (displayed), built as the run
    answers, while the plan it reads is at hand. Pending runs split the first configured poll interval into two checks."""
    import time
    import uuid
    from dataclasses import asdict
    from kge.adapters.questionstate import for_profile
    from kge.pipeline.questionrun import timeout, interval
    from kge.adapters.deadline import request_timeout, request_window
    state = state or for_profile(profile)
    now = (clock or time.time)()
    poll_interval = interval()
    first_poll = poll_interval / 2 or poll_interval
    record = {"id": uuid.uuid4().hex, "principal": who, "request": request.model_dump(mode="json"),
              "config": asdict(cfg), "display": presentation, "identity": c.e.identity, "readable": sorted(c.g.readable_units(who)),
              "state": "running", "created_at": now, "expires_at": now + timeout(), "poll_after": first_poll,
              "next_poll_delays": [first_poll, poll_interval],
              "events": [], "jobs": [], "plan": {}, "completed_at": None,
              "next_poll": now, "lease": now + request_timeout() + 10, **({"card": True} if card else {})}
    from kge.adapters.modelusage import current_usage, current_usage_conversation
    from kge.pipeline.trace import telemetry_current
    if telemetry := telemetry_current():
        record["telemetry_scope"] = {"client": telemetry["kge.client"], "agent": telemetry["kge.agent"], "session": telemetry["gen_ai.conversation.id"]}
    if usage := current_usage():
        conversation = current_usage_conversation()
        record["usage_binding"] = usage[0].scope_binding(usage[1], conversation)
        kind, app = usage[0].run_identity()
        record["usage_scope"] = {"kind": kind, "app": app, "question": usage[1], "conversation": conversation}
    span = new_span("kge.question_run", {"kge.question_run": record["id"], "kge.run_id": record["id"]})
    try:
        if parent := traceparent(span):
            record["trace"] = parent
        with request_window(record["expires_at"] - (clock or time.time)()):
            revision = state.put(record["id"], record, None)
        if on_start is not None:
            on_start(record)
        return _advance_run(c, profile, record, state, revision, clock or time.time, wait, on_event=on_event, stop=stop)
    finally:
        span.end()


@request_budget
def get_run(c, profile, run_id, who, *, state=None, clock=None, on_start=None, on_event=None, stop=None, loaded=None):
    """Any instance can poll; ownership, the current grant and release still govern the answer. A poll that advances
    the run tells on_start(record) first, then on_event(cursor, event) each event as the run records it."""
    import time
    from kge.adapters.questionstate import for_profile
    from kge.adapters.deadline import request_timeout, request_window
    from kge.ports import StateConflict
    state = state or for_profile(profile)
    clock = clock or time.time
    from kge.adapters.modelusage import current_usage, current_usage_conversation
    usage = current_usage()
    def idle():
        if usage and usage[2]:
            usage[0].finish_invocation_no_inference(usage[1], usage[2], reason="idle_poll")
    record, revision = loaded if loaded is not None else state.get(run_id)
    if record is None or record.get("id") != run_id or record["principal"] != who:
        idle()
        return None
    if record.get("usage_binding") != (usage[0].scope_binding(usage[1], current_usage_conversation()) if usage else None):
        idle()
        return None
    if c.e.identity != record["identity"] or not set(record["readable"]) <= c.g.readable_units(who):
        idle()
        return {**{k: v for k, v in run_view(record).items() if k != "reason"}, "events": [], "answer": None, "state": "failed",
                "error": "Access or the served release changed; ask the question again.", "reason": "access_changed"}
    if record["state"] != "running":
        idle()
        return run_view(record)
    now = clock()
    if now < record["expires_at"] and (now < record.get("lease", 0) or now < record["next_poll"]):
        idle()
        return run_view(record)
    if now >= record["expires_at"]:
        return _advance_run(c, profile, record, state, revision, clock)
    if delays := record.get("next_poll_delays"):
        record["poll_after"] = delays.pop(0)
    record["lease"] = now + request_timeout() + 10
    try:
        with request_window(record["expires_at"] - clock()):
            revision = state.put(run_id, record, revision)
    except StateConflict:
        with request_window(record["expires_at"] - clock()):
            latest, _ = state.get(run_id)
        idle()
        return run_view(latest)
    if on_start is not None:
        on_start(record)
    return _advance_run(c, profile, record, state, revision, clock, on_event=on_event, stop=stop)


@request_budget
def stop_run(c, profile, run_id, who, *, state=None, clock=None):
    """DELETE /ask/runs/{run_id} and the stop_question_run tool: the signed-in person's running question stopped. Its
    unfinished warehouse jobs are cancelled, as an expired run's are, and the run ends failed, its reason stopped, on
    the revision it was read at; a run another instance changed meanwhile is read again and stopped once more, and a
    second change is the store's conflict. A run that has already ended is given as it is, and a stopped run is never
    resumed. None for an unknown run or another person's."""
    import time
    from kge.adapters.questionstate import for_profile
    from kge.pipeline import questionrun
    from kge.ports import StateConflict
    state = state or for_profile(profile)
    clock = clock or time.time
    for attempt in range(2):
        record, revision = state.get(run_id)
        if record is None or record["principal"] != who:
            return None
        from kge.adapters.modelusage import current_usage, current_usage_conversation
        usage = current_usage()
        if record.get("usage_binding") != (usage[0].scope_binding(usage[1], current_usage_conversation()) if usage else None):
            return None
        if record["state"] != "running":
            return run_view(record)
        _cancel_unfinished(c, record)
        record.pop("answer", None)
        record["events"] = [event for event in record["events"] if event.get("event") != "answer"]
        record.update(state="failed", error=questionrun.STOPPED_TEXT, reason=questionrun.STOPPED, lease=0,
                      completed_at=clock())
        try:
            state.put(run_id, record, revision)
        except StateConflict:
            if attempt:
                raise
            continue
        return run_view(record)


def background_reply(out: dict, engine) -> dict:
    """The ask tool's reply to a background question (start_run's out): its answer, as the same call without background
    gives it, where the run answered within the start wait; else {"run": the run as run_reply gives it}."""
    if out["state"] == "done" and out.get("answer"):
        answer = out["answer"]
        return shown(answer, True, engine) if out.get("_display") else answer
    body, _ = run_reply(out, engine, starting=True)
    return {"run": body}


def _advance_run(c, profile, record, state, revision, clock, wait=0.0, on_event=None, stop=None):
    """Resume the owner's question trace using only its stored, validated opaque telemetry context."""
    from kge.pipeline.trace import telemetry_scope
    from kge.pipeline.trace_context import AGENTS, SESSION
    saved = record.get("telemetry_scope")
    valid = (isinstance(saved, dict) and set(saved) == {"client", "agent", "session"} and saved.get("client") == profile.client
             and isinstance(saved.get("agent"), str) and saved["agent"] in AGENTS and isinstance(saved.get("session"), str)
             and bool(SESSION.fullmatch(saved["session"])))
    scope = telemetry_scope(saved["client"], saved["agent"], saved["session"]) if valid else telemetry_scope()
    with scope:
        span = new_span("kge.question_run.pass", {"kge.question_run": record["id"], "kge.run_id": record["id"]},
                        parent=record.get("trace"), link=True)
        try:
            from kge.services.ask.engine.modelcall import observe_model_dispatch
            with observe_model_dispatch() as witness:
                with as_current(span):
                    out = _advance_pass(c, profile, record, state, revision, clock, wait, on_event, stop)
                if out.get("state") in ("running", "done") and record.get("config") is not None:
                    _settle_rules_only(c, RunConfig(**record["config"]), witness,
                                       reason="idle_poll" if out["state"] == "running" else "rules_only")
            if out.get("state") is not None:
                span.set_attribute("kge.outcome", out.get("state"))
            return out
        except BaseException as exc:
            failed(span, type(exc).__name__)
            raise
        finally:
            span.end()


def _advance_pass(c, profile, record, state, revision, clock, wait, on_event, stop):
    import time
    from kge.adapters.deadline import MIN_CALL_SECONDS, ExternalCallError, request_remaining, request_window
    from kge.ports import StateConflict, StateUnavailable, StoreError
    from kge.pipeline import questionrun

    run_end = time.monotonic() + (record["expires_at"] - clock())
    wait_end = time.monotonic() + wait
    pause = [0.02]

    def save():
        nonlocal revision
        revision = state.put(record["id"], record, revision)

    def emit(event):
        if (event := public_event(event, record["request"].get("compact"))) is not None:
            if event.get("event") == "context" and event in record["events"]:
                return
            record["events"].append(event)
            if on_event is not None:
                on_event(len(record["events"]), event)

    def over_limit():
        return clock() >= record["expires_at"] or time.monotonic() >= run_end - MIN_CALL_SECONDS

    def expire():
        record.pop("answer", None)
        record["events"] = [event for event in record["events"] if event.get("event") != "answer"]
        record.update(state="expired", error=questionrun.TIME_LIMIT, reason=questionrun.RUN_TIME_LIMIT)

    def waited():
        if stop is not None and stop.is_set():
            return False
        left = min(wait_end, run_end - MIN_CALL_SECONDS) - time.monotonic()
        held = request_remaining()
        if held is not None:
            left = min(left, held - 1)
        if left <= 0:
            return False
        time.sleep(min(pause[0], left))
        pause[0] = min(pause[0] * 2, 0.5)
        return True

    if over_limit():
        for job in record["jobs"]:
            if "result" not in job and "handle" in job:
                c.e.w.cancel(job["handle"])
        expire()
        return run_view(record)
    try:
        with request_window(record["expires_at"] - clock()):
            save_final = True
            try:
                while True:
                    for index, job in enumerate(record["jobs"], 1):
                        if "result" in job or "handle" not in job:
                            continue
                        result = c.e.w.poll(job["handle"])
                        while result["state"] == "running" and waited():
                            result = c.e.w.poll(job["handle"])
                        if progress := result.get("progress"):
                            emit({"event": "progress", "stage": "warehouse", "state": result["state"], "statement": index, **progress})
                        if result["state"] == "failed":
                            if over_limit() or result.get("reason") == questionrun.RUN_TIME_LIMIT:
                                expire()
                            else:
                                record.update(state="failed", error=result.get("error") or "The warehouse could not finish this question.",
                                              reason=result.get("reason") or questionrun.WAREHOUSE_FAILURE)
                            return run_view(record)
                        if result["state"] != "done":
                            return run_view(record)
                        handle = job["handle"]
                        job["result"] = {"guard": handle["guard"], "rows": result["rows"], "meta": handle["meta"]}
                        job["progress"] = result.get("progress", {})
                        save()
                        c.e.w.release(handle)
                    try:
                        out = _run_pass(c, profile, record, save, emit, clock)
                    except questionrun.RunPending:
                        if any("result" not in job and "handle" in job for job in record["jobs"]) and waited():
                            continue
                        break
                    record.update(state="done", answer=out, completed_at=clock())
                    break
            except (StateConflict, StateUnavailable, StoreError):
                save_final = False
                raise
            except (QuotaExceeded, QuotaUnavailable) as exc:
                record.update(state="failed", error=str(exc))
            except ExternalCallError as exc:
                if over_limit():
                    expire()
                else:
                    record.update(state="failed", error=str(exc))
            except Exception:
                record.update(state="failed", error="This question could not finish. Please try again.")
            finally:
                if save_final:
                    record.update(lease=0, next_poll=clock() + record["poll_after"])
                    save()
    except StateConflict:
        if (ended := _stopped_meanwhile(c, record, state)) is None:
            raise
        return ended
    except (StateUnavailable, StoreError):
        raise
    except (QuotaExceeded, QuotaUnavailable) as exc:
        record.pop("answer", None)
        record.pop("reason", None)
        record["events"] = [event for event in record.get("events", []) if event.get("event") != "answer"]
        record.update(state="failed", error=str(exc))
    except ExternalCallError as exc:
        if over_limit():
            expire()
        else:
            record.pop("answer", None)
            record.pop("reason", None)
            record["events"] = [event for event in record["events"] if event.get("event") != "answer"]
            record.update(state="failed", error=str(exc))
    except Exception:
        record.pop("answer", None)
        record.pop("reason", None)
        record.update(state="failed", error="This question could not finish. Please try again.")
    return run_view(record)


def _stopped_meanwhile(c, record, state):
    """A pass whose save met a newer record: where that record has ended the run (its owner's stop, or another
    instance's ending), the warehouse jobs this pass submitted and holds unfinished are cancelled and the stored run is
    given; None where the run still runs elsewhere, so the conflict stands."""
    latest, _ = state.get(record["id"])
    if latest is None or latest.get("state") == "running":
        return None
    _cancel_unfinished(c, record)
    return run_view(latest)


def _cancel_unfinished(c, record):
    """Cancel the run's warehouse jobs that hold no result, each on its own: one the warehouse cannot cancel (already
    ended, or the warehouse out of reach) leaves the run's ending as it is, and the others are still cancelled."""
    for job in record["jobs"]:
        if "result" not in job and "handle" in job:
            try:
                c.e.w.cancel(job["handle"])
            except Exception:
                continue


def _run_pass(c, profile, record, save, emit, clock):
    from kge.pipeline import questionrun
    from kge.services.ask.models import Ask

    request = Ask.model_validate(record["request"])
    sent = list(record["events"])
    with questionrun.checkpoints(record, save), recording(record["request"]["question"], emit, sent,
                                                          record.setdefault("trace_parts", [])) as recorder:
        out = ask(c, profile, request.question, record["principal"], request.purpose, request.arm,
                  RunConfig(**record["config"]), products=request.products, history=request.history,
                  compact=False, runtimes=request.runtimes, on_stage=recorder.on_stage)
        trace = out.setdefault("run_trace", {})
        parts = [*record.get("trace_parts", []), trace]
        trace["llm_calls"] = [call for part in parts for call in part.get("llm_calls", [])]
        if started := next((part["started_at"] for part in parts if part.get("started_at")), None):
            trace["started_at"] = started
        trace["total_ms"] = max(0, round((clock() - record["created_at"]) * 1000, 2))
        for key in ("retrieved", "plan_rewrites", "data_reads"):
            if any(key in part for part in parts):
                merged = []
                for part in parts:
                    for entry in part.get(key, []):
                        if entry not in merged:
                            merged.append(entry)
                trace[key] = merged
        trace["stages"] = runtrace.merged_stages(parts)
        trace["tokens"] = {direction: runtrace.total(call.get(key) for call in trace["llm_calls"])
                           for direction, key in (("in", "tokens_in"), ("out", "tokens_out"))}
        out["run_trace"]["warehouse_jobs"] = [
            {"statement": i, "job_id": job["handle"]["id"], **job.get("progress", {})} for i, job in enumerate(record["jobs"], 1)
            if job["handle"]["guard"]["passed"]]
        out["run_trace"]["run_id"] = record["id"]
        full = out
        out = response_view(out, request.compact)
        if record.get("card") and request.compact:
            out["display"] = displayed(response_view(full), True, lambda: c.e)
    recorder.emit_final(out)
    return out
