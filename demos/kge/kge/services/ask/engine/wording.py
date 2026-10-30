"""The readable answer of every behaviour: answer_text, worded by a template from the answer's own fields.

A combined answer brings its own text (kge/services/ask/engine/combined.py, phrase: the template, or a model's wording
that passed the number and citation checks). Every other answer is worded here, by code, after it has run, and never by
a model: phrased_by is "template". The answer is otherwise unchanged, so whatever reads the engine's dicts (the A/B
harness, the margin, parity) reads what it read before.

What the text says, by behaviour:

  a figure       its value as the answer holds it, unrounded; its currency, where the query does not count; the term
                 and the column; the period; and a [n] marker for each citation it is bound to
  parts or sums  each unit's value, the total where the answer adds them, and the reason it gives
  a definition   what is cited, each with its marker where the answer numbers its citations, and what is not cited
  a refusal      what was refused and why, and whom to ask, by role (the data steward, or the business team that owns the
                 data), never by name
  an abstention  what is missing, and whom to ask, by role

No text names a unit the caller may not read, nor a qualified table or column of such a unit, nor a snake_case one
only such a unit holds: each is withheld by Hidden.withheld wherever an answer's own reason would name one. Every number in a text is one the answer holds, and every
[n] one of its citations (stray_numbers, bad_markers: the checks the tests hold every answer to).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from kge.services.ask.engine.combined import names, numbers
from kge.services.ask.engine.reading import currency_of, group_phrase, period_of, term_of

TEMPLATE = "template"
WITHHELD = "(withheld)"
MARKER = re.compile(r"\[(\d+)\]")
DOC_EDGE = r"(?<![\w-]){}(?![\w-])"
NAME_EDGE = r"(?<![\w-]){}(?![\w-])"
UNREADABLE = r"may not read [\w-]+(?:(?:\s*,\s*|\s+and\s+)[\w-]+)*"


@lru_cache(maxsize=8192)
def edge(template: str, value: str, flags: int = 0) -> re.Pattern:
    """The compiled pattern for value at a word edge (DOC_EDGE or NAME_EDGE), compiled once per value: a catalogue
    names more columns than re's own cache of 512 holds, so withholding them one re.sub at a time recompiled every
    pattern on every answer. Only the catalogue's names and documents come here; a question's own values never do, so
    the cache does not grow with what people ask."""
    return re.compile(template.format(re.escape(value)), flags)

STEWARD = "Ask the data steward if it should be answerable."
OWNER = "Ask the business team that owns that data if you need access."
IDENTITY = "Ask the business team that manages your access for an identity that may ask questions."
POLICY = "The data steward can explain the policy that governs it."
ROUTED = "The data steward reviews it from the queue."

FIGURES = ("figure",)
SUMS = ("sum_with_verdict", "sum_without_verdict", "parts_with_reason")
DEFINITIONS = ("cite", "cite_certified", "glossary_definition")
REFUSALS = ("refuse", "blocked_access")
ABSTENTIONS = ("abstain", "abstain_route_steward", "abstain_with_reason")
BEHAVIOURS = ("combined", *FIGURES, *SUMS, *DEFINITIONS, *REFUSALS, *ABSTENTIONS)


def worded(a: dict, e) -> dict:
    """The answer with its answer_text and phrased_by: its own where it has them (a combined answer's), else the template's.
    e: the Engine that answered, for the units' labels and what the caller may read."""
    hidden = Hidden.of(e, a.get("principal") or "")
    protected = protected_values(a)
    if a.get("reason"):
        a = {**a, "reason": hidden.withheld(a["reason"], protected)}
    if a.get("answer_text"):
        return a
    return {**a, "answer_text": template(a, e), "phrased_by": TEMPLATE}


def protected_values(a: dict) -> list[str | None]:
    """The caller's wording and semantic answer values that identifier redaction must keep intact."""
    protected = [a.get("question"), a.get("term"), a.get("definition")]
    question_words = {word.lower() for word in re.findall(r"[\w-]+", a.get("question") or "")}
    for phrase in re.findall(r"'([^']+)'", a.get("reason") or ""):
        if {word.lower() for word in re.findall(r"[\w-]+", phrase)} <= question_words:
            protected.append(phrase)
    for clause in re.findall(UNREADABLE, a.get("reason") or "", re.I):
        protected.extend(unit for unit in re.findall(r"[\w-]+", clause)[3:] if unit.lower() in question_words)
    return protected


def template(a: dict, e) -> str:
    b = a.get("behaviour")
    hidden = Hidden.of(e, a.get("principal") or "")
    if b in FIGURES:
        text = figure_text(a, e)
    elif b in SUMS:
        text = sums_text(a, e)
    elif b in DEFINITIONS:
        text = definition_text(a, hidden.docs)
    elif b in REFUSALS:
        text = refusal_text(a)
    else:
        text = abstention_text(a)
    return hidden.withheld(text, protected_values(a))


def exact(v) -> str:
    """A value as the answer holds it, grouped by thousands and never rounded: 176, 6,400.62, 0.125."""
    if v is None:
        return "no value"
    f = float(v)
    return f"{f:,.0f}" if f.is_integer() else f"{f:,}"


def marks(a: dict) -> str:
    """The markers of the citations a figure is bound to: an answer's numbered citations, in their order."""
    cites = [c for c in a.get("citations") or [] if isinstance(c, dict) and isinstance(c.get("n"), int)]
    return "".join(f" [{c['n']}]" for c in cites)


def lead(a: dict) -> str:
    term, period = term_of(a), period_of(a)
    return (term or "The figure") + (f" for {period}" if period else "")


def figure_text(a: dict, e) -> str:
    where = " · ".join(str(x) for x in (a.get("unit"), a.get("column")) if x)
    source = f" ({where})" if where else ""
    context = a.get("context") or {}
    modes = set(getattr(e, "TIME_MODES", {}) or {}) | {"as reported", "restated", "current version", "current versions"}
    explicit_basis = any(re.search(r"\b" + re.escape(phrase) + r"\b", a.get("question") or "", re.I) for phrase in modes)
    if explicit_basis and context.get("fact") in getattr(getattr(e, "semantics", None), "scd2", {}):
        basis = "as reported at the month's freeze date" if context.get("time_mode") == "as_reported" else "restated using current row versions"
        source += f"; basis: {basis}"
    currency = currency_of(a, e)
    if isinstance(a.get("rows"), dict):
        rows = sorted(a["rows"].items(), key=lambda kv: (kv[1] is None, -(kv[1] or 0)))
        listed = "; ".join(f"{k} {exact(v)}" for k, v in rows) or "no group has a value"
        return f"{lead(a)} {group_phrase(a, e)}" + (f", in {currency}" if currency else "") + f": {listed}{source}{marks(a)}."
    if a.get("figure") is None:
        return f"{lead(a)}: the query returned no value{source}."
    money = f"{currency} {exact(a['figure'])}" if currency else exact(a["figure"])
    return f"{lead(a)}: {money}{source}{marks(a)}." + (f" {a['reason']}" if a.get("reason") else "")


def sums_text(a: dict, e) -> str:
    plan = a.get("plan") if isinstance(a.get("plan"), list) else []
    currency = {p.get("unit"): p.get("currency") for p in plan if isinstance(p, dict)}
    parts = a.get("parts") if isinstance(a.get("parts"), dict) else {}

    def part(unit: str, v) -> str:
        cur = currency.get(unit) or ""
        return f"{e.UNIT_LABEL.get(unit, unit)} " + (f"{cur} " if cur and v is not None else "") + exact(v)
    listed = "; ".join(part(u, v) for u, v in parts.items())
    reason = a.get("reason") or ""
    if a["behaviour"] == "sum_with_verdict":
        return f"{lead(a)}: {exact(a.get('figure'))} in all, the sum of {listed}." + (f" They add: {reason}." if reason else "")
    if a["behaviour"] == "sum_without_verdict":
        return f"{lead(a)}: {exact(a.get('figure'))}, summed as written, with no verdict on whether the parts add: {listed}."
    return f"{lead(a)}: not added, {reason or 'the parts do not add'}. By unit: {listed}."


def definition_text(a: dict, hidden: set[str] = frozenset()) -> str:
    """A definition's text: what it cites and what it does not, never a document of a unit the caller may not read
    (hidden), which is said to be held back."""
    b = a["behaviour"]
    said = "No. " + (a.get("reason") or "") + " " if a.get("answer") == "no" else ""
    if b == "glossary_definition":
        through = f" It is certified through its context {', '.join(a['contexts'])}." if a.get("contexts") else ""
        return (f"{a.get('term')}: {a.get('definition')} This is the pack's glossary's definition: no document defines it, "
                f"so nothing is cited as its definition.{through}")
    if b == "cite":
        found = [f"{d['doc']} ({d.get('shelf') or 'no'} shelf)" for d in a.get("cited_docs") or [] if d["doc"] not in hidden]
        return said + "Found by search, with no shelf rule in this arm: " + (", ".join(found) or "nothing") + "."
    citations = [c for c in a.get("citations") or [] if isinstance(c, dict)]
    if citations:
        cited = "; ".join(f"{c['doc']}" + (f" {c['title']}" if c.get("title") else "") + f" [{c['n']}]"
                          + (f", {c['cited_as']}" if c.get("cited_as") else "") for c in citations if c["doc"] not in hidden)
    else:
        cited = ", ".join(str(d) for d in a.get("cite") or [] if d not in hidden)
    text = said + (f"Cited: {cited}." if cited else "Nothing here may be cited.")
    held = [("superseded", a.get("superseded_not_cited") or []),
            ("not AI-ready", [d.get("doc") for d in a.get("not_ai_ready") or [] if isinstance(d, dict)]),
            ("uncertified", [d.get("id") for d in a.get("uncertified_definition_not_cited") or [] if isinstance(d, dict)])]
    held = [(why, [d for d in docs if d not in hidden]) for why, docs in held]
    not_cited = "; ".join(f"{why}: {', '.join(str(d) for d in docs)}" for why, docs in held if docs)
    if not_cited:
        text += f" Not cited: {not_cited}."
    if a.get("not_readable") or any(d in hidden for d in a.get("cite") or []):
        text += " Documents of a unit you may not read are held back, not cited."
    return text.replace("  ", " ")


def refusal_text(a: dict) -> str:
    reason = a.get("reason") or ""
    if a.get("denied_warehouses"):
        across = (" A join across warehouses runs only if you may read every warehouse in it."
                  if a["behaviour"] == "blocked_access" else "")
        return f"Refused: the question needs data you may not read, so no warehouse was queried.{across} {OWNER}"
    if reason.endswith("is not a querying identity."):
        return f"Refused: you are not signed in as an identity that may ask questions. {IDENTITY}"
    if a.get("restricted") or reason.startswith("Restricted personal data"):
        return ("Refused: the question asks for restricted personal data, which the assistant does not return under any purpose, "
                f"so no warehouse was queried. {POLICY}")
    if reason.startswith("SQL gate"):
        return f"Refused: the query this question needs did not pass the SQL gate, so it did not run. {STEWARD}"
    return f"Refused: {reason or 'the platform will not answer this question as asked.'} {STEWARD}"


def abstention_text(a: dict) -> str:
    reason = a.get("reason") or "the question could not be answered from what is certified."
    return f"No answer: {reason} " + (ROUTED if a.get("behaviour") == "abstain_route_steward" else STEWARD)


COLUMNS = "MATCH (c:Col) RETURN c.unit AS unit, c.tbl AS tbl, c.name AS name"
DOCUMENTS = "MATCH (d:Document) RETURN d.id AS id, d.unit AS unit"
KEPT = 4
_catalogues: dict[str, tuple[list[dict], list[dict]]] = {}


def catalogue(e) -> tuple[list[dict], list[dict]]:
    """Every column by its unit and table, and every document by its unit, of the release e serves: read once per
    release (Engine.identity, its hash, graph generation and chunks pinned together), never once per answer."""
    key = json.dumps(e.identity, sort_keys=True, default=str)
    if key not in _catalogues:
        while len(_catalogues) >= KEPT:
            _catalogues.pop(next(iter(_catalogues)))
        _catalogues[key] = (e.g.q(COLUMNS, {}), e.g.q(DOCUMENTS, {}))
    return _catalogues[key]


class Hidden:
    """What a text for one principal must not name: each unit the principal may not read, by its id and label, its
    qualified tables and columns and each snake_case table or column only such units hold (names: a name a readable
    column has as its business name, BUSINESS, is one the principal reads), and each document of such a unit (docs)."""

    def __init__(self, names: list[str], docs: set[str], shown: list[str]):
        self.names, self.docs, self.shown = names, docs, shown

    @classmethod
    def of(cls, e, principal: str) -> "Hidden":
        readable = e.g.readable_units(principal) if principal else set()
        hidden = [u for u in e.UNIT_LABEL if u not in readable]
        cols, documents = catalogue(e)
        docs = {d["id"] for d in documents if d["unit"] and d["unit"] not in readable}
        shown = sorted({d["id"] for d in documents} - docs, key=len, reverse=True)
        if not hidden:
            return cls([], docs, shown)
        labels = {e.UNIT_LABEL[u] for u in hidden} | set(hidden)
        visible = {e.UNIT_LABEL.get(u, u) for u in readable} | set(readable)
        qualified = {name for c in cols if c["unit"] in hidden
                     for name in (f"{c['unit']}.{c['tbl']}", f"{c['unit']}.{c['tbl']}.{c['name']}")}
        business = getattr(e, "BUSINESS", None) or {}
        seen = {n for c in cols if c["unit"] in readable
                for n in (c["tbl"], c["name"], *business.get(f"{c['tbl']}.{c['name']}", "").split(".")) if n}
        held = {n for c in cols if c["unit"] in hidden for n in (c["tbl"], c["name"]) if "_" in n} - seen
        return cls(sorted((labels - visible) | qualified | held, key=len, reverse=True), docs, shown)

    def withheld(self, text: str, protected: list[str | None] | None = None) -> str:
        """text with each hidden document and name withheld; a document the caller may see is kept whole, whatever
        names its id carries (MD-ACTIVE-CUSTOMER)."""
        kept: list[str] = []

        def protect(value: str) -> str:
            kept.append(value)
            return f"\x00{len(kept) - 1}\x00"

        def keep(m: re.Match) -> str:
            return protect(m.group(0))
        values = {v for v in protected or [] if v}
        named_back: set[str] = set()

        def keep_named_back(m: re.Match) -> str:
            clause = m.group(0)
            for value in sorted(values, key=len, reverse=True):
                pattern = re.compile(NAME_EDGE.format(re.escape(value)), re.I)
                if pattern.search(clause):
                    clause = pattern.sub(keep, clause)
                    named_back.add(value)
            return clause
        text = re.sub(UNREADABLE, keep_named_back, text, flags=re.I)
        for value in sorted(values - named_back, key=len, reverse=True):
            text = text.replace(value, protect(value))
        for doc in sorted(self.docs, key=len, reverse=True):
            text = edge(DOC_EDGE, doc).sub(WITHHELD, text)
        for doc in self.shown:
            text = edge(DOC_EDGE, doc).sub(keep, text)
        for name in self.names:
            text = edge(NAME_EDGE, name, re.I).sub(WITHHELD, text)
        return re.sub("\x00(\\d+)\x00", lambda m: kept[int(m.group(1))], text)

    def leaks(self, text: str, protected: list[str | None] | None = None) -> list[str]:
        """The hidden documents and names text carries: none, once withheld."""
        for value in sorted({v for v in protected or [] if v}, key=len, reverse=True):
            text = text.replace(value, " ")
        said = [d for d in self.docs if edge(DOC_EDGE, d).search(text)]
        for doc in self.shown:
            text = edge(DOC_EDGE, doc).sub(" ", text)
        return said + [n for n in self.names if edge(NAME_EDGE, n, re.I).search(text)]


def held_numbers(a: dict) -> set[float]:
    """Every number the answer holds, outside its text and its run trace (whose timings would hold any number): its
    values, and the numbers in its strings, but for the digits of a name (FY26-Q1, v2)."""
    body = {k: v for k, v in a.items() if k not in ("answer_text", "phrased_by", "run_trace")}
    out: set[float] = set()

    def walk(v) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, str):
            out.update(float(x.replace(",", "")) for x in numbers(v, names(v)))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(json.loads(json.dumps(body, default=str)))
    return out


def stray_numbers(a: dict) -> list[str]:
    """The numbers in the answer's text that the answer does not hold (but for the digits of a name it carries)."""
    text = MARKER.sub("", a.get("answer_text") or "")
    held = held_numbers(a)
    known = names(json.dumps({k: v for k, v in a.items() if k not in ("answer_text", "run_trace")}, default=str))
    return [x for x in numbers(text, known) if float(x.replace(",", "")) not in held]


def bad_markers(a: dict) -> list[int]:
    """The [n] markers in the answer's text that name none of its citations."""
    cited = {c.get("n") for c in a.get("citations") or [] if isinstance(c, dict)}
    return sorted({int(m) for m in MARKER.findall(a.get("answer_text") or "")} - cited)
