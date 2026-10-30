"""The command core's state transitions, independently of the page."""
from pathlib import Path
from dataclasses import replace
import contextlib
import json
import shutil
from types import SimpleNamespace

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from kge.estate import draftflow, onboard
from kge.estate import load_bigquery
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.compiler import emit, pack as packs
from kge.pipeline.profile import Profile
from kge.adapters.objstore import StoreError, open_store
from kge.pipeline.objstore import pull
from kge import packstore
from kge.adapters import appstate
from kge.adapters.appstate import files as files_state
from kge.pipeline import warehouse_versions
from kge.adapters.warehouse import estate_of

DEMO = Path(__file__).resolve().parents[2]


@pytest.fixture
def prof(tmp_path):
    clients = tmp_path / "clients"; target = clients / "meridian"; target.mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", target / "pack.yaml")
    return Profile("meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "profiles.yaml")


def _files_under(*roots):
    return {f"{n}:{path.relative_to(root)}": path.read_bytes()
            for n, root in enumerate(roots) if root.exists()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _v(prof, version: int, unit: str = "leasing"):
    """An onboarded unit's warehouse of one version (kge/pipeline/warehouse_versions.py)."""
    return warehouse_versions.path(prof.warehouses, unit, version)


@pytest.mark.parametrize("remote", [False, True], ids=["local-store", "remote-store"])
@pytest.mark.parametrize("operation", ["create", "draft", "rows", "accept", "certify"])
def test_a_lost_bigquery_manifest_race_leaves_no_files_or_history(operation, remote, monkeypatch, tmp_path, prof):
    """Auxiliary pack files and warehouses land only after the manifest CAS succeeds."""
    if remote:
        remote_root = tmp_path / "object-store"
        monkeypatch.setenv("STORE_URL", f"file://{remote_root}")
        remote_store = open_store(str(remote_root))
        remote_store.put("clients/meridian/pack.yaml", (DEMO / "clients/meridian/pack.yaml").read_bytes())
    else:
        remote_root = tmp_path / "no-remote"

    schema = DEMO / "examples/leasing/schema.sql"
    if operation != "create":
        onboard.create(prof, "leasing", schema, "Asset Finance", rows="none" if operation == "rows" else None,
                       as_="knowledge_engineer")
    if operation in {"rows", "accept", "certify"}:
        onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    if operation == "certify":
        onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")

    before = _files_under(prof.clients / prof.client, prof.warehouses, remote_root)

    class LostRace:
        tables = True

        def __init__(self, store):
            self.files = files_state.FilesState(store)

        @contextlib.contextmanager
        def lock(self):
            with self.files.lock():
                yield

        def manifest(self, unit):
            return self.files.manifest(unit)

        def history(self, unit):
            return self.files.history(unit)

        def commit(self, unit, manifest, additions):
            raise appstate.StateConflict(f"unit {unit} changed by someone else; reload")

    monkeypatch.setattr(appstate, "for_profile", lambda profile, store: LostRace(store))
    call = {
        "create": lambda: onboard.create(prof, "leasing", schema, "Asset Finance", as_="knowledge_engineer"),
        "draft": lambda: onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17"),
        "rows": lambda: onboard.rows(prof, "leasing", "csv", csv_dir=DEMO / "examples/leasing/data", as_="knowledge_engineer"),
        "accept": lambda: onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17"),
        "certify": lambda: onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17"),
    }[operation]
    with pytest.raises(onboard.OnboardError, match="changed by someone else; reload"):
        call()
    assert _files_under(prof.clients / prof.client, prof.warehouses, remote_root) == before


def test_create_draft_accept_and_certify(prof):
    schema = DEMO / "examples/leasing/schema.sql"
    assert onboard.tables(prof, "leasing", schema, "Asset Finance")["exists"] is None
    made = onboard.create(prof, "leasing", schema, "Asset Finance", as_="knowledge_engineer")
    assert made["created"] and made["rows"] == {"lessee": 12, "lease": 12, "rental": 120}
    before = _v(prof, 1).read_bytes()
    assert made["warehouse"] == str(_v(prof, 1)) and not (prof.warehouses / "leasing.duckdb").exists(), \
        "onboarding writes version 1's warehouse of its own, never the established units' <unit>.duckdb"
    assert not onboard.create(prof, "leasing", schema, "Asset Finance", as_="knowledge_engineer")["created"]
    assert _v(prof, 1).read_bytes() == before
    proposed = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    assert not (prof.clients / prof.client / ".onboard.lock").exists()
    assert (prof.run_dir() / "onboard.lock").exists()
    assert [(x["id"], x["state"]) for x in proposed["items"]] == [
        ("source", "drafted"), ("lease-rentals", "drafted"), ("LEASING-D01", "drafted"),
        ("ontology-lease-rentals-fibo-monetary-amount", "drafted")]
    with pytest.raises(onboard.OnboardError) as denied:
        onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    assert denied.value.status == 409
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    with pytest.raises(onboard.OnboardError) as self_certification:
        onboard.certify(prof, "leasing", "all", "business_team", "2026-10-17")
    assert self_certification.value.status == 403
    certified = onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    assert all(x["state"] == "certified" for x in certified["items"])
    assert not onboard.create(prof, "leasing", schema, "Asset Finance", as_="knowledge_engineer")["created"], \
        "an onboarding-owned source remains idempotent after its draft has been merged into the pack"


def test_onboarded_unit_is_loaded_and_routed_in_the_client_bigquery_dataset(prof):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    assert [unit["unit"] for unit in onboard.show(prof)["units"]] == ["leasing"]
    pack = packs.read(prof.clients, prof.client, identities=prof.identities)
    artifact_units = [row["id"] for row in emit.NODES["Source"](pack.data, {})]
    assert "leasing" in artifact_units

    loader = load_bigquery.BigQueryLoader(project="pilot-project", dry_run=True)
    statements = []
    loader.query = lambda sql: statements.append(sql) or {}
    report = load_bigquery.sync_warehouses(loader, prof, prof.client)

    assert set(report) == {"leasing_v1"}, "an onboarded unit's version is loaded as tables of its own"
    assert report["leasing_v1"]["tables"] == 3
    assert all("CREATE OR REPLACE VIEW" not in sql for sql in statements)
    assert any("leasing_v1_lease" in sql and '("kge_unit", "leasing"), ("kge_version", "v1")' in sql for sql in statements)
    warehouses = BigQueryWarehouses(prof, [unit["unit"] for unit in onboard.show(prof)["units"]],
                                    project="pilot-project", versions={"leasing": 1})
    assert warehouses.units == ["leasing"]
    rewritten = warehouses.rewrite_sql("SELECT * FROM lease", unit="leasing")
    assert "leasing_v1_lease" in rewritten
    assert warehouses.dataset_id == "kge_meridian_warehouses"


def test_reset_removes_only_its_unit_and_restores_the_original_inputs(prof):
    pack = prof.clients / prof.client
    (pack / "pack.lock").write_bytes(b"original lock\n")
    artifacts = prof.artifacts / prof.client
    artifacts.mkdir(parents=True)
    original_hash = "a" * 64
    (artifacts / original_hash).mkdir()
    (artifacts / "current").symlink_to(original_hash)
    estate = prof.estate(); (estate / "docs/uncertified").mkdir(parents=True)
    (estate / "documents.json").write_text('[{"doc_id":"BASE"}]\n')
    schema = DEMO / "examples/leasing/schema.sql"
    onboard.create(prof, "leasing", schema, "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    onboard.build(prof)
    (pack / "pack.lock").write_bytes(b"changed lock\n")
    other = pack / "units/other/keep.txt"; other.parent.mkdir(parents=True); other.write_text("keep")
    (prof.warehouses / "other.duckdb").write_text("keep")

    got = onboard.reset(prof, "leasing")

    assert got["current"] == original_hash
    assert (pack / "pack.lock").read_bytes() == b"original lock\n", \
        "the last onboarded unit's reset puts back the lock create() kept, as it does the pack: the lock the released " \
        "graph was compiled with, so the version snapshot reset takes holds it and a later rollback cannot restore one " \
        "that still lists the unit (with another unit left, the lock is make lock's to rebuild: the next test)"
    assert not (pack / "units/leasing").exists()
    assert other.read_text() == "keep" and (prof.warehouses / "other.duckdb").read_text() == "keep"
    assert not (prof.warehouses / "leasing.duckdb").exists() and not (prof.warehouses / "onboarded").exists()
    assert not (estate / "golden_leasing_v1.json").exists()
    assert not (estate / "docs/uncertified/DD-LEASING.md").exists()
    assert (estate / "documents.json").read_text() == '[\n  {\n    "doc_id": "BASE"\n  }\n]'


def test_a_kept_version_snapshot_holds_the_lock_its_artifact_was_compiled_with(prof):
    """The pack's hash leaves the lock out, so snapshot_pack holds the lock to the manifest's lock_hash: a kept snapshot
    with another lock (one that still listed a reset unit) takes the store's when the store's is the artifact's, and one
    that already holds it, or an artifact whose manifest names no lock, is left as it was."""
    import hashlib
    from kge.graph import history
    pack = prof.clients / prof.client
    good, stale = b"lock the graph was compiled with\n", b"lock still listing leasing\n"
    (pack / "pack.lock").write_bytes(good)
    model_hash = "f" * 64
    artifact = prof.artifacts / prof.client / model_hash; artifact.mkdir(parents=True)
    manifest = {"pack": {"hash": "p" * 64}, "lock_hash": hashlib.sha256(good).hexdigest()}
    (artifact / "manifest.json").write_text(json.dumps(manifest))
    snap = prof.artifacts / prof.client / "versions" / model_hash; snap.mkdir(parents=True)
    (snap / "pack.yaml").write_bytes(b"kept pack\n")
    (snap / "pack.lock").write_bytes(stale)

    assert history.snapshot_pack(prof, model_hash, manifest["pack"]["hash"]) is True
    assert (snap / "pack.lock").read_bytes() == good and (snap / "pack.yaml").read_bytes() == b"kept pack\n"

    (pack / "pack.lock").write_bytes(stale)
    assert history.snapshot_pack(prof, model_hash, manifest["pack"]["hash"]) is True
    assert (snap / "pack.lock").read_bytes() == good, "a snapshot that holds its artifact's lock is never overwritten"

    (artifact / "manifest.json").write_text(json.dumps({"pack": manifest["pack"]}))
    (snap / "pack.lock").write_bytes(stale)
    assert history.snapshot_pack(prof, model_hash, manifest["pack"]["hash"]) is True
    assert (snap / "pack.lock").read_bytes() == stale, "a manifest with no lock_hash keeps the behaviour before it"


def test_rows_added_later_name_the_question_as_a_csv_draft_would(prof):
    """README step 10: a draft with no rows, then the CSVs added; the question keeps its own phrase, since its own draft,
    already merged into the pack, holds it, and gains the month and figure of those rows."""
    schema, data = DEMO / "examples/leasing/schema.sql", DEMO / "examples/leasing/data"
    onboard.create(prof, "leasing", schema, "Asset Finance", rows="none", as_="knowledge_engineer")
    drafted = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    question = next(x for x in drafted["items"] if x["kind"] == "question")
    assert question["title"] == "Lease rentals for the latest month with rows"
    onboard.rows(prof, "leasing", "csv", csv={p.name: p.read_bytes() for p in data.glob("*.csv")}, as_="knowledge_engineer")
    items = onboard.show(prof)["units"][0]["items"]
    question = next(x for x in items if x["kind"] == "question")
    assert question["title"] == "Lease rentals for June 2025"
    assert "1461.25" in str(question) and "needs_rows" not in question
    assert [x["id"] for x in items] == ["source", "lease-rentals", "LEASING-D01", "ontology-lease-rentals-fibo-monetary-amount"]


def test_rows_reads_a_csv_folder_without_a_ddl_path(prof):
    schema, data = DEMO / "examples/leasing/schema.sql", DEMO / "examples/leasing/data"
    onboard.create(prof, "leasing", schema, "Asset Finance", rows="none", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    got = onboard.rows(prof, "leasing", "csv", csv_dir=data, as_="knowledge_engineer")
    assert got["changed"] and got["rows"]["origin"] == "csv"
    assert set(got["rows"]["files"]) == {"lessee", "lease", "rental"}


def test_bad_rows_leave_the_unit_unchanged_and_good_rows_can_follow(prof):
    schema, data = DEMO / "examples/leasing/schema.sql", DEMO / "examples/leasing/data"
    onboard.create(prof, "leasing", schema, "Asset Finance", rows="none", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    bad = {p.name: p.read_bytes() for p in data.glob("*.csv")}
    bad["rental.csv"] = bad["rental.csv"].replace(b"1,1,2025-01-01", b"1,999,2025-01-01")
    before = _v(prof, 1).read_bytes()
    with pytest.raises(onboard.OnboardError, match="orphan foreign key lease_id=999") as refused:
        onboard.rows(prof, "leasing", "csv", csv=bad, as_="knowledge_engineer")
    assert refused.value.status == 400
    assert onboard.show(prof)["units"][0]["rows"] == {"origin": "none"}
    assert _v(prof, 1).read_bytes() == before
    assert not (prof.clients / prof.client / "units/leasing/data").exists()
    assert onboard.rows(prof, "leasing", "csv", csv_dir=data, as_="knowledge_engineer")["changed"]


def test_no_rows_cannot_be_accepted_and_do_not_stop_other_units_building(prof):
    schema = DEMO / "examples/leasing/schema.sql"
    onboard.create(prof, "leasing", schema, "Asset Finance", rows="none", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    with pytest.raises(onboard.OnboardError, match="has no rows") as refused:
        onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    assert refused.value.status == 409
    assert onboard.build(prof) == {"units": []}


def test_reset_with_nothing_to_reset_succeeds_and_changes_nothing(prof):
    pack = prof.clients / prof.client
    before = sorted(p.relative_to(prof.clients) for p in prof.clients.rglob("*"))
    got = onboard.reset(prof, "leasing")
    assert got["reset"] == "nothing" and got["current"] is None and got["said"] == ""
    assert sorted(p.relative_to(prof.clients) for p in prof.clients.rglob("*")) == before
    assert not (pack / "units").exists()


def test_reset_one_of_two_onboarded_units_preserves_the_other(prof):
    pack = prof.clients / prof.client
    artifacts = prof.artifacts / prof.client
    artifacts.mkdir(parents=True)
    original_hash = "d" * 64
    (artifacts / original_hash).mkdir()
    (artifacts / "current").symlink_to(original_hash)
    (pack / "pack.lock").write_bytes(b"lock before leasing\n")
    schema = (DEMO / "examples/leasing/schema.sql").read_text()
    onboard.create(prof, "leasing", label="Asset Finance", ddl_text=schema, as_="knowledge_engineer")
    assert (pack / "units/leasing/original-pack.lock").read_bytes() == b"lock before leasing\n"
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    other = pack / "units/leasingb"
    other.mkdir(parents=True)
    (other / "unit.yaml").write_text("format: 1\nunit: leasingb\nrows:\n  origin: invented\n")
    prof.warehouses.mkdir(parents=True, exist_ok=True)
    (prof.warehouses / "leasingb.duckdb").write_bytes(b"other warehouse")
    (pack / "pack.lock").write_bytes(b"lock holding both units\n")

    onboard.reset(prof, "leasing")

    assert (pack / "pack.lock").read_bytes() == b"lock holding both units\n", \
        "with another unit left, the lock create() kept would drop it: the lock is make lock's to rebuild"
    assert not (pack / "units/leasing").exists()
    assert (pack / "units/leasingb/unit.yaml").is_file()
    assert (prof.warehouses / "leasingb.duckdb").is_file()


def test_reset_refuses_when_another_onboarded_unit_names_the_source(prof):
    pack = prof.clients / prof.client
    artifacts = prof.artifacts / prof.client
    artifacts.mkdir(parents=True)
    original_hash = "e" * 64
    (artifacts / original_hash).mkdir()
    (artifacts / "current").symlink_to(original_hash)
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    other = pack / "units/leasingb"
    other.mkdir(parents=True)
    (other / "draft.yaml").write_text(
        "format: 1\nunit: leasingb\nitems:\n- id: source\n  kind: source\n  pack:\n    no_join:\n"
        "    - between: [leasing.lease, leasingb.lease]\n")
    with pytest.raises(onboard.OnboardError, match="named by onboarded unit leasingb") as refused:
        onboard.reset(prof, "leasing")
    assert refused.value.status == 409
    assert (pack / "units/leasing/unit.yaml").is_file()


def test_reset_removes_a_partial_unit_onboarding_left_before_its_reset_record(prof):
    pack = prof.clients / prof.client
    (pack / "pack.lock").write_bytes(b"original lock\n")
    state = pack / "units/leasing"; state.mkdir(parents=True)
    (state / "original-pack.lock").write_bytes(b"original lock\n")
    prof.warehouses.mkdir(parents=True)
    (prof.warehouses / ".onboard-leasing.duckdb").write_bytes(b"part")
    got = onboard.reset(prof, "leasing")
    assert got["reset"] == "partial" and got["said"] == ""
    assert not (pack / "units").exists() and not (prof.warehouses / ".onboard-leasing.duckdb").exists()
    assert (pack / "pack.lock").read_bytes() == b"original lock\n"


def test_reset_restores_a_unit_with_its_reset_record_and_no_warehouse_yet(prof):
    pack = prof.clients / prof.client
    (pack / "pack.lock").write_bytes(b"original lock\n")
    state = pack / "units/leasing"; state.mkdir(parents=True)
    (state / "original-pack.lock").write_bytes(b"original lock\n")
    (state / "original.json").write_text('{"format": 1, "current": "%s", "lock": true}\n' % ("c" * 64))
    (pack / "pack.lock").write_bytes(b"changed lock\n")
    got = onboard.reset(prof, "leasing")
    assert got["reset"] == "unit" and got["said"] == "c" * 64
    assert not (pack / "units").exists() and (pack / "pack.lock").read_bytes() == b"original lock\n", \
        "no other unit left: the lock create() kept is put back, as the pack is"


def test_reset_refuses_a_unit_onboarding_did_not_make(prof):
    """Onboarding state beside a warehouse with no reset record is refused too: the warehouse is not provably
    onboarding's."""
    prof.warehouses.mkdir(parents=True)
    (prof.warehouses / "leasing.duckdb").write_bytes(b"not onboarding's")
    with pytest.raises(onboard.OnboardError) as refused:
        onboard.reset(prof, "leasing")
    assert refused.value.status == 409 and "was not made by onboarding" in refused.value.message
    assert (prof.warehouses / "leasing.duckdb").read_bytes() == b"not onboarding's"
    with pytest.raises(onboard.OnboardError) as pack_source:
        onboard.reset(prof, "retail")
    assert pack_source.value.status == 409 and "was not made by onboarding" in pack_source.value.message
    (prof.clients / prof.client / "units/leasing").mkdir(parents=True)
    (prof.clients / prof.client / "units/leasing/original-pack.lock").write_bytes(b"x")
    with pytest.raises(onboard.OnboardError) as unproven:
        onboard.reset(prof, "leasing")
    assert unproven.value.status == 409 and (prof.warehouses / "leasing.duckdb").exists()


def test_role_permissions_are_the_command_cores_rules(prof):
    assert onboard.permissions(prof, "business_team") == {
        "read": True, "create": False, "draft": False, "accept": True, "certify": False,
        "run": False, "improve": False, "participant": True, "sign_off": False, "release": False}
    for who in ("steward_glossary", "admin"):
        assert onboard.permissions(prof, who) == {
            "read": False, "create": False, "draft": False, "accept": False, "certify": True,
            "run": False, "improve": False, "participant": True, "sign_off": True, "release": True}


def test_identity_read_error_hides_the_path_and_logs_it(prof, tmp_path, caplog):
    prof = replace(prof, identities=tmp_path / "private" / "identities.yaml")
    with pytest.raises(onboard.OnboardError) as refused:
        onboard.permissions(prof, "business_team")
    assert refused.value.status == 409
    assert refused.value.message == "the deployment's identities cannot be read"
    assert str(prof.identities) not in refused.value.message and str(prof.identities) in caplog.text


def test_store_backed_flow_survives_a_fresh_clients_mirror(monkeypatch, tmp_path, prof):
    remote = tmp_path / "object-store"
    url = f"file://{remote}"
    monkeypatch.setenv("STORE_URL", url)
    store = open_store(url)
    for name in ("pack.yaml", "pack.lock"):
        store.put(f"clients/meridian/{name}", (DEMO / "clients/meridian" / name).read_bytes())

    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    shutil.rmtree(prof.clients)
    pull(store, "clients/", prof.clients)
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")

    fresh = tmp_path / "fresh"
    pull(store, "clients/", fresh)
    record = __import__("yaml").safe_load((fresh / "meridian/units/leasing/draft.yaml").read_text())
    assert all(item["certified_by"] == "steward_glossary" for item in record["items"])


def test_store_backed_reset_restores_the_lock_and_removes_only_its_unit(monkeypatch, tmp_path, prof):
    remote = tmp_path / "object-store"
    url = f"file://{remote}"
    monkeypatch.setenv("STORE_URL", url)
    store = open_store(url)
    original_lock = (DEMO / "clients/meridian/pack.lock").read_bytes()
    for name, data in (("pack.yaml", (DEMO / "clients/meridian/pack.yaml").read_bytes()),
                       ("pack.lock", original_lock)):
        store.put(f"clients/meridian/{name}", data)
    artifacts = prof.artifacts / prof.client; artifacts.mkdir(parents=True)
    original_hash = "b" * 64
    (artifacts / original_hash).mkdir(); (artifacts / "current").symlink_to(original_hash)
    estate = prof.estate(); estate.mkdir(parents=True, exist_ok=True)
    (estate / "documents.json").write_text("[]\n")
    store.put("clients/meridian/units/other/unit.yaml", b"unit: other\n")

    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    store.put("clients/meridian/pack.lock", b"changed\n")
    got = onboard.reset(prof, "leasing")

    assert got["current"] == original_hash
    assert store.get("clients/meridian/pack.lock") == b"changed\n"
    assert not store.list("clients/meridian/units/leasing/")
    assert store.get("clients/meridian/units/other/unit.yaml") == b"unit: other\n"


def test_two_store_backed_draft_writers_cannot_clobber(monkeypatch, tmp_path, prof):
    remote = tmp_path / "object-store"
    monkeypatch.setenv("STORE_URL", f"file://{remote}")
    a, b = packstore.for_profile(prof), packstore.for_profile(prof)
    name = "units/leasing/draft.yaml"
    assert not a.exists(name) and not b.exists(name)
    a.write(name, b"first")
    with pytest.raises(StoreError, match="changed by someone else; reload"):
        b.write(name, b"second")
    assert a.read(name) == b"first"


def test_writable_requires_a_store_only_on_the_pilot(monkeypatch, prof):
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert onboard.writable(prof) == (True, "")
    monkeypatch.setenv("K_SERVICE", "kge")
    ok, why = onboard.writable(prof)
    assert not ok and "no STORE_URL" in why
    monkeypatch.setenv("STORE_URL", "file:///tmp/store-is-not-probed")
    assert onboard.writable(prof) == (True, "")


@pytest.mark.parametrize("reference", ["p(id)", "demo.p(id)"])
def test_foreign_keys_in_the_unit_are_resolved_in_the_preview(reference, prof):
    sql = f"CREATE TABLE p (id INTEGER PRIMARY KEY); CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES {reference});"
    got = onboard.tables(prof, "demo", ddl_text=sql)
    assert got["tables"][1]["foreign_keys"] == [{"columns": ["p_id"], "ref_table": "p", "ref_columns": ["id"]}]


@pytest.mark.parametrize("reference, statement", [("other.p(id)", 2), ("missing(id)", 1)])
def test_foreign_keys_outside_the_declared_unit_are_cleanly_refused(reference, statement, prof):
    prefix = "CREATE TABLE p (id INTEGER PRIMARY KEY); " if reference.startswith("other.") else ""
    with pytest.raises(onboard.OnboardError) as refused:
        onboard.tables(prof, "demo", ddl_text=prefix + f"CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES {reference});")
    assert refused.value.status == 422
    assert f"statement {statement}, FOREIGN KEY" in refused.value.message


@pytest.mark.parametrize("text,reason", [(";", "no statements"),
                                          ("CREATE TABLE leasing.x (a " + "(" * 500 + "INTEGER" + ")" * 500 + ")", "nesting")])
def test_cli_cleanly_refuses_empty_and_deeply_nested_ddl(monkeypatch, tmp_path, prof, capsys, text, reason):
    source = tmp_path / "bad.sql"
    source.write_text(text)
    monkeypatch.setattr(onboard.cli, "resolve", lambda profile: prof)
    assert onboard.main(["tables", "--unit", "leasing", "--ddl", str(source)]) == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("who", ["steward_glossary", "steward", "admin"])
def test_only_the_business_team_accepts(who, prof):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    with pytest.raises(onboard.OnboardError) as denied:
        onboard.accept(prof, "leasing", "all", who, "2026-10-17")
    assert denied.value.status == 403


def test_business_team_cannot_certify(prof):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    with pytest.raises(onboard.OnboardError) as denied:
        onboard.certify(prof, "leasing", "all", "business_team", "2026-10-17")
    assert denied.value.status == 403


@pytest.mark.parametrize("operation", ["tables", "create", "draft", "accept", "certify"])
def test_every_onboarding_entry_refuses_a_non_slug_unit(operation, prof):
    kwargs = {"tables": {"ddl_text": "CREATE TABLE t (id INTEGER)"},
              "create": {"ddl_text": "CREATE TABLE t (id INTEGER)", "as_": "knowledge_engineer"},
              "draft": {"as_": "knowledge_engineer"}, "accept": {"as_": "business_team"},
              "certify": {"as_": "steward_glossary"}}[operation]
    with pytest.raises(onboard.OnboardError) as denied:
        getattr(onboard, operation)(prof, "../leasing", **kwargs)
    assert denied.value.status == 400 and "must match" in denied.value.message


def test_onboard_tail_matches_entrypoint_onboard_chain():
    import inspect

    from kge import cli
    make = (DEMO / "Makefile").read_text()
    entrypoint = (DEMO / "entrypoint.sh").read_text()
    assert "\nonboard:\n\t$(KGE) onboard $(KGE_ARGS)\n" in make
    steps = inspect.getsource(cli.Commands.onboard)
    for step in ("self.fresh().check()", '"kge.compiler.compile"', "self.load_steps(", "self.margin_run("):
        assert step in steps, step
    assert 'run kge onboard "$@"' in entrypoint


def test_store_backed_service_is_writable_through_packstore(monkeypatch, prof):
    monkeypatch.setenv("STORE_URL", "file:///object-store")
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert onboard.writable(prof) == (True, "")


@pytest.mark.parametrize("sql", [
    "CREATE TABLE t (id INTEGER UNIQUE)",
    "CREATE TABLE t (id DATE UNIQUE)",
    "CREATE TABLE t (id DATE PRIMARY KEY)",
    "CREATE TABLE t (id BOOLEAN PRIMARY KEY)",
    "CREATE TABLE t (id DECIMAL(2,0))",
    "CREATE TABLE p (id INTEGER, other INTEGER, PRIMARY KEY (id, other)); "
    "CREATE TABLE t (id INTEGER PRIMARY KEY, parent_id INTEGER, FOREIGN KEY (id, parent_id) REFERENCES p (id, other))",
    "CREATE TABLE t (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES t(id))",
])
def test_invented_rows_that_cannot_satisfy_accepted_ddl_are_clean_422s(prof, sql):
    with pytest.raises(onboard.OnboardError) as refused:
        onboard.create(prof, "leasing", label="Asset Finance", ddl_text=sql, as_="knowledge_engineer")
    assert refused.value.status == 422
    assert not (prof.warehouses / "leasing.duckdb").exists() and not _v(prof, 1).exists()
    assert not (prof.warehouses / ".onboard-leasing.duckdb").exists()


def test_build_and_reset_leave_the_clients_own_golden_set_and_summary_whole(prof):
    """A unit onboarded to a client make client did not make (Meridian) adds its own golden_<unit>_v1.json; the
    estate's golden.json and summary.json are its generator's, so onboarding's build never rewrites them, and a run
    that stops after the build (a failed gate) leaves nothing for reset-onboard to restore."""
    estate = prof.estate(); estate.mkdir(parents=True, exist_ok=True)
    golden = b'{"version": "v2", "changes_from": null, "items": []}\n'
    summary = b'{"seed": 7, "units": ["wealth", "markets"]}\n'
    (estate / "golden.json").write_bytes(golden); (estate / "summary.json").write_bytes(summary)
    artifacts = prof.artifacts / prof.client; artifacts.mkdir(parents=True)
    (artifacts / ("a" * 64)).mkdir(); (artifacts / "current").symlink_to("a" * 64)
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")

    assert onboard.build(prof)["units"] == ["leasing"]
    assert (estate / "golden_leasing_v1.json").is_file()
    assert (estate / "golden.json").read_bytes() == golden and (estate / "summary.json").read_bytes() == summary

    onboard.reset(prof, "leasing")
    assert (estate / "golden.json").read_bytes() == golden and (estate / "summary.json").read_bytes() == summary
    assert not (estate / "golden_leasing_v1.json").exists()


def test_an_accepted_semantic_document_with_upstream_certification_uses_the_certified_shelf(prof):
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    record = __import__("yaml").safe_load((prof.clients / prof.client / "units/bancassurance/draft.yaml").read_text())
    terms = {x["id"]:x for x in record["items"] if x["kind"] == "term"}
    assert terms["written_premium"]["pack"]["row_selectors"][0]["predicate"].endswith("policy.status = 'active'")
    assert terms["in_force_premium"]["pack"]["measures"][0]["grain"] == "as_of"
    assert next(x for x in record["items"] if x.get("terms") == ["written_premium"])["expect"]["figure"] == "1200.00"
    assert next(x for x in record["items"] if x.get("terms") == ["in_force_premium"])["expect"]["figure"] == "2000.00"
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    onboard.build(prof)
    docs = json.loads((prof.estate() / "documents.json").read_text())
    supplied = next(d for d in docs if d["doc_id"] == "DOC-BANCASSURANCE-1")
    assert supplied["shelf"] == "certified" and supplied["path"] == "docs/certified/DOC-BANCASSURANCE-1.md"
    assert supplied["certification"]["tier"] == "gold"
    assert (prof.estate() / supplied["path"]).is_file()


def test_osi_recipe_golden_figures_apply_filter_and_month_end_grain(prof):
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    record = __import__("yaml").safe_load((prof.clients / prof.client / "units/bancassurance/draft.yaml").read_text())
    figures = {x["terms"][0]:x["expect"]["figure"] for x in record["items"]
               if x.get("kind") == "question" and x.get("from") != "client"}
    assert figures == {"written_premium":"1200.00", "in_force_premium":"2000.00"}
    client = [x for x in record["items"] if x.get("kind") == "question" and x.get("from") == "client"]
    assert [x["id"] for x in client] == ["BR-01", "BR-02"]
    assert all(x["client_source"] == "Q2 board report" for x in client)
    assert any(x.get("metric") == "ambiguous_premium" and "OR" in x["why"] for x in record["not_drafted"])
    assert any(x.get("golden") == "BR-03" and "1,250.00" in x["ask"] and "1,200.00" in x["ask"]
               for x in record["not_drafted"])
    shown = onboard.show(prof)["units"][0]
    assert any(r["ask"] == "Define ambiguous_premium as plain conditions, or confirm it isn't needed." for r in shown["requests"])
    assert shown["golden"] == {"name": "golden_bancassurance", "version": "v1", "client": 2, "drafted": 2}
    assert any("Your figure for 'Board-reported active-policy written premium for June 2025'" in r["ask"]
               for r in shown["requests"])
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-18")
    onboard.build(prof)
    golden = json.loads((prof.estate() / "golden_bancassurance_v1.json").read_text())
    assert [x["id"] for x in golden["items"]] == ["BANCASSURANCE-D01", "BANCASSURANCE-D02", "BR-01", "BR-02"]


def test_client_golden_sql_refusals_are_open_requests_not_drafts(prof, tmp_path):
    semantic = tmp_path / "semantic"
    shutil.copytree(DEMO / "examples/bancassurance-osi", semantic)
    (semantic / "golden/board-report.yaml").write_text("""\
- id: BAD-PERSONAL
  question: Policyholder names
  expect: {figure: 2}
  sql: SELECT count(policyholder.name) FROM policyholder
  source: Board appendix
- id: BAD-TABLE
  question: Retail rows
  expect: {figure: 1}
  sql: SELECT count(retail.customer_id) FROM retail
  source: Board appendix
- id: BAD-WRITE
  question: Changed premiums
  expect: {figure: 1}
  sql: DELETE FROM premium
  source: Board appendix
- id: BAD-CURRENCY
  question: In-force premium in EUR
  expect: {figure: 3900}
  sql: SELECT sum(premium_snapshot.in_force_premium) FROM premium_snapshot
  source: Board appendix
""")
    pack_path = prof.clients / prof.client / "pack.yaml"
    pack = __import__("yaml").safe_load(pack_path.read_text())
    pack["policies"]["restricted_attributes"].append({
        "id": "bancassurance-personal", "action": "refuse", "cite": "POL-PII-01",
        "columns": ["bancassurance.policyholder.name"], "phrases": ["policyholder names"]})
    pack_path.write_text(__import__("yaml").safe_dump(pack, sort_keys=False))
    onboard.create(prof, "bancassurance", semantic_dir=semantic, label="Bancassurance", as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    shown = onboard.show(prof)["units"][0]
    assert not [x for x in shown["items"] if x.get("from") == "client"]
    asks = "\n".join(x["ask"] for x in shown["requests"])
    assert "restricted personal data" in asks
    assert "table retail is not in the bancassurance warehouse" in asks
    assert "read-only: SELECT or WITH ... SELECT" in asks
    assert "question names EUR; SQL measures GBP" in asks


def test_unit_documents_report_the_certification_path_and_tier(prof):
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance", label="Bancassurance",
                   as_="knowledge_engineer")
    release = prof.landing / prof.client / "current"; release.mkdir(parents=True)
    (release / "certification-paths.json").write_text(json.dumps({"DOC-BANCASSURANCE-1": "upstream"}))
    (release / "certification.json").write_text(json.dumps([
        {"asset_id": "DOC-BANCASSURANCE-1", "ai_ready": "yes", "tier": "gold"}]))
    shown = onboard.show(prof)["units"][0]
    assert shown["documents"] == [{"id": "DOC-BANCASSURANCE-1", "title": "premium-guide.md",
                                    "path": "m365", "tier": "gold"}]

    (release / "certification-paths.json").write_text("{}")
    assert onboard.show(prof)["units"][0]["documents"][0]["path"] == "not-used"


@pytest.mark.parametrize("provider,origin", [("none", "rules"), ("mock", "model")])
def test_markdown_flow_provenance_and_each_node_outcome_are_visible(prof, monkeypatch, provider, origin):
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-markdown",
                   label="Bancassurance", as_="knowledge_engineer")
    history = onboard.show(prof)["units"][0]["history"]
    outcomes = [entry for entry in history if entry["step"].startswith("draft-")]
    assert [entry["step"] for entry in outcomes] == [
        "draft-read", "draft-tables", "draft-recipes", "draft-definitions", "draft-golden"]
    assert all(entry["detail"]["made_by"] == origin for entry in outcomes)
    recipes = next(entry["detail"] for entry in outcomes if entry["step"] == "draft-recipes")
    assert recipes["tries"] == (2 if provider == "mock" else 0)
    result = onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-19")
    made = [(item["id"], item["made_by"], item.get("model")) for item in result["items"]]
    requests = [request["ask"] for request in onboard.show(prof)["units"][0]["requests"]]
    if provider == "mock":
        assert made == [("source", "model", "mock:mock"), ("written_premium", "model", "mock:mock"),
                        ("BANCASSURANCE-D01", "model", "mock:mock"),
                        ("ontology-written_premium-fibo-monetary-amount", "model", "mock:mock")]
        assert result["items"][2]["title"] == "Gross written premium for June 2025"
        assert result["items"][2]["expect"]["figure"] == "2000.50" and requests == []
    else:
        assert made == [("source", "rules", None)]
        assert requests[0].startswith("Restate the prose in semantic.md as tables under ## <table> headings")
        assert requests[1:] == ["Define written_premium as plain conditions"]


def test_the_rule_path_drafts_identically_from_identical_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    records = []
    for n in (1, 2):
        clients = tmp_path / str(n) / "clients"; (clients / "meridian").mkdir(parents=True)
        shutil.copy(DEMO / "clients/meridian/pack.yaml", clients / "meridian/pack.yaml")
        root = tmp_path / str(n)
        fresh = Profile("meridian", "meridian", clients, root / "artifacts", root / "stores", root / "landing",
                        root / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, root / "profiles.yaml")
        monkeypatch.setattr(draftflow, "CHECKPOINTER", InMemorySaver())
        onboard.create(fresh, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-markdown",
                       label="Bancassurance", as_="knowledge_engineer")
        onboard.draft(fresh, "bancassurance", "knowledge_engineer", "2026-10-19")
        records.append((clients / "meridian/units/bancassurance/draft.yaml").read_text())
    assert records[0] == records[1] and "made_by: rules" in records[0]


def test_unit_manifest_history_status_and_requests_follow_the_steps(prof):
    """Lease rentals matches no preloaded concept by name, so the steward is asked to confirm there is no equivalent."""
    schema = DEMO / "examples/leasing/schema.sql"
    onboard.create(prof, "leasing", schema, "Asset Finance", rows="none", as_="knowledge_engineer")
    manifest = __import__("yaml").safe_load((prof.clients / prof.client / "units/leasing/unit.yaml").read_text())
    assert manifest["format"] == 2 and manifest["version"] == 1
    assert manifest["inputs"]["schema.sql"] == manifest["ddl_sha256"]
    assert set(manifest["items"]) == {"physical", "glossary", "metrics", "ontology", "documents", "governance"}
    assert manifest["standards"] == [] and manifest["golden"] == {"name": "golden_leasing", "version": "v1"}
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    shown = onboard.show(prof)["units"][0]
    assert shown["status"].endswith("awaiting acceptance")
    assert any(request["source"] == "schema.sql" for request in shown["requests"])
    onboard.rows(prof, "leasing", "csv", csv_dir=DEMO / "examples/leasing/data", as_="knowledge_engineer")
    history = onboard.show(prof)["units"][0]["history"]
    assert [entry["step"] for entry in history] == ["source", "draft", "draft-standards", "source", "request-closed"]
    assert [r["ask"] for r in onboard.show(prof)["units"][0]["requests"]] == [
        "Confirm there's no industry equivalent for Lease Rentals, or name the nearest."]
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    onboard.record_onboard(prof, "a" * 64, "true")
    assert onboard.show(prof)["units"][0]["status"].endswith("gate passed, awaiting sign-off")
    onboard.record_stage(prof, "sign-off", "steward_glossary", {"model_hash": "a" * 64})
    onboard.record_stage(prof, "release", "steward_glossary",
                         {"model_hash": "a" * 64, "pack_version": "1.3.0"})
    finished = onboard.show(prof)["units"][0]
    assert finished["status"].endswith("released in " + "a" * 12)
    assert [entry["step"] for entry in finished["history"]] == [
        "source", "draft", "draft-standards", "source", "request-closed", "accept", "certify", "request-closed", "onboard",
        "sign-off", "release"]
    assert finished["requests"] == [], "certifying the term's mapping answers the request"


def test_format_one_manifest_is_upgraded_on_the_next_write(prof):
    unit = prof.clients / prof.client / "units/old"; unit.mkdir(parents=True)
    schema = b"CREATE TABLE old.t (day DATE, amount_gbp DECIMAL(10,2));"
    (unit / "schema.sql").write_bytes(schema)
    (unit / "unit.yaml").write_text("format: 1\nunit: old\nlabel: Old\nddl: schema.sql\nddl_sha256: %s\nrows:\n  origin: invented\ncreated:\n  by: business_team\n  on: 2026-10-17\n" % __import__("hashlib").sha256(schema).hexdigest())
    shown = onboard.show(prof)["units"][0]
    assert shown["format"] == 2 and shown["version"] == 1
    onboard.draft(prof, "old", "knowledge_engineer", "2026-10-17")
    assert __import__("yaml").safe_load((unit / "unit.yaml").read_text())["format"] == 2


def test_object_store_history_is_read_modified_and_written(monkeypatch, tmp_path, prof):
    remote = tmp_path / "object-store"; monkeypatch.setenv("STORE_URL", f"file://{remote}")
    store = open_store(f"file://{remote}")
    for name in ("pack.yaml", "pack.lock"):
        store.put(f"clients/meridian/{name}", (DEMO / "clients/meridian" / name).read_bytes())
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    lines = store.get("clients/meridian/units/leasing/history.jsonl").decode().splitlines()
    assert [json.loads(line)["step"] for line in lines] == ["source", "draft", "draft-standards"]


def _client_asks(prof, tmp_path, golden: str, monkeypatch=None) -> tuple[dict, str]:
    semantic_dir = tmp_path / "semantic"
    shutil.copytree(DEMO / "examples/bancassurance-osi", semantic_dir)
    (semantic_dir / "golden/board-report.yaml").write_text(golden)
    onboard.create(prof, "bancassurance", semantic_dir=semantic_dir, label="Bancassurance", as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    shown = onboard.show(prof)["units"][0]
    return shown, "\n".join(x["ask"] for x in shown["requests"])


def test_a_client_question_the_agent_cannot_read_as_the_units_term_is_held(prof, tmp_path):
    shown, asks = _client_asks(prof, tmp_path, """\
- id: HYPHEN
  question: In-force premium at 30 June 2025
  expect: {figure: 2000.00}
  sql: SELECT round(sum(premium_snapshot.in_force_premium), 2) FROM premium_snapshot WHERE premium_snapshot.snapshot_on = DATE '2025-06-30'
  source: Q2 board report
- id: OTHER
  question: Total revenue for June 2025
  expect: {figure: 2000.00}
  sql: SELECT round(sum(premium_snapshot.in_force_premium), 2) FROM premium_snapshot WHERE premium_snapshot.snapshot_on = DATE '2025-06-30'
  source: Q2 board report
- id: PLAIN
  question: In force premium at 30 June 2025
  expect: {figure: 2000.00}
  sql: SELECT round(sum(premium_snapshot.in_force_premium), 2) FROM premium_snapshot WHERE premium_snapshot.snapshot_on = DATE '2025-06-30'
  source: Q2 board report
""")
    assert [x["id"] for x in shown["items"] if x.get("from") == "client"] == ["PLAIN"]
    assert "We can't match 'In-force premium at 30 June 2025' to a term: rephrase it, or add 'in-force premium' as an alias." in asks
    assert "'Total revenue for June 2025' reads as the term 'Revenue', which this unit does not define" in asks


def test_client_ids_currency_words_and_awkward_results_are_requests_not_failures(prof, tmp_path):
    shown, asks = _client_asks(prof, tmp_path, """\
- id: BANCASSURANCE-D01
  question: Written premium for June 2025
  expect: {figure: 1200.00}
  sql: SELECT round(sum(premium.premium_amount), 2) FROM premium JOIN policy ON premium.policy_id = policy.policy_id WHERE policy.status = 'active' AND premium.written_on >= DATE '2025-06-01' AND premium.written_on < DATE '2025-07-01'
  source: Q2 board report
- id: ALL-POLICIES
  question: Written premium for all policies in June 2025
  expect: {figure: 2000.50}
  sql: SELECT round(sum(premium.premium_amount), 2) FROM premium WHERE premium.written_on >= DATE '2025-06-01' AND premium.written_on < DATE '2025-07-01'
  source: Q2 board report
- id: EMPTY-ZERO
  question: Written premium for July 2025
  expect: {figure: 0}
  sql: SELECT sum(premium.premium_amount) FROM premium WHERE premium.written_on >= DATE '2025-07-01' AND premium.written_on < DATE '2025-08-01'
  source: Q2 board report
- id: EMPTY-STATED
  question: Written premium for August 2025
  expect: {figure: 5}
  sql: SELECT sum(premium.premium_amount) FROM premium WHERE premium.written_on >= DATE '2025-08-01' AND premium.written_on < DATE '2025-09-01'
  source: Q2 board report
- id: SETTING
  question: Written premium for September 2025
  expect: {figure: 1}
  sql: SELECT sum(premium.premium_amount) FROM premium WHERE premium.premium_id = CAST(current_setting('temp_directory') AS INTEGER)
  source: Q2 board report
""")
    client = [x["id"] for x in shown["items"] if x.get("from") == "client"]
    assert client == ["ALL-POLICIES", "EMPTY-ZERO"], "'all' is no currency; a NULL sum over an empty period is the 0 stated"
    assert "has the id BANCASSURANCE-D01, which a drafted item already has — give it another id." in asks
    assert "returned no figure (NULL: no rows matched)" in asks
    assert "Your SQL for 'Written premium for September 2025' was refused: server-information function current_setting is not allowed — check it." in asks
    assert "warehouses/" not in asks, "the warehouse's own error, naming a server path, stays in the server log"
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")


def test_client_sql_runs_under_a_time_limit_and_must_return_a_number(monkeypatch):
    import decimal
    import duckdb
    from kge.adapters.warehouse.duckdb import DuckDBSession
    monkeypatch.setattr(onboard.figures, "CLIENT_SQL_SECONDS", 0.2)
    con = DuckDBSession(duckdb.connect())
    with pytest.raises(onboard.ClientFigureError, match="ran for more than 0.2 seconds"):
        onboard._client_figure(con, f"SELECT count(*) FROM range({10**8}) a, range({10**5}) b", decimal.Decimal(1))
    with pytest.raises(onboard.ClientFigureError, match="returned str rather than a number"):
        onboard._client_figure(con, "SELECT 'abc'", decimal.Decimal(1))
    with pytest.raises(onboard.ClientFigureError, match="out of range"):
        onboard._client_figure(con, "SELECT 1e300", decimal.Decimal(1))
    assert onboard._client_figure(con, "SELECT 2000.5", decimal.Decimal(1)) == decimal.Decimal("2000.5")


def test_the_cached_proposal_is_kept_outside_the_uploaded_folder(prof):
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi", label="Bancassurance",
                   as_="knowledge_engineer")
    unit = prof.clients / prof.client / "units/bancassurance"
    assert (unit / "proposal.json").is_file() and not (unit / "semantic/proposal.json").exists()
    held = json.loads((unit / "proposal.json").read_text())
    held["proposal"]["hints"]["measures"][0]["filters"][0]["op"] = "= 'x' OR 1=1 OR 'a' ="
    (unit / "proposal.json").write_text(json.dumps(held))
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    record = __import__("yaml").safe_load((unit / "draft.yaml").read_text())
    predicates = [s["predicate"] for x in record["items"] if x.get("kind") == "term" for s in x["pack"].get("row_selectors", [])]
    assert predicates == ["policy.policy_id = premium.policy_id AND policy.status = 'active'"], "the forged OR never lands"


def test_a_file_that_cannot_be_written_after_the_manifest_rolls_the_manifest_back(prof, monkeypatch):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    before = onboard.show(prof)["units"][0]
    real = packstore.Store.write

    def failing(self, name, data):
        if name.endswith("draft.yaml"):
            raise OSError("disk full")
        return real(self, name, data)
    monkeypatch.setattr(packstore.Store, "write", failing)
    monkeypatch.setattr(onboard.common, "LAND_TRIES", 2)
    with pytest.raises(onboard.OnboardError) as failed:
        onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    assert failed.value.status == 503 and "nothing changed: run it again" in failed.value.message
    after = onboard.show(prof)["units"][0]
    assert after["golden"] == before["golden"] and after["status"] == before["status"]
    assert [e["step"] for e in after["history"]][-3:] == ["draft", "draft-standards", "rolled-back"]
    monkeypatch.setattr(packstore.Store, "write", real)
    assert onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")["drafted"], "the same command then completes"


def test_a_write_that_loses_to_another_is_a_conflict(prof, monkeypatch):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    from kge.adapters.objstore import StoreConflict
    draft_or_manifest = ["draft.yaml"]

    real = packstore.Store.write

    def lost(self, name, data):
        if name.endswith(draft_or_manifest[0]):
            raise StoreConflict(f"{name} changed by someone else; reload")
        return real(self, name, data)
    monkeypatch.setattr(packstore.Store, "write", lost)
    for draft_or_manifest in (["draft.yaml"], ["unit.yaml"]):
        with pytest.raises(onboard.OnboardError) as failed:
            onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
        assert failed.value.status == 409 and "changed by someone else; reload" in failed.value.message


def test_history_a_crash_left_uncounted_is_not_read(prof):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    unit = prof.clients / prof.client / "units/leasing"
    with (unit / "history.jsonl").open("a") as history:
        history.write(json.dumps({"at": "x", "by": "y", "step": "phantom", "version": 1, "detail": {}}) + "\n")
    assert "phantom" not in [e["step"] for e in onboard.show(prof)["units"][0]["history"]]
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    assert [e["step"] for e in onboard.show(prof)["units"][0]["history"]][-2:] == ["draft", "draft-standards"]
    assert "phantom" not in (unit / "history.jsonl").read_text()


def test_an_onboarded_units_currency_answers_only_its_own_terms():
    from types import SimpleNamespace
    from kge.services.ask.engine.core import no_conversion, parse
    estate = dict(ALIASES={"written premium": "Written Premium"}, UNIT_WORDS={}, CURRENCIES=["EUR", "USD"],
                  CONVERSIONS=[("EUR", "USD", "period_end_rate_from_EUR")])
    before = SimpleNamespace(**estate)
    after = SimpleNamespace(**estate, TERM_CURRENCIES={"Written Premium": ["GBP"]})
    g042 = "Total bank revenue in GBP for Q2 2025"
    for domain in (before, after):
        asked = parse(g042, ["Revenue", "Written Premium"], domain)
        assert asked.special == "no_conversion_standard", "G042 abstains before and after the GBP unit is onboarded"
        assert no_conversion("GBP", domain, asked.term) == (
            "No GBP conversion standard exists: certified figures are in EUR and USD, and the only conversion is EUR to USD "
            "(period_end_rate_from_EUR).")
    assert parse("Written premium in GBP for June 2025", ["Revenue", "Written Premium"], after).special is None
    assert parse("Written premium in JPY for June 2025", ["Revenue", "Written Premium"], after).special == "no_conversion_standard"
    for q in ("Corporate credit exposure in USD at 30 June 2025", "Retail revenue in EUR for June 2025",
              "Corporate revenue in EUR for FY26-Q1"):
        assert parse(q, ["Revenue", "Credit Exposure"], after).special == parse(q, ["Revenue", "Credit Exposure"], before).special


def test_the_onboarded_units_are_those_the_artifacts_drafts_name(tmp_path):
    from types import SimpleNamespace
    from kge.services.ask.engine.core import Graph
    artifacts = tmp_path / "artifacts"
    prof = SimpleNamespace(artifacts=artifacts, client="meridian", model_hash=lambda: "a" * 64)
    graph = SimpleNamespace(profile=prof)
    assert Graph.onboarded_units(graph) == set(), "no drafts.json: no onboarded unit, the estate as before"
    folder = artifacts / "meridian" / ("a" * 64); folder.mkdir(parents=True)
    (folder / "drafts.json").write_text(json.dumps({"format": 1, "items": [{"unit": "bancassurance", "id": "source"}]}))
    assert Graph.onboarded_units(graph) == {"bancassurance"}


def test_an_onboarded_snapshot_is_the_latest_one_the_contexts_joined_conditions_keep():
    """The point-in-time path is keyed on the onboarded unit (its drafts.json), not on a context id, and the latest
    snapshot is chosen under every condition the query applies, a joined table's included."""
    import datetime as dt
    from types import SimpleNamespace
    import duckdb
    from kge.services.ask.engine.core import Period
    from kge.semantics.query import Asked, Context, Plan
    con = duckdb.connect()
    con.execute("CREATE TABLE policy (policy_id INT, status VARCHAR)")
    con.execute("INSERT INTO policy VALUES (10, 'active'), (20, 'lapsed')")
    con.execute("CREATE TABLE premium_snapshot (policy_id INT, snapshot_on DATE, in_force_premium DECIMAL(10,2))")
    con.execute("INSERT INTO premium_snapshot VALUES (10, '2025-06-29', 1900), (20, '2025-06-30', 2000)")

    def plan(onboarded: set[str], unit: str = "bancassurance") -> Plan:
        sem = SimpleNamespace(d=SimpleNamespace(ONBOARDED=frozenset(onboarded)),
                              dates=lambda period: (dt.date(2025, 6, 1), dt.date(2025, 6, 30)))
        context = Context(id="in_force", label="", description="", term="t", measure="m", expression="", aggregation="sum",
                          grain="as_of", fact=f"{unit}.premium_snapshot", values=(), filters=(), selectors=(), joins=())
        p = Plan(sem, context, Asked(period=Period("month", "2025-06-01")))
        p.fact, p.period_on, p.select = "premium_snapshot", "premium_snapshot.snapshot_on", "sum(premium_snapshot.in_force_premium)"
        p.joins = ["JOIN policy ON policy.policy_id = premium_snapshot.policy_id"]
        p.where = ["policy.status = 'active'"]
        return p
    assert con.execute(plan({"bancassurance"}).query(None)).fetchone()[0] == 1900, "the active policy's latest snapshot"
    assert "max(" not in plan(set()).query(None), "a context of a unit not onboarded keeps its planning"


LEASING_DDL = (DEMO / "examples/leasing/schema.sql").read_text()
LEASING_CSV = {p.name: p.read_bytes() for p in (DEMO / "examples/leasing/data").glob("*.csv")}
JUNE_ROW = b"121,1,2025-06-30,38.75\n"


def _release(prof, unit: str, model_hash: str = "a" * 64) -> None:
    """What make release records for the units a released candidate carries (kge/graph/release.py)."""
    from kge.pipeline import unit_history
    unit_history.record(prof, "release", "steward_glossary", {"model_hash": model_hash, "pack_version": "1.0.0"}, [unit])


def _live_leasing(prof) -> None:
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=LEASING_CSV, ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    onboard.build(prof)
    _release(prof, "leasing")


def _live_bancassurance(prof) -> None:
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-17")
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-17")
    onboard.build(prof)
    _release(prof, "bancassurance")


def _june(csv: dict) -> dict:
    """The CSVs with one more June rental, which moves the drafted June figure from 1461.25 to 1500.00."""
    return {**csv, "rental.csv": csv["rental.csv"] + JUNE_ROW}


def _unit_files(prof):
    return _files_under(prof.clients / prof.client / "units", prof.warehouses)


def test_an_update_is_classified_item_by_item_against_the_live_draft(prof):
    """The update's draft never reaches the pack before it lands: the live draft is what compiles."""
    _live_bancassurance(prof)
    made = onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2",
                          label="Bancassurance", as_="knowledge_engineer")
    assert made["updated"] and made["version"] == 2 and not made["created"]
    drafted = onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    changes = drafted["changes"]
    assert changes["changed"] == ["source", "written_premium", "BANCASSURANCE-D01", "BR-02"]
    assert changes["added"] == ["commission", "BANCASSURANCE-D03", "ontology-commission-fibo-monetary-amount"], \
        "a new term's question takes the next unused number, and its mapping is new"
    assert changes["removed"] == ["in_force_premium", "BANCASSURANCE-D02", "BR-01", "ontology-in_force_premium-fibo-monetary-amount"]
    assert "ontology-written_premium-fibo-monetary-amount" in changes["unchanged"] and "policy-policyholder-name" in changes["unchanged"]
    details = changes["details"]
    assert details["source"][0] == "table premium: column commission_amount added"
    assert details["written_premium"] == ["recipe policy.policy_id = premium.policy_id AND policy.status = 'active' -> "
                                          "policy.policy_id = premium.policy_id AND policy.status IN ('active', 'lapsed')"]
    assert details["BANCASSURANCE-D01"][0] == "expected figure 1200.00 -> 1650.00"
    items = {x["id"]: x for x in drafted["items"]}
    assert items["BANCASSURANCE-D03"]["expect"]["figure"] == "245.05"
    term = items["commission"]["pack"]
    assert (term.get("contexts") or [{"golden": {"items": ["BANCASSURANCE-D03"]}}])[0]["golden"]["items"] == ["BANCASSURANCE-D03"]
    source_sets = items["source"]["pack"]["tests.sets"][0]["items"]
    assert source_sets == ["BANCASSURANCE-D01"], "the renumbered ids reach the source's golden set"
    assert all(x["state"] == "drafted" for x in drafted["items"] if x.get("change"))
    pack = packs.read(prof.clients, prof.client, lock=False)
    assert "in_force_premium" in {g["id"] for g in pack.data["glossary"]}
    assert "commission" not in {g["id"] for g in pack.data["glossary"]}


def test_unchanged_items_keep_their_state_and_only_the_changes_are_reviewed(prof):
    _live_leasing(prof)
    live = (prof.clients / prof.client / "units/leasing/draft.yaml").read_bytes()
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    drafted = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    assert drafted["changes"]["unchanged"] == ["source", "lease-rentals", "ontology-lease-rentals-fibo-monetary-amount"]
    assert drafted["changes"]["changed"] == ["LEASING-D01"] and not drafted["changes"]["added"]
    assert drafted["changes"]["details"]["LEASING-D01"][0] == "expected figure 1461.25 -> 1500.00"
    items = {x["id"]: x for x in drafted["items"]}
    assert items["lease-rentals"]["state"] == "certified" and items["lease-rentals"]["certified_on"] == "2026-10-17"
    assert items["LEASING-D01"]["state"] == "drafted"
    accepted = onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
    assert accepted["changed"] == ["LEASING-D01"] and accepted["unchanged"] == ["source", "lease-rentals", "ontology-lease-rentals-fibo-monetary-amount"]
    assert (prof.clients / prof.client / "units/leasing/draft.yaml").read_bytes() == live, "review is of the update"
    assert onboard.show(prof)["units"][0]["version"] == 1
    onboard.certify(prof, "leasing", "LEASING-D01", "steward_glossary", "2026-10-18")
    built = onboard.build(prof)
    assert built["updates"] == ["leasing v2 landed (1 changed, 0 added, 0 removed) in a warehouse of its own; v1's "
                                "release, and its rows, stay current until the checks pass and a steward signs off and "
                                "releases it"]
    unit = onboard.show(prof)["units"][0]
    assert unit["version"] == 2 and "update" not in unit and unit["updated"]["changed"] == 1
    assert unit["status"] == "leasing v2: 4/4 accepted, 4/4 certified, v1 live in aaaaaaaaaaaa, awaiting checks"
    landed = next(h for h in unit["history"] if h["step"] == "update")
    assert landed["version"] == 2 and landed["detail"]["changed"] == ["LEASING-D01"] and landed["detail"]["from"] == 1
    question = next(x for x in unit["items"] if x["kind"] == "question")
    assert question["expect"]["figure"] == "1500.00" and "change" not in question
    golden = json.loads((prof.estate() / "golden_leasing_v1.json").read_text())
    assert golden["items"][0]["expect"]["figure"] == "1500.00"
    _release(prof, "leasing", "b" * 64)
    assert onboard.show(prof)["units"][0]["status"].endswith("released in bbbbbbbbbbbb")
    _release(prof, "leasing")
    assert onboard.show(prof)["units"][0]["status"].startswith("leasing v2: 4/4 accepted, 4/4 certified, v1 live in aaaaaaaaaaaa")


def test_a_pending_update_shows_what_changed_on_the_status_line_and_in_onboard_status(prof):
    _live_bancassurance(prof)
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2", label="Bancassurance",
                   as_="knowledge_engineer")
    unit = onboard.show(prof)["units"][0]
    assert unit["status"] == "bancassurance v2 (update): uploaded — awaiting draft; v1 stays live in aaaaaaaaaaaa"
    assert not [h for h in unit["history"] if h["step"] == "request-closed" and h["version"] == 2], \
        "staging an update closes no request"
    assert len(unit["requests"]) == 3
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    said = onboard.status_lines(prof)["said"].splitlines()
    assert said[0] == ("bancassurance v2 (update): 4 changed, 3 added, 4 removed — awaiting acceptance; "
                       "v1 stays live in aaaaaaaaaaaa")
    assert "  added commission: new term: Commission" in said
    assert "  removed in_force_premium: removed term: In Force Premium" in said
    assert any(line.startswith("  changed written_premium: recipe ") for line in said)
    unit = onboard.show(prof)["units"][0]
    assert unit["update"]["changes"]["added"] == ["commission", "BANCASSURANCE-D03", "ontology-commission-fibo-monetary-amount"]
    assert {x["id"]: (x.get("change") or {}).get("kind") for x in unit["items"]}["in_force_premium"] == "removed"
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    assert "— awaiting certification;" in onboard.show(prof)["units"][0]["status"]


def test_a_removal_is_reviewed_and_a_released_golden_question_it_breaks_is_an_open_request(prof):
    _live_bancassurance(prof)
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    asks = {r["source"]: r["ask"] for r in onboard.show(prof)["units"][0]["requests"]}
    assert asks["BR-01"].startswith("Released golden question BR-01 ('In force premium at 30 June 2025') depends on "
                                    "in_force_premium, which this update removes")
    assert "BANCASSURANCE-D02" in asks
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    held = onboard.build(prof)["updates"][0]
    assert held.startswith("bancassurance v2 (update) not landed: 11 item(s) await acceptance or certification")
    assert onboard.show(prof)["units"][0]["version"] == 1
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-18")
    unit = onboard.show(prof)["units"][0]
    assert "BR-01" not in {r["source"] for r in unit["requests"]}, "certifying the removal answers the request"
    closed = [h for h in unit["history"] if h["step"] == "request-closed" and h["detail"]["source"] == "BR-01"]
    assert closed and closed[0]["version"] == 2
    onboard.build(prof)
    unit = onboard.show(prof)["units"][0]
    assert unit["version"] == 2 and {x["id"] for x in unit["items"]} == {
        "source", "written_premium", "commission", "BANCASSURANCE-D01", "BANCASSURANCE-D03", "BR-02",
        "ontology-written_premium-fibo-monetary-amount", "ontology-commission-fibo-monetary-amount", "policy-policyholder-name"}
    pack = packs.read(prof.clients, prof.client, lock=False)
    assert "in_force_premium" not in {g["id"] for g in pack.data["glossary"]} and not pack.findings
    assert "commission" in {g["id"] for g in pack.data["glossary"]}
    files = {p.relative_to(prof.clients / prof.client / "units/bancassurance").as_posix()
             for p in (prof.clients / prof.client / "units/bancassurance").rglob("*") if p.is_file()}
    assert not any(name.startswith("update/") for name in files), "the staged update is gone once it lands"
    assert "commission_amount" in (prof.clients / prof.client / "units/bancassurance/data/premium.csv").read_text()


def test_an_update_cannot_remove_a_term_another_units_question_uses(prof):
    _live_bancassurance(prof)
    other = prof.clients / prof.client / "units/partner"
    other.mkdir(parents=True)
    (other / "unit.yaml").write_text("format: 1\nunit: partner\nlabel: Partner\nrows:\n  origin: invented\n")
    (other / "draft.yaml").write_text("format: 1\nunit: partner\nitems:\n- id: PARTNER-Q1\n  kind: question\n"
                                      "  terms: [in_force_premium]\n  question: In force premium by partner\n")
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    asks = {r["source"]: r["ask"] for r in onboard.show(prof)["units"][0]["requests"]}
    assert asks["partner/PARTNER-Q1"] == ("Question PARTNER-Q1 of unit partner uses in_force_premium, which this update "
                                          "removes: keep the term, or remove that question first.")
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-18")
    said = onboard._land_update(prof, packstore.for_profile(prof), "bancassurance")
    assert said == ("bancassurance v2 (update) not landed: question PARTNER-Q1 of unit partner uses in_force_premium, "
                    "which it removes; v1 stays live")
    assert onboard.show(prof)["units"][0]["version"] == 1


def test_a_schema_change_is_a_changed_table_and_a_renamed_column_a_removed_and_added_term(prof):
    _live_leasing(prof)
    renamed = LEASING_DDL.replace("lease_rentals_usd", "rentals_billed_usd")
    csv = {**LEASING_CSV, "rental.csv": LEASING_CSV["rental.csv"].replace(b"lease_rentals_usd", b"rentals_billed_usd")}
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=csv, ddl_text=renamed, as_="knowledge_engineer")
    changes = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")["changes"]
    assert changes["changed"] == ["source"]
    assert changes["details"]["source"] == ["table rental: column rentals_billed_usd added",
                                            "table rental: column lease_rentals_usd removed"]
    assert changes["added"] == ["rentals-billed", "LEASING-D02", "ontology-rentals-billed-fibo-monetary-amount"]
    assert changes["removed"] == ["lease-rentals", "LEASING-D01", "ontology-lease-rentals-fibo-monetary-amount"]


def test_identical_files_again_change_nothing_live_or_pending(prof):
    _live_leasing(prof)
    before = _unit_files(prof)
    again = onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=LEASING_CSV, ddl_text=LEASING_DDL,
                           as_="knowledge_engineer")
    assert again["said"] == "source exists already; nothing changed" and not again.get("updated")
    assert _unit_files(prof) == before, "byte for byte, history included"
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    pending = _unit_files(prof)
    same = onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV),
                          ddl_text=LEASING_DDL, as_="knowledge_engineer")
    assert same["said"] == "update v2 exists already; nothing changed"
    live = onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=LEASING_CSV, ddl_text=LEASING_DDL,
                          as_="knowledge_engineer")
    assert live["said"] == "source exists already; nothing changed (update v2 is still pending)"
    assert onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")["said"] == \
        "update v2 draft exists already; nothing changed"
    assert _unit_files(prof) == pending


