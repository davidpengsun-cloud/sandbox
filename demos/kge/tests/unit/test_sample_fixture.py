"""The committed sample is derived, and its fixture round trip preserves content."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter

import pytest
import yaml

from kge.adapters.warehouse import open_estate
from kge.compiler import lock
from kge.compiler.inputs import read_declared
from kge.estate import certify, from_files, sample_fixture
from kge.pipeline.settings import ROOT


def contents(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def assert_current(actual, expected):
    held, fresh = contents(actual), contents(expected)
    changed = sorted(name for name in held.keys() | fresh.keys() if held.get(name) != fresh.get(name))
    assert not changed, f"run make sample-fixture: generated fixture differs: {changed}"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("sample-roundtrip")
    estate = root / "generated"
    done = subprocess.run([sys.executable, "-m", "kge.estate.generate", "--out", str(estate)], cwd=ROOT,
                          env={**os.environ, "PROFILE": "meridian", "PROFILES": str(ROOT / "deploy/profiles.yaml")},
                          capture_output=True, text=True, timeout=1200)
    assert done.returncode == 0, done.stdout + done.stderr
    fixture = root / "fixture"
    sample_fixture.write_fixture(estate, fixture)
    return root, estate, fixture


def test_committed_fixture_has_no_generator_drift(generated):
    assert_current(sample_fixture.SAMPLE / "fixture", generated[2])


def test_one_changed_generated_value_fails_the_drift_check(generated, tmp_path):
    """Mutate a generated row, then export it without changing the committed fixture."""
    _, estate, _ = generated
    warehouses = open_estate(estate / "warehouses", "meridian")
    session = warehouses.open("retail", write=True)
    try:
        session.execute("UPDATE branch SET branch_name = branch_name || ' changed' WHERE branch_id = 1")
    finally:
        session.close()
    try:
        sample_fixture.write_fixture(estate, tmp_path / "changed")
        with pytest.raises(AssertionError, match="data/retail/branch.csv"):
            assert_current(sample_fixture.SAMPLE / "fixture", tmp_path / "changed")
    finally:
        session = warehouses.open("retail", write=True)
        try:
            session.execute("UPDATE branch SET branch_name = replace(branch_name, ' changed', '') WHERE branch_id = 1")
        finally:
            session.close()


def test_exact_round_trip_catalogue_rows_documents_chunks_certification_and_sets(generated):
    root, generated_estate, fixture = generated
    rebuilt = root / "rebuilt"
    declared = read_declared(sample_fixture.SAMPLE)
    assert declared.fixture == "fixture" and declared.sets == sample_fixture.SETS
    from_files.build(fixture, rebuilt, "meridian", sets=declared.sets)
    original = open_estate(generated_estate / "warehouses", "meridian")
    restored = open_estate(rebuilt / "warehouses", "meridian")
    schema = from_files.read_schema(fixture)
    total = 0
    for unit, house in schema.items():
        assert certify.catalogue_of(restored, unit) == certify.catalogue_of(original, unit)
        seen = set()
        left, right = original.open(unit), restored.open(unit)
        try:
            for table in house["tables"]:
                assert all(key["ref_table"] in seen for key in table["foreign_keys"])
                seen.add(table["name"])
                columns = ", ".join(c["name"] for c in table["columns"])
                sql = f"SELECT {columns} FROM {table['name']}"
                expected = Counter(left.execute(sql).fetchall())
                assert Counter(right.execute(sql).fetchall()) == expected, f"{unit}.{table['name']}"
                total += expected.total()
        finally:
            left.close()
            right.close()
    assert total == 55470
    def docs(folder):
        return {d["doc_id"]: {k: v for k, v in d.items() if v is not None}
                for d in json.loads((folder / "documents.json").read_text())}
    assert docs(rebuilt) == docs(generated_estate)
    assert contents(rebuilt / "docs") == contents(generated_estate / "docs")
    for doc in docs(generated_estate).values():
        assert certify.chunks(rebuilt, doc) == certify.chunks(generated_estate, doc)
    for name in declared.sets:
        assert (rebuilt / f"{name}.json").read_bytes() == (generated_estate / f"{name}.json").read_bytes()
    mailboxes = certify.mailboxes(sample_fixture.SAMPLE / "pack.yaml")
    landed = [certify.land(folder, "meridian", role_mailboxes=mailboxes) for folder in (generated_estate, rebuilt)]
    assert landed[0][0].id == landed[1][0].id
    assert contents(landed[0][0].path) == contents(landed[1][0].path)
    def by_asset(rows):
        return sorted(rows, key=lambda row: row["asset_id"])
    assert by_asset(landed[0][1]) == by_asset(landed[1][1])
    sources = lock.harvested(sample_fixture.SAMPLE.parent, "meridian")
    for release, _, _ in landed:
        assert lock.render(lock.freeze(release, sources)) == (sample_fixture.SAMPLE / "pack.lock").read_text()
    for filename, key in (("documents/index.json", "id"), ("documents/chunks.jsonl", "id")):
        raw = [release.text(filename) for release, _, _ in landed]
        values = [json.loads(value) if filename.endswith(".json") else [json.loads(line) for line in value.splitlines()]
                  for value in raw]
        assert sorted(values[0], key=lambda value: value[key]) == sorted(values[1], key=lambda value: value[key])


def test_the_generator_records_its_seed_in_the_summary(generated):
    from kge.eval.harness import estate
    assert json.loads((generated[1] / "summary.json").read_text())["seed"] == 7
    assert estate(generated[1]) == {"seed": 7, "generator": "kge/estate/generate.py"}


def test_sample_export_refuses_another_profile(tmp_path):
    done = subprocess.run([sys.executable, "-m", "kge.estate.sample_fixture", "--profile", "thornbury",
                           "--out", str(tmp_path / "fixture")], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert done.returncode == 2 and "invalid choice" in done.stderr
    assert not (tmp_path / "fixture").exists()


def test_fixture_schema_and_rows_pass_bigquery_types():
    """Validate every cell with the pilot adapter's casts, without a cloud call."""
    from kge.adapters.warehouse.types import checked, declared, orphans, refuses_type
    schema = yaml.safe_load((sample_fixture.SAMPLE / "fixture/schema.yaml").read_text())
    for unit, house in schema["warehouses"].items():
        tables = declared(from_files.warehouse_statements(house["tables"]))
        held = {}
        for table in tables:
            assert all(refuses_type(c["type"]) is None for c in table["columns"])
            with (sample_fixture.SAMPLE / "fixture/data" / unit / f"{table['name']}.csv").open(newline="") as stream:
                rows = list(csv.reader(stream))[1:]
            held[table["name"]] = checked(table, [c["name"] for c in table["columns"]],
                                          [[v if v else None for v in row] for row in rows], held)
        orphans(tables, held)
