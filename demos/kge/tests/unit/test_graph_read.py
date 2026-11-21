"""The graph read (cerebro.md §5.1, §5.2): filtered to the units the person may read before it is paged, each link
certified, proposed or inferred with its reason, cursor-paged, no figures, and a fixed number of store queries."""
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.adapters import graphstore
from kge.adapters.graphstore.gql import Namespace, Schema, Translate
from kge.graph.schema import meta_model
from kge.pipeline.overlay import text_sha256
from kge.services import http
from kge.services.ask.engine.graphview import Catalogue, Graph, Products, Rights
from kge.services.graph import mcp as graph_mcp, routes, service

DDL = ("CREATE NODE TABLE Col(id STRING, PRIMARY KEY(id))",
       "CREATE NODE TABLE Term(name STRING, PRIMARY KEY(name))",
       "CREATE NODE TABLE Document(id STRING, unit STRING, PRIMARY KEY(id))",
       "CREATE NODE TABLE Chunk(id STRING, text STRING, readiness STRING, PRIMARY KEY(id))",
       "CREATE NODE TABLE Tag(id STRING, family STRING, value STRING, generator STRING, evidence STRING, "
       "confidence DOUBLE, PRIMARY KEY(id))",
       "CREATE REL TABLE TAGGED(FROM Chunk TO Tag)",
       "CREATE REL TABLE SUPERSEDES(FROM Tag TO Tag)",
       "CREATE REL TABLE MAPS_TO(FROM Col TO Term, certified BOOLEAN, confidence DOUBLE)",
       "CREATE REL TABLE DEFINES(FROM Document TO Term)",
       "CREATE REL TABLE HAS_CHUNK(FROM Document TO Chunk)",
       "CREATE REL TABLE MENTIONS(FROM Chunk TO Term, confidence DOUBLE, generator STRING)")
TERMS = ("Revenue", "Customer", "Salary", "Churn", "Glossary entry", "Customer ID", "Id")
MAPPINGS = (("retail.orders.revenue", "Revenue", True), ("retail.customers.id", "Customer", False),
            ("private.salaries.amount", "Salary", True), ("retail.orders.id", "Id", False))
PRODUCTS = ({"id": "retail.orders", "unit": "retail", "domain": "Sales", "community": "Retail circle",
             "product_owner": "Order Desk", "business_owner": "", "steward": "Ann Steward"},
            {"id": "retail.customers", "unit": "retail", "domain": "", "community": "Retail circle",
             "product_owner": "", "business_owner": "Client Office", "steward": ""},
            {"id": "private.salaries", "unit": "private", "domain": "People", "community": "Payroll circle",
             "product_owner": "Pay Office", "business_owner": "Hidden Board", "steward": "Ann Steward"})
DOCUMENTS = (("D1", "retail", "Revenue policy"), ("D2", "private", "Pay bands"), ("D3", "", "Unscoped note"))
DEFINES = (("D1", "Revenue"), ("D2", "Churn"))
MENTIONS = (("D1", "C1", "Customer", "rules"), ("D1", "C1", "Revenue", "embedding_steward_accepted"),
            ("D2", "C2", "Salary", "rules"), ("D3", "C3", "Revenue", "rules"))
SUGGESTIONS = (("C1", "T1", "Glossary entry", None), ("C1", "T2", "Revenue", None), ("C1", "T3", "Customer", "T4"),
               ("C2", "T5", "Churn", None))
PRIVATE = ("private", "salaries", "amount", "secret_col", "Pay bands", "D2", "Unscoped note", "D3", "People",
           "Payroll circle", "Pay Office", "Hidden Board", "pay_order_rule", "via_pay_rule", "vault", "pairings")
THROUGH = ("vault", "pairings", "pay_id")