def test_a_second_upload_replaces_a_pending_update_and_a_unit_in_first_review_keeps_its_one_upload(prof):
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=LEASING_CSV, ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    with pytest.raises(onboard.OnboardError, match="exists with different data") as refused:
        onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV),
                       ddl_text=LEASING_DDL, as_="knowledge_engineer")
    assert refused.value.status == 409, "an update is for a released unit"
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    _release(prof, "leasing")
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    bigger = {**LEASING_CSV, "rental.csv": LEASING_CSV["rental.csv"] + b"121,1,2025-06-30,138.75\n"}
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=bigger, ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    unit = onboard.show(prof)["units"][0]
    assert unit["update"]["drafted"] is False and unit["status"].startswith("leasing v2 (update): uploaded")
    assert "replaces" in next(h for h in reversed(unit["history"]) if h["step"] == "update-source")["detail"]
    redrafted = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    assert redrafted["changes"]["details"]["LEASING-D01"][0] == "expected figure 1461.25 -> 1600.00"
    with pytest.raises(onboard.OnboardError, match="needs its rows"):
        onboard.create(prof, "leasing", label="Asset Finance", rows="none", ddl_text=LEASING_DDL, as_="knowledge_engineer")


def _artifact(prof, model_hash: str, released: bool = False) -> Path:
    """An artifact as make compile writes it for the pack as it stands (its drafts.json names each onboarded unit's
    warehouse version, kge/compiler/emit.py), and, released, its graph's RELEASED marker as make release writes it."""
    pack = packs.read(prof.clients, prof.client, identities=prof.identities)
    folder = prof.artifacts / prof.client / model_hash
    folder.mkdir(parents=True)
    (folder / "drafts.json").write_text(json.dumps({"format": 1, "items": pack.drafts, "warehouses": pack.warehouses}))
    if released:
        graph = prof.store / prof.client / model_hash
        graph.mkdir(parents=True)
        (graph / "RELEASED").write_text(json.dumps({"ladybug": {"model_hash": model_hash}}))
    return folder


