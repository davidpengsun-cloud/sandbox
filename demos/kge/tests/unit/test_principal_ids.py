"""The access mirror is read once per release (Graph.rights): identity initialization, every access check and whoami
answer from it, a write to the mirror drops it, and a new release identity reads it again."""
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.graphview import Graph

RELEASE = {"model_hash": "ab" * 32, "graph": "g1", "chunks": None}
ROSTER = "MATCH (p:Principal) RETURN p.id AS id, p.role AS role ORDER BY id"
READS = "MATCH (p:Principal)-[:CAN_READ]->(u:Unit) RETURN p.id AS p, u.id AS x"
PROCESSES = "MATCH (p:Principal)-[:MAY_PERFORM]->(x:Process) RETURN p.id AS p, x.id AS x"
WRITERS = "MATCH (p:Principal)-[:CAN_WRITE]->(:Term) RETURN DISTINCT p.id AS p"
MIRROR = [ROSTER, READS, PROCESSES, WRITERS]


class Result:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def get_as_df(self):
        return pd.DataFrame(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


class Store:
    """A graph store connection that answers the access mirror's four reads from a roster (the first principal
    certifies and writes terms), counting every query it is sent."""

    def __init__(self, roster: list[str]):
        self.roster = roster
        self.sent: list[str] = []

    def rows(self, cypher: str) -> list[dict]:
        if cypher == ROSTER:
            return [{"id": who, "role": f"{who}_role"} for who in self.roster]
        if cypher == READS:
            return [{"p": who, "x": unit} for who in self.roster for unit in ("retail", "sme")]
        if cypher == PROCESSES:
            return [{"p": who, "x": "querying"} for who in self.roster] + [{"p": who, "x": "certification"} for who in self.roster[:1]]
        if cypher == WRITERS:
            return [{"p": who} for who in self.roster[:1]]
        return []

    def execute(self, cypher: str, params=None) -> Result:
        self.sent.append(cypher)
        return Result(self.rows(cypher))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def graph(roster: list[str]) -> tuple[Graph, Store]:
    """A Graph on Store, as Graph.__init__ leaves one, but for the database."""
    g = object.__new__(Graph)
    store = Store(roster)
    g.identity = dict(RELEASE)
    g.writes = 0
    g._rights = None
    g._catalogue = None
    g._snapshot_lock = threading.Lock()
    g._domain = {"units": ["retail", "sme"]}
    g.reader = lambda: store
    g.conn = store
    return g, store


@pytest.mark.parametrize("roster", [[], ["role_a"], ["role_a", "role_b", "role_c"]])
@pytest.mark.parametrize("caller", ["app", "agent"])
def test_identity_initialization_reads_the_access_mirror_once(monkeypatch, tmp_path, roster, caller):
    g, store = graph(roster)
    profile = SimpleNamespace(run_dir=lambda: tmp_path)
    engine = SimpleNamespace(g=g, profile=profile, UNIT_ORDER=[])
    if caller == "app":
        from kge import app
        monkeypatch.setattr(app, "_ids", None)
        monkeypatch.setattr(app, "PROFILE", profile)
        monkeypatch.setattr(app, "PASSWORDS", False)
        monkeypatch.setattr(app, "engine", lambda: engine)
        monkeypatch.setattr(app, "_serving", SimpleNamespace(get=lambda: None))
        identities = app.ids
    else:
        agent = Agent(engine, llm=None)
        def identities():
            return agent.identities
    first = identities()
    assert list(first.by_token.values()) == roster
    assert identities() is first
    assert store.sent == MIRROR


def test_access_checks_and_whoami_answer_from_the_rights_read_with_no_query():
    g, store = graph(["ana", "bo"])
    g.rights()
    store.sent.clear()
    assert g.may("ana", "certification") and g.may("bo", "querying") and not g.may("bo", "certification")
    assert not g.may("nobody", "querying")
    assert g.processes("ana") == {"querying", "certification"} and g.processes("nobody") == set()
    assert g.readable_units("bo") == {"retail", "sme"} and g.readable_units("nobody") == set()
    assert g.can_write_terms("ana") and not g.can_write_terms("bo")
    assert g.principal("bo") == {"id": "bo", "role": "bo_role", "reads": ["retail", "sme"], "processes": ["querying"],
                                 "writes_terms": False}
    assert g.principal("nobody") == {"id": "nobody"}
    assert [p["id"] for p in g.principals()] == g.principal_ids() == ["ana", "bo"]
    assert g.join_access("bo", ["sme", "retail"])["allowed"]
    g.readable_units("bo").add("wealth")
    g.processes("bo").add("certification")
    assert g.readable_units("bo") == {"retail", "sme"} and not g.may("bo", "certification")
    assert store.sent == []


def test_a_write_naming_the_access_mirror_drops_the_rights_read_and_others_keep_it():
    g, store = graph(["ana"])
    g.rights()
    store.sent.clear()
    g.q("MERGE (t:Term {name: $n}) SET t.domain = $d", {"n": "x", "d": "Customer"})
    assert g.may("ana", "querying")
    assert store.sent == ["MERGE (t:Term {name: $n}) SET t.domain = $d"]
    store.sent.clear()
    grant = ("MATCH (p:Principal)-[:CAN_WRITE]->(:Term) WITH DISTINCT p MATCH (t:Term {name: $n}) "
             "WHERE NOT EXISTS { MATCH (p)-[:CAN_WRITE]->(t) } CREATE (p)-[:CAN_WRITE]->(t)")
    g.q(grant, {"n": "x"})
    assert g.writes == 2
    assert g.may("ana", "querying")
    assert store.sent == [grant, *MIRROR]


def test_the_catalogue_is_read_once_and_a_write_naming_its_types_drops_it():
    tables = "MATCH (t:Tbl) RETURN t.unit AS unit, t.name AS name"
    g, store = graph([])
    store.rows = lambda cypher: [{"unit": "retail", "name": "customer"}] if cypher == tables else (
        [{"tbl": "retail.customer", "col": "customer_id"}] if "HAS_COLUMN" in cypher else [])
    assert g.unit_tables("retail") == {"customer"} and g.unit_tables("sme") == set()
    assert g.table_columns("retail", "customer") == ["customer_id"] and g.unit_joins("retail") == [] and g.release() is None
    assert len(store.sent) == 4
    store.sent.clear()
    g.unit_tables("retail").add("x")
    assert g.unit_tables("retail") == {"customer"} and store.sent == []
    g.q("MATCH (c:Col {id: $id}) SET c.description = $d", {"id": "retail.customer.customer_id", "d": "x"})
    assert g.unit_tables("retail") == {"customer"} and len(store.sent) == 5


def test_a_new_release_identity_reads_the_access_mirror_again():
    g, store = graph(["ana"])
    g.rights()
    g.identity = {**RELEASE, "graph": "g2"}
    assert g.rights().release == g.identity
    assert store.sent == MIRROR * 2


def test_requests_arriving_together_read_the_access_mirror_once():
    g, store = graph(["ana", "bo"])
    start = threading.Barrier(8)
    seen = []

    def check():
        start.wait()
        seen.append(g.may("bo", "querying"))
    threads = [threading.Thread(target=check) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == [True] * 8
    assert store.sent == MIRROR
