"""An answer's own fields read as words: its term, its period, its currency, what its rows are grouped by, and whether its
query counts. The display block (present.py) and the readable answer (wording.py) read an answer the same way."""
from __future__ import annotations

import re

from kge.services.ask.engine.core import month_name

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
    said = next((t.get("period") for t in a.get("trace") or [] if t.get("step") == "parse"), None)
    if said:
        kind, _, value = said.partition(" ")
        return month(value) if kind == "month" else value or kind
    months = a.get("months") or []
    return month(months[0]) if len(months) == 1 else f"{month(months[0])} to {month(months[-1])}" if months else ""


def month(iso: str) -> str:
    return f"{month_name(int(iso[5:7]))} {iso[:4]}" if re.match(r"\d{4}-\d\d", iso or "") else iso


def group_phrase(a: dict, e) -> str:
    """The pack's phrase for what the rows are grouped by: the one the question says, else the first the pack gives."""
    col, q = a.get("group_by") or "", (a.get("question") or "").lower()
    phrases = [p for p, c in e.GROUP_BY.items() if c == col or c.endswith("." + col)]
    said = sorted((p for p in phrases if p in q), key=len, reverse=True)
    return (said or phrases or [f"by {col}"])[0]


def currency_of(a: dict, e) -> str | None:
    """The figure's currency: its measure's (a context answer), its certified column's (the planner's part, the agent's
    figure); none where the query counts, and never a guess."""
    ctx = a.get("context") or {}
    if ctx.get("count") or counts(a.get("sql")):
        return None
    if ctx.get("measure"):
        rows = e.g.q("MATCH (m:Measure {id: $m}) RETURN m.currency AS currency", {"m": ctx["measure"]})
        return (rows[0]["currency"] or None) if rows else None
    part = next((p for p in (a.get("parts") if isinstance(a.get("parts"), list) else a.get("figures") or []) if p.get("unit") == a.get("unit")), {})
    return part.get("currency") or None
