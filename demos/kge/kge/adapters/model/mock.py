"""The mock model, MODEL_PROVIDER=mock: offline, deterministic, and labelled mock wherever it runs.

No model key is configured for this demo, so the reference agent's model path
(kge/services/ask/engine/agent.py) is proven end to end with this stand-in. It reads only the
prompt it is sent, as a model would, and answers the agent's contract the way a
careful reader of that prompt would:

  sql     from the context block. Given the graph's package (graph on), each
          query is built from "Columns by role" (the measure, its filters, the
          period and the declared keys), the gold and silver sources are cited
          by number, and the behaviour comes from the scope, "Do not use",
          "Not glossary terms", "One customer group" and "Across units". A
          term the package gives as a context ("Contexts") is answered from it
          alone: its query from the parts it lists, a query of two units
          through the certified join it names, or the reply it says the graph
          decided. Given only the warehouse schema (graph off), a column is
          guessed from the question's own words and what search found is cited.
  phrase  the answer, worded from the facts it is given and nothing else.
  draft   onboarding's drafting steps (kge/estate/draftflow.py), for the labelled bancassurance Markdown fixture only:
          fixed replies, not a Markdown reader. Its first read proposes one recipe on a column that does not
          exist; the recipes step's checker refuses it, and the retry, which carries the checker's error, corrects
          the column. So the retry path runs offline, the same way every time. The standards step, for any unit:
          its first reply maps the first drafted term to an IRI the platform does not preload, which the checker
          refuses; the retry takes each term's first candidate from the rules' list the prompt carries.

So the two arms' replies differ only because their context blocks do. It knows
no other prompt: the tagger and /ask stay on rules under it. The client's units
are the ones the prompt names, as a model would read them (prompt_units).

It is a LangChain chat model, as every model of the demo is (kge/adapters/model/providers.py):
ChatMock, one human message in and reply()'s answer out, in its mode, served by
"mock", its tokens counted as the words of the prompt and the reply.

MOCK_MODE (or LLM(..., mode=...)) makes it an adversary the seam must catch:

  careful            no adversary (the default)
  restricted_column  the first plan reads restricted personal data: the gate refuses, the retry is careful
  undeclared_join    the first plan joins two tables on a key guessed by its name, which no declared join links:
                     the gate refuses, the retry is careful
  constant_figure    the first plan selects a constant as the figure: the gate refuses, the retry is careful
  scaled_figure      the first plan doubles the aggregate: the gate refuses, the retry is careful
  key_name_match     across a certified join, the first plan joins the two units' customer keys to each other on their
                     names, not through the cross-reference: the gate refuses, the retry is careful
  missing_certification
                     across a certified join, the first plan leaves out the cross-reference's certification filter:
                     the gate refuses, the retry is careful
  birth_date_match   across a certified join, the first plan matches on a date of birth, restricted personal data:
                     the gate refuses, the retry is careful
  outer_join_match   across a certified join, the first plan LEFT JOINs the other unit's table to the cross-reference
                     and moves the certification filter and the window into that join's ON, where an outer join keeps
                     the matches they leave out: the gate refuses, the retry is careful
  second_alias_match across a certified join, the first plan puts the certification filter and the window on a second
                     alias of the cross-reference, while the alias the match goes through carries none: the gate
                     refuses, the retry is careful
  malformed_json     the plan is not one JSON object: an abstention, and nothing runs
  invented_number    the wording carries a number the result does not hold: the template is kept
  bad_marker         a citation and a [n] marker that name no source: both are rejected

A mock run proves the seam, not a model's quality.
"""
from __future__ import annotations

import json
import re

import sqlglot
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from sqlglot import exp

from kge.adapters.model.providers import MOCK, one_prompt, result

MODES = ("careful", "restricted_column", "undeclared_join", "constant_figure", "scaled_figure",
         "malformed_json", "invented_number", "bad_marker", "key_name_match", "missing_certification", "birth_date_match",
         "outer_join_match", "second_alias_match")
IMPROVE_MODES = ("careful", "unallowlisted_section", "certified_true", "unknown_column", "invented_alias", "duplicate_alias")
ACROSS = ("key_name_match", "missing_certification", "birth_date_match", "outer_join_match",
          "second_alias_match")
FIRST_PLAN = ("restricted_column", "undeclared_join", "constant_figure", "scaled_figure", *ACROSS)
NAMES_UNITS = re.compile(r"business units?: ([^.]+)\.")
CONTEXT_OPEN, CONTEXT_CLOSE = "<context>", "</context>"
MISS_OPEN, MISS_CLOSE = "<miss>", "</miss>"
SQL_TASK, PHRASE_TASK = "You are a text-to-SQL agent", "Write the answer to the analyst's question"
IMPROVE_TASK = "You are a knowledge-graph improvement proposer"
RETRY = "\n\nYour last reply was refused by the SQL gate, and nothing ran:\n"
RETRY_END = "Reply again with the whole JSON object."
SEMANTIC_TASK = "Onboarding drafting step:"
SOURCE = re.compile(r"\s*\[(\d+)\] (\S+) \"([^\"]*)\"(.*)")
PERSONAL = re.compile(r"email|date_of_birth|first_name|last_name")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
COMMON = set("""a about across all an and any are as at average bank by calendar count date day did do does during enterprise
fiscal for from fy give group had has have how in into is it many me month much number of on or over per quarter show tell
than that the their there these they this those to was were what when where which who whose with year""".split()) | set(MONTHS)
DEFINE = re.compile(r"(what is the definition|what is an? |what makes|how is |how are |explain |which (calendar|version|fiscal)|"
                    r"is |are |does |do |may |where |can (?!you))")
