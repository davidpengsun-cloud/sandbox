"""Meta-model 1.6.0's term edges on both graph stores (issue #511): two CODED_AS edges between one term and one column, with
different codes, are kept apart by their edge ids, and IS_A is no reserved word, in Ladybug's Cypher or in BigQuery's GQL
(the BigQuery store run on its twin, tests/graphtwin.py).

    .venv/bin/python -m pytest tests/integration/test_term_edges.py -q

A scratch copy of the Thornbury sample pack, raised to 1.6.0, gains two class terms and four member terms on Thornbury's
own columns, each coded column mapped to its class as S37 asks, and one code/name pair, written here: synthetic data, no
question set, nothing of the Meridian sample. It compiles end to end, checks included, then loads on each store the way
kge/graph/load.py does (the store's own DDL, then the copy-stage files, nodes first), and four reads must give the same
rows on both.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.scratch import DEMO, compile_pack

SAMPLE = "thornbury"
LINE, SEGMENT = "underwriting.policy.line_code", "underwriting.insured.segment_code"
READ = '''
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from kge.adapters.graphstore import batch, graph_path, graph_q, store
from kge.graph.load import open_graph

art, root = Path(sys.argv[1]), Path(sys.argv[2])
man = json.loads((art / "manifest.json").read_text())
model = yaml.safe_load((art / "graph-model.yaml").read_text())
conn = open_graph(graph_path(root / "thornbury" / man["model_hash"]), fresh=True)
store().create(conn, model, store().ddl(art, model))
order = man["load_order"]
pairs = Counter(step["table"] for step in order if "from" in step)
group = {step["path"]: (step["from"], step["to"]) for step in order if "from" in step and pairs[step["table"]] > 1}
with batch(conn) as w:
    for step in [s for s in order if "from" not in s] + [s for s in order if "from" in s]:
        if step["stage"] == "copy" and step["rows"]:
            w.load_file(step["table"], art / step["path"], group.get(step["path"]))
q = graph_q(conn)
print(json.dumps({
    "is_a": q("MATCH (x:Term)-[:IS_A]->(y:Term) RETURN x.name AS a, y.name AS b ORDER BY a, b"),
    "broader": q("MATCH (x:Term)-[:BROADER]->(y:Term) RETURN x.name AS a, y.name AS b ORDER BY a, b"),
    "coded_as": q("MATCH (t:Term)-[c:CODED_AS]->(x:Col) RETURN t.name AS term, x.id AS col, c.code AS code "
                  "ORDER BY term, col, code"),
    "on_column": q("MATCH (v:Vocabulary)-[r:ON_COLUMN]->(x:Col) RETURN v.id AS entry, x.id AS col, r.role AS role "
                   "ORDER BY entry, col"),
}, sort_keys=True))
'''
EXPECTED = {
    "is_a": [{"a": "General Liability (line)", "b": "line of business"}, {"a": "Middle (segment)", "b": "segment"},
             {"a": "Property (line)", "b": "line of business"}, {"a": "Small (segment)", "b": "segment"}],
    "broader": [{"a": "Small (segment)", "b": "Property (line)"}],
    "coded_as": [{"term": "General Liability (line)", "col": LINE, "code": "GL"},
                 {"term": "General Liability (line)", "col": LINE, "code": "GLI"},
                 {"term": "Property (line)", "col": LINE, "code": "PROP"},
                 {"term": "Small (segment)", "col": SEGMENT, "code": "SMALL"}],
    "on_column": [{"entry": "code_name_pair-1", "col": "underwriting.insured.insured_id", "role": "code"},
                  {"entry": "code_name_pair-1", "col": "underwriting.insured.insured_name", "role": "name"},
                  {"entry": "predicate-1", "col": "claims.claim.closed_on", "role": "predicate"},
                  {"entry": "value-1", "col": LINE, "role": "value"},
                  {"entry": "value-2", "col": SEGMENT, "role": "value"}],
}


def term(i: str, name: str, **fields) -> dict:
    return {"id": i, "name": name, "domain": "Underwriting", "steward": "Underwriting Data Steward",
            "definition": f"{name}: a synthetic term of the test.", "status": "certified", **fields}


@pytest.fixture(scope="module")
def artifact(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("term-edges")
    clients = tmp / "clients"
    shutil.copytree(DEMO / "clients" / SAMPLE, clients / SAMPLE)
    path = clients / SAMPLE / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["manifest"]["meta_model_version"] = "1.6.0"
    data["manifest"]["platform"]["meta_model"] = "^1.6"
    data["schema"]["meta_model"] = "1.6"
    data["glossary"] += [
        term("line-of-business", "line of business"),
        term("segment", "segment"),
        term("property-line", "Property (line)", is_a=["line-of-business"], codes=[{"column": LINE, "code": "PROP"}]),
        term("liability-line", "General Liability (line)", is_a=["line-of-business"],
             codes=[{"column": LINE, "code": "GL"}, {"column": LINE, "code": "GLI"}]),
        term("small-segment", "Small (segment)", is_a=["segment"], broader=["property-line"],
             codes=[{"column": SEGMENT, "code": "SMALL"}]),
        term("middle-segment", "Middle (segment)", is_a=["segment"]),
    ]
    data["mappings"] += [{"column": LINE, "term": "line-of-business", "certified": False},
                         {"column": SEGMENT, "term": "segment", "certified": False}]
    data["terminology"]["code_name_pairs"] = [{"code": "underwriting.insured.insured_id", "name": "underwriting.insured.insured_name",
                                              "members": [{"code": "1", "name": "Synthetic Insured"}]}]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return compile_pack(tmp / "out", clients=clients, client=SAMPLE)


def run(command: list[str], **env) -> subprocess.CompletedProcess:
    out = subprocess.run(command, cwd=DEMO, capture_output=True, text=True, timeout=600,
                         env={**os.environ, "PROFILE": "", **env})
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    return out


@pytest.fixture(scope="module")
def script(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("term-edges-script") / "read_term_edges.py"
    path.write_text(READ)
    return path


def test_the_term_edges_read_the_same_on_ladybug_and_on_bigquery(tmp_path, artifact, script):
    """Two CODED_AS edges from General Liability (line) to one column, GL and GLI, come back apart on both stores, and
    so do the IS_A rows: IS_A is not reserved in either store's query language."""
    assert artifact.name == json.loads((artifact / "manifest.json").read_text())["model_hash"]
    ladybug = run([sys.executable, str(script), str(artifact), str(tmp_path / "ladybug")], GRAPH_STORE="")
    (tmp_path / "twin").mkdir()
    twin = run([sys.executable, "-m", "tests.graphtwin", str(tmp_path / "twin" / "graph.ladybug"), str(artifact), str(script),
                str(artifact), str(tmp_path / "bigquery")], GRAPH_STORE="bigquery", BIGQUERY_PROJECT="twin-project")
    assert re.search(r"graphtwin: [1-9]\d* queries", twin.stderr), twin.stderr[-2000:]
    on_ladybug = json.loads(ladybug.stdout.strip().splitlines()[-1])
    on_bigquery = json.loads(twin.stdout.strip().splitlines()[-1])
    assert on_ladybug == EXPECTED
    assert on_bigquery == on_ladybug
