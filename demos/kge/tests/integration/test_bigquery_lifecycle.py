"""The production BigQuery lifecycle on one transport stand-in, with real versioned graph contents."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import re
import shutil
import sys
import subprocess

import pytest
import yaml

from kge import cli
from kge.adapters.bigquery import BigQueryLoader
from kge.pipeline.profile import resolve
from tests import bigquery_fake

DEMO = Path(__file__).resolve().parents[2]


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    """Only process and network transports are substituted; commands execute their real modules."""
    client = "thornbury"
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / client, clients / client)
    pack_path = clients / client / "pack.yaml"
    pack_data = yaml.safe_load(pack_path.read_text())
    pack_data["manifest"]["gates"] = {"golden_min_pass": dict.fromkeys(("within", "cross", "shelf", "abstain"), 1.0)}
    pack_path.write_text(yaml.safe_dump(pack_data))
    identities = tmp_path / "identities.yaml"
    shutil.copyfile(DEMO / "deploy" / client / "identities.yaml", identities)
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": client, "profiles": {client: {
        "client": client, "asker": "actuary", "clients": str(clients), "artifacts": str(tmp_path / "artifacts"),
        "store": str(tmp_path / "stores"), "landing": str(tmp_path / "landing"),
        "warehouses": str(tmp_path / "estate" / "warehouses"), "identities": str(identities), "baseline": None}}}))
    for name in ("STORE_URL", "LANDING_URL", "TMP_URL", "HASH", "SERVE_HASH", "FROM", "BASELINE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"PROFILES": str(registry), "PROFILE": client, "WAREHOUSE_BACKEND": "bigquery",
                        "RETRIEVAL_BACKEND": "bigquery", "GRAPH_STORE": "bigquery", "APP_STATE_BACKEND": "files",
                        "BIGQUERY_PROJECT": bigquery_fake.PROJECT, "MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules",
                        "MODEL_HIGH_PROVIDER": "none", "MODEL_MEDIUM_PROVIDER": "none", "MODEL_LOW_PROVIDER": "none",
                        "EMBEDDING_PROVIDER": "hash", "EVAL_CONCURRENCY": "1", "REPEATS": "1"}.items():
        monkeypatch.setenv(name, value)
    fake = bigquery_fake.install(monkeypatch)
    twin = bigquery_fake.install_graph(monkeypatch, fake, tmp_path / "twin.ladybug", None)
    original_module = cli.Commands.module

    def module(self, name, *args, capture=False, check=True):
        if name == "kge.graph.load" and "--artifact" in args:
            twin.artifact = Path(args[args.index("--artifact") + 1])
        output = io.StringIO()
        previous = sys.argv
        sys.argv = [name, *map(str, args)]
        code = 0
        try:
            with redirect_stdout(output):
                try:
                    runpy.run_module(name, run_name="__main__")
                except SystemExit as exc:
                    code = exc.code or 0
        finally:
            sys.argv = previous
        if check and code:
            raise cli.Failed(code, name + ": " + output.getvalue())
        if not capture:
            print(output.getvalue(), end="")
        return output.getvalue().rstrip("\n") if capture else code

    assert original_module is not module
    monkeypatch.setattr(cli.Commands, "module", module)
    original_run = subprocess.run
    def run(command, **kwargs):
        if command[:3] != [sys.executable, "-m", "kge.eval.stages"]:
            return original_run(command, **kwargs)
        from kge.eval import stages
        previous = sys.stdin
        try:
            sys.stdin = io.StringIO(kwargs["input"])
            code = stages.main()
        finally:
            sys.stdin = previous
        return subprocess.CompletedProcess(command, code)
    monkeypatch.setattr(subprocess, "run", run)
    yield fake, twin, resolve(), clients / client
    if twin.conn is not None:
        twin.conn.close()
        twin.db.close()
    fake.con.close()


def test_transport_runs_real_query_wait_pages_loads_and_atomic_aborts(monkeypatch):
    fake = bigquery_fake.install(monkeypatch)
    loader = BigQueryLoader(client="sample")
    fake.page_size = 2
    fake.pending_polls = 2
    loader.run("CREATE TABLE `pilot-project.sample.rows` (id INT64)")
    loader.load_rows("sample", "rows", [{"name": "id", "type": "INT64"}], [{"id": i} for i in range(5)])
    assert loader.rows("SELECT id FROM `pilot-project.sample.rows` ORDER BY id") == [(i,) for i in range(5)]
    assert any("pageToken=2" in request["url"] for request in fake.requests)
    assert any("uploadType=multipart" in request["url"] for request in fake.requests)
    loader.run("CREATE TABLE `pilot-project.sample._kge_catalogue` (id INT64)")
    fake.catalogue_aborts = 1
    with pytest.raises(RuntimeError, match="concurrent update"):
        loader.run("BEGIN TRANSACTION; INSERT INTO `pilot-project.sample._kge_catalogue` VALUES (1); COMMIT TRANSACTION;")
    assert fake.rows("_kge_catalogue") == []
    fake.cancel_next = True
    with pytest.raises(RuntimeError, match="cancelled"):
        loader.run("SELECT 9")


def test_warehouses_retrieval_and_graph_onboard_together(cloud):
    fake, twin, prof, pack = cloud
    cli.Commands({"REPEATS": "1"}).onboard()
    summary = json.loads((prof.estate() / "summary.json").read_text())
    assert summary["warehouse_versions"] == {"underwriting": 1, "claims": 1}
    assert twin.generations
    assert any(name.startswith("chunks_") for dataset, name in fake.objects)
    assert fake.loads and any("query" in item.get("body", {}) for item in fake.requests)


def test_three_releases_keep_exact_resources_and_previous_restores_answers(cloud):
    fake, twin, prof, pack = cloud
    from kge.graph.release import served
    releases = []
    for index in range(3):
        if index:
            table = pack / "fixture" / "data" / "underwriting" / "insured.csv"
            table.write_text(table.read_text().replace("Alder Fabrication", "Alder Fabrication II", 1))
        cli.Commands({"REPEATS": "1"}).onboard()
        candidates = [p for p in (prof.artifacts / prof.client).iterdir() if p.is_dir() and re.fullmatch("[a-f0-9]{64}", p.name)]
        candidate = max(candidates, key=lambda p: p.stat().st_mtime_ns).name
        cli.Commands({"HASH": candidate, "ROLE": "claims_steward"}).sign_off()
        cli.Commands({"HASH": candidate, "REPEATS": "1"}).release()
        entry = served(prof.store / prof.client)[0]
        releases.append(entry)
        assert entry["warehouses"] == {"underwriting": index + 1, "claims": 1}
    ledger = served(prof.store / prof.client)
    assert ledger == releases[:0:-1]
    assert releases[0]["graph"] != releases[1]["graph"]
    assert releases[1]["graph"] != releases[2]["graph"]
    graph_names = {name for _, name in fake.objects if name.startswith("g")}
    for entry in ledger:
        assert any(entry["graph"] in name for name in graph_names), (entry, sorted(graph_names))
        assert any(f"{dataset}.{name}" == entry["chunks"] for dataset, name in fake.objects)
    assert not any(releases[0]["graph"] in name for name in graph_names)
    assert not any(f"{dataset}.{name}" == releases[0]["chunks"] for dataset, name in fake.objects)
    cli.Commands({"PREVIOUS": "yes"}).release()
    assert prof.serve().identity() == releases[1]
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    warehouse = BigQueryWarehouses(prof, ["underwriting"], versions=prof.serve().identity()["warehouses"])
    assert warehouse.rows("underwriting", "SELECT insured_name FROM insured WHERE insured_id = 1000") == [("Alder Fabrication II",)]
    assert cli.Commands({"HASH": releases[1]["hash"], "REPEATS": "1"}).module(
        "kge.eval.margin", "--hash", releases[1]["hash"], "--repeats", "1", "--baseline", "") == 0


def test_five_thousand_ready_chunks_copy_through_bounded_transport(cloud, tmp_path):
    import csv
    from kge.adapters.graphstore import store
    fake, twin, prof, pack = cloud
    cli.Commands({"REPEATS": "1"}).onboard()
    candidate = next(p.name for p in (prof.artifacts / prof.client).iterdir() if p.is_dir() and re.fullmatch("[a-f0-9]{64}", p.name))
    backend = store()
    graph = backend.open(prof.at(candidate).graph_path())
    conn = backend.connect(graph)
    text = '界"\\\n' * 900
    path = tmp_path / "ready.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "doc_id", "ordinal", "text", "readiness"])
        writer.writerows((f"bulk-{i}", "MD-GWP", i, text, "citable") for i in range(5000))
    start = len(fake.requests)
    with backend.batch(conn) as writer:
        writer.load_file("Chunk", path)
    sent = [r for r in fake.requests[start:] if r.get("body", {}).get("queryParameters")]
    assert len(sent) > 1
    assert all(r["bytes"] <= 9_000_000 for r in sent)
    result = conn.execute("MATCH (c:Chunk) WHERE c.doc_id = $doc RETURN c.id, c.text, c.readiness", {"doc": "MD-GWP"})
    rows = result.get_as_df().to_dict("records")
    copied = [row for row in rows if row["c.id"].startswith("bulk-")]
    assert len(copied) == 5000
    assert all(row["c.text"] == text and row["c.readiness"] == "citable" for row in copied)
    conn.close()
    graph.close()


@pytest.mark.parametrize("invalid", ["description", "unit", "staging"])
def test_invalid_fixture_is_rejected_before_any_bigquery_writes(cloud, monkeypatch, invalid):
    fake, twin, prof, pack = cloud
    schema_path = pack / "fixture" / "schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    if invalid == "description":
        schema["warehouses"]["underwriting"]["tables"][0]["columns"][0]["description"] = "d" * 1025
    elif invalid == "unit":
        schema["warehouses"]["u" * 64] = schema["warehouses"].pop("underwriting")
    else:
        monkeypatch.setenv("BIGQUERY_LOAD_INLINE_MAX", "1")
    schema_path.write_text(yaml.safe_dump(schema))
    with pytest.raises((cli.Failed, ValueError, RuntimeError)):
        cli.Commands({"REPEATS": "1"}).onboard()
    assert not fake.objects
    assert not fake.loads
    statements = [r.get("body", {}).get("query", r.get("body", {}).get("configuration", {}).get("query", {}).get("query", ""))
                  for r in fake.requests]
    assert not any(re.search(r"\b(CREATE|INSERT|UPDATE|DELETE|DROP|MERGE)\b", sql, re.I) for sql in statements)


def test_failed_onboard_removes_only_its_candidate_resources(cloud, monkeypatch):
    fake, twin, prof, pack = cloud
    original = cli.Commands.module
    def fail_margin(self, name, *args, **kwargs):
        if name == "kge.eval.margin":
            return 1
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(cli.Commands, "module", fail_margin)
    with pytest.raises(cli.Failed):
        cli.Commands({"REPEATS": "1"}).onboard()
    assert not any(name.startswith(("g", "chunks_", "underwriting_", "claims_")) for _, name in fake.objects)
    assert not prof.current()


def test_same_hash_unreleased_onboard_replaces_its_candidate_generation(cloud):
    fake, twin, prof, pack = cloud
    cli.Commands({"REPEATS": "1"}).onboard()
    first = {name for _, name in fake.objects if name.startswith(("g", "chunks_"))}
    table = pack / "fixture" / "data" / "underwriting" / "insured.csv"
    table.write_text(table.read_text().replace("Alder Fabrication", "Alder Fabrication II", 1))
    cli.Commands({"REPEATS": "1"}).onboard()
    second = {name for _, name in fake.objects if name.startswith(("g", "chunks_"))}
    assert first and second and first.isdisjoint(second)
    assert len(second) == len(first)
    assert not prof.current()


def test_cli_sync_failure_preserves_previously_published_bigquery_resources(cloud, monkeypatch):
    from kge.graph.release import served
    fake, twin, prof, pack = cloud
    old = old_record = None
    for index in range(3):
        if index:
            table = pack / "fixture" / "data" / "underwriting" / "insured.csv"
            table.write_text(table.read_text().replace("Alder Fabrication", "Alder Fabrication II", 1))
        cli.Commands({"REPEATS": "1"}).onboard()
        candidate = max((p for p in (prof.artifacts / prof.client).iterdir()
                         if re.fullmatch("[a-f0-9]{64}", p.name)), key=lambda p: p.stat().st_mtime_ns).name
        cli.Commands({"HASH": candidate, "ROLE": "claims_steward"}).sign_off()
        if index < 2:
            cli.Commands({"HASH": candidate, "REPEATS": "1"}).release()
            if old is None:
                old = served(prof.store / prof.client)[0]
                old_record = json.loads((prof.graph_path().parent / "release.json").read_text())
            continue
        before = set(fake.objects)
        request_start = len(fake.requests)
        def fail_sync(self, model_hash=None):
            raise OSError("injected BigQuery sync failure")
        monkeypatch.setattr(cli.Commands, "sync", fail_sync)
        with pytest.raises(OSError, match="injected BigQuery sync failure"):
            cli.Commands({"HASH": candidate, "REPEATS": "1"}).release()
        statements = [request.get("body", {}).get("query", "") for request in fake.requests[request_start:]]
        assert not any(re.search(r"\bDROP\b", sql, re.I) for sql in statements)
        assert before <= set(fake.objects)
        assert any(old["graph"] in name for _, name in fake.objects)
        assert any(f"{dataset}.{name}" == old["chunks"] for dataset, name in fake.objects)
        from kge.adapters.graphstore.bigquery import BigQueryStore, Graph
        from kge.adapters.warehouse.bigquery import BigQueryWarehouses
        graph = Graph(prof.at(old["hash"]).graph_path(), pointer=old_record["graph"])
        conn = BigQueryStore().connect(graph)
        assert conn.execute("MATCH (c:Chunk) RETURN count(*) AS n").get_as_df().iloc[0]["n"] == 5
        conn.close()
        graph.close()
        warehouse = BigQueryWarehouses(prof, ["underwriting"], versions=old["warehouses"])
        assert warehouse.rows("underwriting", "SELECT insured_name FROM insured WHERE insured_id = 1000") == [("Alder Fabrication",)]


def test_five_thousand_large_retrieval_rows_use_readable_staging_and_await_load(monkeypatch):
    from types import SimpleNamespace
    from kge.adapters import objstore
    fake = bigquery_fake.install(monkeypatch)
    class Scratch:
        def source_uri(self, key):
            return "gs://fake-scratch/" + key
        def put(self, key, data):
            fake.staged[self.source_uri(key)] = data
        def get(self, key):
            return fake.staged[self.source_uri(key)]
        def delete(self, key):
            fake.staged.pop(self.source_uri(key), None)
    monkeypatch.setenv("TMP_URL", "gs://fake-scratch")
    monkeypatch.setattr(objstore, "open_scratch", lambda client: Scratch())
    monkeypatch.setattr(objstore, "open_store", lambda _: SimpleNamespace(bucket_metadata=lambda: "bucket metadata"))
    loader = BigQueryLoader(client="sample")
    schema = [{"name": "id", "type": "INT64"}, {"name": "text", "type": "STRING"}]
    rows = [{"id": i, "text": "界" * 1000} for i in range(5000)]
    size = len(("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode())
    loader.preflight_staging(size)
    assert not fake.staged and not fake.objects
    fake.pending_polls = 2
    assert loader.load_rows("sample", "chunks", schema, rows) == 5000
    assert len(fake.rows("chunks")) == 5000
    assert all(row[1] == "界" * 1000 for row in fake.rows("chunks"))
    assert not fake.staged
    assert any(r.get("content_type") == "application/json" for r in fake.requests)
    assert not any("uploadType=multipart" in r["url"] for r in fake.requests)


def test_reader_isolates_v0_v1_and_unit_x_through_real_information_schema(monkeypatch, tmp_path):
    """Published catalogue entries bound real REST metadata queries despite colliding physical prefixes."""
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
    from kge.ports import WarehouseRefused

    fake = bigquery_fake.install(monkeypatch)
    estate = BigQueryEstate("isolation")
    scopes = [("unit", None, "legacy"), ("unit", 1, "versioned"), ("unit_x", None, "neighbor")]
    try:
        for index, (unit, version, marker) in enumerate(scopes, 1):
            statements = [f"CREATE TABLE parent (id INTEGER PRIMARY KEY, {marker} VARCHAR)",
                          "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent (id))",
                          f"CREATE TABLE {marker}_only (id INTEGER PRIMARY KEY)"]
            staged = estate.stage(unit, statements, version)
            staged.load("parent", ["id", marker], [[n, marker] for n in range(index)])
            staged.load("child", ["id", "parent_id"], [[0, 0]])
            estate.publish(unit, version)
        fake.page_size = 1
        fake.pending_polls = 1
        start = len(fake.requests)
        for index, (unit, version, marker) in enumerate(scopes, 1):
            reader = BigQueryWarehouses(tmp_path, ["unit", "unit_x"], client="isolation",
                                        project=bigquery_fake.PROJECT, versions={unit: version or 0})
            columns = reader.catalogue_columns(unit)
            assert set(columns) == {("parent", "id", "INT64"), ("parent", marker, "STRING"),
                                    ("child", "id", "INT64"), ("child", "parent_id", "INT64"),
                                    (f"{marker}_only", "id", "INT64")}
            assert reader.columns(unit) == [(table, col, {"INT64": "BIGINT", "STRING": "VARCHAR"}[kind])
                                             for table, col, kind in columns]
            assert reader.calendar_tables(unit) == {"parent", "child", f"{marker}_only"}
            assert sorted(reader.primary_keys(unit)) == sorted([(name, ["id"]) for name in
                                                                ("parent", "child", f"{marker}_only")])
            assert reader.foreign_keys(unit) == [("child", ["parent_id"], "parent", ["id"])]
            assert reader.table_facts(unit, "parent")["records"] == index
            for foreign in ("v1_parent", "x_parent", "unpublished"):
                with pytest.raises(WarehouseRefused, match="no such table"):
                    reader.table_facts(unit, foreign)
            assert reader.rows(unit, f"SELECT {marker} FROM parent") == [(marker,)] * index
            reader.close()
        sent = fake.requests[start:]
        queries = [item["body"]["query"] for item in sent if "query" in item.get("body", {})]
        for unit, version, _ in scopes:
            assert sum("_kge_catalogue" in sql and f'WHERE unit = "{unit}" AND version = {version or 0}' in sql
                       for sql in queries) == 1
        assert all("STARTS_WITH" not in sql for sql in queries)
        assert any("INFORMATION_SCHEMA.KEY_COLUMN_USAGE" in sql for sql in queries)
        assert any("INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE" in sql for sql in queries)
        assert any("pageToken=" in item["url"] for item in sent)
    finally:
        fake.con.close()
