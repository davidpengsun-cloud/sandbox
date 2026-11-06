"""Fixture rebuilds preserve served warehouse versions and skip unchanged inputs."""
from __future__ import annotations

import hashlib
import json

import pytest

from kge.adapters.warehouse import estate_of
from kge.estate import certify, from_files
from kge.landing.zone import Zone
from kge.pipeline import warehouse_versions
from kge.pipeline.profile import Profile
from tests import bigquery_fake
from tests.unit.test_from_files import NOTE, fixture


@pytest.fixture(params=["duckdb", "bigquery"])
def built(tmp_path, monkeypatch, request):
    monkeypatch.setenv("WAREHOUSE_BACKEND", request.param)
    fake = bigquery_fake.install(monkeypatch) if request.param == "bigquery" else None
    clients = tmp_path / "clients"
    fx = fixture(clients / "shop")
    (fx.parent / "pack.yaml").write_text("manifest: {client: shop}\nterminology: {}\n")
    prof = Profile("shop", "shop", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "estate" / "warehouses", tmp_path / "identities.yaml", None, tmp_path / "profiles.yaml")
    return prof, fx, fake


def rebuild(prof, fx, **kwargs):
    """Commit a successful caller run after proving the builder itself left the new digest unset."""
    result = from_files.build(fx, prof.estate(), prof.client, if_changed=True, landing=prof.landing, **kwargs)
    summary = prof.estate() / "summary.json"
    recorded = json.loads(summary.read_text())
    if result["rebuilt"]:
        assert "fixture_sha256" not in recorded
        summary.write_text(json.dumps({**recorded, "fixture_sha256": result["fixture_sha256"]}))
    return result


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def warehouse_bytes(fake, unit, version):
    names = sorted(t for t in fake.held() if t.startswith(f"{unit}_v{version}_"))
    columns = fake.con.execute("SELECT table_name, column_name, data_type, ordinal_position "
                               "FROM information_schema.columns ORDER BY table_name, ordinal_position").fetchall()
    return json.dumps({"rows": {t: fake.rows(t) for t in names}, "labels": {t: fake.labels[t] for t in names},
                       "columns": [row for row in columns if row[0] in names],
                       "catalogue": [row for row in fake.rows("_kge_catalogue") if row[:2] == (unit, version)]},
                      sort_keys=True, default=str).encode()


def test_digest_is_sorted_relative_paths_nul_bytes_nul(tmp_path):
    fx = fixture(tmp_path)
    (fx / "empty").write_bytes(b"")
    (fx / "nested").mkdir()
    (fx / "nested" / "raw").write_bytes(b"\xff\x00\r\n")
    expected = hashlib.sha256(b"".join(name.encode() + b"\0" + data + b"\0"
                                      for name, data in sorted(contents(fx).items()))).hexdigest()
    assert from_files.fixture_sha256(tmp_path) == expected
    (tmp_path / "pack.yaml").write_text("outside the fixture")
    assert from_files.fixture_sha256(tmp_path) == expected
    (fx / "empty").rename(fx / "renamed")
    assert from_files.fixture_sha256(tmp_path) != expected


def test_unchanged_build_writes_and_certifies_nothing(built, monkeypatch):
    prof, fx, fake = built
    first = rebuild(prof, fx)
    first_release = Zone(prof.landing, prof.client).open().id
    assert first == {"rebuilt": True, "why": "no-estate", "version": 1 if fake else None,
                     "fixture_sha256": from_files.fixture_sha256(fx.parent)}
    summary = json.loads((prof.estate() / "summary.json").read_text())
    assert summary["version"] == first["version"] and summary["fixture_sha256"] == first["fixture_sha256"]
    before = contents(prof.estate())
    landing = contents(prof.landing)
    estate = estate_of(prof)

    def refused(*args, **kwargs):
        pytest.fail("unchanged fixture must not stage, publish or certify")

    monkeypatch.setattr(estate, "stage", refused)
    monkeypatch.setattr(estate, "publish", refused)
    monkeypatch.setattr(certify, "land", refused)
    (fx / ".DS_Store").write_bytes(b"Mac folder metadata copied by rsync")
    loads = list(fake.loads) if fake else []
    assert rebuild(prof, fx, estate=estate) == {**first, "rebuilt": False, "why": "unchanged"}
    assert contents(prof.estate()) == before and contents(prof.landing) == landing
    assert Zone(prof.landing, prof.client).open().id == first_release
    assert not fake or fake.loads == loads
    assert warehouse_versions.estate_live(prof) == ({"shop": 1} if fake else {})


