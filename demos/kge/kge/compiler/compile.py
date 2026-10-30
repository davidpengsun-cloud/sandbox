"""Compile a client's pack into a graph artifact.

    python3 -m kge.compiler.compile [--profile <id>] [--meta-model meta-model.yaml] [--check]
    python3 -m kge.compiler.compile --client <client> --clients clients --out build/artifacts
    make compile
    make check                       # --check, after the lock's check: writes nothing

Reads the platform's meta-model (meta-model.yaml), the client's pack
(<clients>/<client>/pack.yaml and the files its manifest includes) and its lock
(pack.lock beside it), and nothing else: no landing, no warehouse, no graph.
It checks the pack first (kge/compiler/checks.py) and lists every finding on
stderr as "<rule> <path>: <message>"; on any finding it exits 2 and writes
nothing. Otherwise it writes the artifact (kge/compiler/emit.py) to
<out>/<client>/<model_hash>/ and prints that path, its one line on stdout.

With --check it checks the pack and builds the artifact in memory, and writes
nothing: it prints the path the artifact has, or would have, and says on
stderr whether it is compiled yet. make check runs it after the lock's check,
and make load and make release read the pack's model hash from it.

The same pack, lock and compiler give the same hash and the same bytes, so a
second compile finds the artifact there and writes nothing; a folder already
under the hash that holds other bytes stops the compile (exit 2). The checks it
cannot make are printed as not checked here, and so is how many of the foreign
keys the lock harvested no declared join covers: candidates for one, which
graph-model.yaml lists (join_candidates).

The client, the folder holding its pack and where artifacts go are the
profile's (kge/pipeline/profile.py: --profile, else PROFILE, else the registry's
default; deploy/profiles.yaml in the demo): its client, its clients and its
artifacts. --client, --clients and --out name them instead, each one given
replacing the profile's. The path it prints is relative to the current folder
where it is under it.

The compiler's own version, VERSION, is what a pack's manifest.platform.compiler
range is held to: 0.2 filled meta-model 1.1's types, 0.3 1.2's, and 0.4 fills
1.3's, the platform's preloaded standards and the pack's mappings to them.

Names no client: the client is the deployment's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from kge.pipeline.settings import ROOT
from kge.compiler import checks, emit
from kge.compiler import pack as packs
from kge.compiler.pack import Finding
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.profile import ProfileError, resolve, shown

DEMO = ROOT
VERSION = "0.4.0"

def report(found: list[Finding], checked: bool = True) -> int:
    for f in found:
        print(f, file=sys.stderr)
    if checked:
        print(f"compile: {checks.NOT_CHECKED}", file=sys.stderr)
    print(f"compile: {len(found)} finding{'' if len(found) == 1 else 's'}; nothing was written.", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose pack, from where, to where: the profile's client, clients and artifacts (default: "
                                      "PROFILE, else the registry's default)")
    ap.add_argument("--client", help="whose pack to compile, instead of the profile's client")
    ap.add_argument("--clients", help="the folder holding each client's pack, instead of the profile's")
    ap.add_argument("--out", help="where artifacts go, a folder per client, instead of the profile's artifacts")
    ap.add_argument("--meta-model", default=str(DEMO / "meta-model.yaml"), help="the platform's meta-model (default meta-model.yaml)")
    ap.add_argument("--check", action="store_true", help="check the pack and print the artifact's path, writing nothing")
    args = ap.parse_args(argv)
    identities = None
    if None in (args.client, args.clients, args.out):
        try:
            prof = resolve(args.profile)
        except ProfileError as exc:
            print(f"compile: {exc}. Nothing was written.", file=sys.stderr)
            return 2
        args.client = prof.client if args.client is None else args.client
        args.clients = prof.clients if args.clients is None else args.clients
        args.out = prof.artifacts if args.out is None else args.out
        identities = prof.identities
    if not CLIENT_ID.fullmatch(args.client):
        return report([Finding("S00", "--client", f"--client {args.client!r} is refused: {CLIENT_ID_RULE}; it names "
                               "the client's folder")], checked=False)
    try:
        meta = packs.meta_model(Path(args.meta_model))
        if not isinstance(meta, dict) or not all(isinstance(meta.get(k), dict) for k in ("node_types", "edge_types")):
            raise ValueError("it has no node_types and edge_types")
        if checks.semver(meta.get("version")) is None:
            raise ValueError(f"its version, {meta.get('version')!r}, is not semver")
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"compile: the meta-model, {args.meta_model}, cannot be read ({exc if isinstance(exc, ValueError) else type(exc).__name__}). "
              "Nothing was written.", file=sys.stderr)
        return 2
    identities = identities or DEMO / "deploy" / args.client / "identities.yaml"
    pack = packs.read(Path(args.clients), args.client, identities=identities)
    found = checks.check(pack, meta, VERSION, emit.FILLS)
    if found:
        return report(found)
    from kge.compiler import catalogue as discovery_catalogue
    catalogue, found = discovery_catalogue.read(pack)
    if found:
        return report(found)
    catalogue = discovery_catalogue.derive(catalogue, pack)
    artifact = emit.build(pack, meta, VERSION, catalogue)
    if args.check:
        folder = Path(args.out) / args.client / artifact.model_hash
        man = artifact.manifest
        print(f"compile: {checks.NOT_CHECKED}", file=sys.stderr)
        print(f"compile: {man['pack']['id']} {man['pack']['version']} for {args.client}, against meta-model {meta['version']}: no "
              f"findings; its model hash is {artifact.model_hash}, "
              f"{'compiled' if (folder / emit.MANIFEST).is_file() else 'not compiled yet (make compile writes it)'}. Nothing was "
              "written.", file=sys.stderr)
        print(f"{shown(folder)}/")
        return 0
    try:
        folder, written = emit.write(artifact, Path(args.out), args.client)
    except emit.Clash as exc:
        print(f"compile: {exc}", file=sys.stderr)
        return 2
    man = artifact.manifest
    print(f"compile: {checks.NOT_CHECKED}", file=sys.stderr)
    keys = sum(len(t["foreign_keys"]) for held in pack.lock["sources"].values() for t in held["tables"])
    print(f"compile: {len(checks.candidates(pack))} of {keys} harvested foreign keys no declared join covers: join candidates, "
          "listed in graph-model.yaml (join_candidates)", file=sys.stderr)
    print(f"compile: {man['pack']['id']} {man['pack']['version']} for {args.client}, against meta-model {meta['version']}: "
          f"{len(artifact.tree())} files, {sum(man['counts']['nodes'].values())} node rows and "
          f"{sum(man['counts']['edges'].values())} edge rows; {'written' if written else 'already compiled, unchanged'}", file=sys.stderr)
    print(f"{shown(folder)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
