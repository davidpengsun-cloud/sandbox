"""Two profiles from one platform build, and no data access between them: issue #39's exit gate (item 11).

    .venv/bin/python -m pytest tests/integration/test_profiles.py -q

The deployment's registry (deploy/profiles.yaml) serves two clients from one
platform build: Meridian, the demo's client, and Thornbury Mutual Casualty, a
fixture profile (clients/thornbury/: a pack, its lock and a fixture estate
built from files by kge/estate/from_files.py), with no Python of its own and no
noun shared with Meridian. Run side by side, the README's two terminals:

  make demo                               Meridian, http://127.0.0.1:8000
  make demo PROFILE=thornbury PORT=8001   Thornbury, http://127.0.0.1:8001

Here both run on scratch copies, served side by side by uvicorn on sockets of
their own (no port), through one registry whose Thornbury pack sits outside the
demo folder, as it would in a repository of its own (owner question 4: moving
a client's pack out of the repository is a registry edit). Each answers its own
smoke question, and its trace names its own profile and artifact; a token of
one profile's is a 401 on the other; /principals, /ab and /golden list the
profile's own alone, and the page's title is its pack's; Thornbury, asked a
Meridian question, abstains with no warehouse query. A steward's decision on
one changes only that one's overlay hash. make agent, make eval and make ab, run for
Thornbury with no principal named, ask as its deployment's asker, actuary, on a
copy of its build no API serves. Every path a profile names sits under
its own roots, its identities and its estate's files aside, and none under the
other's, so each still runs with the other's roots moved away. Replay refuses
another client's journal, the loader another client's artifact, and the
registry two profiles whose roots overlap. No platform file names Thornbury,
and the commit that added it touched only its own folder, deploy/, the tests
and the README (R8: a client is config).

Builds Thornbury into a temporary folder and serves a scratch copy of the
demo's build: stop the API first. Runs on the store this run is on.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml

from tests.scratch import DEMO, profile_of, scratch_build, script

THORNBURY = "thornbury"
MERIDIAN_Q, MERIDIAN_FIGURE = "Total retail revenue for June 2025", 13287.67
THORNBURY_Q, THORNBURY_FIGURE = "Gross written premium for June 2025", 37646.14
ROOT_NAMES = ("pack", "artifacts", "store", "landing", "warehouses")
ALONE = "thornbury-alone.yaml"


def identities(client: str) -> set[str]:
    return {p["principal"] for p in yaml.safe_load((DEMO / "deploy" / client / "identities.yaml").read_text())["principals"]}


def run(path: str, *args, registry: Path, profile: str) -> subprocess.CompletedProcess:
    return script(path, *args, env={"PROFILES": str(registry), "PROFILE": profile})


def ok(r: subprocess.CompletedProcess) -> str:
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return r.stdout


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """One registry, two profiles: Meridian on a scratch copy of the demo's build, its pack folder copied beside it, and
    Thornbury built from the committed fixture, its pack in a folder outside the demo, as make demo PROFILE=thornbury
    builds it: estate, certification, the lock's check, compile, load, pipeline and release.

    Meridian's own folders hold its own, as a deployment's would: its current artifact is copied rather than linked to
    the demo's and its landing is a folder of the build's own, so moving a root away moves this copy's, never the
    demo's; its warehouses stay a link. A copy of Thornbury's build, through a registry of its own that no API here
    serves, is for the commands that open its graph (make agent, make eval) while the APIs serve theirs."""
    from kge.pipeline.profile import read
    root = tmp_path_factory.mktemp("profiles")
    build = scratch_build(root / "build")
    meridian = profile_of(build)
    shutil.copytree(meridian.clients / meridian.client, root / "mclients" / meridian.client)
    cur = meridian.current()
    held = meridian.artifacts / meridian.client / cur.name
    held.unlink()
    shutil.copytree(cur, held)
    landing = build / "landing"
    landing.unlink()
    landing.mkdir()
    (landing / meridian.client).symlink_to(DEMO / "build" / "landing" / meridian.client)
    outside = root / "elsewhere" / "clients"
    shutil.copytree(DEMO / "clients" / THORNBURY, outside / THORNBURY)
    rel = lambda p: os.path.relpath(p, root)  # noqa: E731
    registry = root / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": meridian.id, "profiles": {
        meridian.id: {"client": meridian.client, "clients": "mclients", "artifacts": rel(meridian.artifacts), "store": rel(meridian.store),
                      "landing": rel(meridian.landing), "warehouses": rel(meridian.warehouses),
                      "identities": str(DEMO / "deploy" / meridian.client / "identities.yaml"),
                      "baseline": f"mclients/{meridian.client}/baselines/{meridian.baseline.name}"},
        THORNBURY: {"client": THORNBURY, "clients": rel(outside), "artifacts": "tbuild/artifacts", "store": "tbuild/stores",
                    "landing": "tbuild/landing", "warehouses": f"tbuild/{THORNBURY}/warehouses",
                    "identities": str(DEMO / "deploy" / THORNBURY / "identities.yaml")}}}, sort_keys=False))
    for step in (("kge/estate/from_files.py",), ("kge/estate/certify.py",), ("kge/compiler/lock.py", "--check")):
        ok(run(*step, registry=registry, profile=THORNBURY))
    art = ok(run("kge/compiler/compile.py", registry=registry, profile=THORNBURY)).strip()
    h = Path(art).name
    for step in (("kge/graph/load.py", "--artifact", art), ("kge/pipeline/run.py", "--hash", h), ("kge/graph/release.py", "--hash", h, "--mark-loaded"),
                 ("kge/graph/release.py", "--hash", h, "--ungated")):
        ok(run(*step, registry=registry, profile=THORNBURY))
    shutil.copytree(root / "tbuild", root / "tcopy", symlinks=True)
    (root / ALONE).write_text(yaml.safe_dump({"default": THORNBURY, "profiles": {THORNBURY: {
        **yaml.safe_load(registry.read_text())["profiles"][THORNBURY], "artifacts": "tcopy/artifacts", "store": "tcopy/stores",
        "landing": "tcopy/landing", "warehouses": f"tcopy/{THORNBURY}/warehouses"}}}, sort_keys=False))
    _, held = read(registry)
    runs = held[meridian.id].run_dir() / "runs" / "20261014T000000Z-abc123"
    runs.mkdir(parents=True)
    (runs / "manifest.json").write_text(json.dumps({"principal": "analyst_enterprise", "repeats": 1, "sets": {}, "arms": []}))
    (runs / "report.html").write_text("<html></html>")
    return root, registry, held[meridian.id], held[THORNBURY]


class Served:
    """The API for one profile, on a socket of its own: the process, and a client that calls it."""

    def __init__(self, registry: Path, profile, sock: Path):
        self.profile, self.sock = profile, sock
        env = {**os.environ, "PROFILES": str(registry), "PROFILE": profile.id, "DEMO_MODE": "",
               "MODEL_PROVIDER": "none"}
        self.log = open(sock.with_suffix(".log"), "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kge.app:app", "--app-dir", ".", "--uds", str(sock)], cwd=DEMO,
                                     env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.http = httpx.Client(transport=httpx.HTTPTransport(uds=str(sock)), base_url="http://served", timeout=120)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"the API for {profile.id} stopped: {sock.with_suffix('.log').read_text()[-2000:]}")
            if sock.exists():
                try:
                    if self.http.get("/profile").status_code == 200:
                        return
                except httpx.TransportError:
                    pass
            time.sleep(0.3)
        raise RuntimeError(f"the API for {profile.id} did not start: {sock.with_suffix('.log').read_text()[-2000:]}")

    def token(self, who: str) -> dict:
        from kge.services.identity.service import token_for
        return {"authorization": f"Bearer {token_for(self.profile.run_dir(), who)}"}

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        self.http.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.log.close()


def socket_dir() -> Path:
    """A folder for the two APIs' sockets. A socket's path is limited to about a hundred bytes (104 on macOS, 108 on
    Linux), which a test's temporary folder can pass, so it is a folder of the system's temporary folder's own."""
    d = Path(tempfile.mkdtemp(prefix="kge"))
    if len(str(d / "t.sock")) > 100:
        shutil.rmtree(d)
        d = Path(tempfile.mkdtemp(prefix="kge", dir="/tmp"))
    return d


