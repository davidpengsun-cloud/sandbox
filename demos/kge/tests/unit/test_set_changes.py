"""Question sets beside a baseline: a set at the version the baseline ran is compared on every score; none declares
its changes, and git history is their record."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from kge.eval import harness, margin, report, sets
from tests.unit.test_heldout_sets import _rec


def item(i, expect=None):
    return {"id": i, "kind": "within", "question": i, "expect": expect or {}}


def write_set(path, ids, *, version="v1", extra=None):
    data = {"version": version, "items": [item(i) for i in ids], **(extra or {})}
    path.write_text(json.dumps(data))
    return data


def metadata(path):
    data = sets.read(path)
    return {"version": data["version"], "file": path.name, "size": len(data["items"]), "items": len(data["items"]),
            "item_sha256": sets.item_hashes(data["items"])}


def test_a_set_is_read_without_a_change_declaration_and_an_old_one_is_ignored(tmp_path):
    path = tmp_path / "golden.json"
    write_set(path, ["A", "B"], version="v2")
    assert sets.read(path) == {"version": "v2", "items": [item("A"), item("B")]}
    old = {"version": "v1", "items": ["A"], "sha256": "not a digest", "why": ""}
    write_set(path, ["A", "B"], version="v2", extra={"changes_from": old})
    assert sets.read(path) == {"version": "v2", "items": [item("A"), item("B")]}


def test_a_set_is_the_baseline_s_at_the_same_version_and_items_run():
    then = {"version": "v1", "items": 3, "size": 3, "file": "golden.json"}
    assert report.same_set(then, {**then})
    for now in ({**then, "version": "v2"}, {**then, "items": 2}, {k: v for k, v in then.items() if k != "version"}, None):
        assert not report.same_set(then, now), now


def declared_pack(tmp_path, names=("golden",)):
    folder = tmp_path / "client"
    folder.mkdir()
    pack = folder / "pack.yaml"
    pack.write_text(yaml.safe_dump({"manifest": {"inputs": {"estate": {"fixture": "fixture"}, "sets": list(names)}},
                                   "tests": {"sets": [{"name": "golden", "version": "v2"}]}}))
    return pack


def test_declared_sets_exclude_stale_files_and_include_live_unit_sets(tmp_path):
    pack = declared_pack(tmp_path, ("golden", "heldout", "custom"))
    unit = pack.parent / "units" / "leasing"
    unit.mkdir(parents=True)
    (pack.parent / "units" / ".hidden").mkdir()
    names = ["golden", "heldout", "custom", "golden_leasing_v1", "team_leasing_v1", "heldout_leasing_v1"]
    assert sets.declared_files(pack) == names
    assert sets.declared_files(pack.parent) == names
    estate = tmp_path / "estate"
    estate.mkdir()
    for name in names + ["stale_v1"]:
        write_set(estate / f"{name}.json", [name], version="v2" if name == "golden" else "v1")
    got = harness.choose_sets("all", estate, sets.declared(pack), pack)
    assert got == names
    assert "stale_v1" not in harness.describe_sets(estate, harness.load(estate, got, None))
    with pytest.raises(SystemExit, match="manifest.inputs.sets"):
        harness.choose_sets("stale_v1", estate, sets.declared(pack), pack)
    (estate / "custom.json").unlink()
    with pytest.raises(SystemExit, match="declared set custom.*missing"):
        harness.choose_sets("all", estate, sets.declared(pack), pack)


@pytest.mark.parametrize("explicit_team", [False, True])
def test_live_unit_without_team_set(tmp_path, explicit_team):
    names = ("golden", "team_leasing_v1") if explicit_team else ("golden",)
    pack = declared_pack(tmp_path, names)
    (pack.parent / "units" / "leasing").mkdir(parents=True)
    estate = tmp_path / "estate"
    estate.mkdir()
    for name in ("golden", "golden_leasing_v1", "heldout_leasing_v1"):
        write_set(estate / f"{name}.json", [name], version="v2" if name == "golden" else "v1")
    if explicit_team:
        with pytest.raises(SystemExit, match="declared set team_leasing_v1.*missing"):
            harness.choose_sets("all", estate, sets.declared(pack), pack)
    else:
        assert harness.choose_sets("all", estate, sets.declared(pack), pack) == [
            "golden", "golden_leasing_v1", "heldout_leasing_v1"]
    with pytest.raises(SystemExit, match="declared set team_leasing_v1.*missing"):
        harness.choose_sets("team_leasing_v1", estate, sets.declared(pack), pack)


def test_older_packs_keep_file_discovery(tmp_path):
    pack = tmp_path / "pack.yaml"
    pack.write_text("manifest: {}\n")
    assert sets.declared_files(pack) is None
    for name in ("golden", "heldout", "stale_v1"):
        write_set(tmp_path / f"{name}.json", [name], version="v2" if name == "golden" else "v1")
    assert harness.choose_sets("all", tmp_path, {}, pack) == ["golden", "heldout", "stale_v1"]


def records(ids=("A", "B", "C"), repeats=2):
    out = []
    for n in range(1, repeats + 1):
        for arm in report.MARGIN_ARMS:
            for i in ids:
                r = _rec(arm, i, "golden")
                r["repeat"] = n
                r["measures"]["citations"] = {"cited": 3 if i == "A" else 1, "tier_correct": 3 if i == "A" else 0}
                r["measures"]["numbers"] = {"checked": True, "caught": n, "shown": 0}
                out.append(r)
    return out


def manifest(repeats=2):
    return {"run_id": "r", "started_at": "today", "finished_at": "today", "principal": "reader", "profile": "client",
            "repeats": repeats, "arms": [{"arm": a, "mode": "rules", "side": a, "config": a} for a in report.MARGIN_ARMS],
            "sets": {"golden": {"version": "v1", "items": 3, "size": 3, "file": "golden.json",
                                "item_sha256": sets.item_hashes([item(i) for i in "ABC"])}}}


def test_baseline_keeps_every_score_and_the_baselines_set_is_compared_on_each():
    original = records()
    b = report.baseline(manifest(), original, "before")
    assert set(b["items"]["golden"]["A"]["control"]) == set(report.MARGIN)
    assert b["items"]["golden"]["A"]["control"]["tier_correct"] == 1
    assert b["items"]["golden"]["A"]["control"]["numbers_caught"] == 1.5
    now = records()
    for r in now:
        if r["arm"] == "treatment" and r["id"] == "A":
            r["measures"]["citations"]["tier_correct"] = 0
    got = report.margin_beside(manifest(), report.margins(now), b)
    assert set(r["score"] for r in got["compared"]["golden"]) == set(report.MARGIN) and got["changed"] == {}
    assert any(r["kind"] == "all" and r["score"] == "tier_correct" for r in got["fell"])
    expected = report.margins(original)["golden"]
    assert all(r["then"] == expected[r["kind"]][r["score"]] for r in got["compared"]["golden"])
    text = report.markdown(manifest(), now, b)
    assert "The same golden set as the baseline's (v1)." in text and "Tier-correct citations" in text


@pytest.mark.parametrize("score", list(report.MARGIN))
def test_each_score_is_compared_on_the_baselines_set(score):
    original = records()
    b = report.baseline(manifest(), original, "before")
    now = records()
    for r in now:
        if r["arm"] != "treatment":
            continue
        m = r["measures"]
        if score == "pass_rate":
            r["ok"] = False
        elif score == "figure_correct":
            m["figure_ok"] = False
        elif score == "behaviour_class":
            m["behaviour"]["class"] = False
        elif score == "gate_first_try":
            m["gate"]["first_try"] = False
        elif score == "tier_correct":
            m["citations"]["tier_correct"] = 0
        else:
            m["numbers"][score.removeprefix("numbers_")] += 5
    got = report.margin_beside(manifest(), report.margins(now), b)
    assert any(r["score"] == score and r["kind"] == "all" for r in got["fell"])


def test_missing_scores_are_a_regression():
    b = report.baseline(manifest(), records(), "before")
    now = records()
    for r in now:
        r["measures"]["citations"] = {"cited": 0, "tier_correct": 0}
    got = report.margin_beside(manifest(), report.margins(now), b)
    assert any(r["score"] == "tier_correct" and r["now"] is None for r in got["fell"])


def margin_setup(tmp_path, monkeypatch, *, roles=("reader",)):
    m = manifest()
    b = report.baseline(m, records(), "before")
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps(b))
    prof = SimpleNamespace(id="client", baseline=base, pin=lambda: tmp_path, estate=lambda: tmp_path,
                           paths=lambda: {"pack": tmp_path / "pack.yaml"}, current=lambda: None, model_hash=lambda: "abc",
                           asking=lambda: "reader")
    prof.at = lambda model_hash: prof
    monkeypatch.setattr(margin, "resolve", lambda _: prof)
    monkeypatch.setattr(margin, "thresholds", lambda _: {})
    monkeypatch.setattr(margin, "drafted", lambda _: None)
    monkeypatch.setattr(margin, "missing", lambda *a: None)
    monkeypatch.setattr(harness, "choose_sets", lambda *a: ["golden"])
    monkeypatch.setattr(margin, "candidate_roles", lambda *_: {role: {} for role in roles})
    engine = SimpleNamespace(g=SimpleNamespace(principal_ids=lambda: pytest.fail("the gate must not read people")), close=lambda: None)
    monkeypatch.setattr(margin.Engine, "candidate", lambda _: engine)
    return b, prof, engine


def test_missing_baseline_role_names_the_fix_before_asking(tmp_path, monkeypatch, capsys):
    margin_setup(tmp_path, monkeypatch, roles=())
    assert margin.check([]) == 2
    err = capsys.readouterr().err
    assert "B01 the role it asked as no longer exists: reader" in err
    assert margin.BASELINE_FIX in err


def test_other_graph_store_names_the_baseline_fix(tmp_path, monkeypatch, capsys):
    b, prof, _ = margin_setup(tmp_path, monkeypatch)
    b["graph"] = {"store": {"engine": "previous"}}
    prof.baseline.write_text(json.dumps(b))
    monkeypatch.setattr(margin, "store_info", lambda: {"engine": "current"})
    assert margin.check([]) == 2
    err = capsys.readouterr().err
    assert "B02 the baseline has nothing comparable" in err and margin.BASELINE_FIX in err


def test_no_comparable_set_names_the_fix_and_never_marks_gate(tmp_path, monkeypatch, capsys):
    _, prof, engine = margin_setup(tmp_path, monkeypatch)
    engine.profile = SimpleNamespace(graph_path=lambda: tmp_path / "graph", model_hash=lambda: "abc")
    monkeypatch.setattr(margin, "chunks", lambda _: True)
    monkeypatch.setattr(margin, "EvidenceArms", lambda *a, **k: None)
    monkeypatch.setattr(harness, "run", lambda *a, **k: tmp_path)
    m = manifest()
    m["sets"] = {"new_v1": m["sets"]["golden"]}
    recs = [{**r, "set": "new_v1"} for r in records()]
    monkeypatch.setattr(report, "load", lambda _: (m, recs, None))
    monkeypatch.setattr(margin, "v05_proposal", lambda _: [])
    monkeypatch.setattr(margin, "mark_gate", lambda *a: pytest.fail("An unusable baseline cannot pass a gate"))
    assert margin.check([]) == 2
    err = capsys.readouterr().err
    assert "B02 the baseline has nothing comparable" in err and margin.BASELINE_FIX in err


def test_a_citation_regression_fails_the_gate(tmp_path, monkeypatch, capsys):
    b, prof, engine = margin_setup(tmp_path, monkeypatch)
    engine.profile = SimpleNamespace(graph_path=lambda: tmp_path / "graph", model_hash=lambda: "abc")
    monkeypatch.setattr(margin, "chunks", lambda _: True)
    monkeypatch.setattr(margin, "EvidenceArms", lambda *a, **k: None)
    monkeypatch.setattr(harness, "run", lambda *a, **k: tmp_path)
    recs = records()
    for r in recs:
        if r["arm"] == "treatment":
            r["measures"]["citations"]["tier_correct"] = 0
    monkeypatch.setattr(report, "load", lambda _: (manifest(), recs, b))
    monkeypatch.setattr(margin, "v05_proposal", lambda _: [])
    marks = []
    monkeypatch.setattr(margin, "mark_gate", lambda graph, verdict: marks.append(verdict))
    evidence = []
    monkeypatch.setattr("kge.eval.stages.keep_pre", lambda prof, folder, passed: evidence.append(passed))
    assert margin.check([]) == 1
    assert len(marks) == 1 and marks[0]["passed"] is False
    assert evidence == [False]
    out = capsys.readouterr().out
    assert "Tier-correct citations" in out
    assert f"golden v1: the baseline's set; its margin, {len(report.MARGIN)} scores per kind and overall" in out


def test_the_harness_records_each_items_content_and_no_set_fingerprint(tmp_path):
    path = tmp_path / "golden.json"
    write_set(path, ["A", "B"], version="v2", extra={"changes_from": {"version": "v1", "sha256": "a" * 64}})
    got = harness.describe_sets(tmp_path, harness.load(tmp_path, ["golden"], None))
    assert got == {"golden": {"items": 2, "size": 2, "version": "v2", "file": "golden.json",
                              "item_sha256": sets.item_hashes([item("A"), item("B")])}}


def test_new_baseline_scores_still_drive_sample_questions(tmp_path):
    path = tmp_path / "golden.json"
    write_set(path, ["A", "B"])
    m = manifest()
    m["sets"]["golden"] = metadata(path)
    recs = records(("A", "B"))
    for r in recs:
        if r["id"] == "B":
            r["ok"] = False
    b = report.baseline(m, recs, "before")
    shown = sets.golden(tmp_path, b, "treatment", "client")[0]
    assert shown["samples"] == ["A"] and shown["misses"] == ["B"]


def test_another_clients_baseline_names_the_fix(tmp_path, monkeypatch, capsys):
    b, prof, _ = margin_setup(tmp_path, monkeypatch)
    b["profile"] = "another_client"
    prof.baseline.write_text(json.dumps(b))
    assert margin.check([]) == 2
    err = capsys.readouterr().err
    assert "another_client" in err and "B02" in err and margin.BASELINE_FIX in err
