"""Public Ask interpretation uses the shared checked planner and governed evidence.

Metadata retains its routing; every numeric reply uses the common plan contract.
"""
from __future__ import annotations

from kge.services.ask.engine.contextpath import recorded

import datetime
import json
import re

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.question import OUTSIDE_PACK, grounded, parse, catalogue_question, definition_request
from kge.services.ask.engine.core import AGENT_ARMS, MODEL, PRESETS, STAGES, Engine, RunConfig, pair
from kge.adapters.model import no_fallback

PLANNER = "query-plan-v1"
NUMBER = re.compile(r"(?:(?<![\w.,])-)?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")
TOKEN = re.compile(r"\w(?:[\w.-]*\w)?")


def names(*texts: str) -> set[str]:
    """The names in texts: each token that mixes letters and digits, such as EM-004, FY26-Q1 or v2, and each part of
    one between hyphens that does too (FY26 and Q1)."""
    out = set()
    for t in (t for s in texts for t in TOKEN.findall(s or "")):
        out |= {n for n in [t, *t.split("-")] if re.search(r"\d", n) and re.search(r"[^\W\d_]", n)}
    return out


def numbers(text: str, known: set[str] = frozenset()) -> list[str]:
    """Every number in text, but for the digits of the known names it carries whole, which are taken out first,
    the longest first. Any other digits are a number, whatever they are glued to: a check of them fails closed. A number
    may be signed (-3000), glued to letters (USD9999), after a hyphen (the 200 of 100-200), or a bare decimal (.5)."""
    for name in sorted(known, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w|[.,]\d)", " ", text)
    return NUMBER.findall(text)


def role(impl: dict) -> str:
    """flag: a yes/no column that selects members. event: a row that says something happened. measure: a number."""
    if (impl.get("dtype") or "").upper() == "BOOLEAN":
        return "flag"
    if impl["col"].lower().endswith(("_id", "_no", "_ref")):
        return "event"
    return "measure"


def cohort(req, impls: dict[str, list[dict]]) -> tuple[list[str], list[str], list[str]] | None:
    """The shape /ask answers whole: one customer group, selected and narrowed by terms on a flag or an event,
    with measures over it. (the terms with a certified column, the group's, the measures), or None for any other
    question: one term, a term that only narrows a figure, two measures, a definition."""
    shaped = [t for t in impls if impls[t]]
    roles = {t: {role(i) for i in impls[t]} for t in shaped}
    group = [t for t in shaped if roles[t] & {"flag", "event"}]
    measures = [t for t in shaped if roles[t] == {"measure"}]
    if not group or len(shaped) < 2 or req.definitional or req.special:
        return None
    return shaped, group, measures


def across(e: Engine, units: list[str]) -> dict:
    """A customer group across units, which a cohort, built within one unit, does not answer: each pair with its no-join
    fact where it has one (facts), the first fact asserted (fact; None if no pair has one), the pairs a certified join
    links instead (certified), the behaviour and the reason. A pair with a no-join fact makes it a refusal; units that
    only a certified join links are no refused join, so it is an abstention, which says the certified join exists."""
    pairs = [(a, b) for i, a in enumerate(units) for b in units[i + 1:]]
    facts = [{"pair": pair(a, b, e), **f} for a, b in pairs if (f := e.g.no_join(a, b))]
    certified = [{"pair": pair(a, b, e), **c} for a, b in pairs if (c := e.g.certified_join(a, b))]
    labels = " and ".join(e.UNIT_LABEL[u] for u in units)
    if facts:
        why = "no certified join exists between them" if not certified else "a customer group is built within one unit"
        return {"behaviour": "refuse", "facts": facts, "fact": facts[0], "certified": certified,
                "reason": f"A customer group cannot span {labels}: each unit keys its customers differently and {why}. {facts[0]['note']}"}
    linked = "; ".join(f"{c['pair']} through the certified join {c['declared_join']} ({c['note']})" for c in certified)
    return {"behaviour": "abstain", "facts": [], "fact": None, "certified": certified,
            "reason": f"A customer group is built within one unit, and {labels} key their customers differently. They join only "
                      f"through a certified join ({linked}), which a customer group does not use: ask for a figure over it."}


