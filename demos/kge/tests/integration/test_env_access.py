"""Batch 3: embeddings (8), environment, the graph lock and its files (9), identity and roles (10).

    .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine.core import Engine
from kge.adapters.graphstore import graph_path, sidecars
from tests.scratch import BUILD, graph_of, profile_of, registry, run_of, scratch_build, script

DEMO = BUILD.parent


@pytest.fixture()
def build(tmp_path):
    return scratch_build(tmp_path / "build")


def serving(build: Path, pid: str) -> None:
    """The pid an API serving build's profile writes, in the profile's run state."""
    run_of(build).mkdir(parents=True, exist_ok=True)
    (run_of(build) / "api.pid").write_text(pid)


def test_local_embedder_fails_loudly_without_its_package(monkeypatch):
    from kge.adapters.model import embed
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        embed.make_embedder()
    monkeypatch.setenv("EMBEDDING_PROVIDER", "word2vec")
    with pytest.raises(ValueError):
        embed.make_embedder()


def test_local_embedder_projects_rather_than_truncates(monkeypatch):
    """The stand-in model puts all its signal beyond index 128, which truncation would lose."""
    from kge.adapters.model import embed

    class Fake:
        def __init__(self, name):
            pass

        def get_sentence_embedding_dimension(self):
            return 384

        def encode(self, text, normalize_embeddings=True):
            v = np.zeros(384)
            v[300 + len(text) % 50] = 1.0
            return v

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=Fake))
    e = embed.LocalEmbedder()
    v = np.array(e.embed("abc"))
    assert e.name == "all-MiniLM-L6-v2+jl128" and v.shape == (embed.DIM,)
    assert np.linalg.norm(v) == pytest.approx(1.0) and np.abs(v).sum() > 0


def test_retrieval_only_scores_chunks_from_the_querying_model(build):
    e = Engine(profile_of(build))
    hits = e.g.retrieve("records retention policy", "admin", "assistant", k=3)
    top = hits["exact"][0]["chunk"]
    e.g.q("MATCH (c:Chunk {id: $c}) SET c.embedding_model = 'some-other-model'", {"c": top})
    again = e.g.retrieve("records retention policy", "admin", "assistant", k=3)
    assert top not in {h["chunk"] for h in again["exact"]} and again["skipped_other_model"] == 1
    e.close()


def test_env_file_is_parsed_and_never_overrides(tmp_path, monkeypatch):
    from kge.pipeline import envfile
    f = tmp_path / ".env"
    f.write_text("# comment\nMODEL_PROVIDER=none          # none | anthropic\nMODEL_BASE_URL='http://localhost:11434/v1'\nEMBEDDING_PROVIDER=hash\n")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "set-outside")
    values = envfile.load_env(f)
    assert values["MODEL_PROVIDER"] == "none"
    import os
    assert os.environ["MODEL_BASE_URL"] == "http://localhost:11434/v1"
    assert os.environ["EMBEDDING_PROVIDER"] == "set-outside"


def test_a_locked_graph_says_who_holds_it(build):
    e = Engine(profile_of(build))
    serving(build, "4242")
    r = script("kge/pipeline/run.py", build=build)
    e.close()
    assert r.returncode == 2 and "locked" in r.stderr and "4242" in r.stderr


@pytest.mark.parametrize("flags", [["--artifact"], ["--principals-only"]], ids=["make graph", "make principals"])
def test_make_graph_refuses_while_the_api_holds_the_graph(build, flags, compiled):
    """Nothing is deleted or rewritten under the API: the lock speaks first."""
    flags = [*flags, compiled] if flags == ["--artifact"] else flags
    e = Engine(profile_of(build))
    serving(build, "4242")
    files = {p.name: p.stat().st_ino for p in [graph_of(build), *sidecars(graph_of(build))]}
    principals = (run_of(build) / "principals.json").read_text()
    r = script("kge/graph/load.py", *flags, build=build)
    still = e.g.q("MATCH (d:Document) RETURN count(*) AS n")[0]["n"]
    e.close()
    assert r.returncode == 2 and "locked" in r.stderr and "4242" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert {p.name: p.stat().st_ino for p in [graph_of(build), *sidecars(graph_of(build))]} == files
    assert (run_of(build) / "principals.json").read_text() == principals and still > 0


LEAVE_A_WAL = """
import os, sys
from kge.adapters.graphstore import connect, open_db
db = open_db(sys.argv[1]); c = connect(db)
c.execute("CREATE NODE TABLE T(id STRING, v STRING, PRIMARY KEY (id))")
c.execute("CREATE (:T {id: 'a', v: 'x'})")
c.close(); db.close()
db = open_db(sys.argv[1]); c = connect(db)
c.execute("MATCH (t:T {id: 'a'}) SET t.v = 'y'")
os._exit(0)
"""

STEWARD_THEN_STOP = """
import os, sys
from kge.services.ask.engine.core import Engine
from kge.pipeline.profile import resolve
e = Engine(resolve())
tag = next(r for r in e.g.steward_queue() if r["doc"] == "POL-RETENTION" and r["family"] == "privacy")
e.g.steward_decide(tag["tag"], "steward_glossary", "reject")
os._exit(0)
"""


def wal(path):
    """The write-ahead log the store keeps beside a graph: graph.ladybug.wal."""
    return path.with_name(path.name + ".wal")


def test_a_fresh_graph_ignores_the_log_the_last_one_left(tmp_path):
    """A process that stops without closing the graph leaves its write-ahead log beside it."""
    from kge.graph.load import open_graph
    path = graph_path(tmp_path)
    subprocess.run([sys.executable, "-c", LEAVE_A_WAL, str(path)], check=True, cwd=DEMO)
    assert wal(path).exists()
    conn = open_graph(path, fresh=True)
    assert conn.execute("CALL show_tables() RETURN *").get_as_df().empty
    conn.close()
    conn.database.close()


@pytest.mark.parametrize("old", ["file", "directory"])
def test_a_fresh_graph_replaces_one_it_cannot_open(tmp_path, old):
    """The lock check opens the old graph; one the store cannot read is replaced, not a reason to stop."""
    from kge.graph.load import open_graph
    path = graph_path(tmp_path)
    if old == "file":
        path.write_text("not a graph " * 1000)
    else:
        path.mkdir()
        (path / "catalog.kz").write_text("an older on-disk format")
    conn = open_graph(path, fresh=True)
    assert path.is_file() and conn.execute("CALL show_tables() RETURN *").get_as_df().empty
    conn.close()
    conn.database.close()


def test_make_graph_runs_after_a_steward_decision(build, compiled):
    """The README's reset: a steward decision, a stop that never closed the graph, then make graph."""
    subprocess.run([sys.executable, "-c", STEWARD_THEN_STOP], check=True, cwd=DEMO,
                   env={**os.environ, "PROFILES": str(registry(build)), "PROFILE": ""})
    assert wal(graph_of(build)).exists()
    r = script("kge/graph/load.py", "--artifact", compiled, build=build)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["documents"] > 0


