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
    people = object()

    def can_write_terms(self, who):
        return who == "business@example.com"

    def may(self, who, process):
        return who == "steward@example.com" and process == "certification"


def client(monkeypatch, tmp_path, who="engineer@example.com", writable=True):
    profile = SimpleNamespace(id="p", client="meridian", artifacts=tmp_path / "artifacts",
                              run_dir=lambda: tmp_path, model_hash=lambda: "a" * 64)
    profile.serve = lambda: profile
    profile.graph_path = lambda: tmp_path / "stores" / profile.model_hash() / "graph.ladybug"
    profile.at = lambda h: SimpleNamespace(graph_path=lambda: tmp_path / "stores" / h / "graph.ladybug")
    engine = SimpleNamespace(g=Graph(), profile=profile)
    fake = SimpleNamespace(PROFILE=profile, engine=lambda: engine, principal=lambda request: request.headers.get("who", who), switch=lambda: None)
    monkeypatch.setattr(onboard, "_app", lambda: fake)
    monkeypatch.setattr(jobs, "pull_config", lambda store, root: profile)
    monkeypatch.setattr(jobs, "pull_work", lambda store, root, prof: None)
    monkeypatch.setattr(onboard.core, "writable", lambda prof: (writable, "read-only until configured" if not writable else ""))
    monkeypatch.setattr(onboard.core, "permissions", lambda prof, principal, writable_=True, reader=None: {
        "read": principal in {"business@example.com", "engineer@example.com"}, "create": writable_ and principal == "engineer@example.com",
        "draft": writable_ and principal == "engineer@example.com", "accept": writable_ and principal == "business@example.com",
        "certify": writable_ and principal == "steward@example.com", "run": writable_ and principal == "engineer@example.com",
        "improve": writable_ and principal == "engineer@example.com",
        "participant": principal in {"business@example.com", "engineer@example.com", "steward@example.com"},
        "sign_off": writable_ and principal == "steward@example.com", "release": writable_ and principal == "steward@example.com"})
    def require_process(prof, principal, process, reader=None):
        allowed = {"drafting": {"engineer@example.com"}, "building": {"engineer@example.com"},
                   "improving": {"engineer@example.com"}}
        if principal not in allowed[process]:
            raise onboard.core.OnboardError(403, f"{principal} is not a {process} identity")
    monkeypatch.setattr(onboard.core, "require_process", require_process)
    monkeypatch.setattr(onboard.core, "show", lambda prof: {"units": []})
    monkeypatch.setattr(jobs, "candidate_permissions", lambda prof, model_hash, principal, reader=None: {
        "participant": principal in {"business@example.com", "engineer@example.com", "steward@example.com"},
        "sign_off": principal == "steward@example.com", "release": principal == "steward@example.com"})
    app = FastAPI(); app.include_router(onboard.router)
    return TestClient(app)


def test_roles_read_only_size_and_malformed_bodies(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard.core, "tables", lambda *a, **k: {"unit": "u", "tables": [], "rows": {"count": 0}})
    body = {"unit": "leasing", "label": "Asset Finance", "ddl": "CREATE TABLE rental (id INTEGER)", "rows": "invented"}
    assert c.post("/onboard/tables", json=body).status_code == 200
    assert c.post("/onboard/tables", json={**body, "ddl": "x" * (256 * 1024 + 1)}).status_code == 413
    assert c.post("/onboard/tables", json={"unit": 3}).status_code == 422
    assert c.post("/onboard/tables", headers={"who": "analyst@example.com"}, json=body).status_code == 403

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
    r = c.post("/onboard/certify", headers={"who": "steward@example.com"}, json={"unit": "leasing", "items": "all"})
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
    assert c.get("/onboard", headers={"who": "business@example.com"}).json()["can"] == {
        "read": True, "create": False, "draft": False, "accept": True, "certify": False,
        "run": False, "improve": False, "participant": True, "sign_off": False, "release": False, "rollback": False}
    assert c.get("/onboard", headers={"who": "steward@example.com"}).json()["can"] == {
        "read": False, "create": False, "draft": False, "accept": False, "certify": True,
        "run": False, "improve": False, "participant": True, "sign_off": True, "release": True, "rollback": True}