def _point(prof, model_hash: str) -> None:
    """make release's swap of the current pointer (kge/graph/release.py swap)."""
    current = prof.artifacts / prof.client / "current"
    current.unlink(missing_ok=True)
    current.symlink_to(model_hash)


JUNE_RENTALS = ("SELECT round(sum(lease_rentals_usd), 2) FROM rental "
                "WHERE billed_on >= DATE '2025-06-01' AND billed_on < DATE '2025-07-01'")


def _served(prof, model_hash: str | None = None):
    """The figure a process started now answers, from the warehouses of the artifact it serves: current's, or a pinned
    hash's (make serve HASH=), exactly as kge/services/ask/engine/core.py Engine opens them."""
    from kge.adapters.warehouse.duckdb import Warehouses
    from kge.services.ask.engine.core import Graph
    from types import SimpleNamespace
    pinned = (prof.at(model_hash) if model_hash else prof).pin()
    graph = SimpleNamespace(profile=pinned)
    versions = Graph.warehouse_versions(graph)
    w = Warehouses(prof.warehouses, ["leasing"], versions=versions, model_hash=pinned.model_hash(),
                   inputs=Graph.warehouse_inputs(graph))
    try:
        return w.run("leasing", JUNE_RENTALS), versions
    finally:
        for con in w.con.values():
            con.close()


