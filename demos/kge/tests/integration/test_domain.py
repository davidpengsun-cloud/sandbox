"""The runtime reads its domain from the graph, as the client's compiled pack loaded it (#39 item 6).

    .venv/bin/python -m pytest tests/integration/test_domain.py -q

Graph.domain() reads, once, what the pack put in the graph: the units in the
pack's order, their labels and customer keys, the restricted columns (Policy
GOVERNS Col), the parser's phrases (Alias), the calendars and each table's
period binding. The Engine holds them as UNIT_ORDER, UNIT_LABEL, UNIT_KEY,
RESTRICTED_COLUMNS, ALIASES and UNIT_WORDS (and CALENDARS, PERIODS), and no
platform module imports anything from kge/estate/: one process serves one client,
and the same code serves any.

What each arm sees does not change. The control's plan inputs and SQL prompt on
every golden and held-out item are byte-equal to those frozen before the change
(tests/fixtures/control_6c897ba.json), with the aliases and the period bindings
taken away too: the control is handed no curated metadata. The graph context
reads its period clause from the bindings and gets what the name heuristic gave
it on every table, period and aggregate; every other context keeps the
heuristic. The fiscal functions, now taking a calendar's year_start_month, are
the April ones they replaced. A scratch copy of the pack with one more alias,
compiled and loaded, is read with no code change: the README's demo step.

Opens the demo's graph through the eng fixture; the scratch pack loads into a
scratch build. Stop the API first.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kge.services.ask.engine.agent import Agent, sql_prompt
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, Engine, Period, calendar_from_fiscal, fiscal_from_calendar, parse
from kge.pipeline.sqlguard import schema_allowlist
from kge.eval import sets
from kge.estate import names
from tests import before
from tests.scratch import BUILD, CLIENT, DEMO, compile_pack, graph_of, load_artifact, profile_of, run_of, scratch_build, script, stand_in, write_registry

PACK = DEMO / "clients" / CLIENT / "pack.yaml"
FIXTURE = DEMO / "tests" / "fixtures" / "control_6c897ba.json"
PLATFORM = ("services", "semantics", "compiler", "eval", "graph", "landing", "pipeline")
PART = ("unit", "status", "column", "sql", "reason", "notes", "join_path", "value")


@pytest.fixture(scope="module")
def pack() -> dict:
    return yaml.safe_load(PACK.read_text())


def test_the_engine_holds_the_pack_s_domain_read_from_the_graph(eng, pack):
    sources = pack["sources"]
    assert eng.UNIT_ORDER == [s["id"] for s in sources], "the pack's order: pair names follow it"
    assert eng.UNIT_LABEL == {s["id"]: s["label"] for s in sources} and eng.UNIT_KEY == {s["id"]: s["entity_key"] for s in sources}
    assert eng.KEY_TABLE == {"retail": "customer", "sme": "client", "corporate": "counterparty", "wealth": "HSHLD_DIM_1"}
    assert eng.RESTRICTED_COLUMNS == [tuple(c.split(".")) for r in pack["policies"]["restricted_attributes"] for c in r["columns"]]
    assert eng.RESTRICTED_PHRASES == {x["column"]: x["phrases"] for r in pack["policies"]["restricted_attributes"]
                                      for x in r.get("phrases", [])}
    assert eng.RESTRICTED_PHRASES["retail.customer.email"] == ["e-mail"]
    name = {g["id"]: g["name"] for g in pack["glossary"]}
    aliases = [a for a in pack["terminology"]["aliases"] if "term" in a or "candidate" in a]
    assert len(aliases) == 31 and len(pack["terminology"]["aliases"]) == 49 + len(names.columns())
    assert list(eng.ALIASES.items()) == [(a["phrase"], name[a["term"]] if "term" in a else f"__candidate__:{a['candidate']}")
                                         for a in aliases], "in the pack's order: a tie in length goes to the first listed"
    assert list(eng.UNIT_WORDS.items()) == [(a["phrase"], a["source"]) for a in pack["terminology"]["aliases"] if "source" in a]
    assert eng.CALENDARS == {c["id"]: {"year_start_month": c["year_start_month"], "quarter_label": c["quarter_label"]}
                             for c in pack["calendars"]}
    assert eng.PERIODS == {b["table"]: {"column": b["column"], "kind": b["kind"], "calendar": b["calendar"],
                                        "rollup_table": (b.get("rollup") or {}).get("table"),
                                        "rollup_column": (b.get("rollup") or {}).get("column")} for b in pack["period_bindings"]}
    assert eng.FISCAL_START == 4, "the calendar the parser's FY labels name starts in April"
    assert eng.g.domain() is eng.g.domain(), "read once"
    assert list(eng.w.con) == eng.UNIT_ORDER, "one warehouse a unit, in the pack's order"


def test_no_module_of_the_runtime_holds_the_domain():
    import kge.services.ask.engine.agent
    import kge.services.ask.engine.combined
    import kge.services.ask.engine.core
    for name in ("UNIT_ORDER", "UNIT_WORDS", "ALIASES", "RESTRICTED_COLUMNS", "UNIT_KEY", "UNIT_LABEL", "UNITS"):
        for mod in (kge.services.ask.engine.core, kge.services.ask.engine.agent, kge.services.ask.engine.combined):
            assert not hasattr(mod, name), f"{mod.__name__}.{name}"
    assert not hasattr(kge.services.ask.engine.agent, "RUNS_SQL"), "computed per engine"


def imports(path: Path) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_no_platform_module_imports_the_estate():
    """grep -rn estate api eval graph pipeline compiler landing finds no import: the dependency runs client to platform."""
    found = {p.relative_to(DEMO).as_posix(): m for p in [DEMO / "kge" / "app.py", *(q for d in PLATFORM for q in sorted((DEMO / "kge" / d).rglob("*.py")))]
             for m in imports(p) if m == "kge.estate" or m.startswith("kge.estate.")}
    assert found == {}


def test_a_graph_with_no_domain_says_so(tmp_path):
    from kge.services.ask.engine.core import Graph
    from kge.adapters.graphstore import connect, open_db
    build = tmp_path / "build"
    build.mkdir()
    write_registry(build)
    stand_in(build)
    graph_of(build).parent.mkdir(parents=True)
    db = open_db(graph_of(build))
    conn = connect(db)
    conn.execute("CREATE NODE TABLE Unit(id STRING, label STRING, key_column STRING, PRIMARY KEY (id))")
    conn.close()
    db.close()
    g = Graph(profile_of(build))
    try:
        with pytest.raises(RuntimeError, match="not loaded from a pack compiled for this platform.s meta-model: make graph"):
            g.domain()
    finally:
        g.close()


def test_the_parser_reads_the_aliases_and_unit_words_it_is_given(eng):
    q = "Total retail takings for June 2025"
    assert parse(q, eng.term_names, eng).term is None
    given = SimpleNamespace(ALIASES={**eng.ALIASES, "takings": "Revenue"}, UNIT_WORDS=eng.UNIT_WORDS)
    r = parse(q, eng.term_names, given)
    assert r.term == "Revenue" and r.units == ["retail"]
    assert parse(q, eng.term_names, given, aliases=False).term is None, "resolution none or words: no alias maps"


def frozen() -> dict:
    return json.loads(FIXTURE.read_text())


def plan_of(a: dict) -> dict:
    """The control's plan inputs, as the fixture keeps them: Engine.answer's trace, each planned part's SQL and outcome."""
    steps = []
    for s in a["trace"]:
        if s.get("step") == "plan":
            s = {**s, "parts": [{k: p.get(k) for k in PART} for p in s["parts"]]}
        steps.append(s)
    return json.loads(json.dumps({"behaviour": a.get("behaviour"), "figure": a.get("figure"), "reason": a.get("reason"), "trace": steps},
                                 default=str))


