"""The BigQuery graph store (kge/adapters/graphstore/bigquery.py, issue #95 item 1.1): GRAPH_STORE picks it, every Cypher
query the demo sends has a BigQuery form (GQL in GRAPH_TABLE for a read, DML on the node and edge tables for a write,
kge/adapters/graphstore/gql.py), the compiled model's BigQuery DDL makes the tables and the property graph
(kge/compiler/emit.py bigquery_ddl), and what BigQuery's REST API answers is read back as Ladybug's rows are. Nothing
here reaches BigQuery: the REST API is a stand-in. Whether BigQuery accepts the text is checked live, by the owner."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kge.adapters import graphstore
from kge.adapters.graphstore import bigquery as bq
from kge.adapters.graphstore.cypher import Unsupported
from kge.adapters.graphstore.gql import TEMPLATE, Namespace, Schema, Translate, bq_type, ddl, decode, encode, rows
from kge.compiler import emit
from kge.graph.schema import meta_model
from kge.pipeline.settings import gcp_native

DEMO = Path(__file__).resolve().parents[2]
HASH = "ab" * 32
GEN = "0123abcd"
NS = Namespace("proj", "kge_meridian_graph", bq.prefix_of(HASH, GEN))
GRAPH = "`proj.kge_meridian_graph.gabababababababab_0123abcd_graph`"


@pytest.fixture(scope="module")
def schema() -> Schema:
    return Schema.from_meta(meta_model(), 128)


@pytest.fixture
def tr(schema):
    return Translate(schema, NS)


def table(name: str) -> str:
    return f"`proj.kge_meridian_graph.gabababababababab_0123abcd_{name}`"


def test_graph_store_is_ladybug_unless_graph_store_says_bigquery(monkeypatch):
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    assert graphstore.store().name == "ladybug" and graphstore.STORE == "ladybug" and graphstore.store().dialect == "kuzu"
    monkeypatch.setenv("GRAPH_STORE", " BigQuery ")
    assert graphstore.store().name == "bigquery" and graphstore.STORE == "bigquery" and graphstore.store().dialect == "bigquery"
    assert graphstore.graph_path(Path("/x")) == Path("/x/graph.bigquery") and graphstore.sidecars(Path("/x/graph.bigquery")) == []
    monkeypatch.setenv("GRAPH_STORE", "kuzu")
    with pytest.raises(ValueError, match="GRAPH_STORE='kuzu'.*ladybug.*bigquery"):
        graphstore.store()


def test_graph_store_bigquery_puts_the_process_on_the_cloud_path(monkeypatch):
    for name in ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE"):
        monkeypatch.delenv(name, raising=False)
    assert not gcp_native()
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    assert gcp_native()


def test_both_stores_offer_the_port_with_the_bigquery_one_importing_no_graph_library():
    from kge.ports import GraphStore
    out = subprocess.run([sys.executable, "-c", "import sys; from kge.adapters.graphstore import bigquery; "
                          "s = bigquery.BigQueryStore(); s.info(); "
                          "print(sorted(m for m in sys.modules if m.split('.')[0] in ('ladybug', 'kuzu', 'real_ladybug') "
                          "or m == 'kge.adapters.graphstore.ladybug'))"], cwd=DEMO, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
    assert isinstance(bq.BigQueryStore(), GraphStore)
    assert bq.BigQueryStore().info() == {"engine": "bigquery", "version": "GoogleSQL", "storage_version": None}


def test_a_read_is_gql_in_graph_table_with_its_columns_named_as_ladybug_names_them(tr):
    plan = tr("MATCH (t:Term) RETURN t.name")
    assert plan.kind == "rows"
    assert plan.sql == f"SELECT c0 FROM GRAPH_TABLE({GRAPH} MATCH (v_t:`Term`) RETURN v_t.`name` AS c0)"
    assert [c.name for c in plan.columns] == ["t.name"]
    plan = tr("MATCH (c:Chunk) RETURN count(c), count(*) AS n, count(*)")
    assert [c.name for c in plan.columns] == ["COUNT(c._ID)", "n", "COUNT_STAR()"]
    assert "COUNT(v_c.`id`) AS c0, COUNT(*) AS c1" in plan.sql


def test_principal_ids_are_one_ordered_gql_projection(tr):
    plan = tr("MATCH (p:Principal) RETURN p.id AS id ORDER BY id")
    assert plan.kind == "rows"
    assert plan.sql == (f"SELECT c0 FROM GRAPH_TABLE({GRAPH} MATCH (v_p:`Principal`) "
                        "RETURN v_p.`id` AS c0) ORDER BY c0 ASC NULLS LAST")
    assert [c.name for c in plan.columns] == ["id"]
    assert plan.params == {}


def test_inline_properties_and_parameters_are_conditions_on_typed_named_parameters(tr):
    plan = tr("MATCH (p:Principal {id: $p})-[:CAN_READ]->(u:Unit) RETURN u.id", {"p": "ana"})
    assert "MATCH (v_p:`Principal`)-[:`CAN_READ`]->(v_u:`Unit`) WHERE (v_p.`id` = @p)" in plan.sql
    assert plan.params == {"p": "STRING"}
    assert "$" not in plan.sql and "ana" not in plan.sql


def test_lists_are_unnested_and_an_array_propertys_null_is_its_length(tr):
    plan = tr("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.embedding IS NOT NULL AND $p IN c.purposes AND d.unit IN $u "
              "AND d.kind IN ['a', 'b'] RETURN c.id", {"p": "analysis", "u": []})
    assert "ARRAY_LENGTH(v_c.`embedding`) > 0" in plan.sql
    assert "@p IN UNNEST(v_c.`purposes`)" in plan.sql and "v_d.`unit` IN UNNEST(@u)" in plan.sql
    assert "v_d.`kind` IN UNNEST(['a', 'b'])" in plan.sql
    assert plan.params == {"p": "STRING", "u": "ARRAY<STRING>"}


def test_optional_match_exists_and_order_with_ladybugs_null_placement(tr):
    plan = tr("MATCH (t:Tag) WHERE NOT EXISTS { MATCH (:Tag)-[:SUPERSEDES]->(t) } OPTIONAL MATCH (c:Chunk)-[:TAGGED]->(t) "
              "RETURN t.id AS id, c.id AS chunk ORDER BY t.confidence DESC, id")
    assert "WHERE (NOT (EXISTS { MATCH (:`Tag`)-[:`SUPERSEDES`]->(v_t) }))" in plan.sql
    assert "OPTIONAL MATCH (v_c:`Chunk`)-[:`TAGGED`]->(v_t)" in plan.sql
    assert plan.sql.endswith("ORDER BY o0 DESC NULLS FIRST, c0 ASC NULLS LAST")
    assert "v_t.`confidence` AS o0" in plan.sql and plan.sql.startswith("SELECT c0, c1 FROM")


def test_with_is_let_order_and_limit_and_a_collect_beside_arrays_groups_by_keys(tr):
    plan = tr("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WITH d, c, array_cosine_similarity(c.embedding, CAST($q AS FLOAT[128])) "
              "AS score ORDER BY round(score, 6) DESC, c.id LIMIT $k OPTIONAL MATCH (c)-[:MENTIONS]->(t:Term) "
              "RETURN c.id AS chunk, c.purposes AS purposes, score, collect(t.name) AS terms ORDER BY score DESC, chunk",
              {"q": [0.5] * 128, "k": 5})
    assert "LET a_score = (1 - COSINE_DISTANCE(v_c.`embedding`, @q)) ORDER BY ROUND(a_score, 6) DESC NULLS FIRST, v_c.`id` ASC NULLS LAST LIMIT @k" in plan.sql
    assert "ANY_VALUE(v_c.`purposes`) AS c1" in plan.sql and "ARRAY_AGG(v_t.`name` IGNORE NULLS) AS c3" in plan.sql
    assert "GROUP BY v_c.`id`" in plan.sql
    assert plan.params == {"k": "INT64", "q": "ARRAY<FLOAT64>"}
    assert [c.empty_is_null for c in plan.columns] == [False, False, False, True]


def test_a_shortest_path_is_any_shortest_with_its_edges_properties_gathered_in_order(tr):
    plan = tr("MATCH p = (a:Tbl {id: $a})-[:JOINS_TABLE* SHORTEST 1..5]-(b:Tbl {id: $b}) "
              "RETURN properties(rels(p),'from_table') AS ft LIMIT 1", {"a": "x", "b": "y"})
    assert "MATCH ANY SHORTEST (v_a:`Tbl`)-[v__1:`JOINS_TABLE`]-{1,5}(v_b:`Tbl`)" in plan.sql
    assert "LET h0 = ARRAY_AGG(v__1.`from_table`) RETURN h0 AS c0 LIMIT 1" in plan.sql


def test_writes_are_dml_on_the_tables_over_the_keys_a_graph_table_match_returns(tr):
    plan = tr("CREATE (:Tag {id: $id, confidence: 1.0, model: $who})", {"id": "t", "who": None})
    assert plan.kind == "write"
    assert plan.sql == f"INSERT INTO {table('Tag')} (`id`, `confidence`, `model`) VALUES (@id, 1.0, @who)"
    assert plan.params == {"id": "STRING", "who": "STRING"}
    plan = tr("MATCH (c:Chunk {id: $c}), (n:Tag {id: $n}), (o:Tag {id: $o}) CREATE (c)-[:TAGGED]->(n), (n)-[:SUPERSEDES]->(o)",
              {"c": "c", "n": "n", "o": "o"})
    assert plan.sql.startswith("BEGIN TRANSACTION; INSERT INTO " + table("TAGGED__Chunk__Tag"))
    assert "(`edge_id`, `src_key`, `dst_key`) SELECT GENERATE_UUID(), s, d FROM GRAPH_TABLE(" in plan.sql
    assert f"INSERT INTO {table('SUPERSEDES__Tag__Tag')}" in plan.sql and plan.sql.endswith("COMMIT TRANSACTION;")
    plan = tr("MERGE (t:Term {name: $n}) SET t.domain = $d", {"n": "x", "d": "y"})
    assert plan.sql == (f"MERGE {table('Term')} AS t USING (SELECT @n AS k) AS s ON t.`name` = s.k WHEN MATCHED THEN UPDATE SET "
                        "`domain` = @d WHEN NOT MATCHED THEN INSERT (`name`, `domain`) VALUES (s.k, @d)")
    plan = tr("MATCH (c:Chunk {id: $c}) SET c.readiness = $r, c.embedding = NULL", {"c": "c", "r": "citable"})
    assert plan.sql.startswith(f"UPDATE {table('Chunk')} SET `readiness` = @r, `embedding` = CAST([] AS ARRAY<FLOAT64>) WHERE `id` IN")
    plan = tr("MATCH (x:Principal {id: $id})-[r:CAN_READ]->(:Unit) DELETE r", {"id": "a"})
    assert plan.sql.startswith(f"DELETE FROM {table('CAN_READ__Principal__Unit')} WHERE `edge_id` IN (SELECT k FROM GRAPH_TABLE(")


def test_detach_delete_takes_the_nodes_edges_out_of_every_table_touching_its_type_first(tr, schema):
    plan = tr("MATCH (t:Tag {id: $id}) DETACH DELETE t", {"id": "x"})
    assert plan.sql.startswith("DECLARE doomed ARRAY<STRING> DEFAULT (SELECT ARRAY_AGG(DISTINCT k) FROM GRAPH_TABLE(")
    touching = [e.table for e, _ in schema.touching("Tag")]
    assert sorted(touching) == ["PROPOSES__Tag__Col", "SUPERSEDES__Tag__Tag", "TAGGED__Chunk__Tag"]
    assert f"DELETE FROM {table('SUPERSEDES__Tag__Tag')} WHERE `src_key` IN UNNEST(doomed) OR `dst_key` IN UNNEST(doomed)" in plan.sql
    assert plan.sql.index("TAGGED__Chunk__Tag") < plan.sql.index(f"DELETE FROM {table('Tag')} WHERE `id` IN UNNEST(doomed)")


def test_ddl_of_a_known_table_is_nothing_and_table_info_reads_the_information_schema(tr):
    assert tr("CREATE NODE TABLE IF NOT EXISTS Process(id STRING, description STRING, PRIMARY KEY (id))").kind == "noop"
    with pytest.raises(Unsupported):
        tr("CREATE NODE TABLE Process(id STRING, PRIMARY KEY (id))")
    plan = tr("CALL table_info('OntologyConcept') RETURN name")
    assert "INFORMATION_SCHEMA.COLUMNS WHERE table_name = @table_info_table" in plan.sql and plan.columns[0].name == "name"


def test_a_table_the_graph_does_not_hold_is_a_binder_exception_as_on_ladybug(tr):
    with pytest.raises(RuntimeError, match="Binder exception"):
        tr("MATCH (x:Nowhere) RETURN x.id")


@pytest.mark.parametrize("text", ["MATCH (n:Tag) RETURN n", "MATCH (n:Tag) RETURN n.id SKIP 1", "UNWIND $x AS y RETURN y",
                                  "MATCH (n:Tag) WHERE n.id STARTS WITH 'a' RETURN n.id", "MATCH (n:Tag) WITH n, count(*) AS c RETURN c"])
def test_anything_outside_the_subset_is_refused_naming_the_query(tr, text):
    with pytest.raises(Unsupported, match="in the query|RETURN of the element|aggregate in WITH"):
        tr(text, {"x": []})


def test_parameters_are_typed_with_nulls_and_arrays_and_rows_decoded_by_the_schema():
    assert encode("a", "STRING", None) == {"name": "a", "parameterType": {"type": "STRING"}, "parameterValue": {}}
    assert encode("f", "ARRAY<FLOAT64>", [1, 0.5])["parameterValue"] == {"arrayValues": [{"value": "1.0"}, {"value": "0.5"}]}
    assert encode("b", "BOOL", False)["parameterValue"] == {"value": "false"}
    assert decode({"type": "BOOLEAN"}, "false") is False and decode({"type": "INTEGER"}, "3") == 3
    assert decode({"type": "FLOAT", "mode": "REPEATED"}, [{"v": "0.25"}]) == [0.25]
    data = {"schema": {"fields": [{"name": "c1", "type": "STRING", "mode": "REPEATED"}, {"name": "c0", "type": "BOOLEAN"}]},
            "rows": [{"f": [{"v": []}, {"v": "true"}]}]}
    from kge.adapters.graphstore.gql import Column
    names, records = rows(data, [Column("c0", "t.ok"), Column("c1", "terms", empty_is_null=True)])
    assert names == ["t.ok", "terms"] and records == [[True, None]]


def test_the_bigquery_ddl_is_the_compiled_models_tables_and_property_graph(schema):
    out = emit.bigquery_ddl(meta_model(), 128)
    assert out == ddl(schema, TEMPLATE)
    graph = out[-1]
    assert out[0] == "DROP PROPERTY GRAPH IF EXISTS `${project}.${dataset}.${prefix}graph`"
    assert graph.startswith("CREATE OR REPLACE PROPERTY GRAPH `${project}.${dataset}.${prefix}graph` NODE TABLES (")
    assert "`${project}.${dataset}.${prefix}Unit` AS `Unit` KEY (`id`) LABEL `Unit` PROPERTIES (`id`, `label`, `key_column`, `ordinal`)" in graph
    assert ("`${project}.${dataset}.${prefix}HAS_TABLE__Unit__Tbl` AS `HAS_TABLE__Unit__Tbl` KEY (`edge_id`) SOURCE KEY (`src_key`) "
            "REFERENCES `Unit` (`id`) DESTINATION KEY (`dst_key`) REFERENCES `Tbl` (`id`) LABEL `HAS_TABLE` PROPERTIES (`edge_id`)") in graph
    assert all("LABEL `ALIAS_OF`" in graph and f"ALIAS_OF__Alias__{end}" in graph for end in ("Term", "Unit", "Col", "Entity"))
    chunk = next(s for s in out if "${prefix}Chunk`" in s and s.startswith("CREATE OR REPLACE TABLE"))
    assert "`purposes` ARRAY<STRING>" in chunk and "`embedding` ARRAY<FLOAT64>" in chunk
    for name in ("Unit", "Tbl", "Col", "Term", "Document", "Chunk", "Tag", "Principal", "HAS_TABLE__Unit__Tbl", "HAS_COLUMN__Tbl__Col",
                 "JOINS__Col__Col", "MAPS_TO__Col__Term", "DEFINES__Document__Term", "TAGGED__Chunk__Tag"):
        assert any(s.startswith(f"CREATE OR REPLACE TABLE `${{project}}.${{dataset}}.${{prefix}}{name}` (") for s in out), name
    assert bq_type("FLOAT[768]") == "ARRAY<FLOAT64>"


def test_the_artifact_and_its_model_hash_are_the_same_whichever_store_loads_it(monkeypatch, tmp_path):
    from kge.compiler import pack as packs
    from kge.compiler.compile import VERSION
    pack = packs.read(DEMO / "clients", "meridian")
    if pack.findings:
        pytest.skip(f"the pack has findings here: {pack.findings[0]}")
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    local = emit.build(pack, meta_model(), VERSION)
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    assert emit.build(pack, meta_model(), VERSION).model_hash == local.model_hash
    assert not any("bigquery" in path for path in local.files)


def test_what_the_store_keeps_for_a_process_is_only_what_the_load_alone_writes():
    """The mutable types, from a compiled artifact's own graph-model.yaml: the pipeline's, a steward's decision's and
    the access mirror's. A read touching any of them is asked of BigQuery each time."""
    import yaml
    from kge.compiler import pack as packs
    from kge.compiler.compile import VERSION
    pack = packs.read(DEMO / "clients", "meridian")
    if pack.findings:
        pytest.skip(f"the pack has findings here: {pack.findings[0]}")
    model = yaml.safe_load(emit.build(pack, meta_model(), VERSION).files["graph-model.yaml"])
    assert Schema.from_graph_model(model).mutable == {"Chunk", "Tag", "HAS_CHUNK", "MENTIONS", "TAGGED", "SUPERSEDES", "PROPOSES",
                                                     "Term", "DEFINES", "CAN_WRITE", "Principal", "CAN_READ", "MAY_PERFORM",
                                                     "Process"}