def test_a_release_reads_the_rows_it_was_checked_on_until_another_release_and_a_rollback_reads_them_again(prof):
    """The owner's requirement: the previous release stays live until sign-off and can be rolled back, its rows with its
    definitions. Landing v2 writes a warehouse of its own; a process started on the released hash reads v1's rows, the
    candidate's checks read v2's, the release of v2 reads v2's, and a rollback to v1's hash reads v1's again."""
    import duckdb
    _live_leasing(prof)
    assert yaml.safe_load((prof.clients / prof.client / "units/leasing/draft.yaml").read_text())["warehouse"]["version"] == 1
    h1, h2 = "1" * 64, "2" * 64
    _artifact(prof, h1, released=True)
    _point(prof, h1)
    assert _served(prof) == (1461.25, {"leasing": 1})
    held = duckdb.connect(str(_v(prof, 1)), read_only=True)
    try:
        onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV),
                       ddl_text=LEASING_DDL, as_="knowledge_engineer")
        onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
        assert not _v(prof, 2).exists() and not list(prof.warehouses.glob(".*")), "staging writes no warehouse"
        onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
        onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
        before = _v(prof, 1).read_bytes()
        assert onboard.build(prof)["updates"][0].startswith("leasing v2 landed")
        assert _v(prof, 1).read_bytes() == before, "landing v2 leaves v1's warehouse as it was"
        assert held.execute(JUNE_RENTALS).fetchone()[0] == 1461.25, "the running service keeps reading v1's rows"
    finally:
        held.close()
    assert yaml.safe_load((prof.clients / prof.client / "units/leasing/draft.yaml").read_text())["warehouse"]["version"] == 2
    _artifact(prof, h2)
    assert _served(prof) == (1461.25, {"leasing": 1}), "before the release, a fresh process answers v1's figure"
    assert _served(prof, h2) == (1500.00, {"leasing": 2}), "the candidate's checks (make margin HASH=) read v2's rows"
    _point(prof, h2)
    (prof.store / prof.client / h2).mkdir(parents=True)
    (prof.store / prof.client / h2 / "RELEASED").write_text(json.dumps({"ladybug": {"model_hash": h2}}))
    assert _served(prof) == (1500.00, {"leasing": 2}), "after the release, v2's figure"
    _point(prof, h1)
    assert _served(prof) == (1461.25, {"leasing": 1}), "after the rollback, v1's figure from v1's rows again"


