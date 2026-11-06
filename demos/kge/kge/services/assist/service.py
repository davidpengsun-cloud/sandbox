"""Select evidence without inventing figures or changing the governed answer."""
from __future__ import annotations

import re

from kge.adapters.deadline import RequestDeadlineExceeded, request_budget
from kge.adapters.model import (
    MOCK,
    NOT_JSON,
    ExternalCallError,
    NoFallback,
    NotJSON,
    model_for,
)
from kge.services import catalogue, search
from kge.services.ask import (
    FIGURE_FOLLOWUP,
    FIGURE_INTENT,
    Hidden,
    ProductScope,
    ask,
    catalogue_question,
    normalise_question,
    parse,
    worded,
)

NOTICE = "The assistant is unavailable; showing governed answers."
SOURCES = ("catalogue", "search", "ask")
ROUTE_PROMPT = (
    'Choose the one source that should answer the question: "catalogue", "search", or "ask". '
    "Never write SQL and never state or calculate a figure.\n"
    '- "catalogue": questions about a data product or table in the catalogue, such as its owner, steward, '
    "certification, quality score, refresh status or schedule, schema or columns, declared joins, lineage, "
    "or listing products in a domain or the catalogue.\n"
    '- "search": discovery questions asking where to find data, which products or tables hold a topic or column, '
    "or what a column name means.\n"
    '- "ask": questions asking for a governed metric figure, count, sum, average, ranking, comparison, breakdown, '
    "or the business or policy definition of a glossary term.\n"
    'Return JSON only: {"source": "catalogue" | "search" | "ask"}\n\nQuestion: '
)
DISCOVERY = re.compile(
    r"\bwhere (?:can i|do i|to) find\b|\bwhich (?:data )?(?:products?|tables?) "
    r"(?:holds?|contains?|have|has|stores?|includes?|mentions?|should i (?:use|query))\b|"
    r"\bwhat data do we have (?:about|on|for)\b|\bis there a (?:data )?(?:product|table) "
    r"(?:with|that (?:has|holds|contains))\b|\bwhat does the (?:column|field) .+ mean\b", re.I)
MUTATION = re.compile(
    r"(?:^|\b(?:please|and|then|can you|could you|will you|would you)\s+)"
    r"(?:certify|onboard|approve|grant|revoke|delete|drop|insert|update|alter|create|publish|sign off|accept|reject)\b", re.I)
METADATA = re.compile(
    r"\bwho (?:owns|runs|is the (?:product |business )?owner|is the (?:data )?steward|is the custodian|"
    r"is responsible|is accountable|looks after|maintains|manages|do i (?:ask|contact)|is the point of contact)\b|"
    r"\bwhen was .+ (?:last )?refreshed\b|\bhas .+ been (?:certified|signed off|approved)\b|"
    r"\bis .+ (?:reliable|signed off|updated (?:daily|weekly|monthly|quarterly|hourly))\b|\bcan (?:i|we) trust\b|"
    r"\bwhat(?:'s| is) the structure of\b|\bwhich (?:fields?|columns?|attributes?) are available\b|"
    r"\bhow up to date is\b|\bwhat (?:is|are) the sources? of .+ (?:product|table)\b|"
    r"\b(?:this data asset|this table|tables are in this domain)\b", re.I)
STOP = set(
    "a an the which what does do we have about on for in of can i where to find product products table tables "
    "data column columns field fields mean holds hold contains contain has stores store includes include mention "
    "mentions should use query addresses that is there with tell me show could would you please give asset assets "
    "catalogue catalog stored track tracks point information details share".split()
)


def tokens(text):
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w
            for w in re.findall(r"[^\W_]+", text.casefold()) if w not in STOP}


def named(question, products):
    q = " " + re.sub(r"\bpoint of contact\b", " ", re.sub(r"[_-]", " ", question.casefold())) + " "
    found = []
    for p in products:
        if any(re.search(r"(?<!\w)" + re.escape(value.casefold().replace("_", " ").replace("-", " ")) + r"(?!\w)", q)
               for value in (p["id"], p["name"], p["table"]) if value.casefold() not in {"product", "table", "data", "domain"}):
            found.append(p)
    if found and not re.search(r"\b(?:join|link|relate)\b", question, re.I):
        found = [p for p in found
                 if not any(p != other and p["name"].casefold() in other["name"].casefold() for other in found)]
    return found


def description(text):
    """Leave out numeric descriptive clauses when no numeric attribute was requested."""
    return " ".join(sentence for sentence in re.split(r"(?<=[.!?])\s+", text)
                    if not re.search(r"(?<![\w])\d+(?![\w])", sentence))


