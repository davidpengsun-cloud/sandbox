"""All input refusals are collected without changing the client's files or estate."""
from dataclasses import asdict, replace
import hashlib
import json
import shutil

import pytest
import yaml

from kge.compiler.pack import Finding
from kge.estate import upfront
from kge.eval import sets
from kge.adapters.appstate import access_state
from kge.pipeline.profile import Profile
from kge.pipeline.settings import ROOT
from kge.ports import EstateLimits


class Estate:
    """Only validation exists: any warehouse access or build fails the test."""

    def __init__(self, limit=None):
        self.limit, self.types = limit, []

    def limits(self):
        return EstateLimits(self.limit, 63)

    def refuses_type(self, value):
        self.types.append(value)
        return f"type {value} is refused" if value == "UNSUPPORTED" else None

    def refuses_schema(self, unit, statements):
        return None

    def refuses_key_type(self, value):
        return None


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def json_put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def edit(path, change):
    value = yaml.safe_load(path.read_text())
    change(value)
    put(path, value)


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def grants(prof, rows):
    state = access_state(prof)
    for row in state.access_grants():
        state.access_delete(row["username"], by="operator@example.com", via="job")
    for username, roles in rows.items():
        state.access_put({"username": username, "name": username, "roles": roles}, by="operator@example.com", via="job")


@pytest.fixture
def client(tmp_path, monkeypatch):
    prof = Profile("acme", "acme", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores",
                   tmp_path / "landing", tmp_path / "estate/warehouses", tmp_path / "deploy/identities.yaml",
                   None, tmp_path / "deploy/profiles.yaml")
    folder = prof.clients / prof.client
    put(folder / "pack.yaml", {
        "manifest": {"client": "acme", "inputs": {"estate": {"fixture": "fixture"}, "sets": ["golden", "heldout"]}},
        "sources": [{"id": "sales", "harvest": True}],
        "access": {"roles": [{"id": "reader", "processes": ["querying"]}, {"id": "steward", "processes": ["certification"]}]},
        "mappings": [{"column": "sales.sale.id"}],
        "tests": {"sets": [{"name": "golden", "version": "v2"}, {"name": "heldout", "version": "v1"}]}})
    grants(prof, {"ann@example.com": ["reader"], "sam@example.com": ["steward"]})
    put(folder / "fixture/schema.yaml", {"warehouses": {"sales": {"label": "Sales", "tables": [
        {"name": "sale", "columns": [{"name": "id", "type": "INTEGER", "description": "Identifier"}], "primary_key": ["id"]}]}}})
    data = folder / "fixture/data/sales/sale.csv"
    data.parent.mkdir(parents=True)
    data.write_text("id\n1\n")
    json_put(folder / "fixture/golden.json", {"version": "v2", "items": [{"id": "one", "question": "One?", "kind": "within", "expect": {}}, {"id": "two", "question": "Two?", "kind": "within", "expect": {}}]})
    json_put(folder / "fixture/heldout.json", [{"id": "held", "question": "One again?", "kind": "within", "expect": {}}])
    doc = folder / "fixture/docs/certified/guide.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\ntitle: Guide\nkind: policy\nunit: sales\n---\nThe guide.\n")
    estate = Estate()
    monkeypatch.setattr(upfront, "estate_of", lambda p: estate)
    monkeypatch.setattr(upfront.graphstore, "store_info", lambda: {"engine": "here"})
    return prof, folder, estate


def rules(result):
    return [f.rule for f in result.findings]


def saved_baseline(prof, folder):
    recorded = {}
    for stem in ("golden", "heldout"):
        path = folder / f"fixture/{stem}.json"
        data = sets.read(path)
        recorded[stem] = {"file": path.name, "version": data["version"], "items": len(data["items"]), "size": len(data["items"]),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "item_sha256": sets.item_hashes(data["items"])}
    path = folder / "baselines/first.json"
    json_put(path, {"principal": "reader", "sets": recorded, "graph": {"store": {"engine": "here"}}})
    return replace(prof, baseline=path)


def test_valid_check_reads_only_and_hashes_paths_and_contents(client, tmp_path):
    prof, folder, estate = client
    before = snapshot(tmp_path)
    result = upfront.check(prof)
    assert result.findings == []
    assert snapshot(tmp_path) == before
    assert estate.types == ["INTEGER"]
    expected = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert result.files == {n: hashlib.sha256(b).hexdigest() for n, b in expected.items()}
    assert result.inputs_sha256 == hashlib.sha256(b"".join(n.encode() + b"\0" + b + b"\0" for n, b in sorted(expected.items()))).hexdigest()
    fixture = folder / "fixture"
    blobs = {p.relative_to(fixture).as_posix(): p.read_bytes() for p in fixture.rglob("*") if p.is_file()}
    assert result.fixture_sha256 == hashlib.sha256(b"".join(n.encode() + b"\0" + b + b"\0" for n, b in sorted(blobs.items()))).hexdigest()
    assert result.declared and result.suggestion is None