class Rest:
    """BigQuery's REST API, stood in: answers queued per call, and every request kept."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[tuple[str, dict | None]] = []

    def post(self, url, body):
        self.sent.append((url, body))
        return self.answer()

    def get(self, url):
        self.sent.append((url, None))
        return self.answer()

    def answer(self):
        """The next answer queued, raised where it is an error."""
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def client(monkeypatch, rest: Rest) -> bq.Client:
    monkeypatch.setattr(bq.Client, "_post", lambda self, url, body: rest.post(url, body))
    monkeypatch.setattr(bq.Client, "_get", lambda self, url: rest.get(url))
    return bq.Client("proj", sleeper=lambda _: None)


def test_the_client_polls_an_unfinished_job_and_reads_every_page(monkeypatch):
    rest = Rest({"jobComplete": False, "jobReference": {"jobId": "j1", "location": "EU"}},
                {"jobComplete": True, "jobReference": {"jobId": "j1"}, "schema": {"fields": [{"name": "c0", "type": "STRING"}]},
                 "rows": [{"f": [{"v": "a"}]}], "pageToken": "p2"},
                {"jobComplete": True, "rows": [{"f": [{"v": "b"}]}]})
    out = client(monkeypatch, rest).query("SELECT 1", [])
    assert [r["f"][0]["v"] for r in out["rows"]] == ["a", "b"]
    assert rest.sent[0][1]["parameterMode"] == "NAMED" and rest.sent[0][1]["useLegacySql"] is False
    assert "/queries/j1?" in rest.sent[1][0] and "location=EU" in rest.sent[1][0] and "pageToken=p2" in rest.sent[2][0]


def test_the_client_runs_an_aborted_transaction_again_and_fails_in_one_line_otherwise(monkeypatch):
    aborted = {"jobComplete": True, "status": {"errorResult": {"reason": "aborted", "message": "Transaction is aborted due to concurrent update"}}}
    rest = Rest(aborted, {"jobComplete": True, "rows": []})
    assert client(monkeypatch, rest).query("UPDATE x SET a = 1 WHERE TRUE", []) == {"schema": {}, "rows": []}
    rest = Rest({"jobComplete": True, "errors": [{"reason": "invalidQuery", "message": "Syntax error"}]})
    with pytest.raises(bq.QueryFailed, match="BigQuery graph query failed: Syntax error"):
        client(monkeypatch, rest).query("SELEC", [])


RATE_LIMITED = ("BigQuery graph error (HTTP 403): Exceeded rate limits: too many api requests per user per method for this "
                "user_method (JobService.query)")
DONE = {"jobComplete": True, "rows": []}


def limited() -> bq.QueryFailed:
    return bq.QueryFailed(RATE_LIMITED, "rateLimitExceeded", 403)


def sleeping(monkeypatch, rest: Rest) -> tuple[bq.Client, list[float]]:
    client(monkeypatch, rest)
    slept: list[float] = []
    return bq.Client("proj", sleeper=slept.append), slept


def test_a_rate_limited_query_is_sent_again_after_a_jittered_back_off(monkeypatch):
    rest = Rest(limited(), limited(), DONE)
    c, slept = sleeping(monkeypatch, rest)
    assert c.query("UPDATE x SET a = 1 WHERE TRUE", []) == {"schema": {}, "rows": []}
    assert len(rest.sent) == 3 and len(slept) == 2
    assert all(bq.BACKOFF * 2 ** i <= s <= 2 * bq.BACKOFF * 2 ** i for i, s in enumerate(slept))
    job = {"jobComplete": True, "status": {"errorResult": {"reason": "jobRateLimitExceeded", "message": "Exceeded rate limits"}}}
    rest = Rest(job, DONE)
    c, slept = sleeping(monkeypatch, rest)
    assert c.query("SELECT 1", []) == {"schema": {}, "rows": []} and len(slept) == 1


def test_a_read_that_fails_on_bigquerys_side_is_sent_again_and_a_write_never_is(monkeypatch):
    backend = bq.QueryFailed("BigQuery graph error (HTTP 503): Backend error", "backendError", 503)
    rest = Rest(backend, DONE)
    c, slept = sleeping(monkeypatch, rest)
    assert c.query("SELECT 1", [], read=True) == {"schema": {}, "rows": []} and len(slept) == 1
    rest = Rest(backend, DONE)
    c, slept = sleeping(monkeypatch, rest)
    with pytest.raises(bq.GraphStoreBusy, match=r"busy: .*HTTP 503.*tried 1 times"):
        c.query("INSERT INTO x VALUES (1)", [])
    assert len(rest.sent) == 1 and slept == []


def test_a_write_whose_job_poll_is_rate_limited_is_never_sent_again_and_a_read_is(monkeypatch):
    """The POST started the job, so a rateLimitExceeded on its poll (polled) may follow a write that ran: it ends as
    GraphStoreBusy, a 503, after one POST; a read is sent again."""
    rest = Rest({"jobComplete": False, "jobReference": {"jobId": "j1"}}, limited(), DONE)
    c, slept = sleeping(monkeypatch, rest)
    with pytest.raises(bq.GraphStoreBusy):
        c.query("INSERT INTO x VALUES (1)", [])
    assert len(rest.sent) == 2 and slept == []
    rest = Rest({"jobComplete": False, "jobReference": {"jobId": "j1"}}, limited(), DONE)
    c, slept = sleeping(monkeypatch, rest)
    assert c.query("SELECT 1", [], read=True) == {"schema": {}, "rows": []} and len(rest.sent) == 3


def test_a_query_still_rate_limited_after_the_retries_is_a_503_and_a_refusal_is_never_retried(monkeypatch):
    from kge.adapters.model.errors import ExternalCallError, transient
    rest = Rest(*[limited() for _ in range(bq.RETRIES + 1)])
    c, slept = sleeping(monkeypatch, rest)
    with pytest.raises(bq.GraphStoreBusy) as raised:
        c.query("SELECT 1", [], read=True)
    assert isinstance(raised.value, ExternalCallError) and transient(raised.value)
    assert str(raised.value).startswith("The graph store is busy: " + RATE_LIMITED)
    assert len(rest.sent) == bq.RETRIES + 1 and len(slept) == bq.RETRIES
    for code, reason in ((403, "quotaExceeded"), (403, "accessDenied"), (400, "invalidQuery")):
        refused = bq.QueryFailed(f"BigQuery graph error (HTTP {code}): no", reason, code)
        rest = Rest(refused)
        c, slept = sleeping(monkeypatch, rest)
        with pytest.raises(bq.QueryFailed) as raised:
            c.query("SELECT 1", [], read=True)
        assert not isinstance(raised.value, ExternalCallError) and len(rest.sent) == 1 and slept == []


def test_a_connection_marks_a_read_so_a_failed_read_is_sent_again(monkeypatch, tmp_path, schema):
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    path = pointer(tmp_path, schema)
    backend = bq.QueryFailed("BigQuery graph error (HTTP 500): Internal error", "internalError", 500)
    unit = {"jobComplete": True, "schema": {"fields": [{"name": "c0", "type": "STRING"}]}, "rows": [{"f": [{"v": "retail"}]}]}
    rest = Rest(backend, unit, backend)
    client(monkeypatch, rest)
    conn = bq.BigQueryStore().connect(bq.BigQueryStore().open(path))
    assert conn.execute("MATCH (p:Principal)-[:CAN_READ]->(u:Unit) RETURN u.id").get_all() == [["retail"]]
    with pytest.raises(bq.GraphStoreBusy):
        conn.execute("MATCH (t:Tag {id: $id}) SET t.value = $v", {"id": "a", "v": "b"})
    assert len(rest.sent) == 3


def pointer(tmp_path: Path, schema: Schema, model_hash: str = HASH, generation: str = GEN) -> Path:
    path = tmp_path / "stores" / "meridian" / model_hash / "graph.bigquery"
    g = bq.Graph(path)
    g.ns.prefix = bq.prefix_of(model_hash, generation)
    g.write(schema)
    return path


def test_the_pointer_names_the_graphs_dataset_prefix_and_schema(monkeypatch, tmp_path, schema):
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    path = pointer(tmp_path, schema)
    held = json.loads(path.read_text())
    assert (held["store"], held["dataset"], held["prefix"], held["graph"]) == ("bigquery", "kge_meridian_graph",
                                                                              "gabababababababab_0123abcd_",
                                                                              "gabababababababab_0123abcd_graph")
    g = bq.BigQueryStore().open(path)
    assert g.schema.nodes.keys() == schema.nodes.keys() and g.ns.graph == GRAPH
    with pytest.raises(ValueError, match="model hash's folder"):
        bq.Graph(tmp_path / "graph.bigquery")
    with pytest.raises(RuntimeError, match="Binder exception.*no tables yet"):
        bq.Connection(bq.Graph(tmp_path / "stores" / "meridian" / ("cd" * 32) / "graph.bigquery")).execute("MATCH (u:Unit) RETURN u.id")


def test_a_connection_reads_rows_keeps_what_only_the_load_writes_and_forgets_it_on_a_write(monkeypatch, tmp_path, schema):
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    schema.mutable = {"Tag", "Chunk"}
    path = pointer(tmp_path, schema)
    unit = {"jobComplete": True, "schema": {"fields": [{"name": "c0", "type": "STRING"}, {"name": "c1", "type": "INTEGER"}]},
            "rows": [{"f": [{"v": "retail"}, {"v": "1"}]}]}
    rest = Rest(unit, {"jobComplete": True}, unit)
    client(monkeypatch, rest)
    conn = bq.BigQueryStore().connect(bq.BigQueryStore().open(path))
    q = bq.BigQueryStore().q(conn)
    assert q("MATCH (u:Unit) RETURN u.id, u.ordinal AS n") == [{"u.id": "retail", "n": 1}]
    assert q("MATCH (u:Unit) RETURN u.id, u.ordinal AS n") == [{"u.id": "retail", "n": 1}]
    assert len(rest.sent) == 1
    conn.execute("MATCH (t:Tag {id: $id}) SET t.value = $v", {"id": "a", "v": "b"})
    assert rest.sent[1][1]["queryParameters"] == [{"name": "id", "parameterType": {"type": "STRING"}, "parameterValue": {"value": "a"}},
                                                  {"name": "v", "parameterType": {"type": "STRING"}, "parameterValue": {"value": "b"}}]
    assert conn.execute("MATCH (u:Unit) RETURN u.id, u.ordinal AS n").get_all() == [["retail", 1]]
    assert len(rest.sent) == 3
    df = conn.execute("MATCH (u:Unit) RETURN u.id, u.ordinal AS n").get_as_df()
    assert list(df.columns) == ["u.id", "n"]


def test_an_artifact_file_loads_with_copys_null_rule_as_typed_rows(monkeypatch, tmp_path, schema):
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    path = pointer(tmp_path, schema)
    nodes = tmp_path / "Col.csv"
    nodes.write_text("id,unit,tbl,name,data_type,description,is_key,certified,grain,currency,calendar,conversion\n"
                     "r.a.x,r,a,x,INT,,true,false,\\N,,\\N,\n")
    edges = tmp_path / "ALIAS_OF.csv"
    edges.write_text("from,to\nrevenue,retail\n")
    rest = Rest({"jobComplete": True})
    client(monkeypatch, rest)
    store = bq.BigQueryStore()
    with store.batch(store.connect(store.open(path))) as w:
        w.load_file("Col", nodes)
        w.load_file("ALIAS_OF", edges, ("Alias", "Unit"))
    (url, body), = rest.sent
    assert body["query"].startswith("BEGIN TRANSACTION; INSERT INTO " + table("Col") + " (`id`, `unit`,")
    assert "FROM UNNEST(@w0) AS r" in body["query"] and body["query"].endswith("COMMIT TRANSACTION;")
    values = body["queryParameters"][0]["parameterValue"]["arrayValues"][0]["structValues"]
    assert values["description"] == {"value": ""} and values["grain"] == {} and values["currency"] == {"value": ""}
    assert values["is_key"] == {"value": "true"} and values["certified"] == {"value": "false"}
    assert (f"INSERT INTO {table('ALIAS_OF__Alias__Unit')} (`edge_id`, `src_key`, `dst_key`) "
            "SELECT GENERATE_UUID(), r._src, r._dst FROM UNNEST(@w1) AS r") in body["query"]
    rest = Rest({"jobComplete": True})
    client(monkeypatch, rest)
    with pytest.raises(ValueError, match="name its pair of ends"):
        with store.batch(store.connect(store.open(path))) as w:
            w.load_file("ALIAS_OF", edges)


def test_a_batch_is_one_script_of_set_based_statements_with_the_meaning_of_its_order(monkeypatch, tmp_path, schema):
    """The pipeline's writes for two chunks: one request, a statement per kind of write and table, in phase order,
    each row in a typed parameter; a write that would undo a kept one of a later phase sends what is kept first."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    path = pointer(tmp_path, schema)
    rest = Rest({"jobComplete": True}, {"jobComplete": True})
    client(monkeypatch, rest)
    store = bq.BigQueryStore()
    with store.batch(store.connect(store.open(path))) as w:
        for c in ("c1", "c2"):
            w.upsert("Chunk", c, {"doc_id": "d", "ordinal": 0, "text": "t"})
            w.create_edge("HAS_CHUNK", "Document", "d", "Chunk", c)
            w.delete_node("Tag", f"{c}/old")
            w.upsert("Tag", f"{c}/new", {"family": "privacy", "confidence": 0.5})
            w.create_edge("TAGGED", "Chunk", c, "Tag", f"{c}/new")
            w.delete_edges("MENTIONS", "Chunk", c, "Term")
            w.create_edge("MENTIONS", "Chunk", c, "Term", "revenue", {"confidence": 0.9, "generator": "rule"})
            w.update("Chunk", c, {"readiness": "citable", "purposes": ["analysis"], "embedding": None})
    (url, body), = rest.sent
    sql = body["query"]
    order = [sql.index(f"DELETE FROM {table('MENTIONS__Chunk__Term')}"), sql.index(f"DELETE FROM {table('Tag')}"),
             sql.index(f"MERGE {table('Chunk')} AS t USING (SELECT * FROM UNNEST(@w"), sql.index(f"INSERT INTO {table('HAS_CHUNK__Document__Chunk')}")]
    assert order == sorted(order)
    assert sql.count(f"INSERT INTO {table('TAGGED__Chunk__Tag')}") == 1
    assert f"WHERE r._src IN (SELECT `id` FROM {table('Chunk')}) AND r._dst IN (SELECT `id` FROM {table('Tag')})" in sql
    update = next(p for p in body["queryParameters"] if p["parameterType"]["arrayType"]["structTypes"][-1]["name"] == "embedding")
    assert update["parameterType"]["arrayType"]["structTypes"][-2] == {"name": "purposes", "type": {"type": "ARRAY", "arrayType": {"type": "STRING"}}}
    assert update["parameterValue"]["arrayValues"][0]["structValues"]["embedding"] == {"arrayValues": []}
    rest = Rest({"jobComplete": True}, {"jobComplete": True})
    client(monkeypatch, rest)
    with store.batch(store.connect(store.open(path))) as w:
        w.upsert("Tag", "t", {"family": "privacy"})
        w.delete_node("Tag", "t")
    assert len(rest.sent) == 2 and rest.sent[0][1]["query"].startswith("MERGE") and "DELETE FROM" in rest.sent[1][1]["query"]


