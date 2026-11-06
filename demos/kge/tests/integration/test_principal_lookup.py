"""Identity reads and access checks answer from the access mirror the engine read as it opened (Graph.rights): the same
role grants the graph holds, applied to the live table's people, and no graph-store query once the engine is open."""
import pytest

from kge.services.ask.engine import graphview
from kge.services.ask.engine.graphview import ACCESS
from kge.services.data import service as data
from kge.services.identity.service import readable_units, whoami
from kge.pipeline.sqlguard import schema_allowlist


class Counted:
    """A graph-store connection that keeps every query it is sent."""

    def __init__(self, conn, sent: list[str]):
        self.conn, self.sent = conn, sent

    def execute(self, cypher, params=None):
        self.sent.append(cypher)
        return self.conn.execute(cypher, params or {})

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self.conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.conn, name)


@pytest.fixture
def sent(eng, monkeypatch) -> list[str]:
    """Every query a read sends the engine's graph store from here on."""
    queries: list[str] = []
    connect = graphview.connect
    monkeypatch.setattr(graphview, "connect", lambda db: Counted(connect(db), queries))
    return queries


def held(eng) -> list[dict]:
    """Each role and its grants, read directly from the graph one query at a time."""
    q = eng.g.q
    rows = q("MATCH (p:Principal) RETURN p.id AS id, p.role AS role ORDER BY id")
    return [{**r, "name": r["id"], "roles": [{"id": r["id"], "label": r["role"]}], "reads": sorted({x["u.id"] for x in q("MATCH (p:Principal {id: $p})-[:CAN_READ]->(u:Unit) RETURN u.id", {"p": r["id"]})},
                                  key=eng.g.domain()["units"].index),
             "processes": sorted({x["x.id"] for x in q("MATCH (p:Principal {id: $p})-[:MAY_PERFORM]->(x:Process) RETURN x.id", {"p": r["id"]})}),
             "writes_terms": q("MATCH (p:Principal {id: $p})-[:CAN_WRITE]->(:Term) RETURN count(*) AS n", {"p": r["id"]})[0]["n"] > 0}
            for r in rows]


def granted(eng, roles: list[dict]) -> list[dict]:
    """The live people and their role unions, derived from raw role grants rather than Graph's permission methods."""
    templates = {r["id"]: r for r in roles}
    rows = {r["username"]: r for r in eng.g.people.grants()}
    expected = []
    for who in sorted(set(rows) | eng.g.people.admins()):
        row = rows.get(who, {})
        held_roles = [templates[role] for role in sorted(row.get("roles", [])) if role in templates and role != "access_admin"]
        labels = [{"id": role["id"], "label": role["role"]} for role in held_roles]
        expected.append({"id": who, "name": row.get("name") or who, "roles": labels,
                         "role": ", ".join(role["label"] for role in labels),
                         "reads": sorted({unit for role in held_roles for unit in role["reads"]}, key=eng.UNIT_ORDER.index),
                         "processes": sorted({process for role in held_roles for process in role["processes"]}),
                         "writes_terms": any(role["writes_terms"] for role in held_roles)})
    return expected


def test_access_checks_and_whoami_give_the_graphs_grants_with_no_graph_store_query(eng, sent):
    expected = held(eng)
    people = granted(eng, expected)
    processes = sorted({p for r in expected for p in r["processes"]})
    assert len(expected) > 1 and len(processes) > 2
    sent.clear()
    assert eng.g.principals() == people
    assert eng.g.principal_ids() == sorted({r["id"] for r in expected + people})
    for principal in expected + people:
        who = principal["id"]
        assert whoami(eng, who) == principal
        assert readable_units(eng, who) == sorted(principal["reads"])
        assert eng.g.processes(who) == set(principal["processes"])
        assert [p for p in processes if eng.g.may(who, p)] == principal["processes"]
        assert eng.g.can_write_terms(who) is principal["writes_terms"]
        assert eng.g.join_access(who, eng.UNIT_ORDER)["allowed_warehouses"] == principal["reads"]
    assert whoami(eng, "unknown-principal") == {"id": "unknown-principal"}
    assert not eng.g.may("unknown-principal", "querying") and eng.g.readable_units("unknown-principal") == set()
    assert sent == []


def test_a_portal_preview_and_query_send_the_graph_store_nothing(eng, sent):
    """whoami, then /data/preview or /data/query, as the portal sends them: rights and the allowlist's catalogue come from
    the engine's per-release reads (Graph.rights, Graph.catalogue), so the graph store is sent nothing before the
    warehouse is. The first traced request takes the run trace's fingerprint of the graph, once per graph and write
    (runtrace.fingerprint): that one is made before counting."""
    who = next(p["id"] for p in eng.g.principals() if "querying" in p["processes"] and p["reads"])
    unit = sorted(eng.g.readable_units(who), key=eng.UNIT_ORDER.index)[0]
    allow = schema_allowlist(eng.g, unit, eng.RESTRICTED_COLUMNS)
    table = sorted(allow.tables)[0]
    column = sorted(allow.columns[table])[0]
    data.preview(eng, who, unit, table, 3)
    sent.clear()
    assert whoami(eng, who)["id"] == who
    assert eng.g.may(who, "querying")
    rows = data.preview(eng, who, unit, table, 3)
    assert 0 < rows.row_count <= 3
    assert whoami(eng, who)["id"] == who
    assert data.query(eng, who, unit, f"SELECT {column} FROM {table} LIMIT 2").row_count == 2
    assert sent == [], sent


def control_plane(eng, sent: list[str]) -> set[str]:
    """The texts of the per-release reads: the access mirror's (ACCESS) and the client domain's, as read_domain sends them."""
    sent.clear()
    eng.g.read_domain()
    return set(sent)


@pytest.mark.parametrize("arm", ["graph", "schema"])
def test_a_golden_pass_reads_rights_and_the_domain_a_constant_number_of_times(eng, sent, arm):
    """A job (make margin, run_golden, the A/B harness, parity, the test drive) answers N questions on one engine, opened
    through Engine.serving or Engine.candidate: the access mirror and the domain it reads do not grow with N. The first
    questions take the run trace's fingerprint (once per graph and write); every question after sends neither."""
    from kge.eval import sets
    domain = control_plane(eng, sent)
    questions = [item["question"] for item in sets.items(eng.profile.estate() / "golden.json")]
    assert len(questions) >= 12
    reads = []
    for batch in (questions[:3], questions[3:12]):
        sent.clear()
        for question in batch:
            eng.answer(question, principal=eng.ASKER, arm_name=arm)
        reads.append([c for c in sent if ACCESS.search(c) or c in domain])
    assert reads[1] == [], reads[1]
