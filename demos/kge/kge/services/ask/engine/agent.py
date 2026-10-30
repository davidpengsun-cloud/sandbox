"""The reference agent: one text-to-SQL agent, run with the graph off and on.

    python3 -m kge.services.ask.engine.agent [--profile <id>] --arm both "<question>"

This is the agent the A/B test runs: ours, built for the demo's sample
text-to-SQL use case on its four warehouses. Both arms are the same agent, with
the same model, the same prompt templates and the same seed. Only the context
block in the prompt differs:

  control    graph off. The warehouse schema of the units the principal may
             read, from each warehouse's own catalogue (tables, columns and
             types, declared keys), and a plain vector search over chunks with
             no tier filter. No term resolution.
  treatment  graph on. The graph's context package: each term the question
             names, with its definition and the documents that define it by
             tier; its certified columns by role (measure, filter, period,
             keys); the declared joins of each unit in play, the only ones
             that exist; the no-join pairs and whether parts may be added, and that
             two different measures never are; the scope (the units this
             principal may read, and those the question needs that it may
             not); for one customer group with figures over
             it, /ask's shape, what the graph decides (no figure: one figure
             of the group is not the answer); what not to use (restricted
             columns, definitions not in force for the period, documents not
             AI-ready); bronze references; and provenance. A term that resolves
             to a context of the query-semantics layer (kge/semantics/query.py) is
             given as that context instead of columns by role: its measure,
             joins and conditions, the period through its calendar rollup, and,
             across units, the certified join, or what the graph decides (a
             month not yet released, a pair of units with a no-join fact,
             restricted personal data asked for across units).
             Never a golden question, its figure or its SQL.

The context workflow (kge/services/ask/engine/context.py), a LangGraph StateGraph with fixed edges,
builds the package, a node per step, each a method below; POST /context and the
MCP tool get_context (kge/app.py) give it to any agent, this one among them.

The agent loop is a LangGraph StateGraph with fixed edges too (loop), a node
per step, each today's code. With a model (or the mock): context -> plan ->
check -> run -> answer; on rules: rules (the planner) -> evidence. The agent
gets its package as a downstream app does, on both arms: POST /context with the
asking principal's own token, at CONTEXT_URL, or with none set, through the
API's own route, token check and JSON in this process (kge/services/ask/engine/context.py, post).

After the context everything is shared, the same code for both arms. The reply
is checked against the contract; every query goes through the gate
(kge/pipeline/sqlguard.py) as a figure, which must be an aggregate of a column the arm's
context gives as a measure, never a constant or arithmetic on one, with one
retry carrying the gate's reasons; the queries run on their units' warehouses;
each figure names the column it measures and is bound to the citation that
defines it (failing that, one about its unit, then one about no unit); the
template renders the answer; and any wording the model offers is used only if
every number in it is in the result, every [n] names a citation and every
document it names is one its context gave and did not withhold. A reply that
cites, with nothing left it may cite, is an abstention. The graph's
decision on a customer group holds whatever the reply: no query of it runs.

The contract, one versioned prompt template per task (PROMPTS):
  sql     from the question and the context the model returns one JSON object,
          {"behaviour", "sql": {unit: query}, "citations": [n], "answer_text"},
          and may add "columns": {unit: "table.column"}, "joins_refused":
          ["unit-unit"] and "reason". A reply that breaks it is an abstention.
  phrase  from the figures and the citations the model words the answer:
          {"answer_text"}.
A model that refuses (a stop_reason of refusal) gives no answer: the plan is an
abstention, the wording the template's, and nothing it said is read. Each call
has its own output budget (MAX_TOKENS): a model that thinks spends part of it on
thinking before its JSON, and 400 left a phrase too little room, so it fell back
to the template (issue #69). The mock ignores the budget.

MODEL_PROVIDER=mock runs the model path on the mock (kge/adapters/model/mock.py):
offline, deterministic, answering the contract from the prompt alone, with
adversarial modes the seam must catch. Its answers and traces say mock.

With no model configured (MODEL_PROVIDER none or unset) the agent runs on
rules: the planner and the template, through Engine.answer, on the arm's run
config (AGENT_ARMS in kge/services/ask/engine/config.py). The control plans as the schema arm does
but with no term resolution at all, guessing a column from the question's own
words against the warehouse schema, and cites what plain retrieval returns; the
treatment plans from the graph's certified mappings, joins and query semantics,
and cites by tier; a customer group it decides as the package does, with no
query. Each figure the planner runs then goes through the evidence
step the model path uses: the arm's context, built as a model would be given
it, and bind, so the treatment binds a figure to the tier rule's citation for
its column's certified term and the control to what plain search returned.
Such a run is labelled rules, never a model's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
from decimal import Decimal
from typing import TypedDict

import sqlglot
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlglot import exp

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.combined import across, cohort, fmt, names, numbers, role
from kge.services.ask.engine.context import ContextError, State, post, service_url, workflow
from kge.services.ask.engine.core import AGENT_ARMS, CITE_AS, MODEL, Engine, RunConfig, asked_window, in_force, pair, parse
from kge.services.ask.engine.question import OUTSIDE_PACK, grounded
from kge.services.identity import Identities
from kge.semantics.query import Asked
from kge.pipeline.sqlguard import build_allowlist, gate, schema_allowlist
from kge.pipeline.trace import transient_failure
from kge.adapters.deadline import RequestDeadlineExceeded
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, warehouse_guard
from kge.adapters.model import NOT_JSON, NotJSON, Refused, no_fallback, request_budget

PROMPTS = {"sql": "agent-sql-v3", "phrase": "agent-phrase-v1"}
MAX_TOKENS = {"sql": 1200, "phrase": 800}
BEHAVIOURS = ("figure", "sum_with_verdict", "parts_with_reason", "refuse", "abstain", "abstain_with_reason", "abstain_route_steward",
              "cite_certified")
SEARCH_K = 5
CONTEXT_OPEN, CONTEXT_CLOSE = "<context>", "</context>"
MARKER = re.compile(r"\[(\d+)\]")
DOC_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
IDS_BUILT = threading.Lock()

SQL_PROMPT = """You are a text-to-SQL agent for a bank's {business_units}. Each unit has
its own {dialect} warehouse. Answer the analyst's question from the context below and nothing else.

Reply with one JSON object and nothing else. Its keys:
  "behaviour"      one of:
                     figure                 one number from one unit's warehouse
                     sum_with_verdict       numbers from several units that may be added: the answer is their total
                     parts_with_reason      numbers from several units that must not be added: give each, and say why
                     refuse                 the question asks for what may not be given or done: say why
                     abstain                the context cannot support an answer: say why
                     abstain_with_reason    the period asked is not yet released, or not booked: say which and when
                     abstain_route_steward  the question names something that is not yet a defined term: say who decides
                     cite_certified         the answer is in the sources: cite them
  "sql"            {"<unit>": "<one {dialect} SELECT on that unit's warehouse, returning one row with one number>"};
                   {} for a behaviour that runs no query
  "columns"        {"<unit>": "<table.column the number measures>"}
  "citations"      [the numbers of the sources the answer rests on]
  "joins_refused"  ["<unit>-<unit>" for each pair of units whose data must not be joined]
  "reason"         why, in one sentence, for anything but a figure
  "answer_text"    for a reply that runs no query: the answer in one or two sentences, with a [n] marker for each source

A query reads one unit's warehouse only, or two, through a declared certified join: then it names every table with
its unit, <unit>.<table>, and is keyed by the unit whose figure it is. Never a table of another unit otherwise. Name
every column: no SELECT *. Its one value is an aggregate of one column of the unit's tables, inside round or not:
round(sum(table.column), 2), count(table.column), avg, min or max; or count(*) with a WHERE on a flag or filter column.
Never a constant, and nothing added to or multiplied into the aggregate. An EXISTS or NOT EXISTS the context gives is
the one subquery a query may hold.
"""

RETRY = """

Your last reply was refused by the SQL gate, and nothing ran:
{reasons}
Reply again with the whole JSON object."""

PHRASE_PROMPT = """Write the answer to the analyst's question in one to three plain sentences, from these facts and nothing
else. Use no number that is not in the facts. Keep each figure's [n] citation marker.

Reply with one JSON object and nothing else: {{"answer_text": "<the answer>"}}

Facts: {facts}

Question: {question}"""


NUMBERS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def sql_prompt(units: list[str], dialect: str) -> str:
    """The SQL task's prompt for the client's units, in the pack's order: how many, and their ids ("two business units:
    alpha and beta"), as the code once wrote them in for the demo's client, word for word; and the SQL dialect the
    warehouses take a query in (WarehouseSet.query_dialect)."""
    n = NUMBERS[len(units) - 1] if 0 < len(units) <= len(NUMBERS) else str(len(units))
    named = units[0] if len(units) == 1 else ", ".join(units[:-1]) + " and " + units[-1]
    return (SQL_PROMPT.replace("{business_units}", f"{n} business unit{'' if len(units) == 1 else 's'}: {named}")
            .replace("{dialect}", dialect))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ContractError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def runs_sql(units: list[str]) -> dict[str, tuple[int, int]]:
    """The behaviours that run queries, each with the fewest and most units' queries it runs: one, or two to every unit."""
    return {"figure": (1, 1), "sum_with_verdict": (2, len(units)), "parts_with_reason": (2, len(units))}


