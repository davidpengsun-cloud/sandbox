"""The client's graph store holds a graph per compiled artifact, and current names the one served (issue #39 item 10).

    .venv/bin/python -m pytest tests/integration/test_release.py -q

Each artifact is loaded into a graph of its own, in a folder of the client's
graph store named by its model hash, build/stores/<client>/<hash>/graph.<store>,
and its load's last step writes the folder's LOADED marker. The runtime serves
the graph of the artifact the client's current pointer names,
build/artifacts/<client>/current (a symlink to <hash>), and reads it once, when
it starts. The steps are separate commands, each taking only the profile: make
lock, make check (checks only, writes nothing), make compile, make load (a
candidate for the pack's hash, the overlay replayed: it never touches another
hash's folder, so the live graph and its lock are left alone) and make release
(refused without LOADED; swaps current atomically). make graph is the four in
a row. make margin HASH=<hash> checks a candidate before it is released (its
GATE), and make sign-off PRINCIPAL=<steward> signs off one that passed (its
SIGNOFF), onboarding's steps 7 and 8 (issue #40): make release refuses a hash
without both, unless it was released before (its RELEASED marker), and make
graph, the developer's bootstrap, releases without them and says so.

The module's own build is a scratch copy of the demo's, serving its current
graph through the API while a pack edit is loaded beside it, and released, and
the old one released again; the rest read files, or a scratch copy each.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from kge.services.ask.engine.core import Engine
from kge.pipeline.profile import ProfileError, resolve
from kge.adapters.graphstore import STORE
from tests.scratch import BUILD, CLIENT, compile_pack, profile_of, registry, running_as, scratch_build, script, write_registry

DEMO = BUILD.parent
ALIAS = "    - {phrase: takings, term: revenue}\n"
ANCHOR = "    - {phrase: revenue, term: revenue}\n"
B40 = DEMO / "clients" / CLIENT / "baselines" / "issue-40.json"
Q = "Total retail revenue for June 2025"
TAKINGS = "Total retail takings for June 2025"


def edited_pack(clients: Path) -> Path:
    """A clients folder holding the client's pack with the README's pack edit, an alias, and its lock: the clients root."""
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    pack = clients / CLIENT / "pack.yaml"
    text = pack.read_text().replace(ALIAS, "")
    assert text.count(ANCHOR) == 1
    pack.write_text(text.replace(ANCHOR, ANCHOR + ALIAS))
    return clients


def made(build: Path, *args, check: bool = True) -> subprocess.CompletedProcess:
    """make, for build's profile (its own registry), on the store this run is on."""
    env = {k: v for k, v in os.environ.items() if k != "PROFILE"} | {"PROFILES": str(registry(build))}
    r = subprocess.run(["make", *args, f"PY={sys.executable}"], capture_output=True, text=True,
                       cwd=DEMO, env=env, timeout=600)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def files(folder: Path) -> dict[str, tuple]:
    """Each file under folder, with its inode, time and size: what a process that wrote nothing there leaves as it was."""
    return {p.relative_to(folder).as_posix(): (os.lstat(p).st_ino, os.lstat(p).st_mtime_ns, os.lstat(p).st_size)
            for p in sorted(folder.rglob("*"))}


def current_of(build: Path) -> str | None:
    link = profile_of(build).artifacts / CLIENT / "current"
    return os.readlink(link) if link.is_symlink() else None


def marker(build: Path, model_hash: str) -> dict:
    path = profile_of(build).store / CLIENT / model_hash / "LOADED"
    return json.loads(path.read_text()) if path.is_file() else {}


def released(build: Path, *args) -> tuple[int, str, str]:
    """kge/graph/release.py, in this process, for build's profile."""
    from kge.graph import release
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with running_as(build), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = release.main(list(args))
    return code, out.getvalue(), err.getvalue()


