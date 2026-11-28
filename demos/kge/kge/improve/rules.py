"""Diagnose step of the improvement job: gives each miss exactly one outcome from a fixed list (#531), with the evidence
improve or drafting needs, from its answer's reason and the run trace's ledger.

The outcomes, in OUTCOMES order, each with its route (who acts on it) and improve's action:
- missing_alias          pack      proposal (one certified term's column comment or name matches, not already held),
                                   already_held (already an alias or candidate), or note (0 or >1 terms match)
- unmapped_column        pack      note to the steward naming the column and the term
- missing_phrase         pack      note naming the words the ledger left unread (with a model, a phrase proposal)
- missing_template       pack      note naming the unapplied operations and the nearest templates (a signature group
                                   of these becomes a template proposal, kge/improve/edits.py)
- template_mismatch      pack      note naming the declared shapes the ledger matched; improve edits no existing shape
- missing_building_block platform  request to the platform team
- missing_join           platform  request to the platform team
- unclear_measure        steward   note to the steward naming both columns

Precedence, first match wins: the four reason rules (missing_alias, unmapped_column, missing_join, unclear_measure),
as before; then, from the ledger (kge/services/ask/engine/planledger.py), an unread unknown word (missing_alias as a
note where any one names a certified term, else missing_phrase); a decline whose reading named no unread span
(missing_building_block, a hypothesis, since the trace cannot say what the decline was about); a declared shape whose
condition holds on the ledger
(template_mismatch); an unapplied operation the block catalogue cannot express (missing_building_block); unapplied
operations it can (missing_template); else missing_building_block, marked a hypothesis, which is also the outcome with
no ledger in the trace (a page question, an answer off the query plan's path, a record from before #531). A
follow-up's ledger holds its own turn's pieces only, so its template and building-block outcomes are hypotheses.

Every diagnosis carries route, trace ('read' or 'absent'), hypothesis, ledger and templates. ledger is the hand-over
kge/improve/edits.py reads: {names, slots, unknown, unapplied}, the sorted operations the question's ledger read, the
count of each slot kind above zero, whether an unknown word was left unread, and each unapplied span with its text,
start, end, kind and operation, plus localized and follow_up (read_ledger); None without a trace. templates is {matched, nearest}: the declared shapes (preload
and pack) whose condition holds on the ledger, and, when none does, up to three that it partly meets, each with what
it misses, what it forbids that the ledger read, and what the trace cannot check (markers and slot counts).
"""
from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any

from kge.compiler import standards
from kge.compiler.checks import shape_condition
from kge.semantics.blocks import CATALOGUE

OUTCOMES = ("missing_alias", "unmapped_column", "missing_phrase", "missing_template", "template_mismatch",
            "missing_building_block", "missing_join", "unclear_measure")
ROUTES = MappingProxyType({"missing_alias": "pack", "unmapped_column": "pack", "missing_phrase": "pack",
                           "missing_template": "pack", "template_mismatch": "pack", "missing_building_block": "platform",
                           "missing_join": "platform", "unclear_measure": "steward"})
RECEIVERS = MappingProxyType({"pack": "pack author", "platform": "platform team", "steward": "steward"})
IMPLIED_OPERATIONS = frozenset({"count", "comparison", "aggregate", "measure", "membership", "mtd", "qtd", "not_null",
                                "not_in", "dimension", "magnitude_gt", "magnitude_ge", "magnitude_lt", "magnitude_le",
                                "separate"})
BLOCK_OPERATIONS = frozenset(CATALOGUE) | frozenset(v for b in CATALOGUE.values() for p in b.params.values()
                                                     if p.type == "enum" for v in p.values) | IMPLIED_OPERATIONS

MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
PERIOD_PATTERNS = (
    re.compile(r"\b(?:for|in|during|over|as of|on|at|to)\s+the\s+latest\s+month(?:\s+with\s+rows)?\b", re.I),
    re.compile(r"\b(?:for|in|during|over|as of|on|at|to)?\s*(?:calendar\s+|fiscal\s+)?(?:fy\d{2}-q[1-4]|q[1-4]\s+\d{4}|\d{4}-q[1-4])\b", re.I),
    re.compile(r"\b(?:for|in|during|over|as of|on|at|to)?\s*(?:\d{1,2}\s+)?(?:" + "|".join(MONTHS) + r")\s+\d{4}\b", re.I),
    re.compile(r"\b(?:for|in|during|over|as of|on|at|to)\s+(?:fy\s*)?\d{4}\b", re.I),
    re.compile(r"\b(?:for|in|during|over)\s+the\s+(?:quarter|month|year|period)\b", re.I),
)
LEAD_WORDS = re.compile(
    r"^(?:what\s+(?:was|were|is|are)|how\s+much\s+(?:was|were|is)|show\s+(?:me\s+)?|give\s+(?:me\s+)?|tell\s+me\s+|total\s+|combined\s+|the\s+)+",
    re.I,
)
TRAIL_PREP = re.compile(r"\b(?:for|in|of|on|by|during|over|at|to|from|across)\s*$", re.I)
NUMERIC_TYPE = re.compile(r"DECIMAL|DOUBLE|FLOAT|INTEGER|BIGINT|NUMERIC", re.I)
NOT_CERTIFIED_RE = re.compile(r"'([^']+)'\s+is\s+not\s+certified\s+for\s+([a-z0-9_-]+)", re.I)
NOT_CERTIFIED_IN_RE = re.compile(r"Not\s+certified\s+in\s+([^:]+):\s*([^.]+)", re.I)
NO_JOIN_PATH_RE = re.compile(r"no\s+declared\s+join\s+path\s+from\s+(\S+)\s+to\s+(\S+)", re.I)


def extract_phrase(question: str, source_phrases: list[str] | None = None) -> str:
    """Strip period expressions, leading question words, and optional unit/source phrases from question."""
    text = re.sub(r"[?!.;,]+", " ", question.strip().lower())
    for pat in PERIOD_PATTERNS:
        text = pat.sub(" ", text)
    text = LEAD_WORDS.sub("", text.strip())
    for src in sorted(source_phrases or [], key=len, reverse=True):
        s = src.strip().lower()
        if s:
            text = re.sub(r"\b(?:in|for|of|at|across)\s+" + re.escape(s) + r"\b", " ", text)
            text = re.sub(r"^" + re.escape(s) + r"\s+", "", text)
    text = TRAIL_PREP.sub("", text.strip())
    text = re.sub(r"^(?:the|a|an)\s+", "", text.strip())
    return " ".join(text.split())


def _matching_comments(phrase: str, evidence: dict[str, Any]) -> list[dict[str, str]]:
    """Find certified columns whose comment, or certified terms whose name, contains phrase as whole words."""
    if not phrase or len(phrase) < 2:
        return []
    pat = re.compile(r"\b" + re.escape(phrase) + r"\b", re.I)
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    columns = evidence.get("columns") or {}
    certified_terms = evidence.get("certified_terms") or {}

    for col_key, col in sorted(columns.items()):
        desc = str(col.get("description") or "")
        for tid in col.get("certified_terms") or []:
            if tid in certified_terms and pat.search(desc):
                if (col_key, tid) not in seen:
                    seen.add((col_key, tid))
                    matches.append({"column": col_key, "term": tid, "comment": desc})

    for tid, tinfo in sorted(certified_terms.items()):
        tname = str(tinfo.get("name") or "")
        if pat.search(tname):
            cols = (evidence.get("term_columns") or {}).get(tid) or [tid]
            col_ref = cols[0]
            if (col_ref, tid) not in seen:
                seen.add((col_ref, tid))
                matches.append({"column": col_ref, "term": tid, "comment": tname})

    return matches


