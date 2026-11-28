"""The fold writes its status projection through the graph store's batch interface."""

import json
from contextlib import contextmanager

import pytest

from kge.eval import shapefold
from kge.adapters.graphstore.bigquery import GraphStoreBusy


class Writer:
    def __init__(self):
        self.calls = []

    def delete_node(self, *args):
        self.calls.append(("delete_node", args))

    def delete_edges(self, *args):
        self.calls.append(("delete_edges", args))

    def upsert(self, *args):
        self.calls.append(("upsert", args))

    def create_edge(self, *args):
        self.calls.append(("create_edge", args))


def test_write_upserts_statuses_and_only_links_existing_shapes(monkeypatch):
    writer = Writer()
    queries = []

    def query(statement):
        queries.append(statement)
        if "ShapeStatus" in statement:
            return [{"id": "s.a@0000-r1"}, {"id": "stale@0000-r1"}]
        if "Shape" in statement:
            return [{"id": "s.a"}]
        raise AssertionError(statement)

    @contextmanager
    def batch(_conn):
        yield writer

    monkeypatch.setattr(shapefold, "graph_q", lambda _conn: query)
    monkeypatch.setattr(shapefold, "batch", batch)
    rows = [
        shapefold.StatusRow("s.a@0000-r1", "s.a", "0000-r1", '{"version":1}'),
        shapefold.StatusRow("coded.builder@0000-r1", "coded.builder", "0000-r1", '{"version":1}'),
    ]
    assert shapefold.write(object(), rows) == 2
    assert len(queries) == 2
    assert ("delete_node", ("ShapeStatus", "stale@0000-r1")) in writer.calls
    assert ("delete_edges", ("STATUS_OF", "ShapeStatus", "s.a@0000-r1", "Shape")) in writer.calls
    assert ("create_edge", ("STATUS_OF", "ShapeStatus", "s.a@0000-r1", "Shape", "s.a")) in writer.calls
    assert not any(call[0] == "create_edge" and call[1][-1] == "coded.builder" for call in writer.calls)
    upserts = [args for method, args in writer.calls if method == "upsert"]
    assert {args[1] for args in upserts} == {row.id for row in rows}
    assert all(args[0] == "ShapeStatus" for args in upserts)


def test_missing_shape_status_type_is_reported_without_graph_writes(monkeypatch):
    class MissingType(RuntimeError):
        pass

    class Store:
        def missing_type(self, exc):
            return isinstance(exc, MissingType)

    def query(_statement):
        raise MissingType("type absent")

    monkeypatch.setattr(shapefold, "graph_q", lambda _conn: query)
    monkeypatch.setattr(shapefold, "store", lambda: Store())
    monkeypatch.setattr(shapefold, "batch", lambda _conn: pytest.fail("must not write an old graph"))
    with pytest.raises(shapefold.NotFolded, match="ShapeStatus"):
        shapefold.write(object(), [])


def test_busy_graph_fails_with_public_report_instead_of_uncaught_error(tmp_path, monkeypatch):
    class Profile:
        client = "synthetic"
        artifacts = tmp_path

        def run_dir(self):
            return tmp_path / "run"

    class EmptyLog:
        def releases(self):
            return []

    class EmptySeals:
        def snapshot(self):
            return {}, {}, "0", frozenset()

        def commit(self, _records, _windows, _version, _known):
            return None

        def events(self):
            return []

        def read(self, _release, _event):
            return None, "0"

        def write(self, _release, _event, _record, _version):
            return "1"

    history = tmp_path / "versions.jsonl"
    history.write_text(json.dumps({"release": "r1", "released_at": "2026-01-01T00:00:00Z"}) + "\n")
    monkeypatch.setattr(shapefold, "versions_path", lambda _profile: history)
    monkeypatch.setattr(shapefold, "_existing_statuses", lambda _conn: set())

    def fail_write(_connection, _rows):
        raise GraphStoreBusy("PRIVATE_QUESTION_EXAMPLE")

    monkeypatch.setattr(shapefold, "write", fail_write)
    with pytest.raises(shapefold.NotFolded) as raised:
        shapefold.run(Profile(), log=EmptyLog(), conn=object(), seals=EmptySeals())
    report = json.loads((Profile().run_dir() / "fold.json").read_text())
    assert report["written"] is False
    assert report["skipped"]
    assert "PRIVATE_QUESTION_EXAMPLE" not in str(raised.value)
    assert "PRIVATE_QUESTION_EXAMPLE" not in json.dumps(report)


def test_failed_partial_graph_projection_can_be_retried_to_exact_state(monkeypatch):
    statuses = {"stale@0000-r1": {"shape": "stale"}}
    edges = {("stale@0000-r1", "stale")}
    fault = {"enabled": True}

    def query(statement):
        if "ShapeStatus" in statement:
            return [{"id": key} for key in sorted(statuses)]
        return [{"id": "s.a"}, {"id": "s.b"}]

    class PartialWriter:
        def delete_node(self, _label, identity):
            statuses.pop(identity, None)
            edges.difference_update({edge for edge in edges if edge[0] == identity})

        def delete_edges(self, _label, _src, identity, _dst):
            edges.difference_update({edge for edge in edges if edge[0] == identity})

        def upsert(self, _label, identity, props):
            if identity.startswith("s.b") and fault["enabled"]:
                raise RuntimeError("synthetic writer failure")
            statuses[identity] = dict(props)

        def create_edge(self, _label, _src, identity, _dst, shape):
            edges.add((identity, shape))

    @contextmanager
    def batch(_conn):
        yield PartialWriter()

    monkeypatch.setattr(shapefold, "graph_q", lambda _conn: query)
    monkeypatch.setattr(shapefold, "batch", batch)
    rows = [
        shapefold.StatusRow("s.a@0000-r1", "s.a", "0000-r1", '{"version":1}'),
        shapefold.StatusRow("s.b@0000-r1", "s.b", "0000-r1", '{"version":1}'),
    ]
    with pytest.raises(RuntimeError, match="synthetic writer failure"):
        shapefold.write(object(), rows)
    assert "s.a@0000-r1" in statuses
    assert "s.b@0000-r1" not in statuses

    fault["enabled"] = False
    assert shapefold.write(object(), rows) == 2
    expected_statuses = {row.id: {"shape": row.shape, "release_event": row.release_event, "counters": row.counters}
                         for row in rows}
    expected_edges = {(row.id, row.shape) for row in rows}
    assert statuses == expected_statuses
    assert edges == expected_edges
    assert shapefold.write(object(), rows) == 2
    assert statuses == expected_statuses
    assert edges == expected_edges
