"""Every Ladybug database has the one configured size limit, including the in-memory database used for store info."""
from __future__ import annotations

import pytest

from kge.adapters.graphstore import ladybug
from kge.pipeline.settings import ladybug_max_db_size


class Result:
    def get_all(self):
        return [["1.2.3"]]


class Connection:
    def __init__(self, db):
        self.db = db

    def execute(self, query):
        return Result()

    def close(self):
        pass


class Module:
    storage_version = 7

    def __init__(self):
        self.calls = []
        self.Connection = Connection

    def Database(self, path=None, **kwargs):
        self.calls.append((path, kwargs))
        return self

    def close(self):
        pass


def test_the_default_limit_is_passed_to_disk_and_memory_databases(monkeypatch, tmp_path):
    module = Module()
    store = ladybug.LadybugStore()
    monkeypatch.delenv("LADYBUG_MAX_DB_MB", raising=False)
    monkeypatch.setattr(store, "module", lambda: module)
    monkeypatch.setattr(ladybug, "_info", None)

    store.open(tmp_path / "graph.ladybug")
    store.info()

    assert module.calls == [
        (str(tmp_path / "graph.ladybug"), {"max_db_size": 128 * 1024 * 1024}),
        (None, {"max_db_size": 128 * 1024 * 1024}),
    ]


def test_a_set_limit_is_used(monkeypatch, tmp_path):
    module = Module()
    store = ladybug.LadybugStore()
    monkeypatch.setenv("LADYBUG_MAX_DB_MB", "64")
    monkeypatch.setattr(store, "module", lambda: module)

    store.open(tmp_path / "graph.ladybug")

    assert module.calls == [(str(tmp_path / "graph.ladybug"), {"max_db_size": 64 * 1024 * 1024})]


@pytest.mark.parametrize("raw", ("0", "-1", "abc", "1.5"))
def test_a_non_positive_whole_number_is_refused_by_name(monkeypatch, raw):
    monkeypatch.setenv("LADYBUG_MAX_DB_MB", raw)

    with pytest.raises(ValueError, match="^LADYBUG_MAX_DB_MB must be a positive whole number"):
        ladybug_max_db_size()
