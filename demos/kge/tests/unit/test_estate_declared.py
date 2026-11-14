"""A fixture replaces estate inputs, keeps warehouse history, and leaves commit to its caller."""
from __future__ import annotations

import json
import shutil

import pytest
import yaml

from kge.adapters.warehouse import open_estate
from kge.estate import certify, from_files
from tests import bigquery_fake
from tests.unit.test_from_files import GOLDEN, build as command, fixture


def declare(pack, sets, fixture_path="fixture"):
    (pack / "pack.yaml").write_text(yaml.safe_dump({
        "manifest": {"client": "shop", "inputs": {"estate": {"fixture": fixture_path}, "sets": list(sets)}},
        "terminology": {},
    }))


def contents(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture(params=["duckdb", "bigquery"])
def estate(tmp_path, monkeypatch, request):
    monkeypatch.setenv("WAREHOUSE_BACKEND", request.param)
    fake = bigquery_fake.install(monkeypatch) if request.param == "bigquery" else None
    fx = fixture(tmp_path / "client")
    out = tmp_path / "estate"
    return fx, out, open_estate(out / "warehouses", "shop"), fake


def test_selected_sets_keep_their_bytes_and_do_not_require_golden(estate):
    fx, out, warehouse, _ = estate
    names = ("team_shop_v1", "sage_v1", "heldout")
    declare(fx.parent, names)
    (fx / "golden.json").unlink()
    for name in names:
        (fx / f"{name}.json").write_text(json.dumps({"version": "v2", "items": GOLDEN}, indent=3) + "\n")
    (fx / "ignored_v1.json").write_text("not JSON")
    summary = from_files.build(fx, out, "shop", estate=warehouse, sets=names)
    for name in names:
        assert (out / f"{name}.json").read_bytes() == (fx / f"{name}.json").read_bytes()
        assert summary[f"{name}_questions"] == 1
    assert not (out / "golden.json").exists()
    assert not (out / "ignored_v1.json").exists()
    assert "golden_questions" not in summary
    assert summary["fixture_sha256"] == from_files.fixture_sha256(fx.parent)


@pytest.mark.parametrize("name", ["golden", "heldout", "sage_v1"])
@pytest.mark.parametrize("invalid", [False, True], ids=["missing", "malformed"])
def test_every_selected_set_is_checked_before_any_write(estate, name, invalid):
    fx, out, warehouse, fake = estate
    from_files.build(fx, out, "shop", estate=warehouse)
    before = contents(out)
    tables = fake.held() if fake else None
    declare(fx.parent, ("golden", name) if name != "golden" else ("golden",))
    path = fx / f"{name}.json"
    if invalid:
        path.write_text('{"version": "v2", "items": "not a list"}')
    elif path.exists():
        path.unlink()
    with pytest.raises(from_files.Unreadable, match=f"{name}.json"):
        from_files.build(fx, out, "shop", estate=warehouse, sets=("golden", name))
    assert contents(out) == before
    assert fake is None or fake.held() == tables


def test_success_removes_sample_files_and_old_docs_but_keeps_warehouses_and_live_sets(estate):
    fx, out, warehouse, fake = estate
    declare(fx.parent, ("golden",))
    out.mkdir()
    stale = {"heldout.json", "golden_wealth_v1.json", "sage_v1.json", "glossary.csv", "mappings.csv",
             "measures.csv", "principals.json", "no_join.json", "units.json", "custom.json",
             "docs/certified/old.md", "docs/uncertified/older.md", "old_inputs/glossary.csv"}
    kept = {"warehouses/old.duckdb", "warehouses/onboarded/old/v1.duckdb",
            "artifacts/shop/old/pack.yaml", "stores/shop/old/LOADED", "landing/shop/current",
            "certification/shop/runs/old.json"}
    for kind in ("golden", "team", "heldout"):
        kept.add(f"{kind}_live_v1.json")
        stale.add(f"{kind}_gone_v1.json")
    (fx.parent / "units/live").mkdir(parents=True)
    (fx.parent / "units/not_a_folder").write_text("not a live unit")
    stale.add("team_not_a_folder_v1.json")
    for name in stale | kept:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    old_tables = fake.held() if fake else None
    from_files.build(fx, out, "shop", estate=warehouse, sets=("golden",))
    assert all(not (out / name).exists() for name in stale)
    assert all((out / name).read_text() == name for name in kept)
    assert (out / "docs/certified/MD-TAKINGS.md").is_file()
    assert (out / "golden.json").read_bytes() == (fx / "golden.json").read_bytes()
    assert fake is None or set(old_tables) <= set(fake.held())


def test_leftover_files_is_a_sorted_read_only_inventory(tmp_path):
    for name in ("summary.json", "golden.json", "sage_v1.json", "docs/certified/old.md", "warehouses/shop.duckdb"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    before = contents(tmp_path)
    assert from_files.leftover_files(tmp_path, {"summary.json", "golden.json"}) == ["docs/certified/old.md", "sage_v1.json"]
    assert contents(tmp_path) == before
    assert from_files.leftover_files(tmp_path / "absent", set()) == []


@pytest.mark.parametrize("deferred", [False, True])
def test_conditional_digest_belongs_to_the_caller_even_after_landing(estate, monkeypatch, deferred):
    fx, out, warehouse, fake = estate
    declare(fx.parent, ("golden",))
    landing = out.parent / "landing"
    calls = []
    original = certify.land

    def land(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(certify, "land", land)
    args = dict(client="shop", estate=warehouse, if_changed=True, landing=landing, defer_landing=deferred, sets=("golden",))
    first = from_files.build(fx, out, **args)
    summary_path = out / "summary.json"
    assert "fixture_sha256" not in json.loads(summary_path.read_text())
    assert first["fixture_sha256"] == from_files.fixture_sha256(fx.parent)
    assert len(calls) == (0 if deferred else 1)
    second = from_files.build(fx, out, **args)
    assert second["rebuilt"] and second["fixture_sha256"] == first["fixture_sha256"]
    assert "fixture_sha256" not in json.loads(summary_path.read_text())
    assert second["version"] == first["version"] == (1 if fake else None)
    summary = json.loads(summary_path.read_text())
    assert summary["warehouse_versions"] == ({"shop": 1} if fake else {})
    assert summary["warehouse_inputs"] == from_files.warehouse_inputs(fx, from_files.read_schema(fx))
    assert warehouse.versions("shop") == ([1] if fake else [])
    summary["fixture_sha256"] = second["fixture_sha256"]
    summary_path.write_text(json.dumps(summary))
    before, landed, count = contents(out), contents(landing), len(calls)
    third = from_files.build(fx, out, **args)
    assert third == {**second, "rebuilt": False, "why": "unchanged"}
    assert contents(out) == before and contents(landing) == landed and len(calls) == count


def test_a_failed_landing_does_not_record_the_digest_or_remove_old_files(estate, monkeypatch):
    fx, out, warehouse, _ = estate
    declare(fx.parent, ("golden",))
    from_files.build(fx, out, "shop", estate=warehouse, sets=("golden",))
    previous = (out / "summary.json").read_bytes()
    (out / "sage_v1.json").write_text("old set")
    (fx / "data/shop/item.csv").write_text("item_id,label\n1,Changed\n2,\n")

    def fail(*args, **kwargs):
        raise RuntimeError("landing failed")

    monkeypatch.setattr(certify, "land", fail)
    with pytest.raises(RuntimeError, match="landing failed"):
        from_files.build(fx, out, "shop", estate=warehouse, if_changed=True, sets=("golden",))
    if warehouse.builds_apart:
        assert (out / "summary.json").read_bytes() == previous
    else:
        expected = json.loads(previous)
        expected.pop("fixture_sha256")
        expected["warehouse_inputs"] = {}
        assert json.loads((out / "summary.json").read_text()) == expected
    assert (out / "sage_v1.json").read_text() == "old set"


def test_changing_the_selected_sets_rebuilds_even_when_fixture_bytes_are_unchanged(estate):
    fx, out, warehouse, _ = estate
    (fx / "sage_v1.json").write_text(json.dumps(GOLDEN))
    declare(fx.parent, ("golden", "sage_v1"))
    from_files.build(fx, out, "shop", estate=warehouse, sets=("golden", "sage_v1"))
    declare(fx.parent, ("golden",))
    result = from_files.build(fx, out, "shop", estate=warehouse, if_changed=True, defer_landing=True, sets=("golden",))
    assert result["rebuilt"] and result["why"] == "fixture-changed"
    assert not (out / "sage_v1.json").exists()


def test_rebuilding_keeps_the_earlier_warehouse_tables_on_a_versioned_estate(estate):
    fx, out, warehouse, fake = estate
    declare(fx.parent, ("golden",))
    first = from_files.build(fx, out, "shop", estate=warehouse, sets=("golden",))
    rows = {name: fake.rows(name) for name in fake.held() if name.startswith("shop_v1_")} if fake else {}
    (fx / "data/shop/sale.csv").write_text("sale_id,item_id,sold_on,amount\n10,1,2025-06-03,9.50\n")
    second = from_files.build(fx, out, "shop", estate=warehouse, sets=("golden",))
    if fake:
        assert first["version"] == 1 and second["version"] == 2
        assert set(rows) == {"shop_v1_item", "shop_v1_sale"}
        assert {name: fake.rows(name) for name in rows} == rows
        old = warehouse.open("shop", first["version"])
        try:
            assert float(old.execute("SELECT sum(amount) FROM sale").fetchone()[0]) == 3.5
        finally:
            old.close()
    current = warehouse.open("shop", second["version"])
    try:
        assert float(current.execute("SELECT sum(amount) FROM sale").fetchone()[0]) == 9.5
    finally:
        current.close()


def test_nested_fixture_keeps_its_packs_live_unit_sets(estate):
    fx, out, warehouse, _ = estate
    pack = fx.parent
    declare(pack, ("golden",), "inputs/source")
    (pack / "units/live").mkdir(parents=True)
    nested = pack / "inputs/source"
    nested.parent.mkdir()
    fx.rename(nested)
    out.mkdir()
    (out / "team_live_v1.json").write_text("page-owned")
    from_files.build(nested, out, "shop", estate=warehouse, if_changed=True, defer_landing=True, sets=("golden",))
    assert (out / "team_live_v1.json").read_text() == "page-owned"


def test_an_empty_document_fixture_removes_the_old_docs_tree(estate):
    fx, out, warehouse, _ = estate
    from_files.build(fx, out, "shop", estate=warehouse)
    shutil.rmtree(fx / "docs")
    from_files.build(fx, out, "shop", estate=warehouse)
    assert not (out / "docs").exists()
    assert json.loads((out / "documents.json").read_text()) == []


@pytest.mark.parametrize("golden_only", [False, True])
def test_the_module_uses_the_packs_declared_sets(tmp_path, golden_only):
    fx = fixture(tmp_path)
    declare(fx.parent, ("sage_v1",))
    (fx / "golden.json").unlink()
    (fx / "sage_v1.json").write_text(json.dumps(GOLDEN))
    out = tmp_path / "estate"
    result = command(fx, out, *(["--golden-only"] if golden_only else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["sage_v1_questions"] == 1
    assert (out / "sage_v1.json").read_bytes() == (fx / "sage_v1.json").read_bytes()
    assert not (out / "golden.json").exists()
