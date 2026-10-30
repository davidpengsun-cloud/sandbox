"""Diagnose step of the improvement job: classifies each miss from its answer's reason and evidence.

Rules only:
- missing_alias    -> proposal (when exactly one certified term's column comment or name matches, and not already held),
                      already_held (when already an alias or candidate), or note (when 0 or >1 terms match)
- unmapped_column  -> note to the steward naming the column and the term
- missing_join     -> request to the platform team
- unclear_measure  -> note to the steward naming both columns
- needs_code       -> request to the platform team
"""
from __future__ import annotations

import re
from typing import Any

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


def diagnose_miss(miss: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify one miss into missing_alias, unmapped_column, missing_join, unclear_measure, or needs_code."""
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

    return {
        "id": item_id,
        "set": set_name,
        "source": miss.get("source", "ab"),
        "question": question,
        "diagnosis": "needs_code",
        "outcome": "request",
        "edit": None,
        "why": f"needs code: {reason or miss.get('why') or 'requires planner or code support'}",
    }


def diagnose_all(ab_misses: list[dict[str, Any]], page_misses: list[dict[str, Any]], evidence: dict[str, Any]) -> dict[str, Any]:
    """Diagnose every A/B miss and every page no-term miss."""
    ab_diag = [diagnose_miss(m, evidence) for m in ab_misses]
    page_diag = [{**diagnose_miss(m, evidence), "count": m.get("count", 1)} for m in page_misses]
    return {"ab_diagnoses": ab_diag, "page_diagnoses": page_diag}