def test_a_stale_job_cannot_release_a_previous_candidate(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    monkeypatch.setattr(jobs, "state", lambda prof, model_hash: {"model_hash": model_hash})
    stale = {"id": "old", "step": "sign-off", "state": "done", "hash": "a" * 64}
    jobs.job_dir(onboard._app()).mkdir()
    jobs.job_path(onboard._app()).write_text(__import__("json").dumps(stale))
    r = c.post("/onboard/run", json={"step": "release"})
    assert r.status_code == 409 and r.json()["detail"] == "run checks for this candidate before sign-off or release"


def test_release_refuses_when_this_jobs_checked_candidate_is_gone(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    monkeypatch.setattr(jobs, "state", lambda prof, model_hash: {"model_hash": model_hash})
    model_hash = "b" * 64
    jobs.job_dir(onboard._app()).mkdir()
    jobs.job_path(onboard._app()).write_text(__import__("json").dumps(
        {"id": "signed", "step": "sign-off", "state": "done", "hash": model_hash,
         "checked_hash": model_hash, "checks_id": "checks"}))
    r = c.post("/onboard/run", json={"step": "release"})
    assert r.status_code == 409 and r.json()["detail"] == "the checked candidate is no longer available"


def test_store_release_failure_is_503_and_discloses_no_store_address(monkeypatch, tmp_path, caplog):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
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
    assert request["command"] == "onboard" and request["principal"] == "engineer@example.com" and request["hash"] is None
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


@pytest.mark.parametrize("cloud", [False, True])
def test_cloud_release_poll_fetches_and_switches_to_the_store_current(monkeypatch, tmp_path, cloud):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    monkeypatch.setenv("STORE_URL", f"file://{tmp_path}/store")
    for name, value in {"VERTEX_PROJECT": "p", "RUN_REGION": "r", "K_SERVICE": "kge-meridian"}.items():
        if cloud:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
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
    assert request["command"] == "onboard" and request["principal"] == "engineer@example.com"

    finished = {"id": request["id"], "step": "checks", "by": "engineer@example.com", "state": "done",
                "started_at": request["timestamp"], "finished_at": request["timestamp"],
                "hash": None, "checked_hash": None, "checks_id": None, "message": "job execution completed"}
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(finished))
    assert c.get("/onboard").json()["job"] == finished


def test_compose_page_can_request_checks_sign_off_and_release_from_stored_work(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    store = tmp_path / "store"
    monkeypatch.setenv("STORE_URL", f"file://{store}")
    model_hash = "d" * 64
    monkeypatch.setattr(jobs, "state", lambda prof, candidate: {"model_hash": candidate})

    checks = c.post("/onboard/run", headers={"who": "engineer@example.com"}, json={"step": "checks"})
    assert checks.status_code == 202 and "onboard-request" in checks.json()["command"]
    first = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    completed = {"id": first["id"], "step": "checks", "by": "engineer@example.com", "state": "done",
                 "started_at": first["timestamp"], "finished_at": first["timestamp"], "hash": model_hash,
                 "checked_hash": model_hash, "checks_id": first["id"], "message": "job execution completed"}
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(completed))

    signed = c.post("/onboard/run", json={"step": "sign-off"})
    assert signed.status_code == 202 and "sign-off" in signed.json()["said"]
    second = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    (store / "clients/meridian/onboard/job.json").write_text(json.dumps(
        {**completed, "id": second["id"], "step": "sign-off", "by": "steward@example.com"}))

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
        "accept": who == "business@example.com", "certify": who == "steward@example.com"})
    monkeypatch.setattr(core.source, "require_process", lambda *args: None)
    monkeypatch.setattr(core.drafting, "require_process", lambda *args: None)
    ddl = (demo / "examples/leasing/schema.sql").read_text()
    csv = {p.name: p.read_text() for p in (demo / "examples/leasing/data").glob("*.csv")}
    core.create(profile, "leasing", label="Asset Finance", rows="csv", csv={k: v.encode() for k, v in csv.items()},
                ddl_text=ddl, as_="engineer@example.com")
    core.draft(profile, "leasing", "engineer@example.com", "2026-10-17")
    unit_history.record(profile, "release", "steward@example.com", {"model_hash": "a" * 64}, ["leasing"])
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
    calls = [("/onboard/draft", {"unit": "leasing"}, "engineer@example.com"),
             ("/onboard/accept", {"unit": "leasing", "items": "all"}, "business@example.com"),
             ("/onboard/run", {"step": "checks"}, "engineer@example.com")]
    for path, body, who_ in calls:
        r = c.post(path, headers={"who": who_}, json=body)
        assert r.status_code == 503, (path, r.status_code, r.text)
        assert r.json()["detail"] == "the onboarding store could not perform the operation"
        assert "private-project-x" not in r.text
    assert "private-project-x" in caplog.text
    monkeypatch.setattr(onboard.core, "require_process", unavailable)
    r = c.post("/onboard/draft", headers={"who": "business@example.com"}, json={"unit": "leasing"})
    assert r.status_code == 503 and r.json()["detail"] == "the onboarding store could not check the role"


