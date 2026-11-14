"""The graph path, projected only from ordered snapshot reads recorded during the request."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.graph.schema import meta_model
from kge.pipeline import trace
from kge.pipeline.trace import QueryCancelled

GROUPS = ("words", "vocabulary", "access", "meaning", "rules", "tables", "runtime")
GROUP_OF = {"Words": "words", "Term": "vocabulary", "Acronym": "vocabulary", "Calendar": "vocabulary",
            "Principal": "access", "Unit": "access", "Context": "meaning", "Measure": "meaning", "Golden": "meaning",
            "Entity": "meaning", "Plan": "meaning", "Check": "rules", "Filter": "rules", "RowSelector": "rules", "Table": "tables", "Runtime": "runtime"}
GOLDEN_EDGE = next(edge["loaded_as"] for edge in meta_model()["edge_types"].values()
                   if edge["from"] == "Context" and edge["to"] == "Golden")
LOG = logging.getLogger(__name__)
ERROR = {"nodes": [], "edges": [], "unused": 0, "error": "The graph path could not be built for this answer."}


class Graphed:
    """The nodes and edges of one graph path as they are added: an id a node of a type and a raw graph id, withheld
    (withheld-<n>) where its name would be; a node added again keeps its place and is used if either was."""

    def __init__(self, redact, restricted: set[str] | frozenset[str] = frozenset()):
        self.withhold = redact
        self.restricted = {c.casefold() for c in restricted if c}
        self.shown: dict[str, str] = {}
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.ids: dict[tuple[str, str], str] = {}
        self.withheld: dict[str, int] = {}
        self.read_nodes = []
        self.read_edges = []

    def redact(self, text: str | None) -> str:
        """text as the asker may see it, each text withheld once per path."""
        text = text or ""
        if text not in self.shown:
            shown = self.withhold(text)
            for column in sorted(self.restricted, key=lambda column: (-len(column), column)):
                shown = re.sub(rf"(?<![A-Za-z0-9]){re.escape(column)}(?![A-Za-z0-9])", "withheld", shown, flags=re.I)
            self.shown[text] = shown
        return self.shown[text]

    def ident(self, kind: str, raw: str, *, force_withhold: bool = False) -> str:
        key = (kind, raw)
        if key not in self.ids:
            shown = self.redact(raw)
            if force_withhold or shown != raw:
                self.withheld[kind] = self.withheld.get(kind, 0) + 1
                shown = f"withheld-{self.withheld[kind]}"
            self.ids[key] = f"{kind}:{shown}"
        return self.ids[key]

    def shown_id(self, kind: str, raw: str) -> str:
        """raw as this path shows it: itself, or the withheld-<n> its id took."""
        return self.ident(kind, raw).split(":", 1)[1]

    def node(self, kind: str, raw: str, *, label: str = "", detail: str = "", used: bool = True, state: str = "done",
             certified: bool = False, badges: list[dict] | None = None, reason: str | None = None) -> str:
        nid = self.ident(kind, raw)
        self.read_nodes.append(nid)
        held = self.nodes.get(nid)
        if held is not None:
            held.update(label=self.redact(label) or held["label"], detail=self.redact(detail) or held["detail"],
                        used=held["used"] or used, certified=held["certified"] or certified)
            if state == "refused":
                held.update(state=state, reason=self.redact(reason))
            return nid
        name = self.redact(raw) if kind == "Words" else raw if self.shown_id(kind, raw) == raw else "withheld"
        out: dict[str, Any] = {"id": nid, "type": kind, "name": name,
                               "label": self.redact(label), "group": GROUP_OF[kind], "used": used, "state": state,
                               "certified": certified, "badges": list(badges or []), "detail": self.redact(detail)}
        if reason is not None:
            out["reason"] = self.redact(reason)
        self.nodes[nid] = out
        return nid

    def edge(self, src: str, dst: str, kind: str, *, label: str | None = None, planner: bool = False, used: bool = True,
             state: str = "done", reason: str | None = None, detail: str | None = None) -> None:
        shown = self.redact(label) if label else kind
        key = (src, dst, kind, shown)
        self.read_edges.append(key)
        held = self.edges.get(key)
        if held is not None:
            held["used"] = held["used"] or used
            if state == "refused":
                held.update(state=state, reason=self.redact(reason))
                if detail is not None:
                    held["detail"] = self.redact(detail)
            return
        out: dict[str, Any] = {"from": src, "to": dst, "type": kind, "label": shown, "planner": planner, "used": used,
                               "state": state}
        if reason is not None:
            out["reason"] = self.redact(reason)
        if detail:
            out["detail"] = self.redact(detail)
        self.edges[key] = out

    def refuse(self, nid: str, reason: str) -> None:
        self.nodes[nid].update({"state": "refused", "reason": reason})

    def refused(self) -> bool:
        return any(n["state"] == "refused" for n in self.nodes.values())

    def path(self, unused: int) -> dict[str, Any]:
        """The nodes in read order, and only the edges both of whose ends are among them."""
        nodes = list(self.nodes.values())
        edges = [e for e in self.edges.values() if e["from"] in self.nodes and e["to"] in self.nodes]
        return {"nodes": nodes, "edges": edges, "unused": unused}


@lru_cache(maxsize=64)
def _set_questions(path: str, stamp: int) -> tuple[str, dict[str, str]]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return "", {}
    items = data.get("items") if isinstance(data, dict) else data
    version = str(data.get("version") or "") if isinstance(data, dict) else "v1"
    return version, {str(i["id"]): i["question"] for i in items or [] if isinstance(i, dict)
                     and i.get("id") is not None and isinstance(i.get("question"), str)}


def golden_questions(engine: Any, name: str, version: str) -> dict[str, str]:
    """A golden set's questions by item id, from the estate's set file (<name>.json or <name>_v<n>.json, the one naming
    version first), each file read once while it is unchanged."""
    profile = getattr(engine, "profile", None)
    if profile is None or not name:
        return {}
    try:
        estate = Path(profile.estate())
    except (AttributeError, OSError, TypeError, ValueError):
        return {}
    found: list[tuple[str, dict[str, str]]] = []
    for p in [estate / f"{name}.json", *sorted(estate.glob(f"{name}_v*.json"))]:
        if p.is_file():
            found.append(_set_questions(str(p), p.stat().st_mtime_ns))
    for held, questions in found:
        if held == version:
            return questions
    return found[-1][1] if found else {}



def table_unit(table: str) -> str:
    return table.split(".", 1)[0] if "." in table else ""


def table_node(g: Graphed, engine: Any, table: str, used: bool = True) -> str:
    nid = g.ident("Table", table)
    if nid in g.nodes:
        g.nodes[nid]["used"] = g.nodes[nid]["used"] or used
        return nid
    nid = g.node("Table", table, used=used, detail=f"Table: {table}\nUnit: {table_unit(table)}")
    if g.nodes[nid]["name"] == "withheld":
        g.nodes[nid]["detail"] = "Table: withheld"
    return nid


def principal_label(engine: Any, principal: str) -> str:
    """The principal's role label, as the release's access mirror holds it: an asker that is a role has one."""
    rights = getattr(getattr(engine, "g", None), "rights", None)
    return str(rights().roles.get(principal) or "") if rights is not None and principal else ""


def unit_label(engine: Any, unit: str) -> str:
    return str((getattr(engine, "UNIT_LABEL", None) or {}).get(unit) or "")


def calendar_detail(engine: Any, calendar: str) -> str:
    held = (getattr(engine, "CALENDARS", None) or {}).get(calendar) or {}
    return f"\nYear starts in month {held['year_start_month']}" if held.get("year_start_month") else ""


class ReadPath:
    """One request's safe projection; the reader, never the finished answer, supplies every element."""

    def __init__(self, engine, principal, question):
        from kge.services.ask.engine.path import _redactor
        self.engine, self.principal, self.question = engine, principal, question
        redact, self.readable = _redactor(engine, principal)
        self.g = Graphed(redact, {str(c) for *_, c in getattr(engine, "RESTRICTED_COLUMNS", ())})
        self.contexts = {}
        self.rules = {}
        self.matches = {}
        self.reached = {}
        self.policies = {}
        self.role_reads = {}
        self.tables = set()
        self.stopped = False
        self.access_stopped = False
        self.error = False
        self.last_unused = 0
        self.emit = trace._graph_emit.get()

    def path(self):
        unused = sum(not used for _, used in self.rules.values())
        try:
            return dict(ERROR) if self.error else self.g.path(unused)
        except (QueryCancelled, RequestDeadlineExceeded):
            raise
        except Exception:
            self.error = True
            LOG.warning("graph path could not be built", exc_info=True)
            return dict(ERROR)

    def record(self, kind, value, used=True):
        if self.error or (self.stopped and kind != 'plan_start'):
            return {"kind": kind, "used": used, "nodes": [], "edges": []}
        self.g.read_nodes, self.g.read_edges = [], []
        before_nodes = {k: dict(v) for k, v in self.g.nodes.items()}
        before_edges = {k: dict(v) for k, v in self.g.edges.items()}
        try:
            getattr(self, kind)(value, used)
            self.details()
        except (QueryCancelled, RequestDeadlineExceeded):
            raise
        except Exception:
            self.error = True
            LOG.warning("graph read could not be recorded", exc_info=True)
        nodes = [v for k, v in self.g.nodes.items() if v != before_nodes.get(k)]
        edges = [v for k, v in self.g.edges.items() if v != before_edges.get(k)]
        events = []
        if self.emit and not self.error:
            for node in nodes:
                events.append({"event": "state_changed" if node["id"] in before_nodes else "node_added",
                               **({"id": node["id"], "state": node["state"]} if node["id"] in before_nodes else {}),
                               "node": dict(node)})
            for edge in edges:
                if edge["from"] in self.g.nodes and edge["to"] in self.g.nodes:
                    events.append({"event": "edge_added", "edge": dict(edge)})
            unused = sum(not taken for _, taken in self.rules.values())
            if unused != self.last_unused:
                self.last_unused = unused
                events.append({"event": "graph_summary", "unused": unused})
        read_nodes = dict.fromkeys([*self.g.read_nodes, *(n["id"] for n in nodes)])
        read_edges = dict.fromkeys([*self.g.read_edges, *(k for k, v in self.g.edges.items() if v != before_edges.get(k))])
        return {"kind": kind, "used": used, "nodes": list(read_nodes),
                "edges": [{field: self.g.edges[key][field] for field in ("from", "to", "type")} for key in read_edges],
                "_events": events, "_emit": self.emit}

    def principal_read(self, value, used):
        principal, readable, *roles = value
        if principal != self.principal:
            return
        self.role_reads = roles[0] if roles else {principal: readable}
        for role, units in sorted(self.role_reads.items()):
            self.g.node("Principal", role, label=principal_label(self.engine, role),
                        detail=f"Principal: {role}\nAsker: {principal}\nReads: {', '.join(sorted(units)) or 'none'}")

    def access(self, value, used):
        principal, units, denied = value
        if principal != self.principal:
            return
        g = self.g
        reads = self.role_reads or {principal: self.readable}
        who = {role: g.node("Principal", role, label=principal_label(self.engine, role)) for role in sorted(reads)}
        for unit in units:
            refused = unit in denied
            reason = g.redact(f"{principal} may not read {unit}.") if refused else None
            uid = g.node("Unit", unit, label=unit_label(self.engine, unit), state="refused" if refused else "done",
                         reason=reason, detail=f"Unit: {unit}")
            for role, nid in who.items():
                if refused or unit in reads[role]:
                    g.edge(nid, uid, "CAN_READ", state="refused" if refused else "done", reason=reason)
                if refused and g.nodes[uid]["name"] == "withheld":
                    g.refuse(nid, reason)
        self.access_stopped = self.stopped = bool(denied)

    def plan_start(self, value, used):
        self.stopped = self.access_stopped

    def words(self, text, used):
        self.g.node("Words", text, detail=f"Said in the question: {text}")

    def match(self, value, used):
        entry, text, vocab, *targets = value
        g = self.g
        word = g.node("Words", text, detail=f"Said in the question: {text}")
        self.matches.setdefault(entry.id, set()).add(word)
        if entry.kind == "calendar":
            cal = g.node("Calendar", entry.id, detail=f"Calendar: {entry.id}" + calendar_detail(self.engine, entry.id))
            g.edge(word, cal, "matches", planner=True)
            return
        if entry.kind in ("context", "measure"):
            kind = entry.kind.capitalize()
            declared = getattr(self.engine.semantics, entry.kind + "_ids", {entry.id})
            if entry.id in declared:
                target = g.node(kind, entry.id, label=entry.label, detail=(
                    "" if g.ident(kind, entry.id) in g.nodes else f"{kind}: {entry.id}"))
                g.edge(word, target, "matches", planner=True)
        if vocab is None:
            return
        names = sorted(vocab.term_ids, key=lambda name: (name, vocab.term_ids[name]))
        term = next((name for name in names if name.casefold() == text.casefold()), None)
        if not term:
            term = (getattr(self.engine, "ALIASES", {}) or {}).get(text.casefold())
        if not term and entry.kind in ("measure", "context"):
            term = next((name for name in names if name.casefold() == entry.label.casefold()), None)
        if not term:
            return
        tid = vocab.term_ids.get(term)
        if not tid:
            return
        term_node = g.node("Term", tid, label=term, detail=f"Term: {term}\nId: {tid}")
        acronym = next((a for a, terms in sorted(vocab.acronyms.items()) if a.casefold() == text.casefold() and tid in terms), None)
        if acronym:
            acr = g.node("Acronym", acronym, detail=f"Acronym: {acronym}\nTerm: {term}")
            g.edge(term_node, acr, "ABBREVIATED_AS")
            g.edge(word, acr, "matches", planner=True)
        else:
            g.edge(word, term_node, "matches", planner=True)
        for identifier in (entry.id, *(targets[0] if targets else ())):
            self.matches.setdefault(identifier + "/term", set()).add(term_node)

    def plan_refusal(self, value, used):
        decision, source, check, fallback = value
        g = self.g
        reason = "; ".join(decision.get("missing") or []) or "Choose the intended declared meaning."
        plan = g.node("Plan", source, label=f"{source.capitalize()} plan", detail=(
            f"Outcome: {decision['kind']}\n" + ("Replacement: rules plan" if fallback == "rules" else "No replacement plan")))
        gate = g.node("Check", source + "/" + check, label=check.capitalize() + " check", state="refused",
                      reason=reason, detail=f"{check.capitalize()} check: {decision['kind']}\n{reason}")
        for word in sorted({w for words in self.matches.values() for w in words if w.startswith("Words:")}):
            g.edge(word, plan, "plans", planner=True)
        g.edge(plan, gate, "CHECKS", planner=True, state="refused", reason=reason, detail=reason)
        self.stopped = fallback != "rules"

    def context(self, value, used):
        context, authored, declared = value
        if context.unit not in self.readable:
            return
        g = self.g
        if authored:
            self.contexts[context.id] = context
            cid = g.node("Context", context.id, label=context.label, used=used)
        if declared:
            mid = g.node("Measure", context.measure, label=context.term, used=used,
                         detail=f"Measure: {context.measure}\nExpression: {context.expression}\nAggregation: {context.aggregation}")
        fact = self.table(context.fact, used)
        if authored:
            g.edge(cid, fact, "USES_FACT", used=used)
        if declared:
            g.edge(mid, fact, "COMPUTED_FROM", used=used, detail="Columns: " + ", ".join(context.values))
            if authored:
                g.edge(cid, mid, "SPECIALIZES", used=used)

    def vocabulary(self, value, used):
        context, term, tid, measure, calendar = value
        g = self.g
        cid = g.ident("Context", context)
        if cid not in g.nodes:
            return
        self.reached[context] = (tid, term)
        term_node = g.node("Term", tid, label=term, used=used, detail=f"Term: {term}\nId: {tid}")
        g.edge(term_node, cid, "RESOLVES_TO", used=used)
        if calendar and f"Calendar:{calendar}" in g.nodes:
            g.edge(g.ident("Measure", measure), g.ident("Calendar", calendar), "IN_CALENDAR", used=used)

    def measure_calendar(self, value, used):
        measure, calendar = value
        mid, cal = self.g.ident("Measure", measure), self.g.ident("Calendar", calendar) if calendar else ""
        if mid in self.g.nodes and cal in self.g.nodes:
            self.g.edge(mid, cal, "IN_CALENDAR", used=used)

    def golden(self, value, used):
        context, golden = value
        g, engine = self.g, self.engine
        cid = g.ident("Context", context)
        if cid not in g.nodes:
            return
        g.nodes[cid]["certified"] = bool(golden)
        if golden:
            golden = sorted(golden, key=lambda item: (item["id"], item["kind"], item["item"]))
            found = next(((i, golden_questions(engine, i["set"], i["version"]).get(i["item"])) for i in golden
                          if " ".join(golden_questions(engine, i["set"], i["version"]).get(i["item"], "").split()).casefold()
                          == " ".join(self.question.split()).casefold()), None)
            items = [(found[0], found[1])] if found else [(item, golden_questions(engine, item["set"], item["version"]).get(item["item"], "")) for item in golden]
            for item, question in items:
                gid = g.node("Golden", item["id"], label=question, certified=True, used=used,
                             detail=f"Golden item: {item['id']}\nSet: {item['set']} {item['version']}\nQuestion: {question}\nCertifies: {g.shown_id('Context', context)}")
                g.edge(cid, gid, item["kind"], used=used)

    def table(self, value, used):
        if not used and table_unit(value) not in self.readable:
            return ""
        if used:
            self.tables.add(value)
        nid = table_node(self.g, self.engine, value, used)
        self.g.read_nodes.append(nid)
        return nid

    def policy(self, value, used):
        context, table, policy = value
        nid = self.table(table, used)
        if nid and policy:
            hidden = self.g.nodes[nid]["name"] == "withheld"
            pid = self.g.ident("Policy", policy["id"], force_withhold=hidden)
            self.g.read_nodes.append(pid)
            self.policies.setdefault(context, {})[policy["id"]] = used
            if hidden:
                return
            ident = self.g.shown_id("Policy", policy["id"])
            self.g.nodes[nid]["badges"] = [{"kind": "SCD2", "id": ident}]
            base = self.g.nodes[nid]["detail"].split("\nPolicy:", 1)[0].split("\nJoinRule:", 1)[0]
            self.g.nodes[nid]["detail"] = self.g.redact(base + f"\nPolicy: {ident} (SCD2; current rows by {policy.get('is_current')}; "
                f"valid {policy.get('valid_from')} to {policy.get('valid_to')})")

    def rule(self, value, used):
        context, kind, rule = value
        if table_unit(rule.table) not in self.readable:
            return
        key = (kind, context, rule.id)
        used = used or self.rules.get(key, (None, False))[1]
        self.rules[key] = (rule, used)
        g = self.g
        rid = g.node(kind, rule.id, label=rule.label, used=used, detail=(
            f"{kind}: {g.shown_id(kind, rule.id)} ({rule.label})\nPredicate: {rule.predicate}\nTable: {rule.table}"))
        cid = g.ident("Context", context)
        if cid in g.nodes:
            g.edge(cid, rid, "REQUIRES_FILTER" if kind == "Filter" else "SELECTS_ROWS_WITH", used=used)
        g.edge(rid, self.table(rule.table, used), "TARGETS", used=used)

    def join(self, value, used):
        context, join = value
        if not used and any(table_unit(t) not in self.readable for t in (join.source, join.target, join.through) if t):
            return
        key = ("JoinRule", context, join.id)
        used = used or self.rules.get(key, (None, False))[1]
        self.rules[key] = (join, used)
        g = self.g
        ident = g.shown_id("JoinRule", join.id)
        g.read_nodes.append(g.ident("JoinRule", join.id))
        g.edge(self.table(join.source, used), self.table(join.target, used), "JoinRule", used=used, label=ident,
               detail=f"Join: {ident} ({join.kind})\nCondition: {join.condition}")

    def entity(self, value, used):
        ident, entity, column, entry_id = value
        table = entity["table"]
        if table_unit(table) not in self.readable:
            return
        g = self.g
        eid = g.node("Entity", ident, label=entity.get("label") or "", used=used,
                     detail=f"Entity: {ident}\nTable: {table}\nKey: {', '.join(entity['key'])}"
                            + (f"\nRows grouped by: {column}" if column else ""))
        g.edge(self.table(table, used), eid, "REPRESENTS", used=used)
        matched = self.matches.get(entry_id + "/term", ()) or self.matches.get(entry_id, ())
        for source in sorted(matched):
            g.edge(source, eid, "matches", planner=True)

    def runtime(self, value, used):
        meta, guard = value
        held = meta.get("runtime")
        if not held:
            return
        g = self.g
        rt = held["id"]
        reason = "; ".join(guard.get("problems") or []) if not guard.get("passed", True) else None
        nid = g.node("Runtime", rt, label=held.get("label") or rt, certified=bool(meta.get("runtime_certified")),
                     state="refused" if reason else "done", reason=reason, detail=(
                         f"Runtime: {rt}\nKind: {held.get('kind', '')} · Dialect: {held.get('dialect', '')}\n"
                         f"Certified: {'yes' if meta.get('runtime_certified') else 'no'}"))
        self.g.nodes[nid]["certified"] = bool(meta.get("runtime_certified"))
        self.g.nodes[nid]["sql"] = g.redact(meta.get("sql") or "")
        for table in sorted(self.tables):
            g.edge(nid, g.ident("Table", table), "IMPLEMENTS", label="HOLDS · IMPLEMENTS", detail=f"{rt}.{table}")

    def details(self):
        g = self.g
        for context in self.contexts.values():
            reached = self.reached.get(context.id)
            source = f"Term:{g.shown_id('Term', reached[0])} ({reached[1]})" if reached else "none recorded"
            lines = [f"Context: {g.shown_id('Context', context.id)} ({context.label})", f"Reached from: {source}",
                     f"Measure: {g.shown_id('Measure', context.measure)}", f"Fact: {g.shown_id('Table', context.fact)}", context.description]
            certified = [e["to"] for e in g.edges.values() if e["from"] == g.ident("Context", context.id) and e["type"] == GOLDEN_EDGE]
            lines.append("Certified by: " + (", ".join(certified) or "none"))
            for kind in ("Filter", "RowSelector", "JoinRule"):
                for used in (True, False):
                    ids = [g.shown_id(kind, raw) for (k, ctx, raw), (_, taken) in self.rules.items()
                           if k == kind and ctx == context.id and taken == used]
                    lines.append(f"{kind} {'used' if used else 'not used'}: " + (", ".join(ids) or "none"))
            policies = [g.shown_id("Policy", raw) for raw, used in self.policies.get(context.id, {}).items() if used]
            lines.append("Policies applied (SCD2): " + (", ".join(dict.fromkeys(policies)) or "none"))
            g.nodes[g.ident("Context", context.id)]["detail"] = g.redact("\n".join(lines))
        for nid, node in g.nodes.items():
            if node["type"] == "Table":
                joins = [f"JoinRule: {e['label']} ({'used' if e['used'] else 'not used'})" for e in g.edges.values()
                         if e["type"] == "JoinRule" and nid in (e["from"], e["to"])]
                base = node["detail"].split("\nJoinRule:", 1)[0]
                node["detail"] = base + ("\n" + "\n".join(joins) if joins else "")


def recorded(call):
    """Keep a single recorder across Combined, Engine and the reference agent's nested calls."""
    @wraps(call)
    def run(owner, question, principal=None, *args, **kwargs):
        if trace._graph_recorder.get() is not None:
            return call(owner, question, principal, *args, **kwargs)
        engine = getattr(owner, "e", owner)
        principal = principal or getattr(engine, "ASKER", "")
        try:
            recorder = ReadPath(engine, principal, question)
        except (QueryCancelled, RequestDeadlineExceeded):
            raise
        except Exception:
            out = call(owner, question, principal, *args, **kwargs)
            return {**out, "graph_path": dict(ERROR)}
        with trace.graph_recording(recorder):
            out = call(owner, question, principal, *args, **kwargs)
            return {**out, "graph_path": recorder.path()}
    return run


def build_graph_path(engine: Any, answer: dict[str, Any]) -> dict[str, Any]:
    """Return only recorded reads, with no SQL, plan, reason or answer-field inference."""
    recorder = trace._graph_recorder.get()
    if recorder is not None:
        return recorder.path()
    return answer.get("graph_path") or {"nodes": [], "edges": [], "unused": 0}