@pytest.fixture(scope="module")
def side_by_side(world):
    _, registry, meridian, thornbury = world
    socks = socket_dir()
    try:
        m = Served(registry, meridian, socks / "m.sock")
        try:
            t = Served(registry, thornbury, socks / "t.sock")
        except BaseException:
            m.stop()
            raise
        yield m, t
        t.stop()
        m.stop()
    finally:
        shutil.rmtree(socks, ignore_errors=True)


def ask(served: Served, who: str, q: str) -> dict:
    r = served.http.post("/answer", json={"question": q}, headers=served.token(who))
    assert r.status_code == 200, r.text
    return r.json()


def test_each_answers_its_own_smoke_question_and_its_trace_names_its_profile_and_artifact(side_by_side):
    m, t = side_by_side
    for served, who, q, figure in ((m, "analyst_enterprise", MERIDIAN_Q, MERIDIAN_FIGURE), (t, "actuary", THORNBURY_Q, THORNBURY_FIGURE)):
        a = ask(served, who, q)
        assert a["behaviour"] == "figure" and a["figure"] == figure, a
        rt = a["run_trace"]
        assert rt["profile"] == served.profile.id and rt["graph"]["artifact"] == served.profile.model_hash(), rt["graph"]
    assert m.profile.model_hash() != t.profile.model_hash()