class Served:
    """The API on a scratch build, as make serve runs it, which a test can restart: a process reads current when it starts."""

    def __init__(self, build: Path, mp: pytest.MonkeyPatch):
        import kge.app as appmod
        self.appmod, self.profile, self.client = appmod, profile_of(build), None
        mp.setenv("MODEL_PROVIDER", "none")
        mp.setattr(appmod, "PROFILE", self.profile)
        self.start()

    def start(self) -> None:
        for name in ("_engine", "_combined", "_agent", "_ids"):
            setattr(self.appmod, name, None)
        self.client = TestClient(self.appmod.app)
        self.client.__enter__()

    def stop(self) -> None:
        if self.client is not None:
            self.client.__exit__(None, None, None)
            self.client = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def ask(self, question: str) -> dict:
        from kge.services.identity.service import token_for
        who = {"authorization": f"Bearer {token_for(self.profile.run_dir(), 'analyst_enterprise')}"}
        r = self.client.post("/answer", json={"question": question}, headers=who)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    """A copy of the demo's build whose API serves its current graph, H0, while make compile load loads the README's pack
    edit beside it as a candidate, H1: H0's folder before and after, and what the load printed."""
    root = tmp_path_factory.mktemp("release")
    build = scratch_build(root / "build")
    write_registry(build, clients=edited_pack(root / "clients"))
    h0 = profile_of(build).model_hash()
    assert h0 and marker(build, h0).get(STORE), "the copy serves a loaded graph"
    live = profile_of(build).store / CLIENT / h0
    with pytest.MonkeyPatch.context() as mp:
        api = Served(build, mp)
        try:
            before = files(live)
            assert api.ask(Q)["run_trace"]["graph"]["artifact"] == h0
            loaded = made(build, "-s", "compile", "load")
            h1 = Path(loaded.stdout.splitlines()[0]).name
            yield SimpleNamespace(build=build, h0=h0, h1=h1, live=live, before=before, after=files(live), loaded=loaded, api=api)
        finally:
            api.stop()


def test_load_makes_a_candidate_beside_the_live_graph_and_never_touches_the_live_hash_s_folder(candidate):
    c = candidate
    assert c.h1 != c.h0 and len(c.h1) == 64, "the pack edit is another model hash"
    assert c.after == c.before, "the live graph's folder (its graph, its sidecars and LOADED) as it was, while the API held it"
    assert current_of(c.build) == c.h0, "load releases nothing"
    assert (profile_of(c.build).store / CLIENT / c.h1 / f"graph.{STORE}").is_file()
    a = c.api.ask(Q)
    assert a["behaviour"] == "figure" and a["run_trace"]["graph"]["artifact"] == c.h0, "the API serves the old hash, and says so"


def test_the_candidate_s_loaded_marker_is_written_last_naming_its_store(candidate):
    c = candidate
    steps = [line for line in c.loaded.stdout.splitlines() if line.startswith("release: ")]
    assert len(steps) == 1 and c.h1 in steps[0] and "LOADED" in steps[0], c.loaded.stdout
    assert c.loaded.stdout.rstrip().endswith(steps[0]), "the marker is the load's last step"
    held = marker(c.build, c.h1)
    assert set(held) == {STORE}, "this store's graph, and no other's"
    entry = held[STORE]
    assert (entry["client"], entry["model_hash"]) == (CLIENT, c.h1) and entry["release"] and entry["chunks"] > 0
    counts, at = json.JSONDecoder().raw_decode(c.loaded.stdout, c.loaded.stdout.index("{"))
    assert counts["model_hash"] == c.h1 and entry["release"] == counts["release"], "the load's, then the pipeline's, then the marker"
    stats, _ = json.JSONDecoder().raw_decode(c.loaded.stdout, c.loaded.stdout.index("{", at))
    assert stats["chunks"] == entry["chunks"] and stats["overlay"]["journal"].startswith(str(profile_of(c.build).overlay_dir()))


def test_a_candidate_s_margin_can_be_checked_before_it_is_released(candidate, capsys):
    from kge.eval import margin
    c = candidate
    with running_as(c.build):
        code = margin.main(["--hash", c.h1, "--repeats", "1", "--sets", "golden", "--baseline", str(B40)])
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    assert f"the graph of {c.h1}" in out.out and "passed: no margin fell" in out.out
    run = Path(out.out.rsplit("report: ", 1)[1].split()[0]).parent
    assert json.loads((run / "manifest.json").read_text())["graph"]["artifact"] == c.h1, "the run names the candidate's artifact"
    assert current_of(c.build) == c.h0 and c.api.ask(Q)["run_trace"]["graph"]["artifact"] == c.h0, "the API kept serving the old"