@pytest.mark.parametrize("phase,visible", [
    ("checks", True), ("sign-off", True), ("release-running", True),
    ("release-done", False), ("released", False), ("unloaded", False),
    ("checks-running", False), ("checks-failed", False),
    ("current-already", False), ("current-already-in-log", False), ("rollback", False),
])
def test_local_status_declares_and_returns_candidate_changes(monkeypatch, tmp_path, phase, visible):
    from kge.graph import history
    c = client(monkeypatch, tmp_path)
    record = {"lock": {"written": True, "changes": [
        {"source": "wealth", "table": "<X>", "column": None, "change": "table-added", "was": None, "now": {}}]},
        "estate": {"rebuilt": True, "why": "fixture-changed", "version": 2, "fixture_sha256": "fixture"},
        "reloaded": True}
    monkeypatch.setenv("STORE_URL", "")
    monkeypatch.setattr(jobs, "current_profile", lambda app: app.PROFILE)
    monkeypatch.setattr(jobs, "follow_release", lambda *a: None)
    monkeypatch.setattr(history, "list_versions", lambda prof: {"versions": []})
    step, _, outcome = phase.partition("-")
    job = {"hash": "a" * 64, "step": step, "state": outcome or "done", "changes": None}
    if phase == "sign-off":
        job.update(step="sign-off", state="done")
    if phase.startswith("current-already"):
        job.update(step="checks", state="done")
        job["message" if phase == "current-already" else "log_tail"] = "onboard: current already"
    candidate = {"changes": record, "loaded": phase != "unloaded",
                 "released": phase == "released", "current": True}
    monkeypatch.setattr(jobs, "load", lambda app: job)
    monkeypatch.setattr(jobs, "state", lambda *a: candidate)
    response = c.get("/onboard")
    assert response.status_code == 200
    result = response.json()
    assert result["changes"] == (record if visible else None)
    assert result["candidate"]["changes"] == result["job"]["changes"] == result["changes"]
    assert job["changes"] is None
    assert candidate["changes"] == record
    assert "changes" in onboard.StatusResponse.model_json_schema()["properties"]


@pytest.mark.parametrize("step,outcome,passed,checked,visible", [
    ("checks", "done", True, True, True), ("checks", "done", False, True, False),
    ("checks", "running", True, True, False), ("checks", "failed", True, True, False),
    ("checks", "done", True, False, False), ("sign-off", "done", None, True, True),
    ("sign-off", "done", True, False, False), ("release", "running", None, True, True),
    ("release", "failed", None, True, True), ("release", "done", True, True, False),
    ("rollback", "done", True, True, False), ("rollback", "running", True, True, False),
])
def test_stored_status_reads_job_evidence_without_local_markers(monkeypatch, tmp_path, step, outcome, passed, checked, visible):
    from kge.graph import history
    c = client(monkeypatch, tmp_path)
    app = onboard._app()
    model_hash = "b" * 64
    app.PROFILE.at = lambda h: SimpleNamespace(graph_path=lambda: tmp_path / "stores" / h / "graph.ladybug")
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setattr(jobs, "current_profile", lambda app: app.PROFILE)
    monkeypatch.setattr(history, "list_versions", lambda prof: {"versions": []})
    record = {"lock": {"written": True, "changes": []}, "estate": {"rebuilt": True, "why": "fixture-changed",
              "version": 2, "fixture_sha256": "f" * 64}, "reloaded": True}
    job = {"id": "checks-1", "hash": model_hash, "checked_hash": model_hash if checked else "c" * 64,
           "checks_id": "checks-1", "step": step, "state": outcome, "gate": {"passed": passed}, "changes": record}
    jobs.save(app, job)
    assert not (tmp_path / "stores").exists()
    assert jobs.state(app.PROFILE, model_hash)["loaded"] is False
    response = c.get("/onboard")
    assert response.status_code == 200
    result = response.json()
    assert result["candidate"]["loaded"] is False
    assert result["changes"] == (record if visible else None)
    assert result["candidate"]["changes"] == result["job"]["changes"] == result["changes"]
    assert jobs.load(app)["changes"] == record
    for field in ("message", "log_tail"):
        jobs.save(app, {**job, field: "onboard: current already"})
        assert c.get("/onboard").json()["changes"] is None
    jobs.save(app, {**job, "step": "release", "state": "done"})
    assert c.get("/onboard").json()["changes"] is None
    jobs.save(app, {**job, "step": "release", "state": "done", "previous": True})
    assert c.get("/onboard").json()["changes"] is None


