"""The versioned warehouses of onboarded units (issue #57): one file per unit version, named by the artifact that reads it.

A unit the client's estate writes keeps its one warehouse, <warehouses>/<unit>.duckdb. A unit onboarding makes keeps one
warehouse per version:

  <warehouses>/onboarded/<unit>/v<N>/<unit>.duckdb
  <warehouses>/onboarded/<unit>/v<N>/inputs.sha256      the digest of the inputs its rows were made from

The file's stem stays the unit's id, as every warehouse's is (certify's catalogue and DuckDB's catalogue name take it
from there), and the version is the folder, out of the way of every reader that globs <warehouses>/*.duckdb.

Landing a unit's version N+1 writes a new file and leaves version N's as it was. The unit's draft.yaml records the
version it was drafted or landed with and the digest of its inputs (its top-level `warehouse: {version, inputs}`),
kge/compiler/pack.py carries it into the pack, and kge/compiler/emit.py writes it into the artifact's drafts.json as
{"warehouses": {<unit>: {"version": N, "inputs": <digest>}}}. The runtime opens the version the artifact it serves names
(kge/services/ask/engine/core.py Engine): a candidate is checked against N+1 while the released graph reads N, and a rollback to an older
hash reads that hash's version again. A named version whose file is not on disk, or whose rows were made from other
inputs than the artifact was checked on (a unit reset and onboarded again numbers its versions from 1 again), is refused,
naming it, never served from another version's rows. An artifact that names no version for a unit (one compiled before
warehouses were versioned) reads the unit's one warehouse, <warehouses>/<unit>.duckdb, as it did then.

Retention: a unit keeps the versions any released artifact of the client names (its RELEASED marker, or the current
pointer) and its live version, the pending candidate's; landing a new version removes the rest (prune).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

FOLDER = "onboarded"
DRAFTS = "drafts.json"
SIDECAR = "inputs.sha256"
HASH = re.compile(r"[0-9a-f]{64}")


class MissingWarehouse(FileNotFoundError):
    """An artifact names a unit's warehouse version that is not on disk, or not the one it was checked on."""


