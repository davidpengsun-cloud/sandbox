"""pack.lock: a client's landed source catalogue, frozen beside its pack, and the check make graph runs first.

    .venv/bin/python -m pytest tests/integration/test_pack_lock.py -q

kge/compiler/lock.py reads the sources the pack harvests (clients/<client>/pack.yaml),
its curated sources among them (since pack 1.1.0, the client cross-reference),
and the catalogue certification landed for each (structured/<source>.json in
the current release), with row counts and storage from the estate adapter,
and writes clients/<client>/pack.lock: canonical JSON, per source its tables,
their columns {name, type, description, ordinal}, primary key and foreign keys
with every column, the landed as-of date, and no timestamp or hash. --check writes nothing and exits 2 with "pack.lock is stale: run make
lock", naming each difference, when the landed catalogue has moved; make graph
runs it before it loads anything.

Reads the demo's landing and copies of its warehouses; lands scratch releases
in temporary folders. Opens no graph.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

from kge.compiler import lock
from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.estate import certify
from kge.landing.zone import Zone
from tests.scratch import BUILD, profile_of, registry, write_registry

DEMO = BUILD.parent
CLIENT = "meridian"
CLIENTS = DEMO / "clients"
LOCKED = CLIENTS / CLIENT / "pack.lock"
LANDING = BUILD / "landing"
STALE = "pack.lock is stale: run make lock"
ABOUT = {"policy": "test-policy", "version": "1.0", "as_of": "2025-07-01", "counts": {}}


def run(*args, cwd: Path = DEMO) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "kge.compiler.lock", *map(str, args)], capture_output=True, text=True, cwd=cwd)


def lock_args(client: str = CLIENT, clients: Path = CLIENTS, landing: Path = LANDING) -> list:
    return ["--client", client, "--clients", clients, "--landing", landing]


def clients_copy(dest: Path, client: str = CLIENT) -> Path:
    """A scratch clients folder holding a copy of client's pack and lock: what make lock may write to."""
    shutil.copytree(CLIENTS / client, dest / client, ignore=shutil.ignore_patterns("baselines"))
    return dest


def estate_copy(dest: Path, change=None) -> Path:
    """A build that certification lands from: the demo's documents and estate summary linked (so make finds the estate
    built), and its warehouses linked, or copied and changed by the statements change names for them. make certify,
    run on it, lands in its own landing."""
    dest.mkdir(parents=True)
    for name in ("documents.json", "docs", "summary.json"):
        (dest / name).symlink_to(BUILD / name)
    (dest / "warehouses").mkdir()
    for w in sorted((BUILD / "warehouses").glob("*.duckdb")):
        if change and change.get(w.stem):
            shutil.copyfile(w, dest / "warehouses" / w.name)
            con = duckdb.connect(str(dest / "warehouses" / w.name))
            try:
                for stmt in change[w.stem]:
                    con.execute(stmt)
            finally:
                con.close()
        else:
            (dest / "warehouses" / w.name).symlink_to(w)
    r = subprocess.run([sys.executable, "-m", "kge.estate.certify", "--build", str(dest), "--client", CLIENT],
                       capture_output=True, text=True, cwd=DEMO)
    assert r.returncode == 0, r.stderr
    return dest


@pytest.fixture(scope="module")
def release():
    return Zone(LANDING, CLIENT).open()


@pytest.fixture(scope="module")
def harvested() -> list[str]:
    pack = yaml.safe_load((CLIENTS / CLIENT / "pack.yaml").read_text())
    return [s["id"] for s in pack["sources"] + pack.get("curated_sources", []) if s.get("harvest")]


def test_the_lock_takes_the_curated_sources_after_the_units(harvested):
    """The client cross-reference is a curated source, no unit: the lock holds its catalogue beside the units'."""
    assert harvested == ["retail", "sme", "corporate", "wealth", "xref"]
    assert lock.harvested(CLIENTS, CLIENT) == harvested
    xref = lock.read(LOCKED)["sources"]["xref"]["tables"]
    assert [t["name"] for t in xref] == ["client_xref"] and xref[0]["foreign_keys"] == []
    assert xref[0]["primary_key"] == ["xref_id", "valid_from"] and len(xref[0]["columns"]) == 13


