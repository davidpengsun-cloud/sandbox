"""The question sets, each a file in the build, build/<name>.json, read the same way wherever a set is read.

A set's first version is a list of items (id, kind, question, expect), as the
held-out set's v1 is. A later version is an object that names its version and
its items:

    {"version": "v2", "items": [...]}

Git history is the record of how a set changed: a set declares no change and
no file digest, and a changes_from that an older file still carries is
ignored. The golden set is golden.json,
the held-out set, which no one sees, heldout.json, and a further set is
<name>_v<n>.json, as kge/eval/harness.py runs it.

A person approves a figure item's reference SQL, recorded as
reference_sql_approved_by (who) and reference_sql_approved_on (the date,
YYYY-MM-DD). They may be given on the item, its template or the suite's
defaults, and pass through to the item as written; scoring never reads them,
and they are never copied into its expect. An item's alternates are its
also_asked_as and then its added_phrasings, expanded only in the object form.
The up-front check F12 (kge/estate/upfront.py) reads these for the items a
question shape cites, through reference_sql, approval_problems, alternates and
locations.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

from kge.adapters.objstore import read_text, source_label
from kge.pipeline.evalargs import check_runtimes

VERSION = re.compile(r"^v(\d+)$")
GOLDEN, HELD_OUT = "golden", "heldout"
FURTHER = re.compile(r"^([a-z][a-z0-9_]*)_v\d+$")
TEMPLATE_VAR = re.compile(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")
SUITE_KEYS = ("suite_id", "suite_version", "title", "defaults")
APPROVAL = ("reference_sql_approved_by", "reference_sql_approved_on")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_heldout(name: str) -> bool:
    """Whether a set name or stem is held out: heldout or heldout_<name>."""
    return name == HELD_OUT or name.startswith(f"{HELD_OUT}_")


def _render_template(template: str, row: dict) -> str:
    """Substitute {var} placeholders from row while leaving ${SCHEMA} placeholders untouched."""
    return TEMPLATE_VAR.sub(lambda m: str(row[m.group(1)]) if m.group(1) in row else m.group(0), template)


def _prefix(tpl: dict) -> str:
    """A template's id prefix: the id its expanded items are numbered under, and the template_id they carry."""
    return str(tpl.get("id_prefix") or tpl.get("template_id") or tpl.get("id") or "T")


def _row_id(prefix: str, idx: int, row: dict) -> str:
    """The id of a template's matrix row, idx counted from 1: the row's own id, else <prefix>_<idx, three digits>."""
    return str(row.get("id") or f"{prefix}_{idx:03d}")


def _normalize_uat_item(raw: dict, defaults: dict) -> dict:
    """Normalize a UAT question or template-expanded item into a standard eval item while keeping its metadata."""
    item = {**defaults, **raw}
    behavior = str(item.get("expected_behavior") or item.get("kind") or "").strip().lower()
    expect = dict(item.get("expect") or {})
    if item.get("expected_total") is not None and "figure" not in expect:
        expect["figure"] = item["expected_total"]
    tol = item.get("tolerance", item.get("tolerance_usd", defaults.get("tolerance")))
    if tol is not None and "tolerance" not in expect:
        expect["tolerance"] = tol
    if item.get("reference_sql") and "reference_sql" not in expect:
        expect["reference_sql"] = item["reference_sql"]
    if item.get("expected_rows") and "rows" not in expect:
        expect["rows"] = item["expected_rows"]
    if item.get("required_signals") and "required_signals" not in expect:
        expect["required_signals"] = list(item["required_signals"])
    if item.get("forbidden_signals") and "forbidden_signals" not in expect:
        expect["forbidden_signals"] = list(item["forbidden_signals"])
    if item.get("runtimes") is not None:
        item["runtimes"] = check_runtimes(item.get("id") or "suite", item["runtimes"])
    if not item.get("kind"):
        if behavior in ("abstain", "clarify", "abstention"):
            item["kind"] = "abstain"
        elif behavior in ("refusal", "refuse"):
            item["kind"] = "refusal"
        elif behavior == "list":
            item["kind"] = "list"
        elif behavior == "point":
            item["kind"] = "point"
        elif behavior == "figure" or "figure" in expect or item.get("reference_sql"):
            item["kind"] = "figure"
        else:
            item["kind"] = "point"
    if expect or "expect" in item:
        item["expect"] = expect
    return item