def test_release_is_refused_until_the_candidate_is_checked_and_signed_off_by_a_steward(candidate):
    from kge.graph import release
    c = candidate
    graph = profile_of(c.build).at(c.h1).graph_path()
    release.write_marker(graph.parent, {}, release.GATE)

    def refused(said: str, *args) -> str:
        r = made(c.build, "-s", *args, check=False)
        assert r.returncode == 2 and "Traceback" not in r.stderr, r.stdout + r.stderr
        for s in (said if isinstance(said, tuple) else (said,)):
            assert s in r.stderr, (s, r.stderr)
        assert current_of(c.build) == c.h0, "current as it was"
        return r.stderr
    err = refused((f"{c.h1} is not through the onboarding gate: not checked", f"make margin HASH={c.h1}",
                   f"make sign-off PRINCIPAL=<steward> HASH={c.h1}", "current is as it was"), "release")
    assert "not signed off" in err
    refused(("not checked", f"make margin HASH={c.h1}", "Nothing was signed off"), "sign-off", "PRINCIPAL=steward_glossary")
    assert release.marked(graph, release.SIGNOFF) is None
    release.mark_gate(graph, {"passed": False, "run_id": "r-failed", "model_hash": c.h1})
    refused(("its post-load check failed (run r-failed)", "it stays a candidate"), "sign-off", "PRINCIPAL=steward_glossary")
    refused(("its post-load check failed (run r-failed)", "it stays a candidate", "current is as it was"), "release")
    release.mark_gate(graph, {"passed": True, "run_id": "r-passed", "model_hash": c.h1})
    refused(("passed its post-load check (run r-passed), not signed off", f"make sign-off PRINCIPAL=<steward> HASH={c.h1}"), "release")
    r = made(c.build, "-s", "sign-off", check=False)
    assert r.returncode == 2 and "make sign-off PRINCIPAL=<steward>" in r.stderr and "certification" in r.stderr, r.stderr
    refused(("analyst_retail may not perform certification", "admin, steward, steward_glossary"), "sign-off", "PRINCIPAL=analyst_retail")
    refused(("nosuch is no principal of the graph", "admin, steward, steward_glossary"), "sign-off", "PRINCIPAL=nosuch")
    assert release.marked(graph, release.SIGNOFF) is None, "a refused sign-off writes nothing"
    r = made(c.build, "-s", "sign-off", "PRINCIPAL=steward_glossary")
    assert f"{c.h1} signed off by steward_glossary" in r.stdout and "run r-passed" in r.stdout and "SIGNOFF" in r.stdout, r.stdout
    signed = release.marked(graph, release.SIGNOFF)
    assert {k: signed[k] for k in ("principal", "run_id", "model_hash")} == {"principal": "steward_glossary", "run_id": "r-passed",
                                                                             "model_hash": c.h1} and signed["at"].endswith("Z")
    assert json.loads((graph.parent / "SIGNOFF").read_text()) == {STORE: signed}, "an entry per store, as LOADED"
    release.mark_gate(graph, {"passed": True, "run_id": "r-again", "model_hash": c.h1})
    refused(("signed off on check run r-passed, and its check since is run r-again", "make sign-off"), "release")


