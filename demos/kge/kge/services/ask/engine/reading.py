"""An answer's own fields read as words: its term, its period, its currency, what its rows are grouped by, and whether its
query counts. The display block (present.py) and the readable answer (wording.py) read an answer the same way."""
from __future__ import annotations

import re

from kge.services.ask.engine.core import month_name
from kge.services.ask.engine.question import GRAIN_NAMES, period_words

SELECTED = re.compile(r"(?is)\bselect\s+(.*?)\bfrom\b")


def counts(sql: str | None) -> bool:
    """Whether a query counts rows: its select list counts, so its figure is in no currency."""
    m = SELECTED.search(sql or "")
    return bool(m and re.search(r"\bcount\s*\(", m.group(1), re.I))


def term_of(a: dict) -> str:
    parsed = next((t for t in a.get("trace") or [] if t.get("step") == "parse"), {})
    term = (a.get("context") or {}).get("term") or parsed.get("term") or ""
    return "" if term.startswith("__") else term


def period_of(a: dict) -> str:
    """The period asked, in words: from the question's parse (kind and value), else the months the answer read."""
    if a.get("period_label"):
        return a["period_label"]
    said = next((t.get("period") for t in a.get("trace") or [] if t.get("step") == "parse"), None)
    if said:
        kind, _, value = said.partition(" ")
        return month(value) if kind == "month" else period_words(kind, value) if value else kind
    months = a.get("months") or []
    return month(months[0]) if len(months) == 1 else f"{month(months[0])} to {month(months[-1])}" if months else ""


def month(iso: str) -> str:
    return f"{month_name(int(iso[5:7]))} {iso[:4]}" if re.match(r"\d{4}-\d\d", iso or "") else iso


def group_phrase(a: dict, e) -> str:
    """The pack's phrase for what the rows are grouped by: the one the question says, else the first the pack gives."""
    col, q = a.get("group_by") or "", (a.get("question") or "").lower()
    context = a.get('context') or {}
    semantics = getattr(e, 'semantics', None)
    ctx = getattr(semantics, 'contexts', {}).get(context.get('id') or context.get('context'))
    bindings = (ctx.bindings or {}) if ctx is not None else {}
    phrases = [p for p, c in e.GROUP_BY.items() if c == col or c.endswith("." + col)
               or bindings.get(c) == col or bindings.get(c, '').endswith('.' + col)]
    said = sorted((p for p in phrases if p in q), key=len, reverse=True)
    return (said or phrases or [f"by {GRAIN_NAMES[col].lower()}" if col in GRAIN_NAMES else f"by {col}"])[0]


def ranking_direction(a: dict) -> str | None:
    """The direction the answer's plan ranks its first ordered output in (asc, desc or both), or None unranked."""
    history = a.get("history") or []
    plan = history[-1].get("plan") if history and isinstance(history[-1], dict) else None
    order = plan.get("order") or [] if isinstance(plan, dict) else []
    return order[0].get("direction") if order and isinstance(order[0], dict) else None


def dimension_value(row, index, rows=()):
    """A row's dimension as people read it: a period in words; a code with a declared name as the name, with the code
    added where another of rows gives a different code the same name, as "name (code)"; any other value as it is."""
    text = row.get("dimension_text", ())
    if index < len(text) and text[index] is not None:
        return text[index]
    value = row["dimensions"][index].get("value")
    label = dimension_label(row, index)
    if label is None:
        return value
    if any(dimension_label(r, index) == label and r["dimensions"][index].get("value") != value
           for r in rows if index < len(r["dimensions"])):
        return f"{label} ({value})"
    return label


def dimension_label(row, index):
    labels = row.get("dimension_labels", ())
    return labels[index] if index < len(labels) else None


def grouped(a: dict) -> list[tuple[str, object]]:
    """A one-way breakdown's rows as they read: a period breakdown in time order, each period in words; any other in
    its ranking's direction, the leader first (smallest first when the plan ranks ascending, as a "least" question
    does), else largest first, under its key; a row with no value last."""
    rows = a.get("rows") or {}
    result = [r for r in (a.get("result") or {}).get("rows", []) if len(r["dimensions"]) == 1 and not r.get("total")]
    words = {str(r["dimensions"][0].get("value")): str(dimension_value(r, 0, result)) for r in result}
    if (a.get("group_by") or "") not in GRAIN_NAMES:
        sign = 1 if ranking_direction(a) == "asc" else -1
        return sorted(((named(words, str(k)), v) for k, v in rows.items()), key=lambda kv: (kv[1] is None, sign * (kv[1] or 0)))
    return [(words.get(str(k), str(k)), v) for k, v in sorted(rows.items())]


def named(words, key):
    """A grouped row's key as it reads: its name where the key is a code, after a ranked row's "top 1: " mark."""
    mark, _, code = key.rpartition(": ")
    return (mark + ": " if mark else "") + words.get(code, code)


def currency_of(a: dict, e) -> str | None:
    """The figure's currency: its measure's (a context answer), its certified column's (the planner's part, the agent's
    figure); none where the query counts, and never a guess."""
    ctx = a.get("context") or {}
    outputs = [c for c in (a.get("result") or {}).get("columns", []) if c["kind"] != "dimension"]
    if len(outputs) == 1:
        return outputs[0]["currency"]
    if ctx.get("count") or counts(a.get("sql")):
        return None
    if ctx.get("measure"):
        rows = e.g.q("MATCH (m:Measure {id: $m}) RETURN m.currency AS currency", {"m": ctx["measure"]})
        return (rows[0]["currency"] or None) if rows else None
    part = next((p for p in (a.get("parts") if isinstance(a.get("parts"), list) else a.get("figures") or []) if p.get("unit") == a.get("unit")), {})
    return part.get("currency") or None
