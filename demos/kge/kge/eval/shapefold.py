"""Fold shadow observations into per-shape, per-release-event evidence.

The fold reads release history in file order, the shadow log and each event's compiled Shape.csv. It writes
ShapeStatus rows and STATUS_OF edges where a shape exists. Ask never reads this projection. Each row's canonical
JSON carries its event, stage, event number, disjoint live/new_phrase/via_terms counts, release mismatches and
optional test scores; it contains no question, figure, principal or review evidence.

Each event, including the open release and a release-less bootstrap, is checkpointed before graph writes. A graph row
whose durable checkpoint is missing stops the fold: a pruned or partial log cannot safely recreate its earlier counts.
Checkpoint updates use conditional writes, and repeated observation ids are checked by digest before late data is added.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from kge.adapters.deadline import ExternalCallError
from kge.adapters.graphstore import GraphLocked, batch, connect, graph_q, open_db, store
from kge.adapters.shadowlog import shadow_log
from kge.adapters.shadowlog.record import ID, NAME, line_of
from kge.compiler.standards import STAGES
from kge.eval.maturity import BUCKETS, Live, tally
from kge.graph.history import versions_path
from kge.pipeline.trace import read_jsonl
from kge.ports import SHADOW_COMPARISONS, ShadowObservation, StoreConflict, StoreError

REPORT = "fold.json"
VERSION = 1
COUNTS = Live.KEYS
ABOVE_SPECIFY = frozenset(STAGES[1:])


@dataclass(frozen=True)
class ReleaseEvent:
    """An ordered release-history event and its effective observation window."""
    index: int
    id: str
    release: str | None
    model_hash: str | None
    released_at: datetime | None
    rollback: bool
    start: datetime | None
    end: datetime | None
    open: bool


@dataclass(frozen=True)
class Assigned:
    """Observations assigned by event, mismatches and unattributable input counts."""
    by_event: dict[int, list[dict]]
    mismatched: dict[int, list[dict]]
    unassigned: int
    malformed: int


@dataclass(frozen=True)
class StatusRow:
    """One shape's event counters, stored as canonical JSON."""
    id: str
    shape: str
    release_event: str
    counters: str


@dataclass(frozen=True)
class FoldReport:
    """Public counts for a fold, with no observation payloads.

    observations, unassigned, release_mismatch and malformed count raw records read in this run, not the historical
    total. unattributed counts assigned observations across both retained records and sealed events, whose earlier
    counts survive even after the raw observations are pruned.
    """
    at: str
    target: str
    hash: str | None
    events: int
    observations: int
    rows: int
    unattributed: int
    unassigned: int
    release_mismatch: int
    malformed: int
    written: bool
    skipped: str | None

    def line(self) -> str:
        """A diagnostic suitable for the job log."""
        if self.skipped:
            return f"shape evidence not folded: {self.skipped}"
        name = (self.hash or "served")[:12]
        return (f"folded {self.observations} retained observation(s) over {self.events} release event(s) into "
                f"{self.rows} ShapeStatus on {name}; {self.unassigned} unassigned, "
                f"{self.release_mismatch} retained release mismatch(es)")

    def to_json(self) -> dict:
        """A JSON-ready diagnostic."""
        return asdict(self)


class NotFolded(RuntimeError):
    """The fold could not complete; its message is safe for the operator."""


def parse_time(text: object) -> datetime | None:
    """Parse only timestamps explicitly stated as UTC."""
    if not isinstance(text, str) or not (text.endswith("Z") or text.endswith("+00:00")):
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(timezone.utc) if value.utcoffset() == timedelta(0) else None


def events(rows: Sequence[Mapping]) -> tuple[ReleaseEvent, ...]:
    """Keep history order; a backwards timestamp creates an empty window, never a reordered release."""
    source = [row for row in rows if isinstance(row, Mapping)]
    starts: list[datetime | None] = []
    latest = None
    for row in source:
        when = parse_time(row.get("released_at"))
        if when is not None and (latest is None or when > latest):
            latest = when
        starts.append(latest)
    result = []
    for index, row in enumerate(source):
        release = row.get("release") if isinstance(row.get("release"), str) else None
        model_hash = row.get("model_hash") if isinstance(row.get("model_hash"), str) else None
        name = release or (model_hash or "none")[:12]
        result.append(ReleaseEvent(index, f"{index:04d}-{name}", release, model_hash,
                                   parse_time(row.get("released_at")), row.get("rollback_of") is not None,
                                   starts[index], starts[index + 1] if index + 1 < len(source) else None,
                                   index == len(source) - 1))
    return tuple(result)


