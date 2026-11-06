"""The question sets, each a file in the build, build/<name>.json, read the same way wherever a set is read.

A set's first version is a list of items (id, kind, question, expect), as the
held-out set's v1 is. A later version is an object that names its version and
declares what changed from the one before it, so a run on it can still be put
beside a baseline recorded on that one:

    {"version": "v2",
     "changes_from": {"version": "v1", "items": ["<id>", ...], "sha256": "<v1's file>", "why": "..."},
     "items": [...]}

changes_from names the version it changed from, the items whose expectations
changed (each still in the set), optional removed ids (each absent), and that
version's file by its sha256, so only a
baseline that ran that very file is put beside it, and why. Every other item is
that version's, unchanged. kge/eval/report.py and kge/eval/margin.py read it from the
run's manifest, where the harness records it. The golden set is golden.json,
the held-out set, which no one sees, heldout.json, and a further set is
<name>_v<n>.json, as kge/eval/harness.py runs it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

VERSION = re.compile(r"^v(\d+)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GOLDEN, HELD_OUT = "golden", "heldout"
FURTHER = re.compile(r"^([a-z][a-z0-9_]*)_v\d+$")


def is_heldout(name: str) -> bool:
    """Whether a set name or stem is held out: heldout or heldout_<name>."""
    return name == HELD_OUT or name.startswith(f"{HELD_OUT}_")


def read(path: Path) -> dict:
    """{"version", "changes_from", "items"}: a list is its set's first version, v1, declaring no change. Anything else
    is a ValueError naming the file and what is wrong with it."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise ValueError(f"{path}: not JSON: {exc}") from None
    if isinstance(data, list):
        return {"version": "v1", "changes_from": None, "items": checked_items(path, data)}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a set is a list of items, or an object naming its version and its items")
    if not isinstance(data.get("items"), list):
        raise ValueError(f"{path}: a set names its items, a list")
    items = checked_items(path, data["items"])
    if "catalogue" in data:
        if not isinstance(data["catalogue"], list):
            raise ValueError(f"{path}: catalogue items are a list")
        checked_items(path, data["catalogue"])
    version = data.get("version")
    if not isinstance(version, str) or not VERSION.match(version):
        raise ValueError(f"{path}: a set names its version, v<n>, not {version!r}")
    change = data.get("changes_from")
    if change is not None:
        change = checked_change(path, change, version, {i["id"] for i in items})
    return {"version": version, "changes_from": change, "items": items}


def declared(pack: Path) -> dict[str, str]:
    """The sets a client's pack says it is tested on (tests.sets), each name with its version: what a run on the profile
    takes and the version it expects of each. None named, or no pack to read, is {}: the harness's own then (SETS)."""
    import yaml
    try:
        raw = yaml.safe_load(Path(pack).read_text())
    except (OSError, yaml.YAMLError):
        return {}
    named = ((raw.get("tests") or {}).get("sets") or []) if isinstance(raw, dict) and isinstance(raw.get("tests"), dict) else []
    return {s["name"]: s["version"] for s in named
            if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("version"), str)}


def items(path: Path) -> list[dict]:
    """A set's items, whichever version its file is."""
    return read(path)["items"]


def declared_files(pack: Path) -> list[str] | None:
    """The declared set stems, including each live unit's sets; None for an older pack."""
    from kge.compiler.inputs import declared_findings, read_declared

    folder = Path(pack) if Path(pack).is_dir() else Path(pack).parent
    findings = declared_findings(folder)
    if findings:
        raise ValueError("; ".join(str(f) for f in findings))
    declared = read_declared(folder)
    if declared is None:
        return None
    names = list(declared.sets)
    for unit in sorted((folder / "units").glob("*")):
        if unit.is_dir() and not unit.name.startswith("."):
            names.extend(f"{prefix}_{unit.name}_v1" for prefix in ("golden", "team", "heldout"))
    return list(dict.fromkeys(names))