JOIN = re.compile(r"\b(join|joined|match|matches|line up|also)\b")
ADD = re.compile(r"\b(combined|total|add|sum|plus|across)\b")
COUNT = re.compile(r"\b(how many|number of|count)\b")
NUMERIC = re.compile(r"DECIMAL|DOUBLE|FLOAT|INTEGER|BIGINT|NUMERIC")


class ChatMock(BaseChatModel):
    """The mock as a LangChain chat model: one human message in, reply()'s answer to it out, in its mode, served by mock,
    its tokens counted as the words of the prompt and the reply. Offline."""
    mode: str = "careful"

    @property
    def _llm_type(self) -> str:
        return MOCK

    @property
    def _identifying_params(self) -> dict:
        return {"model": MOCK, "mode": self.mode}

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None, **kwargs) -> ChatResult:
        prompt = one_prompt(messages)
        text = reply(prompt, self.mode)
        return result(text, MOCK, len(prompt.split()), len(text.split()))


def reply(prompt: str, mode: str = "careful") -> str:
    """The mock's reply to one of the agent's prompts. Raises RuntimeError for any other."""
    if mode not in MODES and mode not in IMPROVE_MODES:
        raise ValueError(f"MOCK_MODE must be one of {', '.join((*MODES, *IMPROVE_MODES))}, not {mode!r}")
    if prompt.startswith(SQL_TASK) and CONTEXT_OPEN in prompt and CONTEXT_CLOSE in prompt:
        return plan(prompt, mode)
    if prompt.startswith(PHRASE_TASK) and "\nFacts: " in prompt and "\n\nQuestion: " in prompt:
        return phrase(prompt, mode)
    if prompt.startswith(IMPROVE_TASK) and MISS_OPEN in prompt and MISS_CLOSE in prompt:
        return improve_reply(prompt, mode)
    if prompt.startswith(SEMANTIC_TASK) and "<semantic-file>" in prompt and "</semantic-file>" in prompt:
        return draft(prompt, mode)
    raise RuntimeError("the mock model answers only the reference agent's prompts (kge/services/ask/engine/agent.py)")


DRAFT_TABLES = [{"name": "premium", "description": "Written premiums", "columns": [
    {"name": "premium_id", "type": "BIGINT", "description": "Premium identifier", "key": "primary"},
    {"name": "accounting_date", "type": "DATE", "description": "Accounting date"},
    {"name": "premium_amount", "type": "DECIMAL(18,2)", "description": "Written premium"}]}]
DRAFT_MEASURE = {"table": "premium", "column": "premium_amount", "term": "written_premium", "name": "Written Premium",
                 "definition": "The written premium amount.", "currency": "GBP", "time": "accounting_date",
                 "aliases": [], "filters": [{"column": "premium.premium_amount", "op": ">", "value": 0}]}


def draft(prompt: str, mode: str) -> str:
    """One drafting step's reply. The step, and whether this is a retry, are read from the prompt's own lines before
    its tagged blocks, never from the blocks, which hold untrusted data and may say anything. Its tables are the
    labelled fixture's facts as a model would read them from examples/bancassurance-markdown/semantic.md."""
    if mode == "malformed_json":
        return '{"tables":'
    head = prompt.split("\n<", 1)[0]
    step = head[len(SEMANTIC_TASK):].split(".", 1)[0].strip()
    corrected = "Your previous reply was refused by the checks" in head
    if step in {"read", "tables", "recipes"}:
        measure = dict(DRAFT_MEASURE)
        if step == "read" and not corrected:
            measure["filters"] = [{"column": "premium.premium_amount_gbp", "op": ">", "value": 0}]
        return json.dumps({"tables": DRAFT_TABLES, "measures": [measure]})
    if step == "definitions":
        return json.dumps({"definitions": [{"term": "written_premium", "aliases": ["gross written premium"],
                                            "definition": "The sum of premium_amount (GBP) by accounting_date; "
                                                          "refunds are excluded."}]})
    if step == "golden":
        return json.dumps({"golden": [{"term": "written_premium", "subject": "gross written premium"}]})
    if step == "standards":
        return standards(prompt, corrected)
    raise RuntimeError(f"the mock model has no reply for drafting step {step!r}")


NOT_PRELOADED = "https://example.com/ontology/not-preloaded"
DRAFTED_TERM = re.compile(r"term (\S+) \|.*\| candidates: (.*)$")


def standards(prompt: str, corrected: bool) -> str:
    """The standards step's reply, read from the drafted terms and the rules' candidates in <drafted>, as a model reads
    them: first an IRI the platform does not preload, then, told why, each term's first candidate."""
    block = prompt.split("\n<drafted>\n", 1)[1].split("\n</drafted>", 1)[0] if "\n<drafted>\n" in prompt else ""
    terms = [m.groups() for m in map(DRAFTED_TERM.match, block.splitlines()) if m]
    if not corrected and terms:
        return json.dumps({"mappings": [{"term": terms[0][0], "iri": NOT_PRELOADED, "relation": "broadMatch",
                                         "why": "mock: a concept the platform does not preload"}]})
    out = []
    for term, candidates in terms:
        first = candidates.split("; ")[0]
        if first != "none":
            concept, relation = first.rsplit(" ", 1)
            out.append({"term": term, "iri": concept.split("=", 1)[1], "relation": relation,
                        "why": "mock: the rules' first candidate"})
    return json.dumps({"mappings": out})