def assign(observations: Iterable[Mapping], history: Sequence[ReleaseEvent]) -> Assigned:
    """Attribute each valid observation to its time window and check its served release."""
    by_event: dict[int, list[dict]] = defaultdict(list)
    mismatched: dict[int, list[dict]] = defaultdict(list)
    unassigned = malformed = 0
    for value in observations:
        row = dict(value) if isinstance(value, Mapping) else {}
        when = parse_time(row.get("time"))
        shapes = row.get("shapes")
        if (when is None or not isinstance(row.get("release"), str) or not isinstance(shapes, list)
                or not all(isinstance(shape, str) and NAME.fullmatch(shape) for shape in shapes)
                or row.get("comparison") not in SHADOW_COMPARISONS):
            malformed += 1
            continue
        try:
            if set(row) - {field.name for field in fields(ShadowObservation)} - {"via_terms"}:
                raise ValueError("observation has unrecognised fields")
            named = {field.name: row[field.name] for field in fields(ShadowObservation) if field.name in row}
            for field in ("graph_shapes", "coded_shapes", "shapes"):
                if not isinstance(named[field], list):
                    raise ValueError("shape fields must be lists")
                named[field] = tuple(named[field])
            line_of(ShadowObservation(**named))
            tally([row])
        except (KeyError, TypeError, ValueError):
            malformed += 1
            continue
        event = next((e for e in reversed(history) if e.start is not None and e.start <= when
                      and (e.end is None or when < e.end)), None)
        if event is None:
            unassigned += 1
        elif row["release"] != event.release:
            mismatched[event.index].append(row)
        else:
            by_event[event.index].append(row)
    return Assigned(dict(by_event), dict(mismatched), unassigned, malformed)


