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
GATE passed, and the graph, opened as --mark-loaded opens it, lets the
principal perform certification (the pack's roles bound to the deployment's
identities: no principal is named here). A reload takes it out with GATE's.

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
from collections.abc import Callable
import json
import os
import secrets
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kge.pipeline.profile import CURRENT, HASH, Profile, ProfileError, resolve, shown
from kge.adapters import appstate
from kge.adapters.graphstore import LOADED, held
from kge.adapters.warehouse import estate_of
from kge.pipeline import unit_history, warehouse_versions

GATE, SIGNOFF, RELEASED = "GATE", "SIGNOFF", "RELEASED"
RELOADED = "RELOADED"
SERVED = "served.json"
CERTIFICATION = "certification"
STEWARD = "make sign-off PRINCIPAL=<steward> HASH={}"


if __name__ == "__main__":
    sys.modules.setdefault("kge.graph.release", sys.modules[__name__])


class Unreleasable(Exception):
    """A hash this process will not release, or a graph it will not mark loaded, and why."""


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
    put(graph, RELOADED, {"at": now()})
    for name in (LOADED, GATE, SIGNOFF, RELEASED):
        entries = held(Path(graph).parent, name)
        if entries.pop(store_of(graph), None) is not None or (Path(graph).parent / name).exists():
            write_marker(Path(graph).parent, entries, name)


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
        for name in (LOADED, GATE, SIGNOFF, RELEASED):
            (folder / name).unlink(missing_ok=True)


def state(prof: Profile, model_hash: str) -> dict:
    """Release state from marker files and current only; never opens a graph."""
    graph = prof.at(model_hash).graph_path()
    checked, signed = marked(graph, GATE), marked(graph, SIGNOFF)
    return {"model_hash": model_hash, "loaded": loaded(graph) is not None,
            "gate": ({"passed": checked.get("passed"), "run_id": checked.get("run_id")} if checked else None),
            "signed_off": ({k: signed.get(k) for k in ("principal", "at", "run_id")} if signed else None),
            "released": marked(graph, RELEASED) is not None, "current": prof.model_hash() == model_hash}


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


def certifiers(prof: Profile, graph: Path) -> tuple[set, list]:
    """The principals graph holds, and those it lets perform certification: the pack's roles bound to the deployment's
    identities when it was loaded. Opens the graph, as mark does."""
    from kge.adapters.graphstore import connect, open_db
    db = open_db(graph, prof.run_dir() / "api.pid")
    conn = connect(db)
    try:
        everyone = {r[0] for r in conn.execute("MATCH (p:Principal) RETURN p.id").get_all()}
        may = sorted(r[0] for r in conn.execute("MATCH (p:Principal)-[:MAY_PERFORM]->(:Process {id: $x}) RETURN p.id",
                                                 {"x": CERTIFICATION}).get_all())
    finally:
        conn.close()
        db.close()
    return everyone, may


