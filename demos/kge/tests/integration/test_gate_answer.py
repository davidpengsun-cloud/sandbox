"""Every /answer query passes the SQL gate before it runs, in every arm.

    .venv/bin/python -m pytest tests/integration/test_gate_answer.py -q

The graph arm's allowlist is the question's certified measure, its filter
columns, the declared join keys and the period columns. The other arms get the
warehouse schema. In every arm a restricted personal-data column is refused,
and nothing refused reaches the warehouse.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp
from fastapi.testclient import TestClient

from kge.services.ask.engine.core import ARMS, PRESETS, Request
from kge.pipeline.sqlguard import build_allowlist, check, gate, schema_allowlist
from kge.estate.spec import RESTRICTED_COLUMNS
from kge.eval import sets
from kge.eval.run_golden import score, uncertified_ids
from tests.scratch import BUILD, profile_of, scratch_build

GOLDEN = sets.items(BUILD / "golden.json")
EMAIL = {"unit": "retail", "tbl": "customer", "col": "email", "dtype": "VARCHAR", "grain": "", "currency": "",
         "calendar": "", "conversion": "", "certified_by": "", "certified_on": ""}


def test_graph_arm_scores_every_golden_item_with_the_gate_on(eng):
    unc = uncertified_ids(BUILD)
    failed = [it["id"] for it in GOLDEN if not score(it, eng.answer(it["question"], arm_name="graph"), unc)[0]]
    assert failed == []


@pytest.mark.parametrize("arm", sorted(ARMS))
def test_no_golden_query_is_refused_and_what_ran_passes_on_its_own(eng, arm, monkeypatch):
    ran: list[tuple[str, str]] = []
    real = eng.w.run
    monkeypatch.setattr(eng.w, "run", lambda unit, sql, *rest: ran.append((unit, sql)) or real(unit, sql, *rest))
    for it in GOLDEN:
        a = eng.answer(it["question"], arm_name=arm)
        planned = a.get("plan") or (a["parts"] if isinstance(a.get("parts"), list) else [])
        assert not [p for p in planned if p.get("status") == "refused"], it["id"]
    assert ran
    for unit, sql in ran:
        assert check(sql, schema_allowlist(eng.g, unit, RESTRICTED_COLUMNS)) == [], sql


@pytest.mark.parametrize("gid, column", [("G004", "region_code"), ("G007", "product_name"), ("G012", "team_code"),
                                         ("G015", "country_code"), ("G019", "RISK_PRFL_CD"), ("G020", "RVW_OUTCM_CD")])
def test_a_filter_column_is_allowed_in_the_graph_arm(eng, gid, column):
    item = next(it for it in GOLDEN if it["id"] == gid)
    a = eng.answer(item["question"], arm_name="graph")
    assert a["behaviour"] == "figure" and column in a["sql"]


@pytest.mark.parametrize("value, literal", [
    ("O'BRIEN", "'O''BRIEN'"),
    ("x' OR '1'='1", "'x'' OR ''1''=''1'"),
])
def test_a_filter_value_is_one_escaped_sql_literal(eng, value, literal):
    req = Request("probe", filters=[("product_name", "=", value)])
    impl = {"unit": "retail", "tbl": "monthly_revenue", "col": "total_revenue_usd", "dtype": "DOUBLE", "grain": "",
            "currency": "", "calendar": "", "conversion": ""}
    planned = eng.plan_unit(req, impl, PRESETS["graph"])
    assert planned["status"] == "ok" and f"product.product_name = {literal}" in planned["sql"]
    tree = sqlglot.parse_one(planned["sql"], read="duckdb")
    assert value in [lit.this for lit in tree.find_all(exp.Literal) if lit.is_string]
    assert not list(tree.find_all(exp.Or))


def test_a_lone_quote_is_a_gate_refusal_not_an_exception(eng):
    allow = schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS)
    assert check("SELECT count(*) FROM monthly_revenue WHERE product_id = '", allow)[0].startswith("does not parse:")


@pytest.mark.parametrize("arm", sorted(ARMS))
def test_a_restricted_column_is_refused_and_never_queried(eng, arm, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(eng.w, "run", lambda unit, sql, *rest: ran.append(sql))
    monkeypatch.setattr(eng, "resolve", lambda term, a: [dict(EMAIL)])
    a = eng.answer("Average retail revenue in June 2025", arm_name=arm)
    assert a["behaviour"] == "refuse" and "restricted" in a["reason"]
    assert ran == []


def test_the_restricted_list_names_customer_personal_data():
    assert {("retail", "customer", "email"), ("retail", "customer", "date_of_birth")} <= set(RESTRICTED_COLUMNS)


@pytest.mark.parametrize("sql", ["SELECT count(customer.email) FROM customer",
                                 "SELECT count(c.email) FROM customer c",
                                 "SELECT count(email) FROM customer",
                                 "SELECT count(*) FROM customer WHERE date_of_birth < DATE '1970-01-01'"])
def test_the_schema_allowlist_still_refuses_restricted_columns(eng, sql):
    allow = schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS)
    assert any("restricted" in p for p in check(sql, allow)), check(sql, allow)


def test_the_schema_allowlist_admits_an_ordinary_column(eng):
    allow = schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS)
    assert check("SELECT count(*) FROM customer WHERE customer.segment_code = 'MASS'", allow) == []


BYPASSES = [
    "SELECT * FROM customer",
    "SELECT c.* FROM customer c",
    "SELECT COLUMNS('email') FROM customer",
    "SELECT max(epoch(COLUMNS('date_of_birth'))) FROM customer WHERE customer_id = 118",
    "SELECT #2 FROM customer",
    "SELECT max(EMAIL) AS x FROM customer",
    'SELECT max("Email") FROM customer',
    "SELECT C.email AS C FROM customer c",
    "SELECT count(*) AS EMAIL FROM customer WHERE EMAIL LIKE 'a%'",
    "SELECT 1 AS x FROM customer UNION ALL SELECT EMAIL AS EMAIL FROM customer",
    "SELECT c FROM customer c",
    "SELECT max(customer::VARCHAR) AS customer FROM customer",
    "SELECT count(*) FROM customer c WHERE c::VARCHAR LIKE '%okafor118@%'",
    "SELECT max(length(struct_extract(c, 'email'))) FROM customer c",
    "SELECT c['email'] AS e FROM customer c",
    "SELECT (c).email AS e FROM customer c",
    "SELECT max(x.y.email) AS m FROM (SELECT c AS y FROM customer c) x",
    "WITH customer AS (SELECT email AS email FROM customer) SELECT max(email) AS m FROM customer",
    "WITH x AS (SELECT email AS e FROM customer) SELECT max(e) FROM x",
    "SELECT count(*) FROM customer NATURAL JOIN branch",
    "SELECT count(*) FROM customer JOIN branch USING (opened_on)",
    "SELECT count(*) FROM customer JOIN product ON customer.branch_id + 0 = product.product_id",
    "SELECT count(*) FROM customer JOIN product ON customer.branch_id IN (product.product_id)",
    "SELECT count(*) FROM customer JOIN product ON customer.branch_id IS NOT DISTINCT FROM product.product_id",
    "SELECT count(*) FROM customer, product",
    "SELECT count(*) FROM read_csv('/etc/passwd')",
    "SELECT max(length(segment_code)) AS n FROM customer c(customer_id, first_name, last_name, date_of_birth, segment_code)",
    "SELECT count(*) FROM customer c(cid, fn, ln, dob, em, seg, customer_id, op) JOIN account a ON c.customer_id = a.customer_id",
    "SELECT count(*) FROM customer c WHERE EXISTS (SELECT 1 FROM product p WHERE p.product_id = c.branch_id)",
]


@pytest.mark.parametrize("mode", ["schema", "certified"])
@pytest.mark.parametrize("sql", BYPASSES)
def test_a_bypass_is_refused(eng, mode, sql):
    """Each bypass reads a column without naming it, or a join without declaring it, and was a working bypass in review;
    each must stay refused against both allowlists."""
    allow = (schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS) if mode == "schema"
             else build_allowlist(eng.g, "retail", [("customer", "segment_code")], RESTRICTED_COLUMNS))
    assert check(sql, allow), sql


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM customer WHERE customer.segment_code = 'MASS'",
    "SELECT segment_code AS s, count(*) AS n FROM customer GROUP BY s ORDER BY s",
    "WITH x AS (SELECT customer.segment_code AS s FROM customer) SELECT count(*) FROM x WHERE x.s = 'MASS'",
    "SELECT count(*) FROM customer JOIN branch ON customer.branch_id = branch.branch_id WHERE branch.region_code = 'NW'",
    "SELECT b.region_code AS r, (SELECT count(*) FROM customer c WHERE c.branch_id = b.branch_id) AS n FROM branch b",
    "SELECT count(*) FROM customer c JOIN account a ON a.customer_id = c.customer_id "
    "WHERE c.branch_id IN (SELECT b.branch_id FROM branch b WHERE b.branch_id = c.branch_id AND a.customer_id = c.customer_id)",
])

def test_an_ordinary_query_still_passes(eng, sql):
    """A subquery inside a predicate is its own scope: its correlation is checked there, not read as a join."""
    assert check(sql, schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS)) == []


def test_a_source_that_is_not_a_table_is_named_in_the_refusal(eng):
    problems = check("SELECT count(*) FROM read_csv('/etc/passwd')", schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS))
    assert any("read_csv" in p.lower() and "not in the retail warehouse" in p for p in problems), problems


def test_a_model_cannot_read_a_restricted_column_through_ask(eng, monkeypatch):
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_combined import Q, Stub
    evil = "SELECT (SELECT max(epoch(COLUMNS('date_of_birth'))) FROM customer) AS c1_count, 0 AS c2_count, 0 AS m1_value, 0 AS m2_value"
    c = Combined(eng, llm=Stub(sql=[evil, evil]))
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    a = c.ask(Q, "analyst_retail")
    assert a["behaviour"] == "abstain" and "figures" not in a


def test_a_query_the_warehouse_cannot_plan_is_an_abstention_not_a_refusal(eng):
    """The question resolves to a sum over a VARCHAR grade."""
    a = eng.answer("Total corporate internal rating June 2025", arm_name="graph")
    assert a["behaviour"] == "abstain" and "dry run failed" in a["reason"]


def test_a_certified_allowlist_cannot_be_given_a_restricted_column(eng):
    allow = build_allowlist(eng.g, "retail", [("customer", "email")], RESTRICTED_COLUMNS)
    assert any("restricted" in p for p in check("SELECT count(customer.email) FROM customer", allow))


@pytest.mark.parametrize("mode", ["schema", "certified"])
def test_a_restricted_column_is_reported_as_restricted_first_and_only_so(eng, mode):
    """The control has no certification, so "not certified" would mislead; restricted data is the reason that
    matters."""
    allow = (schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS) if mode == "schema"
             else build_allowlist(eng.g, "retail", [("monthly_revenue", "total_revenue_usd")], RESTRICTED_COLUMNS))
    for sql in ["SELECT count(customer.first_name) FROM customer",
                "SELECT count(customer.email) FROM customer JOIN account ON account.customer_id = customer.customer_id "
                "WHERE account.currency_code = 'USD'"]:
        problems = gate(eng.w.con["retail"], sql, allow, figure=True)["problems"]
        assert "is restricted personal data (POL-DATA-CLASS)" in problems[0], problems
        assert not [p for p in problems if "not certified" in p and ("first_name" in p or "email" in p)], problems


REVENUE = [("monthly_revenue", "total_revenue_usd")]
JUNE = "monthly_revenue.month_start = DATE '2025-06-01'"
TOTAL = "SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue"
NOT_FIGURES = [
    ("SELECT 13287.67", "aggregate"),
    ("SELECT 99999.0 AS x FROM monthly_revenue LIMIT 1", "aggregate"),
    (f"SELECT round(sum(monthly_revenue.total_revenue_usd), 2) * 2 FROM monthly_revenue WHERE {JUNE}", "aggregate"),
    (f"SELECT sum(monthly_revenue.total_revenue_usd) + 1 FROM monthly_revenue WHERE {JUNE}", "aggregate"),
    ("SELECT max(13287.67) FROM monthly_revenue", "one column"),
    (f"SELECT sum(monthly_revenue.total_revenue_usd * 2) FROM monthly_revenue WHERE {JUNE}", "one column"),
    ("WITH c AS (SELECT 99999.0 AS v FROM monthly_revenue) SELECT sum(c.v) FROM c", "not a column of a table"),
    ("SELECT sum(t.v) FROM (SELECT monthly_revenue.total_revenue_usd AS v FROM monthly_revenue) t", "not a column of a table"),
    ("SELECT round(sum(monthly_revenue.total_revenue_usd), 2), count(*) FROM monthly_revenue", "one value"),
    ("SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue GROUP BY monthly_revenue.month_start", "GROUP BY"),
    ("SELECT count(*) FROM monthly_revenue", "count(*)"),
    (f"SELECT count(*) FROM monthly_revenue WHERE {JUNE}", "count(*)"),
    (f"SELECT round(sum(monthly_revenue.total_revenue_usd), -3) FROM monthly_revenue WHERE {JUNE}", "aggregate"),
    (f"SELECT round(sum(monthly_revenue.total_revenue_usd), 1 - 3) FROM monthly_revenue WHERE {JUNE}", "aggregate"),
    (f"SELECT round(sum(monthly_revenue.total_revenue_usd), 2.5) FROM monthly_revenue WHERE {JUNE}", "aggregate"),
    (f"{TOTAL}, unnest([1, 2]) WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL}, unnest([1, 2, 3]) WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL}, (VALUES (1), (2)) AS v WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL}, (SELECT 1 UNION ALL SELECT 2) AS s WHERE {JUNE}", "the warehouse's own tables"),
    (f"WITH s AS (SELECT 1 AS k UNION ALL SELECT 2) {TOTAL}, s WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL} JOIN (SELECT 1 AS k UNION ALL SELECT 2) s ON TRUE WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL}, LATERAL (SELECT 1 AS k) l WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL}, range(3) WHERE {JUNE}", "not in the retail warehouse"),
    (f"WITH c AS (SELECT complaint.customer_id FROM complaint) {TOTAL} JOIN c ON monthly_revenue.account_id = c.customer_id "
     f"WHERE {JUNE}", "the warehouse's own tables"),
    (f"{TOTAL} WHERE {JUNE} AND monthly_revenue.account_id IN (SELECT complaint.customer_id FROM complaint)", "the warehouse's own tables"),
    (f"{TOTAL} WHERE {JUNE} AND monthly_revenue.account_id = ANY (SELECT complaint.customer_id FROM complaint)", "the warehouse's own tables"),
    (f"{TOTAL} WHERE {JUNE} AND EXISTS (SELECT complaint.customer_id FROM complaint "
     "WHERE complaint.customer_id = monthly_revenue.account_id)", "not a declared join"),
    (f"{TOTAL} WHERE {JUNE} AND random() < 0.5", "every row asked for"),
    (f"{TOTAL} WHERE {JUNE} AND gen_random_uuid() IS NOT NULL", "every row asked for"),
    (f"{TOTAL} USING SAMPLE 50% WHERE {JUNE}", "every row asked for"),
]


@pytest.mark.parametrize("mode", ["schema", "certified"])
@pytest.mark.parametrize("sql, says", NOT_FIGURES)
def test_a_figure_that_is_not_an_aggregate_of_a_column_is_refused(eng, mode, sql, says):
    """A rounding to a negative or computed precision changes the figure (13,287.67 reported as 13,000); the right
    aggregate over rows the query multiplied, narrowed on an undeclared key or sampled each passed the gate in both
    arms before."""
    allow = (schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS) if mode == "schema"
             else build_allowlist(eng.g, "retail", REVENUE, RESTRICTED_COLUMNS))
    if "unnest([1, 2, 3])" in sql:
        assert gate(eng.w.con["retail"], sql, allow, figure=False)["passed"]
    g = gate(eng.w.con["retail"], sql, allow, figure=True)
    assert not g["passed"] and g["failed_at"] == "static" and "figure" in g["checks"], sql
    assert any(says in p for p in g["problems"]), g["problems"]
    assert "measured" not in g


@pytest.mark.parametrize("sql, certified, measured", [
    (f"SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue WHERE {JUNE}", REVENUE,
     ["monthly_revenue.total_revenue_usd"]),
    ("SELECT sum(r.total_revenue_usd) AS total FROM monthly_revenue r WHERE r.month_start = DATE '2025-06-01'", REVENUE,
     ["monthly_revenue.total_revenue_usd"]),
    (f"SELECT round(sum(monthly_revenue.total_revenue_usd)) FROM monthly_revenue WHERE {JUNE}", REVENUE,
     ["monthly_revenue.total_revenue_usd"]),
    ("SELECT count(*) FROM customer_activity WHERE customer_activity.is_active_flag AND customer_activity.month_start = DATE '2025-06-01'",
     [("customer_activity", "is_active_flag")], ["customer_activity.is_active_flag"]),
    ("SELECT count(*) FROM complaint WHERE complaint.resolved_on IS NULL", [("complaint", "complaint_id"), ("complaint", "resolved_on")],
     ["complaint.resolved_on"]),
    ("SELECT count(DISTINCT customer.customer_id) FROM customer", [("customer", "customer_id")], ["customer.customer_id"]),
    ("SELECT round(avg(daily_balance.ledger_balance), 2) FROM daily_balance", [("daily_balance", "ledger_balance")],
     ["daily_balance.ledger_balance"]),
])
def test_a_figure_names_the_column_it_measures(eng, sql, certified, measured):
    for allow in (schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS), build_allowlist(eng.g, "retail", certified, RESTRICTED_COLUMNS)):
        g = gate(eng.w.con["retail"], sql, allow, figure=True)
        assert g["passed"] and g["measured"] == measured, (sql, g["problems"])


def test_the_certified_allowlist_measures_only_what_is_certified(eng):
    """The period column and the join keys are on the allowlist, but a figure does not measure them."""
    allow = build_allowlist(eng.g, "retail", REVENUE, RESTRICTED_COLUMNS)
    g = gate(eng.w.con["retail"], "SELECT count(monthly_revenue.account_id) FROM monthly_revenue", allow, figure=True)
    assert not g["passed"] and any("monthly_revenue.account_id is not a measure" in p for p in g["problems"])
    assert gate(eng.w.con["retail"], "SELECT count(monthly_revenue.account_id) FROM monthly_revenue",
                schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS), figure=True)["passed"]


def test_the_figure_check_is_the_agents_and_the_presets_gate_is_unchanged(eng):
    allow = schema_allowlist(eng.g, "retail", RESTRICTED_COLUMNS)
    g = gate(eng.w.con["retail"], "SELECT 13287.67", allow)
    assert g["passed"] and "figure" not in g["checks"] and "measured" not in g


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_eval_runs_as_the_caller(client):
    rows = {r["id"]: r for r in client.post("/eval", headers=client.as_("analyst_retail")).json()["graph"]["rows"]}
    assert rows["G001"]["ok"]
    assert not rows["G012"]["ok"]
    assert "may not read sme" in rows["G012"]["why"]


def test_sql_check_validates_without_executing(client):
    who = client.as_("analyst_retail")
    good = client.post("/sql/check", json={"query": "SELECT count(*) FROM monthly_revenue"}, headers=who)
    assert good.status_code == 200 and good.json()["ok"]
    assert good.json()["tables"] == ["monthly_revenue"] and good.json()["columns"] == []

    planned = client.post("/sql/check", json={"query": "SELECT error('ran') FROM monthly_revenue"}, headers=who)
    assert planned.status_code == 200 and planned.json()["ok"], "planned, not run: a run would raise 'ran'"

    restricted = client.post("/sql/check", json={"query": "SELECT email FROM customer"}, headers=who).json()
    assert not restricted["ok"] and "restricted personal data" in " ".join(restricted["reasons"])

    dropped = client.post("/sql/check", json={"query": "DROP TABLE customer"}, headers=who).json()
    assert not dropped["ok"] and any("read-only" in reason for reason in dropped["reasons"])

    for bad in ("SELECT (((", "SELECT 'unterminated FROM customer", "", ";", "\x00\x01"):
        garbage = client.post("/sql/check", json={"query": bad}, headers=who)
        assert garbage.status_code == 200 and not garbage.json()["ok"] and garbage.json()["reasons"], bad

    unknown = client.post("/sql/check", json={"query": "SELECT count(*) FROM no_such_table"}, headers=who).json()
    assert not unknown["ok"] and unknown["reasons"] == ["not available: no_such_table is in no warehouse analyst_retail may read"], unknown
    denied = client.post("/sql/check", json={"query": "SELECT count(*) FROM flow"}, headers=who).json()
    assert not denied["ok"] and denied["reasons"] == ["not available: flow is in no warehouse analyst_retail may read"], denied
    assert [r.split(": ", 1)[1].replace("flow", "T") for r in denied["reasons"]] == [r.split(": ", 1)[1].replace("no_such_table", "T") for r in unknown["reasons"]], \
        "an unknown table and one the principal may not read are answered alike"
    parse_error = client.post("/sql/check", json={"query": "SELEC x FROM"}, headers=who).json()
    assert parse_error["reasons"] == ["does not parse: this is not a query the SQL check can read (line 1, column 12)"], parse_error

    two = client.post("/sql/check", json={"query": "SELECT 1; SELECT 2"}, headers=who).json()
    assert not two["ok"] and any("one statement" in reason for reason in two["reasons"])

    cte = client.post("/sql/check", json={"query": "WITH m AS (SELECT count(*) AS n FROM monthly_revenue) SELECT n FROM m"},
                      headers=who).json()
    assert cte["ok"] and cte["tables"] == ["monthly_revenue"], cte

    assert client.post("/sql/check", json={"query": "SELECT 1"}).status_code == 401
    assert client.post("/sql/check", json={"query": "SELECT 1", "principal": "analyst_enterprise"},
                       headers=who).status_code == 422


def test_sql_check_reads_a_table_named_by_its_unit_in_that_units_warehouse(client):
    """<unit>.<table> where the principal may read the unit is checked as the bare table is, restricted columns and all;
    an unreadable unit, an unknown unit and an unknown table are answered alike, as an unknown table is. Never run."""
    def check(who, query):
        return client.post("/sql/check", json={"query": query}, headers=client.as_(who)).json()

    readable = check("analyst_retail", "SELECT count(*) FROM retail.monthly_revenue")
    assert readable == check("analyst_retail", "SELECT count(*) FROM monthly_revenue") and readable["ok"], readable
    assert check("analyst_retail", "SELECT error('ran') FROM retail.monthly_revenue")["ok"], "planned, not run"

    for query in ("SELECT email FROM retail.customer", "SELECT retail.customer.email FROM retail.customer"):
        restricted = check("analyst_enterprise", query)
        assert not restricted["ok"] and restricted["reasons"] == ["column customer.email is restricted personal data (POL-DATA-CLASS)"], restricted

    answers = {query: check("analyst_retail", query)["reasons"] for query in (
        "SELECT count(*) FROM wealth.TRAN_FACT_1", "SELECT count(*) FROM retail.no_such_table", "SELECT count(*) FROM nowhere.monthly_revenue")}
    assert answers == {"SELECT count(*) FROM wealth.TRAN_FACT_1": ["not available: wealth.TRAN_FACT_1 is in no warehouse analyst_retail may read"],
                       "SELECT count(*) FROM retail.no_such_table": ["not available: retail.no_such_table is in no warehouse analyst_retail may read"],
                       "SELECT count(*) FROM nowhere.monthly_revenue": ["not available: nowhere.monthly_revenue is in no warehouse analyst_retail may read"]}
    assert len({r[0].split(" is ", 1)[1] for r in answers.values()}) == 1, "an unreadable unit, an unknown table and an unknown unit alike"

    two = check("analyst_enterprise", "SELECT count(*) FROM retail.monthly_revenue CROSS JOIN wealth.TRAN_FACT_1")
    assert not two["ok"] and two["reasons"] == ["the query's tables are not in one warehouse this principal may read: a query reads one warehouse"]


def test_sql_check_names_an_unknown_column_plainly_and_alike_wherever_it_exists(client):
    """A name the warehouse does not resolve is an unknown column, with its line and column, never the resolver's own text;
    freeze_date, a column only of Wealth's month_end, which analyst_retail may not read, is as unknown as one no table has."""
    def reasons(query):
        return client.post("/sql/check", json={"query": query}, headers=client.as_("analyst_retail")).json()["reasons"]

    assert reasons("SELECT month_end FROM monthly_revenue") == ["unknown column month_end (line 1, column 16)"]
    assert reasons("SELECT freeze_date FROM monthly_revenue") == ["unknown column freeze_date (line 1, column 18)"]
    assert reasons("SELECT no_such_col FROM monthly_revenue") == ["unknown column no_such_col (line 1, column 18)"]
    said = " ".join(reasons("SELECT freeze_date FROM monthly_revenue") + reasons("SELECT m.nope FROM monthly_revenue m"))
    assert "could not be resolved" not in said and "Line:" not in said and "warehouse" not in said, said
