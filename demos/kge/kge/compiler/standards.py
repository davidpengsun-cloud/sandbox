"""The platform's preloaded reference standards (standards/preload.yaml): read, checked, and given to the compiler and
to onboarding.

The preload is platform data, not a client's: a fixed, versioned set of external standards' concepts, loaded into every
client's graph as reference OntologyConcepts (meta-model 1.3.0). A pack maps its terms, entities and restricted-data
policies to them (its ontology_mappings, check S30), and onboarding drafts candidate mappings to them
(kge/estate/draftflow.py). Neither may name a concept that is not here: widening the set is a platform update. The SKOS
mapping relations are read with the concept as the object: broadMatch says the concept is the broader.

Format 2 (preload version 1.1.0, meta-model 1.6.0) adds the platform's side of question shapes. The closed list of
operations a phrase may mark (held equal to the lexer's operation table); the coded plan builders a shape's `replaces`
may name; the clarification texts a rule's `message` names by id; and the base phrases, shapes, recipes and
clarification rules every graph loads, which a pack extends and never redefines (checks S32 to S36); no two base shapes
replace one coded builder, and no base phrase has a leading or trailing space. The grammar's other
closed lists are code, here: the slot kinds, the stages, the position constraints and the lexer's markers. A preload
recipe is checked as a pack's is (kge/compiler/plans.py), and a format-1 file is refused.

Preload version 1.2.0 also sets the fewest alternate phrasings a question shape's test item has
(shape_test_items.min_also_asked_as), the minimum the up-front check F12 holds a cited set item to
(kge/estate/upfront.py).

Preload version 1.3.0 holds baseline v1: the coded planner's phrase table, frozen, every row `baseline: true`. A phrase
names exactly one of an operation or `filler: true`, except a baseline phrase, which may name both: the four words the
coded table reads both ways (in, over, their, which) keep both readings.

A preload that does not hold its own shape is a platform defect, so load raises rather than reporting a finding.
Names no client.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

import yaml

from kge.compiler import plans
from kge.pipeline.settings import ROOT

PRELOAD = ROOT / "standards" / "preload.yaml"
NOTICES = PRELOAD.parent / "NOTICES.md"
FORMAT = 2
RELATIONS = ("exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch")
SUB_GRAPHS = ("physical", "glossary", "metrics", "ontology", "documents", "governance")
DEFINITION_TEXT = ("verbatim", "own wording")
SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
BUILDER = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?")
SLOT_KINDS = ("measure", "context", "entity", "dimension", "period", "range", "number", "comparison_op", "direction")
STAGES = ("specify", "shadow", "graph_first", "retired")
CONSTRAINTS = ("before", "after", "adjacent")
MARKERS = ("from_to", "asks_one", "measure_part")
HELD_BUILDERS: Mapping[str, str] = MappingProxyType({
    "planshapes.compose:ranking": (
        "no marker reproduces the coded limit: asks_one with a default limit of 1 differs from it (\"which category had "
        "the most ...\" ranks every row with no limit; only two directions or a partition give one row per end), and "
        "adjacent_direction covers a number beside its direction word but not one beside the ranked breakdown "
        "(\"which 3 categories had the most ...\")"),
    "planshapes.parts": (
        "measure_part marks every predicate a pack flags part: true, alone or coordinated, while the coded parts split a "
        "measure only on predicates named as separate parts (\"A and B\")"),
})
EMPTY: Mapping = MappingProxyType({})


class Preload(NamedTuple):
    version: str
    standards: dict[str, dict]
    concepts: dict[str, dict]
    by_iri: dict[str, dict]
    defaults: dict
    operations: tuple[str, ...] = ()
    coded_builders: tuple[str, ...] = ()
    clarification_texts: Mapping[str, str] = EMPTY
    phrases: Mapping[str, dict] = EMPTY
    shapes: Mapping[str, dict] = EMPTY
    recipes: Mapping[str, dict] = EMPTY
    clarifications: Mapping[str, dict] = EMPTY
    min_also_asked_as: int = 3

    def names(self) -> frozenset[str]:
        """The names a shape's or a clarification rule's requires and forbids may use: the operations and the markers."""
        return frozenset(self.operations) | frozenset(MARKERS)

    def operand(self, x: str) -> bool:
        """Whether x may be a position constraint's operand: an operation, a marker or a slot kind."""
        return x in self.operations or x in MARKERS or x in SLOT_KINDS

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
    return Preload(data["version"], standards, concepts, by_iri, defaults, **_question_shapes(data, fail),
                   **_test_items(data, fail))