def read_reply(reply: object, domain) -> dict:
    """The model's plan, checked against the contract and normalised. Raises ContractError naming every problem.
    domain: the Engine, or anything that carries the client's domain as it does (UNIT_ORDER here: the units a reply
    may name, in the pack's order)."""
    units = domain.UNIT_ORDER
    queries = runs_sql(units)
    if not isinstance(reply, dict):
        raise ContractError(["the reply is not a JSON object"])
    problems: list[str] = []
    behaviour = reply.get("behaviour")
    if behaviour not in BEHAVIOURS:
        problems.append(f"behaviour must be one of {', '.join(BEHAVIOURS)}, not {behaviour!r}")
    sql = reply.get("sql") or {}
    if not isinstance(sql, dict) or not all(isinstance(q, str) and q.strip() for q in sql.values()):
        problems.append("sql must map a unit to one query")
        sql = {}
    for unit in sql:
        if unit not in units:
            problems.append(f"sql names no unit {unit}; the units are {', '.join(units)}")
    if behaviour in queries:
        lo, hi = queries[behaviour]
        if behaviour == "figure" and len(sql) != 1:
            problems.append(f"a figure runs one unit's query, not {len(sql)}")
        elif not lo <= len(sql) <= hi:
            problems.append(f"a {behaviour} reply runs two or more units' queries, not {len(sql)}")
    elif behaviour in BEHAVIOURS and sql:
        problems.append(f"a {behaviour} reply runs no query")
    cites = reply.get("citations") or []
    if not isinstance(cites, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in cites):
        problems.append("citations must be a list of source numbers")
        cites = []
    columns = reply.get("columns") or {}
    if not isinstance(columns, dict) or not all(isinstance(c, str) for c in columns.values()):
        problems.append("columns must map a unit to table.column")
        columns = {}
    refused = reply.get("joins_refused") or []
    pairs = [p.split("-") for p in refused] if isinstance(refused, list) and all(isinstance(p, str) for p in refused) else None
    if pairs is None or any(len(p) != 2 or p[0] == p[1] or not set(p) <= set(units) for p in pairs):
        problems.append("joins_refused must list unit pairs, such as " + ("-".join(units[:2]) if len(units) > 1 else "<unit>-<unit>"))
        pairs = []
    for key in ("reason", "answer_text"):
        if not isinstance(reply.get(key) or "", str):
            problems.append(f"{key} must be text")
    if problems:
        raise ContractError(problems)
    return {"behaviour": behaviour,
            "sql": {u: sql[u].strip().rstrip(";").strip() for u in units if u in sql},
            "citations": list(dict.fromkeys(cites)),
            "columns": {u: c for u, c in columns.items() if u in sql},
            "joins_refused": sorted({pair(a, b, domain) for a, b in pairs}, key=lambda p: [units.index(u) for u in p.split("-")]),
            "reason": (reply.get("reason") or "").strip(),
            "answer_text": (reply.get("answer_text") or "").strip()}


def join_path(sql: str) -> list[str]:
    """Each join the query makes, as from_table.column -> to_table.column, in the order written."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    names = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    path = []
    for j in tree.find_all(exp.Join):
        on, right = j.args.get("on"), j.this
        if not (isinstance(on, exp.EQ) and isinstance(on.this, exp.Column) and isinstance(on.expression, exp.Column)):
            continue
        a, b = (on.expression, on.this) if on.this.table == right.alias_or_name else (on.this, on.expression)
        path.append(f"{names.get(a.table, a.table)}.{a.name} -> {names.get(b.table, b.table)}.{b.name}")
    return path


def named_for(question: str, req, terms: list[str], domain) -> dict[str, list[str]]:
    """The units the question names for each term. A question of two measures ("A plus B") names a unit for
    the measure on its side of the plus; any other names its units for the term it is planned on, and only that.
    domain: the Engine, or anything that carries the client's domain as it does (ALIASES, UNIT_WORDS)."""
    if req.special != "two_measures":
        return {t: list(req.units) if t == req.term else [] for t in terms}
    sides = re.split(r"\bplus\b", question.lower())
    out = {}
    for t in terms:
        side = next((x for x in sides if re.search(r"\b" + re.escape(t.lower()) + r"s?\b", x)
                     or any(a in x for a, to in domain.ALIASES.items() if to == t)), "")
        out[t] = [u for w, u in domain.UNIT_WORDS.items() if re.search(r"\b" + w + r"\b", side)]
    return out


def and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else " and ".join(items) if len(items) == 2 else ", ".join(items[:-1]) + ", and " + items[-1]


def bind(f: dict, citations: list[dict], defines: dict, in_window=frozenset(), own: bool = False) -> int | None:
    """The citation a figure rests on: the one that defines a term its column is certified for, and of those the one
    in force for the period asked (in_window: their numbers); failing that, one about its unit. With own (graph on,
    the treatment), that is all: a figure no citation defines or is about the unit of rests on none, never on another
    term's definition or a document about no unit or another one that the question happened to retrieve. Without
    (graph off, whose search has no term to bind by), then one about no unit that defines no other term; then one
    about no unit; then the first. Among equals, the reply's order wins."""
    terms = set(defines.get((f["unit"], f["column"])) or ())

    def rank(c: dict) -> int:
        if terms & set(c.get("terms") or ()):
            return 0 if c["n"] in in_window else 1
        if c["unit"] == f["unit"]:
            return 2
        return (3 if not c.get("terms") else 4) if c["unit"] == "" else 5
    if own:
        citations = [c for c in citations if rank(c) <= 2]
    return min(citations, key=rank)["n"] if citations else None


def supported(r: dict, question: str) -> set[float]:
    """The numbers wording may carry: the result's figures, and the numbers in the question and in what it cites
    (a version, a year in a title), but not the digits of a name in them (FY26-Q1, v3)."""
    allowed = {float(f["value"]) for f in r.get("figures") or [] if f["value"] is not None}
    if r.get("figure") is not None:
        allowed.add(float(r["figure"]))
    for s in [question] + [f"{c.get('version') or ''} {c.get('title') or ''}" for c in r.get("citations") or []]:
        s = MARKER.sub("", s)
        allowed |= {float(x.replace(",", "")) for x in numbers(s, names(s))}
    return allowed


def carried(r: dict, question: str) -> set[str]:
    """The names wording may carry, digits and all: the question's, the ids and titles of what the answer cites and
    lists, the ids of every document its context named, the columns its figures measure, and its reason. Any other
    digits in the wording are numbers."""
    docs = (r.get("citations") or []) + (r.get("references") or [])
    shown = [d for t in r.get("trace") or [] if t.get("step") == "context" for d in [s[1] for s in t["sources"]] + (t.get("named") or [])]
    return names(question, r.get("reason") or "", *[f"{d.get('doc') or ''} {d.get('title') or ''}" for d in docs], *shown,
                 *[f.get("column") or "" for f in r.get("figures") or []])


def documents_in(text: str, ids: set[str]) -> set[str]:
    """The document ids text names, in any case, and one glued to more (WIKI-003-based): the longest that is an id."""
    out = set()
    for token in DOC_TOKEN.findall(text or ""):
        parts = token.upper().split("-")
        out |= {next(("-".join(parts[:j]) for j in range(len(parts), 0, -1) if "-".join(parts[:j]) in ids), None)} - {None}
    return out


def wording(text: str, allowed: set[float], markers: set[int], known: set[str] = frozenset()) -> tuple[list[str], list[int]]:
    """The numbers in text that the result does not hold, and the [n] markers that name no citation. A number is
    any digits outside a known name (carried), signed or glued to letters or a hyphen: -3000, USD9999, 100-200, .5."""
    bad = sorted({int(m) for m in MARKER.findall(text)} - markers)
    stray = [x for x in numbers(MARKER.sub("", text), known) if float(x.replace(",", "")) not in allowed]
    return stray, bad


MODEL_STEPS = ("context", "plan", "check", "run", "answer")
RULES_STEPS = ("rules", "evidence")


class Turn(TypedDict, total=False):
    """What the loop's steps pass on: the question asked, then what each step adds for the ones after it. Once a step has
    given the answer, each step after it passes it on."""
    question: str
    principal: str
    purpose: str
    arm: str
    cfg: RunConfig
    info: dict
    out: dict
    ctx: dict
    prompt: str
    reply: dict | None
    why: str | None
    guards: dict
    result: dict
    planned: dict
    answer: dict


def mode_of(t: Turn) -> str:
    return "rules" if t["info"]["mode"] == "rules" else "model"


def loop(agent) -> CompiledStateGraph:
    """The agent loop for agent: each node the agent's method <step>_step. The mode picks the branch at START, rules with
    no model; after that no node picks the next."""
    g = StateGraph(Turn)
    for name in MODEL_STEPS + RULES_STEPS:
        g.add_node(name, getattr(agent, f"{name}_step"))
    g.add_conditional_edges(START, mode_of, {"model": MODEL_STEPS[0], "rules": RULES_STEPS[0]})
    for steps in (MODEL_STEPS, RULES_STEPS):
        for a, b in zip(steps, steps[1:]):
            g.add_edge(a, b)
        g.add_edge(steps[-1], END)
    return g.compile()


