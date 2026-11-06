"""The rich answer: a display block for the page, built by code from an answer that has already run (issue #52, R2).

An add-on, never part of the answer. The page's Rich answers switch, off on every page load, asks POST /answer or /ask
for ?display=1, and only then does kge/app.py add present(answer, engine) to the answer, as display. The A/B
harness, eval, margin and parity call the engine and the agent, never the routes, and nothing of theirs imports this
module, so their records are the same with it as without it. present() reads the answer and changes nothing in it.

display v1, JSON only:

  headline    {text, by}: one sentence. The answer's own text where it has one (a combined answer's, or the reference
              agent's on a model or the mock, which the agent's number check has passed); else, for an answer with
              figures, one written here from the answer's own values, which this module checks as the agent checks a
              model's (every number one the answer holds, every [n] one of its citations); else its reason. by says who
              wrote it: template, model:<provider:model> or mock:<model>; the agent's "template (model text ...)" is
              relabelled the mock's on the mock, which is no model.
  table       the figures, or null for an answer with none: the grouped rows, largest first; the parts across units, in
              the answer's order, with each part's currency, grain and calendar and the citation it is bound to; one
              figure; or a combined answer's figures. Values as the answer holds them, unrounded; every row kept; a
              total only where addable() holds; currency null where the query counts; cite, the citation bound to
              every row of a grouped answer.
  chart       a bar chart drawn by code from two of the table's columns (x, y), with no values of its own, so it cannot
              say what the table does not: kind, x, y, sort, max_bars, by. null unless addable() holds; chart_note then
              says why, in the answer's own words where it has them.
  queries     every query the answer ran or tried, in order, with the gate's verdict: the release rule's query (gate
              null: the gate passed it, or nothing after it would have run, and its checks are not kept), the figure's,
              each part's, and each try a model or the mock made.
  checked     {line, passed, about}: the one line naming the checked queries, as "gate passed · 2 checked queries · SQL
              by planner · text by template"; about lists each, for the line's tooltip. null where no query ran.
  notes       the answer's own notes, as it gives them (as reported, the latest released month, a certified join, no
              period asked): the card without the switch shows them, so the rich card does too.

Code decides everything here: which rows and columns, whether there is a chart or a total, the order and the cap. No model
is asked anything. present() never raises: anything it cannot read gives {v: 1, error}, and the page draws the card it
draws without the switch.
"""
from __future__ import annotations

import re

from kge.services.ask.engine.agent import planned_figures, supported, template, wording
from kge.services.ask.engine.combined import fmt, names
from kge.services.ask.engine.reading import counts, currency_of, group_phrase, period_of, term_of

V = 1
MAX_BARS = 12
SUMS = ("sum_with_verdict", "sum_without_verdict", "parts_with_reason")
NO_METADATA = "no measure metadata in this arm"
RELEASE = "the release rule's query: the gate passed it, or nothing after it would have run; its checks are not kept"


