"""Structured query path (nodes and edges) for every Ask response."""
from __future__ import annotations

import re
from typing import Any

from kge.services.ask.engine.reading import currency_of, period_of, term_of
from kge.services.ask.engine.wording import NAME_EDGE, WITHHELD, Hidden, catalogue, edge, exact

SPINE_ORDER = ("question", "terms", "measure", "tables", "access", "runtime", "query", "figure")


def _format_ms(ms: float | int | None) -> str | None:
    if ms is None:
        return None
    val = max(0.0, float(ms))
    if val < 10.0:
        return f"{val:.1f} ms"
    if val < 1000.0:
        return f"{round(val)} ms"
    return f"{val / 1000.0:.2f} s"


def _stage_times(answer: dict[str, Any]) -> dict[str, str | None]:
    override = answer.get("_stage_ms")
    by_name: dict[str, float] = {}
    if isinstance(override, dict):
        for name, ms in override.items():
            by_name[str(name)] = float(ms or 0.0)
    else:
        rt = answer.get("run_trace")
        if isinstance(rt, dict) and isinstance(rt.get("stages"), list):
            for st in rt["stages"]:
                if isinstance(st, dict) and st.get("stage"):
                    by_name[str(st["stage"])] = float(st.get("ms") or 0.0)
    return {
        "question": _format_ms(by_name.get("understand", 0.1)),
        "terms": _format_ms(by_name.get("resolve", by_name.get("understand", 0.1))),
        "measure": _format_ms(by_name.get("plan", 0.1)),
        "tables": _format_ms(by_name.get("plan", 0.1)),
        "access": _format_ms(by_name.get("access", 0.1)),
        "runtime": _format_ms(by_name.get("plan", 0.1)),
        "query": _format_ms(
            (by_name.get("gate", 0.0) + by_name.get("execute", 0.0))
            if ("gate" in by_name or "execute" in by_name)
            else 0.1
        ),
        "figure": _format_ms(
            by_name.get(
                "render",
                by_name.get(
                    "phrase",
                    by_name.get(
                        "judge",
                        by_name.get("bind", by_name.get("retrieve")),
                    ),
                ),
            )
        ),
    }


def _redactor(engine: Any, principal: str):
    hidden = Hidden.of(engine, principal) if engine is not None and hasattr(engine, "g") else None
    readable = (
        engine.g.readable_units(principal)
        if engine is not None and hasattr(engine, "g") and principal
        else set()
    )
    unreadable_tokens: list[str] = []
    if engine is not None and hasattr(engine, "g"):
        cols, _ = catalogue(engine)
        business = getattr(engine, "BUSINESS", None) or {}
        readable_col_names = {
            c["name"].lower()
            for c in cols
            if c["unit"] in readable
        }
        restricted_set = {
            (u.lower(), t.lower(), c.lower())
            for u, t, c in (getattr(engine, "RESTRICTED_COLUMNS", None) or [])
        }
        tokens: set[str] = set()
        for u, t, c in restricted_set:
            tokens.update({f"{u}.{t}.{c}", f"{t}.{c}", c})
            b_name = business.get(f"{t}.{c}")
            if b_name:
                tokens.add(b_name)
                if "." in b_name:
                    tokens.add(b_name.split(".", 1)[1])
        for c in cols:
            u, t, col = c["unit"], c["tbl"], c["name"]
            if u not in readable:
                tokens.update({f"{u}.{t}.{col}", f"{u}.{t}", f"{t}.{col}"})
                if col.lower() not in readable_col_names:
                    tokens.add(col)
                b_name = business.get(f"{t}.{col}")
                if b_name:
                    tokens.add(b_name)
        unreadable_tokens = sorted((tok for tok in tokens if tok), key=len, reverse=True)

    def redact(text: str | None, *, protect: list[str | None] | None = None) -> str:
        if not text:
            return ""
        out = str(text)
        if hidden is not None:
            out = hidden.withheld(out, protect or [])
        folded = out.lower() if out.isascii() else None
        for tok in unreadable_tokens:
            if folded is not None and tok.isascii() and tok.lower() not in folded:
                continue
            out = edge(NAME_EDGE, tok, re.I).sub("withheld", out)
            folded = out.lower() if out.isascii() else None
        out = out.replace(WITHHELD, "withheld")
        return out

    return redact, readable


