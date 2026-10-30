"""The deployment's profiles: which client a process serves, and where that client's things are.

    python3 -m kge.pipeline.profile [--profile <id>]     # check the registry; say where the profile's things are
    python3 -m kge.pipeline.profile --check              # the same, in one line (make serve, before it serves)
    python3 -m kge.pipeline.profile --make               # name=value words, for the Makefile

A profile is picked at deployment, never per request, and one runtime process
serves one (issue #39: one process per client, chosen by config). So the
registry is deployment config, deploy/profiles.yaml, not the platform's: the
platform names no client. It holds a default and, per profile id, the client
and pointers only, each resolved relative to the registry file:

  client       the client's id, following CLIENT_ID and CLIENT_ID_RULE: its pack's manifest.client
  clients      the folder holding the client's own, <clients>/<client>/: its pack
               (pack.yaml, pack.lock) and baselines
  artifacts    compiled artifacts, the client's in <artifacts>/<client>/<model_hash>/,
               and its current pointer, <artifacts>/<client>/current, a symlink to the
               <model_hash> whose graph is served (kge/graph/release.py swaps it)
  store        graph stores, the client's in <store>/<client>/: a graph per artifact
               loaded, <model_hash>/graph.<store> with its LOADED marker, the runtime
               overlay (overlay/), and the run state of the process that serves it
               (run/: the tokens, the API's pid, the A/B runs, the pipeline's stats,
               the eval)
  landing      the landing zone, the client's releases in <landing>/<client>/
  warehouses   the client's warehouses, <warehouses>/<unit>.duckdb, read only; an
               onboarded unit's one per version, <warehouses>/onboarded/<unit>/v<N>/
               <unit>.duckdb (kge/pipeline/warehouse_versions.py)
  identities   the deployment's principals, bound to the pack's roles, and the
               querying identity a command asks as when it names none (asker)
  baseline     the client's A/B baseline, or null for none

A registry written before Ladybug became the only graph store may still give a
profile graph_store: ladybug (or null). It is read and ignored; any other value
is refused, since Kuzu was removed.

resolve() gives the profile: the id it is given, else PROFILE (the environment,
then .env), else the registry's default. PROFILES names another registry file,
which is how a test points a process at a scratch copy. An unknown id fails,
naming the known ones. The registry is checked whole first, and refused whole,
naming each problem: every profile id a slug, every client following CLIENT_ID
and CLIENT_ID_RULE, every field known and every pointer given; each profile's
own roots (its client's folder under clients, artifacts, store and landing,
and its warehouses) neither shared with nor
nested in any other root, its own or another profile's, so no profile reads or
writes another's; no profile's identities or baseline inside another profile's
roots; and the artifact a profile's current pointer names
(<artifacts>/<client>/current) compiled for the profile's client.

A profile serves the graph of the artifact its client's current pointer names,
read when it is asked (model_hash, graph_path), or the one it is pinned to: at()
pins a profile to a hash, a candidate's (make load, make margin HASH=), and
pin() to the one current names now, which is how a process reads current once,
when it starts (kge/services/ask/engine/core.py Engine). With nothing released and no hash pinned,
there is no graph to name: graph_path raises ProfileError, saying make graph
releases one.

The simulated estate's own files, the question sets, its summary and its
document index, are written beside the warehouses (kge/estate/generate.py --out
writes <out>/warehouses/ among them): estate() is that folder.

Names no client: the registry does.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.envfile import load_env
from kge.pipeline.settings import ROOT, settings

DEMO = ROOT
REGISTRY = DEMO / "deploy" / "profiles.yaml"
SLUG = re.compile(r"[a-z][a-z0-9-]*")
HASH = re.compile(r"[0-9a-f]{1,64}")
ROOTS = ("clients", "artifacts", "store", "landing")
POINTERS = (*ROOTS, "warehouses", "identities", "baseline")
FIELDS = ("client", *POINTERS)
OPTIONAL = ("baseline",)
RETIRED = {"graph_store": (None, "ladybug")}
CURRENT, MANIFEST = "current", "manifest.json"
RUN = {"tokens": "tokens.json", "pid": "api.pid", "serving": "api.json", "runs": "runs", "pipeline_stats": "pipeline_stats.json",
       "eval": "eval.json", "principals": "principals.json"}


class ProfileError(ValueError):
    """No such profile, or a registry that cannot be used, and why."""


@dataclass(frozen=True)
class Profile:
    """One profile of the registry, its pointers resolved: absolute, as the registry gives them (no link followed).
    pinned is the model hash whose graph the profile names (at, pin); None means whichever current names. serving: the
    profile a server opens its graph by (serve): the generation its hash's last release recorded, where the store keeps
    one (GraphStore.served), never what a later load of the same hash repoints; every other step reads what its hash's
    folder names now, the last load's."""
    id: str
    client: str
    clients: Path
    artifacts: Path
    store: Path
    landing: Path
    warehouses: Path
    identities: Path
    baseline: Path | None
    registry: Path
    pinned: str | None = None
    serving: bool = False

    def roots(self) -> dict[str, Path]:
        """The profile's own folders, which no other root shares or holds: its client's pack folder, artifacts, graph store
        and landing, and its warehouses."""
        return {"pack": self.clients / self.client, "artifacts": self.artifacts / self.client, "store": self.store / self.client,
                "landing": self.landing / self.client, "warehouses": self.warehouses}

    def current(self) -> Path | None:
        """The artifact the client's current pointer names, <artifacts>/<client>/current; None while there is none."""
        link = self.artifacts / self.client / CURRENT
        return link.resolve() if link.is_dir() else None

    def at(self, model_hash: str) -> Profile:
        """The profile pinned to model_hash: its graph is that artifact's, whatever current names. ProfileError if it is not
        a model hash (lower-case hex), so no name reaches outside the client's folders."""
        if not isinstance(model_hash, str) or not HASH.fullmatch(model_hash):
            raise ProfileError(f"{model_hash!r} is not a model hash (lower-case hex, as make compile names an artifact's folder)")
        return replace(self, pinned=model_hash)

    def serve(self) -> Profile:
        """The profile as a server opens it (serving): the API and the job's eval, which answer as the release does."""
        return replace(self, serving=True)

    def pin(self) -> Profile:
        """The profile pinned to the hash current names now, read once: a process that starts on it keeps serving that
        graph when current is swapped, until it restarts. Itself if it is pinned already, or nothing is released."""
        if self.pinned is not None:
            return self
        cur = self.model_hash()
        return self if cur is None else self.at(cur)

    def model_hash(self) -> str | None:
        """The model hash whose graph the profile names: the one it is pinned to, else the current artifact's, its folder's
        name; None while there is none."""
        if self.pinned is not None:
            return self.pinned
        cur = self.current()
        return cur.name if cur else None

    def graph_path(self) -> Path:
        """The client's graph of the artifact the profile names (model_hash), in its graph store:
        <store>/<client>/<model_hash>/graph.ladybug. ProfileError while nothing is released."""
        from kge.adapters.graphstore import graph_path, served
        h = self.model_hash()
        if h is None:
            raise ProfileError(f"profile {self.id} has released no graph: its current pointer, {self.artifacts / self.client / CURRENT}, "
                               "names no artifact; make graph compiles the pack, loads it and releases it")
        path = graph_path(self.store / self.client / h)
        return served(path) if self.serving else path

    def identity(self) -> dict:
        """The release identity of the graph the profile names (graph_path: for a profile that serves, what its hash's
        last release recorded): its model hash, its graph's generation (GraphStore.generation) and the chunks table its
        pointer names (dataset.table), the last two None where the store or retrieval keeps none. A release of the same
        hash at a new generation is a new identity, which a service follows (kge/services/onboard/jobs.py
        follow_release). For a profile that serves, it is read from one read of the release record (released), where
        there is one. ProfileError while nothing is released."""
        from kge.adapters.graphstore import generation
        from kge.adapters.retrieval import named_chunks
        path = self.graph_path()
        record = self.released()
        if record is not None:
            return self.identity_of(record)
        return {"hash": self.model_hash(), "graph": generation(path), "chunks": named_chunks(self)}

    def released(self) -> dict | None:
        """For a profile that serves, the release record of the hash it names (kge/adapters/released.py), read once: the
        graph and the chunks one release named, together, which a server opens from (kge/services/ask/engine/graphview.py
        Graph) and its identity is read from (identity_of). None for a profile that does not serve, or a hash no release
        recorded anything of (a graph that is its own file, searching its own vectors). ProfileError while nothing is
        released."""
        from kge.adapters import released
        path = self.graph_path()
        return released.read(path.parent) if self.serving else None

    def identity_of(self, record: dict) -> dict:
        """The release identity a release record names (released): its hash, its graph's generation and its chunks
        table."""
        from kge.adapters.graphstore import generation_of
        from kge.adapters.retrieval import entry_chunks
        return {"hash": self.model_hash(), "graph": generation_of(record.get("graph")), "chunks": entry_chunks(record.get("chunks"))}

    def overlay_dir(self) -> Path:
        """The runtime overlay, in the client's graph store: the steward decisions' journal (kge/pipeline/overlay.py)."""
        return self.store / self.client / "overlay"

    def run_dir(self) -> Path:
        """The run state of the process serving the profile, in the client's graph store (RUN)."""
        return self.store / self.client / "run"

    def estate(self) -> Path:
        """The simulated estate the warehouses are part of: the folder that holds them, where the estate's question sets,
        its summary and its document index sit beside them."""
        return self.warehouses.parent

    def asker(self) -> str | None:
        """The querying identity a command asks as when it names none: the one the deployment's identities name (asker,
        which the graph load checks is one of their querying identities), else the first querying identity of the access
        mirror the graph load wrote (principals.json, in the run state); None if neither says. Never a client's principal
        named in code: who may ask is the deployment's."""
        try:
            named = yaml.safe_load(self.identities.read_text()).get("asker")
        except (OSError, UnicodeDecodeError, yaml.YAMLError, AttributeError):
            named = None
        if isinstance(named, str) and named.strip():
            return named.strip()
        try:
            bound = json.loads((self.run_dir() / RUN["principals"]).read_text())
        except (OSError, ValueError):
            return None
        return next((p["principal"] for p in bound if isinstance(p, dict) and isinstance(p.get("principal"), str)
                     and "querying" in (p.get("processes") or [])), None) if isinstance(bound, list) else None

    def asking(self, named: str | None = None) -> str:
        """Who a command asks as: the principal it names, else the profile's asker. ProfileError if there is neither."""
        who = named or self.asker()
        if not who:
            raise ProfileError(f"no principal named, and profile {self.id} has none to ask as: its identities, {self.identities}, "
                               "name no asker, and no graph load has bound a querying identity (make graph); name one with "
                               "--as, or PRINCIPAL for make")
        return who

    def paths(self) -> dict[str, Path]:
        """Every file and folder of the profile's, by name: what its commands and its runtime read and write."""
        pack, run = self.clients / self.client, self.run_dir()
        out = {"pack": pack / "pack.yaml", "lock": pack / "pack.lock", "artifacts": self.artifacts / self.client,
               "landing": self.landing / self.client, "landed": self.landing / self.client / CURRENT, "warehouses": self.warehouses,
               "estate": self.estate(), "identities": self.identities, "overlay": self.overlay_dir(),
               "run": run, **{name: run / at for name, at in RUN.items()}}
        if self.baseline is not None:
            out["baseline"] = self.baseline
        cur = self.current()
        if cur is not None:
            out["current"] = cur
        if self.model_hash() is not None:
            out["graph"] = self.graph_path()
        return out