def test_the_committed_lock_is_what_make_lock_writes_from_the_landed_catalogue(release, harvested):
    frozen = lock.freeze(release, harvested, DuckDBEstate(BUILD / "warehouses"))
    assert lock.render(frozen) == LOCKED.read_text(), "make lock on this estate would change the committed pack.lock"
    assert frozen["client"] == CLIENT and sorted(frozen["sources"]) == sorted(harvested)


def test_the_lock_round_trips_and_holds_the_landed_catalogue_whole(release, harvested):
    text = LOCKED.read_text()
    held = json.loads(text)
    assert lock.render(held) == text, "canonical: sorted keys, a trailing newline, and nothing render would change"
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert lock.read(LOCKED) == held
    for s in harvested:
        landed = release.structured(s)["tables"]
        tables = held["sources"][s]["tables"]
        assert [t["name"] for t in tables] == sorted(t["name"] for t in landed)
        assert len(tables) == len(landed)
        for t, l in zip(tables, sorted(landed, key=lambda t: t["name"])):
            assert set(t) == {"name", "columns", "primary_key", "foreign_keys", "rows", "storage"}
            session = DuckDBEstate(BUILD / "warehouses").open(s)
            try:
                assert t["rows"] == session.describe(t["name"])["rows"]
                assert t["storage"] == session.storage(t["name"])
            finally:
                session.close()
            assert t["columns"] == [{"name": c["name"], "type": c["type"], "description": c["comment"], "ordinal": c["ordinal"]}
                                    for c in sorted(l["columns"], key=lambda c: c["ordinal"])]
            assert t["primary_key"] == l["primary_key"] and t["foreign_keys"] == l["foreign_keys"], (s, t["name"])
    counts = {k: sum(len(t[k]) for s in held["sources"].values() for t in s["tables"]) for k in ("columns", "foreign_keys")}
    assert sum(len(s["tables"]) for s in held["sources"].values()) == 60 and counts == {"columns": 912, "foreign_keys": 44}


def test_the_lock_holds_no_timestamp_no_hash_and_no_release_id(release):
    text = LOCKED.read_text()
    assert not re.search(r"\b[0-9a-f]{12,}\b", text), "no hex digest: the disclosure guard flags them"
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text) and release.id not in text
    assert set(json.loads(text)) == {"client", "format", "sources", "as_of"}
    assert json.loads(text)["as_of"] == release.manifest["as_of"]


def test_make_lock_writes_the_same_bytes_and_leaves_an_unchanged_lock_alone(tmp_path):
    clients = clients_copy(tmp_path / "clients")
    path = clients / CLIENT / "pack.lock"
    names = sorted(p.name for p in path.parent.iterdir())
    before = os.stat(path).st_mtime_ns, os.stat(path).st_ino
    r = run(*lock_args(clients=clients))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["written"] is False and (out["sources"], out["tables"], out["columns"], out["foreign_keys"]) == (5, 60, 912, 44)
    assert (os.stat(path).st_mtime_ns, os.stat(path).st_ino) == before and path.read_bytes() == LOCKED.read_bytes()
    path.unlink()
    r = run(*lock_args(clients=clients))
    assert r.returncode == 0 and json.loads(r.stdout)["written"] is True, r.stderr
    assert path.read_bytes() == LOCKED.read_bytes(), "a fresh lock is byte for byte the committed one"
    assert sorted(p.name for p in path.parent.iterdir()) == names, "no temporary file is left behind"


def test_check_passes_on_the_demo_estate_and_on_a_fresh_landing_of_it(tmp_path):
    r = run(*lock_args(), "--check")
    assert r.returncode == 0 and STALE not in r.stdout + r.stderr, r.stderr
    build = estate_copy(tmp_path / "build")
    before = LOCKED.read_bytes()
    r = run(*lock_args(landing=build / "landing"), "--check")
    assert r.returncode == 0, r.stderr
    assert LOCKED.read_bytes() == before, "--check writes nothing"