def test_an_update_after_a_rollback_is_numbered_above_every_kept_version_and_never_overwrites_one(prof):
    """Release v2, roll back to v1 (the rollback restores the unit's files, unit.yaml among them, as v1's), and upload
    again: the new version is v3, not a second v2 over the released one, so v2's hash can still be rolled back to."""
    from kge.graph import history
    _live_leasing(prof)
    h1, h2 = "1" * 64, "2" * 64
    _artifact(prof, h1, released=True)
    _point(prof, h1)
    v1_files = history._capture_store(prof)

    def update(rows, on):
        onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=rows, ddl_text=LEASING_DDL,
                       as_="knowledge_engineer")
        onboard.draft(prof, "leasing", "knowledge_engineer", on)
        onboard.accept(prof, "leasing", "all", "business_team", on)
        onboard.certify(prof, "leasing", "all", "steward_glossary", on)
        return onboard.build(prof)["updates"][0]

    assert update(_june(LEASING_CSV), "2026-10-18").startswith("leasing v2 landed")
    _artifact(prof, h2, released=True)
    _point(prof, h2)
    v2 = _v(prof, 2).read_bytes(), warehouse_versions.stamped(prof.warehouses, "leasing", 2)
    history._restore_captured_store(prof, v1_files)
    _point(prof, h1)
    assert onboard._manifest(prof, packstore.for_profile(prof), "leasing")["version"] == 1, "the rollback restored v1"
    third = {**LEASING_CSV, "rental.csv": LEASING_CSV["rental.csv"] + b"122,1,2025-06-29,10.00\n"}
    assert update(third, "2026-10-19").startswith("leasing v3 landed"), "numbered above v2, which h2 still names"
    assert warehouse_versions.versions(prof.warehouses, "leasing") == [1, 2, 3]
    assert (_v(prof, 2).read_bytes(), warehouse_versions.stamped(prof.warehouses, "leasing", 2)) == v2, \
        "v2's warehouse is as h2 was released on"
    assert _served(prof, h2) == (1500.00, {"leasing": 2}), "h2 can still be rolled back to, on its own rows"
    assert _served(prof) == (1461.25, {"leasing": 1})


