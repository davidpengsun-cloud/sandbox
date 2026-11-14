"""Graph paths consume ordered snapshot records, never SQL, plans or refusal wording."""
from __future__ import annotations

import itertools
import json
import threading
from types import SimpleNamespace

import pytest

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.adapters.graphstore.gql import Namespace, Schema, Translate
from kge.graph.schema import meta_model
from kge.pipeline import trace
from kge.pipeline.trace import QueryCancelled
from kge.semantics.query import Join, Rule, read_context
from kge.services.ask.engine.contextpath import Graphed, ReadPath, build_graph_path, recorded
from kge.services.ask.engine.graphview import CALENDAR_SNAPSHOT, VOCABULARY, Graph
from kge.services.ask.engine.path import PathStreamRecorder, build_path

QUESTION = "Net flows for FY26-Q1 by region"
RELEASES = itertools.count()


def test_sql_only_tables_remain_in_the_activity_path_without_creating_graph_reads(tmp_path):
    eng = engine(tmp_path)
    answer = {'question': QUESTION, 'principal': 'analyst', 'behaviour': 'figure',
              'sql': 'SELECT * FROM wealth.FLOW JOIN wealth.MONTH ON TRUE'}
    path = build_path(eng, answer)
    assert next(n for n in path['nodes'] if n['id'] == 'tables')['label'] == 'wealth.FLOW, wealth.MONTH'
    assert read_path(eng).path()['nodes'] == []


class FakeGraph:
    def __init__(self, reads: dict[str, set[str]]):
        self.reads = reads
        self.term_ids = {"Net Flows": "net-flows", "Region": "region"}

    def readable_units(self, principal: str) -> set[str]:
        return set(self.reads.get(principal, set()))

    def catalogue(self):
        return SimpleNamespace(columns={"wealth.FLOW": ["AMT_USD", "CUST_SSN", "FLOW_DT"], "wealth.MONTH": ["EOM_DT"],
                                        "wealth.REGION": ["RGN_CD"], "wealth.ACCT": ["ACCT_ID"],
                                        "retail.secret_ledger": ["ledger_code"]},
                               documents=())

    def vocabulary(self):
        return SimpleNamespace(term_ids=dict(self.term_ids), acronyms={"NF": ("net-flows",)},
                               calendars={"wealth.net-flows": "fiscal_april"},
                               golden={"flows": ({"id": "golden_x_v2:X01", "set": "golden_x", "version": "v2", "item": "X01",
                                                  "kind": "CERTIFIED_BY"},
                                                 {"id": "golden_x_v2:X02", "set": "golden_x", "version": "v2", "item": "X02",
                                                  "kind": "CERTIFIED_BY"})})

    def rights(self):
        return SimpleNamespace(roles={"analyst": "Analyst"})


def engine(tmp_path):
    (tmp_path / "golden_x_v1.json").write_text(json.dumps({"version": "v2", "items": [
        {"id": "X01", "question": QUESTION}, {"id": "X02", "question": "Net flows for FY26-Q2"}]}))
    context = SimpleNamespace(
        id="flows", unit="wealth", bindings={}, label="Net flows", description="Flows in released months.", term="Net Flows", measure="wealth.net-flows",
        expression="sum(FLOW.AMT_USD)", aggregation="sum", fact="wealth.FLOW", values=("wealth.FLOW.AMT_USD",),
        filters=(Rule("released", "Released months", "MONTH.EOM_DT IS NOT NULL", "wealth.MONTH"),
                 Rule("large", "Large flows", "FLOW.AMT_USD > 100", "wealth.FLOW")),
        selectors=(Rule("not_test", "Not a test account", "ACCT.ACCT_ID = FLOW.ACCT_ID", "wealth.ACCT", "exclude"),
                   Rule("with_ledger", "Has a ledger", "secret_ledger.ledger_code = 'X'", "retail.secret_ledger", "include")),
        joins=(Join("flow_month", "calendar_rollup", "FLOW.FLOW_DT = MONTH.EOM_DT", None, None, "wealth.FLOW", "wealth.MONTH", True),
               Join("flow_region", "key", "FLOW.RGN_CD = REGION.RGN_CD", None, None, "wealth.FLOW", "wealth.REGION", False),
               Join("flow_ledger", "key", "FLOW.ACCT_ID = secret_ledger.acct", None, None, "wealth.FLOW", "retail.secret_ledger",
                    False)))
    return SimpleNamespace(
        identity={"release": next(RELEASES)}, g=FakeGraph({"analyst": {"wealth", "retail"}, "wealth_only": {"wealth"},
                                                           "retail_only": {"retail"}}),
        UNIT_LABEL={"wealth": "Wealth", "retail": "Retail"}, BUSINESS={}, RESTRICTED_COLUMNS=[("wealth", "FLOW", "CUST_SSN")],
        GROUP_BY={"by region": "wealth.REGION.RGN_CD"}, ALIASES={"net flows": "Net Flows"},
        CALENDARS={"fiscal_april": {"year_start_month": 4}}, profile=SimpleNamespace(estate=lambda: tmp_path),
        semantics=SimpleNamespace(
            contexts={"flows": context},
            scd2={"wealth.FLOW": {"id": "scd2-flow", "is_current": "CURR_IN"}, "wealth.REGION": {"id": "scd2-region"}},
            entities={"wealth_region": {"label": "region", "table": "wealth.REGION", "key": ["RGN_CD"]}},
            measure_declarations={"wealth.net-flows": {"col": "wealth.FLOW.AMT_USD", "currency": "USD"}}))



