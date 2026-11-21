"""The steward service's logic: the queue a steward reads, and a decision, which is the runtime overlay."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from kge.pipeline.overlay import OverlayError
from kge.pipeline.profile import Profile
from kge.services.ask import DECISIONS, Engine
from kge.services.graph import node_id

CURATE_KINDS: dict[str, tuple[str, ...]] = {
    "term_tags": ("suggested_mention",),
    "new_words": ("candidate_term",),
    "filter_values": ("filter_value",),
    "documents": ("document_tier", "privacy"),
    "business_terms": ("business_term",),
    "cross_references": ("xref_match",),
}
KIND_OF = {family: kind for kind, families in CURATE_KINDS.items() for family in families}


def queue(engine: Engine, who: str) -> list[dict]:
    """The queue: tags to decide, and the cross-reference matches certification did not certify, for one who reads every
    unit a match links; each item with the Curate kind its family is listed under (CURATE_KINDS, cerebro.md §6.2: privacy under documents beside
    document_tier, xref_match its own list), and the graph node Curate opens it on where
    there is one (node_of)."""
    rows = []
    for item in engine.g.steward_queue(who):
        node = node_of(item)
        rows.append({**item, "kind": KIND_OF[item["family"]], **({"node": node} if node else {})})
    return rows


def node_of(item: dict) -> str | None:
    """The graph read's id of the node a queue item opens on, by the graph read's own rule (node_id): the node the
    store names, else the document of the passage the item tags. An item on no passage (a draft's proposals, a
    similar-name tag: its `doc` is a column or "<unit> draft") and a cross-reference match (its `doc` is a curated
    table) name no document, so they open on nothing until the store names their node."""
    if isinstance(item.get("node"), str) and item["node"]:
        return item["node"]
    if item.get("family") == "xref_match" or not item.get("chunk") or not isinstance(item.get("doc"), str) or not item["doc"]:
        return None
    return node_id("document", item["doc"])


def decided(engine: Engine, who: str) -> list[dict]:
    """Past suggested passage–term decisions, with the journal's model release hash."""
    return engine.g.steward_decided(who)


def decide(engine: Engine, tag: str, who: str, decision: str, value: str | None, definition: str | None) -> dict:
    """A decision on a queue item, written to the graph and journaled in the client's graph store
    (kge/pipeline/overlay.py), then the engine refreshed. A decision not in DECISIONS is a ValueError; one the journal did
    not take is the overlay's error, the engine refreshed all the same; the graph's own refusals are raised as they are."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    try:
        result = engine.g.steward_decide(tag, who, decision, value, definition)
    except OverlayError:
        engine.refresh()
        raise
    engine.refresh()
    return result


GAPS_DAYS = 7
GAPS_LIMIT = 20


def _when(value) -> datetime | None:
    """A row's `at` as an aware UTC time, or None where it is missing or not a time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unmatched_rows(profile: Profile) -> list[dict]:
    """The unmatched-question log's rows, a line that is not a JSON object skipped; none where there is no log."""
    path = profile.run_dir() / "unmatched.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def gaps(profile: Profile, now: datetime | None = None, days: int = GAPS_DAYS, limit: int = GAPS_LIMIT) -> dict:
    """Gaps people hit: the unmatched questions logged in the last `days` days, grouped by their text case-folded with
    its whitespace collapsed, each with how often it was asked and when last, its most recent spelling shown; most asked
    first, then most recent, at most `limit`. Only the question's text, a count and a time leave: who asked, the arm,
    the path and the reason stay in the log."""
    since = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=days)
    total = 0
    groups: dict[str, dict] = {}
    for row in _unmatched_rows(profile):
        at = _when(row.get("at"))
        question = row.get("question")
        if at is None or at < since or not isinstance(question, str):
            continue
        key = " ".join(question.split()).casefold()
        if not key:
            continue
        total += 1
        text = question.strip()
        group = groups.setdefault(key, {"text": text, "count": 0, "last": at})
        group["count"] += 1
        if at >= group["last"]:
            group["last"], group["text"] = at, text
    ranked = sorted(groups.values(), key=lambda g: (-g["count"], -g["last"].timestamp()))[:max(limit, 0)]
    return {"since": _iso(since), "days": days, "total": total,
            "gaps": [{"text": g["text"], "count": g["count"], "last_at": _iso(g["last"])} for g in ranked]}