def test_a_rollback_that_removes_the_unit_removes_the_versions_no_release_keeps(prof):
    """A rollback to before the unit (kge/graph/history.py _cleanup_removed_units) removes its unreleased candidate's
    version; one a released hash names stays, so rolling forward to that hash reads the rows it was released on."""
    from kge.graph import history
    (prof.artifacts / prof.client / ("0" * 64)).mkdir(parents=True)
    _live_leasing(prof)
    h1 = "1" * 64
    _artifact(prof, h1, released=True)
    _point(prof, h1)
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
    onboard.build(prof)
    _artifact(prof, "2" * 64)
    assert warehouse_versions.versions(prof.warehouses, "leasing") == [1, 2]
    _point(prof, "0" * 64)
    history._cleanup_removed_units(prof, {"leasing"})
    assert warehouse_versions.versions(prof.warehouses, "leasing") == [1], "h1 is released on v1; v2's candidate goes"
    (prof.store / prof.client / h1 / "RELEASED").unlink()
    history._cleanup_removed_units(prof, {"leasing"})
    assert not (prof.warehouses / "onboarded").exists(), "no release keeps any version: the unit's folder goes"


@pytest.mark.parametrize("folder", sorted(p.name for p in (DEMO / "examples").glob("bancassurance*") if p.is_dir()))
def test_every_bancassurance_example_folder_sources_and_drafts(prof, monkeypatch, folder):
    """make source and make draft on each example folder, as README step 14 names them: each one's seeds load into its
    declared tables and it drafts; the templated dbt folder is refused before anything is written."""
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    if folder == "bancassurance-unsafe":
        from kge.estate import semantic
        with pytest.raises(semantic.SemanticError, match="templates are refused"):
            onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples" / folder, label="Bancassurance",
                           as_="knowledge_engineer")
        assert not (prof.clients / prof.client / "units").exists() and not (prof.warehouses / "onboarded").exists()
        return
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples" / folder, label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-17")
    record = yaml.safe_load((prof.clients / prof.client / "units/bancassurance/draft.yaml").read_text())
    assert record["items"], f"{folder} drafted nothing"


