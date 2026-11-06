"""Role artifacts stay fixed while live people change, including an old release's graph."""
import json
from types import SimpleNamespace

import pytest
import yaml

from kge.compiler import catalogue, emit, pack as packs
from kge.compiler.compile import VERSION
from kge.compiler.emit import roles as role_templates
from kge.graph.load import role_principals, write_principals
from kge.pipeline.access_table import Access
from kge.pipeline.settings import ROOT
from tests.unit.test_principal_ids import graph, MIRROR, ROSTER, READS, PROCESSES, WRITERS


ROLES = {
    "reader": {"id": "reader", "label": "Analyst", "reads": ["retail"], "processes": ["querying"]},
    "keeper": {"id": "keeper", "label": "Steward", "reads": ["sme"], "writes": ["terms"],
               "processes": ["certification"]},
}


def live_graph(rows, *, legacy=False):
    g, store = graph([])
    g._role_templates = ROLES
    now = [0]
    calls = []

    def grants():
        calls.append(now[0])
        return [dict(row) for row in rows]

    g.people = Access(SimpleNamespace(access_grants=grants), clock=lambda: now[0])
    nodes = {"old_reader": ROLES["reader"], "old_keeper": ROLES["keeper"]} if legacy else ROLES

    def result(query):
        if query == ROSTER:
            return [{"id": key, "role": role["label"]} for key, role in nodes.items()]
        if query == READS:
            return [{"p": key, "x": unit} for key, role in nodes.items() for unit in role["reads"]]
        if query == PROCESSES:
            return [{"p": key, "x": proc} for key, role in nodes.items() for proc in role["processes"]]
        if query == WRITERS:
            return [{"p": key} for key, role in nodes.items() if "terms" in role.get("writes", [])]
        return []

    store.rows = result
    return g, store, now, calls


@pytest.mark.parametrize("legacy", [False, True])
def test_live_person_unions_roles_refreshes_and_loses_access_without_graph_queries(legacy):
    rows = [{"username": "person@example.com", "name": "Person", "roles": ["reader", "keeper"]}]
    g, store, now, calls = live_graph(rows, legacy=legacy)
    assert g.readable_units("person@example.com") == {"retail", "sme"}
    assert g.processes("person@example.com") == {"querying", "certification"}
    assert g.can_write_terms("person@example.com")
    assert g.principal("person@example.com")["name"] == "Person"
    assert g.principal("person@example.com")["roles"] == [
        {"id": "keeper", "label": "Steward"}, {"id": "reader", "label": "Analyst"}]
    assert g.principal_ids() == ["keeper", "person@example.com", "reader"]
    assert [row["id"] for row in g.principals()] == ["person@example.com"]
    assert store.sent == MIRROR and calls == [0]
    rows[0] = {**rows[0], "name": "Renamed", "roles": ["reader"]}
    now[0] = 59
    assert g.may("person@example.com", "certification")
    now[0] = 60
    assert g.readable_units("person@example.com") == {"retail"}
    assert not g.can_write_terms("person@example.com")
    assert g.principal("person@example.com")["name"] == "Renamed"
    rows.clear()
    now[0] = 120
    assert not g.may("person@example.com", "querying")
    assert g.principal_ids_people() == []
    assert store.sent == MIRROR and calls == [0, 60, 120]


def test_role_scoped_access_never_reads_the_people_table():
    g, store, _, calls = live_graph([])
    with g.as_roles():
        assert g.readable_units("reader") == {"retail"}
        assert g.processes("keeper") == {"certification"}
        assert g.can_write_terms("keeper")
        assert g.role_ids() == {"reader", "keeper"}
        assert g.principal_ids() == ["keeper", "reader"]
        assert g.principal("reader")["name"] == "Analyst"
    assert store.sent == MIRROR and calls == []


def test_legacy_username_matching_a_role_uses_live_union_and_not_implicit_grants():
    rows = [{"username": "reader", "name": "Legacy person", "roles": ["reader", "keeper"]}]
    g, _, now, _ = live_graph(rows)
    assert g.readable_units("reader") == {"retail", "sme"}
    rows[0] = {**rows[0], "roles": ["keeper"]}
    now[0] = 60
    assert g.readable_units("reader") == {"sme"}
    with g.as_roles():
        assert g.readable_units("reader") == {"retail"}
    assert g.readable_units("reader") == {"sme"}


def test_migrated_legacy_username_signs_in_and_keeps_its_role_rights():
    g, _, now, _ = live_graph([{"username": "legacy.reader@example.com", "name": "Old Reader", "roles": ["reader"]}], legacy=True)
    from kge.services.identity.service import Identities
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        ids = Identities(Path(folder), g.principal_ids_people())
        assert "legacy.reader@example.com" in ids.by_token.values()
        assert ids.token_for("old_reader") is None
    assert g.readable_units("legacy.reader@example.com") == {"retail"}
    assert g.principal("legacy.reader@example.com")["name"] == "Old Reader"
    assert not g.may("old_keeper", "certification")


def test_reserved_administrator_gets_no_pack_permissions(monkeypatch):
    monkeypatch.setenv("ACCESS_ADMINS", "bootstrap@example.com")
    g, _, _, _ = live_graph([{"username": "operator@example.com", "name": "Operator", "roles": ["access_admin"]}])
    for who in ("operator@example.com", "bootstrap@example.com"):
        assert g.principal(who)["roles"] == []
        assert not g.readable_units(who) and not g.processes(who) and not g.can_write_terms(who)