def test_missing_inputs_all_reported_without_writes(client, tmp_path):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: (p["manifest"].update(includes=["extra.yaml"]), p["manifest"]["inputs"].update(catalogue="catalogue.yaml")))
    (folder / "fixture/golden.json").unlink()
    before = snapshot(tmp_path)
    result = upfront.check(prof)
    assert rules(result).count("I01") == 3
    assert any(f.path == "pack.yaml:manifest.inputs.catalogue" for f in result.findings)
    assert any(f.rule == "I01" and f.path == "pack.yaml:manifest.includes" for f in result.findings)
    assert snapshot(tmp_path) == before


def test_absent_declared_fixture_never_falls_back(client):
    prof, folder, _ = client
    shutil.rmtree(folder / "fixture")
    assert rules(upfront.check(prof)) == ["I02"]
    assert any(f.rule == "I02" and f.path == "pack.yaml:manifest.inputs.estate.fixture" and
               f.message == "acme's pack declares a fixture and none was uploaded: upload it, or run teardown to start over"
               for f in upfront.check(prof).findings)


def test_malformed_declarations_and_undeclared_test_set(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p["tests"]["sets"].append({"name": "extra", "version": "v1"}))
    assert "I03" in rules(upfront.check(prof))
    edit(folder / "pack.yaml", lambda p: p["manifest"].update(inputs={"estate": {"fixture": "../bad"}, "sets": [], "catalogue": 3}))
    result = upfront.check(prof)
    assert rules(result).count("I00") == 3
    assert result.declared and result.suggestion is None