def _find_unmapped_column(unit: str, term_label: str, evidence: dict[str, Any]) -> tuple[str | None, str | None]:
    """Find a single numeric, non-key column in unit matching term_label."""
    columns = evidence.get("columns") or {}
    certified_terms = evidence.get("certified_terms") or {}
    term_id = next(
        (tid for tid, t in certified_terms.items() if t.get("name", "").lower() == term_label.lower() or tid == term_label.lower()),
        term_label.lower().replace(" ", "-"),
    )
    words = {w for w in re.findall(r"[a-z0-9]+", term_label.lower()) if len(w) > 2 and w not in {"usd", "the", "and", "for"}}
    candidates: list[str] = []
    for col_key, col in sorted(columns.items()):
        if col.get("unit") != unit or col.get("is_key"):
            continue
        if not NUMERIC_TYPE.search(str(col.get("type") or "")):
            continue
        if col.get("certified_terms"):
            continue
        haystack = f"{col.get('name', '')} {col.get('description', '')}".lower()
        if words and any(w in haystack for w in words):
            candidates.append(col_key)
    if len(candidates) == 1:
        return candidates[0], term_id
    return None, term_id


def _reason_rules(miss: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any] | None:
    """The four reason rules: missing_alias, unmapped_column, missing_join or unclear_measure from the answer's reason
    and figure, or None where none holds."""
    reason = str(miss.get("reason") or "")
    question = str(miss.get("question") or "")
    behaviour = str(miss.get("behaviour") or "")
    expect = miss.get("expect") or {}
    item_id = miss.get("id")
    set_name = miss.get("set")

    if reason.startswith("No glossary term recognised") or (
        behaviour == "abstain" and reason == "The context names no column for this question."
    ):
        phrase = extract_phrase(question)
        existing_aliases = evidence.get("existing_aliases") or {}
        candidate_phrases = set(evidence.get("candidate_phrases") or [])
        matches = _matching_comments(phrase, evidence)
        terms = sorted({m["term"] for m in matches})

        if phrase in existing_aliases or phrase in candidate_phrases:
            return {
                "id": item_id,
                "set": set_name,
                "source": miss.get("source", "ab"),
                "question": question,
                "diagnosis": "missing_alias",
                "outcome": "already_held",
                "phrase": phrase,
                "matching_comments": matches,
                "edit": None,
                "why": f"phrase {phrase!r} is already held in the pack or steward queue",
            }
        if len(terms) == 1:
            term_id = terms[0]
            col_ref = matches[0]["column"]
            term_name = ((evidence.get("certified_terms") or {}).get(term_id) or {}).get("name") or term_id
            return {
                "id": item_id,
                "set": set_name,
                "source": miss.get("source", "ab"),
                "question": question,
                "diagnosis": "missing_alias",
                "outcome": "proposal",
                "phrase": phrase,
                "term": term_id,
                "term_name": term_name,
                "evidence_column": col_ref,
                "matching_comments": matches,
                "edit": {"section": "terminology.aliases", "phrase": phrase, "term": term_id},
                "why": f"missing alias: add {phrase!r} for {term_name} (from the comment on {col_ref})",
            }
        if len(terms) > 1:
            names = [((evidence.get("certified_terms") or {}).get(t) or {}).get("name") or t for t in terms]
            return {
                "id": item_id,
                "set": set_name,
                "source": miss.get("source", "ab"),
                "question": question,
                "diagnosis": "missing_alias",
                "outcome": "note",
                "phrase": phrase,
                "matching_comments": matches,
                "edit": None,
                "why": f"missing alias: {phrase!r} matches multiple certified terms ({', '.join(names)})",
            }
        return {
            "id": item_id,
            "set": set_name,
            "source": miss.get("source", "ab"),
            "question": question,
            "diagnosis": "missing_alias",
            "outcome": "note",
            "phrase": phrase,
            "matching_comments": [],
            "edit": None,
            "why": f"missing alias: no certified column comment or term names {phrase!r}",
        }

    m_uncert = NOT_CERTIFIED_RE.search(reason)
    if m_uncert:
        term_label, unit = m_uncert.group(1), m_uncert.group(2).lower()
        col, term_id = _find_unmapped_column(unit, term_label, evidence)
        if col:
            return {
                "id": item_id,
                "set": set_name,
                "source": miss.get("source", "ab"),
                "question": question,
                "diagnosis": "unmapped_column",
                "outcome": "note",
                "column": col,
                "term": term_id,
                "edit": None,
                "why": f"unmapped column: {col} in {unit} matches {term_label!r}; steward certification is required before the planner can use it",
            }

    m_join = NO_JOIN_PATH_RE.search(reason)
    if m_join:
        t_from, t_to = m_join.group(1), m_join.group(2)
        return {
            "id": item_id,
            "set": set_name,
            "source": miss.get("source", "ab"),
            "question": question,
            "diagnosis": "missing_join",
            "outcome": "request",
            "from_table": t_from,
            "to_table": t_to,
            "edit": None,
            "why": f"missing join: no declared join path from {t_from} to {t_to} (join paths come from foreign keys in pack.lock)",
        }

    exp_col = expect.get("column")
    got_col = miss.get("column")
    fig_ok = (miss.get("measures") or {}).get("figure_ok")
    if behaviour == "figure" and fig_ok is False and exp_col and got_col and exp_col != got_col:
        return {
            "id": item_id,
            "set": set_name,
            "source": miss.get("source", "ab"),
            "question": question,
            "diagnosis": "unclear_measure",
            "outcome": "note",
            "columns": [got_col, exp_col],
            "edit": None,
            "why": f"unclear measure: answered from {got_col} instead of expected {exp_col}",
        }
    for tid, cols in (evidence.get("term_columns") or {}).items():
        if len(cols) > 1 and got_col and any(c.endswith(f".{got_col}") or c == got_col for c in cols):
            if fig_ok is False:
                return {
                    "id": item_id,
                    "set": set_name,
                    "source": miss.get("source", "ab"),
                    "question": question,
                    "diagnosis": "unclear_measure",
                    "outcome": "note",
                    "columns": list(cols),
                    "edit": None,
                    "why": f"unclear measure: term {tid} has multiple certified columns ({', '.join(cols)})",
                }

    return None


