from types import SimpleNamespace

import pytest

from kge.graph import history, load, release
from kge.pipeline import objstore
from tests.unit.test_data_governance import governed as governed
from tests.unit.test_objstore import image as image, onboard_without_units
from tests.unit.test_released_generation import HASH, profile
from tests.unit.test_versions import _make_artifact, prof as prof


@pytest.mark.parametrize("kind", ["preview", "export", "query"])
def test_context_selector_does_not_filter_another_units_direct_read(governed, kind):
    from kge.services.data import service

    context = governed.semantics.contexts["measure"]
    argument = "SELECT id FROM facts ORDER BY id" if kind == "query" else "facts"
    object.__setattr__(context, "fact", "another.month_end")
    result = getattr(service, kind)(governed, "reader", "unit", argument)
    assert sorted(row["id"] for row in result.rows) == [1, 2]
    object.__setattr__(context, "fact", "unit.month_end")
    result = getattr(service, kind)(governed, "reader", "unit", argument)
    assert [row["id"] for row in result.rows] == [1]


@pytest.mark.parametrize("store", ["ladybug", "bigquery"])
@pytest.mark.parametrize("failure", [None, "load", "margin"])
def test_reload_loads_unchanged_current_and_checks_without_publishing(image, tmp_path, store, failure):
    h, done, calls = onboard_without_units(image, tmp_path, store, store)
    assert done.returncode == 0 and calls == [["check", "compile"]]
    env = {"PROFILES": str(tmp_path / "profiles.yaml"), "GRAPH_STORE": store}
    if failure:
        env.update(MAKE_EXIT="2", MAKE_EXIT_TARGET=failure)
    done, calls = image("reload", store=False, env=env)
    assert done.returncode == (2 if failure else 0), done.stderr
    expected = [["load", f"HASH={h}"]]
    if failure != "load":
        expected.append(["margin", f"HASH={h}"])
    assert [call["args"] for call in calls] == expected
    assert "reload" in objstore.JOBS


@pytest.mark.parametrize("gate", [None, False, True])
def test_reloaded_current_needs_a_new_gate_and_signoff(tmp_path, monkeypatch, gate):
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    prof = profile(tmp_path)
    folder = prof.artifacts / prof.client / HASH
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("{}")
    (folder.parent / "current").symlink_to(HASH)
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.touch()
    release.put(graph, release.LOADED, {"client": prof.client, "model_hash": HASH})
    if gate is not None:
        release.put(graph, release.GATE, {"model_hash": HASH, "passed": gate, "run_id": "new"})
    monkeypatch.setattr(load, "read_artifact", lambda *_: {"model_hash": HASH})
    monkeypatch.setattr(release, "review_drafts", lambda *_: None)
    monkeypatch.setattr(history, "review_proposals", lambda *_: None)
    monkeypatch.setattr(release, "estate_of", lambda *_: SimpleNamespace(check=lambda *_: None))
    with pytest.raises(release.Unreleasable, match="not through the onboarding gate"):
        release.release(prof, HASH)
    assert prof.model_hash() == HASH
    assert release.marked(graph, release.RELEASED) is None


@pytest.mark.parametrize("margin_exit", [0, 1, 2])
def test_reload_pulls_landing_saves_work_and_never_publishes(image, tmp_path, margin_exit):
    from kge.adapters.objstore import open_store
    from tests.unit.test_objstore import PEOPLE, names

    for args in (("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=ann"),
                 ("put-example", "EXAMPLE=acme"), ("seed",), ("release",)):
        done, _ = image(*args)
        assert done.returncode == 0, done.stderr
    store = open_store(str(tmp_path / "store"))
    published = store.get("releases/acme/a1.tar.gz")
    done, calls = image("reload", app="reload-job", env={"MAKE_EXIT": str(margin_exit), "MAKE_EXIT_TARGET": "margin"})
    assert done.returncode == margin_exit, done.stderr
    assert [call["args"] for call in calls] == [["load", "HASH=a1"], ["margin", "HASH=a1"]]
    assert all(call["landed"] for call in calls)
    assert "build/stores/acme/a1/LOADED" in names(store.get("work/acme.tar.gz"))
    assert store.get("releases/acme/a1.tar.gz") == published
    assert store.get("releases/acme/current") == b"a1\n"


