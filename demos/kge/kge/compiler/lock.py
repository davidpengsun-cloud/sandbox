"""Freeze a client's landed source catalogue into its pack.lock, or check the lock still holds it.

    python3 -m kge.compiler.lock [--profile <id>] [--release current|<id>] [--check]
    python3 -m kge.compiler.lock --client <client> --clients clients --landing build/landing [--check]
    make lock

The pack (<clients>/<client>/pack.yaml, with the files its manifest includes,
read as the compiler reads it: kge/compiler/pack.py) declares the client's
sources, and after them its curated sources (the curated layer's own, such as
a certified cross-reference). Each it marks harvest: true takes its tables,
columns and keys from the catalogue AI certification landed for it,
structured/<source>.json in the release (kge/landing/zone.py), never from a
warehouse. Row counts and storage are read through the estate adapter, at each
source's live warehouse version. The lock, <clients>/<client>/pack.lock,
freezes those catalogues, so a compile can read the pack and the lock and
nothing live. Per source it holds the tables in name order, each with its
columns {name, type, description, ordinal} in position order, its primary key
and its foreign keys {columns, ref_table, ref_columns}, every column of a
composite key kept. A column's description is its landed comment, null if it
had none. The lock is canonical JSON (sorted keys, a trailing newline) with no
timestamp and no hash. It includes the landed as-of date, so the same catalogue
and estate give the same bytes; it is committed beside the pack, which it
belongs to.

Writing leaves a lock that is already current as it is. --check writes
nothing: it exits 0 when the lock holds what the release landed, and 2 with
"pack.lock is stale: run make lock", naming each difference, when it does not;
make graph runs it before it loads anything. Either way it exits 2, saying
why, when there is nothing to freeze: no landed release, a pack that is not
the client's, or a harvested source that landed no catalogue.

The client, the folder holding its pack and the landing zone are the
profile's (kge/pipeline/profile.py: --profile, else PROFILE, else the registry's
default): its client, its clients and its landing. --client, --clients and
--landing name them instead, each one given replacing the profile's.

--add-new also accepts drift in a unit onboarding made, whose tables may change
when an update to it lands: the update's changed source item was accepted and
certified before it landed (kge/estate/onboard/), so that drift is the reviewed
one. Any other source's drift is still refused.

Names no client: the client is the deployment's.
"""

from __future__ import annotations


import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from kge.pipeline.settings import ROOT
from kge.compiler import pack as packs
from kge.compiler.pack import LOCK, PACK
from kge.landing.zone import LandingError, Zone
from kge.pipeline import warehouse_versions
from kge.pipeline.profile import ProfileError, resolve, shown
from kge.ports import WarehouseRefused

DEMO = ROOT
FORMAT = packs.LOCK_FORMAT


class LockError(RuntimeError):
    """A pack the lock cannot be taken for, or a lock that cannot be read."""


def harvested(clients: Path, client: str) -> list[str]:
    """The ids of the sources client's pack harvests, then of its curated sources, in the pack's order. Refused if the
    pack is not client's."""
    path = Path(clients) / client / PACK
    read = packs.read(Path(clients), client, lock=False)
    if read.findings:
        raise LockError(f"{path} cannot be read: {read.findings[0]}")
    pack = read.data
    manifest = pack.get("manifest") if isinstance(pack, dict) else None
    declared = manifest.get("client") if isinstance(manifest, dict) else None
    if declared != client:
        raise LockError(f"{path} declares client {declared!r}, not {client!r}: a pack is its own client's")
    out = []
    for section in ("sources", "curated_sources"):
        sources = pack.get(section) or []
        if not isinstance(sources, list) or not all(isinstance(s, dict) and isinstance(s.get("id"), str) for s in sources):
            raise LockError(f"{path}: {section} is a list, each with an id")
        out += [s["id"] for s in sources if s.get("harvest") is True]
    return out