PHRASED = ("also_asked_as", "added_phrasings", "expected_behavior")


def expand_suite_items(label: Path | str, data: dict) -> list[dict]:
    """Expand items/questions, templates+matrix, and also_asked_as variants in deterministic order, then each item's
    added_phrasings (phrasings the set adds to a question's own, numbered after its also_asked_as, so their ids keep
    their numbers). Outside a UAT suite, an item with no also_asked_as, added_phrasings or expected_behavior is kept as
    written, so a set that gains phrased items does not change its other items."""
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    has_uat = any(k in data for k in ("questions", "templates", "runtimes", *SUITE_KEYS))
    if "items" in data and not isinstance(data["items"], list):
        raise ValueError(f"{label}: a set names its items, a list")
    if "questions" in data and not isinstance(data["questions"], list):
        raise ValueError(f"{label}: questions must be a list")
    if "templates" in data and not isinstance(data["templates"], list):
        raise ValueError(f"{label}: templates must be a list")
    if "items" not in data and "questions" not in data and "templates" not in data:
        raise ValueError(f"{label}: a set names its items, a list")
    base_list: list[dict] = []
    existing_ids: set[str] = set()
    for raw in list(data.get("items") or []) + list(data.get("questions") or []):
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            if raw["id"] in existing_ids:
                continue
            existing_ids.add(raw["id"])
        base_list.append(raw)
    existing_templates = {
        i.get("template_id") for i in base_list if isinstance(i, dict) and isinstance(i.get("template_id"), str)
    }
    for tpl in data.get("templates") or []:
        if not isinstance(tpl, dict) or not isinstance(tpl.get("matrix"), list):
            raise ValueError(f"{label}: each template must be an object with a matrix list")
        prefix = _prefix(tpl)
        if prefix in existing_templates or tpl.get("id") in existing_templates or tpl.get("template_id") in existing_templates:
            continue
        q_tpl = str(tpl.get("question_template") or tpl.get("question") or "")
        sql_tpl = tpl.get("reference_sql")
        base_fields = {k: v for k, v in tpl.items() if k not in ("id_prefix", "question_template", "matrix")}
        for idx, row in enumerate(tpl["matrix"], 1):
            if not isinstance(row, dict):
                raise ValueError(f"{label}: template {prefix} matrix entries must be objects")
            item_id = _row_id(prefix, idx, row)
            if item_id in existing_ids:
                continue
            merged = {**base_fields, **row}
            merged["id"] = item_id
            merged["template_id"] = prefix
            if q_tpl and "question" not in row:
                merged["question"] = _render_template(q_tpl, row)
            if isinstance(sql_tpl, str) and "reference_sql" not in row:
                merged["reference_sql"] = _render_template(sql_tpl, row)
            base_list.append(merged)
            existing_ids.add(item_id)
    if not has_uat and not any(isinstance(i, dict) and any(k in i for k in PHRASED) for i in base_list):
        return checked_items(label, base_list)
    expanded: list[dict] = []
    for raw in base_list:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ValueError(f"{label}: the items are objects, each with its id")
        if not has_uat and not any(k in raw for k in PHRASED):
            expanded.append(raw)
            continue
        norm = _normalize_uat_item(raw, defaults)
        alts = norm.pop("also_asked_as", None)
        added = norm.pop("added_phrasings", None)
        expanded.append(norm)
        if added is not None and not isinstance(added, list):
            raise ValueError(f"{label}: added_phrasings on {norm['id']} must be a list")
        phrasings = [("also_asked_as", q) for q in (alts if isinstance(alts, list) else [])] + [("added_phrasings", q) for q in added or []]
        if phrasings:
            for alt_idx, (key, alt_q) in enumerate(phrasings, 1):
                if not isinstance(alt_q, str) or not alt_q.strip():
                    raise ValueError(f"{label}: {key} entries on {norm['id']} must be non-empty strings")
                alt_item = dict(norm)
                alt_item["id"] = f"{norm['id']}_alt{alt_idx}"
                alt_item["question"] = alt_q
                alt_item["variant_of"] = norm["id"]
                expanded.append(alt_item)
    return checked_items(label, expanded)