def test_release_swaps_current_once_signed_off_and_a_restart_serves_the_new_hash_then_the_old_again_with_no_new_sign_off(candidate):
    from kge.graph import release
    c = candidate
    graph = profile_of(c.build).at(c.h1).graph_path()
    before = c.api.ask(TAKINGS)
    assert before["behaviour"] != "figure", "the old hash has no such phrase"
    release.mark_gate(graph, {"passed": True, "run_id": "r-release", "model_hash": c.h1})
    made(c.build, "-s", "sign-off", "PRINCIPAL=steward_glossary", f"HASH={c.h1}")
    r = made(c.build, "-s", "release")
    assert current_of(c.build) == c.h1 and c.h1 in r.stdout and c.h0 in r.stdout, r.stdout
    assert "signed off by steward_glossary on check run r-release" in r.stdout, r.stdout
    assert {k: v for k, v in release.marked(graph, release.RELEASED).items() if k != "at"} == {
        "model_hash": c.h1, "signed_off_by": "steward_glossary", "run_id": "r-release"}
    old = profile_of(c.build).at(c.h0).graph_path()
    assert release.marked(old, release.RELEASED)["model_hash"] == c.h0, "the hash current named was released: it says so now"
    assert c.api.ask(TAKINGS)["run_trace"]["graph"]["artifact"] == c.h0, "the running API reads current only when it starts"
    c.api.restart()
    a = c.api.ask(TAKINGS)
    assert a["run_trace"]["graph"]["artifact"] == c.h1 and a["behaviour"] == "figure" and a["figure"] == pytest.approx(13287.67)
    assert release.marked(old, release.SIGNOFF) is None and release.gate(old) is None, "never checked or signed off"
    r = made(c.build, "-s", "release", f"HASH={c.h0}")
    assert current_of(c.build) == c.h0 and "released before" in r.stdout, r.stdout
    assert c.api.ask(TAKINGS)["run_trace"]["graph"]["artifact"] == c.h1
    c.api.restart()
    assert c.api.ask(Q)["run_trace"]["graph"]["artifact"] == c.h0 and c.api.ask(TAKINGS)["behaviour"] != "figure"
    assert sorted(p.name for p in (profile_of(c.build).store / CLIENT).iterdir() if len(p.name) == 64) == sorted([c.h0, c.h1]), \
        "old folders stay"


def test_release_refuses_a_hash_that_is_not_loaded_and_leaves_current_as_it_was(tmp_path):
    build = scratch_build(tmp_path / "build")
    write_registry(build, clients=edited_pack(tmp_path / "clients"))
    h0 = current_of(build)
    art = compile_pack(profile_of(build).artifacts, profile_of(build).clients)
    h1 = art.name

    def refused(model_hash: str, said: str) -> None:
        code, out, err = released(build, "--hash", model_hash)
        assert code == 2 and said in err and "Traceback" not in err, err
        assert current_of(build) == h0, "current as it was"
    refused("../" + h0, "is not a model hash")
    refused("0" * 64, "no artifact")
    refused(h1, "not LOADED")
    assert "make load" in released(build, "--hash", h1)[2]
    r = script("kge/graph/load.py", "--artifact", art, build=build)
    assert r.returncode == 0, r.stderr
    refused(h1, "not LOADED")
    other = "retired"
    folder = profile_of(build).store / CLIENT / h1
    (folder / "LOADED").write_text(json.dumps({other: {"client": CLIENT, "model_hash": h1, "release": "x", "chunks": 1}}))
    refused(h1, f"not LOADED on {STORE}")
    code, out, err = released(build, "--hash", h1, "--mark-loaded")
    assert code == 0, err
    assert set(marker(build, h1)) == {other, STORE}, "a removed store's entry is never this graph's, and is kept"
    assert (f"current is {h0}: make margin HASH={h1} checks it, make sign-off PRINCIPAL=<steward> HASH={h1} signs it off, "
            f"then make release HASH={h1} swaps current to it") in out, "make load's next step is the onboarding gate's"
    term = art / "data" / "nodes" / "Term.csv"
    kept = term.read_bytes()
    term.write_bytes(kept + b"x\n")
    refused(h1, "not whole")
    term.write_bytes(kept)
    code, out, err = released(build, "--hash", h1)
    assert code == 2 and "not through the onboarding gate" in err and current_of(build) == h0, err
    code, out, err = released(build, "--hash", h1, "--ungated")
    assert code == 0 and current_of(build) == h1, err
    assert "released without the onboarding gate" in out, out
    from kge.graph import release
    entry = release.marked(folder / f"graph.{STORE}", release.RELEASED)
    assert {k: v for k, v in entry.items() if k != "at"} == {"model_hash": h1, "signed_off_by": None, "run_id": None}