def test_check_fails_after_a_schema_change_naming_it_and_make_lock_takes_it_in(tmp_path):
    change = {"retail": ["ALTER TABLE branch ADD COLUMN manager_code VARCHAR",
                         "COMMENT ON COLUMN branch.manager_code IS 'Branch manager'"]}
    build = estate_copy(tmp_path / "build", change)
    before = LOCKED.read_bytes()
    r = run(*lock_args(landing=build / "landing"), "--check")
    assert r.returncode == 2 and STALE in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert "retail.branch.manager_code: new column" in r.stderr, r.stderr
    assert LOCKED.read_bytes() == before, "a stale check writes nothing"
    clients = clients_copy(tmp_path / "clients")
    r = run(*lock_args(clients=clients, landing=build / "landing"))
    assert r.returncode == 0 and json.loads(r.stdout)["written"] is True, r.stderr
    branch = next(t for t in lock.read(clients / CLIENT / "pack.lock")["sources"]["retail"]["tables"] if t["name"] == "branch")
    assert branch["columns"][-1] == {"name": "manager_code", "type": "VARCHAR", "description": "Branch manager", "ordinal": 5}
    assert run(*lock_args(clients=clients, landing=build / "landing"), "--check").returncode == 0
    assert LOCKED.read_bytes() == before


def test_check_names_each_kind_of_change(release, harvested):
    locked = lock.freeze(release, harvested)
    now = json.loads(json.dumps(locked))
    retail = {t["name"]: t for t in now["sources"]["retail"]["tables"]}
    retail["account"]["columns"][3]["type"] = "INTEGER"
    retail["account"]["columns"][3]["description"] = "Status"
    retail["account"]["columns"] = [c for c in retail["account"]["columns"] if c["name"] != "closed_on"]
    retail["account"]["primary_key"] = ["account_id", "customer_id"]
    retail["account"]["foreign_keys"] = [k for k in retail["account"]["foreign_keys"] if k["ref_table"] != "product"]
    retail["branch"]["foreign_keys"] = [{"columns": ["region_code", "opened_on"], "ref_table": "region", "ref_columns": ["code", "since"]}]
    now["sources"]["retail"]["tables"] = [t for t in now["sources"]["retail"]["tables"] if t["name"] != "complaint"]
    now["sources"]["retail"]["tables"].append({"name": "ledger", "columns": [], "primary_key": [], "foreign_keys": []})
    del now["sources"]["sme"]
    now["sources"]["ledgers"] = {"tables": []}
    lines = lock.drift(locked, now)
    for expected in ("retail.account.status_code: type was 'VARCHAR', now 'INTEGER'",
                     "retail.account.status_code: description was 'OPEN, DORMANT or CLOSED', now 'Status'",
                     "retail.account.closed_on: column gone",
                     "retail.account: primary key was (account_id), now (account_id, customer_id)",
                     "retail.account: foreign key (product_id) -> product (product_id) gone",
                     "retail.branch: new foreign key (region_code, opened_on) -> region (code, since)",
                     "retail.complaint: table gone", "retail.ledger: new table",
                     "sme: no longer a source the pack harvests", "ledgers: new source"):
        assert expected in lines, (expected, lines)
    assert lock.drift(locked, locked) == []


def test_a_missing_or_unreadable_lock_is_stale(tmp_path):
    clients = clients_copy(tmp_path / "clients")
    path = clients / CLIENT / "pack.lock"
    path.unlink()
    r = run(*lock_args(clients=clients), "--check")
    assert r.returncode == 2 and STALE in r.stderr and "no " in r.stderr and not path.exists(), r.stderr
    path.write_text("{not json")
    r = run(*lock_args(clients=clients), "--check")
    assert r.returncode == 2 and STALE in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert path.read_text() == "{not json"


