"""The BigQuery graph store end to end on the twin (tests/graphtwin.py): BigQuery's REST API stood in by one that answers
each query with what Ladybug answers its Cypher twin, so the store's whole path but BigQuery's own execution runs here
(issue #95 item 1.1).

  - Parity: the three presets on the golden and held-out sets, asked through the BigQuery store, give the 183 results
    the local stack gives, as make parity counts them.
  - Load, pipeline and steward: a graph loaded, tagged and decided on through the BigQuery store (its DDL from the
    compiled model, its files as typed rows, every write as DML) holds what the same steps give on Ladybug directly,
    table by table and property by property, the run's own times and decision ids aside.

Whether BigQuery accepts the GQL and SQL text, and gives the rows Ladybug gives, is the owner's live check."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.scratch import DEMO, profile_of, registry, scratch_build

SNAPSHOT = DEMO / "clients" / "meridian" / "baselines" / "parity-4d9d5c9.json"
STATED = DEMO / "clients" / "meridian" / "baselines" / "parity-stated.json"
PARITY = "passed: 164 of 183 results unchanged, 19 moved as stated"
DECIDE = '''
from kge.services.ask.engine.core import Engine
from kge.pipeline.profile import resolve
eng = Engine.candidate(resolve())
g = eng.g
steward = next(p["id"] for p in g.principals() if "certification" in p["processes"] and p["writes_terms"])
engineer = next(p for p in g.principals() if p["id"] == "knowledge_engineer")
assert engineer["processes"] == ["building", "drafting", "improving", "querying"]
assert not engineer["writes_terms"]
assert len(g.q("MATCH (p:Process) RETURN p.id")) == 7
queue = g.steward_queue(steward)
term = next(r for r in queue if r["family"] == "candidate_term")
g.steward_decide(term["tag"], steward, "accept", "Digital Engagement Score", "logins per active customer per month")
privacy = next((r for r in queue if r["family"] == "privacy" and not r.get("second_check_required_to_reject")), None)
if privacy:
    g.steward_decide(privacy["tag"], steward, "reject")
match = next((r for r in queue if r["family"] == "xref_match"), None)
if match:
    g.steward_decide(match["tag"], steward, "accept")
print("decided")
'''

BUDGET = '''
import json
from kge.adapters.graphstore import bigquery
from kge.pipeline.profile import resolve
from kge.pipeline.sqlguard import schema_allowlist
from kge.services.ask.engine.core import Engine
from kge.services.ask.engine.graphview import ACCESS
from kge.services.data import service as data
from kge.services.identity.service import whoami
eng = Engine.serving(resolve())
q = eng.g.q
expected = [{**r, "reads": sorted({x["u.id"] for x in q("MATCH (p:Principal {id: $p})-[:CAN_READ]->(u:Unit) RETURN u.id", {"p": r["id"]})},
                                  key=eng.UNIT_ORDER.index),
             "processes": sorted({x["x.id"] for x in q("MATCH (p:Principal {id: $p})-[:MAY_PERFORM]->(x:Process) RETURN x.id", {"p": r["id"]})}),
             "writes_terms": q("MATCH (p:Principal {id: $p})-[:CAN_WRITE]->(:Term) RETURN count(*) AS n", {"p": r["id"]})[0]["n"] > 0}
            for r in q("MATCH (p:Principal) RETURN p.id AS id, p.role AS role ORDER BY id")]
posted, texts = [], []
post, execute = bigquery.Client._post, bigquery.Connection.execute
bigquery.Client._post = lambda self, url, body: (posted.append(url), post(self, url, body))[1]
bigquery.Connection.execute = lambda self, cypher, params=None: (texts.append(cypher), execute(self, cypher, params))[1]
processes = sorted({p for r in expected for p in r["processes"]})
same = [whoami(eng, r["id"]) == r and [p for p in processes if eng.g.may(r["id"], p)] == r["processes"]
        and eng.g.readable_units(r["id"]) == set(r["reads"]) and eng.g.can_write_terms(r["id"]) is r["writes_terms"] for r in expected]
access = len(posted)
who = next(r["id"] for r in expected if "querying" in r["processes"] and r["reads"])
unit = sorted(eng.g.readable_units(who), key=eng.UNIT_ORDER.index)[0]
allow = schema_allowlist(eng.g, unit, eng.RESTRICTED_COLUMNS)
table = sorted(allow.tables)[0]
column = sorted(allow.columns[table])[0]
data.preview(eng, who, unit, table, 3)
posted.clear()
texts.clear()
whoami(eng, who)
eng.g.may(who, "querying")
rows = data.preview(eng, who, unit, table, 3).row_count
whoami(eng, who)
rows += 10 * data.query(eng, who, unit, f"SELECT {column} FROM {table} LIMIT 2").row_count
preview, preview_posts = list(texts), len(posted)
from kge.eval import sets
texts.clear()
eng.g.read_domain()
domain = set(texts)
questions = [item["question"] for item in sets.items(eng.profile.estate() / "golden.json")]
job = []
for batch in (questions[:3], questions[3:12]):
    texts.clear()
    for question in batch:
        eng.answer(question, principal=eng.ASKER, arm_name="graph")
    job.append([t for t in texts if ACCESS.search(t) or t in domain])
print(json.dumps({"principals": len(expected), "same": all(same), "access_posts": access, "rows": rows, "preview_texts": preview,
                  "preview_posts": preview_posts, "questions": len(questions[3:12]), "job_reads": job[1]}))
'''


def twin(build: Path, ladybug: Path, artifact: str, *command: str, env: dict | None = None) -> subprocess.CompletedProcess:
    out = subprocess.run([sys.executable, "-m", "tests.graphtwin", str(ladybug), artifact, *command], cwd=DEMO,
                         capture_output=True, text=True, timeout=1800,
                         env={**__import__("os").environ, "PROFILES": str(registry(build)), "PROFILE": "",
                              "BIGQUERY_PROJECT": "twin-project", **(env or {})})
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    assert re.search(r"graphtwin: [1-9]\d* queries", out.stderr), out.stderr[-2000:]
    return out


def direct(build: Path, *command: str) -> subprocess.CompletedProcess:
    out = subprocess.run([sys.executable, *command], cwd=DEMO, capture_output=True, text=True, timeout=1800,
                         env={**__import__("os").environ, "PROFILES": str(registry(build)), "PROFILE": "", "GRAPH_STORE": ""})
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    return out


def pointer(build: Path) -> tuple[Path, Path]:
    """build's served graph given a BigQuery pointer beside its Ladybug file, as a load through the store writes one:
    the Ladybug file (the twin's) and the artifact."""
    import os
    os.environ.setdefault("BIGQUERY_PROJECT", "twin-project")
    from kge.adapters.graphstore.bigquery import Graph, prefix_of
    from kge.adapters.graphstore.gql import Schema
    prof = profile_of(build)
    lb = prof.graph_path()
    artifact = prof.artifacts / prof.client / prof.model_hash()
    graph = Graph(lb.with_name("graph.bigquery"))
    graph.ns.prefix = prefix_of(prof.model_hash(), "00000000")
    graph.write(Schema.from_graph_model(yaml.safe_load((artifact / "graph-model.yaml").read_text())))
    return lb, artifact


def test_parity_through_the_bigquery_store_is_the_local_stacks(tmp_path):
    build = scratch_build(tmp_path / "build")
    lb, _ = pointer(build)
    out = twin(build, lb, "-", "kge.eval.parity", "--snapshot", str(SNAPSHOT), "--stated", str(STATED),
               env={"GRAPHTWIN_RECORD": str(tmp_path / "texts.json")})
    assert out.stdout.strip().splitlines()[-1] == PARITY
    assert "on bigquery GoogleSQL" in out.stdout
    assert len(json.loads((tmp_path / "texts.json").read_text())) > 20


def test_access_checks_and_a_preview_through_the_bigquery_store_send_it_no_rights_query(tmp_path):
    """On the BigQuery store, as on Ladybug (tests/integration/test_principal_lookup.py): once the engine is open, every
    principal's whoami and access checks give the grants the graph holds with no query sent to BigQuery, a portal's
    whoami, preview and query send it none (the run trace's fingerprint, once per graph, taken first), and a
    golden pass, as a job makes one on its one engine, reads neither the access mirror nor the domain per question."""
    import shutil
    build = scratch_build(tmp_path / "build")
    lb, _ = pointer(build)
    shutil.copytree(profile_of(None).graph_path().parent / "landed-tables", lb.parent / "landed-tables")
    script = tmp_path / "budget.py"
    script.write_text(BUDGET)
    out = json.loads(twin(build, lb, "-", str(script)).stdout.strip().splitlines()[-1])
    assert out["principals"] > 1 and out["same"] is True
    assert out["access_posts"] == 0
    assert out["rows"] == 23
    assert out["preview_texts"] == [] and out["preview_posts"] == 0
    assert out["questions"] == 9 and out["job_reads"] == []

def dump(path: Path, schema) -> dict:
    """Every node and edge of the Ladybug graph at path, by table, each property, sorted: a run's times and the random
    part of a decision's id written the same way whatever their value."""
    from kge.adapters.graphstore.ladybug import LadybugStore
    db = LadybugStore().open(path)
    conn = LadybugStore().connect(db)
    stamp = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\+00:00|Z)?")
    decision = re.compile(r"/h[0-9a-f]{12}")

    def norm(v):
        if isinstance(v, str):
            return decision.sub("/h*", stamp.sub("<at>", v))
        if isinstance(v, float):
            return round(v, 9)
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    out = {}
    try:
        for n in schema.nodes.values():
            props = list(n.props)
            got = conn.execute(f"MATCH (x:{n.label}) RETURN " + ", ".join(f"x.{p}" for p in props)).get_all()
            out[n.label] = sorted((json.dumps([norm(v) for v in r], default=str) for r in got))
        for name, tables in schema.edges.items():
            for e in tables:
                props = list(e.props)
                ret = ", ".join([f"a.{schema.nodes[e.src].key}", f"b.{schema.nodes[e.dst].key}"] + [f"r.{p}" for p in props])
                got = conn.execute(f"MATCH (a:{e.src})-[r:{name}]->(b:{e.dst}) RETURN {ret}").get_all()
                out[e.table] = sorted((json.dumps([norm(v) for v in r], default=str) for r in got))
    finally:
        conn.close()
        db.close()
    return out