def expressible(operation: str) -> bool:
    """Whether the block catalogue (kge/semantics/blocks.py) expresses an operation: a block's name, one of a block's
    enum values, or one the plan's nodes carry without a block of that name (IMPLIED_OPERATIONS, as
    kge/semantics/coverage.py's operations derives them: a count, a comparison, an aggregate, a measure, a membership,
    the to-date ranges, not_null, not_in, a dimension and the magnitude filters), or the plan's separate layout."""
    return operation in BLOCK_OPERATIONS


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _within(p: dict[str, Any], spans: list[tuple[int, int]]) -> bool:
    return any(lo <= p["start"] and p["end"] <= hi for lo, hi in spans)


def _meets(p: dict[str, Any], spans: list[tuple[int, int]]) -> bool:
    return any(p["start"] < hi and lo < p["end"] for lo, hi in spans)


def _pairs(value: Any) -> list[tuple[int, int]]:
    return [(a[0], a[1]) for a in value or [] if isinstance(a, (list, tuple)) and len(a) == 2 and _int(a[0]) and _int(a[1])]


def read_ledger(trace: Any, question: str) -> dict[str, Any] | None:
    """The hand-over ledger {names, slots, unknown, unapplied, localized, follow_up} from a run trace's ledger block and
    the question it was read from, or None where the block is missing or malformed.

    Where the answer was a result, a piece is unapplied when no span the executed plan applied contains it. Where it
    declined, a piece is unapplied when it meets a span the reading said it could not read; with no such span, only the
    unknown words are, the one thing the trace knows went unread, and localized is False: it cannot say which other
    words the decline was about. An unapplied span's text
    is the question's slice at its positions, or None where they fall outside the question. follow_up says the ledger
    holds this turn's pieces only."""
    if not isinstance(trace, dict) or not isinstance(trace.get("pieces"), list):
        return None
    if not isinstance(trace.get("applied", []), list) or not isinstance(trace.get("unread", []), list):
        return None
    pieces = [p for p in trace["pieces"] if isinstance(p, dict) and _int(p.get("start")) and _int(p.get("end"))]
    applied, unread = _pairs(trace.get("applied")), _pairs(trace.get("unread"))
    if trace.get("outcome") == "result":
        left, localized = [p for p in pieces if not _within(p, applied)], True
    elif unread:
        left, localized = [p for p in pieces if _meets(p, unread)], True
    else:
        left, localized = [p for p in pieces if p.get("kind") == "unknown"], False
    unapplied = [{"text": question[p["start"]:p["end"]] if 0 <= p["start"] < p["end"] <= len(question) else None,
                  "start": p["start"], "end": p["end"], "kind": p.get("kind"), "operation": p.get("operation")}
                 for p in left]
    counted = [p.get("slot") for p in pieces if p.get("slot") in standards.SLOT_KINDS]
    return {"names": sorted({p["operation"] for p in pieces if isinstance(p.get("operation"), str) and p["operation"]}),
            "slots": {kind: counted.count(kind) for kind in standards.SLOT_KINDS if kind in counted},
            "unknown": any(u["kind"] == "unknown" for u in unapplied),
            "unapplied": unapplied, "localized": localized, "follow_up": bool(trace.get("follow_up"))}


