"""GET /golden names, under each set's golden20, the showcase questions the client's pack declares (tests.showcase), so
the portal's "Try one of these" list is the pack's own (issue #620, after #590). Synthetic packs and sets only: a set's
items, a pack's tests section, and S38, the compiler's check of the declaration."""
from __future__ import annotations

import json
import shutil

import pytest
from pathlib import Path
from types import SimpleNamespace

import yaml

from kge.compiler import checks
from kge.compiler import pack as packs
from kge.compiler.compile import VERSION
from kge.compiler.emit import FILLS
from kge.compiler.pack import Pack
from kge.eval import sets
from kge.ports import StoreError
from kge.services.ask.engine import showcase as showcases
from kge.services.quality import routes, service

DEMO = Path(__file__).resolve().parents[2]


def item(sid: str, question: str, **extra) -> dict:
    return {"id": sid, "kind": "within", "question": question, "expect": {"unit": "wealth"}, **extra}


def estate(tmp_path):
    folder = tmp_path / "estate"
    folder.mkdir(exist_ok=True)
    (folder / "golden.json").write_text(json.dumps([item("G001", "older golden?")]))
    (folder / "sage_v1.json").write_text(json.dumps({"version": "v1", "items": [
        item("S01", "sage, not shown?"),
        item("S06", "shown first?"),
        item("S06-a", "shown first, reworded?", variant_of="S06"),
        item("S07", "shown second?"),
    ]}))
    (folder / "recent_v1.json").write_text(json.dumps({"version": "v2", "items": [
        item("R01", "net new assets in Q1 2026?"),
        item("R02", "net new assets in March 2026?"),
    ]}))
    return folder


TESTS = {"sets": [{"name": "golden", "version": "v3"},
                  {"name": "sage", "version": "v1", "items": ["S01", "S06", "S06-a", "S07"]},
                  {"name": "recent", "version": "v2", "items": ["R01", "R02"]}]}


def profile(tmp_path, monkeypatch, tests: dict | None, pack: bool = True, remote=None):
    if remote is None:
        monkeypatch.delenv("STORE_URL", raising=False)
    else:
        monkeypatch.setenv("STORE_URL", str(remote))
    folder = (remote or tmp_path) / "clients" / "acme"
    folder.mkdir(parents=True, exist_ok=True)
    (tmp_path / "clients" / "acme").mkdir(parents=True, exist_ok=True)
    if pack:
        (folder / "pack.yaml").write_text(yaml.safe_dump({"manifest": {"client": "acme"}, **({"tests": tests} if tests else {})}))
    held = estate(tmp_path)
    return SimpleNamespace(baseline=None, id="p", client="acme", clients=tmp_path / "clients", run_dir=lambda: tmp_path / "run",
                           estate=lambda: held)


def shown(prof) -> dict[str, list[str]]:
    got = service.golden(prof, SimpleNamespace(UNIT_ORDER=["wealth"]), "graph")
    assert all("showcase" not in s for s in got)
    return {s["name"]: s["golden20"] for s in got}


def test_a_set_names_the_questions_of_the_items_a_showcase_declares_in_its_order(tmp_path):
    got = {s["name"]: s for s in sets.golden(estate(tmp_path), showcased={"set": "sage", "version": "v1", "items": ["S07", "S06"]})}
    assert got["sage"]["showcase"] == ["shown second?", "shown first?"]
    assert got["golden"]["showcase"] == [] and got["recent"]["showcase"] == []
    assert all(s["showcase"] == [] for s in sets.golden(estate(tmp_path))), "no declaration shows none"


def test_a_set_file_at_another_version_than_the_pack_names_shows_none(tmp_path, monkeypatch):
    tests = {"sets": [{**s, "version": "v9"} if s["name"] == "sage" else s for s in TESTS["sets"]],
             "showcase": {"set": "sage", "items": ["S06", "S07"]}}
    assert shown(profile(tmp_path, monkeypatch, tests)) == {"golden": [], "sage": [], "recent": []}


def test_get_golden_serves_exactly_the_items_the_pack_declares(tmp_path, monkeypatch):
    got = shown(profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "sage", "items": ["S06", "S07"]}}))
    assert got == {"golden": [], "sage": ["shown first?", "shown second?"], "recent": []}


def test_a_pack_naming_another_set_with_other_periods_serves_that_set_s_items(tmp_path, monkeypatch):
    got = shown(profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "recent", "items": ["R02", "R01"]}}))
    assert got == {"golden": [], "sage": [], "recent": ["net new assets in March 2026?", "net new assets in Q1 2026?"]}


