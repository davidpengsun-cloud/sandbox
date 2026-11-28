"""What both shadow logs write: one observation checked and put as one line of JSON, its fields and nothing else.

The rules a line holds to are the stage selector's (kge/services/ask/engine/shapestage.py, #519, #520): a takeover is
the shadow stage's decline takeover, the graph side serving its plan where the coded side declined (graph_first and
retired serve the graph over a coded decline without one, and retired serves the graph's decline too); a fallback is
served by the coded side; the comparison is one the two outcomes allow; review evidence is kept exactly for a
disagreement or a takeover, never holds a key that could carry the question, the principal, SQL, rows, figures or answer
text, and holds outside a proposal's plan only declared ids, snake-named kinds and operations, and positions."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timedelta

from kge.adapters.feedbacklog.record import release_segment
from kge.ports import SHADOW_COMPARISONS, SHADOW_OUTCOMES, SHADOW_SERVED, ShadowObservation

NAME = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,255}")
"""A shape id (a pack's slug, checks.SLUG, which may start with a digit) or a coded builder's name, such as
planshapes.compose:range."""

ID = re.compile(r"[0-9a-f]{32}")

TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00")
"""An observation's time as observe writes it: UTC, to the millisecond."""

LEDGER_NAME = re.compile(r"[a-z][a-z0-9_]{0,127}")
"""A ledger piece's kind or operation: a short snake name, never a word from the question."""

CANDIDATE = re.compile(r"[^\x00-\x1f\x7f-\x9f\u2028\u2029]{1,512}")
"""A clarification's candidate: a declared id, at most 512 characters with no control character or line separator. A
value's id (value:<column>:<value>) holds the pack's member text, spaces included."""

FORBIDDEN = frozenset({"question", "principal", "sql", "rows", "figures", "answer_text", "evidence", "obligations",
                       "missing", "text"})
"""Keys that may never appear anywhere inside an observation's evidence."""

EVIDENCE_KEYS = frozenset({"graph", "coded", "ledger"})
SIDE_KEYS = frozenset({"outcome", "plan", "candidates", "applied"})
SIDE_OPTIONAL = frozenset({"truncated"})
LEDGER_KEYS = frozenset({"kind", "operation", "start", "end"})

MAX_LINE = 256 * 1024
"""The longest line, in bytes, a shadow log keeps."""


def forbidden_key(node) -> str | None:
    """The first key from FORBIDDEN found anywhere inside node, or None."""
    if isinstance(node, dict):
        for key, child in node.items():
            if key in FORBIDDEN:
                return key
            found = forbidden_key(child)
            if found is not None:
                return found
    elif isinstance(node, (list, tuple)):
        for child in node:
            found = forbidden_key(child)
            if found is not None:
                return found
    return None


def position(value) -> bool:
    """Whether value is a non-negative integer, not a boolean."""
    return type(value) is int and value >= 0


def check_side(name: str, side, outcome: str) -> None:
    """ValueError unless side is one planner's evidence: its outcome the observation's, a plan only for a proposal,
    declared candidate ids, [start, end] positions, and truncated only as True."""
    if not isinstance(side, dict) or not SIDE_KEYS <= set(side) <= SIDE_KEYS | SIDE_OPTIONAL:
        raise ValueError(f"evidence's {name} side holds exactly outcome, plan, candidates and applied")
    if side["outcome"] != outcome:
        raise ValueError(f"evidence's {name} outcome is the observation's {name} outcome")
    if side["plan"] is not None and (outcome != "propose" or not isinstance(side["plan"], dict)):
        raise ValueError(f"evidence's {name} plan is a proposal's plan, or None")
    if outcome == "propose" and side["plan"] is None and side.get("truncated") is not True:
        raise ValueError(f"evidence's {name} proposal drops its plan only where the side is marked truncated")
    candidates = side["candidates"]
    if not isinstance(candidates, list) or any(not isinstance(c, str) or not CANDIDATE.fullmatch(c) for c in candidates):
        raise ValueError(f"evidence's {name} candidates are declared ids")
    applied = side["applied"]
    if not isinstance(applied, list) or any(
            not isinstance(p, list) or len(p) != 2 or not all(position(v) for v in p) or p[0] >= p[1] for p in applied):
        raise ValueError(f"evidence's {name} applied spans are [start, end] positions")
    if "truncated" in side and side["truncated"] is not True:
        raise ValueError(f"evidence's {name} truncated marker is true or absent")


def check_evidence(evidence, graph_outcome: str, coded_outcome: str) -> None:
    """ValueError unless evidence is {"graph": side, "coded": side, "ledger": [piece, ...]} with no FORBIDDEN key, each
    side as check_side holds it and each ledger piece a kind, an operation and a position, never a word."""
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS:
        raise ValueError("evidence holds exactly graph, coded and ledger")
    found = forbidden_key(evidence)
    if found is not None:
        raise ValueError(f"evidence may not hold {found!r}")
    check_side("graph", evidence["graph"], graph_outcome)
    check_side("coded", evidence["coded"], coded_outcome)
    ledger = evidence["ledger"]
    if not isinstance(ledger, list) or any(not isinstance(p, dict) or set(p) != LEDGER_KEYS for p in ledger):
        raise ValueError("evidence's ledger holds pieces of kind, operation, start and end alone")
    for piece in ledger:
        if not isinstance(piece["kind"], str) or not LEDGER_NAME.fullmatch(piece["kind"]):
            raise ValueError("a ledger piece's kind is a snake name")
        if piece["operation"] is not None and (not isinstance(piece["operation"], str)
                                               or not LEDGER_NAME.fullmatch(piece["operation"])):
            raise ValueError("a ledger piece's operation is a snake name or None")
        if not position(piece["start"]) or not position(piece["end"]) or piece["start"] >= piece["end"]:
            raise ValueError("a ledger piece's start and end are positions")


def check_time(time) -> None:
    """ValueError unless time is an ISO-8601 time in UTC to the millisecond, as observe writes it."""
    try:
        parsed = datetime.fromisoformat(time) if isinstance(time, str) and TIME.fullmatch(time) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{time!r} is not an ISO-8601 time in UTC")


def finite(node) -> bool:
    """Whether every number inside node is finite."""
    if isinstance(node, float):
        return math.isfinite(node)
    if isinstance(node, dict):
        return all(finite(child) for child in node.values())
    if isinstance(node, (list, tuple)):
        return all(finite(child) for child in node)
    return True


def comparison_of(graph_outcome: str, coded_outcome: str, stated: str) -> str:
    """The comparison two outcomes allow: propose_decline where exactly one proposes, declines_differ where both decline
    with different kinds, agree where both decline with the same kind; where both propose, plans_differ or agree, which
    only the plans tell, so the stated one where it is either."""
    proposals = (graph_outcome == "propose") + (coded_outcome == "propose")
    if proposals == 1:
        return "propose_decline"
    if proposals == 0:
        return "agree" if graph_outcome == coded_outcome else "declines_differ"
    return stated if stated in ("agree", "plans_differ") else "agree"


def line_of(observation: ShadowObservation) -> bytes:
    """The observation as one line of JSON, its fields alone; ValueError for anything but a ShadowObservation that keeps
    every rule in this module's docstring."""
    o = observation
    if type(o) is not ShadowObservation:
        raise ValueError("a shadow log keeps a ShadowObservation and nothing else")
    if not isinstance(o.id, str) or not ID.fullmatch(o.id):
        raise ValueError("an observation's id is 32 lowercase hex characters")
    check_time(o.time)
    release_segment(o.release)
    for field in ("graph_shapes", "coded_shapes", "shapes"):
        names = getattr(o, field)
        if not isinstance(names, tuple) or any(not isinstance(n, str) or not NAME.fullmatch(n) for n in names):
            raise ValueError(f"{field} is a tuple of shape or builder names")
        if len(set(names)) != len(names):
            raise ValueError(f"{field} names each shape once")
    if o.shapes != tuple(sorted(set(o.graph_shapes) | set(o.coded_shapes))):
        raise ValueError("shapes is the sorted union of graph_shapes and coded_shapes")
    if o.graph_outcome not in SHADOW_OUTCOMES or o.coded_outcome not in SHADOW_OUTCOMES:
        raise ValueError(f"each outcome must be one of {', '.join(SHADOW_OUTCOMES)}")
    if o.comparison not in SHADOW_COMPARISONS:
        raise ValueError(f"comparison must be one of {', '.join(SHADOW_COMPARISONS)}, not {o.comparison!r}")
    if o.comparison != comparison_of(o.graph_outcome, o.coded_outcome, o.comparison):
        raise ValueError(f"a {o.graph_outcome} beside a {o.coded_outcome} cannot be {o.comparison!r}")
    if o.served not in SHADOW_SERVED:
        raise ValueError(f"served must be one of {', '.join(SHADOW_SERVED)}, not {o.served!r}")
    if any(type(flag) is not bool for flag in (o.fallback, o.takeover, o.new_phrase, o.via_terms)):
        raise ValueError("fallback, takeover, new_phrase and via_terms are booleans")
    if o.takeover and not (o.served == "graph" and o.graph_outcome == "propose" and o.coded_outcome != "propose"):
        raise ValueError("a takeover is the graph side serving its plan where the coded side declined")
    if o.fallback and o.served != "coded":
        raise ValueError("a fallback is the coded side serving")
    if (o.evidence is not None) != (o.comparison != "agree" or o.takeover):
        raise ValueError("review evidence is kept for a disagreement or a takeover, and only then")
    if o.evidence is not None:
        check_evidence(o.evidence, o.graph_outcome, o.coded_outcome)
    if not finite(o.evidence):
        raise ValueError("a shadow observation must be plain JSON")
    try:
        row = asdict(o)
        if not o.via_terms:
            row.pop("via_terms")
        text = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("a shadow observation must be plain JSON") from None
    line = (text + "\n").encode("utf-8")
    if len(line) > MAX_LINE:
        raise ValueError(f"a shadow observation is at most {MAX_LINE} bytes")
    return line


def _valid_observation(row: dict) -> bool:
    shape_fields = ("graph_shapes", "coded_shapes", "shapes")
    if any(not isinstance(row.get(field), list) for field in shape_fields):
        return False
    shapes = {field: tuple(row[field]) for field in shape_fields}
    try:
        line_of(ShadowObservation(**(row | shapes)))
    except (TypeError, ValueError):
        return False
    return True


def unique_observations(records: Iterable[dict]) -> list[dict]:
    """Keep the first valid observation for each id, leaving unrecognized records for fold validation."""
    rows = list(records)
    positions: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        identity = row.get("id") if isinstance(row, dict) else None
        if isinstance(identity, str) and ID.fullmatch(identity):
            positions.setdefault(identity, []).append(index)

    retries = set()
    for repeated in positions.values():
        if len(repeated) > 1:
            valid = [index for index in repeated if _valid_observation(rows[index])]
            retries.update(valid[1:])
    return [row for index, row in enumerate(rows) if index not in retries] if retries else rows
