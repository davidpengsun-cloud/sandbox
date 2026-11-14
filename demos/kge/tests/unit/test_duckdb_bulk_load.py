import duckdb
import pytest

from kge.adapters.warehouse.duckdb import DuckDBSession
from kge.ports import WarehouseRefused


def test_bulk_load_matches_rowwise_schema_constraints_and_rows():
    old = duckdb.connect()
    new = duckdb.connect()
    for con in (old, new):
        con.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        con.execute("INSERT INTO parent VALUES (1), (2)")
        con.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id), value VARCHAR, amount DECIMAL(8,2))")
        con.execute("COMMENT ON COLUMN child.value IS 'description'")
    rows = [["10", "1", "text, with 'quotes'", "1.25"], ["11", "2", None, "-3.50"]]
    old.executemany("INSERT INTO child VALUES (?, ?, ?, ?)", rows)
    session = DuckDBSession(new)
    assert session.load("child", ["id", "parent_id", "value", "amount"], rows) == len(rows)
    assert session.describe("child") == DuckDBSession(old).describe("child")
    assert new.execute("SELECT * FROM child ORDER BY id").fetchall() == old.execute("SELECT * FROM child ORDER BY id").fetchall()
    for query in ("SELECT constraint_type, constraint_column_names, referenced_table, referenced_column_names FROM duckdb_constraints() WHERE table_name='child' ORDER BY constraint_index",):
        assert new.execute(query).fetchall() == old.execute(query).fetchall()


def test_bulk_load_failure_keeps_same_prior_rows_as_rowwise():
    old = duckdb.connect()
    new = duckdb.connect()
    for con in (old, new):
        con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    rows = [["1"], ["2"], ["1"], ["3"]]
    with pytest.raises(duckdb.Error):
        old.executemany("INSERT INTO items VALUES (?)", rows)
    with pytest.raises(WarehouseRefused):
        DuckDBSession(new).load("items", ["id"], rows)
    assert new.execute("SELECT * FROM items ORDER BY id").fetchall() == old.execute("SELECT * FROM items ORDER BY id").fetchall()
