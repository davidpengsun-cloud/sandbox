"""The SQL dialect a model writes is the dialect of the engine that runs it (#97): the warehouse port names it, for a
prompt (WarehouseSet.query_dialect: "DuckDB", "BigQuery GoogleSQL") and for the gate and the warehouses (sql_dialect:
"duckdb", "bigquery"), never a string in the engine. DuckDB's files give exactly the text the prompts held before, so
their hashes do not move. On BigQuery the gate reads a model's SQL as BigQuery's and the warehouses send it as written but
for its tables' names (route_sql): nothing is translated, so DuckDB-only syntax is refused by the gate or BigQuery's dry
run, never run as something else. The code's own SQL stays DuckDB's (AUTHORED) and is rewritten as before; the caller
says which it passes. Run on the stand-in for BigQuery (tests/bigquery_fake.py), which reads BigQuery's dialect."""
from contextlib import contextmanager
from pathlib import Path

import pytest

from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse.duckdb import Warehouses
from kge.services.ask.engine import agent as agent_module

MERIDIAN = ["retail", "sme", "corporate", "wealth"]
BEFORE = ("You are a text-to-SQL agent for a bank's four business units: retail, sme, corporate and wealth. Each unit has\n"
          "its own DuckDB warehouse. Answer the analyst's question from the context below and nothing else.\n")
FIELD = ('  "sql"            {"<unit>": "<one DuckDB SELECT on that unit\'s warehouse, returning one row with one number>"};\n'
         "                   {} for a behaviour that runs no query\n")


class Stand:
    query_dialect, sql_dialect = "Dialect-X", "duckdb"

    def gate(self, unit, sql, allow, dialect="duckdb"):
        return {"passed": False, "problems": ["empty"]}


class Engine:
    UNIT_ORDER = ["retail", "sme"]
    UNIT_LABEL, UNIT_KEY = {"retail": "Retail"}, {"retail": "customer_id"}
    w = Stand()


def test_each_warehouse_backend_names_the_dialect_it_runs():
    assert (Warehouses.query_dialect, Warehouses.sql_dialect) == ("DuckDB", "duckdb")
    assert (BigQueryWarehouses.query_dialect, BigQueryWarehouses.sql_dialect) == ("BigQuery GoogleSQL", "bigquery")


def test_the_duckdb_prompt_is_the_text_it_was():
    prompt = agent_module.sql_prompt(MERIDIAN, Warehouses.query_dialect)
    assert prompt.startswith(BEFORE) and FIELD in prompt and prompt.count("DuckDB") == 2
    assert "{dialect}" not in prompt


def test_the_agent_asks_for_the_dialect_the_warehouses_name():
    agent = agent_module.Agent.__new__(agent_module.Agent)
    agent.e = Engine()
    prompt = agent.prompt("sql", "Q?", "block")
    assert "its own Dialect-X warehouse" in prompt and "<one Dialect-X SELECT on that unit's warehouse" in prompt
    assert "DuckDB" not in prompt


