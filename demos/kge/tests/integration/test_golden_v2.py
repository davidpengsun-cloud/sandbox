"""Golden v2 (#39 item 7a): the owner's Option B, in the expectations first.

    .venv/bin/python -m pytest tests/integration/test_golden_v2.py -q

Retail and Wealth join through a certified client cross-reference (the #39
Option B update), so retail-wealth is no longer a refused join: G021 and G030
drop it from joins_refused, and nothing else changes. The owner's order lets
the expectations change before the runtime. The v2 file declares its change
(changes_from: the version it changed from, the items that changed and the
sha256 of that version's file), so a run on it is still put beside the #38
baseline, recorded on v1: its other 43 questions question by question, and the
margin of the pass rate on them, with G021 and G030 listed apart. The held-out
set stays v1, byte for byte.

Under the golden scorer's subset check (every refused join the item expects is
refused), v2 passes on the runtime. Since item 7d the graph's arms no longer
refuse retail-wealth, so there v2 passes and v1 no longer does; the arms
without the graph refuse no join, and score v1 and v2 alike.

Reads the demo's build; the runtime test opens its graph through the eng
fixture. Stop the API first.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kge.services.ask.engine.core import AGENT_ARMS, PRESETS
from kge.eval import harness, margin, parity, sets
from kge.eval.run_golden import score, uncertified_ids
from tests.scratch import BUILD, DEMO, running_as, scratch_build

BASELINE = DEMO / "clients" / "meridian" / "baselines" / "issue-38.json"
SNAPSHOT = DEMO / "clients" / "meridian" / "baselines" / "parity-4d9d5c9.json"
CHANGED = ["G021", "G030"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def v1() -> list[dict]:
    """Golden v1, as kge/estate/golden.py builds it from the warehouses on disk (read only)."""
    from kge.estate.golden import build_golden
    return json.loads(json.dumps(build_golden(BUILD), default=str))


@pytest.fixture(scope="module")
def v2() -> dict:
    return sets.read(BUILD / "golden.json")


def test_golden_v2_drops_retail_wealth_from_g021_and_g030_and_nothing_else(v1, v2):
    assert v2["version"] == "v2"
    then, now = {i["id"]: i for i in v1}, {i["id"]: i for i in v2["items"]}
    assert list(now) == list(then) and len(now) == 45, "the same 45 questions, in the same order"
    for qid, item in now.items():
        if qid not in CHANGED:
            assert item == then[qid], qid
    for qid in CHANGED:
        was, got = then[qid], now[qid]
        assert "retail-wealth" in was["expect"]["joins_refused"] and "retail-wealth" not in got["expect"]["joins_refused"]
        assert got["expect"]["joins_refused"] == [p for p in was["expect"]["joins_refused"] if p != "retail-wealth"], qid
        assert {**got, "expect": {**got["expect"], "joins_refused": None}} == {**was, "expect": {**was["expect"], "joins_refused": None}}, \
            f"{qid}: only its refused joins move"
    assert now["G021"]["expect"]["joins_refused"] == ["retail-corporate", "sme-corporate", "sme-wealth", "retail-sme", "corporate-wealth"]
    assert now["G030"]["expect"]["joins_refused"] == ["retail-corporate", "sme-corporate", "sme-wealth"]


def test_the_v2_file_declares_the_v1_file_it_changed_from(v1, v2):
    """The v1 file it changed from is the one the #38 baseline ran, by sha256, so its 43 other items are put beside
    it; the held-out set is still v1, byte for byte the file the baseline ran."""
    from kge.estate.golden import set_text
    c = v2["changes_from"]
    assert (c["version"], c["items"]) == ("v1", CHANGED)
    assert "certified client cross-reference" in c["why"] and "retail-wealth" in c["why"]
    b = json.loads(BASELINE.read_text())
    assert c["sha256"] == sha(set_text(v1).encode()) == b["sets"]["golden"]["sha256"]
    assert b["sets"]["golden"]["version"] == "v1"
    assert sets.read(BUILD / "heldout.json") == {"version": "v1", "changes_from": None, "items": json.loads((BUILD / "heldout.json").read_text())}
    assert sha((BUILD / "heldout.json").read_bytes()) == b["sets"]["heldout"]["sha256"]


def test_the_harness_runs_golden_v2_and_heldout_v1_and_records_the_change(v2):
    assert harness.SETS == {"golden": "v2", "heldout": "v1"}
    described = harness.describe_sets(BUILD, {"golden": v2["items"][:2], "heldout": sets.items(BUILD / "heldout.json")})
    assert described["golden"] == {"items": 2, "size": 45, "version": "v2", "file": "golden.json",
                                   "sha256": sha((BUILD / "golden.json").read_bytes()), "changes_from": v2["changes_from"]}
    assert "changes_from" not in described["heldout"] and described["heldout"]["version"] == "v1"


def test_make_golden_writes_v2_and_heldout_v1_from_the_warehouses_on_disk(tmp_path):
    """make golden (kge/estate/generate.py --golden-only) rewrites the sets alone: the same bytes on the same estate."""
    out = tmp_path / "build"
    out.mkdir()
    (out / "warehouses").symlink_to(BUILD / "warehouses")
    r = subprocess.run([sys.executable, "-m", "kge.estate.generate", "--out", str(out), "--golden-only"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    said = json.loads(r.stdout)
    assert {k: said[k] for k in ("golden_questions", "heldout_questions")} == {"golden_questions": 45, "heldout_questions": 16}
    for name in ("golden.json", "heldout.json"):
        assert (out / name).read_bytes() == (BUILD / name).read_bytes(), name


def test_v2_passes_on_the_runtime_and_the_graph_s_arms_no_longer_refuse_retail_wealth(eng, v1, v2):
    """Since item 7d the graph's arms read the certified join and refuse only the pairs with a no-join fact, so v2's
    G021 and G030 pass there and v1's, which expect retail-wealth refused, no longer do. The scorer asks that every
    refused join the item expects is refused; the arms without the graph refuse none, and score v1 and v2 alike."""
    arms = harness.Arms(eng, llm=None)
    unc = uncertified_ids(BUILD)
    then, now = {i["id"]: i for i in v1}, {i["id"]: i for i in v2["items"]}
    for qid in CHANGED:
        for arm in [*PRESETS, *AGENT_ARMS]:
            ans = arms.ask(arm, now[qid]["question"], "analyst_enterprise")
            if arm in ("graph", "treatment"):
                assert score(now[qid], ans, unc)[0] and not score(then[qid], ans, unc)[0], (qid, arm, ans.get("joins_refused"))
                assert "retail-wealth" not in ans["joins_refused"] and "sme-wealth" in ans["joins_refused"], (qid, arm)
            else:
                assert score(now[qid], ans, unc)[0] == score(then[qid], ans, unc)[0], (qid, arm)


def stale(tmp_path: Path, v1: list[dict]) -> Path:
    """A scratch build whose golden.json is still v1, as a build made before v2 holds it: a list, a set's first version.

    The scratch build's golden.json is a link to the demo's own, so it is unlinked first, never written through."""
    from kge.estate.golden import set_text
    build = scratch_build(tmp_path / "build")
    (build / "golden.json").unlink()
    (build / "golden.json").write_text(set_text(v1))
    return build


def test_a_golden_set_older_than_the_code_s_is_refused_naming_make_golden(tmp_path, capsys, v1):
    build = stale(tmp_path, v1)
    with pytest.raises(SystemExit, match=r"golden\.json holds golden v1, and this code runs golden v2: make golden"):
        harness.choose_sets("golden", build)
    assert harness.choose_sets("heldout", build) == ["heldout"], "a list is a set's first version: heldout v1 runs"
    with running_as(build):
        code = margin.main(["--repeats", "1", "--out", str(tmp_path / "runs"), "--baseline", str(BASELINE)])
    err = capsys.readouterr().err
    assert code == 2 and "holds golden v1, and this code runs golden v2: make golden" in err and not (tmp_path / "runs").exists(), err
    with running_as(build):
        code = parity.main(["--snapshot", str(SNAPSHOT)])
    err = capsys.readouterr().err
    assert code == 2 and "holds golden v1, and this code runs golden v2: make golden" in err, err


def test_a_further_set_is_the_version_its_name_says(tmp_path):
    build = scratch_build(tmp_path / "build")
    one = sets.items(BUILD / "golden.json")[:1]
    (build / "extra_v2.json").write_text(json.dumps(one))
    with pytest.raises(SystemExit, match=r"extra_v2\.json holds extra v1"):
        harness.choose_sets("extra_v2", build)
    (build / "extra_v2.json").write_text(json.dumps({"version": "v2", "items": one}))
    assert harness.choose_sets("extra_v2", build) == ["extra_v2"]
    (build / "extra_v1.json").write_text(json.dumps(one))
    assert harness.choose_sets("extra_v1", build) == ["extra_v1"], "a list named _v1 is its first version"


def test_a_set_file_is_a_list_or_an_object_naming_its_version_and_anything_else_is_refused(tmp_path):
    path = tmp_path / "s.json"
    items = [{"id": "A1", "kind": "within", "question": "q", "expect": {}}, {"id": "A2", "kind": "cross", "question": "r", "expect": {}}]
    path.write_text(json.dumps(items))
    assert sets.read(path) == {"version": "v1", "changes_from": None, "items": items} and sets.items(path) == items, "a list is a first version"
    declared = {"version": "v3", "changes_from": {"version": "v2", "items": ["A2"], "sha256": "a" * 64, "why": "asked"}, "items": items}
    path.write_text(json.dumps(declared))
    assert sets.read(path) == declared
    path.write_text(json.dumps({"version": "v2", "items": items}))
    assert sets.read(path) == {"version": "v2", "changes_from": None, "items": items}
    for bad, said in (({"a": 1}, "items"), ("x", "a list of items"), ({"version": "2", "items": items}, "version"),
                      ({"version": "v2", "items": [1]}, "items"),
                      ({"version": "v2", "items": items, "changes_from": {"version": "v1", "items": ["Z9"], "sha256": "a" * 64, "why": "w"}}, "Z9"),
                      ({"version": "v2", "items": items, "changes_from": {"version": "v1", "items": ["A1"], "sha256": "short", "why": "w"}}, "sha256"),
                      ({"version": "v2", "items": items, "changes_from": {"version": "v2", "items": ["A1"], "sha256": "a" * 64, "why": "w"}}, "version"),
                      ({"version": "v2", "items": items, "changes_from": {"version": "v1", "items": [], "sha256": "a" * 64, "why": "w"}}, "items")):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match=said) as exc:
            sets.read(path)
        assert str(path) in str(exc.value), bad
    path.write_text("{")
    with pytest.raises(ValueError, match=str(path)):
        sets.read(path)


def test_the_readme_and_the_makefile_say_what_golden_v2_changed_and_how_it_is_compared():
    readme = " ".join((DEMO / "README.md").read_text().split())
    harness_section = readme.split("## The A/B harness", 1)[1].split(" ## ", 1)[0]
    for needed in ("golden v2", "G021 and G030", "retail-wealth", "certified client cross-reference", "`changes_from`",
                   "its 43 other questions", "listed apart", "the margin of the pass rate on them", "`make golden`",
                   "`.venv/bin/python -m pytest tests/integration/test_golden_v2.py -q`"):
        assert needed in harness_section, needed
    assert "golden v2" in readme.split("## The estate", 1)[1].split(" ## ", 1)[0], "the estate's table names the set's version"
    assert "make golden" in readme.split("## Running it", 1)[1].split(" ## ", 1)[0]
    mk = (DEMO / "Makefile").read_text()
    rule = mk.split("\ngolden:", 1)[0].rsplit("\n\n", 1)[1]
    assert "golden v2" in rule and "heldout v1" in rule, rule