class Agent:
    def __init__(self, engine: Engine, llm="auto"):
        self.e, self.g = engine, engine.g
        from kge.adapters.model import model_for
        self.llm = model_for("answer") if llm == "auto" else llm
        self._catalogue: dict[str, dict] = {}
        self._ids: Identities | None = None
        self.RUNS_SQL = runs_sql(engine.UNIT_ORDER)
        self.workflow = workflow(self)
        self.loop = loop(self)
        self.context_url = service_url()

    @property
    def mode(self) -> str:
        """rules with no model; mock for a client whose provider is mock, so a mock run is never taken for a model's; model otherwise."""
        if self.llm is None:
            return "rules"
        return "mock" if getattr(self.llm, "provider", "") == "mock" else "model"

    def config(self, arm: str) -> RunConfig:
        """The arm's run config: its rule-based preset with no model; with one, the model writes every
        query and may phrase the answer, and the control's model reads the question with no term mapping."""
        if arm not in AGENT_ARMS:
            raise ValueError(f"arm must be one of {', '.join(AGENT_ARMS)}, not {arm!r}")
        cfg = AGENT_ARMS[arm]
        if self.llm is None:
            return cfg
        return cfg.with_stages(sql_source="model", renderer="template" + MODEL, resolution=None if cfg.graph else "none")

    @request_budget
    def ask(self, question: str, principal: str, purpose: str = "analysis", arm: str = "treatment") -> dict:
        """The answer, as the agent loop gives it, each column it names by its business name (Engine.business)."""
        return self.e.business(self.loop.invoke(self.turn(question, principal, purpose, arm))["answer"])

    def turn(self, question: str, principal: str, purpose: str = "analysis", arm: str = "treatment") -> Turn:
        """What the loop starts from: the question, and the arm's run config, checked, with the agent the trace records."""
        cfg = self.e.stamped(self.config(arm))
        bad = cfg.problems("agent", has_model=self.llm is not None)
        if bad:
            raise ValueError("; ".join(bad))
        prompts = dict(PROMPTS) if self.llm is not None else None
        runtrace.agent(arm, self.mode, prompts)
        info = {"arm": arm, "mode": self.mode, "config": cfg.label, "prompt_versions": prompts}
        return {"question": question, "principal": principal, "purpose": purpose, "arm": arm, "cfg": cfg, "info": info}

    @property
    def identities(self) -> Identities:
        """The tokens of the principals of this agent's profile, in its run state (kge/services/identity/service.py): the API's own.
        Built once, whichever thread asks first: each build may mint tokens and write tokens.json, which agents asked
        at once in this process share."""
        if self._ids is None:
            with IDS_BUILT:
                if self._ids is None:
                    self._ids = Identities(self.e.profile.run_dir(), self.g.principal_ids())
        return self._ids

    def token(self, principal: str) -> str | None:
        """principal's own bearer token: what the agent calls the context service with, on principal's behalf."""
        return next((t for t, p in self.identities.by_token.items() if p == principal), None)

    def fetch(self, question: str, principal: str, purpose: str, arm: str) -> dict:
        """The arm's context package through the context contract, as a downstream app gets it (kge/services/ask/engine/context.py, post):
        POST /context as principal, with its own token, at CONTEXT_URL, else in this process. Both arms fetch it so."""
        return post(self, self.context_url, question, principal, purpose, arm)

    def document_ids(self) -> set[str]:
        """Every document the graph holds: the ids a model's wording is read for."""
        return {r["id"] for r in self.g.q("MATCH (d:Document) RETURN d.id AS id")}

    @property
    def source(self) -> str:
        """Who wrote the SQL or the wording: model:<provider:model>, or mock:<model> on the mock, never labelled a model's."""
        if self.mode == "mock":
            return f"mock:{getattr(self.llm, 'model', None) or self.llm.name}"
        return f"model:{self.llm.name}"

    def group(self, req, principal: str) -> dict | None:
        """Graph on, for a question of /ask's shape (one customer group with figures over it, kge/services/ask/engine/combined.py):
        the graph's decision, as /ask makes it before any query ({behaviour, reason, terms, unit, joins_refused}).
        Where /ask would answer, an abstention that says what was asked: a reply here is one figure per unit, and
        one figure of the group is not the answer. None for any other question, and for a principal who may not
        query or read a unit it names, which the rules and the scope already refuse. A group lives in one unit: across
        units it is refused, or not answered where a certified join links them."""
        impls = {t: self.g.implementations(t) for t in req.terms_found if t in self.e.term_names}
        shape = cohort(req, impls)
        readable = self.g.readable_units(principal)
        if shape is None or not self.g.may(principal, "querying") or not set(req.units) <= readable:
            return None
        shaped, group, measures = shape
        out = {"terms": shaped, "unit": None, "joins_refused": []}
        if len(req.units) > 1:
            refused = across(self.e, req.units)
            return {**out, "behaviour": refused["behaviour"], "joins_refused": [f["pair"] for f in refused["facts"]], "reason": refused["reason"]}
        every = [u for u in self.e.UNIT_ORDER if all(any(i["unit"] == u for i in impls[t]) for t in shaped)]
        if not req.units and len(every) != 1:
            return {**out, "behaviour": "abstain", "reason": "Name the business unit: these terms are certified in " + (", ".join(every) or "no single unit") + "."}
        unit = req.units[0] if req.units else every[0]
        if unit not in readable:
            return None
        impl = {t: next((i for i in impls[t] if i["unit"] == unit), None) for t in shaped}
        missing = [t for t in shaped if impl[t] is None]
        if missing:
            return {**out, "behaviour": "abstain", "unit": unit,
                    "reason": f"Not certified in {self.e.UNIT_LABEL[unit]}: {', '.join(missing)}. The answer would rest on a guess."}
        group = sorted(group, key=lambda t: role(impl[t]) != "flag")
        asked = [f"how many meet {group[0]}"] + [f"how many of those had a {t}" for t in group[1:]] + ([f"their {and_list(measures)}"] if measures else [])
        return {**out, "behaviour": "abstain", "unit": unit,
                "reason": f"Not answered: the question asks about one {self.e.UNIT_LABEL[unit]} customer group: {and_list(asked)}. A reply here is one "
                          f"figure per unit, so it gives none rather than one figure for part of the group; the graph preset, on /ask, "
                          f"answers a customer group whole."}

    def grouped(self, question: str, principal: str, purpose: str, cfg: RunConfig) -> dict | None:
        """On rules, graph on: the answer to a question of /ask's shape, the graph's decision with no query run.
        None for any other question, which the planner answers."""
        req = parse(question, self.e.term_names, self.e)
        if self.e.request_problem(req):
            return None
        grp = self.group(req, principal)
        if grp is None:
            return None
        runtrace.use(cfg)
        runtrace.stage("resolve")
        trace = [{"step": "parse", "term": grp["terms"][0], "terms": grp["terms"], "units": req.units or "unspecified",
                  "period": req.period.label() if req.period else None},
                 {"step": "group", "unit": grp["unit"], "behaviour": grp["behaviour"]}]
        runtrace.stage("render")
        return {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label, "trace": trace,
                "versions": {"parser": "rules-v1", "embedding_model": self.g.embedder.name}, "behaviour": grp["behaviour"],
                "reason": grp["reason"], **({"joins_refused": grp["joins_refused"]} if grp["joins_refused"] else {})}

    def context(self, question: str, principal: str, purpose: str, arm: str) -> dict:
        """{arm, block, sources, units, certified, meta, named, withheld, needed, in_window}: the block is what the prompt
        carries; sources are numbered as the block numbers them, each citable or not; certified is what the gate admits;
        meta what the block says of each certified column (grain, currency, calendar, conversion), which a figure
        that measures it carries (graph off, nothing: none is given);
        named is the documents the block names besides its sources, whose ids the number check reads as names, and
        withheld those of them it says not to use (not in force for the period, not AI-ready); needed is the units
        the question needs, which the seam checks access on with those the reply reads (graph on only: graph off
        nothing resolves the question's term); in_window the citable sources in force for the period asked.
        Built by the context workflow: the control's steps for control, the treatment's for any other arm."""
        req = parse(question, self.e.term_names, self.e)
        if reason := self.e.request_problem(req):
            decision = self.e.request_abstention(req, reason)
            return {"arm": "control" if arm == "control" else "treatment", "block": decision["reason"],
                    "sources": [], "units": [], "certified": {}, "in_window": [], "defines": {}, "meta": {},
                    "group": None, "named": [], "withheld": [], "needed": [], "plan": None,
                    "decided": decision}
        return self.workflow.invoke({"question": question, "principal": principal, "purpose": purpose, "arm": arm})["package"]

    def catalogue(self, unit: str) -> dict[str, dict]:
        """A unit's tables as its warehouse's own catalogue declares them: columns and types, key, references."""
        if unit not in self._catalogue:
            w = self.e.w
            tables: dict[str, dict] = {}
            for t, c, d in w.catalogue_columns(unit):
                tables.setdefault(t, {"columns": [], "key": [], "references": []})["columns"].append((c, d))
            for t, cols in w.primary_keys(unit):
                tables[t]["key"] = list(cols)
            for t, cols, rt, rcols in w.foreign_keys(unit):
                tables[t]["references"] += [(c, rt, rc) for c, rc in zip(cols, rcols)]
            self._catalogue[unit] = tables
        return self._catalogue[unit]

    def doc_units(self, docs: list[str]) -> dict[str, str]:
        rows = self.g.q("MATCH (d:Document) WHERE d.id IN $ids RETURN d.id AS doc, d.unit AS unit", {"ids": list(docs)}) if docs else []
        return {r["doc"]: r["unit"] or "" for r in rows}

    def schema(self, state: State) -> dict:
        """Graph off, the scope: the warehouse schema of the units the principal may read."""
        runtrace.stage("resolve")
        units = [u for u in self.e.UNIT_ORDER if u in self.g.readable_units(state["principal"])]
        lines = [f"Scope: the units you may read: {', '.join(units) or 'none'}.", "",
                 "Warehouse schema, from each warehouse's catalogue (table: column type, ...; key; references):"]
        for u in units:
            lines.append(u)
            for t, spec in sorted(self.catalogue(u).items()):
                refs = ", ".join(f"{c} -> {rt}.{rc}" for c, rt, rc in spec["references"])
                lines.append(f"  {t}: " + ", ".join(f"{c} {d}" for c, d in spec["columns"])
                             + (f"; key {', '.join(spec['key'])}" if spec["key"] else "") + (f"; references {refs}" if refs else ""))
        return {"units": units, "lines": lines}

    def search(self, state: State) -> dict:
        """Graph off, the sources: plain vector search, with no tier filter. The package."""
        runtrace.stage("retrieve")
        hits = self.g.retrieve(state["question"], state["principal"], state["purpose"], k=SEARCH_K)["exact"]
        unit_of = self.doc_units([h["doc"] for h in hits])
        sources = [{"n": n, "doc": h["doc"], "chunk": h["chunk"], "title": h["title"], "excerpt": " ".join((h["excerpt"] or "").split()),
                    "unit": unit_of.get(h["doc"], ""), "citable": True} for n, h in enumerate(hits, 1)]
        lines = ["", "Sources found by search, by number:"]
        lines += [f"  [{s['n']}] {s['doc']} \"{s['title']}\" (chunk {s['chunk']}): {s['excerpt']}" for s in sources] or ["  none"]
        return {"lines": lines, "package": {
            "arm": "control", "block": "\n".join(state["lines"] + lines), "sources": sources, "units": state["units"], "certified": {},
            "defines": {}, "meta": {}, "group": None, "named": [], "withheld": [], "needed": [], "in_window": []}}

    def scope(self, state: State) -> dict:
        """Graph on, the scope: the question parsed, the units this principal may read, those in play and those the question
        needs, and, for a term a context answers, the context and its query (kge/semantics/query.py). What the graph refuses
        it refuses to anyone, while an abstention, as a figure, needs the units it reads to be readable."""
        e, g, question, principal = self.e, self.g, state["question"], state["principal"]
        runtrace.stage("resolve")
        req = parse(question, e.term_names, e)
        if len(req.terms_found) > 1 and not req.special:
            with_impl = [t for t in req.terms_found if g.implementations(t)]
            with_context = [t for t in req.terms_found if e.semantics.contexts_of(t)]
            if with_impl or with_context:
                req.term = (with_impl or with_context)[0]
        readable = [u for u in e.UNIT_ORDER if u in g.readable_units(principal)]
        terms = [t for t in req.terms_found if t != "__population__"]
        impls = {t: [i for i in g.implementations(t) if i["unit"] in readable] for t in terms}
        everywhere = {t: sorted({i["unit"] for i in g.implementations(t)}, key=e.UNIT_ORDER.index) for t in terms}
        keyed = {}
        if req.term == "__population__":
            keyed = {r["unit"]: r for r in g.q("MATCH (u:Unit), (c:Col {is_key: true}) WHERE c.unit = u.id AND c.name = u.key_column "
                                               "RETURN u.id AS unit, c.tbl AS tbl, c.name AS col, c.data_type AS dtype")}
            impls["customers"] = [{**keyed[u], "grain": "", "currency": "", "calendar": "", "conversion": "", "certified_by": "",
                                   "certified_on": ""} for u in readable if u in keyed]
            req.agg = "count"
        named = [u for u in req.units if u in readable]
        asked = named_for(question, req, terms, e)
        two = req.special == "two_measures"
        if not two:
            asked = {t: asked[t] or (list(req.units) if everywhere[t] else []) for t in terms}
        if two:
            scope_of = {t: [i["unit"] for i in impls[t] if not asked[t] or i["unit"] in asked[t]] for t in terms}
            in_play = [u for u in e.UNIT_ORDER if any(u in units for units in scope_of.values())]
        else:
            in_play = named or [u for u in e.UNIT_ORDER if any(i["unit"] == u for t in impls for i in impls[t])]
            scope_of = {t: in_play for t in impls}
        if req.term is None or req.definitional or req.special:
            needed = []
        elif req.units:
            needed = list(req.units)
        elif req.term == "__population__":
            needed = [u for u in e.UNIT_ORDER if u in keyed]
        else:
            needed = everywhere.get(req.term, [])
        context = next(iter(e.semantics.contexts_of(req.term)), None) if req.term and not (req.definitional or req.special) else None
        plan = e.semantics.plan(context, Asked.of(req)) if context else None
        if plan is not None:
            needed = [] if plan.decision and plan.decision["behaviour"] == "refuse" else list(plan.units)
        denied = [u for u in dict.fromkeys(list(req.units) + needed) if u not in readable]
        whole = ("every unit's customers" if req.term == "__population__" else f"every unit {req.term} is certified in") + f": {', '.join(needed)}"

        lines = [f"Scope: you may read {', '.join(readable) or 'no unit'}."
                 + (f" The question names {', '.join(req.units)}." if req.units else "")
                 + (f" It names no unit, so it needs {whole}." if denied and not req.units else "")
                 + (f" You may not read {', '.join(denied)}: a question that needs it is refused, and no query runs." if denied else "")]
        return {"req": req, "readable": readable, "terms": terms, "impls": impls, "everywhere": everywhere, "asked": asked, "two": two,
                "scope_of": scope_of, "in_play": in_play, "needed": needed, "context": context, "plan": plan, "denied": denied, "lines": lines}

    def define(self, state: State) -> dict:
        """Graph on, the terms, with their definitions and where each is certified. A definition asked of a term no document
        defines is the pack's glossary's, which the code holds whatever the reply (Engine.from_glossary)."""
        e, g, req, terms, impls, context = self.e, self.g, state["req"], state["terms"], state["impls"], state["context"]
        asked, readable, everywhere = state["asked"], state["readable"], state["everywhere"]
        undefined = e.undefined(req)
        lines = ["", "Terms:"] if terms else []
        for t in terms:
            row = g.q("MATCH (t:Term {name: $t}) RETURN t.definition AS definition, t.steward AS steward", {"t": t})
            d = row[0] if row else {"definition": "", "steward": ""}
            lines.append(f"  {t}: {d['definition'] or '(no definition recorded)'}" + (f" Steward: {d['steward']}." if d["steward"] else ""))
            if undefined is not None and t == undefined["term"]:
                lines.append("    defined by no document: the definition above is the pack's glossary's, and the answer; no source below defines it")
            for i in impls[t]:
                lines.append(f"    certified in {i['unit']}: {i['tbl']}.{i['col']} ({i['dtype']}; {role(i)}; grain {i['grain'] or '-'}, "
                             f"currency {i['currency'] or '-'}, calendar {i['calendar'] or '-'}, conversion {i['conversion'] or '-'}; "
                             f"certified by {i['certified_by']} on {i['certified_on']})")
            missing = [u for u in (asked[t] if req.units else readable) if u not in everywhere[t]]
            if context is not None and t == context.term:
                lines.append(f"    resolves to the context {context.id}, below: its figure is built as the context declares it")
            elif missing:
                lines.append(f"    not certified in {', '.join(missing)}: a figure there would rest on a guess")
        return {"undefined": undefined, "lines": lines}

    def route(self, state: State) -> dict:
        """Graph on, a phrase a tagger proposed as a term: routed until a steward accepts it into the glossary, as Engine.answer
        routes it."""
        e, g, req, question = self.e, self.g, state["req"], state["question"]
        candidates = {r["v"] for r in g.q("MATCH (t:Tag {family: 'candidate_term'}) RETURN DISTINCT t.value AS v")
                      if r["v"] and r["v"].lower() in question.lower()} | ({req.candidate} if req.candidate else set())
        candidates = {c for c in candidates if c not in e.term_names}
        named: list[str] = []
        lines: list[str] = []
        if candidates:
            lines += ["", "Not glossary terms:"]
            for c in sorted(candidates):
                found = sorted({s["id"] for s in g.candidate_sources(c)})
                named += found
                lines.append(f"  {c}: not a term in the glossary." + (f" A definition exists only on the uncertified shelf ({', '.join(found)}); "
                                                                      f"route it to the steward queue." if found else ""))
        return {"named": named, "lines": lines}

    def customer_group(self, state: State) -> dict:
        """Graph on, one customer group with figures over it: the graph's decision, which the code holds whatever the reply."""
        group = self.group(state["req"], state["principal"])
        return {"group": group, "lines": ["", "One customer group:", f"  Reply {group['behaviour']}: {group['reason']}"] if group else []}

    def linked(self, state: State) -> dict:
        """Graph on, which of one unit's customers the other has too, as Engine.answer plans it, through the certified join: what
        the graph decided of it holds whatever the reply (restricted personal data asked for across the two units is refused)."""
        req = state["req"]
        linked = self.e.linked_plan(req) if req.special == "cross_unit_entity" and len(req.units) == 2 else None
        held = {k: linked.decision[k] for k in ("behaviour", "reason")} if linked is not None and linked.decision else None
        return {"held": held, "lines": ["", "Across units, through the certified join:", f"  Reply {held['behaviour']}: {held['reason']}"]
                if held else []}

    def columns(self, state: State) -> dict:
        """Graph on, columns by role, per unit, with the period and the joins each needs."""
        req, impls, scope_of, in_play, cfg = state["req"], state["impls"], state["scope_of"], state["in_play"], AGENT_ARMS["treatment"]
        certified: dict[str, list[tuple[str, str]]] = {}
        defines: dict[tuple[str, str], list[str]] = {}
        meta: dict[tuple[str, str], dict] = {}
        roles = []
        for t, rows in impls.items():
            for i in rows:
                defines.setdefault((i["unit"], f"{i['tbl']}.{i['col']}"), []).append(t)
                meta[i["unit"], f"{i['tbl']}.{i['col']}"] = {k: i[k] for k in ("grain", "currency", "calendar", "conversion")}
                if i["unit"] not in scope_of.get(t, in_play):
                    continue
                roles += self.roles(req, t, i, cfg, certified)
        return {"certified": certified, "defines": defines, "meta": meta,
                "lines": ["", "Columns by role (measure, filter, period, keys):"] + roles if roles else []}

    def contexts(self, state: State) -> dict:
        """Graph on, a context: its query, as the graph declares it, in place of columns by role."""
        return {"lines": self.context_lines(state["plan"], state["denied"]) if state["plan"] is not None else []}

    def joins(self, state: State) -> dict:
        """Graph on, the declared joins of each unit in play: the only ones that exist, whatever two columns' names suggest."""
        joins = []
        for u in (state["in_play"] if state["plan"] is None else []):
            rows = self.g.q("MATCH (a:Tbl)-[j:JOINS_TABLE]->(b:Tbl) WHERE a.unit = $u RETURN j.from_table AS ft, j.from_column AS fc, "
                            "j.to_table AS tt, j.to_column AS tc ORDER BY ft, fc, tt, tc", {"u": u})
            if rows:
                joins.append(f"  {u}: " + "; ".join(f"{r['ft']}.{r['fc']} = {r['tt']}.{r['tc']}" for r in rows))
        return {"lines": ["", "Declared joins, the only ones that exist (table.column = table.column):"] + joins if joins else []}

    def across(self, state: State) -> dict:
        """Graph on, across units: no joins, and whether the parts may be added. Two measures are never added, wherever each is
        certified, and none of their units join: the units are every one either measure is certified in."""
        e, g, req, terms, impls, everywhere = self.e, self.g, state["req"], state["terms"], state["impls"], state["everywhere"]
        in_play, readable, two, plan, cfg = state["in_play"], state["readable"], state["two"], state["plan"], AGENT_ARMS["treatment"]
        measures = [t for t in terms if everywhere[t]]
        across = [u for u in e.UNIT_ORDER if any(u in everywhere[t] for t in measures)] if two else in_play
        notes, rests_on = [], []
        for a in range(len(across)):
            for b in range(a + 1, len(across)):
                f = g.no_join(across[a], across[b])
                if f:
                    notes.append(f"  No join between {across[a]} and {across[b]} (asserted by {f['asserted_by']} on {f['asserted_on']}): {f['note']}")
                    rests_on += [u for u in (across[a], across[b]) if u in readable]
        if len(in_play) > 1 and not two and plan is None:
            parts = [{"status": "ok", "column": f"{i['tbl']}.{i['col']}", **{k: i[k] for k in ("grain", "currency", "calendar", "conversion")}}
                     for t in impls for i in impls[t] if i["unit"] in in_play and (t == req.term or t == "customers")]
            if len({p["column"] for p in parts}) > 1:
                verdict, why = e.commensurable(parts, req, cfg)
                notes.append(f"  Adding across units: {req.term if req.term != '__population__' else 'customers'} in {', '.join(in_play)} "
                             + ("may be added: " if verdict else "must not be added; report the parts: ") + why)
        if two and len(measures) > 1:
            notes.append("  " + " and ".join(measures) + " are different measures with different meanings: never added together")
        return {"rests_on": rests_on, "lines": ["", "Across units:"] + notes if notes else []}

    def withhold(self, state: State) -> dict:
        """Graph on, what not to use: restricted personal data, definitions not in force for the period, documents not AI-ready."""
        e, readable = self.e, state["readable"]
        runtrace.stage("retrieve")
        cited = e.cite_tiers(state["req"], state["principal"], state["purpose"])
        restricted = [f"{t}.{c} ({u})" for u, t, c in e.RESTRICTED_COLUMNS if u in readable]
        cites = ", ".join(dict.fromkeys(e.RESTRICTED_CITES.values()))
        lines = ["", "Do not use:", "  restricted personal data, in no query and under no purpose" + (f" ({cites})" if cites else "") + ": "
                 + (", ".join(restricted) or "none")]
        named: list[str] = []
        withheld: list[str] = []
        if cited.get("superseded_not_cited"):
            lines.append("  not in force for the period asked, never cited: " + ", ".join(cited["superseded_not_cited"]))
            named += cited["superseded_not_cited"]
            withheld += cited["superseded_not_cited"]
        if cited.get("not_ai_ready"):
            lines.append("  landed as not AI-ready, never retrieved or cited: " + ", ".join(sorted({d["doc"] for d in cited["not_ai_ready"]})))
            named += [d["doc"] for d in cited["not_ai_ready"]]
            withheld += [d["doc"] for d in cited["not_ai_ready"]]
        return {"cited": cited, "named": named, "withheld": withheld, "lines": lines}

    def tiers(self, state: State) -> dict:
        """Graph on, the sources by tier: gold and silver citable, bronze listed for reasoning only."""
        sources = self.tiered_sources(state["cited"], state["rests_on"], state["purpose"])
        lines = ["", "Sources, by number (cite gold as the authority, silver with its attribution):"]
        lines += [f"  [{s['n']}] {s['doc']} \"{s['title']}\" v{s['version']}, {s['tier']}, "
                  + ("the authority" if s["tier"] == "gold" else f"cite with attribution: {s['attribution']}")
                  + f"; {s['why']}; in force {s['in_force']}" + (f" (chunk {s['chunk']}): {s['excerpt']}" if s["chunk"] else "")
                  for s in sources if s["citable"]] or ["  none"]
        lines += ["", "References, for reasoning only, never cited (" + ("what the question retrieved, none of which defines its term"
                                                                       if state["undefined"] is not None else "bronze") + "):"]
        lines += [f"  [{s['n']}] {s['doc']} \"{s['title']}\"" + (f" (chunk {s['chunk']}): {s['excerpt']}" if s["chunk"] else "")
                  for s in sources if not s["citable"]] or ["  none"]
        return {"sources": sources, "lines": lines}

    def provenance(self, state: State) -> dict:
        """Graph on, provenance: who certified each column and when, and the certification each source's tier landed with. The
        package."""
        e, g, sources, plan = self.e, self.g, state["sources"], state["plan"]
        policy = g.q("MATCH (d:Document) WHERE d.cert_policy <> '' RETURN DISTINCT d.cert_policy AS p, d.cert_version AS v ORDER BY p, v")
        lines = ["", "Provenance: each certified column names the steward who certified it and when; each source's tier is as "
                 "upstream AI certification landed it (" + ", ".join(f"{r['p']} version {r['v']}" for r in policy) + "); graph model "
                 + runtrace.fingerprint(g)["model"][:12] + "."]
        window = asked_window(state["req"], e)
        in_window = sorted(s["n"] for s in sources if s["citable"] and "cert_from" in s and in_force(s, window))
        decided = plan.decided if plan is not None else state["held"] or (glossary(state["cited"]) if state["undefined"] is not None else None)
        return {"lines": lines, "package": {
            "arm": "treatment", "block": "\n".join(state["lines"] + lines), "sources": sources, "units": state["readable"],
            "certified": state["certified"], "in_window": in_window, "defines": state["defines"], "meta": state["meta"], "group": state["group"],
            "named": sorted(set(state["named"])), "withheld": sorted(set(state["withheld"])), "needed": state["needed"],
            "plan": plan if plan is not None and not plan.decision else None, "decided": decided}}

    def context_lines(self, plan, denied: list[str]) -> list[str]:
        """The Contexts section: the context the question's term resolves to, and its query in parts, as Engine.answer
        assembles it (kge/semantics/query.py): what to select, from, join and require, what to group by, and, across units,
        the certified join's ends. Or what the graph decides instead: a refusal or an abstention, or a month not yet
        released (the release rule reads the release driver, only once the principal may read every unit). Ids and
        declared conditions only: never a golden question, its figure or its SQL."""
        c = plan.context
        lines = ["", "Contexts (a term that resolves to a context is answered by it alone: build the query from these lines and "
                     "nothing else):", f"  {c.term} resolves to {c.id}, {c.label}: {c.description}"]
        decision = plan.decision
        if decision is None and not denied:
            q = self.e.context_query(plan)
            decision = q.get("decision")
        if denied and decision and decision["behaviour"] != "refuse":
            return lines
        if decision:
            plan.decided = {k: decision[k] for k in ("behaviour", "reason", "joins_refused") if k in decision}
            return lines + [f"    reply    {decision['behaviour']}" + (f" [{', '.join(decision['joins_refused'])}]"
                                                                      if decision.get("joins_refused") else "") + f": {decision['reason']}"]
        if denied:
            return lines
        parts = plan.parts(q["months"])
        lines += [f"    unit     {plan.unit}", f"    measure  {parts['select']}", f"    from     {parts['from']}"]
        lines += [f"    join     {j}" for j in parts["joins"]] + [f"    where    {w}" for w in parts["where"]]
        if parts["group"]:
            lines.append(f"    group    {parts['group']}")
        for k in plan.keys:
            lines.append(f"    across   {k['unit']}: {k['far']} ~ {k['near']}, only through the certified join {k['join']} "
                         f"({k['through']}), as the EXISTS above: name every table <unit>.<table>, and key the query by {plan.unit}")
        return lines + [f"    note     {n}" for n in plan.notes]

    def roles(self, req, term: str, i: dict, cfg: RunConfig, certified: dict) -> list[str]:
        """One term's certified column in one unit, by role, with the period and the declared joins it needs."""
        unit, table, col = i["unit"], i["tbl"], i["col"]
        certified.setdefault(unit, []).append((table, col))
        filtered = any(f == col for f, _, _ in req.filters)
        if role(i) == "flag":
            how = f"count(*) of the rows where {table}.{col} is true"
        elif filtered or req.agg == "count" or role(i) == "event":
            how = f"count({table}.{col})"
        elif req.agg == "avg":
            how = f"round(avg({table}.{col}), 2)"
        else:
            how = f"round(sum({table}.{col}), 2)"
        out = [f"  {unit}, {term}:", f"    measure  {table}.{col}: {how}"]
        keys: list[str] = []
        for fcol, op, val in req.filters:
            owners = self.g.column_owners(unit, fcol)
            if not owners:
                out.append(f"    filter   {fcol}: no such column in {unit}")
                continue
            owner = table if table in owners else owners[0]
            certified[unit].append((owner, fcol))
            value = "" if op in ("IS NULL", "= TRUE") else f" {val}" if isinstance(val, (int, float)) else f" '{val}'"
            out.append(f"    filter   {owner}.{fcol} {'is true' if op == '= TRUE' else op}{value}")
            if owner != table:
                path = self.g.join_path(unit, table, owner)
                prev = table
                for hop in path or []:
                    fwd = hop["from_table"] == prev
                    a, b = (hop["from_table"], hop["to_table"]) if fwd else (hop["to_table"], hop["from_table"])
                    ac, bc = (hop["from_column"], hop["to_column"]) if fwd else (hop["to_column"], hop["from_column"])
                    keys.append(f"{a}.{ac} = {b}.{bc}")
                    prev = b
                if not path:
                    keys.append(f"no declared join from {table} to {owner}")
        pc, pj, pn = self.e.period_clause(unit, table, req.period, cfg, req.agg, self.e.w.sql_dialect)
        if req.period is not None:
            out.append("    period   " + (pc or ("not available" if pc is None else "none needed")) + (f"  -- {pn}" if pn else ""))
        keys = [j.split(" ON ", 1)[1] for j in pj] + keys
        if keys:
            out.append("    keys     " + "; ".join(keys))
        return out

    def tiered_sources(self, cited: dict, rests_on: list[str], purpose: str) -> list[dict]:
        """The tier rule's citations (gold, silver) and references (bronze), numbered in one sequence, and the
        data dictionaries a no-join fact rests on. Only gold and silver are citable."""
        g = self.g
        out: list[dict] = []
        seen: set[str] = set()

        def add(doc, citable, **more):
            if doc in seen:
                return
            seen.add(doc)
            out.append({"n": len(out) + 1, "doc": doc, "citable": citable, "chunk": None, "excerpt": "", "terms": [], **more})

        landed = g.certification([c["doc"] for c in cited.get("citations") or []])
        for c in cited.get("citations") or []:
            term = c["why"].removeprefix("certified definition of ") if c["why"].startswith("certified definition of ") else None
            add(c["doc"], True, title=c["title"], tier=c["tier"], cited_as=c["cited_as"], owner=c["owner"], version=c["version"],
                attribution=c.get("attribution"), in_force=c["in_force"], why=c["why"], chunk=c["chunk"], terms=[term] if term else [],
                cert_from=landed[c["doc"]]["cert_from"], cert_to=landed[c["doc"]]["cert_to"])
        if rests_on:
            dds = g.data_dictionaries(rests_on)
            cert = g.certification(dds)
            for doc in dds:
                c = cert[doc]
                if c["ai_ready"] and c["tier"] in CITE_AS and in_force(c, None):
                    first = g.q("MATCH (d:Document {id: $d})-[:HAS_CHUNK]->(c:Chunk) WHERE c.readiness <> 'embed-prohibited' AND $p IN c.purposes "
                                "RETURN c.id AS chunk ORDER BY c.ordinal LIMIT 1", {"d": doc, "p": purpose})
                    if first:
                        runtrace.retrieved([{"doc": doc, "chunk": first[0]["chunk"], "tier": c["tier"], "ai_ready": c["ai_ready"]}], "rests_on")
                    add(doc, True, title=c["title"], tier=c["tier"], cited_as=CITE_AS[c["tier"]], owner=c["owner"], version=c["version"],
                        attribution=f"{c['owner']}, version {c['version']}" if c["tier"] == "silver" else None,
                        in_force=f"{c['cert_from']} to {c['cert_to'] or 'now'}", why="the unit's keys, which the no-join fact rests on",
                        chunk=first[0]["chunk"] if first else None, cert_from=c["cert_from"], cert_to=c["cert_to"])
        for r in cited.get("references") or []:
            add(r["doc"], False, title=r["title"], tier=r["tier"], why=r["why"], chunk=r.get("chunk"), excerpt=r.get("excerpt") or "")
        chunks = [s["chunk"] for s in out if s["chunk"] and not s["excerpt"]]
        text = {r["id"]: r["x"] for r in g.q("MATCH (c:Chunk) WHERE c.id IN $ids RETURN c.id AS id, left(c.text, 200) AS x", {"ids": chunks})} if chunks else {}
        unit_of = self.doc_units([s["doc"] for s in out])
        for s in out:
            s["excerpt"] = " ".join((s["excerpt"] or text.get(s["chunk"]) or "").split())
            s["unit"] = unit_of.get(s["doc"], "")
        return out

    def prompt(self, task: str, question: str, block: str = "", facts: dict | None = None) -> str:
        if task == "sql":
            return f"{sql_prompt(self.e.UNIT_ORDER, self.e.w.query_dialect)}\n{CONTEXT_OPEN}\n{block}\n{CONTEXT_CLOSE}\n\nQuestion: {question}"
        return PHRASE_PROMPT.format(facts=json.dumps(facts, sort_keys=True), question=question)

    def call(self, task: str, prompt: str, block: str = "") -> tuple[object | None, str | None]:
        """(the model's JSON, None) or (None, why there is none). The trace records the prompt's version and
        hashes: of the prompt less its context block, the same for both arms, and of the block alone; a prompt
        with no block (the phrase call's) has the one hash, of the prompt. A call that failed is an abstention, never
        a guess, named only by its exception's type, since a transport's text can quote the key. On the pilot's cloud
        path (gcp_native) a failed call, or a reply that is not JSON, stops the run instead (NoFallback), and so does a failed
        phrase step, which the local stack answers with the template."""
        less = prompt.replace(block, "") if block else prompt
        with runtrace.prompting(PROMPTS[task], digest(less), digest(block) if block else None):
            try:
                return self.llm.json(prompt, MAX_TOKENS[task]), None
            except NotJSON as exc:
                no_fallback(f"the agent's {task} step", NOT_JSON)
                return None, f"the model's reply was not one JSON object ({str(exc).splitlines()[0][:100]})"
            except Refused as exc:
                return None, str(exc)
            except Exception as exc:  # noqa: BLE001
                transient_failure(exc)
                no_fallback(f"the agent's {task} step", exc)
                return None, f"the model call failed ({type(exc).__name__})"

    def allowlist(self, ctx: dict, unit: str):
        """The same gate for both arms; what it admits is the arm's context: the schema, or the certified columns.
        A figure may measure what the context gives: any column the schema admits, or the certified ones; for a term a
        context answers, the context's own allowlist (kge/semantics/query.py)."""
        if ctx["arm"] == "control":
            return schema_allowlist(self.g, unit, self.e.RESTRICTED_COLUMNS)
        plan = ctx.get("plan")
        if plan is not None and unit == plan.unit:
            return plan.allow
        return build_allowlist(self.g, unit, ctx["certified"].get(unit, []), self.e.RESTRICTED_COLUMNS)

    def gate(self, ctx: dict, unit: str, sql: str) -> dict:
        """The gate, as a figure, where the query runs: the unit's warehouse, or for a context of two units a session
        that attaches both (the access rule has passed on every unit before this is reached)."""
        plan, dialect = ctx.get("plan"), self.e.w.sql_dialect
        if plan is not None and unit == plan.unit:
            with self.e.session(plan, dialect) as con:
                return warehouse_guard(gate(con, sql, plan.allow, figure=True, dialect=dialect))
        return warehouse_guard(self.e.w.gate(unit, sql, self.allowlist(ctx, unit), figure=True, dialect=dialect))

    def row(self, ctx: dict, unit: str, sql: str) -> tuple | None:
        plan, dialect = ctx.get("plan"), self.e.w.sql_dialect
        if plan is not None and unit == plan.unit:
            with self.e.session(plan, dialect) as con:
                return con.execute(sql).fetchone()
        return self.e.w.row(unit, sql, dialect)

    def context_step(self, t: Turn) -> dict:
        """Check access, fetch context, then require a readable term or grounded citable passage before model planning."""
        question, principal, purpose, arm, cfg = t["question"], t["principal"], t["purpose"], t["arm"], t["cfg"]
        runtrace.use(cfg, self.llm)
        trace: list[dict] = []
        out = {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label,
               "agent": {**t["info"], "sql_source": self.source, "renderer": cfg.renderer}, "trace": trace,
               "attempts": [], "versions": {"prompts": dict(PROMPTS), "model": self.llm.name, "embedding_model": self.g.embedder.name}}
        runtrace.stage("access")
        if not self.g.may(principal, "querying"):
            return {"answer": stop(out, "refuse", f"{principal} is not a querying identity.")}
        req = parse(question, self.e.term_names, self.e)
        if reason := self.e.request_problem(req):
            decision = self.e.request_abstention(req, reason)
            return {"answer": stop(out, decision["behaviour"], decision["reason"])}
        ctx = self.fetch(question, principal, purpose, arm)
        if req.special is None and (req.term is None or req.definitional) and not self.e.definition_in_scope(question, principal) \
                and not grounded(question, [s for s in ctx["sources"] if s["citable"]], self.e):
            return {"answer": stop(out, "abstain", OUTSIDE_PACK)}
        trace.append({"step": "context", "arm": arm, "sources": [[s["n"], s["doc"], s["citable"]] for s in ctx["sources"]],
                      "named": ctx["named"], "hash": digest(ctx["block"])})
        return {"out": out, "ctx": ctx}

    def plan_step(self, t: Turn) -> dict:
        """The model proposes: the SQL task's prompt, its context block the arm's, and the reply checked against the contract."""
        if "answer" in t:
            return {}
        runtrace.stage("plan")
        prompt = self.prompt("sql", t["question"], t["ctx"]["block"])
        reply, why = self.plan(prompt, t["ctx"]["block"])
        return {"prompt": prompt, "reply": reply, "why": why}

    def check_step(self, t: Turn) -> dict:
        """Code decides, before any query runs: a reply that broke the contract is an abstention; what the graph decided holds;
        access, on what the question needs and the reply reads; then the gate (kge/pipeline/sqlguard.py) on every query, with one
        retry carrying its reasons."""
        if "answer" in t:
            return {}
        principal, ctx, out, prompt, reply, why = t["principal"], t["ctx"], t["out"], t["prompt"], t["reply"], t["why"]
        trace = out["trace"]
        for attempt in (1, 2):
            if why:
                return {"answer": stop(out, "abstain", f"No answer: {why}.")}
            group = ctx["group"]
            if group and reply["sql"]:
                trace.append({"step": "group", "unit": group["unit"], "behaviour": group["behaviour"], "overruled": reply["behaviour"]})
                return {"answer": stop(out, group["behaviour"], group["reason"],
                                       **({"joins_refused": group["joins_refused"]} if group["joins_refused"] else {}))}
            decided = ctx.get("decided")
            if decided:
                trace.append({"step": "context", "behaviour": decided["behaviour"], "reply": reply["behaviour"]})
                more = {k: v for k, v in decided.items() if k not in ("behaviour", "reason", "joins_refused")}
                return {"answer": stop(out, decided["behaviour"], decided["reason"], **({"joins_refused": decided["joins_refused"]}
                                                                                        if decided.get("joins_refused") else {}), **more)}
            runtrace.stage("access")
            access = self.g.join_access(principal, ctx["needed"] + list(reply["sql"]))
            if not access["allowed"]:
                denied = access["denied_warehouses"]
                trace.append({"step": "access", "units": access["warehouses_involved"], "denied": denied})
                if len(access["warehouses_involved"]) > 1:
                    return {"answer": stop(out, "blocked_access", f"Cross-warehouse query blocked: {principal} may not read "
                                                                  f"{', '.join(denied)}. No warehouse was queried.", denied_warehouses=denied,
                                           sql_run=[], allowed_warehouses=access["allowed_warehouses"],
                                           warehouses_involved=access["warehouses_involved"])}
                return {"answer": stop(out, "refuse", f"{principal} may not read {', '.join(denied)}. No warehouse was queried.",
                                       denied_warehouses=denied)}
            runtrace.stage("gate")
            guards = {u: self.gate(ctx, u, q) for u, q in reply["sql"].items()}
            out["attempts"] += [{"try": attempt, "unit": u, "sql": reply["sql"][u], **guards[u]} for u in reply["sql"]]
            if all(gd["passed"] for gd in guards.values()):
                break
            reasons = "\n".join(f"  {u}: " + "; ".join(gd["problems"]) for u, gd in guards.items() if not gd["passed"])
            if attempt == 2:
                return {"answer": stop(out, "abstain", "No query passed the gate, after one retry. " + reasons.strip())}
            runtrace.stage("plan")
            reply, why = self.plan(prompt + RETRY.format(reasons=reasons), ctx["block"])
        trace.append({"step": "plan", "behaviour": reply["behaviour"], "units": list(reply["sql"]),
                      "tries": len({a["try"] for a in out["attempts"]})})
        return {"reply": reply, "guards": guards}

    def run_step(self, t: Turn) -> dict:
        """Run what passed the gate, and bind each figure to a citation (execute)."""
        if "answer" in t:
            return {}
        return {"result": self.execute(t["reply"], t["ctx"], t["out"], t["guards"])}

    def answer_step(self, t: Turn) -> dict:
        """Render the answer, and check the model's wording (render)."""
        if "answer" in t:
            return {}
        return {"answer": self.render(t["result"], t["reply"], t["question"], t["ctx"])}

    def rules_step(self, t: Turn) -> dict:
        """The planner and the template on the arm's config (Engine.ruled, which gates and runs every query it plans, its
        columns as the warehouse names them: the agent binds each to its citation by them, and names them by their business
        names when it answers, ask). Graph
        on, a customer group is the graph's to decide first. A real run, labelled as one."""
        question, principal, purpose, cfg = t["question"], t["principal"], t["purpose"], t["cfg"]
        return {"planned": (self.grouped(question, principal, purpose, cfg) if cfg.graph else None)
                or self.e.ruled(question, principal, purpose, config=cfg)}

    def evidence_step(self, t: Turn) -> dict:
        """The evidence step both paths share, on the arm's context through the context contract (evidence)."""
        r = self.evidence(t["question"], t["principal"], t["purpose"], t["arm"], t["planned"])
        return {"answer": {**r, "agent": {**t["info"], "sql_source": "planner", "renderer": "template"}}}

    def plan(self, prompt: str, block: str) -> tuple[dict | None, str | None]:
        """(the model's reply, checked against the contract, None) or (None, why there is none)."""
        raw, failed = self.call("sql", prompt, block)
        if failed:
            return None, failed
        try:
            return read_reply(raw, self.e), None
        except ContractError as exc:
            return None, "the model's reply broke the contract: " + "; ".join(exc.problems)

    def execute(self, reply: dict, ctx: dict, out: dict, guards: dict) -> dict:
        """Run what passed the gate and bind each figure to a citation: the result the template renders. Each figure
        names the column the gate found it measures: the one the reply names if it is among them, and carries
        what the context says of that column (its grain, currency, calendar and conversion), if anything."""
        b = reply["behaviour"]
        runtrace.stage("execute")
        figures = []
        for n, (unit, sql) in enumerate(reply["sql"].items(), 1):
            try:
                row = self.row(ctx, unit, sql)
                value = row[0] if row else None
                if isinstance(value, bool) or (value is not None and not isinstance(value, (int, float, Decimal))):
                    raise TypeError(f"the query returned {type(value).__name__}, not a number")
                value, error = (float(value) if value is not None else None), None
            except RequestDeadlineExceeded:
                raise
            except Exception as exc:
                transient_failure(exc)
                value, error = None, WAREHOUSE_PROBLEM
            measured, claimed = guards[unit]["measured"], reply["columns"].get(unit)
            column = claimed if claimed in measured else measured[0]
            figures.append({"id": f"f{n}", "unit": unit, "value": value, "sql": sql, "join_path": join_path(sql), "column": column,
                            **ctx["meta"].get((unit, column), {}), **({"error": error} if error else {})})
        citations, rejected = self.cite(ctx, reply["citations"], figures)
        result = {**out, "behaviour": b, "citations": citations, "cite": [c["doc"] for c in citations], "citations_rejected": rejected,
                  "joins_refused": reply["joins_refused"], "references": references(ctx)}
        if figures:
            result.update(figures=figures, queries=dict(reply["sql"]), parts={f["unit"]: f["value"] for f in figures})
        missing = [f for f in figures if f["value"] is None]
        if b == "figure":
            f = figures[0]
            if missing:
                return {**result, "behaviour": "abstain", "reason": f"{f['unit']}: {f.get('error') or 'the query returned no value'}"}
            result.update(figure=f["value"], unit=f["unit"], column=f["column"], join_path=f["join_path"], sql=f["sql"])
        elif b in ("sum_with_verdict", "parts_with_reason"):
            result.update(commensurable=b == "sum_with_verdict" and not missing, missing_parts=[f["unit"] for f in missing])
            if missing:
                result.update(behaviour="parts_with_reason", reason="incomplete: " + "; ".join(
                    f"{f['unit']} {f.get('error') or 'returned no value'}" for f in missing) + ". A total without every part is not the total asked for")
            elif b == "sum_with_verdict":
                result["figure"] = round(sum(f["value"] for f in figures), 2)
        elif b == "cite_certified" and not citations:
            why = "; ".join(f"[{x['n']}] " + (f"{x['doc']}, " if x.get("doc") else "") + x["why"] for x in rejected)
            result.update(behaviour="abstain", reason="No answer: the reply cites no source this context may cite" + (f" ({why})" if why else "") + ".")
        return result

    def cite(self, ctx: dict, numbers: list[int], figures: list[dict]) -> tuple[list[dict], list[dict]]:
        """The evidence step, the same on rules and with a model: a citation only for a source the context numbered
        and may cite; each figure bound to the citation that defines what it measures, failing that by unit (graph off, then any)."""
        runtrace.stage("bind")
        by_n = {s["n"]: s for s in ctx["sources"]}
        citations, rejected = [], []
        for n in numbers:
            s = by_n.get(n)
            if s is None:
                rejected.append({"n": n, "why": f"no source {n} in the context"})
            elif not s["citable"]:
                rejected.append({"n": n, "doc": s["doc"], "why": f"{s['tier']}: for reasoning and reference, never cited"})
            else:
                citations.append({k: s.get(k) for k in ("n", "doc", "chunk", "title", "tier", "cited_as", "version", "attribution", "unit")}
                                 | {"why": s.get("why") or "found by search", "terms": list(s.get("terms") or [])})
        for f in figures:
            f["cite"] = bind(f, citations, ctx["defines"], set(ctx["in_window"]), own=ctx["arm"] == "treatment")
        return citations, rejected

    def evidence(self, question: str, principal: str, purpose: str, arm: str, r: dict) -> dict:
        """On rules, after the planner: the arm's context, as a model would be given it (fetch), and each figure the
        planner ran bound by the evidence step. The rules cite, for each figure, the source bind picks among
        the citable ones: in the treatment the tier rule's citation for its column's certified term (gold as
        the authority, silver with attribution), else one about its unit, else none; in the control what plain
        search returned. What the planner answered is unchanged; an answer with no figure is the planner's alone."""
        figures = planned_figures(r)
        if not figures:
            return r
        ctx = self.fetch(question, principal, purpose, arm)
        citable = [s for s in ctx["sources"] if s["citable"]]
        own = ctx["arm"] == "treatment"
        chosen = list(dict.fromkeys(n for f in figures if (n := bind(f, citable, ctx["defines"], set(ctx["in_window"]), own)) is not None))
        citations, rejected = self.cite(ctx, chosen, figures)
        trace = r["trace"] + [{"step": "context", "arm": arm, "sources": [[s["n"], s["doc"], s["citable"]] for s in ctx["sources"]],
                               "named": ctx["named"], "hash": digest(ctx["block"])},
                              {"step": "bind", "figures": {f["id"]: f["cite"] for f in figures}}]
        return {**r, "trace": trace, "figures": figures, "citations": citations, "cite": [c["doc"] for c in citations],
                "citations_rejected": rejected, "references": references(ctx)}

    def render(self, r: dict, reply: dict, question: str, ctx: dict) -> dict:
        """The template, always; the model's wording (its reason, its answer) only if every number in it is
        in the result, every [n] names a citation and every document it names is one its context gave and
        did not withhold. The number check covers all the model's wording."""
        runtrace.stage("render")
        markers = {c["n"] for c in r["citations"]}
        allowed = supported(r, question)
        ids = self.document_ids()
        told = [question] + [p for t in r.get("attempts") or [] for p in t["problems"]]
        given = (documents_in(ctx["block"], ids) - set(ctx["withheld"])) | documents_in("\n".join(told), ids)
        stray: list[str] = []
        bad: list[int] = []
        docs: list[str] = []
        checked = False
        reason = r.get("reason")
        if not reason and reply["reason"]:
            checked = True
            stray, bad = wording(reply["reason"], allowed, markers, carried(r, question))
            docs = sorted(documents_in(reply["reason"], ids) - given)
            reason = ("(the model's reason is withheld: it carried a number, marker or document the result does not hold)"
                      if stray or bad or docs else reply["reason"])
        r = {**r, "reason": reason or ""}
        text = template(r, self.e)
        said, phrased_by = "", "template"
        if r["behaviour"] in self.RUNS_SQL:
            facts = {"behaviour": r["behaviour"], "total": fmt(r["figure"]) if r["behaviour"] == "sum_with_verdict" else None,
                     "figures": [{"unit": self.e.UNIT_LABEL[f["unit"]], "value": fmt(f["value"]), "column": f["column"], "cite": f["cite"]}
                                 for f in r["figures"]],
                     "reason": r["reason"], "citations": [{"n": c["n"], "doc": c["doc"], "title": c["title"], "version": c["version"]}
                                                          for c in r["citations"]]}
            raw, failed = self.call("phrase", self.prompt("phrase", question, facts=facts))
            said = str(raw.get("answer_text") or "").strip() if isinstance(raw, dict) else ""
            if failed:
                no_fallback("the agent's phrase step", failed)
                phrased_by = f"template ({failed})"
        elif reply["behaviour"] not in self.RUNS_SQL and r["behaviour"] == reply["behaviour"]:
            said = reply["answer_text"]
        if said:
            checked = True
            s, m = wording(said, allowed, markers, carried(r, question))
            d = sorted(documents_in(said, ids) - given)
            stray, bad, docs = stray + s, bad + m, docs + d
            if s or m or d:
                why = ([f"carried numbers or markers not in the result: {', '.join(s[:3] + [f'[{x}]' for x in m[:3]])}"] if s or m else []) \
                    + ([f"named documents its context did not give or said not to use: {', '.join(d[:3])}"] if d else [])
                phrased_by = "template (model text " + "; and ".join(why) + ")"
            else:
                text, phrased_by = said, self.source
        if checked:
            runtrace.number_check(passed=not (stray or bad or docs), stray_numbers=len(stray), bad_markers=len(bad), bad_documents=len(docs))
        r["trace"].append({"step": "answer", "phrased_by": phrased_by})
        return {**r, "answer_text": text, "phrased_by": phrased_by}


