"""The ask service's logic, one function a capability, whatever the transport (the HTTP routes, the MCP tools): an answer
(ask, answer), each with its readable text; a term's map; and the unmatched questions the improvement job reads."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from contextlib import contextmanager
from pathlib import Path

from kge.adapters.deadline import ExternalCallError
from kge.adapters.model import request_budget
from kge.pipeline.profile import Profile
from kge.pipeline.questionrun import RunPending
from kge.pipeline.trace import QueryCancelled, cancellable, check_cancelled, graph_stream, trigger_cancel
from kge.pipeline.settings import ROOT
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.contextpath import build_graph_path
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, Engine, RunConfig
from kge.services.ask.engine.path import PathStreamRecorder, build_path
from kge.services.ask.engine.present import present
from kge.services.ask.engine.wording import worded
from kge.services.ask.models import AskResponse, QuestionRunResponse

_Q_PREFIXES = ("what", "was", "were", "is", "are", "the", "show", "give", "me", "tell", "how", "much", "please")
MAX_UNMATCHED_CHARS = 500
COMPACT_DROPPED = frozenset(("sql", "sql_run", "run_trace", "trace", "plan", "plan_vocabulary", "named",
                             "guard", "vector_hits", "candidates", "candidate_sources", "queue", "display", "join_path", "path", "graph_path"))
CONTEXT_LABELS = frozenset(("id", "context", "label", "term", "time_mode"))
COMPACT_RECORDS = frozenset(("decided", "figures", "citations", "sources"))
STREAM_DROPPED = frozenset(("node_added", "edge_added", "edge_changed", "state_changed", "graph_summary", "plan", "stage"))
CONTEXT_SECTIONS = ("Not glossary terms:", "One customer group:", "Across units:", "Across units, through the certified join:",
                    "Columns by role (", "Contexts (", "Contexts:", "Declared joins, the only ones", "Do not use:",
                    "Sources, by number", "Provenance: each certified column")


def response_view(out: dict, compact: bool = False) -> dict:
    """A view of the computed response, with no reads. History is the opaque, SQL-free continuation contract:
    its checked plan and access binding must round-trip unchanged for a follow-up turn."""
    if not compact:
        return out

    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {key: ({k: v for k, v in item.items() if k in CONTEXT_LABELS} if key == "context" and isinstance(item, dict)
                      else project(item) if key in COMPACT_RECORDS else item)
                for key, item in value.items() if key not in COMPACT_DROPPED
                and not (key == "parts" and isinstance(item, list))}

    compact_out = project(out)
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
def ask(c: Combined, profile: Profile, question: str, who: str, purpose: str, arm: str, cfg: RunConfig, products: list[str] | None = None, history=(), compact: bool = False, runtimes: list[str] | None = None, on_stage=None) -> dict:
    """POST /ask: the graph arm answered whole (Combined.ask) as who, on cfg, with its run trace and its answer_text.
    A combined answer's text is its own (the template, or a model's that passed the checks); any other is the template's.
    On the cloud path a model step that fails stops it (NoFallback, a 503), never a silent template."""
    out = runtrace.run("ask", lambda: c.ask(question, who, purpose, config=cfg, products=products, history=history, runtimes=runtimes), graph=c.g, on_stage=on_stage)
    log_unmatched(profile, "/ask", arm, cfg.graph, who, question, out, c.e)
    return response_view(worded(out, c.e), compact)


def answer(e: Engine, profile: Profile, question: str, who: str, purpose: str, arm: str, cfg: RunConfig, products: list[str] | None = None, history=(), runtimes: list[str] | None = None, on_stage=None) -> dict:
    """POST /answer: the rule-based answer (Engine.answer) as who, on cfg, with its run trace and the template's
    answer_text."""
    out = runtrace.run("answer", lambda: e.answer(question, who, purpose, config=cfg, products=products, history=history, runtimes=runtimes), graph=e.g, on_stage=on_stage)
    log_unmatched(profile, "/answer", arm, cfg.graph, who, question, out, e)
    return worded(out, e)


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


def agent(ag, profile: Profile, a, who: str, on_stage=None) -> dict:
    """POST /agent: the reference agent's answer on a's arm as who, with its run trace, its path and its graph path."""
    out = runtrace.run("agent", lambda: ag.ask(a.question, who, a.purpose, arm=a.arm, products=a.products, history=a.history, runtimes=a.runtimes), graph=ag.g, on_stage=on_stage)
    log_unmatched(profile, "/agent", a.arm, a.arm == "treatment", who, a.question, out, ag.e)
    out["path"] = build_path(ag.e, out)
    out["graph_path"] = build_graph_path(ag.e, out)
    return out


def context(ag, a, who: str):
    """POST /context: the context package the reference agent's prompt carries on a's arm, built as who: with a's
    products, history and plan vocabulary where a asks for the plan vocabulary, else from the question alone."""
    if a.plan_vocabulary:
        return ag.context(a.question, who, a.purpose, a.arm, products=a.products, plan_vocabulary=True, history=a.history, runtimes=a.runtimes)
    return ag.context(a.question, who, a.purpose, a.arm, runtimes=a.runtimes)


def retrieve(e: Engine, r, who: str) -> dict:
    """POST /retrieve: vector search over the chunks who may read for r's purpose, at r's shelf and tiers. A bad shelf or
    tier is a ValueError."""
    return e.g.retrieve(r.query, who, r.purpose, r.k, r.shelf, r.tiers)


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
        events.append({"event": "error", "error": out["error"]})
    return events


def public_event(event: dict, compact) -> dict | None:
    """A stream's event as the request sees it: whole, or for a compact request none of the graph's or the plan's events
    and a context by its labels alone. None for an event a compact request does not see."""
    if compact:
        if event.get("event") in STREAM_DROPPED:
            return None
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

    threading.Thread(target=worker, daemon=True, name="ask-stream").start()

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

    threading.Thread(target=worker, daemon=True, name="ask-run").start()
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
    if record.get("display") and not record.get("request", {}).get("compact"):
        out["_display"] = True
    return out


@request_budget
def start_run(c, profile, request, who, cfg, *, state=None, clock=None, presentation=False, wait=0.0, on_start=None,
              on_event=None, stop=None):
    """Start governed work and acknowledge its id as soon as the warehouse owns the wait. on_start(record) is told the
    run as soon as it is saved, on_event(cursor, event) each event as the run records it, and a set stop ends the wait
    for the warehouse early."""
    import time
    import uuid
    from dataclasses import asdict
    from kge.adapters.questionstate import for_profile
    from kge.pipeline.questionrun import timeout, interval
    from kge.adapters.deadline import request_timeout, request_window
    state = state or for_profile(profile)
    now = (clock or time.time)()
    record = {"id": uuid.uuid4().hex, "principal": who, "request": request.model_dump(mode="json"),
              "config": asdict(cfg), "display": presentation, "identity": c.e.identity, "readable": sorted(c.g.readable_units(who)),
              "state": "running", "created_at": now, "expires_at": now + timeout(), "poll_after": interval(),
              "events": [], "jobs": [], "plan": {}, "completed_at": None,
              "next_poll": now, "lease": now + request_timeout() + 10}
    with request_window(record["expires_at"] - (clock or time.time)()):
        revision = state.put(record["id"], record, None)
    if on_start is not None:
        on_start(record)
    return _advance_run(c, profile, record, state, revision, clock or time.time, wait, on_event=on_event, stop=stop)


@request_budget
def get_run(c, profile, run_id, who, *, state=None, clock=None, on_start=None, on_event=None, stop=None):
    """Any instance can poll; ownership, the current grant and release still govern the answer. A poll that advances
    the run tells on_start(record) first, then on_event(cursor, event) each event as the run records it."""
    import time
    from kge.adapters.questionstate import for_profile
    from kge.adapters.deadline import request_timeout, request_window
    from kge.ports import StateConflict
    state = state or for_profile(profile)
    clock = clock or time.time
    record, revision = state.get(run_id)
    if record is None or record["principal"] != who:
        return None
    if c.e.identity != record["identity"] or not set(record["readable"]) <= c.g.readable_units(who):
        return {**run_view(record), "events": [], "answer": None, "state": "failed",
                "error": "Access or the served release changed; ask the question again."}
    if record["state"] != "running":
        return run_view(record)
    now = clock()
    if now < record["expires_at"] and (now < record.get("lease", 0) or now < record["next_poll"]):
        return run_view(record)
    if now >= record["expires_at"]:
        return _advance_run(c, profile, record, state, revision, clock)
    record["lease"] = now + request_timeout() + 10
    try:
        with request_window(record["expires_at"] - clock()):
            revision = state.put(run_id, record, revision)
    except StateConflict:
        with request_window(record["expires_at"] - clock()):
            latest, _ = state.get(run_id)
        return run_view(latest)
    if on_start is not None:
        on_start(record)
    return _advance_run(c, profile, record, state, revision, clock, on_event=on_event, stop=stop)


def _advance_run(c, profile, record, state, revision, clock, wait=0.0, on_event=None, stop=None):
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
        record.update(state="expired", error=questionrun.TIME_LIMIT)

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
                            if over_limit():
                                expire()
                            else:
                                record.update(state="failed", error=result.get("error") or "The warehouse could not finish this question.")
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
    except (StateConflict, StateUnavailable, StoreError):
        raise
    except ExternalCallError as exc:
        if over_limit():
            expire()
        else:
            record.pop("answer", None)
            record["events"] = [event for event in record["events"] if event.get("event") != "answer"]
            record.update(state="failed", error=str(exc))
    except Exception:
        record.pop("answer", None)
        record.update(state="failed", error="This question could not finish. Please try again.")
    return run_view(record)


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
        out = response_view(out, request.compact)
    recorder.emit_final(out)
    return out
