"""The question lexer's phrase table as a graph holds it (kge/services/ask/engine/graphview.py VOCABULARY["phrases"]).

    .venv/bin/python -m pytest tests/unit/test_phrase_graph.py -q

The Thornbury sample pack is built with the platform's own preload into an artifact, which loads on a scratch graph on this
run's store; the phrase read the engine makes once per release gives baseline v1, equal to the lexer's constant. A graph
loaded before the Phrase type existed reads no table, and the lexer then reads the constant. No question set, fixture or
data of the Meridian sample is read.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from kge.compiler import emit
from kge.compiler import pack as packs
from kge.compiler.compile import VERSION
from kge.graph.schema import meta_model
from kge.semantics.coverage import BASELINE_PHRASES
from kge.semantics.phrasetable import PhraseTable
from kge.services.ask.engine.graphview import VOCABULARY, Graph

DEMO = Path(__file__).resolve().parents[2]
SAMPLE = "thornbury"


@pytest.fixture(scope="module")
def artifact() -> emit.Artifact:
    """The sample pack as it is, built with the platform's own preload."""
    pack = packs.read(DEMO / "clients", SAMPLE)
    assert not pack.findings, pack.findings
    return emit.build(pack, meta_model(), VERSION)


def load(art: Path, graph: Path):
    """A fresh graph on this run's store: the artifact's DDL, then COPY of each file load_order stages for copy."""
    from kge.adapters.graphstore import connect, open_db
    db = open_db(graph)
    conn = connect(db)
    for stmt in (art / "ddl" / "kuzu.cypher").read_text().splitlines():
        conn.execute(stmt.removesuffix(";"))
    for step in json.loads((art / "manifest.json").read_text())["load_order"]:
        if step["stage"] == "copy":
            ends = f", from='{step['from']}', to='{step['to']}'" if "from" in step else ""
            conn.execute(f"COPY {step['table']} FROM '{(art / step['path']).as_posix()}' "
                         f"(header=true, parallel=false, null_strings=['\\\\N']{ends})")
    return db, conn


def graph(q) -> Graph:
    held = Graph.__new__(Graph)
    held.identity, held._snapshot_lock = {"release": "r1"}, threading.RLock()
    held.q = q
    return held


def test_the_graphs_phrase_rows_are_baseline_v1(tmp_path, artifact):
    folder, _ = emit.write(artifact, tmp_path / "out", SAMPLE)
    db, conn = load(folder, tmp_path / "graph")
    try:
        result = conn.execute(VOCABULARY["phrases"])
        names = result.get_column_names()
        rows = [dict(zip(names, values)) for values in result.get_all()]
    finally:
        conn.close()
        db.close()
    assert names == ["phrase", "operation", "filler", "baseline"]
    assert len(rows) == 364 == len(BASELINE_PHRASES.rows())
    assert [r["phrase"] for r in rows] == sorted(r["phrase"] for r in rows)
    table = PhraseTable.from_rows(rows)
    assert table == BASELINE_PHRASES and table.source == "graph" and not table.extended
    held = graph(lambda cypher, params=None: rows if cypher == VOCABULARY["phrases"] else [])
    assert held.vocabulary().phrases == BASELINE_PHRASES


def test_a_graph_without_the_phrase_type_reads_no_table():
    def q(cypher, params=None):
        if "Phrase" in cypher:
            raise RuntimeError("Binder exception: Table Phrase does not exist.")
        return []
    held = graph(q)
    assert held.vocabulary().phrases is None
    assert held.read_phrases() is None


def test_a_graph_with_no_phrase_rows_reads_no_table_and_any_other_failure_is_raised():
    assert graph(lambda cypher, params=None: []).vocabulary().phrases is None

    def refused(cypher, params=None):
        if "Phrase" in cypher:
            raise RuntimeError("the store refused the read")
        return []
    with pytest.raises(RuntimeError, match="refused"):
        graph(refused).vocabulary()


def test_a_graph_whose_phrases_hold_none_of_baseline_v1_reads_no_table():
    rows = [{"phrase": "tally", "operation": "count", "filler": None, "baseline": None}]
    assert graph(lambda cypher, params=None: rows if cypher == VOCABULARY["phrases"] else []).read_phrases() is None