def test_loading_the_same_hash_records_landed_tables_and_clears_approval(tmp_path, compiled, monkeypatch):
    import json
    import os
    import subprocess
    import sys

    from kge.landing.zone import Zone
    from kge.pipeline.landed_tables import landed_tables
    from tests.scratch import profile_of, write_registry

    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    registry = write_registry(tmp_path, artifacts=compiled.parent.parent)
    prof = profile_of(tmp_path)
    landed = Zone(prof.landing, prof.client).land_files(
        {"documents/index.json": b"[]",
         "structured/retail.json": json.dumps({"source": "retail", "tables": [{"name": "customer"}]}).encode()}, {})
    graph = prof.at(compiled.name).graph_path()
    graph.parent.mkdir(parents=True)
    for name in (release.LOADED, release.GATE, release.SIGNOFF, release.RELEASED):
        release.put(graph, name, {"client": prof.client, "model_hash": compiled.name})
    assert landed_tables(prof.at(compiled.name), landed.about()) is None
    done = subprocess.run([sys.executable, "-m", "kge.graph.load", "--artifact", str(compiled)],
                          env={**os.environ, "PROFILES": str(registry), "MODEL_PROVIDER": "none"},
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert landed_tables(prof.at(compiled.name), landed.about()) == frozenset({"retail.customer"})
    assert all(release.marked(graph, name) is None for name in
               (release.LOADED, release.GATE, release.SIGNOFF, release.RELEASED))


def test_reload_without_current_refuses_before_loading(image):
    from tests.unit.test_objstore import PEOPLE

    done, _ = image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=ann")
    assert done.returncode == 0, done.stderr
    done, calls = image("reload", app="empty-reload")
    assert done.returncode == 2 and "reload: no current release" in done.stderr
    assert calls == []


@pytest.fixture
def previous_and_candidate(prof, monkeypatch):
    import yaml

    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    h1 = _make_artifact(prof)
    release.release(prof, h1, ungated=True)
    pack = prof.clients / prof.client / "pack.yaml"
    data = yaml.safe_load(pack.read_text())
    data["manifest"]["version"] = "1.6.0"
    pack.write_text(yaml.safe_dump(data, sort_keys=False))
    h2 = _make_artifact(prof)
    graph = prof.at(h2).graph_path()
    release.mark_gate(graph, {"model_hash": h2, "passed": True, "run_id": "h2-checked"})
    release.put(graph, release.SIGNOFF, {"model_hash": h2, "principal": "steward_glossary", "run_id": "h2-checked"})
    return prof, h1, h2


@pytest.mark.parametrize("gate", [None, False, True], ids=["unchecked", "failed", "unsigned"])
def test_reloaded_previous_hash_cannot_bypass_gate_after_another_release(previous_and_candidate, gate):
    prof, h1, h2 = previous_and_candidate
    graph = prof.at(h1).graph_path()
    entry = release.loaded(graph)
    release.unmark(graph)
    release.put(graph, release.LOADED, entry)
    if gate is not None:
        release.mark_gate(graph, {"model_hash": h1, "passed": gate, "run_id": "h1-reloaded"})
    assert prof.model_hash() == h1

    release.release(prof, h2)
    assert release.marked(graph, release.RELEASED) is None
    with pytest.raises(release.Unreleasable, match="not through the onboarding gate"):
        release.release(prof, h1)
    assert prof.model_hash() == h2

    release.mark_gate(graph, {"model_hash": h1, "passed": True, "run_id": "h1-rechecked"})
    release.put(graph, release.SIGNOFF, {"model_hash": h1, "principal": "steward_glossary", "run_id": "h1-rechecked"})
    release.release(prof, h1)
    assert prof.model_hash() == h1
    assert release.marked(graph, release.RELEASED)["run_id"] == "h1-rechecked"


@pytest.mark.parametrize("legacy", [False, True], ids=["marked-release", "legacy-release"])
def test_previous_release_without_reload_still_rolls_back(previous_and_candidate, legacy):
    prof, h1, h2 = previous_and_candidate
    graph = prof.at(h1).graph_path()
    if legacy:
        release.write_marker(graph.parent, {}, release.RELEASED)
    release.release(prof, h2)
    assert release.marked(graph, release.RELEASED) is not None
    assert release.gate(graph) is None
    assert release.marked(graph, release.SIGNOFF) is None
    was, how = release.release(prof, h1)
    assert was == h2 and how == "released before, so no new sign-off"
    assert prof.model_hash() == h1