class Asked:
    name = "test:asked"

    def __init__(self):
        self.prompts = []

    def json(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return {"sql": ""}


class Allow:
    def as_prompt(self):
        return "Allowed: retail.sales.amount"


def test_the_typed_interpreter_requests_data_instead_of_a_sql_dialect():
    from kge.services.ask.engine.planmodel import read
    from tests.plan_fixtures import vocabulary
    model = Asked()
    assert read(model, 'Metric', vocabulary()).kind == 'abstain'
    assert model.prompts[0].startswith('query-plan-v1\n')
    assert 'Dialect-X' not in model.prompts[0]


BIGQUERY_SQL = ("SELECT ROUND(SAFE_DIVIDE(SUM(t.revenue), COUNT(t.customer_id)), 2) FROM `monthly_revenue` AS t "
                "WHERE DATE_TRUNC(t.month_start, MONTH) = DATE '2025-06-01'")
FIGURE_SQL = "SELECT ROUND(SUM(t.revenue), 2) FROM `monthly_revenue` AS t WHERE DATE_TRUNC(t.month_start, MONTH) = DATE '2025-06-01'"


def allowlist(attached: bool = False):
    from kge.pipeline.sqlguard import Allowlist
    t = "retail.monthly_revenue" if attached else "monthly_revenue"
    cols = {"month_start", "revenue", "customer_id", "status"}
    return Allowlist("retail", {t}, {t: set(cols)}, restricted={(t, "email")}, schema={t: cols | {"email"}},
                     measures={(t, "revenue"), (t, "customer_id")}, attached=attached)


@pytest.fixture
def bigquery(monkeypatch):
    """BigQueryWarehouses for retail and wealth, every request answered by the stand-in, which holds retail's
    monthly_revenue as the dataset's retail_monthly_revenue; sent: each request's body."""
    from tests.bigquery_fake import FakeBigQuery
    fake = FakeBigQuery()
    fake.con.execute("CREATE TABLE retail_monthly_revenue (month_start DATE, revenue DECIMAL(18,2), customer_id BIGINT, "
                     "status VARCHAR, email VARCHAR)")
    fake.con.execute("INSERT INTO retail_monthly_revenue VALUES ('2025-06-01', 100.00, 1, 'open', 'a@x'), "
                     "('2025-06-15', 50.50, 2, 'closed', 'b@x'), ('2025-07-01', 7.00, 3, 'open', 'c@x')")
    w = BigQueryWarehouses(Path("warehouses"), ["retail", "wealth"], project="pilot-project", client="meridian")
    w.sent, w.fake = [], fake

    def post(url, headers, body):
        w.sent.append(body)
        return fake.run(None, body["query"], dry_run=body.get("dryRun", False))
    monkeypatch.setattr(w, "_post", post)
    monkeypatch.setattr(w, "token", lambda: ("stand-in", "pilot-project"))
    return w


def test_a_models_bigquery_sql_passes_the_gate_and_runs_as_written_but_for_its_tables(bigquery):
    """SAFE_DIVIDE, DATE_TRUNC(d, MONTH) and backticks: BigQuery's, read as BigQuery's by the gate, dry run and run as
    the same text, the model's own with only the table routed to the dataset's."""
    guard = bigquery.gate("retail", BIGQUERY_SQL, allowlist(), dialect="bigquery")
    assert guard["passed"], guard
    routed = BIGQUERY_SQL.replace("`monthly_revenue` AS t", "`retail_monthly_revenue` AS t")
    assert bigquery.sent[-1] == {**bigquery.sent[-1], "query": routed, "dryRun": True}
    assert bigquery.run("retail", BIGQUERY_SQL, "bigquery") == 75.25
    assert bigquery.sent[-1]["query"] == routed and "dryRun" not in bigquery.sent[-1]


def test_the_same_sql_is_refused_where_the_warehouses_are_duckdb_s():
    """DATE_TRUNC(d, MONTH) is BigQuery's: read as DuckDB's, MONTH is a column the warehouse does not have."""
    from kge.pipeline.sqlguard import check
    assert check(FIGURE_SQL, allowlist(), "bigquery") == []
    assert check(FIGURE_SQL, allowlist(), "duckdb")
    with pytest.raises(ValueError, match="DuckDB's SQL, not 'bigquery'"):
        Warehouses.reads(Warehouses, "bigquery")


@pytest.mark.parametrize("sql", ["SELECT sum(t.revenue) FROM monthly_revenue t JOIN orders o ON t.customer_id = o.customer_id",
                                 "SELECT sum(t.email) FROM monthly_revenue t",
                                 "SELECT sum(revenue) FROM monthly_revenue WHERE status = 'open'",
                                 "SELECT t.revenue FROM other.monthly_revenue t"])
def test_unknown_and_forbidden_tables_and_columns_are_refused_the_same_way_in_both_dialects(sql):
    from kge.pipeline.sqlguard import check
    assert check(sql, allowlist(), "bigquery") == check(sql, allowlist(), "duckdb")
    assert check(sql, allowlist(), "bigquery") or "status = 'open'" in sql


def test_a_table_in_another_project_or_dataset_is_refused(bigquery):
    guard = bigquery.gate("retail", "SELECT sum(t.revenue) FROM `proj.ds.monthly_revenue` t", allowlist(), dialect="bigquery")
    assert not guard["passed"] and guard["failed_at"] == "static"
    assert any("outside this unit's warehouse" in p for p in guard["problems"]), guard
    assert bigquery.sent == []


def test_duckdb_only_syntax_from_a_model_is_refused_or_reaches_bigquery_as_written(bigquery):
    """Integer division (//) does not parse as BigQuery's; a double-quoted name, a string in BigQuery that would add up a
    constant with no error, is refused; DuckDB's :: cast is sent verbatim for BigQuery's own dry run to judge (the
    stand-in runs it, BigQuery refuses it: the owner's live check), never written as CAST by this code."""
    allow = allowlist()
    divided = bigquery.gate("retail", "SELECT sum(t.revenue) // 2 FROM monthly_revenue t", allow, dialect="bigquery")
    assert not divided["passed"] and divided["problems"][0].startswith("does not parse")
    quoted = bigquery.gate("retail", "SELECT sum(t.revenue) FROM monthly_revenue t WHERE \"status\" = 'open'", allow,
                           dialect="bigquery")
    assert quoted["problems"] == ['"status" is a string in this dialect, not a name: quote a table or column with backticks']
    assert bigquery.sent == []
    bigquery.gate("retail", "SELECT sum(t.revenue)::FLOAT64 FROM monthly_revenue t", allow, dialect="bigquery")
    assert bigquery.sent[-1]["query"] == "SELECT sum(t.revenue)::FLOAT64 FROM `retail_monthly_revenue` t"


def test_a_double_quoted_string_stays_a_string_where_it_names_nothing(bigquery):
    from kge.pipeline.sqlguard import check
    assert check('SELECT sum(t.revenue) FROM monthly_revenue t WHERE t.status = "open"', allowlist(), "bigquery") == []
    assert check('SELECT sum(t.revenue) FROM monthly_revenue t WHERE t."status" = \'open\'', allowlist(), "duckdb") == []


def test_an_attached_session_routes_each_unit_s_tables_and_qualified_columns(bigquery):
    sql = ("SELECT ROUND(SUM(retail.monthly_revenue.revenue), 2) FROM `retail.monthly_revenue` "
           "WHERE DATE_TRUNC(retail.monthly_revenue.month_start, MONTH) = DATE '2025-06-01'")
    with bigquery.attached(["retail", "wealth"], "bigquery") as con:
        assert con.execute(sql).fetchone() == (150.5,)
    assert bigquery.sent[-1]["query"] == (
        "SELECT ROUND(SUM(retail_monthly_revenue.revenue), 2) FROM `retail_monthly_revenue` AS retail_monthly_revenue "
        "WHERE DATE_TRUNC(retail_monthly_revenue.month_start, MONTH) = DATE '2025-06-01'")


def test_the_code_s_own_sql_is_still_duckdb_s_and_rewritten(bigquery):
    """The planner's SQL, DuckDB's (AUTHORED, the default), goes through rewrite_sql as before: a tuple's COUNT(DISTINCT)
    that BigQuery lacks is written as BigQuery counts it."""
    bigquery.rows("retail", "SELECT count(DISTINCT (status, customer_id)) FROM monthly_revenue")
    assert "TO_JSON_STRING(STRUCT(status AS k0, customer_id AS k1))" in bigquery.sent[-1]["query"]
    with pytest.raises(ValueError, match="not 'snowflake'"):
        bigquery.rows("retail", "SELECT 1", "snowflake")


class Scripted:
    """A model that writes the SQL it is given, for any prompt."""
    name = "test:scripted"

    def __init__(self, sql: str):
        self.sql, self.prompts = sql, []

    def json(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return {"sql": self.sql}


class OnBigQuery:
    UNIT_ORDER = ["retail", "wealth"]
    UNIT_LABEL, UNIT_KEY = {"retail": "Retail"}, {"retail": "customer_id"}

    def __init__(self, w):
        self.w = w

    @contextmanager
    def session(self, plan, dialect="duckdb"):
        from kge.services.ask.engine.core import Engine
        with Engine.session(self, plan, dialect) as con:
            yield con


def test_a_sql_reply_cannot_enter_the_typed_interpreter_or_reach_bigquery(bigquery):
    from kge.services.ask.engine.planmodel import read
    from tests.plan_fixtures import vocabulary
    model = Scripted(BIGQUERY_SQL)
    assert read(model, 'Metric', vocabulary()).kind == 'abstain'
    assert len(model.prompts) == 2 and bigquery.sent == []




BIGQUERY_FUNCTIONS = {"Sum", "Count", "Avg", "Min", "Max", "Round", "Coalesce", "Nullif", "Cast", "LastDay", "DateTrunc",
                      "Exists", "Case", "If", "Concat", "CurrentTimestamp", "Struct", "Array", "Unnest", "TimeToStr", "TsOrDsToDate",
                      "And", "Or", "Not", "Distinct", "JSONFormat"}


def _fixed_sql() -> list[tuple[str, str]]:
    """The code's own SQL that reaches the warehouses on BigQuery: every golden_wealth query, as written and naive, and
    the rules path's month filter (kge/services/ask/engine/core.py)."""
    from kge.estate import golden_wealth
    out = []
    for item in golden_wealth.items():
        for sql in [item.sql, *(s for _, s in item.naive), *([item.as_written[1]] if item.as_written else [])]:
            if sql:
                out.append((item.id, sql))
    out.append(("rules month", "SELECT round(sum(monthly_revenue.revenue), 2) FROM monthly_revenue "
                               "WHERE strftime(monthly_revenue.month_start, '%Y-%m-01') = '2025-06-01'"))
    return out


def test_the_code_s_own_duckdb_sql_is_written_as_bigquery_it_runs():
    """Grok n1 on #97: the code's fixed SQL stays DuckDB's and is rewritten; each form it uses is written as valid
    BigQuery. DuckDB's date_trunc('month', d) on a DATE is DATE_TRUNC(d, MONTH), never TIMESTAMP_TRUNC, an interval's
    count a number (INTERVAL 1 MONTH), last_day LAST_DAY, strftime FORMAT_DATE; no function is left that BigQuery lacks."""
    import sqlglot
    from sqlglot import exp
    from kge.adapters.warehouse.bigquery import rewrite_sql
    seen = set()
    for name, sql in _fixed_sql():
        text = rewrite_sql(sql, lambda unit: unit, "wealth")
        tree = sqlglot.parse_one(text, read="bigquery")
        kinds = {type(n).__name__ for n in tree.walk() if isinstance(n, exp.Func)}
        assert kinds <= BIGQUERY_FUNCTIONS, (name, kinds - BIGQUERY_FUNCTIONS, text)
        assert "TIMESTAMP_TRUNC" not in text and "INTERVAL '" not in text, (name, text)
        seen |= kinds
    assert {"DateTrunc", "LastDay", "TimeToStr"} <= seen
    example = rewrite_sql("SELECT 1 FROM flow f JOIN month_end m ON m.month_end_date=date_trunc('month', f.flow_date)"
                          "+INTERVAL 1 MONTH-INTERVAL 1 DAY", lambda unit: unit, "wealth")
    assert "m.month_end_date = DATE_TRUNC(f.flow_date, MONTH) + INTERVAL 1 MONTH - INTERVAL 1 DAY" in example
    month = rewrite_sql("SELECT 1 FROM t WHERE strftime(t.d, '%Y-%m-01') = '2025-06-01'", lambda unit: unit, "retail")
    assert "FORMAT_DATE('%Y-%m-01', t.d) = '2025-06-01'" in month




def test_a_table_named_in_another_case_is_routed_by_its_declared_name(bigquery):
    """Grok n2 on #97: the gate reads a table's name in any case, so MONTHLY_REVENUE passes it; BigQuery's table names are
    case-sensitive, so the route names the table as the allowlist the gate checked spells it (#176: the physical name,
    whatever its case), `retail_monthly_revenue`, never `retail_MONTHLY_REVENUE`, in a unit's query and in an attached
    session's, its column qualifiers following, and a unit's name in lower case."""
    sql = "SELECT ROUND(SUM(t.revenue), 2) FROM MONTHLY_REVENUE AS t"
    guard = bigquery.gate("retail", sql, allowlist(), dialect="bigquery")
    assert guard["passed"], guard
    assert bigquery.sent[-1]["query"] == "SELECT ROUND(SUM(t.revenue), 2) FROM `retail_monthly_revenue` AS t"
    assert bigquery.run("retail", sql, "bigquery") == 157.5
    assert bigquery.sent[-1]["query"] == "SELECT ROUND(SUM(t.revenue), 2) FROM `retail_monthly_revenue` AS t"
    attached = "SELECT SUM(Retail.Monthly_Revenue.revenue) FROM `Retail.Monthly_Revenue`"
    bigquery.learn(allowlist(attached=True))
    with bigquery.attached(["retail", "wealth"], "bigquery") as con:
        assert con.execute(attached).fetchone() == (157.5,)
    assert bigquery.sent[-1]["query"] == (
        "SELECT SUM(retail_monthly_revenue.revenue) FROM `retail_monthly_revenue` AS retail_monthly_revenue")


def test_period_clause_emits_format_date_for_bigquery_and_strftime_for_duckdb():
    """Live E2E finding on /agent ('Lease rentals for June 2025'): event_date tables queried by month must emit
    FORMAT_DATE('%Y-%m-01', col) when dialect='bigquery' (used in Agent.roles and Combined.generate prompts whose
    model output is gated with dialect='bigquery' without rewrite_sql), while defaulting to DuckDB strftime."""
    from kge.services.ask.engine.core import AGENT_ARMS, Engine, Period
    eng = Engine.__new__(Engine)
    eng.PERIODS = {"leasing.rental": {"column": "billed_on", "kind": "event_date", "calendar": "CAL-GREG", "rollup_table": None}}
    eng.CALENDARS = {"CAL-GREG": {"year_start_month": 1}}
    eng.g = type("G", (), {"table_columns": lambda self, u, t: ["billed_on", "lease_rentals_usd"]})()
    eng.w = type("W", (), {"sql_dialect": "bigquery", "columns": lambda self, u: [("rental", "billed_on", "DATE")]})()
    p = Period("month", "2025-06-01")
    for cfg in (AGENT_ARMS["treatment"], AGENT_ARMS["control"]):
        duck_where, _, _ = eng.period_clause("leasing", "rental", p, cfg)
        bq_where, _, _ = eng.period_clause("leasing", "rental", p, cfg, dialect="bigquery")
        assert duck_where == "strftime(rental.billed_on, '%Y-%m-01') = '2025-06-01'"
        assert bq_where == "FORMAT_DATE('%Y-%m-01', rental.billed_on) = '2025-06-01'"



class PeriodsOnBigQuery(OnBigQuery):
    """The engine's own period_clause, on the BigQuery stand-in's warehouses, with retail's monthly_revenue bound as an
    event_date table by month_start, so a month is the clause written_in puts in the warehouses' dialect."""
    PERIODS = {"retail.monthly_revenue": {"column": "month_start", "kind": "event_date", "calendar": "CAL-GREG",
                                          "rollup_table": None}}
    CALENDARS = {"CAL-GREG": {"year_start_month": 1}}

    def period_clause(self, *args, **kwargs):
        from kge.services.ask.engine.core import Engine
        return Engine.period_clause(self, *args, **kwargs)


def test_legacy_context_and_typed_compiler_keep_event_months_valid_in_bigquery(bigquery):
    """The retained Context speaks the warehouse dialect; typed numeric SQL uses checked date boundaries."""
    from types import SimpleNamespace
    from kge.services.ask.engine.core import AGENT_ARMS, Period
    june = Period("month", "2025-06-01")
    clause = "FORMAT_DATE('%Y-%m-01', monthly_revenue.month_start) = '2025-06-01'"
    agent = agent_module.Agent.__new__(agent_module.Agent)
    agent.e = PeriodsOnBigQuery(bigquery)
    req = SimpleNamespace(filters=[], agg="sum", period=june)
    lines = agent.roles(req, "revenue", {"unit": "retail", "tbl": "monthly_revenue", "col": "revenue", "dtype": "DECIMAL"},
                        AGENT_ARMS["treatment"], {})
    prompt = agent.prompt("sql", "Retail revenue for June 2025", "\n".join(lines))
    assert f"    period   {clause}" in prompt and "strftime" not in prompt, prompt
    from kge.adapters.warehouse.bigquery import rewrite_sql
    from tests.plan_fixtures import model, name
    from tests.unit.test_plan_compiler import compiled
    statement = compiled(model(time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}}),
                         'Metric June 2025', {'June 2025': 'time'}).answer.sql
    sql = rewrite_sql(statement, lambda table: table)
    assert '2025-06-01' in sql and '2025-07-01' in sql and 'strftime' not in sql


def test_recorded_mock_plan_uses_authored_sql_on_bigquery(monkeypatch, tmp_path):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.planmodel import read
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import vocabulary
    from tests.plan_recordings import recorded
    from tests.unit.test_planner_jobs import jobs, setup

    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        out = Planner(vocabulary(), warehouses, Hidden([], set(), [])).answer(
            "Metric", lambda question, scoped: read(recorded(), question, scoped))
        assert out["kind"] == "result", out
        assert float(out["result"]["rows"][0]["cells"][0]["value"]["value"]) == 15
        assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()