def registry_path(registry: Path | str | None = None) -> Path:
    """The registry file: the one given, else PROFILES (the environment, then .env), else the deployment's."""
    load_env()
    return Path(registry or (settings().PROFILES or "").strip() or REGISTRY)


def resolve(profile_id: str | None = None, registry: Path | str | None = None) -> Profile:
    """The profile this process serves: the id given, else PROFILE (the environment, then .env), else the registry's
    default, from the registry checked whole (read). ProfileError if there is no such profile, naming the known ones."""
    path = registry_path(registry)
    default, held = read(path)
    pid = (profile_id or settings().PROFILE or "").strip() or default
    if pid not in held:
        raise ProfileError(f"no profile {pid!r} in {path}: the known profiles are {', '.join(held)}")
    return held[pid]


def read(path: Path) -> tuple[str, dict[str, Profile]]:
    """The registry's default and its profiles, by id, in its order; ProfileError naming every problem if it cannot be
    used (see the module's docstring)."""
    path = Path(path)
    if not path.is_file():
        raise ProfileError(f"no profiles registry {path}: deploy/profiles.yaml names each profile and its pointers")
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"the profiles registry {path} cannot be read: {exc}") from None
    if not isinstance(raw, dict):
        raise ProfileError(f"the profiles registry {path} is not a mapping of default and profiles")
    problems: list[str] = []
    entries = raw.get("profiles")
    if not isinstance(entries, dict) or not entries:
        problems.append("no profiles")
        entries = {}
    held: dict[str, Profile] = {}
    for pid, e in entries.items():
        p = profile(path, pid, e, problems)
        if p is not None:
            held[p.id] = p
    default = raw.get("default")
    if entries and default not in entries:
        problems.append(f"the default, {default!r}, is no profile")
    unknown = [k for k in raw if k not in ("default", "profiles")]
    if unknown:
        problems.append(f"{', '.join(map(str, unknown))} {'is' if len(unknown) == 1 else 'are'} not a registry field")
    if not problems:
        problems += overlaps(list(held.values())) + current_clients(list(held.values()))
    if problems:
        raise ProfileError(f"the profiles registry {path} is refused: " + "; ".join(problems))
    return default, held