def _test_items(data: dict, fail) -> dict:
    """shape_test_items, checked: min_also_asked_as, the fewest alternate phrasings (also_asked_as and added_phrasings
    together) a set item a question shape cites has, F12's minimum. A whole number, at least 1; format 2 always holds
    it."""
    held = data.get("shape_test_items")
    least = held.get("min_also_asked_as") if isinstance(held, dict) else None
    if not isinstance(least, int) or isinstance(least, bool) or least < 1:
        fail("shape_test_items.min_also_asked_as is a whole number, at least 1")
    return {"min_also_asked_as": least}


def _listed(entries) -> list[str]:
    """The names a requires or forbids list gives: each text, and each member of a {one_of: [...]} entry."""
    out = []
    for entry in entries if isinstance(entries, list) else [entries]:
        if isinstance(entry, dict) and isinstance(entry.get("one_of"), list):
            out += entry["one_of"]
        else:
            out.append(entry)
    return out


def _question_shapes(data: dict, fail) -> dict:
    """Format 2's question-shape keys, checked: the closed lists first, then the base set that names them."""
    operations = data.get("operations")
    if not isinstance(operations, list) or not operations or len(set(map(str, operations))) != len(operations) \
            or not all(isinstance(o, str) and SNAKE.fullmatch(o) for o in operations):
        fail("operations is a list of distinct operation names (snake_case texts)")
    builders = data.get("coded_builders") or []
    if not isinstance(builders, list):
        fail("coded_builders is a list of planshapes.<function>[:<case>] names")
    for k, b in enumerate(builders):
        if not isinstance(b, str) or not BUILDER.fullmatch(b):
            fail(f"coded builder {b!r} is a text: <module>.<function>[:<case>]")
        if b in builders[:k]:
            fail(f"coded builder {b} is given twice")
    texts: dict[str, str] = {}
    for t in data.get("clarification_texts") or []:
        tid = t.get("id") if isinstance(t, dict) else None
        if not isinstance(tid, str) or not SLUG.fullmatch(tid):
            fail(f"clarification text {tid!r}'s id is a slug")
        if tid in texts:
            fail(f"clarification text {tid} is given twice")
        if not isinstance(t.get("text"), str) or not t["text"].strip():
            fail(f"clarification text {tid} has a text")
        texts[tid] = t["text"]
    names = frozenset(operations) | frozenset(MARKERS)
    phrases: dict[str, dict] = {}
    for ph in data.get("phrases") or []:
        p = ph.get("phrase") if isinstance(ph, dict) else None
        if not isinstance(p, str) or not p.strip() or p != p.lower():
            fail(f"phrase {p!r} is lower-case text")
        if p != p.strip():
            fail(f"phrase {p!r} has leading or trailing space: a phrase is its words alone")
        if p in phrases:
            fail(f"phrase {p!r} is given twice")
        operation, filler = "operation" in ph, ph.get("filler") is True
        dual = operation and filler and ph.get("baseline") is not True
        if not (operation or filler) or "filler" in ph and not filler or dual:
            fail(
                f"phrase {p!r} names one of operation or filler: true (both only on a baseline phrase, "
                "which keeps the coded table's two readings)"
            )
        if "operation" in ph and ph["operation"] not in operations:
            fail(f"phrase {p!r} names operation {ph['operation']!r}, which is not in operations")
        if "baseline" in ph and not isinstance(ph["baseline"], bool):
            fail(f"phrase {p!r}'s baseline is true or false")
        phrases[p] = ph
    recipes: dict[str, dict] = {}
    for r in data.get("recipes") or []:
        rid = r.get("id") if isinstance(r, dict) else None
        if not isinstance(rid, str) or not SLUG.fullmatch(rid):
            fail(f"recipe {rid!r}'s id is a slug")
        if rid in recipes:
            fail(f"recipe {rid} is given twice")
        for loc, msg in plans.recipe_problems(r):
            fail(f"recipe {rid}: {loc + ': ' if loc else ''}{msg}")
        recipes[rid] = r
    shapes: dict[str, dict] = {}
    replaced: dict[str, str] = {}
    for sh in data.get("shapes") or []:
        sid = sh.get("id") if isinstance(sh, dict) else None
        if not isinstance(sid, str) or not SLUG.fullmatch(sid):
            fail(f"shape {sid!r}'s id is a slug")
        if sid in shapes:
            fail(f"shape {sid} is given twice")
        if not isinstance(sh.get("recipe"), str) or sh["recipe"] not in recipes:
            fail(f"shape {sid} uses recipe {sh.get('recipe')!r}, which is not in recipes")
        if "replaces" in sh and sh["replaces"] not in builders:
            fail(f"shape {sid} replaces {sh['replaces']!r}, which is not in coded_builders")
        if "replaces" in sh and sh["replaces"] in replaced:
            fail(f"shape {sid} replaces {sh['replaces']}, which shape {replaced[sh['replaces']]} replaces already: one shape "
                 "mirrors each coded builder")
        if "replaces" in sh:
            replaced.setdefault(sh["replaces"], sid)
        if sh.get("stage") not in STAGES:
            fail(f"shape {sid}'s stage is one of {', '.join(STAGES)}")
        if sh.get("replaces") in HELD_BUILDERS and sh.get("stage") == "retired":
            fail(f"shape {sid} replaces {sh['replaces']}, which stays coded and is never retired: "
                 f"{HELD_BUILDERS[sh['replaces']]}")
        slots = sh.get("slots")
        if not isinstance(slots, list) or not all(isinstance(x, dict) and x.get("kind") in SLOT_KINDS for x in slots):
            fail(f"shape {sid}'s slots each have a kind: one of {', '.join(SLOT_KINDS)}")
        for x in _listed(sh.get("requires") or []) + _listed(sh.get("forbids") or []):
            if not isinstance(x, str) or x not in names:
                fail(f"shape {sid} names {x!r}, which is not an operation or a marker")
        shapes[sid] = sh
    clarifications: dict[str, dict] = {}
    for c in data.get("clarifications") or []:
        cid = c.get("id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not SLUG.fullmatch(cid):
            fail(f"clarification {cid!r}'s id is a slug")
        if cid in clarifications:
            fail(f"clarification {cid} is given twice")
        if not isinstance(c.get("message"), str) or c["message"] not in texts:
            fail(f"clarification {cid}'s message {c.get('message')!r} is not in clarification_texts")
        when = c.get("when") if isinstance(c.get("when"), dict) else {}
        for x in _listed(when.get("requires") or []) + _listed(when.get("forbids") or []):
            if not isinstance(x, str) or x not in names:
                fail(f"clarification {cid} names {x!r}, which is not an operation or a marker")
        clarifications[cid] = c
    return {"operations": tuple(operations), "coded_builders": tuple(builders),
            "clarification_texts": MappingProxyType(texts), "phrases": MappingProxyType(phrases),
            "shapes": MappingProxyType(shapes), "recipes": MappingProxyType(recipes),
            "clarifications": MappingProxyType(clarifications)}


@lru_cache(maxsize=4)
def _load(path: str, stamp: float) -> Preload:
    return check(yaml.safe_load(Path(path).read_text()))


def load(path: Path = PRELOAD) -> Preload:
    """The preload at path (the platform's, by default), checked; read again when the file changes."""
    path = Path(path)
    return _load(str(path), path.stat().st_mtime)
