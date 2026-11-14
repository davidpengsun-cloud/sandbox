"""The inputs a pack declares, and the files that are outside that declaration.

Declarations describe where the pack's inputs come from, outside its model hash.
A pack without them keeps the earlier file-discovery rules. Reading a malformed
declaration returns no declaration; callers refuse its I00 findings before work.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml

from kge.compiler.pack import Finding, INCLUDE, PACK, load_yaml

INPUTS = "inputs"
STEM = re.compile(r"[a-z][a-z0-9_]*")
FURTHER = re.compile(r"[a-z][a-z0-9_]*_v\d+")
OWNED = {"baselines", "versions", "proposals", "units", "onboard"}


@dataclass(frozen=True)
class Declared:
    fixture: str | None
    generator: str | None
    catalogue: str | None
    sets: tuple[str, ...]
    includes: tuple[str, ...]


@lru_cache(maxsize=1)
def _manifest_text(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    manifest = data.get("manifest") if isinstance(data, dict) else None
    return manifest if isinstance(manifest, dict) else {}


def _manifest(pack_folder: Path) -> dict:
    try:
        return _manifest_text((Path(pack_folder) / PACK).read_text())
    except (OSError, UnicodeDecodeError):
        return {}


def _inside(folder: Path, name: object) -> bool:
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        return False
    try:
        return Path(folder).resolve() in (Path(folder) / name).resolve().parents
    except (OSError, ValueError, RuntimeError):
        return False


def _findings(pack_folder: Path, manifest: dict) -> list[Finding]:
    if INPUTS not in manifest:
        return []
    value = manifest[INPUTS]
    at = f"{PACK}:manifest.{INPUTS}"
    if not isinstance(value, dict):
        return [Finding("I00", at, "is a mapping of estate, catalogue and sets")]
    out = [Finding("I00", f"{at}.{key}", "is not an input declaration")
           for key in value if key not in {"estate", "catalogue", "sets"}]
    estate = value.get("estate")
    if not isinstance(estate, dict) or set(estate) not in ({"fixture"}, {"generator"}):
        out.append(Finding("I00", f"{at}.estate", "names exactly one of fixture or generator"))
    elif "fixture" in estate and not _inside(pack_folder, estate["fixture"]):
        out.append(Finding("I00", f"{at}.estate.fixture", "is a relative folder inside the client's folder, with no .."))
    elif "generator" in estate and estate["generator"] != "sample":
        out.append(Finding("I00", f"{at}.estate.generator", "is sample, the kit's sample generator"))
    catalogue = value.get("catalogue")
    if catalogue is not None and (not _inside(pack_folder, catalogue) or not catalogue.endswith(".yaml")):
        out.append(Finding("I00", f"{at}.catalogue", "is a relative .yaml file inside the client's folder, or null"))
    sets = value.get("sets")
    if not isinstance(sets, list) or not sets:
        out.append(Finding("I00", f"{at}.sets", "is a non-empty list of distinct set file stems"))
    else:
        seen = set()
        for index, name in enumerate(sets):
            if not isinstance(name, str) or not STEM.fullmatch(name):
                out.append(Finding("I00", f"{at}.sets[{index}]", "is a set file stem: a lower-case letter, then letters, digits or underscores"))
            elif name in seen:
                out.append(Finding("I00", f"{at}.sets[{index}]", f"repeats set {name}"))
            else:
                seen.add(name)
    return out


def declared_findings(pack_folder: Path) -> list[Finding]:
    """Every malformed input declaration, without checking whether its files exist."""
    return _findings(pack_folder, _manifest(pack_folder))


def read_declared(pack_folder: Path) -> Declared | None:
    """Read valid declarations; None for an older pack or one with I00 findings.

    Pack reading owns unreadable YAML and malformed includes, as S01 findings.
    Missing declared inputs are the up-front check's, not declaration syntax.
    """
    manifest = _manifest(pack_folder)
    if INPUTS not in manifest or _findings(pack_folder, manifest):
        return None
    value = manifest[INPUTS]
    estate = value["estate"]
    includes = manifest.get("includes")
    names = tuple(name for name in includes if isinstance(name, str) and INCLUDE.fullmatch(name)
                  and _inside(pack_folder, name)) if isinstance(includes, list) else ()
    return Declared(estate.get("fixture"), estate.get("generator"), value.get("catalogue"), tuple(value["sets"]), names)


def declared_files(pack_folder: Path, declared: Declared) -> list[str]:
    """The declared people-owned files outside the fixture, relative to the pack."""
    return list(dict.fromkeys([PACK, *declared.includes, *([declared.catalogue] if declared.catalogue else [])]))


def _set_file(path: Path) -> bool:
    """Recognise standard set names and other JSON files holding question items."""
    if path.stem in {"golden", "heldout"} or FURTHER.fullmatch(path.stem):
        return True
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if isinstance(value, dict) and isinstance(value.get("items"), list) and "version" in value:
        return True
    items = value.get("items") if isinstance(value, dict) else value
    return isinstance(items, list) and bool(items) and all(
        isinstance(item, dict) and "id" in item and "question" in item for item in items)


def ignored(pack_folder: Path, estate: Path, declared: Declared) -> list[str]:
    """Files outside the declaration, excluding uploaded people, state and dotfiles."""
    pack_folder, estate = Path(pack_folder), Path(estate)
    files = set(declared_files(pack_folder, declared))
    out = []
    for path in sorted(pack_folder.rglob("*")):
        relative = path.relative_to(pack_folder)
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        if relative.as_posix() in files or relative.as_posix() in {"pack.lock", "people.yaml"} or relative.parts[0] in OWNED:
            continue
        if declared.fixture and PurePosixPath(relative.as_posix()).is_relative_to(declared.fixture):
            continue
        out.append(f"clients/{pack_folder.name}/{relative.as_posix()}")
    sets = set(declared.sets)
    units = pack_folder / "units"
    if units.is_dir():
        sets.update(f"{kind}_{unit.name}_v1" for unit in units.iterdir() if unit.is_dir()
                    for kind in ("golden", "team", "heldout"))
    out.extend(f"estate/{path.name}" for path in sorted(estate.glob("*.json"))
               if path.is_file() and not path.name.startswith(".") and path.stem not in sets and _set_file(path))
    return out


def suggest(pack_folder: Path, estate: Path) -> str:
    """The manifest block that preserves the sets and inputs the earlier rules read."""
    pack_folder, estate = Path(pack_folder), Path(estate)
    data = load_yaml(pack_folder / PACK, PACK, [])
    data = data if isinstance(data, dict) else {}
    manifest = data.get("manifest")
    includes = manifest.get("includes") if isinstance(manifest, dict) else None
    if isinstance(includes, list):
        for name in includes:
            if _inside(pack_folder, name) and INCLUDE.fullmatch(name):
                more = load_yaml(pack_folder / name, name, [])
                if isinstance(more, dict):
                    for key, value in more.items():
                        data.setdefault(key, value)
    tests = data.get("tests")
    entries = tests.get("sets") if isinstance(tests, dict) else None
    named = {item["name"] for item in entries if isinstance(item, dict) and isinstance(item.get("name"), str)
             and isinstance(item.get("version"), str)} if isinstance(entries, list) else set()
    sets = [name for name in ("golden", "heldout") if not named or name in named]
    sets += sorted(path.stem for path in estate.glob("*.json") if path.is_file() and FURTHER.fullmatch(path.stem))
    value = {"estate": {"fixture": "fixture"} if (pack_folder / "fixture/schema.yaml").is_file() else {"generator": "sample"}}
    if (pack_folder / "catalogue.yaml").is_file():
        value["catalogue"] = "catalogue.yaml"
    value["sets"] = sets
    return yaml.safe_dump({INPUTS: value}, sort_keys=False)