def glossary(cited: dict) -> dict:
    """The tier rule's answer on a term no document defines (Engine.from_glossary), as the code holds it whatever the reply:
    the pack's glossary's definition, citing nothing, with what the question retrieved as references."""
    return {k: v for k, v in cited.items() if k not in ("superseded_not_cited", "not_ai_ready", "not_readable")}


def references(ctx: dict) -> list[dict]:
    """The context's sources that may not be cited: bronze, for reasoning and reference."""
    return [{"n": s["n"], "doc": s["doc"], "tier": s.get("tier"), "title": s.get("title"), "why": s.get("why")} for s in ctx["sources"] if not s["citable"]]


def planned_figures(r: dict) -> list[dict]:
    """Each figure the planner ran, in the shape the model path gives one: a figure, or each part it ran across units."""
    if r.get("behaviour") == "figure":
        parts = [{"unit": r["unit"], "value": r["figure"], "column": r["column"], "sql": r["sql"], "join_path": r.get("join_path") or []}]
    elif isinstance(r.get("plan"), list):
        parts = [{"unit": p["unit"], "value": p.get("value"), "column": p["column"], "sql": p["sql"], "join_path": p.get("join_path") or []}
                 for p in r["plan"] if p.get("status") == "ok"]
    else:
        parts = []
    return [{"id": f"f{n}", **p} for n, p in enumerate(parts, 1)]