CITED_FOR = ("certified definition", "certified passage", "retrieved by the question")


def number_citations(citations: list[dict], figures: list[dict]) -> None:
    """Number the citations in the order the answer reads them: the documents the figures rest on, in figure order,
    then the rest by why they are cited and by id. The same question then gets the same markers every time, whatever
    order the graph returned its rows in. A figure's cite holds a document id on the way in and its number on the way out."""
    used = list(dict.fromkeys(f["cite"] for f in figures if f["cite"]))
    citations.sort(key=lambda c: (used.index(c["doc"]) if c["doc"] in used else len(used),
                                  CITED_FOR.index(c["why"]) if c["why"] in CITED_FOR else len(CITED_FOR), c["doc"]))
    for n, c in enumerate(citations, 1):
        c["n"] = n
    by_doc = {c["doc"]: c["n"] for c in citations}
    for f in figures:
        f["cite"] = by_doc.get(f["cite"])


def measure_agg(term: str, question: str) -> str:
    t = term.lower()
    return "avg" if t.startswith("average") or re.search(r"\baverage " + re.escape(t), question.lower()) else "sum"


def period_words(period) -> str:
    if period is None:
        return "all periods"
    if period.kind == "month":
        return datetime.date.fromisoformat(period.value).strftime("%B %Y")
    return period.value