def test_a_composite_key_keeps_every_column_from_warehouse_to_lock(tmp_path):
    """Keys come from the landing; row counts and storage come through the warehouse adapter."""
    (tmp_path / "warehouses").mkdir()
    warehouse = tmp_path / "warehouses" / "ledger.duckdb"
    con = duckdb.connect(str(warehouse))
    try:
        con.execute("CREATE TABLE book (book_code VARCHAR, entity_no INTEGER, title VARCHAR, PRIMARY KEY (book_code, entity_no))")
        con.execute("CREATE TABLE entry (entry_id INTEGER PRIMARY KEY, book_code VARCHAR, entity_no INTEGER, amount DECIMAL(14,2), "
                    "FOREIGN KEY (book_code, entity_no) REFERENCES book (book_code, entity_no))")
        con.execute("COMMENT ON COLUMN entry.amount IS 'Posted amount'")
    finally:
        con.close()
    zone = Zone(tmp_path / "landing", "fixture")
    zone.land_files({"structured/ledger.json": certify.canonical(certify.catalogue_of(DuckDBEstate(warehouse.parent), warehouse.stem)).encode()}, ABOUT)
    clients = tmp_path / "clients"
    (clients / "fixture").mkdir(parents=True)
    (clients / "fixture" / "pack.yaml").write_text(yaml.safe_dump(
        {"manifest": {"client": "fixture"}, "sources": [{"id": "ledger", "harvest": True}, {"id": "manual", "harvest": False}]}))
    r = run(*lock_args("fixture", clients, tmp_path / "landing"))
    assert r.returncode == 0, r.stderr
    held = lock.read(clients / "fixture" / "pack.lock")
    assert list(held["sources"]) == ["ledger"], "a source the pack does not harvest is not locked"
    book, entry = held["sources"]["ledger"]["tables"]
    assert book["primary_key"] == ["book_code", "entity_no"]
    assert entry["foreign_keys"] == [{"columns": ["book_code", "entity_no"], "ref_table": "book", "ref_columns": ["book_code", "entity_no"]}]
    assert entry["columns"][3] == {"name": "amount", "type": "DECIMAL(14,2)", "description": "Posted amount", "ordinal": 4}
    assert book["columns"][0]["description"] is None, "a column with no comment is locked as it landed"
    assert run(*lock_args("fixture", clients, tmp_path / "landing"), "--check").returncode == 0


def test_with_nothing_landed_it_stops_naming_make_certify(tmp_path):
    (tmp_path / "landing").mkdir()
    for check in ([], ["--check"]):
        r = run(*lock_args(landing=tmp_path / "landing"), *check)
        assert r.returncode == 2 and "make certify" in r.stderr and "Traceback" not in r.stderr, r.stderr


def test_it_refuses_a_pack_that_is_not_the_clients_and_a_harvested_source_that_did_not_land(tmp_path):
    clients = clients_copy(tmp_path / "clients")
    pack = clients / CLIENT / "pack.yaml"
    text = pack.read_text()
    pack.write_text(text.replace(f"client: {CLIENT}\n", "client: someone-else\n", 1))
    r = run(*lock_args(clients=clients))
    assert r.returncode == 2 and "someone-else" in r.stderr and "Traceback" not in r.stderr, r.stderr
    p = yaml.safe_load(text)
    p["sources"].append({"id": "treasury", "harvest": True})
    pack.write_text(yaml.safe_dump(p, sort_keys=False))
    before = (clients / CLIENT / "pack.lock").read_bytes()
    for check in ([], ["--check"]):
        r = run(*lock_args(clients=clients), *check)
        assert r.returncode == 2 and "treasury" in r.stderr and "make certify" in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert (clients / CLIENT / "pack.lock").read_bytes() == before
    r = run(*lock_args("../" + CLIENT, clients))
    assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr


def test_the_lock_reads_warehouses_only_through_the_adapter_and_names_no_client():
    src = (DEMO / "kge" / "compiler" / "lock.py").read_text()
    assert not re.search(r"^\s*(?:import|from)\s+(?:kge\.)?(?:duckdb|estate)\b", src, re.M)
    assert "from kge.adapters.warehouse import open_estate" in src
    assert not re.search(r"(?:import|from)\s+kge\.adapters\.warehouse\.(?:duckdb|bigquery)\b", src)
    for f in (DEMO / "kge" / "compiler").glob("*.py"):
        for noun in ("meridian", "retail", "corporate", "wealth", "sme", "household", "revenue"):
            assert not re.search(rf"\b{noun}\b", f.read_text().lower()), (f.name, noun)


