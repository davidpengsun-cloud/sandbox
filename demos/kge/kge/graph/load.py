"""Load a compiled pack, and the landed estate, into the graph.

    python3 -m kge.graph.load [--profile <id>] --artifact <artifacts>/<client>/<model_hash>/ [--release current|<release_id>]
    make graph

make graph checks the pack's lock, compiles the pack (kge/compiler/compile.py) and
passes the artifact folder the compile printed here. The artifact is checked
whole before the graph is touched: each file's sha256 against its manifest, no
file the manifest does not list, the model hash over them, its format, a
meta-model version this platform loads (its own major, no newer than its own;
meta-model.yaml), the encoder's embedding dimension (kge/adapters/model/embed.py), and
the client: the profile's (--profile, else PROFILE, else the registry's
default: kge/pipeline/profile.py), and the landing's. Everything else it reads and
writes is the profile's too: its landing, its identities, its graph store.
Then, into a fresh graph of the artifact's own, in the folder of the client's
graph store named by its model hash (<store>/<client>/<model_hash>/), so a load
never touches another artifact's graph, the one the API serves among them:

  1. the artifact's DDL in the graph store's dialect: ddl/kuzu.cypher for
     Ladybug, the compiled model's BigQuery DDL (kge/compiler/emit.py
     bigquery_ddl, from graph-model.yaml) for BigQuery, its tables and
     property graph;
  2. its files, nodes then edges, in its load order, as the store loads a file
     (load_file: COPY with null_strings=['\\N'] in Ladybug, a typed INSERT in
     BigQuery), so a text field keeps '' apart from NULL; a rel group's file
     names its pair of ends;
  3. the landing: each document of the release's index, with what upstream AI
     certification landed for it, SUPERSEDED_BY, and the DEFINES rows the
     artifact stages for the documents that landed. A document the pack names
     that never landed is S12, which the compiler cannot check: a warning on
     stderr, and no edge;
  4. the access mirror: the deployment's identities (the profile's,
     deploy/<client>/identities.yaml in the demo) bound to the artifact's role
     templates (access/roles.json) give Principal, CAN_READ, CAN_WRITE and
     MAY_PERFORM, with the platform's processes; principals.json in the
     profile's run state (Profile.run_dir), which kge/services/identity/service.py reads, is
     written with the same principals;
  5. a Release node naming the release, which the pipeline reads its chunks
     from, and a GraphModel node naming the artifact, which the run trace
     reports;
  6. the matches of a curated source certification landed for a steward
     (review/<source>.json: proposed, or below its confidence floor), a Tag of
     family xref_match each, on no chunk: its value the curated table and the
     match's id, its confidence and method the match's, its evidence opening
     with what upstream made of it ("proposed upstream", "rejected upstream")
     and saying whether it is below the floor. The steward queue shows them to
     whoever may certify and reads every unit the table's declared join links;
     a decision is the graph's record, never written back.

Between 3 and 4, the runtime overlay: the candidate terms stewards accepted, as
the client's overlay journal holds them (<store>/<client>/overlay/,
kge/pipeline/overlay.py), each with its DEFINES edge from the document that defines
it, so the access mirror gives it its writers as it gives every term. A term the
artifact already holds keeps the artifact's definition, and only the edge is
attached. A record whose document or chunk did not land in the release, or
whose chunk landed with other text, is an orphan: not attached, and named on
stderr and in orphans.json. kge/pipeline/run.py replays the decisions themselves.
A journal holding another client's records stops the load before the graph is
touched.

It prints the counts (the units' tables and columns, then, since meta-model
1.1, a curated source's tables, the contexts, the declared joins and the
matches for review) and the model hash. It never opens a warehouse or a raw
document: the physical and curated layers are the compiled pack's (its lock is
the landed catalogue), the documents and their certification the landing's.
Chunks and tags come later, from the pipeline. Rebuilding the graph from
scratch is the normal case: what stewards decided comes back from the overlay
journal, never from the old graph. An artifact, a release or identities it will
not load stop it (exit 2), saying why; so does the API holding the graph,
naming the API's pid. Either way it deletes nothing.

The release is --release (default current) of the profile's client's landing;
with none landed, or a release that is not whole, it stops, naming make
certify.

Each document takes what upstream AI certification landed for it in the
release's certification.json: AI-ready, tier, evidence, policy and effective
window. This fails closed: a document with no landed row, or no rows at all,
loads as not AI-ready with no tier, and the loader says so on stderr. The graph
only reads the landing; nothing writes back.

kge/graph/legacy.py is the loader this one replaced, which built the graph row by
row from the build's curated files; it is kept as the parity oracle
(tests/integration/test_parity.py).

    python3 -m kge.graph.load [--profile <id>] --principals-only

Re-syncs only the access mirror (Principal, CAN_READ, CAN_WRITE) into an
existing graph, from the profile's identities and the role templates of its
client's pack (<clients>/<client>/pack.yaml, read as the compiler reads it),
leaving chunks, tags and steward decisions alone; the graph's Release must name
the profile's client. It also rewrites principals.json in the run state to
match. Use it after changing either without regenerating the estate. The graph
must not be open elsewhere (stop the API first: the graph store holds a file
lock).

The graph is the profile's, graph.ladybug in the artifact's folder of its
client's graph store (Profile.graph_path): build/stores/<client>/<model_hash>/graph.ladybug
in the demo. A load that starts on a folder takes its
store's entry out of the folder's LOADED marker once it holds the graph's lock,
before it replaces anything; make load's last step writes it again, once the
pipeline has run (kge/graph/release.py). The load releases nothing: the client's
current pointer, which names the graph the runtime serves, moves only with make
release. --principals-only re-syncs the graph current names.

The artifact, the release and the identities are read before the graph is
opened, so with any of them wrong the graph on disk stays as it is. Only the
artifact's own graph, in the folder its model hash names, is opened: no other
artifact's, the live one's lock among them. The overlay's accepted terms are
replayed before the access mirror, which gives every term its writers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath

import yaml

from kge.compiler import pack as packs
from kge.compiler.checks import semver
from kge.compiler.emit import FORMAT, MANIFEST, roles as role_templates
from kge.landing.zone import CERTIFICATION, LandingError, Zone
from kge.adapters.model import DIM
from kge.pipeline.access import process_vocabulary
from kge.pipeline.overlay import LOAD, Journal, OverlayError, replay_terms, warn
from kge.pipeline.profile import Profile, ProfileError, resolve
from kge.pipeline.landed_tables import write_landed_tables

DDL_FILE, ROLES, GRAPH_MODEL = "ddl/kuzu.cypher", "access/roles.json", "graph-model.yaml"
NAME = re.compile(r"[A-Za-z_]\w*")
TIERS = ("gold", "silver", "bronze")


def open_graph(path: Path, fresh: bool = False, pidfile: Path | None = None):
    """The graph at path, or with fresh an empty one. Fresh removes the old graph's sidecars too:
    a process that stopped without closing it leaves graph.<store>.wal, which a new
    graph would replay and fail on.

    Fresh deletes nothing while another process holds the old graph. Deleting a
    file skips the store's lock, so it opens the old graph first and the lock answers:
    GraphLocked, naming the API's pid (pidfile, the profile's). Nor while the other store is loaded in this
    process: that refusal comes first. Once the lock has answered, and before anything is deleted, the store's
    entry goes from the LOADED marker beside the graph (kge/graph/release.py): a graph being replaced is not loaded."""
    from kge.graph.release import unmark
    from kge.adapters.graphstore import GraphLocked, connect, open_db, sidecars, store_module
    store_module()
    path = Path(path)
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                open_db(path, pidfile).close()
            except GraphLocked:
                raise
            except Exception:
                pass
        unmark(path)
        for p in [path, *sidecars(path)]:
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    return connect(open_db(path, pidfile))


def read_certification(release, doc_ids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """What upstream certification landed for each document in release, as Document properties, and what to warn about.

    Fail closed: no release, a release with no certification.json, or no row for
    a document, loads it as not AI-ready with no tier. A tier is kept only on a
    row landed AI-ready."""
    warnings: list[str] = []
    rows: dict[str, dict] = {}
    landed = release is not None and release.has(CERTIFICATION)
    if landed:
        rows = {r["asset_id"]: r for r in release.certification()}
    else:
        where = f"{release} lands no {CERTIFICATION}" if release is not None else "nothing has landed"
        warnings.append(f"{where}: every document loads as ai_ready = false, so the tier rule retrieves nothing. "
                        "Run make certify (kge/estate/certify.py) and load the graph again.")
    out, missing, untiered = {}, [], []
    for d in doc_ids:
        r = rows.get(d)
        if r is None:
            missing.append(d)
            out[d] = {"ai_ready": False, "tier": None, "cert_evidence": "no certification landed", "cert_policy": "", "cert_version": "",
                      "cert_from": "", "cert_to": ""}
            continue
        ready = r.get("ai_ready") == "yes"
        tier = r.get("tier") if ready and r.get("tier") in TIERS else None
        if ready and tier is None:
            untiered.append(d)
        out[d] = {"ai_ready": ready, "tier": tier, "cert_evidence": r.get("evidence") or "", "cert_policy": r.get("policy_id") or "",
                  "cert_version": r.get("version") or "", "cert_from": r.get("effective_from") or "", "cert_to": r.get("effective_to") or ""}
    if missing and landed:
        warnings.append(f"no certification row for {len(missing)} document(s) ({listed(missing)}): they load as ai_ready = false")
    if untiered:
        warnings.append(f"{len(untiered)} document(s) landed AI-ready with no valid tier ({listed(untiered)}): no tier filter retrieves them")
    unknown = sorted(set(rows) - set(doc_ids))
    if unknown:
        warnings.append(f"{len(unknown)} landed row(s) name no document in the estate ({listed(unknown)}): ignored")
    return out, warnings


def listed(ids: list[str], most: int = 8) -> str:
    return ", ".join(ids[:most]) + (f" and {len(ids) - most} more" if len(ids) > most else "")


def load_principals(conn, principals: list[dict], processes: dict[str, str] | None = None, w=None) -> int:
    """The access mirror: who may read which unit, write which terms, and perform which process, written on the graph
    store's writer w (a batch of its own, ended here, if none is given).

    Idempotent. Each principal's edges are replaced, so narrowing what a
    principal's role reads narrows it in the graph too: its edges go first, then
    it and its new ones are written.
    """
    from kge.adapters.graphstore import batch
    if processes is None:
        from kge.graph.schema import meta_model
        processes = meta_model()["node_types"]["Process"]["constants"]
    processes = process_vocabulary(processes)
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Process(id STRING, description STRING, PRIMARY KEY (id))")
    conn.execute("CREATE REL TABLE IF NOT EXISTS MAY_PERFORM(FROM Principal TO Process)")
    terms = ([r[0] for r in conn.execute("MATCH (t:Term) RETURN t.name").get_all()]
             if any("terms" in p.get("writes", []) for p in principals) else [])
    if w is None:
        with batch(conn) as own:
            return load_principals(conn, principals, processes, own)
    for pid, desc in processes.items():
        w.upsert("Process", pid, {"description": desc})
    for p in principals:
        w.delete_edges("CAN_READ", "Principal", p["principal"], "Unit")
        w.delete_edges("CAN_WRITE", "Principal", p["principal"], "Term")
        w.delete_edges("MAY_PERFORM", "Principal", p["principal"], "Process")
        w.upsert("Principal", p["principal"], {"role": p["role"]})
        for u in p["reads"]:
            w.create_edge("CAN_READ", "Principal", p["principal"], "Unit", u)
        if "terms" in p.get("writes", []):
            for t in terms:
                w.create_edge("CAN_WRITE", "Principal", p["principal"], "Term", t)
        for proc in p.get("processes", []):
            w.create_edge("MAY_PERFORM", "Principal", p["principal"], "Process", proc)
    return len(principals)


def sync_principals(prof: Profile) -> int:
    """The access mirror re-synced: the profile's identities bound to the role templates of its client's pack, as a load
    binds them, into the profile's graph (the one current names), whose Release must name the profile's client.
    Unloadable if the pack cannot be read, or nothing is released. A missing graph is not opened, since opening it
    would make an empty one, and the graph is opened before principals.json is touched, so a locked graph leaves that
    file alone too. A serving process reads the access mirror once per release (Graph.rights), and on a store that lets
    it run beside this one (BigQuery) it keeps the roles it read: so the command says to restart it."""
    try:
        graph = prof.graph_path()
    except ProfileError as exc:
        raise Unloadable(str(exc)) from None
    if not graph.exists():
        raise Unloadable(f"there is no graph {graph}: make graph loads one")
    conn = open_graph(graph, pidfile=prof.run_dir() / "api.pid")
    held = conn.execute("MATCH (r:Release) RETURN r.client").get_all()
    if len(held) != 1:
        raise Unloadable(f"the graph {graph} names no one release, so no client: load it with make graph")
    client = held[0][0]
    if client != prof.client:
        raise Unloadable(f"the graph {graph} was loaded for {client}, and profile {prof.id} is {prof.client}'s")
    pack = packs.read(prof.clients, client, lock=False)
    if pack.findings or not isinstance(pack.data.get("access"), dict) or not isinstance(pack.data.get("sources"), list):
        raise Unloadable(f"{client}'s pack cannot be read ({pack.findings[0] if pack.findings else 'no access or sources'}): "
                         "make compile names what is wrong")
    roles = {r["id"]: r for r in role_templates(pack.data)["roles"]}
    principals = bound(prof.identities, client, roles)
    write_principals(prof.run_dir(), principals)
    n = load_principals(conn, principals)
    def ids(v):
        return list(v) if isinstance(v, (list, tuple)) else []
    reads = {r[0]: ids(r[1]) for r in conn.execute("MATCH (p:Principal) OPTIONAL MATCH (p)-[:CAN_READ]->(u:Unit) RETURN p.id, collect(u.id)").get_as_df().itertuples(index=False)}
    procs = {r[0]: ids(r[1]) for r in conn.execute("MATCH (p:Principal) OPTIONAL MATCH (p)-[:MAY_PERFORM]->(x:Process) RETURN p.id, collect(x.id)").get_as_df().itertuples(index=False)}
    for pid in sorted(reads):
        print(f"{pid:20} reads {', '.join(sorted(reads[pid])) or '-':32} performs {', '.join(sorted(procs.get(pid, [])))}")
    print(RESTART)
    print(json.dumps({"principals": n}))
    return 0


def landed_release(prof: Profile, release: str):
    """The release to load, checked whole: release (an id, or current) of the profile's client's landing. LandingError
    says why there is none."""
    return Zone(prof.landing, prof.client).open(release)


RESTART = "restart the service so it reads the new roles: a running one keeps the roles it read until it restarts or follows a release"


class Unloadable(Exception):
    """An artifact or identities this loader will not load, and why."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_artifact(folder: Path, client: str | None) -> dict:
    """The manifest of the artifact in folder, once the artifact is found whole and loadable here, for client if one is
    given. Unloadable, naming what is wrong, otherwise; nothing is written either way."""
    folder = Path(folder)
    path = folder / MANIFEST
    if not path.is_file():
        raise Unloadable(f"{folder} holds no {MANIFEST}: it is not a compiled artifact (make compile prints one)")
    try:
        man = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Unloadable(f"its {MANIFEST} cannot be read ({type(exc).__name__}: {exc})") from None
    if not isinstance(man, dict) or not isinstance(man.get("files"), dict) or not isinstance(man.get("load_order"), list):
        raise Unloadable(f"its {MANIFEST} is not an artifact's manifest: it lists no files and no load order")
    if man.get("format") != FORMAT:
        raise Unloadable(f"its format is {man.get('format')!r}; this loader loads {FORMAT}")
    listed = man["files"]
    outside = sorted(p for p in listed if not isinstance(p, str) or PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts
                     or "\\" in p or p == MANIFEST)
    if outside:
        raise Unloadable(f"its {MANIFEST} lists {', '.join(map(str, outside))}, outside the artifact's own files")
    have = {p.relative_to(folder).as_posix(): p for p in sorted(folder.rglob("*")) if p.is_file()}
    have.pop(MANIFEST)
    wrong = ([f"{p} is missing" for p in sorted(set(listed) - set(have))]
             + [f"{p} is not one of its files" for p in sorted(set(have) - set(listed))]
             + [f"{p} is not the file its manifest hashed" for p in sorted(set(listed) & set(have))
                if sha256(have[p].read_bytes()) != listed[p]])
    if wrong:
        raise Unloadable(f"it is not whole: {'; '.join(wrong[:6])}{'; and more' if len(wrong) > 6 else ''}")
    if sha256("".join(f"{listed[p]}  {p}\n" for p in sorted(listed)).encode()) != man.get("model_hash"):
        raise Unloadable(f"its model hash, {man.get('model_hash')!r}, is not the hash of its files")
    platform = meta_model_version()
    made = semver(man.get("meta_model_version"))
    if made is None or made[0] != platform[0] or made > platform:
        raise Unloadable(f"it was compiled against meta-model {man.get('meta_model_version')!r}, which this platform, on meta-model "
                         f"{'.'.join(map(str, platform))}, does not load: compile it again (make compile)")
    dim = (man.get("params") or {}).get("embedding_dim")
    if dim != DIM:
        raise Unloadable(f"its embedding dimension {dim} is not the encoder's ({DIM}, kge/adapters/model/embed.py): compile it with "
                         f"embedding_dim: {DIM}")
    if client is not None and man.get("client") != client:
        raise Unloadable(f"it is {man.get('client')!r}'s artifact, and this profile is {client}'s")
    for need in (DDL_FILE, ROLES, GRAPH_MODEL):
        if need not in listed:
            raise Unloadable(f"it has no {need}")
    for step in man["load_order"]:
        ok = (isinstance(step, dict) and step.get("path") in listed and isinstance(step.get("stage"), str)
              and isinstance(step.get("rows"), int)
              and all(isinstance(step.get(k), str) and NAME.fullmatch(step[k]) for k in ("table", *(("from", "to") if "from" in step else ()))))
        if not ok:
            raise Unloadable(f"its load order holds a step it cannot load: {step!r}")
    return man