def plan(prompt: str, mode: str) -> str:
    """The sql task's reply. It is a retry only when the refusal block is the last, as the agent appends it, so a
    question that quotes the block is not one; told why, a first-plan adversary answers carefully, and any other mode
    never sends the refused query again."""
    block, tail = prompt.split(CONTEXT_OPEN, 1)[1].split(CONTEXT_CLOSE, 1)
    retry = RETRY in tail and tail.endswith(RETRY_END)
    asked, refusal = tail.rsplit(RETRY, 1) if retry else (tail, "")
    question = asked.split("Question:", 1)[1].strip()
    ctx = Context(block.strip("\n"), prompt_units(prompt))
    ctx.googlesql = GOOGLESQL in prompt.split(CONTEXT_OPEN, 1)[0]
    careful, parts = Reader(ctx, question).reply()
    if mode == "malformed_json":
        return '{"behaviour": "' + careful["behaviour"] + '", "sql": {'
    if retry:
        if mode in FIRST_PLAN:
            return said(careful, ctx)
        return said(after_refusal(refusal), ctx)
    if mode in FIRST_PLAN:
        bad = adversary(mode, ctx, careful, parts)
        if bad:
            return said(bad, ctx)
    if mode == "bad_marker":
        bogus = max([s["n"] for s in ctx.sources] + [0]) + 1
        careful["citations"] = careful["citations"] + [bogus]
        if careful["answer_text"]:
            careful["answer_text"] += f" See also [{bogus}]."
    if mode == "invented_number" and careful["answer_text"]:
        careful["answer_text"] += " It was 7,919.13 the year before."
    return said(careful, ctx)


GOOGLESQL = "BigQuery GoogleSQL"


def said(reply: dict, ctx: Context) -> str:
    """The reply as the mock sends it: its queries in the dialect the prompt asks for. Asked for BigQuery's GoogleSQL
    (the warehouses' query_dialect), each table is a backticked name and a month of dates is DATE_TRUNC(d, MONTH)
    (period), as a model asked for GoogleSQL writes them; asked for DuckDB's, the text is what it always was."""
    if not ctx.googlesql or not reply.get("sql"):
        return json.dumps(reply)
    written = {}
    for unit, sql in reply["sql"].items():
        tree = sqlglot.parse_one(sql, read="bigquery")
        for table in tree.find_all(exp.Table):
            table.this.set("quoted", True)
        written[unit] = tree.sql("bigquery")
    return json.dumps({**reply, "sql": written})


def prompt_units(prompt: str) -> list[str]:
    """The client's units, as the prompt names them, in its order: none if it names none."""
    m = NAMES_UNITS.search(prompt.split("\n", 1)[0])
    if not m:
        return []
    return [u.strip() for u in m.group(1).replace(" and ", ", ").split(",") if u.strip()]


def after_refusal(reasons: str) -> dict:
    """A careful reader the gate refused: restricted data is a refusal, anything else an abstention."""
    said = " ".join(line.strip() for line in reasons.splitlines() if line.startswith("  "))
    if "restricted personal data" in said:
        return no_query("refuse", "The question needs restricted personal data, which no query may read: " + said)
    return no_query("abstain", "No query this context supports passed the gate: " + said)


def adversary(mode: str, ctx: Context, careful: dict, parts: dict) -> dict | None:
    """The first plan of an adversarial mode: a figure query the gate must refuse."""
    unit = next(iter(parts), None)
    p = parts.get(unit)
    sql: str | None = None
    if mode == "restricted_column":
        pool = ([(u, t, c) for u, t, c in ctx.restricted]
                or [(u, t, c) for u in ctx.schema for t, cols in ctx.schema[u].items() for c, _ in cols["columns"] if PERSONAL.search(c)])
        pool = [r for r in pool if r[0] in ctx.readable]
        u, t, c = next((r for r in pool if r[0] == unit), pool[0] if pool else (None, None, None))
        unit, sql = u, f"SELECT count({t}.{c}) FROM {t}" if t else None
    elif p and mode == "undeclared_join":
        guess = ctx.guessed_key(unit, p["from"])
        if guess:
            c, other = guess
            sql = query(p["select"], p["from"], [f"JOIN {other} ON {p['from']}.{c} = {other}.{c}"] + p["joins"], p["where"])
    elif p and mode == "constant_figure":
        sql = f"SELECT 99999.0 AS total FROM {p['from']} LIMIT 1"
    elif p and mode == "scaled_figure":
        sql = query(p["select"] + " * 2", p["from"], p["joins"], p["where"])
    elif p and mode in ACROSS and p.get("across"):
        sql = query(p["select"], p["from"], p["joins"], [crossed(mode, w, p["across"][0], ctx) for w in p["where"]], p.get("group"))
    if not (unit and sql):
        return None
    return {**careful, "behaviour": "figure", "sql": {unit: sql}, "columns": {}, "joins_refused": [], "reason": "", "answer_text": ""}


def crossed(mode: str, where: str, across: dict, ctx: Context) -> str:
    """A condition of a query of two units, as an adversary writes it: the semi-join through the cross-reference
    replaced by the two units' keys joined on their names, stripped of its certification filter (a comparison with a
    string), matched on a date of birth, outer-joined with its conditions in the ON, or with its conditions on a second
    alias of the cross-reference. Any other condition as it was."""
    t = across["through"]
    if not re.search(rf"\bAS {t}\b", where):
        return where
    if mode in ("outer_join_match", "second_alias_match"):
        return misplaced(mode, where, t, across["near"])
    far_alias, far_col = across["far"].split(".")
    if mode == "key_name_match":
        ref = re.search(rf"(\w+\.\w+) AS {far_alias}\b", where)
        return f"EXISTS (SELECT 1 FROM {ref.group(1)} AS {far_alias} WHERE {across['far']} = {across['near']})" if ref else where
    if mode == "missing_certification":
        return re.sub(r"(WHERE |AND )\w+\.\w+ = '[^']*'( AND )?", lambda m: m.group(1) if m.group(2) else "", where, count=1).replace(" AND )", ")")
    ref = re.search(rf"(\w+)\.(\w+) AS {far_alias}\b", where)
    dob = next((c for u, tb, c in ctx.restricted if ref and (u, tb) == ref.groups() and re.search("birth|date", c)), None)
    return where.replace(f"ON {across['far']} ", f"ON {far_alias}.{dob} ", 1) if dob else where