def excerpt(text: str | None, term: str, width: int = 220) -> str:
    """A window of the passage around the first mention of the term, not just its opening lines."""
    text = re.sub(r"\s+", " ", (text or "").replace("**", "")).strip()
    m = re.search(r"\b" + re.escape(term.lower().rstrip("s")), text.lower())
    if not m or m.start() < width // 3:
        return text[:width]
    start = max(0, m.start() - width // 3)
    return "…" + text[start:start + width]


def fmt(v) -> str:
    if v is None:
        return "none"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


class Combined:
    def __init__(self, engine: Engine, llm="auto"):
        self.e, self.g = engine, engine.g
        from kge.adapters.model import model_for
        llm = model_for("context") if llm == "auto" else llm
        self.llm = llm

    def default_config(self, arm: str = "graph") -> RunConfig:
        """The arm's preset with the model in every stage that can use one, if a model
        is configured; the preset itself if not. This is what /ask did before the run
        config existed."""
        cfg = PRESETS[arm]
        if self.llm is None:
            return cfg
        return cfg.with_stages(**{s: v + MODEL for s, v in cfg.stages().items() if v + MODEL in STAGES[s]})

    @recorded
    def ask(self, question: str, principal: str, purpose: str = "analysis", config: RunConfig | None = None,
            products: list[str] | None = None, history=(), runtimes: list[str] | None = None) -> dict:
        return self.e.business(self.governed(question, principal, purpose, config, products, history, runtimes=runtimes))

    def governed(self, question: str, principal: str, purpose: str = "analysis", config: RunConfig | None = None,
                 products: list[str] | None = None, history=(), runtimes: list[str] | None = None) -> dict:
        scope = ProductScope(self.e, products)
        out = self._governed(question, principal, purpose, config, scope, history, runtimes=runtimes)
        out.setdefault("combined", False)
        out.setdefault("history", [])
        for key, value in scope.metadata().items():
            out.setdefault(key, value)
        return out

    def _governed(self, question: str, principal: str, purpose: str, config: RunConfig | None, scope, history=(),
                  runtimes: list[str] | None = None) -> dict:
        cfg = self.e.stamped(config or self.default_config())
        if bad := cfg.problems("agent" if cfg.arm in AGENT_ARMS else "ask", has_model=self.llm is not None):
            raise ValueError("; ".join(bad))
        req = parse(question, self.e.term_names, self.e)
        if cfg.graph:
            req = definition_request(req)
        portal_card = scope.portal_only()
        if portal_card:
            if decision := scope.check(principal, req.units):
                return {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label,
                        "combined": False, "versions": {"parser": "rules-v1"}, **decision}
            return {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label,
                    "combined": False, "behaviour": "abstain", "facts": [], "figures": [],
                    "trace": [{"step": "portal_only"}],
                    "reason": "Not in the knowledge graph. No figure was read.",
                    "versions": {"parser": "rules-v1"}}
        metadata = cfg.graph and req.special is None and (req.definitional or (req.term is None and req.counted is None
                                       and req.agg == 'sum' and req.period is None and scope.products is None))
        if metadata and not history and not req.restricted and not req.problem and not catalogue_question(question, req.terms_found):
            classify = cfg.uses_model('resolution') and not (req.definitional and req.term is not None
                                                              and not req.units and scope.products is None)
            runtrace.use(cfg, self.llm if cfg.model_stages() else None)
            runtrace.stage("understand")
            out = {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label, "combined": False,
                   "versions": {"parser": "rules-v1", "model": self.llm.name if cfg.uses_model("resolution") else "none",
                                "embedding_model": self.g.embedder.name},
                   "trace": [{"step": "understand", "term": req.term, "terms": req.terms_found,
                              "resolved_by": "rules+model" if classify else "rules", "period_by": req.period_by,
                              "period": req.period.label() if req.period else None}]}
            runtrace.stage("access")
            if decision := scope.check(principal, req.units):
                return {**out, **decision}
            if not self.g.may(principal, "querying"):
                return {**out, "behaviour": "refuse", "reason": f"{principal} is not a querying identity."}
            if runtimes is not None:
                sel = self.e.w.select_runtime([], runtimes=runtimes, readable_units=self.g.readable_units(principal))
                if not sel.get("ok"):
                    return {**out, "behaviour": "refuse", "reason": sel.get("reason") or "No runtime is available for this request.",
                            **({"_skipped_runtimes": sel["skipped"]} if sel.get("skipped") else {})}
            if not req.definitional:
                from kge.services.ask.engine import planpublic, planrules
                from kge.semantics.coverage import one_meaning
                vocabulary = planrules.declared_vocabulary(self.e, principal, purpose, scope, cfg, req)
                pieces = planrules.ledger(question, 'turn', vocabulary)
                if planpublic.measure_question(question) or any(len(p.candidates) > 1 and not one_meaning(p.candidates, vocabulary) for p in pieces):
                    return self.e.ruled(question, principal, purpose, config=cfg, products=scope.products,
                                        history=history, _combined=self, runtimes=runtimes)
            if classify:
                self.understand(question, req.terms_found)
            if req.definitional:
                runtrace.stage("retrieve")
                definition = self.e.definition_answer(req, principal, purpose, cfg)
                return scope.documents({**out, **definition})
            vocabulary = [{"excerpt": " ".join([*self.e.term_names, *self.e.ALIASES])}]
            in_scope = (grounded(question, vocabulary, self.e)
                        or grounded(question, self.g.retrieve(question, principal, purpose, k=3)["exact"], self.e))
            return {**out, "behaviour": "abstain", "reason": "No glossary term recognised in the question." if in_scope else OUTSIDE_PACK}
        return self.e.ruled(question, principal, purpose, config=cfg, products=scope.products,
                            history=history, _combined=self, runtimes=runtimes)

    def understand(self, question: str, found: list[str]) -> tuple[list[str], object]:
        """Metadata glossary classification; never authorizes numeric execution. Anything not in the glossary is
        dropped, and a failed model call supplies neither terms nor a period; on the pilot's cloud path
        (gcp_native) it stops the run instead (NoFallback), as a failed query or phrasing call does."""
        prompt = ("Glossary terms: " + "; ".join(self.e.term_names) + "\nWhich of these terms does the question below ask about? "
                  "Use only names from the list, spelled exactly. Also read the question's entire period, if stated. "
                  "Return JSON only: {\"terms\": [str], \"period\": null | {\"kind\": \"day|month|quarter|year|range\", "
                  "\"value\": str, \"start\": str, \"end\": str}}. The period object is optional. "
                  "For day use value YYYY-MM-DD; month YYYY-MM; calendar quarter YYYY-Q1 through Q4; year YYYY. "
                  "For a range use start/end ISO dates (omit an unknown endpoint), with no value. "
                  "Omit fields that do not apply. Quarter values are calendar quarters; if a fiscal reading cannot be "
                  "established from the calendars below, use null. "
                  "Since, from/to, between, YTD, half-years and comparisons are ranges, never a single endpoint. "
                  "Use null if the period is absent or ambiguous; do not invent a year or silently correct an impossible date. "
                  "\nCalendars: " + json.dumps(getattr(self.e, "CALENDARS", {})) + "\n\nQuestion: " + question)
        try:
            reply = self.llm.json(prompt, 400)
            names = reply.get("terms", [])
        except Exception as exc:
            no_fallback("reading the question", exc)
            return [], None
        names = names if isinstance(names, list) else []
        return [t for t in names if isinstance(t, str) and t in self.e.term_names and t not in found], reply.get("period")

    def q(self, cypher: str, params: dict) -> list[dict]:
        """Graph query with only the parameters it names; the graph refuses any it does not use."""
        return self.g.q(cypher, {k: v for k, v in params.items() if re.search(r"\$" + k + r"\b", cypher)})

    def sources(self, question: str, terms: list[str], principal: str, purpose: str, unit: str) -> dict:
        """What the answer cites, what it shows and does not use, and what the gate holds back. Of a term's definitions
        the certified, current version is cited and the rest shown, not used; what the privacy gate holds back is named,
        never quoted; uncertified material is shown so a reader sees it exists, but a unit the principal cannot read is
        not even named."""
        readable = sorted(self.g.readable_units(principal))
        p = {"terms": terms, "purpose": purpose, "units": readable}
        visible = "(d.unit = '' OR d.unit IN $units)"
        citations: list[dict] = []
        not_cited: list[dict] = []
        withheld: list[dict] = []

        def add_cite(doc, title, version, chunk, excerpt, term, why):
            c = next((c for c in citations if c["doc"] == doc), None)
            if c:
                if term not in c["terms"]:
                    c["terms"].append(term)
                return
            citations.append({"n": None, "doc": doc, "title": title, "version": version, "chunk": chunk,
                              "excerpt": excerpt, "terms": [term], "why": why})

        defs = self.q(f"MATCH (d:Document)-[:DEFINES]->(t:Term) WHERE t.name IN $terms AND {visible} "
                        "OPTIONAL MATCH (d)-[:SUPERSEDED_BY]->(n:Document) "
                        "RETURN t.name AS term, d.id AS doc, d.title AS title, d.shelf AS shelf, d.version AS version, d.tier AS tier, "
                        "d.ai_ready AS ai_ready, n.id AS superseded_by "
                        "ORDER BY d.published_at DESC, d.id, t.name", p)
        for d in defs:
            first = self.g.q("MATCH (d:Document {id: $d})-[:HAS_CHUNK]->(c:Chunk) WHERE c.readiness <> 'embed-prohibited' AND $purpose IN c.purposes "
                             "RETURN c.id AS chunk, c.text AS text ORDER BY c.ordinal LIMIT 1", {"d": d["doc"], "purpose": purpose})
            d.update({"chunk": first[0]["chunk"], "excerpt": excerpt(first[0]["text"], d["term"])} if first else {"chunk": None, "excerpt": None})
            if first:
                runtrace.retrieved([{"doc": d["doc"], "chunk": first[0]["chunk"], "tier": d["tier"], "ai_ready": d["ai_ready"]}], "definition")
        for d in defs:
            if d["shelf"] != "certified":
                not_cited.append({"doc": d["doc"], "title": d["title"], "term": d["term"], "why": "uncertified definition"})
            elif d["superseded_by"]:
                not_cited.append({"doc": d["doc"], "title": d["title"], "term": d["term"], "version": d["version"],
                                  "why": f"superseded by {d['superseded_by']}"})
            elif d["chunk"]:
                add_cite(d["doc"], d["title"], d["version"], d["chunk"], d["excerpt"], d["term"], "certified definition")
            else:
                withheld.append({"doc": d["doc"], "term": d["term"], "why": "no chunk the gate lets this purpose read"})
        for m in self.q(f"MATCH (d:Document {{shelf: 'certified'}})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(t:Term) WHERE t.name IN $terms AND {visible} "
                          "AND c.readiness <> 'embed-prohibited' AND $purpose IN c.purposes AND NOT EXISTS { MATCH (d)-[:SUPERSEDED_BY]->() } "
                          "RETURN t.name AS term, d.id AS doc, d.title AS title, d.version AS version, d.tier AS tier, d.ai_ready AS ai_ready, "
                          "c.id AS chunk, c.text AS text ORDER BY doc, chunk, term", p):
            runtrace.retrieved([m], "mention")
            add_cite(m["doc"], m["title"], m["version"], m["chunk"], excerpt(m["text"], m["term"]), m["term"], "certified passage")
        for w in self.q(f"MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(t:Term) WHERE t.name IN $terms AND {visible} "
                          "AND (c.readiness = 'embed-prohibited' OR NOT ($purpose IN c.purposes)) "
                          "RETURN DISTINCT d.id AS doc, d.title AS title, c.id AS chunk, t.name AS term ORDER BY doc, chunk, term", p):
            withheld.append({**w, "why": "held back by the privacy gate: personal data; named, not quoted"})
        seen = {n["doc"] for n in not_cited} | {c["doc"] for c in citations}
        unit_of = {r["doc"]: r["unit"] for r in self.g.q("MATCH (d:Document {shelf: 'uncertified'}) RETURN d.id AS doc, d.unit AS unit")}
        mentions = self.q(f"MATCH (d:Document {{shelf: 'uncertified'}})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(t:Term) "
                          f"WHERE t.name IN $terms AND {visible} "
                          "RETURN t.name AS term, d.id AS id, d.title AS title, d.tier AS tier, d.ai_ready AS ai_ready, c.id AS chunk, "
                          "CASE WHEN c.readiness = 'embed-prohibited' THEN '[withheld: this chunk is embed-prohibited]' ELSE left(c.text, 160) END AS excerpt "
                          "ORDER BY t.name, d.id, c.id", p)
        for t in terms:
            for u in [m for m in mentions if m['term'] == t][:8]:
                if unit_of.get(u["id"]) not in ("", None, *readable):
                    continue
                runtrace.retrieved([{"doc": u["id"], "chunk": u["chunk"], "tier": u["tier"], "ai_ready": u["ai_ready"]}], "uncertified_mention")
                if u["id"] not in seen:
                    seen.add(u["id"])
                    not_cited.append({"doc": u["id"], "title": u["title"], "term": t, "why": "uncertified", "excerpt": u["excerpt"]})
        hits = self.g.retrieve(question, principal, purpose, k=5, shelf="certified")["exact"]
        superseded = {n["doc"] for n in not_cited if n["why"].startswith("superseded")}
        for h in hits:
            overlap = [t for t in (h["terms"] or []) if t in terms]
            if overlap and h["doc"] not in superseded:
                add_cite(h["doc"], h["title"], "", h["chunk"], h["excerpt"], overlap[0], "retrieved by the question")
        return {"citations": citations, "not_cited": not_cited, "withheld": withheld,
                "vector_hits": [{"doc": h["doc"], "chunk": h["chunk"], "score": round(float(h["score"]), 3)} for h in hits]}

    def template(self, r: dict) -> str:
        fs = r["figures"]
        cite = lambda f: f" [{f['cite']}]" if f.get("cite") else ""  # noqa: E731
        money = lambda f: (f["currency"] + " " if f["currency"] else "") + fmt(f["value"])  # noqa: E731
        version = {t: c["version"] for c in r["citations"] if c["why"] == "certified definition" for t in c["terms"]}
        named = lambda f: f["term"] if f["term"].lower().startswith(("average", "total")) else f"{'average' if f['kind'] == 'avg' else 'total'} {f['term']}"  # noqa: E731
        counts = [f for f in fs if f["kind"] == "count"]
        measures = [f for f in fs if f["kind"] != "count"]
        first = counts[0]
        v = f" v{version[first['term']]}" if version.get(first["term"]) else ""
        s = f"{r['unit_label']}, {r['period']}: {fmt(first['value'])} customers met the {first['term']} definition{v}{cite(first)}."
        for f in counts[1:]:
            s += f" Of those, {fmt(f['value'])} had a {f['term']}{cite(f)}."
        if measures:
            who = f"those {fmt(counts[-1]['value'])} customers" if len(counts) > 1 else "them"
            s += f" For {who}: " + "; ".join(f"{named(f)} {money(f)}{cite(f)}" for f in measures) + "."
        if r["not_cited"]:
            unused = {why: sorted({n["doc"] for n in r["not_cited"] if n["why"].startswith(why)}) for why in ("superseded", "uncertified")}
            s += " Not used: " + "; ".join(f"{why}, {', '.join(docs)}" for why, docs in unused.items() if docs) + "."
        if r["withheld"]:
            s += " Held back by the privacy gate: " + ", ".join(sorted({w["doc"] for w in r["withheld"]})) + "."
        return s

    def allowed_numbers(self, r: dict) -> set[float]:
        nums = {float(f["value"]) for f in r["figures"] if f["value"] is not None}
        for s in [r["period"], r["unit_label"]] + [c.get("version") or "" for c in r["citations"]] + [n.get("version") or "" for n in r["not_cited"]]:
            nums |= {float(x.replace(",", "")) for x in numbers(s, names(s))}
        return nums

    def phrase(self, r: dict, cfg: RunConfig) -> tuple[str, str]:
        """The template always; the model's wording only if the renderer uses it and every number in it is in the
        result. Each citation marker must name a citation, and the names the model was given (ids, versions, labels) are
        not numbers."""
        text = self.template(r)
        if not cfg.uses_model("renderer"):
            return text, "template"
        facts = {k: r[k] for k in ("unit_label", "period", "figures", "citations", "not_cited", "withheld")}
        prompt = ("Rewrite these facts as a short plain-English answer for a bank analyst. Use only these facts; add no number that is not "
                  "in them; keep the [n] citation markers; say what was not used and what was held back.\n\n"
                  + str(facts) + "\n\nReturn JSON only: {\"answer\": str}")
        try:
            said = str(self.llm.json(prompt, 500).get("answer", "")).strip()
        except Exception as exc:  # noqa: BLE001
            no_fallback("phrasing the answer", exc)
            return text, "template (model call failed)"
        if not said:
            return text, "template"
        markers = {int(m) for m in re.findall(r"\[(\d+)\]", said)}
        bad_marks = sorted(markers - {c["n"] for c in r["citations"]})
        allowed = self.allowed_numbers(r)
        known = names(r["question"], str(facts))
        stray = [x for x in numbers(re.sub(r"\[\d+\]", "", said), known) if float(x.replace(",", "")) not in allowed]
        runtrace.number_check(passed=not (stray or bad_marks), stray_numbers=len(stray), bad_markers=len(bad_marks))
        if stray or bad_marks:
            what = ", ".join(stray[:3] + [f"[{m}]" for m in bad_marks[:3]])
            return text, f"template (model text carried numbers or markers not in the result: {what})"
        return said, "model:" + self.llm.name