def test_a_pack_with_no_declaration_serves_none(tmp_path, monkeypatch):
    assert shown(profile(tmp_path, monkeypatch, TESTS)) == {"golden": [], "sage": [], "recent": []}


def test_no_pack_to_read_serves_none(tmp_path, monkeypatch):
    assert shown(profile(tmp_path, monkeypatch, None, pack=False)) == {"golden": [], "sage": [], "recent": []}


def test_the_pack_is_read_on_each_call_and_parsed_once_per_content(tmp_path, monkeypatch):
    prof = profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "sage", "items": ["S06"]}})
    parsed = []
    real = showcases.yaml.safe_load

    def counted(data):
        parsed.append(data)
        return real(data)
    monkeypatch.setattr(showcases.yaml, "safe_load", counted)
    monkeypatch.setattr(showcases, "_parsed", {})
    for _ in range(3):
        assert showcases.showcase(prof) == {"set": "sage", "version": "v1", "items": ["S06"]}
    assert len(parsed) == 1
    path = tmp_path / "clients" / "acme" / "pack.yaml"
    path.write_text(yaml.safe_dump({"tests": {**TESTS, "showcase": {"set": "recent", "items": ["R01"]}}}))
    assert showcases.showcase(prof) == {"set": "recent", "version": "v2", "items": ["R01"]}, "an onboarded pack edit shows"
    assert len(parsed) == 2


def test_a_parse_another_request_clears_meanwhile_still_answers(tmp_path, monkeypatch):
    prof = profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "sage", "items": ["S06"]}})

    class Forgetful(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            self.clear()
    monkeypatch.setattr(showcases, "_parsed", Forgetful())
    assert showcases.showcase(prof) == {"set": "sage", "version": "v1", "items": ["S06"]}


def test_the_showcase_is_read_from_the_pack_store_where_it_is_remote(tmp_path, monkeypatch):
    remote = tmp_path / "bucket"
    prof = profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "recent", "items": ["R01"]}}, remote=remote)
    assert not (tmp_path / "clients" / "acme" / "pack.yaml").exists()
    assert shown(prof) == {"golden": [], "sage": [], "recent": ["net new assets in Q1 2026?"]}


def test_a_pack_store_that_cannot_be_read_is_a_503_never_none(tmp_path, monkeypatch):
    prof = profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "sage", "items": ["S06"]}})

    class Broken:
        def read(self, name):
            raise StoreError("gs://bucket/clients/acme/pack.yaml: unavailable")
    monkeypatch.setattr(showcases.packstore, "for_profile", lambda p: Broken())
    with pytest.raises(StoreError):
        showcases.showcase(prof)
    monkeypatch.setattr(routes.served, "engine", lambda: SimpleNamespace(UNIT_ORDER=["wealth"]), raising=False)
    monkeypatch.setattr(routes.served, "PROFILE", prof, raising=False)
    monkeypatch.setattr(routes.served, "DEFAULT_ARM", "graph", raising=False)
    monkeypatch.setattr(routes.identity, "readable_units", lambda eng, who: [])
    monkeypatch.setattr(routes.service, "suggestions_meta", lambda *a: {"unreadable_questions": set()})
    with pytest.raises(routes.HTTPException) as refused:
        routes.golden(who="analyst")
    assert refused.value.status_code == 503


def test_a_pack_that_is_no_yaml_or_names_an_unknown_set_shows_none():
    assert showcases.declared({"tests": {**TESTS, "showcase": {"set": "nowhere", "items": ["S06"]}}}) is None
    assert showcases.declared({"tests": TESTS}) is None and showcases.declared(None) is None


def test_get_golden_leaves_a_question_on_a_unit_the_caller_may_not_read_out_of_golden20(monkeypatch, tmp_path):
    prof = profile(tmp_path, monkeypatch, {**TESTS, "showcase": {"set": "sage", "items": ["S06", "S07"]}})
    monkeypatch.setattr(routes.served, "engine", lambda: SimpleNamespace(UNIT_ORDER=["wealth"]), raising=False)
    monkeypatch.setattr(routes.served, "PROFILE", prof, raising=False)
    monkeypatch.setattr(routes.served, "DEFAULT_ARM", "graph", raising=False)
    monkeypatch.setattr(routes.served, "SERVE_HASH", None, raising=False)
    monkeypatch.setattr(routes.identity, "readable_units", lambda eng, who: [])
    monkeypatch.setattr(routes.service, "suggestions_meta", lambda *a: {"unreadable_questions": {"shown second?"}})
    sage = next(s for s in routes.golden(who="analyst")["sets"] if s["name"] == "sage")
    assert sage["golden20"] == ["shown first?"] and "shown second?" not in sage["questions"]


