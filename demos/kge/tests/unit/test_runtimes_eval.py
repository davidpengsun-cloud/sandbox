"""Unit tests for Slice 4 of #347: evaluation across runtimes, reference_sql routing, divergence reporting, and HTTP /eval."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters.warehouse.duckdb import Warehouses as DuckDBWarehouses
from kge.eval import sets
from kge.eval.run_golden import (
    compare_runtimes,
    resolve_suite_truth,
    score_with_runtime_truth,
)
from kge.services.http import principal
from kge.services.quality import routes
from tests.unit.test_eval_suite_warehouse import reference_allowlist


def _build_two_runtime_warehouses(tmp_path: Path, *, copy_total: float = 300.0) -> tuple[DuckDBWarehouses, Path]:
    ref_db = tmp_path / "retail.duckdb"
    con_ref = duckdb.connect(str(ref_db))
    con_ref.execute(
        "CREATE TABLE monthly_revenue (month_start DATE, segment VARCHAR, net_revenue DOUBLE)"
    )
    con_ref.execute(
        "INSERT INTO monthly_revenue VALUES ('2025-06-01', 'Mass', 100.0), ('2025-06-01', 'Affluent', 200.0)"
    )
    con_ref.close()

    ext_db = tmp_path / "ext_copy.duckdb"
    con_ext = duckdb.connect(str(ext_db))
    con_ext.execute(
        "CREATE TABLE ext_rev_tbl (m_dt DATE, seg_code VARCHAR, rev_usd DOUBLE)"
    )
    con_ext.execute(
        f"INSERT INTO ext_rev_tbl VALUES ('2025-06-01', 'Mass', 100.0), ('2025-06-01', 'Affluent', {copy_total - 100.0})"
    )
    con_ext.close()

    w = DuckDBWarehouses(tmp_path, ["retail"])
    subgraph = {
        "reference_allowlists": {"retail": reference_allowlist({
            "monthly_revenue": {"month_start", "segment", "net_revenue"},
        })},
        "default_runtimes": ["reference"],
        "runtimes": [
            {
                "id": "reference",
                "label": "Reference warehouse",
                "kind": "reference",
                "dialect": "duckdb",
                "location": "",
                "execution_identity": "",
                "billing_project": "",
                "units": ["retail"],
            },
            {
                "id": "ext_copy",
                "label": "External Copy",
                "kind": "external",
                "dialect": "duckdb",
                "location": str(ext_db),
                "execution_identity": "",
                "billing_project": "",
                "units": ["retail"],
            },
        ],
        "runtime_tables": [
            {
                "id": "ext_copy.retail.monthly_revenue",
                "runtime": "ext_copy",
                "implements": "retail.monthly_revenue",
                "name": "ext_rev_tbl",
            }
        ],
        "runtime_columns": [
            {
                "id": "ext_copy.retail.monthly_revenue.month_start",
                "runtime": "ext_copy",
                "implements": "retail.monthly_revenue.month_start",
                "name": "m_dt",
                "data_type": "DATE",
            },
            {
                "id": "ext_copy.retail.monthly_revenue.segment",
                "runtime": "ext_copy",
                "implements": "retail.monthly_revenue.segment",
                "name": "seg_code",
                "data_type": "VARCHAR",
            },
            {
                "id": "ext_copy.retail.monthly_revenue.net_revenue",
                "runtime": "ext_copy",
                "implements": "retail.monthly_revenue.net_revenue",
                "name": "rev_usd",
                "data_type": "DOUBLE",
            },
        ],
        "certified_in": {"m_rev": "reference"},
        "partitioned": {},
    }
    w.configure_runtimes(subgraph)
    return w, ext_db


def test_suite_declares_runtimes_and_rejects_locations(tmp_path: Path):
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(
        json.dumps(
            {
                "suite_id": "uat_rt",
                "suite_version": "1.0",
                "runtimes": ["ext_copy", "reference"],
                "questions": [
                    {
                        "id": "Q1",
                        "question": "Total revenue for June 2025",
                        "expected_behavior": "figure",
                        "reference_sql": "SELECT SUM(net_revenue) FROM ${SCHEMA}.monthly_revenue WHERE month_start = '2025-06-01'",
                    }
                ],
            }
        )
    )
    parsed = sets.read(suite_file)
    assert parsed["runtimes"] == ["ext_copy", "reference"]
    assert len(parsed["items"]) == 1

    bad_file = tmp_path / "bad_suite.json"
    bad_file.write_text(
        json.dumps(
            {
                "suite_id": "bad_rt",
                "suite_version": "1.0",
                "runtimes": ["/tmp/ext.duckdb"],
                "questions": [{"id": "Q1", "question": "Total revenue"}],
            }
        )
    )
    with pytest.raises(ValueError, match="never a location"):
        sets.read(bad_file)


def test_reference_sql_routes_to_same_runtime_as_answer(tmp_path: Path):
    w, _ = _build_two_runtime_warehouses(tmp_path, copy_total=350.0)
    try:
        sql = "SELECT SUM(net_revenue) AS total FROM ${SCHEMA}.monthly_revenue WHERE month_start = '2025-06-01'"
        ref_res = w.resolve_reference_sql(sql, unit="retail", runtime_id="reference")
        ext_res = w.resolve_reference_sql(sql, unit="retail", runtime_id="ext_copy")
        assert ref_res["figure"] == pytest.approx(300.0)
        assert ref_res["runtime"]["id"] == "reference"
        assert ext_res["figure"] == pytest.approx(350.0)
        assert ext_res["runtime"]["id"] == "ext_copy"
        assert "ext_rev_tbl" in ext_res["sql"]
        assert "rev_usd" in ext_res["sql"]

        item = {
            "id": "Q1",
            "kind": "figure",
            "unit": "retail",
            "question": "Total revenue for June 2025",
            "reference_sql": sql,
            "expect": {"reference_sql": sql},
        }
        resolve_suite_truth([item], w, default_unit="retail", runtimes=["reference"])
        assert item["expect"]["figure"] == pytest.approx(300.0)

        ans_from_ext = {
            "behaviour": "figure",
            "figure": 350.0,
            "runtime": {"id": "ext_copy", "label": "External Copy", "kind": "external"},
        }
        ok, why = score_with_runtime_truth(item, ans_from_ext, w=w, default_unit="retail")
        assert ok, why
    finally:
        w.close()


def test_compare_runtimes_reports_where_copies_diverge(tmp_path: Path):
    w, _ = _build_two_runtime_warehouses(tmp_path, copy_total=350.0)
    try:
        items = [
            {
                "id": "Q_SAME",
                "kind": "figure",
                "unit": "retail",
                "question": "Mass revenue for June 2025",
                "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue WHERE segment = 'Mass'",
                "expect": {},
            },
            {
                "id": "Q_DIFF_FIG",
                "kind": "figure",
                "unit": "retail",
                "question": "Total revenue for June 2025",
                "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue",
                "expect": {},
            },
            {
                "id": "Q_MISSING_TBL",
                "kind": "figure",
                "unit": "retail",
                "question": "Branch count",
                "expect": {"figure": 5.0},
            },
        ]

        def fake_ask(item: dict, rts: list[str]) -> dict:
            rt = rts[0]
            if item["id"] == "Q_SAME":
                return {
                    "behaviour": "figure",
                    "figure": 100.0,
                    "runtime": {"id": rt},
                    "runtime_certified": True,
                }
            if item["id"] == "Q_DIFF_FIG":
                return {
                    "behaviour": "figure",
                    "figure": 300.0 if rt == "reference" else 350.0,
                    "runtime": {"id": rt},
                    "runtime_certified": rt == "reference",
                    "certified_in": "reference",
                }
            return {
                "behaviour": "figure" if rt == "reference" else "refuse",
                "figure": 5.0 if rt == "reference" else None,
                "reason": "" if rt == "reference" else "no runtime in ['ext_copy'] holds tables: branches",
                "runtime": {"id": rt},
            }

        comparison = compare_runtimes(
            items,
            fake_ask,
            ["reference", "ext_copy"],
            w=w,
            default_unit="retail",
        )
        assert comparison["runtimes"] == ["reference", "ext_copy"]
        assert comparison["by_runtime"]["reference"]["by_kind"]["figure"] == {"passed": 3, "of": 3}
        assert comparison["by_runtime"]["ext_copy"]["by_kind"]["figure"] == {"passed": 2, "of": 3}
        divs = {d["id"]: d for d in comparison["divergences"]}
        assert set(divs) == {"Q_DIFF_FIG", "Q_MISSING_TBL"}
        assert divs["Q_DIFF_FIG"]["reason"] == "figure"
        assert divs["Q_DIFF_FIG"]["by_runtime"]["reference"]["figure"] == 300.0
        assert divs["Q_DIFF_FIG"]["by_runtime"]["ext_copy"]["figure"] == 350.0
        assert divs["Q_MISSING_TBL"]["reason"] == "behaviour"
        assert divs["Q_MISSING_TBL"]["by_runtime"]["ext_copy"]["behaviour"] == "refuse"
    finally:
        w.close()


@pytest.mark.parametrize("arm", ["graph", "control", "graph,treatment"])
def test_http_eval_starts_uat_run_and_reads_stored_results(monkeypatch, tmp_path: Path, arm):
    import threading
    import time
    from kge.adapters.objstore import StoreError

    w, _ = _build_two_runtime_warehouses(tmp_path, copy_total=350.0)
    try:
        estate_dir = tmp_path / "estate"
        estate_dir.mkdir()
        (estate_dir / "documents.json").write_text("[]")
        (estate_dir / "golden.json").write_text(
            json.dumps(
                [
                    {
                        "id": "G1",
                        "kind": "within",
                        "unit": "retail",
                        "question": "Total revenue for June 2025",
                        "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue",
                        "expect": {"behaviour": "figure"},
                    }
                ]
            )
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        who = "engineer-principal"
        graph = SimpleNamespace(
            may=lambda person, process: person == who and process == "building",
            acting_roles=lambda person: {"knowledge_engineer"},
            readable_units=lambda person: {"retail"} if person == who else set(),
        )
        entered = threading.Event()
        release = threading.Event()

        def fake_answer(question: str, principal: str, arm_name: str = "graph", runtimes: list[str] | None = None) -> dict:
            entered.set()
            release.wait(timeout=5.0)
            rt = (runtimes or ["reference"])[0]
            fig = 300.0 if rt == "reference" else 350.0
            return {
                "behaviour": "figure",
                "figure": fig,
                "runtime": {"id": rt, "label": rt, "kind": "reference" if rt == "reference" else "external"},
                "runtime_certified": rt == "reference",
                "certified_in": "reference",
            }

        agent_calls = []

        def agent_ask(question, principal, *, arm, runtimes):
            agent_calls.append(arm)
            return fake_answer(question, principal, arm_name=arm, runtimes=runtimes)

        monkeypatch.setattr("kge.services.quality.service.Agent", lambda engine: SimpleNamespace(ask=agent_ask))
        engine = SimpleNamespace(g=graph, w=w, UNIT_ORDER=["retail"], answer=fake_answer)
        prof = SimpleNamespace(estate=lambda: estate_dir, run_dir=lambda: run_dir, id="test", client="test")
        identities = SimpleNamespace(is_admin=lambda person: False)
        monkeypatch.setattr(routes, "served", SimpleNamespace(PROFILE=prof, engine=lambda: engine, ids=lambda: identities))

        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[principal] = lambda: who

        with TestClient(app) as client:
            assert client.get("/eval").status_code == 404
            assert client.post("/eval", json={"suite": "/etc/passwd"}).status_code == 422
            assert client.post("/eval", json={"arm": "unknown"}).status_code == 422

            post_res = client.post(
                "/eval",
                json={"arm": arm, "runtimes": ["reference", "ext_copy"]},
            )
            assert post_res.status_code == 202
            started = post_res.json()
            assert "job" in started
            assert started["job"]["step"] == "eval"
            assert started["job"]["id"]

            assert entered.wait(timeout=5.0)
            running_res = client.get("/eval")
            assert running_res.status_code == 200
            running_body = running_res.json()
            assert running_body["state"] == "running"
            assert running_body["id"] == started["job"]["id"]
            release.set()

            body = None
            for _ in range(200):
                get_res = client.get("/eval")
                assert get_res.status_code == 200
                candidate = get_res.json()
                if candidate.get("state") != "running":
                    body = candidate
                    break
                time.sleep(0.01)
            assert body is not None
            for chosen in arm.split(","):
                assert body[chosen]["by_kind"]["within"] == {"passed": 1, "of": 1}
            assert set(agent_calls) == set(arm.split(",")) & {"control", "treatment"}
            assert body["runtimes"] == ["reference", "ext_copy"]
            assert len(body["divergences"]) == 1
            assert body["divergences"][0]["id"] == "G1"
            assert body["divergences"][0]["reason"] == "figure"

            monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
            monkeypatch.setattr(
                "kge.services.quality.service.the_store",
                lambda: SimpleNamespace(get=lambda _k: (_ for _ in ()).throw(StoreError("boom"))),
            )
            monkeypatch.setattr("kge.services.onboard.load_job", lambda _app: None)
            assert client.get("/eval").status_code == 503
    finally:
        w.close()


def test_evaluate_reuses_truth_cache_across_arms_and_onboard_request_runs_eval(monkeypatch, tmp_path: Path):
    import io
    import tarfile
    import yaml
    from kge.adapters.appstate import access_state
    from kge.adapters.objstore import open_store
    from kge.pipeline import objstore
    from kge.services.quality import service as quality_service

    w, _ = _build_two_runtime_warehouses(tmp_path, copy_total=300.0)
    try:
        estate_dir = tmp_path / "estate"
        estate_dir.mkdir()
        (estate_dir / "documents.json").write_text("[]")
        (estate_dir / "golden.json").write_text(
            json.dumps(
                [
                    {
                        "id": "G1",
                        "kind": "within",
                        "unit": "retail",
                        "question": "Total revenue",
                        "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue",
                        "expect": {"behaviour": "figure"},
                    }
                ]
            )
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        resolve_calls = []
        orig_resolve = w.resolve_reference_sql

        def counting_resolve(*args, **kwargs):
            resolve_calls.append((args, kwargs))
            return orig_resolve(*args, **kwargs)

        monkeypatch.setattr(w, "resolve_reference_sql", counting_resolve)
        engine = SimpleNamespace(
            g=SimpleNamespace(readable_units=lambda who: {"retail"}),
            w=w,
            UNIT_ORDER=["retail"],
            answer=lambda q, principal, arm_name="graph", runtimes=None: {
                "behaviour": "figure",
                "figure": 300.0,
                "runtime": {"id": (runtimes or ["reference"])[0]},
            },
        )
        prof = SimpleNamespace(estate=lambda: estate_dir, run_dir=lambda: run_dir, id="test", client="test")
        quality_service.evaluate(prof, engine, "engineer-principal", arm="all", runtimes=["ext_copy"])
        assert [c[1]["runtime_id"] for c in resolve_calls] == ["ext_copy", "reference"]
    finally:
        w.close()

    store = open_store(str(tmp_path / "store"))
    model_hash = "a" * 64
    roles_name = f"build/artifacts/meridian/{model_hash}/access/roles.json"
    archive = io.BytesIO()
    payload = json.dumps({"roles": [{"id": "engineer", "processes": ["building"]}]}).encode()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        member = tarfile.TarInfo(roles_name)
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    store.put("releases/meridian/current", f"{model_hash}\n".encode())
    store.put(f"releases/meridian/{model_hash}.tar.gz", archive.getvalue())
    identities = tmp_path / "identities.yaml"
    identities.write_text("principals:\n  - {principal: sam@example.com, role: engineer}\n")
    prof_job = SimpleNamespace(
        id="stored",
        estate=lambda: estate_dir,
        client="meridian",
        clients=tmp_path / "clients",
        identities=identities,
        store=tmp_path / "stores",
        run_dir=lambda: tmp_path / "job_run",
    )
    for bound in yaml.safe_load(identities.read_text())["principals"]:
        access_state(prof_job).access_put(
            {"username": bound["principal"], "name": bound["principal"], "roles": [bound["role"]]},
            by="operator@example.com",
            via="migration",
        )
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda *a: prof_job)
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(objstore.subprocess, "run", execute)
    store.put(
        "clients/meridian/onboard/request.json",
        json.dumps(
            {
                "id": "20261108T100000Z-abcdef",
                "command": "eval",
                "principal": "sam@example.com",
                "hash": None,
                "timestamp": "2026-11-08T10:00:00Z",
            }
        ).encode(),
    )
    objstore.onboard_request(tmp_path)
    assert calls == [["sh", str(tmp_path / "entrypoint.sh"), "eval", "PRINCIPAL=sam@example.com", "ARM=all"]]

    calls.clear()
    store.put(
        "clients/meridian/onboard/request.json",
        json.dumps(
            {
                "id": "20261108T100001Z-abcdef",
                "command": "eval",
                "principal": "sam@example.com",
                "hash": None,
                "timestamp": "2026-11-08T10:00:01Z",
                "arm": "graph,schema,control",
                "runtimes": ["reference", "BQ_Live"],
            }
        ).encode(),
    )
    objstore.onboard_request(tmp_path)
    assert calls == [
        [
            "sh",
            str(tmp_path / "entrypoint.sh"),
            "eval",
            "PRINCIPAL=sam@example.com",
            "ARM=graph,schema,control",
            "RUNTIMES=reference,BQ_Live",
        ]
    ]


    from contextlib import nullcontext, redirect_stderr, redirect_stdout
    from kge.eval import run_golden
    from kge.services.onboard import jobs

    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    access_state(prof_job).access_put({"username": "sam@example.com", "name": "Sam", "roles": ["engineer"]},
                                    by="operator@example.com", via="migration")
    monkeypatch.setattr(jobs, "the_store", lambda: store)
    monkeypatch.setattr(jobs, "_cloud", lambda: False)
    monkeypatch.setattr(quality_service, "the_store", lambda: store)
    prof_job.serve = lambda: prof_job
    prof_job.asking = lambda who: who
    monkeypatch.setattr(run_golden, "resolve", lambda profile: prof_job)
    warehouse_dir = tmp_path / "stored_warehouses"
    warehouse_dir.mkdir()
    stored_w, _ = _build_two_runtime_warehouses(warehouse_dir)
    caller = "sam@example.com"
    graph = SimpleNamespace(may=lambda who, process: who == caller and process == "building",
                            acting_roles=lambda who: {"engineer"}, readable_units=lambda who: {"retail"},
                            as_roles=nullcontext)
    agent_calls = []
    preset_calls = []

    def answer(question, principal, arm_name, runtimes=None):
        preset_calls.append((principal, arm_name))
        return {"behaviour": "figure", "figure": 300.0, "runtime": {"id": "reference"}}

    def agent_ask(question, principal, *, arm, runtimes):
        agent_calls.append((principal, arm))
        return {"behaviour": "figure", "figure": 300.0, "runtime": {"id": "reference"}}

    stored_engine = SimpleNamespace(g=graph, w=stored_w, UNIT_ORDER=["retail"], answer=answer, close=lambda: None)
    monkeypatch.setattr(run_golden.Engine, "serving", lambda profile: stored_engine)
    monkeypatch.setattr(run_golden, "Agent", lambda engine: SimpleNamespace(ask=agent_ask))
    served_app = SimpleNamespace(PROFILE=prof_job, engine=lambda: stored_engine,
                                 ids=lambda: SimpleNamespace(is_admin=lambda who: False))
    monkeypatch.setattr(routes, "served", served_app)
    api = FastAPI()
    api.include_router(routes.router)
    api.dependency_overrides[principal] = lambda: caller

    def execute_eval(args, **kwargs):
        arm_arg, = [arg.removeprefix("ARM=") for arg in args if arg.startswith("ARM=")]
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = run_golden.main(["--arm", arm_arg, "--as", caller])
        except SystemExit as exc:
            code = 2
            stderr.write(str(exc))
        if code == 0:
            store.put("clients/meridian/onboard/eval.json", (prof_job.run_dir() / "eval.json").read_bytes())
        return SimpleNamespace(returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())

    monkeypatch.setattr(objstore.subprocess, "run", execute_eval)
    try:
        with TestClient(api) as client:
            for arm in ("control", "graph,treatment"):
                response = client.post("/eval", json={"arm": arm})
                assert response.status_code == 202, response.text
                request = json.loads(store.get("clients/meridian/onboard/request.json"))
                assert request["arm"] == arm and request["principal"] == caller
                objstore.onboard_request(tmp_path)
                job = json.loads(store.get("clients/meridian/onboard/job.json"))
                assert job["state"] == "done", job
                results = client.get("/eval").json()
                for chosen in arm.split(","):
                    assert results[chosen]["within"] == {"passed": 1, "of": 1}
            assert agent_calls == [(caller, "control"), (caller, "treatment")]
            assert preset_calls == [(caller, "graph")]
            assert client.post("/eval", json={"arm": "unknown"}).status_code == 422
        request["id"] = "20261108T100002Z-abcdef"
        request["arm"] = "unknown"
        store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
        with pytest.raises(objstore.Refused, match="run_golden: no preset"):
            objstore.onboard_request(tmp_path)
        job = json.loads(store.get("clients/meridian/onboard/job.json"))
        assert job["state"] == "failed"
        assert "run_golden: no preset 'unknown'" in job["message"]
    finally:
        stored_w.close()


def test_external_bigquery_runtime_offline_execution_and_eval(monkeypatch, tmp_path: Path):
    from kge.pipeline.sqlguard import Allowlist
    from tests import bigquery_fake

    fake = bigquery_fake.install(monkeypatch)
    fake.load_rows(
        None,
        "ext_bq_ds",
        "ext_rev_tbl",
        [
            {"name": "m_dt", "type": "DATE"},
            {"name": "seg_code", "type": "STRING"},
            {"name": "rev_usd", "type": "FLOAT64"},
        ],
        [
            {"m_dt": "2025-06-01", "seg_code": "Mass", "rev_usd": 100.0},
            {"m_dt": "2025-06-01", "seg_code": "Affluent", "rev_usd": 250.0},
        ],
    )

    ref_db = tmp_path / "retail.duckdb"
    con_ref = duckdb.connect(str(ref_db))
    con_ref.execute(
        "CREATE TABLE monthly_revenue (month_start DATE, segment VARCHAR, net_revenue DOUBLE)"
    )
    con_ref.execute(
        "INSERT INTO monthly_revenue VALUES ('2025-06-01', 'Mass', 100.0), ('2025-06-01', 'Affluent', 200.0)"
    )
    con_ref.close()

    w = DuckDBWarehouses(tmp_path, ["retail"])
    try:
        subgraph = {
            "reference_allowlists": {"retail": reference_allowlist({
                "monthly_revenue": {"month_start", "segment", "net_revenue"},
            })},
            "default_runtimes": ["reference"],
            "runtimes": [
                {
                    "id": "reference",
                    "label": "Reference warehouse",
                    "kind": "reference",
                    "dialect": "duckdb",
                    "location": "",
                    "execution_identity": "",
                    "billing_project": "",
                    "units": ["retail"],
                },
                {
                    "id": "ext_bq",
                    "label": "External BigQuery",
                    "kind": "external",
                    "dialect": "bigquery",
                    "location": "pilot-project.ext_bq_ds",
                    "execution_identity": "",
                    "billing_project": "pilot-project",
                    "units": ["retail"],
                },
            ],
            "runtime_tables": [
                {
                    "id": "ext_bq.retail.monthly_revenue",
                    "runtime": "ext_bq",
                    "implements": "retail.monthly_revenue",
                    "name": "ext_rev_tbl",
                }
            ],
            "runtime_columns": [
                {
                    "id": "ext_bq.retail.monthly_revenue.month_start",
                    "runtime": "ext_bq",
                    "implements": "retail.monthly_revenue.month_start",
                    "name": "m_dt",
                    "data_type": "DATE",
                },
                {
                    "id": "ext_bq.retail.monthly_revenue.segment",
                    "runtime": "ext_bq",
                    "implements": "retail.monthly_revenue.segment",
                    "name": "seg_code",
                    "data_type": "VARCHAR",
                },
                {
                    "id": "ext_bq.retail.monthly_revenue.net_revenue",
                    "runtime": "ext_bq",
                    "implements": "retail.monthly_revenue.net_revenue",
                    "name": "rev_usd",
                    "data_type": "DOUBLE",
                },
            ],
            "certified_in": {"m_rev": "reference"},
            "partitioned": {},
        }
        w.configure_runtimes(subgraph)
        health = w.runtimes_health()
        assert health["ext_bq"]["available"] is True

        allow = Allowlist(
            unit="retail",
            tables={"monthly_revenue"},
            columns={"monthly_revenue": {"month_start", "segment", "net_revenue"}},
            joins=set(),
            schema={"monthly_revenue": {"month_start": "DATE", "segment": "VARCHAR", "net_revenue": "DOUBLE"}},
        )
        stmt = {
            "sql": "SELECT SUM(net_revenue) AS total FROM monthly_revenue WHERE month_start = '2025-06-01'",
            "unit": "retail",
            "allow": allow,
        }
        guard, rows, meta = w.execute_statement(stmt, runtime_id="ext_bq", measures=("m_rev",))
        assert guard["passed"] is True
        assert rows == [(350.0,)]
        assert meta["runtime"]["id"] == "ext_bq"
        assert meta["runtime_certified"] is False
        assert meta["certified_in"] == "reference"

        requests_before = len(fake.requests)
        res = w.resolve_reference_sql(
            "SELECT SUM(net_revenue) AS total FROM ${SCHEMA}.monthly_revenue WHERE month_start = '2025-06-01'",
            unit="retail",
            runtime_id="ext_bq",
        )
        assert res["figure"] == pytest.approx(350.0)
        assert res["runtime"]["id"] == "ext_bq"
        assert "ext_rev_tbl" in res["sql"]
        requests = fake.requests[requests_before:]
        assert len(requests) == 2
        assert sum(bool(r["body"].get("configuration", {}).get("dryRun") or r["body"].get("dryRun")) for r in requests) == 1
        w._runtimes.reference_allowlists["retail"] = reference_allowlist(
            {"monthly_revenue": {"month_start", "segment", "net_revenue"}},
            [("retail", "monthly_revenue", "net_revenue")],
        )
        before_refusal = len(fake.requests)
        item = {"id": "restricted", "kind": "figure", "unit": "retail", "expect": {},
                "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue"}
        cache = resolve_suite_truth([item], w, runtime_id="ext_bq", readable_units={"retail"})
        entry, = cache.values()
        assert entry["refused"] and "restricted" in entry["error"]
        assert len(fake.requests) == before_refusal
        cache = resolve_suite_truth([dict(item, expect={})], w, runtime_id="ext_bq", readable_units=set())
        entry, = cache.values()
        assert entry["refused"] and "access denied for unit retail" in entry["error"]
        assert len(fake.requests) == before_refusal

        orig_run = fake.run

        def fail_on_execute(loader, sql, dataset_id=None, dry_run=False, job_timeout_ms=None):
            if not dry_run:
                raise RuntimeError("BigQuery execution boom")
            return orig_run(loader, sql, dataset_id, dry_run, job_timeout_ms)

        monkeypatch.setattr(fake, "run", fail_on_execute)
        guard_err, rows_err, _ = w.execute_statement(stmt, runtime_id="ext_bq", measures=("m_rev",))
        assert guard_err["passed"] is False
        assert guard_err["failed_at"] == "execute"
        assert "execution failed:" in guard_err["problems"][0]
        assert rows_err is None
    finally:
        w.close()


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("denial", ["restricted", "unit", "star"])
def test_external_reference_truth_refused_before_execution(monkeypatch, tmp_path, selected, denial):
    w, _ = _build_two_runtime_warehouses(tmp_path)
    try:
        w._runtimes.reference_allowlists["retail"] = reference_allowlist(
            {"monthly_revenue": {"month_start", "segment", "net_revenue"}},
            [("retail", "monthly_revenue", "net_revenue")] if denial == "restricted" else (),
        )
        monkeypatch.setattr(w._runtimes, "_bounded_duckdb", lambda *a: pytest.fail("refused truth was executed"))
        sql = "SELECT * FROM monthly_revenue" if denial == "star" else "SELECT SUM(net_revenue) FROM monthly_revenue"
        item = {"id": "denied", "kind": "figure", "unit": "retail", "reference_sql": sql, "expect": {}}
        refused = []
        cache = resolve_suite_truth([item], w, readable_units=set() if denial == "unit" else {"retail"},
                                    refused=refused,
                                    **({"runtimes": ["ext_copy"]} if selected else {"runtime_id": "ext_copy"}))
        entry, = cache.values()
        assert entry["refused"] is True
        expected = "access denied for unit retail" if denial == "unit" else "restricted" if denial == "restricted" else "select *"
        assert expected in entry["error"].lower()
        assert refused == [{"id": "denied", "runtime": "ext_copy", "reason": entry["error"]}]
        assert "ground_truth_refused" in score_with_runtime_truth(item, {})[1]
    finally:
        w.close()


def test_truth_cache_cannot_cross_readable_scopes(tmp_path):
    w, _ = _build_two_runtime_warehouses(tmp_path)
    try:
        item = {"id": "scope", "kind": "figure", "unit": "retail", "expect": {},
                "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue"}
        cache = resolve_suite_truth([item], w, runtime_id="ext_copy", readable_units={"retail"})
        ans = {"behaviour": "figure", "figure": 300.0, "runtime": {"id": "ext_copy"}}
        assert score_with_runtime_truth(item, ans, w=w, cache=cache, readable_units={"retail"})[0]
        ok, why = score_with_runtime_truth(item, ans, w=w, cache=cache, readable_units=set())
        assert not ok and "ground_truth_refused" in why and "access denied for unit retail" in why
        assert len(cache) == 2
    finally:
        w.close()


@pytest.mark.parametrize("readable", [set(), {"retail"}])
def test_evaluate_truth_uses_eval_identity_access(tmp_path, readable):
    from kge.services.quality import service

    w, _ = _build_two_runtime_warehouses(tmp_path)
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "documents.json").write_text("[]")
    (estate / "golden.json").write_text(json.dumps([{
        "id": "G1", "kind": "figure", "unit": "retail", "question": "Total revenue",
        "reference_sql": "SELECT SUM(net_revenue) FROM monthly_revenue", "expect": {},
    }]))
    identities = []

    def readable_units(who):
        identities.append(who)
        return readable

    engine = SimpleNamespace(g=SimpleNamespace(readable_units=readable_units), w=w, UNIT_ORDER=["retail"],
                             answer=lambda *a, **kw: {"behaviour": "figure", "figure": 300.0, "runtime": {"id": "ext_copy"}})
    profile = SimpleNamespace(estate=lambda: estate, run_dir=lambda: tmp_path / "run")
    try:
        report = service.evaluate(profile, engine, "eval-identity", arm="graph")
        row, = report["graph"]["rows"]
        assert row["ok"] == bool(readable)
        assert identities == ["eval-identity"]
        if not readable:
            assert "ground_truth_refused" in row["why"] and "access denied for unit retail" in row["why"]
            assert report["refused"] == [{"id": "G1", "runtime": "ext_copy",
                                           "reason": "ext_copy: access denied for unit retail"}]
        else:
            assert report["refused"] == []
    finally:
        w.close()
