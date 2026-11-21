"""Each warehouse advances only when its own schema or CSV changes."""
import json
import shutil

import yaml

from kge.estate import from_files
from kge.pipeline import warehouse_versions
from tests.unit.test_fixture_rebuild import built as built, rebuild, warehouse_bytes


def two_units(fx):
    schema = yaml.safe_load((fx / "schema.yaml").read_text())
    schema["warehouses"]["other"] = schema["warehouses"]["shop"]
    (fx / "schema.yaml").write_text(yaml.safe_dump(schema))
    shutil.copytree(fx / "data" / "shop", fx / "data" / "other")


def test_only_changed_warehouse_advances_and_preserves_rows(built):
    prof, fx, fake = built
    two_units(fx)
    rebuild(prof, fx)
    before = json.loads((prof.estate() / "summary.json").read_text())
    untouched = warehouse_bytes(fake, "other", 1) if fake else None
    csv = fx / "data" / "shop" / "sale.csv"
    csv.write_text(csv.read_text().replace("1.50", "9.50"))
    result = rebuild(prof, fx)
    after = json.loads((prof.estate() / "summary.json").read_text())
    assert after["rows"]["other"] == before["rows"]["other"]
    assert after["warehouse_inputs"]["other"] == before["warehouse_inputs"]["other"]
    assert after["warehouse_inputs"]["shop"] != before["warehouse_inputs"]["shop"]
    assert result["version"] is None
    if fake:
        assert warehouse_versions.estate_live(prof) == {"other": 1, "shop": 2}
        assert warehouse_bytes(fake, "other", 1) == untouched
        assert not any(name.startswith("other_v2_") for name in fake.held())


def test_document_and_question_changes_keep_warehouse_versions(built):
    prof, fx, fake = built
    rebuild(prof, fx)
    before = json.loads((prof.estate() / "summary.json").read_text())
    loads = len(fake.loads) if fake else 0
    note = fx / "docs" / "uncertified" / "EM-01.md"
    note.write_text(note.read_text() + "\nAnother detail.\n")
    (fx / "golden.json").write_text("[]")
    result = rebuild(prof, fx)
    after = json.loads((prof.estate() / "summary.json").read_text())
    assert result["rebuilt"]
    assert after["warehouse_inputs"] == before["warehouse_inputs"]
    assert after["warehouse_versions"] == before["warehouse_versions"]
    assert after["rows"] == before["rows"]
    assert not fake or len(fake.loads) == loads


def test_legacy_unchanged_digest_bootstraps_without_rewriting(built):
    prof, fx, fake = built
    rebuild(prof, fx)
    path = prof.estate() / "summary.json"
    summary = json.loads(path.read_text())
    del summary["warehouse_inputs"]
    del summary["warehouse_versions"]
    path.write_text(json.dumps(summary))
    loads = len(fake.loads) if fake else 0
    assert not rebuild(prof, fx)["rebuilt"]
    restored = json.loads(path.read_text())
    assert restored["warehouse_inputs"] == from_files.warehouse_inputs(fx, from_files.read_schema(fx))
    assert restored["warehouse_versions"] == ({"shop": 1} if fake else {})
    assert not fake or len(fake.loads) == loads


def test_legacy_changed_digest_conservatively_rebuilds_all_units(built):
    prof, fx, fake = built
    two_units(fx)
    rebuild(prof, fx)
    path = prof.estate() / "summary.json"
    summary = json.loads(path.read_text())
    del summary["warehouse_inputs"]
    del summary["warehouse_versions"]
    path.write_text(json.dumps(summary))
    note = fx / "docs" / "uncertified" / "EM-01.md"
    note.write_text(note.read_text() + "\nChanged before the digest upgrade.\n")
    rebuild(prof, fx)
    assert warehouse_versions.estate_live(prof) == ({"other": 2, "shop": 2} if fake else {})


def test_staging_preflight_precedes_warehouse_writes(built, monkeypatch):
    prof, fx, fake = built

    def refuse(*args):
        raise ValueError("TMP_URL is required")

    monkeypatch.setattr(from_files, "preflight_loads", refuse)
    import pytest
    with pytest.raises(ValueError, match="TMP_URL"):
        rebuild(prof, fx)
    assert not prof.estate().exists()
    assert not fake or not fake.loads


def test_catalogue_aborted_transaction_retries_without_duplicate_rows(built, monkeypatch):
    prof, fx, fake = built
    if fake is None:
        return
    from kge.adapters.warehouse import bigquery_estate
    monkeypatch.setattr(bigquery_estate.time, "sleep", lambda _: None)
    fake.catalogue_aborts = 1
    rebuild(prof, fx)
    assert fake.catalogue_aborts == 0
    assert len(fake.rows("_kge_catalogue")) == 1
    assert len(fake.rows("shop_v1_sale")) == 2