def sign_off(prof: Profile, model_hash: str, principal: str) -> dict:
    """The steward's sign-off on model_hash's graph, onboarding's step 8: written into the SIGNOFF marker beside it, for
    this store, once its graph is LOADED, its post-load check passed (GATE), and principal is one the graph lets perform
    certification. Its entry; Unreleasable, nothing written, otherwise."""
    graph = prof.at(model_hash).graph_path()
    verdict = checked(graph, model_hash)
    everyone, may = certifiers(prof, graph)
    if principal not in may:
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
    entry = {"client": prof.client, "model_hash": prof.model_hash(), "release": releases[0][0], "chunks": chunks[0][0] if chunks else 0}
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
    model_hash: str,
    ungated: bool = False,
    build_units: Callable[[Profile], Any] | None = None,
) -> tuple[str | None, str]:
    """model_hash released: current swapped to it, once its artifact is found whole and the profile's client's, its graph
    LOADED on this process's store, and through the onboarding gate (through), unless ungated; then marked RELEASED. The
    hash current named before, and how it was let through; Unreleasable, current as it was, otherwise. Each onboarded
    unit's warehouse version the artifact names must be on disk, since a hash served from another version's rows would
    answer with figures nobody checked."""
    from kge.graph import history
    from kge.graph.load import Unloadable, read_artifact
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
    before = model_hash == was or marked(graph, RELEASED) is not None
    review_drafts(folder, model_hash, before)
    try:
        named = warehouse_versions.named(folder)
        estate_of(prof).check(list(named), named, model_hash, warehouse_versions.inputs_named(folder))
    except (ValueError, warehouse_versions.MissingWarehouse) as exc:
        raise Unreleasable(str(exc)) from None
    history.review_proposals(prof, model_hash, before)
    is_rollback = bool(before and was and was != model_hash)
    if is_rollback:
        history.check_rollback_allowed(prof, model_hash, was)
    entry = loaded(graph)
    if entry is None or not graph.exists():
        raise Unreleasable(f"{shown(graph)} is not LOADED on {store_of(graph)}: make load HASH={model_hash} loads it, the overlay "
                           "replayed, and marks it")
    if (entry.get("client"), entry.get("model_hash")) != (prof.client, model_hash):
        raise Unreleasable(f"{shown(graph)} is marked LOADED for {entry.get('client')}'s {entry.get('model_hash')}: make load "
                           f"HASH={model_hash} loads it again")
    signed = None if ungated or marked(graph, RELEASED) is not None else through(graph, model_hash)
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
    records = released.paths(graph.parent)
    prev_records = {p: p.read_bytes() if p.is_file() else None for p in records}
    try:
        unit_csv = folder / "data" / "nodes" / "Unit.csv"
        if unit_csv.is_file() and hasattr(prof, "warehouses"):
            import csv
            with unit_csv.open(newline="") as fh:
                units = [row["id"] for row in csv.DictReader(fh) if row.get("id")]
            estate = estate_of(prof)
            missing = [estate.label(u) for u in units if u not in named and not estate.exists(u)]
            if missing:
                raise Unreleasable(
                    f"no {', '.join(missing)}: the graph's units each need a warehouse "
                    "(make estate builds base units; onboard the unit to build an onboarded warehouse)"
                )
        swap(prof, model_hash)
        released.write(graph.parent, model_hash, release_entry(graph), retrieval.release_entry(graph.parent))
        at = now()
        if marked(graph, RELEASED) is None:
            put(graph, RELEASED, {"model_hash": model_hash, "at": at, "signed_off_by": signed["principal"] if signed else None,
                                  "run_id": signed["run_id"] if signed else None})
        if old is not None and loaded(old) is not None and marked(old, RELEASED) is None and marked(old, RELOADED) is None:
            put(old, RELEASED, {"model_hash": was, "at": at, "signed_off_by": None, "run_id": None})
        existing_rows = history.read_jsonl(vpath) if vpath.is_file() else []
        last_recorded = existing_rows[-1].get("model_hash") if existing_rows else None
        if was != model_hash or last_recorded != model_hash:
            history.record_release(prof, model_hash, was, before, ungated, signed, at, pack_restored=is_rollback)
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
        raise
    if is_rollback and removed_units:
        history._cleanup_removed_units(prof, removed_units)
    pack_version = ((man.get("pack") or {}).get("version") if isinstance(man.get("pack"), dict)
                    else man.get("pack_version") or man.get("version"))
    try:
        unit_history.record(prof, "release", signed["principal"] if signed else "developer",
                            {"model_hash": model_hash, "pack_version": pack_version}, draft_units(folder))
    except Exception as exc:
        logging.getLogger(__name__).warning("release took effect; unit history could not be recorded: %s", exc)
    try:
        dropped = retire(prof, model_hash)
        if dropped:
            logging.getLogger(__name__).info("release dropped %d graph generation(s) nothing reads: %s", len(dropped), ", ".join(dropped))
    except Exception as exc:
        logging.getLogger(__name__).warning("release took effect; graphs nothing reads could not be dropped: %s", exc)
    if ungated:
        how = "released without the onboarding gate (the developer's bootstrap: no make margin HASH=, no make sign-off)"
    elif before:
        how = "released before, so no new sign-off"
    else:
        how = f"signed off by {signed['principal']} on check run {signed['run_id']}"
    return was, how