def test_a_token_of_one_profile_s_is_no_token_on_the_other(side_by_side):
    m, t = side_by_side
    assert m.http.get("/whoami", headers=m.token("analyst_enterprise")).status_code == 200
    assert t.http.get("/whoami", headers=t.token("actuary")).status_code == 200
    assert t.http.get("/whoami", headers=m.token("analyst_enterprise")).status_code == 401
    assert m.http.get("/whoami", headers=t.token("actuary")).status_code == 401
    assert t.http.get("/whoami", headers=m.token("pipeline_tagger")).status_code == 401, "a principal of the same name is no help"


def test_one_browser_keeps_a_session_on_each_page(side_by_side):
    """The README's two pages in one browser, which keeps cookies per host, not per port (RFC 6265): each profile's
    process names its session cookie after its profile, so signing in on one page leaves the other signed in, and
    signing out of one signs out of that one alone."""
    m, t = side_by_side
    pages = {"http://127.0.0.1:8000": m, "http://127.0.0.1:8001": t}
    browser = httpx.Client(mounts={base: httpx.HTTPTransport(uds=str(s.sock)) for base, s in pages.items()}, timeout=120)
    try:
        (mp, _), (tp, _) = pages.items()
        for base, served, who in ((mp, m, "analyst_enterprise"), (tp, t, "actuary")):
            token = served.token(who)["authorization"].split(None, 1)[1]
            assert browser.post(f"{base}/session", json={"token": token}).status_code == 200
        assert len({c.name for c in browser.cookies.jar}) == 2 and {c.domain for c in browser.cookies.jar} == {"127.0.0.1"}
        assert browser.get(f"{mp}/whoami").json()["id"] == "analyst_enterprise"
        assert browser.get(f"{tp}/whoami").json()["id"] == "actuary"
        for base, q, figure in ((mp, MERIDIAN_Q, MERIDIAN_FIGURE), (tp, THORNBURY_Q, THORNBURY_FIGURE)):
            r = browser.post(f"{base}/answer", json={"question": q})
            assert r.status_code == 200 and r.json()["figure"] == figure, r.text
        assert browser.delete(f"{tp}/session").status_code == 200
        assert browser.get(f"{tp}/whoami").status_code == 401 and browser.get(f"{mp}/whoami").status_code == 200
    finally:
        browser.close()