@pytest.mark.parametrize("ungated", [False, True], ids=["gated", "ungated"])
def test_release_refuses_an_uncertified_draft_even_when_the_gate_is_bypassed(tmp_path, ungated):
    """review_drafts is deliberately called before the release function branches on ungated, so it holds either way."""
    from kge.graph import release
    folder = tmp_path / ("a" * 64); folder.mkdir()
    (folder / "drafts.json").write_text(json.dumps({"format": 1, "items": [
        {"unit": "leasing", "id": "lease-rentals", "state": "accepted"}]}))
    with pytest.raises(release.Unreleasable, match=r"leasing/lease-rentals \(accepted\)"):
        release.review_drafts(folder, "a" * 64, released_before=False)
    assert isinstance(ungated, bool)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"format": 1, "items": {}}'])
def test_release_fails_closed_on_malformed_drafts_metadata(tmp_path, content):
    from kge.graph import release
    folder = tmp_path / ("b" * 64); folder.mkdir()
    (folder / "drafts.json").write_text(content)
    with pytest.raises(release.Unreleasable, match="(unreadable|invalid) drafts.json"):
        release.review_drafts(folder, "b" * 64, released_before=False)


def test_the_marker_names_only_a_graph_loaded_from_that_artifact(tmp_path):
    build = scratch_build(tmp_path / "build")
    prof = profile_of(build)
    fake = "f" * 64
    code, _, err = released(build, "--hash", fake, "--mark-loaded")
    assert code == 2 and "no graph" in err and "make load" in err, err
    (prof.store / CLIENT / fake).mkdir()
    shutil.copy2(prof.graph_path(), prof.store / CLIENT / fake / prof.graph_path().name)
    code, _, err = released(build, "--hash", fake, "--mark-loaded")
    assert code == 2 and f"loaded from {prof.model_hash()}" in err, err
    assert not (prof.store / CLIENT / fake / "LOADED").exists()


def test_a_load_takes_its_store_s_markers_out_before_it_replaces_the_graph_and_only_once_it_holds_the_lock(tmp_path):
    from kge.graph import release
    from kge.adapters.graphstore import open_db
    build = scratch_build(tmp_path / "build")
    prof = profile_of(build)
    h0, art, graph = prof.model_hash(), prof.current(), prof.graph_path()
    release.mark_gate(graph, {"passed": True, "run_id": "r1", "model_hash": h0})
    for name, entry in ((release.SIGNOFF, {"principal": "steward_glossary", "run_id": "r1", "model_hash": h0}),
                        (release.RELEASED, {"model_hash": h0, "signed_off_by": "steward_glossary", "run_id": "r1"})):
        release.write_marker(graph.parent, {STORE: entry}, name)
    db = open_db(graph)
    try:
        r = script("kge/graph/load.py", "--artifact", art, build=build)
        assert r.returncode == 2 and "locked" in r.stderr, r.stderr
        assert STORE in marker(build, h0), "a load the lock stopped took nothing out"
        assert all(release.marked(graph, n) for n in (release.GATE, release.SIGNOFF, release.RELEASED))
    finally:
        db.close()
    r = script("kge/graph/load.py", "--artifact", art, build=build)
    assert r.returncode == 0, r.stderr
    assert STORE not in marker(build, h0), "a graph whose load has not finished is not LOADED"
    for name in (release.GATE, release.SIGNOFF, release.RELEASED):
        assert release.marked(graph, name) is None and not (graph.parent / name).exists(), name
    assert released(build, "--hash", h0)[0] == 2
    assert released(build, "--hash", h0, "--mark-loaded")[0] == 0 and STORE in marker(build, h0)
    code, out, err = released(build, "--hash", h0, "--sign-off", "--as", "steward_glossary")
    assert code == 2 and "not checked" in err and f"make margin HASH={h0}" in err, "reloaded: its sign-off went with its check"
    code, out, err = released(build, "--hash", h0, "--ungated")
    assert code == 0 and "was current already" in out and "released without the onboarding gate" in out, out + err
    assert {k: v for k, v in release.marked(graph, release.RELEASED).items() if k != "at"} == {
        "model_hash": h0, "signed_off_by": None, "run_id": None}, "released again, ungated, and it says so"


