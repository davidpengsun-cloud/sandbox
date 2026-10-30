"""The A/B baselines: #38's, kept as the before; #39's (issue #39 item 7f), kept as it was; and #40's, which runs are put
beside since issue #40 released assets under management (AUM) by a pack edit alone.

    .venv/bin/python -m pytest tests/unit/test_baselines.py -q

clients/meridian/baselines/issue-38.json is #38's: recorded before #39 changed
anything, on golden v1 and heldout v1, and kept byte for byte as the before
(eval/baseline.json until #39 item 9 moved the baselines under the client).
clients/meridian/baselines/issue-39.json is #39's: one make ab run on a clean
build, every arm on rules, on golden v2, heldout v1 and golden_wealth_v1, the
Wealth expansion's set. clients/meridian/baselines/issue-40.json is #40's,
recorded the same way on a clean build once AUM was released: make ab and make
margin put runs beside it (the Makefile's BASELINE, the profile's in
deploy/profiles.yaml); kge/eval/margin.py, platform code, names no client's file, so
it checks against the baseline it is given, or the profile's, and says so when
there is none. The hashes the #39 and #40 baselines record are allowlisted for
the disclosure guard, each with its reason. BASE, 4d9d5c9, is the commit #39
started from, where #38's baseline was last written; golden v2's change from v1,
G021 and G030, is the owner's (Option B).

Reads files, git and the Makefile's dry run; the check run end to end beside
each baseline is in tests/integration/test_margin.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kge.services.ask.engine.core import AGENT_ARMS, PRESETS
from kge.eval import margin, report, sets
from kge.adapters.graphstore import STORE
from tests.scratch import BUILD, CLIENT, running_as, write_registry

DEMO = Path(__file__).resolve().parents[2]
REPO = DEMO.parents[1]
B38 = DEMO / "clients" / CLIENT / "baselines" / "issue-38.json"
B38_THEN = "docs/use-cases/kge/demo/eval/baseline.json"
B39 = DEMO / "clients" / CLIENT / "baselines" / "issue-39.json"
B40 = DEMO / "clients" / CLIENT / "baselines" / "issue-40.json"
AUM = ["W16", "W17"]
BASE = "4d9d5c9"
ARMS = list(PRESETS) + list(AGENT_ARMS)
SETS = {"golden": ("v2", 45), "heldout": ("v1", 16), "golden_wealth_v1": ("v1", 30)}
CHANGED = ["G021", "G030"]
WORDS = ("none one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
         "eighteen nineteen twenty").split() + [f"twenty-{w}" for w in "one two three four five six seven eight nine".split()] + ["thirty"]
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def b38() -> dict:
    return json.loads(B38.read_text())


@pytest.fixture(scope="module")
def b39() -> dict:
    return json.loads(B39.read_text())


@pytest.fixture(scope="module")
def b40() -> dict:
    return json.loads(B40.read_text())


def test_the_38_baseline_is_kept_byte_for_byte_as_the_before(b38):
    assert b38["baseline"] == "before the generator changes of #39" and b38["run_id"] == "20261011T081011Z-66c499"
    assert {s: v["version"] for s, v in b38["sets"].items()} == {"golden": "v1", "heldout": "v1"}
    then = subprocess.run(["git", "show", f"{BASE}:{B38_THEN}"], cwd=REPO, capture_output=True, timeout=60)
    if then.returncode != 0:
        pytest.skip(f"no git history holding {BASE} here: {then.stderr.decode(errors='replace').strip()}")
    assert then.stdout == B38.read_bytes(), f"{B38.relative_to(DEMO)} is #38's baseline, kept byte for byte since {BASE} ({B38_THEN} then)"


def test_the_39_baseline_is_one_rules_run_of_every_arm_on_the_three_sets(b39, b38):
    assert b39["estate"]["seed"] == 7 and b39["llm"] is None and b39["repeats"] == 3 and b39["principal"] == "analyst_enterprise"
    assert [(a["arm"], a["mode"]) for a in b39["arms"]] == [(a, "rules") for a in ARMS], "every arm, on rules"
    assert {s: (v["version"], v["size"]) for s, v in b39["sets"].items()} == SETS
    for s, v in b39["sets"].items():
        assert v["items"] == v["size"], (s, "the whole set ran")
        if s != "golden_wealth_v1":
            assert v["sha256"] == sha(BUILD / v["file"]), (s, "the build's set file is the one the baseline ran: make golden")
        ids = [i["id"] for i in sets.items(BUILD / v["file"])]
        assert list(b39["items"][s]) == ids, s
        assert all(set(r) == {"kind", *ARMS} for r in b39["items"][s].values()), (s, "each question's passes, per arm")
        assert set(b39["passed"][s]) == set(b39["measures"][s]) == set(ARMS), s
    c = b39["sets"]["golden"]["changes_from"]
    assert c["version"] == "v1" and c["items"] == CHANGED and c["sha256"] == b38["sets"]["golden"]["sha256"]
    assert b39["sets"]["heldout"]["sha256"] == b38["sets"]["heldout"]["sha256"]
    g = b39["graph"]
    assert all(DIGEST.fullmatch(g[k] or "") for k in ("model", "legacy_model", "artifact", "overlay")), g
    assert g["store"]["engine"] == STORE and g["store"]["version"] and g["store"]["storage_version"] is not None, g["store"]


def test_the_39_baseline_records_the_margin_on_every_set_and_on_rules_its_repeats_agree(b39):
    for s in SETS:
        kinds = {i["kind"] for i in b39["items"][s].values()}
        assert list(b39["margin"][s]) == [k for k in report.KINDS if k in kinds] + ["all"], s
        for k, scores in b39["margin"][s].items():
            assert set(scores) == set(report.MARGIN), (s, k)
            for name, x in scores.items():
                assert x is None or (x["min"] == x["max"] and x["n"] == b39["repeats"]), (s, k, name, "on rules the repeats agree")
    assert margin.unusable(b39) is None, "make margin can check against it"


def test_beside_the_38_baseline_no_question_moved_but_the_two_golden_v2_changed(b39, b38):
    for s in ("golden", "heldout"):
        assert list(b39["items"][s]) == list(b38["items"][s]), s
        for i, then in b38["items"][s].items():
            if s == "golden" and i in CHANGED:
                continue
            assert b39["items"][s][i] == then, (s, i)
    assert b39["passed"]["heldout"] == b38["passed"]["heldout"]
    assert b39["margin"]["heldout"] == b38["margin"]["heldout"]


def test_the_40_baseline_is_one_rules_run_of_every_arm_on_the_three_sets_as_39s_was(b40, b39):
    assert b40["baseline"] == "of #40, with assets under management" and b40["profile"] == CLIENT
    for k in ("llm", "repeats", "principal"):
        assert b40[k] == b39[k], (k, "run the same way")
    changed = lambda b: {s: {k: x for k, x in v.items() if not (s == "golden_wealth_v1" and k == "sha256")} for s, v in b["sets"].items()}  # noqa: E731
    assert changed(b40) == changed(b39), "the same sets; golden_wealth_v1's file reads physical names since pack 1.5.0"
    assert b40["estate"] == {"seed": 7, "generator": "kge/estate/generate.py"}, "the same seed, the generator where it now lives"
    assert [(a["arm"], a["mode"]) for a in b40["arms"]] == [(a, "rules") for a in ARMS], "every arm, on rules"
    g = b40["graph"]
    assert all(DIGEST.fullmatch(g[k] or "") for k in ("model", "legacy_model", "artifact", "overlay")), g
    assert all(g[k] != b39["graph"][k] for k in ("model", "legacy_model", "artifact")), "a graph with AUM in it"
    assert margin.unusable(b40) is None, "make margin can check against it"


def test_beside_the_39_baseline_aum_moved_w16_and_w17_on_graph_on_and_xb1_r_on_the_graph_off_presets(b40, b39):
    """The owner's option A (issue #40). On graph on (the graph preset and the agent's treatment) only W16 and W17 moved:
    they pass, on AUM. On the graph-off presets, which read the pack's words too, W17 passes on catalog (refused, two
    measures not added), and XB1-R (XB1 asked as analyst_retail) no longer passes on schema or catalog: still no figure and
    no warehouse queried, but refused on schema without the join-time access refusal's own behaviour, and abstaining on
    catalog. Golden and held-out did not move (the 183 hold). XB2's answer moved too, from parts to one figure, which no
    pass holds: it had matched its expected behaviour class by accident, so its class margin fell beside #39's; the owner
    gated that one release on the pack's gates alone (make margin HASH= BASELINE=), and runs are put beside #40's since.
    #40's was recorded again on the SAGE wealth data (pack 1.5.0, issue #176, the owner's say-so), and again once
    Wealth's fifteen other tables took physical names in SAGE's style: no pass moved on graph on; on graph off, which is
    handed no curated metadata and finds a column by its name's words, G017, G018 (catalog), G020 and H005 no longer
    pass, since the words no longer name Wealth's columns (QTR_FEE_REV_FACT_1.REV_AM, AUA_FACT_1.AUA_AM), so the
    golden and held-out margins rose and none fell."""
    moved = {(s, i, arm): (b39["items"][s][i][arm], v[arm]) for s in SETS for i, v in b40["items"][s].items() for arm in ARMS
             if v[arm] != b39["items"][s][i][arm]}
    assert moved == {("golden_wealth_v1", i, arm): (0, 1) for i in AUM for arm in ("graph", "treatment")} | {
        ("golden_wealth_v1", "W17", "catalog"): (0, 1), ("golden_wealth_v1", "XB1-R", "schema"): (1, 0),
        ("golden_wealth_v1", "XB1-R", "catalog"): (1, 0)} | {
        ("golden", "G017", arm): (1, 0) for arm in ("schema", "catalog", "control")} | {
        ("golden", "G018", "catalog"): (1, 0), ("golden", "G020", "schema"): (1, 0), ("golden", "G020", "control"): (1, 0)} | {
        ("heldout", "H005", arm): (1, 0) for arm in ("schema", "catalog", "control")}, moved
    for s in ("golden", "heldout"):
        assert all(b40["passed"][s][arm] == b39["passed"][s][arm] for arm in ("graph", "treatment")), s
        for kind, scores in b39["margin"][s].items():
            for score in ("pass_rate", "figure_correct"):
                then, now = scores[score], b40["margin"][s][kind][score]
                assert (then is None and now is None) or now["mean"] >= then["mean"], (s, kind, score)


def dry(monkeypatch, capsys, tmp_path, *args: str) -> list[list[str]]:
    """What make <args> runs, through kge with a stand-in for each module: each module's arguments, and what kge says."""
    from tests.unit.test_cli import plan
    capsys.readouterr()
    code, ran = plan(monkeypatch, *args, f"MARGIN_STATUS={tmp_path / 'margin.status'}", "PY=python3",
                     compiled=f"build/artifacts/{CLIENT}/{'a' * 64}/")
    assert code == 0
    said = [line.split() for line in capsys.readouterr().out.splitlines()]
    return said + ran


