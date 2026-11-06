"""A GoogleSQL reserved keyword used as a name without backticks is refused by BigQuery (#208: the access audit's `at`),
though sqlglot reads it as a name and writes it back quoted: the stand-in (tests/bigquery_fake.py) refuses it on the text
as sent, and the app state's access statements are sent with every such name quoted."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlglot

from kge.adapters import appstate
from kge.adapters.appstate import bigquery
from tests import bigquery_fake

ADMIN = "admin@example.com"
PERSON = {"username": "reader@example.com", "name": "Reader", "roles": ["reader"]}


@pytest.mark.parametrize("sql", [
    "CREATE TABLE IF NOT EXISTS `p.d.access_audit` (sequence INT64, at TIMESTAMP, `by` STRING)",
    "INSERT `p.d.access_audit` (sequence, at, `by`) VALUES (@sequence, TIMESTAMP(@at), @by)",
    "SELECT sequence, CAST(at AS STRING) AS at, `by` FROM `p.d.access_audit`",
    "SELECT * FROM `p.d.role_audit` ORDER BY at DESC, id DESC",
    "SELECT at.x FROM `p.d.t`",
    "SELECT t.at AS at FROM `p.d.t` t",
    "SELECT t.at, at FROM `p.d.t` t",
])
def test_an_unquoted_reserved_keyword_is_refused_as_bigquery_refuses_it(sql):
    with pytest.raises(RuntimeError, match="Unexpected keyword"):
        bigquery_fake.refuse_reserved(sqlglot.parse_one(sql, read="bigquery"))


@pytest.mark.parametrize("sql", [
    "CREATE TABLE IF NOT EXISTS `p.d.access_audit` (sequence INT64, `at` TIMESTAMP, `by` STRING, updated_at TIMESTAMP)",
    "INSERT `p.d.access_audit` (sequence, `at`, `by`) VALUES (@sequence, TIMESTAMP(@at), @by)",
    "SELECT sequence, CAST(`at` AS STRING) AS `at`, CAST(NULL AS STRING) AS name FROM `p.d.access_audit` WHERE TRUE",
    "SELECT a.end FROM `p.d.t` a",
    "SELECT t.at, t.x.at, t.x.y.at, t.x.y.z.at FROM `p.d.t` t",
    "SELECT t . at FROM `p.d.t` t ORDER BY t.at",
    "SELECT `at`.x FROM `p.d.t` AS `at`",
])
def test_quoted_reserved_names_parameters_and_later_path_segments_pass(sql):
    bigquery_fake.refuse_reserved(sqlglot.parse_one(sql, read="bigquery"))


def test_every_access_statement_the_app_state_sends_quotes_its_reserved_names(monkeypatch, tmp_path):
    """Creating the tables, the migration, a grant, a removal, the grants and the audit, each as sent to BigQuery."""
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    monkeypatch.setenv("APP_STATE_BACKEND", "bigquery")
    monkeypatch.delenv("STORE_URL", raising=False)
    fake, sent = bigquery_fake.FakeBigQuery(), []
    monkeypatch.setattr(bigquery, "_ENSURED", set())
    monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: sent.append(body["query"]) or fake.state_post(body))
    state = appstate.access_state(SimpleNamespace(client="sample", store=tmp_path / "store"))
    state.access_migrate([PERSON], by=ADMIN)
    state.access_put({**PERSON, "roles": ["analyst"]}, by=ADMIN, via="api")
    state.access_delete(PERSON["username"], by=ADMIN, via="api")
    assert state.access_grants() == []
    assert [entry["sequence"] for entry in state.access_audit()] == [3, 2, 1]
    assert any("access_audit`\n    (sequence INT64, `at` TIMESTAMP" in sql for sql in sent)
    statements = [s for sql in sent for s in sqlglot.parse(sql.replace("ASSERT ", "SELECT "), read="bigquery") if s]
    assert len(statements) > 10
    for statement in statements:
        bigquery_fake.refuse_reserved(statement)