def test_ignored_and_owned_files_do_not_enter_digest(client):
    prof, folder, _ = client
    before = upfront.check(prof)
    for name in ("catalogue.yaml", "unused.yaml", "pack.lock", "units/x/note", "baselines/old.json", ".DS_Store"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unused")
    json_put(prof.estate() / "stray_v1.json", [{"id": "stray"}])
    after = upfront.check(prof)
    assert after.findings == []
    assert after.ignored == ["clients/acme/catalogue.yaml", "clients/acme/unused.yaml", "estate/stray_v1.json"]
    assert before.inputs_sha256 == after.inputs_sha256


def test_legacy_suggests_declarations_and_ignores_nothing(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p["manifest"].pop("inputs"))
    (folder / "stray.yaml").write_text("unused")
    result = upfront.check(prof)
    assert result.findings == []
    assert not result.declared and result.ignored == []
    assert "fixture: fixture" in result.suggestion
    assert "golden" in result.suggestion and "heldout" in result.suggestion


def test_includes_checked_and_hashed(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: (p["manifest"].update(includes=["mappings.yaml"]), p.pop("mappings")))
    put(folder / "mappings.yaml", {"mappings": [{"column": "sales.sale.missing"}]})
    result = upfront.check(prof)
    assert any(f.rule == "F03" and f.path == "mappings.yaml:mappings[0].column" for f in result.findings)
    assert "mappings.yaml" in result.files


def test_harvested_sources_catalogue_and_pack_references(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: (p["sources"].append({"id": "missing", "harvest": True}),
                                          p["mappings"].append({"column": "sales.unknown.id"}),
                                          p["manifest"]["inputs"].update(catalogue="catalogue.yaml")))
    put(folder / "catalogue.yaml", {"format": 1, "units": ["alien"], "products": []})
    result = upfront.check(prof)
    assert set(rules(result)) == {"F01", "F02", "F03"}
    assert any(f.path == "catalogue.yaml:units" for f in result.findings)


def test_catalogue_checks_current_fixture_not_stale_lock(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p["manifest"]["inputs"].update(catalogue="catalogue.yaml"))
    put(folder / "catalogue.yaml", {"format": 1, "units": ["sales"], "products": [{"id": "sales.missing", "unit": "sales", "table": "missing"}]})
    json_put(folder / "pack.lock", {"sources": {"sales": {"tables": [{"name": "missing", "columns": []}]}}})
    assert any(f.rule == "F02" and "sales.missing" in f.message for f in upfront.check(prof).findings)


@pytest.mark.parametrize("limit,length,refused", [(None, 1025, False), (1024, 1024, False), (1024, 1025, True), (2, 3, True)])
def test_description_limit_from_port(client, limit, length, refused):
    prof, folder, estate = client
    estate.limit = limit
    edit(folder / "fixture/schema.yaml", lambda s: s["warehouses"]["sales"]["tables"][0]["columns"][0].update(description="x" * length))
    assert ("F05" in rules(upfront.check(prof))) is refused


def test_types_descriptions_parent_order_and_every_csv_row(client):
    prof, folder, _ = client
    def damage(schema):
        table = schema["warehouses"]["sales"]["tables"][0]
        table["columns"][0].update(type="UNSUPPORTED", description="")
        table["foreign_keys"] = [{"columns": ["id"], "ref_table": "later", "ref_columns": ["id"]},
                                 {"columns": ["id"], "ref_table": "unknown", "ref_columns": ["id"]}]
        schema["warehouses"]["sales"]["tables"].append({"name": "later", "columns": [{"name": "id", "type": "INTEGER", "description": "ID"}], "primary_key": ["id"]})
    edit(folder / "fixture/schema.yaml", damage)
    (folder / "fixture/data/sales/sale.csv").write_text("wrong\n1,2\n1,2,3\n")
    result = upfront.check(prof)
    assert {"F04", "F05", "F06", "F07"} <= set(rules(result))
    assert [f.path for f in result.findings if f.rule == "F07"] == ["fixture/data/sales/sale.csv:row 1", "fixture/data/sales/sale.csv:row 2", "fixture/data/sales/sale.csv:row 3", "fixture/data/sales/later.csv:row 1"]


@pytest.mark.parametrize("text", ["no front matter", "---\n[bad, shape]\n---\ntext", "---\ntitle: T\nkind: policy\nunit: unknown\n---\ntext", "---\ntitle: [\n---\ntext"])
def test_bad_documents_are_findings(client, text):
    prof, folder, _ = client
    (folder / "fixture/docs/certified/guide.md").write_text(text)
    assert "F08" in rules(upfront.check(prof))


@pytest.mark.parametrize("value", [[{"id": "one"}], {"version": "v2", "items": [{"id": "one"}, {"id": "one"}]}, {"version": "v2", "items": "wrong"}, 5])
def test_question_format_and_version(client, value):
    prof, folder, _ = client
    json_put(folder / "fixture/golden.json", value)
    assert "F09" in rules(upfront.check(prof))


def test_changed_question_file_requires_declaration(client):
    prof, folder, _ = client
    prof = saved_baseline(prof, folder)
    json_put(folder / "fixture/golden.json", {"version": "v2", "items": [{"id": "one", "question": "Changed?"}]})
    assert "F10" in rules(upfront.check(prof))


def test_valid_removal_comparable_and_wrong_digest_refused(client):
    prof, folder, _ = client
    prof = saved_baseline(prof, folder)
    before = json.loads(prof.baseline.read_text())["sets"]["golden"]
    edit(folder / "pack.yaml", lambda p: (p["manifest"]["inputs"].update(sets=["golden"]), p["tests"].update(sets=[{"name": "golden", "version": "v3"}])))
    changed = {"version": "v3", "changes_from": {"version": "v2", "sha256": before["sha256"], "items": [], "removed": ["two"], "why": "Retired"}, "items": [{"id": "one", "question": "One?", "kind": "within", "expect": {}}]}
    json_put(folder / "fixture/golden.json", changed)
    assert upfront.check(prof).findings == []
    changed["changes_from"]["sha256"] = "0" * 64
    json_put(folder / "fixture/golden.json", changed)
    assert {"F10", "B02"} <= set(rules(upfront.check(prof)))


def test_access_lists_every_missing_role_without_hashing_people(client, tmp_path):
    prof, folder, _ = client
    initial = upfront.check(prof)
    grants(prof, {"ann@example.com": ["reader", "absent"], "sam@example.com": ["missing", "access_admin"]})
    before = snapshot(tmp_path)
    result = upfront.check(prof)
    assert rules(result) == ["A01", "A01"]
    assert [(f.path, f.message) for f in result.findings] == [
        ("access:ann@example.com" + ".roles", "ann@example.com holds absent, which the pack does not define"),
        ("access:sam@example.com" + ".roles", "sam@example.com holds missing, which the pack does not define")]
    assert result.files == initial.files and result.inputs_sha256 == initial.inputs_sha256
    assert snapshot(tmp_path) == before


def test_empty_access_and_admin_only_need_no_pack_role_or_identity_seed(client, tmp_path):
    prof, _, _ = client
    grants(prof, {})
    before = snapshot(tmp_path)
    assert upfront.check(prof).findings == []
    assert snapshot(tmp_path) == before
    grants(prof, {"operator@example.com": ["access_admin"]})
    assert upfront.check(prof).findings == []


def test_baseline_missing_role_and_other_store_explain_fix(client):
    prof, folder, _ = client
    prof = saved_baseline(prof, folder)
    b = json.loads(prof.baseline.read_text())
    b.update(principal="gone", graph={"store": {"engine": "elsewhere"}})
    json_put(prof.baseline, b)
    result = upfront.check(prof)
    assert rules(result) == ["B01", "B02"]
    assert "the role it asked as no longer exists: gone" in result.findings[0].message
    assert all(f.message.endswith(upfront.BASELINE_FIX) for f in result.findings)


def test_baseline_no_comparable_declared_version(client):
    prof, folder, _ = client
    prof = saved_baseline(prof, folder)
    b = json.loads(prof.baseline.read_text())
    b["sets"] = {"absent": {"file": "absent.json", "version": "v1"}}
    json_put(prof.baseline, b)
    result = upfront.check(prof)
    assert rules(result) == ["B02"]
    assert "no declared set at a comparable version" in result.findings[0].message


def test_checks_extend_in_order_cli_lists_all_and_writes_nothing(client, monkeypatch, capsys, tmp_path):
    prof, folder, _ = client
    order = []
    def appended(ctx):
        order.append(ctx)
        return [Finding("NEXT", "fixture:limit", "next issue's port limit")]
    monkeypatch.setattr(upfront, "CHECKS", upfront.CHECKS + [appended])
    monkeypatch.setattr(upfront, "resolve", lambda: prof)
    (folder / "fixture/data/sales/sale.csv").write_text("wrong\n1,2\n")
    grants(prof, {"bad@example.com": ["unknown"]})
    before = snapshot(tmp_path)
    target = tmp_path / "report.json"
    assert upfront.main(["--json", str(target)]) == 2
    output = capsys.readouterr().out
    assert len(order) == 1 and order[0].estate is client[2]
    assert order[0].declared.fixture == "fixture"
    assert "F07 fixture/data/sales/sale.csv:row 1" in output
    for rule in ("F07", "A01", "NEXT"):
        assert f"onboard: refused: {rule}" in output
    assert "onboard: refused, 4 finding(s): nothing was written" in output
    assert not target.exists() and snapshot(tmp_path) == before


def test_cli_success_json_and_legacy_suggestion(client, monkeypatch, capsys, tmp_path):
    prof, folder, _ = client
    monkeypatch.setattr(upfront, "resolve", lambda: prof)
    (folder / "stray.yaml").write_text("unused")
    target = tmp_path / "report.json"
    assert upfront.main(["--json", str(target)]) == 0
    assert "onboard: ignored: clients/acme/stray.yaml" in capsys.readouterr().out
    assert json.loads(target.read_text()) == asdict(upfront.check(prof))
    edit(folder / "pack.yaml", lambda p: p["manifest"].pop("inputs"))
    assert upfront.main([]) == 0
    assert "  inputs:" in capsys.readouterr().out


def test_committed_fixture_passes_without_lock(client):
    prof, folder, _ = client
    shutil.rmtree(folder)
    shutil.copytree(ROOT / "clients/fellmoor", folder)
    grants(prof, {})
    assert upfront.check(prof).findings == []


@pytest.mark.parametrize("value", [None, [], {"warehouses": []}, {"warehouses": {"sales": None}},
    {"warehouses": {"sales": {"label": "Sales", "tables": 2}}},
    {"warehouses": {"sales": {"label": "Sales", "tables": [{"name": "sale", "columns": 2, "foreign_keys": 3}]}}}])
def test_malformed_schema_still_checks_access(client, value):
    prof, folder, _ = client
    put(folder / "fixture/schema.yaml", value)
    grants(prof, {"ann@example.com": ["missing"]})
    result = upfront.check(prof)
    assert {"A01"} <= set(rules(result))
    assert any(r in rules(result) for r in ("F01", "F03"))


@pytest.mark.parametrize("value", [[], {"principals": 3}, {"principals": [3]}, {"principals": [{"principal": []}]}])
def test_legacy_identities_are_not_read(client, value):
    prof, folder, _ = client
    put(prof.identities, value)
    (folder / "fixture/data/sales/sale.csv").unlink()
    assert rules(upfront.check(prof)) == ["F07"]


@pytest.mark.parametrize("value", [None, [], {"roles": 3}, {"roles": [{}]}, {"roles": [{"id": "reader", "processes": 2}]}])
def test_malformed_roles_are_findings(client, value):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p.update(access=value))
    assert {"S01", "A01"} <= set(rules(upfront.check(prof)))