def test_prune_drops_the_generations_nothing_may_read(monkeypatch, tmp_path, schema):
    """A generation stays while a pointer names it, or the release ledger's releases do (held: the one just released and
    the one before it, which a service may serve until it follows); every other is dropped, its tables and its property
    graph. The ledger before release identities, graph.bigquery.served, is kept from once, then retired."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    root = tmp_path / "stores" / "meridian"
    current = pointer(tmp_path, schema, HASH, "11111111")
    (root / "graph.bigquery.served").write_text(json.dumps(["gcdcdcdcdcdcdcdcd_22222222_"]))
    listed = {"jobComplete": True, "schema": {"fields": [{"name": "table_name", "type": "STRING"}]},
              "rows": [{"f": [{"v": t}]} for t in ("gabababababababab_11111111_Unit", "gcdcdcdcdcdcdcdcd_22222222_Unit",
                                                   "gcdcdcdcdcdcdcdcd_33333333_Unit", "gcdcdcdcdcdcdcdcd_33333333_HAS_TABLE__Unit__Tbl",
                                                   "unit_manifest", "chunks")]}
    rest = Rest(listed, {"jobComplete": True})
    client(monkeypatch, rest)
    assert bq.BigQueryStore().prune(root, []) == ["gcdcdcdcdcdcdcdcd_33333333_"]
    drop = rest.sent[1][1]["query"]
    assert "DROP PROPERTY GRAPH IF EXISTS `proj.kge_meridian_graph.gcdcdcdcdcdcdcdcd_33333333_graph`" in drop
    assert "DROP TABLE IF EXISTS `proj.kge_meridian_graph.gcdcdcdcdcdcdcdcd_33333333_HAS_TABLE__Unit__Tbl`" in drop
    assert "11111111" not in drop and "22222222" not in drop and "unit_manifest" not in drop
    assert not (root / "graph.bigquery.served").exists()
    rest = Rest(dict(listed, rows=listed["rows"][:3]), {"jobComplete": True})
    client(monkeypatch, rest)
    pointer(tmp_path, schema, HASH, "44444444")
    assert bq.BigQueryStore().prune(root, ["gcdcdcdcdcdcdcdcd_33333333_"]) == ["gabababababababab_11111111_",
                                                                             "gcdcdcdcdcdcdcdcd_22222222_"]
    assert bq.BigQueryStore().generation(current) == "gabababababababab_44444444_"
    assert bq.BigQueryStore().generation(tmp_path / "none") is None
    from kge.adapters.graphstore.ladybug import LadybugStore
    assert LadybugStore().prune(tmp_path) == [] and LadybugStore().generation(tmp_path / "graph.ladybug") is None


def test_a_failed_onboards_saved_pointer_names_its_generation_until_a_later_release_prunes_it(
        monkeypatch, tmp_path, schema):
    """The REST stand-in holds BigQuery's generations: failed work restored for retry keeps its generation named; once a
    changed candidate supersedes that pointer, release-time prune drops the old generation and keeps the new one."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    root = tmp_path / "stores" / "meridian"
    failed = pointer(tmp_path, schema, HASH, "11111111")
    old = f"g{HASH[:16]}_11111111_"
    new = f"g{HASH[:16]}_22222222_"
    listed = {"jobComplete": True, "schema": {"fields": [{"name": "table_name", "type": "STRING"}]},
              "rows": [{"f": [{"v": f"{old}Unit"}]}, {"f": [{"v": f"{new}Unit"}]}]}
    rest = Rest(listed, {"jobComplete": True})
    client(monkeypatch, rest)
    assert bq.BigQueryStore().generation(failed) == old
    assert bq.BigQueryStore().prune(root, []) == [new]

    pointer(tmp_path, schema, HASH, "22222222")
    rest = Rest(listed, {"jobComplete": True})
    client(monkeypatch, rest)
    assert bq.BigQueryStore().prune(root, []) == [old]
    assert old in rest.sent[1][1]["query"] and new not in rest.sent[1][1]["query"]


