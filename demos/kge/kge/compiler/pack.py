"""A client's pack as the compiler reads it: pack.yaml and the files its manifest includes, the pack's lock, and the
platform's meta-model.

A client's folder, <clients>/<client>/, holds its pack: pack.yaml, which holds
the manifest and may hold every section inline, and the files the manifest's
includes name, each a YAML mapping of further sections, named relative to the
folder and inside it. Each section is kept in one file, and the manifest in
pack.yaml; resolved, the pack is the same however it is split, and its hash is
taken over what it says (canonical JSON), not over how the files are laid out
or commented. A date YAML reads as a date is kept as the ISO string it spells.
Beside it, pack.lock is the catalogue kge/compiler/lock.py froze.

Reading never raises on a pack: what it cannot read is a finding (S01), so a
compile lists every finding and writes nothing. Names no client: the client is
the caller's, chosen at deployment.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import yaml

from kge.pipeline.access import accepts_drafts as accepts_drafts

PACK = "pack.yaml"
LOCK = "pack.lock"
LOCK_FORMAT = 1
INCLUDE = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.ya?ml")


class Finding(NamedTuple):
    """One thing a pack gets wrong: the rule, where (<file>:<location>), and what."""
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule} {self.path}: {self.message}"


@dataclass
class Pack:
    client: str
    folder: Path
    data: dict = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)
    lock: object = None
    lock_bytes: bytes = b""
    findings: list[Finding] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    warehouses: dict[str, dict] = field(default_factory=dict)
    principal_roles: dict[str, dict] = field(default_factory=dict)

    def at(self, location: str) -> str:
        """A finding's path for a location in the pack (mappings[3].column): the file holding its section, and it."""
        section = re.match(r"[^.\[]*", location).group(0)
        return f"{self.origin.get(section, PACK)}:{location}"

    @property
    def hash(self) -> str:
        """sha256 of the resolved pack as canonical JSON, less the includes, which only say where its sections are kept."""
        manifest = self.data.get("manifest")
        if isinstance(manifest, dict):
            manifest = {k: v for k, v in manifest.items() if k != "includes"}
        resolved = {**self.data, "manifest": manifest}
        if self.drafts:
            resolved["_drafts"] = self.drafts
        if self.warehouses:
            resolved["_warehouses"] = self.warehouses
        return hashlib.sha256(canonical(resolved).encode()).hexdigest()

    @property
    def lock_hash(self) -> str:
        return hashlib.sha256(self.lock_bytes).hexdigest()


def canonical(value) -> str:
    """Compact canonical JSON: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def dated(value):
    """value with every date or datetime YAML read as the ISO string it was written as."""
    if isinstance(value, dict):
        return {k: dated(v) for k, v in value.items()}
    if isinstance(value, list):
        return [dated(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def meta_model(path: Path) -> dict:
    """The platform's meta-model, as written."""
    return yaml.safe_load(Path(path).read_text())


def load_yaml(path: Path, name: str, findings: list[Finding]):
    """A file's YAML, or None with a finding saying why it cannot be read."""
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        findings.append(Finding("S01", name, f"cannot be read as YAML ({getattr(exc, 'problem', None) or type(exc).__name__}{where})"))
    except (OSError, UnicodeDecodeError) as exc:
        findings.append(Finding("S01", name, f"cannot be read ({type(exc).__name__})"))
    return None