def test_role_rows_and_command_defaults_have_no_people(tmp_path):
    rows = role_principals(ROLES)
    assert [row["principal"] for row in rows] == ["reader", "keeper"]
    assert rows[1] == {"principal": "keeper", "role": "Steward", "reads": ["sme"],
                       "writes": ["terms"], "processes": ["certification"]}
    write_principals(tmp_path, rows)
    assert json.loads((tmp_path / "principals.json").read_text()) == rows
    assert not (tmp_path / "ROSTER").exists()


@pytest.mark.parametrize("client,prefix", [("meridian", "e2d00fb2"), ("fellmoor", "9035322f"), ("thornbury", "b1a273bb")])
def test_people_cannot_change_pack_or_artifact_bytes(tmp_path, client, prefix):
    meta = packs.meta_model(ROOT / "meta-model.yaml")
    from kge.adapters.appstate.files import FilesState
    from kge.adapters.objstore import open_store
    state = FilesState(None, open_store(str(tmp_path / "access")))

    def compiled():
        pack = packs.read(ROOT / "clients", client)
        assert pack.hash.startswith(prefix)
        enrichment, findings = catalogue.read(pack)
        assert findings == []
        return emit.build(pack, meta, VERSION, catalogue.derive(enrichment, pack))

    before = compiled()
    state.access_put({"username": "changed@example.com", "name": "Changed", "roles": ["reader"]},
                     by="operator@example.com", via="job")
    after = compiled()
    assert before.model_hash == after.model_hash and before.files == after.files
    assert b"changed@example.com" not in b"".join(before.files.values())


def test_catalogue_titles_and_certifying_role_validation():
    pack = packs.read(ROOT / "clients", "meridian")
    titles = {role["label"] for role in pack.data["access"]["roles"]}
    derived = catalogue.derive(None, pack)
    assert all(row["product_owner"] in titles and row["business_owner"] in titles and row["steward"] in titles
               for row in derived["products"])
    product = derived["products"][0]
    enrichment = {"format": 1, "units": [product["unit"]], "products": [
        {"id": product["id"], "unit": product["unit"], "table": product["table"], "steward": "analyst_retail"}]}
    assert any("performs certification" in finding.message for finding in catalogue.validate(enrichment, pack))
    enrichment["products"][0]["steward"] = "steward_glossary"
    assert catalogue.validate(enrichment, pack) == []
    assert catalogue.derive(enrichment, pack)["products"][0]["steward"] == "Steward"
    assert {row["principal"] for row in role_principals({r["id"]: r for r in role_templates(pack.data)["roles"]})} == {
        role["id"] for role in pack.data["access"]["roles"]}


def test_load_writes_exactly_one_principal_node_per_role(tmp_path, monkeypatch):
    from kge.adapters.graphstore import connect, open_db
    from kge.graph.load import load_principals
    db = open_db(tmp_path / "roles.ladybug")
    conn = connect(db)
    try:
        for ddl in (
            "CREATE NODE TABLE Principal(id STRING, role STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE Unit(id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE Term(name STRING, PRIMARY KEY (name))",
            "CREATE REL TABLE CAN_READ(FROM Principal TO Unit)",
            "CREATE REL TABLE CAN_WRITE(FROM Principal TO Term)",
        ):
            conn.execute(ddl)
        conn.execute("CREATE (:Unit {id: 'retail'})")
        conn.execute("CREATE (:Unit {id: 'sme'})")
        conn.execute("CREATE (:Term {name: 'Revenue'})")
        rows = role_principals(ROLES)
        assert load_principals(conn, rows) == 2
        assert load_principals(conn, rows) == 2
        assert conn.execute("MATCH (p:Principal) RETURN p.id, p.role ORDER BY p.id").get_all() == [
            ["keeper", "Steward"], ["reader", "Analyst"]]
        assert conn.execute("MATCH (p:Principal)-[:CAN_READ]->(u:Unit) RETURN p.id, u.id ORDER BY p.id").get_all() == [
            ["keeper", "sme"], ["reader", "retail"]]
        from kge.graph import load
        conn.execute("CREATE NODE TABLE Release(client STRING, PRIMARY KEY (client))")
        conn.execute("CREATE (:Release {client: 'acme'})")
        conn.execute("CREATE (:Principal {id: 'retired@example.com', role: 'Analyst'})")
        client = tmp_path / "clients" / "acme"
        client.mkdir(parents=True)
        (client / "pack.yaml").write_text(yaml.safe_dump({
            "sources": [{"id": "retail"}, {"id": "sme"}], "access": {"roles": list(ROLES.values())}}))
        prof = SimpleNamespace(id="acme", client="acme", clients=client.parent,
                               graph_path=lambda: tmp_path / "roles.ladybug", run_dir=lambda: tmp_path / "run")
        seeded = []
        monkeypatch.setattr(load, "open_graph", lambda *args, **kwargs: conn)
        monkeypatch.setattr(load.access_table, "seed", lambda profile, *, roles: seeded.append((profile, roles)))
        assert load.sync_principals(prof) == 0
        assert len(seeded) == 1 and seeded[0][0] is prof
        assert set(seeded[0][1]) == {"keeper", "reader"}
        assert seeded[0][1]["reader"]["processes"] == ["querying"]
        assert conn.execute("MATCH (p:Principal) RETURN p.id ORDER BY p.id").get_all() == [["keeper"], ["reader"]]
        assert sorted(json.loads((prof.run_dir() / "principals.json").read_text()), key=lambda row: row["principal"]) == sorted(rows, key=lambda row: row["principal"])
    finally:
        conn.close()
        db.close()
