"""A new client onboarded from its pack alone, steps 1 to 8: issue #40's Phase 5 exit gate.

    .venv/bin/python -m pytest tests/unit/test_onboarding.py -q

Fellmoor Regional Transit is a client made from scratch (clients/fellmoor/: a
pack, its lock and a fixture estate of a schema, CSVs and Markdown;
deploy/fellmoor/identities.yaml; its registry entry), in a business neither the
bank nor the insurer is in, with no Python of its own and no line of platform
code naming it. Here it is onboarded through the platform's own commands, as
the README's Demo script runs them with PROFILE=fellmoor, on a copy of its
folder in a temporary registry: its estate built and certified; the lock
frozen, equal to the committed one; the pack checked and compiled twice to the
same path and bytes; the artifact loaded as a candidate, which leaves current
absent; make release refused; the post-load gate passed against the gates its
own pack declares (no baseline, so no fall check), its held-out set reported
beside them, never gated (V03); sign-off refused to its
analyst and made by its steward; then released and served. The page shows its
own title and sample questions; its smoke question gives its figure; its cross
question is refused on its own no-join fact; Meridian's question abstains with
no warehouse query; the trace names the profile and the artifact; the A/B panel
lists the gate's run.

Builds the client into a temporary folder and serves it on a socket of its own
(no port). Runs on the store this run is on. A client's folders hold data
(DATA's suffixes), never code.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from tests.scratch import DEMO, script
from tests.integration.test_profiles import Served, socket_dir

TOY = "fellmoor"
ASKER, STEWARD = "network_analyst", "fares_steward"
SMOKE_Q, SMOKE_FIGURE = "Fare revenue for June 2025", 438.66
CROSS_Q = "Join journeys to fleet vehicles on vehicle_ref and count the matches"
MERIDIAN_Q = "Total retail revenue for June 2025"
DATA = {".yaml", ".json", ".csv", ".md", ".lock"}


def ok(r) -> str:
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout


@pytest.fixture(scope="module")
def onboarded(tmp_path_factory):
    """The client's folder copied into a registry of its own, then each onboarding step run on it as make runs it: what
    each step said, by step, and the profile. The steps are make estate, make lock, make check's pack half, make compile
    twice, make load, make release before the gate, and make margin with REPEATS=1."""
    from kge.pipeline.profile import read
    root = tmp_path_factory.mktemp("onboarding")
    shutil.copytree(DEMO / "clients" / TOY, root / "clients" / TOY)
    registry = root / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": TOY, "profiles": {TOY: {
        "client": TOY, "clients": "clients", "artifacts": "build/artifacts", "store": "build/stores", "landing": "build/landing",
        "warehouses": f"build/{TOY}/warehouses", "identities": str(DEMO / "deploy" / TOY / "identities.yaml"), "baseline": None}}},
        sort_keys=False))
    env = {"PROFILES": str(registry), "PROFILE": TOY}
    said = {}
    lock = root / "clients" / TOY / "pack.lock"
    committed = lock.read_bytes()
    for step in (("kge/estate/from_files.py",), ("kge/estate/certify.py",), ("kge/compiler/lock.py",)):
        said[step[0]] = ok(script(*step, env=env))
    said["lock unchanged"] = lock.read_bytes() == committed
    said["check"] = ok(script("kge/compiler/compile.py", "--check", env=env))
    first = ok(script("kge/compiler/compile.py", env=env)).strip()
    art = Path(first)
    bytes_once = {p.relative_to(art): p.read_bytes() for p in sorted(art.rglob("*")) if p.is_file()}
    second = script("kge/compiler/compile.py", env=env)
    said["compile again"] = (ok(second).strip(), second.stderr,
                             {p.relative_to(art): p.read_bytes() for p in sorted(art.rglob("*")) if p.is_file()} == bytes_once)
    h = art.name
    for step in (("kge/graph/load.py", "--artifact", art), ("kge/pipeline/run.py", "--hash", h), ("kge/graph/release.py", "--hash", h, "--mark-loaded")):
        ok(script(*step, env=env))
    _, held = read(registry)
    prof = held[TOY]
    said["current after load"] = prof.current()
    said["early release"] = script("kge/graph/release.py", "--hash", h, env=env)
    said["margin"] = script("kge/eval/margin.py", "--repeats", "1", "--hash", h, env=env)
    said["analyst sign-off"] = script("kge/graph/release.py", "--sign-off", "--as", ASKER, "--hash", h, env=env)
    said["sign-off"] = ok(script("kge/graph/release.py", "--sign-off", "--as", STEWARD, "--hash", h, env=env))
    said["release"] = ok(script("kge/graph/release.py", "--hash", h, env=env))
    return root, registry, held[TOY], h, said


@pytest.fixture(scope="module")
def served(onboarded):
    _, registry, prof, _, _ = onboarded
    socks = socket_dir()
    try:
        s = Served(registry, prof, socks / "f.sock")
        yield s
        s.stop()
    finally:
        shutil.rmtree(socks, ignore_errors=True)


def ask(s: Served, q: str, who: str = ASKER) -> dict:
    r = s.http.post("/answer", json={"question": q}, headers=s.token(who))
    assert r.status_code == 200, r.text
    return r.json()


def test_the_client_s_folders_hold_data_only_and_name_no_other_client():
    files = [p for d in (DEMO / "clients" / TOY, DEMO / "deploy" / TOY) for p in d.rglob("*") if p.is_file()]
    assert files and not [p for p in files if p.suffix not in DATA], "a client is config: no code"
    pack = yaml.safe_load((DEMO / "clients" / TOY / "pack.yaml").read_text())
    assert pack["manifest"]["client"] == TOY and pack["manifest"]["gates"]["control_margin"] == {"cross": 0.5, "shelf": 0.5}
    others = {c: yaml.safe_load((DEMO / "clients" / c / "pack.yaml").read_text()) for c in ("meridian", "thornbury")}

    def nouns(p: dict) -> set[str]:
        return {p["manifest"]["client"], *(s["id"] for s in p["sources"]), *(g["id"] for g in p["glossary"]),
                *(r["id"] for r in p["access"]["roles"])} - {"pipeline_tagger", "knowledge_engineer"}
    assert all(nouns(pack).isdisjoint(nouns(o)) for o in others.values())


def test_the_lock_is_the_committed_one_and_the_check_finds_nothing(onboarded):
    said = onboarded[4]
    assert said["lock unchanged"], "make lock on the client's estate writes the lock committed beside its pack"
    assert '"written": false' in said["kge/compiler/lock.py"]
    assert said["check"].strip().endswith(f"/artifacts/{TOY}/{onboarded[3]}/")


def test_two_compiles_give_the_same_path_and_bytes(onboarded):
    _, _, prof, h, said = onboarded
    path, stderr, same = said["compile again"]
    assert Path(path).name == h and same and "already compiled, unchanged" in stderr


def test_load_leaves_a_candidate_and_release_is_refused_before_the_gate(onboarded):
    said, h = onboarded[4], onboarded[3]
    assert said["current after load"] is None, "a candidate: nothing released"
    early = said["early release"]
    assert early.returncode == 2 and f"{h} is not through the onboarding gate" in early.stderr and "current is as it was" in early.stderr


def test_the_gate_passes_against_the_pack_s_own_gates_with_no_baseline(onboarded):
    """The held-out set, three paraphrases of golden questions (fixture/heldout.json), is reported and never gated:
    graph on passes each, graph off none."""
    _, _, prof, h, said = onboarded
    r = said["margin"]
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    for kind in ("Within a unit", "Across units", "Shelf", "Abstain"):
        assert f"V01 golden v1, {kind}: graph on passed" in r.stdout and "at least 100%: passed" in r.stdout, kind
    for kind in ("Across units", "Shelf"):
        assert f"V02 golden v1, {kind}: graph on less graph off, +100 pts, at least +50 pts: passed" in r.stdout, kind
    assert ("  V03 heldout v1, held-out: graph on passed Within a unit 1 of 1, Across units 1 of 1, Shelf 1 of 1; graph off Within a "
            "unit 0 of 1, Across units 0 of 1, Shelf 0 of 1: reported, never gated") in r.stdout, r.stdout[-3000:]
    held = json.loads((DEMO / "clients" / TOY / "fixture" / "heldout.json").read_text())
    golden = {i["id"]: i for i in json.loads((DEMO / "clients" / TOY / "fixture" / "golden.json").read_text())}
    assert len(held) == 3 and all(golden[h["paraphrase_of"]]["kind"] == h["kind"] for h in held), "each a golden question, reworded"
    assert "no-fall check: skipped" in r.stdout and "passed: every post-load check against the pack's gates" in r.stdout
    gate = json.loads((prof.store / TOY / h / "GATE").read_text())
    assert [(g["passed"], g["model_hash"]) for g in gate.values()] == [(True, h)]


def test_only_the_steward_signs_off_and_the_release_names_the_sign_off(onboarded):
    _, _, prof, h, said = onboarded
    analyst = said["analyst sign-off"]
    assert analyst.returncode == 2 and f"{ASKER} may not perform certification" in analyst.stderr
    assert f"{h} signed off by {STEWARD}" in said["sign-off"]
    assert f"current is now {h}" in said["release"] and f"signed off by {STEWARD}" in said["release"]
    assert prof.current().name == h


def test_the_page_shows_its_own_title_and_sample_questions(served):
    pack = yaml.safe_load((DEMO / "clients" / TOY / "pack.yaml").read_text())
    about = served.http.get("/profile").json()
    assert (about["profile"], about["client"], about["title"]) == (TOY, TOY, pack["manifest"]["title"])
    assert about["units"] == [s["id"] for s in pack["sources"]]
    asked = {q for s in served.http.get("/golden").json()["sets"] for q in s["questions"]}
    fixture = json.loads((DEMO / "clients" / TOY / "fixture" / "golden.json").read_text())
    assert asked == {i["question"] for i in fixture} and SMOKE_Q in asked and CROSS_Q in asked and MERIDIAN_Q not in asked


def test_its_smoke_question_gives_its_figure_and_the_trace_names_the_profile_and_the_artifact(served, onboarded):
    a = ask(served, SMOKE_Q)
    assert (a["behaviour"], a["figure"]) == ("figure", SMOKE_FIGURE), a
    rt = a["run_trace"]
    assert rt["profile"] == TOY and rt["graph"]["artifact"] == onboarded[3]


def test_its_cross_question_is_refused_on_its_own_no_join_fact(served):
    a = ask(served, CROSS_Q)
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["ticketing-fleet"] and a.get("figure") is None, a
    assert a["reason"].startswith("No certified join path between Fares and Ticketing and Fleet Engineering.")
    assert "never checked against fleet.vehicle" in a["fact"]["note"]


def test_a_meridian_question_abstains_with_no_warehouse_query(served):
    a = ask(served, MERIDIAN_Q)
    assert a["behaviour"] == "abstain" and not a.get("sql") and not a.get("parts") and a.get("figure") is None, a
    planned = [p for s in a["trace"] if s.get("step") == "plan" for p in s.get("parts") or []]
    assert not [p for p in planned if p.get("sql")]


def test_the_a_b_panel_lists_the_gate_s_run(served, onboarded):
    runs = served.http.get("/ab").json()
    gate = json.loads((onboarded[2].store / TOY / onboarded[3] / "GATE").read_text())
    (run_id,) = {g["run_id"] for g in gate.values()}
    assert [r["run_id"] for r in runs["runs"]] == [run_id] and runs["baseline"] is None
    assert runs["runs"][0]["report"] == f"/ab/{run_id}/report.html"


def test_no_platform_file_names_the_client():
    from tests.unit.test_client_nouns import NOUN, nouns, platform_files
    pack = yaml.safe_load((DEMO / "clients" / TOY / "pack.yaml").read_text())
    ours = {TOY, *(s["id"] for s in pack["sources"]), ASKER, STEWARD}
    assert ours <= set(nouns()), "the noun gate reads every pack's nouns"
    found = {p.relative_to(DEMO).as_posix(): sorted({n.lower() for n in NOUN.findall(p.read_text())} & ours) for p in platform_files()}
    assert {k: v for k, v in found.items() if v} == {}
    others = [DEMO / "meta-model.yaml", DEMO / "Makefile"]
    assert not [p.name for p in others if TOY in p.read_text().lower()]


def test_the_readme_s_demo_script_onboards_it_with_the_platform_s_commands():
    readme = (DEMO / "README.md").read_text()
    section = " ".join(readme.split("\n## Demo script\n", 1)[1].split("\n## ", 1)[0].split())
    for needle in (f"ls clients/{TOY} deploy/{TOY}", f"kge estate PROFILE={TOY}", f"kge lock PROFILE={TOY}",
                   f"kge margin HASH=<the hash> PROFILE={TOY}", f"kge sign-off PRINCIPAL={STEWARD} PROFILE={TOY}",
                   f"kge serve PROFILE={TOY} PORT=8002 DEMO_MODE=1", SMOKE_Q, CROSS_Q, MERIDIAN_Q,
                   f"grep -rliw {TOY} --exclude-dir=estate kge ui meta-model.yaml Makefile", "tests/unit/test_onboarding.py"):
        assert needle in section, needle