def test_each_lists_its_own_principals_runs_questions_and_title(side_by_side):
    m, t = side_by_side
    for served, client in ((m, m.profile.client), (t, THORNBURY)):
        assert served.http.get("/principals").status_code == 401
        assert {p["id"] for p in served.http.get("/principals", headers=served.token(served.profile.asker())).json()} == identities(client)
        pack = yaml.safe_load((served.profile.clients / client / "pack.yaml").read_text())
        about = served.http.get("/profile").json()
        assert (about["profile"], about["client"], about["title"]) == (served.profile.id, client, pack["manifest"]["title"])
        assert about["units"] == [s["id"] for s in pack["sources"]]
        assert about["shell"] == {"logo": False, "portal_url": None}, "alone, with no logo: the page's text brand, one product"
    assert [r["run_id"] for r in m.http.get("/ab").json()["runs"]] == ["20261014T000000Z-abc123"]
    assert t.http.get("/ab").json() == {"runs": [], "baseline": None}, "no A/B baseline: a fixture profile"
    asked = {q for s in t.http.get("/golden").json()["sets"] for q in s["questions"]}
    fixture = json.loads((DEMO / "clients" / THORNBURY / "fixture" / "golden.json").read_text())
    assert asked == {i["question"] for i in fixture} and THORNBURY_Q in asked and MERIDIAN_Q not in asked
    assert MERIDIAN_Q in {q for s in m.http.get("/golden").json()["sets"] for q in s["questions"]}


def test_thornbury_asked_a_meridian_question_abstains_with_no_warehouse_query(side_by_side):
    _, t = side_by_side
    a = ask(t, "actuary", MERIDIAN_Q)
    assert a["behaviour"] == "abstain" and not a.get("sql") and not a.get("parts") and a.get("figure") is None, a
    planned = [p for s in a["trace"] if s.get("step") == "plan" for p in s.get("parts") or []]
    assert not [p for p in planned if p.get("sql")], "no warehouse query was planned, let alone run"


def overlay(served: Served, who: str, q: str) -> str:
    return ask(served, who, q)["run_trace"]["graph"]["overlay"]


def test_a_steward_decision_on_one_profile_changes_only_its_own_overlay(side_by_side):
    m, t = side_by_side
    before = overlay(m, "analyst_enterprise", MERIDIAN_Q), overlay(t, "actuary", THORNBURY_Q)
    queue = t.http.get("/steward/queue", headers=t.token("claims_steward")).json()
    mailbox = next(i for i in queue if i["family"] == "privacy" and i["value"] == "personal_data:email")
    assert mailbox["evidence"] == "fnol@thornbury.example" and mailbox["assessment"] == "likely_functional", \
        "Thornbury's functional mailbox, its pack's, kept in clear"
    r = t.http.post("/steward/decide", json={"tag": mailbox["tag"], "decision": "reject"}, headers=t.token("claims_steward"))
    assert r.status_code == 200, r.text
    after = overlay(m, "analyst_enterprise", MERIDIAN_Q), overlay(t, "actuary", THORNBURY_Q)
    assert after[0] == before[0] and after[1] != before[1]
    journal = t.profile.overlay_dir() / "decisions.jsonl"
    assert journal.is_file() and not (m.profile.overlay_dir() / "decisions.jsonl").exists()
    assert {json.loads(line)["client"] for line in journal.read_text().splitlines()} == {THORNBURY}


def test_make_agent_make_eval_and_make_ab_ask_thornbury_s_questions_as_its_own_querying_identity(world):
    """make agent, make eval and make ab named no principal and asked as Meridian's analyst_enterprise, which Thornbury
    does not have: the agent refused both arms, and the eval and the A/B stopped. With none named, a command asks as the
    querying identity the profile's deployment names (asker, deploy/thornbury/identities.yaml: actuary), and so does the
    engine. On a copy of Thornbury's build that no API serves."""
    from kge.services.ask.engine.core import Engine
    from kge.pipeline.profile import resolve
    alone = world[0] / ALONE
    env = {**{k: v for k, v in os.environ.items() if k not in ("PRINCIPAL", "MAKEFLAGS", "MAKELEVEL", "MFLAGS")},
           "PROFILES": str(alone), "PROFILE": THORNBURY}

    def make(*args) -> str:
        r = subprocess.run(["make", "-s", *args, f"PY={sys.executable}"], capture_output=True, text=True, cwd=DEMO, env=env, timeout=600)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        return r.stdout
    said = make("agent", f"Q={THORNBURY_Q}")
    assert said.count(f"figure {THORNBURY_FIGURE:,.2f}") == 2 and "not a querying identity" not in said, said
    said = make("eval")
    assert "asked as actuary" in said, said
    graph = next(line for line in said.splitlines() if line.startswith("graph "))
    scores = [tuple(map(int, x.split("/"))) for x in graph.split()[1:]]
    assert scores and all(got == of for got, of in scores) and sum(of for _, of in scores) == 5, graph
    make("ab", "ARMS=graph,treatment", "REPEATS=1")
    runs = sorted((world[0] / "tcopy" / "stores" / THORNBURY / "run" / "runs").iterdir())
    assert len(runs) == 1 and json.loads((runs[0] / "manifest.json").read_text())["principal"] == "actuary"
    e = Engine(resolve(THORNBURY, registry=alone))
    try:
        a = e.answer(THORNBURY_Q)
        assert (a["principal"], a["behaviour"], a.get("figure")) == ("actuary", "figure", THORNBURY_FIGURE), a
    finally:
        e.close()