def read(path: Path | str) -> dict:
    """{"version", "items"}: a list is its set's first version, v1. Anything else is a ValueError naming the file and
    what is wrong with it."""
    label = source_label(path)
    try:
        data = json.loads(read_text(path))
    except ValueError as exc:
        raise ValueError(f"{label}: not JSON: {exc}") from None
    if isinstance(data, list):
        return {"version": "v1", "items": checked_items(label, data)}
    if not isinstance(data, dict):
        raise ValueError(f"{label}: a set is a list of items, or an object naming its version and its items")
    items = expand_suite_items(label, data)
    if "catalogue" in data:
        if not isinstance(data["catalogue"], list):
            raise ValueError(f"{label}: catalogue items are a list")
        checked_items(label, data["catalogue"])
    version = data.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 1:
        version = f"v{version}"
    elif version is None and isinstance(data.get("suite_version"), str) and data["suite_version"].strip():
        version = "v1"
    if not isinstance(version, str) or not VERSION.match(version):
        raise ValueError(f"{label}: a set names its version, v<n>, not {version!r}")
    extra = {k: data[k] for k in SUITE_KEYS if k in data}
    if "runtimes" in data:
        extra["runtimes"] = check_runtimes(label, data["runtimes"])
    elif isinstance(data.get("defaults"), dict) and "runtimes" in data["defaults"]:
        extra["runtimes"] = check_runtimes(label, data["defaults"]["runtimes"])
    return {"version": version, "items": items, **extra}


def reference_sql(item: dict) -> str | None:
    """The item's reference SQL as a run reads it: its own reference_sql, else its expect's; None where neither is a
    non-blank text."""
    own = item.get("reference_sql")
    if isinstance(own, str) and own.strip():
        return own
    expect = item.get("expect")
    held = expect.get("reference_sql") if isinstance(expect, dict) else None
    return held if isinstance(held, str) and held.strip() else None


def approval_problems(item: dict) -> list[str]:
    """What is wrong with the item's approval of its reference SQL: [] where it gives neither field, or both well
    formed. Both or neither; reference_sql_approved_by a non-empty text, the person; reference_sql_approved_on a date,
    YYYY-MM-DD."""
    given = [k for k in APPROVAL if k in item]
    if not given:
        return []
    if len(given) == 1:
        missing = next(k for k in APPROVAL if k not in item)
        return [f"names {given[0]} without {missing}"]
    out = []
    who, when = item[APPROVAL[0]], item[APPROVAL[1]]
    if not isinstance(who, str) or not who.strip():
        out.append(f"{APPROVAL[0]} is a non-empty text: the person who approved the reference SQL")
    if not _iso_date(when):
        out.append(f"{APPROVAL[1]} is a date, YYYY-MM-DD, not {when!r}")
    return out


def _iso_date(value) -> bool:
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def approved(item: dict) -> bool:
    """Whether a person approved the item's reference SQL: both fields given, and well formed."""
    return all(k in item for k in APPROVAL) and not approval_problems(item)


def _phrasing(question) -> str:
    """A question as two phrasings are compared: case folded, its runs of white space one space."""
    return " ".join(str(question).casefold().split())


def alternates(items: list[dict]) -> dict[str, int]:
    """How many distinct alternate phrasings each item of a read set has, by id: the items that are its variants
    (variant_of: expanded from also_asked_as, then added_phrasings, or written so in the file), counting a phrasing
    once, ignoring case and spacing, and not the item's own question. An item with none is absent."""
    own = {i["id"]: _phrasing(i.get("question", "")) for i in items if "variant_of" not in i and isinstance(i.get("id"), str)}
    seen: dict[str, set[str]] = {}
    for i in items:
        base = i.get("variant_of")
        if isinstance(base, str) and _phrasing(i.get("question", "")) != own.get(base):
            seen.setdefault(base, set()).add(_phrasing(i.get("question", "")))
    return {base: len(held) for base, held in seen.items()}


