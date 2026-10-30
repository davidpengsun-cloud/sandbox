"""One question, one governed answer: figures from the warehouse and the documents that back them.

A question of one shape: one customer group, optionally narrowed, and measures
over it. The README's worked example asks how many of a unit's active customers
in a month raised a complaint that month, and their average balance and fee
income.

Four stages, and the model is used in two of them at most:

  understand  rules parse the question; with a model configured, the model may
              add glossary terms the rules missed (never a term outside it), and
              place a period only where the rules found a wholly unread period
  retrieve    vector search over chunks the principal may read, for the purpose
              stated, then the graph: which documents define each term, which
              are superseded, uncertified, or held back by the privacy gate
  traverse    graph templates: certified column per term, join path to the
              unit's customer key, access; the planner builds one SQL query;
              the gate (kge/pipeline/sqlguard.py) checks it and EXPLAIN dry-runs it
              before it runs. Only if the planner cannot build the query, and a
              model is configured, does the model write it, and the same gate
              checks what the model wrote
  answer      the figures and citations are fixed before any prose is written;
              the model may phrase them, and a sentence carrying a number that
              is not in the result is thrown away for the template

Code decides access, certification and which SQL runs. The model interprets the
question and phrases the answer. Questions that are not of this shape (one
cohort, optional narrowing, measures over it) go to Engine.answer unchanged.

Which stages use the model is the run config's to say (kge/services/ask/engine/config.py): by default
every stage that can, if a model is configured, and none if not. A config that
asks for the model with none configured is refused, never answered by rules.
The mock (MODEL_PROVIDER=mock) is not a model here: it answers only the
reference agent's prompts, so /ask runs on rules under it.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, warehouse_guard
from kge.adapters.deadline import RequestDeadlineExceeded
from kge.services.ask.engine.core import MODEL, PRESETS, STAGES, Engine, RunConfig, pair, parse
from kge.services.ask.engine.question import COMMON, OUTSIDE_PACK, content_words, grounded
from kge.services.ask.engine.model_period import read as read_model_period, validate as validate_model_period
from kge.pipeline.sqlguard import build_allowlist
from kge.ports import AUTHORED
from kge.adapters.model import MOCK, NOT_JSON, NotJSON, no_fallback

PLANNER = "cohort-v1"
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
        return dt.date.fromisoformat(period.value).strftime("%B %Y")
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
        self.llm = None if getattr(llm, "provider", None) == MOCK else llm

    def default_config(self, arm: str = "graph") -> RunConfig:
        """The arm's preset with the model in every stage that can use one, if a model
        is configured; the preset itself if not. This is what /ask did before the run
        config existed."""
        cfg = PRESETS[arm]
        if self.llm is None:
            return cfg
        return cfg.with_stages(**{s: v + MODEL for s, v in cfg.stages().items() if v + MODEL in STAGES[s]})

    def ask(self, question: str, principal: str, purpose: str = "analysis", config: RunConfig | None = None) -> dict:
        """The governed answer (governed), each column it names by its business name (Engine.business)."""
        return self.e.business(self.governed(question, principal, purpose, config))

    def governed(self, question: str, principal: str, purpose: str = "analysis", config: RunConfig | None = None) -> dict:
        """The governed answer. A question of another shape gets the rule-based answer, on the same config less the
        model stages it does not have. Model-named terms form a cohort only when the question shares content with
        those terms' names or aliases. Only terms retained by the cohort can ground it. Other unmatched questions need
        a definition grounded in a citable passage. A cohort lives in one unit: across units it is refused, or, where only a
        certified join links them, not answered here."""
        cfg = self.e.stamped(config or self.default_config())
        bad = cfg.problems("ask", has_model=self.llm is not None)
        if bad:
            raise ValueError("; ".join(bad))
        runtrace.use(cfg, self.llm if cfg.model_stages() else None)
        runtrace.stage("understand")
        req = parse(question, self.e.term_names, self.e)
        if req.problem and (not cfg.uses_model("resolution") or not req.model_period_allowed):
            rules = cfg.with_stages(**{s: getattr(cfg, s).removesuffix(MODEL) for s in cfg.model_stages()})
            return {**self.e.answer(question, principal, purpose, config=rules, _request=req), "combined": False}
        terms, resolved_by = [t for t in req.terms_found if t in self.e.term_names], "rules"
        period_problem = None
        if cfg.uses_model("resolution"):
            extra, model_period = self.understand(question, terms)
            if req.model_period_allowed:
                period, model_period_problem = read_model_period(model_period)
                if period is not None:
                    req = parse(question, self.e.term_names, self.e, period=period)
                else:
                    req.period_by = "model"
                    period_problem = " ".join(filter(None, [req.problem, self.e.semantics.unplaced([question]), model_period_problem]))
            proposed = cohort(req, {t: self.g.implementations(t) for t in terms + extra})
            retained = proposed[0] if proposed else []
            vocabulary = " ".join([*retained, *[a for a, t in self.e.ALIASES.items() if t in retained]])
            words = content_words(vocabulary, self.e) - COMMON - {"new", "open", "test", "total", "number"}
            if extra and content_words(question, self.e) & words:
                terms, resolved_by = terms + extra, "rules+model"
        runtrace.stage("resolve")
        impls = {t: self.g.implementations(t) for t in terms}
        shape = cohort(req, impls)
        if reason := period_problem or self.e.request_problem(req, terms):
            allowed = self.g.may(principal, "querying")
            return {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label,
                    "combined": False, "behaviour": ("abstain_with_reason" if period_problem else "abstain") if allowed else "refuse",
                    "reason": reason if allowed else f"{principal} is not a querying identity.",
                    "trace": [{"step": "understand", "term": req.term, "terms": terms, "resolved_by": resolved_by,
                               "period": req.period.label() if req.period else None, "period_by": req.period_by}],
                    "versions": {"parser": "rules-v1", "model": self.llm.name if cfg.uses_model("resolution") else "none",
                                 "embedding_model": self.g.embedder.name}}
        if req.term is None and req.special is None and shape is None:
            out = {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label,
                   "combined": False,
                   "versions": {"parser": "rules-v1", "model": self.llm.name if cfg.uses_model("resolution") else "none",
                                "embedding_model": self.g.embedder.name},
                   "trace": [{"step": "understand", "term": None, "terms": terms,
                              "resolved_by": "rules+model" if cfg.uses_model("resolution") else "rules", "period_by": req.period_by}]}
            runtrace.stage("access")
            if not self.g.may(principal, "querying"):
                return {**out, "behaviour": "refuse", "reason": f"{principal} is not a querying identity."}
            if not req.definitional:
                vocabulary = [{"excerpt": " ".join([*self.e.term_names, *self.e.ALIASES])}]
                in_scope = (grounded(question, vocabulary, self.e)
                            or grounded(question, self.g.retrieve(question, principal, purpose, k=3)["exact"], self.e))
                reason = "No glossary term recognised in the question." if in_scope else OUTSIDE_PACK
                return {**out, "behaviour": "abstain", "reason": reason}
            runtrace.stage("retrieve")
            return {**out, **self.e.definition_answer(req, principal, purpose, cfg)}
        if shape is None:
            rules = cfg.with_stages(**{s: getattr(cfg, s).removesuffix(MODEL) for s in cfg.model_stages()})
            answer = self.e.answer(question, principal, purpose, config=rules, _request=req)
            if cfg.uses_model("resolution") and req.period_by == "none" and not req.definitional and answer["behaviour"] == "figure":
                note = "no period asked: every month the filters keep"
                if note not in answer.setdefault("notes", []):
                    answer["notes"].insert(0, note)
            if req.period_by == "model":
                answer["versions"].update(parser="rules-v1+" + self.llm.name, model=self.llm.name)
                runtrace.use(cfg, self.llm)
            return {**answer, "combined": False}
        shaped, cohort_terms, measure_terms = shape

        used = self.llm.name if cfg.model_stages() else "none"
        trace = [{"step": "understand", "term": cohort_terms[0], "terms": shaped, "resolved_by": resolved_by,
                  "units": req.units or "unspecified", "period": req.period.label() if req.period else None, "period_by": req.period_by}]
        out = {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label, "combined": True, "trace": trace,
               "versions": {"parser": "rules-v1" + ("+" + self.llm.name if cfg.uses_model("resolution") else ""), "planner": PLANNER,
                            "model": used, "embedding_model": self.g.embedder.name}}
        runtrace.stage("access")
        if not self.g.may(principal, "querying"):
            return {**out, "behaviour": "refuse", "reason": f"{principal} is not a querying identity."}
        runtrace.stage("resolve")

        if len(req.units) > 1:
            refused = across(self.e, req.units)
            trace.append({"step": "traverse", "no_join": refused["facts"], "certified_join": refused["certified"]})
            return {**out, "behaviour": refused["behaviour"], "joins_refused": [f["pair"] for f in refused["facts"]], "reason": refused["reason"],
                    **({"fact": refused["fact"]} if refused["fact"] else {}),
                    **({"certified_joins": refused["certified"]} if refused["certified"] else {})}
        if req.units:
            unit = req.units[0]
        else:
            both = [u for u in self.e.w.units if all(any(i["unit"] == u for i in impls[t]) for t in shaped)]
            if len(both) != 1:
                return {**out, "behaviour": "abstain", "reason": "Name the business unit: these terms are certified in " + (", ".join(both) or "no single unit") + "."}
            unit = both[0]
            trace[0]["units"] = f"{unit} (the only unit where every term is certified)"

        runtrace.stage("access")
        access = self.g.join_access(principal, [unit])
        trace.append({"step": "access", "rule": "read", "principal": principal, "units": [unit], "denied": access["denied_warehouses"], "allowed": access["allowed"]})
        if not access["allowed"]:
            return {**out, "behaviour": "refuse", "denied_warehouses": [unit], "sql_run": [],
                    "reason": f"{principal} may not read {unit}. No warehouse was queried and no {self.e.UNIT_LABEL[unit]} document was retrieved."}

        runtrace.stage("resolve")
        chosen = {t: next((i for i in impls[t] if i["unit"] == unit), None) for t in shaped}
        missing = [t for t, i in chosen.items() if i is None]
        if missing:
            return {**out, "behaviour": "abstain", "reason": f"Not certified in {self.e.UNIT_LABEL[unit]}: {', '.join(missing)}. The answer would rest on a guess."}
        trace.append({"step": "map", "columns": [{"term": t, "column": f"{i['tbl']}.{i['col']}", "role": role(i), "certified_by": i["certified_by"],
                                                  "certified_on": i["certified_on"]} for t, i in chosen.items()]})

        runtrace.stage("plan")
        spec = self.spec(question, [(t, chosen[t]) for t in cohort_terms], [(t, chosen[t]) for t in measure_terms])
        if req.period_by == "model":
            reason = validate_model_period(self.e, req.period, [(unit, f["impl"]["tbl"], f["kind"]) for f in spec], cfg)
            if reason:
                return {**out, "behaviour": "abstain_with_reason", "reason": reason}
        allow = build_allowlist(self.g, unit, [(i["tbl"], i["col"]) for i in chosen.values()], self.e.RESTRICTED_COLUMNS)
        plan = self.plan(unit, req.period, spec, cfg)
        source, attempts, dialect = PLANNER, [], AUTHORED
        if plan.get("sql"):
            runtrace.stage("gate")
            guard = warehouse_guard(self.e.w.gate(unit, plan["sql"], allow, dialect=AUTHORED))
            attempts.append({"source": PLANNER, "sql": plan["sql"], **guard})
        else:
            guard = {"passed": False, "problems": [plan["reason"]]}
            attempts.append({"source": PLANNER, "sql": None, **guard})
        sql = plan.get("sql")
        if not guard["passed"] and cfg.uses_model("sql_source"):
            source, dialect = "model:" + self.llm.name, self.e.w.sql_dialect
            sql, guard, generated = self.generate(question, unit, req.period, spec, allow, cfg)
            attempts += generated
        trace.append({"step": "plan", "source": source, "join_paths": plan.get("paths", []), "notes": plan.get("notes", []), "attempts": attempts})
        if not guard["passed"]:
            why = "; ".join(guard["problems"])
            hint = ("" if cfg.uses_model("sql_source") else
                    " A model can be configured to try a generated query; it passes the same gate." if self.llm is None else
                    " With sql_source=planner+model the model may try a generated query; it passes the same gate.")
            return {**out, "behaviour": "abstain", "unit": unit, "guard": guard, "attempts": attempts,
                    "reason": f"No query passed the gate ({why}).{hint}"}

        runtrace.stage("execute")
        try:
            row = self.e.w.row(unit, sql, dialect)
        except RequestDeadlineExceeded:
            raise
        except Exception:
            return {**out, "behaviour": "abstain", "reason": WAREHOUSE_PROBLEM}
        figures = []
        for f, v in zip(spec, row):
            val = float(v) if v is not None else None
            figures.append({**{k: f[k] for k in ("id", "label", "term", "kind", "column", "certified_by", "certified_on", "currency")}, "value": val})
        trace.append({"step": "execute", "unit": unit, "rows": 1, "values": {f["id"]: f["value"] for f in figures}})

        runtrace.stage("retrieve")
        docs = self.sources(question, shaped, principal, purpose, unit)
        trace.append({"step": "retrieve", "vector_hits": docs.pop("vector_hits"), "cited": [c["doc"] for c in docs["citations"]],
                      "not_cited": [n["doc"] for n in docs["not_cited"]], "withheld": [w["doc"] for w in docs["withheld"]]})
        runtrace.stage("bind")
        for f in figures:
            f["cite"] = next((c["doc"] for c in docs["citations"] if f["term"] in c["terms"]), None)
        number_citations(docs["citations"], figures)

        result = {**out, "behaviour": "combined", "unit": unit, "unit_label": self.e.UNIT_LABEL[unit], "period": period_words(req.period),
                  "figures": figures, "sql": sql, "sql_source": source, "guard": guard, **docs}
        runtrace.stage("render")
        text, phrased_by = self.phrase(result, cfg)
        trace.append({"step": "answer", "phrased_by": phrased_by})
        return {**result, "answer_text": text, "phrased_by": phrased_by}

    def understand(self, question: str, found: list[str]) -> tuple[list[str], object]:
        """Glossary terms and an optional period read in one call. Anything not in the glossary is
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
        except Exception as exc:  # noqa: BLE001
            no_fallback("reading the question", exc)
            return [], None
        names = names if isinstance(names, list) else []
        return [t for t in names if isinstance(t, str) and t in self.e.term_names and t not in found], reply.get("period")

    def spec(self, question: str, cohort: list[tuple[str, dict]], measures: list[tuple[str, dict]]) -> list[dict]:
        """What the query must return, in order: the cohort counts, then each measure over the final cohort."""
        cohort = sorted(cohort, key=lambda ti: role(ti[1]) != "flag")
        out = []
        for n, (t, i) in enumerate(cohort, 1):
            label = f"{t}" if n == 1 else f"of those, with a {t}"
            out.append({"id": f"c{n}", "label": label, "term": t, "kind": "count", "role": role(i), "impl": i})
        for n, (t, i) in enumerate(measures, 1):
            agg = measure_agg(t, question)
            out.append({"id": f"m{n}", "label": t, "term": t, "kind": agg, "role": "measure", "impl": i})
        for f in out:
            i = f["impl"]
            f.update(column=f"{i['tbl']}.{i['col']}", certified_by=i["certified_by"], certified_on=i["certified_on"], currency=i.get("currency") or "")
        return out

    def key_table(self, unit: str) -> tuple[str, str]:
        key = self.e.UNIT_KEY[unit]
        rows = self.g.q("MATCH (a:Tbl)-[j:JOINS_TABLE]->(b:Tbl) WHERE b.unit = $u AND j.to_column = $k RETURN DISTINCT b.name AS t", {"u": unit, "k": key})
        return (rows[0]["t"] if rows else ""), key

    def key_of(self, unit: str, table: str) -> tuple[str, list[str], list[str]] | None:
        """The expression that gives a row's customer key, the joins it needs, and the path used."""
        kt, key = self.key_table(unit)
        if table == kt:
            return f"{table}.{key}", [], []
        path = self.g.join_path(unit, table, kt)
        if not path:
            return None
        joins, used, prev = [], [], table
        for hop in path:
            fwd = hop["from_table"] == prev
            a, b = (hop["from_table"], hop["to_table"]) if fwd else (hop["to_table"], hop["from_table"])
            ac, bc = (hop["from_column"], hop["to_column"]) if fwd else (hop["to_column"], hop["from_column"])
            used.append(f"{a}.{ac} -> {b}.{bc}")
            if b == kt and bc == key:
                return f"{a}.{ac}", joins, used
            joins.append(f"JOIN {b} ON {a}.{ac} = {b}.{bc}")
            prev = b
        return f"{kt}.{key}", joins, used

    def plan(self, unit: str, period, spec: list[dict], cfg: RunConfig = PRESETS["graph"]) -> dict:
        ctes, paths, notes, prev = [], [], [], None
        for f in spec:
            i = f["impl"]
            table = i["tbl"]
            k = self.key_of(unit, table)
            if k is None:
                return {"reason": f"no declared join path from {table} to the {unit} customer key"}
            key, joins, used = k
            paths += [p for p in used if p not in paths]
            pc, pj, pn = self.e.period_clause(unit, table, period, cfg, "count" if f["kind"] == "count" else f["kind"])
            if pc is None:
                return {"reason": f"{f['term']}: {pn}"}
            if pn:
                notes.append(f"{f['term']}: {pn}")
            where = [pc] if pc else []
            if f["role"] == "flag":
                where.append(f"{table}.{i['col']}")
            if prev:
                where.append(f"{key} IN (SELECT k FROM {prev})")
            tail = "".join(" " + j for j in pj + joins) + (" WHERE " + " AND ".join(where) if where else "")
            if f["kind"] == "count":
                ctes.append(f"{f['id']} AS (SELECT DISTINCT {key} AS k FROM {table}{tail})")
                prev = f["id"]
            else:
                agg = "avg" if f["kind"] == "avg" else "sum"
                ctes.append(f"{f['id']} AS (SELECT round({agg}({table}.{i['col']}), 2) AS v FROM {table}{tail})")
        cols = [f"(SELECT count(*) FROM {f['id']}) AS {f['id']}_count" if f["kind"] == "count" else f"(SELECT v FROM {f['id']}) AS {f['id']}_value" for f in spec]
        return {"sql": "WITH " + ",\n     ".join(ctes) + "\nSELECT " + ",\n       ".join(cols), "paths": paths, "notes": notes}

    def generate(self, question: str, unit: str, period, spec: list[dict], allow,
                 cfg: RunConfig = PRESETS["graph"]) -> tuple[str | None, dict, list[dict]]:
        """Text to SQL, for a shape the planner cannot build. Two tries; every try goes through the gate. A failed model
        call is recorded by its exception's type alone, since a transport's message can quote the key. On the pilot's
        cloud path (gcp_native) a failed call or a reply that is not JSON stops the run (NoFallback), as on every route."""
        hints = []
        for f in spec:
            pc, _, _ = self.e.period_clause(unit, f["impl"]["tbl"], period, cfg, f["kind"], self.e.w.sql_dialect)
            if pc:
                hints.append(f"  {f['impl']['tbl']}: {pc}")
        wanted = "\n".join(f"  {f['id']}_{'count' if f['kind'] == 'count' else 'value'}: "
                           + (f"number of distinct customers in {f['label']}" if f["kind"] == "count" else f"{f['kind']} of {f['column']} over the last group, rounded to 2")
                           for f in spec)
        base = (f"Write one {self.e.w.query_dialect} SQL query for the {self.e.UNIT_LABEL[unit]} warehouse. SELECT only (WITH is allowed). Use only the columns and "
                f"joins listed. Return exactly one row with these columns, in this order:\n{wanted}\nEach group narrows the one before it, "
                f"by the customer key {self.e.UNIT_KEY[unit]}.\n{allow.as_prompt()}\nPeriod filters:\n" + "\n".join(hints)
                + f"\n\nQuestion: {question}\nReturn JSON only: {{\"sql\": str}}")
        attempts, prompt = [], base
        for _ in range(2):
            runtrace.stage("plan")
            try:
                sql = str(self.llm.json(prompt, 900).get("sql", "")).strip().rstrip(";")
            except NotJSON as exc:
                no_fallback("writing the query", NOT_JSON)
                attempts.append({"source": "model:" + self.llm.name, "sql": None, "passed": False, "problems": [f"model reply was not JSON: {str(exc)[:120]}"]})
                break
            except Exception as exc:  # noqa: BLE001
                no_fallback("writing the query", exc)
                attempts.append({"source": "model:" + self.llm.name, "sql": None, "passed": False, "problems": [f"model call failed ({type(exc).__name__})"]})
                break
            runtrace.stage("gate")
            guard = warehouse_guard(self.e.w.gate(unit, sql, allow, dialect=self.e.w.sql_dialect))
            attempts.append({"source": "model:" + self.llm.name, "sql": sql, **guard})
            if guard["passed"]:
                return sql, guard, attempts
            prompt = base + "\n\nYour last query was rejected: " + "; ".join(guard["problems"]) + ". Fix it."
        return None, {"passed": False, "problems": attempts[-1]["problems"]}, attempts

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
        for t in terms:
            for u in self.g.uncertified_mentions(t, limit=8):
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
