"""Role rights are read once per release; identity initialization uses the separately cached access table.
A write to the mirror drops its snapshot, and a new release identity reads it again."""
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from kge.pipeline.access_table import Access
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
    calls = []
    def grants():
        calls.append(True)
        return [{"username": f"{who}@example.com", "name": who, "roles": [who]} for who in roster]
    g.people = Access(SimpleNamespace(access_grants=grants, calls=calls))
    g._role_templates = {who: {"label": f"{who}_role"} for who in roster}
    g._roles_only = False
    g.identity = dict(RELEASE)
    g.writes = 0
    g._rights = None
    g._catalogue = None
    g.catalogue_figures = lambda _: {}
    g._snapshot_lock = threading.RLock()
    g._domain = {"units": ["retail", "sme"]}
    g.reader = lambda: store
    g.conn = store
    return g, store


@pytest.mark.parametrize("roster", [[], ["role_a"], ["role_a", "role_b", "role_c"]])
@pytest.mark.parametrize("caller", ["app", "agent", "gate"])
def test_identity_initialization_reads_the_access_table_once_without_graph_queries(monkeypatch, tmp_path, roster, caller):
    g, store = graph(roster)
    g._roles_only = caller == "gate"
    profile = SimpleNamespace(run_dir=lambda: tmp_path)
    engine = SimpleNamespace(g=g, profile=profile, UNIT_ORDER=[])
    if caller == "app":
        from kge import app
        monkeypatch.setattr(app, "_ids", None)
        monkeypatch.setattr(app, "PROFILE", profile)
        monkeypatch.setattr(app, "PASSWORDS", False)
        monkeypatch.setattr(app, "engine", lambda: engine)
        monkeypatch.setattr(app, "_serving", SimpleNamespace(get=lambda: None))
        monkeypatch.setattr(app.Access, "for_profile", lambda _: g.people)
        identities = app.ids
    else:
        agent = Agent(engine, llm=None)
        def identities():
            return agent.identities
    first = identities()
    if caller == "app":
        assert first.known == {f"{role}@example.com" for role in roster}
        assert first.by_token == {}
    else:
        expected = set(roster) | ({f"{role}@example.com" for role in roster} if caller == "agent" else set())
        assert set(first.by_token.values()) == expected
    assert identities() is first
    assert store.sent == ([] if caller == "app" else MIRROR)
    assert g.people.state.calls == ([] if caller == "gate" else [True])


def test_access_checks_and_whoami_answer_from_the_rights_read_with_no_query():
    g, store = graph(["ana", "bo"])
    g.rights()
    store.sent.clear()
    assert g.may("ana@example.com", "certification") and g.may("bo@example.com", "querying") and not g.may("bo@example.com", "certification")
    assert not g.may("nobody@example.com", "querying")
    assert g.processes("ana@example.com") == {"querying", "certification"} and g.processes("nobody@example.com") == set()
    assert g.readable_units("bo@example.com") == {"retail", "sme"} and g.readable_units("nobody@example.com") == set()
    assert g.can_write_terms("ana@example.com") and not g.can_write_terms("bo@example.com")
    assert g.principal("bo@example.com") == {"id": "bo@example.com", "name": "bo", "roles": [{"id": "bo", "label": "bo_role"}], "role": "bo_role", "reads": ["retail", "sme"], "processes": ["querying"],
                                 "writes_terms": False}
    assert g.principal("nobody@example.com") == {"id": "nobody@example.com"}
    assert [p["id"] for p in g.principals()] == ["ana@example.com", "bo@example.com"]
    assert g.principal_ids() == ["ana", "ana@example.com", "bo", "bo@example.com"]
    assert g.join_access("bo@example.com", ["sme", "retail"])["allowed"]
    g.readable_units("bo@example.com").add("wealth")
    g.processes("bo@example.com").add("certification")
    assert g.readable_units("bo@example.com") == {"retail", "sme"} and not g.may("bo@example.com", "certification")
    assert store.sent == []


