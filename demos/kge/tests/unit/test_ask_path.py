"""Slice 1 (MAR-163 / #361): structured query path in Ask responses and Graph tab contract."""
from __future__ import annotations

import pytest

from kge.pipeline.profile import resolve
from kge.services.ask import service
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import Engine, PRESETS
from kge.services.ask.engine.path import SPINE_ORDER, build_path


def test_path_nodes_and_edges_on_meridian_and_compact_omission():
    prof = resolve("meridian")
    eng = Engine.serving(prof)
    try:
        who = "analyst_retail.meridian@example.com"
        q = "Total retail revenue for June 2025"
        comb = Combined(eng, llm=None)
        ag = Agent(eng, llm=None)

        ans = service.answer(eng, prof, q, who, "analysis", "graph", PRESETS["graph"])
        ask_full = service.ask(comb, prof, q, who, "analysis", "graph", PRESETS["graph"], compact=False)
        ask_compact = service.ask(comb, prof, q, who, "analysis", "graph", PRESETS["graph"], compact=True)
        agent_out = ag.ask(q, who, "analysis", arm="treatment")
        agent_out["path"] = build_path(eng, agent_out)

        assert "path" not in ask_compact
        for payload in (ans, ask_full, agent_out):
            path = payload.get("path")
            assert isinstance(path, dict)
            nodes = path["nodes"]
            edges = path["edges"]
            assert [n["id"] for n in nodes] == list(SPINE_ORDER)
            for node in nodes:
                for key in ("id", "kind", "label", "state", "time", "detail"):
                    assert key in node
                assert node["state"] == "done"
                if node["id"] != "figure":
                    assert node["time"] is not None
            by_id = {n["id"]: n for n in nodes}
            assert payload["sql"] in by_id["query"]["detail"]
            edge_pairs = {(e["from"], e["to"]): e["label"] for e in edges}
            assert ("question", "terms") in edge_pairs
            assert ("terms", "measure") in edge_pairs
            assert ("measure", "tables") in edge_pairs
            assert ("tables", "runtime") in edge_pairs
            assert ("access", "runtime") in edge_pairs
            assert ("runtime", "query") in edge_pairs
            assert "query & cost checks" in edge_pairs[("runtime", "query")]
            assert ("query", "figure") in edge_pairs
    finally:
        eng.close()


def test_path_refused_access_marks_access_red_and_following_nodes_waiting():
    prof = resolve("meridian")
    eng = Engine.serving(prof)
    try:
        who = "analyst_retail.meridian@example.com"
        out = service.answer(
            eng,
            prof,
            "What was wealth revenue in June 2025?",
            who,
            "analysis",
            "graph",
            PRESETS["graph"],
        )
        assert out["behaviour"] in ("refuse", "blocked_access")
        nodes = out["path"]["nodes"]
        by_id = {n["id"]: n for n in nodes}
        assert by_id["access"]["state"] == "refused"
        assert by_id["access"].get("reason")
        assert by_id["measure"]["label"] == "withheld"
        assert by_id["tables"]["label"] == "withheld"
        for after_id in ("runtime", "query", "figure"):
            assert by_id[after_id]["state"] == "waiting"
    finally:
        eng.close()


def test_path_withholds_restricted_and_unreadable_columns():
    prof = resolve("meridian")
    eng = Engine.serving(prof)
    try:
        who = "analyst_retail.meridian@example.com"
        out = service.answer(
            eng,
            prof,
            "What are the email addresses of retail customers?",
            who,
            "analysis",
            "graph",
            PRESETS["graph"],
        )
        assert out["behaviour"] == "refuse"
        path_str = str(out["path"])
        assert "email" not in path_str.lower() or "withheld" in path_str
        by_id = {n["id"]: n for n in out["path"]["nodes"]}
        assert by_id["measure"]["label"] == "withheld"
        assert by_id["tables"]["label"] == "withheld"
        assert by_id["access"]["state"] == "refused"
    finally:
        eng.close()


