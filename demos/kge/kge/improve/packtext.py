"""Adds entries to a pack's text without rewriting it, and checks that a candidate pack only adds what the allowlist
allows.

An improvement edit (#452 requirement 4) retains add-only aliases and allows add-only phrases, plus, at the specify
stage, one new shape with its recipe, its shape_tests entry and its worked examples, one stem in manifest.inputs.sets,
one entry in tests.sets and one new set file; it changes nothing else. The writers here place new lines into the
existing YAML text, so every comment, blank line and layout choice of the steward's file survives, and each checks its
own result by parsing it back: the parsed pack must equal the original with exactly the addition made, or the writer
raises Unplaceable. bounded() compares two parsed packs and names every change outside the allowlist.

The module never reads a file: callers hand it the text and write the result.
"""
from __future__ import annotations

import copy
import json
import re

import yaml

ADDITIVE = ("phrases", "shapes", "recipes", "shape_tests", "examples")
APPROVALS = ("approved_by", "approved_on", "reference_sql_approved_by", "reference_sql_approved_on")
FIXED_TOP = ("manifest", "terminology", "tests")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TOP_KEY = re.compile(r"^[A-Za-z0-9_-]+:")
EMPTY_LIST = re.compile(r"\s*\[\s*\](\s*#.*)?\s*")
CHANGED = "changed: an improvement edit only adds"


class Unplaceable(ValueError):
    """The text's layout cannot take the addition without rewriting it."""


def flow(value) -> str:
    """One line of YAML flow form for a value; a bare scalar loses the '...' document end marker."""
    out = yaml.safe_dump(value, default_flow_style=True, sort_keys=False, width=10**9, allow_unicode=True).rstrip("\n")
    if out.endswith("\n..."):
        out = out[: -len("\n...")]
    return out


def _indent(line: str) -> int:
    """The number of leading spaces on a line."""
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> bool:
    """Whether a line carries YAML content: neither blank nor a comment alone."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _block_end(lines: list[str], idx: int) -> int:
    """The index of the first line after the key on line idx that no longer belongs to that key's value."""
    own = _indent(lines[idx])
    for j in range(idx + 1, len(lines)):
        line = lines[j]
        if not _content(line):
            continue
        ind = _indent(line)
        if ind < own or (ind == own and not line.lstrip(" ").startswith("-")):
            return j
    return len(lines)


def _find(lines: list[str], start: int, end: int, key: str) -> int | None:
    """The line of `key:` among the direct children of the block between start and end, or None."""
    level = None
    for j in range(start, end):
        if not _content(lines[j]):
            continue
        if level is None:
            level = _indent(lines[j])
        if _indent(lines[j]) == level and re.match(rf"{re.escape(key)}:(\s|$)", lines[j][level:]):
            return j
    return None


def _rest(line: str) -> str:
    """What follows the key's colon on a `key: value` line, without the line end."""
    return line.rstrip("\r\n").split(":", 1)[1]


def _flow_list(rest: str) -> tuple[list, str] | None:
    """A one-line flow list and its trailing comment (with the spacing before it), or None when rest is not one."""
    if not rest.strip().startswith("["):
        return None
    for p in [i for i, ch in enumerate(rest) if ch == "]"]:
        tail = rest[p + 1:]
        if not re.fullmatch(r"\s*(#.*)?", tail):
            continue
        try:
            value = yaml.safe_load(rest[: p + 1])
        except yaml.YAMLError:
            continue
        if isinstance(value, list):
            return value, tail.rstrip()
    return None


def _place(lines: list[str], idx: int, values: list, where: str) -> list[str]:
    """Append values to the list held by the key on line idx, in whichever one-line or block form it already has."""
    line = lines[idx]
    head = line.rstrip("\r\n").split(":", 1)[0] + ":"
    rest = _rest(line)
    indent = " " * _indent(line)
    new = [flow(v) for v in values]
    if EMPTY_LIST.fullmatch(rest):
        comment = (EMPTY_LIST.fullmatch(rest).group(1) or "").rstrip()
        block = [f"{head}{comment}\n"] + [f"{indent}  - {n}\n" for n in new]
        return lines[:idx] + block + lines[idx + 1:]
    found = _flow_list(rest)
    if found is not None:
        existing, comment = found
        return lines[:idx] + [f"{head} {flow(existing + values)}{comment}\n"] + lines[idx + 1:]
    if rest.strip() and not rest.strip().startswith("#"):
        raise Unplaceable(f"{where} is neither a block list nor a one-line flow list: adding to it would rewrite it")
    return _place_block(lines, idx, new, where)