def read_path(eng, who="analyst", question=QUESTION, events=None):
    with trace.graph_stream(events.append if events is not None else None):
        return ReadPath(eng, who, question)


def returned_context(eng):
    eng.semantics.graph_vocabulary = eng.g.vocabulary()
    read_context(eng.semantics, eng.semantics.contexts["flows"])


def test_elements_stream_at_each_record_in_read_order_before_answer(tmp_path):
    eng, events = engine(tmp_path), []
    rec = read_path(eng, events=events)
    with trace.graph_recording(rec):
        trace.graph_read("principal_read", ("analyst", {"wealth", "retail"}))
        assert [e["node"]["id"] for e in events] == ["Principal:analyst"]
        trace.graph_read("access", ("analyst", ["wealth"], []))
        assert events[-1]["event"] == "edge_added"
        returned_context(eng)
        trace.graph_read("join", ("flows", eng.semantics.contexts["flows"].joins[0]))
        assert not any(e["event"] == "answer" for e in events)
        out = {"graph_path": build_graph_path(eng, {})}
    PathStreamRecorder(QUESTION, events.append).emit_final(out)
    assert events[-1]["event"] == "answer"
    first_seen = list(dict.fromkeys(e["node"]["id"] for e in events if e["event"] in ("node_added", "state_changed")))
    assert first_seen == [n["id"] for n in out["graph_path"]["nodes"]]
    assert first_seen.index("Principal:analyst") < first_seen.index("Context:flows")
    nodes, edges = {}, {}
    for event in events[:-1]:
        if event["event"] in ("node_added", "state_changed"):
            nodes[event["node"]["id"]] = event["node"]
        elif event["event"] == "edge_added":
            e = event["edge"]
            assert e["from"] in nodes and e["to"] in nodes
            edges[e["from"], e["to"], e["type"], e["label"]] = e
    assert list(nodes.values()) == out["graph_path"]["nodes"]
    assert list(edges.values()) == out["graph_path"]["edges"]


@pytest.mark.parametrize("fields", [
    {"sql": "SELECT * FROM wealth.UNREAD"},
    {"reference_sql": "SELECT * FROM wealth.UNREAD"},
    {"context": {"context": "flows", "fact": "wealth.FLOW", "joins": ["flow_month"]}},
    {"history": [{"question": QUESTION, "plan": {"measures": [{"context": {"id": "flows"}}]}}]},
    {"behaviour": "refuse", "reason": "SQL gate: no runtime holds wealth.FLOW; may not read retail."},
    {"result": {"columns": [{"lineage": ["flows", "wealth.net-flows"]}]}},
])
def test_no_sql_plan_context_lineage_or_reason_inference(tmp_path, fields):
    eng = engine(tmp_path)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        assert build_graph_path(eng, {"question": QUESTION, "principal": "analyst", **fields}) == {
            "nodes": [], "edges": [], "unused": 0}
    assert build_graph_path(eng, fields)["nodes"] == []


def test_only_consumed_rules_and_joins_are_used_and_details_list_both(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng)
    context = eng.semantics.contexts["flows"]
    with trace.graph_recording(rec):
        returned_context(eng)
        assert rec.path()["unused"] == 7
        trace.graph_read("rule", (context.id, "Filter", context.filters[0]))
        trace.graph_read("rule", (context.id, "RowSelector", context.selectors[0]))
        trace.graph_read("join", (context.id, context.joins[0]))
        trace.graph_read("policy", (context.id, context.fact, eng.semantics.scd2[context.fact]))
    path = rec.path()
    assert path["unused"] == 4
    assert {e["label"] for e in path["edges"] if e["type"] == "JoinRule" and e["used"]} == {"flow_month"}
    by_id = {n["id"]: n for n in path["nodes"]}
    detail = by_id["Context:flows"]["detail"]
    for line in ("Reached from: Term:net-flows (Net Flows)", "Certified by: Golden:golden_x_v2:X01", "Filter used: released",
                 "Filter not used: large", "RowSelector used: not_test", "RowSelector not used: with_ledger",
                 "JoinRule used: flow_month", "JoinRule not used: flow_region, flow_ledger", "Policies applied (SCD2): scd2-flow"):
        assert line in detail
    assert "JoinRule: flow_month (used)" in by_id["Table:wealth.FLOW"]["detail"]
    assert "Policy: scd2-flow (SCD2;" in by_id["Table:wealth.FLOW"]["detail"]


