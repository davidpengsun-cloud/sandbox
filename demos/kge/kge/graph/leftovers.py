"""The onboard journal and sweep: drop only objects no kept state names.

The caller holds the client's job lease throughout. The three public sets describe
backend objects. Private folder stamps travel with the journal too: an in-place
graph has no generation, but a failed load must still lose its LOADED approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from kge.adapters import graphstore, released as release_records, retrieval
from kge.adapters.warehouse import estate_of
from kge.graph import release
from kge.pipeline import warehouse_versions
from kge.pipeline.profile import HASH

JOURNAL = "onboard-journal.json"


@dataclass(frozen=True)
class Held:
    generations: frozenset[str]
    chunks: frozenset[str]
    versions: frozenset[tuple[str, int]]
    _folders: frozenset[tuple[str, tuple[int, ...]]] = field(default_factory=frozenset, init=False,
                                                           repr=False, compare=False)

    def to_json(self) -> dict:
        """A deterministic JSON value, including the local journal's folder stamps."""
        return {"generations": sorted(self.generations), "chunks": sorted(self.chunks),
                "versions": [list(v) for v in sorted(self.versions)],
                "folders": {name: list(stamp) for name, stamp in sorted(self._folders)}}

    @classmethod
    def from_json(cls, value: dict) -> Held:
        out = cls(frozenset(value["generations"]), frozenset(value["chunks"]),
                  frozenset((unit, version) for unit, version in value["versions"]))
        object.__setattr__(out, "_folders", frozenset((name, tuple(stamp))
                                                   for name, stamp in value.get("folders", {}).items()))
        return out

    def __sub__(self, other: Held) -> Held:
        return Held(self.generations - other.generations, self.chunks - other.chunks,
                    self.versions - other.versions)


def _folders(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if HASH.fullmatch(p.name) and p.is_dir()) if root.is_dir() else []


def _stamp(path: Path) -> tuple[int, ...]:
    if not path.is_file() or graphstore.generation(path) is not None:
        return ()
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def held(prof) -> Held:
    """Everything held by the client's three backends, plus its local folder snapshot."""
    estate = estate_of(prof)
    versions = {(unit, version) for unit, held_versions in estate.held().items() for version in held_versions}
    versions.update((unit, 0) for unit in estate.established())
    out = Held(frozenset(graphstore.generations()), frozenset(retrieval.chunks_tables(prof)), frozenset(versions))
    object.__setattr__(out, "_folders", frozenset((folder.name, _stamp(graphstore.graph_path(folder)))
                                               for folder in _folders(prof.store / prof.client)))
    return out


def _list(path: Path) -> list[dict]:
    """Missing records name nothing; unreadable records refuse cleanup before any drop."""
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise ValueError(f"{path}: expected a list of release records")
    return value


def _record(value: dict) -> Held:
    graph, chunks = value.get("graph"), value.get("chunks")
    generation = graph if isinstance(graph, str) else graphstore.generation_of(graph)
    table = chunks if isinstance(chunks, str) else retrieval.entry_chunks(chunks)
    versions = set(value.get("warehouses", {}).items())
    versions.update((value.get("inputs") or {}).get("warehouses", {}).items())
    return Held(frozenset([generation]) if generation else frozenset(),
                frozenset([table]) if table else frozenset(),
                frozenset(versions))


def _union(*values: Held) -> Held:
    return Held(frozenset().union(*(v.generations for v in values)),
                frozenset().union(*(v.chunks for v in values)),
                frozenset().union(*(v.versions for v in values)))


def _released_records(prof) -> list[dict]:
    """The two ledger identities, with legacy records used only before a ledger exists."""
    root = prof.store / prof.client
    ledger = root / release.SERVED
    records = _list(ledger)[:2]
    kept = _list(root / release.KEPT)
    if ledger.exists():
        return [{**record, "inputs": next((item.get("inputs") for item in kept
                 if release_records.identity(item["identity"]) == release_records.identity(record)), None)}
                for record in records]
    if kept:
        return [{**item["identity"], "inputs": item.get("inputs")} for item in kept[:2]]
    legacy = []
    for folder in _folders(root):
        record = release_records.read(folder)
        if record is not None:
            stamp = max((p.stat().st_mtime_ns for p in release_records.paths(folder) if p.is_file()), default=0)
            legacy.append((stamp, folder.name, record))
    return [record for _, _, record in sorted(legacy, key=lambda item: item[:2], reverse=True)[:2]]


def released(prof) -> Held:
    """Only the two kept releases protect additions to a run's journal snapshot."""
    records = [{**record, "warehouses": release.record_versions(prof, record)}
               for record in _released_records(prof)]
    out = _union(*(_record(record) for record in records))
    hashes = {record["hash"] for record in records if record.get("hash")}
    object.__setattr__(out, "_folders", frozenset((name, ()) for name in hashes))
    return out


def _pointer(prof, folder: Path) -> dict:
    path = graphstore.graph_path(folder)
    versions = warehouse_versions.named(prof.artifacts / prof.client / folder.name)
    for entry in graphstore.held(folder).values():
        versions.update(entry.get("warehouses", {}))
    return {"hash": folder.name, "graph": graphstore.release_entry(path),
            "chunks": retrieval.release_entry(folder), "warehouses": versions}