def under(path: Path, roots) -> bool:
    p = Path(path).resolve() if Path(path).exists() else Path(path)
    return any(p == r or r in p.parents for r in roots)


def test_every_path_a_profile_names_sits_under_its_own_roots_and_none_under_the_other_s(world):
    """Its identities are the deployment's file for it, and its estate's folder holds the simulated client's systems (the
    demo's own estate is build/ itself, as the layout has it): each is its own, and neither sits under the other's
    roots."""
    _, _, meridian, thornbury = world
    for p, other in ((meridian, thornbury), (thornbury, meridian)):
        own = [Path(r) for r in p.roots().values()] + [Path(r).resolve() for r in p.roots().values()]
        theirs = [Path(r).resolve() for r in other.roots().values()]
        paths = p.paths()
        assert set(ROOT_NAMES) == set(p.roots())
        for name, path in paths.items():
            if name in ("identities", "estate"):
                assert path.resolve() in (DEMO / "deploy" / p.client / "identities.yaml", p.warehouses.parent.resolve())
            else:
                assert under(path, own), (p.id, name, path)
            assert not under(path, theirs), (p.id, name, path)
    assert thornbury.identities == DEMO / "deploy" / THORNBURY / "identities.yaml"


def test_thornbury_s_pack_sits_outside_the_demo_folder(world):
    _, _, _, thornbury = world
    assert DEMO not in thornbury.clients.resolve().parents and (thornbury.clients / THORNBURY / "pack.yaml").is_file()


def moved(profile):
    """The profile's roots moved away, and back after."""
    class Away:
        def __enter__(self):
            self.held = [(Path(r), Path(f"{r}.away")) for r in profile.roots().values()]
            for r, away in self.held:
                r.rename(away)
            return self

        def __exit__(self, *exc):
            for r, away in self.held:
                away.rename(r)
    return Away()


def test_each_profile_runs_with_the_other_s_roots_moved_away(world, side_by_side):
    """After the servers stop: each engine opens its own graph, warehouses and run state, and answers, with every root of
    the other profile's out of the way."""
    from kge.services.ask.engine.core import Engine
    _, _, meridian, thornbury = world
    for served in side_by_side:
        served.stop()
    for p, other, who, q, figure in ((meridian, thornbury, "analyst_enterprise", MERIDIAN_Q, MERIDIAN_FIGURE),
                                     (thornbury, meridian, "actuary", THORNBURY_Q, THORNBURY_FIGURE)):
        with moved(other):
            assert not any(Path(r).exists() for r in other.roots().values())
            e = Engine(p)
            try:
                a = e.answer(q, who)
                assert a["behaviour"] == "figure" and a["figure"] == figure, (p.id, a)
            finally:
                e.close()


def test_replay_refuses_another_client_s_journal_and_the_loader_another_client_s_artifact(world, side_by_side, tmp_path):
    _, registry, meridian, thornbury = world
    for served in side_by_side:
        served.stop()
    mine = meridian.overlay_dir() / "decisions.jsonl"
    mine.parent.mkdir(parents=True, exist_ok=True)
    held = mine.read_bytes() if mine.exists() else None
    theirs = thornbury.overlay_dir() / "decisions.jsonl"
    mine.write_bytes(theirs.read_bytes() if theirs.exists() else (json.dumps({"client": THORNBURY}) + "\n").encode())
    try:
        r = run("kge/pipeline/run.py", registry=registry, profile=meridian.id)
        assert r.returncode == 2 and THORNBURY in r.stderr and "Nothing was written" in r.stderr, r.stderr
    finally:
        mine.unlink()
        if held is not None:
            mine.write_bytes(held)
    art = meridian.current()
    r = run("kge/graph/load.py", "--artifact", art, registry=registry, profile=THORNBURY)
    assert r.returncode == 2 and f"it is '{meridian.client}''s artifact, and this profile is {THORNBURY}'s" in r.stderr, r.stderr
    assert "Nothing was loaded" in r.stderr