def test_stopping_the_api_closes_the_graph(build, monkeypatch):
    """A steward's decision sits in the write-ahead log while the graph is open; once the API closes it, the store
    checkpoints the log into the graph."""
    import kge.app as appmod
    from kge.services.identity.service import token_for
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        steward = {"authorization": f"Bearer {token_for(run_of(build), 'steward_glossary')}"}
        tag = next(t for t in c.get("/steward/queue", headers=steward).json() if t["doc"] == "POL-RETENTION" and t["family"] == "privacy")
        assert c.post("/steward/decide", json={"tag": tag["tag"], "decision": "reject"}, headers=steward).status_code == 200
        assert wal(graph_of(build)).exists()
    assert appmod._engine is None
    assert not wal(graph_of(build)).exists()


@pytest.fixture()
def client(build, monkeypatch):
    import kge.app as appmod
    monkeypatch.setattr(appmod, "PROFILE", profile_of(build))
    monkeypatch.setattr(appmod, "_engine", None)
    monkeypatch.setattr(appmod, "_ids", None)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


Q = "Combined Retail and SME revenue for June 2025"


def test_identity_endpoints_keep_every_engineering_process_from_the_graph(client):
    who = client.get("/whoami", headers=client.as_("knowledge_engineer"))
    assert who.status_code == 200
    engineer = who.json()
    assert engineer["processes"] == ["building", "drafting", "improving", "querying"]
    assert not engineer["writes_terms"]
    assert client.get("/principals").status_code == 401
    principals = client.get("/principals", headers=client.as_("knowledge_engineer"))
    assert principals.status_code == 200
    assert engineer == next(p for p in principals.json() if p["id"] == "knowledge_engineer")
    assert client.get("/steward/queue", headers=client.as_("knowledge_engineer")).status_code == 403


def test_no_identity_no_answer(client):
    assert client.post("/answer", json={"question": Q}).status_code == 401
    assert client.get("/steward/queue").status_code == 401


def test_a_body_cannot_name_its_principal(client):
    r = client.post("/answer", json={"question": Q, "principal": "admin"}, headers=client.as_("steward"))
    assert r.status_code == 422


def test_the_join_rule_through_the_api(client):
    admin = client.post("/answer", json={"question": Q}, headers=client.as_("admin")).json()
    steward = client.post("/answer", json={"question": Q}, headers=client.as_("steward")).json()
    assert admin["behaviour"] == "sum_with_verdict" and admin["principal"] == "admin"
    assert steward["behaviour"] == "blocked_access" and steward["denied_warehouses"] == ["sme"]