def test_make_ab_make_margin_and_make_baseline_use_the_40_baseline_and_the_38th_on_request(monkeypatch, capsys, tmp_path):
    rel = B40.relative_to(DEMO).as_posix()
    assert re.search(r"^BASELINE \?= \$\(call profile,baseline\)$", (DEMO / "Makefile").read_text(), re.M), "the profile's"
    from kge.pipeline.profile import resolve
    assert resolve(registry=DEMO / "deploy" / "profiles.yaml", profile_id=CLIENT).baseline == B40
    def lines(*args: str, module: str) -> list[list[str]]:
        return [line for line in dry(monkeypatch, capsys, tmp_path, *args) if module in line]

    ab = lines("ab", module="kge.eval.harness")
    assert len(ab) == 1 and ab[0][-2:] == ["--baseline", rel], ab
    said, check = lines("margin", f"HASH={'a' * 64}", module="kge.eval.margin")
    assert said[:3] == ["python3", "-m", "kge.eval.margin"] and said[-4:-2] == ["--baseline", rel], said
    assert check[0] == "kge.eval.margin" and check[3:5] == ["--baseline", rel], check
    keep = lines("baseline", "RUN=r1", module="kge.eval.report")
    assert len(keep) == 1 and keep[0][2:4] == ["--save-baseline", rel], keep
    ab = lines("ab", "BASELINE=clients/meridian/baselines/issue-38.json", "SETS=golden,heldout", module="kge.eval.harness")
    assert len(ab) == 1 and ab[0][3:5] == ["--sets", "golden,heldout"], ab
    assert ab[0][-2:] == ["--baseline", "clients/meridian/baselines/issue-38.json"], ab
    estate = lines("check-estate", module="kge.estate.check_estate")
    assert estate[0][-2:] == ["--baseline", "clients/meridian/baselines/issue-38.json"], estate