def test_create_runs_the_ddl_in_the_graphs_namespace_then_writes_the_pointer(monkeypatch, tmp_path):
    import yaml
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    path = tmp_path / "stores" / "meridian" / HASH / "graph.bigquery"
    rest = Rest({"jobComplete": True})
    client(monkeypatch, rest)
    store = bq.BigQueryStore()
    conn = store.connect(store.open(path))
    doc = {"node_types": {"Source": {"loaded_as": "Unit", "key": "id", "origin": "pack", "properties": [{"name": "id", "type": "STRING"}]}},
           "edge_types": {"NO_JOIN": {"loaded_as": "NO_JOIN", "from": "Source", "to": "Source", "origin": "pack", "properties": []}}}
    store.create(conn, yaml.safe_load(yaml.safe_dump(doc)), emit.bigquery_ddl(doc))
    sql = rest.sent[0][1]["query"]
    generation = re.search(r"gabababababababab_([0-9a-f]{8})_Unit", sql).group(1)
    assert "${" not in sql and f"CREATE OR REPLACE TABLE `proj.kge_meridian_graph.gabababababababab_{generation}_Unit` (`id` STRING);" in sql
    assert f"CREATE OR REPLACE PROPERTY GRAPH `proj.kge_meridian_graph.gabababababababab_{generation}_graph`" in sql
    held = json.loads(path.read_text())
    assert held["schema"]["nodes"] == [["Unit", "id", {"id": "STRING"}]] and held["prefix"] == f"gabababababababab_{generation}_"
    rest.answers.append({"jobComplete": True})
    store.create(store.connect(store.open(path)), doc, emit.bigquery_ddl(doc))
    assert json.loads(path.read_text())["prefix"] != held["prefix"]
    assert f"gabababababababab_{generation}_" not in rest.sent[1][1]["query"]