def test_the_registry_refuses_two_profiles_whose_roots_overlap(world, tmp_path):
    from kge.pipeline.profile import ProfileError, read
    root, registry, meridian, _ = world
    reg = yaml.safe_load(registry.read_text())
    reg["profiles"][THORNBURY]["warehouses"] = reg["profiles"][meridian.id]["warehouses"]
    bad = root / "overlapping.yaml"
    bad.write_text(yaml.safe_dump(reg, sort_keys=False))
    with pytest.raises(ProfileError, match=f"{THORNBURY}'s warehouses root .* is {meridian.id}'s warehouses root"):
        read(bad)


def test_no_platform_file_names_thornbury():
    from tests.unit.test_client_nouns import NOUN, nouns, platform_files
    pack = yaml.safe_load((DEMO / "clients" / THORNBURY / "pack.yaml").read_text())
    ours = {THORNBURY, *(s["id"] for s in pack["sources"])}
    assert ours <= set(nouns()), "the noun gate reads every pack's nouns"
    found = {p.relative_to(DEMO).as_posix(): sorted({n.lower() for n in NOUN.findall(p.read_text())} & ours) for p in platform_files()}
    assert {k: v for k, v in found.items() if v} == {}


def test_thornbury_shares_no_noun_with_meridian():
    packs = {c: yaml.safe_load((DEMO / "clients" / c / "pack.yaml").read_text()) for c in ("meridian", THORNBURY)}
    nouns = {c: {p["manifest"]["client"], *(s["id"] for s in p["sources"]), *(g["id"] for g in p["glossary"])} for c, p in packs.items()}
    assert nouns["meridian"].isdisjoint(nouns[THORNBURY])
    assert identities(THORNBURY) == {"actuary", "claims_steward", "pipeline_tagger", "knowledge_engineer"}


def test_the_commit_that_added_thornbury_touched_only_its_folder_deploy_the_tests_and_the_readme():
    """R8: a second client is config. The commit that added clients/thornbury/pack.yaml changed no platform file. A history
    that squashed it into others (a merged pull request) has no such commit to look at."""
    top = DEMO.parents[1]
    rel = (DEMO / "clients" / THORNBURY / "pack.yaml").relative_to(top).as_posix()
    log = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H%x09%s", "--", rel], cwd=top, capture_output=True, text=True)
    if log.returncode != 0 or not log.stdout.strip():
        pytest.skip(f"no git history that adds {rel} here")
    sha, subject = log.stdout.strip().splitlines()[-1].split("\t", 1)
    if "#39 item 11" not in subject:
        pytest.skip(f"the commit that adds {rel} is {subject!r}, not item 11's own: a squashed history")
    files = subprocess.run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha], cwd=top, capture_output=True,
                           text=True).stdout.split()
    demo = DEMO.relative_to(top).as_posix() + "/"
    allowed = (f"{demo}clients/{THORNBURY}/", f"{demo}deploy/", f"{demo}tests/", f"{demo}README.md")
    assert files and all(f.startswith(allowed) for f in files), [f for f in files if not f.startswith(allowed)]


def test_the_readme_says_how_to_run_both_and_what_the_exit_test_holds():
    readme = " ".join((DEMO / "README.md").read_text().split())
    for needle in ("make demo PROFILE=thornbury PORT=8001", "tests/integration/test_profiles.py", "Thornbury Mutual Casualty",
                   "clients/thornbury/fixture/", "kge/estate/from_files.py", "401", "make eval PROFILE=thornbury",
                   f'make agent PROFILE=thornbury Q="{THORNBURY_Q}"', "`asker`"):
        assert needle in readme, needle