def test_installing_over_a_kept_version_made_from_other_inputs_is_refused(tmp_path):
    part = tmp_path / "part.duckdb"
    part.write_bytes(b"first")
    warehouse_versions.install(part, tmp_path, "leasing", 2, "a" * 64)
    part.write_bytes(b"second")
    with pytest.raises(FileExistsError, match="leasing's warehouse v2 is kept"):
        warehouse_versions.install(part, tmp_path, "leasing", 2, "b" * 64, keep={1, 2})
    assert _v(SimpleNamespace(warehouses=tmp_path), 2).read_bytes() == b"first" and part.is_file()
    warehouse_versions.install(part, tmp_path, "leasing", 2, "a" * 64, keep={1, 2})


def test_retention_keeps_every_released_version_and_the_candidate_and_a_missing_version_is_refused(prof, monkeypatch):
    """Landing a version keeps those a release names (and current's) and the new one; an unreleased candidate's version
    is removed when a newer one lands, and the hash that names it is refused by name, never served another version's
    rows; reset removes every version. A file of an older layout beside the versions changes nothing, and a unit onboarded
    again after its reset numbers its versions from 1 again, so v1's hash, released before and naming other inputs, is
    refused rather than served the new rows."""
    from kge.graph import release
    from kge.pipeline.warehouse_versions import MissingWarehouse
    (prof.artifacts / prof.client / ("0" * 64)).mkdir(parents=True)
    _point(prof, "0" * 64)
    _live_leasing(prof)
    h1, h2, h3 = "1" * 64, "2" * 64, "3" * 64
    _artifact(prof, h1, released=True)
    _point(prof, h1)
    for version, (rows, h) in enumerate([(_june(LEASING_CSV), h2), ({**LEASING_CSV, "rental.csv": LEASING_CSV["rental.csv"] +
                                          b"122,1,2025-06-29,10.00\n"}, h3)], 2):
        onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=rows, ddl_text=LEASING_DDL,
                       as_="knowledge_engineer")
        onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
        onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
        onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
        onboard.build(prof)
        _artifact(prof, h)
    assert warehouse_versions.versions(prof.warehouses, "leasing") == [1, 3], \
        "v1 is released and current; v3 is the candidate; v2's candidate was superseded unreleased"
    with pytest.raises(MissingWarehouse, match=rf"the artifact {h2[:12]} reads leasing's warehouse v2 "
                                               r"\(onboarded/leasing/v2/leasing\.duckdb\), not on disk") as refused:
        _served(prof, h2)
    assert "never read from another one" in str(refused.value)
    from kge.graph import load
    (prof.artifacts / prof.client / h2 / "manifest.json").write_text("{}")
    monkeypatch.setattr(load, "read_artifact", lambda folder, client: {"model_hash": h2})
    with pytest.raises(release.Unreleasable, match="leasing's warehouse v2"):
        release.release(prof, h2)
    assert (prof.artifacts / prof.client / "current").readlink().name == h1, "current is as it was"
    (prof.warehouses / "leasing.duckdb").write_bytes(b"not v2")
    with pytest.raises(MissingWarehouse):
        _served(prof, h2)
    (prof.warehouses / "leasing.duckdb").unlink()
    onboard.reset(prof, "leasing")
    assert not (prof.warehouses / "onboarded").exists(), "reset removes every version of the unit"
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    assert _v(prof, 1).is_file()
    with pytest.raises(MissingWarehouse, match="rows made from other inputs than the artifact was checked on"):
        _served(prof, h1)