def cypher_literals() -> dict[str, str]:
    return bq.literals(DEMO / "kge")


def test_every_cypher_literal_of_the_code_has_a_bigquery_form(tr):
    held = cypher_literals()
    assert len(held) > 100
    failures = {}
    for where, text in held.items():
        names = set(re.findall(r"\$(\w+)", text))
        try:
            tr(text, {n: "x" for n in names})
        except Exception as exc:
            failures[where] = f"{type(exc).__name__}: {exc}"
    assert failures == {}


def test_a_release_ships_the_graph_stores_own_graph_and_no_other(monkeypatch, tmp_path):
    """releases/<client>/<hash>.tar.gz (kge/pipeline/objstore.py release_paths): on BigQuery the pointer and the
    markers, never a graph.ladybug or graph.kuzu left in the folder; on Ladybug its file and sidecars, as before."""
    from types import SimpleNamespace
    from kge.pipeline.objstore import release_paths
    folder = tmp_path / "stores" / "meridian" / HASH
    folder.mkdir(parents=True)
    for name in ("graph.bigquery", "graph.ladybug", "graph.ladybug.wal", "graph.kuzu", "LOADED", "RELEASED"):
        (folder / name).write_text("x")
    (tmp_path / "estate").mkdir()

    class Prof(SimpleNamespace):
        def at(self, h):
            return SimpleNamespace(graph_path=lambda: graphstore.graph_path(tmp_path / "stores" / "meridian" / h))

        def run_dir(self):
            return tmp_path / "run"

        def estate(self):
            return tmp_path / "estate"
    prof = Prof(artifacts=tmp_path / "artifacts", client="meridian", store=tmp_path / "stores", warehouses=tmp_path / "wh")
    names = lambda: sorted(p.name for p in release_paths(prof, HASH))   # noqa: E731
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    assert names() == ["LOADED", "RELEASED", "graph.bigquery"]
    monkeypatch.delenv("GRAPH_STORE")
    assert names() == ["LOADED", "RELEASED", "graph.ladybug", "graph.ladybug.wal"]