def catalogue_text(e, question, selected, visible, readable=None):
    q = question.casefold()
    join_visible = visible if readable is None else readable
    sections = []
    listing = bool(re.search(r"\b(?:list|products are|tables are|what products|what tables)\b", q))
    count = bool(re.search(r"\bhow many\b", q))
    if count and re.search(r"\b(?:products|tables)\b", q):
        return f"From the catalogue: {len(selected)} products in the requested view."
    for p in selected:
        parts = [f"{p['name']} ({p['id']})."]
        if re.search(r"\b(?:who|owner|steward|custodian|responsible|accountable|looks? after|point of contact|maintains?|manages?)\b", q):
            keys = [("product_owner", "Product owner"), ("business_owner", "Business owner"), ("steward", "Steward")]
            parts.append("; ".join(f"{label}: {p.get(key) or 'not recorded'}" for key, label in keys) + ".")
        elif re.search(r"\b(?:certified|certification|approved|reliable|signed off|trust|trusted)\b", q):
            parts.append("Recorded badges: " + (", ".join(p["badges"]) or "none; certification is not recorded") + ".")
        elif "quality" in q and "score" in q:
            parts.append(f"Quality score: {p['quality_score']}.")
        elif re.search(r"\b(?:refresh|refreshed|cadence|frequency|updated|up to date)\b", q):
            m = re.search(r"\brefreshed (?:daily|weekly|monthly|quarterly|hourly)\b", p["description"], re.I)
            cadence = f"Refresh cadence: {m[0]}." if m else "Refresh cadence is not separately recorded in the catalogue."
            parts.append(f"Last refresh: {p.get('last_refresh') or 'not recorded'}. {cadence}")
        elif re.search(r"\b(?:join|link|relate|lineage|upstream|downstream|sources? of)\b", q):
            joins = [f"{p['unit_id']}.{j['ft']}.{j['fc']} to {p['unit_id']}.{j['tt']}.{j['tc']}"
                     for j in e.g.unit_joins(p["unit_id"])
                     if p['table'] in (j['ft'], j['tt']) and
                     all(f"{p['unit_id']}.{j[k]}" in join_visible for k in ('ft', 'tt'))]
            parts.append("Declared joins: " + ("; ".join(joins) or "none recorded in scope") + ".")
            if re.search(r"\b(?:lineage|upstream|downstream|sources? of)\b", q):
                parts.append("Upstream: " + (", ".join(p["lineage"]["upstream"]) or "none") +
                             ". Downstream: " + (", ".join(p["lineage"]["downstream"]) or "none") + ".")
        elif re.search(r"\b(?:fields?|columns?|attributes?|schema|structure|types?)\b", q):
            if count:
                parts.append(f"Fields: {len(p['fields'])}.")
            else:
                types = bool(re.search(r"\b(?:data )?types?\b", q))
                parts.append("Fields: " + "; ".join(f["name"] + f" ({f['business_name']})" +
                             (f" {f['data_type']}" if types else "") +
                             (f": {description(f['description'])}" if "mean" in q else "") for f in p["fields"]) + ".")
        elif not listing:
            parts.append(description(p["description"]))
            parts.append("Fields: " + ", ".join(f["name"] for f in p["fields"]) + ".")
        sections.append(" ".join(parts))
    return "From the catalogue: " + "\n\n".join(sections)


def ask_sources(out):
    sources = [{"kind": "ask", "id": c["doc"], "title": c.get("title") or c["doc"],
                "unit": c.get("unit") or out.get("unit") or "", "why": c.get("why") or "Governed citation"}
               for c in out.get("citations") or [] if isinstance(c, dict) and c.get("doc")]
    known = {s["id"] for s in sources}
    for c in out.get("cite") or []:
        if isinstance(c, str) and c not in known:
            sources.append({"kind": "ask", "id": c, "title": c, "unit": out.get("unit") or "", "why": "Governed definition"})
            known.add(c)
    if out.get("column"):
        identifier = ".".join(str(out[k]) for k in ("unit", "column") if out.get(k))
        if identifier not in known:
            sources.append({"kind": "ask", "id": identifier, "title": out["column"],
                            "unit": out.get("unit") or "", "why": "Governed answer source"})
            known.add(identifier)
    for step in out.get("plan") or []:
        if isinstance(step, dict) and step.get("status") == "ok" and step.get("column"):
            identifier = ".".join(str(step[k]) for k in ("unit", "column") if step.get(k))
            if identifier not in known:
                sources.append({"kind": "ask", "id": identifier, "title": step["column"],
                                "unit": step.get("unit") or "", "why": "Governed answer source"})
                known.add(identifier)
    return sources