def freeze(release, sources: list[str], estate=None, versions: dict[str, int] | None = None) -> dict:
    """The lock for sources, from the catalogues release landed for them; with estate, each table's rows and storage
    from its warehouse, an onboarded unit's the version versions names (its draft's live one, as certify catalogues it)."""
    frozen, versions = {}, versions or {}
    for s in sources:
        if not release.has(f"structured/{s}.json"):
            raise LockError(f"{release} landed no catalogue for {s}, a source the pack harvests. make certify lands one "
                            "for each warehouse it reads: run it, or take the source out of the pack")
        tables = []
        session = estate.open(s, versions.get(s)) if estate is not None else None
        for t in sorted(release.structured(s)["tables"], key=lambda t: t["name"]):
            facts = session.describe(t["name"]) if session is not None else {}
            tables.append({
                "name": t["name"],
                **({"rows": facts["rows"], "storage": session.storage(t["name"])} if session is not None else {}),
                "columns": [{"name": c["name"], "type": c["type"], "description": c["comment"], "ordinal": c["ordinal"]}
                            for c in sorted(t["columns"], key=lambda c: c["ordinal"])],
                "primary_key": list(t["primary_key"]),
                "foreign_keys": [{"columns": list(k["columns"]), "ref_table": k["ref_table"], "ref_columns": list(k["ref_columns"])}
                                 for k in t["foreign_keys"]],
            })
        if session is not None:
            session.close()
        frozen[s] = {"tables": tables}
    return {"client": release.client, "format": FORMAT, "as_of": release.manifest.get("as_of"), "sources": frozen}


def render(lock: dict) -> str:
    """Canonical JSON: sorted keys, one space of indent, a trailing newline."""
    return json.dumps(lock, indent=1, sort_keys=True) + "\n"


def read(path: Path) -> dict:
    """A lock as written, or LockError if it is missing or not a lock."""
    try:
        lock = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise LockError(f"there is no {path} yet") from None
    except (OSError, ValueError) as exc:
        raise LockError(f"{path} cannot be read ({type(exc).__name__})") from None
    if not isinstance(lock, dict) or not isinstance(lock.get("sources"), dict):
        raise LockError(f"{path} is not a lock: it has no sources")
    return lock


def foreign_key(k: dict) -> str:
    return f"({', '.join(k['columns'])}) -> {k['ref_table']} ({', '.join(k['ref_columns'])})"


def drift(locked: dict, landed: dict) -> list[str]:
    """What landed that the lock does not hold, one line per difference: sources, tables, columns, keys."""
    lines = []
    for field in ("client", "format", "as_of"):
        if locked.get(field) != landed[field]:
            lines.append(f"the lock's {field} is {locked.get(field)!r}, not {landed[field]!r}")
    was, now = locked["sources"], landed["sources"]
    for s in sorted(set(was) | set(now)):
        if s not in was:
            lines.append(f"{s}: new source")
            continue
        if s not in now:
            lines.append(f"{s}: no longer a source the pack harvests")
            continue
        old = {t["name"]: t for t in was[s].get("tables", [])}
        new = {t["name"]: t for t in now[s]["tables"]}
        for name in sorted(set(old) | set(new)):
            at = f"{s}.{name}"
            if name not in old:
                lines.append(f"{at}: new table")
                continue
            if name not in new:
                lines.append(f"{at}: table gone")
                continue
            a, b = old[name], new[name]
            for fact in ("rows", "storage"):
                if a.get(fact) != b.get(fact):
                    lines.append(f"{at}: {fact} was {a.get(fact)!r}, now {b.get(fact)!r}")
            ca = {c["name"]: c for c in a.get("columns", [])}
            cb = {c["name"]: c for c in b["columns"]}
            for c in [*(c["name"] for c in b["columns"]), *(c for c in ca if c not in cb)]:
                if c not in ca:
                    lines.append(f"{at}.{c}: new column")
                elif c not in cb:
                    lines.append(f"{at}.{c}: column gone")
                else:
                    lines += [f"{at}.{c}: {f} was {ca[c].get(f)!r}, now {cb[c][f]!r}"
                              for f in ("type", "description", "ordinal") if ca[c].get(f) != cb[c][f]]
            if a.get("primary_key") != b["primary_key"]:
                lines.append(f"{at}: primary key was ({', '.join(a.get('primary_key') or [])}), now ({', '.join(b['primary_key'])})")
            ka = [foreign_key(k) for k in a.get("foreign_keys", [])]
            kb = [foreign_key(k) for k in b["foreign_keys"]]
            lines += [f"{at}: new foreign key {k}" for k in kb if k not in ka]
            lines += [f"{at}: foreign key {k} gone" for k in ka if k not in kb]
    return lines