def test_the_jobs_work_carries_the_graph_stores_own_graph_and_no_other(monkeypatch, tmp_path):
    from kge.pipeline.objstore import work_paths
    folder = tmp_path / "build" / "stores" / "meridian" / HASH
    folder.mkdir(parents=True)
    for name in ("graph.bigquery", "graph.ladybug", "graph.ladybug.wal", "graph.kuzu", "LOADED"):
        (folder / name).write_text("x")
    (folder.parent / "served.json").write_text("[]")
    names = lambda: sorted(p.name for p in work_paths(tmp_path) if p.is_file())   # noqa: E731
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    assert names() == ["LOADED", "graph.bigquery", "served.json"], "the release ledger travels with the work"
    monkeypatch.delenv("GRAPH_STORE")
    assert names() == ["LOADED", "graph.ladybug", "graph.ladybug.wal", "served.json"]


class Prof:
    """A profile pinned to a model hash, its folder of the client's graph store under root."""

    def __init__(self, root: Path, model_hash: str, client: str = "meridian"):
        self.root, self.pinned, self.client = root, model_hash, client

    def graph_path(self) -> Path:
        return self.root / "stores" / self.client / self.pinned / "graph.bigquery"


def test_each_sync_names_a_chunks_table_of_its_own_and_the_served_one_is_left_alone(monkeypatch, tmp_path):
    """A candidate's sync of the hash being served writes a fresh table and its pointer; the release's pointer, carried
    in the served copy, still names the table it named; GRAPH_DATASET=candidates puts a candidate's in the candidates
    dataset; a mistyped value is refused."""
    from kge.adapters import retrieval
    from kge.adapters.retrieval import bigquery as bqr
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.delenv("GRAPH_DATASET", raising=False)
    served = Prof(tmp_path / "service", HASH)
    job = Prof(tmp_path / "job", HASH)
    dataset, first = retrieval.new_chunks_table(served, HASH)
    assert dataset == "kge_meridian_graph" and re.fullmatch(r"chunks_abababababababab_[0-9a-f]{8}", first)
    retrieval.name_chunks(served, dataset, first)
    _, second = retrieval.new_chunks_table(job, HASH)
    retrieval.name_chunks(job, dataset, second)
    assert second != first
    assert bqr.chunks_table(served) == f"`proj.kge_meridian_graph.{first}`"
    assert bqr.chunks_table(job) == f"`proj.kge_meridian_graph.{second}`"
    with pytest.raises(RuntimeError, match="names no BigQuery chunks table yet"):
        bqr.chunks_table(Prof(tmp_path / "none", HASH))
    monkeypatch.setenv("GRAPH_DATASET", "candidates")
    assert retrieval.new_chunks_table(job, HASH)[0] == "kge_meridian_candidates"
    graph = bq.Graph(tmp_path / "x" / "stores" / "meridian" / HASH / "graph.bigquery")
    assert graph.ns.dataset == "kge_meridian_candidates"
    monkeypatch.setenv("GRAPH_DATASET", "served")
    with pytest.raises(ValueError, match="GRAPH_DATASET='served'"):
        retrieval.new_chunks_table(job, HASH)