@pytest.mark.parametrize("path,body", [("/onboard/rollback", {}), ("/onboard/run", {"step": "rollback"})])
def test_rollback_is_steward_only_and_queues_previous_release(monkeypatch, tmp_path, path, body):
    c = client(monkeypatch, tmp_path)
    store = tmp_path / "store"
    monkeypatch.setenv("STORE_URL", str(store))
    assert c.post(path, json=body).status_code == 403
    assert c.post(path, json=body, headers={"who": "business@example.com"}).status_code == 403
    response = c.post(path, json=body, headers={"who": "steward@example.com"})
    assert response.status_code == 202
    request = json.loads((store / "clients/meridian/onboard/request.json").read_text())
    assert request["command"] == "release" and request["previous"] is True
    assert request["hash"] is None
    assert request["checked_hash"] is None


def test_current_hash_after_reload_cannot_skip_candidate_checks(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "state", lambda *a: {"released": False, "current": True})
    response = c.post("/onboard/run", json={"step": "release", "hash": "a" * 64})
    assert response.status_code == 409
    assert response.json()["detail"] == "run checks for this candidate before sign-off or release"


@pytest.mark.parametrize("model_hash", ["a" * 64, "b" * 64])
def test_only_released_hash_gets_the_rollback_shortcut(monkeypatch, tmp_path, model_hash):
    from kge.graph import history
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "state", lambda *a: {"released": True})
    called = []
    monkeypatch.setattr(history, "check_rollback_allowed", lambda *a: called.append(a[1]))
    monkeypatch.setattr(jobs, "read_artifact", lambda *a: {"model_hash": model_hash})
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    response = c.post("/onboard/run", json={"step": "release", "hash": model_hash})
    assert response.status_code == 202 and called == [model_hash]
    jobs.active.discard(response.json()["job"]["id"])


def test_rollback_local_command_and_input(monkeypatch, tmp_path):
    from kge.services.onboard import service
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    assert service.COMMANDS["rollback"][-1] == "--previous"
    response = c.post("/onboard/run", json={"step": "rollback", "hash": "a" * 64})
    assert response.status_code == 422


def test_progress_uses_the_cli_step_count():
    from kge.pipeline.onboard_steps import ONBOARD_STEPS
    from kge.services.onboard import service
    match = service.PROGRESS.search(f"onboard: [{ONBOARD_STEPS}/{ONBOARD_STEPS}] margin")
    assert match and int(match.group(1)) == ONBOARD_STEPS


def test_change_lines_describe_schema_objects_as_words():
    from kge.services.onboard.service import change_lines
    old = {"columns": ["x"], "ref_table": "old", "ref_columns": ["id"]}
    new = {"columns": ["x", "y"], "ref_table": "new", "ref_columns": ["id", "kind"]}
    changes = [
        {"column": "c", "change": "column-added", "was": None,
         "now": {"name": "c", "type": "INTEGER", "description": None, "ordinal": 1}},
        {"column": "d", "change": "column-removed", "now": None,
         "was": {"name": "d", "type": "VARCHAR", "description": None, "ordinal": 2}},
        {"column": None, "change": "key-changed", "was": {"primary_key": ["x"], "foreign_keys": [old]},
         "now": {"primary_key": ["x", "y"], "foreign_keys": [new]}},
        {"column": None, "change": "key-changed", "was": {"primary_key": ["x", "y"], "foreign_keys": []},
         "now": {"primary_key": [], "foreign_keys": []}},
    ]
    assert change_lines({"lock": {"changes": [{"source": "a", "table": "b", **c} for c in changes]}}) == [
        "Column a.b.c added (INTEGER)", "Column a.b.d removed (was VARCHAR)",
        "Primary key of a.b changed from (x) to (x, y)",
        "Foreign key of a.b (x) referencing old (id) removed",
        "Foreign key of a.b (x, y) referencing new (id, kind) added",
        "Primary key of a.b changed from (x, y) to none",
    ]
    reordered = {"source": "a", "table": "b", "change": "key-changed",
                 "was": {"primary_key": [], "foreign_keys": [old, new]},
                 "now": {"primary_key": [], "foreign_keys": [new, old]}}
    assert change_lines({"lock": {"changes": [reordered]}}) == [
        "Foreign keys of a.b reordered from (x) referencing old (id); (x, y) referencing new (id, kind) "
        "to (x, y) referencing new (id, kind); (x) referencing old (id)"]