def test_every_rule_can_be_reported_together(client, tmp_path):
    prof, folder, estate = client
    prof = saved_baseline(prof, folder)
    baseline = json.loads(prof.baseline.read_text())
    baseline.update(principal="gone", graph={"store": {"engine": "elsewhere"}})
    json_put(prof.baseline, baseline)
    def pack_changes(p):
        p["manifest"].update(includes=["absent.yaml"])
        p["manifest"]["inputs"].update(catalogue="catalogue.yaml")
        p["tests"]["sets"].append({"name": "stray", "version": "v1"})
        p["sources"].append({"id": "absent", "harvest": True})
        p["mappings"].append({"column": "sales.sale.absent"})
    edit(folder / "pack.yaml", pack_changes)
    put(folder / "catalogue.yaml", {"format": 1, "units": ["unknown"], "products": []})
    def schema_changes(s):
        table = s["warehouses"]["sales"]["tables"][0]
        table["columns"][0].update(type="UNSUPPORTED", description="")
        table["foreign_keys"] = [{"ref_table": "later", "columns": ["id"], "ref_columns": ["id"]}]
    edit(folder / "fixture/schema.yaml", schema_changes)
    (folder / "fixture/data/sales/sale.csv").write_text("wrong\n1,2\n")
    (folder / "fixture/docs/certified/guide.md").write_text("bad front matter")
    json_put(folder / "fixture/golden.json", {"version": "v1", "items": [{"id": "one"}]})
    grants(prof, {"ann@example.com": ["unknown"]})
    before = snapshot(tmp_path)
    result = upfront.check(prof)
    assert {"I01", "I03", *(f"F{i:02}" for i in range(1, 11)), "A01", "B01", "B02"} <= set(rules(result))
    assert snapshot(tmp_path) == before
    assert estate.types == ["UNSUPPORTED"]