def misplaced(mode: str, where: str, t: str, near: str) -> str:
    """The semi-join through the cross-reference t (EXISTS (SELECT 1 FROM <cross-reference> AS t JOIN <other table> ON ...
    WHERE ...)) with t's own conditions, its certification filter and window, where they do not hold: in the ON of its
    join made LEFT (outer_join_match), or on a second alias of t joined to near on the same key, the alias the match
    goes through left with none (second_alias_match). As it was if it has no such conditions."""
    tree = sqlglot.parse_one(where, read="duckdb")
    sub = next((s for s in tree.find_all(exp.Select) if s.args.get("from_") is not None and s.args["from_"].this.alias_or_name == t), None)
    if sub is None or not sub.args.get("joins") or sub.args.get("where") is None:
        return where
    conds = list(sub.args["where"].this.flatten()) if isinstance(sub.args["where"].this, exp.And) else [sub.args["where"].this]
    own = [c for c in conds if not c.find(exp.Exists) and {x.table for x in c.find_all(exp.Column)} == {t}]
    near_alias = near.split(".")[0]
    link = next((c for c in conds if isinstance(c, exp.EQ) and {x.table for x in c.find_all(exp.Column)} == {t, near_alias}), None)
    if not own or (mode == "second_alias_match" and link is None):
        return where
    if mode == "outer_join_match":
        j = sub.args["joins"][0]
        j.set("side", "LEFT")
        j.set("on", exp.and_(j.args["on"], *own))
        rest = [c for c in conds if all(c is not o for o in own)]
        sub.set("where", exp.Where(this=exp.and_(*rest)) if rest else None)
        return tree.sql("duckdb")
    t2 = f"{t}_2"
    key = next(x.name for x in link.find_all(exp.Column) if x.table == t)
    second = sub.args["from_"].this.copy()
    second.set("alias", exp.TableAlias(this=exp.to_identifier(t2)))
    sub.append("joins", exp.Join(this=second, on=sqlglot.parse_one(f"{t2}.{key} = {near}", read="duckdb")))
    for c in own:
        for x in c.find_all(exp.Column):
            if x.table == t:
                x.set("table", exp.to_identifier(t2))
    return tree.sql("duckdb")


def no_query(behaviour: str, reason: str, citations: list[int] | None = None, answer: str | None = None, **more) -> dict:
    return {"behaviour": behaviour, "sql": {}, "columns": {}, "citations": citations or [], "joins_refused": [],
            "reason": reason, "answer_text": answer if answer is not None else reason, **more}


def query(select: str, table: str, joins: list[str], where: list[str], group: str | None = None) -> str:
    head = f"{group}, {select}" if group else select
    return (f"SELECT {head} FROM {table}" + "".join(f" {j}" for j in joins) + (" WHERE " + " AND ".join(where) if where else "")
            + (f" GROUP BY {group}" if group else ""))