def protected_records(prof, *, include_published: bool = True) -> list[dict]:
    """Release identities, the current pointer and one most recent completed candidate."""
    from kge.pipeline.objstore import published_records
    root = prof.store / prof.client
    published = published_records(prof) if include_published else None
    records = _released_records(prof) if published is None else list(published)
    current = prof.model_hash()
    if current:
        folder = root / current
        record = release_records.read(folder)
        if record is not None:
            records.append(record)
        if record is None or graphstore.loaded(graphstore.graph_path(folder)):
            records.append(_pointer(prof, folder))
    candidates = []
    for folder in _folders(root):
        path = graphstore.graph_path(folder)
        if release._newer_load(path):
            candidates.append(((folder / release.LOADED).stat().st_mtime_ns, folder.name, folder))
    if candidates:
        records.append(_pointer(prof, max(candidates)[2]))
    return [{**record, "warehouses": release.record_versions(prof, record)} for record in records]


def named(prof) -> Held:
    """The bounded retention floor, preserving warehouses outside the assessed model."""
    records = protected_records(prof)
    versions = set(warehouse_versions.estate_live(prof).items())
    values = [_record(record) for record in records]
    managed = {unit for value in values for unit, _ in value.versions} | {unit for unit, _ in versions}
    for artifact in _folders(prof.artifacts / prof.client):
        managed.update(release.record_versions(prof, {"hash": artifact.name,
                                                     "warehouses": warehouse_versions.named(artifact)}))
    summary = prof.estate() / "summary.json"
    if summary.is_file():
        managed.update(json.loads(summary.read_text()).get("fixture_units", []))
    estate = estate_of(prof)
    versions.update((unit, 0) for unit in estate.established() if unit not in managed)
    for unit, held_versions in estate.held().items():
        if unit not in managed:
            versions.update((unit, version) for version in held_versions)
    values.append(Held(frozenset(), frozenset(), frozenset(versions)))
    return _union(*values)


def _unmark(path: Path) -> None:
    """Keep the check's evidence while withdrawing the failed load's approvals."""
    gate = path.parent / release.GATE
    saved = gate.read_bytes() if gate.is_file() else None
    try:
        release.unmark(path)
    finally:
        if saved is not None:
            gate.write_bytes(saved)


def invalidate(prof, generations=(), chunks=()) -> None:
    """Withdraw local pointers and approvals for retired remote objects before saving work."""
    for folder in _folders(prof.store / prof.client):
        path = graphstore.graph_path(folder)
        generation = graphstore.generation(path)
        table = retrieval.entry_chunks(retrieval.release_entry(folder))
        if generation in generations or table in chunks:
            release.unmark(path)
        if generation in generations:
            path.unlink(missing_ok=True)
        if table in chunks:
            retrieval.forget_chunks(folder)


def prepare_retention(prof) -> None:
    """Invalidate unprotected local remote pointers without dropping any backend object."""
    protected = _union(*(_record(record) for record in protected_records(prof, include_published=False)))
    generations, chunks = set(), set()
    for folder in _folders(prof.store / prof.client):
        generation = graphstore.generation(graphstore.graph_path(folder))
        table = retrieval.entry_chunks(retrieval.release_entry(folder))
        if generation is not None and generation not in protected.generations:
            generations.add(generation)
        if table is not None and table not in protected.chunks:
            chunks.add(table)
    invalidate(prof, generations, chunks)


def _drop(prof, wanted: Held) -> Held:
    folders = _folders(prof.store / prof.client)
    pointers = [(graphstore.graph_path(folder),
                 graphstore.generation(graphstore.graph_path(folder)),
                 retrieval.entry_chunks(retrieval.release_entry(folder))) for folder in folders]
    generations = frozenset(graphstore.drop_generations(sorted(wanted.generations)))
    chunks = frozenset(retrieval.drop_chunks(prof, sorted(wanted.chunks)))
    estate = estate_of(prof)
    versions = set()
    for unit, version in sorted(wanted.versions):
        estate.drop(unit, version)
        versions.add((unit, version))
    for path, generation, table in pointers:
        if generation in generations or table in chunks:
            _unmark(path)
        if generation in generations:
            path.unlink(missing_ok=True)
        if table in chunks:
            retrieval.forget_chunks(path.parent)
    return Held(generations, chunks, frozenset(versions))


def sweep(prof) -> Held:
    """Drop a killed run's unnamed objects; the caller prints lines from the result."""
    return _drop(prof, held(prof) - named(prof))


def drop_new(prof, before: Held) -> Held:
    """Drop this run's additions, preserving prior candidates and anything released."""
    now, kept = held(prof), released(prof)
    dropped = _drop(prof, (now - before) - kept)
    previous, protected = dict(before._folders), dict(kept._folders)
    for name, stamp in sorted(now._folders):
        if not stamp or previous.get(name) == stamp:
            continue
        if name in protected and name not in previous:
            continue
        path = graphstore.graph_path(prof.store / prof.client / name)
        _unmark(path)
        if name not in previous and name not in protected:
            for file in [path, *graphstore.sidecars(path)]:
                file.unlink(missing_ok=True)
    return dropped


def lines(dropped: Held, verb: str) -> list[str]:
    parts = ([f"generation {p}" for p in sorted(dropped.generations)]
             + [f"chunks {t}" for t in sorted(dropped.chunks)]
             + [f"warehouse {u} v{v}" for u, v in sorted(dropped.versions)])
    return [f"onboard: {verb} {part}" for part in parts or ["nothing"]]