def test_path_includes_certified_runtime_and_skipped_runtimes():
    synthetic = {
        "question": "What was CS net new assets for HNWI clients in Q1 2025?",
        "principal": "cs_nna_steward",
        "purpose": "analysis",
        "arm": "graph",
        "behaviour": "figure",
        "figure": 1250000.0,
        "unit": "cs",
        "column": "fact_flows.net_new_assets_chf",
        "sql": "SELECT SUM(net_new_assets_chf) FROM fact_flows JOIN dim_client USING (client_id)",
        "reference_sql": "SELECT SUM(net_new_assets_chf) FROM fact_flows JOIN dim_client USING (client_id)",
        "guard": {"passed": True, "checks": ["read-only", "tables", "columns", "joins", "cost"]},
        "runtime": {
            "id": "nna_app_runtime",
            "label": "CS NNA BigQuery app runtime (9 tables)",
            "kind": "external",
            "dialect": "bigquery",
        },
        "runtime_certified": True,
        "certified_in": "nna_app_runtime",
        "physical_tables": ["fact_flows", "dim_client"],
        "_skipped_runtimes": [
            {
                "id": "nna_shadow_runtime",
                "label": "CS NNA BigQuery shadow runtime (6 tables)",
                "reason": "table cs.dim_client has no IMPLEMENTS mapping in runtime nna_shadow_runtime",
            }
        ],
        "trace": [{"step": "parse", "term": "net new assets", "period": "2025-Q1", "units": ["cs"]}],
    }
    path = build_path(None, synthetic)
    by_id = {n["id"]: n for n in path["nodes"]}
    assert by_id["runtime"]["state"] == "done"
    assert by_id["runtime"]["certified"] is True
    assert by_id["figure"]["certified"] is True
    assert "runtime:nna_shadow_runtime" in by_id
    skipped = by_id["runtime:nna_shadow_runtime"]
    assert skipped["state"] == "skipped"
    assert "dim_client" in skipped["reason"]
    assert any(e["from"] == "tables" and e["to"] == "runtime:nna_shadow_runtime" and e["label"] == "skipped" for e in path["edges"])