def rules_config(cfg):
    return cfg.with_stages(**{key: ("planner" if value == "model" else value.removesuffix("+model"))
                              for key, value in cfg.stages().items()})


def routing_model(c):
    llm = model_for("assist")
    if getattr(llm, "provider", None) == MOCK:
        llm = None
    if llm is not None:
        return llm
    return c.llm if hasattr(c.llm, "json") and getattr(c.llm, "provider", None) != MOCK else None


def route_question(llm, question: str) -> str:
    try:
        reply = llm.json(ROUTE_PROMPT + question, 200)
    except RequestDeadlineExceeded:
        raise
    except NotJSON:
        raise NoFallback(f"routing the question: {NOT_JSON}, and the assistant does not fall back to rules") from None
    except Exception as exc:  # noqa: BLE001
        detail = f"the model call failed ({exc if isinstance(exc, ExternalCallError) else type(exc).__name__})"
        raise NoFallback(f"routing the question: {detail}, and the assistant does not fall back to rules") from exc
    source = reply.get("source") if isinstance(reply, dict) else None
    if source not in SOURCES:
        raise NoFallback("routing the question: model reply named no valid source, and the assistant does not fall back to rules")
    return source


def regex_source(q: str, req) -> str:
    figure_cue = bool(FIGURE_INTENT.search(q) or FIGURE_FOLLOWUP.search(q))
    discovery = bool(DISCOVERY.search(q)) and not (
        figure_cue or re.search(r"\b(?:above|below|over|under|greater than|less than|more than|fewer than)\s+\d", q)
    )
    figure_overview = bool(re.search(r"\ban? overview of\b.*\bby\b(?!\s+(?:fields?|columns?|attributes?|owners?|stewards?|domains?|units?)\b)", q)
                           and req.term) or bool(re.search(r"\bwhat does .+ earn\b", q))
    metadata = (discovery or catalogue_question(q, req.terms_found) or (bool(METADATA.search(q)) and not figure_cue and "growth" not in q)) and not figure_overview
    return "search" if discovery else "catalogue" if metadata else "ask"


def search_matches(e, who, q, visible, units, readable, scope):
    terms = " ".join(w for w in re.findall(r"[\w]+", q) if w not in STOP and w not in e.UNIT_ORDER).strip(" ?.")
    if not terms:
        return terms, [], []
    hits = search.search(e, who, search.Search(query=terms, limit=100), products=scope.products).hits
    ids = {p["id"] for p in visible}
    hits = [h for h in hits if h.unit in (units or set(readable)) and
            (scope.products is None or h.id in ids or h.id.rsplit(".", 1)[0] in ids or any(v in ids for v in h.why.via))]
    wanted = tokens(terms)
    matches = []
    for p in visible:
        fields = [f for f in p["fields"] if wanted and wanted <= tokens(" ".join([f["name"], f["business_name"], f["description"], *f["terms"]]))]
        if fields or (wanted and wanted <= tokens(" ".join([p["name"], p["table"], p["description"]]))) or any(h.id == p["id"] or h.id.startswith(p["id"] + ".") for h in hits):
            matches.append((p, fields))
    return terms, hits, matches