def _place_block(lines: list[str], idx: int, new: list[str], where: str) -> list[str]:
    """Insert item lines after the last content line of a block list, at the indent of its items."""
    end = _block_end(lines, idx)
    items = [j for j in range(idx + 1, end) if _content(lines[j]) and lines[j].lstrip(" ").startswith("- ")]
    if not items:
        raise Unplaceable(f"{where} holds no block list items to add after")
    pad = " " * _indent(lines[items[0]])
    last = max(j for j in range(idx + 1, end) if _content(lines[j]))
    head = lines[: last + 1]
    if not head[-1].endswith("\n"):
        head[-1] += "\n"
    return head + [f"{pad}- {n}\n" for n in new] + lines[last + 1:]


def _load(text: str, where: str):
    """Parse YAML text, or raise Unplaceable naming where the result was meant to change."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise Unplaceable(f"{where}: the edited text no longer parses ({exc})") from exc


def _check(out: str, expected, where: str) -> str:
    """Return out when it parses to expected, else raise Unplaceable naming where."""
    if _load(out, where) != expected:
        raise Unplaceable(f"{where}: the edited text does not parse to the original with only the addition made")
    return out


def append_entries(text: str, section: str, entries: list[dict]) -> str:
    """Append entries to one of the ADDITIVE top-level lists, creating the section at the end when it is absent."""
    if section not in ADDITIVE:
        raise ValueError(f"{section} is not a section an improvement edit adds to: {', '.join(ADDITIVE)}")
    expected = copy.deepcopy(_load(text, section)) or {}
    if not isinstance(expected, dict):
        raise Unplaceable(f"{section}: the pack is not a mapping")
    held = expected.get(section, [])
    if not isinstance(held, list):
        raise Unplaceable(f"{section} is not a list")
    expected[section] = held + list(entries)
    lines = text.splitlines(keepends=True)
    idx = next((j for j, line in enumerate(lines) if re.match(rf"{re.escape(section)}:(\s|$)", line)), None)
    if idx is None:
        base = text if not text or text.endswith("\n") else text + "\n"
        out = base + f"\n{section}:\n" + "".join(f"  - {flow(e)}\n" for e in entries)
    else:
        out = "".join(_place(lines, idx, list(entries), section))
    return _check(out, expected, section)


def _child_line(lines: list[str], path: tuple[str, ...], missing: str) -> int:
    """The line of the nested key named by path, each a direct child of the one before; Unplaceable when absent or when
    a parent holds a flow mapping."""
    start, end, idx = 0, len(lines), None
    for depth, key in enumerate(path):
        idx = _find(lines, start, end, key)
        if idx is None or (depth == 0 and _indent(lines[idx]) != 0):
            raise Unplaceable(missing)
        rest = _rest(lines[idx]).strip()
        if depth < len(path) - 1 and rest and not rest.startswith("#"):
            raise Unplaceable(f"{'.'.join(path[: depth + 1])} is written in flow form: adding to it would rewrite it")
        start, end = idx + 1, _block_end(lines, idx)
    return idx


def _nested(data, path: tuple[str, ...]):
    """The value at path in parsed data, or None when any step is missing or not a mapping."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _append_nested(text: str, path: tuple[str, ...], value, missing: str) -> str:
    """Append one value to the list at path in the text, checking the parsed result gains exactly that value."""
    where = ".".join(path)
    expected = copy.deepcopy(_load(text, where))
    held = _nested(expected, path)
    if not isinstance(held, list):
        raise Unplaceable(missing)
    held.append(value)
    lines = text.splitlines(keepends=True)
    idx = _child_line(lines, path, missing)
    return _check("".join(_place(lines, idx, [value], where)), expected, where)


def add_input_set(text: str, stem: str) -> str:
    """Add one set stem to manifest.inputs.sets."""
    missing = "the pack declares no manifest.inputs.sets: adding one would change which sets every run reads"
    held = _nested(_load(text, "manifest.inputs.sets"), ("manifest", "inputs", "sets"))
    if isinstance(held, list) and stem in held:
        raise Unplaceable(f"manifest.inputs.sets already lists {stem}")
    return _append_nested(text, ("manifest", "inputs", "sets"), stem, missing)


