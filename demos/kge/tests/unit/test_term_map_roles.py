"""A term's map (GET /map/{term} and the map_term tool) for every role that may ask, on an invented two-unit pack: an
engineer's is full, with its physical columns, context joins, no-join facts and certified joins; a business user's or
a steward's is compact, in business names, with none of them; both carry the term's explained paths (#628), in
business words for all but the engineer; a unit the caller may not read is in neither, over HTTP
and MCP alike; and an admin-only person is refused and audited on the ask row."""
import json
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.adapters.roleaudit import role_audit_log
from kge.semantics.query import Context, Join, Rule
from kge.services import http
from kge.services.ask import mcp as ask_mcp, routes as ask_routes

TERM = "Net Sales"
PEOPLE = {"business": "analyst_north", "steward": "steward_north", "engineer": "engineer_both", "admin": "admin"}
ROLES = {"analyst_north": {"processes": ["querying"]},
         "steward_north": {"processes": ["querying", "certification"]},
         "engineer_both": {"processes": ["querying", "building"]},
         "admin": {"processes": ["querying", "certification"]}}
READS = {"analyst_north": {"north"}, "steward_north": {"north"}, "engineer_both": {"north", "south"}, "admin": {"north"}}
PHYSICAL = ("SALES_FCT_1", "NET_AMT", "sum(SALES_FCT_1.NET_AMT)", "north.SALES_FCT_1", "north.REGION_DIM_1",
            "south_sales", "net_amount", "south.REGION_DIM_1")
UNREADABLE = ("south", "South Region", "south_sales", "SOUTH-POLICY", "South Sales Office")


class Graph:
    """An invented graph: Net Sales certified in north (SALES_FCT_1.NET_AMT) and south (south_sales.net_amount), one
    context in each unit, a no-join fact and a certified join between them."""

    people = SimpleNamespace(is_admin=lambda who: False)

    def acting_roles(self, who):
        return {who}

    def role_templates(self):
        return ROLES

    def may(self, who, process):
        return process == "querying" and who in ROLES

    def readable_units(self, who):
        return READS.get(who, set())

    def implementations(self, term):
        return [{"unit": "north", "tbl": "SALES_FCT_1", "col": "NET_AMT", "dtype": "DECIMAL(14,2)", "grain": "month",
                 "currency": "EUR", "calendar": "calendar", "conversion": "none", "certified_by": "Sales Data Steward",
                 "certified_on": "2026-01-15"},
                {"unit": "south", "tbl": "south_sales", "col": "net_amount", "dtype": "DECIMAL(14,2)", "grain": "month",
                 "currency": "EUR", "calendar": "calendar", "conversion": "none", "certified_by": "Sales Data Steward",
                 "certified_on": "2026-01-15"}] if term == TERM else []

    def q(self, cypher, params=None):
        if "BROADER" in cypher or "CODED_AS" in cypher:
            return []
        if "t.id AS id" in cypher:
            return [{"name": TERM, "id": "net-sales", "definition": "Sales less returns and discounts, in euros."}]
        if "Measure" in cypher:
            return [{"id": "north.net-sales", "currency": "EUR"}, {"id": "south.net-sales", "currency": "EUR"}]
        if "Term" in cypher:
            return [{"definition": "Sales less returns and discounts, in euros.", "steward": "Sales Data Steward"}]
        raise AssertionError(cypher)

    def domain(self):
        return {"labels": {"north": "North Region", "south": "South Region"}}

    def catalogue_products(self):
        return SimpleNamespace(items=(
            {"id": "north.SALES_FCT_1", "unit": "north", "domain": "Sales", "community": "Sales performance",
             "business_owner": "North Sales Office", "product_owner": "Steward", "steward": "Steward"},
            {"id": "south.south_sales", "unit": "south", "domain": "Sales", "community": "Sales performance",
             "business_owner": "South Sales Office", "product_owner": "Steward", "steward": "Steward"}))

    def defining_docs(self, term):
        return [{"id": "MD-NET-SALES", "title": "Metric Definition: Net Sales", "shelf": "certified", "version": "1.0",
                 "published_at": "2026-01-15", "superseded_by": None},
                {"id": "SOUTH-POLICY", "title": "South returns policy", "shelf": "certified", "version": "2.0",
                 "published_at": "2026-02-01", "superseded_by": None}]

    def certification(self, ids):
        return {doc: {"unit": "south" if doc == "SOUTH-POLICY" else ""} for doc in ids}

    def uncertified_mentions(self, term):
        return [{"id": "NOTE-1", "title": "A sales note", "excerpt": "NET_AMT is net of returns"}]

    def no_join(self, a, b):
        return {"asserted_by": "Data Office", "asserted_on": "2026-01-15", "note": "north and south customers differ"}

    def certified_join(self, a, b):
        return {"declared_join": "north_south_region", "asserted_by": "Data Office", "asserted_on": "2026-01-15"}

    def standards(self, term):
        return []


