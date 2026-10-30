"""The question sets, each a file in the build, build/<name>.json, read the same way wherever a set is read.

A set's first version is a list of items (id, kind, question, expect), as the
held-out set's v1 is. A later version is an object that names its version and
declares what changed from the one before it, so a run on it can still be put
beside a baseline recorded on that one:

    {"version": "v2",
     "changes_from": {"version": "v1", "items": ["<id>", ...], "sha256": "<v1's file>", "why": "..."},
     "items": [...]}

changes_from names the version it changed from, the items whose expectations
changed (each still in the set), that version's file by its sha256, so only a
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


def checked_items(path: Path, items: list) -> list[dict]:
    if not all(isinstance(i, dict) and isinstance(i.get("id"), str) for i in items):
        raise ValueError(f"{path}: the items are objects, each with its id")
    return items


def checked_change(path: Path, c, version: str, ids: set[str]) -> dict:
    """changes_from as the set declares it: an earlier version, the changed items (each in the set), that version's
    file's sha256 and why."""
    if not isinstance(c, dict):
        raise ValueError(f"{path}: changes_from is an object: version, items, sha256 and why")
    was = c.get("version")
    if not isinstance(was, str) or not VERSION.match(was) or int(was[1:]) >= int(version[1:]):
        raise ValueError(f"{path}: changes_from names the earlier version it changed from, not {was!r} (the set is {version})")
    named = c.get("items")
    if not isinstance(named, list) or not named or not all(isinstance(i, str) for i in named):
        raise ValueError(f"{path}: changes_from names the items that changed, a list of ids")
    gone = [i for i in named if i not in ids]
    if gone:
        raise ValueError(f"{path}: changes_from names {', '.join(gone)}, which the set does not hold")
    if not isinstance(c.get("sha256"), str) or not SHA256.match(c["sha256"]):
        raise ValueError(f"{path}: changes_from names the file it changed from by its sha256, 64 hex characters")
    if not isinstance(c.get("why"), str) or not c["why"].strip():
        raise ValueError(f"{path}: changes_from says why")
    return {"version": was, "items": list(named), "sha256": c["sha256"], "why": c["why"]}


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
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ran = next((k for k, s in (baseline.get("sets") or {}).items() if s.get("file") == Path(path).name and s.get("sha256") == sha), None)
    if ran is None:
        return {}
    passes = (baseline.get("items") or {}).get(ran) or {}
    asked: dict[str, list] = {}
    for i in items:
        if isinstance(i.get("question"), str) and not i.get("principal"):
            got = (passes.get(i["id"]) or {}).get(arm)
            asked.setdefault(i["question"], []).append(None if got is None else got == 1)
    return asked