def append_test_set(text: str, entry: dict) -> str:
    """Add one entry to tests.sets."""
    missing = ("the pack declares no tests.sets: the harness takes only the sets a pack names (kge/eval/sets.py "
               "declared), so naming the first one would change which sets every run takes")
    held = _nested(_load(text, "tests.sets"), ("tests", "sets"))
    if isinstance(held, list) and any(isinstance(s, dict) and s.get("name") == entry.get("name") for s in held):
        raise Unplaceable(f"tests.sets already names {entry.get('name')}")
    return _append_nested(text, ("tests", "sets"), entry, missing)


def _bump(version) -> str | None:
    """The minor bump of a semver string (1.2.3 -> 1.3.0), or None when it is not one."""
    m = SEMVER.match(str(version).strip()) if version is not None else None
    return f"{int(m.group(1))}.{int(m.group(2)) + 1}.0" if m else None


def _show(value) -> str:
    """A short, stable rendering of a value for a violation sentence."""
    return json.dumps(value, sort_keys=True, default=str)


def _added(old, new, where: str, out: list[str]) -> list:
    """The entries new adds after old (absent counts as []); a violation when old is not kept as a prefix."""
    old = [] if old is None else old
    new = [] if new is None else new
    if not isinstance(old, list) or not isinstance(new, list):
        out.append(f"{where} is not a list")
        return []
    if new[: len(old)] != old:
        out.append(f"{where} {CHANGED}")
        return []
    return new[len(old):]