def test_the_markers_beside_loaded_are_an_entry_per_store_and_a_reload_drops_each_of_the_store_s(tmp_path):
    from kge.graph import release
    folder = tmp_path / ("c" * 64)
    folder.mkdir()
    graph, other = folder / "graph.ladybug", folder / "graph.retired"
    names = (release.LOADED, release.GATE, release.SIGNOFF, release.RELEASED)
    for name in names:
        release.write_marker(folder, {"ladybug": {"model_hash": "c" * 64, "name": name}, "retired": {"model_hash": "c" * 64}}, name)
    assert [release.marked(graph, n)["name"] for n in names] == list(names)
    release.unmark(graph)
    assert all(release.marked(graph, n) is None and release.marked(other, n) for n in names), "the other store's kept"
    first_reload = release.marked(graph, release.RELOADED)
    assert first_reload and release.marked(other, release.RELOADED) is None
    release.unmark(other)
    assert sorted(p.name for p in folder.iterdir()) == [release.RELOADED], "only load invalidations stay"
    assert release.marked(graph, release.RELOADED) == first_reload
    assert release.marked(other, release.RELOADED)


def two_artifacts(tmp_path: Path):
    """A registry of one profile, alpha, with two artifacts and current naming the first: no graph."""
    path = tmp_path / "deploy" / "profiles.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"default": "alpha", "profiles": {"alpha": {
        "client": "alpha", "clients": "../clients", "artifacts": "../build/artifacts", "store": "../build/stores",
        "landing": "../build/landing", "warehouses": "../build/warehouses", "identities": "alpha/identities.yaml"}}}))
    home = tmp_path / "build" / "artifacts" / "alpha"
    for h in ("ab12", "cd34"):
        (home / h).mkdir(parents=True)
        (home / h / "manifest.json").write_text(json.dumps({"client": "alpha", "model_hash": h}))
    (home / "current").symlink_to("ab12")
    return resolve("alpha", registry=path), home