SOURCE = re.compile(r"  \[\d+\] (\S+) .* \(chunk (\S+)\): .*")


class Expansion:
    """What the expansion added: tables and columns 4d9d5c9 did not have, and chunks whose text it did not have. The
    Wealth expansion (#39 item 7b) adds tables and columns to the warehouses whose schema the control reads, and chunks
    to two data dictionaries, so these take it back out of what the control sees now, to put it beside what it saw. The
    curated source's tables (pack 1.1.0, #39 item 7c) are in no warehouse the control reads. Since pack 1.5.0 every
    Wealth table holds a physical name (kge/estate/names.py), so the control reads none of Wealth's tables under the
    name it had: Wealth's schema is taken out on both sides (wealth), then's and now's, and the control's plans that
    read Wealth are pinned as they are now (CHANGED_PLANS)."""

    def __init__(self, eng):
        self.tables_then, self.columns_then = before.tables_then()
        now = {r["id"] for r in eng.g.q("MATCH (:Unit)-[:HAS_TABLE]->(t:Tbl) RETURN t.id AS id")}
        self.tables = now - self.tables_then
        self.wealth = {t for t in now | self.tables_then if t.startswith("wealth.")}
        self.units = set(eng.UNIT_ORDER)
        then = before.raw_chunks_then()
        self.chunks = {r["id"] for r in eng.g.q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE d.kind = 'data_dictionary' "
                                               "RETURN c.id AS id, c.text AS text") if then.get(r["id"]) != r["text"]}

    def sources(self, then: str, now: str) -> tuple[str, str]:
        """The two prompts with their search sources renumbered without the expansion's chunks: what it found then, in
        its order, of which the search now finds the first ones, the rest pushed out by a chunk the expansion added or
        changed. Returns (then, now) to compare, with now's sources cut to what then had, and None if they disagree."""
        def split(text):
            lines = text.split("\n")
            kept = [(i, m) for i, line in enumerate(lines) if (m := SOURCE.fullmatch(line))]
            return lines, [(i, m) for i, m in kept if m[2] not in self.chunks]
        a, sa = split(then)
        b, sb = split(now)
        strip = lambda line: re.sub(r"^  \[\d+\] ", "  ", line)  # noqa: E731
        drop_a = {i for i, m in [(i, SOURCE.fullmatch(x)) for i, x in enumerate(a)] if m}
        drop_b = {i for i, m in [(i, SOURCE.fullmatch(x)) for i, x in enumerate(b)] if m}
        rest_a, rest_b = [strip(a[i]) for i, _ in sa], [strip(b[i]) for i, _ in sb]
        same = rest_b == rest_a[:len(rest_b)]
        a = [x for i, x in enumerate(a) if i not in drop_a]
        b = [x for i, x in enumerate(b) if i not in drop_b]
        return "\n".join(a), "\n".join(b) if same else None

    def schema(self, text: str, unit: str | None = None) -> str:
        """A prompt's warehouse schema (under each unit's name: "  table: column TYPE, ...; key ...; references ..."), or
        a unit's allowlist ("  table: column, ..." and "  table.column = table.column"), without the expansion."""
        out = []
        for line in text.split("\n"):
            if line in self.units:
                unit = line
            m = re.fullmatch(r"  (\w+): (.*)", line)
            j = re.fullmatch(r"  (\w+)\.\w+ = (\w+)\.\w+", line)
            if unit and m and f"{unit}.{m[1]}" in self.tables | self.wealth:
                continue
            if unit and j and {f"{unit}.{j[1]}", f"{unit}.{j[2]}"} & (self.tables | self.wealth):
                continue
            if unit and m and f"{unit}.{m[1]}" in self.tables_then:
                cols, *rest = m[2].split("; ")
                kept = [c for c in cols.split(", ") if f"{unit}.{m[1]}.{c.split(' ')[0]}" in self.columns_then]
                line = f"  {m[1]}: " + "; ".join([", ".join(kept), *rest])
            out.append(line)
        return "\n".join(out)

    def plan(self, plan: dict) -> dict:
        """The control's plan without a column the expansion added among those its words guessed, nor a Wealth column (its
        name is physical since pack 1.5.0)."""
        steps = [{**s, "implementations": [i for i in s["implementations"] if i in self.columns_then and not i.startswith("wealth.")]}
                 if s.get("step") == "map" else s
                 for s in plan["trace"]]
        return {**plan, "trace": steps}