def present(answer: dict, engine) -> dict:
    """The display block of answer, built by code; {v: 1, error} for one it cannot read, never an exception."""
    try:
        return build(answer, engine)
    except Exception as exc:  # noqa: BLE001
        return {"v": V, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def build(a: dict, e) -> dict:
    if not isinstance(a, dict):
        raise TypeError("an answer is a JSON object")
    if "plan" in a and not isinstance(a.get("plan"), (list, type(None))):
        raise TypeError("an answer plan is a list")
    table, kind = table_of(a, e)
    ok, note = addable(a, table, kind)
    chart = None
    if ok:
        chart = {"kind": "bar", "x": table["columns"][0]["key"], "y": "value", "sort": "desc", "max_bars": MAX_BARS, "by": "code"}
        if kind == "parts":
            table["total"] = {"label": "Total", "value": a["figure"]}
    head = headline(a, e, table, kind)
    qs = queries(a)
    return {"v": V, "headline": head, "table": table, "chart": chart, "chart_note": None if ok else note, "queries": qs,
            "checked": checked(qs, head), "notes": [str(n) for n in a.get("notes") or []]}


def table_of(a: dict, e) -> tuple[dict | None, str | None]:
    b = a.get("behaviour")
    if b == "combined":
        return combined_table(a), "figures"
    if b == "figure" and isinstance(a.get("rows"), dict):
        return grouped_table(a, e), "grouped"
    if b in SUMS and parts_of(a):
        return parts_table(a, e), "parts"
    if b == "figure" and a.get("figure") is not None:
        return figure_table(a, e), "figure"
    return None, None


def number(v):
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
        raise TypeError(f"a figure is a number, not {v!r}")
    return v


def column(key: str, label: str, kind: str = "text", currency: str | None = None) -> dict:
    return {"key": key, "label": label, "type": kind, "currency": currency}


def grouped_table(a: dict, e) -> dict:
    rows = sorted(((str(k), number(v)) for k, v in a["rows"].items()), key=lambda kv: (kv[1] is None, -(kv[1] or 0)))
    term, period, phrase, cur = term_of(a), period_of(a), group_phrase(a, e), currency_of(a, e)
    label = phrase[3:].capitalize() if phrase.startswith("by ") else phrase
    cites = {f.get("cite") for f in a.get("figures") or []} - {None}
    cite = cites.pop() if len(cites) == 1 else None
    cols = [column("group", label), column("value", term, "number", cur)]
    if cite is not None:
        cols.append(column("source", "Source", "cite"))
    return {"caption": " · ".join(x for x in (term, period, phrase, cur) if x), "cite": cite, "columns": cols,
            "rows": [[name, value] + ([cite] if cite is not None else []) for name, value in rows], "total": None}


def parts_table(a: dict, e) -> dict:
    parts, cites = parts_of(a), {f["unit"]: f.get("cite") for f in a.get("figures") or []}
    conv = any(p.get("conversion") not in (None, "", "none") for p in parts)
    note = any(value_of(p) is None for p in parts)
    has_source = any(cites.get(p["unit"]) is not None for p in parts)
    cols = [column("unit", "Unit"), column("value", "Figure", "number"), column("currency", "Currency"), column("grain", "Grain"),
            column("calendar", "Calendar")] + ([column("conversion", "Conversion")] if conv else []) \
        + ([column("source", "Source", "cite")] if has_source else []) + ([column("note", "Note", "note")] if note else [])
    rows = []
    for p in parts:
        v = number(value_of(p))
        cell = {"unit": e.UNIT_LABEL.get(p["unit"], p["unit"]), "value": v, "currency": None if counts(p.get("sql")) else p.get("currency") or None,
                "grain": p.get("grain") or None, "calendar": p.get("calendar") or None, "conversion": p.get("conversion") or None,
                "source": cites.get(p["unit"]), "note": None if v is not None else p.get("reason") or p.get("error") or "no value"}
        rows.append([cell[c["key"]] for c in cols])
    calendars = {p.get("calendar") for p in parts if value_of(p) is not None and p.get("calendar")}
    period = period_of(a) if len(calendars) <= 1 else ""
    return {"caption": " · ".join(x for x in (term_of(a), period, "by unit") if x), "cite": None, "columns": cols, "rows": rows,
            "total": None}


def figure_table(a: dict, e) -> dict:
    f = (a.get("figures") or [{}])[0]
    cur = currency_of(a, e)
    cols = [column("unit", "Unit"), column("value", "Figure", "number", cur), column("column", "Column")]
    if f.get("cite") is not None:
        cols.append(column("source", "Source", "cite"))
    row = {"unit": e.UNIT_LABEL.get(a.get("unit"), a.get("unit")), "value": number(a["figure"]), "column": a.get("column"), "source": f.get("cite")}
    return {"caption": " · ".join(x for x in (term_of(a), period_of(a), cur) if x), "cite": None, "columns": cols,
            "rows": [[row[c["key"]] for c in cols]], "total": None}


def combined_table(a: dict) -> dict:
    has_source = any(f.get("cite") is not None for f in a["figures"])
    cols = [column("figure", "Figure"), column("value", "Value", "number"), column("currency", "Currency")]
    if has_source:
        cols.append(column("source", "Source", "cite"))
    rows = [[f"{f['label']} ({f['kind']})", number(f["value"]), None if f["kind"] == "count" else f.get("currency") or None]
            + ([f.get("cite")] if has_source else []) for f in a["figures"]]
    return {"caption": " · ".join(x for x in (a.get("unit_label"), a.get("period")) if x), "cite": None, "columns": cols, "rows": rows,
            "total": None}


def parts_of(a: dict) -> list[dict]:
    """The parts across units: the planner's plan, or the reference agent's figures on a model or the mock."""
    if isinstance(a.get("plan"), list):
        return a["plan"]
    return (a.get("figures") or []) if isinstance(a.get("parts"), dict) else []


def value_of(p: dict):
    return p.get("value") if p.get("status", "ok") == "ok" else None


def addable(a: dict, table: dict | None, kind: str | None) -> tuple[bool, str | None]:
    """(whether the table is charted, and a total shown; why not). Grouped rows: two or more with a value. Parts across
    units: only where the answer adds them (sum_with_verdict), the code's verdict says they add (commensurable) and every
    part's own grain, currency and calendar are given and equal, with no conversion; so never on a reply's claim alone.
    A single figure, or a combined answer's figures of different kinds, is never charted."""
    if kind == "grouped":
        count = sum(r[1] is not None for r in table["rows"])
        return (True, None) if count >= 2 else (False, "fewer than two groups have a value")
    if kind != "parts":
        return False, None
    why = differs(parts_of(a))
    if a["behaviour"] == "sum_with_verdict" and a.get("commensurable") is True and why is None and a.get("figure") is not None:
        return True, None
    if a["behaviour"] == "sum_with_verdict":
        return False, f"the reply's total is not supported by the graph's measure metadata: {why or 'no verdict that the parts add'}"
    return False, a.get("reason") or why


def differs(parts: list[dict]) -> str | None:
    """Why the parts' own metadata says they do not add, or None where it says they do."""
    ok = [p for p in parts if value_of(p) is not None]
    if len(ok) < 2:
        return "fewer than two parts have a value"
    if not all(p.get("grain") and p.get("currency") and p.get("calendar") for p in ok):
        return NO_METADATA
    if any(counts(p.get("sql")) for p in ok):
        return "counts of different populations are reported as parts, not summed"
    why = [f"{k} differs" for k in ("grain", "calendar", "currency") if len({p[k] for p in ok}) > 1]
    if {p.get("conversion") for p in ok} != {"none"}:
        why.append("a conversion is involved")
    return "; ".join(why) or None


def headline(a: dict, e, table: dict | None, kind: str | None) -> dict:
    """The sentence. An answer's own text has been through its own number check already; a sentence of this code's
    own that carries a number the answer does not hold is never shown."""
    mock = (a.get("agent") or {}).get("mode") == "mock"
    if a.get("answer_text"):
        text, by = a["answer_text"], a.get("phrased_by") or "template"
    elif table is None:
        text, by = a.get("reason") or "", "template"
    else:
        text, by = written(a, e, table, kind), "template"
        stray, bad = wording(text, supported(a, a.get("question") or "") | held(a), {c["n"] for c in a.get("citations") or []},
                             known(a, table))
        if stray or bad:
            text, by = "The figures are in the table, as the queries returned them.", "template (its sentence did not pass the number check)"
    if mock:
        text, by = text.replace("the model's reason", "the mock's reason"), by.replace("model text", "mock text")
    return {"text": text, "by": by}


def written(a: dict, e, table: dict, kind: str) -> str:
    if kind == "grouped":
        valued = [r for r in table["rows"] if r[1] is not None]
        if len(valued) > 5:
            named = f"the largest {valued[0][0]} {fmt(valued[0][1])}, the smallest {valued[-1][0]} {fmt(valued[-1][1])}; the table lists every one"
        else:
            named = "; ".join(f"{r[0]} {fmt(r[1])}" for r in valued) or "no group has a value"
        cur = table["columns"][1]["currency"]
        lead = term_of(a) + (f" for {period_of(a)}" if period_of(a) else "") + f" {group_phrase(a, e)}" + (f", in {cur}" if cur else "")
        provenance = f" [{table['cite']}]" if table["cite"] else source_of(a)
        return f"{lead}: {named}{provenance}."
    figures = a.get("figures") or planned_figures(a)
    if a["behaviour"] == "sum_without_verdict":
        mark = lambda f: f" [{f['cite']}]" if f.get("cite") else ""  # noqa: E731
        parts = "; ".join(f"{e.UNIT_LABEL.get(f['unit'], f['unit'])} {fmt(f['value'])}{mark(f)}" for f in figures if f.get("value") is not None)
        return f"{fmt(a['figure'])}, summed as written, with no verdict on whether the parts add: {parts}."
    return template({**a, "figures": figures}, e)


def source_of(a: dict) -> str:
    """The ordinary grouped card's unit and column, when no document citation supplies the provenance."""
    source = " · ".join(str(v) for v in (a.get("unit"), a.get("column")) if v)
    return f" ({source})" if source else ""


def held(a: dict) -> set[float]:
    """Every value the answer holds: its figure, its grouped rows and the codes they are grouped by where a code is a
    number (a source system's id, 100), its parts and its figures."""
    vals = [a.get("figure"), *(a.get("rows") or {}).values(), *[f.get("value") for f in a.get("figures") or []]]
    vals += [float(k) for k in (a.get("rows") or {}) if isinstance(k, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", k)]
    vals += [p.get("value") for p in (a.get("plan") if isinstance(a.get("plan"), list) else []) if isinstance(p, dict)]
    vals += list(a["parts"].values()) if isinstance(a.get("parts"), dict) else [p.get("value") for p in a.get("parts") or [] if isinstance(p, dict)]
    return {float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)}


def known(a: dict, table: dict) -> set[str]:
    """The names a sentence of the code's own may carry, digits and all: the question's, the reason's, what the answer cites,
    the columns it measures, the table's caption (the period among it) and its row labels."""
    docs = [d for d in (a.get("citations") or []) + (a.get("references") or []) if isinstance(d, dict)]
    return names(a.get("question") or "", a.get("reason") or "", a.get("column") or "", table["caption"],
                 *[f"{d.get('doc') or ''} {d.get('title') or ''}" for d in docs], *[f.get("column") or "" for f in a.get("figures") or []],
                 *[str(r[0]) for r in table["rows"]]) | set(table["caption"].split(" · "))


def verdict(g: dict | None) -> dict | None:
    if not isinstance(g, dict) or "passed" not in g:
        return None
    return {"passed": bool(g["passed"]), "failed_at": g.get("failed_at"), "problems": list(g.get("problems") or []), "checks": list(g.get("checks") or [])}


def queries(a: dict) -> list[dict]:
    """Every query the answer ran or tried, in order, each with the gate's verdict."""
    by = (a.get("agent") or {}).get("sql_source") or a.get("sql_source") or "planner"
    one = lambda role, unit, sql, gate, ran, **more: {"role": role, "unit": unit, "column": more.get("column"), "by": more.get("by", by),  # noqa: E731
                                                      "try": more.get("try"), "sql": sql, "gate": gate, "ran": ran, "note": more.get("note")}
    out = []
    plan = a["plan"] if isinstance(a.get("plan"), list) else a["parts"] if isinstance(a.get("parts"), list) and not a.get("sql") else []
    if a.get("attempts"):
        out = [one("try", t.get("unit"), t.get("sql"), verdict(t), bool(t.get("passed")), by=t.get("source") or by, **{"try": t.get("try")})
               for t in a["attempts"]]
    elif plan:
        for p in plan:
            ok = p.get("status") == "ok"
            out.append(one("part", p.get("unit"), p.get("sql"), verdict(p.get("guard")), ok and bool(p.get("sql")), column=p.get("column"),
                           note=None if ok else p.get("reason")))
    else:
        ran = list(a.get("sql_run") or [])
        if a.get("sql") and a["sql"] not in ran:
            ran.append(a["sql"])
        guard = a.get("guard") or next((p.get("guard") for p in a.get("parts") or [] if isinstance(p, dict) and p.get("sql") == a.get("sql")), None)
        for sql in ran:
            if sql == a.get("sql"):
                g = verdict(guard)
                role = "release" if refused_release(a, sql, g) else "figure"
                out.append(one(role, a.get("unit"), sql, g, bool(g and g["passed"]), column=a.get("column"),
                               note=RELEASE if role == "release" else None))
            else:
                out.append(one("release", a.get("unit"), sql, None, True, note=RELEASE))
    return [{"n": n, **q} for n, q in enumerate(out, 1)]


def refused_release(a: dict, sql: str, guard: dict | None) -> bool:
    """A refused context release-driver query precedes, and therefore cannot be, the aggregate figure query."""
    return bool(a.get("context") and guard and not guard["passed"] and not re.search(r"\b(sum|count|avg|min|max)\s*\(", sql, re.I))


def checked(qs: list[dict], head: dict) -> dict | None:
    """The one line naming the checked queries: the gate's verdict, how many it checked, who wrote the SQL and the text.
    The release rule's query counts as checked: it goes through run_plan, so one that succeeded passed the gate."""
    gated = [q for q in qs if q["gate"] is not None or q["role"] == "release"]
    if not gated:
        return None
    refused = [q for q in gated if q["gate"] is not None and not q["gate"]["passed"]]
    n = len(gated)
    line = (f"gate refused {len(refused)}" if refused else "gate passed") + f" · {n} checked {'query' if n == 1 else 'queries'}" \
        + f" · SQL by {', '.join(dict.fromkeys(q['by'] for q in gated))} · text by {head['by']}"
    say = lambda q: q["note"] if q["gate"] is None else "passed" if q["gate"]["passed"] else "refused: " + "; ".join(q["gate"]["problems"])  # noqa: E731
    return {"line": line, "passed": not refused,
            "about": "; ".join(f"{q['n']}. {q['role']}{' ' + q['unit'] if q['unit'] else ''}" + (f" (try {q['try']})" if q["try"] else "")
                               + f": {say(q)}" for q in gated)}