def _misfit(shape: dict[str, Any], names: set[str], slots: dict[str, int]) -> dict[str, list[str]]:
    """What keeps a shape's condition from holding on a ledger: the operations and one_of groups it misses and the
    slot counts out of its range (missing), what it forbids that the ledger read (forbidden), and what the trace cannot
    check (unchecked): the markers it requires and its position constraints."""
    a = shape_condition(shape)
    markers = set(standards.MARKERS)
    missing = sorted(a.plain - names - markers)
    missing += sorted("one_of:" + "|".join(sorted(g)) for g in a.groups if not (g & names) and not (g & markers))
    missing += sorted(f"slot:{kind}" for kind, (lo, hi) in a.ranges.items() if not lo <= slots.get(kind, 0) <= hi)
    unchecked = sorted((a.plain | frozenset().union(*a.groups)) & markers) + (["constraints"] if a.constraints else [])
    return {"missing": missing, "forbidden": sorted(a.forbids & names), "unchecked": unchecked}


def templates(ledger: dict[str, Any] | None, shapes: dict[str, dict], limit: int = 3) -> dict[str, list]:
    """{matched, nearest} for a ledger over the declared shapes (id -> declaration): matched holds the ids of shapes
    whose condition holds on it, with nothing unchecked; nearest, only when none matched, the first limit shapes that
    require or forbid an operation the ledger read, by fewest misfits, then id, each {shape, missing, forbidden,
    unchecked}."""
    if not ledger:
        return {"matched": [], "nearest": []}
    names, slots = set(ledger["names"]), ledger["slots"]
    matched, near = [], []
    for sid, shape in sorted(shapes.items()):
        if not isinstance(shape, dict):
            continue
        fit = _misfit(shape, names, slots)
        if not fit["missing"] and not fit["forbidden"] and not fit["unchecked"]:
            matched.append(sid)
            continue
        a = shape_condition(shape)
        if (a.plain | frozenset().union(*a.groups) | a.forbids) & names:
            near.append({"shape": sid, **fit})
    near.sort(key=lambda n: (len(n["missing"]) + len(n["forbidden"]), n["shape"]))
    return {"matched": matched, "nearest": [] if matched else near[:limit]}


def _quoted(spans: list[dict[str, Any]]) -> str:
    return ", ".join(repr(s["text"]) for s in spans if s.get("text") is not None) or "none"