def test_session_cookie_sign_in(client, build):
    from kge.services.identity.service import token_for
    r = client.post("/session", json={"token": token_for(run_of(build), "steward")})
    assert r.status_code == 200 and r.json()["id"] == "steward"
    assert client.get("/whoami").json()["id"] == "steward"
    assert client.post("/answer", json={"question": Q}).json()["behaviour"] == "blocked_access"
    client.delete("/session")
    assert client.get("/whoami").status_code == 401


def test_demo_mode_is_off_by_default(client):
    assert client.get("/mode").json() == {"demo_mode": False}
    assert client.post("/session/demo", json={"principal": "admin"}).status_code == 404


@pytest.fixture()
def local_client(build, monkeypatch):
    """A client calling from 127.0.0.1 with demo mode on."""
    import kge.app as appmod
    monkeypatch.setattr(appmod, "PROFILE", profile_of(build))
    monkeypatch.setattr(appmod, "_engine", None)
    monkeypatch.setattr(appmod, "_ids", None)
    monkeypatch.setattr(appmod, "DEMO_MODE", True)
    with TestClient(appmod.app, base_url="http://localhost", client=("127.0.0.1", 50000)) as c:
        yield c
    appmod._engine.close() if appmod._engine else None


def test_demo_sign_in_by_name_still_enforces_access(local_client):
    """Signed in by name, the steward still cannot read SME: the rules come from identity, not the sign-in route."""
    assert local_client.get("/mode").json() == {"demo_mode": True}
    r = local_client.post("/session/demo", json={"principal": "steward"})
    assert r.status_code == 200 and r.json()["id"] == "steward"
    assert local_client.get("/whoami").json()["id"] == "steward"
    assert local_client.post("/answer", json={"question": Q}).json()["behaviour"] == "blocked_access"
    assert local_client.post("/session/demo", json={"principal": "nobody"}).status_code == 404


def test_demo_mode_refuses_callers_off_this_machine(build, monkeypatch):
    import kge.app as appmod
    monkeypatch.setattr(appmod, "PROFILE", profile_of(build))
    monkeypatch.setattr(appmod, "_engine", None)
    monkeypatch.setattr(appmod, "_ids", None)
    monkeypatch.setattr(appmod, "DEMO_MODE", True)
    with TestClient(appmod.app, client=("remote-caller", 50000)) as c:
        assert c.get("/mode").json() == {"demo_mode": False}
        assert c.post("/session/demo", json={"principal": "admin"}).status_code == 404
    appmod._engine.close() if appmod._engine else None


def test_demo_mode_does_not_open_the_query_body(local_client):
    local_client.post("/session/demo", json={"principal": "steward"})
    assert local_client.post("/answer", json={"question": Q, "principal": "admin"}).status_code == 422


def test_querying_identities_cannot_certify(client):
    assert client.get("/steward/queue", headers=client.as_("analyst_retail")).status_code == 403
    tag = client.get("/steward/queue", headers=client.as_("steward")).json()[0]["tag"]
    r = client.post("/steward/decide", json={"tag": tag, "decision": "accept"}, headers=client.as_("analyst_retail"))
    assert r.status_code == 403


def test_decisions_are_validated(client):
    tag = next(t for t in client.get("/steward/queue", headers=client.as_("steward")).json() if t["family"] == "candidate_term")["tag"]
    assert client.post("/steward/decide", json={"tag": tag, "decision": "maybe"}, headers=client.as_("steward")).status_code == 400
    assert client.post("/steward/decide", json={"tag": "no-such-tag", "decision": "accept"}, headers=client.as_("steward")).status_code == 404
    assert client.post("/steward/decide", json={"tag": tag, "decision": "accept"}, headers=client.as_("steward")).status_code == 200
    assert client.post("/steward/decide", json={"tag": tag, "decision": "reject"}, headers=client.as_("steward")).status_code == 409


def test_the_tagging_identity_cannot_query_or_certify(client):
    assert client.post("/answer", json={"question": Q}, headers=client.as_("pipeline_tagger")).status_code == 403
    assert client.get("/steward/queue", headers=client.as_("pipeline_tagger")).status_code == 403


def test_certification_needs_can_write_in_core(build):
    e = Engine(profile_of(build))
    tag = next(t for t in e.g.steward_queue() if t["family"] == "candidate_term")["tag"]
    with pytest.raises(PermissionError):
        e.g.steward_decide(tag, "analyst_retail", "accept")
    with pytest.raises(PermissionError):
        e.g.steward_decide(tag, "pipeline_tagger", "accept")
    assert e.answer(Q, principal="pipeline_tagger")["behaviour"] == "refuse"
    e.close()


def test_only_a_tagging_identity_runs_the_pipeline(build):
    r = script("kge/pipeline/run.py", "--as", "steward", build=build)
    assert r.returncode == 2 and "not a tagging identity" in r.stderr
    assert "run as one of the graph's: admin, pipeline_tagger" in r.stderr, "the deployment's, read from the graph, never named in code"
