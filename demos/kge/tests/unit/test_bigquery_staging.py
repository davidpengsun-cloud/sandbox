"""BigQuery loads staged in the scratch area (issue #99): a load whose newline-delimited JSON is larger than
BIGQUERY_LOAD_INLINE_MAX is put as one file in the client's scratch area, tmp/<client>/ in the tmp bucket TMP_URL names,
and loaded from its gs:// URI; a smaller one, or one whose scratch area is a local folder, is sent inline as before.

    .venv/bin/python -m pytest tests/unit/test_bigquery_staging.py -q

BigQuery is a stand-in for its REST API's two load-job inserts (the multipart upload and the job with sourceUris), which
keeps each table's rows as BigQuery would read them from the newline-delimited JSON; Cloud Storage is the stand-in of
tests/gcs_stub.py, or the emulator KGE_GCS_EMULATOR names, reached through the scratch area's own store with
STORAGE_EMULATOR_HOST set, so no credentials are used. Nothing reaches Google."""
from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import urllib.request
import uuid

import pytest

from kge.adapters import bigquery
from kge.adapters.bigquery import BigQueryLoader, serialize_val
from kge.adapters.objstore import gcs as gcs_backend
from kge.adapters.objstore import open_store
from tests.gcs_stub import serve

PROJECT = "your-pilot-project-id"
SCHEMA = [{"name": "id", "type": "INT64", "mode": "NULLABLE"}, {"name": "name", "type": "STRING", "mode": "NULLABLE"},
          {"name": "amount", "type": "NUMERIC", "mode": "NULLABLE"}, {"name": "booked", "type": "DATE", "mode": "NULLABLE"},
          {"name": "terms", "type": "STRING", "mode": "REPEATED"}]


def rows(n: int) -> list[dict]:
    """n rows as the sync serialises DuckDB's values: unicode, nulls, NUMERIC, dates and a repeated column."""
    return [{"id": i, "name": None if i % 7 == 0 else f"Café №{i} — \"quoted\"\nline",
             "amount": serialize_val(decimal.Decimal(f"{i}.{i % 100:02d}")),
             "booked": serialize_val(dt.date(2026, 1 + i % 12, 1 + i % 28)), "terms": [f"t{i}", "ü"]} for i in range(n)]


class StandInBigQuery:
    """The load jobs BigQuery is sent: each table's rows, read from the multipart body's data or from the object a job's
    sourceUris names, through the scratch area's store, as BigQuery reads it as the caller's account."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.inline: list[str] = []
        self.staged: list[str] = []
        self.seen_while_loading: list[list[str]] = []

    def answer(self, request) -> bytes:
        url, body = request.full_url, request.data
        if "/upload/bigquery/v2/" in url:
            assert "uploadType=multipart" in url
            boundary = request.get_header("Content-type").split("boundary=", 1)[1]
            parts = body.split(f"--{boundary}".encode())
            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
            data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
            self.inline.append(meta["configuration"]["load"]["destinationTable"]["tableId"])
        else:
            assert url == f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/jobs"
            meta = json.loads(body)
            [uri] = meta["configuration"]["load"]["sourceUris"]
            bucket, _, name = uri[len("gs://"):].partition("/")
            data = open_store(f"gs://{bucket}").get(name)
            assert data is not None, f"{uri}: no object for the load job to read"
            self.seen_while_loading.append(open_store(f"gs://{bucket}").list())
            self.staged.append(uri)
        load = meta["configuration"]["load"]
        assert load["sourceFormat"] == "NEWLINE_DELIMITED_JSON" and load["schema"] == {"fields": SCHEMA}
        table = load["destinationTable"]["tableId"]
        loaded = [json.loads(line) for line in data.decode("utf-8").split("\n") if line]
        self.tables[table] = loaded if load["writeDisposition"] == "WRITE_TRUNCATE" else self.tables.get(table, []) + loaded
        return json.dumps({"jobReference": {"jobId": f"load-{uuid.uuid4().hex[:8]}"}, "status": {"state": "DONE"}}).encode()


class Response:
    def __init__(self, data: bytes):
        self.data, self.status = data, 200

    def read(self) -> bytes:
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def bq(monkeypatch):
    """The stand-in behind every BigQuery call the loader makes; any other call (the emulator's) goes where it was sent."""
    fake, real = StandInBigQuery(), urllib.request.urlopen
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: Response(fake.answer(request))
                        if isinstance(request, urllib.request.Request) and "bigquery.googleapis.com" in request.full_url
                        else real(request, timeout=timeout))
    monkeypatch.setattr(BigQueryLoader, "token", lambda self: ("stand-in", PROJECT))
    monkeypatch.setattr(BigQueryLoader, "get", lambda self, url, headers: {"status": {"state": "DONE"}})
    return fake