def _ledger_rules(miss: dict[str, Any], evidence: dict[str, Any], ledger: dict[str, Any] | None,
                  tmpl: dict[str, list]) -> tuple[dict[str, Any], bool]:
    """(diagnosis, hypothesis) for a miss no reason rule explains, from its ledger: an unread unknown word, a matched
    template, an operation no block expresses, operations no template covers, else a building block as a hypothesis."""
    base = {"id": miss.get("id"), "set": miss.get("set"), "source": miss.get("source", "ab"),
            "question": str(miss.get("question") or ""), "edit": None}
    reason = str(miss.get("reason") or miss.get("why") or "")
    if ledger is None:
        return {**base, "diagnosis": "missing_building_block", "outcome": "request",
                "why": "missing building block (platform team), a hypothesis: the run trace holds no ledger"
                       + (f"; {reason}" if reason else "")}, True
    un = ledger["unapplied"]
    unknown = [u for u in un if u["kind"] == "unknown" and u["text"]]
    if unknown:
        phrases = [" ".join(str(u["text"]).lower().split()) for u in unknown]
        named = [(ph, m) for ph in phrases if (m := _matching_comments(ph, evidence))]
        phrase, matches = named[0] if named else (phrases[0], [])
        if matches:
            terms = ", ".join(sorted({m["term"] for m in matches}))
            return {**base, "diagnosis": "missing_alias", "outcome": "note", "phrase": phrase, "matching_comments": matches,
                    "why": f"missing alias (pack author): {phrase!r} is read as no term and names {terms} in a certified "
                           f"comment or term; unapplied: {_quoted(unknown)}"}, False
        return {**base, "diagnosis": "missing_phrase", "outcome": "note", "phrase": phrase,
                "why": f"missing phrase (pack author): {phrase!r} is read as no phrase; unapplied: {_quoted(unknown)}"}, False
    if not ledger["localized"]:
        return {**base, "diagnosis": "missing_building_block", "outcome": "request",
                "why": "missing building block (platform team), a hypothesis: the answer declined and its reading named no "
                       "unread span" + (f"; {reason}" if reason else "")}, True
    guess = ledger["follow_up"]
    if tmpl["matched"]:
        return {**base, "diagnosis": "template_mismatch", "outcome": "note",
                "why": f"template mismatch (pack author): the question matches {', '.join(tmpl['matched'])}, and the answer "
                       f"still missed; unapplied: {_quoted(un)}"}, guess
    ops = [u for u in un if isinstance(u.get("operation"), str) and u["operation"]]
    hard = sorted({u["operation"] for u in ops if not expressible(u["operation"])})
    if hard:
        return {**base, "diagnosis": "missing_building_block", "outcome": "request",
                "why": f"missing building block (platform team): no block expresses {', '.join(hard)}; unapplied: "
                       f"{_quoted(ops)}"}, guess
    if ops:
        near = "; nearest: " + ", ".join(f"{n['shape']} needs {', '.join(n['missing']) or 'nothing it can check'}"
                                         for n in tmpl["nearest"]) if tmpl["nearest"] else ""
        return {**base, "diagnosis": "missing_template", "outcome": "note",
                "why": f"missing template (pack author): no declared shape covers {', '.join(sorted({u['operation'] for u in ops}))}"
                       f"; unapplied: {_quoted(ops)}{near}"}, guess
    return {**base, "diagnosis": "missing_building_block", "outcome": "request",
            "why": "missing building block (platform team), a hypothesis: the ledger shows no unread word or operation"
                   + (f"; {reason}" if reason else "")}, True


def diagnose_miss(miss: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Give one miss one outcome from OUTCOMES, with its route, the run trace's ledger and the matched or nearest
    templates handed over (the module's docstring has the precedence). miss's trace is its run trace's ledger block;
    evidence's shapes the declared shapes by id."""
    question = str(miss.get("question") or "")
    ledger = read_ledger(miss.get("trace"), question)
    tmpl = templates(ledger, evidence.get("shapes") or {})
    found, hypothesis = _reason_rules(miss, evidence), False
    if found is None:
        found, hypothesis = _ledger_rules(miss, evidence, ledger, tmpl)
        if ledger is not None and any(u["text"] is None for u in ledger["unapplied"]):
            hypothesis = True
    carried = {k: miss[k] for k in ("kind", "expect", "unit") if k in miss and k not in found}
    return {**found, **carried, "route": ROUTES[found["diagnosis"]], "trace": "read" if ledger is not None else "absent",
            "hypothesis": hypothesis, "ledger": ledger, "templates": tmpl}


def diagnose_all(ab_misses: list[dict[str, Any]], page_misses: list[dict[str, Any]], evidence: dict[str, Any]) -> dict[str, Any]:
    """Diagnose every A/B miss and every page no-term miss."""
    ab_diag = [diagnose_miss(m, evidence) for m in ab_misses]
    page_diag = [{**diagnose_miss(m, evidence), "count": m.get("count", 1)} for m in page_misses]
    return {"ab_diagnoses": ab_diag, "page_diagnoses": page_diag}