def s38(tmp_path, showcase) -> list[tuple[str, str, str]]:
    pack = Pack("acme", tmp_path, data={"tests": {**TESTS, "showcase": showcase}})
    return [(f.rule, f.path.partition(":")[2], f.message) for f in checks.showcase(pack)]


def test_s38_passes_a_declaration_of_items_a_named_set_lists(tmp_path):
    assert s38(tmp_path, {"set": "recent", "items": ["R01", "R02"]}) == []


def test_s38_refuses_a_set_the_pack_does_not_name(tmp_path):
    assert s38(tmp_path, {"set": "nowhere", "items": ["S06"]}) == [
        ("S38", "tests.showcase.set", "nowhere is not a question set the pack names (tests.sets)")]


def test_s38_refuses_an_item_the_set_does_not_list_and_an_item_given_twice(tmp_path):
    assert s38(tmp_path, {"set": "sage", "items": ["S06", "S99", "S06"]}) == [
        ("S38", "tests.showcase.items[1]", "S99 is not an item of sage v1"),
        ("S38", "tests.showcase.items[2]", "S06 is listed already")]


def test_s38_refuses_a_held_out_set(tmp_path):
    pack = Pack("acme", tmp_path, data={"tests": {"sets": [*TESTS["sets"], {"name": "heldout_wealth", "version": "v1", "items": ["H01"]}],
                                                  "showcase": {"set": "heldout_wealth", "items": ["H01"]}}})
    assert [(f.rule, f.message) for f in checks.showcase(pack)] == [
        ("S38", "heldout_wealth is a held-out set: its questions are never shown, so none of them is a showcase question")]


def test_s38_refuses_a_set_that_lists_no_items_and_a_declaration_of_none(tmp_path):
    assert s38(tmp_path, {"set": "golden", "items": ["G001"]}) == [
        ("S38", "tests.showcase.set", "golden names no items in tests.sets: the showcase questions are items a named set "
                                      "lists, by id")]
    assert s38(tmp_path, {"set": "sage", "items": []}) == [
        ("S38", "tests.showcase.items", "names no item: a showcase lists the items it shows, by id")]


def test_s01_refuses_a_showcase_that_names_no_set(tmp_path):
    pack = Pack("acme", tmp_path, data={"tests": {**TESTS, "showcase": {"items": ["S06"]}}})
    assert any(f.rule == "S01" and "tests.showcase" in f.path for f in checks.sections(pack))


def checked(tmp_path, showcase: dict) -> list[tuple[str, str, str]]:
    """Every finding of checks.check on a scratch copy of the Thornbury sample pack, its golden set listing two items, with
    showcase declared."""
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / "thornbury", clients / "thornbury")
    path = clients / "thornbury" / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["tests"] = {"sets": [{"name": "golden", "version": "v1", "items": ["G001", "G002"]}], "showcase": showcase}
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    got = checks.check(packs.read(clients, "thornbury"), packs.meta_model(DEMO / "meta-model.yaml"), VERSION, FILLS)
    return [(f.rule, f.path.partition(":")[2], f.message) for f in got]


def test_the_compiler_runs_s38_on_a_pack_that_otherwise_passes(tmp_path):
    assert checked(tmp_path / "passes", {"set": "golden", "items": ["G002", "G001"]}) == []
    assert checked(tmp_path / "refused", {"set": "golden", "items": ["G003"]}) == [
        ("S38", "tests.showcase.items[0]", "G003 is not an item of golden v1")]


def test_the_portal_s_showcase_fixture_is_the_sample_pack_s_showcase_questions():
    """The portal's offline run of Cerebro on every showcase question (portal/server/test/cerebro-golden.test.js, issues
    #697 and #700) reads them from a fixture: the questions of the items the sample pack declares, in its order."""
    from kge.estate.golden_sage import golden_20
    showcased = yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())["tests"]["showcase"]
    asked = {item.id: item.question for item, *_ in golden_20()}
    fixture = json.loads((DEMO / "portal" / "server" / "test" / "fixtures" / "cerebro-showcase-questions.json").read_text())
    assert fixture == {"set": showcased["set"], "items": showcased["items"], "questions": [asked[i] for i in showcased["items"]]}