def read(clients: Path, client: str, lock: bool = True, identities: Path | None = None,
         without_unit: str | None = None) -> Pack:
    """client's pack from <clients>/<client>/, its includes resolved, and with lock its lock; what cannot be read, as
    findings. without_unit leaves that onboarded unit's draft out: an update to the unit is drafted against the pack as
    it would be without the unit, as its first draft was (kge/estate/onboard/drafting.py)."""
    folder = Path(clients) / client
    pack = Pack(client, folder)
    if not (folder / PACK).is_file():
        pack.findings.append(Finding("S01", PACK, f"there is no pack for {client}: {folder / PACK} is not a file"))
        return pack
    data = load_yaml(folder / PACK, PACK, pack.findings)
    if data is None and pack.findings:
        return pack
    if not isinstance(data, dict):
        pack.findings.append(Finding("S01", PACK, "is a mapping of sections, the manifest first"))
        return pack
    bindings = None
    if identities is not None:
        try:
            bindings = yaml.safe_load(Path(identities).read_text())["principals"]
            if not isinstance(bindings, list) or not all(isinstance(i, dict) and isinstance(i.get("principal"), str)
                                                         and isinstance(i.get("role"), str) for i in bindings):
                raise TypeError("principals is not a list of principal/role bindings")
        except (OSError, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
            pack.findings.append(Finding("S01", str(identities), f"deployment identities cannot be read ({type(exc).__name__})"))
    for section, value in data.items():
        pack.data[section], pack.origin[section] = value, PACK
    manifest = data.get("manifest")
    includes = manifest.get("includes") if isinstance(manifest, dict) else None
    if isinstance(includes, list):
        for i, name in enumerate(includes):
            include(pack, name, f"{PACK}:manifest.includes[{i}]")
    if bindings is not None:
        roles = {r["id"]: r for r in (pack.data.get("access") or {}).get("roles", [])}
        pack.principal_roles = {i["principal"]: roles.get(i["role"], {}) for i in bindings}
    merge_drafts(pack, without_unit)
    pack.data = dated(pack.data)
    if lock:
        read_lock(pack)
    return pack


DRAFT_SECTIONS = {"sources", "no_join", "period_bindings", "terminology.aliases", "glossary", "measures", "mappings",
                  "filters", "row_selectors", "contexts"}
DRAFT_SECTIONS.add("tests.sets")
DRAFT_SECTIONS |= {"ontology_mappings", "policies.restricted_attributes"}
CERTIFIED_ONLY = {"ontology_mappings", "policies.restricted_attributes"}
DRAFT_KINDS = ("source", "term", "question", "mapping", "policy")


def merge_drafts(pack: Pack, without_unit: str | None = None) -> None:
    """Merge machine-written unit records after includes, in unit and item order.

    Draft files are deliberately outside pack.yaml: kge/estate/spec.py and the
    generator continue to see the original four-unit specification.  State is
    rendered here so it contributes to Pack.hash and every compiled row.

    A drafted mapping to a preloaded concept, and a candidate restricted-data
    policy, merge only once certified, so neither reaches the graph or the gate
    before the steward certifies it; a unit maps only its own drafted terms and
    policies, never another unit's or the pack's own. The warehouse version and
    input digest a draft names (kge/pipeline/warehouse_versions.py) go into the
    artifact, so the runtime reads that version's rows; an older draft names none.
    """

    from kge.packstore import Store
    store = Store(pack.folder)
    folders = sorted({name.split("/")[1] for name in store.list("units") if name.startswith("units/") and name.count("/") >= 2})
    for unit in folders:
        name = f"units/{unit}/draft.yaml"
        if unit == without_unit or not store.exists(name):
            continue
        try:
            draft = yaml.safe_load(store.read(name))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            pack.findings.append(Finding("S01", name, f"cannot be read ({type(exc).__name__})"))
            continue
        if draft is None:
            continue
        if not isinstance(draft, dict) or draft.get("format") != 1:
            pack.findings.append(Finding("S01", name, "unknown draft format (expected format: 1)"))
            continue
        if draft.get("unit") != unit:
            pack.findings.append(Finding("S01", name, f"unit {draft.get('unit')!r} does not equal folder {unit!r}"))
        if draft.get("warehouse") is not None:
            from kge.pipeline.warehouse_versions import entry
            found = entry(draft["warehouse"])
            if found:
                pack.warehouses[unit] = {"version": found[0], **({"inputs": found[1]} if found[1] else {})}
            else:
                pack.findings.append(Finding("S01", name, "warehouse is {version, inputs}: the unit's warehouse version, a "
                                                          "whole number from 1, and its inputs' sha256"))
        items, seen = draft.get("items"), set()
        if not isinstance(items, list):
            pack.findings.append(Finding("S01", name, "items is a list"))
            continue
        terms = {g.get("id") for g in pack.data.get("glossary", []) if isinstance(g, dict)} | \
                {x.get("id") for x in items if isinstance(x, dict) and x.get("kind") == "term"}
        unit_terms = {g.get("id") for x in items if isinstance(x, dict) and x.get("kind") == "term"
                      for g in (x.get("pack") or {}).get("glossary", []) if isinstance(g, dict)}
        for i, item in enumerate(items):
            at = f"{name}:items[{i}]"
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                pack.findings.append(Finding("S01", at, "is an item with an id")); continue
            if item["id"] in seen:
                pack.findings.append(Finding("S01", at, f"duplicate id {item['id']}"))
            seen.add(item["id"])
            if item.get("kind") not in DRAFT_KINDS:
                pack.findings.append(Finding("S01", at, f"unknown kind {item.get('kind')!r}")); continue
            accepted, certified = item.get("accepted_by"), item.get("certified_by")
            if accepted and pack.principal_roles and not accepts_drafts(pack.principal_roles.get(accepted, {})):
                pack.findings.append(Finding("S01", at, f"accepted_by {accepted!r} is not a business-team principal"))
                accepted = None
            if certified and pack.principal_roles and "certification" not in pack.principal_roles.get(certified, {}).get("processes", []):
                pack.findings.append(Finding("S01", at, f"certified_by {certified!r} is not a steward principal"))
                certified = None
            if certified and not accepted:
                pack.findings.append(Finding("S01", at, "certified_by is set without accepted_by"))
            if certified and certified == accepted:
                pack.findings.append(Finding("S01", at, "certified_by equals accepted_by"))
            if item.get("kind") == "question" and any(t not in terms for t in item.get("terms", [])):
                pack.findings.append(Finding("S01", at, "question names an unknown drafted term"))
            pack.drafts.append({"unit": unit, "id": item["id"], "kind": item.get("kind"),
                                "state": "certified" if certified else "accepted" if accepted else "drafted",
                                "accepted_by": accepted, "certified_by": certified,
                                **({"set": item.get("set"), "terms": item.get("terms")} if item.get("kind") == "question" else {})})
            additions = item.get("pack") or {}
            unknown = set(additions) - DRAFT_SECTIONS
            if unknown:
                pack.findings.append(Finding("S01", at, f"may not write section(s) {', '.join(sorted(unknown))}")); continue
            for section, entries in additions.items():
                if not isinstance(entries, list):
                    pack.findings.append(Finding("S01", at, f"draft section {section} is a list")); continue
                if section in CERTIFIED_ONLY and not certified:
                    continue
                if section == "terminology.aliases":
                    target, key = pack.data.setdefault("terminology", {}).setdefault("aliases", []), "terminology"
                elif section == "policies.restricted_attributes":
                    target, key = pack.data.setdefault("policies", {}).setdefault("restricted_attributes", []), "policies"
                elif section == "tests.sets":
                    target, key = pack.data.setdefault("tests", {}).setdefault("sets", []), "tests"
                else:
                    target, key = pack.data.setdefault(section, []), section
                for j, raw in enumerate(entries):
                    if not isinstance(raw, dict):
                        pack.findings.append(Finding("S01", f"{at}.pack.{section}[{j}]", "is an object")); continue
                    entry = dict(raw)
                    confined = {
                        "sources": entry.get("id") == unit,
                        "period_bindings": str(entry.get("table", "")).startswith(unit + "."),
                        "measures": str(entry.get("id", "")).startswith(unit + ".") and str(entry.get("column", "")).startswith(unit + "."),
                        "mappings": str(entry.get("column", "")).startswith(unit + "."),
                        "filters": str(entry.get("table", "")).startswith(unit + "."),
                        "row_selectors": str(entry.get("table", "")).startswith(unit + "."),
                        "contexts": str(entry.get("fact", "")).startswith(unit + ".") and str(entry.get("measure", "")).startswith(unit + "."),
                        "ontology_mappings": (entry.get("term") in unit_terms if "term" in entry else
                                              str(entry.get("policy", "")).startswith(unit + "-") if "policy" in entry else False),
                        "policies.restricted_attributes": str(entry.get("id", "")).startswith(unit + "-") and
                                                          all(str(c).startswith(unit + ".") for c in entry.get("columns") or [None]),
                    }.get(section, True)
                    if not confined:
                        pack.findings.append(Finding("S01", f"{at}.pack.{section}[{j}]", f"must belong to unit {unit}"))
                        continue
                    if section == "glossary":
                        entry.update(status="certified" if certified else "candidate")
                        if certified:
                            entry.pop("version", None)
                        else:
                            entry["version"] = "draft"
                    elif section == "mappings":
                        entry["certified"] = bool(certified)
                        if certified:
                            entry.update(certified_by=certified, certified_on=item.get("certified_on"))
                    elif section == "ontology_mappings":
                        entry.update(certified_by=certified, certified_on=item.get("certified_on"))
                    elif section == "no_join":
                        entry.update(asserted_by=certified or "make draft",
                                     asserted_on=item.get("certified_on") if certified else draft.get("drafted", {}).get("on"))
                    target.append(entry)
                pack.origin[key] = name


def include(pack: Pack, name, at: str) -> None:
    """The sections of one included file, into pack: a .yaml file inside the client's folder, named relative to it."""
    if not isinstance(name, str) or not INCLUDE.fullmatch(name) or ".." in PurePosixPath(name).parts:
        pack.findings.append(Finding("S01", at, f"{name!r} is not an include: a .yaml file in the client's folder, named relative to it"))
        return
    path = pack.folder / name
    if not path.is_file():
        pack.findings.append(Finding("S01", at, f"{name} is not a file in the client's folder"))
        return
    if pack.folder.resolve() not in path.resolve().parents:
        pack.findings.append(Finding("S01", at, f"{name} leads outside the client's folder"))
        return
    found: list[Finding] = []
    more = load_yaml(path, name, found)
    pack.findings += found
    if found:
        return
    if not isinstance(more, dict):
        pack.findings.append(Finding("S01", name, "is a mapping of sections"))
        return
    for section, value in more.items():
        if section == "manifest":
            pack.findings.append(Finding("S01", f"{name}:manifest", f"the manifest is kept in {PACK}, which names the includes"))
        elif section in pack.origin:
            pack.findings.append(Finding("S01", f"{name}:{section}", f"is in {pack.origin[section]} already: a section is kept in one file"))
        else:
            pack.data[section], pack.origin[section] = value, name


def read_lock(pack: Pack) -> None:
    """The lock beside the pack, as written; a finding if there is none or it is not JSON."""
    try:
        pack.lock_bytes = (pack.folder / LOCK).read_bytes()
    except FileNotFoundError:
        pack.findings.append(Finding("S01", LOCK, "there is none: make lock (kge/compiler/lock.py) writes it from the landed catalogue"))
        return
    except OSError as exc:
        pack.findings.append(Finding("S01", LOCK, f"cannot be read ({type(exc).__name__})"))
        return
    try:
        pack.lock = json.loads(pack.lock_bytes)
    except ValueError:
        pack.findings.append(Finding("S01", LOCK, "is not JSON, as make lock writes it: run make lock"))
