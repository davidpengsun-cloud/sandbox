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

class Router:
    provider = "offline-router"
    def json(self, prompt, max_tokens=200):
        assert "How does this table join to other tables?" in prompt
        return {"source": "catalogue"}

engine = Engine.serving(resolve())
try:
    combined = Combined(engine, llm=None)
    enabled = os.environ["MODEL_PROVIDER"] == "mock"
    router = Router() if enabled else None
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
    assert json.loads(output.stdout.strip().splitlines()[-1]) == [2, 0, 0, 0, 0, 0]