def test_a_release_prunes_the_chunks_tables_nothing_names(monkeypatch, tmp_path):
    from kge.adapters import retrieval
    from kge.adapters.retrieval import bigquery as bqr
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    root = tmp_path / "job" / "stores" / "meridian"
    current = Prof(tmp_path / "job", HASH)
    retrieval.name_chunks(current, "kge_meridian_graph", "chunks_abababababababab_11111111")
    (root / "chunks.bigquery.served").write_text(json.dumps(["chunks_abababababababab_22222222"]))
    held = ["kge_meridian_graph.chunks_abababababababab_44444444"]
    sent: list[str] = []

    def query(sql, project=None):
        sent.append(sql)
        names = ("chunks_abababababababab_11111111", "chunks_abababababababab_22222222", "chunks_abababababababab_33333333",
                 "chunks_abababababababab_44444444", "chunks", "gabababababababab_11111111_Unit")
        return [{"table_name": t} for t in names] if "INFORMATION_SCHEMA" in sql else []
    monkeypatch.setattr(bqr, "bq_query", query)
    assert retrieval.prune(root, held) == ["chunks_abababababababab_33333333"]
    assert sent[-1] == "DROP TABLE IF EXISTS `proj.kge_meridian_graph.chunks_abababababababab_33333333`;"
    assert not (root / "chunks.bigquery.served").exists()
    assert retrieval.prune(root, held) == ["chunks_abababababababab_22222222", "chunks_abababababababab_33333333"]
    monkeypatch.setenv("RETRIEVAL_BACKEND", "graph")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    assert retrieval.prune(root, held) == []


def test_the_improvement_jobs_candidate_steps_write_only_the_candidates_dataset(monkeypatch):
    from types import SimpleNamespace
    from kge.improve import abtest
    monkeypatch.delenv("GRAPH_DATASET", raising=False)
    env = abtest._sub_env(SimpleNamespace(registry=None, id="meridian"))
    assert env["GRAPH_DATASET"] == "candidates" and env["PROFILE"] == "meridian"


def test_on_the_whole_cloud_path_the_work_and_a_release_carry_no_duckdb_or_ladybug_file(monkeypatch, tmp_path):
    """WAREHOUSE_BACKEND, RETRIEVAL_BACKEND, APP_STATE_BACKEND and GRAPH_STORE all bigquery: the job's work and a
    release's tarball hold the graph's pointer and its chunks pointer, never a .duckdb warehouse nor a Ladybug or Kuzu
    graph, even ones an older work left in build."""
    import io
    import tarfile
    from kge.pipeline import objstore
    from kge.pipeline.profile import Profile
    app = tmp_path / "app"
    prof = Profile("meridian", "meridian", app / "clients", app / "build/artifacts", app / "build/stores", app / "build/landing",
                   app / "build/warehouses", DEMO / "deploy/meridian/identities.yaml", None, app / "deploy/profiles.yaml")
    folder = f"build/stores/meridian/{HASH}"
    for name in ("build/warehouses/retail.duckdb", "build/summary.json", f"build/artifacts/meridian/{HASH}/manifest.json",
                 f"{folder}/graph.bigquery", f"{folder}/chunks.bigquery", f"{folder}/graph.ladybug", f"{folder}/graph.ladybug.wal",
                 f"{folder}/graph.kuzu", f"{folder}/LOADED"):
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        (app / name).write_text("x")
    for name in ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE"):
        monkeypatch.setenv(name, "bigquery")
    released = tarfile.open(fileobj=io.BytesIO(objstore.tarball(app, objstore.release_paths(prof, HASH)))).getnames()
    work = tarfile.open(fileobj=io.BytesIO(objstore.tarball(app, objstore.work_paths(app, objstore.local_files(prof))))).getnames()
    for names in (released, work):
        assert f"{folder}/graph.bigquery" in names and f"{folder}/chunks.bigquery" in names
        assert not [n for n in names if n.endswith(".duckdb") or "/graph.ladybug" in n or "/graph.kuzu" in n], names