def context(unit, fact):
    return Context(id=f"{unit}_net_sales", label=f"{unit.title()} net sales", description=f"sum of {fact}.NET_AMT",
                   term=TERM, measure=f"{unit}.net-sales", expression=f"sum({fact}.NET_AMT)", aggregation="sum",
                   grain="month", fact=f"{unit}.{fact}", values=(),
                   filters=(Rule("booked", "Booked sales only", "STATUS_CD = 'B'", f"{unit}.{fact}"),),
                   selectors=(Rule("tests", "Test accounts left out", "TEST_IN = 'Y'", f"{unit}.REGION_DIM_1", "exclude"),),
                   joins=(Join(f"{unit}_region", "key", "REGION_ID = REGION_ID", None, None, f"{unit}.{fact}",
                               f"{unit}.REGION_DIM_1", True),))


@pytest.fixture
def served(monkeypatch, tmp_path):
    monkeypatch.delenv("STORE_URL", raising=False)
    contexts = [context("north", "SALES_FCT_1"), context("south", "south_sales")]
    entities = {f"{unit}_{name}": {"label": label, "table": f"{unit}.{table}", "key": ["ID"]}
                for unit, fact in (("north", "SALES_FCT_1"), ("south", "south_sales"))
                for name, label, table in (("sale", "sale", fact), ("region", "sales region", "REGION_DIM_1"))}
    semantics = SimpleNamespace(contexts_of=lambda term: list(contexts) if term == TERM else [],
                                contexts={c.id: c for c in contexts}, entities=entities, scd2={}, releases={},
                                declared={j.id: j for c in contexts for j in c.joins},
                                measure_declarations={c.measure: {"aggregation": "sum", "grain": "month", "currency": "EUR",
                                                                  "snapshot": False} for c in contexts})
    engine = SimpleNamespace(g=Graph(), UNIT_ORDER=["north", "south"], UNIT_LABEL={"north": "North Region", "south": "South Region"},
                             term_names=[TERM], semantics=semantics)
    state = SimpleNamespace(engine=lambda: engine, SIGN_IN="sign in",
                            PROFILE=SimpleNamespace(run_dir=lambda: tmp_path, client="invented"),
                            ids=lambda: SimpleNamespace(resolve=lambda token, cookie: token.removeprefix("Bearer ") if token else None))
    for module in (http, ask_routes, ask_mcp):
        monkeypatch.setattr(module, "served", state)
    return state


@pytest.fixture
def client(served):
    app = FastAPI()
    app.include_router(ask_routes.router)
    app.dependency_overrides[http.principal] = principal_from
    with TestClient(app) as c:
        yield c


def principal_from(request: Request) -> str:
    return request.headers["who"]


def over_http(client, who):
    return client.get(f"/map/{TERM}", headers={"who": who})


def over_mcp(who):
    server = ask_mcp.mcp_server()
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={"authorization": f"Bearer {who}"})))
    return anyio.run(lambda: server._tool_manager.call_tool("map_term", {"term": TERM}, ctx, convert_result=True))