def recipe(make: str, target: str) -> str:
    """The target's recipe as make runs it: each canned recipe it expands (define NAME ... endef, as $(NAME)) put in
    its place, so the steps a target shares with others (LOAD_STEPS) are read where they run."""
    body = make.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    for name, text in re.findall(r"^define (\w+)\n(.*?)\nendef$", make, re.M | re.S):
        body = body.replace(f"$({name})", text)
    return body


def test_make_lock_writes_it_and_every_graph_load_checks_it_first():
    make = (DEMO / "Makefile").read_text()
    assert re.search(r"^PACK := \$\(call profile,pack\)$", make, re.M), "the client's own folder, the profile's"
    assert re.search(r"^\.PHONY:.*\block\b", make, re.M)
    from tests.unit.test_cli import planned
    for target in ("lock", "graph", "$(GRAPH)"):
        assert recipe(make, target).split() == ["$(KGE)", target.strip("$()").lower(), "$(KGE_ARGS)"], target
    cmd = " -m kge.compiler.lock"
    summary, landed = str(DEMO / "Makefile"), str(DEMO / "build" / "no-such-landed-release")
    lock_recipe = planned("lock", f"LANDED={DEMO}").splitlines()
    assert len(lock_recipe) == 1 and lock_recipe[0].endswith(cmd), lock_recipe
    first = planned("lock", f"LANDED={landed}", f"SUMMARY={summary}").splitlines()
    assert first[0].endswith(" -m kge.estate.certify") and first[-1].endswith(cmd), "the landed release first"
    for name, args in (("graph", ("graph",)), ("$(GRAPH)", ("pipeline", f"GRAPH={landed}"))):
        body = planned(*args)
        assert f"{cmd} --check" in body, name
        assert body.index("--check") < body.index("kge.graph.load"), f"{name}: the lock is checked before anything loads"


def made(build: Path, *args) -> subprocess.CompletedProcess:
    """make, for build's profile (its own registry: tests/scratch.py), on the store this run is on."""
    env = {k: v for k, v in os.environ.items() if k != "PROFILE"} | {"PROFILES": str(registry(build))}
    return subprocess.run(["make", *args, f"PY={sys.executable}"], capture_output=True, text=True,
                          cwd=DEMO, env=env, timeout=120)


def test_make_graph_stops_on_a_stale_lock_and_loads_nothing(tmp_path):
    build = estate_copy(tmp_path / "build", {"wealth": ["ALTER TABLE HSHLD_DIM_1 ADD COLUMN SEGMT_CD VARCHAR"]})
    write_registry(build)
    r = made(build, "-s", "graph")
    assert r.returncode != 0 and STALE in r.stderr and "wealth.HSHLD_DIM_1.SEGMT_CD: new column" in r.stderr, r.stdout + r.stderr
    assert profile_of(build).model_hash() is None and not (build / "stores").exists(), "nothing was loaded, nor released"


def test_a_schema_change_reaches_the_check_only_once_certification_lands_it(tmp_path):
    """make graph reads the release already landed and certifies nothing: a warehouse's schema change alone leaves the
    lock current, and the check stops the load once make certify has landed the change."""
    build = estate_copy(tmp_path / "build")
    wealth = build / "warehouses" / "wealth.duckdb"
    wealth.unlink()
    shutil.copyfile(BUILD / "warehouses" / "wealth.duckdb", wealth)
    con = duckdb.connect(str(wealth))
    try:
        con.execute("ALTER TABLE HSHLD_DIM_1 ADD COLUMN SEGMT_CD VARCHAR")
    finally:
        con.close()
    r = run(*lock_args(landing=build / "landing"), "--check")
    assert r.returncode == 0 and STALE not in r.stderr, "the landed release has not moved, so the lock is current"
    write_registry(build)
    from tests.unit.test_cli import planned
    dry = planned("graph", PROFILES=str(registry(build)))
    assert "kge.compiler.lock" in dry and "kge.estate.certify" not in dry, dry
    certified = made(build, "-s", "certify")
    assert certified.returncode == 0, certified.stderr
    r = run(*lock_args(landing=build / "landing"), "--check")
    assert r.returncode == 2 and STALE in r.stderr and "wealth.HSHLD_DIM_1.SEGMT_CD: new column" in r.stderr, r.stderr