def served(root: Path, now: dict | None = None) -> list[dict]:
    """The release ledger, root/served.json in the client's graph store: the identities (Profile.identity: model hash,
    graph generation, chunks table) of the release just made and of the one before it, now first when it is given,
    which is then written back. A service follows each release (kge/services/onboard/jobs.py follow_release, on its
    next status poll, or a restart), so it holds one of these two as long as it follows before the release after
    next; a prune keeps what they name."""
    path = Path(root) / SERVED
    try:
        held = [i for i in json.loads(path.read_text()) if isinstance(i, dict)]
    except (OSError, ValueError, TypeError):
        held = []
    if now is not None:
        held = [now] + [i for i in held if i != now][:1]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{SERVED}.{os.getpid()}.{secrets.token_hex(4)}")
        tmp.write_text(json.dumps(held, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return held


def retire(prof: Profile, model_hash: str) -> list[str]:
    """Once model_hash is released: its release identity, as a server opens it, put first in the release ledger
    (served), and what no pointer and neither of the ledger's two releases names dropped, graph generations and chunks
    tables alike. What was dropped."""
    from kge.adapters import retrieval
    from kge.adapters.graphstore import prune
    root = prof.store / prof.client
    held = served(root, prof.at(model_hash).serve().identity())
    return (prune(root, [i["graph"] for i in held if i.get("graph")])
            + retrieval.prune(root, [i["chunks"] for i in held if i.get("chunks")]))


def main(argv: list[str] | None = None) -> int:
    import importlib
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose graph (default: PROFILE, else the registry's default)")
    ap.add_argument("--hash", required=True, help="the model hash of the artifact whose graph to release (make release: the one "
                                                  "the pack compiles to, unless HASH names another)")
    ap.add_argument("--mark-loaded", action="store_true", help="make load's last step: mark the hash's graph LOADED on this "
                                                               "store, once it is found whole; releases nothing")
    ap.add_argument("--sign-off", action="store_true", help="make sign-off: the steward's sign-off on the hash's graph, once its "
                                                            "post-load check passed (GATE); releases nothing")
    ap.add_argument("--as", dest="principal", help="who signs off (make sign-off PRINCIPAL=): one whose role may perform "
                                                   "certification")
    ap.add_argument("--ungated", action="store_true", help="make graph's and make demo's release, the developer's bootstrap: "
                                                           "no post-load check or sign-off asked, and it says so")
    args = ap.parse_args(argv)
    if args.sign_off and not args.principal:
        ap.error("--sign-off needs --as <principal>: make sign-off PRINCIPAL=<steward>")
    from kge.adapters.graphstore import GraphLocked, store
    if args.sign_off:
        try:
            prof = resolve(args.profile)
            entry = sign_off(prof, args.hash, args.principal)
        except (ProfileError, Unreleasable, GraphLocked, OSError) as exc:
            print(f"sign-off: {exc}. Nothing was signed off.", file=sys.stderr)
            return 2
        print(f"sign-off: {args.hash} signed off by {entry['principal']}, on its post-load check, run {entry['run_id']}, passed: "
              f"{shown(prof.at(args.hash).graph_path().parent / SIGNOFF)}. make release HASH={args.hash} swaps current to it")
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
                f"current is {cur or 'none'}: make margin HASH={args.hash} checks it, make sign-off PRINCIPAL=<steward> "
                f"HASH={args.hash} signs it off, then make release HASH={args.hash} swaps current to it")
            print(f"release: {args.hash} LOADED on {store().name}: {shown(target.graph_path())}, release {entry['release']}, "
                  f"{entry['chunks']} chunks; {then}")
            return 0
        was, how = release(
            prof,
            args.hash,
            ungated=args.ungated,
            build_units=lambda p: getattr(importlib.import_module("kge.estate.onboard"), "build")(p),
        )
    except (ProfileError, Unreleasable, GraphLocked, OSError, appstate.StateUnavailable) as exc:
        cur_after = prof.model_hash() if prof is not None else None
        if prof is not None and cur_after != was_before:
            print(f"release: {exc}. current is now {cur_after}, not {was_before or 'none'}.", file=sys.stderr)
        else:
            print(f"release: {exc}. current is as it was.", file=sys.stderr)
        return 2
    link = prof.artifacts / prof.client / CURRENT
    if was == args.hash:
        print(f"release: {args.hash} was current already{', ' + how if args.ungated else ''}: {shown(link)} -> {args.hash}")
    else:
        print(f"release: profile {prof.id}'s current is now {args.hash} (was {was or 'none'}), {how}: {shown(link)} -> "
              f"{args.hash}. A page release switches Cloud Run processes as they poll; Compose and other local processes "
              "need an API restart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