def both(client, who):
    r = over_http(client, who)
    assert r.status_code == 200, r.text
    result = over_mcp(who)
    assert not result.isError
    assert json.loads(result.content[0].text) == result.structuredContent == r.json()
    return r.json()


def test_coded_member_direct_and_alias_maps_follow_unit_access(served, monkeypatch):
    from kge.adapters.graphstore.members import MemberTerms
    from kge.services import graph as graphservice
    from kge.services.ask import service
    from kge.services.ask.engine import openitems

    engine = served.engine()
    graph = engine.g
    term, alias, column = 'Crown (territory)', 'crown territory', 'south.south_sales.territory_code'
    monkeypatch.setattr(graph, 'member_terms', lambda: MemberTerms({}, frozenset({term}), ((term, column, 'CR'),),
                                                                 ((alias, term),), ((term, column),)), raising=False)
    domain = graph.domain
    monkeypatch.setattr(graph, 'domain', lambda: {**domain(), 'aliases': {alias: term}, 'restricted': []})
    monkeypatch.setattr(graph, 'defining_docs', lambda _: [])
    monkeypatch.setattr(graphservice, 'term_paths', lambda *args, **kwargs: [])
    monkeypatch.setattr(openitems, 'readable_names', lambda *args: {})
    native = graph.q

    def read(query, params=None):
        if 't.definition AS definition' in query:
            if params.get('t') == term or params.get('names') == [term]:
                return [{'name': term, 'id': 'crown', 'definition': 'A governed territory.', 'steward': 'Steward'}]
            return []
        return native(query, params)

    monkeypatch.setattr(graph, 'q', read)
    owner, denied = PEOPLE['engineer'], PEOPLE['business']
    direct = service.term_map(engine, term, owner, compact=True)
    assert direct['definition'] == 'A governed territory.' and direct['data_sets'][0]['unit'] == 'south'
    direct = service.term_map(engine, term, denied, compact=True)
    assert not direct['definition'] and direct['data_sets'] == []
    alias_map = service.term_map(engine, alias, owner, compact=True)
    assert [meaning['id'] for meaning in alias_map['resolves_to']] == ['crown']
    assert 'resolves_to' not in service.term_map(engine, alias, denied, compact=True)

    from kge.services.ask.models import TermMap
    monkeypatch.setitem(READS, owner, {'north'})
    monkeypatch.setattr(graph, 'uncertified_mentions', lambda _: [])
    known = TermMap.model_validate(service.term_map(engine, term, owner)).model_dump(exclude_unset=True)
    unknown = TermMap.model_validate(service.term_map(engine, 'Unknown territory', owner)).model_dump(exclude_unset=True)
    assert {**known, 'term': unknown['term']} == unknown


@pytest.mark.parametrize("role", ["business", "steward"])
def test_a_non_engineers_map_is_compact_with_no_physical_column_join_or_no_join_fact(client, served, role):
    body = both(client, PEOPLE[role])
    assert body["compact"] is True
    assert not {"implementations", "no_join", "certified_joins", "uncertified_mentions"} & set(body), body
    text = json.dumps(body)
    assert not [name for name in PHYSICAL if name in text], text
    assert (body["term"], body["steward"], body["definition"]) == (TERM, "Sales Data Steward",
                                                                    "Sales less returns and discounts, in euros.")
    assert body["data_sets"] == [{"unit": "north", "unit_label": "North Region", "domain": "Sales",
                                  "community": "Sales performance", "business_owner": "North Sales Office",
                                  "currency": "EUR", "certified_by": "Sales Data Steward", "certified_on": "2026-01-15"}]
    assert body["contexts"] == [{"label": "North net sales", "measure": TERM, "currency": "EUR", "unit": "north",
                                 "unit_label": "North Region", "filters": ["Booked sales only"],
                                 "row_selectors": ["Test accounts left out"]}]
    assert [d["id"] for d in body["defined_by"]] == ["MD-NET-SALES"]
    path, = body["paths"]
    assert (path["term"], path["context"], path["unit"], path["fact"]) == (TERM, "North net sales", "North Region", "sale")
    assert [(h["source"], h["target"], h["cardinality"], h["type"], h["use"]) for h in path["hops"]] == [
        ("sale", "sales region", "one-to-one", "inner", "always")]
    assert [(c["kind"], c["label"]) for c in path["conditions"]] == [("filter", "Booked sales only"),
                                                                   ("exclusion", "Test accounts left out")]
    assert not {"condition", "predicate", "expression"} & ({k for h in path["hops"] for k in h} | set(path["measure"]))