def catalogue(identity, extra_tables=0):
    tables = {"retail": frozenset({"orders", "customers", *(f"t{i}" for i in range(extra_tables))}),
              "private": frozenset({"salaries"}), "vault": frozenset({"pairings"})}
    columns = {"retail.orders": ("id", "revenue", "customer_id"), "retail.customers": ("id",),
               "private.salaries": ("amount", "secret_col"), "vault.pairings": ("customer_id", "pay_id"), **{f"retail.t{i}": ("c",) for i in range(extra_tables)}}
    joins = {"retail": ({"ft": "orders", "fc": "customer_id", "tt": "customers", "tc": "id"},),
             "private": ({"ft": "salaries", "fc": "amount", "tt": "salaries", "tc": "amount"},)}
    rules = ({"id": "order_customer_rule", "kind": "many_to_one", "through": None, "ft": "retail.customers",
              "tt": "retail.orders"},
             {"id": "order_self_rule", "kind": "self", "through": None, "ft": "retail.orders", "tt": "retail.orders"},
             {"id": "pay_order_rule", "kind": "many_to_one", "through": None, "ft": "private.salaries",
              "tt": "retail.orders"},
             {"id": "via_pay_rule", "kind": "cross_reference", "through": "vault.pairings", "ft": "retail.customers",
              "tt": "private.salaries"})
    documents = tuple({"id": d, "unit": u, "title": t, "kind": "policy", "owner": "", "version": "1",
                       "published_at": "", "shelf": "curated"} for d, u, t in DOCUMENTS)
    return Catalogue(release=identity, tables=tables, joins=joins, columns=columns, landed=None,
                     figures={"retail.orders": {"rows": 1234, "bytes": 5678}}, documents=documents, rules=rules)


@pytest.fixture
def graph(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    store = graphstore.store()
    db = store.open(tmp_path / "graph.ladybug")
    with store.connect(db) as conn:
        for ddl in DDL:
            conn.execute(ddl).close()
        for col, _, _ in MAPPINGS:
            conn.execute("CREATE (:Col {id: $c})", {"c": col}).close()
        for term in TERMS:
            conn.execute("CREATE (:Term {name: $t})", {"t": term}).close()
        for col, term, certified in MAPPINGS:
            conn.execute("MATCH (c:Col), (t:Term) WHERE c.id = $c AND t.name = $t "
                         "CREATE (c)-[:MAPS_TO {certified: $k, confidence: 0.75}]->(t)",
                         {"c": col, "t": term, "k": certified}).close()
        for doc, unit, _ in DOCUMENTS:
            conn.execute("CREATE (:Document {id: $d, unit: $u})", {"d": doc, "u": unit}).close()
        for doc, term in DEFINES:
            conn.execute("MATCH (d:Document), (t:Term) WHERE d.id = $d AND t.name = $t CREATE (d)-[:DEFINES]->(t)",
                         {"d": doc, "t": term}).close()
        for doc, chunk, term, generator in MENTIONS:
            conn.execute("MERGE (c:Chunk {id: $c}) SET c.text = $x, c.readiness = 'citable'",
                         {"c": chunk, "x": f"passage {chunk}"}).close()
            conn.execute("MATCH (d:Document), (c:Chunk) WHERE d.id = $d AND c.id = $c MERGE (d)-[:HAS_CHUNK]->(c)",
                         {"d": doc, "c": chunk}).close()
            conn.execute("MATCH (c:Chunk), (t:Term) WHERE c.id = $c AND t.name = $t "
                         "CREATE (c)-[:MENTIONS {confidence: 0.81, generator: $g}]->(t)",
                         {"c": chunk, "t": term, "g": generator}).close()
        for chunk, tag, term, superseded_by in SUGGESTIONS:
            conn.execute("MATCH (c:Chunk) WHERE c.id = $c CREATE (c)-[:TAGGED]->(:Tag {id: $t, family: 'suggested_mention', "
                         "value: $v, generator: 'embedding', evidence: $e, confidence: 0.66})",
                         {"c": chunk, "t": tag, "v": term, "e": text_sha256(f"passage {chunk}")}).close()
            if superseded_by:
                conn.execute("MATCH (t:Tag) WHERE t.id = $t CREATE (:Tag {id: $h, family: 'suggested_mention', "
                             "generator: 'human'})-[:SUPERSEDES]->(t)", {"t": tag, "h": superseded_by}).close()
    g = object.__new__(Graph)
    g.db = db
    g.identity = {"hash": "current"}
    g.writes = 0
    g._snapshot_lock = threading.RLock()
    g._term_reads = {}
    g._catalogue = catalogue(g.identity)
    g._products = Products(g.identity, PRODUCTS)
    g._rights = Rights(g.identity, {"biz": "Business", "boss": "Everything", "nobody": "Nobody"},
                       {"biz": frozenset({"retail"}), "boss": frozenset({"retail", "private"})},
                       {"biz": frozenset({"querying"}), "boss": frozenset({"querying"})}, frozenset())
    g._role_templates = {"biz": {}, "boss": {}, "nobody": {}}
    g._roles_only = False
    g.people = SimpleNamespace(roles_of=lambda who: {who})
    yield g
    db.close()


@pytest.fixture
def client(graph, monkeypatch, tmp_path):
    engine = SimpleNamespace(g=graph)
    fake = SimpleNamespace(engine=lambda: engine, PROFILE=SimpleNamespace(run_dir=lambda: tmp_path, client="test"))
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(http, "cerebro_held", lambda who, state=None: ["business"])
    monkeypatch.setattr(routes, "served", fake)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[http.principal] = principal_from_header
    with TestClient(app) as c:
        yield c


def ask(client, question):
    return client.post("/graph/explore", json={"question": question})


def principal_from_header(request: Request) -> str:
    return request.headers.get("who", "biz")


def whole(graph, who):
    return service.read(SimpleNamespace(g=graph), who, limit=service.MAX_LIMIT)


def every_page(client, who, limit, **params):
    pages, cursor = [], None
    while True:
        query = {"limit": limit, **params, **({"cursor": cursor} if cursor else {})}
        response = client.get("/graph", params=query, headers={"who": who})
        assert response.status_code == 200, response.text
        pages.append(response.json())
        cursor = response.json().get("cursor")
        if cursor is None:
            return pages


def numbers(value):
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in numbers(v)]
    return []