def test_load_pipeline_and_steward_through_the_bigquery_store_give_the_ladybug_graph(tmp_path):
    """Two copies of the build: one loaded, run through the pipeline and decided on as the local stack does, the other
    the same through the BigQuery store on the twin, whose own Ladybug graph it then is. The two graphs hold the same."""
    from kge.adapters.graphstore.gql import Schema
    local = scratch_build(tmp_path / "local", graph=False)
    cloud = scratch_build(tmp_path / "cloud", graph=False)
    prof = profile_of(local)
    artifact = prof.artifacts / prof.client / prof.model_hash()
    script = tmp_path / "decide.py"
    script.write_text(DECIDE)

    direct(local, "-m", "kge.graph.load", "--artifact", str(artifact))
    direct(local, "-m", "kge.pipeline.run")
    direct(local, str(script))
    direct(local, "-m", "kge.pipeline.run")

    lb = tmp_path / "twin" / "graph.ladybug"
    lb.parent.mkdir()
    twin(cloud, lb, str(artifact), "kge.graph.load", "--artifact", str(artifact))
    store = profile_of(cloud).graph_path().with_name("graph.bigquery")
    held = json.loads(store.read_text())
    assert held["store"] == "bigquery" and held["dataset"] == "kge_meridian_graph"
    assert re.fullmatch(f"g{prof.model_hash()[:16]}_[0-9a-f]{{8}}_", held["prefix"])
    twin(cloud, lb, str(artifact), "kge.pipeline.run")
    assert "decided" in twin(cloud, lb, str(artifact), str(script)).stdout
    twin(cloud, lb, str(artifact), "kge.pipeline.run")
    marked = twin(cloud, lb, str(artifact), "kge.graph.release", "--hash", prof.model_hash(), "--mark-loaded")
    assert "LOADED on bigquery" in marked.stdout
    assert "bigquery" in json.loads((store.parent / "LOADED").read_text())
    made = sorted(p.name for p in store.parent.iterdir())
    assert "graph.bigquery" in made and not [n for n in made if n.startswith(("graph.ladybug", "graph.kuzu"))], made

    schema = Schema.from_graph_model(yaml.safe_load((artifact / "graph-model.yaml").read_text()))
    want, got = dump(profile_of(local).graph_path(), schema), dump(lb, schema)
    assert want.keys() == got.keys()
    differ = {t: (len(want[t]), len(got[t])) for t in want if want[t] != got[t]}
    assert differ == {}, differ
    assert sum(len(v) for v in want.values()) > 1000


def test_the_twin_answers_no_query_it_has_no_cypher_twin_for(tmp_path):
    """So every command above ran on the store's translations alone: a query sent any other way would fail the run."""
    from tests.graphtwin import Twin
    t = Twin(tmp_path / "graph.ladybug", None)
    with pytest.raises(AssertionError, match="no Cypher twin"):
        t.post("https://bigquery.googleapis.com/bigquery/v2/projects/p/queries", {"query": "SELECT 1", "queryParameters": []})