class Context:
    """The context block, read section by section, as the agent writes it."""

    def __init__(self, block: str, units: list[str] = ()):
        self.googlesql = False
        self.units: list[str] = list(units)
        self.readable: list[str] = []
        self.named: list[str] = []
        self.denied: list[str] = []
        self.schema: dict[str, dict[str, dict]] = {}
        self.sources: list[dict] = []
        self.terms: list[dict] = []
        self.not_terms: list[tuple[str, str]] = []
        self.blocks: list[dict] = []
        self.no_join: list[tuple[str, str, str]] = []
        self.adding: tuple[bool, str] | None = None
        self.never_added: str | None = None
        self.group: tuple[str, str] | None = None
        self.restricted: list[tuple[str, str, str]] = []
        self.joins: dict[str, list[tuple[tuple[str, str], tuple[str, str]]]] = {}
        self.contexts: dict | None = None
        for para in block.split("\n\n"):
            lines = para.split("\n")
            head = re.match(r"[A-Za-z ]*", lines[0]).group().strip()
            getattr(self, "read_" + head.lower().replace(" ", "_"), lambda _lines: None)(lines)

    def read_scope(self, lines):
        s = lines[0]
        units = lambda m: [u for u in self.units if m and re.search(r"\b" + re.escape(u) + r"\b", m.group(1))]  # noqa: E731
        self.readable = units(re.search(r"may read:? ([^.:]*)", s))
        self.named = units(re.search(r"The question names ([^.]*)\.", s))
        self.denied = units(re.search(r"You may not read ([^:]*):", s))

    def read_warehouse_schema(self, lines):
        unit = None
        for line in lines[1:]:
            if not line.startswith(" "):
                unit = line.strip()
                self.schema[unit] = {}
                continue
            table, rest = line.strip().split(": ", 1)
            parts = rest.split("; ")
            cols = [tuple(c.split(" ", 1)) for c in parts[0].split(", ")]
            refs = []
            for p in parts[1:]:
                if p.startswith("references "):
                    for r in p.removeprefix("references ").split(", "):
                        col, target = r.split(" -> ")
                        refs.append((col, *target.split(".")))
            self.schema[unit][table] = {"columns": cols, "references": refs}

    def read_declared_joins(self, lines):
        for line in lines[1:]:
            unit, pairs = line.strip().split(": ", 1)
            self.joins[unit] = [tuple(tuple(side.split(".")) for side in pair.split(" = ")) for pair in pairs.split("; ")]

    def declared(self, unit: str) -> list[tuple[tuple[str, str], tuple[str, str]]]:
        """The unit's declared joins, ((table, column), (table, column)): the package's, or the schema's references."""
        if unit in self.joins:
            return self.joins[unit]
        return [((t, c), (rt, rc)) for t, spec in self.schema.get(unit, {}).items() for c, rt, rc in spec["references"]]

    def guessed_key(self, unit: str, table: str) -> tuple[str, str] | None:
        """(column, other table): a join column of table that another table has by the same name, where no declared
        join links the two. The guess a reader makes from names alone; a key-like name first."""
        pairs = self.declared(unit)
        linked = {frozenset(p) for p in pairs}
        cols: dict[str, set[str]] = {}
        for (a, ac), (b, bc) in pairs:
            cols.setdefault(a, set()).add(ac)
            cols.setdefault(b, set()).add(bc)
        for c in sorted(cols.get(table, ()), key=lambda c: (not c.endswith(("_id", "_no", "_ref")), c)):
            for other in sorted(cols):
                if other != table and c in cols[other] and frozenset({(table, c), (other, c)}) not in linked:
                    return c, other
        return None

    def read_sources_found_by_search(self, lines):
        self.read_sources(lines, citable=True)

    def read_sources(self, lines, citable=True):
        for line in lines[1:]:
            m = SOURCE.match(line)
            if m:
                tier = re.match(r" v\S+, (gold|silver)", m.group(4))
                why = re.search(r"; (certified definition of [^;]+|the unit's keys[^;]*|retrieved by the question);", m.group(4))
                self.sources.append({"n": int(m.group(1)), "doc": m.group(2), "title": m.group(3), "citable": citable,
                                     "tier": tier.group(1) if tier else None, "why": why.group(1) if why else ""})

    def read_references(self, lines):
        self.read_sources(lines, citable=False)

    def read_terms(self, lines):
        for line in lines[1:]:
            if line.startswith("    "):
                text = line.strip()
                if text.startswith("certified in "):
                    cur = re.search(r"currency (\w+)", text)
                    self.terms[-1]["currencies"].add(cur.group(1) if cur else "")
                elif text.startswith("not certified in "):
                    self.terms[-1]["missing"] = [u for u in self.units if re.search(r"\b" + re.escape(u) + r"\b", text.split(":")[0])]
            elif line.startswith("  "):
                self.terms.append({"name": line.strip().split(":", 1)[0], "currencies": set(), "missing": []})

    def read_contexts(self, lines):
        c = {"join": [], "where": [], "across": [], "note": []}
        for line in lines[2:]:
            kind, _, text = line.strip().partition(" ")
            text = text.strip()
            if kind in ("join", "where", "note"):
                c[kind].append(text)
            elif kind == "across":
                m = re.match(r"(\w+): (\w+\.\w+) ~ (\w+\.\w+), only through the certified join (\w+) \((\w+)\)", text)
                if m:
                    c["across"].append({"unit": m.group(1), "far": m.group(2), "near": m.group(3), "join": m.group(4), "through": m.group(5)})
                c["unit"] = re.search(r"key the query by (\w+)", text).group(1) if "key the query by" in text else c.get("unit")
            elif kind:
                c[kind] = text
        self.contexts = c

    def read_one_customer_group(self, lines):
        m = re.match(r"\s*Reply (\w+): (.*)", lines[1]) if len(lines) > 1 else None
        if m:
            self.group = (m.group(1), m.group(2))

    def read_not_glossary_terms(self, lines):
        self.not_terms = [tuple(line.strip().split(": ", 1)) for line in lines[1:] if ": " in line]

    def read_columns_by_role(self, lines):
        for line in lines[1:]:
            if not line.startswith("    "):
                unit, term = line.strip().rstrip(":").split(", ", 1)
                self.blocks.append({"unit": unit, "term": term, "filters": [], "period": None, "keys": []})
                continue
            kind, text = line.strip().split(None, 1)
            b = self.blocks[-1]
            if kind == "measure":
                b["measure"], b["how"] = text.split(": ", 1)
            elif kind == "filter":
                b["filters"].append(text)
            elif kind == "period":
                b["period"] = text
            elif kind == "keys":
                b["keys"] = text.split("; ")

    def read_across_units(self, lines):
        for line in lines[1:]:
            text = line.strip()
            m = re.match(r"No join between (\w+) and (\w+) \([^)]*\): (.*)", text)
            if m:
                self.no_join.append((m.group(1), m.group(2), m.group(3)))
            elif text.startswith("Adding across units: "):
                body = text.removeprefix("Adding across units: ")
                if " may be added: " in body:
                    self.adding = (True, body.split(" may be added: ", 1)[1])
                elif "; report the parts: " in body:
                    self.adding = (False, body.split("; report the parts: ", 1)[1])
            elif "never added together" in text:
                self.never_added = text

    def read_do_not_use(self, lines):
        for line in lines[1:]:
            if line.strip().startswith("restricted personal data"):
                for item in line.split(": ", 1)[1].split(", "):
                    m = re.match(r"(\w+)\.(\w+) \((\w+)\)", item.strip())
                    if m:
                        self.restricted.append((m.group(3), m.group(1), m.group(2)))