def item_hashes(items: list[dict]) -> dict[str, str]:
    """Each item's content, so a later declaration can be checked against what ran."""
    return {i["id"]: hashlib.sha256(json.dumps(i, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for i in items}


def undeclared_change(path: Path, baseline_set: dict | str | None) -> str | None:
    """Why a changed file does not declare its changes from the baseline's exact file."""
    if not isinstance(baseline_set, dict):
        return None
    try:
        sha = sha256(path)
        if sha == baseline_set.get("sha256"):
            return None
        data = read(path)
    except (OSError, ValueError) as exc:
        return str(exc)
    change = data["changes_from"]
    if change is None:
        return f"{path}: changed since the baseline without changes_from"
    if change["version"] != baseline_set.get("version") or change["sha256"] != baseline_set.get("sha256"):
        return f"{path}: changes_from must name the baseline's version and sha256"
    before = baseline_set.get("item_sha256")
    if isinstance(before, dict):
        now = item_hashes(data["items"])
        changed = {i for i in now if now[i] != before.get(i)}
        removed = set(before) - set(now)
        if changed != set(change["items"]) or removed != set(change.get("removed", [])):
            return f"{path}: changes_from must name exactly the changed items {sorted(changed)} and removed items {sorted(removed)}"
    return None


def checked_items(path: Path, items: list) -> list[dict]:
    if not all(isinstance(i, dict) and isinstance(i.get("id"), str) for i in items):
        raise ValueError(f"{path}: the items are objects, each with its id")
    if len({i["id"] for i in items}) != len(items):
        raise ValueError(f"{path}: each item has a distinct id")
    return items


def checked_change(path: Path, c, version: str, ids: set[str]) -> dict:
    """changes_from as the set declares it: an earlier version, the changed items (each in the set), that version's
    file's sha256, why, and optional removed ids, each no longer in the set."""
    if not isinstance(c, dict):
        raise ValueError(f"{path}: changes_from is an object: version, items, sha256 and why")
    was = c.get("version")
    if not isinstance(was, str) or not VERSION.match(was) or int(was[1:]) >= int(version[1:]):
        raise ValueError(f"{path}: changes_from names the earlier version it changed from, not {was!r} (the set is {version})")
    named = c.get("items")
    removed = c.get("removed", [])
    if not isinstance(named, list) or not all(isinstance(i, str) for i in named):
        raise ValueError(f"{path}: changes_from names the items that changed, a list of ids")
    if not isinstance(removed, list) or not all(isinstance(i, str) for i in removed):
        raise ValueError(f"{path}: changes_from removed is a list of ids")
    if not named and not removed:
        raise ValueError(f"{path}: changes_from names the items that changed or were removed")
    if len(set(named)) != len(named) or len(set(removed)) != len(removed):
        raise ValueError(f"{path}: changes_from lists each id once")
    if set(removed) & ids:
        raise ValueError(f"{path}: changes_from removed names items still in the set: {', '.join(sorted(set(removed) & ids))}")
    gone = [i for i in named if i not in ids]
    if gone:
        raise ValueError(f"{path}: changes_from names {', '.join(gone)}, which the set does not hold")
    if not isinstance(c.get("sha256"), str) or not SHA256.match(c["sha256"]):
        raise ValueError(f"{path}: changes_from names the file it changed from by its sha256, 64 hex characters")
    if not isinstance(c.get("why"), str) or not c["why"].strip():
        raise ValueError(f"{path}: changes_from says why")
    return {"version": was, "items": list(named), "sha256": c["sha256"], "why": c["why"],
            **({"removed": list(removed)} if "removed" in c else {})}


def golden(estate: Path, baseline: dict | None = None, arm: str | None = None, profile: str | None = None,
           units: list[str] | None = None) -> list[dict]:
    """The golden sets the estate holds, for the page's sample questions: the golden set, then each further set, by
    name, each {name, version, questions, samples, misses}: its distinct questions, in its order, as text alone, never
    an expectation, a figure or SQL. The held-out set is no golden set, and is never among them: it is held out. samples
    are the questions the page offers first: those the profile's A/B baseline answered as expected on arm, the page's
    default (every item that asks one as the run's principal passed, on every repeat), where the baseline is the
    profile's and ran that very file; else, or where it answered none, every question. misses are the questions the
    page marks as known misses: those the same baseline records an item of failing on arm; none without one."""
    estate = Path(estate)
    paths = [estate / f"{GOLDEN}.json"] if (estate / f"{GOLDEN}.json").is_file() else []
    paths += sorted(p for p in estate.glob("*.json") if p.is_file() and FURTHER.match(p.stem) and not is_heldout(FURTHER.match(p.stem).group(1)))
    out = []
    for path in paths:
        data = read(path)
        if units is not None and any((i.get("expect") or {}).get("unit") not in units for i in data["items"]
                                     if isinstance(i.get("expect"), dict) and (i.get("expect") or {}).get("unit")):
            continue
        m = FURTHER.match(path.stem)
        questions = list(dict.fromkeys(i["question"] for i in data["items"] if isinstance(i.get("question"), str)))
        asked = recorded(path, data["items"], baseline, arm, profile)
        out.append({"name": m.group(1) if m else path.stem, "version": data["version"], "questions": questions,
                    "samples": [q for q, ok in asked.items() if all(ok)] or questions,
                    "misses": [q for q, ok in asked.items() if False in ok]})
    return out


def recorded(path: Path, items: list[dict], baseline: dict | None, arm: str | None, profile: str | None) -> dict[str, list]:
    """The baseline's record of each question of a set's file on arm: for each item that asks it as the run's principal
    (an item asked as another principal is not the page's to ask), True if it passed on every repeat, False if the record
    says it did not, None if it holds none. Empty if the baseline is another profile's or did not run that very file."""
    if not baseline or not arm or baseline.get("profile") != profile:
        return {}
    sha = sha256(path)
    ran = next((k for k, s in (baseline.get("sets") or {}).items() if s.get("file") == Path(path).name and s.get("sha256") == sha), None)
    if ran is None:
        return {}
    passes = (baseline.get("items") or {}).get(ran) or {}
    asked: dict[str, list] = {}
    for i in items:
        if isinstance(i.get("question"), str) and not i.get("principal"):
            got = (passes.get(i["id"]) or {}).get(arm)
            if isinstance(got, dict):
                got = got.get("pass_rate")
            asked.setdefault(i["question"], []).append(None if got is None else got == 1)
    return asked


def catalogue(path: Path) -> list[dict]:
    """The catalogue abstention items (C01-C20) a golden set file holds, or [] when absent."""
    path = Path(path)
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, dict) and isinstance(data.get("catalogue"), list):
        return checked_items(path, data["catalogue"])
    return []


def sha256(path: Path) -> str:
    """Hash the core set, stripping catalogue to preserve historical baseline comparisons.

    issue-38.json, issue-39.json and issue-40.json predate catalogue questions;
    those extra items must not invalidate their unchanged core hashes.
    """
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw)
    except ValueError:
        return hashlib.sha256(raw).hexdigest()
    if isinstance(data, dict) and "catalogue" in data:
        core = {k: v for k, v in data.items() if k != "catalogue"}
        return hashlib.sha256(json.dumps(core, indent=2, default=str).encode()).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def catalogue_sha256(path: Path) -> str | None:
    """Hash the canonical catalogue list independently of the historical core, or None when absent."""
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or "catalogue" not in data:
        return None
    items = checked_items(path, data["catalogue"])
    return hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