def lock_paragraph(readme: str) -> str:
    return " ".join(readme.split("**The pack's lock.**", 1)[1].split("\n\n", 1)[0].split())


def test_the_readme_says_the_check_reads_the_landed_release_and_shows_it_in_that_order():
    """The check holds the lock to the landed catalogue, not to a warehouse: a schema change stops a load only once
    certification has landed it, by make certify or make estate. The README says so, and shows it in that order."""
    para = lock_paragraph((DEMO / "README.md").read_text())
    assert "warehouse's schema has moved since the lock was taken" not in para, "a warehouse's change alone stops no load"
    assert "landed catalogue has moved since the lock was taken" in para and f'"{STALE}"' in para
    assert "`make certify`" in para and "`make estate`" in para
    show = para.split("To see it", 1)[1]
    assert show.index("ALTER TABLE HSHLD_DIM_1 ADD COLUMN SEGMT_CD") < show.index("`make certify`") < show.index("`make graph`")
    assert "wealth.HSHLD_DIM_1.SEGMT_CD" in show


def test_the_readme_names_each_generated_file_that_is_committed_and_what_it_holds():
    """No warehouse and no document generated for the estate is committed, only records that hold no warehouse row: the
    two A/B baselines, the parity and estate snapshots, the pack's lock and the control's frozen inputs, which quote the
    opening of the landed chunks the control retrieved. The estate section's first paragraph names each one
    (tests/unit/test_baselines.py holds it to every such file git tracks)."""
    readme = (DEMO / "README.md").read_text()
    first = " ".join(readme.split("## The estate", 1)[1].split("\n\n", 2)[1].split())
    committed = [*(p.relative_to(DEMO).as_posix() for p in sorted((CLIENTS / CLIENT).glob("baselines/*.json"))),
                 LOCKED.relative_to(DEMO).as_posix(), "tests/fixtures/control_6c897ba.json"]
    assert len(committed) >= 3
    for path in committed:
        assert f"`{path}`" in first, path
    assert "but one record" not in first
    for holds in ("pass rates", "figures and ids only", "tables, columns and keys", "never a warehouse row"):
        assert holds in first, holds


def test_the_readme_says_what_the_lock_holds_and_how_to_refresh_it():
    readme = (DEMO / "README.md").read_text()
    for needle in ("clients/meridian/pack.lock", "kge/compiler/lock.py", "make lock", STALE, "tests/integration/test_pack_lock.py"):
        assert needle in readme, needle
    running = readme.split("## Running it", 1)[1].split("\n## ", 1)[0]
    assert "make lock" in running


def test_add_new_takes_an_onboarded_units_changed_tables_and_still_refuses_any_other_drift(tmp_path):
    """An update to an onboarded unit changes its tables once its changed source item is certified and lands
    (kge/estate/onboard/); --add-new, which make onboard runs, takes that drift in, and any other source's stays
    refused."""
    change = {"retail": ["ALTER TABLE branch ADD COLUMN manager_code VARCHAR"]}
    build = estate_copy(tmp_path / "build", change)
    clients = clients_copy(tmp_path / "clients")
    before = (clients / CLIENT / "pack.lock").read_bytes()
    r = run(*lock_args(clients=clients, landing=build / "landing"), "--add-new")
    assert r.returncode == 2 and "retail.branch.manager_code: new column" in r.stderr, r.stderr
    assert (clients / CLIENT / "pack.lock").read_bytes() == before, "a refused drift writes nothing"
    marker = clients / CLIENT / "units" / "retail" / "schema.sql"
    marker.parent.mkdir(parents=True)
    marker.write_text("CREATE SCHEMA retail;\n")
    r = run(*lock_args(clients=clients, landing=build / "landing"), "--add-new")
    assert r.returncode == 0 and json.loads(r.stdout)["written"] is True, r.stderr
    assert run(*lock_args(clients=clients, landing=build / "landing"), "--check").returncode == 0