@request_budget
def assist(c, profile, question, who, purpose, arm, cfg, products=None):
    e = c.e
    scope = ProductScope(e, products)
    req = parse(question, e.term_names, e)
    q = normalise_question(question).casefold()
    router = routing_model(c)
    available = router is not None or c.llm is not None
    source = "catalogue"

    def finish(out, sources=None):
        base = {"question": question, "principal": who, "purpose": purpose, **scope.metadata(),
                "figure": None, "figures": [], **out}
        base = worded(base, e)
        return {**base, "source": source, "sources": sources or [], "assistant_available": available,
                "notice": "" if available else NOTICE}

    if not e.g.may(who, "querying"):
        return finish({"behaviour": "refuse", "reason": f"{who} is not a querying identity."})
    checked = scope.check(who, req.units)
    denied = scope.denied_units(who, req.units)
    if MUTATION.search(q):
        if denied:
            return finish({**(checked or {}), "behaviour": "refuse", "denied_warehouses": denied,
                           "reason": "The requested catalogue is not readable. No warehouse was queried."})
        if checked:
            return finish(checked)
        reason = "The assistant is read-only; it cannot perform changes or grant access."
        return finish({"behaviour": "abstain", "reason": reason, "answer_text": f"Abstained: {reason}", "phrased_by": "template"})
    source = route_question(router, question) if router is not None else regex_source(q, req)
    if source == "ask":
        out = ask(c, profile, question, who, purpose, arm, cfg if c.llm is not None else rules_config(cfg), products=products)
        return {**out, "source": "ask", "sources": ask_sources(out), "assistant_available": available,
                "notice": "" if available else NOTICE}
    if denied:
        return finish({**(checked or {}), "behaviour": "refuse", "denied_warehouses": denied,
                       "reason": "The requested catalogue is not readable. No warehouse was queried."})
    if checked:
        return finish(checked)
    readable = sorted(e.g.readable_units(who))
    all_products = [p.model_dump() for p in catalogue.get(e.profile, readable).products]
    for p in all_products:
        p["id"] = f"{p['unit_id']}.{p['table']}"
    domains = set()
    units = set(req.units)
    for p in all_products:
        for key in ("domain", "community"):
            if p[key] and re.search(r"(?<!\w)" + re.escape(p[key].casefold()) + r"(?!\w)", q):
                domains.add(p[key])
                units.add(p["unit_id"])
    unknown_domain = re.search(r"\b(?:products|tables) (?:are )?in (.+?)[?.!]*$", q)
    if unknown_domain and not units and not re.fullmatch(r"(?:the )?(?:catalogue|catalog|this domain|this unit|selected domain)", unknown_domain[1].strip(" .?!")):
        return finish({"behaviour": "abstain", "reason": "The requested product is not in the readable catalogue."})
    candidates = [p for p in all_products if (not units or p["unit_id"] in units) and
                  (not domains or p["domain"] in domains or p["community"] in domains)]
    mentioned = named(q, candidates)
    qualified = set(re.findall(r"\b\w+\.\w+\b", q))
    scoped_names = {p["name"].casefold() for p in mentioned if scope.contains(p["id"])}
    mentioned = [p for p in mentioned if scope.contains(p["id"]) or p["id"].casefold() in qualified
                 or p["name"].casefold() not in scoped_names]
    visible = [p for p in candidates if scope.contains(p["id"])] or candidates
    listing = bool(re.search(r"\b(?:list|what products|what tables|products are|tables are|how many (?:products|tables))\b", q))
    if source == "search":
        terms, hits, matches = search_matches(e, who, q, visible, units, readable, scope)
        if not hits and not matches and scope.products is not None:
            terms, hits, matches = search_matches(e, who, q, candidates, units, readable, ProductScope(e, None))
        if not terms and not listing:
            return finish({"behaviour": "abstain", "reason": "No search topic was specified."})
        if not matches and not hits and listing:
            matches = [(p, []) for p in visible]
        sources = [{"kind": "search", "id": h.id, "title": h.title, "unit": h.unit, "why": h.why.text} for h in hits]
        sources += [{"kind": "catalogue", "id": p["id"], "title": p["name"], "unit": p["unit_id"],
                     "why": "Matching catalogue fields or description"} for p, _ in matches]
        if not sources:
            return finish({"behaviour": "abstain", "reason": "No matching source is in the readable catalogue or Search."})
        sections = [f"{p['name']} ({p['id']}): {description(p['description'])}" +
                    (" Fields: " + "; ".join(f"{f['name']} ({f['business_name']}): {description(f['description'])}" for f in fields) if fields else "")
                    for p, fields in matches]
        if not sections:
            sections = [f"{h.title} ({h.id})" for h in hits]
        text = "From Search and the readable catalogue: " + "\n\n".join(sections)
        return finish({"behaviour": "search", "answer_text": Hidden.of(e, who).withheld(text), "phrased_by": "template"}, sources)
    implicit = bool(re.search(r"\b(?:this|selected|current) (?:data asset|product|table|domain)\b", q))
    selected = mentioned or visible if listing or implicit else mentioned
    if qualified - {p["id"].casefold() for p in selected}:
        selected = []
    if not selected:
        return finish({"behaviour": "abstain", "reason": "The requested product is not in the readable catalogue."})
    text = catalogue_text(e, q, selected, {p["id"] for p in visible},
                          {p["id"] for p in all_products} if scope.products is not None else None)
    return finish({"behaviour": "catalogue", "answer_text": Hidden.of(e, who).withheld(text), "phrased_by": "template"},
                  [{"kind": "catalogue", "id": p["id"], "title": p["name"], "unit": p["unit_id"], "why": "Released catalogue metadata"} for p in selected])