def test_a_unit_the_person_may_not_read_is_absent_with_no_trace(client):
    pages = every_page(client, "biz", 2)
    text = json.dumps(pages)
    for secret in PRIVATE:
        assert secret not in text, secret
    ids = {n["id"] for page in pages for n in page["nodes"]}
    assert {"table:retail.orders", "column:retail.orders.revenue", "document:D1", "term:Revenue", "term:Customer",
            "term:Glossary entry", "term:Salary", "term:Churn"} <= ids
    ends = {end for page in pages for link in page["links"] for end in (link["source"], link["target"])}
    assert not ends & {"term:Salary", "term:Churn"}


def test_one_who_reads_the_unit_sees_it(graph):
    page = whole(graph, "boss")
    ids = {n.id for n in page.nodes}
    assert {"table:private.salaries", "column:private.salaries.secret_col", "document:D2", "term:Salary",
            "term:Churn"} <= ids
    assert "document:D3" not in ids


def test_each_link_has_one_status_and_a_reason(graph):
    links = {(l.source, l.kind, l.target): l for l in whole(graph, "biz").links}
    assert links[("column:retail.orders.revenue", "maps_to", "term:Revenue")].status == "certified"
    assert links[("column:retail.customers.id", "maps_to", "term:Customer")].status == "proposed"
    assert links[("document:D1", "mentions", "term:Customer")].status == "inferred"
    assert links[("document:D1", "mentions", "term:Revenue")].status == "certified"
    assert links[("document:D1", "defines", "term:Revenue")].status == "certified"
    assert links[("table:retail.orders", "has_column", "column:retail.orders.revenue")].status == "certified"
    assert links[("table:retail.orders", "joins", "table:retail.customers")].status == "certified"
    assert links[("document:D1", "mentions", "term:Glossary entry")].status == "proposed"
    assert all(l.why for l in links.values())
    boss = {(l.source, l.kind, l.target): l for l in whole(graph, "boss").links}
    assert boss[("document:D2", "mentions", "term:Churn")].status == "proposed"
    assert boss[("document:D2", "mentions", "term:Salary")].status == "inferred"


def test_pages_together_are_the_whole_read_each_link_once(client, graph):
    pages = every_page(client, "boss", 3)
    assert len(pages) > 2
    assert all(len(page["nodes"]) <= 3 for page in pages)
    assert "cursor" not in pages[-1]
    nodes = [n for page in pages for n in page["nodes"]]
    links = [tuple(sorted(l.items())) for page in pages for l in page["links"]]
    full = whole(graph, "boss")
    assert nodes == [n.model_dump() for n in full.nodes]
    assert len(links) == len(set(links))
    assert set(links) == {tuple(sorted(l.model_dump().items())) for l in full.links}