class Reader:
    """A careful reader of one question and its context. reply() is (the plan, each unit's query parts)."""

    def __init__(self, ctx: Context, question: str):
        self.ctx, self.q, self.ql = ctx, question, question.lower()

    def units_named(self) -> list[str]:
        if self.ctx.named:
            return self.ctx.named
        return [u for u in self.ctx.units if re.search(r"\b" + re.escape(u) + r"\b", self.ql)]

    def reply(self) -> tuple[dict, dict]:
        ctx, ql = self.ctx, self.ql
        named = self.units_named()
        denied = ctx.denied or [u for u in named if u not in ctx.readable]
        if denied:
            return no_query("refuse", f"The question needs {', '.join(denied)}, which this analyst may not read."), {}
        asked = [(u, t, c) for u, t, c in ctx.restricted if all(w in ql for w in c.split("_"))]
        if asked:
            u, t, c = asked[0]
            return no_query("refuse", f"{t}.{c} in {u} is restricted personal data: it is in no query, under no purpose."), {}
        if ctx.not_terms:
            name, text = ctx.not_terms[0]
            return no_query("abstain_route_steward", f"{name} is {text.rstrip('.')}. The steward decides whether it becomes a term."), {}
        if ctx.group:
            behaviour, why = ctx.group
            return no_query(behaviour, why, joins_refused=[f"{a}-{b}" for a, b, _ in ctx.no_join] if behaviour == "refuse" else []), {}
        if ctx.contexts is not None:
            return self.from_contexts()
        if DEFINE.match(ql):
            cited = [s for s in ctx.sources if s["citable"]]
            if not cited:
                return no_query("abstain", "No source in the context answers the question."), {}
            said = "The sources say: " + "; ".join(f"{s['doc']} \"{s['title']}\" [{s['n']}]" for s in cited) + "."
            return no_query("cite_certified", "", [s["n"] for s in cited], said), {}
        if JOIN.search(ql) and len(named) > 1:
            facts = [f for f in ctx.no_join if f[0] in named and f[1] in named]
            if facts:
                return no_query("refuse", facts[0][2], joins_refused=[f"{a}-{b}" for a, b, _ in facts]), {}
            if ctx.schema:
                return no_query("abstain", "A query reads one unit's warehouse; nothing here joins " + " to ".join(named) + "."), {}
        if ctx.blocks:
            return self.from_roles(named)
        if ctx.schema:
            return self.from_schema(named)
        missing = next((t for t in ctx.terms if t["missing"]), None)
        if missing:
            return no_query("abstain", f"{missing['name']} is not certified in {', '.join(missing['missing'])}: a figure there would rest on a guess."), {}
        return no_query("abstain", "The context names no column for this question."), {}

    def from_contexts(self) -> tuple[dict, dict]:
        """The query the context lists, part by part, keyed by the unit it names; or the reply it says the graph decided."""
        c, ctx = self.ctx.contexts, self.ctx
        if "reply" in c:
            m = re.match(r"(\w+)(?: \[([^\]]*)\])?: (.*)", c["reply"])
            pairs = [x.strip() for x in m.group(2).split(",")] if m and m.group(2) else []
            return no_query(m.group(1) if m else "abstain", m.group(3) if m else c["reply"], joins_refused=pairs), {}
        if "measure" not in c or "from" not in c:
            return no_query("abstain", "The context gives no query for this question."), {}
        unit = c.get("unit") or (ctx.readable or [""])[0]
        part = {"select": c["measure"], "from": c["from"], "joins": c["join"], "where": c["where"], "group": c.get("group"),
                "across": c["across"]}
        part["sql"] = query(part["select"], part["from"], part["joins"], part["where"], part["group"])
        cites = [s["n"] for s in ctx.sources if s["citable"] and s["why"].startswith("certified definition of")]
        return {"behaviour": "figure", "sql": {unit: part["sql"]}, "columns": {}, "citations": cites, "joins_refused": [], "reason": "",
                "answer_text": ""}, {unit: part}

    def from_roles(self, named: list[str]) -> tuple[dict, dict]:
        ctx = self.ctx
        first: dict[str, dict] = {}
        for b in ctx.blocks:
            first.setdefault(b["unit"], b)
        missing = [u for u in named if u not in first]
        if missing:
            term = next((t for t in ctx.terms if set(missing) & set(t["missing"])), None)
            return no_query("abstain", (f"{term['name']} is" if term else "The question's measure is") +
                            f" not certified in {', '.join(missing)}: a figure there would rest on a guess."), {}
        cur = re.search(r"\b(GBP|EUR|USD|JPY|CHF)\b", self.q)
        if cur and cur.group(1) not in set().union(*[t["currencies"] for t in ctx.terms] or [set()]):
            return no_query("abstain", f"No certified column reports in {cur.group(1)}."), {}
        units = list(first)
        refused = [f"{a}-{b}" for a, b, _ in ctx.no_join if a in units and b in units]
        if ctx.never_added:
            return no_query("refuse", ctx.never_added, joins_refused=[f"{a}-{b}" for a, b, _ in ctx.no_join]), {}
        parts = {}
        for u in units:
            part = self.from_block(first[u])
            if isinstance(part, str):
                return no_query("abstain", f"{u}: {part}"), {}
            parts[u] = part
        cites = [s["n"] for s in ctx.sources if s["citable"] and (s["why"].startswith("certified definition of")
                                                                    or (len(units) > 1 and s["why"].startswith("the unit's keys")))]
        out = {"sql": {u: p["sql"] for u, p in parts.items()}, "columns": {u: p["measure"] for u, p in parts.items()},
               "citations": cites, "joins_refused": refused, "answer_text": ""}
        if len(units) == 1:
            return {"behaviour": "figure", **out, "reason": ""}, parts
        adds, why = ctx.adding or (False, "the context does not say the parts may be added")
        return {"behaviour": "sum_with_verdict" if adds else "parts_with_reason", **out, "reason": why[0].upper() + why[1:] + "."}, parts

    @staticmethod
    def from_block(b: dict) -> dict | str:
        """One unit's query from its columns by role, or why there is none."""
        table = b["measure"].split(".")[0]
        where: list[str] = []
        if b["how"].startswith("count(*) of the rows where "):
            select = "count(*)"
            where.append(b["how"].removeprefix("count(*) of the rows where ").removesuffix(" is true"))
        else:
            select = b["how"]
        for f in b["filters"]:
            if ": no such column" in f:
                return f"the question narrows by {f.split(':')[0]}, which it does not have"
            where.append(f.removesuffix(" is true"))
        if b["period"]:
            clause, _, note = b["period"].partition("  -- ")
            if clause == "not available":
                return note or "the period asked is not available"
            if clause != "none needed":
                where.append(clause)
        joins, tables = [], [table]
        for key in b["keys"]:
            if key.startswith("no declared join"):
                return key
            left, right = key.split(" = ")
            ta, tb = left.split(".")[0], right.split(".")[0]
            new = tb if ta in tables else ta
            if new in tables:
                where.append(key)
            else:
                joins.append(f"JOIN {new} ON {key}")
                tables.append(new)
        where = list(dict.fromkeys(where))
        return {"sql": query(select, table, joins, where), "select": select, "from": table, "joins": joins, "where": where,
                "measure": b["measure"]}

    def words(self) -> list[str]:
        out: list[str] = []
        for w in re.findall(r"[a-z][a-z_]+", self.ql):
            if w in COMMON or w in self.ctx.units:
                continue
            w = w[:-3] + "y" if w.endswith("ies") else w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w
            if w not in out:
                out.append(w)
        return out

    def guess(self, unit: str, words: list[str]) -> tuple[tuple, str, str, str] | None:
        """The column whose name, and its table's, the question's words name most; ties to the more specific
        name, then to a number for an amount or a flag for a count. (score, table, column, type) or None."""
        counting, best = bool(COUNT.search(self.ql)), None
        for table, spec in self.ctx.schema.get(unit, {}).items():
            for col, dtype in spec["columns"]:
                in_col = [w for w in words if w in col]
                score = len(in_col) + sum(1 for w in words if w in table)
                if not score:
                    continue
                keyish = col.endswith(("_id", "_no", "_ref"))
                fit = (2 if dtype == "BOOLEAN" else 1 if keyish else 0) if counting else (1 if NUMERIC.search(dtype) and not keyish else 0)
                rank = (score, len([w for w in in_col if w not in table]), fit)
                if best is None or rank > best[0]:
                    best = (rank, table, col, dtype)
        return best

    def from_schema(self, named: list[str]) -> tuple[dict, dict]:
        ctx, words = self.ctx, self.words()
        units = named or ctx.readable
        picks = {u: g for u in units if (g := self.guess(u, words))}
        if not named and picks and not re.search(r"across the bank|\bbank\b|enterprise", self.ql):
            best = max(picks, key=lambda u: picks[u][0])
            picks = {best: picks[best]}
        if not picks:
            return no_query("abstain", "No column in the warehouse schema is named by the question's words."), {}
        parts = {u: self.schema_query(u, t, c, d) for u, (_, t, c, d) in picks.items()}
        out = {"sql": {u: p["sql"] for u, p in parts.items()}, "columns": {u: p["measure"] for u, p in parts.items()},
               "citations": [s["n"] for s in ctx.sources[:1]], "joins_refused": [], "answer_text": ""}
        if len(parts) == 1:
            return {"behaviour": "figure", **out, "reason": ""}, parts
        if ADD.search(self.ql):
            return {"behaviour": "sum_with_verdict", **out, "reason": "The question asks for their total."}, parts
        return {"behaviour": "parts_with_reason", **out, "reason": "The question asks for each unit's figure."}, parts

    def schema_query(self, unit: str, table: str, col: str, dtype: str) -> dict:
        t = f"{table}.{col}"
        where: list[str] = []
        if dtype == "BOOLEAN":
            select = "count(*)"
            where.append(t)
        elif COUNT.search(self.ql) or col.endswith(("_id", "_no", "_ref")) or not NUMERIC.search(dtype):
            select = f"count({t})"
        elif "average" in self.ql:
            select = f"round(avg({t}), 2)"
        else:
            select = f"round(sum({t}), 2)"
        joins, period = self.period(unit, table)
        return {"sql": query(select, table, joins, where + period), "select": select, "from": table, "joins": joins,
                "where": where + period, "measure": t}

    def period(self, unit: str, table: str) -> tuple[list[str], list[str]]:
        """(joins, conditions) for the period the question names, on what the table's schema shows."""
        spec = self.ctx.schema[unit][table]
        cols = {c for c, _ in spec["columns"]}
        m_day = re.search(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b", self.ql)
        m_month = re.search(r"\b(" + "|".join(MONTHS) + r") (\d{4})\b", self.ql)
        m_fq = re.search(r"\bfy(\d{2})-q([1-4])\b", self.ql)
        m_cq = re.search(r"\bq([1-4]) (\d{4})\b", self.ql)
        m_year = re.search(r"\b(20\d{2})\b", self.ql)
        if m_day:
            y, mo, d = int(m_day.group(3)), MONTHS.index(m_day.group(2)) + 1, int(m_day.group(1))
            if "as_of" in cols:
                return [], [f"{table}.as_of = DATE '{y}-{mo:02d}-{d:02d}'"]
            if "month_start" in cols:
                return [], [f"{table}.month_start = DATE '{y}-{mo:02d}-01'"]
            return [], []
        if m_fq:
            return [], [f"{table}.fiscal_quarter = 'FY{m_fq.group(1)}-Q{m_fq.group(2)}'"] if "fiscal_quarter" in cols else []
        if m_cq:
            n, y = m_cq.group(1), m_cq.group(2)
            if "fiscal_quarter" in cols:
                return [], [f"{table}.fiscal_quarter = 'FY{y[2:]}-Q{n}'"]
            if "month_start" in cols and ("month_start", "calendar_month", "month_start") in spec["references"]:
                return ([f"JOIN calendar_month ON {table}.month_start = calendar_month.month_start"],
                        [f"calendar_month.calendar_quarter = '{y}-Q{n}'"])
            return [], []
        if m_month:
            y, mo = int(m_month.group(2)), MONTHS.index(m_month.group(1)) + 1
            if "month_start" in cols:
                return [], [f"{table}.month_start = DATE '{y}-{mo:02d}-01'"]
            if "as_of" in cols and self.ctx.googlesql:
                return [], [f"DATE_TRUNC({table}.as_of, MONTH) = DATE '{y}-{mo:02d}-01'"]
            if "as_of" in cols:
                nxt = f"{y + (mo == 12)}-{mo % 12 + 1:02d}-01"
                return [], [f"{table}.as_of >= DATE '{y}-{mo:02d}-01'", f"{table}.as_of < DATE '{nxt}'"]
            return [], []
        if m_year:
            y = int(m_year.group(1))
            col = next((c for c in ("month_start", "as_of") if c in cols), None) or next((c for c, _ in spec["columns"] if c.endswith("_on")), None)
            return [], [f"{table}.{col} >= DATE '{y}-01-01'", f"{table}.{col} < DATE '{y + 1}-01-01'"] if col else []
        return [], []


def phrase(prompt: str, mode: str) -> str:
    """The answer worded from the facts alone, each figure with its marker."""
    facts = json.loads(prompt.split("\nFacts: ", 1)[1].split("\n\nQuestion: ", 1)[0])
    figures = facts.get("figures") or []
    mark = lambda f: f" [{f['cite']}]" if f.get("cite") else ""  # noqa: E731
    parts = "; ".join(f"{f['unit']} {f['value']}{mark(f)}" for f in figures)
    if facts.get("behaviour") == "figure" and figures:
        text = f"{figures[0]['unit']}: {figures[0]['value']}{mark(figures[0])}."
    elif facts.get("behaviour") == "sum_with_verdict":
        text = f"{facts.get('total')} in all, the sum of {parts}." + (f" {facts['reason']}" if facts.get("reason") else "")
    else:
        text = f"Not added: {facts.get('reason') or 'the parts do not add'}. {parts}."
    if mode == "invented_number":
        first = float(str(figures[0]["value"]).replace(",", "")) if figures and figures[0]["value"] != "none" else 7918.13
        text += f" The month before it was {first * 1.07 + 1:,.2f}."
    if mode == "bad_marker":
        text += f" See also [{max([c['n'] for c in facts.get('citations') or []] + [0]) + 1}]."
    return json.dumps({"answer_text": text})


def improve_reply(prompt: str, mode: str) -> str:
    """The mock's reply to improve-propose-v1: reads the <miss> payload and returns {kind, edit, why}.
    In careful mode it proposes an alias only when the diagnosis is missing_alias, exactly one term's comment matches,
    and the alias is not already held."""
    if mode == "malformed_json":
        return '{"kind": "proposal", "edit": {'
    raw = prompt.split(MISS_OPEN, 1)[1].split(MISS_CLOSE, 1)[0].strip()
    payload = json.loads(raw)
    phrase_txt = (payload.get("phrase") or "").strip().lower()
    matches = payload.get("matching_comments") or []
    terms = sorted({m.get("term") for m in matches if m.get("term")})
    existing = {str(a).strip().lower() for a in (payload.get("existing_aliases") or [])}
    cert_terms = sorted(payload.get("certified_terms") or [])
    first_term = terms[0] if terms else (cert_terms[0] if cert_terms else "sample-term")

    if mode == "unallowlisted_section":
        return json.dumps({
            "kind": "proposal",
            "edit": {"section": "glossary", "id": "rogue-term", "phrase": phrase_txt or "lease income", "term": first_term},
            "why": "adversarial edit outside the allowlist",
        })
    if mode == "certified_true":
        return json.dumps({
            "kind": "proposal",
            "edit": {
                "section": "terminology.aliases",
                "phrase": phrase_txt or "lease income",
                "term": first_term,
                "certified": True,
            },
            "why": "adversarial self-certified edit",
        })
    if mode == "unknown_column":
        return json.dumps({
            "kind": "proposal",
            "edit": {
                "section": "terminology.aliases",
                "phrase": phrase_txt or "lease income",
                "term": first_term,
                "column": "nonexistent.table.missing_column",
            },
            "why": "adversarial edit naming a column that does not exist",
        })
    if mode == "invented_alias":
        return json.dumps({
            "kind": "proposal",
            "edit": {"section": "terminology.aliases", "phrase": "invented ungrounded phrase", "term": first_term},
            "why": "adversarial alias not present in any DDL comment or term",
        })
    if mode == "duplicate_alias":
        dup = sorted(existing)[0] if existing else "sample-term"
        return json.dumps({
            "kind": "proposal",
            "edit": {"section": "terminology.aliases", "phrase": dup, "term": first_term},
            "why": "adversarial duplicate alias",
        })

    if payload.get("diagnosis") == "missing_alias" and phrase_txt and len(terms) == 1 and phrase_txt not in existing:
        col = matches[0].get("column") or terms[0]
        return json.dumps({
            "kind": "proposal",
            "edit": {"section": "terminology.aliases", "phrase": phrase_txt, "term": terms[0]},
            "why": f"missing alias: {phrase_txt!r} in the comment on {col}",
        })
    return json.dumps({
        "kind": "request",
        "edit": None,
        "why": payload.get("why") or "needs code",
    })
