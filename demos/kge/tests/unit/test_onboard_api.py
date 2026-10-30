"""The onboarding HTTP boundary: roles, write policy, validation and response shape."""
import json
import io
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.services.onboard import jobs, routes as onboard
from kge.services.onboard.service import page_item
from kge.eval import sets


class Graph:
    def can_write_terms(self, who):
        return who == "business"

    def may(self, who, process):
        return who == "steward" and process == "certification"


def client(monkeypatch, tmp_path, who="engineer", writable=True):
    profile = SimpleNamespace(id="p", client="meridian", artifacts=tmp_path / "artifacts",
                              run_dir=lambda: tmp_path, model_hash=lambda: "a" * 64)
    engine = SimpleNamespace(g=Graph(), profile=profile)
    fake = SimpleNamespace(PROFILE=profile, engine=lambda: engine, principal=lambda request: request.headers.get("who", who), switch=lambda: None)
    monkeypatch.setattr(onboard, "_app", lambda: fake)
    monkeypatch.setattr(onboard.core, "writable", lambda prof: (writable, "read-only until configured" if not writable else ""))
    monkeypatch.setattr(onboard.core, "permissions", lambda prof, principal, writable_=True: {
        "read": principal in {"business", "engineer"}, "create": writable_ and principal == "engineer",
        "draft": writable_ and principal == "engineer", "accept": writable_ and principal == "business",
        "certify": writable_ and principal == "steward", "run": writable_ and principal == "engineer",
        "improve": writable_ and principal == "engineer",
        "participant": principal in {"business", "engineer", "steward"},
        "sign_off": writable_ and principal == "steward", "release": writable_ and principal == "steward"})
    def require_process(prof, principal, process):
        allowed = {"drafting": {"engineer"}, "building": {"engineer"},
                   "improving": {"engineer"}}
        if principal not in allowed[process]:
            raise onboard.core.OnboardError(403, f"{principal} is not a {process} identity")
    monkeypatch.setattr(onboard.core, "require_process", require_process)
    monkeypatch.setattr(onboard.core, "show", lambda prof: {"units": []})
    app = FastAPI(); app.include_router(onboard.router)
    return TestClient(app)


def test_roles_read_only_size_and_malformed_bodies(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard.core, "tables", lambda *a, **k: {"unit": "u", "tables": [], "rows": {"count": 0}})
    body = {"unit": "leasing", "label": "Asset Finance", "ddl": "CREATE TABLE rental (id INTEGER)", "rows": "invented"}
    assert c.post("/onboard/tables", json=body).status_code == 200
    assert c.post("/onboard/tables", json={**body, "ddl": "x" * (256 * 1024 + 1)}).status_code == 413
    assert c.post("/onboard/tables", json={"unit": 3}).status_code == 422
    assert c.post("/onboard/tables", headers={"who": "analyst"}, json=body).status_code == 403

    read_only = client(monkeypatch, tmp_path, writable=False)
    assert read_only.post("/onboard/source", json=body).status_code == 409


def test_source_response_does_not_disclose_its_warehouse_path(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard.core, "create", lambda *args, **kwargs: {
        "unit": "leasing", "created": True, "warehouse": str(tmp_path / "warehouses/leasing.duckdb")})
    response = c.post("/onboard/source", json={"unit": "leasing", "label": "Asset Finance",
                                                 "ddl": "CREATE TABLE t (id INTEGER)"})
    assert response.status_code == 200
    assert response.json() == {"unit": "leasing", "created": True}
    assert str(tmp_path) not in response.text


