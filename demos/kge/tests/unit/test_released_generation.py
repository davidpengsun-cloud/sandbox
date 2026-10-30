"""The released generation (#97): on BigQuery a load of a hash makes a new generation of its graph and chunks, and a
release records which one a server opens. The release's markers are read for the hash whatever generation a server
opens. On stand-ins: no BigQuery is called."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kge.adapters import released
from kge.adapters.graphstore import bigquery as bq
from kge.adapters.graphstore.gql import Schema
from kge.graph import release
from kge.pipeline.profile import Profile

HASH = "ab" * 32


def profile(tmp_path: Path) -> Profile:
    return Profile(id="p", client="meridian", clients=tmp_path / "clients", artifacts=tmp_path / "artifacts",
                   store=tmp_path / "stores", landing=tmp_path / "landing", warehouses=tmp_path / "warehouses",
                   identities=tmp_path / "identities.yaml", baseline=None, registry=tmp_path / "profiles.yaml")


def pointer(tmp_path: Path, generation: str, model_hash: str = HASH) -> Path:
    """The hash's graph pointer at generation, written as a load writes it."""
    path = tmp_path / "stores" / "meridian" / model_hash / "graph.bigquery"
    g = bq.Graph(path)
    g.ns.prefix = bq.prefix_of(model_hash, generation)
    g.write(Schema.build([], []))
    return path


def test_a_served_profile_reads_the_release_s_markers_for_its_hash(monkeypatch, tmp_path):
    """Copilot on #97 (kge/app.py): the API's profile serves (Profile.serve), so its graph path is the released pointer,
    its hash's release record, release.json; the markers (LOADED, GATE, SIGNOFF, RELEASED) are keyed by the store, bigquery, which the
    store names, never a key read off the pointer's file name. So the page's release state, the job's candidate and
    its sign-off read the same as for the hash's own profile."""
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    graph = pointer(tmp_path, "1a1a1a1a")
    released.write(graph.parent, HASH, bq.BigQueryStore().release_entry(graph), None)
    entry = {"client": "meridian", "model_hash": HASH, "passed": True, "run_id": "r1", "principal": "steward", "at": "t"}
    for name in (release.LOADED, release.GATE, release.SIGNOFF, release.RELEASED):
        release.write_marker(graph.parent, {"bigquery": entry}, name)
    prof = profile(tmp_path)
    served = prof.serve()
    assert served.at(HASH).graph_path().name == "release.json"
    assert release.state(served, HASH) == release.state(prof, HASH)
    assert release.state(served, HASH) == {
        "model_hash": HASH, "loaded": True, "gate": {"passed": True, "run_id": "r1"},
        "signed_off": {"principal": "steward", "at": "t", "run_id": "r1"}, "released": True, "current": False}
    assert release.marked(served.at(HASH).graph_path(), release.SIGNOFF) == entry
    from kge.services.onboard import jobs
    assert jobs.candidate(SimpleNamespace(PROFILE=served), {"hash": HASH})[1]["loaded"] is True


DEMO = Path(__file__).resolve().parents[2]
CHECKS = {"kge/eval/margin.py": "a candidate's check measures the generation its hash's last load made"}


