"""Scoped join metadata reuses the release snapshot on the pilot's graph store."""
import json

import pytest

from tests.integration.test_graphstore_twin import pointer, twin
from tests.scratch import scratch_build


@pytest.mark.parametrize("provider", ["none", "mock"])
def test_scoped_join_requests_keep_the_bigquery_job_budget(tmp_path, provider):
    build = scratch_build(tmp_path / "build")
    lb, _ = pointer(build)
    script = tmp_path / "assist_budget.py"
    script.write_text('''import json
import os
from kge.adapters.graphstore import bigquery
from kge.pipeline.profile import resolve
from kge.services.ask import Combined, Engine
from kge.services.assist import service

from tests.plan_recordings import recorded

engine = Engine.serving(resolve())
try:
    combined = Combined(engine, llm=None)
    enabled = os.environ["MODEL_PROVIDER"] == "mock"
    router = recorded() if enabled else None
    service.model_for = lambda agent: router
    posted = []
    post = bigquery.Client._post
    def counted(self, url, body):
        posted.append(url)
        return post(self, url, body)
    bigquery.Client._post = counted
    results = []
    for product, who, expected in (
        ("wealth.TRAN_FACT_1", "advisor_wealth.meridian@example.com", ["TRAN_TYPE_1", "SRCE_SYS_1", "MONTH_DIM_1"]),
        ("retail.monthly_revenue", "analyst_retail.meridian@example.com", ["calendar_month", "account"]),
        ("wealth.BRCH_DIM_1", "advisor_wealth.meridian@example.com", []),
    ):
        for repeat in range(2):
            posted.clear()
            answer = service.assist(combined, engine.profile, "How does this table join to other tables?", who,
                                    "analysis", "graph", combined.default_config(), products=[product])
            assert answer["behaviour"] == "catalogue", answer
            assert answer["assistant_available"] is enabled, answer
            text = answer["answer_text"].split("Declared joins: ", 1)[1].removesuffix(".")
            if expected:
                assert len(text.split("; ")) == len(expected), answer
                unit = product.split(".")[0]
                assert all(unit + "." + table in text for table in expected), answer
            else:
                assert text == "none recorded in scope", answer
            results.append(len(posted))
    print(json.dumps(results))
finally:
    engine.close()
''')
    output = twin(build, lb, "-", str(script), env={"MODEL_PROVIDER": provider})
    assert json.loads(output.stdout.strip().splitlines()[-1]) == [0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("missing", [False, True])
def test_recorded_assist_numeric_route_and_missing_route_keep_request_job_budgets(monkeypatch, tmp_path, missing):
    from kge.services.ask import Combined
    from kge.services.ask.engine import wording
    from kge.services.assist import service
    from tests.integration.test_planner_request_jobs import budgets, pilot
    from tests.plan_recordings import public_recorded, recorded

    fake, graph, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        question = "Total retail revenue for June 2025"
        baseline_jobs = []
        if not missing:
            original = opened()
            monkeypatch.setattr(wording, "_catalogues", {})
            baseline = Combined(original, llm=None)
            monkeypatch.setattr(service, "model_for", lambda agent: None)
            for cold in (True, False):
                fake.requests.clear()
                answer = service.assist(baseline, original.profile, question, "analyst_enterprise", "analysis", "graph",
                                        baseline.default_config())
                assert answer["figure"] == 13287.67
                baseline_jobs.append(budgets(fake))
                assert baseline_jobs[-1]["graph"] <= (56 if cold else 1)
            original.close()
            engines.remove(original)
        bootstrap = opened()
        cfg = Combined(bootstrap, llm=recorded()).default_config().with_stages(renderer="template")
        model = recorded({}) if missing else public_recorded(bootstrap, question, "analyst_enterprise", config=cfg)
        bootstrap.close()
        engines.remove(bootstrap)
        monkeypatch.setattr(wording, "_catalogues", {})
        engine = opened()
        monkeypatch.setattr(service, "model_for", lambda agent: model)
        combined = Combined(engine, llm=model)
        for cold in (True, False):
            fake.requests.clear()
            answer = service.assist(combined, engine.profile, question, "analyst_enterprise", "analysis", "graph",
                                    cfg)
            jobs = budgets(fake)
            assert jobs["warehouse"] == jobs["dry"] == (0 if missing else 1), jobs
            assert jobs["catalogue"] == jobs["retrieval"] == 0, jobs
            assert jobs["graph"] <= ((14 if cold else 1) if missing else baseline_jobs[0 if cold else 1]["graph"]), jobs
            if not missing:
                assert {k: v for k, v in jobs.items() if k != "graph"} == {
                    k: v for k, v in baseline_jobs[0 if cold else 1].items() if k != "graph"}
            assert answer["assistant_available"] is True and answer["notice"] == ""
            if missing:
                assert answer["behaviour"] == "abstain" and "Missing routing recording:" in answer["reason"]
                assert answer["figure"] is None and answer["figures"] == [] and answer["sources"] == []
            else:
                assert answer["source"] == "ask" and answer["figure"] == 13287.67
                assert answer["guard"]["passed"] and answer["sources"]
                assert answer["versions"]["interpretation"] == "model:mock:mock"
        if missing:
            fake.requests.clear()
            denied = service.assist(combined, engine.profile, question, "advisor_wealth", "analysis", "graph", cfg,
                                    products=["retail.monthly_revenue"])
            assert denied["behaviour"] == "refuse" and denied["denied_warehouses"] == ["retail"]
            assert all(budgets(fake)[kind] == 0 for kind in ("warehouse", "dry", "catalogue", "retrieval"))
    finally:
        for engine in engines:
            engine.close()
        if graph.conn is not None:
            graph.conn.close()
            graph.db.close()
        fake.con.close()