def test_golden_suggestions_meta_includes_question_units_glossary_and_retention(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    import pytest
    from kge.pipeline.settings import past_questions_retention_days
    from kge.services.quality import service as quality_service

    estate_dir = tmp_path / "estate"
    estate_dir.mkdir(parents=True, exist_ok=True)
    (estate_dir / "golden.json").write_text(json.dumps({
        "name": "golden",
        "version": "v1",
        "items": [
            {"id": "q1", "question": "Total retail revenue for June 2025", "expect": {"unit": "retail"}},
            {"id": "q2", "question": "Wealth Net New Assets for Q1 2025", "expect": {"unit": "wealth"}},
        ],
    }))
    fake_prof = SimpleNamespace(estate=lambda: estate_dir)
    fake_eng = SimpleNamespace(
        UNIT_ORDER=["retail", "corporate", "wealth"],
        term_names=["Net New Assets", "Total Revenue"],
        ALIASES={},
        UNIT_WORDS={"retail": "retail", "wealth": "wealth", "corporate": "corporate"},
        semantics=SimpleNamespace(
            contexts_of=lambda t: [SimpleNamespace(unit="wealth" if "Net New" in t else "retail")]
        ),
        g=SimpleNamespace(
            q=lambda query: (
                [{"name": "Net New Assets", "unit": "wealth"}, {"name": "Total Revenue", "unit": "retail"}]
                if "MAPS_TO" in query
                else [{"name": "Net New Assets"}, {"name": "Total Revenue"}]
            ),
        ),
    )
    monkeypatch.setenv("PAST_QUESTIONS_RETENTION_DAYS", "14")
    meta = quality_service.suggestions_meta(fake_prof, fake_eng)
    assert meta["retention_days"] == 14
    assert meta["question_units"] == {
        "Total retail revenue for June 2025": ["retail"],
        "Wealth Net New Assets for Q1 2025": ["wealth"],
    }
    assert meta["unreadable_questions"] == set()
    assert isinstance(meta["glossary"], list)
    assert len(meta["glossary"]) == 2
    assert all("term" in g and isinstance(g["units"], list) for g in meta["glossary"])

    meta_retail = quality_service.suggestions_meta(fake_prof, fake_eng, readable_units=["retail"])
    assert meta_retail["question_units"] == {"Total retail revenue for June 2025": ["retail"]}
    assert meta_retail["unreadable_questions"] == {"Wealth Net New Assets for Q1 2025"}
    assert [g["term"] for g in meta_retail["glossary"]] == ["Total Revenue"]

    for bad in ("abc", "-1"):
        monkeypatch.setenv("PAST_QUESTIONS_RETENTION_DAYS", bad)
        with pytest.raises(ValueError, match="PAST_QUESTIONS_RETENTION_DAYS must be a whole number at least 0"):
            past_questions_retention_days()


@pytest.mark.parametrize("kind,definitional,expect,readable,visible", [
    ("shelf", False, {}, ["retail"], True),
    ("definition", False, {}, ["retail"], True),
    ("within", True, {}, ["retail"], False),
    ("within", False, {}, ["retail"], False),
    ("figure", True, {}, ["retail"], False),
    ("cross", True, {}, ["retail"], False),
    ("cross", False, {}, ["retail"], False),
    ("point", True, {}, ["retail"], False),
    (None, True, {}, ["retail"], False),
    ("shelf", True, {"figure": 0}, ["retail"], False),
    ("definition", True, {"figures": {"retail": 50, "sme": 50}}, ["retail"], False),
    ("shelf", True, {"rows": {"total": 100}}, ["retail"], False),
    ("shelf", True, {"parts": {"retail": 50, "sme": 50}}, ["retail"], False),
    ("shelf", True, {"answer": 100}, ["retail"], False),
    ("shelf", True, {"answer": "1e2"}, ["retail"], False),
    ("shelf", True, {"behaviour": "figure"}, ["retail"], False),
    ("shelf", True, {"no_figure": True}, ["retail"], True),
    ("shelf", True, {}, ["wealth"], False),
    ("shelf", True, {}, [], False),
    ("within", False, {"figure": 100}, ["retail", "sme"], True),
])
def test_golden_suggestions_require_any_definition_unit_but_every_figure_or_cross_unit(
    monkeypatch, tmp_path, kind, definitional, expect, readable, visible,
):
    import json
    from types import SimpleNamespace
    from kge.services.quality import service as quality_service

    question = "A question shared by two units"
    (tmp_path / "golden.json").write_text(json.dumps({"version": "v1", "items": [
        {"id": "shared", "question": question, "kind": kind, "expect": {"units": ["retail", "sme"], **expect}},
    ]}))
    monkeypatch.setattr(quality_service, "parse", lambda *args: SimpleNamespace(
        units=[], terms_found=[], term=None, definitional=definitional,
    ))
    engine = SimpleNamespace(UNIT_ORDER=["retail", "sme", "wealth"], term_names=[],
                             g=SimpleNamespace(q=lambda query: []))
    meta = quality_service.suggestions_meta(SimpleNamespace(estate=lambda: tmp_path), engine, readable)
    assert (question in meta["question_units"]) is visible
    assert (question in meta["unreadable_questions"]) is (not visible)
    if visible:
        assert meta["question_units"][question] == ["retail", "sme"]

def test_ask_stream_ndjson_events_and_cancellation():
    import threading
    import pytest
    from kge.pipeline.trace import QueryCancelled, cancellable, check_cancelled, register_cancel_hook, trigger_cancel
    from kge.services.ask.engine import runtrace
    from kge.services.ask.engine.path import PathStreamRecorder

    events: list[dict] = []
    rec = PathStreamRecorder("Total wealth NNA for Q1 2025", events.append)
    rec.emit_initial()

    def _run():
        for st in ("understand", "resolve", "access", "plan", "gate", "execute"):
            runtrace.stage(st)
        return {
            "question": "Total wealth NNA for Q1 2025",
            "principal": "analyst@example.com",
            "purpose": "analysis",
            "arm": "graph",
            "behaviour": "figure",
            "figure": 42000.0,
            "unit": "wealth",
            "column": "fact_flows.nna_usd",
            "sql": "SELECT SUM(nna_usd) FROM wealth_fact_flows",
            "guard": {"passed": True, "checks": ["read-only", "cost"]},
            "trace": [{"step": "parse", "term": "Net New Assets", "period": "2025-Q1", "units": ["wealth"]}],
        }

    out = runtrace.run("ask", _run, graph=None, on_stage=rec.on_stage)
    out["run_trace"]["stages"] = [
        {"stage": "understand", "ms": 14.0},
        {"stage": "resolve", "ms": 92.0},
        {"stage": "access", "ms": 56.0},
        {"stage": "plan", "ms": 23.0},
        {"stage": "gate", "ms": 11.0},
        {"stage": "execute", "ms": 34.0},
        {"stage": "render", "ms": 18.0},
    ]
    out["path"] = build_path(None, out)
    rec.emit_final(out)

    event_types = [ev["event"] for ev in events]
    assert "node_added" in event_types
    assert "edge_added" in event_types
    assert "state_changed" in event_types
    assert event_types[-1] == "answer"
    final_answer = events[-1]["answer"]
    assert final_answer["behaviour"] == "figure"
    nodes_by_id = {n["id"]: n for n in final_answer["path"]["nodes"]}
    assert nodes_by_id["question"]["time"] == "14 ms"
    assert nodes_by_id["terms"]["time"] == "92 ms"
    assert nodes_by_id["access"]["time"] == "56 ms"
    assert nodes_by_id["query"]["time"] == "45 ms"

    ev = threading.Event()
    hook_called = []
    with pytest.raises(QueryCancelled):
        with cancellable(ev) as (_e, hooks):
            register_cancel_hook(lambda: hook_called.append(True))
            trigger_cancel(ev, hooks)
            check_cancelled()
    assert hook_called == [True]
    assert hooks == []

    from kge.adapters.bigquery import BigQueryLoader
    loader = BigQueryLoader(project="test-proj")
    loader.token = lambda: ("tok", "test-proj")
    posted_urls: list[str] = []
    cancelled_jobs: list[str] = []

    def fake_post(url, _headers, body, timeout=120):
        posted_urls.append(url)
        if url.endswith("/jobs"):
            return {
                "jobReference": {**body["jobReference"], "location": "us-central1"},
                "status": {"state": "RUNNING"},
            }
        if "/cancel" in url:
            cancelled_jobs.append(url)
            return {}
        return {}

    def fake_get(url, _headers, timeout=120):
        trigger_cancel(ev2, hooks2)
        return {"jobComplete": False}

    loader.post = fake_post
    loader.get = fake_get
    ev2 = threading.Event()
    with pytest.raises(QueryCancelled):
        with cancellable(ev2) as (_e2, hooks2):
            loader.run("SELECT 1")
    assert any(u.endswith("/jobs") for u in posted_urls)
    assert len(cancelled_jobs) == 1
    assert "location=us-central1" in cancelled_jobs[0]


def test_golden_figure_with_definitional_opening_needs_all_units_with_real_parser(eng, tmp_path):
    import json
    from dataclasses import replace
    from kge.eval import sets
    from kge.services.ask import parse
    from kge.services.quality import service as quality_service

    profile = resolve("meridian")
    question = "What is the latest retail and SME revenue?"
    item = {"id": "figure", "kind": "figure", "question": question,
            "expect": {"figure": 100, "units": ["retail", "sme"]}}
    definitions = [i for i in sets.items(profile.estate() / "golden.json") if i["id"] in ("G031", "G036", "G040")]
    assert len(definitions) == 3
    (tmp_path / "golden.json").write_text(json.dumps([item, *definitions]))
    req = parse(question, eng.term_names, eng)
    assert req.definitional and set(req.units) == {"retail", "sme"}

    retail = eng.g.readable_units("analyst_retail")
    assert set(retail) == {"retail"}
    profile = replace(profile, warehouses=tmp_path / "warehouses")
    meta = quality_service.suggestions_meta(profile, eng, retail)
    assert question not in meta["question_units"]
    assert question in meta["unreadable_questions"]
    assert {i["question"] for i in definitions} <= meta["question_units"].keys()

    meta = quality_service.suggestions_meta(profile, eng, ["retail", "sme"])
    assert meta["question_units"][question] == ["retail", "sme"]


@pytest.mark.parametrize("disconnected", [True, False])
def test_stream_disconnect_cancels_once_and_completion_never_cancels(monkeypatch, disconnected):
    import asyncio
    from threading import Event
    from types import SimpleNamespace
    from kge.pipeline.trace import check_cancelled
    from kge.services.ask import routes, service

    started, stopped, release = Event(), Event(), Event()
    cancellations = []
    original = service.trigger_cancel

    def cancel(event, hooks):
        cancellations.append(True)
        original(event, hooks)
        release.set()

    def compute(on_stage):
        started.set()
        try:
            if disconnected:
                assert release.wait(2)
                check_cancelled()
            return {"question": "probe", "behaviour": "figure", "figure": 1}
        finally:
            stopped.set()

    async def is_disconnected():
        return disconnected

    monkeypatch.setattr(service, "trigger_cancel", cancel)
    response = routes._stream_response(SimpleNamespace(is_disconnected=is_disconnected), "probe", compute)
    assert started.wait(1)

    async def read():
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(read())
    assert stopped.wait(1)
    assert len(cancellations) == int(disconnected)
    assert any(b'"event": "answer"' in chunk for chunk in chunks) is not disconnected


@pytest.mark.parametrize("stages,expected", [({}, None), ({"execute": 12}, None), ({"render": 18}, "18 ms"),
                                             ({"phrase": 0}, "0.0 ms")])
def test_figure_time_requires_a_recorded_stage(stages, expected):
    answer = {"question": "probe", "behaviour": "figure", "figure": 1, "_stage_ms": stages}
    figure = next(node for node in build_path(None, answer)["nodes"] if node["id"] == "figure")
    assert figure["state"] == "done"
    assert figure["time"] == expected


def test_duckdb_cancel_hooks_do_not_accumulate_per_execute_or_leak_between_scopes():
    from types import SimpleNamespace
    from kge.adapters.warehouse.duckdb import RequestCursor
    from kge.pipeline.trace import QueryCancelled, cancellable, trigger_cancel

    interrupted = []
    cursor = RequestCursor(SimpleNamespace(execute=lambda *args: None, interrupt=lambda: interrupted.append(True)))
    for _ in range(2):
        with cancellable() as (event, hooks):
            for _ in range(20):
                cursor.execute("SELECT 1")
            assert len(hooks) == 1
            trigger_cancel(event, hooks)
            with pytest.raises(QueryCancelled):
                cursor.execute("SELECT 2")
        assert hooks == []
    assert interrupted == [True, True]
