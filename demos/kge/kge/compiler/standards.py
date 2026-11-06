"""The platform's preloaded reference standards (standards/preload.yaml): read, checked, and given to the compiler and
to onboarding.

The preload is platform data, not a client's: a fixed, versioned set of external standards' concepts, loaded into every
client's graph as reference OntologyConcepts (meta-model 1.3.0). A pack maps its terms, entities and restricted-data
policies to them (its ontology_mappings, check S30), and onboarding drafts candidate mappings to them
(kge/estate/draftflow.py). Neither may name a concept that is not here: widening the set is a platform update. The SKOS
mapping relations are read with the concept as the object: broadMatch says the concept is the broader.


A preload that does not hold its own shape is a platform defect, so load raises rather than reporting a finding.
Names no client.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import yaml

from kge.pipeline.settings import ROOT

PRELOAD = ROOT / "standards" / "preload.yaml"
NOTICES = PRELOAD.parent / "NOTICES.md"
FORMAT = 1
RELATIONS = ("exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch")
SUB_GRAPHS = ("physical", "glossary", "metrics", "ontology", "documents", "governance")
DEFINITION_TEXT = ("verbatim", "own wording")
SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


class Preload(NamedTuple):
    version: str
    standards: dict[str, dict]
    concepts: dict[str, dict]
    by_iri: dict[str, dict]
    defaults: dict

    def concept_row(self, concept: dict) -> dict:
        """A concept as the graph's OntologyConcept holds it (meta-model 1.3.0)."""
        s = self.standards[concept["standard"]]
        return {"id": concept["id"], "label": concept["label"], "iri": concept["iri"], "definition": concept["definition"],
                "sub_graph": concept["sub_graph"], "standard": s["id"], "standard_version": s["version"],
                "licence": s["licence"], "licence_url": s["licence_url"], "source": concept["source"]}

    def used(self, iris) -> list[dict]:
        """The standards, with their versions, that the concepts at iris come from: what a unit's mappings rest on."""
        ids = {self.by_iri[i]["standard"] for i in iris if i in self.by_iri}
        return [{"id": s["id"], "version": s["version"], "preload": self.version} for s in self.standards.values() if s["id"] in ids]


def kebab(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", name).replace("_", "-").lower()


def concept_id(standard: str, iri: str) -> str:
    """A preloaded concept's id: <standard>-<its IRI's local name, kebab-cased>; DPV's personal-data extension's,
    dpv-pd-<name>. A locator's file suffix is no part of the name."""
    local = re.split(r"[#/]", iri.rstrip("/"))[-1]
    local = re.sub(r"\.(ya?ml|json|rdf|ttl)$", "", local)
    return f"{standard}{'-pd' if '/dpv/pd#' in iri else ''}-{kebab(local)}"


def check(data) -> Preload:
    """The preload, checked: raises ValueError naming the first thing wrong."""
    def fail(message: str):
        raise ValueError(f"standards/preload.yaml: {message}")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        fail(f"is a mapping with format: {FORMAT}")
    if not isinstance(data.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", data["version"]):
        fail("version is semver (major.minor.patch)")
    standards: dict[str, dict] = {}
    for s in data.get("standards") or []:
        if not isinstance(s, dict) or not all(isinstance(s.get(k), str) and s[k] for k in
                                              ("id", "name", "publisher", "version", "licence", "licence_url")):
            fail(f"standard {s!r} needs an id, name, publisher, version, licence and licence_url")
        if not SLUG.fullmatch(s["id"]) or s["id"] in standards:
            fail(f"standard id {s['id']!r} is a slug, given once")
        standards[s["id"]] = s
    concepts: dict[str, dict] = {}
    by_iri: dict[str, dict] = {}
    for c in data.get("concepts") or []:
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k] for k in
                                              ("id", "standard", "iri", "label", "definition", "definition_text",
                                               "sub_graph", "source")):
            fail(f"concept {c!r} needs an id, standard, iri, label, definition, definition_text, sub_graph and source")
        if c["standard"] not in standards:
            fail(f"concept {c['id']} names standard {c['standard']!r}, which is not listed")
        if c["id"] != concept_id(c["standard"], c["iri"]):
            fail(f"concept {c['id']}'s id is not {concept_id(c['standard'], c['iri'])!r}, the id its IRI gives")
        if c["id"] in concepts or c["iri"] in by_iri:
            fail(f"concept {c['id']} ({c['iri']}) is given twice")
        if c["sub_graph"] not in SUB_GRAPHS:
            fail(f"concept {c['id']}'s sub_graph is one of {', '.join(SUB_GRAPHS)}")
        if c["definition_text"] not in DEFINITION_TEXT:
            fail(f"concept {c['id']}'s definition_text is verbatim or own wording")
        if not c["iri"].startswith(("http://", "https://")):
            fail(f"concept {c['id']}'s iri is an http(s) IRI")
        concepts[c["id"]], by_iri[c["iri"]] = c, c
    defaults = data.get("defaults") or {}
    rules = [defaults.get("currency_measure")] + list((defaults.get("personal_data") or {}).values())
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("concept") not in concepts or rule.get("relation") not in RELATIONS:
            fail(f"default {rule!r} names a listed concept and a SKOS relation")
    if "default" not in (defaults.get("personal_data") or {}):
        fail("defaults.personal_data has a default")
    return Preload(data["version"], standards, concepts, by_iri, defaults)


@lru_cache(maxsize=4)
def _load(path: str, stamp: float) -> Preload:
    return check(yaml.safe_load(Path(path).read_text()))


def load(path: Path = PRELOAD) -> Preload:
    """The preload at path (the platform's, by default), checked; read again when the file changes."""
    path = Path(path)
    return _load(str(path), path.stat().st_mtime)