def stop(out: dict, behaviour: str, reason: str, **more) -> dict:
    """An answer that ends before any query runs: the code's reason, as the template words it."""
    runtrace.stage("render")
    out["trace"].append({"step": "answer", "phrased_by": "template"})
    return {**out, "behaviour": behaviour, "reason": reason, **more, "answer_text": reason, "phrased_by": "template"}

def template(r: dict, domain) -> str:
    """The answer from the structured result alone. domain: the Engine, or anything that carries the client's domain
    as it does (UNIT_LABEL here)."""
    b, reason = r["behaviour"], r.get("reason") or ""
    label = domain.UNIT_LABEL
    mark = lambda f: f" [{f['cite']}]" if f.get("cite") else ""  # noqa: E731
    parts = "; ".join(f"{label[f['unit']]} {fmt(f['value'])}{mark(f)}" for f in r.get("figures") or [])
    if b == "figure":
        f = r["figures"][0]
        return f"{label[f['unit']]}: {fmt(f['value'])}" + (f" ({f['column']})" if f["column"] else "") + f"{mark(f)}."
    if b == "sum_with_verdict":
        return f"{fmt(r['figure'])}, the sum of {parts}." + (f" {reason}" if reason else "")
    if b == "parts_with_reason":
        return f"Not added: {reason or 'the parts do not add'}. {parts}."
    if b == "cite_certified":
        cites = ", ".join(f"{c['doc']} [{c['n']}]" for c in r["citations"]) or "nothing the answer may cite"
        return (f"{reason} " if reason else "") + f"Sources: {cites}."
    return reason or {"refuse": "Refused.", "abstain_route_steward": "Not a term yet: routed to the steward."}.get(b, "No answer.")