def profile(path: Path, pid, e, problems: list[str]) -> Profile | None:
    """One entry, checked; None, with its problems added, if it cannot be used. Its paths are relative to the
    registry file, normalised with no link followed."""
    if not isinstance(pid, str) or not SLUG.fullmatch(pid):
        problems.append(f"profile id {pid!r} is not a slug ({SLUG.pattern})")
        return None
    if not isinstance(e, dict):
        problems.append(f"{pid}: not a mapping of its client and pointers")
        return None
    before = len(problems)
    problems += [f"{pid}: {k} is not a profile field" for k in e if k not in FIELDS and k not in RETIRED]
    problems += [f"{pid}: {k} is retired, Ladybug being the only graph store (Kuzu was removed): remove the line, not {e[k]!r}"
                 for k, kept in RETIRED.items() if k in e and e[k] not in kept]
    problems += [f"{pid}: no {k}" for k in FIELDS if k not in OPTIONAL and k not in e]
    client = e.get("client")
    if "client" in e and (not isinstance(client, str) or not CLIENT_ID.fullmatch(client)):
        problems.append(f"{pid}: client {client!r} is refused: {CLIENT_ID_RULE}")
    for k in POINTERS:
        v = e.get(k)
        if k in e and not (v is None and k in OPTIONAL) and (not isinstance(v, str) or not v.strip()):
            problems.append(f"{pid}: {k} is not a path, {v!r}")
    if len(problems) > before:
        return None
    at = lambda v: Path(os.path.normpath(path.parent.absolute() / v.strip()))  # noqa: E731
    return Profile(id=pid, client=client, clients=at(e["clients"]), artifacts=at(e["artifacts"]), store=at(e["store"]),
                   landing=at(e["landing"]), warehouses=at(e["warehouses"]), identities=at(e["identities"]),
                   baseline=at(e["baseline"]) if e.get("baseline") else None, registry=path)