def test_bad_ddl_is_a_clean_400_at_the_http_boundary(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    profile = onboard._app().PROFILE
    monkeypatch.setattr(onboard.core, "tables", __import__("kge.estate.onboard", fromlist=["tables"]).tables)
    profile.clients = tmp_path / "clients"
    profile.client = "meridian"
    profile.warehouses = tmp_path / "warehouses"
    profile.identities = Path(__file__).resolve().parents[2] / "deploy/meridian/identities.yaml"
    for ddl_text, reason in [(";", "no statements"),
                             ("CREATE TABLE leasing.x (a " + "(" * 500 + "INTEGER" + ")" * 500 + ")", "nesting")]:
        r = c.post("/onboard/tables", json={"unit": "leasing", "label": "Asset Finance", "ddl": ddl_text})
        assert r.status_code == 400 and reason in r.json()["detail"]


def test_unsafe_ddl_is_refused_by_statement_before_rows_are_asked_for(monkeypatch, tmp_path):
    """The page's default row mode is CSVs beside the DDL, so an unsafe DDL with no CSVs chosen must still be refused
    for its statement (README, Demo script step 10), not for the CSVs it has not reached yet."""
    c = client(monkeypatch, tmp_path)
    profile = onboard._app().PROFILE
    monkeypatch.setattr(onboard.core, "tables", __import__("kge.estate.onboard", fromlist=["tables"]).tables)
    profile.clients = tmp_path / "clients"
    profile.warehouses = tmp_path / "warehouses"
    demo = Path(__file__).resolve().parents[2]
    unsafe = (demo / "examples/leasing/unsafe.sql").read_text()
    r = c.post("/onboard/tables", json={"unit": "leasing", "label": "Asset Finance", "ddl": unsafe, "rows": "csv"})
    assert r.status_code == 400
    assert "CREATE TABLE AS SELECT" in r.json()["detail"] and "statement 1" in r.json()["detail"]
    safe = (demo / "examples/leasing/schema.sql").read_text()
    r = c.post("/onboard/tables", json={"unit": "leasing", "label": "Asset Finance", "ddl": safe, "rows": "csv"})
    assert r.status_code == 400 and "rows csv needs" in r.json()["detail"]


def test_duckdb_rejections_and_parser_shape_errors_are_clean_422s(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    profile = onboard._app().PROFILE
    monkeypatch.setattr(onboard.core, "tables", __import__("kge.estate.onboard", fromlist=["tables"]).tables)
    profile.clients = tmp_path / "clients"
    cases = [
        "CREATE TABLE p (id INTEGER); CREATE TABLE c (p_id INTEGER REFERENCES p(id))",
        "CREATE TABLE t (missing INTEGER, PRIMARY KEY (id))",
        "CREATE TABLE p (id INTEGER PRIMARY KEY); CREATE TABLE c (p_id DATE REFERENCES p(id))",
        "CREATE TABLE c (p_id INTEGER REFERENCES p(id)); CREATE TABLE p (id INTEGER PRIMARY KEY)",
        "CREATE TABLE t (a INTEGER PRIMARY KEY, b INTEGER PRIMARY KEY)",
        "CREATE TABLE t (id INTEGER, id DATE)",
        "CREATE TABLE t (id INTEGER); CREATE TABLE t (other INTEGER)",
        "CREATE TABLE t ()",
        "CREATE TABLE t (id DECIMAL(40,2))",
        "CREATE TABLE t (id DECIMAL(2,3))",
        "CREATE TABLE t (FOREIGN KEY REFERENCES p)",
    ]
    for ddl_text in cases:
        r = c.post("/onboard/tables", json={"unit": "leasing", "label": "Asset Finance", "ddl": ddl_text})
        assert r.status_code == 422, (ddl_text, r.text)


def test_reserved_unit_names_are_clean_400s(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard.core, "tables", __import__("kge.estate.onboard", fromlist=["tables"]).tables)
    onboard._app().PROFILE.clients = tmp_path / "clients"
    for unit in sorted(__import__("kge.estate.ddl", fromlist=["RESERVED_UNITS"]).RESERVED_UNITS):
        r = c.post("/onboard/tables", json={"unit": unit, "label": "Reserved", "ddl": "CREATE TABLE t (id INTEGER)"})
        assert r.status_code == 400 and "reserved by DuckDB" in r.json()["detail"]


def test_tables_reports_the_invented_row_count_for_each_table(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard.core, "tables", __import__("kge.estate.onboard", fromlist=["tables"]).tables)
    profile = onboard._app().PROFILE
    profile.clients = tmp_path / "clients"
    ddl = (Path(__file__).resolve().parents[2] / "examples/leasing/schema.sql").read_text()
    body = c.post("/onboard/tables", json={"unit": "leasing", "label": "Asset Finance", "ddl": ddl}).json()
    assert body["rows"]["count"] == 144
    assert [(table["name"], table["rows"]) for table in body["tables"]] == [
        ("lessee", 12), ("lease", 12), ("rental", 120)]


def test_source_uses_csvs_when_rows_is_omitted(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(onboard.core, "create", lambda *args, **kwargs: seen.update(kwargs) or {"created": True})
    response = c.post("/onboard/source", json={"unit": "leasing", "label": "Asset Finance",
                      "ddl": "CREATE TABLE t (id INTEGER)", "csv": {"t.csv": "id\n1\n"}})
    assert response.status_code == 200
    assert seen["rows"] is None and seen["csv"] == {"t.csv": b"id\n1\n"}


def test_transition_errors_keep_the_core_status(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    def refused(*args, **kwargs):
        raise onboard.core.OnboardError(409, "accept first")
    monkeypatch.setattr(onboard.core, "certify", refused)
    r = c.post("/onboard/certify", headers={"who": "steward"}, json={"unit": "leasing", "items": "all"})
    assert r.status_code == 409 and r.json()["detail"] == "accept first"


def test_store_errors_are_logged_without_disclosing_the_store_url(monkeypatch, tmp_path, caplog):
    c = client(monkeypatch, tmp_path)
    secret = "gs://" + "private-project-kge-store"
    monkeypatch.setattr(onboard.core, "show", lambda prof: (_ for _ in ()).throw(
        onboard.StoreError(f"cannot read clients/meridian/pack.yaml at {secret}: HTTP 403")))
    response = c.get("/onboard")
    assert response.status_code == 503
    assert response.json()["detail"] == "the onboarding store could not perform the operation"
    assert secret not in response.text and secret in caplog.text


def test_invalid_operation_hides_a_filesystem_path_and_logs_it(monkeypatch, tmp_path, caplog):
    c = client(monkeypatch, tmp_path)
    secret = str(tmp_path / "private" / "pack.yaml")
    monkeypatch.setattr(onboard.core, "tables", lambda *a, **k: (_ for _ in ()).throw(OSError(secret)))
    response = c.post("/onboard/tables", json={"unit": "leasing", "label": "Asset Finance",
                                                "ddl": "CREATE TABLE t (id INTEGER)"})
    assert response.status_code == 422
    assert response.json()["detail"] == "the onboarding input could not be processed"
    assert secret not in response.text and secret in caplog.text


def test_golden_hides_a_set_for_a_unit_the_engine_does_not_serve(tmp_path):
    (tmp_path / "golden.json").write_text('[{"id":"A","question":"kept","expect":{"unit":"retail"}}]')
    (tmp_path / "golden_leasing_v1.json").write_text(
        '{"version":"v1","items":[{"id":"B","question":"hidden","expect":{"unit":"leasing"}}]}')
    assert [s["name"] for s in sets.golden(tmp_path, units=["retail"])] == ["golden"]
    assert sets.golden(tmp_path)[1]["questions"] == ["hidden"], "the existing shape is unchanged without a unit filter"


def test_item_response_exposes_detail_not_pack():
    item = page_item({"id": "Q", "kind": "question", "state": "drafted", "question": "How much?",
                      "expect": {"figure": "12.34"}, "sql": "SELECT 12.34", "set": "golden_u", "terms": ["x"],
                      "pack": {"not": "public"}})
    assert "pack" not in item and item["detail"] == {"question": "How much?", "sql": "SELECT 12.34", "set": "golden_u",
                                                       "terms": ["x"], "figure": "12.34"}


def test_panel_flags_are_the_core_permissions(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    assert c.get("/onboard", headers={"who": "business"}).json()["can"] == {
        "read": True, "create": False, "draft": False, "accept": True, "certify": False,
        "run": False, "improve": False, "participant": True, "sign_off": False, "release": False}
    assert c.get("/onboard", headers={"who": "steward"}).json()["can"] == {
        "read": False, "create": False, "draft": False, "accept": False, "certify": True,
        "run": False, "improve": False, "participant": True, "sign_off": True, "release": True}


def test_a_stale_job_cannot_release_a_previous_candidate(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward")
    monkeypatch.setattr(jobs, "state", lambda prof, model_hash: {"model_hash": model_hash})
    stale = {"id": "old", "step": "sign-off", "state": "done", "hash": "a" * 64}
    jobs.job_dir(onboard._app()).mkdir()
    jobs.job_path(onboard._app()).write_text(__import__("json").dumps(stale))
    r = c.post("/onboard/run", json={"step": "release"})
    assert r.status_code == 409 and r.json()["detail"] == "run checks for this candidate before sign-off or release"


def test_release_refuses_when_this_jobs_checked_candidate_is_gone(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward")
    monkeypatch.setattr(jobs, "state", lambda prof, model_hash: {"model_hash": model_hash})
    model_hash = "b" * 64
    jobs.job_dir(onboard._app()).mkdir()
    jobs.job_path(onboard._app()).write_text(__import__("json").dumps(
        {"id": "signed", "step": "sign-off", "state": "done", "hash": model_hash,
         "checked_hash": model_hash, "checks_id": "checks"}))
    r = c.post("/onboard/run", json={"step": "release"})
    assert r.status_code == 409 and r.json()["detail"] == "the checked candidate is no longer available"


def test_store_release_failure_is_503_and_discloses_no_store_address(monkeypatch, tmp_path, caplog):
    c = client(monkeypatch, tmp_path, who="steward")
    monkeypatch.setenv("STORE_URL", "gs://private-project-kge-store")
    model_hash = "b" * 64
    monkeypatch.setattr(jobs, "state", lambda prof, candidate: {"model_hash": candidate})
    monkeypatch.setattr(jobs, "load", lambda app: {"id": "signed", "step": "sign-off", "state": "done",
                                                   "hash": model_hash, "checked_hash": model_hash,
                                                   "checks_id": "checks"})
    monkeypatch.setattr(jobs, "the_store", lambda: SimpleNamespace(
        get=lambda key: (_ for _ in ()).throw(onboard.StoreError("gs://private-project-kge-store refused"))))
    response = c.post("/onboard/run", json={"step": "release"})
    assert response.status_code == 503
    assert response.json()["detail"] == "the onboarding store could not validate the checked candidate"
    assert "private-project" not in response.text and "private-project" in caplog.text


def test_a_recheck_supersedes_the_previous_passing_run(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    model_hash = "c" * 64
    monkeypatch.setattr(jobs, "state", lambda prof, candidate: {"model_hash": candidate})
    jobs.job_dir(onboard._app()).mkdir()
    jobs.job_path(onboard._app()).write_text(__import__("json").dumps(
        {"id": "passed", "step": "checks", "state": "done", "hash": model_hash,
         "checked_hash": model_hash, "checks_id": "passed"}))

    class Thread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(jobs.threading, "Thread", Thread)
    job = c.post("/onboard/run", json={"step": "checks"}).json()["job"]
    assert job["checked_hash"] is None and job["checks_id"] is None


def test_cloud_run_client_starts_the_own_job_without_overrides(monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.path, body))
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"name":"projects/p/locations/r/operations/one"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv("CLOUD_RUN_API", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("VERTEX_PROJECT", "p"); monkeypatch.setenv("VERTEX_REGION", "global"); monkeypatch.setenv("RUN_REGION", "r")
    monkeypatch.setenv("K_SERVICE", "kge-meridian")
    try:
        result = jobs.RunClient().run()
    finally:
        server.shutdown(); server.server_close()
    assert seen == [("/v2/projects/p/locations/r/jobs/kge-meridian:run", b"{}")]
    assert result == "one"


def test_cloud_run_http_failure_is_summarised(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(403); self.end_headers()
            self.wfile.write(b'{"error":{"message":"project-secret detail"}}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name, value in {"CLOUD_RUN_API": f"http://127.0.0.1:{server.server_port}", "VERTEX_PROJECT": "p",
                        "VERTEX_REGION": "global", "RUN_REGION": "r", "K_SERVICE": "kge-meridian"}.items():
        monkeypatch.setenv(name, value)
    try:
        try:
            jobs.RunClient().run()
            assert False, "the failed operation must be raised"
        except jobs.RunError as exc:
            assert str(exc) == "job service returned HTTP 403" and "project-secret" not in str(exc)
    finally:
        server.shutdown(); server.server_close()


def test_store_backed_page_starts_one_cloud_job_at_a_time(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setenv("STORE_URL", f"file://{tmp_path}/store")
    monkeypatch.setenv("VERTEX_PROJECT", "model-project")
    monkeypatch.setenv("VERTEX_REGION", "global")
    monkeypatch.setenv("RUN_REGION", "europe-west2")
    monkeypatch.setenv("K_SERVICE", "kge-meridian")
    monkeypatch.setattr(jobs.RunClient, "run", lambda self: "opaque-one")
    first = c.post("/onboard/run", json={"step": "checks"})
    assert first.status_code == 202 and first.json()["job"]["execution"] == "opaque-one"
    request = json.loads((tmp_path / "store/clients/meridian/onboard/request.json").read_text())
    assert request["command"] == "onboard" and request["principal"] == "engineer" and request["hash"] is None
    second = c.post("/onboard/run", json={"step": "checks"})
    assert second.status_code == 409 and "already running" in second.json()["detail"]


def test_store_backed_running_job_past_the_execution_timeout_becomes_stale_and_may_be_superseded(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    store = tmp_path / "store"
    monkeypatch.setenv("STORE_URL", f"file://{store}")
    first = c.post("/onboard/run", json={"step": "checks"})
    assert first.status_code == 202
    path = store / "clients/meridian/onboard/job.json"
    job = json.loads(path.read_text())
    job["started_at"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(job))

    stale = c.get("/onboard").json()["job"]
    assert stale["state"] == "stale" and "185 minutes" in stale["message"]
    replacement = c.post("/onboard/run", json={"step": "checks"})
    assert replacement.status_code == 202
    assert replacement.json()["job"]["id"] != stale["id"]


def test_store_backed_running_job_within_the_three_hour_execution_timeout_stays_running(monkeypatch, tmp_path):
    """The job may run three hours (the run module's job_timeout); its marker turns stale only five minutes past that."""
    c = client(monkeypatch, tmp_path)
    store = tmp_path / "store"
    monkeypatch.setenv("STORE_URL", f"file://{store}")
    assert c.post("/onboard/run", json={"step": "checks"}).status_code == 202
    path = store / "clients/meridian/onboard/job.json"
    for minutes, state in ((180, "running"), (186, "stale")):
        job = json.loads(path.read_text())
        job.update(state="running", started_at=(datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        path.write_text(json.dumps(job))
        assert c.get("/onboard").json()["job"]["state"] == state, minutes


def test_cloud_job_start_failure_consumes_the_exact_request(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    store = tmp_path / "store"
    for name, value in {"STORE_URL": f"file://{store}", "VERTEX_PROJECT": "p", "VERTEX_REGION": "global",
                        "RUN_REGION": "r", "K_SERVICE": "kge-meridian"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(jobs.RunClient, "run", lambda self: (_ for _ in ()).throw(jobs.RunError("refused")))
    response = c.post("/onboard/run", json={"step": "checks"})
    assert response.status_code == 409
    assert (store / "clients/meridian/onboard/request.json").read_bytes() == b""
    assert json.loads((store / "clients/meridian/onboard/job.json").read_text())["state"] == "failed"


def test_cloud_release_poll_fetches_and_switches_to_the_store_current(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward")
    monkeypatch.setenv("STORE_URL", f"file://{tmp_path}/store")
    for name, value in {"VERTEX_PROJECT": "p", "RUN_REGION": "r", "K_SERVICE": "kge-meridian"}.items():
        monkeypatch.setenv(name, value)
    released = "e" * 64
    store = tmp_path / "store"
    (store / "releases/meridian").mkdir(parents=True)
    (store / "releases/meridian/current").write_text(released + "\n")
    (store / "clients/meridian/onboard").mkdir(parents=True)
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(
        {"id": "release", "step": "release", "state": "done", "hash": released}))
    monkeypatch.setattr(jobs, "pull_config", lambda store_, app: onboard._app().PROFILE)
    monkeypatch.setattr(jobs, "pull_work", lambda store_, app, prof: None)
    monkeypatch.setattr(jobs, "state", lambda prof, model_hash: {"model_hash": model_hash})
    fetched = []
    monkeypatch.setattr(jobs, "fetch", lambda store_, app, prof: fetched.append(prof.client) or released)
    switched = []
    onboard._app().switch = lambda: switched.append(True)
    response = c.get("/onboard")
    assert response.status_code == 200
    assert fetched == ["meridian"] and switched == [True]


def test_compose_page_writes_the_request_and_reads_the_jobs_result(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    store = tmp_path / "store"
    monkeypatch.setenv("STORE_URL", f"file://{store}")

    started = c.post("/onboard/run", json={"step": "checks"})
    assert started.status_code == 202
    assert started.json()["said"].startswith(
        "Under Docker Compose run: docker compose run --rm job onboard-request"
    )
    assert started.json()["command"] == "docker compose run --rm job onboard-request"
    request = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    assert request["command"] == "onboard" and request["principal"] == "engineer"

    finished = {"id": request["id"], "step": "checks", "by": "engineer", "state": "done",
                "started_at": request["timestamp"], "finished_at": request["timestamp"],
                "hash": None, "checked_hash": None, "checks_id": None, "message": "job execution completed"}
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(finished))
    assert c.get("/onboard").json()["job"] == finished


def test_compose_page_can_request_checks_sign_off_and_release_from_stored_work(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward")
    store = tmp_path / "store"
    monkeypatch.setenv("STORE_URL", f"file://{store}")
    model_hash = "d" * 64
    monkeypatch.setattr(jobs, "state", lambda prof, candidate: {"model_hash": candidate})

    checks = c.post("/onboard/run", headers={"who": "engineer"}, json={"step": "checks"})
    assert checks.status_code == 202 and "onboard-request" in checks.json()["command"]
    first = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    completed = {"id": first["id"], "step": "checks", "by": "engineer", "state": "done",
                 "started_at": first["timestamp"], "finished_at": first["timestamp"], "hash": model_hash,
                 "checked_hash": model_hash, "checks_id": first["id"], "message": "job execution completed"}
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(completed))

    signed = c.post("/onboard/run", json={"step": "sign-off"})
    assert signed.status_code == 202 and "sign-off" in signed.json()["said"]
    second = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(
        {**completed, "id": second["id"], "step": "sign-off", "by": "steward"}))

    archive = io.BytesIO()
    manifest_name = f"build/artifacts/meridian/{model_hash}/manifest.json"
    with tarfile.open(fileobj=archive, mode="w:gz") as output:
        payload = json.dumps({"client": "meridian", "model_hash": model_hash}).encode()
        member = tarfile.TarInfo(manifest_name); member.size = len(payload)
        output.addfile(member, io.BytesIO(payload))
    (store / "work").mkdir()
    (store / "work/meridian.tar.gz").write_bytes(archive.getvalue())

    released = c.post("/onboard/run", json={"step": "release"})
    assert released.status_code == 202 and "release" in released.json()["said"]
    request = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    assert request["command"] == "release" and request["hash"] == model_hash


def test_an_update_items_change_reaches_the_page():
    changed = {"kind": "changed", "was": "f00dfacecafebeef", "details": ["expected figure 1200.00 -> 1650.00"]}
    item = page_item({"id": "Q", "kind": "question", "state": "drafted", "change": changed, "expect": {"figure": "1650.00"}})
    assert item["change"] == changed
    assert page_item({"id": "T", "kind": "term", "state": "certified"})["change"] is None, "an unchanged item has none"


def test_the_pages_upload_for_a_released_unit_stages_an_update(monkeypatch, tmp_path):
    """The page's DDL and CSV upload is the same command as the first time: for a released unit, different files are an
    update. The page signs in as engineer, the name the command core checks against its roles."""
    import shutil
    from kge.estate import onboard as core
    from kge.pipeline import unit_history
    demo = Path(__file__).resolve().parents[2]
    c = client(monkeypatch, tmp_path)
    profile = onboard._app().PROFILE
    profile.clients, profile.warehouses = tmp_path / "clients", tmp_path / "warehouses"
    profile.identities = demo / "deploy/meridian/identities.yaml"
    (profile.clients / "meridian").mkdir(parents=True)
    shutil.copy(demo / "clients/meridian/pack.yaml", profile.clients / "meridian/pack.yaml")
    monkeypatch.setattr(onboard.core, "create", core.create)
    monkeypatch.setattr(core.common, "permissions", lambda prof, who, writable_=True: {
        "accept": who == "business", "certify": who == "steward"})
    monkeypatch.setattr(core.source, "require_process", lambda *args: None)
    monkeypatch.setattr(core.drafting, "require_process", lambda *args: None)
    ddl = (demo / "examples/leasing/schema.sql").read_text()
    csv = {p.name: p.read_text() for p in (demo / "examples/leasing/data").glob("*.csv")}
    core.create(profile, "leasing", label="Asset Finance", rows="csv", csv={k: v.encode() for k, v in csv.items()},
                ddl_text=ddl, as_="engineer")
    core.draft(profile, "leasing", "engineer", "2026-10-17")
    unit_history.record(profile, "release", "steward", {"model_hash": "a" * 64}, ["leasing"])
    body = {"unit": "leasing", "label": "Asset Finance", "ddl": ddl, "rows": "csv",
            "csv": {**csv, "rental.csv": csv["rental.csv"] + "121,1,2025-06-30,38.75\n"}}
    response = c.post("/onboard/source", json=body)
    assert response.status_code == 200, response.text
    made = response.json()
    assert made["updated"] is True and made["version"] == 2 and made["created"] is False
    assert made["said"] == "update v2 of leasing staged; the live version is unchanged: draft it to see what changed"
    assert "warehouse" not in made


def test_a_bigquery_job_state_that_cannot_be_read_is_a_clear_503_not_no_job(monkeypatch, tmp_path):
    import urllib.error
    c = client(monkeypatch, tmp_path)
    monkeypatch.setenv("APP_STATE_BACKEND", "bigquery")
    monkeypatch.delenv("STORE_URL", raising=False)
    for failure in (RuntimeError("BigQuery state error (HTTP 403): denied"), ValueError("not a project"),
                    urllib.error.URLError("no route to bigquery.googleapis.com")):
        def job(failure=failure):
            raise failure
        monkeypatch.setattr(jobs.appstate, "job_backend", lambda prof: SimpleNamespace(job=job))
        r = c.get("/onboard")
        assert r.status_code == 503, (failure, r.status_code)
        assert r.json()["detail"] == "the onboarding store could not read the onboarding job state"
        assert "bigquery.googleapis.com" not in r.text and "denied" not in r.text


def test_a_bigquery_job_state_that_cannot_be_opened_is_the_same_503(monkeypatch, tmp_path):
    """Constructing the job's BigQuery state can fail too (a dataset name it refuses, a client it cannot make): that is
    the same safe 503 as a failed read on GET /onboard, and as a failed write when the job is saved (a Copilot finding on
    #97), never a 500 or the backend's own words."""
    c = client(monkeypatch, tmp_path)
    monkeypatch.setenv("APP_STATE_BACKEND", "bigquery")
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge-pilot!")
    r = c.get("/onboard")
    assert r.status_code == 503 and r.json()["detail"] == "the onboarding store could not read the onboarding job state"
    assert "kge-pilot" not in r.text
    with pytest.raises(jobs.JobRefused) as refused:
        jobs.save(onboard._app(), {"id": "j", "state": "running"})
    assert (refused.value.status, str(refused.value)) == (503, "the onboarding store could not save the onboarding job state")
    monkeypatch.delenv("BIGQUERY_DATASET_PREFIX")
    for failure in (RuntimeError("no credentials"), ValueError("not a project"), OSError("unreachable")):
        def opened(prof, failure=failure):
            raise failure
        monkeypatch.setattr(jobs.appstate, "job_backend", opened)
        r = c.get("/onboard")
        assert r.status_code == 503 and r.json()["detail"] == "the onboarding store could not read the onboarding job state"
        with pytest.raises(jobs.JobRefused, match="could not save the onboarding job state"):
            jobs.save(onboard._app(), {"id": "j", "state": "running"})

    def refused_write(job):
        raise RuntimeError("BigQuery state error (HTTP 403): denied")
    monkeypatch.setattr(jobs.appstate, "job_backend", lambda prof: SimpleNamespace(save_job=refused_write))
    with pytest.raises(jobs.JobRefused, match="could not save the onboarding job state"):
        jobs.save(onboard._app(), {"id": "j", "state": "running"})


def test_a_job_read_while_it_starts_or_finishes_is_never_taken_for_interrupted(monkeypatch, tmp_path):
    """The two orderings a GET /onboard can meet a job in, driven in turn. Start: the job's first save, then a read before
    the job's thread is under way. End: a read of the file while the job runs, then the job's last save and its id let
    go, before the read asks which jobs run. Neither reads it as interrupted; a running job no process runs is."""
    import json
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "state", lambda prof, candidate: {"model_hash": candidate})
    jobs.job_dir(onboard._app()).mkdir()

    class Thread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(jobs.threading, "Thread", Thread)
    real_save, seen = jobs.save, []

    def save_then_read(app, job):
        real_save(app, job)
        seen.append(jobs.load(onboard._app())["state"])
    monkeypatch.setattr(jobs, "save", save_then_read)
    job = c.post("/onboard/run", json={"step": "checks"}).json()["job"]
    assert seen == ["running"] and json.loads(jobs.job_path(onboard._app()).read_text())["state"] == "running", "start"
    monkeypatch.setattr(jobs, "save", real_save)

    path = jobs.job_path(onboard._app())
    running = path.read_text()
    finished = {**json.loads(running), "state": "done", "finished_at": jobs.now()}

    class FinishesWhileRead:
        def read_text(self):
            path.write_text(json.dumps(finished))
            jobs.active.discard(job["id"])
            return running
    monkeypatch.setattr(jobs, "job_path", lambda app: FinishesWhileRead())
    assert jobs.load(onboard._app())["state"] == "running"
    monkeypatch.undo()
    assert json.loads(path.read_text())["state"] == "done", "end: the finished job is left done"

    path.write_text(running)
    jobs.active.discard(job["id"])
    c = client(monkeypatch, tmp_path)
    assert jobs.load(onboard._app())["state"] == "interrupted", "a running job no process runs is interrupted"


def test_app_state_that_cannot_be_signed_in_to_is_the_store_s_503_on_every_route(monkeypatch, tmp_path, caplog):
    """Grok m2 on #97: the BigQuery app state's StateUnavailable (no credentials, no project), raised under a route's
    operation (draft, accept, a run of the checks through core.show) or its role check, is the onboarding store's 503,
    never a bare 500 with the backend's words."""
    from kge.ports import StateUnavailable
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "start", lambda *a: {"id": "j"})

    def unavailable(*args, **kwargs):
        raise StateUnavailable("no credentials for project private-project-x")
    for name in ("draft", "accept", "show"):
        monkeypatch.setattr(onboard.core, name, unavailable)
    calls = [("/onboard/draft", {"unit": "leasing"}, "engineer"),
             ("/onboard/accept", {"unit": "leasing", "items": "all"}, "business"),
             ("/onboard/run", {"step": "checks"}, "engineer")]
    for path, body, who_ in calls:
        r = c.post(path, headers={"who": who_}, json=body)
        assert r.status_code == 503, (path, r.status_code, r.text)
        assert r.json()["detail"] == "the onboarding store could not perform the operation"
        assert "private-project-x" not in r.text
    assert "private-project-x" in caplog.text
    monkeypatch.setattr(onboard.core, "require_process", unavailable)
    r = c.post("/onboard/draft", headers={"who": "business"}, json={"unit": "leasing"})
    assert r.status_code == 503 and r.json()["detail"] == "the onboarding store could not check the role"