def meta_model_version() -> tuple[int, int, int]:
    """The version of the platform's meta-model, which this loader is built for."""
    from kge.graph.schema import meta_model
    return semver(meta_model()["version"])


def bound(path: Path, client: str, roles: dict[str, dict]) -> list[dict]:
    """The deployment's principals in path, each bound to its role template: who it is, the role's label, what it reads
    and writes, and the processes it may perform, as the run state's principals.json holds them. Unloadable if the file is not
    client's, lists no principal, binds one to a role the artifact does not hold, or names as its asker (the identity a
    command asks as when it names none) one that is not among its querying identities."""
    try:
        ids = yaml.safe_load(Path(path).read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise Unloadable(f"the identities, {path}, cannot be read ({type(exc).__name__})") from None
    owner = ids.get("client") if isinstance(ids, dict) else None
    if owner != client:
        raise Unloadable(f"the identities, {path}, are {owner!r}'s, and this deployment loads {client}")
    if not isinstance(ids.get("principals"), list) or not ids["principals"]:
        raise Unloadable(f"the identities, {path}, list no principals")
    out = []
    for b in ids["principals"]:
        if not isinstance(b, dict) or not isinstance(b.get("principal"), str):
            raise Unloadable(f"the identities, {path}, list {b!r}, which is not a principal bound to a role")
        r = roles.get(b.get("role"))
        if r is None:
            raise Unloadable(f"the identities, {path}, bind {b['principal']} to {b.get('role')!r}, which is not one of the artifact's "
                             f"roles ({', '.join(sorted(roles))})")
        p = {"principal": b["principal"], "role": r["label"], "reads": list(r["reads"])}
        if r.get("writes"):
            p["writes"] = list(r["writes"])
        p["processes"] = list(r["processes"])
        out.append(p)
    if "asker" in ids:
        asking = [p["principal"] for p in out if "querying" in p["processes"]]
        if ids["asker"] not in asking:
            raise Unloadable(f"the identities, {path}, name {ids['asker']} as the identity a command asks as, which is not one of "
                             f"their querying identities ({', '.join(asking) or 'none'})")
    return out


def keys(conn, model: dict, only: set[str] | None = None) -> dict[str, tuple[str, set]]:
    """Each node table's key property and the keys it holds, by its loaded name: those only names, else every one."""
    out = {}
    for t in model["node_types"].values():
        if only is not None and t["loaded_as"] not in only:
            continue
        held = conn.execute(f"MATCH (x:{t['loaded_as']}) RETURN x.{t['key']}").get_all()
        out[t["loaded_as"]] = (t["key"], {r[0] for r in held})
    return out


def documents(w, release) -> tuple[list[dict], int, int]:
    """The release's documents as Document nodes, with what certification landed for each (fail closed), and the
    SUPERSEDED_BY edges between them: the documents, how many, and how many landed AI-ready."""
    docs = release.documents()
    cert, warnings = read_certification(release, [d["id"] for d in docs])
    for said in warnings:
        print(f"kge/graph/load.py: WARNING: {said}", file=sys.stderr)
    ready = 0
    for d in docs:
        w.create_node("Document", {"id": d["id"], "title": d["title"], "shelf": d["shelf"], "kind": d["kind"], "unit": d["unit"] or "",
                                   "owner": d["owner"] or "", "version": d["version"] or "", "published_at": d["published_at"] or "",
                                   **cert[d["id"]]})
        ready += cert[d["id"]]["ai_ready"]
    for d in docs:
        if d.get("superseded_by"):
            w.create_edge("SUPERSEDED_BY", "Document", d["id"], "Document", d["superseded_by"])
    return docs, len(docs), ready


def staged(w, folder: Path, step: dict, held: dict, ends: tuple[str, str] | None, release) -> int:
    """A file the artifact stages for the landing (DEFINES: the pack names the documents, the landing holds them): its
    rows whose ends are both in the graph, copied; each other row a warning, S12, and no edge. How many were copied."""
    with (folder / step["path"]).open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    kept = []
    for r in rows:
        missing = [(t, k) for t, k in ((step["from"], r[0]), (step["to"], r[1])) if k not in held[t][1]]
        if missing:
            gone = " and ".join(f"{t} {k}" for t, k in missing)
            print(f"kge/graph/load.py: WARNING: S12 {step['table']} {r[0]} -> {r[1]}: {gone} did not land in release {release.id}, "
                  "so there is no edge", file=sys.stderr)
        else:
            kept.append(r)
    if kept:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            with path.open("w", newline="") as f:
                out = csv.writer(f, lineterminator="\n")
                out.writerow(header)
                out.writerows(kept)
            w.load_file(step["table"], path, ends)
    return len(kept)


def evidence(match: dict, floor: float) -> str:
    """What a match's row in the steward queue says of it, beside its table and id (the tag's value) and its confidence:
    what upstream made of it first (kge/services/ask/engine/graphview.py Graph.matches reads it back as the row's upstream), then whether it is
    below the floor, and its method."""
    below = f", below the floor {floor}" if float(match["confidence"]) < floor else ""
    return f"{match['status'].lower()} upstream{below}; a {match['method']} match"


def matches(w, release) -> int:
    """A curated source's matches certification landed for a steward: one Tag each (see the module's step 6). How many."""
    n = 0
    for source, held in release.reviews().items():
        table = f"{source}.{held['table']}"
        for m in held["matches"]:
            w.create_node("Tag", {"id": f"{table}/match/{m['id']}", "family": "xref_match", "value": f"{table}:{m['id']}",
                                  "generator": "certification", "confidence": float(m["confidence"]), "model": m["method"],
                                  "prompt_version": "", "created_at": "", "evidence": evidence(m, held["floor"])})
            n += 1
    return n


def count(conn, cypher: str) -> int:
    return conn.execute(cypher).get_all()[0][0]


def write_principals(folder: Path, principals: list[dict]) -> None:
    """principals.json in folder, the profile's run state, as kge/services/identity/service.py reads it, replaced whole: never written
    through a link."""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".principals.json.{os.getpid()}"
    tmp.write_text(json.dumps(principals, indent=2))
    os.replace(tmp, folder / "principals.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose graph to load, and from what: the profile (default: PROFILE, else the registry's "
                                      "default), its client, landing, identities and graph store")
    ap.add_argument("--artifact", help="the compiled artifact to load: the folder make compile prints, <artifacts>/<client>/<model_hash>/")
    ap.add_argument("--release", default="current", help="the landed release to load: its id, or current (the default)")
    ap.add_argument("--principals-only", action="store_true", help="re-sync the access mirror into an existing graph and stop")
    args = ap.parse_args()
    from kge.adapters.graphstore import GraphLocked, graph_q
    try:
        prof = resolve(args.profile)
    except ProfileError as exc:
        print(f"graph: {exc}. Nothing was loaded.", file=sys.stderr)
        return 2
    if args.principals_only:
        try:
            return sync_principals(prof)
        except GraphLocked as exc:
            print(f"graph: {exc}", file=sys.stderr)
            return 2
        except Unloadable as exc:
            print(f"graph: {exc}. Nothing was synced.", file=sys.stderr)
            return 2
    if not args.artifact:
        print("graph: no artifact to load: name the compiled pack with --artifact <dir>, the folder make compile prints "
              "(make graph checks the lock, compiles the pack and loads it). Nothing was loaded.", file=sys.stderr)
        return 2
    folder = Path(args.artifact)
    try:
        man = read_artifact(folder, prof.client)
        release = landed_release(prof, args.release)
        if man["client"] != release.client:
            raise Unloadable(f"it is {man['client']!r}'s artifact, and the landing loaded is {release.client}'s")
        model = yaml.safe_load((folder / GRAPH_MODEL).read_text())
        roles = {r["id"]: r for r in json.loads((folder / ROLES).read_text())["roles"]}
        principals = bound(prof.identities, release.client, roles)
        journal = Journal(prof.overlay_dir(), release.client)
        decided = journal.decisions()
        conn = open_graph(prof.at(man["model_hash"]).graph_path(), fresh=True, pidfile=prof.run_dir() / "api.pid")
        from kge.adapters.retrieval import forget_chunks
        forget_chunks(prof.at(man["model_hash"]).graph_path().parent)
    except OverlayError as exc:
        print(f"graph: {exc}. Nothing was loaded.", file=sys.stderr)
        return 2
    except Unloadable as exc:
        print(f"graph: the artifact {folder}: {exc}. Nothing was loaded.", file=sys.stderr)
        return 2
    except LandingError as exc:
        hint = "" if "make certify" in str(exc) else ". make certify (kge/estate/certify.py) lands the estate again"
        print(f"graph: {exc}{hint}. Nothing was loaded.", file=sys.stderr)
        return 2
    except GraphLocked as exc:
        print(f"graph: {exc}", file=sys.stderr)
        return 2

    from kge.adapters.graphstore import batch, store
    store().create(conn, model, store().ddl(folder, model))
    order = man["load_order"]
    pairs = Counter(step["table"] for step in order if "from" in step)
    group = {step["path"]: (step["from"], step["to"]) for step in order if "from" in step and pairs[step["table"]] > 1}
    with batch(conn) as w:
        for step in [s for s in order if "from" not in s] + [s for s in order if "from" in s]:
            if step["stage"] == "copy" and step["rows"]:
                w.load_file(step["table"], folder / step["path"], group.get(step["path"]))
        docs, n_docs, ready = documents(w, release)
    later = [step for step in order if step["stage"] != "copy"]
    held = keys(conn, model, {step[k] for step in later for k in ("from", "to")})
    defines = 0
    with batch(conn) as w:
        for step in later:
            n = staged(w, folder, step, held, group.get(step["path"]), release)
            defines += n if step["table"] == "DEFINES" else 0

    if decided:
        terms, orphans = replay_terms(graph_q(conn), decided, release)
        warn(LOAD, orphans)
        journal.orphans(LOAD, orphans, fresh=True)
        print(f"kge/graph/load.py: overlay: {len(decided)} decision{'' if len(decided) == 1 else 's'} journaled in {journal.path}: "
              f"{terms} accepted term{'' if terms == 1 else 's'} replayed here, the decisions themselves by kge/pipeline/run.py",
              file=sys.stderr)

    with batch(conn) as w:
        load_principals(conn, principals, model["node_types"]["Process"]["constants"], w)
        w.create_node("Release", release.about())
        w.create_node("GraphModel", {"id": man["model_hash"], "client": man["client"], "pack_version": man["pack"]["version"],
                                     "meta_model_version": man["meta_model_version"], **({"title": man["title"]} if "title" in man else {})})
        held = matches(w, release)
    write_principals(prof.run_dir(), principals)
    write_landed_tables(prof.at(man["model_hash"]).graph_path().parent, release)
    counts = {"units": count(conn, "MATCH (x:Unit) RETURN count(x)"), "tables": count(conn, "MATCH (:Unit)-[:HAS_TABLE]->(x:Tbl) RETURN count(x)"),
              "columns": count(conn, "MATCH (:Unit)-[:HAS_TABLE]->(:Tbl)-[:HAS_COLUMN]->(x:Col) RETURN count(x)"),
              "joins": count(conn, "MATCH ()-[r:JOINS]->() RETURN count(r)"),
              "terms": count(conn, "MATCH (x:Term) RETURN count(x)"), "mappings": count(conn, "MATCH ()-[r:MAPS_TO]->() RETURN count(r)"),
              "no_join": count(conn, "MATCH ()-[r:NO_JOIN]->() RETURN count(r)"), "principals": len(principals), "documents": n_docs,
              "ai_ready": ready, "defines": defines,
              "curated_tables": count(conn, "MATCH (:CuratedSource)-[:HAS_CURATED_TABLE]->(x:Tbl) RETURN count(x)"),
              "contexts": count(conn, "MATCH (x:Context) RETURN count(x)"), "declared_joins": count(conn, "MATCH (x:JoinRule) RETURN count(x)"),
              "matches_for_review": held}
    print(json.dumps({**counts, "release": release.id, "client": release.client, "model_hash": man["model_hash"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