def test_an_engineers_map_is_full_with_its_columns_joins_and_no_join_facts(client, served):
    body = both(client, PEOPLE["engineer"])
    assert body["compact"] is False
    assert [(i["unit"], i["tbl"], i["col"], i["readable"]) for i in body["implementations"]] == [
        ("north", "SALES_FCT_1", "NET_AMT", True), ("south", "south_sales", "net_amount", True)]
    assert [(c["fact"], c["expression"], [j["to"] for j in c["joins"]]) for c in body["contexts"]] == [
        ("north.SALES_FCT_1", "sum(SALES_FCT_1.NET_AMT)", ["north.REGION_DIM_1"]),
        ("south.south_sales", "sum(south_sales.NET_AMT)", ["south.REGION_DIM_1"])]
    assert [n["pair"] for n in body["no_join"]] == ["north-south"]
    assert [c["pair"] for c in body["certified_joins"]] == ["north-south"]
    assert body["uncertified_mentions"] and [d["id"] for d in body["defined_by"]] == ["MD-NET-SALES", "SOUTH-POLICY"]
    assert {d["unit"] for d in body["data_sets"]} == {"north", "south"}
    assert [(p["fact"], p["measure"]["expression"], [(h["target"], h["condition"]) for h in p["hops"]]) for p in body["paths"]] == [
        ("north.SALES_FCT_1", "sum(SALES_FCT_1.NET_AMT)", [("north.REGION_DIM_1", "REGION_ID = REGION_ID")]),
        ("south.south_sales", "sum(south_sales.NET_AMT)", [("south.REGION_DIM_1", "REGION_ID = REGION_ID")])]


@pytest.mark.parametrize("role", ["business", "steward"])
def test_a_unit_the_caller_may_not_read_is_absent(client, served, role):
    text = json.dumps(both(client, PEOPLE[role]))
    assert not [word for word in UNREADABLE if word in text], text


def test_an_admin_alone_is_refused_the_map_on_both_transports_and_audited(client, served):
    r = over_http(client, PEOPLE["admin"])
    assert r.status_code == 403, r.text
    result = anyio.run(lambda: _refused(PEOPLE["admin"]))
    assert result.startswith("403:")
    entries = role_audit_log(served.PROFILE.run_dir(), served.PROFILE.client).newest(10)
    assert {(e["row"], e["endpoint"], e["outcome"]) for e in entries} == {
        ("ask", "GET /map/{term:path}", "refused"), ("ask", "mcp map_term", "refused")}


async def _refused(who):
    from mcp.server.fastmcp.exceptions import ToolError
    server = ask_mcp.mcp_server()
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={"authorization": f"Bearer {who}"})))
    try:
        await server._tool_manager.call_tool("map_term", {"term": TERM}, ctx, convert_result=True)
    except ToolError as exc:
        return str(exc)
    raise AssertionError("the map was not refused")


def test_every_role_that_may_ask_is_audited_on_the_ask_row(client, served):
    for role in ("business", "steward", "engineer"):
        both(client, PEOPLE[role])
    entries = role_audit_log(served.PROFILE.run_dir(), served.PROFILE.client).newest(100)
    assert {(e["row"], e["outcome"]) for e in entries} == {("ask", "allowed")}
    assert {e["endpoint"] for e in entries} == {"GET /map/{term:path}", "mcp map_term"}
