"""The graph tools explain a question's whole path from what the graph holds (#628), on a synthetic pack: a calendar
roll-up with no declared join options, a key join declared left, a hierarchy with codes, a one-to-many join with a
validity window, a source-system cross-reference, a reach into a unit no one here reads, a filter, an exclusion, a
condition the question states, SCD2 and release policies, and a derived measure. Business words for every role,
physical names for an engineer; a unit the person may not read is absent; no figure; no model call, no warehouse query."""
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.adapters import graphstore
from kge.adapters.graphstore.gql import Namespace, Schema, Translate
from kge.graph.schema import meta_model
from kge.semantics.query import Context, Join, Rule
from kge.semantics.vocabulary import compiled as compiled_vocabulary
from kge.services import http
from kge.services.ask.engine.graphview import Catalogue, Graph, Products, Rights
from kge.services.graph import mcp as graph_mcp, routes, service

DDL = ("CREATE NODE TABLE Col(id STRING, PRIMARY KEY(id))",
       "CREATE NODE TABLE Term(name STRING, id STRING, definition STRING, PRIMARY KEY(name))",
       "CREATE NODE TABLE Document(id STRING, unit STRING, PRIMARY KEY(id))",
       "CREATE NODE TABLE Chunk(id STRING, text STRING, readiness STRING, PRIMARY KEY(id))",
       "CREATE NODE TABLE Tag(id STRING, family STRING, value STRING, generator STRING, evidence STRING, "
       "confidence DOUBLE, PRIMARY KEY(id))",
       "CREATE REL TABLE TAGGED(FROM Chunk TO Tag)",
       "CREATE REL TABLE SUPERSEDES(FROM Tag TO Tag)",
       "CREATE REL TABLE MAPS_TO(FROM Col TO Term, certified BOOLEAN, confidence DOUBLE)",
       "CREATE REL TABLE DEFINES(FROM Document TO Term)",
       "CREATE REL TABLE HAS_CHUNK(FROM Document TO Chunk)",
       "CREATE REL TABLE MENTIONS(FROM Chunk TO Term, confidence DOUBLE, generator STRING)",
       "CREATE REL TABLE BROADER(FROM Term TO Term)",
       "CREATE REL TABLE IS_A(FROM Term TO Term)",
       "CREATE REL TABLE CODED_AS(FROM Term TO Col, code STRING)")
TERMS = (("Revenue", "revenue", "Money taken at the till, before refunds."), ("Revenue Share", "revenue-share", "A share of revenue."),
         ("Sales Region", "sales-region", "A region stores sell in."), ("North", "north", "The northern stores."),
         ("East", "east", "The eastern stores."), ("Country", "country", "The whole country."),
         ("Ledger Code", "ledger-code", "A vault code."))
COLUMNS = ("shop.REGION.RGN_CD", "vault.SECRET.CODE", "shop.SALES.AMT")
IS_A = (("North", "Sales Region"), ("East", "Sales Region"))
BROADER = (("North", "Country"), ("East", "Country"))
CODED_AS = (("North", "shop.REGION.RGN_CD", "N"), ("East", "shop.REGION.RGN_CD", "E"), ("Sales Region", "vault.SECRET.CODE", "SR"),
            ("Ledger Code", "vault.SECRET.CODE", "LC"))
WINDOW = {"date": "shop.SALES.SOLD_ON", "effective": "shop.ITEM.EFF_DT", "expiry": "shop.ITEM.EXP_DT"}
JOINS = (Join("sales_day", "calendar_rollup", "SALES.DAY_ID = DAY.DAY_ID", None, None, "shop.SALES", "shop.DAY", True),
         Join("sales_store", "key", "SALES.STORE_ID = STORE.STORE_ID", None, None, "shop.SALES", "shop.STORE", False,
              {"join": "sales_store", "cardinality": "one-to-one", "type": "left"}),
         Join("store_region", "hierarchy", "STORE.RGN_CD = REGION.RGN_CD", None, None, "shop.STORE", "shop.REGION", False,
              {"join": "store_region", "cardinality": "one-to-one"}),
         Join("sales_item", "key", "SALES.ITEM_ID = ITEM.ITEM_ID", None, None, "shop.SALES", "shop.ITEM", False,
              {"join": "sales_item", "cardinality": "one-to-many", "type": "left", "validity_window": WINDOW}),
         Join("sales_secret", "key", "SALES.SECRET_ID = SECRET.SECRET_ID", None, None, "shop.SALES", "vault.SECRET", False),
         Join("store_client", "cross_reference", "STORE.STORE_ID = MATCH.STORE_ID AND MATCH.CLIENT_ID = CLIENT.CLIENT_ID", None,
              "xref.MATCH", "shop.STORE", "bank.CLIENT", False))