SQL_PROMPT_V2 = """You are a text-to-SQL agent for a bank's four business units: retail, sme, corporate and wealth. Each unit has
its own DuckDB warehouse. Answer the analyst's question from the context below and nothing else.

Reply with one JSON object and nothing else. Its keys:
  "behaviour"      one of:
                     figure                 one number from one unit's warehouse
                     sum_with_verdict       numbers from several units that may be added: the answer is their total
                     parts_with_reason      numbers from several units that must not be added: give each, and say why
                     refuse                 the question asks for what may not be given or done: say why
                     abstain                the context cannot support an answer: say why
                     abstain_route_steward  the question names something that is not yet a defined term: say who decides
                     cite_certified         the answer is in the sources: cite them
  "sql"            {"<unit>": "<one DuckDB SELECT on that unit's warehouse, returning one row with one number>"};
                   {} for a behaviour that runs no query
  "columns"        {"<unit>": "<table.column the number measures>"}
  "citations"      [the numbers of the sources the answer rests on]
  "joins_refused"  ["<unit>-<unit>" for each pair of units whose data must not be joined]
  "reason"         why, in one sentence, for anything but a figure
  "answer_text"    for a reply that runs no query: the answer in one or two sentences, with a [n] marker for each source

A query reads one unit's warehouse only, never a table of another unit. Name every column: no SELECT *.
Its one value is an aggregate of one column of that unit's tables, inside round or not: round(sum(table.column), 2),
count(table.column), avg, min or max; or count(*) with a WHERE on a flag or filter column. Never a constant, and
nothing added to or multiplied into the aggregate.
"""


def control_differences(eng) -> list[str]:
    """The frozen prompts are put beside today's with agent-sql-v3's template in agent-sql-v2's place: v3 (#39 item 7d)
    lets a query read two units through a declared certified join and a reply abstain with a reason, the spec's stated
    change to the prompt both arms share."""
    snap = frozen()
    lines, who = snap["lines"], snap["principal"]
    agent = Agent(eng, llm=None)
    added = Expansion(eng)
    out = []
    for key, item in snap["items"].items():
        s, qid = key.split(":")
        q = next(x["question"] for x in sets.items(BUILD / f"{s}.json") if x["id"] == qid)
        plan = added.plan(plan_of(eng.answer(q, who, "analysis", config=AGENT_ARMS["control"])))
        if plan != added.plan(item["plan"]):
            out.append(f"{key} plan: {json.dumps(added.plan(item['plan']), sort_keys=True)} → {json.dumps(plan, sort_keys=True)}")
        then, prompt = added.sources(added.schema("\n".join(lines[i] for i in item["prompt"]).replace(
            SQL_PROMPT_V2, sql_prompt(eng.UNIT_ORDER, eng.w.query_dialect))),
            added.schema(agent.prompt("sql", q, agent.context(q, who, "analysis", "control")["block"])))
        if prompt is None:
            out.append(f"{key} prompt: its search found, of what it found before, other chunks or in another order")
        elif prompt != then:
            first = next(i for i, (a, b) in enumerate(zip(then.split("\n") + [""], prompt.split("\n") + [""])) if a != b)
            out.append(f"{key} prompt, line {first + 1}: {then.split(chr(10))[first:first + 1]} → {prompt.split(chr(10))[first:first + 1]}")
    for unit, idx in snap["allowlists"].items():
        if added.schema(schema_allowlist(eng.g, unit, eng.RESTRICTED_COLUMNS).as_prompt(), unit) != added.schema("\n".join(lines[i] for i in idx), unit):
            out.append(f"the schema allowlist of {unit}")
    return out