def test_row_selector_can_read_other_table_of_same_source_and_reports_missing_column(client):
    prof, folder, _ = client
    edit(folder / "fixture/schema.yaml", lambda s: s["warehouses"]["sales"]["tables"].append(
        {"name": "other", "columns": [{"name": "id", "type": "INTEGER", "description": "ID"}], "primary_key": ["id"]}))
    (folder / "fixture/data/sales/other.csv").write_text("id\n1\n")
    edit(folder / "pack.yaml", lambda p: p.update(row_selectors=[{"table": "sales.sale", "predicate": "sale.id = other.id"}]))
    assert upfront.check(prof).findings == []
    edit(folder / "pack.yaml", lambda p: p["row_selectors"][0].update(predicate="sale.id = other.missing"))
    assert any(f.rule == "F03" and f.path.endswith("row_selectors[0].predicate") for f in upfront.check(prof).findings)


def test_legacy_further_sets_are_read_from_estate(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p["manifest"].pop("inputs"))
    json_put(prof.estate() / "extra_v1.json", [{"id": "extra", "kind": "within", "question": "Extra?", "expect": {}}])
    assert upfront.check(prof).findings == []
    json_put(prof.estate() / "extra_v1.json", [{"id": "extra"}])
    assert "F09" in rules(upfront.check(prof))


def test_declared_further_sets_and_implicit_page_sets(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: (p["manifest"]["inputs"]["sets"].append("extra_v1"),
                                          p["tests"]["sets"].append({"name": "extra", "version": "v1"})))
    question = [{"id": "extra", "kind": "within", "question": "Extra?", "expect": {}}]
    json_put(folder / "fixture/extra_v1.json", question)
    (folder / "units/new").mkdir(parents=True)
    json_put(prof.estate() / "golden_new_v1.json", question)
    edit(folder / "pack.yaml", lambda p: p["tests"]["sets"].append({"name": "golden_new", "version": "v1"}))
    result = upfront.check(prof)
    assert result.findings == []
    assert "fixture/extra_v1.json" in result.files
    assert result.ignored == []


def test_generator_reads_estate_sets_without_fixture(client):
    prof, folder, _ = client
    prof.estate().mkdir(parents=True)
    for name in ("golden", "heldout"):
        shutil.copyfile(folder / f"fixture/{name}.json", prof.estate() / f"{name}.json")
    edit(folder / "pack.yaml", lambda p: p["manifest"]["inputs"].update(estate={"generator": "sample"}))
    result = upfront.check(prof)
    assert result.findings == []
    assert result.fixture_sha256 is None
    assert set(result.files) == {"pack.yaml"}
    assert "clients/acme/fixture/schema.yaml" in result.ignored


def test_nested_fixture_and_content_changes(client):
    prof, folder, _ = client
    target = folder / "inputs/estate"
    target.parent.mkdir()
    (folder / "fixture").rename(target)
    edit(folder / "pack.yaml", lambda p: p["manifest"]["inputs"].update(estate={"fixture": "inputs/estate"}))
    before = upfront.check(prof)
    assert before.findings == []
    (target / ".ignored").write_text("ignored")
    assert upfront.check(prof).inputs_sha256 == before.inputs_sha256
    path = target / "data/sales/sale.csv"
    path.write_text(path.read_text().replace("1", "2"))
    after = upfront.check(prof)
    assert after.inputs_sha256 != before.inputs_sha256
    assert after.fixture_sha256 != before.fixture_sha256