CONTEXT = Context("sales", "Store sales", "Sales at the till.", "Revenue", "shop.revenue", "sum(SALES.AMT)", "sum", "day",
                  "shop.SALES", ("shop.SALES.AMT",),
                  (Rule("valid", "Valid sales only", "SALES.OK_IN = 1", "shop.SALES"),
                   Rule("vault_cleared", "Vault cleared", "SECRET.CLEARED_IN = 1", "vault.SECRET")),
                  (Rule("not_test", "Not a test store", "STORE.STORE_ID = SALES.STORE_ID AND STORE.TEST_IN = 1", "shop.STORE", "exclude"),
                   Rule("vault_only", "Vaulted sales", "SECRET.SECRET_ID = SALES.SECRET_ID", "vault.SECRET", "include")),
                  JOINS, version_dates={"shop.ITEM": "shop.SALES.SOLD_ON"})
VOCABULARY = [{"kind": "predicate", "params": {"phrases": ["North region", "the north"], "column": "shop.REGION.RGN_CD", "op": "eq",
                                               "value": "N"}},
              {"kind": "derived_measure", "params": {"id": "revenue-share", "term": "revenue-share", "label": "revenue share of total",
                                                     "formula": {"kind": "share", "numerator": "shop.revenue",
                                                                 "denominator": "shop.revenue"},
                                                     "contexts": {"numerator": "sales", "denominator": "sales"}}}]
PHYSICAL = ("SALES", "STORE_ID", "shop.STORE", "RGN_CD", "ITEM.", "SOLD_ON", "EFF_DT", "OK_IN", "sum(", "xref", "MATCH")
SECRET = ("vault", "SECRET", "Ledger", "LC", "SR", "sales_secret", "vault_only", "Vaulted", "Vault cleared")
QUESTION = "Revenue for the North region by region"


def ddl_graph(tmp_path):
    store = graphstore.store()
    db = store.open(tmp_path / "graph.ladybug")
    with store.connect(db) as conn:
        for ddl in DDL:
            conn.execute(ddl).close()
        for col in COLUMNS:
            conn.execute("CREATE (:Col {id: $c})", {"c": col}).close()
        for name, slug, definition in TERMS:
            conn.execute("CREATE (:Term {name: $n, id: $i, definition: $d})", {"n": name, "i": slug, "d": definition}).close()
        for rel, pairs in (("IS_A", IS_A), ("BROADER", BROADER)):
            for a, b in pairs:
                conn.execute(f"MATCH (a:Term), (b:Term) WHERE a.name = $a AND b.name = $b CREATE (a)-[:{rel}]->(b)",
                             {"a": a, "b": b}).close()
        for term, col, code in CODED_AS:
            conn.execute("MATCH (t:Term), (c:Col) WHERE t.name = $t AND c.id = $c CREATE (t)-[:CODED_AS {code: $k}]->(c)",
                         {"t": term, "c": col, "k": code}).close()
    return db