@pytest.fixture
def tmp_bucket(monkeypatch):
    """TMP_URL a bucket on a GCS emulator: KGE_GCS_EMULATOR's, else the stand-in's; a bucket no other test uses."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, ",".join(x for x in (os.environ.get(name, ""), "127.0.0.1", "localhost") if x))
    monkeypatch.setattr(gcs_backend, "BACKOFF", 0.01)
    bucket = f"t{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("TMP_URL", f"gs://{bucket}")
    real = os.environ.get("KGE_GCS_EMULATOR", "").strip()
    if real:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", real)
        yield bucket
        return
    with serve() as srv:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"127.0.0.1:{srv.server_address[1]}")
        yield bucket


def ndjson_size(batch: list[dict]) -> int:
    return len(("\n".join(json.dumps(r, ensure_ascii=False) for r in batch) + "\n").encode("utf-8"))


def test_a_load_above_the_threshold_goes_through_the_scratch_area_and_one_below_stays_inline(bq, tmp_bucket, monkeypatch):
    big, small = rows(400), rows(3)
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", str(ndjson_size(small)))
    loader = BigQueryLoader(project=PROJECT, client="meridian")

    assert loader.load_rows("kge_meridian_warehouses", "small", SCHEMA, small) == 3
    assert bq.inline == ["small"] and bq.staged == [], "at the threshold, inline"

    assert loader.load_rows("kge_meridian_warehouses", "big", SCHEMA, big) == 400
    [uri] = bq.staged
    assert uri.startswith(f"gs://{tmp_bucket}/tmp/meridian/bigquery/kge_meridian_warehouses/big/") and uri.endswith(".json")
    assert bq.seen_while_loading == [[uri[len(f"gs://{tmp_bucket}/"):]]], "one file, under the client's own prefix"
    assert bq.inline == ["small"], "above it, staged: no rows in the request"
    assert open_store(f"gs://{tmp_bucket}").list() == [], "the staged file is deleted once the job is done"
    assert bq.tables["big"] == big and bq.tables["small"] == small


def test_the_loaded_rows_are_identical_inline_and_staged(bq, tmp_bucket, monkeypatch):
    """The same rows loaded each way, a table made and then appended to, give the same table."""
    batch, more = rows(250), rows(10)
    loader = BigQueryLoader(project=PROJECT, client="meridian")
    inline, staged = str(ndjson_size(batch)), "1"
    for table, first, then in (("inline", inline, staged), ("staged", staged, inline)):
        monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", first)
        loader.load_rows("kge_meridian_graph", table, SCHEMA, batch)
        monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", then)
        loader.load_rows("kge_meridian_graph", table, SCHEMA, more, "WRITE_APPEND")
    assert bq.inline == ["inline", "staged"] and [u.split("/")[-2] for u in bq.staged] == ["inline", "staged"]
    assert bq.tables["inline"] == bq.tables["staged"] == batch + more


def test_a_large_load_stays_inline_where_the_scratch_area_is_a_local_folder(bq, tmp_path, monkeypatch):
    """No load job reads a local folder: with TMP_URL unset (the local stack, Compose) or a folder, the load goes inline
    and nothing is written to the scratch area."""
    batch = rows(50)
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "1")
    monkeypatch.delenv("TMP_URL", raising=False)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "unset"))
    BigQueryLoader(project=PROJECT, client="meridian").load_rows("kge_meridian_graph", "unset", SCHEMA, batch)
    monkeypatch.setenv("TMP_URL", str(tmp_path / "folder"))
    BigQueryLoader(project=PROJECT, client="meridian").load_rows("kge_meridian_graph", "folder", SCHEMA, batch)
    assert bq.inline == ["unset", "folder"] and bq.staged == []
    assert bq.tables["unset"] == bq.tables["folder"] == batch
    assert not any(p.is_file() for p in tmp_path.rglob("*")), "nothing staged"


def test_a_loader_with_no_client_sends_every_load_inline(bq, tmp_bucket, monkeypatch):
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "1")
    BigQueryLoader(project=PROJECT).load_rows("kge_meridian_graph", "t", SCHEMA, rows(5))
    assert bq.inline == ["t"] and bq.staged == []


def test_the_staged_file_is_deleted_when_the_load_job_fails(bq, tmp_bucket, monkeypatch):
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "1")
    monkeypatch.setattr(BigQueryLoader, "get", lambda self, url, headers: {
        "status": {"state": "DONE", "errorResult": {"message": "bad row"}}})
    with pytest.raises(RuntimeError, match="BigQuery load job failed: bad row"):
        BigQueryLoader(project=PROJECT, client="meridian").load_rows("kge_meridian_graph", "t", SCHEMA, rows(5))
    assert len(bq.staged) == 1 and open_store(f"gs://{tmp_bucket}").list() == []


def test_the_threshold_defaults_to_five_mib_and_refuses_a_mistype(monkeypatch):
    monkeypatch.delenv("BIGQUERY_LOAD_INLINE_MAX", raising=False)
    assert bigquery.inline_max() == 5 * 1024 * 1024
    monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "1048576")
    assert bigquery.inline_max() == 1048576
    for bad in ("0", "-1", "1.5", "5MB", "lots"):
        monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", bad)
        with pytest.raises(ValueError, match="^BIGQUERY_LOAD_INLINE_MAX="):
            bigquery.inline_max()


def test_the_sync_and_the_estate_give_their_loader_the_client(monkeypatch):
    """The two places a loader is made for a client's loads, the sync's main and the BigQuery estate, name the client, so
    their large loads stage under its own prefix."""
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
    assert BigQueryEstate("thornbury").loader.client == "thornbury"
    import inspect
    from kge.estate import load_bigquery
    assert "client=client" in inspect.getsource(load_bigquery.main)
