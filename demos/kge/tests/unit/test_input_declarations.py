"""Input declarations are explicit, confined to the pack and outside its hash."""
from dataclasses import FrozenInstanceError
import json

import pytest
import yaml

from kge.compiler import checks, lock
from kge.compiler.inputs import INPUTS, Declared, declared_files, declared_findings, ignored, read_declared, suggest
from kge.compiler.pack import read
from kge.pipeline.settings import ROOT


def write_pack(folder, inputs, **sections):
    folder.mkdir(parents=True, exist_ok=True)
    value = {"manifest": {INPUTS: inputs}, **sections}
    (folder / "pack.yaml").write_text(yaml.safe_dump(value))
    return folder


def fixture_inputs(**changes):
    return {"estate": {"fixture": "fixture"}, "sets": ["golden", "heldout"], **changes}


def put(folder, name, value="data"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def test_read_keeps_fixed_shape_and_declared_files_without_requiring_them(tmp_path):
    folder = write_pack(tmp_path / "client", fixture_inputs(catalogue="discovery/catalogue.yaml"))
    raw = yaml.safe_load((folder / "pack.yaml").read_text())
    raw["manifest"]["includes"] = ["terms.yaml", "sections/tests.yaml"]
    (folder / "pack.yaml").write_text(yaml.safe_dump(raw))
    declared = read_declared(folder)
    assert declared == Declared("fixture", None, "discovery/catalogue.yaml", ("golden", "heldout"),
                                ("terms.yaml", "sections/tests.yaml"))
    assert declared_findings(folder) == []
    assert declared_files(folder, declared) == ["pack.yaml", "terms.yaml", "sections/tests.yaml", "discovery/catalogue.yaml"]
    with pytest.raises(FrozenInstanceError):
        declared.fixture = "elsewhere"


@pytest.mark.parametrize("raw", ["", "[]", "null", "manifest: []", "manifest: {}", "[broken", "manifest: {includes: []}"])
def test_missing_or_unreadable_declarations_never_raise(tmp_path, raw):
    assert read_declared(tmp_path) is None
    (tmp_path / "pack.yaml").write_text(raw)
    assert read_declared(tmp_path) is None
    assert declared_findings(tmp_path) == []
    (tmp_path / "pack.yaml").write_bytes(b"\xff")
    assert read_declared(tmp_path) is None


@pytest.mark.parametrize(("value", "entry"), [
    (None, ""), ([], ""), ({}, ".estate"),
    (fixture_inputs(estate={}), ".estate"),
    (fixture_inputs(estate={"fixture": "fixture", "generator": "sample"}), ".estate"),
    (fixture_inputs(estate={"generator": "client"}), ".estate.generator"),
    (fixture_inputs(estate={"fixture": "../fixture"}), ".estate.fixture"),
    (fixture_inputs(estate={"fixture": "/fixture"}), ".estate.fixture"),
    (fixture_inputs(estate={"fixture": "."}), ".estate.fixture"),
    (fixture_inputs(estate={"fixture": "a/../fixture"}), ".estate.fixture"),
    (fixture_inputs(estate={"fixture": 42}), ".estate.fixture"),
    (fixture_inputs(catalogue="../catalogue.yaml"), ".catalogue"),
    (fixture_inputs(catalogue="catalogue.json"), ".catalogue"),
    (fixture_inputs(catalogue=["catalogue.yaml"]), ".catalogue"),
    (fixture_inputs(sets=[]), ".sets"), (fixture_inputs(sets="golden"), ".sets"),
    (fixture_inputs(sets=["golden", "golden"]), ".sets[1]"),
    (fixture_inputs(sets=["golden.json"]), ".sets[0]"),
    (fixture_inputs(sets=["../golden"]), ".sets[0]"),
    (fixture_inputs(sets=[{}]), ".sets[0]"),
    (fixture_inputs(other=True), ".other"),
])
def test_malformed_declarations_have_i00_paths_and_no_declared_value(tmp_path, value, entry):
    write_pack(tmp_path, value)
    findings = declared_findings(tmp_path)
    assert findings and all(f.rule == "I00" for f in findings)
    assert f"pack.yaml:manifest.inputs{entry}" in {f.path for f in findings}
    assert read_declared(tmp_path) is None


def test_every_malformed_field_is_reported_together(tmp_path):
    write_pack(tmp_path, {"estate": {"generator": "other"}, "catalogue": "/bad.yaml", "sets": [None, "a", "a"], "unknown": 0})
    assert {f.path for f in declared_findings(tmp_path)} == {
        "pack.yaml:manifest.inputs.estate.generator", "pack.yaml:manifest.inputs.catalogue",
        "pack.yaml:manifest.inputs.sets[0]", "pack.yaml:manifest.inputs.sets[2]", "pack.yaml:manifest.inputs.unknown"}


@pytest.mark.parametrize("key", ["fixture", "catalogue"])
def test_paths_cannot_escape_through_symlinks(tmp_path, key):
    folder = tmp_path / "client"
    folder.mkdir()
    (folder / "outside").symlink_to(tmp_path, target_is_directory=True)
    value = fixture_inputs(estate={"fixture": "outside/fixture"}) if key == "fixture" else fixture_inputs(catalogue="outside/catalogue.yaml")
    write_pack(folder, value)
    assert read_declared(folder) is None
    assert len(declared_findings(folder)) == 1


def test_ignored_files_exclude_owned_paths_declared_fixture_and_live_unit_sets(tmp_path):
    folder, estate = tmp_path / "client", tmp_path / "estate"
    write_pack(folder, fixture_inputs(catalogue="descriptions.yaml"))
    for name in ("pack.lock", "baselines/one.json", "versions/hash/pack.yaml", "proposals/one.yaml",
                 "units/new/draft.yaml", "onboard/job.json", ".DS_Store", ".hidden/file.yaml", "nested/.hidden",
                 "fixture/schema.yaml", "fixture/data/unit/table.csv", "descriptions.yaml"):
        put(folder, name)
    for name in ("catalogue.yaml", "old-include.yaml", "other/data.csv", "fixture-old/schema.yaml"):
        put(folder, name)
    for name in ("golden", "heldout", "golden_new_v1", "team_new_v1", "heldout_new_v1", "stray_v2"):
        put(estate, name + ".json", "[]")
    put(estate, "extra.json", json.dumps({"version": "v2", "items": [{"id": "q", "question": "Why?"}]}))
    put(estate, "empty.json", json.dumps({"version": "v2", "items": []}))
    put(estate, "documents.json", json.dumps([{"id": "doc", "text": "text"}]))
    put(estate, "summary.json", "{}")
    put(estate, ".hidden_v1.json", "[]")
    before = {str(p): p.read_bytes() for base in (folder, estate) for p in base.rglob("*") if p.is_file()}
    assert ignored(folder, estate, read_declared(folder)) == [
        "clients/client/catalogue.yaml", "clients/client/fixture-old/schema.yaml", "clients/client/old-include.yaml",
        "clients/client/other/data.csv", "estate/empty.json", "estate/extra.json", "estate/stray_v2.json"]
    assert before == {str(p): p.read_bytes() for base in (folder, estate) for p in base.rglob("*") if p.is_file()}


def test_declared_includes_are_not_ignored(tmp_path):
    put(tmp_path, "pack.yaml", "manifest:\n  includes: [parts/terms.yaml]\n  inputs:\n    estate: {generator: sample}\n    sets: [golden]\n")
    put(tmp_path, "parts/terms.yaml", "glossary: []")
    assert ignored(tmp_path, tmp_path / "absent", read_declared(tmp_path)) == []


def test_suggestion_matches_legacy_discovery_and_is_a_manifest_child(tmp_path):
    folder, estate = tmp_path / "client", tmp_path / "estate"
    put(folder, "pack.yaml", "manifest: {includes: [tests.yaml]}\n")
    put(folder, "tests.yaml", "tests: {sets: [{name: golden, version: v2}]}\n")
    put(folder, "fixture/schema.yaml")
    put(folder, "catalogue.yaml")
    put(estate, "heldout.json", "[]")
    put(estate, "further_v1.json", "[]")
    put(estate, "summary.json", "{}")
    value = yaml.safe_load(suggest(folder, estate))
    assert value == {"inputs": {"estate": {"fixture": "fixture"}, "catalogue": "catalogue.yaml", "sets": ["golden", "further_v1"]}}
    assert read_declared(folder) is None
    assert yaml.safe_load(suggest(tmp_path / "missing", estate))["inputs"] == {
        "estate": {"generator": "sample"}, "sets": ["golden", "heldout", "further_v1"]}


@pytest.mark.parametrize(("client", "expected", "fixture", "sets"), [
    ("meridian", "e2d00fb2432913ff53a1472d9d7df0bd373d85a0e4b0236fbd52579fb1d1bc66", "fixture",
     ("golden", "heldout", "golden_wealth_v1", "sage_v1", "golden_figures_v1", "golden_documents_v1", "heldout_documents_v1")),
    ("fellmoor", "9035322f9fad26b74a9ad7e59e346a327814c12bbf68c90d85e2fdd0327bd94c", "fixture", ("golden", "heldout")),
    ("thornbury", "b1a273bb5352e11e0e18e4775de3db0975acd15e0830869f362730ad00a74e82", "fixture", ("golden",)),
])
def test_committed_packs_declare_their_inputs_and_keep_their_original_hash(client, expected, fixture, sets):
    pack = read(ROOT / "clients", client)
    declaration = read_declared(pack.folder)
    assert declaration.fixture == fixture and declaration.sets == sets
    assert declaration.generator == ("sample" if fixture is None else None)
    assert declaration.catalogue == ("catalogue.yaml" if client == "meridian" else None)
    assert declared_findings(pack.folder) == []
    assert not [f for f in checks.sections(pack) if f.path.startswith("pack.yaml:manifest")]
    assert pack.hash == expected
    pack.data["manifest"].pop("inputs")
    assert pack.hash == expected


def test_input_changes_are_outside_hash_but_model_changes_are_not(tmp_path):
    folder = write_pack(tmp_path / "client", fixture_inputs(), access={"roles": [{"id": "reader"}]})
    pack = read(folder.parent, folder.name, lock=False)
    before = pack.hash
    pack.data["manifest"]["inputs"] = fixture_inputs(estate={"fixture": "replacement"}, sets=["golden"])
    assert pack.hash == before
    pack.data["access"]["roles"][0]["id"] = "other"
    assert pack.hash != before


def test_ignored_files_do_not_change_the_hash(tmp_path):
    folder = write_pack(tmp_path / "client", fixture_inputs())
    before = read(folder.parent, folder.name, lock=False).hash
    put(folder, "catalogue.yaml", "[invalid YAML")
    put(folder, "stray.yaml", "access: {roles: [{id: unbound}]}")
    assert read(folder.parent, folder.name, lock=False).hash == before
    assert ignored(folder, tmp_path / "estate", read_declared(folder)) == [
        "clients/client/catalogue.yaml", "clients/client/stray.yaml"]


def test_compiler_check_reports_declaration_syntax(tmp_path):
    folder = write_pack(tmp_path / "client", fixture_inputs(sets=[]))
    pack = read(folder.parent, folder.name)
    assert any(f.rule == "I00" and f.path == "pack.yaml:manifest.inputs.sets"
               for f in checks.check(pack, {"node_types": {}, "edge_types": {}}, "0.4.0", set()))


@pytest.mark.parametrize("estate", [{"fixture": "fixture"}, {"generator": "sample"}])
def test_uploaded_people_are_never_reported_as_ignored(tmp_path, estate):
    folder = write_pack(tmp_path / "client", fixture_inputs(estate=estate))
    put(folder, "people.yaml", "people: []")
    put(folder, "stray.yaml")
    assert ignored(folder, tmp_path / "estate", read_declared(folder)) == ["clients/client/stray.yaml"]


def test_lock_refresh_reports_each_declaration_finding_once(tmp_path, monkeypatch):
    import shutil
    from kge.compiler import catalogue

    folder = tmp_path / "meridian"
    shutil.copytree(ROOT / "clients/meridian", folder)
    path = folder / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["manifest"]["inputs"]["sets"] = []
    data["manifest"]["inputs"]["estate"] = {"generator": "invalid"}
    path.write_text(yaml.safe_dump(data))

    def unexpected_catalogue_read(pack):
        pytest.fail("Malformed declarations must stop catalogue reading")

    monkeypatch.setattr(catalogue, "read", unexpected_catalogue_read)
    frozen = json.loads((folder / "pack.lock").read_text())
    findings = lock.refresh_findings(tmp_path, "meridian", frozen)
    expected = [str(f) for f in declared_findings(folder)]
    assert len(expected) == 2
    assert [f for f in findings if f.startswith("I00 ")] == expected
    assert not any(f.startswith("S01 ") for f in findings)