def test_a_write_naming_the_access_mirror_drops_the_rights_read_and_others_keep_it():
    g, store = graph(["ana"])
    g.rights()
    store.sent.clear()
    g.q("MERGE (t:Term {name: $n}) SET t.domain = $d", {"n": "x", "d": "Customer"})
    assert g.may("ana@example.com", "querying")
    assert store.sent == ["MERGE (t:Term {name: $n}) SET t.domain = $d"]
    store.sent.clear()
    grant = ("MATCH (p:Principal)-[:CAN_WRITE]->(:Term) WITH DISTINCT p MATCH (t:Term {name: $n}) "
             "WHERE NOT EXISTS { MATCH (p)-[:CAN_WRITE]->(t) } CREATE (p)-[:CAN_WRITE]->(t)")
    g.q(grant, {"n": "x"})
    assert g.writes == 2
    assert g.may("ana@example.com", "querying")
    assert store.sent == [grant, *MIRROR]


def test_the_catalogue_is_read_once_and_a_write_naming_its_types_drops_it():
    tables = "MATCH (t:Tbl) RETURN t.unit AS unit, t.name AS name"
    g, store = graph([])
    store.rows = lambda cypher: [{"unit": "retail", "name": "customer"}] if cypher == tables else (
        [{"tbl": "retail.customer", "col": "customer_id"}] if "HAS_COLUMN" in cypher else [])
    assert g.unit_tables("retail") == {"customer"} and g.unit_tables("sme") == set()
    assert g.table_columns("retail", "customer") == ["customer_id"] and g.unit_joins("retail") == [] and g.release() is None
    assert len(store.sent) == 5, "one query per kind, the documents' metadata among them (#271)"
    store.sent.clear()
    g.unit_tables("retail").add("x")
    assert g.unit_tables("retail") == {"customer"} and store.sent == []
    g.q("MATCH (c:Col {id: $id}) SET c.description = $d", {"id": "retail.customer.customer_id", "d": "x"})
    assert g.unit_tables("retail") == {"customer"} and len(store.sent) == 6


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
        seen.append(g.may("bo@example.com", "querying"))
    threads = [threading.Thread(target=check) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == [True] * 8
    assert store.sent == MIRROR


def test_term_and_implementation_reads_are_cached_until_a_term_write_runs():
    g, store = graph(["ana"])
    terms_q = "MATCH (t:Term) RETURN t.name"
    impl_q = ("MATCH (c:Col)-[m:MAPS_TO {certified: true}]->(t:Term {name: $t}) RETURN c.unit AS unit, c.tbl AS tbl, "
              "c.name AS col, c.data_type AS dtype, c.grain AS grain, c.currency AS currency, c.calendar AS calendar, "
              "c.conversion AS conversion, m.certified_by AS certified_by, m.certified_on AS certified_on ORDER BY c.unit")
    store.rows = lambda cypher: [{"t.name": "revenue"}] if cypher == terms_q else (
        [{"unit": "retail", "tbl": "monthly_revenue", "col": "total_revenue_usd", "dtype": "DOUBLE",
          "grain": "monthly", "currency": "USD", "calendar": "gregorian", "conversion": None,
          "certified_by": "steward", "certified_on": "2026-01-01"}] if cypher == impl_q else [])
    assert g.terms() == ["revenue"]
    assert g.terms() == ["revenue"]
    cat_terms: list[str] = []
    worker = threading.Thread(
        target=lambda: cat_terms.extend(g.kept("_catalogue", lambda: SimpleNamespace(release=g.identity, terms=g.terms())).terms),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert cat_terms == ["revenue"]
    assert len(g.implementations("revenue")) == 1
    assert len(g.implementations("revenue")) == 1
    assert store.sent == [terms_q, impl_q]
    g.q("MERGE (t:Term {name: $n}) SET t.domain = $d", {"n": "margin", "d": "Finance"})
    assert g.terms() == ["revenue"]
    assert store.sent == [terms_q, impl_q, "MERGE (t:Term {name: $n}) SET t.domain = $d", terms_q]


def test_in_flight_term_read_does_not_repopulate_cache_after_interleaved_term_write():
    g, store = graph(["ana"])
    terms_q = "MATCH (t:Term) RETURN t.name"
    current = "old"
    entered = threading.Event()
    release_read = threading.Event()

    def rows(cypher: str) -> list[dict]:
        if cypher == terms_q:
            val = current
            if not entered.is_set():
                entered.set()
                release_read.wait(timeout=2.0)
            return [{"t.name": val}]
        return []

    store.rows = rows
    in_flight: list[list[str]] = []
    reader = threading.Thread(target=lambda: in_flight.append(g.terms()), daemon=True)
    reader.start()
    assert entered.wait(timeout=2.0)
    current = "new"
    g.q("MERGE (t:Term {name: $n})", {"n": "new"})
    release_read.set()
    reader.join(timeout=2.0)
    assert not reader.is_alive()
    assert in_flight == [["old"]]
    assert g.terms() == ["new"]
    assert store.sent == [terms_q, "MERGE (t:Term {name: $n})", terms_q]


def test_request_limits_middleware_resolves_principal_once_per_request(monkeypatch):
    import anyio
    from types import SimpleNamespace
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from kge.services import http as http_mod

    resolve_calls = 0

    def resolve(auth_header, cookie):
        nonlocal resolve_calls
        resolve_calls += 1
        return "ana@example.com"

    fake_served = SimpleNamespace(
        UNRELEASED_MESSAGE=None,
        COOKIE="kge_session",
        SIGN_IN="Sign in",
        ids=lambda: SimpleNamespace(resolve=resolve),
    )
    monkeypatch.setattr(http_mod, "served", fake_served)
    seen: list[str] = []
    sent_messages: list[dict] = []

    async def inner(scope, receive, send):
        seen.append(http_mod.principal(Request(scope)))
        await JSONResponse({"who": seen[-1]})(scope, receive, send)

    app = http_mod.RequestLimitsMiddleware(inner)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ask",
        "raw_path": b"/ask",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer token-123")],
    }
    messages = [{"type": "http.request", "body": b"{}", "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(msg):
        sent_messages.append(msg)

    anyio.run(app, scope, receive, send)
    assert seen == ["ana@example.com"]
    assert resolve_calls == 1


def test_defining_document_readiness_is_cached_by_release_purpose_and_graph_revision():
    g, store = graph(['ana'])
    ready = True
    store.rows = lambda cypher: [{'doc': 'definition'}] if ready and 'HAS_CHUNK' in cypher else []
    assert g.definition_documents('analysis') == {'definition'}
    leaked = g.definition_documents('analysis')
    leaked.clear()
    assert g.definition_documents('analysis') == {'definition'}
    assert len(store.sent) == 1
    assert g.definition_documents('reporting') == {'definition'}
    assert len(store.sent) == 2
    ready = False
    g.q("MATCH (c:Chunk) WHERE c.id = $id SET c.readiness = 'embed-prohibited'", {'id': 'chunk'})
    assert g.definition_documents('analysis') == set()
    assert len(store.sent) == 4
    ready = True
    g.identity = {**g.identity, 'graph': 'next-generation'}
    assert g.definition_documents('analysis') == {'definition'}
    assert len(store.sent) == 5


def test_in_flight_definition_read_cannot_recache_a_revoked_chunk():
    g, store = graph(['ana'])
    entered, finish = threading.Event(), threading.Event()
    ready = True
    def rows(cypher):
        if 'RETURN DISTINCT d.id AS doc' in cypher:
            current = ready
            if not entered.is_set():
                entered.set()
                finish.wait(timeout=2)
            return [{'doc': 'definition'}] if current else []
        return []
    store.rows = rows
    result = []
    worker = threading.Thread(target=lambda: result.append(g.definition_documents('analysis')), daemon=True)
    worker.start()
    assert entered.wait(timeout=2)
    ready = False
    g.q("MATCH (c:Chunk) WHERE c.id = $id SET c.readiness = 'embed-prohibited'", {'id': 'chunk'})
    finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [{'definition'}]
    assert g.definition_documents('analysis') == set()
    assert len(store.sent) == 3