def overlaps(held: list[Profile]) -> list[str]:
    """Each root shared with or nested in another, its own profile's or another's, and each profile's identities or
    baseline inside another profile's roots, compared as the links on disk resolve."""
    out: list[str] = []
    seen: list[tuple[str, str, Path]] = []
    for p in held:
        for name, root in p.roots().items():
            r = root.resolve()
            for who, other, o in seen:
                if r == o:
                    out.append(f"{p.id}'s {name} root {r} is {who}'s {other} root")
                elif o in r.parents:
                    out.append(f"{p.id}'s {name} root {r} is inside {who}'s {other} root {o}")
                elif r in o.parents:
                    out.append(f"{who}'s {other} root {o} is inside {p.id}'s {name} root {r}")
            seen.append((p.id, name, r))
    for p in held:
        for name, f in (("identities", p.identities), ("baseline", p.baseline)):
            if f is None:
                continue
            f = f.resolve()
            for who, other, o in seen:
                if who != p.id and (f == o or o in f.parents):
                    out.append(f"{p.id}'s {name} {f} is inside {who}'s {other} root {o}")
    return out


def current_clients(held: list[Profile]) -> list[str]:
    """Each profile whose current artifact was compiled for another client, as its manifest says."""
    out = []
    for p in held:
        cur = p.current()
        if cur is None:
            continue
        try:
            client = json.loads((cur / MANIFEST).read_text()).get("client")
        except (OSError, ValueError, AttributeError):
            out.append(f"{p.id}: its current artifact, {cur}, has no manifest it can read")
            continue
        if client != p.client:
            out.append(f"{p.id}: its current artifact, {cur}, is client {client}'s, not {p.client}'s")
    return out