def test_no_figures_in_any_page(client):
    for page in every_page(client, "boss", 4):
        assert numbers({k: v for k, v in page.items()}) == []
        assert "1234" not in json.dumps(page) and "0.81" not in json.dumps(page) and "0.75" not in json.dumps(page)


def test_a_cursor_this_service_did_not_give_is_a_400(client):
    for cursor in ("not-base64!!", "eyJ4IjogMX0", "bnVsbA"):
        response = client.get("/graph", params={"cursor": cursor})
        assert response.status_code == 400, cursor


def test_neighbourhood_of_a_term_and_a_node_the_person_may_not_see(client):
    page = client.get("/graph", params={"node": "term:Revenue"}).json()
    ids = {n["id"] for n in page["nodes"]}
    assert ids == {"term:Revenue", "column:retail.orders.revenue", "document:D1"}
    assert client.get("/graph", params={"node": "term:Salary"}).json()["nodes"] == [{"kind": "term", "id": "term:Salary",
                                                                                    "name": "Salary"}]
    assert all("term:Revenue" in (l["source"], l["target"]) for l in page["links"])
    hidden = client.get("/graph", params={"node": "table:private.salaries"})
    missing = client.get("/graph", params={"node": "table:retail.nowhere"})
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_one_without_querying_is_refused(client):
    assert client.get("/graph", headers={"who": "nobody"}).status_code == 403


def test_a_read_sends_a_fixed_number_of_store_queries_and_a_repeat_none(graph, monkeypatch):
    sent = []
    real = Graph.reader

    def counted(self):
        sent.append(1)
        return real(self)
    monkeypatch.setattr(Graph, "reader", counted)
    whole(graph, "boss")
    first = len(sent)
    assert first <= 5
    graph._term_reads.clear()
    graph._passage_links = None
    sent.clear()
    graph._catalogue = catalogue(graph.identity, extra_tables=50)
    page = whole(graph, "boss")
    assert len(sent) == first
    assert len([n for n in page.nodes if n.kind == "table"]) == 53
    sent.clear()
    whole(graph, "biz")
    assert sent == []
    graph.writes += 1
    whole(graph, "biz")
    assert len(sent) == 2


def test_the_reads_run_on_every_graph_store(graph, monkeypatch):
    sent = []
    real = Graph.q

    def recorded(self, cypher, params=None):
        sent.append(cypher)
        return real(self, cypher, params)
    monkeypatch.setattr(Graph, "q", recorded)
    whole(graph, "boss")
    translate = Translate(Schema.from_meta(meta_model(), 128), Namespace("proj", "kge_graph", "g_test"))
    assert len(set(sent)) >= 5
    for cypher in set(sent):
        assert "GRAPH_TABLE" in translate(cypher).sql, cypher


def test_domains_communities_and_people_come_from_readable_products_proposed(graph):
    page = whole(graph, "biz")
    ids = {n.id: n.kind for n in page.nodes}
    assert ids["domain:Sales"] == "domain" and ids["community:Retail circle"] == "community"
    assert ids["person:Order Desk"] == ids["person:Ann Steward"] == ids["person:Client Office"] == "person"
    links = {(l.source, l.kind, l.target): l for l in page.links}
    synced = [links[("table:retail.orders", "in_domain", "domain:Sales")],
              links[("domain:Sales", "in_community", "community:Retail circle")],
              links[("table:retail.customers", "in_community", "community:Retail circle")],
              links[("person:Order Desk", "owns", "table:retail.orders")],
              links[("person:Client Office", "owns", "table:retail.customers")],
              links[("person:Ann Steward", "stewards", "table:retail.orders")]]
    assert all(l.status == "proposed" and "portal syncs" in l.why for l in synced)
    assert ("person:Ann Steward", "stewards", "table:private.salaries") not in links
    boss = {n.id for n in whole(graph, "boss").nodes}
    assert {"domain:People", "community:Payroll circle", "person:Pay Office", "person:Hidden Board"} <= boss


def test_a_column_named_as_a_term_has_an_inferred_name_match_never_over_a_mapping(graph):
    links = {(l.source, l.kind, l.target): l for l in whole(graph, "biz").links}
    match = links[("column:retail.orders.customer_id", "name_match", "term:Customer ID")]
    assert match.status == "inferred" and "name match only, not a verified" in match.why
    assert links[("column:retail.customers.id", "name_match", "term:Id")].status == "inferred"
    assert links[("column:retail.orders.id", "maps_to", "term:Id")].status == "proposed"
    assert ("column:retail.orders.id", "name_match", "term:Id") not in links
    assert ("column:retail.orders.revenue", "name_match", "term:Revenue") not in links