def test_digest_skips_dot_names_at_every_depth_but_keeps_visible_files(tmp_path):
    fx = fixture(tmp_path)
    expected = from_files.fixture_sha256(tmp_path)
    (fx / ".DS_Store").write_bytes(b"folder metadata")
    (fx / "data" / ".DS_Store").write_bytes(b"nested folder metadata")
    (fx / ".cache").mkdir()
    (fx / ".cache" / "visible-name").write_bytes(b"hidden folder contents")
    assert from_files.fixture_sha256(tmp_path) == expected
    (fx / "data" / "visible.extra").write_bytes(b"fixture contents")
    assert from_files.fixture_sha256(tmp_path) != expected


def test_changed_row_preserves_old_tables_and_third_build_prunes_to_two(built):
    prof, fx, fake = built
    first = rebuild(prof, fx)
    first_release = Zone(prof.landing, prof.client).open().id
    old = {t: fake.rows(t) for t in fake.held() if t.startswith("shop_v1_")} if fake else {}
    served = warehouse_bytes(fake, "shop", 1) if fake else None
    csv = fx / "data" / "shop" / "sale.csv"
    csv.write_text(csv.read_text().replace("1.50", "9.50"))
    second = rebuild(prof, fx)
    assert second["why"] == "fixture-changed" and second["rebuilt"]
    assert second["fixture_sha256"] != first["fixture_sha256"]
    assert second["version"] == (2 if fake else None)
    estate = estate_of(prof)
    session = estate.open("shop", second["version"])
    try:
        assert float(session.execute("SELECT sum(amount) FROM sale").fetchone()[0]) == 11.5
    finally:
        session.close()
    if fake:
        assert {t: fake.rows(t) for t in old} == old
        assert warehouse_bytes(fake, "shop", 1) == served
        assert fake.datasets == {"kge_shop_warehouses"}
        release = Zone(prof.landing, prof.client).open()
        assert release.id != first_release
        assert list(json.loads((release.path / "warehouse-inputs.json").read_text())) == [
            "bigquery:kge_shop_warehouses.shop_v2#catalogue"]
        repeated, _, _ = certify.land(prof.estate(), prof.client, prof.landing)
        assert repeated.id == release.id and not repeated.new
    previous_release = warehouse_bytes(fake, "shop", 2) if fake else None
    csv.write_text(csv.read_text().replace("9.50", "8.50"))
    third = rebuild(prof, fx)
    assert third["version"] == (3 if fake else None)
    assert warehouse_versions.prune_estate(prof, {"shop": {2, 3}}) == ({"shop": [1]} if fake else {})
    assert estate.versions("shop") == ([2, 3] if fake else [])
    if fake:
        assert warehouse_bytes(fake, "shop", 2) == previous_release
        assert not any(t.startswith("shop_v1_") for t in fake.held())
        assert {t for t in fake.held() if t != "_kge_catalogue"} == {
            f"shop_v{v}_{t}" for v in (2, 3) for t in ("item", "sale")}


def test_added_document_rebuilds_and_lands_its_passage(built):
    prof, fx, fake = built
    rebuild(prof, fx)
    previous = Zone(prof.landing, prof.client).open().id
    (fx / "docs" / "uncertified" / "EM-02.md").write_text(NOTE + "\nThe second delivery arrives at noon.\n")
    changed = rebuild(prof, fx)
    assert changed["why"] == "fixture-changed" and changed["rebuilt"]
    release = Zone(prof.landing, prof.client).open()
    assert release.id != previous
    assert "second delivery" in (release.path / "documents" / "EM-02.md").read_text()


