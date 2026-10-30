"""The graph store: Ladybug, the only one, opened through its adapter alone (kge/adapters/graphstore/).

    .venv/bin/python -m pytest tests/integration/test_store.py -q

kge/adapters/graphstore/ladybug.py is the only module that imports it. A graph is
graph.ladybug in the client's graph store (build/stores/meridian/ in the demo).
The graph's legacy model digest, over what 4d9d5c9 held (tests/before.py), is
#38's A/B baseline's model hash (clients/meridian/baselines/issue-38.json),
recorded on Kuzu 0.11.3 before #39, and so is its overlay hash once each chunk's
raw text stands in for the masked text it landed with: the store and the
landing moved, what the graph held did not.

Opens the demo's graph through the eng fixture, which assumes a fresh build
(make graph, then make pipeline): stop the API first.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from kge.services.ask.engine import runtrace
from kge.adapters import graphstore
from tests import before
from tests.scratch import BUILD, graph_of

DEMO = Path(__file__).resolve().parents[2]
IMPORTS = re.compile(r"^\s*(?:import\s+(?:[\w.]+\s*,\s*)*ladybug\b|from\s+ladybug\b)|import_module\(\s*['\"]ladybug['\"]"
                     r"|\bladybug\.(?:Database|Connection)\b", re.M)


def python(code: str) -> subprocess.CompletedProcess:
    """code in a fresh interpreter from the demo folder."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=DEMO)


def sources() -> list[Path]:
    skip = {".venv", "build", "__pycache__", ".pytest_cache"}
    return sorted(p for p in DEMO.rglob("*.py") if not skip & set(p.relative_to(DEMO).parts))


def test_only_the_graph_store_adapter_imports_the_graph_store():
    """An import of ladybug, or a use of it by its module name, anywhere but kge/adapters/graphstore/ladybug.py, which
    imports it by STORE."""
    found = [str(p.relative_to(DEMO)) for p in sources() if p.name != Path(__file__).name and IMPORTS.search(p.read_text())]
    assert found == [], f"only kge/adapters/graphstore/ladybug.py imports ladybug, not {found}"
    assert "importlib.import_module(STORE)" in (DEMO / "kge" / "adapters" / "graphstore" / "ladybug.py").read_text()
    for line in ("import os, sys, ladybug\n", "from ladybug import Database\n", "db = ladybug.Database(path)\n", "importlib.import_module('ladybug')\n"):
        assert IMPORTS.search(line), f"the guard sees {line!r}"


def test_ladybug_with_no_wheel_says_where_the_local_stack_runs():
    r = python("import sys; sys.modules['ladybug'] = None\nfrom kge.adapters.graphstore import store_module\n"
               "try:\n    store_module()\nexcept ImportError as exc:\n    print(exc)\n")
    assert r.stdout.strip() == graphstore.NO_WHEEL, r.stdout + r.stderr
    assert "macOS 15" in graphstore.NO_WHEEL and "Compose" in graphstore.NO_WHEEL


def test_the_graph_and_its_sidecars(tmp_path):
    store = graphstore.STORE
    assert store == "ladybug"
    assert graphstore.graph_path(tmp_path) == tmp_path / f"graph.{store}"
    for name in (f"graph.{store}", f"graph.{store}.wal", f"graph.{store}.shadow", "graph.other", "graph.other.wal", f"x.{store}.wal"):
        (tmp_path / name).write_text("")
    assert graphstore.sidecars(graphstore.graph_path(tmp_path)) == [tmp_path / f"graph.{store}.shadow", tmp_path / f"graph.{store}.wal"]


def test_store_info_names_the_engine_and_the_databases_own_version(eng):
    with eng.g.reader() as conn:
        info = graphstore.store_info(conn)
    assert info == graphstore.store_info() and set(info) == {"engine", "version", "storage_version"}
    assert info["engine"] == "ladybug" and isinstance(info["storage_version"], int)
    assert info["version"].startswith("0.21.") and info["storage_version"] == 47, info


def test_the_run_trace_and_the_ab_manifest_name_the_store(eng):
    fp = runtrace.fingerprint(eng.g)
    assert fp["store"] == graphstore.store_info() and set(fp) == {"model", "legacy_model", "artifact", "overlay", "store"}


def test_the_graph_holds_what_the_baseline_recorded(eng):
    """The legacy model digest of a fresh build (the DDL and the curated layers as before #39, so leaving out the landing
    release), over the tables and columns 4d9d5c9 had, is #38's A/B baseline's model hash, recorded on Kuzu 0.11.3: the
    Wealth expansion (#39 item 7b) only adds. Its overlay, over the chunks 4d9d5c9 had, is the baseline's once each
    chunk's raw text then stands in for the text it landed with: documents land masked, the data dictionaries of the two
    units the client cross-reference links say so in their first chunks, and neither changed anything else the overlay
    holds (readiness, tags, mentions, decisions, proposals). The overlay holds no vector: the three that masking moved,
    the emails signed with a labelled customer's name, are test_landing.py's (f). The store changed, and the landing,
    and the estate grew, but what the graph held did not move (tests/before.py)."""
    base = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())["graph"]
    assert runtrace.fingerprint(eng.g)["legacy_model"] != base["model"], "the expansion's tables are in the graph"
    assert before.legacy_model(eng.g) == base["model"], "a fresh build: make graph, then make pipeline"
    overlay, differs = before.overlay(eng.g)
    assert differs == 6 + 2, "the six chunks masking changed, and the two dictionaries' first chunks"
    assert overlay == base["overlay"], "a fresh build: make graph, then make pipeline"
    assert graph_of(BUILD).exists()


def test_the_ab_report_names_the_store_and_the_baseline_s_as_not_recorded():
    from kge.eval import report
    base = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())
    assert "store" not in base["graph"] and report.store(base["graph"]) == "not recorded (Kuzu 0.11.3)"
    s = {"engine": "ladybug", "version": "0.21.2", "storage_version": 47}
    assert report.store({**base["graph"], "store": s}) == "ladybug 0.21.2 (storage version 47)"


def test_a_tag_matched_by_where_keeps_its_properties_past_an_empty_optional_match(eng):
    """Ladybug 0.21 drops the properties of a node matched by its key inline ({id: $id}) when an OPTIONAL MATCH after
    it finds nothing. The steward's decision reads its tag with WHERE, which keeps them."""
    tag = next(r for r in eng.g.steward_queue() if r["family"] == "privacy")["tag"]
    rows = eng.g.q("MATCH (t:Tag) WHERE t.id = $id OPTIONAL MATCH (s:Tag)-[:SUPERSEDES]->(t) RETURN t.family AS family, count(s) AS n",
                   {"id": tag})
    assert rows == [{"family": "privacy", "n": 0}]