def test_candidate_changes_are_plain_english_and_escaped(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to check the page's rendering without a browser")
    page = (Path(__file__).resolve().parents[2] / "ui/index.html").read_text()
    render = page.split("function renderObChanges(record, descriptions = null) {", 1)[1].split("function renderObVersions", 1)[0]
    script = r'''
const assert = require('node:assert/strict');
const element = {innerHTML: ''};
const $ = () => element;
''' + next(line for line in page.splitlines() if line.startswith('const esc =')) + '\n' + "function renderObChanges(record, descriptions = null) {" + render + r'''
renderObChanges({lock:{changes:[
  {source:'wealth', table:'X', change:'table-added'},
  {source:'wealth', table:'Y', change:'table-removed'},
  {source:'a', table:'b', column:'c', change:'column-added', now:{name:'c',type:'INTEGER',description:null,ordinal:1}},
  {source:'a', table:'b', column:'d', change:'column-removed', was:{name:'d',type:'VARCHAR',description:null,ordinal:2}},
  {source:'a', table:'b', column:'e', change:'column-retyped', was:'INTEGER', now:'VARCHAR'},
  {source:'a', table:'b', column:'f', change:'column-described', was:'old', now:'<img onerror=alert(1)>'},
  {source:'a', table:'b', column:'g', change:'column-moved', was:1, now:2},
  {source:'a', table:'b', change:'key-changed',
    was:{primary_key:['id'],foreign_keys:[{columns:['x'],ref_table:'old',ref_columns:['id']}]},
    now:{primary_key:['id','other'],foreign_keys:[{columns:['x'],ref_table:'<img onerror=alert(1)>',ref_columns:['id']}]}}
]}, estate:{rebuilt:true, why:'fixture-changed'}, reloaded:true});
for (const expected of ['Table wealth.X added', 'Table wealth.Y removed', 'Column a.b.c added (INTEGER)',
  'Column a.b.d removed (was VARCHAR)', 'Column a.b.e retyped from INTEGER to VARCHAR',
  'Primary key of a.b changed from (id) to (id, other)', 'Foreign key of a.b (x) referencing old (id) removed',
  'Foreign key of a.b (x) referencing &lt;img onerror=alert(1)&gt; (id) added',
  'Description of column a.b.f changed from old to &lt;img', 'Column a.b.g moved from position 1 to 2',
  'Estate rebuilt: the fixture files changed', 'Reloaded the same model', '&lt;img']) assert.ok(element.innerHTML.includes(expected), expected);
assert.ok(!element.innerHTML.includes('<img'));
assert.ok(!element.innerHTML.includes('[object Object]'));
assert.ok(!element.innerHTML.includes('primary_key'));
assert.ok(!element.innerHTML.includes('{'));
const first = {columns:['x'],ref_table:'old',ref_columns:['id']};
const second = {columns:['y'],ref_table:'new',ref_columns:['id']};
renderObChanges({lock:{changes:[{source:'a',table:'b',change:'key-changed',
  was:{primary_key:[],foreign_keys:[first,second]},now:{primary_key:[],foreign_keys:[second,first]}}]}});
assert.ok(element.innerHTML.includes('Foreign keys of a.b reordered from (x) referencing old (id); (y) referencing new (id) to (y) referencing new (id); (x) referencing old (id)'));
renderObChanges({lock:{changes:[]}}, ['Column a.b.c added (<img onerror=alert(1)>)']);
assert.ok(element.innerHTML.includes('Column a.b.c added (&lt;img'));
assert.ok(!element.innerHTML.includes('<img'));
renderObChanges({lock:{changes:[]}, estate:{rebuilt:false, why:'unchanged'}, reloaded:false});
assert.ok(element.innerHTML.includes('No schema changes'));
assert.ok(element.innerHTML.includes('Estate not rebuilt: the fixture files are unchanged'));
renderObChanges({inputs:{ignored:['clients/c/<img>'],suggestion:'inputs:\n  catalogue: <script>',
  upload:{sha256:'a'.repeat(64)}},people:{reloaded:true,added:['<img>'],removed:['old']},rechecked:true});
for (const expected of ['Ignored: clients/c/&lt;img&gt;', '<pre>Declarations to add\ninputs:\n  catalogue: &lt;script&gt;</pre>',
  'Upload aaaaaaaaaaaa', 'Checked again: the last check did not pass']) {
  assert.ok(element.innerHTML.includes(expected), expected);
}
assert.ok(!element.innerHTML.includes('<img>') && !element.innerHTML.includes('<script>'));
renderObChanges({inputs:{suggestion:'unused'}}, ['Declarations to add\ninputs:\n  sets: [<img>]']);
assert.ok(element.innerHTML.includes('<pre>Declarations to add\ninputs:\n  sets: [&lt;img&gt;]</pre>'));
assert.ok(!element.innerHTML.includes('unused'));
renderObChanges(null);
assert.equal(element.innerHTML, '');
'''
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    assert 'href="${esc(job.report)}"' in page
    draw = "async function drawOnboard() {" + page.split("async function drawOnboard() {", 1)[1].split("function updateUnit", 1)[0]
    lifecycle = r'''
const assert = require('node:assert/strict');
const elements = new Map();
const $ = key => { if (!elements.has(key)) elements.set(key, {innerHTML:'', dataset:{}}); return elements.get(key); };
const document = {querySelectorAll:()=>[], activeElement:null};
let OB, OB_POLL, ME = {id:'steward@example.com'}, response;
const canSee = () => true;
const api = async () => response;
const clearObStarted = () => {};
const renderObUnits = () => '';
const renderObProposal = () => {};
const renderObNotes = () => {};
const renderObVersions = () => {};
const jobStrip = () => '';
const drawNextStep = () => {};
const loadProfile = async () => {};
const loadPrincipals = async () => {};
const loadSamples = async () => {};
''' + next(line for line in page.splitlines() if line.startswith('const esc =')) + '\n' + "function renderObChanges(record, descriptions = null) {" + render + draw + r'''
(async () => {
  response = {writable:true, can:{}, units:[], changes:{reloaded:true}, candidate:{released:false}, job:{step:'checks',state:'done'}};
  await drawOnboard();
  assert.ok($('#ob-changes').innerHTML.includes('Reloaded the same model'));
  response = {...response, changes:null, job:{step:'checks', state:'done',message:'onboard: current already'}};
  await drawOnboard();
  assert.equal($('#ob-changes').innerHTML, '');
  response = {...response, job:{step:'release', state:'done'}};
  await drawOnboard();
  assert.equal($('#ob-changes').innerHTML, '');
})().catch(e => {console.error(e); process.exit(1)});
'''
    subprocess.run([node, "-e", lifecycle], check=True, capture_output=True, text=True)



def test_checked_current_hash_keeps_the_gated_release_command_and_report(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    model_hash = "a" * 64
    record = {"reloaded": True}
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": model_hash, "checked_hash": model_hash,
                                                   "checks_id": "fresh", "changes": record,
                                                   "report": "/ab/fresh/report.html"})
    monkeypatch.setattr(jobs, "state", lambda *a: {"released": False, "current": True})
    monkeypatch.setattr(jobs, "read_artifact", lambda *a: {"model_hash": model_hash})
    commands = []
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(
        start=lambda: commands.append(kwargs["args"][2])))
    response = c.post("/onboard/run", json={"step": "release", "hash": model_hash})
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["changes"] == record and job["report"] == "/ab/fresh/report.html"
    assert commands[0][-2:] == ["--hash", model_hash]
    assert "--ungated" not in commands[0] and "--previous" not in commands[0]
    jobs.active.discard(job["id"])