def digest(inputs: dict, rows: dict) -> str:
    """The digest of what a version's rows are made from: its manifest's inputs (each file's sha256) and its row source."""
    return hashlib.sha256(json.dumps({"inputs": inputs, "rows": rows}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def unit_folder(warehouses: Path, unit: str) -> Path:
    return Path(warehouses) / FOLDER / unit


def path(warehouses: Path, unit: str, version: int) -> Path:
    """The warehouse of version of an onboarded unit."""
    return unit_folder(warehouses, unit) / f"v{int(version)}" / f"{unit}.duckdb"


def stamped(warehouses: Path, unit: str, version: int) -> str | None:
    """The inputs digest a version's warehouse was made from; None if it has none."""
    try:
        return (path(warehouses, unit, version).parent / SIDECAR).read_text().strip() or None
    except OSError:
        return None


def install(part: Path, warehouses: Path, unit: str, version: int, inputs: str, keep=frozenset()) -> Path:
    """A built warehouse (part) put in place as the unit's version, with the digest of its inputs. The old digest goes
    first, so a stop part-way leaves a file with no digest, which ensure_warehouse rebuilds and the runtime refuses.
    keep: the versions a release still names (kept); one of them on disk is never replaced by rows made from other
    inputs (FileExistsError, part left as it was), so a hash released on it can still be rolled back to."""
    target = path(warehouses, unit, version)
    if int(version) in keep and target.is_file() and stamped(warehouses, unit, version) != inputs:
        raise FileExistsError(f"{unit}'s warehouse v{int(version)} is kept for a release and was made from other inputs: "
                              "it is not replaced")
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / SIDECAR).unlink(missing_ok=True)
    os.replace(part, target)
    (target.parent / SIDECAR).write_text(inputs + "\n")
    return target


def versions(warehouses: Path, unit: str) -> list[int]:
    """The versions of an onboarded unit with a warehouse on disk, oldest first."""
    folder = unit_folder(warehouses, unit)
    found = []
    for child in folder.glob("v*") if folder.is_dir() else []:
        if re.fullmatch(r"v[1-9][0-9]*", child.name) and (child / f"{unit}.duckdb").is_file():
            found.append(int(child.name[1:]))
    return sorted(found)


def held(warehouses: Path) -> dict[str, list[int]]:
    """Every onboarded unit's versions on disk, by unit."""
    root = Path(warehouses) / FOLDER
    out = {}
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        found = versions(warehouses, child.name) if child.is_dir() else []
        if found:
            out[child.name] = found
    return out


def any_held(warehouses: Path) -> bool:
    """Whether the folder holds any warehouse, established or onboarded."""
    return any(Path(warehouses).glob("*.duckdb")) or bool(held(warehouses))


def entry(value) -> tuple[int, str | None] | None:
    """(version, inputs digest) of a draft's or an artifact's warehouse entry: {version, inputs}, or a bare version;
    None if it is neither."""
    if isinstance(value, dict):
        version, inputs = value.get("version"), value.get("inputs")
        if inputs is not None and not (isinstance(inputs, str) and HASH.fullmatch(inputs)):
            return None
    else:
        version, inputs = value, None
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None
    return version, inputs


def read(artifact: Path) -> dict[str, tuple[int, str | None]]:
    """{unit: (version, inputs digest)} the artifact's drafts.json names; empty when it names none (a client with no
    onboarded unit, or an artifact compiled before warehouses were versioned). ValueError for one it names malformed."""
    try:
        data = json.loads((Path(artifact) / DRAFTS).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"{Path(artifact).name}/{DRAFTS} cannot be read: {exc}") from None
    found = data.get("warehouses") if isinstance(data, dict) else None
    if found is None:
        return {}
    out = {u: entry(v) for u, v in found.items()} if isinstance(found, dict) else None
    if out is None or not all(isinstance(u, str) and v is not None for u, v in out.items()):
        raise ValueError(f"{Path(artifact).name}/{DRAFTS}: warehouses is {{unit: {{version, inputs}}}}, each version a "
                         "whole number from 1")
    return out


def named(artifact: Path) -> dict[str, int]:
    """{unit: version} the artifact's drafts.json names (read)."""
    return {u: v for u, (v, _) in read(artifact).items()}


def inputs_named(artifact: Path) -> dict[str, str]:
    """{unit: inputs digest} the artifact's drafts.json names (read), for the units it names one for."""
    return {u: d for u, (_, d) in read(artifact).items() if d}


def paths(warehouses: Path, units: list[str], versions_: dict[str, int], model_hash: str | None = None,
          inputs: dict[str, str] | None = None) -> dict[str, Path]:
    """Each unit's warehouse: the version the artifact names, else the unit's one warehouse. MissingWarehouse naming each
    named version that is not on disk, or whose rows were made from other inputs than inputs names for it; a named
    version is never read from another."""
    warehouses, inputs = Path(warehouses), inputs or {}
    out = {u: path(warehouses, u, versions_[u]) if u in versions_ else warehouses / f"{u}.duckdb" for u in units}
    whose = f"the artifact {model_hash[:12]}" if model_hash else "the artifact"
    gone = [u for u in units if u in versions_ and not out[u].is_file()]
    if gone:
        raise MissingWarehouse(
            f"{whose} reads " + ", ".join(f"{u}'s warehouse v{versions_[u]} ({FOLDER}/{u}/v{versions_[u]}/{u}.duckdb)"
                                          for u in gone) +
            f", not on disk in {warehouses}: a unit's version is never read from another one. Release a hash whose "
            "warehouses are kept, or make onboard to build the live version again")
    other = [u for u in units if u in versions_ and u in inputs and stamped(warehouses, u, versions_[u]) != inputs[u]]
    if other:
        raise MissingWarehouse(
            f"{whose} reads " + ", ".join(f"{u}'s warehouse v{versions_[u]}" for u in other) +
            f" in {warehouses}, which holds rows made from other inputs than the artifact was checked on (the unit was "
            "reset and onboarded again, or the file was replaced): a unit's version is never read from another one's rows")
    return out


def live_versions(pack: Path) -> dict[str, int]:
    """The version each onboarded unit's draft (<pack>/units/<unit>/draft.yaml, in the client's pack folder) names: what
    the pack compiles against, and so what certify catalogues for the lock. A unit whose draft names none is left out
    (its one warehouse is the established one)."""
    import yaml
    out = {}
    root = Path(pack) / "units"
    for draft in sorted(root.glob("*/draft.yaml")) if root.is_dir() else []:
        try:
            record = yaml.safe_load(draft.read_text())
        except (OSError, yaml.YAMLError):
            continue
        found = entry(record.get("warehouse")) if isinstance(record, dict) and record.get("warehouse") is not None else None
        if found:
            out[draft.parent.name] = found[0]
    return out


def _released(store_folder: Path) -> bool:
    try:
        marker = json.loads((store_folder / "RELEASED").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(marker, dict) and any(isinstance(v, dict) for v in marker.values())


def kept(prof, unit: str, live_version: int | None) -> set[int]:
    """The versions of unit to keep: its live version (the pending candidate's; None for a unit no longer onboarded) and
    every version an artifact of the client names that was released (its graph's RELEASED marker on any store) or that
    current names, where something has been released."""
    keep = {int(live_version)} if live_version is not None else set()
    home = prof.artifacts / prof.client
    try:
        current = prof.model_hash()
    except Exception:   # noqa: BLE001
        current = None
    for folder in sorted(home.iterdir()) if home.is_dir() else []:
        if not HASH.fullmatch(folder.name) or not folder.is_dir():
            continue
        try:
            version = named(folder).get(unit)
        except ValueError:
            continue
        if version is None:
            continue
        if folder.name == current or _released(prof.store / prof.client / folder.name):
            keep.add(version)
    return keep


def prune(prof, unit: str, live_version: int | None) -> list[int]:
    """Remove the unit's versions no release names and that are not its live one (None: a unit no longer onboarded,
    whose warehouses go too once nothing is kept); the versions removed. On whichever backend holds them: the estate's
    warehouse port (kge/adapters/warehouse/ estate_of)."""
    from kge.adapters.warehouse import estate_of
    estate = estate_of(prof)
    keep = kept(prof, unit, live_version)
    removed = []
    for version in estate.versions(unit):
        if version not in keep:
            estate.drop(unit, version)
            removed.append(version)
    if live_version is None and not estate.versions(unit):
        estate.remove(unit)
    return removed


def highest(warehouses: Path, unit: str) -> int:
    """The highest version of unit on disk (0 if none): a new version is numbered above it, so it never takes the number
    of one a released hash still reads (a rollback restores an older manifest, and keeps every released version's file).
    A version a release names that is not on disk is left out: reset removed it, and the unit onboarded again numbers
    from 1 again, its rows refused to that release by their inputs digest (paths)."""
    return max([0, *versions(warehouses, unit)])


def remove(warehouses: Path, unit: str) -> None:
    """Every version of the unit, and the onboarded folder if it is left empty."""
    shutil.rmtree(unit_folder(warehouses, unit), ignore_errors=True)
    root = Path(warehouses) / FOLDER
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()