def test_prune_takes_each_dataset_a_pointer_names_not_the_first_pointers(monkeypatch, tmp_path, schema):
    """A candidate's pointer in the candidates dataset that sorts before the released graph's (Grok m1 on #97) no longer
    sends the whole prune there: each dataset is listed and pruned on its own, what any pointer or the ledger names kept,
    and the graph dataset's stale generations dropped."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    root = tmp_path / "stores" / "meridian"
    monkeypatch.setenv("GRAPH_DATASET", "candidates")
    pointer(tmp_path, schema, "0a" * 32, "aaaaaaaa")
    monkeypatch.delenv("GRAPH_DATASET")
    pointer(tmp_path, schema, HASH, "11111111")

    def listing(*names):
        return {"jobComplete": True, "schema": {"fields": [{"name": "table_name", "type": "STRING"}]},
                "rows": [{"f": [{"v": t}]} for t in names]}
    rest = Rest(listing("g0a0a0a0a0a0a0a0a_aaaaaaaa_Unit", "g0a0a0a0a0a0a0a0a_bbbbbbbb_Unit"), {"jobComplete": True},
                listing("gabababababababab_11111111_Unit", "gabababababababab_22222222_Unit"), {"jobComplete": True})
    client(monkeypatch, rest)
    assert bq.BigQueryStore().prune(root, []) == ["g0a0a0a0a0a0a0a0a_bbbbbbbb_", "gabababababababab_22222222_"]
    queries = [body["query"] for _, body in rest.sent]
    assert "`proj.kge_meridian_candidates`.INFORMATION_SCHEMA" in queries[0] and "kge_meridian_graph" in queries[2]
    assert "kge_meridian_candidates.g0a0a0a0a0a0a0a0a_bbbbbbbb_Unit" in queries[1] and "aaaaaaaa" not in queries[1]
    assert "kge_meridian_graph.gabababababababab_22222222_Unit" in queries[3] and "11111111" not in queries[3]


def test_the_chunks_prune_takes_each_dataset_a_pointer_names(monkeypatch, tmp_path):
    from kge.adapters import retrieval
    from kge.adapters.retrieval import bigquery as bqr
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    root = tmp_path / "job" / "stores" / "meridian"
    retrieval.name_chunks(Prof(tmp_path / "job", "0a" * 32), "kge_meridian_candidates", "chunks_0a0a0a0a0a0a0a0a_aaaaaaaa")
    current = Prof(tmp_path / "job", HASH)
    retrieval.name_chunks(current, "kge_meridian_graph", "chunks_abababababababab_11111111")
    sent: list[str] = []

    def query(sql, project=None):
        sent.append(sql)
        if "INFORMATION_SCHEMA" not in sql:
            return []
        held = {"kge_meridian_candidates": ("chunks_0a0a0a0a0a0a0a0a_aaaaaaaa", "chunks_0a0a0a0a0a0a0a0a_bbbbbbbb"),
                "kge_meridian_graph": ("chunks_abababababababab_11111111", "chunks_abababababababab_22222222")}
        return [{"table_name": t} for d, names in held.items() if f".{d}`" in sql for t in names]
    monkeypatch.setattr(bqr, "bq_query", query)
    assert retrieval.prune(root, []) == ["chunks_0a0a0a0a0a0a0a0a_bbbbbbbb", "chunks_abababababababab_22222222"]
    assert "DROP TABLE IF EXISTS `proj.kge_meridian_graph.chunks_abababababababab_22222222`;" in sent


def test_prune_keeps_the_generation_a_release_recorded_after_a_reload(monkeypatch, tmp_path, schema):
    """A reload of the released hash repoints its folder; the release record still names the generation a server
    opens, so no prune drops it while it is recorded."""
    from kge.adapters import released
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    root = tmp_path / "stores" / "meridian"
    path = pointer(tmp_path, schema, HASH, "11111111")
    released.write(path.parent, HASH, bq.BigQueryStore().release_entry(path), None)
    assert bq.BigQueryStore().served(path) == path.with_name("release.json")
    pointer(tmp_path, schema, HASH, "22222222")
    listed = {"jobComplete": True, "schema": {"fields": [{"name": "table_name", "type": "STRING"}]},
              "rows": [{"f": [{"v": f"gabababababababab_{g}_Unit"}]} for g in ("11111111", "22222222", "33333333")]}
    rest = Rest(listed, {"jobComplete": True})
    client(monkeypatch, rest)
    assert bq.BigQueryStore().prune(root, []) == ["gabababababababab_33333333_"]


def test_the_chunks_prune_keeps_the_table_a_release_recorded_after_a_reload(monkeypatch, tmp_path):
    """Grok n1 on #97, the graph's test above for chunks: a reload of the released hash syncs a new chunks table and
    repoints the folder's chunks.bigquery; the release record still names the table a server searches, so no prune
    drops it while it is recorded."""
    from kge.adapters import released, retrieval
    from kge.adapters.retrieval import bigquery as bqr
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    root = tmp_path / "job" / "stores" / "meridian"
    prof = Prof(tmp_path / "job", HASH)
    retrieval.name_chunks(prof, "kge_meridian_graph", "chunks_abababababababab_11111111")
    folder = prof.graph_path().parent
    released.write(folder, HASH, None, retrieval.release_entry(folder))
    retrieval.name_chunks(prof, "kge_meridian_graph", "chunks_abababababababab_22222222")
    sent: list[str] = []

    def query(sql, project=None):
        sent.append(sql)
        names = [f"chunks_abababababababab_{g}" for g in ("11111111", "22222222", "33333333")]
        return [{"table_name": t} for t in names] if "INFORMATION_SCHEMA" in sql else []
    monkeypatch.setattr(bqr, "bq_query", query)
    assert retrieval.prune(root, []) == ["chunks_abababababababab_33333333"]
    assert sent[-1] == "DROP TABLE IF EXISTS `proj.kge_meridian_graph.chunks_abababababababab_33333333`;"


def test_the_readable_answer_s_reads_have_bigquery_forms(tr):
    """What wording.py reads to keep an answer's text off what the caller may not read: GQL reads in GRAPH_TABLE."""
    from kge.services.ask.engine.wording import COLUMNS, DOCUMENTS
    for query, columns in ((COLUMNS, "v_c.`unit` AS c0, v_c.`tbl` AS c1, v_c.`name` AS c2"), (DOCUMENTS, "v_d.`id` AS c0, v_d.`unit` AS c1")):
        sql = tr(query, {}).sql
        assert sql.startswith("SELECT ") and f"FROM GRAPH_TABLE({GRAPH} MATCH" in sql and f"RETURN {columns})" in sql, sql