@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("step,same_hash,released,kept", [
    ("sign-off", True, False, True), ("release", True, False, True),
    ("release", False, False, False), ("release", True, True, False),
    ("release", False, True, False), ("rollback", True, False, False),
])
def test_job_replacement_carries_only_its_candidate_changes(monkeypatch, tmp_path, stored, step, same_hash, released, kept):
    from kge.graph import history
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    model_hash = "a" * 64
    previous = {"hash": model_hash if same_hash else "b" * 64, "checked_hash": model_hash,
                "checks_id": "fresh", "changes": {"reloaded": True}, "report": "/ab/fresh/report.html"}
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store") if stored else "")
    monkeypatch.setattr(jobs, "load", lambda app: previous)
    monkeypatch.setattr(jobs, "state", lambda *a: {"released": released})
    monkeypatch.setattr(jobs, "read_artifact", lambda *a: {"model_hash": model_hash})
    monkeypatch.setattr(jobs, "stored_candidate", lambda *a: True)
    monkeypatch.setattr(history, "check_rollback_allowed", lambda *a: None)
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    body = {"step": step, **({"hash": model_hash} if step == "release" else {})}
    response = c.post("/onboard/run", json=body)
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["changes"] == (previous["changes"] if kept else None)
    assert job["report"] == (previous["report"] if kept else None)
    jobs.active.discard(job["id"])


@pytest.mark.parametrize("closed_step", ["release", "rollback"])
def test_stored_signoff_does_not_reopen_changes_after_a_completed_release(monkeypatch, tmp_path, closed_step):
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    app = onboard._app()
    model_hash = "a" * 64
    app.PROFILE.at = lambda h: SimpleNamespace(graph_path=lambda: tmp_path / "stores" / h / "graph.ladybug")
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    jobs.save(app, {"hash": model_hash, "checked_hash": model_hash, "checks_id": "checked",
                   "step": closed_step, "state": "done", "changes": {"reloaded": True}})
    response = c.post("/onboard/run", json={"step": "sign-off"})
    assert response.status_code == 202
    assert response.json()["job"]["changes"] is None
    assert jobs.load(app)["changes"] is None