def locations(path: Path | str) -> dict[str, str]:
    """Where each item written in a set's file sits in it, by id, the first written winning as read keeps it: [k] in a
    list file; in an object, items[k], then questions[k], then templates[t].matrix[r] under the id the row expands to.
    An alternate expanded from also_asked_as or added_phrasings is not written, so not listed; an item written with
    its own variant_of is. A file that cannot be read or is not JSON raises, as read does."""
    data = json.loads(read_text(path))
    out: dict[str, str] = {}
    if isinstance(data, list):
        for k, raw in enumerate(data):
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                out.setdefault(raw["id"], f"[{k}]")
        return out
    if not isinstance(data, dict):
        return out
    for key in ("items", "questions"):
        listed = data.get(key)
        for k, raw in enumerate(listed if isinstance(listed, list) else []):
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                out.setdefault(raw["id"], f"{key}[{k}]")
    templates = data.get("templates")
    for t, tpl in enumerate(templates if isinstance(templates, list) else []):
        if not isinstance(tpl, dict) or not isinstance(tpl.get("matrix"), list):
            continue
        for r, row in enumerate(tpl["matrix"]):
            if isinstance(row, dict):
                out.setdefault(_row_id(_prefix(tpl), r + 1, row), f"templates[{t}].matrix[{r}]")
    return out


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
    """Each item's content, by id, as a run records it, so a baseline can be held to the sets it ran."""
    return {i["id"]: hashlib.sha256(json.dumps(i, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for i in items}


def checked_items(path: Path, items: list) -> list[dict]:
    if not all(isinstance(i, dict) and isinstance(i.get("id"), str) for i in items):
        raise ValueError(f"{path}: the items are objects, each with its id")
    if len({i["id"] for i in items}) != len(items):
        raise ValueError(f"{path}: each item has a distinct id")
    return items


def golden(estate: Path, baseline: dict | None = None, arm: str | None = None, profile: str | None = None,
           units: list[str] | None = None, category: str | None = None) -> list[dict]:
    """The golden sets the estate holds, for the page's sample questions: the golden set, then each further set, by
    name, each {name, version, questions, samples, misses}: its distinct questions, in its order, as text alone, never
    an expectation, a figure or SQL. The held-out set is no golden set, and is never among them: it is held out. samples
    are the questions the page offers first: those the profile's A/B baseline answered as expected on arm, the page's
    default (every item that asks one as the run's principal passed, on every repeat), where the baseline is the
    profile's and ran that file at its version; else, or where it answered none, every question. misses are the questions the
    page marks as known misses: those the same baseline records an item of failing on arm; none without one. Given a
    category, each set also names, under category_questions, the questions of its items in that category that are no
    other item's phrasing (no variant_of), in its order: the golden 20 for the golden-20 category, as make golden20
    reads them (tests/integration/test_golden20.py, golden_items)."""
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
        asked = recorded(path, data["version"], data["items"], baseline, arm, profile)
        shown = {"name": m.group(1) if m else path.stem, "version": data["version"], "questions": questions,
                 "samples": [q for q, ok in asked.items() if all(ok)] or questions,
                 "misses": [q for q, ok in asked.items() if False in ok]}
        if category is not None:
            shown["category_questions"] = list(dict.fromkeys(
                i["question"] for i in data["items"]
                if isinstance(i.get("question"), str) and i.get("category") == category and not i.get("variant_of")))
        out.append(shown)
    return out


def recorded(path: Path, version: str, items: list[dict], baseline: dict | None, arm: str | None,
             profile: str | None) -> dict[str, list]:
    """The baseline's record of each question of a set's file on arm: for each item that asks it as the run's principal
    (an item asked as another principal is not the page's to ask), True if it passed on every repeat, False if the record
    says it did not, None if it holds none. Empty if the baseline is another profile's or did not run that file at
    that version."""
    if not baseline or not arm or baseline.get("profile") != profile:
        return {}
    ran = next((k for k, s in (baseline.get("sets") or {}).items()
                if isinstance(s, dict) and s.get("file") == Path(path).name and s.get("version") == version), None)
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