def test_empty_baseline_sets_have_nothing_comparable(client):
    prof, folder, _ = client
    for name, content in (("golden", {"version": "v2", "items": []}), ("heldout", [])):
        json_put(folder / f"fixture/{name}.json", content)
    prof = saved_baseline(prof, folder)
    assert rules(upfront.check(prof)) == ["B02"]


@pytest.mark.parametrize("text", ["not JSON", "[]", '{"sets": []}'])
def test_unreadable_baseline_explains_fix(client, text):
    prof, folder, _ = client
    path = folder / "baseline.json"
    path.write_text(text)
    result = upfront.check(replace(prof, baseline=path))
    assert rules(result) == ["B02"]
    assert result.findings[0].message.endswith(upfront.BASELINE_FIX)


def test_direct_table_column_and_sql_references_all_checked(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p.update(
        entities=[{"table": "sales.sale", "key": ["unknown"]}],
        period_bindings=[{"table": "sales.sale", "column": "unknown"}],
        policies={"scd2": [{"table": "sales.sale", "valid_from": "unknown"}]},
        measures=[{"column": "sales.sale.id", "expression": "sum(sale.unknown)"}],
        filters=[{"table": "sales.sale", "predicate": "sale.unknown = 1"}],
        joins=[{"from": "sales.sale", "to": "sales.unknown", "condition": "sale.id = unknown.id"}]))
    result = upfront.check(prof)
    assert rules(result) == ["F03"] * 6
    assert len({f.path for f in result.findings}) == 6


def test_malformed_declarations_never_use_legacy_inputs(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p["manifest"].update(inputs={"estate": {}, "sets": []}))
    json_put(prof.estate() / "stale_v1.json", "invalid set")
    result = upfront.check(prof)
    assert rules(result) == ["I00", "I00"]
    assert result.suggestion is None
    assert result.fixture_sha256 is None


def test_actual_estate_limits_and_types_need_no_warehouse_io(client, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
    from kge.adapters.warehouse.duckdb import DuckDBEstate

    prof, folder, _ = client
    edit(folder / "fixture/schema.yaml", lambda s: s["warehouses"]["sales"]["tables"][0]["columns"][0].update(
        type="INTERVAL", description="x" * 1025))
    local = DuckDBEstate(tmp_path / "never-created")
    monkeypatch.setattr(upfront, "estate_of", lambda p: local)
    before = snapshot(tmp_path)
    assert upfront.check(prof).findings == []
    remote = BigQueryEstate("acme", loader=SimpleNamespace(dataset_prefix="test"))
    monkeypatch.setattr(upfront, "estate_of", lambda p: remote)
    assert rules(upfront.check(prof)) == ["F04", "F05"]
    assert snapshot(tmp_path) == before
    assert not (tmp_path / "never-created").exists()
    assert remote.staged == {} and not remote._ready


def test_missing_schema_keeps_independent_findings(client, tmp_path):
    prof, folder, estate = client
    prof = saved_baseline(prof, folder)
    (folder / "fixture/schema.yaml").unlink()
    (folder / "fixture/golden.json").unlink()
    (folder / "fixture/heldout.json").unlink()
    edit(folder / "pack.yaml", lambda p: (p["manifest"].update(includes=["absent.yaml"]),
        p["manifest"]["inputs"].update(catalogue="catalogue.yaml"),
        p["tests"]["sets"].append({"name": "stray", "version": "v1"})))
    put(folder / "catalogue.yaml", {"format": 1, "units": ["sales"],
        "products": [{"id": "sales.sale", "unit": "sales", "table": "sale"}]})
    grants(prof, {"ann@example.com": ["unknown"]})
    before = snapshot(tmp_path)
    assert rules(upfront.check(prof)) == ["S01", "I01", "I02", "I03", "A01"]
    assert estate.types == []
    assert snapshot(tmp_path) == before
    put(folder / "catalogue.yaml", {"format": 1, "units": ["unknown"], "products": []})
    (folder / "fixture/docs/certified/guide.md").write_text("bad front matter")
    result = upfront.check(prof)
    assert {"F02", "F08"} <= set(rules(result))
    assert not {"F01", "F03", "B02"} & set(rules(result))


def test_estate_findings_use_relative_paths(client):
    prof, folder, _ = client
    edit(folder / "pack.yaml", lambda p: p["manifest"]["inputs"].update(estate={"generator": "sample"}))
    json_put(prof.estate() / "golden.json", {"version": "v2", "items": [{"id": "bad"}]})
    result = upfront.check(prof)
    assert [(f.rule, f.path) for f in result.findings] == [
        ("I01", "heldout.json:set"), ("F09", "golden.json:items[0].question"),
        ("F09", "golden.json:items[0].kind"), ("F09", "golden.json:items[0].expect")]


def test_baseline_role_survives_revocation_and_malformed_roles_are_findings(client):
    prof, folder, _ = client
    prof = saved_baseline(prof, folder)
    grants(prof, {})
    assert upfront.check(prof).findings == []
    edit(folder / "pack.yaml", lambda p: p.update(access=["malformed"]))
    assert {"S01", "B01"} <= set(rules(upfront.check(prof)))


def test_bigquery_label_and_key_limits_are_collected_before_io(client, monkeypatch):
    from types import SimpleNamespace
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate

    prof, folder, _ = client
    remote = BigQueryEstate("acme", loader=SimpleNamespace(dataset_prefix="test"))
    monkeypatch.setattr(upfront, "estate_of", lambda _: remote)

    def oversized(schema):
        house = schema["warehouses"].pop("sales")
        schema["warehouses"]["s" * 64] = house
        house["tables"][0]["columns"][0]["type"] = "DOUBLE"

    edit(folder / "fixture/schema.yaml", oversized)
    found = upfront.check(prof).findings
    assert any(f.rule == "F01" and "63" in f.message for f in found)
    assert any(f.rule == "F04" and "key" in f.message.lower() for f in found)
    assert remote.staged == {} and not remote._ready


def test_check_collects_missing_tmp_staging_failure_before_writes(client, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate

    prof, _, _ = client
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "graph")
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "1")
    monkeypatch.delenv("TMP_URL", raising=False)
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *args, **kwargs: None)
    estate = BigQueryEstate("acme", loader=SimpleNamespace(dataset_prefix="test"))
    monkeypatch.setattr(upfront, "estate_of", lambda _: estate)
    def no_write(*args, **kwargs):
        pytest.fail("upfront staging refusal must precede warehouse writes")
    monkeypatch.setattr(estate, "stage", no_write)
    monkeypatch.setattr(estate, "_ensure", no_write)
    before = snapshot(tmp_path)
    result = upfront.check(prof)
    assert [(finding.rule, finding.path) for finding in result.findings] == [("F11", "onboard:loads")]
    assert "TMP_URL" in result.findings[0].message
    assert snapshot(tmp_path) == before
    assert estate.staged == {} and not estate._ready
    monkeypatch.setattr(upfront, "staging", lambda ctx: [])
    assert upfront.check(prof).findings == []


