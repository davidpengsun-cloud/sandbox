"""The showcase questions a client's pack declares (tests.showcase, issue #620): the questions the portal offers first,
Cerebro's "Try one of these", by id from a set the pack's tests.sets names. GET /golden serves them (the quality
service); no platform or portal code names a question, a period or a category for them."""
from __future__ import annotations

import hashlib

import yaml

from kge import packstore

PACK = "pack.yaml"

MISSING = object()
_parsed: dict[str, dict | None] = {}


def declared(pack: dict) -> dict | None:
    """A pack's showcase as {set, version, items}: the set by its tests.sets name, the version tests.sets names for it,
    and the items by id, in the pack's order. None where the pack declares none, names a set tests.sets does not, or
    holds an entry of another shape, which the compiler refuses (S01, S38)."""
    tests = pack.get("tests") if isinstance(pack, dict) else None
    entry = tests.get("showcase") if isinstance(tests, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("set"), str) or not isinstance(entry.get("items"), list):
        return None
    named = [s.get("version") for s in tests.get("sets") or [] if isinstance(s, dict) and s.get("name") == entry["set"]]
    if not named or not isinstance(named[0], str):
        return None
    return {"set": entry["set"], "version": named[0], "items": [i for i in entry["items"] if isinstance(i, str)]}


def showcase(profile) -> dict | None:
    """The showcase the profile's client's pack declares (declared), read through the pack store on each call, as the
    golden sets it names are read from the estate on each call, so the two are of the same onboarding; the pack is
    parsed once per content. None where there is no pack to read or it is no YAML the compiler would take; a store
    that cannot be read is its StoreError (a 503), never taken for none."""
    try:
        data = packstore.for_profile(profile).read(PACK)
    except FileNotFoundError:
        return None
    key = hashlib.sha256(data).hexdigest()
    held = _parsed.get(key, MISSING)
    if held is not MISSING:
        return held
    try:
        found = declared(yaml.safe_load(data))
    except yaml.YAMLError:
        found = None
    _parsed.clear()
    _parsed[key] = found
    return found