def test_large_catalogue_schema_is_refused_without_credentials():
    from types import SimpleNamespace
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
    estate = BigQueryEstate("shop", loader=SimpleNamespace(dataset_prefix="test"))
    tables = [{"name": f"table_{i}", "columns": [{"name": "id", "type": "INTEGER", "description": "x" * 1024}],
               "primary_key": ["id"]} for i in range(900)]
    refusal = estate.refuses_schema("shop", from_files.warehouse_statements(tables))
    assert "SQL byte budget" in refusal
    assert not estate._ready


def test_publish_failure_cleans_only_new_versions(built, monkeypatch):
    prof, fx, fake = built
    if fake is None:
        return
    from kge.adapters.warehouse import estate_of
    import pytest
    two_units(fx)
    rebuild(prof, fx)
    before = (prof.estate() / "summary.json").read_bytes()
    for unit in ("shop", "other"):
        path = fx / "data" / unit / "sale.csv"
        path.write_text(path.read_text().replace("1.50", "9.50"))
    estate = estate_of(prof)
    publish = estate.publish
    calls = []
    def fail_second(unit, version, inputs):
        calls.append(unit)
        if len(calls) == 2:
            raise RuntimeError("catalogue refused")
        publish(unit, version, inputs)
    monkeypatch.setattr(estate, "publish", fail_second)
    with pytest.raises(RuntimeError, match="catalogue refused"):
        rebuild(prof, fx, estate=estate)
    assert (prof.estate() / "summary.json").read_bytes() == before
    assert estate.versions("shop") == [1]
    assert estate.versions("other") == [1]
    assert not any("_v2_" in name for name in fake.held())


def test_partial_ddl_failure_removes_child_before_changed_schema_retry(built, monkeypatch):
    prof, fx, fake = built
    if fake is None:
        return
    import pytest
    from kge.adapters.warehouse import estate_of
    schema_path = fx / "schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    schema["warehouses"]["shop"]["tables"].append(
        {"name": "last", "columns": [{"name": "id", "type": "INTEGER", "description": "ID"}], "primary_key": ["id"]})
    schema_path.write_text(yaml.safe_dump(schema))
    (fx / "data" / "shop" / "last.csv").write_text("id\n1\n")
    estate = estate_of(prof)
    run = estate.loader.run
    def fail_last(sql, *args, **kwargs):
        if sql.startswith("CREATE TABLE") and "shop_v1_last`" in sql:
            raise RuntimeError("third DDL refused")
        return run(sql, *args, **kwargs)
    monkeypatch.setattr(estate.loader, "run", fail_last)
    with pytest.raises(RuntimeError, match="third DDL refused"):
        rebuild(prof, fx, estate=estate)
    assert not any(name.startswith("shop_v1_") for name in fake.held())
    assert not (prof.estate() / "summary.json").exists()
    assert estate.staged == {}
    schema["warehouses"]["shop"]["tables"] = schema["warehouses"]["shop"]["tables"][:1]
    schema_path.write_text(yaml.safe_dump(schema))
    monkeypatch.setattr(estate.loader, "run", run)
    rebuild(prof, fx, estate=estate)
    assert estate.versions("shop") == [1]
    assert fake.rows("shop_v1_item") == [(1, "Tea"), (2, None)]


def test_local_publish_failure_invalidates_only_replaced_warehouse_inputs(built, monkeypatch):
    if built[2] is not None:
        return
    import pytest
    from kge.adapters.warehouse import estate_of
    prof, fx, _ = built
    two_units(fx)
    rebuild(prof, fx)
    summary = prof.estate() / "summary.json"
    before = json.loads(summary.read_text())
    csv = fx / "data/shop/sale.csv"
    released = csv.read_bytes()
    csv.write_text(csv.read_text().replace("1.50", "9.50"))
    estate = estate_of(prof)
    publish = estate.publish

    def fail_after_publish(*args):
        publish(*args)
        raise RuntimeError("interrupted after publishing local rows")

    monkeypatch.setattr(estate, "publish", fail_after_publish)
    with pytest.raises(RuntimeError, match="interrupted after publishing"):
        rebuild(prof, fx, estate=estate)
    failed = json.loads(summary.read_text())
    assert "fixture_sha256" not in failed
    assert failed["warehouse_inputs"] == {"other": before["warehouse_inputs"]["other"]}
    csv.write_bytes(released)
    monkeypatch.setattr(estate, "publish", publish)
    staged = []
    stage = estate.stage

    def record_stage(unit, *args):
        staged.append(unit)
        return stage(unit, *args)

    monkeypatch.setattr(estate, "stage", record_stage)
    assert rebuild(prof, fx, estate=estate)["rebuilt"]
    assert staged == ["shop"]
    with estate.open("shop") as session:
        assert float(session.execute("SELECT sum(amount) FROM sale").fetchone()[0]) == 3.5