@pytest.mark.parametrize("step", ["sign-off", "release"])
@pytest.mark.parametrize("writable", [False, True])
def test_non_steward_without_candidate_is_forbidden_before_run_checks(monkeypatch, tmp_path, step, writable):
    c = client(monkeypatch, tmp_path, writable=writable)
    monkeypatch.setattr(jobs, "load", lambda app: None)
    response = c.post("/onboard/run", json={"step": step})
    assert response.status_code == 403
    assert response.json()["detail"] == "engineer@example.com is not a steward whose role performs certification"
    assert not jobs.job_path(onboard._app()).exists()


@pytest.mark.parametrize("step", ["sign-off", "release"])
@pytest.mark.parametrize("stored", [False, True])
def test_candidate_roles_use_the_live_table_without_a_roster(monkeypatch, tmp_path, step, stored):
    from dataclasses import replace
    from kge.pipeline.profile import resolve
    real_permissions = jobs.candidate_permissions
    c = client(monkeypatch, tmp_path)
    app = onboard._app()
    prof = app.PROFILE = replace(resolve(), artifacts=tmp_path / "build/artifacts", store=tmp_path / "build/store")
    model_hash = "b" * 64
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    role_file = prof.artifacts / prof.client / model_hash / "access/roles.json"
    role_file.parent.mkdir(parents=True)
    role_file.write_text(json.dumps({"roles": [
        {"id": "renamed", "processes": ["certification"]},
        {"id": "reader", "processes": ["querying"]}]}))
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store") if stored else "")
    from kge.adapters import appstate
    from kge.pipeline.access_table import Access
    table = appstate.access_state(prof)
    for username, role in (("engineer@example.com", "renamed"), ("steward@example.com", "reader")):
        table.access_put({"username": username, "name": username, "roles": [role]}, by="operator@example.com", via="job")
    app.engine().g.people = Access(table, clock=lambda: 0)
    before = table.access_grants()
    if stored:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            archive.add(role_file, arcname=role_file.relative_to(tmp_path).as_posix())
        jobs.the_store().put(f"work/{prof.client}.tar.gz", data.getvalue())
    monkeypatch.setattr(jobs, "candidate_permissions", real_permissions)
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": model_hash, "checked_hash": model_hash, "checks_id": "checked"})
    monkeypatch.setattr(jobs, "state", lambda *args: {"released": False})
    monkeypatch.setattr(jobs, "read_artifact", lambda *args: {"model_hash": model_hash})
    monkeypatch.setattr(jobs, "stored_candidate", lambda *args: True)
    launched = []
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: launched.append(True)))
    body = {"step": step}
    refused = c.post("/onboard/run", headers={"who": "steward@example.com"}, json=body)
    assert refused.status_code == 403
    assert refused.json()["detail"] == f"steward@example.com may not sign off {model_hash[:12]}: its roles do not let them certify"
    assert not launched and not jobs.job_path(app).exists()
    accepted = c.post("/onboard/run", headers={"who": "engineer@example.com"}, json=body)
    assert accepted.status_code == 202, accepted.text
    assert table.access_grants() == before
    table.access_delete("engineer@example.com", by="operator@example.com", via="job")
    app.engine().g.people.clock = lambda: 61
    assert not real_permissions(prof, model_hash, "engineer@example.com", app.engine().g.people)["sign_off"]
    jobs.active.discard(accepted.json()["job"]["id"])


@pytest.mark.parametrize("step", ["sign-off", "release"])
def test_candidate_role_dependency_checks_the_jobs_hash_before_start(monkeypatch, tmp_path, step):
    c = client(monkeypatch, tmp_path)
    model_hash = "b" * 64
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": model_hash, "checked_hash": model_hash, "checks_id": "checked"})
    seen = []

    def permissions(prof, candidate, who, reader=None):
        seen.append((candidate, who))
        return {"sign_off": who == "engineer@example.com", "release": who == "engineer@example.com"}

    monkeypatch.setattr(jobs, "candidate_permissions", permissions)
    monkeypatch.setattr(jobs, "start", lambda *args: {"job": {"state": "running"}})
    body = {"step": step, "hash": "c" * 64}
    assert c.post("/onboard/run", headers={"who": "steward@example.com"}, json=body).status_code == 403
    assert c.post("/onboard/run", headers={"who": "engineer@example.com"}, json=body).status_code == 202
    assert seen == [(model_hash, "steward@example.com"), (model_hash, "engineer@example.com")]