def test_the_pack_s_join_rules_are_certified_joins_once_per_pair_and_only_where_both_ends_are_readable(graph):
    """A join rule is a joins link naming the rule; a pair a declared key joins keeps the key's link alone, either way
    round; a rule with an end in a unit the person may not read is absent."""
    joins = [link for link in whole(graph, "biz").links if link.kind == "joins"]
    pairs = [frozenset((link.source, link.target)) for link in joins]
    assert len(pairs) == len(set(pairs))
    keyed = next(link for link in joins if {link.source, link.target} == {"table:retail.orders", "table:retail.customers"})
    assert "declared key" in keyed.why
    self_join = next(link for link in joins if link.source == link.target == "table:retail.orders")
    assert self_join.status == "certified" and "order_self_rule" in self_join.why
    assert not any("pay_order_rule" in link.why or "via_pay_rule" in link.why for link in joins)
    boss = [link for link in whole(graph, "boss").links if link.kind == "joins"]
    assert any("pay_order_rule" in link.why for link in boss)



def test_a_cross_reference_through_a_table_no_one_reads_is_one_certified_joins_link_where_both_ends_are_read(client, graph):
    """A cross-reference joins two units through a curated source's table no person reads: one who reads both ends sees
    one certified joins link naming the rule, and the through table is no node, no link's end, and named nowhere in
    the read, its pages or the neighbourhood of either end."""
    view = whole(graph, "boss")
    ends = {"table:retail.customers", "table:private.salaries"}
    held = [link for link in view.links if link.kind == "joins" and {link.source, link.target} == ends]
    assert len(held) == 1, held
    assert held[0].status == "certified" and "via_pay_rule" in held[0].why
    assert not any(node.id.startswith(("table:vault.", "column:vault.")) for node in view.nodes)
    assert not any(end.startswith(("table:vault.", "column:vault.")) for link in view.links for end in (link.source, link.target))
    pages = every_page(client, "boss", 3)
    pages += [client.get("/graph", params={"node": end}, headers={"who": "boss"}).json() for end in sorted(ends)]
    text = json.dumps(pages)
    for name in THROUGH:
        assert name not in text, name
    assert all({link["source"], link["target"]} != ends or "via_pay_rule" in link["why"]
               for page in pages for link in page["links"])


def test_a_cross_reference_with_an_end_the_person_may_not_read_is_absent_with_no_trace(client, graph):
    """One who reads one end of a cross-reference alone sees no link of it and nothing of its rule, its other end or the
    table it joins through, on any page or in the neighbourhood of the end it reads."""
    view = whole(graph, "biz")
    assert not any("via_pay_rule" in link.why for link in view.links)
    assert "table:private.salaries" not in {node.id for node in view.nodes}
    pages = every_page(client, "biz", 3)
    pages.append(client.get("/graph", params={"node": "table:retail.customers"}, headers={"who": "biz"}).json())
    text = json.dumps(pages)
    for name in ("via_pay_rule", "private", "salaries", *THROUGH):
        assert name not in text, name

def test_explore_gives_a_summary_without_figures_and_an_encoded_deep_link(client):
    body = client.get("/graph/explore", params={"node": "term:Revenue"}).json()
    assert body["node"] == {"kind": "term", "id": "term:Revenue", "name": "Revenue"}
    assert body["link"] == "/cerebro?mode=graph&node=term%3ARevenue"
    assert "Certified links to" in body["summary"] and "revenue (column)" in body["summary"]
    assert not any(ch.isdigit() for ch in body["summary"])
    spaced = client.get("/graph/explore", params={"node": "community:Retail circle"}).json()
    assert spaced["link"] == "/cerebro?mode=graph&node=community%3ARetail%20circle"
    assert "Proposed links to" in spaced["summary"]
    hidden = client.get("/graph/explore", params={"node": "table:private.salaries"})
    missing = client.get("/graph/explore", params={"node": "table:retail.nowhere"})
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()
    assert client.get("/graph/explore", params={"node": "term:Revenue"}, headers={"who": "nobody"}).status_code == 403


