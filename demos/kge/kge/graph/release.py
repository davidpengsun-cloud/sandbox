"""Release a loaded graph: the client's current pointer swapped to it.

    python3 -m kge.graph.release [--profile <id>] --hash <model_hash>                  # make release [HASH=<model_hash>]
    python3 -m kge.graph.release [--profile <id>] --hash <model_hash> --mark-loaded    # make load's last step
    python3 -m kge.graph.release [--profile <id>] --hash <model_hash> --sign-off --as <principal>   # make sign-off PRINCIPAL=
    python3 -m kge.graph.release [--profile <id>] --hash <model_hash> --ungated        # make graph's and make demo's release

Each compiled artifact is loaded into a graph of its own, in the client's graph
store, in a folder named by the artifact's model hash:
<store>/<client>/<model_hash>/graph.ladybug (Profile.graph_path). make load
loads one there as a candidate: kge/graph/load.py, then kge/pipeline/run.py on it,
which replays the runtime overlay; and last, --mark-loaded, which finds the
graph whole (its GraphModel the artifact's, its Release the profile's client's)
and writes the folder's LOADED marker, naming the store whose graph it is, the
client, the artifact, the release and how many chunks the pipeline wrote. A
load that starts again on a hash takes its store's entry out of the marker
before it replaces the graph (kge/graph/load.py open_graph), once it holds the
graph's lock, so a graph whose load did not finish is never LOADED. The
marker is JSON, an entry keyed by the store, ladybug: the form it has had since
there were two stores, so an older folder's marker still reads, and an entry
for a store since removed is never this graph's. Beside it, the
post-load check (make margin, kge/eval/margin.py) writes its verdict on the graph,
the folder's GATE marker, an entry per store the same way: whether it passed,
its run and the hash. A load that starts again on a hash takes the store's
entry out of GATE with LOADED's, so a check made on the graph it replaces
never stands for the new one.

Then the steward's sign-off, onboarding's step 8 (issue #40): --sign-off --as
<principal> (make sign-off PRINCIPAL=) writes the folder's SIGNOFF marker, the
same way, {principal, at, run_id, model_hash}, once the graph is LOADED, its
GATE passed, and the live table's grants against the candidate's role templates let the
principal perform certification (kge/pipeline/roles.py: no principal is named here).
A reload takes it out with GATE's.

The runtime serves the graph of the artifact the client's current pointer
names, <artifacts>/<client>/current, a symlink to <model_hash> beside it, and
reads it once, when it starts (Engine pins its profile). Releasing swaps the
pointer to a hash whose artifact is whole and the profile's client's and whose
graph is LOADED, and through the onboarding gate: its GATE passed
and its SIGNOFF made on that check, or released before (its RELEASED marker),
so a rollback needs no new sign-off. Reloading current clears that marker and
needs a new check and sign-off. The swap is a new link under a temporary name,
then os.replace over the old one, which renames it in
one step, so current names one hash or the other at every instant, never none.
Anything else is refused, exit 2, naming why, and current is left as it was.
A release writes the folder's RELEASED marker, an entry per store, {model_hash,
at, signed_off_by, run_id}, where it has none, and one for the hash current
named before (it was released: a build made before the marker), unless a load
has invalidated its approvals (RELOADED, an entry per store). That record stays
across loads, so moving current away cannot grant approval to a reloaded graph.
A reload takes RELEASED out with LOADED's. --ungated is make graph's and make
demo's release, the developer's bootstrap: no check and no sign-off asked, and it says so
(signed_off_by null). A process serving the old graph keeps serving it until it
restarts. The old folders stay, artifact and graph alike: releasing an old
hash again needs no load.
Once a release is made, the graph store and retrieval drop what nothing reads any more (prune: on BigQuery, the
generations of graph tables and the chunks tables no hash's graph names, nor the one just released or the one released
before it; on Ladybug and the graph's own vectors, nothing, the folders stay). A release snapshots the units' manifests from the state backend
(kge/graph/history.py), so a BigQuery backend that cannot be signed in to refuses
it in one line, current as it was.

Names no client: the profile does.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable
import json
import os
import secrets
import sys
import logging
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kge.pipeline.profile import CURRENT, HASH, Profile, ProfileError, resolve, shown
from kge.pipeline.settings import settings
from kge.adapters import appstate
from kge.adapters.graphstore import LOADED, held
from kge.adapters.warehouse import estate_of
from kge.pipeline import unit_history, warehouse_versions

GATE, SIGNOFF, RELEASED = "GATE", "SIGNOFF", "RELEASED"
RELOADED = "RELOADED"
CHANGES = "CHANGES"
SERVED = "served.json"
INPUTS = "INPUTS"
KEPT = "kept.json"
CERTIFICATION = "certification"
STEWARD = "make sign-off PRINCIPAL=<steward-email> HASH={}"


if __name__ == "__main__":
    sys.modules.setdefault("kge.graph.release", sys.modules[__name__])


class Unreleasable(Exception):
    """A hash this process will not release, or a graph it will not mark loaded, and why."""


class PostReleaseFailed(Exception):
    """The release succeeded and stays served, but its automatic evaluation failed."""


def store_of(graph: Path) -> str:
    """The store graph is kept by, which keys its entry in each marker. The graph store this process runs on names its
    own: its graph in the folder, and each record a release made of it (kge/adapters/released.py paths), so a server's
    graph, the release record that selects the generation it opens (release.json), reads the same markers as its hash's
    graph. Another store's graph left in the folder (one made before a switch, graph.<store>) keeps its own entry."""
    from kge.adapters import released
    from kge.adapters.graphstore import store
    own, graph = store(), Path(graph)
    if graph == own.path(graph.parent) or graph in released.paths(graph.parent):
        return own.name
    return graph.name.removeprefix("graph.")