def test_unconditional_seed_rebuilds_even_with_the_same_sha(built, monkeypatch):
    prof, fx, fake = built
    builds = []
    original_build = from_files.build_warehouse

    def build_warehouse(*args, **kwargs):
        builds.append(args)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(from_files, "build_warehouse", build_warehouse)
    first = from_files.build(fx, prof.estate(), prof.client)
    second = from_files.build(fx, prof.estate(), prof.client)
    assert len(builds) == 2
    assert first["rows"] == second["rows"]
    assert first["fixture_sha256"] == second["fixture_sha256"]
    assert second["version"] == (2 if fake else None)


def test_legacy_zero_is_kept_then_pruned_without_touching_new_version(built):
    prof, fx, fake = built
    if not fake:
        return
    estate = estate_of(prof)
    houses = from_files.read_schema(fx)
    from_files.build_warehouse(estate, "shop", houses["shop"]["tables"], fx / "data" / "shop")
    estate.publish("shop")
    old = {t: fake.rows(t) for t in ("shop_item", "shop_sale")}
    assert rebuild(prof, fx)["version"] == 1
    assert {t: fake.rows(t) for t in old} == old
    assert estate.label("shop", 0).endswith(".shop")
    assert warehouse_versions.prune_estate(prof, {"shop": {0, 1}}) == {}
    assert {t: fake.rows(t) for t in old} == old
    assert warehouse_versions.prune_estate(prof, {"shop": {1}}) == {"shop": [0]}
    assert not set(old) & set(fake.held())
    assert estate.versions("shop") == [1]