def fold(history: Sequence[ReleaseEvent], assigned: Assigned, stages: Mapping[int, Mapping[str, str]],
         tests: Mapping[int, Mapping[str, dict]] = MappingProxyType({})) -> tuple[list[StatusRow], int]:
    """Build deterministic, redacted rows; an observation counts once for each named shape."""
    result: list[StatusRow] = []
    numbers: dict[str, int] = defaultdict(int)
    unattributed = 0
    for event in history:
        declared = stages.get(event.index, {})
        for shape, stage in declared.items():
            if stage in ABOVE_SPECIFY:
                numbers[shape] += 1
        observations = assigned.by_event.get(event.index, [])
        mismatches = assigned.mismatched.get(event.index, [])
        unattributed += sum(not row["shapes"] for row in observations)
        names = {shape for shape, stage in declared.items() if stage in ABOVE_SPECIFY}
        names.update(shape for row in (*observations, *mismatches) for shape in row["shapes"])
        for shape in sorted(names):
            relevant = [row for row in observations if shape in row["shapes"]]
            buckets = tally(relevant)
            contents = {"version": VERSION,
                        "event": {"index": event.index, "release": event.release, "model_hash": event.model_hash,
                                  "released_at": event.released_at.isoformat().replace("+00:00", "Z") if event.released_at else None,
                                  "rollback": event.rollback, "open": event.open},
                        "stage": declared.get(shape), "event_number": numbers[shape],
                        **{bucket: asdict(buckets[bucket]) for bucket in BUCKETS},
                        "release_mismatch": sum(shape in row["shapes"] for row in mismatches),
                        "tests": tests.get(event.index, {}).get(shape)}
            encoded = json.dumps(contents, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            result.append(StatusRow(f"{shape}@{event.id}", shape, event.id, encoded))
    return sorted(result, key=lambda row: (row.shape, row.release_event)), unattributed


def total(counters: Mapping) -> dict[str, int]:
    """Sum every disjoint bucket using the shared maturity counter contract."""
    from kge.eval.maturity import total as total_live
    return asdict(total_live({bucket: Live.of(counters[bucket]) for bucket in BUCKETS}))


def runs(history: Sequence[ReleaseEvent], rows: Sequence[StatusRow], holds: Callable[[Mapping], bool]) -> dict[str, int]:
    """Count consecutive qualifying events; rollbacks and closed shortfalls break the run."""
    indexed = {(row.shape, row.release_event): json.loads(row.counters) for row in rows}
    names = {row.shape for row in rows}
    result = {}
    for shape in names:
        count = 0
        for event in history:
            row = indexed.get((shape, event.id))
            if event.rollback:
                count = 0
            elif row is not None and holds(row):
                count += 1
            elif not event.open:
                count = 0
        result[shape] = count
    return result


def stages_of(prof, model_hash: str) -> dict[str, str]:
    """Read the compiled artifact's declared shape stages, if available."""
    path = Path(prof.artifacts) / prof.client / model_hash / "data" / "nodes" / "Shape.csv"
    try:
        with path.open(encoding="utf-8", newline="") as src:
            return {row["id"]: row["stage"] for row in csv.DictReader(src) if row.get("id") and row.get("stage")}
    except (OSError, UnicodeError, csv.Error):
        return {}


def read_observations(log) -> list[dict]:
    """Read the append-only shadow log, retaining neither question nor raw data in the output."""
    return [row for release in log.releases() for row in log.read(release)]


def _existing_statuses(conn) -> set[str]:
    """Read the projection before saving any checkpoint or replacing a graph row."""
    try:
        return {row["id"] for row in graph_q(conn)("MATCH (s:ShapeStatus) RETURN s.id AS id")}
    except Exception as exc:
        if store().missing_type(exc):
            raise NotFolded("this graph has no ShapeStatus type (built before meta-model 1.6.0)") from exc
        raise


def write(conn, rows: Sequence[StatusRow]) -> int:
    """Replace the graph projection, preserving source evidence outside the graph.

    A batch is not atomic on every backend. An interrupted write can leave a partial projection; a retry reads its
    current rows and rewrites them from durable evidence, including stale-node and edge cleanup.
    """
    query = graph_q(conn)
    try:
        existing = {row["id"] for row in query("MATCH (s:ShapeStatus) RETURN s.id AS id")}
    except Exception as exc:
        if store().missing_type(exc):
            raise NotFolded("this graph has no ShapeStatus type (built before meta-model 1.6.0)") from exc
        raise
    try:
        shapes = {row["id"] for row in query("MATCH (s:Shape) RETURN s.id AS id")}
    except Exception as exc:
        if not store().missing_type(exc):
            raise
        shapes = set()
    with batch(conn) as writer:
        for stale in sorted(existing - {row.id for row in rows}):
            writer.delete_node("ShapeStatus", stale)
        for row in rows:
            if row.id in existing:
                writer.delete_edges("STATUS_OF", "ShapeStatus", row.id, "Shape")
            writer.upsert("ShapeStatus", row.id, {"shape": row.shape, "release_event": row.release_event,
                                                 "counters": row.counters})
            if row.shape in shapes:
                writer.create_edge("STATUS_OF", "ShapeStatus", row.id, "Shape", row.shape)
    return len(rows)


def open_target(prof, model_hash: str | None):
    """Open the candidate or a pinned served graph; the caller owns both handles."""
    if model_hash is None:
        pinned = prof.serve().pin()
        record = pinned.released()
        db = open_db(pinned.graph_path(), prof.run_dir() / "api.pid", (record or {}).get("graph"))
    else:
        db = open_db(prof.at(model_hash).graph_path(), prof.run_dir() / "api.pid")
    try:
        return db, connect(db)
    except Exception:
        db.close()
        raise


def _combine(prior: StatusRow, delta: StatusRow) -> StatusRow:
    """Add only newly arrived observations to a validated sealed row."""
    before, added = json.loads(prior.counters), json.loads(delta.counters)
    for field in ("index", "release", "model_hash", "released_at", "rollback"):
        if before["event"][field] != added["event"][field]:
            raise ValueError("sealed event no longer matches release history")
    if before["stage"] != added["stage"] or before["event_number"] != added["event_number"]:
        raise ValueError("sealed event no longer matches its compiled shape")
    for bucket in BUCKETS:
        old, new = Live.of(before[bucket]), Live.of(added[bucket])
        added[bucket] = asdict(Live.of({key: getattr(old, key) + getattr(new, key) for key in Live.KEYS}))
    added["release_mismatch"] += before["release_mismatch"]
    if added["tests"] is None:
        added["tests"] = before["tests"]
    return StatusRow(delta.id, delta.shape, delta.release_event,
                     json.dumps(added, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def _seal_row(row: Mapping, event: ReleaseEvent | None) -> StatusRow:
    """Validate sealed counter schema and its event identity before trusting its evidence."""
    try:
        result = StatusRow(**row)
        data = json.loads(result.counters)
        if not isinstance(data, dict) or set(data) != {"version", "event", "stage", "event_number", *BUCKETS,
                                                       "release_mismatch", "tests"}:
            raise ValueError("sealed counters have unexpected fields")
        if type(data["version"]) is not int or data["version"] != VERSION:
            raise ValueError("sealed counters have an unsupported version")
        if type(data["event_number"]) is not int or data["event_number"] < 0:
            raise ValueError("sealed event number is invalid")
        if type(data["release_mismatch"]) is not int or data["release_mismatch"] < 0:
            raise ValueError("sealed mismatch count is invalid")
        if data["stage"] is not None and data["stage"] not in STAGES:
            raise ValueError("sealed stage is invalid")
        meta = data["event"]
        if not isinstance(meta, dict) or set(meta) != {"index", "release", "model_hash", "released_at", "rollback", "open"}:
            raise ValueError("sealed event metadata is invalid")
        if type(meta["index"]) is not int or meta["index"] < 0 or type(meta["rollback"]) is not bool or type(meta["open"]) is not bool:
            raise ValueError("sealed event metadata is invalid")
        for bucket in BUCKETS:
            Live.of(data[bucket])
        if event is not None:
            released_at = event.released_at.isoformat().replace("+00:00", "Z") if event.released_at else None
            if (meta["index"], meta["release"], meta["model_hash"], meta["released_at"], meta["rollback"]) != (
                    event.index, event.release, event.model_hash, released_at, event.rollback):
                raise ValueError("sealed event no longer matches release history")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("sealed event counters are invalid") from exc


def _observation_digest(row: Mapping) -> str:
    """A stable digest validates a repeated ID without retaining its potentially sensitive payload."""
    value = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_state(row: StatusRow, event: ReleaseEvent) -> StatusRow:
    """Refresh the open flag when history closes an event even if its compiled artifact is unavailable."""
    contents = json.loads(row.counters)
    contents["event"]["open"] = event.open
    return StatusRow(row.id, row.shape, row.release_event,
                     json.dumps(contents, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def _seal_release(event: ReleaseEvent) -> str:
    """Give release-less bootstrap events a valid namespace; the history-indexed id keeps it distinct."""
    return event.release if event.release is not None else "bootstrap"


def _time_text(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _durable(history: Sequence[ReleaseEvent], assigned: Assigned, stages: Mapping, seals,
             existing: frozenset[str] = frozenset()) -> tuple[list[StatusRow], int]:
    """Reconcile checkpoints with unseen IDs; refuse an existing projection without its source before any write.

    Open events are checkpointed too, so a repeated fold can prove earlier evidence survived. The bootstrap namespace
    is paired with a history-indexed event id, keeping it separate from an actual release named bootstrap.
    """
    saved, windows, manifest_version, known_ids = seals.snapshot()
    event_index = {(_seal_release(event), event.id): event for event in history}
    if set(saved) - set(event_index):
        raise ValueError("sealed events do not match release history")
    protected = known_ids | {row["id"] for snapshot in saved.values() for row in snapshot["rows"]}
    if existing - protected:
        raise ValueError("existing shape evidence has no matching durable checkpoint")
    owners = {}
    for key, snapshot in saved.items():
        if snapshot["unattributed"] > len(snapshot["observation_digests"]):
            raise ValueError("sealed counts exceed their observation ids")
        for raw in snapshot["rows"]:
            row = _seal_row(raw, event_index.get(key))
            counts = json.loads(row.counters)
            total = sum(Live.of(counts[bucket]).compared for bucket in BUCKETS) + counts["release_mismatch"]
            if total > len(snapshot["observation_digests"]):
                raise ValueError("sealed counts exceed their observation ids")
        for identity in snapshot["observation_digests"]:
            if identity in owners and owners[identity] != key:
                raise ValueError("an observation id belongs to more than one event")
            owners[identity] = key
    raw = {}
    for event in history:
        for row in (*assigned.by_event.get(event.index, []), *assigned.mismatched.get(event.index, [])):
            identity = row.get("id")
            if identity in raw:
                raise ValueError("observations must have distinct valid ids before folding")
            raw[identity] = _observation_digest(row)
    rebuild = set()
    rebuilt_tests = {}
    for key, snapshot in saved.items():
        event = event_index[key]
        prior = windows.get(key)
        current_start, current_end = _time_text(event.start), _time_text(event.end)
        if prior is not None and (prior["start"], prior["end"]) == (current_start, current_end):
            continue
        safe_close = (prior is not None and prior["start"] == current_start and prior["end"] is None
                      and current_end is not None and (not snapshot["observation_digests"]
                           or (parse_time(prior["min_time"]) is not None and parse_time(prior["max_time"]) is not None
                               and event.start is not None and parse_time(prior["min_time"]) >= event.start
                               and parse_time(prior["max_time"]) < event.end)))
        if safe_close:
            continue
        if any(raw.get(identity) != digest for identity, digest in snapshot["observation_digests"].items()):
            raise ValueError("the release window changed without complete retained observations")
        rebuild.add(key)
    if rebuild:
        declared = {index: dict(rows) for index, rows in stages.items()}
        for key in rebuild:
            event = event_index[key]
            for old in saved[key]["rows"]:
                data = json.loads(old["counters"])
                if data["stage"] is not None:
                    declared.setdefault(event.index, {}).setdefault(old["shape"], data["stage"])
                if data["tests"] is not None:
                    rebuilt_tests.setdefault(event.index, {})[old["shape"]] = data["tests"]
            for identity in saved[key]["observation_digests"]:
                owners.pop(identity, None)
        stages = declared
        saved = {key: row for key, row in saved.items() if key not in rebuild}
        windows = {key: row for key, row in windows.items() if key not in rebuild}
    filtered: dict[int, list[dict]] = {}
    mismatched: dict[int, list[dict]] = {}
    covered: dict[int, dict[str, str]] = {}
    raw_ids = set()
    records, next_windows = {}, {}
    for event in history:
        event_rows = assigned.by_event.get(event.index, [])
        event_bad = assigned.mismatched.get(event.index, [])
        values = [row.get("id") for row in (*event_rows, *event_bad)]
        if any(not isinstance(value, str) or not ID.fullmatch(value) for value in values) or len(values) != len(set(values)):
            raise ValueError("observations must have distinct valid ids before folding")
        if raw_ids.intersection(values):
            raise ValueError("an observation id belongs to more than one event")
        raw_ids.update(values)
        prior = saved.get((_seal_release(event), event.id))
        known = dict(prior["observation_digests"]) if prior else {}
        for row in (*event_rows, *event_bad):
            identity = row["id"]
            digest = _observation_digest(row)
            if identity in owners and owners[identity] != (_seal_release(event), event.id):
                raise ValueError("an observation id belongs to more than one event")
            if identity in known and known[identity] != digest:
                raise ValueError("an observation id has conflicting contents")
            known[identity] = digest
        previous = prior["observation_digests"] if prior else {}
        covered[event.index] = known
        filtered[event.index] = [row for row in event_rows if row["id"] not in previous]
        mismatched[event.index] = [row for row in event_bad if row["id"] not in previous]
    new, unattributed = fold(history, Assigned(filtered, mismatched, assigned.unassigned, assigned.malformed), stages, rebuilt_tests)
    merged = {(row.shape, row.release_event): row for row in new}
    for (release, event_id), snapshot in saved.items():
        unattributed += snapshot["unattributed"]
        for raw in snapshot["rows"]:
            old = _seal_row(raw, event_index.get((release, event_id)))
            key = (old.shape, old.release_event)
            merged[key] = _combine(old, merged[key]) if key in merged else _event_state(old, event_index[(release, event_id)])
    rows = sorted(merged.values(), key=lambda row: (row.shape, row.release_event))
    for event in history:
        release = _seal_release(event)
        old = saved.get((release, event.id))
        event_rows = [row for row in rows if row.release_event == event.id]
        new_unattributed = sum(not row["shapes"] for row in filtered.get(event.index, []))
        snapshot = {"version": VERSION, "release": release, "event": event.id,
                    "observation_digests": dict(sorted(covered[event.index].items())), "rows": [asdict(row) for row in event_rows],
                    "unattributed": (old["unattributed"] if old else 0) + new_unattributed}
        key = (release, event.id)
        previous = windows.get(key) or {}
        times = [row["time"] for row in (*filtered.get(event.index, []), *mismatched.get(event.index, []))]
        times = [parse_time(value) for value in times]
        times.extend(parse_time(previous.get(field)) for field in ("min_time", "max_time"))
        times = [value for value in times if value is not None]
        records[key] = snapshot
        next_windows[key] = {"start": _time_text(event.start), "end": _time_text(event.end),
                             "min_time": _time_text(min(times)) if times else None,
                             "max_time": _time_text(max(times)) if times else None}
    if records != saved or next_windows != windows:
        seals.commit(records, next_windows, manifest_version, frozenset(protected | {row.id for row in rows}))
    return rows, unattributed


def _report(prof, report: FoldReport):
    """Publish a report under the same conditional-write lock used by a preceding run's finalizer."""
    from kge.adapters.objstore import open_store

    try:
        report_store = open_store(str(Path(prof.run_dir()).resolve()), private=True)
        data = json.dumps(report.to_json(), sort_keys=True, allow_nan=False).encode("utf-8")
        candidate = _candidate_store(prof, report.hash) if report.target == "candidate" else None
        if candidate:
            candidate_store, key = candidate
            for _ in range(3):
                _, version = candidate_store.get_version(key)
                try:
                    candidate_store.put_version(key, data, version)
                    break
                except StoreConflict:
                    continue
            else:
                raise StoreError("the candidate fold report changed concurrently")
        for _ in range(3):
            _, version = report_store.get_version(REPORT)
            try:
                return report_store, report_store.put_version(REPORT, data, version)
            except StoreConflict:
                continue
        raise StoreError("the fold report changed concurrently")
    except (OSError, StoreError):
        raise NotFolded("the fold report could not be written") from None


def _candidate_store(prof, model_hash):
    from kge.adapters.objstore import open_store
    from kge.adapters.shadowlog.record import release_segment
    from kge.pipeline.settings import settings

    if not isinstance(model_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", model_hash):
        return None
    url = (settings().STORE_URL or "").strip()
    if url:
        return open_store(url, private=True), f"fold-reports/{release_segment(prof.client)}/{model_hash}.json"
    return open_store(str(Path(prof.run_dir()).resolve()), private=True), f"fold-reports/{model_hash}.json"


def candidate_report(prof, model_hash):
    """Read the candidate's public fold diagnostic without pulling job work into the service."""
    try:
        candidate = _candidate_store(prof, model_hash)
        data = candidate[0].get(candidate[1]) if candidate else None
        result = json.loads(data) if data is not None else None
    except (OSError, StoreError, UnicodeError, ValueError):
        return None
    reasons = {None, "fold is in progress; mutex release is pending", "the release history is empty",
               "this graph has no ShapeStatus type (built before meta-model 1.6.0)",
               "the graph is open in another process (the API): stop it before folding",
               "the graph store could not complete the fold", "the shape evidence store could not complete the fold",
               "the shape evidence files could not be read or written", "the shape evidence or release history is invalid",
               "the fold report could not be written"}
    counts = ("events", "observations", "rows", "unattributed", "unassigned", "release_mismatch", "malformed")
    if (isinstance(result, dict) and set(result) == {f.name for f in fields(FoldReport)}
            and result.get("target") == "candidate" and result.get("hash") == model_hash
            and parse_time(result.get("at")) is not None
            and all(type(result.get(k)) is int and result[k] >= 0 for k in counts)
            and type(result.get("written")) is bool and (result.get("skipped") is None or isinstance(result["skipped"], str))
            and result.get("skipped") in reasons
            and (result["written"] == (result["skipped"] is None))):
        return result
    return None


def last_report(prof) -> dict | None:
    """Read the last complete report, or return None if it is unavailable."""
    try:
        result = json.loads((Path(prof.run_dir()) / REPORT).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def run(prof, *, model_hash: str | None = None, log=None, conn=None, now=None, seals=None) -> FoldReport:
    """Serialize the whole fold: checkpoints alone cannot fence a later multi-write graph projection.

    A busy or unreadable mutex leaves the previous report alone, since another holder may be writing it. A crashed
    holder is never taken over automatically; an operator must confirm its graph operations stopped before recovery.
    """
    from kge.adapters.shadowlog.foldlock import FoldBusy, fold_mutex, held
    try:
        with held(fold_mutex(prof.run_dir(), prof.client)):
            current = now or datetime.now(timezone.utc)
            at = current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            target = "candidate" if model_hash is not None else "served"
            report_store, pending_version = _report(
                prof, FoldReport(at, target, model_hash, 0, 0, 0, 0, 0, 0, 0, False,
                                 "fold is in progress; mutex release is pending"))
            try:
                candidate = _candidate_store(prof, model_hash) if target == "candidate" else None
                candidate_version = candidate[0].get_version(candidate[1])[1] if candidate else None
            except (OSError, StoreError):
                raise NotFolded("the fold report could not be read") from None
            report = _run_locked(prof, model_hash=model_hash, log=log, conn=conn, now=now, seals=seals)
        if report.written:
            try:
                data = json.dumps(report.to_json(), sort_keys=True, allow_nan=False).encode("utf-8")
                if candidate:
                    try:
                        candidate[0].put_version(candidate[1], data, candidate_version)
                    except StoreConflict:
                        pass
                report_store.put_version(REPORT, data, pending_version)
            except StoreConflict:
                pass
            except (OSError, StoreError):
                raise NotFolded("the fold report could not be finalized") from None
        return report
    except (FoldBusy, StoreError) as exc:
        raise NotFolded("the fold mutex could not be acquired or released; inspect it before retrying") from exc


def _run_locked(prof, *, model_hash: str | None = None, log=None, conn=None, now=None, seals=None) -> FoldReport:
    """Fold the release history and shadow log into the requested graph and a public diagnostic."""
    from kge.adapters.shadowlog import shadow_seals

    current = now or datetime.now(timezone.utc)
    at = current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    target = "candidate" if model_hash is not None else "served"
    history: tuple[ReleaseEvent, ...] = ()
    observations: list[dict] = []
    assigned = Assigned({}, {}, 0, 0)
    rows: list[StatusRow] = []
    unattributed = 0
    try:
        path = versions_path(prof)
        history = events(read_jsonl(path)) if path.exists() else ()
        if not history:
            report = FoldReport(at, target, model_hash, 0, 0, 0, 0, 0, 0, 0, False, "the release history is empty")
            _report(prof, report)
            return report
        log = log if log is not None else shadow_log(prof.run_dir(), prof.client)
        seals = seals if seals is not None else shadow_seals(prof.run_dir(), prof.client)
        observations = read_observations(log)
        assigned = assign(observations, history)
        stages = {event.index: stages_of(prof, event.model_hash) for event in history if event.model_hash}
        if conn is None:
            db, connection = open_target(prof, model_hash)
            try:
                existing = _existing_statuses(connection)
                rows, unattributed = _durable(history, assigned, stages, seals, frozenset(existing))
                write(connection, rows)
            finally:
                try:
                    connection.close()
                finally:
                    db.close()
        else:
            existing = _existing_statuses(conn)
            rows, unattributed = _durable(history, assigned, stages, seals, frozenset(existing))
            write(conn, rows)
        report = FoldReport(at, target, model_hash, len(history), len(observations), len(rows), unattributed,
                            assigned.unassigned, sum(map(len, assigned.mismatched.values())), assigned.malformed, True, None)
        return report
    except (ExternalCallError, GraphLocked, StoreError, OSError, ValueError, NotFolded) as exc:
        if isinstance(exc, GraphLocked):
            reason = "the graph is open in another process (the API): stop it before folding"
        elif isinstance(exc, NotFolded):
            reason = str(exc)
        elif isinstance(exc, ExternalCallError):
            reason = "the graph store could not complete the fold"
        elif isinstance(exc, StoreError):
            reason = "the shape evidence store could not complete the fold"
        elif isinstance(exc, OSError):
            reason = "the shape evidence files could not be read or written"
        else:
            reason = "the shape evidence or release history is invalid"
        report = FoldReport(at, target, model_hash, len(history), len(observations), len(rows), unattributed,
                            assigned.unassigned, sum(map(len, assigned.mismatched.values())), assigned.malformed, False, reason)
        try:
            _report(prof, report)
        except NotFolded:
            pass
        raise NotFolded(reason) from exc