def loaded(graph: Path) -> dict | None:
    """The LOADED entry of graph's store, in graph's folder; None if its load has not finished."""
    return held(Path(graph).parent).get(store_of(graph))


def marked(graph: Path, name: str) -> dict | None:
    """graph's store's entry in the marker name beside it; None if it has none."""
    return held(Path(graph).parent, name).get(store_of(graph))


def write_marker(folder: Path, entries: dict, name: str = LOADED) -> None:
    """The named marker in folder, LOADED by default, replaced whole (a temporary file renamed over
    it); removed if it holds no entry."""
    folder = Path(folder)
    path = folder / name
    if not entries:
        path.unlink(missing_ok=True)
        return
    tmp = folder / f".{name}.{os.getpid()}.{secrets.token_hex(4)}"
    tmp.write_text(json.dumps(entries, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def unmark(graph: Path) -> None:
    """graph's store taken out of the LOADED marker beside it, and out of its GATE, SIGNOFF and RELEASED: its graph is about
    to be replaced, and a check, a sign-off or a release of the old graph says nothing of the new one. Record that first
    in RELOADED, kept across loads and releases: this graph can never qualify for the legacy RELEASED backfill again,
    though a new check and sign-off, or the developer's bootstrap, can release it normally."""
    from kge.adapters import released
    (Path(graph).parent / released.PRE_EVAL).unlink(missing_ok=True)
    put(graph, RELOADED, {"at": now()})
    for name in (LOADED, GATE, SIGNOFF, RELEASED, CHANGES):
        entries = held(Path(graph).parent, name)
        if entries.pop(store_of(graph), None) is not None or (Path(graph).parent / name).exists():
            write_marker(Path(graph).parent, entries, name)


def mark_changes(graph: Path, record: dict) -> None:
    """Record the lock and estate changes checked by this load, for its steward."""
    put(graph, CHANGES, record)


def changes(graph: Path) -> dict | None:
    """The changes recorded after this load, or None before one was recorded."""
    return marked(graph, CHANGES)


def gate(graph: Path) -> dict | None:
    """The GATE entry of graph's store, in graph's folder: the post-load check's verdict on it (kge/eval/margin.py), {passed,
    run_id, model_hash}; None if it has not been checked since it was loaded."""
    return held(Path(graph).parent, GATE).get(store_of(graph))


def reset_marks(prof: Profile, restored_hash: str) -> None:
    """Invalidate graphs whose kept packs the last unit's reset discards, on every store. The restored base retains
    its marks and newly kept pack. Clear LOADED too, so releasing the rebuilt base cannot backfill RELEASED on the
    previous current graph. Other hashes need a fresh load, check and sign-off. Keep release records and served.json:
    they pin generations for servers still following the previous release, not permission to release again."""
    for folder in (prof.store / prof.client).glob("*"):
        if folder.name == restored_hash or not HASH.fullmatch(folder.name) or not folder.is_dir():
            continue
        for name in (LOADED, GATE, SIGNOFF, RELEASED, CHANGES):
            (folder / name).unlink(missing_ok=True)


def state(prof: Profile, model_hash: str) -> dict:
    """Release state from marker files and current only; never opens a graph."""
    graph = prof.at(model_hash).graph_path()
    checked, signed = marked(graph, GATE), marked(graph, SIGNOFF)
    return {"model_hash": model_hash, "loaded": loaded(graph) is not None,
            "gate": ({"passed": checked.get("passed"), "run_id": checked.get("run_id")} if checked else None),
            "signed_off": ({k: signed.get(k) for k in ("principal", "at", "run_id")} if signed else None),
            "changes": changes(graph), "released": marked(graph, RELEASED) is not None, "current": prof.model_hash() == model_hash}


def put(graph: Path, name: str, entry: dict) -> None:
    """entry written into the marker name beside graph, for graph's store, the other stores' entries kept."""
    entries = held(Path(graph).parent, name)
    entries[store_of(graph)] = entry
    write_marker(Path(graph).parent, entries, name)


def mark_gate(graph: Path, entry: dict) -> None:
    """The post-load check's verdict on graph written into the GATE marker beside it, for graph's store."""
    put(graph, GATE, entry)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def checked(graph: Path, model_hash: str) -> dict:
    """graph's post-load check, passed: its GATE entry. Unreleasable, naming the next step, if graph is not LOADED for
    model_hash on its store, or not checked since it was loaded, or its check failed."""
    entry = loaded(graph)
    if entry is None or not Path(graph).exists() or entry.get("model_hash") != model_hash:
        raise Unreleasable(f"{shown(graph)} is not LOADED on {store_of(graph)}: make load HASH={model_hash} loads it, then make "
                           f"margin HASH={model_hash} checks it")
    verdict = gate(graph)
    if verdict is None or verdict.get("model_hash") != model_hash:
        raise Unreleasable(f"{model_hash} is not through the onboarding gate: not checked since it was loaded, not signed off: "
                           f"make margin HASH={model_hash} checks it, then {STEWARD.format(model_hash)} signs it off")
    if verdict.get("passed") is not True:
        raise Unreleasable(f"{model_hash} is not through the onboarding gate: its post-load check failed (run "
                           f"{verdict.get('run_id')}), so it stays a candidate; make margin HASH={model_hash} names what failed")
    return verdict


def sign_off(prof: Profile, model_hash: str, principal: str = "", *, role: str | None = None) -> dict:
    """The steward's sign-off on model_hash's graph, onboarding's step 8: written into the SIGNOFF marker beside it, for
    this store, once its graph is LOADED, its post-load check passed (GATE), and the live table grants principal
    certification in the candidate roles. Its entry; Unreleasable, nothing written, otherwise."""
    graph = prof.at(model_hash).graph_path()
    verdict = checked(graph, model_hash)
    from kge.pipeline import access_table, roles
    candidates = roles.candidate_roles(prof, model_hash)
    if role is not None:
        if principal:
            raise Unreleasable("sign off as a person or as a role, never both")
        if role == access_table.ACCESS_ADMIN or CERTIFICATION not in candidates.get(role, {}).get("processes", []):
            raise Unreleasable(f"role {role} may not perform {CERTIFICATION} in the candidate")
        principal = f"role:{role}"
    else:
        people = access_table.Access.for_profile(prof)
        try:
            principal = access_table.normalize_username(principal)
        except ValueError as exc:
            raise Unreleasable(f"invalid principal may not sign off: {exc}") from None
        allowed = people.permissions(principal, candidates)["sign_off"]
        everyone = people.people()
        may = sorted(p for p in everyone if people.permissions(p, candidates)["sign_off"])
        if not allowed:
            who = f"may not perform {CERTIFICATION} in" if principal in everyone else "is no principal of"
            raise Unreleasable(f"{principal} {who} the graph of {model_hash}, so may not sign it off; sign off as one whose role may "
                               f"perform {CERTIFICATION}: {', '.join(may) or 'none'}")
    entry = {"principal": principal, "at": now(), "run_id": verdict["run_id"], "model_hash": model_hash}
    put(graph, SIGNOFF, entry)
    try:
        unit_history.record(prof, "sign-off", principal, {"model_hash": model_hash, "run_id": verdict["run_id"]},
                            draft_units(prof.artifacts / prof.client / model_hash))
    except Exception as exc:
        logging.getLogger(__name__).warning("sign-off took effect; unit history could not be recorded: %s", exc)
    return entry


def moved_inputs(prof: Profile, model_hash: str) -> list[str]:
    """Every recorded input whose bytes moved since onboarding, including added fixture files."""
    from kge.compiler.inputs import read_declared
    marker = prof.store / prof.client / model_hash / INPUTS
    if not marker.is_file():
        return []
    record = json.loads(marker.read_text())
    folder = prof.clients / prof.client
    moved = []
    for name, digest in record.get("files", {}).items():
        path = folder / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            moved.append(name)
    declared = read_declared(folder)
    fixture = declared.fixture if declared else "fixture"
    if record.get("fixture_sha256") and fixture:
        root = folder / fixture
        digest = hashlib.sha256()
        files = sorted(p for p in root.rglob("*") if p.is_file()
                       and not any(part.startswith(".") for part in p.relative_to(root).parts))
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        if not root.is_dir() or digest.hexdigest() != record["fixture_sha256"]:
            moved.append(fixture + "/")
    return sorted(set(moved))


def kept(prof: Profile) -> list[dict]:
    """The two release snapshots, in the same order as served.json."""
    path = prof.store / prof.client / KEPT
    if not path.is_file():
        return []
    from kge.adapters.released import content_inputs
    return [{**{key: value for key, value in entry.items() if key != "roster"},
             "inputs": content_inputs(entry.get("inputs")), "audit": entry.get("audit", {})}
            for entry in json.loads(path.read_text())]


def _newer_load(graph: Path) -> bool:
    """A completed load or sign-off after this hash's last release belongs to a new candidate.
    Marker times distinguish a new load within the release id's second; older records use RELEASED's time.
    """
    from kge.adapters import released
    if loaded(graph) is None:
        return False
    record = released.path(graph.parent)
    if not record.is_file():
        record = graph.parent / RELEASED
    if not record.is_file():
        return True
    at = record.stat().st_mtime_ns
    return any(path.is_file() and path.stat().st_mtime_ns > at
               for path in (graph.parent / LOADED, graph.parent / SIGNOFF))


def keep_release(prof: Profile, entry: dict) -> None:
    """Record the release just made and its predecessor, including releases of one model hash."""
    from kge.adapters.released import content_inputs, identity
    entry = {**{key: value for key, value in entry.items() if key != "roster"},
             "inputs": content_inputs(entry.get("inputs")), "audit": entry.get("audit", {})}
    prior = kept(prof)
    ledger = served(prof.store / prof.client)
    for old in ledger:
        if not old.get("release") and all(identity(item["identity"]) != old for item in prior):
            prior.append({"identity": old, "inputs": None, "audit": {},
                          "tarball": f"releases/{prof.client}/{old['hash']}.tar.gz",
                          "snapshot": f"versions/{old['hash']}/"})
    prior.sort(key=lambda item: next((i for i, old in enumerate(ledger) if identity(item["identity"]) == old), len(ledger)))
    entries = [entry] + [old for old in prior if identity(old["identity"]) != identity(entry["identity"])][:1]
    path = prof.store / prof.client / KEPT
    tmp = path.with_name(f".{KEPT}.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(entries, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _rollback_release(prof: Profile, saved: dict, *, defer_retention: bool = False) -> tuple[str | None, str]:
    """Restore a kept release without making another identity or rebuilding its archive."""
    from kge.adapters import released
    from kge.graph import history
    from kge import packstore
    identity = saved["identity"]
    model_hash, was = identity["hash"], prof.model_hash()
    if not identity.get("release") and model_hash == was:
        raise Unreleasable("the previous same-hash release was published before release ids: its original tarball is not kept")
    checked = history.review_restore(prof, saved)
    store = packstore.for_profile(prof)
    names = history._input_names(prof)
    captured = {name: store.read(name) for name in names if store.exists(name)}
    removed_units = {name.split("/")[1] for name in captured if name.startswith("units/")} - {
        name.split("/")[1] for name in checked[0] if name.startswith("units/")}
    captured_state = history._capture_store(prof)
    before_runtime = history.capture_runtime(prof, model_hash)
    graph = prof.at(model_hash).graph_path()
    candidate_unreleased = marked(graph, RELEASED) is None
    root = prof.store / prof.client
    records = [root / KEPT, root / SERVED, prof.run_dir() / "rollback.json", history.versions_path(prof)]
    before_records = {p: p.read_bytes() if p.is_file() else None for p in records}
    try:
        history.restore_release(prof, saved, checked=checked)
        graph = prof.at(model_hash).graph_path()
        record = released.previous(graph.parent, identity)
        if identity.get("graph"):
            history.restore_runtime(prof, model_hash, {name: data for name, data in before_runtime.items()
                                                       if name.startswith("graph/") and name not in {
                                                           "graph/" + released.RECORD, "graph/" + released.PRE_EVAL,
                                                           "graph/" + released.POST_EVAL}})
        released.write(graph.parent, model_hash, record["graph"], record["chunks"], record["warehouses"],
                       identity.get("release"), saved.get("inputs"), saved.get("audit", {}))
        if candidate_unreleased:
            for name in (RELEASED, SIGNOFF):
                entries = held(graph.parent, name)
                entries.pop(store_of(graph), None)
                write_marker(graph.parent, entries, name)
        swap(prof, model_hash)
        history.record_release(prof, model_hash, was, True, False, None, now(), pack_restored=True)
        keep_release(prof, saved)
        served(root, identity)
    except BaseException:
        for name in history._input_names(prof) - captured.keys():
            store.delete(name)
        for name, data in captured.items():
            store.write(name, data)
        history._restore_captured_store(prof, captured_state)
        history.mirror_runtime(prof, model_hash, before_runtime)
        for name in (checked[1] or {}).keys() - before_runtime.keys():
            if name.startswith("warehouses/"):
                (prof.warehouses / name.removeprefix("warehouses/")).unlink(missing_ok=True)
        for path, data in before_records.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        if was is not None and prof.model_hash() != was:
            swap(prof, was)
        raise
    try:
        history._cleanup_removed_units(prof, removed_units, prune_warehouses=not defer_retention)
    except Exception as exc:
        logging.getLogger(__name__).warning("release restored; retention failed: %s", exc)
    try:
        post_release(prof.serve())
    finally:
        if not defer_retention:
            finish(prof, model_hash)
    return was, f"restored the previous release {model_hash}"


def through(graph: Path, model_hash: str) -> dict:
    """model_hash's graph through the onboarding gate, steps 7 and 8: its SIGNOFF entry, made on its GATE's passed check.
    Unreleasable, naming the next step, otherwise."""
    verdict = checked(graph, model_hash)
    signed = marked(graph, SIGNOFF)
    if signed is None or signed.get("model_hash") != model_hash:
        raise Unreleasable(f"{model_hash} is not through the onboarding gate: it passed its post-load check (run "
                           f"{verdict['run_id']}), not signed off: {STEWARD.format(model_hash)} signs it off")
    if signed.get("run_id") != verdict.get("run_id"):
        raise Unreleasable(f"{model_hash} is not through the onboarding gate: it was signed off on check run {signed.get('run_id')}, "
                           f"and its check since is run {verdict.get('run_id')}: {STEWARD.format(model_hash)} signs that one off")
    return signed


def mark(prof: Profile) -> dict:
    """The graph of the hash prof is pinned to, found whole and marked LOADED for its store: make load's last step.
    Unreleasable if there is no graph, or it was not loaded from that artifact for the profile's client."""
    from kge.adapters.graphstore import connect, open_db
    graph = prof.graph_path()
    if not graph.exists():
        raise Unreleasable(f"no graph {shown(graph)}: make load HASH={prof.model_hash()} loads it")
    db = open_db(graph, prof.run_dir() / "api.pid")
    conn = connect(db)
    try:
        def rows(cypher: str) -> list:
            try:
                return conn.execute(cypher).get_all()
            except RuntimeError:
                return []
        models = [r[0] for r in rows("MATCH (m:GraphModel) RETURN m.id")]
        releases = rows("MATCH (r:Release) RETURN r.id, r.client")
        chunks = rows("MATCH (c:Chunk) RETURN count(c)")
    finally:
        conn.close()
        db.close()
    if models != [prof.model_hash()]:
        got = ", ".join(models) or "no artifact"
        raise Unreleasable(f"the graph {shown(graph)} was loaded from {got}, not from the artifact {prof.model_hash()}: "
                           f"make load HASH={prof.model_hash()} loads it")
    if len(releases) != 1 or releases[0][1] != prof.client:
        raise Unreleasable(f"the graph {shown(graph)} names no one release of {prof.client}'s: make load HASH={prof.model_hash()} loads it")
    entry = {"client": prof.client, "model_hash": prof.model_hash(), "release": releases[0][0], "chunks": chunks[0][0] if chunks else 0,
             "warehouses": warehouse_versions.estate_live(prof)}
    entries = held(graph.parent)
    entries[store_of(graph)] = entry
    write_marker(graph.parent, entries)
    return entry


def swap(prof: Profile, model_hash: str) -> None:
    """The client's current pointer swapped to model_hash, atomically: a link to it under a temporary name, renamed over
    the old one (os.replace). The old link is never removed first; if the rename fails, the temporary link goes and
    current is as it was."""
    home = prof.artifacts / prof.client
    tmp = home / f".{CURRENT}.{os.getpid()}.{secrets.token_hex(4)}"
    os.symlink(model_hash, tmp)
    try:
        os.replace(tmp, home / CURRENT)
    except BaseException:
        os.unlink(tmp)
        raise


def review_drafts(folder: Path, model_hash: str, released_before: bool) -> None:
    """Refuse malformed or unfinished onboarding review metadata before either gated or ungated release."""
    drafts_file = folder / "drafts.json"
    if released_before or not drafts_file.is_file():
        return
    try:
        drafts = json.loads(drafts_file.read_text())
    except (OSError, ValueError) as exc:
        raise Unreleasable(f"the artifact {model_hash} has unreadable drafts.json: {exc}") from None
    if not isinstance(drafts, dict) or drafts.get("format") != 1 or not isinstance(drafts.get("items"), list) or \
            not all(isinstance(x, dict) for x in drafts["items"]):
        raise Unreleasable(f"the artifact {model_hash} has invalid drafts.json: expected format 1 with a list of items")
    waiting = [f"{x.get('unit')}/{x.get('id')} ({x.get('state')})" for x in drafts["items"] if x.get("state") != "certified"]
    if waiting:
        raise Unreleasable(f"the artifact {model_hash} holds drafted items no steward has certified: {', '.join(waiting)}: "
                           "make onboard checks a candidate; make accept and make certify-draft, or the page's Onboard panel, "
                           "take them through; then make sign-off and make release")


def draft_units(folder: Path) -> list[str]:
    """Units whose accepted onboarding items the candidate carries."""
    try:
        data = json.loads((folder / "drafts.json").read_text())
    except (OSError, ValueError):
        return []
    return sorted({item["unit"] for item in data.get("items", [])
                   if isinstance(item, dict) and item.get("unit") and item.get("state") in {"accepted", "certified"}})


def release(
    prof: Profile,
    model_hash: str | None = None,
    ungated: bool = False,
    build_units: Callable[[Profile], Any] | None = None,
    previous: bool = False,
    defer_retention: bool = False,
) -> tuple[str | None, str]:
    """model_hash released: current swapped to it, once its artifact is found whole and the profile's client's, its graph
    LOADED on this process's store, and through the onboarding gate (through), unless ungated; then marked RELEASED. The
    hash current named before, and how it was let through; Unreleasable, current as it was, otherwise. Each onboarded
    unit's warehouse version the artifact names must be on disk, since a hash served from another version's rows would
    answer with figures nobody checked."""
    from kge.graph import history
    from kge.graph.load import Unloadable, read_artifact
    defer_retention = defer_retention or bool(os.getenv("STORE_URL", "").strip())
    if previous:
        ledger = served(prof.store / prof.client)
        if len(ledger) < 2:
            raise Unreleasable("no previous release in served.json")
        model_hash = ledger[1]["hash"]
        from kge.adapters.released import identity
        saved = next((entry for entry in kept(prof) if identity(entry["identity"]) == ledger[1]), None)
        if saved is None and ledger[1].get("release"):
            raise Unreleasable(f"the pack snapshot versions/{model_hash}/{ledger[1]['release']}/ is not held")
        return _rollback_release(prof, saved or {"identity": ledger[1], "inputs": None, "audit": {},
                                                "snapshot": f"versions/{model_hash}/",
                                                "tarball": f"releases/{prof.client}/{model_hash}.tar.gz"},
                                 defer_retention=defer_retention)
    new_load = False
    if model_hash != prof.model_hash():
        saved = next((entry for entry in kept(prof) if entry["identity"]["hash"] == model_hash), None)
        if saved is not None:
            graph = prof.at(model_hash).graph_path()
            if loaded(graph) is None and not history.has_kept_pack(prof, model_hash):
                raise Unreleasable(f"{shown(graph)} is not LOADED on {store_of(graph)}: make load HASH={model_hash} loads it")
            new_load = _newer_load(graph)
            if not new_load:
                history.check_rollback_allowed(prof, model_hash, prof.model_hash())
                return _rollback_release(prof, saved, defer_retention=defer_retention)
    moved = moved_inputs(prof, model_hash)
    if moved:
        raise Unreleasable(f"the stored inputs moved after {model_hash[:12]} was built: {', '.join(moved)}: "
                           "onboard again, or upload the inputs it was built from")
    target = prof.at(model_hash)
    folder = prof.artifacts / prof.client / model_hash
    if not (folder / "manifest.json").is_file():
        raise Unreleasable(f"no artifact {shown(folder)}: make compile writes one, under its model hash")
    try:
        man = read_artifact(folder, prof.client)
    except Unloadable as exc:
        raise Unreleasable(f"the artifact {shown(folder)}: {exc}") from None
    if man["model_hash"] != model_hash:
        raise Unreleasable(f"the artifact {shown(folder)} is {man['model_hash']}'s, not {model_hash}'s: make compile writes it "
                           "under its model hash")
    graph = target.graph_path()
    was = prof.model_hash()
    prior_identity = prof.serve().identity() if was and not served(prof.store / prof.client) else None
    before = not new_load and (model_hash == was or marked(graph, RELEASED) is not None)
    review_drafts(folder, model_hash, before)
    entry = loaded(graph)
    try:
        fixtures = (entry or {}).get("warehouses", {})
        versions = {**warehouse_versions.named(folder), **fixtures}
        inputs = {u: digest for u, digest in warehouse_versions.inputs_named(folder).items() if u not in fixtures}
        estate_of(prof).check(list(versions), versions, model_hash, inputs)
    except (ValueError, warehouse_versions.MissingWarehouse) as exc:
        raise Unreleasable(str(exc)) from None
    history.review_proposals(prof, model_hash, before)
    is_rollback = bool(before and was and was != model_hash)
    if is_rollback:
        history.check_rollback_allowed(prof, model_hash, was)
    if entry is None or not graph.exists():
        raise Unreleasable(f"{shown(graph)} is not LOADED on {store_of(graph)}: make load HASH={model_hash} loads it, the overlay "
                           "replayed, and marks it")
    if (entry.get("client"), entry.get("model_hash")) != (prof.client, model_hash):
        raise Unreleasable(f"{shown(graph)} is marked LOADED for {entry.get('client')}'s {entry.get('model_hash')}: make load "
                           f"HASH={model_hash} loads it again")
    signed = None if ungated or (not new_load and marked(graph, RELEASED) is not None) else through(graph, model_hash)
    prev_store = history._capture_store(prof) if is_rollback else None
    removed_units = history.removed_units_for_restore(prof, model_hash) if is_rollback else set()
    added_units = history.added_units_for_restore(prof, model_hash) if is_rollback else set()
    if is_rollback:
        history.restore_pack(prof, model_hash, cleanup_removed=False, build_units=build_units)
    vpath = history.versions_path(prof)
    prev_vbytes = vpath.read_bytes() if vpath.is_file() else None
    prev_graph_rel = held(graph.parent, RELEASED)
    old: Path | None = None
    if was and was != model_hash:
        try:
            old = prof.at(was).graph_path()
        except ProfileError:
            old = None
    prev_old_rel = held(old.parent, RELEASED) if old is not None else None
    from kge.adapters import released, retrieval
    from kge.adapters.graphstore import release_entry
    records = [*released.paths(graph.parent), prof.store / prof.client / SERVED, prof.store / prof.client / KEPT]
    prev_records = {p: p.read_bytes() if p.is_file() else None for p in records}
    prev_times = {p: (p.stat().st_atime_ns, p.stat().st_mtime_ns) for p in records if p.is_file()}
    release_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    input_path = graph.parent / INPUTS
    release_inputs = released.content_inputs(json.loads(input_path.read_text())) if input_path.is_file() else None
    audit = history.release_audit(prof, model_hash)
    snapshot = history.snapshot_release(prof, model_hash, release_id, release_inputs)
    try:
        if hasattr(prof, "warehouses"):
            import csv
            units = []
            for kind in ("Unit", "CuratedSource"):
                unit_csv = folder / "data" / "nodes" / f"{kind}.csv"
                if unit_csv.is_file():
                    with unit_csv.open(newline="") as fh:
                        units.extend(row["id"] for row in csv.DictReader(fh) if row.get("id"))
            estate = estate_of(prof)
            missing = [estate.label(u) for u in units if u not in versions and not estate.exists(u)]
            if missing:
                raise Unreleasable(
                    f"no {', '.join(missing)}: the graph's units each need a warehouse "
                    "(make estate builds base units; onboard the unit to build an onboarded warehouse)"
                )
        swap(prof, model_hash)
        released.write(graph.parent, model_hash, release_entry(graph), retrieval.release_entry(graph.parent),
                       entry.get("warehouses", {}), release_id, release_inputs, audit)
        at = now()
        if marked(graph, RELEASED) is None:
            put(graph, RELEASED, {"model_hash": model_hash, "at": at, "signed_off_by": signed["principal"] if signed else None,
                                  "run_id": signed["run_id"] if signed else None})
        if old is not None and loaded(old) is not None and marked(old, RELEASED) is None and marked(old, RELOADED) is None:
            put(old, RELEASED, {"model_hash": was, "at": at, "signed_off_by": None, "run_id": None})
        history.record_release(prof, model_hash, was, before, ungated, signed, at, pack_restored=is_rollback)
        if not os.getenv("STORE_URL", "").strip():
            history.archive_runtime(prof, model_hash, release_id)
        if prior_identity is not None:
            served(prof.store / prof.client, prior_identity)
        keep_release(prof, {"identity": prof.at(model_hash).serve().identity(), "inputs": release_inputs,
                            "audit": audit, "tarball": f"releases/{prof.client}/{model_hash}/{release_id}.tar.gz",
                            "snapshot": snapshot})
        served(prof.store / prof.client, prof.at(model_hash).serve().identity())
    except BaseException:
        try:
            if prof.model_hash() != was:
                if was is not None:
                    swap(prof, was)
                else:
                    (prof.artifacts / prof.client / CURRENT).unlink(missing_ok=True)
        except OSError:
            pass
        for p, before_bytes in prev_records.items():
            try:
                if before_bytes is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_bytes(before_bytes)
                    os.utime(p, ns=prev_times[p])
            except OSError:
                pass
        try:
            write_marker(graph.parent, prev_graph_rel, RELEASED)
            if old is not None and prev_old_rel is not None:
                write_marker(old.parent, prev_old_rel, RELEASED)
        except OSError:
            pass
        try:
            if prev_vbytes is None:
                vpath.unlink(missing_ok=True)
            elif not vpath.is_file() or vpath.read_bytes() != prev_vbytes:
                vpath.write_bytes(prev_vbytes)
        except OSError:
            pass
        if is_rollback and prev_store is not None:
            history._restore_captured_store(prof, prev_store)
        if is_rollback and added_units:
            history._cleanup_removed_units(prof, added_units)
        history.snapshot_store(prof).delete(snapshot)
        raise
    if is_rollback and removed_units:
        history._cleanup_removed_units(prof, removed_units, prune_warehouses=not defer_retention)
    pack_version = ((man.get("pack") or {}).get("version") if isinstance(man.get("pack"), dict)
                    else man.get("pack_version") or man.get("version"))
    try:
        unit_history.record(prof, "release", signed["principal"] if signed else "developer",
                            {"model_hash": model_hash, "pack_version": pack_version}, draft_units(folder))
    except Exception as exc:
        logging.getLogger(__name__).warning("release took effect; unit history could not be recorded: %s", exc)
    if ungated:
        how = "released without the onboarding gate (the developer's bootstrap: no make margin HASH=, no make sign-off)"
    elif before:
        how = "released before, so no new sign-off"
    else:
        how = f"signed off by {signed['principal']} on check run {signed['run_id']}"
    try:
        post_release(prof.serve())
    finally:
        if not defer_retention:
            finish(prof, model_hash)
    return was, how


def prepare_retention(prof: Profile) -> None:
    """Invalidate obsolete local approvals before the caller saves work, without remote deletion."""
    from kge.graph import leftovers
    leftovers.prepare_retention(prof)


def finish(prof: Profile, model_hash: str | None = None) -> None:
    """Retire only after the caller has synced and saved a committed release."""
    try:
        model_hash = model_hash or prof.model_hash()
        if model_hash is None:
            return
        dropped = retire(prof, model_hash)
        if dropped:
            logging.getLogger(__name__).info("release dropped %d unused objects: %s", len(dropped), ", ".join(dropped))
    except Exception as exc:
        logging.getLogger(__name__).warning("release saved; retention failed: %s", exc)


def finish_onboard(prof: Profile) -> None:
    """Replace an older unreleased generation only after its successor is saved."""
    from kge.graph import leftovers
    try:
        leftovers.prepare_retention(prof)
        dropped = leftovers.sweep(prof)
        for line in leftovers.lines(dropped, "retired"):
            logging.getLogger(__name__).info("%s", line)
    except Exception as exc:
        logging.getLogger(__name__).warning("onboard saved; retention failed: %s", exc)


def post_release(prof: Profile) -> None:
    """P14 hook: automatically evaluate the served release before the caller records its baseline.

    Called after successful release and rollback, outside their restore-on-error transactions.
    An evaluation failure must propagate while leaving the new release served.
    """
    from kge.adapters import released
    folder = prof.graph_path().parent
    if not (folder / released.PRE_EVAL).is_file():
        released.write_evaluation(folder, "post", {
            "passed": True, "compared": False, "status": "not compared", "identity": prof.identity(), "at": now(),
            "message": "eval post not compared: no pre-release results to compare", "model_graded": {"status": "not applicable"}})
        print("eval post not compared: no pre-release results to compare", flush=True)
        return
    (folder / released.POST_EVAL).unlink(missing_ok=True)
    result = subprocess.run([sys.executable, "-m", "kge.eval.stages"],
                            input=json.dumps(asdict(prof.pin()), default=str), text=True)
    if result.returncode:
        if released.evaluation(prof.graph_path().parent, "post") is None:
            released.write_evaluation(prof.graph_path().parent, "post", {
                "passed": False, "identity": prof.identity(), "at": now(),
                "message": "EVAL POST FAILED: evaluator did not finish; run kge rollback"})
        raise PostReleaseFailed("EVAL POST FAILED: the release stays served; run kge rollback")


def served(root: Path, now: dict | None = None) -> list[dict]:
    """The release ledger, root/served.json in the client's graph store: the identities (Profile.identity: model hash,
    graph generation, chunks table and warehouse versions) of the release just made and of the one before it, now first when it is given,
    which is then written back. A service follows each release (kge/services/onboard/jobs.py follow_release, on its
    next status poll, or a restart), so it holds one of these two as long as it follows before the release after
    next; a prune keeps what they name."""
    from kge.adapters.released import identity
    path = Path(root) / SERVED
    try:
        held = [identity(i) for i in json.loads(path.read_text()) if isinstance(i, dict)]
    except (OSError, ValueError, TypeError):
        held = []
    if now is not None:
        now = identity(now)
        held = [now] + [i for i in held if i != now][:1]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{SERVED}.{os.getpid()}.{secrets.token_hex(4)}")
        tmp.write_text(json.dumps(held, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return held


def record_versions(prof: Profile, record: dict) -> dict[str, int]:
    """Exact release versions, including established v0 only for its artifact's declared units."""
    import csv
    versions = dict((record.get("inputs") or {}).get("warehouses", {}))
    versions.update(record.get("warehouses", {}))
    model_hash = record.get("hash")
    if not model_hash:
        return versions
    for kind in ("Unit", "CuratedSource"):
        path = prof.artifacts / prof.client / model_hash / f"data/nodes/{kind}.csv"
        if path.is_file():
            with path.open(newline="") as stream:
                for row in csv.DictReader(stream):
                    if row.get("id"):
                        versions.setdefault(row["id"], 0)
    return versions


def retire(prof: Profile, model_hash: str) -> list[str]:
    """Once model_hash is released: its release identity, as a server opens it, put first in the release ledger
    (served), and what no pointer and neither of the ledger's two releases names dropped, graph generations and chunks
    tables alike. Fixture warehouses keep explicitly named versions and estate_live.
    The graph generations and chunks tables dropped."""
    from kge.adapters import retrieval
    from kge.adapters.graphstore import prune
    from kge.graph import history, leftovers
    client_store = prof.store / prof.client
    served(client_store, prof.at(model_hash).serve().identity())
    held = leftovers.protected_records(prof)
    leftovers.prepare_retention(prof)
    live = warehouse_versions.estate_live(prof)
    units = set(live).union(*(i.get("warehouses", {}) for i in held))
    summary = prof.estate() / "summary.json"
    if summary.is_file():
        units.update(json.loads(summary.read_text()).get("fixture_units", []))
    keep = {u: {i["warehouses"][u] for i in held if u in i.get("warehouses", {})} | ({live[u]} if u in live else set())
            for u in units}
    warehouse_versions.prune_estate(prof, keep)
    import csv
    named_units = set(units)
    fixture = warehouse_versions.fixture_path(prof.clients / prof.client)
    if fixture is not None and (fixture / "schema.yaml").is_file():
        import yaml
        named_units.update((yaml.safe_load((fixture / "schema.yaml").read_text()) or {}).get("warehouses", {}))
    for identity in held:
        for kind in ("Unit", "CuratedSource"):
            path = prof.artifacts / prof.client / identity["hash"] / f"data/nodes/{kind}.csv"
            if path.is_file():
                with path.open(newline="") as stream:
                    named_units.update(row["id"] for row in csv.DictReader(stream) if row.get("id"))
    estate = estate_of(prof)
    for unit in set(estate.established()) - named_units:
        if estate.versions(unit) == []:
            estate.remove(unit)
    from kge.pipeline.objstore import published_records
    published = published_records(prof)
    retained = kept(prof) + [
        {"identity": record, "snapshot": record.get("snapshot")} for record in published or []]
    snapshots = {entry["snapshot"] for entry in retained if entry.get("snapshot")}
    store = history.snapshot_store(prof)
    for name in store.list("versions"):
        parts = name.split("/")
        if len(parts) > 2 and HASH.fullmatch(parts[1]) and not any(name.startswith(prefix) for prefix in snapshots):
            store.delete(name)
    for local in (client_store / "releases").glob("*"):
        allowed = {e["identity"].get("release") for e in retained if e["identity"]["hash"] == local.name}
        for archive in local.glob("*.tar.gz"):
            if archive.name.removesuffix(".tar.gz") not in allowed:
                archive.unlink()
    version_root = prof.artifacts / prof.client / "versions"
    for path in version_root.rglob("*"):
        name = "versions/" + path.relative_to(version_root).as_posix()
        if path.is_file() and HASH.fullmatch(path.relative_to(version_root).parts[0]) and \
                not any(name.startswith(prefix) for prefix in snapshots):
            path.unlink()
    generations = prune(client_store, [i["graph"] for i in held if i.get("graph")])
    leftovers.invalidate(prof, generations=generations)
    chunks = retrieval.prune(client_store, [i["chunks"] for i in held if i.get("chunks")])
    leftovers.invalidate(prof, chunks=chunks)
    return generations + chunks


def main(argv: list[str] | None = None) -> int:
    import importlib
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose graph (default: PROFILE, else the registry's default)")
    ap.add_argument("--hash", help="the model hash of the artifact whose graph to release (make release: the one "
                                                  "the pack compiles to, unless HASH names another)")
    ap.add_argument("--previous", action="store_true", help="restore served.json's previous release")
    ap.add_argument("--defer-retention", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mark-loaded", action="store_true", help="make load's last step: mark the hash's graph LOADED on this "
                                                               "store, once it is found whole; releases nothing")
    ap.add_argument("--sign-off", action="store_true", help="make sign-off: the steward's sign-off on the hash's graph, once its "
                                                            "post-load check passed (GATE); releases nothing")
    ap.add_argument("--as", dest="principal", help="who signs off (make sign-off PRINCIPAL=): one whose role may perform "
                                                   "certification")
    ap.add_argument("--role", help="operator sign-off as a candidate role; recorded as role:<id>")
    ap.add_argument("--ungated", action="store_true", help="make graph's and make demo's release, the developer's bootstrap: "
                                                           "no post-load check or sign-off asked, and it says so")
    args = ap.parse_args(argv)
    if args.previous and (args.hash or args.mark_loaded or args.sign_off or args.ungated):
        ap.error("--previous is used alone, without --hash or load/sign-off options")
    if not args.previous and not args.hash:
        ap.error("--hash or --previous is required")
    if args.sign_off and bool(args.principal) == bool(args.role):
        ap.error("--sign-off needs exactly one of --as <email> or --role <role-id>")
    if args.role and not args.sign_off:
        ap.error("--role is only for --sign-off")
    from kge.adapters.graphstore import GraphLocked, store
    if args.sign_off:
        try:
            prof = resolve(args.profile)
            entry = sign_off(prof, args.hash, args.principal or "", role=args.role)
        except (ProfileError, Unreleasable, GraphLocked, OSError) as exc:
            print(f"sign-off: {exc}. Nothing was signed off.", file=sys.stderr)
            return 2
        command = "kge" if settings().KGE_VERSION else "make"
        print(f"sign-off: {args.hash} signed off by {entry['principal']}, on its post-load check, run {entry['run_id']}, passed: "
              f"{shown(prof.at(args.hash).graph_path().parent / SIGNOFF)}. {command} release HASH={args.hash} swaps current to it")
        return 0
    prof: Profile | None = None
    was_before: str | None = None
    try:
        prof = resolve(args.profile)
        was_before = prof.model_hash()
        if args.mark_loaded:
            target = prof.at(args.hash)
            entry = mark(target)
            cur = prof.model_hash()
            then = "it is current" if args.hash == cur else (
                f"current is {cur or 'none'}: kge eval pre checks it, kge sign-off signs it off, then kge release "
                f"HASH={args.hash} swaps current to it") if settings().KGE_VERSION else (
                f"current is {cur or 'none'}: make margin HASH={args.hash} checks it, make sign-off PRINCIPAL=<steward-email> "
                f"HASH={args.hash} signs it off, then make release HASH={args.hash} swaps current to it")
            print(f"release: {args.hash} LOADED on {store().name}: {shown(target.graph_path())}, release {entry['release']}, "
                  f"{entry['chunks']} chunks; {then}")
            return 0
        was, how = release(
            prof,
            args.hash,
            ungated=args.ungated,
            previous=args.previous,
            defer_retention=args.defer_retention,
            build_units=lambda p: getattr(importlib.import_module("kge.estate.onboard"), "build")(p),
        )
    except PostReleaseFailed as exc:
        print(str(exc), file=sys.stderr)
        from kge.adapters.released import POST_FAILED
        return POST_FAILED
    except (ProfileError, Unreleasable, GraphLocked, OSError, appstate.StateUnavailable) as exc:
        cur_after = prof.model_hash() if prof is not None else None
        if prof is not None and cur_after != was_before:
            print(f"release: {exc}. current is now {cur_after}, not {was_before or 'none'}.", file=sys.stderr)
        else:
            print(f"release: {exc}. current is as it was.", file=sys.stderr)
        return 2
    if args.previous:
        args.hash = prof.model_hash()
    link = prof.artifacts / prof.client / CURRENT
    if args.previous:
        print(f"release: {how}: {shown(link)} -> {args.hash}")
    elif was == args.hash:
        print(f"release: {args.hash} was current already{', ' + how if args.ungated else ''}: {shown(link)} -> {args.hash}")
    else:
        print(f"release: profile {prof.id}'s current is now {args.hash} (was {was or 'none'}), {how}: {shown(link)} -> "
              f"{args.hash}. A page release switches processes with STORE_URL, including Compose and Cloud Run, as they poll; "
              "after this direct release, restart the API (docker compose restart app, or a new revision on Cloud Run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