def test_no_fixture_does_not_read_or_prune_meridian_warehouses(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    prof = Profile("meridian", "meridian", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores",
                   tmp_path / "landing", tmp_path / "warehouses", tmp_path / "identities.yaml", None, tmp_path / "profiles.yaml")
    assert warehouse_versions.estate_live(prof) == {}
    assert warehouse_versions.prune_estate(prof, {}) == {}
    assert not tmp_path.joinpath("warehouses").exists()


def test_replacing_all_fixture_units_preserves_version_order_and_excludes_legacy_units(built):
    import shutil
    import yaml

    prof, fx, fake = built
    estate = estate_of(prof)
    houses = from_files.read_schema(fx)
    for unit in ("shop", "other"):
        from_files.build_warehouse(estate, unit, houses["shop"]["tables"], fx / "data" / "shop")
        estate.publish(unit)
    rebuild(prof, fx)
    from_files.build(fx, prof.estate(), prof.client)
    from_files.build(fx, prof.estate(), prof.client)
    old = {t: fake.rows(t) for t in fake.held() if t.startswith("shop_")} if fake else {}
    (fx / "schema.yaml").write_text(yaml.safe_dump({"warehouses": {"branch": houses["shop"]}}))
    shutil.copytree(fx / "data" / "shop", fx / "data" / "branch")
    document = fx / "docs" / "certified" / "MD-TAKINGS.md"
    document.write_text(document.read_text().replace("unit: shop", "unit: branch"))
    record = rebuild(prof, fx)
    assert record["version"] == (4 if fake else None)
    release = Zone(prof.landing, prof.client).open()
    assert release.has("structured/branch.json") and release.has("structured/other.json")
    assert not release.has("structured/shop.json")
    again, _, _ = certify.land(prof.estate(), prof.client, prof.landing)
    assert again.id == release.id
    if fake:
        assert {t: fake.rows(t) for t in old} == old
        assert warehouse_versions.prune_estate(prof, {"shop": {3}, "branch": {4}}) == {"shop": [0, 1, 2]}
        assert estate.versions("shop") == [3] and estate.exists("other")


def test_certify_cli_reads_the_explicit_estate_location(built, monkeypatch, capsys):
    import sys

    prof, fx, fake = built
    record = from_files.build(fx, prof.estate(), prof.client)
    monkeypatch.setattr(sys, "argv", ["certify", "--build", str(prof.estate()), "--clients", str(prof.clients),
                                     "--client", prof.client, "--landing", str(prof.landing)])
    assert certify.main() == 0
    first = json.loads(capsys.readouterr().out)
    assert certify.main() == 0
    second = json.loads(capsys.readouterr().out)
    assert second["release"] == first["release"] and not second["new"]
    release = Zone(prof.landing, prof.client).open()
    assert release.has("structured/shop.json")
    if fake:
        inputs = json.loads((release.path / "warehouse-inputs.json").read_text())
        assert f"bigquery:kge_shop_warehouses.shop_v{record['version']}#catalogue" in inputs


def test_failed_certification_does_not_record_the_fixture_as_built(built, monkeypatch):
    prof, fx, fake = built
    rebuild(prof, fx)
    before = (prof.estate() / "summary.json").read_bytes()
    csv = fx / "data" / "shop" / "sale.csv"
    csv.write_text(csv.read_text().replace("1.50", "9.50"))

    def failed(*args, **kwargs):
        raise RuntimeError("certification failed")

    with monkeypatch.context() as broken:
        broken.setattr(certify, "land", failed)
        with pytest.raises(RuntimeError, match="certification failed"):
            rebuild(prof, fx)
    if fake:
        assert (prof.estate() / "summary.json").read_bytes() == before
    else:
        expected = json.loads(before)
        expected.pop("fixture_sha256")
        expected["warehouse_inputs"] = {}
        assert json.loads((prof.estate() / "summary.json").read_text()) == expected
    assert rebuild(prof, fx)["why"] == "fixture-changed"


def test_onboard_never_commits_digest_when_a_later_lock_fails(built, monkeypatch):
    from kge import cli

    from tests.unit.test_cli import stand_in_onboard
    stand_in_onboard(monkeypatch)
    prof, fx, fake = built
    monkeypatch.setattr(cli.profile, "resolve", lambda *args, **kwargs: prof)
    commands = cli.Commands({"PACK": str(fx.parent), "LANDED": str(prof.landing / prof.client / "current")})
    attempts, landings = [], []
    fail = True
    original_land = certify.land

    def land(*args, **kwargs):
        landings.append(args)
        return original_land(*args, **kwargs)

    monkeypatch.setattr(certify, "land", land)

    def module(name, *args, **kwargs):
        if name == "kge.estate.onboard":
            assert args == ("build", "--json")
            assert not landings
            assert "fixture_sha256" not in json.loads((prof.estate() / "summary.json").read_text())
            return '{"units": []}'
        if name == "kge.estate.certify":
            attempts.append(name)
            if fail:
                raise cli.Failed(2, name)
            certify.land(prof.estate(), prof.client, prof.landing, estate=estate_of(prof),
                         onboarded=warehouse_versions.live_versions(fx.parent, build=prof.estate()))
            return 0
        assert name == "kge.compiler.lock"
        raise cli.Failed(7, "stop after landing")

    monkeypatch.setattr(commands, "module", module)
    with pytest.raises(cli.Failed) as failed:
        commands.onboard()
    assert failed.value.returncode == 2 and len(attempts) == 1
    assert not landings and not (prof.landing / prof.client / "current").exists()
    assert "fixture_sha256" not in json.loads((prof.estate() / "summary.json").read_text())
    assert not (prof.estate() / "landing").exists()
    fail = False
    with pytest.raises(cli.Failed) as stopped:
        commands.onboard()
    assert stopped.value.returncode == 7 and len(attempts) == 2
    assert len(landings) == 1
    summary = json.loads((prof.estate() / "summary.json").read_text())
    assert "fixture_sha256" not in summary
    assert not (prof.estate() / "landing").exists()
    assert Zone(prof.landing, prof.client).open().has("structured/shop.json")