@pytest.mark.parametrize("fixture", [True, False], ids=["fixture", "fixtureless"])
@pytest.mark.parametrize("source", ["estate", "live", "pending", "dictionary", "pending_dictionary"])
def test_staging_includes_unit_documents_and_dictionaries_before_any_writes(client, monkeypatch, tmp_path, fixture, source):
    from types import SimpleNamespace
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
    from kge.compiler.pack import read

    prof, folder, _ = client
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("TMP_URL", raising=False)
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *args, **kwargs: None)
    estate = BigQueryEstate("acme", loader=SimpleNamespace(dataset_prefix="test"))
    ctx = upfront.Context(prof, read(prof.clients, prof.client, lock=False), None,
                          folder / "fixture" if fixture else None, estate, None)
    planned = []
    real_preflight = upfront.preflight_loads
    monkeypatch.setattr(upfront, "preflight_loads", lambda client, loads, chunks: planned.append((loads, chunks)))
    assert upfront.staging(ctx) == []
    small = sum(len(chunk.encode()) + 5000 for chunk in planned[-1][1])
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", str(max(20000, small * 2)))
    text = "Additional unit material. " * 20000
    if source == "estate":
        path = prof.estate() / "docs/uncertified/live.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        json_put(prof.estate() / "documents.json", [{"doc_id": "LIVE", "unit": "extra", "shelf": "uncertified",
                                                    "title": "Live material", "path": "docs/uncertified/live.md"}])
    elif source in {"dictionary", "pending_dictionary"}:
        path = folder / "units/extra" / ("update/schema.sql" if source == "pending_dictionary" else "schema.sql")
        path.parent.mkdir(parents=True)
        text = "Additional unit material. " * 3000
        path.write_text("\n".join(f"CREATE TABLE ledger_{n} (id INTEGER PRIMARY KEY);\n"
                                   f"COMMENT ON TABLE ledger_{n} IS '{text[:900]}';" for n in range(90)))
    else:
        root = folder / "units/extra"
        if source == "pending":
            root /= "update"
        path = root / "semantic/docs/guide.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\ntitle: Unit material\nkind: policy\npublished_at: 2026-01-01\nreviewed_at: 2026-01-01T00:00:00Z\n---\n" + text)
    def no_write(*args, **kwargs):
        pytest.fail("staging planning must not write warehouse or catalogue state")
    monkeypatch.setattr(estate, "stage", no_write)
    monkeypatch.setattr(estate, "_ensure", no_write)
    before = snapshot(tmp_path)
    assert upfront.staging(ctx) == []
    assert sum(len(chunk.encode()) for chunk in planned[-1][1]) >= len(text.encode())
    monkeypatch.setattr(upfront, "preflight_loads", real_preflight)
    found = upfront.staging(ctx)
    assert [(item.rule, item.path) for item in found] == [("F11", "onboard:loads")]
    assert "TMP_URL" in found[0].message
    assert snapshot(tmp_path) == before
    assert estate.staged == {} and not estate._ready


