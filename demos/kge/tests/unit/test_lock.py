"""A graph the API holds fails the tests that open it, naming the API's process; it never skips them.

    .venv/bin/python -m pytest tests/unit/test_lock.py -q

The graph store lets one process open the graph at a time. The tests that read the
demo's own graph open it once per module through the eng fixture
(tests/conftest.py, tests/scratch.py:open_engine). Run beside the API, a
skip there passed the run with a third of the suite skipped and, under -q,
no word of why; a failure turns it red and says to stop the API.

Needs no graph: the store's own lock error is raised in place of opening the file
(kge/adapters/graphstore/: store_module, the store this run is on).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from kge.adapters.graphstore import store_module
from tests.scratch import graph_of, open_engine, run_of, stand_in, write_registry

TESTS = Path(__file__).resolve().parents[1]


def test_a_locked_graph_fails_the_test_and_names_the_apis_process(monkeypatch, tmp_path):
    """The graph's file is written first, since a graph that is not there is never opened."""
    def held(path, **kwargs):
        raise RuntimeError(f"IO exception: Could not set lock on file : {path}")
    monkeypatch.setattr(store_module(), "Database", held)
    write_registry(tmp_path)
    stand_in(tmp_path)
    graph_of(tmp_path).parent.mkdir(parents=True)
    graph_of(tmp_path).write_text("")
    run_of(tmp_path).mkdir()
    (run_of(tmp_path) / "api.pid").write_text("4242")
    with pytest.raises(pytest.fail.Exception) as failed:
        open_engine(tmp_path)
    assert str(failed.value).startswith("graph is locked, stop the API first: ") and "the demo API (pid 4242)" in str(failed.value)
    assert failed.value.pytrace is False and failed.value.__context__ is None, "the reason alone, with no traceback or chained errors"


def test_any_other_error_opening_the_graph_is_raised_as_itself(monkeypatch, tmp_path):
    def broken(path, **kwargs):
        raise RuntimeError("Catalog exception: the file is not a graph")
    monkeypatch.setattr(store_module(), "Database", broken)
    write_registry(tmp_path)
    stand_in(tmp_path)
    graph_of(tmp_path).parent.mkdir(parents=True)
    graph_of(tmp_path).write_text("")
    with pytest.raises(RuntimeError, match="not a graph"):
        open_engine(tmp_path)


def test_no_test_module_opens_the_demos_graph_itself_or_skips_on_a_lock():
    for path in sorted(TESTS.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text()
        assert "Engine(BUILD)" not in text, f"{path.name}: the eng fixture opens the demo's graph"
        assert not re.search(r"except \w+(Error|Locked)\b[^\n]*:\n\s*pytest\.skip", text), f"{path.name}: a lock is a failure"


def test_the_locked_graph_message_names_the_profile_and_the_address_of_the_api_that_holds_it(monkeypatch, tmp_path):
    """The API writes, beside its pid, the profile it serves and its address (api.json, the profile's run state), so the
    message's way round the lock, the golden set through the API, is that API's: Thornbury served on 8001, as the README
    serves it beside Meridian, is never sent to 8000. An api.json another process left, or none, gives no address."""
    import json

    from kge.adapters.graphstore import GraphLocked, open_db

    def held(path, **kwargs):
        raise RuntimeError(f"IO exception: Could not set lock on file : {path}")
    monkeypatch.setattr(store_module(), "Database", held)
    run = tmp_path / "run"
    run.mkdir()
    (run / "api.pid").write_text("4242")
    graph = tmp_path / "graph.store"

    def said() -> str:
        with pytest.raises(GraphLocked) as exc:
            open_db(graph, run / "api.pid")
        return str(exc.value)
    (run / "api.json").write_text(json.dumps({"pid": 4242, "profile": "thornbury", "url": "http://127.0.0.1:8001"}))
    assert "the demo API (pid 4242)" in said() and "kge/eval/run_golden.py --profile thornbury --api http://127.0.0.1:8001" in said()
    assert "8000" not in said()
    for stale in ({"pid": 77, "profile": "thornbury", "url": "http://127.0.0.1:8001"}, "not json"):
        (run / "api.json").write_text(stale if isinstance(stale, str) else json.dumps(stale))
        assert "kge/eval/run_golden.py --profile <its profile> --api <its address>" in said() and not re.search(r":80\d\d", said())
    (run / "api.json").unlink()
    assert "kge/eval/run_golden.py --profile <its profile> --api <its address>" in said() and not re.search(r":80\d\d", said())
    from kge import cli
    from kge.cli import servers
    for name in ("HOST", "API"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(TESTS.parent)
    monkeypatch.setattr(servers.profile, "main", lambda argv: 0)
    _, _, env = servers.api_spec(cli.Commands({"PORT": "8001", "PORTAL_SYNC_TOKEN": "given"}))
    assert env["API_URL"] == "http://127.0.0.1:8001", env["API_URL"]