def test_explore_a_question_s_neighbourhood_names_what_it_matches_and_nothing_private(client):
    body = ask(client, "What was REVENUE by orders in Sales?").json()
    assert body["node"] == {"kind": "term", "id": "term:Revenue", "name": "Revenue"}
    assert body["link"] == "/cerebro?mode=graph&node=term%3ARevenue"
    assert body["summary"].startswith("The question names Revenue (term), Sales (domain), orders (table), revenue (column).")
    assert "Certified links to" in body["summary"] and "Revenue policy (document)" in body["summary"]
    assert "REVENUE" not in body["summary"] and not any(ch.isdigit() for ch in body["summary"])
    private = ask(client, "salaries amount secret_col in People and Pay bands").json()
    assert private == {"summary": "No node you may read matches the question.", "link": "/cerebro?mode=graph"}
    assert not any(word in json.dumps(private) for word in PRIVATE)
    assert ask(client, "Churn").json()["node"]["id"] == "term:Churn"
    assert "Pay bands" not in ask(client, "Churn").json()["summary"]
    for body in ({}, {"question": ""}, {"question": "Revenue", "node": "term:Revenue"}):
        assert client.post("/graph/explore", json=body).status_code == 422
    assert client.post("/graph/explore", json={"question": "Revenue"}, headers={"who": "nobody"}).status_code == 403


def test_a_question_goes_in_the_body_so_no_url_or_access_log_carries_it(client, caplog):
    """GET takes a node alone; the question form is POST, its text in the body, so the request line uvicorn's access log
    writes (method, path and query string) never holds it."""
    seen = []
    app = client.app

    async def spy(scope, receive, send):
        if scope["type"] == "http":
            seen.append(f'{scope["method"]} {scope["path"]}?{scope["query_string"].decode()}')
        await app(scope, receive, send)
    with TestClient(spy, headers=client.headers) as spied, caplog.at_level("DEBUG"):
        answered = spied.post("/graph/explore", json={"question": "Revenue secretword"})
    assert answered.status_code == 200 and answered.json()["node"]["id"] == "term:Revenue"
    assert seen == ["POST /graph/explore?"]
    assert "secretword" not in caplog.text
    refused = client.get("/graph/explore", params={"question": "Revenue"})
    assert refused.status_code == 422 and refused.json()["detail"][0]["loc"] == ["query", "node"]
    spec = client.app.openapi()["paths"]["/graph/explore"]
    assert [p["name"] for p in spec["get"]["parameters"]] == ["node"]
    assert "parameters" not in spec["post"] and "requestBody" in spec["post"]


def test_the_mcp_tools_give_the_route_s_page_and_errors(client, monkeypatch):
    import anyio
    from mcp.server.fastmcp.exceptions import ToolError

    async def invoke(ctx, call, check):
        try:
            return call(check(ctx))
        except routes.HTTPException as exc:
            raise ToolError(f"{exc.status_code}: {exc.detail}") from None

    server = graph_mcp.mcp_server(invoke)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert set(tools) == {"read_graph", "explore", "explore_question"}
    assert tools["explore"].meta["route"] == "GET /graph/explore"
    assert tools["explore_question"].meta["route"] == "POST /graph/explore"
    assert all(tool.meta["tags"] == ["external: knowledge"] for tool in tools.values())

    async def run(name, **arguments):
        return await tools[name].fn(ctx="biz", **arguments)
    page = anyio.run(lambda: run("read_graph", node="term:Revenue"))
    assert page.model_dump(exclude_unset=True) == client.get("/graph", params={"node": "term:Revenue"}).json()
    explored = anyio.run(lambda: run("explore", node="term:Revenue"))
    assert explored.model_dump() == client.get("/graph/explore", params={"node": "term:Revenue"}).json()
    asked = anyio.run(lambda: run("explore_question", question="Revenue"))
    assert asked.model_dump(exclude_unset=True) == ask(client, "Revenue").json()
    with pytest.raises(ToolError, match="^404: No such node.$"):
        anyio.run(lambda: run("explore", node="table:private.salaries"))
    with pytest.raises(ToolError, match="^400: "):
        anyio.run(lambda: run("read_graph", cursor="bnVsbA"))
    with pytest.raises(ToolError, match="^403: "):
        anyio.run(lambda: tools["read_graph"].fn(ctx="nobody"))