def _extract_ref_tables(answer: dict[str, Any]) -> list[str]:
    tables: list[str] = []
    for step in answer.get("trace") or []:
        if isinstance(step, dict) and step.get("step") == "sources":
            for t in step.get("tables") or []:
                if t and t not in tables:
                    tables.append(str(t))
    ctx = answer.get("context") or {}
    if isinstance(ctx, dict) and ctx.get("fact") and ctx["fact"] not in tables:
        tables.append(str(ctx["fact"]))
    if not tables:
        ref_sql = answer.get("reference_sql") or answer.get("sql") or ""
        if isinstance(ref_sql, str) and ref_sql.strip():
            for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)", ref_sql, re.I):
                tbl = m.group(1)
                if tbl.upper() not in ("SELECT", "UNNEST") and tbl not in tables:
                    tables.append(tbl)
    return tables


def build_path(engine: Any, answer: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build the structured query path `{nodes, edges}` from the finished answer."""
    principal = str(answer.get("principal") or "")
    purpose = str(answer.get("purpose") or "analysis")
    question = str(answer.get("question") or "")
    behaviour = str(answer.get("behaviour") or "")
    redact, readable = _redactor(engine, principal)
    times = _stage_times(answer)

    raw_reason = str(answer.get("reason") or "")
    protected = [question] if not answer.get("restricted") else []
    reason = redact(raw_reason, protect=protected)

    trace = answer.get("trace") if isinstance(answer.get("trace"), list) else []
    parse_step = next(
        (s for s in trace if isinstance(s, dict) and s.get("step") in ("parse", "understand")),
        {},
    )
    term = term_of(answer) or parse_step.get("term") or ""
    terms_list = parse_step.get("terms") or ([term] if term else [])
    period = period_of(answer) or parse_step.get("period") or ""
    ctx = answer.get("context") if isinstance(answer.get("context"), dict) else {}
    time_mode = ctx.get("time_mode") or ""

    asked_units: list[str] = []
    raw_units = parse_step.get("units")
    if isinstance(raw_units, list):
        asked_units.extend(str(u) for u in raw_units if u)
    if answer.get("unit"):
        asked_units.append(str(answer["unit"]))
    if answer.get("denied_warehouses"):
        asked_units.extend(str(u) for u in (answer.get("denied_warehouses") or []) if u)
    asked_units = list(dict.fromkeys(asked_units))

    has_unreadable_unit = bool(
        answer.get("denied_warehouses")
        or (asked_units and readable and any(u not in readable for u in asked_units))
        or "may not read" in raw_reason.lower()
    )
    has_restricted = bool(
        answer.get("restricted") or raw_reason.startswith("Restricted personal data")
    )

    guard = answer.get("guard") if isinstance(answer.get("guard"), dict) else {}
    skipped_runtimes = list(answer.pop("_skipped_runtimes", None) or [])
    if not skipped_runtimes and isinstance(answer.get("path"), dict):
        for prev_node in answer["path"].get("nodes") or []:
            if isinstance(prev_node, dict) and prev_node.get("state") == "skipped":
                nid = str(prev_node.get("id") or "")
                sk_id = nid.split(":", 1)[1] if ":" in nid else nid
                if sk_id:
                    skipped_runtimes.append({
                        "id": sk_id,
                        "label": prev_node.get("label") or sk_id,
                        "reason": prev_node.get("reason") or "skipped",
                    })

    refused_at: str | None = None
    stopped_after: str | None = None
    is_definitional = behaviour in ("cite_certified", "cite", "glossary_definition")

    if behaviour in ("refuse", "blocked_access"):
        failed_at = str(guard.get("failed_at") or "")
        if failed_at in ("static", "partition", "explain", "cost", "execute") or raw_reason.startswith("SQL gate"):
            refused_at = "query"
        elif failed_at in ("runtime", "implements") or (
            any(
                k in raw_reason.lower()
                for k in (
                    "no runtime",
                    "unknown runtime",
                    "runtime list",
                    "implements mapping",
                    "unavailable",
                    "not held by runtime",
                    "execution_identity",
                    "unmapped reference column",
                )
            )
            and not has_unreadable_unit
        ):
            refused_at = "runtime"
        elif answer.get("joins_refused") and not has_unreadable_unit and not has_restricted:
            refused_at = "tables"
        else:
            refused_at = "access"
    elif behaviour in ("abstain", "abstain_with_reason", "abstain_route_steward", "clarify"):
        if answer.get("sql") or answer.get("sql_run"):
            if guard and not guard.get("passed", True):
                refused_at = "query"
            else:
                stopped_after = "figure"
        elif not term or "period" in raw_reason.lower() or "date" in raw_reason.lower():
            stopped_after = "terms"
        else:
            stopped_after = "measure"

    def node_state(node_id: str) -> str:
        idx = SPINE_ORDER.index(node_id)
        if refused_at is not None:
            ref_idx = SPINE_ORDER.index(refused_at)
            if idx < ref_idx:
                return "done"
            if idx == ref_idx:
                return "refused"
            return "waiting"
        if is_definitional:
            return "done" if node_id in ("question", "terms", "access", "figure") else "waiting"
        if stopped_after is not None:
            stop_idx = SPINE_ORDER.index(stopped_after)
            return "done" if idx <= stop_idx else "waiting"
        return "done"

    q_redacted = redact(question)
    q_node = {
        "id": "question",
        "kind": "question",
        "label": q_redacted,
        "state": node_state("question"),
        "time": times["question"],
        "certified": False,
        "detail": f"Question: {q_redacted}\nPrincipal: {principal} · Purpose: {purpose}",
    }

    terms_display = ", ".join(redact(str(t)) for t in terms_list if t) if terms_list else ""
    period_display = redact(str(period)) if period else ""
    terms_parts = [p for p in (terms_display, period_display) if p]
    terms_label = " · ".join(terms_parts) if terms_parts else "Terms & period"
    terms_detail_lines = [
        f"Terms: {terms_display or 'none'}",
        f"Period: {period_display or 'all available'}",
    ]
    if time_mode:
        terms_detail_lines.append(f"Basis: {time_mode}")
    if stopped_after == "terms" and reason:
        terms_detail_lines.append(f"Reason: {reason}")
    terms_node = {
        "id": "terms",
        "kind": "terms",
        "label": terms_label,
        "state": node_state("terms"),
        "time": times["terms"] if node_state("terms") == "done" else None,
        "certified": False,
        "detail": "\n".join(terms_detail_lines),
    }

    m_state = node_state("measure")
    if has_unreadable_unit or has_restricted:
        m_label = "withheld"
        m_detail = "Measure: withheld\nColumn: withheld"
    else:
        raw_col = str(answer.get("column") or ctx.get("column") or "")
        if not raw_col and answer.get("figures"):
            first_fig = (answer.get("figures") or [{}])[0]
            if isinstance(first_fig, dict):
                raw_col = str(first_fig.get("column") or "")
        col_redacted = redact(raw_col)
        measure_name = redact(str(ctx.get("measure") or term or ""))
        m_label = col_redacted or measure_name or ("withheld" if raw_col else "Measure")
        m_lines = []
        if measure_name:
            m_lines.append(f"Measure: {measure_name}")
        if col_redacted:
            m_lines.append(f"Column: {col_redacted}")
        currency = currency_of(answer, engine) if engine is not None else ""
        if currency:
            m_lines.append(f"Currency: {currency}")
        if stopped_after == "measure" and reason:
            m_lines.append(f"Reason: {reason}")
        m_detail = "\n".join(m_lines) if m_lines else m_label
    measure_node = {
        "id": "measure",
        "kind": "measure",
        "label": m_label,
        "state": m_state,
        "time": times["measure"] if m_state == "done" else None,
        "certified": False,
        "detail": m_detail,
    }

    t_state = node_state("tables")
    if has_unreadable_unit or has_restricted:
        t_label = "withheld"
        t_detail = "Reference tables: withheld"
    else:
        ref_tables = [redact(t) for t in _extract_ref_tables(answer)]
        joins = [redact(str(j)) for j in (answer.get("join_path") or []) if j]
        if t_state == "refused":
            t_label = reason or "Join refused"
            t_detail = reason
        else:
            t_label = ", ".join(ref_tables) if ref_tables else "Reference tables"
            t_lines = [f"Reference tables: {', '.join(ref_tables) if ref_tables else 'none'}"]
            if joins:
                t_lines.append(f"Join path: {'; '.join(joins)}")
            t_detail = "\n".join(t_lines)
    tables_node: dict[str, Any] = {
        "id": "tables",
        "kind": "tables",
        "label": t_label,
        "state": t_state,
        "time": times["tables"] if t_state == "done" else None,
        "certified": False,
        "detail": t_detail,
    }
    if t_state == "refused":
        tables_node["reason"] = reason

    a_state = node_state("access")
    if a_state == "refused":
        a_label = reason or "Access refused"
        a_detail = reason or "Access refused"
    elif a_state == "done":
        a_label = f"Access check ({principal})" if principal else "Access check"
        checked_units = [u for u in asked_units if not readable or u in readable]
        a_detail = f"Principal: {principal or 'anonymous'}\nUnits checked: {', '.join(checked_units) if checked_units else 'allowed'}\nVerdict: allowed"
    else:
        a_label = "Access check"
        a_detail = "Waiting"
    access_node: dict[str, Any] = {
        "id": "access",
        "kind": "access",
        "label": a_label,
        "state": a_state,
        "time": times["access"] if a_state == "done" else None,
        "certified": False,
        "detail": a_detail,
    }
    if a_state == "refused":
        access_node["reason"] = reason

    rt_state = node_state("runtime")
    rt_info = answer.get("runtime") if isinstance(answer.get("runtime"), dict) else None
    if rt_info is None and rt_state == "done":
        rt_mgr = getattr(getattr(engine, "w", None), "_runtimes", None)
        if rt_mgr is not None and hasattr(rt_mgr, "runtime_summary"):
            rt_info = rt_mgr.runtime_summary("reference")
        else:
            rt_info = {"id": "reference", "label": "Reference warehouse", "kind": "reference", "dialect": "duckdb"}
    rt_certified = bool(answer.get("runtime_certified"))
    phys_tables = [redact(str(t)) for t in (answer.get("physical_tables") or []) if t]
    if rt_state == "refused":
        rt_label = reason or "Runtime refused"
        rt_detail = reason or "Runtime refused"
    elif rt_state == "done" and rt_info is not None:
        rt_label = str(rt_info.get("label") or rt_info.get("id") or "reference")
        rt_lines = [
            f"Runtime: {rt_info.get('id')} ({rt_label})",
            f"Kind: {rt_info.get('kind') or 'reference'} · Dialect: {rt_info.get('dialect') or 'duckdb'}",
            f"Certified: {'yes' if rt_certified else 'no'}",
        ]
        if phys_tables:
            rt_lines.append(f"Physical tables: {', '.join(phys_tables)}")
        if answer.get("certified_in"):
            rt_lines.append(f"Certified in: {answer['certified_in']}")
        rt_detail = "\n".join(rt_lines)
    else:
        rt_label = str((rt_info or {}).get("label") or (rt_info or {}).get("id") or "Runtime")
        rt_detail = "Waiting for access check"
    runtime_node: dict[str, Any] = {
        "id": "runtime",
        "kind": "runtime",
        "label": rt_label,
        "state": rt_state,
        "time": times["runtime"] if rt_state == "done" else None,
        "certified": rt_certified if rt_state == "done" else False,
        "detail": rt_detail,
    }
    if rt_state == "refused":
        runtime_node["reason"] = reason

    skipped_nodes: list[dict[str, Any]] = []
    for sk in skipped_runtimes:
        if not isinstance(sk, dict) or not sk.get("id"):
            continue
        sk_id = str(sk["id"])
        sk_label = str(sk.get("label") or sk_id)
        sk_reason = redact(str(sk.get("reason") or "skipped"))
        skipped_nodes.append({
            "id": f"runtime:{sk_id}",
            "kind": "runtime",
            "label": sk_label,
            "state": "skipped",
            "time": None,
            "certified": False,
            "reason": sk_reason,
            "detail": f"Skipped runtime: {sk_id} ({sk_label})\nReason: {sk_reason}",
        })

    q_state = node_state("query")
    runtime_sql = str(answer.get("sql") or "")
    if not runtime_sql and isinstance(answer.get("figures"), list):
        fig_sqls = [str(f.get("sql")) for f in answer["figures"] if isinstance(f, dict) and f.get("sql")]
        runtime_sql = ";\n".join(fig_sqls)
    if not runtime_sql and isinstance(answer.get("sql_run"), list) and answer["sql_run"]:
        runtime_sql = ";\n".join(str(s) for s in answer["sql_run"] if s)
    dialect_label = str((rt_info or {}).get("dialect") or "sql")
    if q_state == "refused":
        query_label = reason or "Query refused"
        query_detail = f"{reason}\n\n{runtime_sql}".strip() if runtime_sql else reason
    elif q_state == "done":
        query_label = f"Query ({dialect_label})"
        query_detail = runtime_sql or "SELECT 1"
    else:
        query_label = "Query"
        query_detail = "Waiting for runtime"
    query_node: dict[str, Any] = {
        "id": "query",
        "kind": "query",
        "label": query_label,
        "state": q_state,
        "time": times["query"] if q_state == "done" else None,
        "certified": False,
        "detail": query_detail,
    }
    if q_state == "refused":
        query_node["reason"] = reason

    f_state = node_state("figure")
    currency = currency_of(answer, engine) if engine is not None and not has_unreadable_unit else ""
    fig_certified = bool(
        f_state == "done"
        and behaviour in ("figure", "sum_with_verdict", "cite_certified")
        and (answer.get("runtime_certified") is True or (answer.get("runtime_certified") is None and bool(answer.get("cite") or answer.get("citations"))))
    )
    if f_state == "done":
        if answer.get("figure") is not None:
            fig_label = f"{currency + ' ' if currency else ''}{exact(answer['figure'])}"
        elif isinstance(answer.get("figures"), list) and answer["figures"]:
            parts = [
                f"{f.get('label') or f.get('term') or f.get('id')}: {exact(f.get('value'))}"
                for f in answer["figures"]
                if isinstance(f, dict) and f.get("value") is not None
            ]
            fig_label = "; ".join(parts) if parts else behaviour.replace("_", " ")
        elif isinstance(answer.get("rows"), dict) and answer["rows"]:
            fig_label = f"{len(answer['rows'])} grouped rows"
        else:
            fig_label = behaviour.replace("_", " ") or "Answer"
        fig_detail = redact(str(answer.get("answer_text") or answer.get("reason") or fig_label), protect=protected)
    else:
        fig_label = "Figure"
        fig_detail = "No figure computed"
    figure_node: dict[str, Any] = {
        "id": "figure",
        "kind": "figure",
        "label": fig_label,
        "state": f_state,
        "time": times["figure"] if f_state == "done" else None,
        "certified": fig_certified,
        "detail": fig_detail,
    }

    nodes = [
        q_node,
        terms_node,
        measure_node,
        tables_node,
        access_node,
        runtime_node,
        *skipped_nodes,
        query_node,
        figure_node,
    ]

    checks = guard.get("checks") if isinstance(guard.get("checks"), list) else []
    if checks:
        edge_checks_label = "query & cost checks (" + " · ".join(str(c) for c in checks) + ")"
    else:
        edge_checks_label = "query & cost checks"

    edges: list[dict[str, str]] = [
        {"from": "question", "to": "terms", "label": ""},
        {"from": "terms", "to": "measure", "label": ""},
        {"from": "measure", "to": "tables", "label": ""},
        {"from": "tables", "to": "runtime", "label": ""},
        {"from": "access", "to": "runtime", "label": "access check"},
    ]
    for sk_node in skipped_nodes:
        edges.append({"from": "tables", "to": sk_node["id"], "label": "skipped"})
    edges.extend([
        {"from": "runtime", "to": "query", "label": edge_checks_label},
        {"from": "query", "to": "figure", "label": ""},
    ])

    return {"nodes": nodes, "edges": edges}



GRAPH_EVENTS = ("node_added", "state_changed", "edge_added", "edge_changed", "graph_summary", "plan", "context", "stage")
STAGE_FIELDS = ("ms", "llm_calls", "tokens_in", "tokens_out")


def edge_key(link: dict) -> tuple:
    return link.get("from"), link.get("to"), link.get("type"), link.get("label")


def graph_node(event: dict) -> dict | None:
    node = event.get("node")
    return node if event.get("event") in ("node_added", "state_changed") and isinstance(node, dict) and "type" in node else None


def graph_edge(event: dict) -> dict | None:
    link = event.get("edge")
    return link if event.get("event") in ("edge_added", "edge_changed") and isinstance(link, dict) and "type" in link else None


class PathStreamRecorder:
    """One answer's live events, shared by a direct stream and a background run: each graph node, edge, the plan and
    the context sent once (node_added, edge_added), and a later change as its own event (state_changed, edge_changed);
    each stage as it starts and, with its time so far across the run's passes, as it ends; the nodes the stage under
    way reads, and the Runtime while its query runs, shown running until it ends; the read-but-not-used count only
    once the plan is chosen. sent holds the events a run sent before this pass; a later pass, which replays the
    pipeline, holds its graph until its replay reaches a stage not yet done, sends only what changed, and from there
    streams live again. parts is the run's list of earlier
    passes' traces, which a pause adds to. Its finish (emit_final) makes what was sent agree with the answer: its
    graph_path and its run_trace's stages."""

    def __init__(self, question: str, emit, sent=(), parts: list | None = None):
        self.emit = emit
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.stages: dict[str, dict] = {}
        self.plan = self.context = self.unused = None
        for event in sent:
            self._remember(event)
        self.later = any(event.get("event") in GRAPH_EVENTS for event in sent)
        self.caught_up = not self.later
        self.parts = parts if parts is not None else []
        self.base = list(self.parts)
        self.open = next((name for name, held in self.stages.items() if held.get("state") == "running"), None)
        self.running = {nid for nid, node in self.nodes.items() if node.get("state") == "running"}
        self.real: dict[str, dict] = {}
        self.held_nodes: dict[str, dict] = {}
        self.held_edges: dict[tuple, dict] = {}
        self.held_unused = None
        self.settled = self.plan is not None

    def _remember(self, event: dict) -> None:
        kind = event.get("event")
        if (node := graph_node(event)) is not None:
            self.nodes[node["id"]] = node
        elif (link := graph_edge(event)) is not None:
            self.edges[edge_key(link)] = link
        elif kind == "graph_summary":
            self.unused = event.get("unused")
        elif kind == "plan":
            self.plan = event.get("plan")
        elif kind == "context":
            self.context = event.get("context")
        elif kind == "stage" and event.get("stage"):
            self.stages[event["stage"]] = event

    def _send(self, event: dict) -> None:
        self._remember(event)
        self.emit(event)

    def emit_initial(self) -> None:
        """The first recorded, redacted read supplies the initial nodes."""

    def publish(self, event: dict) -> None:
        """An event from the pipeline (the graph's reads, the plan, the context, warehouse progress), sent as this
        recorder's rules say."""
        kind = event.get("event")
        if (node := graph_node(event)) is not None:
            self.real[node["id"]] = node
            if self.later:
                self.held_nodes[node["id"]] = node
                return
            if self.open is not None and node.get("state") == "done":
                shown = node["id"] in self.running and self.nodes.get(node["id"], {}).get("state") == "running"
                self.running.add(node["id"])
                if shown:
                    return
            self._show_node(node)
        elif (link := graph_edge(event)) is not None:
            if self.later:
                self.held_edges[edge_key(link)] = link
            else:
                self._show_edge(link)
        elif kind == "graph_summary":
            self.held_unused = event.get("unused")
            if self.settled and not self.later:
                self._show_unused()
        elif kind in ("plan", "context"):
            if event.get(kind) != getattr(self, kind):
                self._send(event)
            if kind == "plan":
                self.settled = True
                if not self.later:
                    self._show_unused()
        else:
            self.emit(event)

    def _show_node(self, node: dict) -> None:
        shown = {**node, "state": "running"} if node["id"] in self.running and node.get("state") == "done" else node
        prior = self.nodes.get(node["id"])
        if prior == shown:
            return
        if prior is None:
            self._send({"event": "node_added", "node": shown})
        else:
            self._send({"event": "state_changed", "id": shown["id"], "state": shown["state"], "node": shown})

    def _show_edge(self, link: dict) -> None:
        prior = self.edges.get(edge_key(link))
        if prior != link:
            self._send({"event": "edge_added" if prior is None else "edge_changed", "edge": link})

    def _show_unused(self) -> None:
        if self.held_unused is not None and self.held_unused != self.unused:
            self._send({"event": "graph_summary", "unused": self.held_unused})

    def _flush(self) -> None:
        for node in self.held_nodes.values():
            self._show_node(node)
        for link in self.held_edges.values():
            self._show_edge(link)
        if self.settled:
            self._show_unused()
        self.held_nodes, self.held_edges = {}, {}

    def _overlay(self, target: set) -> None:
        for nid in sorted(self.running - target):
            self.running.discard(nid)
            real = self.real.get(nid) or (self.nodes.get(nid) and {**self.nodes[nid], "state": "done"})
            if real:
                self._show_node(real)
        for nid in sorted(target - self.running):
            self.running.add(nid)
            node = self.real.get(nid) or self.nodes.get(nid)
            if node and node.get("state") in ("done", "running"):
                self._show_node({**node, "state": "done"})

    def _executing(self) -> set:
        known = {**self.nodes, **self.real}
        return {nid for nid, node in known.items() if node.get("type") == "Runtime" and node.get("state") in ("done", "running")}

    def _totals(self) -> list[dict]:
        from kge.pipeline.trace import current
        from kge.services.ask.engine.runtrace import merged_stages
        trace = current()
        now = [{"stages": trace.stage_totals()}] if trace is not None and hasattr(trace, "stage_totals") else []
        return merged_stages([*self.base, *now])

    def _stage_done(self, stage: dict) -> None:
        event = {"event": "stage", "stage": stage["stage"], "state": "done", **{key: stage.get(key) for key in STAGE_FIELDS}}
        if self.stages.get(stage["stage"]) != event:
            self._send(event)

    def _close_stage(self) -> None:
        name, self.open = self.open, None
        if name is not None:
            held = next((stage for stage in self._totals() if stage["stage"] == name), None)
            if held is not None:
                self._stage_done(held)

    def on_stage(self, stage_name: str) -> None:
        from kge.pipeline.trace import check_cancelled
        check_cancelled()
        if not self.caught_up:
            if self.stages.get(stage_name, {}).get("state") == "done":
                return
            self.caught_up = True
            self._flush()
            self.later = False
        if stage_name == self.open:
            return
        target = self._executing() if stage_name == "execute" else set()
        self._close_stage()
        self._overlay(target)
        self.open = stage_name
        if self.stages.get(stage_name, {}).get("state") != "running":
            self._send({"event": "stage", "stage": stage_name, "state": "running"})

    def pause(self) -> None:
        """The pass waits for the warehouse: what it holds is sent, and each stage it replayed with its time so far;
        the stage under way stays running until a later pass ends it."""
        from kge.services.ask.engine.runtrace import merged_stages
        self._flush()
        for stage in merged_stages(self.parts):
            if stage["stage"] != self.open and self.stages.get(stage["stage"], {}).get("state") == "done":
                self._stage_done(stage)

    def emit_final(self, answer: dict[str, Any]) -> None:
        self._flush()
        self._close_stage()
        self._overlay(set())
        graph_path = answer.get("graph_path")
        if isinstance(graph_path, dict):
            for node in graph_path.get("nodes", []):
                self.real[node["id"]] = node
                self._show_node(node)
            for link in graph_path.get("edges", []):
                self._show_edge(link)
            if graph_path.get("unused", 0) != self.unused:
                self._send({"event": "graph_summary", "unused": graph_path.get("unused", 0)})
        elif isinstance(answer.get("path"), dict):
            for node in answer["path"].get("nodes", []):
                self.emit({"event": "node_added", "node": {**node, "state": "pending", "time": None}})
            for edge in answer["path"].get("edges", []):
                self.emit({"event": "edge_added", "edge": edge})
            for node in answer["path"].get("nodes", []):
                self.emit({"event": "state_changed", "id": node["id"], "state": node["state"], "node": node})
        stages = (answer.get("run_trace") or {}).get("stages") if isinstance(answer.get("run_trace"), dict) else None
        for stage in stages or []:
            if isinstance(stage, dict) and stage.get("stage"):
                self._stage_done(stage)
        self.emit({"event": "answer", "answer": answer})