def _mapping(value, where: str, out: list[str]) -> dict:
    """The value when it is a mapping (absent counts as {}); a violation otherwise."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        out.append(f"{where} is not a mapping")
        return {}
    return value


def _same_except(before: dict, after: dict, skip: tuple[str, ...], where: str, out: list[str]) -> None:
    """Every key of two mappings but those in skip is deep-equal."""
    for key in sorted(set(before) | set(after), key=str):
        if key not in skip and before.get(key) != after.get(key):
            out.append(f"{where}{key} {CHANGED}")


def _top(before: dict, after: dict, out: list[str]) -> None:
    """Top-level keys outside manifest, terminology, tests and ADDITIVE are deep-equal; none is removed or added but a
    newly created ADDITIVE section."""
    for key in sorted(set(before) - set(after), key=str):
        out.append(f"{key} removed: an improvement edit only adds")
    for key in sorted(set(after) - set(before) - set(ADDITIVE) - set(FIXED_TOP), key=str):
        out.append(f"{key} added: an improvement edit adds only {', '.join(ADDITIVE)}")
    shared = (set(before) & set(after)) - set(ADDITIVE) - set(FIXED_TOP)
    for key in sorted(shared, key=str):
        if before[key] != after[key]:
            out.append(f"{key} {CHANGED}")


def _manifest(before: dict, after: dict, next_version: str, out: list[str]) -> list:
    """manifest is unchanged but its version, the minor bump, and at most one stem added to inputs.sets."""
    bm = _mapping(before.get("manifest"), "manifest", out)
    am = _mapping(after.get("manifest"), "manifest", out)
    _same_except(bm, am, ("version", "inputs"), "manifest.", out)
    if _bump(bm.get("version")) != next_version:
        out.append(f"version {_show(next_version)} is not the minor bump of {_show(bm.get('version'))}: an "
                   "improvement edit raises the minor version only")
    if am.get("version") != next_version:
        out.append(f"manifest.version is {_show(am.get('version'))}, not {_show(next_version)}")
    bi = _mapping(bm.get("inputs"), "manifest.inputs", out)
    ai = _mapping(am.get("inputs"), "manifest.inputs", out)
    _same_except(bi, ai, ("sets",), "manifest.inputs.", out)
    stems = _added(bi.get("sets"), ai.get("sets"), "manifest.inputs.sets", out)
    if stems and not isinstance(bi.get("sets"), list):
        out.append("manifest.inputs.sets is created: adding one would change which sets every run reads")
    if any(stem in _listed(bi.get("sets")) for stem in stems if isinstance(stem, str)):
        out.append("an added manifest.inputs.sets stem is already listed")
    if len(stems) > 1:
        out.append(f"manifest.inputs.sets adds {len(stems)} stems: an improvement edit adds at most one")
    return stems


def _terminology(before: dict, after: dict, out: list[str]) -> list:
    """terminology is unchanged but aliases added at the end, each exactly {phrase, term}."""
    bt = _mapping(before.get("terminology"), "terminology", out)
    at = _mapping(after.get("terminology"), "terminology", out)
    _same_except(bt, at, ("aliases",), "terminology.", out)
    aliases = _added(bt.get("aliases"), at.get("aliases"), "terminology.aliases", out)
    held = {_key(a.get("phrase")) for a in _listed(bt.get("aliases")) if isinstance(a, dict)}
    for a in aliases:
        if not isinstance(a, dict) or set(a) != {"phrase", "term"}:
            out.append(f"added alias {_show(a)} is not exactly {{phrase, term}}")
        elif _key(a["phrase"]) in held:
            out.append(f"added alias {_show(a['phrase'])} repeats a phrase terminology.aliases holds")
        else:
            held.add(_key(a["phrase"]))
    return aliases


def _listed(value) -> list:
    """value where it is a list, else none."""
    return value if isinstance(value, list) else []


def _key(value) -> str:
    """value as a comparable key, whatever its type: a text is itself, lower case and trimmed; anything else its JSON."""
    return value.strip().lower() if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


def _good_items(items) -> bool:
    """Whether items is a non-empty list of distinct strings."""
    return (isinstance(items, list) and bool(items) and all(isinstance(i, str) for i in items)
            and len(set(items)) == len(items))


def _tests(before: dict, after: dict, out: list[str]) -> list:
    """tests is unchanged but at most one entry added to sets, exactly {name, version: v1, items}."""
    bt = _mapping(before.get("tests"), "tests", out)
    at = _mapping(after.get("tests"), "tests", out)
    _same_except(bt, at, ("sets",), "tests.", out)
    added = _added(bt.get("sets"), at.get("sets"), "tests.sets", out)
    if len(added) > 1:
        out.append(f"tests.sets adds {len(added)} entries: an improvement edit adds at most one")
    if added and not isinstance(bt.get("sets"), list):
        out.append("tests.sets is created: adding the first would change which sets every run takes")
    names = {_key(s.get("name")) for s in _listed(bt.get("sets")) if isinstance(s, dict)}
    if any(isinstance(s, dict) and _key(s.get("name")) in names for s in added):
        out.append("an added tests.sets entry reuses a name tests.sets holds")
    for s in added:
        if not isinstance(s, dict) or set(s) != {"name", "version", "items"}:
            out.append(f"added tests.sets entry {_show(s)} is not exactly {{name, version, items}}")
        elif s["version"] != "v1" or not _good_items(s["items"]):
            out.append(f"added tests.sets entry {_show(s.get('name'))} needs version v1 and a non-empty list of "
                       "distinct item ids")
    return added


def _approvals(value) -> bool:
    """Whether any key in APPROVALS appears anywhere inside value."""
    if isinstance(value, dict):
        return any(k in APPROVALS for k in value) or any(_approvals(v) for v in value.values())
    if isinstance(value, list):
        return any(_approvals(v) for v in value)
    return False


def _phrases(before: dict, added: list, out: list[str]) -> None:
    """Each added phrase is exactly {phrase, operation}, never a filler, and a phrase the pack does not hold."""
    held = {_key(p.get("phrase")) for p in _listed(before.get("phrases")) if isinstance(p, dict)}
    for p in added:
        if not isinstance(p, dict) or set(p) != {"phrase", "operation"}:
            out.append(f"added phrase {_show(p)} is not exactly {{phrase, operation}}")
        elif p["operation"] == "filler":
            out.append(f"added phrase {_show(p['phrase'])} is a filler: an improvement edit never adds one")
        elif _key(p["phrase"]) in held:
            out.append(f"added phrase {_show(p['phrase'])} repeats a phrase the pack holds")
        else:
            held.add(_key(p["phrase"]))


def _shape(before: dict, added: list, out: list[str]) -> dict | None:
    """The one new shape, checked: at specify, no replaces, no constraints, an id new to shapes."""
    if len(added) > 1:
        out.append(f"shapes adds {len(added)} shapes: an improvement edit adds at most one")
    if not added:
        return None
    shape = added[0]
    if not isinstance(shape, dict):
        out.append(f"added shape {_show(shape)} is not a mapping")
        return None
    if shape.get("stage") != "specify":
        out.append(f"new shape {_show(shape.get('id'))} is at stage {_show(shape.get('stage'))}: an improvement edit "
                   "adds shapes at specify only")
    for key in ("replaces", "constraints"):
        if key in shape:
            out.append(f"new shape {_show(shape.get('id'))} carries {key}: an improvement edit never adds it")
    held = before.get("shapes") if isinstance(before.get("shapes"), list) else []
    if any(isinstance(s, dict) and s.get("id") == shape.get("id") for s in held):
        out.append(f"new shape id {_show(shape.get('id'))} is already in shapes")
    return shape


def _bundle(before: dict, shape: dict | None, adds: dict, out: list[str]) -> None:
    """The recipe, shape_tests entry, examples, stem and tests.sets entry that come with the new shape, and nothing
    of the kind without one."""
    named = {"recipes": "a recipe", "shape_tests": "a shape_tests entry", "examples": "an example",
             "stems": "a manifest.inputs.sets stem", "sets": "a tests.sets entry"}
    if shape is None:
        for key, what in named.items():
            if adds[key]:
                out.append(f"{what} added without a new shape: an improvement edit adds it only with one")
        return
    for key, what in named.items():
        if not adds[key]:
            out.append(f"new shape {_show(shape.get('id'))} comes without {what}")
    _recipe(before, shape, adds["recipes"], out)
    test_set = adds["sets"][0] if adds["sets"] and isinstance(adds["sets"][0], dict) else {}
    _shape_test(shape, adds["shape_tests"], test_set, out)
    _examples(before, shape, adds["examples"], out)
    want = "shapes_" + str(shape.get("id")).replace("-", "_")
    if test_set and test_set.get("name") != want:
        out.append(f"added tests.sets entry {_show(test_set.get('name'))} is not {_show(want)}, the new shape's set")
    if adds["stems"] and test_set and adds["stems"][0] != f"{test_set.get('name')}_v1":
        out.append(f"added stem {_show(adds['stems'][0])} is not {_show(str(test_set.get('name')) + '_v1')}, the new "
                   "tests.sets entry's file")


def _recipe(before: dict, shape: dict, added: list, out: list[str]) -> None:
    """At most one recipe, the one the new shape names, with an id new to recipes."""
    held = before.get("recipes") if isinstance(before.get("recipes"), list) else []
    if len(added) > 1:
        out.append(f"recipes adds {len(added)} recipes: an improvement edit adds at most one")
    if added and (not isinstance(added[0], dict) or added[0].get("id") != shape.get("recipe")):
        out.append(f"added recipe {_show(added[0])} is not the new shape's recipe {_show(shape.get('recipe'))}")
    elif added and _key(added[0].get("id")) in {_key(r.get("id")) for r in held if isinstance(r, dict)}:
        out.append(f"added recipe id {_show(added[0].get('id'))} is already in recipes")


def _shape_test(shape: dict, added: list, test_set: dict, out: list[str]) -> None:
    """At most one shape_tests entry, for the new shape, citing the new set with exactly its items."""
    if len(added) > 1:
        out.append(f"shape_tests adds {len(added)} entries: an improvement edit adds at most one")
    if not added:
        return
    entry = added[0]
    want = {"shape": shape.get("id"), "tests": {"set": test_set.get("name"), "items": test_set.get("items")}}
    if entry != want:
        out.append(f"added shape_tests entry {_show(entry)} does not cite the new shape with the new tests.sets "
                   f"entry and exactly its items {_show(want)}")


def _examples(before: dict, shape: dict, added: list, out: list[str]) -> None:
    """Each added example names the new shape and has an id new to examples."""
    held = before.get("examples") if isinstance(before.get("examples"), list) else []
    ids = {_key(e.get("id")) for e in held if isinstance(e, dict)}
    for e in added:
        if not isinstance(e, dict):
            out.append(f"added example {_show(e)} is not a mapping")
            continue
        if e.get("shape") != shape.get("id"):
            out.append(f"added example {_show(e.get('id'))} names {_show(e.get('shape'))}, not the new shape")
        if _key(e.get("id")) in ids:
            out.append(f"added example id {_show(e.get('id'))} is already in examples")
        ids.add(_key(e.get("id")))


def bounded(before: dict, after: dict, next_version: str) -> list[str]:
    """Compare two parsed packs: every way the candidate after goes beyond the improvement allowlist, as sentences.
    [] means it is within the bound."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ["a pack is not a mapping"]
    out: list[str] = []
    _top(before, after, out)
    stems = _manifest(before, after, next_version, out)
    aliases = _terminology(before, after, out)
    sets = _tests(before, after, out)
    adds = {s: _added(before.get(s), after.get(s), s, out) for s in ADDITIVE}
    _phrases(before, adds["phrases"], out)
    shape = _shape(before, adds["shapes"], out)
    _bundle(before, shape, {"recipes": adds["recipes"], "shape_tests": adds["shape_tests"],
                            "examples": adds["examples"], "stems": stems, "sets": sets}, out)
    for where, entries in (("terminology.aliases", aliases), ("tests.sets", sets), *adds.items()):
        for e in entries:
            if _approvals(e):
                out.append(f"an added {where} entry carries an approval: an improvement edit never approves")
    return out