def write(path: Path, text: str) -> bool:
    """path holds text, swapped in whole. False if it held it already: nothing is written."""
    path = Path(path)
    if path.is_file() and path.read_text() == text:
        return False
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose pack and landing: the profile's client, clients and landing (default: PROFILE, else "
                                      "the registry's default)")
    ap.add_argument("--client", help="whose pack to lock, instead of the profile's client")
    ap.add_argument("--clients", help="the folder holding each client's pack, instead of the profile's")
    ap.add_argument("--landing", help="the landing zone, instead of the profile's")
    ap.add_argument("--warehouses", help="the estate's warehouse folder, instead of the profile's")
    ap.add_argument("--release", default="current", help="the landed release to freeze: its id, or current (the default)")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 2 if the lock does not hold what landed")
    ap.add_argument("--add-new", action="store_true", help="add only harvested sources the lock does not hold, and the tables of "
                    "units onboarding made (an update to one changes them); refuse any other drift")
    args = ap.parse_args(argv)
    if None in (args.client, args.clients, args.landing):
        try:
            prof = resolve(args.profile)
        except ProfileError as exc:
            print(f"lock: {exc}. Nothing was {'checked' if args.check else 'locked'}.", file=sys.stderr)
            return 2
        args.client = prof.client if args.client is None else args.client
        args.clients = prof.clients if args.clients is None else args.clients
        args.landing = prof.landing if args.landing is None else args.landing
        args.warehouses = prof.warehouses if args.warehouses is None else args.warehouses
    elif args.warehouses is None:
        args.warehouses = Path(args.landing).parent / "warehouses"
    try:
        zone = Zone(Path(args.landing), args.client)
        sources = harvested(Path(args.clients), args.client)
        release = zone.open(args.release)
        from kge.adapters.warehouse import open_estate
        frozen = freeze(release, sources, open_estate(Path(args.warehouses), args.client),
                        warehouse_versions.live_versions(Path(args.clients) / args.client))
    except (LandingError, LockError, WarehouseRefused) as exc:
        hint = ""
        if isinstance(exc, LandingError) and "make certify" not in str(exc):
            hint = ". make certify (kge/estate/certify.py) lands the estate again"
        print(f"lock: {exc}{hint}. Nothing was {'checked' if args.check else 'locked'}.", file=sys.stderr)
        return 2
    path = Path(args.clients) / args.client / LOCK
    text = render(frozen)
    if args.add_new:
        try:
            old = read(path)
            lines = drift(old, frozen)
        except (OSError, LockError, KeyError, TypeError, AttributeError) as exc:
            print(f"lock: cannot add new sources: {exc}. Nothing was written.", file=sys.stderr)
            return 2
        allowed = {f"{s}: new source" for s in set(frozen["sources"]) - set(old["sources"])}
        onboarded = {s for s in frozen["sources"] if (Path(args.clients) / args.client / "units" / s / "schema.sql").is_file()}
        if any(line not in allowed and not any(line.startswith((f"{s}.", f"{s}:")) for s in onboarded) for line in lines):
            print("lock: pack.lock is stale; --add-new changes only new harvested sources: " + "; ".join(lines), file=sys.stderr)
            return 2
        written = write(path, text)
        print(json.dumps({"lock": shown(path), "written": written, "added": sorted(set(frozen["sources"]) - set(old["sources"]))}, indent=2))
        return 0
    if args.check:
        try:
            current = path.is_file() and path.read_text() == text
            lines = [] if current else drift(read(path), frozen)
        except (OSError, LockError) as exc:
            current, lines = False, [str(exc)]
        except (KeyError, TypeError, AttributeError):
            current, lines = False, []
        if current:
            print(f"lock: {shown(path)} holds the catalogue release {release.id} landed")
            return 0
        print(f"lock: {shown(path)} is stale: run make lock. Release {release.id} of {args.client} landed a catalogue it does not hold:",
              file=sys.stderr)
        for line in lines or ["the file is not as make lock writes it"]:
            print(f"  {line}", file=sys.stderr)
        return 2
    written = write(path, text)
    tables = [t for s in frozen["sources"].values() for t in s["tables"]]
    print(json.dumps({"lock": shown(path), "client": args.client, "release": release.id, "written": written,
                      "sources": len(frozen["sources"]), "tables": len(tables), "columns": sum(len(t["columns"]) for t in tables),
                      "foreign_keys": sum(len(t["foreign_keys"]) for t in tables)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