CHANGED_PLANS = {
    "golden:G017": "{\"behaviour\": \"abstain\", \"figure\": null, \"reason\": \"\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"fiscal_quarter FY26-Q1\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"wealth\"]}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.revenue_id\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"revenue\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": []}, {\"parts\": [], \"step\": \"plan\"}]}",
    "golden:G018": "{\"behaviour\": \"abstain\", \"figure\": null, \"reason\": \"wealth: dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"as_of 2025-06-30\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": \"unspecified\"}, {\"certified\": false, \"implementations\": [], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"asset\", \"under\", \"advice\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": [\"wealth\"]}, {\"parts\": [{\"column\": \"ACCT_DIM2_1.LGCY_ASSET_MVMNT_CD\", \"join_path\": null, \"notes\": null, \"reason\": \"dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"sql\": \"SELECT round(sum(ACCT_DIM2_1.LGCY_ASSET_MVMNT_CD), 2) FROM ACCT_DIM2_1\", \"status\": \"error\", \"unit\": \"wealth\", \"value\": null}], \"step\": \"plan\"}]}",
    "golden:G019": "{\"behaviour\": \"figure\", \"figure\": 48.0, \"reason\": null, \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [[\"RISK_PRFL_CD\", \"=\", \"GROWTH\"]], \"period\": \"fiscal_quarter FY26-Q1\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"wealth\"]}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.revenue_id\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"revenue\", \"household\", \"growth\", \"risk\", \"profile\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": [\"wealth\"]}, {\"parts\": [{\"column\": \"HSHLD_DIM_1.RISK_PRFL_CD\", \"join_path\": [], \"notes\": [\"no period column; period ignored\"], \"reason\": null, \"sql\": \"SELECT count(*) FROM HSHLD_DIM_1 WHERE HSHLD_DIM_1.RISK_PRFL_CD = 'GROWTH'\", \"status\": \"ok\", \"unit\": \"wealth\", \"value\": 48.0}], \"step\": \"plan\"}]}",
    "golden:G020": "{\"behaviour\": \"abstain\", \"figure\": null, \"reason\": \"No column in the warehouse schema is named by the question's words.\", \"trace\": [{\"agg\": \"count\", \"candidate\": null, \"filters\": [[\"RVW_OUTCM_CD\", \"=\", \"ESCALATED\"]], \"period\": \"year 2025\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": \"unspecified\"}, {\"certified\": false, \"implementations\": [], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"suitability\", \"review\", \"escalated\"]}]}",
    "golden:G021": "{\"behaviour\": \"sum_without_verdict\", \"figure\": 20996676.33, \"reason\": \"no measure metadata in this arm; summed as written\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"calendar_quarter 2025-Q2\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": \"all\"}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.total_revenue_usd\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"total\", \"revenue\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"join_requires_all_warehouses\", \"step\": \"access\", \"units\": [\"retail\", \"sme\", \"corporate\"]}, {\"parts\": [{\"column\": \"monthly_revenue.total_revenue_usd\", \"join_path\": [\"monthly_revenue.month_start -> calendar_month.month_start\"], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue JOIN calendar_month ON monthly_revenue.month_start = calendar_month.month_start WHERE calendar_month.calendar_quarter = '2025-Q2'\", \"status\": \"ok\", \"unit\": \"retail\", \"value\": 39983.72}, {\"column\": \"monthly_client_revenue.revenue_usd\", \"join_path\": [\"monthly_client_revenue.month_start -> calendar_month.month_start\"], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(monthly_client_revenue.revenue_usd), 2) FROM monthly_client_revenue JOIN calendar_month ON monthly_client_revenue.month_start = calendar_month.month_start WHERE calendar_month.calendar_quarter = '2025-Q2'\", \"status\": \"ok\", \"unit\": \"sme\", \"value\": 953824.62}, {\"column\": \"quarterly_revenue.revenue_eur\", \"join_path\": [], \"notes\": [\"quarter label taken as written\"], \"reason\": null, \"sql\": \"SELECT round(sum(quarterly_revenue.revenue_eur), 2) FROM quarterly_revenue WHERE quarterly_revenue.fiscal_quarter = 'FY25-Q2'\", \"status\": \"ok\", \"unit\": \"corporate\", \"value\": 20002867.99}], \"step\": \"plan\"}, {\"commensurable\": null, \"joins_refused\": [], \"missing\": [], \"reason\": \"no measure metadata in this arm; summed as written\", \"step\": \"verdict\"}]}",
    "golden:G025": "{\"behaviour\": \"figure\", \"figure\": 18669131.39, \"reason\": null, \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"fiscal_quarter FY26-Q1\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"corporate\", \"wealth\"]}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.revenue_id\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"combined\", \"revenue\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": [\"corporate\"]}, {\"parts\": [{\"column\": \"quarterly_revenue.revenue_eur\", \"join_path\": [], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(quarterly_revenue.revenue_eur), 2) FROM quarterly_revenue WHERE quarterly_revenue.fiscal_quarter = 'FY26-Q1'\", \"status\": \"ok\", \"unit\": \"corporate\", \"value\": 18669131.39}], \"step\": \"plan\"}]}",
    "golden:G028": "{\"behaviour\": \"parts_with_reason\", \"figure\": null, \"reason\": \"incomplete: wealth dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.. A total without every part is not the total asked for\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": null, \"special\": \"cross_unit_entity\", \"step\": \"parse\", \"term\": null, \"units\": [\"sme\", \"wealth\"]}, {\"certified\": false, \"implementations\": [\"sme.client.client_no\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"client\", \"also\", \"household\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"join_requires_all_warehouses\", \"step\": \"access\", \"units\": [\"sme\", \"wealth\"]}, {\"parts\": [{\"column\": \"client.client_no\", \"join_path\": [], \"notes\": [], \"reason\": null, \"sql\": \"SELECT count(*) FROM client\", \"status\": \"ok\", \"unit\": \"sme\", \"value\": 60.0}, {\"column\": \"CLIENT_CD_ROLLUP_1.CLNT_CD\", \"join_path\": null, \"notes\": null, \"reason\": \"dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"sql\": \"SELECT round(sum(CLIENT_CD_ROLLUP_1.CLNT_CD), 2) FROM CLIENT_CD_ROLLUP_1\", \"status\": \"error\", \"unit\": \"wealth\", \"value\": null}], \"step\": \"plan\"}, {\"commensurable\": false, \"joins_refused\": [], \"missing\": [\"wealth\"], \"reason\": \"incomplete: wealth dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.. A total without every part is not the total asked for\", \"step\": \"verdict\"}]}",
    "golden:G030": "{\"behaviour\": \"parts_with_reason\", \"figure\": null, \"reason\": \"incomplete: corporate returned no value. A total without every part is not the total asked for\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"calendar_quarter 2025-Q1\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": \"all\"}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.revenue_id\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"revenue\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"join_requires_all_warehouses\", \"step\": \"access\", \"units\": [\"retail\", \"sme\", \"corporate\"]}, {\"parts\": [{\"column\": \"monthly_revenue.revenue_id\", \"join_path\": [\"monthly_revenue.month_start -> calendar_month.month_start\"], \"notes\": [], \"reason\": null, \"sql\": \"SELECT count(*) FROM monthly_revenue JOIN calendar_month ON monthly_revenue.month_start = calendar_month.month_start WHERE calendar_month.calendar_quarter = '2025-Q1'\", \"status\": \"ok\", \"unit\": \"retail\", \"value\": 1040.0}, {\"column\": \"monthly_client_revenue.revenue_usd\", \"join_path\": [\"monthly_client_revenue.month_start -> calendar_month.month_start\"], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(monthly_client_revenue.revenue_usd), 2) FROM monthly_client_revenue JOIN calendar_month ON monthly_client_revenue.month_start = calendar_month.month_start WHERE calendar_month.calendar_quarter = '2025-Q1'\", \"status\": \"ok\", \"unit\": \"sme\", \"value\": 899746.55}, {\"column\": \"quarterly_revenue.revenue_eur\", \"join_path\": [], \"notes\": [\"quarter label taken as written\"], \"reason\": null, \"sql\": \"SELECT round(sum(quarterly_revenue.revenue_eur), 2) FROM quarterly_revenue WHERE quarterly_revenue.fiscal_quarter = 'FY25-Q1'\", \"status\": \"ok\", \"unit\": \"corporate\", \"value\": null}], \"step\": \"plan\"}, {\"commensurable\": false, \"joins_refused\": [], \"missing\": [\"corporate\"], \"reason\": \"incomplete: corporate returned no value. A total without every part is not the total asked for\", \"step\": \"verdict\"}]}",
    "golden:G042": "{\"behaviour\": \"sum_without_verdict\", \"figure\": 20996676.33, \"reason\": \"no measure metadata in this arm; summed as written\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"calendar_quarter 2025-Q2\", \"special\": \"no_conversion_standard\", \"step\": \"parse\", \"term\": null, \"units\": \"all\"}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.total_revenue_usd\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"total\", \"revenue\", \"gbp\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"join_requires_all_warehouses\", \"step\": \"access\", \"units\": [\"retail\", \"sme\", \"corporate\"]}, {\"parts\": [{\"column\": \"monthly_revenue.total_revenue_usd\", \"join_path\": [\"monthly_revenue.month_start -> calendar_month.month_start\"], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue JOIN calendar_month ON monthly_revenue.month_start = calendar_month.month_start WHERE calendar_month.calendar_quarter = '2025-Q2'\", \"status\": \"ok\", \"unit\": \"retail\", \"value\": 39983.72}, {\"column\": \"monthly_client_revenue.revenue_usd\", \"join_path\": [\"monthly_client_revenue.month_start -> calendar_month.month_start\"], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(monthly_client_revenue.revenue_usd), 2) FROM monthly_client_revenue JOIN calendar_month ON monthly_client_revenue.month_start = calendar_month.month_start WHERE calendar_month.calendar_quarter = '2025-Q2'\", \"status\": \"ok\", \"unit\": \"sme\", \"value\": 953824.62}, {\"column\": \"quarterly_revenue.revenue_eur\", \"join_path\": [], \"notes\": [\"quarter label taken as written\"], \"reason\": null, \"sql\": \"SELECT round(sum(quarterly_revenue.revenue_eur), 2) FROM quarterly_revenue WHERE quarterly_revenue.fiscal_quarter = 'FY25-Q2'\", \"status\": \"ok\", \"unit\": \"corporate\", \"value\": 20002867.99}], \"step\": \"plan\"}, {\"commensurable\": null, \"joins_refused\": [], \"missing\": [], \"reason\": \"no measure metadata in this arm; summed as written\", \"step\": \"verdict\"}]}",
    "heldout:H005": "{\"behaviour\": \"abstain\", \"figure\": null, \"reason\": \"\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"fiscal_quarter FY26-Q1\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"wealth\"]}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.revenue_id\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"revenue\", \"booked\", \"management\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": []}, {\"parts\": [], \"step\": \"plan\"}]}",
    "heldout:H008": "{\"behaviour\": \"figure\", \"figure\": 18669131.39, \"reason\": null, \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": \"fiscal_quarter FY26-Q1\", \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"corporate\", \"wealth\"]}, {\"certified\": false, \"implementations\": [\"retail.monthly_revenue.revenue_id\", \"sme.monthly_client_revenue.revenue_usd\", \"corporate.quarterly_revenue.revenue_eur\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"sum\", \"revenue\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": [\"corporate\"]}, {\"parts\": [{\"column\": \"quarterly_revenue.revenue_eur\", \"join_path\": [], \"notes\": [], \"reason\": null, \"sql\": \"SELECT round(sum(quarterly_revenue.revenue_eur), 2) FROM quarterly_revenue WHERE quarterly_revenue.fiscal_quarter = 'FY26-Q1'\", \"status\": \"ok\", \"unit\": \"corporate\", \"value\": 18669131.39}], \"step\": \"plan\"}]}",
    "heldout:H009": "{\"behaviour\": \"parts_with_reason\", \"figure\": null, \"reason\": \"incomplete: wealth dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.. A total without every part is not the total asked for\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": null, \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"sme\", \"wealth\"]}, {\"certified\": false, \"implementations\": [\"sme.client.client_no\"], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"can\", \"you\", \"match\", \"client\", \"household\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"join_requires_all_warehouses\", \"step\": \"access\", \"units\": [\"sme\", \"wealth\"]}, {\"parts\": [{\"column\": \"client.client_no\", \"join_path\": [], \"notes\": [], \"reason\": null, \"sql\": \"SELECT count(*) FROM client\", \"status\": \"ok\", \"unit\": \"sme\", \"value\": 60.0}, {\"column\": \"CLIENT_CD_ROLLUP_1.CLNT_CD\", \"join_path\": null, \"notes\": null, \"reason\": \"dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"sql\": \"SELECT round(sum(CLIENT_CD_ROLLUP_1.CLNT_CD), 2) FROM CLIENT_CD_ROLLUP_1\", \"status\": \"error\", \"unit\": \"wealth\", \"value\": null}], \"step\": \"plan\"}, {\"commensurable\": false, \"joins_refused\": [], \"missing\": [\"wealth\"], \"reason\": \"incomplete: wealth dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.. A total without every part is not the total asked for\", \"step\": \"verdict\"}]}",
    "heldout:H012": "{\"behaviour\": \"abstain\", \"figure\": null, \"reason\": \"wealth: dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": null, \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": [\"wealth\"]}, {\"certified\": false, \"implementations\": [], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"explain\", \"management\", \"use\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": [\"wealth\"]}, {\"parts\": [{\"column\": \"BRCH_DIM_1.INSERT_USER_NM\", \"join_path\": null, \"notes\": null, \"reason\": \"dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"sql\": \"SELECT round(sum(BRCH_DIM_1.INSERT_USER_NM), 2) FROM BRCH_DIM_1\", \"status\": \"error\", \"unit\": \"wealth\", \"value\": null}], \"step\": \"plan\"}]}",
    "heldout:H013": "{\"behaviour\": \"abstain\", \"figure\": null, \"reason\": \"wealth: dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"trace\": [{\"agg\": \"sum\", \"candidate\": null, \"filters\": [], \"period\": null, \"special\": null, \"step\": \"parse\", \"term\": null, \"units\": \"unspecified\"}, {\"certified\": false, \"implementations\": [], \"step\": \"map\", \"term\": \"__words__\", \"words\": [\"acceptable\", \"embed\", \"restricted\", \"personal\", \"data\", \"assistant\"]}, {\"allowed\": true, \"denied\": [], \"principal\": \"analyst_enterprise\", \"rule\": \"read\", \"step\": \"access\", \"units\": [\"wealth\"]}, {\"parts\": [{\"column\": \"D_ACCT_SEGMT.INCL_IN_DATA_LOAD_IN\", \"join_path\": null, \"notes\": null, \"reason\": \"dry run failed: Binder Error: No function matches the given name and argument types 'sum(VARCHAR)'. You might need to add explicit type casts.\", \"sql\": \"SELECT round(sum(D_ACCT_SEGMT.INCL_IN_DATA_LOAD_IN), 2) FROM D_ACCT_SEGMT\", \"status\": \"error\", \"unit\": \"wealth\", \"value\": null}], \"step\": \"plan\"}]}",
}
"""The control plans that read Wealth, as control_differences prints each now (pack 1.5.0): SAGE's tables enter the
control arm's name-based heuristic, and every Wealth table and column holds a physical name, so the heuristic, which is
handed no curated metadata, maps other columns or none, and these plans move. Every plan that reads no Wealth table,
every prompt and every allowlist, Wealth's schema taken out on both sides, stays byte-equal."""


def changed_plans(eng) -> list[str]:
    """The differences control_differences must report, the frozen plan beside the pinned new one, in its order."""
    items, added = frozen()["items"], Expansion(eng)
    return [f"{key} plan: {json.dumps(added.plan(items[key]['plan']), sort_keys=True)} → {now}" for key, now in CHANGED_PLANS.items()]


def test_the_control_s_plan_inputs_and_prompt_are_byte_equal_to_before(eng):
    """The frozen control prompt and plans remain byte-equal after removing added schema and dictionary chunks, and
    Wealth's schema, except for the named plans that read Wealth, whose tables and columns hold physical names since
    pack 1.5.0."""
    assert len(frozen()["items"]) == 61
    assert control_differences(eng) == changed_plans(eng), "only the control-arm plans that read Wealth move, as pinned"


def test_the_control_sees_the_expansion_s_tables_in_the_warehouse_schema(eng):
    """Graph off, the control reads each warehouse's catalogue: the Wealth tables under their physical names and the
    counterparty columns are in it, so the comparison above takes something out."""
    added = Expansion(eng)
    assert added.tables == {f"wealth.{t}" for t in names.tables().values()}, "every Wealth table, under its physical name"
    agent = Agent(eng, llm=None)
    q = "Total retail revenue for June 2025"
    prompt = agent.prompt("sql", q, agent.context(q, frozen()["principal"], "analysis", "control")["block"])
    assert "  TRAN_FACT_1: DW_TRAN_ID BIGINT" in prompt and "  HSHLD_DIM_1: HSHLD_DK" in prompt and "counterparty_bank VARCHAR" in prompt
    assert added.schema(prompt) != prompt and "  TRAN_FACT_1:" not in added.schema(prompt) and "  HSHLD_DIM_1:" not in added.schema(prompt)
    assert added.chunks == {"DD-RETAIL#0", "DD-RETAIL#3", "DD-WEALTH#0", *(f"DD-WEALTH#{i}" for i in range(3, 9))}


def test_the_control_is_handed_no_alias_and_no_period_binding(eng, monkeypatch):
    """Take the curated vocabulary away, and the control plans and prompts exactly as before."""
    monkeypatch.setattr(eng, "ALIASES", {})
    monkeypatch.setattr(eng, "PERIODS", {})
    monkeypatch.setattr(eng, "CALENDARS", {})
    monkeypatch.setattr(eng, "FISCAL_START", 7)
    assert control_differences(eng) == changed_plans(eng), "the same plans move, as pinned, without curated aliases"


def test_the_graph_context_reads_the_bindings_and_the_others_the_names(eng, monkeypatch):
    june = Period("month", "2025-06-01")
    for cfg in (PRESETS["schema"], PRESETS["catalog"], AGENT_ARMS["control"]):
        assert eng.period_clause("retail", "monthly_revenue", june, cfg) == ("monthly_revenue.month_start = DATE '2025-06-01'", [], None)
    monkeypatch.setattr(eng, "PERIODS", {})
    for cfg in (PRESETS["graph"], AGENT_ARMS["treatment"]):
        assert eng.period_clause("retail", "monthly_revenue", june, cfg) == ("", [], "no period column; period ignored")
    assert eng.period_clause("retail", "monthly_revenue", june, PRESETS["schema"])[0] == "monthly_revenue.month_start = DATE '2025-06-01'"


def fiscal_from_calendar_6c897ba(cq: str) -> str:
    """The April fiscal function as kge/services/ask/engine/core.py had it at 6c897ba, before the bindings: the oracle they are held to."""
    y, n = int(cq[:4]), int(cq[-1])
    return f"FY{(y % 100):02d}-Q4" if n == 1 else f"FY{((y + 1) % 100):02d}-Q{n - 1}"


def calendar_from_fiscal_6c897ba(fq: str) -> str:
    fy, n = int(fq[2:4]), int(fq[-1])
    return f"20{fy:02d}-Q1" if n == 4 else f"20{(fy - 1):02d}-Q{n + 1}"


def period_clause_6c897ba(cols: set[str], table: str, period: Period | None, graph: bool, agg: str = "sum"):
    if period is None:
        return "", [], None
    if "month_start" in cols:
        if period.kind == "month":
            return f"{table}.month_start = DATE '{period.value}'", [], None
        if period.kind == "calendar_quarter":
            return f"calendar_month.calendar_quarter = '{period.value}'", [f"JOIN calendar_month ON {table}.month_start = calendar_month.month_start"], None
        if period.kind == "fiscal_quarter":
            cq = calendar_from_fiscal_6c897ba(period.value)
            return f"calendar_month.calendar_quarter = '{cq}'", [f"JOIN calendar_month ON {table}.month_start = calendar_month.month_start"], f"{period.value} read as calendar {cq} for a calendar-year unit"
        if period.kind == "year":
            return f"{table}.month_start >= DATE '{period.value}-01-01' AND {table}.month_start < DATE '{int(period.value) + 1}-01-01'", [], None
        if period.kind == "as_of":
            d = dt.date.fromisoformat(period.value)
            return f"{table}.month_start = DATE '{d.year}-{d.month:02d}-01'", [], "as-at date read as its calendar month"
    if "fiscal_quarter" in cols:
        if period.kind == "fiscal_quarter":
            return f"{table}.fiscal_quarter = '{period.value}'", [], None
        if period.kind == "calendar_quarter":
            if not graph:
                return f"{table}.fiscal_quarter = 'FY{period.value[2:4]}-Q{period.value[-1]}'", [], "quarter label taken as written"
            fq = fiscal_from_calendar_6c897ba(period.value)
            return f"{table}.fiscal_quarter = '{fq}'", [], f"calendar {period.value} is {fq} on the April fiscal year (STD-FISCAL-CAL)"
        if period.kind == "month":
            return None, [], "quarterly grain: a single month is not available"
        if period.kind == "year":
            return f"fiscal_calendar.fiscal_year = {int(period.value) + 1}", [f"JOIN fiscal_calendar ON {table}.fiscal_quarter = fiscal_calendar.fiscal_quarter"], "calendar year read as the fiscal year starting that April"
        if period.kind == "as_of":
            d = dt.date.fromisoformat(period.value)
            cq = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
            return f"{table}.fiscal_quarter = '{fiscal_from_calendar_6c897ba(cq)}'", [], "as-at date read as its fiscal quarter"
    if "as_of" in cols:
        if period.kind == "as_of":
            return f"{table}.as_of = DATE '{period.value}'", [], None
        if period.kind == "month":
            d = dt.date.fromisoformat(period.value)
            nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
            return f"{table}.as_of >= DATE '{d.isoformat()}' AND {table}.as_of < DATE '{nxt.isoformat()}'", [], None
        if period.kind == "calendar_quarter":
            y, n = int(period.value[:4]), int(period.value[-1])
            end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[n]
            return f"{table}.as_of = DATE '{y}-{end}'", [], "quarter read as its end date"
        if period.kind == "year":
            lo, hi = f"DATE '{period.value}-01-01'", f"DATE '{int(period.value) + 1}-01-01'"
            if agg == "avg":
                return f"{table}.as_of >= {lo} AND {table}.as_of < {hi}", [], None
            return (f"{table}.as_of = (SELECT max(as_of) FROM {table} WHERE as_of >= {lo} AND as_of < {hi})", [],
                    "snapshots do not add across dates; the year is read as its last snapshot")
    date_cols = [c for c in cols if c.endswith("_on") and c not in ("resolved_on", "closed_on", "end_on")]
    if date_cols and period.kind == "year":
        dc = sorted(date_cols)[0]
        return f"{table}.{dc} >= DATE '{period.value}-01-01' AND {table}.{dc} < DATE '{int(period.value) + 1}-01-01'", [], None
    if date_cols and period.kind == "month":
        return f"strftime({table}.{sorted(date_cols)[0]}, '%Y-%m-01') = '{period.value}'", [], None
    return "", [], "no period column; period ignored"


PERIODS = [None, *(Period(k, v) for k, vs in {
    "month": ("2025-06-01", "2024-12-01", "2025-01-01"),
    "calendar_quarter": ("2025-Q1", "2025-Q2", "2025-Q3", "2024-Q4"),
    "fiscal_quarter": ("FY26-Q1", "FY25-Q4", "FY26-Q3", "FY25-Q2"),
    "year": ("2024", "2025"),
    "as_of": ("2025-06-30", "2025-03-31", "2024-12-31", "2025-02-14")}.items() for v in vs)]


def test_on_every_table_period_and_aggregate_each_context_gets_what_it_got_before(eng):
    """On each of the 35 tables of 4d9d5c9. The Wealth expansion's tables (#39 item 7b) have no period binding yet: their
    time semantics are the pack's next version's (7c). Since pack 1.5.0 Wealth's tables hold physical names in SAGE's
    style (kge/estate/names.py): the graph context reads their bindings and gets, under the physical names, what the name
    heuristic gave on the names they had; every other context finds their period column by its name, as before, so on a
    physical name it finds none."""
    then, _ = before.tables_then()
    renamed, physical_of = before.business_names()
    columns = {v: k for k, v in physical_of.items()}
    business = {v.split(".")[1]: k.split(".")[1] for k, v in renamed.items()}
    tables = [(r["u"], r["t"], renamed.get(r["id"], r["id"])) for r in eng.g.q("MATCH (t:Tbl) RETURN t.unit AS u, t.name AS t, t.id AS id ORDER BY t.id")
              if renamed.get(r["id"], r["id"]) in then]
    assert len(tables) == 35
    configs = [*PRESETS.values(), *AGENT_ARMS.values()]
    moved = []
    for unit, table, was in tables:
        cols = set(eng.g.table_columns(unit, table))
        named = {c: physical_of.get(f"{unit}.{table}.{c}", f"..{c}").rsplit(".", 1)[1] for c in cols}
        bare = {v: k for k, v in named.items() if k != v}

        def spelled(clause: tuple) -> tuple:
            """A clause the heuristic wrote on the names 4d9d5c9 had, under the physical names (Wealth's tables alone)."""
            def text(x):
                if not x:
                    return x
                x = re.sub(r"\b(\w+)\.(\w+)\b", lambda m: ".".join(columns.get(f"wealth.{m[1]}.{m[2]}", f"wealth.{business[m[1]]}.{m[2]}").split(".")[1:])
                           if m[1] in business else m[0], x)
                x = re.sub(r"\b(JOIN|FROM) (\w+)\b", lambda m: f"{m[1]} {business.get(m[2], m[2])}", x)
                return re.sub(r"(?<![.\w])(\w+)(?![.\w])", lambda m: bare.get(m[1], m[1]), x)
            return (text(clause[0]), [text(j) for j in clause[1]], clause[2])
        for period in PERIODS:
            for agg in ("sum", "avg", "count"):
                for cfg in configs:
                    if table == was.split(".")[1] or not cfg.graph:
                        then_ = period_clause_6c897ba(cols, table, period, cfg.graph, agg)
                    else:
                        then_ = spelled(period_clause_6c897ba(set(named.values()), was.split(".")[1], period, cfg.graph, agg))
                    now = eng.period_clause(unit, table, period, cfg, agg)
                    if now != then_:
                        moved.append(f"{cfg.arm} {unit}.{table} {period and period.label()} {agg}: {then_} → {now}")
    assert moved == [], "\n".join(moved[:10])


def test_the_fiscal_functions_are_the_april_ones_on_an_april_year_and_follow_any_other_start():
    """Over two-digit fiscal years, as the labels write them, clear of the century's ends."""
    for y in range(2001, 2098):
        for n in range(1, 5):
            cq, fq = f"{y}-Q{n}", f"FY{y % 100:02d}-Q{n}"
            assert fiscal_from_calendar(cq, 4) == fiscal_from_calendar_6c897ba(cq), cq
            assert calendar_from_fiscal(fq, 4) == calendar_from_fiscal_6c897ba(fq), fq
            for start in (1, 4, 7, 10):
                assert calendar_from_fiscal(fiscal_from_calendar(cq, start), start) == cq, (cq, start)
    assert fiscal_from_calendar("2025-Q2", 1) == "FY25-Q2" and calendar_from_fiscal("FY25-Q2", 1) == "2025-Q2"
    assert fiscal_from_calendar("2025-Q3", 7) == "FY26-Q1" and fiscal_from_calendar("2025-Q2", 7) == "FY25-Q4"
    assert calendar_from_fiscal("FY26-Q1", 7) == "2025-Q3" and calendar_from_fiscal("FY26-Q4", 7) == "2026-Q2"


def test_a_scratch_pack_with_one_more_alias_is_read_with_no_code_change(tmp_path, eng):
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    path = clients / CLIENT / "pack.yaml"
    text = path.read_text()
    line = "    - {phrase: revenue, term: revenue}\n"
    assert text.count(line) == 1
    path.write_text(text.replace(line, line + "    - {phrase: takings, term: revenue}\n"))
    art = compile_pack(tmp_path / "artifacts", clients)
    build = scratch_build(tmp_path / "build", graph=False)
    r = load_artifact(build, art)
    assert r.returncode == 0, r.stderr
    e = Engine(profile_of(build))
    try:
        assert e.ALIASES["takings"] == "Revenue" and "takings" not in eng.ALIASES
        took = e.answer("Total retail takings for June 2025")
        said = e.answer("Total retail revenue for June 2025")
        assert took["trace"][0]["term"] == "Revenue" and took["behaviour"] == "figure"
        assert took["figure"] == said["figure"] == 13287.67 and took["sql"] == said["sql"]
        assert eng.answer("Total retail takings for June 2025")["behaviour"] == "abstain", "the demo's own pack has no such phrase"
    finally:
        e.close()


def test_make_principals_binds_the_identities_to_the_pack_s_roles(tmp_path):
    build = scratch_build(tmp_path / "build")
    before = json.loads((BUILD / "principals.json").read_text())
    written = run_of(build) / "principals.json"
    written.unlink()
    r = script("kge/graph/load.py", "--principals-only", build=build)
    assert r.returncode == 0, r.stderr
    assert json.loads(written.read_text()) == before, "in the profile's run state, as the estate writes it"
    assert json.loads(r.stdout.splitlines()[-1]) == {"principals": len(before)}
    assert "restart the service so it reads the new roles" in r.stdout
    assert not written.is_symlink(), "written whole, never through a link"