def test_certify_catalogues_the_version_the_live_draft_names_as_the_units_source(prof):
    """make onboard's certify step freezes the catalogue the candidate compiles against: the landed version's, whose
    columns may differ from the released one's, under the unit's own source id."""
    from kge.estate import certify
    _live_bancassurance(prof)
    _artifact(prof, "1" * 64, released=True)
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    pack = prof.clients / prof.client
    assert warehouse_versions.live_versions(pack) == {"bancassurance": 1}, \
        "a pending update names no warehouse: the live draft is still v1's"
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-18")
    onboard.build(prof)
    live = warehouse_versions.live_versions(pack)
    assert live == {"bancassurance": 2}
    estate = estate_of(prof)
    cat = certify.catalogue_of(estate, "bancassurance", live["bancassurance"])
    assert cat["source"] == "bancassurance"
    assert "commission_amount" in [c["name"] for t in cat["tables"] if t["name"] == "premium" for c in t["columns"]]
    assert "commission_amount" not in [c["name"] for t in certify.catalogue_of(estate, "bancassurance", 1)["tables"]
                                       if t["name"] == "premium" for c in t["columns"]], "v1's rows are kept as they were"
    named = packs.read(pack.parent, prof.client, identities=prof.identities).warehouses
    assert named["bancassurance"]["version"] == 2
    assert named["bancassurance"]["inputs"] == warehouse_versions.stamped(prof.warehouses, "bancassurance", 2)


@pytest.mark.parametrize("step", ["stage", "draft", "land"])
def test_a_lost_race_on_an_update_leaves_nothing_changed(step, monkeypatch, prof):
    _live_leasing(prof)
    if step != "stage":
        onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV),
                       ddl_text=LEASING_DDL, as_="knowledge_engineer")
    if step == "land":
        onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
        onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
        onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
    before = _unit_files(prof)
    real = files_state.FilesState.commit

    def lost(self, unit, manifest, additions):
        raise appstate.StateConflict(f"unit {unit} changed by someone else; reload")
    monkeypatch.setattr(files_state.FilesState, "commit", lost)
    call = {"stage": lambda: onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV),
                                            ddl_text=LEASING_DDL, as_="knowledge_engineer"),
            "draft": lambda: onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18"),
            "land": lambda: onboard.build(prof)}[step]
    with pytest.raises(onboard.OnboardError, match="changed by someone else; reload"):
        call()
    assert _unit_files(prof) == before
    monkeypatch.setattr(files_state.FilesState, "commit", real)


def test_a_landing_whose_files_cannot_be_written_puts_the_live_files_back(prof, monkeypatch):
    _live_leasing(prof)
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
    before = _unit_files(prof)
    real = packstore.Store.write

    def failing(self, name, data):
        if name.endswith("units/leasing/draft.yaml") or name == "units/leasing/draft.yaml":
            raise OSError("disk full")
        return real(self, name, data)
    monkeypatch.setattr(packstore.Store, "write", failing)
    monkeypatch.setattr(onboard.common.time, "sleep", lambda _: None)
    with pytest.raises(onboard.OnboardError) as failed:
        onboard.build(prof)
    assert failed.value.status == 503
    monkeypatch.setattr(packstore.Store, "write", real)
    after = _unit_files(prof)
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    assert changed <= {"0:leasing/history.jsonl", "0:leasing/unit.yaml"}, changed
    unit = onboard.show(prof)["units"][0]
    assert unit["version"] == 1 and unit["update"]["version"] == 2, "the update is still pending, to land again"
    assert onboard.build(prof)["updates"][0].startswith("leasing v2 landed")


class MemoryBigQuery:
    """The BigQuery app-state backend's contract, in process: one manifest row per unit with a revision that each
    commit must match (compare-and-swap), history and requests written with it."""

    tables = True

    def __init__(self, store):
        self.store, self.rows, self.history_rows, self.requests = store, {}, {}, {}

    @contextlib.contextmanager
    def lock(self):
        with self.store.lock():
            yield

    def units(self):
        return sorted(self.rows)

    def manifest(self, unit):
        if unit not in self.rows:
            raise FileNotFoundError(unit)
        revision, manifest = self.rows[unit]
        return {**json.loads(json.dumps(manifest)), "_app_revision": revision}

    def history(self, unit):
        return list(self.history_rows.get(unit, []))

    def snapshot(self, unit):
        return self.manifest(unit), self.history(unit)

    def commit(self, unit, manifest, additions):
        manifest = dict(manifest)
        expected = int(manifest.pop("_app_revision", 0))
        if self.rows.get(unit, (0, None))[0] != expected:
            raise appstate.StateConflict(f"unit {unit} changed by someone else; reload")
        self.rows[unit] = (expected + 1, json.loads(json.dumps(manifest)))
        self.history_rows.setdefault(unit, []).extend(json.loads(json.dumps(additions)))
        self.requests[unit] = list(manifest.get("requests", []))
        return {**manifest, "_app_revision": expected + 1}

    def delete(self, unit):
        for table in (self.rows, self.history_rows, self.requests):
            table.pop(unit, None)


@pytest.mark.parametrize("remote", [False, True], ids=["local-store", "remote-store"])
def test_an_update_on_the_bigquery_state_backend_and_a_remote_store(remote, monkeypatch, tmp_path, prof):
    """When another writer moves the unit on between the update's read and its commit, the update's draft is refused
    and nothing it would write appears."""
    if remote:
        remote_root = tmp_path / "object-store"
        monkeypatch.setenv("STORE_URL", f"file://{remote_root}")
        open_store(str(remote_root)).put("clients/meridian/pack.yaml", (DEMO / "clients/meridian/pack.yaml").read_bytes())
    held = {}
    monkeypatch.setattr(appstate, "for_profile", lambda profile, store: held.setdefault("state", MemoryBigQuery(store)))
    _live_leasing(prof)
    state = held["state"]
    assert state.rows["leasing"][1]["release"]["version"] == 1
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=_june(LEASING_CSV), ddl_text=LEASING_DDL,
                   as_="knowledge_engineer")
    assert state.rows["leasing"][1]["update"]["version"] == 2 and not (prof.clients / prof.client / "units/leasing/unit.yaml").exists()
    real = state.commit
    def interloper(unit, manifest, additions):
        revision, row = state.rows[unit]
        state.rows[unit] = (revision + 1, row)
        state.commit = real
        return real(unit, manifest, additions)
    state.commit = interloper
    with pytest.raises(onboard.OnboardError, match="changed by someone else"):
        onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    assert not packstore.for_profile(prof).exists("units/leasing/update/draft.yaml")
    drafted = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    assert drafted["changes"]["changed"] == ["LEASING-D01"]
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
    assert onboard.build(prof)["updates"][0].startswith("leasing v2 landed")
    assert state.rows["leasing"][1]["version"] == 2 and "update" not in state.rows["leasing"][1]
    assert [h["step"] for h in state.history_rows["leasing"]][-1] == "update"
    if remote:
        assert open_store(str(tmp_path / "object-store")).get("clients/meridian/units/leasing/data/rental.csv").endswith(JUNE_ROW)


def test_an_update_lists_documents_and_aliases_and_landing_leaves_no_stale_document(prof, tmp_path):
    guide = (DEMO / "examples/bancassurance/docs/premium-guide.md").read_text()
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    shutil.copytree(DEMO / "examples/bancassurance-osi", v1)
    (v1 / "docs").mkdir()
    (v1 / "docs/premium-guide.md").write_text(guide)
    (v1 / "docs/second-guide.md").write_text(guide.replace("Bancassurance premium guide", "Bancassurance second guide"))
    shutil.copytree(v1, v2)
    (v2 / "docs/second-guide.md").unlink()
    (v2 / "docs/premium-guide.md").write_text(guide + "\nRevised for the third quarter.\n")
    model = (v2 / "model.yaml").read_text()
    (v2 / "model.yaml").write_text(model.replace("synonyms: [gross written premium]", "synonyms: [gross written premium, gwp]"))
    onboard.create(prof, "bancassurance", semantic_dir=v1, label="Bancassurance", as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-17")
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-17")
    onboard.build(prof)
    assert {d["doc_id"] for d in json.loads((prof.estate() / "documents.json").read_text())} >= {
        "DOC-BANCASSURANCE-1", "DOC-BANCASSURANCE-2"}
    _release(prof, "bancassurance")
    onboard.create(prof, "bancassurance", semantic_dir=v2, label="Bancassurance", as_="knowledge_engineer")
    changes = onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")["changes"]
    assert changes["changed"] == ["source", "written_premium"] and not changes["added"] and not changes["removed"]
    assert changes["details"]["source"] == ["document second-guide.md removed", "document premium-guide.md changed"]
    assert changes["details"]["written_premium"] == ["alias 'gwp' added"]
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-18")
    assert onboard.build(prof)["updates"][0].startswith("bancassurance v2 landed (2 changed")
    docs = [d["doc_id"] for d in json.loads((prof.estate() / "documents.json").read_text())
            if d["doc_id"].startswith("DOC-BANCASSURANCE-")]
    assert docs == ["DOC-BANCASSURANCE-1"], "the dropped document is gone from the index"
    shelved = sorted(p.name for p in (prof.estate() / "docs").rglob("DOC-BANCASSURANCE-*.md"))
    assert shelved == ["DOC-BANCASSURANCE-1.md"]
    assert "Revised for the third quarter." in next((prof.estate() / "docs").rglob("DOC-BANCASSURANCE-1.md")).read_text()


def test_make_draft_with_a_mistyped_tier_provider_is_one_line_and_exit_2(monkeypatch, prof, capsys):
    """make draft (and make source, which drafts) resolve the onboard tier's model: an unknown MODEL_HIGH_PROVIDER is one
    line on stderr, exit 2, as a refused semantic folder is, never a traceback; with --json it is the error's object."""
    monkeypatch.setattr(onboard.cli, "resolve", lambda _p=None: prof)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    folder = str(DEMO / "examples/bancassurance-markdown")
    assert onboard.main(["create", "--unit", "bancassurance", "--semantic", folder, "--as", "knowledge_engineer"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "antropic")
    said = ("MODEL_HIGH_PROVIDER must be one of gemini, anthropic, openai, ollama, vertex-claude, vertex-gemini, mock or "
            "none, not 'antropic'")
    assert onboard.main(["draft", "--unit", "bancassurance", "--as", "knowledge_engineer"]) == 2
    assert capsys.readouterr().err == f"onboard: {said}\n"
    assert onboard.main(["draft", "--unit", "bancassurance", "--as", "knowledge_engineer", "--json"]) == 2
    assert json.loads(capsys.readouterr().out) == {"ok": False, "status": 400, "error": said}