def engines_opened(roots: list[Path], base: Path) -> list[str]:
    """Each place under roots that opens an engine other than through Engine.serving (the release's generation), or
    through Engine.candidate (what the hash's folder names now) outside the candidate checks (CHECKS), the engine's own
    module aside. The class is followed however it is named (Grok m2 on #97): Engine, a module's .Engine, or a name it
    is imported as or assigned to, or getattr(module, "Engine"); called itself, or through __new__ (Engine.__new__ or
    object.__new__(Engine))."""
    import ast
    opened = "open it with Engine.serving, or Engine.candidate for a check"
    out = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(base).as_posix()
            if rel == "kge/services/ask/engine/core.py":
                continue
            tree = ast.parse(path.read_text())
            names = {"Engine"} | {a.asname for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                                  for a in n.names if a.name == "Engine" and a.asname}

            def is_engine(node: ast.AST) -> bool:
                return ((isinstance(node, ast.Name) and node.id in names)
                        or (isinstance(node, ast.Attribute) and node.attr == "Engine")
                        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                            and node.args[1].value == "Engine"))
            bound = None
            while bound != names:
                bound = set(names)
                names |= {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and is_engine(n.value)
                          for t in n.targets if isinstance(t, ast.Name)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if is_engine(f):
                    out.append(f"{rel}:{node.lineno}: Engine(...): {opened}")
                elif isinstance(f, ast.Attribute) and f.attr == "__new__" and (
                        is_engine(f.value) or any(is_engine(a) for a in node.args)):
                    out.append(f"{rel}:{node.lineno}: Engine.__new__: {opened}")
                elif isinstance(f, ast.Attribute) and is_engine(f.value) and f.attr == "candidate" and rel not in CHECKS:
                    out.append(f"{rel}:{node.lineno}: Engine.candidate outside a candidate's check")
    return out


def test_every_entry_point_that_answers_as_the_release_opens_the_released_generation():
    """Copilot on #97 (kge/eval/run_golden.py): the API, make eval, make ab (the container's eval), make agent, parity and
    the access demo answer as the release, so each opens its engine through one function, Engine.serving, which opens
    the generation the release recorded (Profile.serve); only a candidate's check (make margin) opens what its hash's
    last load made, through Engine.candidate. The API's own profile serves too."""
    roots = [p for p in sorted(DEMO.iterdir()) if p.is_dir() and p.name not in {"tests", ".venv", "build", "node_modules"}
             and not p.name.startswith(".")]
    assert engines_opened(roots, DEMO) == []
    import kge.app
    assert kge.app.PROFILE.serving


def test_an_engine_opened_another_way_is_caught(tmp_path):
    (tmp_path / "kge" / "eval").mkdir(parents=True)
    (tmp_path / "kge" / "eval" / "x.py").write_text(
        "from kge.services.ask.engine.core import Engine\n\n\ndef f(p):\n"
        "    a = Engine(p)\n    b = Engine.candidate(p)\n    return a, b, Engine.serving(p)\n")
    (tmp_path / "kge" / "eval" / "margin.py").write_text("def f(p, Engine):\n    return Engine.candidate(p)\n")
    assert engines_opened([tmp_path / "kge"], tmp_path) == [
        "kge/eval/x.py:5: Engine(...): open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/x.py:6: Engine.candidate outside a candidate's check"]


def test_an_engine_opened_by_a_qualified_name_an_alias_or_new_is_caught(tmp_path):
    """Grok m2 on #97: the scan follows the class however it is named, core.Engine(...), an alias of it imported as
    another name, and Engine.__new__, besides Engine(...) itself; the two sanctioned entry points stay allowed."""
    (tmp_path / "kge" / "eval").mkdir(parents=True)
    (tmp_path / "kge" / "eval" / "y.py").write_text(
        "from kge.services.ask.engine import core\nfrom kge.services.ask.engine.core import Engine as E\n\n\ndef f(p):\n"
        "    a = core.Engine(p)\n    b = E(p)\n    c = E.__new__(E)\n    d = core.Engine.candidate(p)\n"
        "    return a, b, c, d, E.serving(p), core.Engine.serving(p)\n")
    assert engines_opened([tmp_path / "kge"], tmp_path) == [
        "kge/eval/y.py:6: Engine(...): open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/y.py:7: Engine(...): open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/y.py:8: Engine.__new__: open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/y.py:9: Engine.candidate outside a candidate's check"]


def test_serving_opens_the_released_generation_and_candidate_the_last_load(monkeypatch, tmp_path):
    from kge.services.ask.engine import core
    opened = []
    monkeypatch.setattr(core.Engine, "__init__", lambda self, prof: opened.append(prof))
    prof = profile(tmp_path)
    core.Engine.serving(prof)
    core.Engine.candidate(prof.at(HASH))
    assert [p.serving for p in opened] == [True, False] and opened[1].pinned == HASH


def test_an_engine_reached_by_assignment_object_new_or_getattr_is_caught(tmp_path):
    """Grok n1 on #97: a name the class is assigned to (E = Engine; E(p)), object.__new__(Engine), and the class reached
    by getattr(core, "Engine"), called; the two sanctioned entry points stay allowed through each."""
    (tmp_path / "kge" / "eval").mkdir(parents=True)
    (tmp_path / "kge" / "eval" / "z.py").write_text(
        "from kge.services.ask.engine import core\nfrom kge.services.ask.engine.core import Engine\n\n\ndef f(p):\n"
        "    E = Engine\n    a = E(p)\n    b = object.__new__(Engine)\n    c = getattr(core, 'Engine')(p)\n"
        "    F = getattr(core, 'Engine')\n    d = F(p)\n"
        "    return a, b, c, d, E.serving(p), getattr(core, 'Engine').serving(p)\n")
    assert engines_opened([tmp_path / "kge"], tmp_path) == [
        "kge/eval/z.py:7: Engine(...): open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/z.py:8: Engine.__new__: open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/z.py:9: Engine(...): open it with Engine.serving, or Engine.candidate for a check",
        "kge/eval/z.py:11: Engine(...): open it with Engine.serving, or Engine.candidate for a check"]


def test_the_release_record_is_written_under_a_temporary_name_and_renamed_into_place(monkeypatch, tmp_path):
    """Grok m3 on #97: the record is written whole, under a temporary name in its folder, and renamed over the old in
    one step (os.replace), so no reader ever opens it half written."""
    import os
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    graph = pointer(tmp_path, "1a1a1a1a")
    renamed = []
    real = os.replace
    monkeypatch.setattr(released.os, "replace", lambda src, dst: (renamed.append((Path(src), Path(dst))), real(src, dst))[1])
    record = released.write(graph.parent, HASH, bq.BigQueryStore().release_entry(graph), None)
    assert len(renamed) == 1
    (tmp, dst), = renamed
    assert dst == record and tmp.parent == record.parent and tmp != record and not tmp.exists()


def test_a_server_opens_its_graph_and_pins_its_chunks_from_one_read_of_the_record(monkeypatch, tmp_path):
    """Grok m3 on #97: Graph(...) opens the graph the release record names (never the folder's pointer, which a later
    load moved) and pins the chunks of that same read of it: a release written between opening the graph and pinning
    the chunks changes neither, so the pair is one release's. (Resolving the served path reads the record too, to find
    where it is; the pair comes from one read.)"""
    from kge.services.ask.engine import graphview
    for name, value in {"GRAPH_STORE": "bigquery", "BIGQUERY_PROJECT": "proj", "RETRIEVAL_BACKEND": "bigquery"}.items():
        monkeypatch.setenv(name, value)
    graph = pointer(tmp_path, "1a1a1a1a")
    first = bq.BigQueryStore().release_entry(graph)
    released.write(graph.parent, HASH, first, {"dataset": "kge_meridian_graph", "table": f"chunks_{HASH[:16]}_1a1a1a1a"})
    pointer(tmp_path, "9f9f9f9f")
    opened = []

    def open_db(path, pidfile, entry=None):
        opened.append(entry)
        moved = bq.Graph(graph)
        moved.ns.prefix = bq.prefix_of(HASH, "2b2b2b2b")
        released.write(graph.parent, HASH, {**first, "prefix": bq.prefix_of(HASH, "2b2b2b2b")},
                       {"dataset": "kge_meridian_graph", "table": f"chunks_{HASH[:16]}_2b2b2b2b"})
        return SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(graphview, "open_db", open_db)
    monkeypatch.setattr(graphview, "connect", lambda db: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(graphview, "make_embedder", lambda: None)
    g = graphview.Graph(profile(tmp_path).serve().at(HASH))
    assert opened == [first]
    assert g.chunks == f"`proj.kge_meridian_graph.chunks_{HASH[:16]}_1a1a1a1a`"
    assert g.identity["graph"] == bq.BigQueryStore().generation_of(first)


def test_the_store_says_whether_a_graph_is_loaded_from_its_pointer_and_marker(monkeypatch, tmp_path):
    """GraphStore.loaded, which profile --make's loaded= and so make onboard's skip read: BigQuery's pointer names a
    generation and the folder's LOADED marker holds BigQuery's entry; never an entry of the other store's, a marker or a
    pointer that cannot be read, or a graph a load has taken out of LOADED."""
    from kge.adapters.graphstore import loaded
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    graph = pointer(tmp_path, "1a1a1a1a")
    entry = {"client": "meridian", "model_hash": HASH}
    assert loaded(graph) is False
    release.write_marker(graph.parent, {"ladybug": entry})
    assert loaded(graph) is False
    release.write_marker(graph.parent, {"bigquery": entry, "ladybug": entry})
    assert loaded(graph) is True
    (graph.parent / release.LOADED).write_text("{not json")
    assert loaded(graph) is False
    release.write_marker(graph.parent, {"bigquery": entry})
    graph.write_text("{}")
    assert loaded(graph) is False
    graph.unlink()
    assert loaded(graph) is False
    graph = pointer(tmp_path, "2b2b2b2b")
    assert loaded(graph) is True
    release.unmark(graph)
    assert loaded(graph) is False


def test_ladybug_says_a_graph_is_loaded_once_its_file_and_its_entry_are_there(monkeypatch, tmp_path):
    from kge.adapters.graphstore import loaded
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    graph = tmp_path / "stores" / "meridian" / HASH / "graph.ladybug"
    graph.parent.mkdir(parents=True)
    release.write_marker(graph.parent, {"ladybug": {"client": "meridian", "model_hash": HASH}})
    assert loaded(graph) is False
    graph.write_text("")
    assert loaded(graph) is True
    release.write_marker(graph.parent, {"bigquery": {"client": "meridian", "model_hash": HASH}})
    assert loaded(graph) is False


def test_bigquery_says_not_loaded_where_only_ladybug_s_graph_and_entry_are(monkeypatch, tmp_path):
    """The live run on #97: a folder the local stack loaded (graph.ladybug, LOADED for ladybug) and no BigQuery pointer
    is not loaded on BigQuery, even with BigQuery's entry copied into its marker."""
    from kge.adapters.graphstore import loaded
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    folder = tmp_path / "stores" / "meridian" / HASH
    folder.mkdir(parents=True)
    (folder / "graph.ladybug").write_text("")
    entry = {"client": "meridian", "model_hash": HASH}
    release.write_marker(folder, {"ladybug": entry})
    graph = bq.BigQueryStore().path(folder)
    assert loaded(graph) is False
    release.write_marker(folder, {"ladybug": entry, "bigquery": entry})
    assert loaded(graph) is False