def shown(path: Path) -> str:
    """A path as a command run from the current folder names it: relative where it is under it."""
    path = Path(path)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile (default: PROFILE, else the registry's default)")
    ap.add_argument("--check", action="store_true", help="say which profile, in one line")
    ap.add_argument("--make", action="store_true", help="name=value words for the Makefile: the profile's id, client, "
                                                       "paths, relative to the current folder where they are under it, the "
                                                       "model hash current names, loaded=yes where current's graph is loaded "
                                                       "on this graph store, and sync=<module>, the step that copies a "
                                                       "loaded graph where its warehouses and retrieval read it, if any")
    ap.add_argument("--hash", help="with --make, report whether this model hash is loaded instead of current")
    args = ap.parse_args(argv)
    try:
        p = resolve(args.profile)
    except (ProfileError, ValueError) as exc:
        print(f"profile: {exc}", file=sys.stderr)
        return 2
    from kge.adapters.graphstore import graph_path, loaded, store_name
    paths = p.paths()
    if args.make:
        from kge.adapters import sync_step
        words = {"id": p.id, "client": p.client, "pack": shown(p.clients / p.client), "estate": shown(p.estate()),
                 "landed": shown(paths["landed"]), "store": shown(p.store / p.client), "runs": shown(paths["runs"]),
                 "baseline": shown(p.baseline) if p.baseline else "", "artifacts": shown(paths["artifacts"]),
                 "current": p.model_hash() or "", "sync": sync_step() or "",
                 "loaded": "yes" if (args.hash or p.model_hash()) and
                 loaded(graph_path(p.store / p.client / (args.hash or p.model_hash()))) else "",
                 "graphfile": f"graph.{store_name()}"}
        print(" ".join(f"{k}={v}" for k, v in words.items() if v))
        return 0
    graph = f"its graph {shown(paths['graph'])}" if "graph" in paths else "no graph released yet (make graph)"
    print(f"profile {p.id}, client {p.client}, from {shown(p.registry)}, on {store_name()}" + (f": {graph}" if args.check else ""))
    if args.check:
        return 0
    for name in ("pack", "lock", "artifacts", "current", "landed", "warehouses", "estate", "identities", "graph", "overlay", "run", "baseline"):
        if name in paths:
            print(f"  {name:<11} {shown(paths[name])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