def main() -> int:
    """The command line. Settings that name no model for the answer tier give one line, never a traceback, and a
    context service that refuses, or that nothing serves at CONTEXT_URL, gives no package and so no answer."""
    from kge.adapters.model import ModelSettingError
    from kge.pipeline.profile import ProfileError, resolve
    from kge.adapters.graphstore import GraphLocked
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--profile", help="the profile whose graph to ask (default: PROFILE, else the registry's default)")
    ap.add_argument("--arm", default="both", help="control, treatment or both")
    ap.add_argument("--as", dest="principal", help="the querying identity to ask as (default: the one the profile's deployment "
                                                   "names, asker in its identities)")
    ap.add_argument("--json", action="store_true", help="print each answer whole, with its run trace")
    args = ap.parse_args()
    arms = list(AGENT_ARMS) if args.arm == "both" else [args.arm]
    try:
        prof = resolve(args.profile)
        who = prof.asking(args.principal)
        eng = Engine.serving(prof)
    except (ProfileError, GraphLocked) as exc:
        print(f"agent: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            agent = Agent(eng)
        except ModelSettingError as exc:
            print(f"agent: {exc}", file=sys.stderr)
            return 2
        for arm in arms:
            try:
                a = runtrace.run("agent", lambda: agent.ask(args.question, who, arm=arm), graph=eng.g)
            except ContextError as exc:
                print(f"agent: {exc}", file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps(a, indent=2, default=str))
                continue
            text = a.get("answer_text") or a.get("reason") or ""
            refused = [t for t in a.get("attempts") or [] if not t["passed"]]
            if refused:
                text = f"gate refused try {refused[0]['try']} ({refused[0]['unit']}): {refused[0]['problems'][0]}\n  " + text
            if a.get("phrased_by", "").startswith("template ("):
                text += f"\n  {a['phrased_by']}"
            parts = a.get("parts") if isinstance(a.get("parts"), dict) and a["behaviour"] != "figure" else {}
            print(f"{arm} ({a['agent']['mode']}, config {a['arm']}): {a['behaviour']}"
                  + (f" {fmt(a['figure'])}" if a.get("figure") is not None else "")
                  + (f" from {a['column']}" if a.get("column") else "")
                  + (" parts " + ", ".join(f"{u} {fmt(v)}" for u, v in parts.items()) if parts else "")
                  + (f" cites {', '.join(a['cite'])}" if a.get("cite") else "") + (f"\n  {text}" if text else ""))
    finally:
        eng.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