@pytest.mark.parametrize("pending", [False, True], ids=["live-repair", "pending-update"])
def test_fixtureless_unit_csv_staging_is_checked_before_writes(client, monkeypatch, tmp_path, pending):
    from kge.compiler.pack import read
    prof, folder, estate = client
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "graph")
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "20000")
    monkeypatch.delenv("TMP_URL", raising=False)
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *args, **kwargs: None)
    root = folder / "units/extra" / ("update" if pending else "")
    path = root / "data/ledger.csv"
    path.parent.mkdir(parents=True)
    path.write_text("id,note\n1," + "x" * 30000 + "\n")
    ctx = upfront.Context(prof, read(prof.clients, prof.client, lock=False), None, None, estate, None)
    before = snapshot(tmp_path)
    found = upfront.staging(ctx)
    assert len(found) == 1 and found[0].rule == "F11" and "TMP_URL" in found[0].message
    assert snapshot(tmp_path) == before


def test_pending_glossary_names_are_included_in_every_planned_chunk(client, monkeypatch):
    from kge.compiler.pack import read
    prof, folder, estate = client
    put(folder / "units/extra/update/draft.yaml", {"items": [{"pack": {"glossary": [{"name": "Pending term"}]}}]})
    captured = []
    monkeypatch.setattr(upfront, "preflight_loads", lambda client, loads, chunks: captured.extend(chunks))
    ctx = upfront.Context(prof, read(prof.clients, prof.client, lock=False), None, folder / "fixture", estate, None)
    assert upfront.staging(ctx) == []
    assert captured and all("Pending term" in chunk for chunk in captured)


@pytest.mark.parametrize("fixture", [True, False], ids=["fixture", "fixtureless"])
@pytest.mark.parametrize("pending", [False, True], ids=["live", "pending"])
def test_staging_ignores_dotfiles_in_every_pack_scan(client, monkeypatch, fixture, pending):
    from kge.compiler.pack import read
    prof, folder, estate = client
    source = folder / "units/extra" / ("update" if pending else "")
    doc = source / "semantic/docs/guide.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\ntitle: Unit guide\nkind: policy\n---\nVisible unit document.\n")
    data = source / "data/ledger.csv"
    data.parent.mkdir()
    data.write_text("id,note\n1,Visible row\n")
    put(source / "draft.yaml", {"items": [{"pack": {"glossary": [{"name": "Visible term"}]}}]})
    planned = []
    monkeypatch.setattr(upfront, "preflight_loads", lambda client, loads, chunks: planned.append((loads, chunks)))
    ctx = upfront.Context(prof, read(prof.clients, prof.client, lock=False), None,
                          folder / "fixture" if fixture else None, estate, None)
    assert upfront.staging(ctx) == []
    before = planned[-1]
    assert before[0] and any("Visible unit document" in chunk and "Visible term" in chunk for chunk in before[1])
    for name in ("semantic/docs/._guide.md", "semantic/docs/.DS_Store", "semantic/docs/.cache/visible.md",
                 "data/._ledger.csv", "data/.DS_Store", ".cache/draft.yaml"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\xffMac metadata")
    for name in ("data/ledger.csv", "semantic/docs/guide.md", "schema.sql", "draft.yaml"):
        path = folder / "units/.cache" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\xffMac metadata")
    assert upfront.staging(ctx) == []
    assert planned[-1] == before


def test_staging_plans_local_embeddings_without_loading_a_model(monkeypatch):
    from kge.adapters import preflight_loads
    from kge.adapters.bigquery import BigQueryLoader
    from kge.adapters.model import embed
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda: None)
    def no_model(*args, **kwargs):
        pytest.fail("read-only staging planning must not initialize or download models")
    monkeypatch.setattr(embed.LocalEmbedder, "__init__", no_model)
    sizes = []
    monkeypatch.setattr(BigQueryLoader, "preflight_staging", lambda self, size: sizes.append(size))
    preflight_loads("acme", [], ["Document text"])
    assert len(sizes) == 1 and sizes[0] > embed.DIM * 26
    monkeypatch.setenv("EMBEDDING_PROVIDER", "invalid")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER must be hash, local or vertex"):
        preflight_loads("acme", [], ["Document text"])
