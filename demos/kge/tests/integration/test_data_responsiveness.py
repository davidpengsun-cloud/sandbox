import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from kge.services.data import service


def test_heavy_person_query_leaves_other_governed_reads_responsive(eng):
    sql = ("WITH source AS (SELECT customer_id FROM customer) "
           "SELECT sum(hash(a.customer_id, b.customer_id, c.customer_id, d.customer_id, e.customer_id)) "
           "FROM source a CROSS JOIN source b CROSS JOIN source c CROSS JOIN source d CROSS JOIN source e")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        heavy = pool.submit(service.query, eng, "analyst_retail", "retail", sql)
        time.sleep(0.1)
        assert not heavy.done()
        reading = time.monotonic()
        result = service.preview(eng, "analyst_enterprise", "retail", "customer", 1)
        latency = time.monotonic() - reading
        assert result.row_count == 1
        assert latency < 1
        with pytest.raises(service.DataRefused, match="5 second time limit"):
            heavy.result()
    elapsed = time.monotonic() - started
    print(f"governed query stopped in {elapsed:.3f}s; concurrent preview in {latency:.3f}s")
    assert 4.5 < elapsed < 6
    assert service.preview(eng, "analyst_enterprise", "retail", "customer", 1).row_count == 1


def test_attack_or_and_union_are_refused_without_slowing_other_people(eng):
    attacks = ["SELECT customer_id FROM customer WHERE " + " OR ".join(f"customer_id = {i}" for i in range(30000)),
               " UNION ALL ".join("SELECT 1" for _ in range(3000))]
    def refused(sql):
        started = time.monotonic()
        with pytest.raises(service.DataRefused, match="complexity limit") as error:
            service.query(eng, "analyst_retail", "retail", sql)
        assert error.value.status == 400
        return time.monotonic() - started
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(refused, sql) for sql in attacks]
        started = time.monotonic()
        result = service.query(eng, "analyst_enterprise", "retail", "SELECT count(*) AS n FROM customer")
        elapsed = time.monotonic() - started
        assert result.rows == [{"n": 200}]
        assert elapsed < 1
        assert all(job.result() < 1 for job in jobs)
    print(f"OR and UNION refused under 1s; concurrent ordinary query {elapsed:.3f}s")


@pytest.mark.parametrize("sql", [
    "SELECT count(*) AS n FROM TRAN_FACT_1 f JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID",
    "SELECT count(*) AS n FROM ADV_DIM_1 v JOIN BRCH_DIM_1 o ON v.BRCH_CD = o.BRCH_CD",
    "SELECT count(*) AS n FROM CLNT_SEGMT_DIM_1 c JOIN CLNT_SEGMT_DIM_1 p ON c.PARNT_SEGMT_CD = p.SEGMT_CD",
    "SELECT count(*) AS n FROM TRAN_FACT_1 f, MONTH_DIM_1 m WHERE last_day(f.DW_TRAN_DT) = m.CLDR_EOM_DT",
])
def test_run_a_query_accepts_the_packs_declared_joins(eng, sql):
    assert service.query(eng, "advisor_wealth", "wealth", sql).rows[0]["n"] > 0


@pytest.mark.parametrize("unit,who,sql", [
    ("retail", "analyst_retail", "SELECT count(*) FROM account a LEFT JOIN customer c ON a.customer_id = c.customer_id"),
    ("wealth", "advisor_wealth", "SELECT count(*) FROM ACCT_DIM2_1 a LEFT JOIN HSHLD_DIM_1 h ON a.HSHLD_DK = h.HSHLD_DK"),
])
def test_left_join_has_the_same_explained_refusal_in_every_unit(eng, unit, who, sql):
    with pytest.raises(service.DataRefused, match="LEFT join.*inner joins only") as error:
        service.query(eng, who, unit, sql)
    assert error.value.status == 400


def test_rules_answer_names_the_pack_version_basis_without_changing_the_figure(eng):
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.service import ask
    combined = Combined(eng)
    for question, figure, basis in [
        ("NNA for March 2025 as reported", 5868354.54, "as reported at the month's freeze date"),
        ("net new assets for March 2025 restated", 5920237.28, "restated using current row versions"),
        ("net new assets for March 2025 using current versions", 5920237.28, "restated using current row versions"),
        ("net new assets for March 2025 using the current version", 5920237.28, "restated using current row versions"),
    ]:
        answer = ask(combined, eng.profile, question, "advisor_wealth", "analysis", "graph", combined.default_config())
        assert answer["behaviour"] == "figure"
        assert answer["figure"] == pytest.approx(figure, abs=0.005)
        assert basis in answer["answer_text"]


def test_plain_rules_answer_keeps_its_original_wording(eng):
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.service import ask
    combined = Combined(eng)
    answer = ask(combined, eng.profile, "net new assets for March 2025", "advisor_wealth", "analysis", "graph", combined.default_config())
    assert answer["figure"] == pytest.approx(5920237.28, abs=0.005)
    assert "basis:" not in answer["answer_text"]


def test_http_transaction_export_discloses_the_actual_attack_cap(eng, monkeypatch):
    import csv
    import io
    from kge.services.data import routes
    monkeypatch.setenv("DATA_EXPORT_ROW_CAP", "10000")
    monkeypatch.setattr(routes.served, "engine", lambda: eng)
    available = service.query(eng, "analyst_retail", "retail", "SELECT count(*) AS n FROM transaction").rows[0]["n"]
    response = routes.export(routes.ProductExport(unit="retail", table="transaction"), "analyst_retail")
    rows = list(csv.DictReader(io.StringIO(response.body.decode())))
    assert available > len(rows) == 10000
    assert response.headers["x-export-capped-at"] == "10000"
    assert "incomplete" in response.headers["x-export-note"]


@pytest.mark.parametrize("kind", ["OR", "UNION"])
@pytest.mark.parametrize("nested", [0, 3])
def test_128_terms_run_through_the_data_service(eng, kind, nested):
    sql = ("SELECT customer_id FROM customer WHERE " + " OR ".join("customer_id = customer_id" for _ in range(128))
           if kind == "OR" else " UNION ALL ".join("SELECT 1 AS n" for _ in range(128)))
    for _ in range(nested):
        sql = f"SELECT count(*) AS n FROM ({sql}) nested"
    assert service.query(eng, "analyst_retail", "retail", sql).row_count > 0


def test_complete_customer_export_has_no_cap_warning(eng, monkeypatch):
    from kge.services.data import routes
    monkeypatch.setenv("DATA_EXPORT_ROW_CAP", "10000")
    monkeypatch.setattr(routes.served, "engine", lambda: eng)
    response = routes.export(routes.ProductExport(unit="retail", table="customer"), "analyst_retail")
    assert "x-export-capped-at" not in response.headers
    assert "x-export-note" not in response.headers


def test_even_a_declared_function_is_refused_in_caller_on(eng):
    with pytest.raises(service.DataRefused, match="conjunction .* of column equalities"):
        service.query(eng, "advisor_wealth", "wealth",
                      "SELECT count(*) FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON last_day(f.DW_TRAN_DT) = m.CLDR_EOM_DT")