@pytest.fixture
def graph(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    db = ddl_graph(tmp_path)
    g = object.__new__(Graph)
    g.db = db
    g.identity = {"hash": "synthetic"}
    g.writes = 0
    g._snapshot_lock = threading.RLock()
    g._term_reads = {}
    tables = {"shop": frozenset({"SALES", "DAY", "STORE", "REGION", "ITEM"}), "bank": frozenset({"CLIENT"}),
              "vault": frozenset({"SECRET"})}
    g._catalogue = Catalogue(release=g.identity, tables=tables, joins={}, columns={"shop.REGION": ("RGN_CD",)}, landed=None,
                             figures={}, documents=(), rules=())
    g._products = Products(g.identity, ())
    g._rights = Rights(g.identity, {"biz": "Business", "shoponly": "Shop", "boss": "Everything"},
                       {"biz": frozenset({"shop", "bank"}), "shoponly": frozenset({"shop"}),
                        "boss": frozenset({"shop", "bank", "vault"})},
                       {who: frozenset({"querying"}) for who in ("biz", "shoponly", "boss")}, frozenset())
    g._role_templates = {"biz": {}, "shoponly": {}, "boss": {}}
    g._roles_only = False
    g.people = SimpleNamespace(roles_of=lambda who: {who})
    yield g
    db.close()


def synthetic(g):
    """The engine of a synthetic pack: its query-semantics layer and the domain the rules parser reads."""
    words = compiled_vocabulary(VOCABULARY)
    sem = SimpleNamespace(
        contexts={"sales": CONTEXT}, declared={j.id: j for j in JOINS},
        scd2={"shop.STORE": {"id": "scd2-store", "table": "shop.STORE", "valid_from": "EFF_DT", "valid_to": "EXP_DT",
                             "is_current": "CURR_IN"}},
        releases={"shop.DAY": {"id": "day-release", "table": "shop.DAY", "period": "DAY_DT", "released": "RLSD_IN",
                               "release_date": "RLSE_DT", "freeze_date": "FRZ_DT"}},
        entities={"shop_sale": {"label": "sale", "table": "shop.SALES", "key": ["SALE_ID"]},
                  "shop_day": {"label": "trading_day", "table": "shop.DAY", "key": ["DAY_ID"]},
                  "shop_store": {"label": "store", "table": "shop.STORE", "key": ["STORE_ID"]},
                  "shop_region": {"label": "sales region", "table": "shop.REGION", "key": ["RGN_CD"]},
                  "shop_item": {"label": "item", "table": "shop.ITEM", "key": ["ITEM_ID"]},
                  "bank_client": {"label": "Bank client", "table": "bank.CLIENT", "key": ["CLIENT_ID"]},
                  "vault_secret": {"label": "secret", "table": "vault.SECRET", "key": ["SECRET_ID"]}},
        measure_declarations={"shop.revenue": {"measure": "shop.revenue", "col": "shop.SALES.AMT", "aggregation": "sum",
                                               "grain": "day", "currency": "USD", "snapshot": False}})
    return SimpleNamespace(
        g=g, semantics=sem, term_names=[t for t, _, _ in TERMS], ALIASES={"takings": "Revenue", "revenue share": "Revenue Share"},
        UNIT_WORDS={}, UNIT_LABEL={"shop": "Shops", "bank": "Banking", "vault": "Vault"},
        BUSINESS={"SALES.AMT": "sale amount", "SALES.SOLD_ON": "sale date", "REGION.RGN_CD": "region code"},
        PERIODS={"shop.SALES": {"column": "SOLD_ON", "kind": "event_date", "calendar": "fiscal_april"}},
        GROUP_BY={"by region": "shop.REGION.RGN_CD"}, VOCABULARY=VOCABULARY, FILTERS=words.filters,
        FILTER_COLUMNS=words.columns, INTENTS=words.intents)


def explain(g, who="biz", engineer=False, question=QUESTION):
    return service.explore(synthetic(g), who, question=question, engineer=engineer)


def test_explore_question_gives_the_whole_path_in_order(graph):
    out = explain(graph)
    path, = out.paths
    assert (path.term, path.basis, path.definition) == ("Revenue", "Revenue", "Money taken at the till, before refunds.")
    assert (path.context, path.context_id, path.unit, path.fact) == ("Store sales", "sales", "Shops", "sale")
    assert path.measure.model_dump(exclude_unset=True) == {"name": "Revenue", "aggregation": "sum", "grain": "day", "currency": "USD", "snapshot": False}
    assert [(h.order, h.id, h.use) for h in path.hops] == [
        (1, "sales_day", "always"), (2, "sales_store", "question"), (3, "store_region", "question"),
        (4, "sales_item", "available"), (5, "store_client", "available")]
    hops = {h.id: h for h in path.hops}
    day = hops["sales_day"]
    assert (day.kind, day.source, day.target, day.cardinality, day.type, day.declared, day.window) == (
        "calendar_rollup", "sale", "trading day", "one-to-one", "inner", [], None)
    assert "not declared: as SQL generation assumes" in day.said and "left only where an exclusion" not in day.said
    assert "which joins left only where an exclusion must keep rows with no match" in hops["store_region"].said
    store = hops["sales_store"]
    assert (store.type, store.declared) == ("left", ["cardinality", "type"]) and store.warning is None
    item = hops["sales_item"]
    assert (item.cardinality, item.type, item.declared) == ("one-to-many", "left", ["cardinality", "type", "validity_window"])
    assert item.window == "the item version in force on sale date"
    assert item.warning.startswith("One-to-many: a sale row can match several item rows")
    assert path.warnings == [] and "One-to-many" not in out.summary
    assert hops["store_client"].kind == "cross_reference" and "certified matches only" in hops["store_client"].said
    kinds = [(d.kind, d.via) for d in path.dimensions]
    assert kinds == [("calendar", "sales_day"), ("hierarchy", "store_region"), ("cross_reference", "store_client")]
    calendar, region, xref = path.dimensions
    assert calendar.said == "Dates roll up to trading day on the fiscal april, from the sale date (event date)."
    assert [(lv.name, lv.code, lv.column, lv.broader) for lv in region.levels] == [
        ("East", "E", "region code", "Country"), ("North", "N", "region code", "Country"), ("the north", "N", "region code", None),
        ("by region", None, "region code", None)]
    assert xref.name == "Bank client"
    assert [(c.kind, c.id, c.label, c.on) for c in path.conditions] == [
        ("filter", "valid", "Valid sales only", "sale"), ("exclusion", "not_test", "Not a test store", "store"),
        ("asked", "asked:north region n", "North region (N)", "sales region")]
    assert [(t.kind, t.on) for t in path.time_rules] == [("release_driver", "trading day"), ("scd2", "store"),
                                                         ("version_date", "item")]
    assert "Revenue (Store sales): sale rows summed by day in USD; joined in order sale to trading day (one-to-one, inner), " \
           "then sale to store (one-to-one, left), then store to sales region (one-to-one, inner)" in out.summary
    assert "filters: Valid sales only; exclusions: Not a test store; as asked: North region (N)." in out.summary


def explained_text(out) -> str:
    """The explained paths and their sentences: the neighbourhood sentence before them is the graph read's own, which
    names its table and column nodes as the graph read does for everyone."""
    return json.dumps([p.model_dump(exclude_unset=True) for p in out.paths]) + " ".join(map(service.path_summary, out.paths))


def test_non_engineers_see_business_words_and_an_engineer_physical_names(graph):
    business = explained_text(explain(graph))
    for physical in PHYSICAL:
        assert physical not in business, physical
    assert '"condition"' not in business and '"predicate"' not in business and '"expression"' not in business
    engineer = explain(graph, engineer=True)
    path, = engineer.paths
    hop = {h.id: h for h in path.hops}["sales_item"]
    assert (hop.source, hop.target, hop.condition) == ("shop.SALES", "shop.ITEM", "SALES.ITEM_ID = ITEM.ITEM_ID")
    assert hop.window == "shop.SALES.SOLD_ON BETWEEN shop.ITEM.EFF_DT AND shop.ITEM.EXP_DT"
    assert path.measure.expression == "sum(SALES.AMT)"
    assert path.conditions[-1].id == "asked:shop.REGION.RGN_CD"
    assert [c.predicate for c in path.conditions] == [
        "SALES.OK_IN = 1", "STORE.STORE_ID = SALES.STORE_ID AND STORE.TEST_IN = 1", "shop.REGION.RGN_CD = 'N'"]
    assert "(EFF_DT to EXP_DT, current where CURR_IN)" in path.time_rules[1].said
    assert "(DAY_DT released by RLSD_IN, frozen on FRZ_DT, released on RLSE_DT)" in path.time_rules[0].said


def test_a_unit_the_person_may_not_read_is_absent(graph):
    for who in ("biz", "shoponly"):
        for engineer in (False, True):
            text = json.dumps(explain(graph, who, engineer).model_dump())
            for secret in SECRET:
                assert secret not in text, (who, engineer, secret)
    shop_only = json.dumps(explain(graph, "shoponly").model_dump())
    assert "store_client" not in shop_only and "Bank client" not in shop_only and "Banking" not in shop_only
    everything, = explain(graph, "boss").paths
    assert "sales_secret" in [h.id for h in everything.hops]
    assert {"vault_only", "vault_cleared"} <= {c.id for c in everything.conditions}


def test_a_question_whose_term_has_no_context_the_person_may_read_has_no_path(graph):
    engine = synthetic(graph)
    graph._rights = Rights(graph.identity, {"outsider": "Bank"}, {"outsider": frozenset({"bank"})},
                           {"outsider": frozenset({"querying"})}, frozenset())
    out = service.explore(engine, "outsider", question=QUESTION)
    assert out.paths is None and "Store sales" not in out.summary


def test_a_derived_measure_explains_its_operands(graph):
    out = explain(graph, question="Revenue share by region")
    numerator, denominator, plain = out.paths
    assert (plain.context_id, plain.part_of) == ("sales", None)
    assert (numerator.context_id, numerator.part_of, numerator.basis) == (
        "sales", "the numerator of revenue share of total, a share", "Revenue share")
    assert denominator.part_of == "the denominator of revenue share of total, a share"
    assert "Revenue (Store sales, the numerator of revenue share of total, a share)" in out.summary


def test_a_stated_size_threshold_is_explained_without_its_figure(graph):
    path, = explain(graph, question="Revenue from sales of $250K or more").paths
    asked = path.conditions[-1]
    assert (asked.kind, asked.id) == ("asked", "asked:size threshold")
    assert asked.label == "a size threshold the question states, on each row's sale amount, whatever its sign"
    assert "250" not in json.dumps(path.model_dump())


def test_explore_a_hierarchy_term_lists_its_members_and_codes(graph):
    engine = synthetic(graph)
    out = service.explore(engine, "biz", node="term:Sales Region")
    assert [(lv.name, lv.code, lv.column, lv.broader, lv.of) for lv in out.members] == [
        ("Sales Region", None, None, None, None), ("East", "E", "region code", None, "Sales Region"),
        ("North", "N", "region code", None, "Sales Region")]
    assert "Its hierarchy: Sales Region, East (E), North (N)." in out.summary
    assert "SR" not in json.dumps(out.model_dump())
    north = service.explore(engine, "boss", node="term:North", engineer=True).members
    assert [(lv.name, lv.code, lv.column, lv.broader, lv.of) for lv in north] == [
        ("North", "N", "shop.REGION.RGN_CD", "Country", "Sales Region"), ("Country", None, None, None, None),
        ("Sales Region", "SR", "vault.SECRET.CODE", None, None)]
    assert service.explore(engine, "biz", node="term:Revenue").members is None


def test_explore_a_join_gives_its_facts_and_hides_one_the_person_may_not_read(graph):
    engine = synthetic(graph)
    out = service.explore(engine, "biz", node="join:sales_item")
    assert out.node.model_dump() == {"kind": "join", "id": "join:sales_item", "name": "sales_item"}
    assert out.link == "/cerebro?mode=graph&node=table%3Ashop.SALES"
    assert service.read(engine, "biz", node="table:shop.SALES").nodes
    assert (out.hop.cardinality, out.hop.type, out.hop.order, out.hop.use) == ("one-to-many", "left", None, None)
    assert out.summary.startswith("sales_item is a join: sale to item, a key join: one-to-many, a left join, valid on")
    assert "One-to-many" in out.summary
    for hidden in ("join:sales_secret", "join:store_client", "join:nowhere"):
        who = "shoponly" if hidden == "join:store_client" else "biz"
        with pytest.raises(LookupError, match="No such node."):
            service.explore(engine, who, node=hidden)
    assert service.explore(engine, "biz", node="join:store_client").hop.kind == "cross_reference"


def test_a_repeat_explain_sends_the_graph_store_no_query_and_each_read_runs_on_every_store(graph, monkeypatch):
    sent = []
    real = Graph.q

    def recorded(self, cypher, params=None):
        sent.append(cypher)
        return real(self, cypher, params)
    monkeypatch.setattr(Graph, "q", recorded)
    reads = []
    real_reader = Graph.reader

    def counted(self):
        reads.append(1)
        return real_reader(self)
    monkeypatch.setattr(Graph, "reader", counted)
    explain(graph)
    service.explore(synthetic(graph), "biz", node="term:Sales Region")
    assert reads
    reads.clear()
    explain(graph)
    service.explore(synthetic(graph), "biz", node="term:Sales Region")
    assert reads == []
    translate = Translate(Schema.from_meta(meta_model(), 128), Namespace("proj", "kge_graph", "g_test"))
    for cypher in {service.TERM_DEFINITIONS, service.BROADER_READ, service.IS_A_READ, service.CODED_AS_READ}:
        assert cypher in sent and "GRAPH_TABLE" in translate(cypher).sql, cypher


@pytest.fixture
def client(graph, monkeypatch, tmp_path):
    engine = synthetic(graph)
    fake = SimpleNamespace(engine=lambda: engine, PROFILE=SimpleNamespace(run_dir=lambda: tmp_path, client="test"))
    held = {"roles": ["business"]}
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(http, "cerebro_held", lambda who, state=None: held["roles"])
    monkeypatch.setattr(routes, "served", fake)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[http.principal] = principal_from_header
    with TestClient(app) as c:
        c.held = held
        yield c


def principal_from_header(request: Request) -> str:
    return request.headers.get("who", "biz")


def test_the_route_and_the_tool_give_the_path_in_business_words_or_an_engineer_s(client):
    import anyio
    body = client.post("/graph/explore", json={"question": QUESTION}).json()
    assert [h["id"] for h in body["paths"][0]["hops"]][:3] == ["sales_day", "sales_store", "store_region"]
    assert not any(p in json.dumps(body["paths"]) for p in PHYSICAL)
    assert client.get("/graph/explore", params={"node": "join:sales_secret"}).status_code == 404
    assert client.get("/graph/explore", params={"node": "join:sales_item"}).json()["hop"]["target"] == "item"
    client.held["roles"] = ["engineer"]
    engineer = client.post("/graph/explore", json={"question": QUESTION}).json()
    assert engineer["paths"][0]["hops"][0]["source"] == "shop.SALES"
    assert client.get("/graph/explore", params={"node": "join:sales_item"}).json()["hop"]["target"] == "shop.ITEM"

    async def invoke(ctx, call, check):
        return call(check(ctx))
    tools = {tool.name: tool for tool in graph_mcp.mcp_server(invoke)._tool_manager.list_tools()}
    asked = anyio.run(lambda: tools["explore_question"].fn(ctx="biz", question=QUESTION))
    assert asked.model_dump() == engineer
    client.held["roles"] = ["business"]
    asked = anyio.run(lambda: tools["explore_question"].fn(ctx="biz", question=QUESTION))
    assert asked.model_dump() == body


def test_a_counted_entity_and_a_banded_group_by_mark_the_hops_they_need(graph):
    engine = synthetic(graph)
    engine.semantics.entities["shop_item"]["label"] = "item"
    engine.VOCABULARY = [*VOCABULARY, {"kind": "band_options", "params": {"id": "region_band", "column": "shop.REGION.RGN_CD"}}]
    req = SimpleNamespace(group_by=["region_band"], filtered=[], counted=None, entity=None, units=[])
    assert service.needed(CONTEXT, req, engine) == {"sales_store", "store_region"}
    req = SimpleNamespace(group_by=[], filtered=[], counted="shop_item", entity=None, units=[])
    assert service.needed(CONTEXT, req, engine) == {"sales_item"}
    req = SimpleNamespace(group_by=[], filtered=[], counted=None, entity=None, units=["bank"])
    assert service.needed(CONTEXT, req, engine) == {"store_client"}


def test_a_graph_without_the_term_edge_types_explores_with_no_hierarchy(graph, monkeypatch):
    real = Graph.q

    def missing(self, cypher, params=None):
        if any(name in cypher for name in ("BROADER", "IS_A", "CODED_AS")):
            raise RuntimeError("Binder exception: Table BROADER does not exist.")
        return real(self, cypher, params)
    monkeypatch.setattr(Graph, "q", missing)
    monkeypatch.setattr(service, "missing_type", lambda exc: "does not exist" in str(exc))
    engine = synthetic(graph)
    assert service.explore(engine, "biz", node="term:Sales Region").members is None
    assert explain(graph).paths[0].hops


def test_stated_conditions_are_worded_and_an_engineer_s_predicate_is_sql(graph):
    engine = synthetic(graph)
    context = Context(**{**{k: getattr(CONTEXT, k) for k in ("id", "label", "description", "term", "measure", "expression",
                                                             "aggregation", "fact", "values", "filters", "selectors", "joins")},
                         "grain": "day"})
    req = SimpleNamespace(filters=[("RGN_CD", "not_in", ("N", "E"))], filtered=["shop.REGION.RGN_CD"])
    words_ = service.Words(engine, False, frozenset({"shop"}))
    asked, = service.asked_conditions(words_, engine, context, req, "Revenue outside some regions")
    assert asked.label == "region code is none of N, E" and asked.predicate is None
    engineer = service.Words(engine, True, frozenset({"shop"}))
    asked, = service.asked_conditions(engineer, engine, context, req, "Revenue outside some regions")
    assert asked.predicate == "shop.REGION.RGN_CD not_in ('N', 'E')"


def test_a_cross_reference_reads_from_the_context_s_unit_and_hides_its_curated_table(graph):
    engine = synthetic(graph)
    far_first = Join("client_store", "cross_reference", "CLIENT.CLIENT_ID = MATCH.CLIENT_ID AND MATCH.STORE_ID = STORE.STORE_ID",
                     None, "xref.MATCH", "bank.CLIENT", "shop.STORE", False)
    business = service.hop_of(service.Words(engine, False, frozenset({"shop", "bank"})), far_first, 1, "available", "shop")
    assert (business.source, business.target) == ("store", "Bank client")
    engineer = service.hop_of(service.Words(engine, True, frozenset({"shop", "bank"})), far_first, 1, "available", "shop")
    assert engineer.condition is None
    reads_xref = service.hop_of(service.Words(engine, True, frozenset({"shop", "bank", "xref"})), far_first, 1, "available", "shop")
    assert "MATCH" in reads_xref.condition


def test_the_tool_s_text_and_structured_content_agree_with_the_route(client):
    """FastMCP gives a tool's reply twice, as structured content (model_dump) and as text (pydantic_core.to_json): both
    are the route's JSON, with no null for a part that does not apply."""
    import anyio
    import pydantic_core

    async def invoke(ctx, call, check):
        return call(check(ctx))
    tools = {tool.name: tool for tool in graph_mcp.mcp_server(invoke)._tool_manager.list_tools()}
    for name, arguments, route in (("explore", {"node": "term:Revenue"}, lambda: client.get("/graph/explore", params={"node": "term:Revenue"})),
                                   ("explore", {"node": "join:sales_day"}, lambda: client.get("/graph/explore", params={"node": "join:sales_day"})),
                                   ("explore_question", {"question": QUESTION},
                                    lambda: client.post("/graph/explore", json={"question": QUESTION}))):
        result = anyio.run(lambda: tools[name].fn(ctx="biz", **arguments))
        text = pydantic_core.to_json(result).decode()
        assert json.loads(text) == result.model_dump() == route().json(), name
        assert "null" not in text, name


def test_a_version_date_with_no_business_name_is_a_date_of_its_table(graph):
    engine = synthetic(graph)
    engine.BUSINESS = {}
    path, = service.explore(engine, "biz", question=QUESTION).paths
    assert path.time_rules[-1].said == "The item is read as the version valid on a date of the sale."
    assert {h.id: h for h in path.hops}["sales_item"].window == "the item version in force on a date of the sale"


def test_a_real_store_error_is_not_read_as_a_missing_type(graph, monkeypatch):
    monkeypatch.setattr(Graph, "q", lambda self, cypher, params=None: (_ for _ in ()).throw(RuntimeError("store down")))
    with pytest.raises(RuntimeError, match="store down"):
        service.held_rows(graph, service.BROADER_READ)


def test_map_term_s_per_term_view_is_the_path_explore_question_explains_for_the_term_alone(graph):
    engine = synthetic(graph)
    for who in ("biz", "shoponly", "boss"):
        for engineer in (False, True):
            paths = service.term_paths(engine, who, "Revenue", engineer)
            assert paths == service.question_paths(engine, who, "Revenue", engineer), (who, engineer)
    business = json.dumps([p.model_dump() for p in service.term_paths(engine, "biz", "Revenue", False)])
    for hidden in (*PHYSICAL, *SECRET):
        assert hidden not in business, hidden
    path, = service.term_paths(engine, "biz", "Revenue", False)
    assert [(h.id, h.use) for h in path.hops] == [("sales_day", "always"), ("sales_store", "available"),
                                                 ("store_region", "available"), ("sales_item", "available"),
                                                 ("store_client", "available")]
    assert [p.part_of for p in service.term_paths(engine, "biz", "Revenue Share", False)] == [
        "the numerator of revenue share of total, a share", "the denominator of revenue share of total, a share"]
    graph._rights = Rights(graph.identity, {"outsider": "Bank"}, {"outsider": frozenset({"bank"})},
                           {"outsider": frozenset({"querying"})}, frozenset())
    assert service.term_paths(engine, "outsider", "Revenue", False) == []


def test_a_one_to_many_warning_is_the_path_s_only_where_the_question_joins_that_hop(graph):
    engine = synthetic(graph)
    engine.semantics.entities["shop_item"]["label"] = "item"
    req = SimpleNamespace(group_by=[], filtered=[], filters=[], counted="shop_item", entity=None, units=[])
    definitions, _, broader, codes = service.term_reads(engine)
    words_ = service.Words(engine, False, frozenset({"shop", "bank"}))
    path = service.explained(engine, words_, CONTEXT, req, "", definitions, codes, broader)
    item, = [h for h in path.hops if h.id == "sales_item"]
    assert item.use == "question" and path.warnings == [item.warning]
    assert "One-to-many" in service.path_summary(path)


def test_a_size_threshold_is_explained_only_where_ask_reads_one_on_rows(graph):
    path, = explain(graph, question="Revenue of at least $5 million by region").paths
    assert [c for c in path.conditions if "size threshold" in c.label] == []
    engine = synthetic(graph)
    engine.semantics.entities["shop_sale"]["label"] = "delivery"
    assert service.sized(engine, CONTEXT, "Revenue from deliveries of at least $5 million")
    path, = explain(graph, question="Revenue from sales worth over 250 by region").paths
    assert [c.label for c in path.conditions if "size threshold" in c.label] == [
        "a size threshold the question states, on each row's sale amount, whatever its sign"]


def test_time_rules_are_those_sql_generation_applies(graph):
    engine = synthetic(graph)
    engine.semantics.scd2["shop.ITEM"] = {"id": "scd2-item", "table": "shop.ITEM", "valid_from": "EFF_DT", "valid_to": "EXP_DT",
                                          "is_current": "CURR_IN"}
    engine.semantics.releases["shop.STORE"] = {"id": "store-release", "table": "shop.STORE", "period": "P", "released": "R",
                                               "release_date": "RD", "freeze_date": "FD"}
    path, = service.term_paths(engine, "biz", "Revenue", False)
    assert [(t.kind, t.on) for t in path.time_rules] == [("release_driver", "trading day"), ("scd2", "store"),
                                                         ("version_date", "item")]
    dated = context_with(JOINS)
    words_ = service.Words(engine, False, frozenset({"shop", "bank"}))
    assert service.dated_by_join(dated, "shop.ITEM", engine.semantics.scd2["shop.ITEM"])
    assert [(t.kind, t.on) for t in service.time_rules_of(words_, engine, dated, ["shop.SALES", "shop.STORE", "shop.ITEM"])] == [
        ("scd2", "store")]
    by_date = Context(CONTEXT.id, CONTEXT.label, CONTEXT.description, CONTEXT.term, CONTEXT.measure, CONTEXT.expression,
                      CONTEXT.aggregation, CONTEXT.grain, CONTEXT.fact, CONTEXT.values, CONTEXT.filters, CONTEXT.selectors,
                      CONTEXT.joins, version_dates={"shop.STORE": "shop.SALES.SOLD_ON"})
    assert [(t.kind, t.on) for t in service.time_rules_of(words_, engine, by_date, ["shop.SALES", "shop.STORE"])] == [
        ("version_date", "store")]
    aliased = Join("store_again", "key", "STORE.STORE_ID = prior_store.STORE_ID AND prior_store.CURR_IN = 0", "prior_store",
                   None, "shop.STORE", "shop.STORE", False)
    store_policy = engine.semantics.scd2["shop.STORE"]
    assert not service.dated_by_join(context_with((*JOINS, aliased)), "shop.STORE", store_policy)
    undated = context_with(tuple(Join(j.id, j.kind, j.condition, j.alias, j.through, j.source, j.target, j.required)
                                 if j.id == "sales_item" else j for j in JOINS))
    assert [(t.kind, t.on) for t in service.time_rules_of(words_, engine, undated, ["shop.SALES", "shop.STORE", "shop.ITEM"])] == [
        ("scd2", "store"), ("scd2", "item")]


def context_with(joins) -> Context:
    """The synthetic context with these joins and no version date of its own."""
    c = CONTEXT
    return Context(c.id, c.label, c.description, c.term, c.measure, c.expression, c.aggregation, c.grain, c.fact, c.values,
                   c.filters, c.selectors, joins)


def test_a_self_join_names_its_far_side_by_its_alias(graph):
    engine = synthetic(graph)
    parent = Join("region_parent", "hierarchy", "REGION.PARENT_CD = parent_region.RGN_CD", "parent_region", None,
                  "shop.REGION", "shop.REGION", False)
    business = service.hop_of(service.Words(engine, False, frozenset({"shop"})), parent)
    assert (business.source, business.target) == ("sales region", "parent region")
    engineer = service.hop_of(service.Words(engine, True, frozenset({"shop"})), parent)
    assert (engineer.source, engineer.target) == ("shop.REGION", "shop.REGION AS parent_region")


def test_map_term_explains_the_term_s_own_contexts_and_no_other_term_its_words_contain(graph):
    engine = synthetic(graph)
    by_question = service.question_paths(engine, "biz", "Revenue share", False)
    by_term = service.term_paths(engine, "biz", "Revenue Share", False)
    assert [(p.term, p.part_of) for p in by_question] == [(p.term, p.part_of) for p in by_term] + [("Revenue", None)]