def test_candidate_without_role_artifact_refuses_cleanly(monkeypatch, tmp_path):
    real_permissions = jobs.candidate_permissions
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    prof = onboard._app().PROFILE
    prof.store = tmp_path / "graphs"
    prof.at = lambda h: prof
    monkeypatch.setenv("STORE_URL", "")
    monkeypatch.setattr(jobs, "candidate_permissions", real_permissions)
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": "b" * 64, "checked_hash": "b" * 64, "checks_id": "checked"})
    monkeypatch.setattr(jobs, "state", lambda *args: {})
    response = c.post("/onboard/run", json={"step": "sign-off"})
    assert response.status_code == 409
    assert response.json()["detail"] == "bbbbbbbbbbbb's candidate roles cannot be read: onboard again"


def test_status_buttons_use_candidate_permissions_and_keep_report(monkeypatch, tmp_path):
    from kge.graph import history
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    monkeypatch.setenv("STORE_URL", "")
    monkeypatch.setattr(jobs, "current_profile", lambda app: app.PROFILE)
    monkeypatch.setattr(history, "list_versions", lambda prof: {"versions": []})
    record = {"inputs": {"ignored": ["clients/c/<img>"], "suggestion": "inputs:\n  sets: [golden]",
                          "upload": {"sha256": "a" * 64}},
              "people": {"reloaded": True, "added": ["new"], "removed": ["old"]}, "rechecked": True}
    job = {"hash": "b" * 64, "state": "done", "step": "checks", "report": "/ab/check/report.html"}
    monkeypatch.setattr(jobs, "load", lambda app: job)
    monkeypatch.setattr(jobs, "state", lambda *args: {"loaded": True, "released": False, "changes": record})
    monkeypatch.setattr(jobs, "candidate_permissions", lambda *args: {"sign_off": False, "release": False})
    response = c.get("/onboard")
    assert response.status_code == 200
    data = response.json()
    assert data["can"]["sign_off"] is data["can"]["release"] is False
    assert data["job"]["report"] == job["report"]
    assert data["change_lines"] == ["Ignored: clients/c/<img>", "Declarations to add\ninputs:\n  sets: [golden]",
                                     "Upload aaaaaaaaaaaa",
                                     "Checked again: the last check did not pass"]


def test_legacy_people_changes_are_not_candidate_changes():
    from kge.services.onboard.service import change_lines
    assert change_lines({"people": {"reloaded": True, "added": [], "removed": []}}) == []
    assert change_lines({"people": {"reloaded": False}, "inputs": {"upload": None}}) == []


@pytest.mark.parametrize("missing", ["work", "roles"])
def test_stored_candidate_permissions_never_fall_back_to_local_files(monkeypatch, tmp_path, missing):
    from dataclasses import replace
    from kge.pipeline.profile import resolve
    prof = replace(resolve(), artifacts=tmp_path / "build/artifacts", store=tmp_path / "build/store")
    model_hash = "c" * 64
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    files = {"roles": (prof.artifacts / prof.client / model_hash / "access/roles.json",
                       {"roles": [{"id": "steward@example.com", "processes": ["certification"]}]}),
             "roster": (prof.store / prof.client / model_hash / "ROSTER",
                        {"bindings": [{"principal": "steward@example.com", "role": "steward@example.com"}]})}
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, (path, content) in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content))
            if name != missing:
                archive.add(path, arcname=path.relative_to(tmp_path).as_posix())
    if missing != "work":
        jobs.the_store().put(f"work/{prof.client}.tar.gz", data.getvalue())
    with pytest.raises(jobs.JobRefused) as refused:
        jobs.candidate_permissions(prof, model_hash, "steward@example.com")
    assert refused.value.status == 409
    assert str(tmp_path) not in refused.value.message


def test_upload_record_without_digest_can_be_shown():
    from kge.services.onboard.service import change_lines
    assert change_lines({"inputs": {"upload": {"prefix": "uploads/client/id"}}}) == ["Upload "]


@pytest.mark.parametrize("allowed", [False, True])
def test_rollback_status_uses_served_permissions_not_live_pack(monkeypatch, tmp_path, allowed):
    from kge.graph import history
    c = client(monkeypatch, tmp_path, who="steward@example.com")
    app = onboard._app()
    monkeypatch.setenv("STORE_URL", "")
    monkeypatch.setattr(jobs, "current_profile", lambda app: app.PROFILE)
    monkeypatch.setattr(history, "list_versions", lambda prof: {"versions": []})
    monkeypatch.setattr(jobs, "shown_candidate", lambda *args: (None, None))
    monkeypatch.setattr(app.engine().g, "may", lambda who, process: allowed)
    monkeypatch.setattr(onboard.core, "permissions", lambda *args, **kwargs: {"release": allowed, "certify": allowed, "participant": True})
    data = c.get("/onboard").json()
    assert data["can"]["rollback"] is allowed
    assert data["can"]["release"] is allowed
    if not allowed:
        assert c.post("/onboard/rollback").status_code == 403
