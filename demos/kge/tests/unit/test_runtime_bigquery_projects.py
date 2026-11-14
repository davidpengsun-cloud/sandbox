"""Runtime data projects stay separate from the project that submits and pays for query jobs."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from kge.adapters.bigquery import BigQueryLoader
from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import Allowlist
from kge.pipeline.trace import cancellable
from kge.ports import QueryLimits
from tests.unit.test_bigquery import JsonResponse


BILLING = "billing-project"
DATA = "data-project"
DATASET = "runtime_data"
SQL = "SELECT SUM(amount) FROM flow"
RESULT = {"jobComplete": True, "totalBytesProcessed": "10",
          "schema": {"fields": [{"name": "total", "type": "INTEGER"}]},
          "rows": [{"f": [{"v": "250"}]}]}


class ProjectRest:
    """Only the data project owns this dataset; query jobs belong to the billing project."""

    def __init__(self, data_project):
        self.data_project = data_project
        self.calls = []

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path.removeprefix("/bigquery/v2/projects/")
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.method, path, body))
        if "/datasets/" in path:
            if path != f"{self.data_project}/datasets/{DATASET}":
                raise HTTPError(request.full_url, 404, "dataset not found", {}, None)
            return JsonResponse({"location": "EU"})
        assert path.startswith(f"{BILLING}/"), path
        if request.method == "POST":
            query = body.get("configuration", {}).get("query", body)
            assert query["defaultDataset"] == {"projectId": self.data_project, "datasetId": DATASET}
            if path.endswith("/jobs"):
                assert body["jobReference"]["projectId"] == BILLING
                assert body["jobReference"]["location"] == "EU"
                return JsonResponse({"jobReference": body["jobReference"]})
            assert path == f"{BILLING}/queries"
        else:
            assert path.startswith(f"{BILLING}/queries/")
        return JsonResponse(RESULT)

    @property
    def jobs(self):
        return [body for method, _, body in self.calls if method == "POST"]

    @property
    def lookups(self):
        return [path for _, path, _ in self.calls if "/datasets/" in path]


@pytest.fixture
def rest(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("BIGQUERY_MAX_BYTES_BILLED", "12345")
    monkeypatch.setattr(BigQueryLoader, "token", lambda self: ("offline", self._project or BILLING))

    def install(data_project):
        fake = ProjectRest(data_project)
        monkeypatch.setattr("kge.adapters.http.open_http", fake.open)
        return fake

    return install


def runtime(location):
    manager = RuntimeManager(SimpleNamespace(units=["u"], curated=[], project=BILLING))
    manager.runtimes["live"] = {"id": "live", "dialect": "bigquery", "kind": "external",
                                "location": location, "billing_project": BILLING, "units": ["u"]}
    manager.rt_tables = {("live", "flow"): {"table": "FLOW"}}
    manager.rt_columns = {("live", "flow"): {"amount": {"column": "net_amount", "type": "INTEGER"}}}
    manager.ref_table_units = {"flow": "u"}
    schema = {"flow": {"amount"}}
    allow = Allowlist("u", set(schema), schema, schema=schema)
    manager.reference_allowlists["u"] = allow
    return manager, allow


@pytest.mark.parametrize("location,data_project", [(f"{DATA}.{DATASET}", DATA), (DATASET, BILLING)])
@pytest.mark.parametrize("entry", ["execute_statement", "resolve_reference_sql"])
def test_runtime_stream_and_blocking_share_data_project_and_two_job_budget(rest, location, data_project, entry):
    executions = []
    for streamed in (False, True):
        fake = rest(data_project)
        manager, allow = runtime(location)
        with cancellable() if streamed else nullcontext():
            if entry == "execute_statement":
                guard, rows, meta = manager.execute_statement({"sql": SQL, "unit": "u", "allow": allow},
                                                              runtime_id="live")
                assert guard["passed"], guard
                assert rows == [(250,)]
            else:
                meta = manager.resolve_reference_sql(SQL, unit="u", runtime_id="live", readable_units={"u"})
                assert meta["rows"] == [(250,)]
        assert len(fake.jobs) == 2
        dry, job = fake.jobs
        assert dry["dryRun"] is True
        assert dry["defaultDataset"] == {"projectId": data_project, "datasetId": DATASET}
        billed = job["configuration"]["query"] if streamed else job
        assert billed["query"] == dry["query"] == meta["sql"]
        assert billed["defaultDataset"] == dry["defaultDataset"]
        assert billed["maximumBytesBilled"] == "12345"
        assert fake.lookups == ([f"{data_project}/datasets/{DATASET}"] if streamed else [])
        executions.append((billed["query"], billed["defaultDataset"]))
    assert executions[0] == executions[1]


@pytest.mark.parametrize("dataset,data_project", [((DATA, DATASET), DATA), (DATASET, BILLING), (("", DATASET), BILLING)])
def test_bounded_loader_uses_data_location_and_default_but_bills_job_project(rest, dataset, data_project):
    fake = rest(data_project)
    loader = BigQueryLoader(project=BILLING)
    limits = QueryLimits(seconds=60, scan_bytes=12345)
    loader.run("SELECT SUM(net_amount) FROM FLOW", dataset_id=dataset, dry_run=True, limits=limits)
    result = loader.run("SELECT SUM(net_amount) FROM FLOW", dataset_id=dataset, limits=limits)
    assert result == {"schema": RESULT["schema"], "rows": RESULT["rows"]}
    assert len(fake.jobs) == 2
    dry, job = fake.jobs
    assert dry["dryRun"] is True
    assert job["configuration"]["query"]["query"] == dry["query"]
    assert job["configuration"]["query"]["defaultDataset"] == dry["defaultDataset"]
    assert job["configuration"]["query"]["maximumBytesBilled"] == "12345"
    assert fake.lookups == [f"{data_project}/datasets/{DATASET}"]


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("gate", ["static", "partition"])
def test_runtime_static_and_partition_refusals_send_no_bigquery_requests(rest, streamed, gate):
    fake = rest(DATA)
    manager, allow = runtime(f"{DATA}.{DATASET}")
    sql = "SELECT * FROM flow" if gate == "static" else SQL
    if gate == "partition":
        manager.partitioned = {"flow": "month_start"}
    with cancellable() if streamed else nullcontext():
        guard, rows, _ = manager.execute_statement({"sql": sql, "unit": "u", "allow": allow}, runtime_id="live")
    assert guard["failed_at"] == gate
    assert rows is None
    assert fake.calls == []