def test_the_swap_is_atomic_a_new_link_renamed_over_the_old(tmp_path, monkeypatch):
    """A reader never misses the current link while it is swapped. macOS's readlink can say EINVAL while a rename lands
    over a link, so there an EINVAL counts only if lstat misses the link too; Linux's never does, so every error counts."""
    from kge.graph import release
    prof, home = two_artifacts(tmp_path)
    seen, missed, stop = set(), [], threading.Event()

    def watch():
        while not stop.is_set():
            try:
                seen.add(os.readlink(home / "current"))
            except OSError as exc:
                if not (exc.errno == errno.EINVAL and sys.platform == "darwin"):
                    missed.append(exc)
                    continue
                try:
                    os.lstat(home / "current")
                except OSError as gone:
                    missed.append(gone)
    t = threading.Thread(target=watch)
    t.start()
    try:
        for i in range(300):
            release.swap(prof, ("cd34", "ab12")[i % 2])
    finally:
        stop.set()
        t.join()
    assert not missed and seen == {"ab12", "cd34"} and os.readlink(home / "current") == "ab12"
    assert sorted(p.name for p in home.iterdir()) == ["ab12", "cd34", "current"], "no temporary link is left"

    def no_unlink(*_a, **_k):
        raise AssertionError("the old link is replaced, never removed first")
    monkeypatch.setattr(os, "unlink", no_unlink)
    monkeypatch.setattr(os, "remove", no_unlink)
    release.swap(prof, "cd34")
    assert os.readlink(home / "current") == "cd34"
    monkeypatch.undo()

    def broken(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        release.swap(prof, "ab12")
    monkeypatch.undo()
    assert os.readlink(home / "current") == "cd34" and sorted(p.name for p in home.iterdir()) == ["ab12", "cd34", "current"], \
        "a swap that failed leaves current as it was, and no temporary link"


def test_a_profile_s_graph_is_in_the_folder_of_the_hash_current_names_read_once_when_pinned(tmp_path):
    prof, home = two_artifacts(tmp_path)
    store = tmp_path / "build" / "stores" / "alpha"
    assert prof.model_hash() == "ab12" and prof.graph_path() == store / "ab12" / f"graph.{STORE}"
    assert prof.at("cd34").graph_path() == store / "cd34" / f"graph.{STORE}" and prof.at("cd34").current() == home / "ab12"
    pinned = prof.pin()
    (home / "current").unlink()
    (home / "current").symlink_to("cd34")
    assert pinned.model_hash() == "ab12" and prof.model_hash() == "cd34", "a pinned profile keeps the hash it read; current moved"
    assert prof.paths()["graph"] == prof.graph_path() and prof.paths()["current"] == home / "cd34"
    for bad in ("../ab12", "AB12", "", "ab12/x"):
        with pytest.raises(ProfileError, match="is not a model hash"):
            prof.at(bad)


def test_with_nothing_released_each_command_says_make_graph(tmp_path):
    prof, home = two_artifacts(tmp_path)
    (home / "current").unlink()
    assert prof.model_hash() is None and "graph" not in prof.paths()
    with pytest.raises(ProfileError) as exc:
        prof.graph_path()
    assert str(exc.value) == (f"profile alpha has released no graph: its current pointer, {home / 'current'}, names no artifact; "
                              "make graph compiles the pack, loads it and releases it")
    with pytest.raises(FileNotFoundError, match="has released no graph"):
        Engine(prof)
    env = {"PROFILES": str(prof.registry), "PROFILE": ""}
    r = subprocess.run([sys.executable, "-m", "kge.pipeline.run"], capture_output=True, text=True, cwd=DEMO, env={**os.environ, **env}, timeout=120)
    assert r.returncode == 2 and "has released no graph" in r.stderr and "make graph" in r.stderr and "Traceback" not in r.stderr, r.stderr


def dry(*args) -> str:
    """The commands make <args> runs, through kge, each module a stand-in: one line each."""
    from tests.unit.test_cli import planned
    return planned(*args)


def order(text: str, *steps: str) -> list[int]:
    at, out = 0, []
    for s in steps:
        at = text.index(s, at)
        out.append(at)
    return out


def test_make_graph_is_the_lock_check_compile_load_and_release_in_a_row():
    graph = dry("graph")
    order(graph, "kge.compiler.lock --check", "kge.compiler.compile", "kge.graph.load --artifact", "kge.pipeline.run --hash",
          "kge.graph.release --hash", "--mark-loaded", "kge.graph.release --hash")
    assert "kge.compiler.compile --check" not in graph, "make graph compiles"
    assert "--mark-loaded" not in graph.rsplit("kge.graph.release --hash", 1)[1], "released last, once the load is LOADED"
    assert graph.rstrip().endswith("--ungated"), "the developer's bootstrap: released without the onboarding gate"


def test_each_step_is_a_command_of_its_own_taking_only_the_profile():
    check = dry("check")
    order(check, "kge.compiler.lock --check", "kge.compiler.compile --check")
    assert "kge.graph.load" not in check and "kge.graph.release" not in check
    load = dry("load")
    order(load, "kge.compiler.compile --check", "kge.graph.load --artifact", "kge.pipeline.run --hash", "kge.graph.release --hash",
          "--mark-loaded")
    assert "kge.compiler.lock" not in load and load.count("kge.graph.release") == 1, "load releases nothing"
    release = dry("release")
    order(release, "kge.compiler.compile --check", "kge.graph.release --hash")
    assert "kge.graph.load" not in release and "--mark-loaded" not in release and "--ungated" not in release
    sign = dry("sign-off", "PRINCIPAL=steward_glossary")
    order(sign, "kge.compiler.compile --check", "kge.graph.release --sign-off --as steward_glossary --hash")
    assert "kge.graph.load" not in sign and "kge.eval.margin" not in sign
    h = "ab" * 32
    assert f"kge.graph.release --sign-off --as steward_glossary --hash {h}" in dry("sign-off", "PRINCIPAL=steward_glossary", f"HASH={h}")
    assert "kge.compiler.compile" not in dry("sign-off", "PRINCIPAL=steward_glossary", f"HASH={h}")
    assert "kge.compiler.compile" not in dry("release", f"HASH={h}") and f"kge.graph.release --hash {h}" in dry("release", f"HASH={h}")
    assert f"/{h}/" in dry("load", f"HASH={h}") and "kge.compiler.compile" not in dry("load", f"HASH={h}")
    assert f"kge.eval.margin --repeats 3 --baseline clients/meridian/baselines/issue-40.json --hash {h}" in dry("margin", f"HASH={h}")
    assert f"kge.pipeline.run --hash {h}" in dry("pipeline", f"HASH={h}")
    make = (DEMO / "Makefile").read_text()
    for target in ("check", "load", "release", "sign-off"):
        assert f" {target} " in make.split(".PHONY:", 1)[1].split("\n", 1)[0] + " ", target


def test_make_check_writes_nothing_where_nothing_has_landed(tmp_path):
    """make check is checks only: on a build that has landed nothing it neither generates the estate nor certifies it. The
    lock's check stops, naming make certify; the pack is still checked against its lock; make check fails, writing nothing."""
    build = tmp_path / "build"
    build.mkdir()
    write_registry(build, artifacts=build / "artifacts", store=build / "stores", landing=build / "landing",
                   warehouses=build / "warehouses")
    from tests.unit.test_cli import planned
    plan = planned("check", PROFILES=str(registry(build)))
    assert "kge.estate.generate" not in plan and "kge.estate.certify" not in plan, plan
    order(plan, "-m kge.compiler.lock --check", "-m kge.compiler.compile --check")
    r = made(build, "check", check=False)
    assert r.returncode != 0, r.stdout + r.stderr
    assert f"nothing has landed for {CLIENT}" in r.stderr and "make certify" in r.stderr, r.stderr
    assert "no findings" in r.stderr and "Nothing was written" in r.stderr, "the pack checked all the same"
    assert sorted(build.rglob("*")) == [build / "profiles.yaml"], "nothing written"


def test_compile_check_checks_the_pack_and_writes_nothing(tmp_path, compiled):
    """The pack is broken whatever version it is on: a literal version goes stale when the pack moves, and the mutation
    then does nothing."""
    out = tmp_path / "out"
    r = script("kge/compiler/compile.py", "--check", "--client", CLIENT, "--clients", DEMO / "clients", "--out", out)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"{out / CLIENT / compiled.name}/" and not out.exists(), "the path it would write, and nothing written"
    assert "not compiled yet" in r.stderr and "make compile" in r.stderr
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    pack = clients / CLIENT / "pack.yaml"
    text, changed = re.subn(r"(?m)^(\s*meta_model_version:\s*)\S+", r"\1one", pack.read_text())
    assert changed == 1, "the pack names its meta_model_version once"
    pack.write_text(text)
    r = script("kge/compiler/compile.py", "--check", "--client", CLIENT, "--clients", clients, "--out", out)
    assert r.returncode == 2 and "S00" in r.stderr and "nothing was written" in r.stderr and not out.exists(), r.stderr


def test_the_readme_says_how_a_candidate_is_loaded_checked_and_released():
    readme = (DEMO / "README.md").read_text()
    running = readme.split("## Running it", 1)[1]
    commands = running.split("```", 2)[1]
    for needed in ("make check", "make load", "make release", "make margin HASH=<hash>", "make release HASH=<hash>",
                   "make sign-off PRINCIPAL=<steward>", "build/stores/meridian/<model_hash>/graph.ladybug",
                   "build/artifacts/meridian/current"):
        assert needed in commands, needed
    lines = commands.splitlines()
    first = lambda prefix: next(i for i, line in enumerate(lines) if line.startswith(prefix))  # noqa: E731
    assert first("make margin HASH=<hash> ") < min(first("make sign-off "), first("make release ")), \
        "the list puts make sign-off or make release before the make margin HASH=<hash> they need"
    para = " ".join(running.split("**Releasing a graph.**", 1)[1].split("\n\n", 1)[0].split())
    for needed in ("`make compile load`", "`make release`", "`LOADED`", "restart", "`make release HASH=<old hash>`", "the trace",
                   "`make margin HASH=<hash>`", "`os.replace`", "`.venv/bin/python -m pytest tests/integration/test_release.py -q`",
                   "`build/stores/meridian/<model_hash>/`", "never touches", "`make sign-off PRINCIPAL=<steward>`", "`GATE`",
                   "`SIGNOFF`", "`RELEASED`", "certification", "released without the onboarding gate", "no new sign-off"):
        assert needed in para, needed
    assert "make sign-off" in running.split("**The lock.**", 1)[1].split("\n\n", 1)[0], "it runs beside the API"