def test_eval_margin_names_no_clients_baseline_and_says_so_when_given_none(tmp_path, capsys):
    (tmp_path / "build").mkdir()
    write_registry(tmp_path / "build", baseline=None)
    with running_as(tmp_path / "build"):
        code = margin.main(["--repeats", "1", "--out", str(tmp_path / "runs")])
    err = capsys.readouterr().err
    assert code == 2 and "--baseline" in err and "make margin" in err and "Traceback" not in err, err
    assert not (tmp_path / "runs").exists(), "nothing ran"
    assert "baseline.json" not in (DEMO / "kge" / "eval" / "margin.py").read_text().split("def check", 1)[1], \
        "no file is taken for the client's baseline by default"


@pytest.mark.parametrize("baseline", [B39, B40], ids=["39", "40"])
def test_the_disclosure_guard_allows_each_digest_the_39_and_40_baselines_record_and_no_other_line(baseline):
    rel = baseline.relative_to(REPO).as_posix()
    allowed = {}
    for line in (REPO / "tools" / "disclosure-allow.txt").read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if not line.startswith("#") and len(cells) == 4 and cells[0] == rel:
            allowed[int(cells[1])] = cells[3]
    digests = {n for n, line in enumerate(baseline.read_text().splitlines(), 1) if DIGEST.search(line)}
    assert len(digests) == 8, "the graph's four hashes, three set files and the v1 file golden v2 changed from"
    assert set(allowed) == digests, (sorted(allowed), sorted(digests))
    assert all("sha256 hex digest" in why and "not an account number" in why for why in allowed.values()), allowed
    r = subprocess.run([sys.executable, "tools/check_disclosure.py", rel], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_readme_says_which_baseline_runs_are_put_beside_and_how_to_put_one_beside_the_38th():
    readme = (DEMO / "README.md").read_text()
    section = flat(readme.split("## The A/B harness", 1)[1].split("\n## ", 1)[0])
    rel = B40.relative_to(DEMO).as_posix()
    for needed in (f"`{rel}`", "`clients/meridian/baselines/issue-39.json`", "`clients/meridian/baselines/issue-38.json`", "`make ab BASELINE=clients/meridian/baselines/issue-38.json SETS=golden,heldout`",
                   "kept byte for byte", "`.venv/bin/python -m pytest tests/unit/test_baselines.py -q`"):
        assert needed in section, needed
    assert f"`python3 -m kge.eval.margin --baseline {rel}`" in section
    running = flat(readme.split("## Running it", 1)[1].split("\n## ", 1)[0])
    assert f"beside {rel}" in running and "beside clients/meridian/baselines/issue-38.json" not in running


def test_the_readme_gives_the_name_the_39_baseline_was_recorded_with_so_following_it_gives_the_committed_file(
        b39, monkeypatch, capsys, tmp_path):
    """Without NAME, make baseline names a baseline after its file (issue-39). #39's was recorded with a NAME, so the README
    gives the whole command, name and all: following it records the committed file, but for the run and its time."""
    section = flat((DEMO / "README.md").read_text().split("## The A/B harness", 1)[1].split("\n## ", 1)[0])
    name = b39["baseline"]
    assert name == "of #39, with the Wealth expansion and golden_wealth_v1", name
    assert f'`make baseline RUN=<run_id> NAME="{name}"`' in section, "the README names the NAME #39's baseline was recorded with"
    assert "without `NAME` a baseline is named after its file" in section
    named = json.loads(B40.read_text())["baseline"]
    assert (f'`make baseline RUN=<run_id> BASELINE={B40.relative_to(DEMO).as_posix()} NAME="{named}"`') in section
    keep = [line for line in dry(monkeypatch, capsys, tmp_path, "baseline", "RUN=r1", f"NAME={name}") if "kge.eval.report" in line]
    assert len(keep) == 1 and keep[0][-2:] == ["--name", name], keep
    bare = [line for line in dry(monkeypatch, capsys, tmp_path, "baseline", "RUN=r1") if "kge.eval.report" in line]
    assert len(bare) == 1 and "--name" not in bare[0], bare


def test_what_it_shows_says_the_harness_runs_every_set_the_pack_names_not_the_golden_and_held_out_alone():
    readme = (DEMO / "README.md").read_text()
    point = next(line for line in readme.splitlines() if line.startswith("8. **The A/B harness.**"))
    assert "the question sets the profile's pack names" in point and "golden_wealth_v1" in point, point


def test_the_readme_names_the_38th_where_it_means_it_and_says_beside_the_40th_every_graph_hash_is_the_baseline_s(b40, b38):
    readme = (DEMO / "README.md").read_text()
    bare = re.findall(r"[^.]{0,60}\bthe A/B baseline\b[^.]{0,60}", readme, re.I)
    assert not bare, bare
    rel = B40.relative_to(DEMO).as_posix()
    landing = flat(next(p for p in readme.split("\n\n") if "The run trace's model hash covers the release" in p))
    assert "#38's A/B baseline's model hash (`clients/meridian/baselines/issue-38.json`)" in landing, landing
    assert f"Beside #40's baseline (`{rel}`)" in landing, landing
    row = flat(next(line for line in readme.splitlines() if line.startswith("| `graph` |")))
    assert "#38's A/B baseline (`clients/meridian/baselines/issue-38.json`)" in row and f"#40's baseline (`{rel}`)" in row, row
    assert "`model`, `legacy_model`, `artifact` and `overlay` are each the baseline's" in row, row
    said = "This run's graph model is the same."
    assert f'"{said}"' in row and f'graph = "{said}"' in (DEMO / "kge" / "eval" / "report.py").read_text()
    assert set(b40["graph"]) == {"model", "legacy_model", "artifact", "overlay", "store"} and set(b38["graph"]) == {"model", "overlay"}


def test_the_wealth_sets_results_the_readme_quotes_are_the_40_baselines(b40):
    readme = flat((DEMO / "README.md").read_text())
    passed = b40["passed"]["golden_wealth_v1"]
    on, off = passed["graph"]["all"], passed["control"]["all"]
    assert passed["treatment"]["all"] == on and on == round(on) and off == round(off)
    assert (f"On rules, graph on (the graph preset and the agent's treatment) passes {WORDS[round(on)]} of the thirty, and the "
            f"agent's control, graph off, {WORDS[round(off)]}.") in readme
    g = b40["margin"]["golden_wealth_v1"]["all"]
    pts = {m: round(g[m]["mean"] * 100) for m in ("pass_rate", "figure_correct", "behaviour_class")}
    assert (f"On golden_wealth_v1 the treatment is ahead by {pts['pass_rate']} points on pass rate, {pts['figure_correct']} on "
            f"figure correct and {pts['behaviour_class']} on behaviour class") in readme


def test_the_readme_names_every_generated_record_committed_and_no_warehouse_row_or_document_is():
    """The estate's data is never committed: no warehouse and no document generate.py writes. What is, results or structure
    from a run, the README names: the baselines, the parity and estate snapshots, the pack's lock and the control's inputs
    frozen before item 6, which quote the opening of the landed, masked chunks the control retrieved, never a row.
    parity-stated.json is written by hand and the spec_ fixtures freeze kge/estate/spec.py's values, so neither is
    generated."""
    listed = subprocess.run(["git", "ls-files", "clients/meridian", "tests/fixtures"], cwd=DEMO, capture_output=True, text=True,
                            timeout=60)
    assert listed.returncode == 0, listed.stderr
    generated = [f for f in listed.stdout.split() if f.endswith((".json", ".lock")) and not f.endswith("parity-stated.json")
                 and not f.startswith("tests/fixtures/spec_")]
    assert "tests/fixtures/control_6c897ba.json" in generated and "clients/meridian/pack.lock" in generated, generated
    readme = (DEMO / "README.md").read_text()
    para = flat(next(p for p in readme.split("\n\n") if p.startswith("Everything is generated by `kge/estate/generate.py`")))
    assert "None of the data it generates is committed" not in para
    assert "No warehouse and no document it generates is committed" in para and "never a warehouse row" in para
    for f in generated:
        assert f"`{f}`" in para, f
    assert "`tests/fixtures/`" in para, "the other fixtures: earlier packs and meta-models, and kge/estate/spec.py's values"


def test_the_readme_says_the_38_baseline_was_recorded_on_38s_branch_not_this_one(b38):
    """#38's baseline was recorded on #38's branch (PR #41), on 11 Oct, before #39's branch existed; "this branch", in #39's
    README, is #39's, where #39's baseline was recorded."""
    assert b38["recorded_at"].startswith("2026-10-11")
    section = flat((DEMO / "README.md").read_text().split("## The A/B harness", 1)[1].split("\n## ", 1)[0])
    assert "on a clean build of this branch before the generator changes of #39" not in section
    assert "recorded the same way on a clean build of #38's branch (PR #41), before #39 changed anything" in section


def test_the_demo_script_names_each_sample_question_graph_on_does_not_answer_as_its_set_expects(b40):
    """The page offers every question of the golden sets as a sample (All 78 sample questions). The README's Demo script
    names, by id, each one that graph on (the graph preset or the agent's treatment) did not answer as its set expects in
    the profile's baseline, #40's, with what the page gives for it, so a presenter is never surprised by one; any other id
    it names, it names as answered: W16 and W17, on AUM, which graph on answers since pack 1.3.0."""
    items = {i: v for s in ("golden", "golden_wealth_v1") for i, v in b40["items"][s].items()}
    misses = {i for i, v in items.items() if v["graph"] < 1 or v["treatment"] < 1}
    readme = (DEMO / "README.md").read_text()
    script = readme.split("\n## Demo script\n", 1)[1].split("\n## ", 1)[0]
    named = set(re.findall(r"\b(?:G\d{3}|W\d\d|XB\d)\b", script))
    assert misses and misses <= named, sorted(misses - named)
    assert named - misses == set(AUM) and all(items[i]["graph"] == items[i]["treatment"] == 1 for i in AUM), sorted(named - misses)
