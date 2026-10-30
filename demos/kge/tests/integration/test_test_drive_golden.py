"""The test drive's golden check (#101 §3.7) on the built example: the golden set through Engine.serving on the graph
preset, scored by the golden scorer, every item passing; one item the graph cannot answer as expected fails it.

    .venv/bin/python -m pytest tests/integration/test_test_drive_golden.py -q

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import copy

import pytest

from kge.eval import sets
from kge.services.onboard import test_drive_run


def test_the_golden_check_passes_on_the_example():
    report = {}
    item = test_drive_run.golden_check(report)
    assert item.result == "passed", item.detail
    gold = report["golden"]
    assert gold["of"] > 0 and gold["passed"] == gold["of"] and gold["failures"] == []
    assert gold["held_out"]["of"] > 0


def test_one_failing_graph_item_fails_the_golden_check(monkeypatch):
    real, wrong = sets.items, []

    def wrong_figure(path):
        items = real(path)
        if path.name != "golden.json":
            return items
        items = copy.deepcopy(items)
        target = next(i for i in items if i["expect"].get("figure"))
        target["expect"]["figure"] = str(float(target["expect"]["figure"]) + 1000)
        wrong.append(target["id"])
        return items

    monkeypatch.setattr(test_drive_run.sets, "items", wrong_figure)
    report = {}
    item = test_drive_run.golden_check(report)
    assert item.result == "failed" and item.what_to_do
    assert [f["id"] for f in report["golden"]["failures"]] == wrong
    assert wrong[0] in item.detail


def test_real_catalogue_and_data_checks_pass_on_the_built_example(tmp_path, monkeypatch):
    from tests.scratch import DEMO
    example = tmp_path / "meridian/clients/meridian"
    example.mkdir(parents=True)
    for name in ("pack.yaml", "pack.lock"):
        (example / name).write_bytes((DEMO / "clients/meridian" / name).read_bytes())
    monkeypatch.setenv("FROM", str(tmp_path))
    assert "retail" in test_drive_run.catalogue_check()
    assert "preview and query returned rows" in test_drive_run.data_check()


def test_every_product_default_query_uses_its_permitted_columns_and_passes():
    from kge.pipeline.profile import resolve
    from kge.services.ask import Engine
    from kge.services.catalogue import get
    from kge.services.data import preview, query
    from kge.services.data.engine.service import DataRefused
    profile = resolve("meridian")
    engine = Engine.serving(profile)
    try:
        products = get(profile, list(engine.g.readable_units("analyst_enterprise"))).products
        assert len(products) == 59
        for product in products:
            sample = preview(engine, "analyst_enterprise", product.unit_id, product.table, 1)
            restricted = {column for unit, table, column in engine.RESTRICTED_COLUMNS
                          if (unit, table) == (product.unit_id, product.table)}
            assert not restricted.intersection(sample.columns), product.id
            sql = f"SELECT {', '.join(f'{product.table}.{name}' for name in sample.columns)} FROM {product.table} LIMIT 20"
            answer = query(engine, "analyst_enterprise", product.unit_id, sql)
            assert answer.columns == sample.columns
            assert answer.row_count <= 20
        with pytest.raises(DataRefused):
            query(engine, "analyst_enterprise", "wealth", "SELECT acct_nm FROM account")
    finally:
        engine.close()