def test_planner_matches_and_entity_read_form_dashed_links(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        entry = SimpleNamespace(id="wealth.REGION.RGN_CD", kind="column", label="region")
        trace.graph_read("match", (entry, "region", eng.g.vocabulary()))
        trace.graph_read("entity", ("wealth_region", eng.semantics.entities["wealth_region"], entry.id, entry.id))
    path = rec.path()
    assert {"Words:region", "Term:region", "Entity:wealth_region", "Table:wealth.REGION"} == {n["id"] for n in path["nodes"]}
    assert ("Term:region", "Entity:wealth_region", "matches") in {(e["from"], e["to"], e["type"]) for e in path["edges"]}
    assert all(e["planner"] == (e["type"] == "matches") for e in path["edges"])
    assert any(e["type"] == "REPRESENTS" for e in path["edges"])


@pytest.mark.parametrize("seed", [1, 3])
def test_entity_match_edges_keep_the_same_order_across_processes(seed):
    import os
    import subprocess
    import sys
    from kge.pipeline.settings import ROOT

    script = '''
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.unit.test_graph_path import engine, read_path
with TemporaryDirectory() as folder:
    eng = engine(Path(folder))
    rec = read_path(eng)
    sources = {rec.g.node("Term", term) for term in ("net-new-assets", "division")}
    rec.matches["division/term"] = sources
    rec.entity(("wealth_region", eng.semantics.entities["wealth_region"], None, "division"), True)
    print(json.dumps([e["from"] for e in rec.path()["edges"] if e["type"] == "matches"]))
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONHASHSEED": str(seed)}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["Term:division", "Term:net-new-assets"]


@pytest.mark.parametrize("certified", [False, True])
def test_runtime_sql_is_redacted_and_certification_follows_the_pack(tmp_path, certified):
    eng = engine(tmp_path)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        returned_context(eng)
        trace.graph_read("runtime", ({"runtime": {"id": "reference", "label": "Reference"},
                    "runtime_certified": certified, "sql": "SELECT FLOW.CUST_SSN FROM wealth.FLOW"}, {"passed": True}))
    runtime = next(n for n in rec.path()["nodes"] if n["type"] == "Runtime")
    assert runtime["certified"] is certified
    assert runtime["sql"] == "SELECT withheld FROM wealth.FLOW"
    assert "CUST_SSN" not in json.dumps(rec.path())


def test_a_unit_refusal_is_red_with_its_reason_and_stops_all_further_reads(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng, "retail_only")
    with trace.graph_recording(rec):
        trace.graph_read("access", ("retail_only", ["wealth"], ["wealth"]))
        before = json.dumps(rec.path())
        returned_context(eng)
        trace.graph_read("runtime", ({"runtime": {"id": "reference"}}, {"passed": True}))
    assert before == json.dumps(rec.path())
    assert {n["type"] for n in rec.path()["nodes"]} == {"Principal", "Unit"}
    edge, = rec.path()["edges"]
    assert edge["type"] == "CAN_READ" and edge["state"] == "refused" and edge["reason"]
    assert "wealth" not in before.lower() and "TRAN_FACT" not in before


def test_unreadable_unused_elements_are_omitted_without_names_or_policy_ids(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng, "wealth_only")
    with trace.graph_recording(rec):
        returned_context(eng)
        trace.graph_read("policy", ("flows", "retail.secret_ledger", {"id": "secret-policy"}))
    text = json.dumps(rec.path())
    assert "secret" not in text and "with_ledger" not in text and "flow_ledger" not in text
    assert rec.path()["unused"] == 5


def test_context_details_use_withheld_rule_and_join_ids(tmp_path):
    eng = engine(tmp_path)
    context = eng.semantics.contexts["flows"]
    context.filters = (Rule("CUST_SSN_filter", "Restricted filter", "FLOW.CUST_SSN > 0", "wealth.FLOW"),)
    context.joins = (Join("join_CUST_SSN", "key", "FLOW.CUST_SSN = REGION.RGN_CD", None, None,
                          "wealth.FLOW", "wealth.REGION", False),)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        returned_context(eng)
    text = json.dumps(rec.path())
    assert "CUST_SSN" not in text
    detail = next(n["detail"] for n in rec.path()["nodes"] if n["type"] == "Context")
    assert "Filter not used: withheld-1" in detail and "JoinRule not used: withheld-1" in detail


@pytest.mark.parametrize("failure", [QueryCancelled, RequestDeadlineExceeded])
def test_stops_and_deadlines_propagate_from_recorded_reads(tmp_path, monkeypatch, failure):
    rec = read_path(engine(tmp_path))
    def broken(*args):
        raise failure()
    monkeypatch.setattr(rec, "table", broken)
    with trace.graph_recording(rec), pytest.raises(failure):
        trace.graph_read("table", "wealth.FLOW")


def test_build_failure_leaves_the_answer_whole_with_an_empty_path(tmp_path, monkeypatch):
    eng = engine(tmp_path)
    def broken(*args):
        raise ValueError("bad read")
    monkeypatch.setattr(ReadPath, "context", broken)
    @recorded
    def ask(owner, question, principal):
        returned_context(owner)
        return {"figure": 42, "sql": "SELECT 42"}
    out = ask(eng, QUESTION, "analyst")
    assert out["figure"] == 42 and out["sql"] == "SELECT 42"
    assert out["graph_path"]["nodes"] == [] and out["graph_path"]["error"]


def test_an_edge_to_a_node_the_path_does_not_hold_is_dropped():
    g = Graphed(lambda text: text or "")
    held = g.node("Context", "flows")
    g.edge(held, "Table:wealth.NOWHERE", "USES_FACT")
    assert g.path(0)["edges"] == []


def test_the_vocabulary_reads_run_on_every_graph_store():
    """Each read Graph.vocabulary makes is in the port's Cypher subset, so the BigQuery twin runs it as Ladybug does."""
    translate = Translate(Schema.from_meta(meta_model(), 128), Namespace("proj", "kge_graph", "g_test"))
    for name, cypher in {**VOCABULARY, "startup": CALENDAR_SNAPSHOT}.items():
        assert "GRAPH_TABLE" in translate(cypher).sql, name


def vocabulary_graph(q) -> Graph:
    graph = object.__new__(Graph)
    graph.identity, graph._vocabulary, graph._snapshot_lock = {"release": "r1"}, None, threading.RLock()
    graph.q = q
    return graph


def test_vocabulary_initialises_when_absent_and_reads_once_per_release():
    calls = []

    def q(cypher, params=None):
        calls.append(cypher)
        return [{"id": "net-flows", "name": "Net Flows"}] if cypher == VOCABULARY["terms"] else []

    graph = vocabulary_graph(q)
    del graph._vocabulary
    held = graph.vocabulary()
    assert held.term_ids == {"Net Flows": "net-flows"}
    for _ in range(10):
        assert graph.vocabulary() is held
    assert len(calls) == len(VOCABULARY), "no additional graph jobs per request after the snapshot"
    graph.identity = {"release": "r2"}
    assert graph.vocabulary() is not held
    assert graph.vocabulary().release == graph.identity
    assert len(calls) == 2 * len(VOCABULARY)


@pytest.mark.parametrize("term_reads", [False, True])
def test_term_writes_drop_vocabulary_even_before_a_term_read(term_reads):
    from tests.unit.test_principal_ids import graph

    g, store = graph([])
    held = g.vocabulary()
    if term_reads:
        g.terms()
    else:
        del g._term_reads
    g.q("MERGE (t:Term {name: $name})", {"name": "Net Flows"})
    assert g._vocabulary is None
    before = len(store.sent)
    assert g.vocabulary() is not held
    assert len(store.sent) == before + len(VOCABULARY)
    g.vocabulary()
    assert len(store.sent) == before + len(VOCABULARY)


def test_a_failed_vocabulary_read_is_raised_and_never_kept():
    """A cancelled (or timed-out, or refused) read propagates, keeps nothing, and the next call reads again."""
    calls = []

    def q(cypher, params=None):
        calls.append(cypher)
        if len(calls) == 1:
            raise QueryCancelled("the request was cancelled")
        if cypher == VOCABULARY["terms"]:
            return [{"id": "net-flows", "name": "Net Flows"}]
        return []
    graph = vocabulary_graph(q)
    with pytest.raises(QueryCancelled):
        graph.vocabulary()
    assert graph._vocabulary is None
    assert graph.vocabulary().term_ids == {"Net Flows": "net-flows"}
    assert len(calls) == 1 + len(VOCABULARY)
    graph.vocabulary()
    assert len(calls) == 1 + len(VOCABULARY), "kept once read"


def test_a_type_the_graph_does_not_hold_reads_as_none_and_a_steward_term_is_named_by_its_name():
    def q(cypher, params=None):
        if cypher == VOCABULARY["golden"]:
            raise RuntimeError("Binder exception: Table Golden does not exist.")
        if cypher == VOCABULARY["terms"]:
            return [{"id": "net-flows", "name": "Net Flows"}, {"id": None, "name": "Liquidity Buffer"}]
        if cypher == VOCABULARY["acronyms"]:
            return [{"term": None, "name": "Liquidity Buffer", "acronym": "LB"}]
        return []
    held = vocabulary_graph(q).vocabulary()
    assert held.golden == {}
    assert held.term_ids == {"Net Flows": "net-flows", "Liquidity Buffer": "Liquidity Buffer"}
    assert held.acronyms == {"LB": ("Liquidity Buffer",)}



@pytest.mark.parametrize("adapter", ["ladybug", "bigquery"])
def test_missing_graph_types_are_classified_by_the_store_port(adapter, monkeypatch):
    from kge.adapters.graphstore import store
    monkeypatch.setenv("GRAPH_STORE", adapter)
    assert store().missing_type(RuntimeError("Binder exception: Table Golden does not exist."))
    assert not store().missing_type(RuntimeError("Binder exception: Cannot find property item for Golden."))
    assert not store().missing_type(QueryCancelled("Table Golden does not exist."))
    assert not store().missing_type(RequestDeadlineExceeded())


def test_a_pack_without_golden_reads_none_through_the_bigquery_port(monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    schema = Schema.from_meta(meta_model(), 128)
    del schema.nodes["Golden"]
    translate = Translate(schema, Namespace("proj", "kge_graph", "g_test"))
    def q(cypher, params=None):
        translate(cypher)
        return []
    assert vocabulary_graph(q).vocabulary().golden == {}


def test_repeated_reads_still_record_returned_ids_without_duplicate_stream_events(tmp_path):
    from kge.services.ask.engine.runtrace import start
    events = []
    rec = read_path(engine(tmp_path), events=events)
    with start("ask") as run, trace.graph_recording(rec):
        trace.graph_read("table", "wealth.FLOW")
        trace.graph_read("table", "wealth.FLOW")
        assert [n["id"] for n in rec.path()["nodes"]] == ["Table:wealth.FLOW"]
        assert not hasattr(run, "graph_reads")
    assert len(events) == 1


def test_synthetic_contexts_measures_and_flag_rules_are_not_stored_graph_nodes(tmp_path):
    eng = engine(tmp_path)
    eng.semantics.context_ids, eng.semantics.measure_ids, eng.semantics.graph_rule_ids = frozenset(), frozenset(), frozenset()
    eng.semantics.declared = {}
    rec = read_path(eng)
    with trace.graph_recording(rec):
        returned_context(eng)
    assert {n["id"] for n in rec.path()["nodes"]} == {"Table:wealth.FLOW"}


def test_compiler_consumption_points_record_used_rules_and_joins(tmp_path):
    from kge.semantics.query import Asked, Plan
    eng = engine(tmp_path)
    eng.semantics.graph_vocabulary = eng.g.vocabulary()
    context = eng.semantics.contexts["flows"]
    rec = read_path(eng)
    with trace.graph_recording(rec):
        plan = Plan(eng.semantics, context, Asked())
        plan.fact = plan.name(context.fact)
        plan.filter(context.filters[0], plan.outer())
        plan.selector(context.selectors[0], plan.outer())
        plan.add_join(context.joins[0])
    used = {n["id"] for n in rec.path()["nodes"] if n["used"]}
    assert {"Filter:released", "RowSelector:not_test", "Table:wealth.MONTH"} <= used
    assert "Filter:large" not in used
    assert {e["label"] for e in rec.path()["edges"] if e["type"] == "JoinRule" and e["used"]} == {"flow_month"}


def test_golden_nodes_keep_stored_ids_for_questions_outside_the_golden_set(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng, question="Net flows in another period")
    with trace.graph_recording(rec):
        returned_context(eng)
    assert {n["id"] for n in rec.path()["nodes"] if n["type"] == "Golden"} == {
        "Golden:golden_x_v2:X01", "Golden:golden_x_v2:X02"}


def test_startup_calendar_read_uses_one_job_and_keeps_the_returned_vocabulary():
    calls = []
    def q(cypher):
        calls.append(cypher)
        return [{"id": "fiscal_april", "start": 4, "label": "FY{yy}-Q{n}", "term_id": "net-flows", "term": "Net Flows", "acronym": "NF", "measure": "wealth.net-flows",
                 "calendar": "fiscal_april", "context": "flows", "golden": "golden_x_v2:X01", "item": "X01",
                 "kind": "CERTIFIED_BY"}] * 2
    graph = vocabulary_graph(q)
    assert graph.calendar_snapshot() == {"fiscal_april": {"year_start_month": 4, "quarter_label": "FY{yy}-Q{n}"}}
    assert calls == [CALENDAR_SNAPSHOT]
    assert graph.vocabulary().term_ids == {"Net Flows": "net-flows"}
    assert graph.vocabulary().acronyms == {"NF": ("net-flows",)}
    assert len(graph.vocabulary().golden["flows"]) == 1
    assert len(calls) == 1


def test_startup_golden_paths_compare_equal_across_hash_seeds():
    """Rows tied on the startup snapshot's calendar id may arrive in any order."""
    import os
    import subprocess
    import sys

    from kge.eval.stages import compare
    from kge.pipeline.settings import ROOT

    script = '''
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from kge.pipeline import trace
from tests.unit.test_graph_path import engine, read_path, returned_context, vocabulary_graph
with TemporaryDirectory() as folder:
    eng = engine(Path(folder))
    def q(cypher):
        return [{"id": "fiscal_april", "start": 4, "label": "FY{yy}-Q{n}",
                 "term_id": "net-flows", "term": "Net Flows", "acronym": "NF",
                 "measure": "wealth.net-flows", "calendar": "fiscal_april", "context": "flows",
                 "golden": "golden_x_v2:" + item, "item": item, "kind": "CERTIFIED_BY"}
                for item in {"X01", "X02", "X03"}]
    graph = vocabulary_graph(q)
    graph.calendar_snapshot()
    eng.g.vocabulary = graph.vocabulary
    rec = read_path(eng, question="Net flows outside the golden set")
    with trace.graph_recording(rec):
        returned_context(eng)
    print(json.dumps({"answer": {"graph_path": rec.path()}, "retrieved": []}))
'''
    evidence = []
    for seed in (0, 1):
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, "PYTHONHASHSEED": str(seed), "MODEL_PROVIDER": "none"}, timeout=30)
        assert result.returncode == 0, result.stderr
        evidence.append(json.loads(result.stdout))
    record = {"set": "heldout", "id": "outside-golden", "arm": "treatment", "principal": "analyst",
              "repeat": 1, "ok": True}
    before = {"records": [{**record, "deterministic": evidence[0]}], "must_pass": []}
    assert compare(before, [{**record, "deterministic": evidence[1]}])["passed"], evidence
    for held in evidence:
        assert [n["id"] for n in held["answer"]["graph_path"]["nodes"] if n["type"] == "Golden"] == [
            "Golden:golden_x_v2:X01", "Golden:golden_x_v2:X02", "Golden:golden_x_v2:X03"]


def test_calendar_projection_is_retained_without_another_store_job():
    from kge.adapters.graphstore.bigquery import BigQueryStore, Connection
    schema = Schema.from_meta(meta_model(), 128)
    schema.mutable.add("Term")
    translate = Translate(schema, Namespace("proj", "kge_graph", "g_test"))
    graph = SimpleNamespace(schema=schema, translate=lambda: translate, kept={}, lock=threading.RLock(),
                            client=SimpleNamespace(query=lambda *args: pytest.fail("unexpected store job")))
    conn = Connection(graph)
    query = "MATCH (c:Calendar) RETURN c.id AS id, c.year_start_month AS start, c.quarter_label AS label ORDER BY c.id"
    store = BigQueryStore()
    store.remember_read(conn, query, [{"id": "fiscal_april", "start": 4, "label": "FY{yy}-Q{n}"}])
    assert conn.execute(query).get_all() == [["fiscal_april", 4, "FY{yy}-Q{n}"]]
    assert conn.execute("MATCH (c:Calendar) RETURN c.id, c.year_start_month, c.quarter_label ORDER BY c.id").get_all() == [
        ["fiscal_april", 4, "FY{yy}-Q{n}"]]
    with pytest.raises(ValueError, match="immutable"):
        store.remember_read(conn, "MATCH (t:Term) RETURN t.name", [{"t.name": "Net Flows"}])


def test_access_records_use_stored_role_principals_for_the_signed_in_viewer(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        trace.graph_read("principal_read", ("analyst", {"wealth", "retail"}, {"wealth_reader": {"wealth"}, "retail_reader": {"retail"}}))
        trace.graph_read("access", ("analyst", ["wealth"], []))
    assert {n["id"] for n in rec.path()["nodes"] if n["type"] == "Principal"} == {
        "Principal:wealth_reader", "Principal:retail_reader"}
    assert {(e["from"], e["to"]) for e in rec.path()["edges"] if e["type"] == "CAN_READ"} == {
        ("Principal:wealth_reader", "Unit:wealth")}


def test_restricted_column_names_in_hyphenated_ids_and_labels_are_withheld(tmp_path):
    eng = engine(tmp_path)
    context = eng.semantics.contexts["flows"]
    context.filters = (Rule("rule-CUST_SSN-filter", "rule-CUST_SSN-filter", "FLOW.CUST_SSN > 0", "wealth.FLOW"),)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        returned_context(eng)
    assert "CUST_SSN" not in json.dumps(rec.path())
    assert any(n["id"] == "Filter:withheld-1" for n in rec.path()["nodes"])


def test_stream_events_publish_after_the_recorder_adds_the_read(tmp_path):
    seen = []
    def emit(event):
        assert event["node"]["id"] in {n["id"] for n in rec.path()["nodes"]}
        seen.append(event)
    with trace.graph_stream(emit):
        rec = ReadPath(engine(tmp_path), "analyst", QUESTION)
    with trace.graph_recording(rec):
        trace.graph_read("table", "wealth.FLOW")
    assert len(seen) == 1
    assert "_events" not in seen[0] and "_emit" not in seen[0]


def test_recorder_names_policy_and_join_ids_while_the_path_uses_badges_and_edges(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        returned_context(eng)
        assert "JoinRule:flow_month" in rec.record("join", ("flows", eng.semantics.contexts["flows"].joins[0]))["nodes"]
        assert "Policy:scd2-flow" in rec.record("policy", ("flows", "wealth.FLOW", eng.semantics.scd2["wealth.FLOW"]))["nodes"]
    assert not any(n["type"] in ("JoinRule", "Policy") for n in rec.path()["nodes"])


def test_a_recorded_curated_table_read_is_shown_withheld_and_keeps_its_used_join(tmp_path):
    eng = engine(tmp_path)
    catalogue = eng.g.catalogue()
    catalogue.columns["xref.link_table"] = ["LINK_SECRET"]
    eng.g.catalogue = lambda: catalogue
    rec = read_path(eng)
    join = Join("certified_link", "xref", "link_table.LINK_SECRET = FLOW.ACCT_ID", None, "xref.link_table",
                "wealth.FLOW", "retail.secret_ledger", False)
    with trace.graph_recording(rec):
        trace.graph_read("table", "xref.link_table")
        trace.graph_read("policy", ("flows", "xref.link_table", {"id": "policy-LINK_SECRET", "is_current": "LINK_SECRET"}))
        trace.graph_read("join", ("flows", join))
    path = rec.path()
    hidden = next(n for n in path["nodes"] if n["type"] == "Table" and n["name"] == "withheld")
    assert hidden["used"] and hidden["badges"] == [] and hidden["detail"] == "Table: withheld"
    assert any(e["type"] == "JoinRule" and e["used"] for e in path["edges"])
    assert "LINK_SECRET" not in json.dumps(path) and "xref.link_table" not in json.dumps(path)


def test_concurrent_reads_keep_record_and_stream_order_together(tmp_path):
    from contextvars import copy_context
    publishing, release, second_started, second_done = (threading.Event() for _ in range(4))
    events = []
    def emit(event):
        if event["node"]["id"] == "Table:wealth.FLOW":
            publishing.set()
            assert release.wait(2)
        events.append(event)
    with trace.graph_stream(emit):
        rec = ReadPath(engine(tmp_path), "analyst", QUESTION)
    with trace.graph_recording(rec):
        first_context, second_context = copy_context(), copy_context()
        first = threading.Thread(target=lambda: first_context.run(trace.graph_read, "table", "wealth.FLOW"))
        def second_read():
            second_started.set()
            second_context.run(trace.graph_read, "table", "wealth.MONTH")
            second_done.set()
        second = threading.Thread(target=second_read)
        first.start()
        assert publishing.wait(2)
        second.start()
        try:
            assert second_started.wait(2)
            assert not second_done.wait(.05)
            assert [n["id"] for n in rec.path()["nodes"]] == ["Table:wealth.FLOW"]
        finally:
            release.set()
            first.join(2)
            second.join(2)
        assert not first.is_alive() and not second.is_alive()
        assert [n["id"] for n in rec.path()["nodes"]] == ["Table:wealth.FLOW", "Table:wealth.MONTH"]
    assert [e["node"]["id"] for e in events] == ["Table:wealth.FLOW", "Table:wealth.MONTH"]


@pytest.mark.parametrize('gate', ['request', 'plan'])
def test_ledger_matches_are_recorded_before_a_request_or_plan_gate_refuses(tmp_path, monkeypatch, gate):
    from kge.semantics import plancheck
    eng, events = engine(tmp_path), []
    entries = {i: SimpleNamespace(id=i, kind=k, label=label) for i, k, label in [
        ('flows', 'context', 'Net Flows'), ('wealth.net-flows', 'measure', 'Net Flows'),
        ('fiscal_april', 'calendar', 'Fiscal calendar')]}
    eng.semantics.graph_vocabulary = eng.g.vocabulary()
    vocab = SimpleNamespace(entries=entries, semantics=eng.semantics, contexts=eng.semantics.contexts,
                            periods={'wealth.FLOW': {'calendar': 'fiscal_april'}},
                            calendars={'fiscal_april': {'quarter_label': 'FY{yy}-Q{n}'}}, denial=lambda text: True)
    pieces = [SimpleNamespace(kind='name', candidates=('flows', 'wealth.net-flows'), text='Net flows', operation=None),
              SimpleNamespace(kind='time', candidates=(), text='FY26-Q1', operation=None, value=SimpleNamespace(kind='fiscal_quarter'))]
    monkeypatch.setattr(plancheck, 'ledger', lambda *args: pieces)
    rec = read_path(eng, events=events)
    with trace.graph_recording(rec):
        decision = (plancheck.check_request(vocab, QUESTION) if gate == 'request'
                    else plancheck.check_plan(None, vocab, QUESTION))
        assert decision.kind == 'refuse'
        path = rec.path()
        assert {('Words:Net flows', 'Context:flows'), ('Words:Net flows', 'Measure:wealth.net-flows'),
                ('Words:FY26-Q1', 'Calendar:fiscal_april')} <= {(e['from'], e['to']) for e in path['edges']}
        assert all(e['planner'] and e['type'] == 'matches' for e in path['edges'])
        assert not any(n['type'] in ('Table', 'Filter', 'Runtime') for n in path['nodes'])
        trace.graph_read('plan_refusal', ({'kind': decision.kind, 'missing': list(decision.missing)}, 'query', 'plan', None))
    assert events[-1]['edge']['state'] == 'refused'
    assert events[-1]['edge']['reason'] == decision.missing[0]


def test_a_typed_plan_refusal_is_redacted_terminal_and_streamed_with_its_message(tmp_path):
    eng, events = engine(tmp_path), []
    rec = read_path(eng, 'wealth_only', events=events)
    with trace.graph_recording(rec):
        trace.graph_read('plan_refusal', ({'kind': 'abstain', 'missing': [
            'rule-CUST_SSN-filter uses retail.secret_ledger.ledger_code']}, 'model', 'plan', None))
        before = json.dumps(rec.path())
        returned_context(eng)
        trace.graph_read('runtime', ({'runtime': {'id': 'reference'}}, {'passed': True}))
    assert before == json.dumps(rec.path())
    assert {n['type'] for n in rec.path()['nodes']} == {'Plan', 'Check'}
    edge, = rec.path()['edges']
    assert edge['state'] == 'refused' and edge['to'] == 'Check:model/plan'
    assert edge['reason'] == edge['detail'] == 'rule-withheld-filter uses withheld'
    assert not any(token in before for token in ('CUST_SSN', 'secret_ledger', 'ledger_code'))
    assert events[-1] == {'event': 'edge_added', 'edge': edge}


def test_a_rules_replacement_continues_beside_the_refused_model_branch(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng)
    with trace.graph_recording(rec):
        trace.graph_read('plan_refusal', ({'kind': 'clarify', 'missing': []}, 'model', 'plan', 'rules'))
        trace.graph_read('table', 'wealth.FLOW')
    gate = next(n for n in rec.path()['nodes'] if n['type'] == 'Check')
    assert gate['state'] == 'refused' and gate['reason'] == 'Choose the intended declared meaning.'
    assert any(n['id'] == 'Table:wealth.FLOW' for n in rec.path()['nodes'])
    assert not any(e['from'] == gate['id'] for e in rec.path()['edges'])
    assert 'Replacement: rules plan' in next(n['detail'] for n in rec.path()['nodes'] if n['type'] == 'Plan')


def test_repeated_model_refusals_update_and_stream_redacted_reason_and_detail(tmp_path):
    eng, events = engine(tmp_path), []
    rec = read_path(eng, 'wealth_only', events=events)
    with trace.graph_recording(rec):
        trace.graph_read('plan_refusal', ({'kind': 'abstain', 'missing': ['First CUST_SSN']}, 'model', 'plan', None))
        first, = rec.path()['edges']
        assert first['reason'] == first['detail'] == 'First withheld'
        assert events[-1] == {'event': 'edge_added', 'edge': first}
        trace.graph_read('plan_start', None)
        trace.graph_read('plan_refusal', ({'kind': 'clarify', 'missing': [
            'Second retail.secret_ledger.ledger_code']}, 'model', 'plan', None))
    second, = rec.path()['edges']
    assert second['reason'] == second['detail'] == 'Second withheld'
    assert events[-1] == {'event': 'edge_added', 'edge': second}
    gate = next(n for n in rec.path()['nodes'] if n['id'] == 'Check:model/plan')
    assert gate['reason'] == 'Second withheld' and gate['detail'] == 'Plan check: clarify\nSecond withheld'
    assert not any(token in json.dumps({'path': rec.path(), 'events': events})
                   for token in ('CUST_SSN', 'secret_ledger', 'ledger_code'))


def test_replacement_preflight_does_not_publish_unexecuted_graph_reads(tmp_path):
    rec = read_path(engine(tmp_path))
    with trace.graph_recording(rec):
        trace.graph_read('words', 'Net flows')
        with trace.graph_recording(None):
            trace.graph_read('table', 'wealth.FLOW')
        trace.graph_read('plan_refusal', ({'kind': 'abstain', 'missing': ['Not released.']}, 'model', 'plan', None))
    assert not any(n['type'] == 'Table' for n in rec.path()['nodes'])
    assert rec.stopped


def test_lexical_matches_never_promote_synthetic_contexts_or_measures(tmp_path):
    eng = engine(tmp_path)
    eng.semantics.context_ids = eng.semantics.measure_ids = frozenset()
    rec = read_path(eng)
    with trace.graph_recording(rec):
        for kind, identifier in [('context', 'flows'), ('measure', 'wealth.net-flows')]:
            trace.graph_read('match', (SimpleNamespace(id=identifier, kind=kind, label='Net Flows'), 'Net flows', eng.g.vocabulary()))
    assert not any(n['type'] in ('Context', 'Measure') for n in rec.path()['nodes'])


def test_a_new_plan_attempt_records_its_path_after_a_scoped_plan_refusal(tmp_path):
    eng, events = engine(tmp_path), []
    rec = read_path(eng, events=events)
    with trace.graph_recording(rec):
        trace.graph_read('plan_refusal', ({'kind': 'abstain', 'missing': ['Unavailable in the page unit.']}, 'query', 'plan', None))
        trace.graph_read('table', 'retail.refused_attempt')
        assert rec.stopped
        trace.graph_read('plan_start', None)
        returned_context(eng)
        trace.graph_read('runtime', ({'runtime': {'id': 'reference'}}, {'passed': True}))
    path = rec.path()
    assert not rec.stopped
    assert {'Context:flows', 'Measure:wealth.net-flows', 'Table:wealth.FLOW', 'Runtime:reference'} <= {
        n['id'] for n in path['nodes'] if n['used']}
    assert not any(n['id'] == 'Table:retail.refused_attempt' for n in path['nodes'])
    refused, = [e for e in path['edges'] if e['state'] == 'refused']
    assert refused['reason'] == 'Unavailable in the page unit.'
    assert not any(e['from'] == refused['to'] for e in path['edges'])
    assert any(e['event'] == 'node_added' and e['node']['id'] == 'Runtime:reference' for e in events)


def test_a_new_plan_attempt_cannot_resume_recording_after_an_access_denial(tmp_path):
    eng = engine(tmp_path)
    rec = read_path(eng, 'retail_only')
    with trace.graph_recording(rec):
        trace.graph_read('access', ('retail_only', ['wealth'], ['wealth']))
        before = json.dumps(rec.path())
        trace.graph_read('plan_start', None)
        returned_context(eng)
        trace.graph_read('runtime', ({'runtime': {'id': 'reference'}}, {'passed': True}))
    assert rec.stopped and rec.access_stopped
    assert json.dumps(rec.path()) == before
