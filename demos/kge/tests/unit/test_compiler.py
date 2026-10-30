"""The compiler: a client's pack and its lock, checked against the platform's meta-model and compiled into an artifact.

    .venv/bin/python -m pytest tests/unit/test_compiler.py -q

kge/compiler/compile.py reads meta-model.yaml, the client's pack (clients/<client>/pack.yaml and the files its manifest
includes) and its pack.lock, and nothing else. It checks the pack first: on any finding it lists each one as
"<rule> <path>: <message>", exits 2 and writes nothing. Otherwise it writes <out>/<client>/<model_hash>/ and prints
that path: graph-model.yaml, ddl/kuzu.cypher, data/nodes/<Type>.csv, data/edges/<EDGE>__<From>__<To>.csv,
access/roles.json, gates.json (the pack's post-load gates, where it declares them) and manifest.json. model_hash is the
sha256 over the sorted (path, sha256) pairs of every file but the manifest, so the same pack, lock and compiler give the
same bytes under the same hash, whatever the working folder or the hash seed, and a folder already under that hash must
hold those bytes, or the compile stops.

Compiles into temporary folders, from the committed pack or scratch copies of it. Two tests load an artifact into a
throwaway graph on the store this run is on: run the suite once per store. Opens no demo graph and reads no build.

CLIENT is the client the demo's build is for, the Makefile's CLIENT. NODES has grown with the Wealth expansion and the
packs and meta-models since (#39 items 7b to 11, issue #40's pack 1.3.0, and meta-model 1.3.0's 23 preloaded reference
concepts in standards/preload.yaml); git history records each step.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kge.compiler import compile as compiler

DEMO = Path(__file__).resolve().parents[2]
CLIENT = "meridian"
CLIENTS = DEMO / "clients"
META_MODEL = DEMO / "meta-model.yaml"
FINDING = re.compile(r"^(S\d\d) (\S+): (.+)$")
HASH = re.compile(r"[0-9a-f]{64}")
NULL = "\\N"
NOT_CHECKED = ("S12", "S13", "S15", "S16-S21")
NODES = {"Unit": 4, "Tbl": 60, "Col": 912, "Term": 49, "Measure": 12, "Calendar": 2, "Alias": 146, "Policy": 8, "CuratedSource": 1,
         "Context": 3, "Filter": 3, "RowSelector": 3, "JoinRule": 15, "Acronym": 26, "Entity": 35, "Relation": 1, "OntologyConcept": 23,
         "Golden": 17, "Vocabulary": 30}
EDGES = {"HAS_TABLE": 59, "HAS_COLUMN": 912, "JOINS": 44, "JOINS_TABLE": 44, "MAPS_TO": 24, "NO_JOIN": 5, "DEFINES": 12,
         "MEASURE_OF": 12, "COMPUTED_FROM": 13, "IN_CALENDAR": 12, "PERIOD_COLUMN": 33, "ALIAS_OF": 142, "GOVERNS": 47,
         "HAS_CURATED_TABLE": 1, "RESOLVES_TO": 3, "SPECIALIZES": 3, "USES_FACT": 3, "CERTIFIED_BY": 17, "REQUIRES_FILTER": 6,
         "SELECTS_ROWS_WITH": 6, "TARGETS": 6, "CONFLICTS_WITH": 2, "REQUIRES_JOIN": 30, "JOIN_FROM": 15, "JOIN_TO": 15, "IMPLEMENTS": 1,
         "ABBREVIATED_AS": 5, "SPELLED_WITH": 34, "REPRESENTS": 35, "IDENTIFIED_BY": 41, "REL_FROM": 1, "REL_TO": 1, "MAPPED_TO": 13,
         "DERIVED_FROM": 1, "NOT_JOINABLE_TO": 1, "CERTIFIED_JOIN": 1}
EDGE_FILES = ["HAS_TABLE__Unit__Tbl", "HAS_COLUMN__Tbl__Col", "JOINS__Col__Col", "JOINS_TABLE__Tbl__Tbl", "MAPS_TO__Col__Term",
              "NO_JOIN__Unit__Unit", "DEFINES__Document__Term", "MEASURE_OF__Measure__Term", "COMPUTED_FROM__Measure__Col",
              "IN_CALENDAR__Measure__Calendar", "PERIOD_COLUMN__Tbl__Col", "ALIAS_OF__Alias__Term", "ALIAS_OF__Alias__Unit",
              "ALIAS_OF__Alias__Col", "ALIAS_OF__Alias__Entity", "GOVERNS__Policy__Col", "HAS_CURATED_TABLE__CuratedSource__Tbl",
              "RESOLVES_TO__Term__Context", "SPECIALIZES__Context__Measure", "USES_FACT__Context__Tbl", "CERTIFIED_BY__Context__Golden",
              "REQUIRES_FILTER__Context__Filter", "SELECTS_ROWS_WITH__Context__RowSelector", "TARGETS__Filter__Tbl",
              "TARGETS__RowSelector__Tbl", "CONFLICTS_WITH__Filter__Filter", "CONFLICTS_WITH__Filter__RowSelector",
              "CONFLICTS_WITH__RowSelector__Filter", "CONFLICTS_WITH__RowSelector__RowSelector", "REQUIRES_JOIN__Context__JoinRule",
              "JOIN_FROM__JoinRule__Tbl", "JOIN_TO__JoinRule__Tbl", "IMPLEMENTS__JoinRule__Relation", "ABBREVIATED_AS__Term__Acronym",
              "SPELLED_WITH__Col__Acronym", "REPRESENTS__Tbl__Entity", "IDENTIFIED_BY__Entity__Col", "REL_FROM__Relation__Entity",
              "REL_TO__Relation__Entity", "MAPPED_TO__Entity__OntologyConcept", "MAPPED_TO__Term__OntologyConcept",
              "MAPPED_TO__Policy__OntologyConcept",
              "DERIVED_FROM__Tbl__Tbl", "NOT_JOINABLE_TO__Col__Tbl", "CERTIFIED_JOIN__Unit__Unit"]
FILES = {"graph-model.yaml", "ddl/kuzu.cypher", "access/roles.json", "gates.json", "catalogue.yaml", "manifest.json",
         *(f"data/nodes/{t}.csv" for t in NODES), *(f"data/edges/{e}.csv" for e in EDGE_FILES)}


def run(*args, cwd: Path = DEMO, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "kge.compiler.compile", *map(str, args)], capture_output=True, text=True, cwd=cwd, env=env)


def compile_args(out, clients=CLIENTS, client: str = CLIENT) -> list:
    return ["--client", client, "--clients", clients, "--out", out]


def printed(r: subprocess.CompletedProcess) -> Path:
    """The artifact folder a compile printed: its one line on stdout, <out>/<client>/<model_hash>/."""
    assert r.returncode == 0, r.stderr
    (line,) = r.stdout.splitlines()
    assert line.endswith("/") and HASH.fullmatch(Path(line).name), line
    return Path(line)


def compile_in(capsys, clients: Path, out: Path, client: str = CLIENT, meta: Path | None = None) -> tuple[int, str, str]:
    """compile.py's main, in this process: its exit code, stdout and stderr."""
    argv = compile_args(out, clients, client) + (["--meta-model", meta] if meta else [])
    code = compiler.main([str(a) for a in argv])
    got = capsys.readouterr()
    return code, got.out, got.err


def findings(err: str) -> list[tuple[str, str, str]]:
    return [m.groups() for m in map(FINDING.match, err.splitlines()) if m]


def tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def rows(folder: Path, path: str) -> list[list[str]]:
    with (folder / path).open(newline="") as f:
        return list(csv.reader(f))


def manifest_of(folder: Path) -> dict:
    return json.loads((folder / "manifest.json").read_text())


def committed() -> tuple[dict, dict]:
    return (yaml.safe_load((CLIENTS / CLIENT / "pack.yaml").read_text()),
            json.loads((CLIENTS / CLIENT / "pack.lock").read_text()))


def scratch(tmp_path: Path, edit=None) -> Path:
    """A clients folder holding a copy of the client's pack and lock with edit(pack, lock) applied: what a compile reads."""
    clients = tmp_path / "clients"
    (clients / CLIENT).mkdir(parents=True)
    pack, lock = committed()
    if edit:
        edit(pack, lock)
    (clients / CLIENT / "pack.yaml").write_text(yaml.safe_dump(pack, sort_keys=False, allow_unicode=True))
    (clients / CLIENT / "pack.lock").write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")
    shutil.copy(CLIENTS / CLIENT / "catalogue.yaml", clients / CLIENT / "catalogue.yaml")
    return clients


def table(lock: dict, source: str, name: str) -> dict:
    return next(t for t in lock["sources"][source]["tables"] if t["name"] == name)


def column(lock: dict, source: str, tbl: str, name: str) -> dict:
    return next(c for c in table(lock, source, tbl)["columns"] if c["name"] == name)


def mapping(pack: dict, col: str) -> dict:
    return next(m for m in pack["mappings"] if m["column"] == col)


@pytest.fixture(scope="module")
def artifact(tmp_path_factory) -> Path:
    """The committed Meridian pack, compiled once for the module."""
    return printed(run(*compile_args(tmp_path_factory.mktemp("artifacts"))))


def test_a_recompile_elsewhere_gives_the_same_tree_and_hash_and_the_file_hashes_are_true(tmp_path):
    """In another working folder, with another hash seed and a relative --out: the same files, byte for byte."""
    one = printed(run(*compile_args(tmp_path / "one"), env={**os.environ, "PYTHONHASHSEED": "0"}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = run(*compile_args("two"), cwd=elsewhere, env={**os.environ, "PYTHONHASHSEED": "4242"})
    assert printed(r) == Path("two") / CLIENT / one.name, "printed as given: relative to where it ran"
    two = elsewhere / printed(r)
    assert tree(one) == tree(two)
    diff = subprocess.run(["diff", "-r", str(one), str(two)], capture_output=True, text=True)
    assert diff.returncode == 0 and diff.stdout == "", diff.stdout
    man, files = manifest_of(one), tree(one)
    assert set(files) == FILES and set(man["files"]) == FILES - {"manifest.json"}
    for path, digest in man["files"].items():
        assert hashlib.sha256(files[path]).hexdigest() == digest, path
    listing = "".join(f"{digest}  {path}\n" for path, digest in sorted(man["files"].items()))
    assert man["model_hash"] == hashlib.sha256(listing.encode()).hexdigest() == one.name


def test_compiling_twice_prints_the_same_path_and_writes_nothing_the_second_time(tmp_path):
    first = printed(run(*compile_args(tmp_path)))
    stamp = {p: (p.stat().st_mtime_ns, p.stat().st_ino) for p in [first, *first.rglob("*")]}
    r = run(*compile_args(tmp_path))
    assert printed(r) == first and "unchanged" in r.stderr, r.stderr
    assert {p: (p.stat().st_mtime_ns, p.stat().st_ino) for p in [first, *first.rglob("*")]} == stamp
    assert [p.name for p in (tmp_path / CLIENT).iterdir()] == [first.name], "no temporary folder is left behind"


def test_a_folder_already_under_the_hash_must_hold_the_same_bytes(tmp_path):
    first = printed(run(*compile_args(tmp_path)))
    terms = first / "data" / "nodes" / "Term.csv"
    terms.write_bytes(terms.read_bytes() + b"x\n")
    before = tree(first)
    r = run(*compile_args(tmp_path))
    assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr, r.stderr
    assert "data/nodes/Term.csv" in r.stderr and "nothing was written" in r.stderr
    assert tree(first) == before, "the folder already there is left as it is"
    terms.write_bytes(before["data/nodes/Term.csv"][:-2])
    (first / "notes.txt").write_text("x")
    r = run(*compile_args(tmp_path))
    assert r.returncode == 2 and "notes.txt" in r.stderr, r.stderr
    assert [p.name for p in (tmp_path / CLIENT).iterdir()] == [first.name]


def test_the_scratch_copy_compiles_to_the_committed_pack_s_hash(tmp_path, artifact):
    """A pack rewritten without its comments, in another layout, is the same pack: the pack's hash is over its content."""
    assert printed(run(*compile_args(tmp_path / "out", scratch(tmp_path)))).name == artifact.name


def test_a_pack_split_into_included_files_compiles_as_the_same_pack(tmp_path, artifact):
    clients = scratch(tmp_path)
    folder = clients / CLIENT
    pack = yaml.safe_load((folder / "pack.yaml").read_text())
    (folder / "glossary.yaml").write_text(yaml.safe_dump({"glossary": pack.pop("glossary")}, sort_keys=False))
    (folder / "parser").mkdir()
    (folder / "parser" / "words.yaml").write_text(
        yaml.safe_dump({"terminology": pack.pop("terminology"), "calendars": pack.pop("calendars")}, sort_keys=False))
    pack["manifest"]["includes"] = ["glossary.yaml", "parser/words.yaml"]
    (folder / "pack.yaml").write_text(yaml.safe_dump(pack, sort_keys=False))
    assert printed(run(*compile_args(tmp_path / "out", clients))).name == artifact.name


def test_it_reads_only_the_meta_model_the_pack_and_the_lock(tmp_path, artifact):
    """And, since meta-model 1.3.0, the platform's own preloaded standards (standards/preload.yaml), which sit beside the
    compiler, not the client: a folder holding only the meta-model, the pack and its lock compiles to the same hash.
    The compiler imports only CLIENT_ID and CLIENT_ID_RULE from the landing layer, and reads no landed release.
    compile.py alone imports kge.pipeline.profile, the deployment's registry (#39 item 9: every CLI takes --profile), and
    pack.py imports kge.pipeline.warehouse_versions only to check the version an onboarded unit's draft names: a name the
    artifact records for the runtime, never a warehouse file the compiler opens. The access helper evaluates a supplied
    role without reading any files, shared with the command core and job runner."""
    alone = tmp_path / "alone"
    (alone / "clients" / CLIENT).mkdir(parents=True)
    shutil.copy(META_MODEL, alone / "meta-model.yaml")
    for name in ("pack.yaml", "pack.lock", "catalogue.yaml"):
        shutil.copy(CLIENTS / CLIENT / name, alone / "clients" / CLIENT / name)
    r = run("--client", CLIENT, "--clients", "clients", "--out", "out", "--meta-model", "meta-model.yaml", cwd=alone,
            env={**os.environ, "PROFILES": str(alone / "no-registry.yaml"), "PROFILE": ""})
    assert (alone / printed(r)).name == artifact.name
    for name in ("pack.py", "checks.py", "emit.py", "compile.py"):
        src = (DEMO / "kge" / "compiler" / name).read_text()
        assert landing_imports_are_only_the_client_id_rule(src), name
        assert not re.search(r"^\s*(?:import|from)\s+(?:kge\.)?(?:duckdb|estate|graph|pipeline(?!\.(?:profile|settings|warehouse_versions|access)\b)|app|services|semantics|eval)\b", src,
                             re.M), name
        assert name == "compile.py" or "kge.pipeline.profile" not in src, name
        assert not re.search(r"warehouses\s*/|\.duckdb", src), name


def landing_imports_are_only_the_client_id_rule(src):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            modules = [alias.name.removeprefix("kge.") for alias in node.names]
            if any(module == "landing" or module.startswith("landing.") for module in modules):
                return False
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").removeprefix("kge.")
            if (module == "kge" or node.level == 2 and not module) and any(alias.name == "landing" for alias in node.names):
                return False
            if module == "landing" or module.startswith("landing."):
                if module != "landing.zone" or not all(
                        alias.name in {"CLIENT_ID", "CLIENT_ID_RULE"} for alias in node.names):
                    return False
    return True


@pytest.mark.parametrize("src, allowed", [
    ("from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE", True),
    ("from kge.landing.zone import CLIENT_ID as rule", True),
    ("from kge.landing.zone import Zone", False),
    ("from kge.landing.zone import *", False),
    ("from kge.landing import zone", False),
    ("from kge import landing", False),
    ("import kge.landing.zone", False),
    ("from kge.landing.other import reader", False),
    ("from ..landing.zone import Zone", False),
    ("from .. import landing", False),
])
def test_compiler_landing_import_guard(src, allowed):
    assert landing_imports_are_only_the_client_id_rule(src) is allowed


@pytest.mark.parametrize("path", [
    ("products", 0),
    ("products", 0, "fields", 0),
    ("products", 0, "fields", 0, "quality"),
    ("products", 0, "lineage"),
    ("products", 0, "counts"),
])
def test_the_catalogue_refuses_an_unknown_key_at_every_level(tmp_path, path):
    clients = scratch(tmp_path)
    catalogue_path = clients / CLIENT / "catalogue.yaml"
    catalogue = yaml.safe_load(catalogue_path.read_text())
    target = catalogue
    for part in path:
        target = target[part]
    target["unexpected"] = "refused"
    catalogue_path.write_text(yaml.safe_dump(catalogue, sort_keys=False))
    result = run(*compile_args(tmp_path / "out", clients))
    assert result.returncode != 0 and "catalogue.yaml:products[0]" in result.stderr and "unexpected" in result.stderr


def test_the_catalogue_refuses_lineage_that_names_no_product(tmp_path):
    clients = scratch(tmp_path)
    catalogue_path = clients / CLIENT / "catalogue.yaml"
    catalogue = yaml.safe_load(catalogue_path.read_text())
    catalogue["products"][0]["lineage"]["upstream"] = ["unknown.product"]
    catalogue_path.write_text(yaml.safe_dump(catalogue, sort_keys=False))
    result = run(*compile_args(tmp_path / "out", clients))
    assert result.returncode != 0
    assert "C01 catalogue.yaml:products[0].lineage.upstream: names unknown product unknown.product" in result.stderr


def test_the_artifact_holds_its_files_and_its_manifest_says_what_and_whose(artifact):
    man = manifest_of(artifact)
    assert set(tree(artifact)) == FILES
    assert set(man) == {"format", "client", "title", "pack", "lock_hash", "meta_model_version", "compiler_version", "target", "params",
                        "files", "counts", "load_order", "model_hash"}
    assert man["title"] == committed()[0]["manifest"]["title"], "the pack's title, which the loader writes on GraphModel"
    assert (man["format"], man["client"], man["target"]) == ("graph-model/v1", CLIENT, "kuzu")
    assert man["pack"]["id"] == "meridian-governance" and man["pack"]["version"] == "1.5.0" and HASH.fullmatch(man["pack"]["hash"])
    assert man["lock_hash"] == hashlib.sha256((CLIENTS / CLIENT / "pack.lock").read_bytes()).hexdigest()
    assert man["meta_model_version"] == yaml.safe_load(META_MODEL.read_text())["version"] == "1.3.0"
    assert man["compiler_version"] == compiler.VERSION and man["params"] == {"embedding_dim": 128, "id_scheme": "v1"}
    assert man["counts"]["nodes"] == NODES and man["counts"]["edges"] == EDGES
    text = (artifact / "manifest.json").read_text()
    assert text == json.dumps(man, indent=1, sort_keys=True) + "\n", "canonical JSON, a trailing newline"


def test_catalogue_facts_come_from_the_certified_warehouse_lock(artifact):
    source = yaml.safe_load((CLIENTS / CLIENT / "catalogue.yaml").read_text())
    compiled = yaml.safe_load((artifact / "catalogue.yaml").read_text())
    lock = committed()[1]
    assert all(not {"records", "storage", "path", "data_format", "last_dq_run"} & set(product) for product in source["products"])
    for product in compiled["products"]:
        table = next(item for item in lock["sources"][product["unit"]]["tables"] if item["name"] == product["table"])
        assert product["records"] == table["rows"]
        assert product["path"] == f"{product['unit']}.{product['table']}"
        assert product["last_dq_run"] == lock["as_of"]
        assert product["storage"]


def test_load_order_is_nodes_then_edges_and_defines_waits_for_the_landing(artifact):
    man = manifest_of(artifact)
    order = man["load_order"]
    nodes = [s for s in order if "from" not in s]
    edges = [s for s in order if "from" in s]
    assert order == nodes + edges and [s["table"] for s in nodes] == list(NODES)
    assert {s["path"] for s in order} == {p for p in FILES if p.startswith("data/")}
    for s in nodes:
        assert s["path"] == f"data/nodes/{s['table']}.csv" and s["stage"] == "copy"
    for s in edges:
        assert s["path"] == f"data/edges/{s['table']}__{s['from']}__{s['to']}.csv"
        assert s["stage"] == ("landing" if s["table"] == "DEFINES" else "copy"), s
    loaded = {s["table"] for s in nodes}
    assert all(s["from"] in loaded and s["to"] in loaded for s in edges if s["stage"] == "copy")
    for kind, steps in (("nodes", nodes), ("edges", edges)):
        totals: dict[str, int] = {}
        for s in steps:
            totals[s["table"]] = totals.get(s["table"], 0) + s["rows"]
        assert totals == man["counts"][kind]


def test_graph_model_yaml_is_the_schema_with_its_logical_and_loaded_names_and_counts(artifact):
    gm = yaml.safe_load((artifact / "graph-model.yaml").read_text())
    meta = yaml.safe_load(META_MODEL.read_text())
    man = manifest_of(artifact)
    for k in ("format", "client", "title", "pack", "lock_hash", "meta_model_version", "compiler_version", "target", "params", "counts"):
        assert gm[k] == man[k], k
    assert list(gm["node_types"]) == list(meta["node_types"]) and list(gm["edge_types"]) == list(meta["edge_types"])
    for kind in ("node_types", "edge_types"):
        for name, t in meta[kind].items():
            g = gm[kind][name]
            assert (g["loaded_as"], g["layer"], g["origin"]) == (t["loaded_as"], t["layer"], t["origin"]), name
            assert [p["name"] for p in g["properties"]] == list(t["properties"] or {}), name
            for p in g["properties"]:
                declared = t["properties"][p["name"]]
                logical = declared.get("logical") if isinstance(declared, dict) else None
                assert p.get("logical") == logical, (name, p)
    assert gm["node_types"]["Source"]["key"] == "id" and gm["node_types"]["Term"]["key"] == "name"
    assert {"name": "embedding", "type": "FLOAT[128]"} in gm["node_types"]["Chunk"]["properties"]
    assert gm["node_types"]["Source"]["rows"] == 4 and gm["node_types"]["Source"]["file"] == "data/nodes/Unit.csv"
    assert gm["node_types"]["Join"]["loaded_as"] == "JoinRule" and gm["edge_types"]["JOIN_FROM"]["logical"] == "FROM"
    assert gm["node_types"]["Document"]["filled_by"] == "load" and gm["node_types"]["Chunk"]["filled_by"] == "pipeline"
    assert list(gm["node_types"]["Process"]["constants"]) == [
        "tagging", "certification", "querying", "drafting", "building", "improving", "accepting"]
    groups = gm["edge_types"]["ALIAS_OF"]["files"]
    assert [(f["from"], f["to"], f["rows"]) for f in groups] == [("Alias", "Term", 30), ("Alias", "Unit", 6), ("Alias", "Col", 103),
                                                                 ("Alias", "Entity", 3)]
    assert gm["edge_types"]["CAN_READ"]["filled_by"] == "load" and gm["extensions"] == []


def test_the_ddl_is_the_meta_model_s_for_the_pack_s_embedding_dimension(artifact):
    from kge.graph import schema
    assert (artifact / "ddl" / "kuzu.cypher").read_text() == "".join(f"{s};\n" for s in schema.DDL)


def test_the_csvs_are_written_one_way(artifact):
    """A header of the loaded property names (from and to first on an edge), rows sorted by key, booleans as true and
    false, floats as repr, '' as '', NULL as \\N in a text column and empty in any other, and \\n line ends."""
    meta = yaml.safe_load(META_MODEL.read_text())
    loaded = {t["loaded_as"]: t for t in meta["node_types"].values()} | {e["loaded_as"]: e for e in meta["edge_types"].values()}
    for step in manifest_of(artifact)["load_order"]:
        raw = (artifact / step["path"]).read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n"), step["path"]
        header, *body = rows(artifact, step["path"])
        props = list(loaded[step["table"]]["properties"] or {})
        assert len(body) == step["rows"]
        if "from" in step:
            assert header == ["from", "to", *props] and body == sorted(body), step["path"]
        else:
            k = header.index(loaded[step["table"]]["key"])
            assert header == props and body == sorted(body, key=lambda r: (r[k], r)), step["path"]
            assert len({r[k] for r in body}) == len(body), step["path"]
    col = rows(artifact, "data/nodes/Col.csv")
    for name in ("is_key", "certified"):
        assert {r[col[0].index(name)] for r in col[1:]} == {"true", "false"}
    assert NULL not in {r[col[0].index("description")] for r in col[1:]}
    measure = rows(artifact, "data/nodes/Measure.csv")
    assert {(r[measure[0].index("snapshot")], r[measure[0].index("population")]) for r in measure[1:]} == {("", NULL)}
    binding = rows(artifact, "data/edges/PERIOD_COLUMN__Tbl__Col.csv")
    pack, _ = committed()
    unrolled = {(b["table"], f"{b['table']}.{b['column']}") for b in pack["period_bindings"] if "rollup" not in b}
    assert {(r[0], r[1]) for r in binding[1:] if r[4:] == [NULL, NULL]} == unrolled and len(unrolled) == 23
    assert {r[-1] for r in rows(artifact, "data/edges/MAPS_TO__Col__Term.csv")[1:]} == {"1.0"}


def test_no_file_holds_a_timestamp_or_an_absolute_path(artifact):
    for path, data in tree(artifact).items():
        text = data.decode()
        assert str(DEMO) not in text and str(artifact.parents[1]) not in text, path
        if path != "catalogue.yaml":
            assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text), path


def test_the_rows_hold_the_pack_and_the_lock_as_the_graph_has_them(artifact):
    """What kge/graph/load.py writes today, from the same pack and lock: units, terms (with their id slug), each column's
    certified flag and measure fields, the declared joins on their first column, the mappings, the no-join facts, the
    restricted columns and the twelve DEFINES edges. The query-semantics layer's rows are tests/unit/test_query_semantics.py's."""
    from kge.estate import spec
    pack, lock = committed()
    body = {path: rows(artifact, path)[1:] for path in (p for p in FILES if p.endswith(".csv"))}
    assert body["data/nodes/Unit.csv"] == sorted([u, spec.UNIT_LABEL[u], spec.UNIT_KEY[u], str(n)] for n, u in enumerate(spec.UNITS, 1))
    terms = {r[0]: r[1:] for r in body["data/nodes/Term.csv"]}
    assert {n: v[:3] for n, v in terms.items()} == {g["term"]: [g["domain"], g["steward"], g["definition"]] for g in spec.GLOSSARY}
    assert {n: v[3] for n, v in terms.items()} == {g["name"]: g["id"] for g in pack["glossary"]}
    header = rows(artifact, "data/nodes/Col.csv")[0]
    col = {r[0]: dict(zip(header, r)) for r in body["data/nodes/Col.csv"]}
    expected = {}
    for s, held in lock["sources"].items():
        for t in held["tables"]:
            for c in t["columns"]:
                expected[f"{s}.{t['name']}.{c['name']}"] = {"unit": s, "tbl": t["name"], "name": c["name"], "data_type": c["type"],
                                                           "description": c["description"] or "",
                                                           "is_key": str(c["name"] in t["primary_key"]).lower()}
    assert {k: {f: v[f] for f in ("unit", "tbl", "name", "data_type", "description", "is_key")} for k, v in col.items()} == expected
    assert {k for k, v in col.items() if v["certified"] == "true"} == {f"{u}.{t}.{c}" for _, u, t, c in spec.CERTIFIED_MAPPINGS}
    fields = ("grain", "currency", "calendar", "conversion")
    measured = {k: {f: v[f] for f in fields} for k, v in col.items() if any(v[f] for f in fields)}
    assert measured == {".".join(k): dict(md) for k, md in spec.MEASURE_METADATA.items()}
    joins = sorted([f"{s}.{t['name']}.{k['columns'][0]}", f"{s}.{k['ref_table']}.{k['ref_columns'][0]}", "declared foreign key"]
                   for s, held in lock["sources"].items() for t in held["tables"] for k in t["foreign_keys"])
    assert body["data/edges/JOINS__Col__Col.csv"] == joins
    assert body["data/edges/JOINS_TABLE__Tbl__Tbl.csv"] == sorted([a.rsplit(".", 1)[0], b.rsplit(".", 1)[0], *a.split(".")[1:], *b.split(".")[1:]]
                                                                  for a, b, _ in joins)
    term_name = {g["id"]: g["name"] for g in pack["glossary"]}
    assert body["data/edges/MAPS_TO__Col__Term.csv"] == sorted(
        [m["column"], term_name[m["term"]], "true", m["certified_by"], m["certified_on"], "human", "1.0"] for m in pack["mappings"])
    assert body["data/edges/NO_JOIN__Unit__Unit.csv"] == sorted(
        [f["a"], f["b"], f["asserted_by"], f["asserted_on"], f["note"]] for f in spec.NO_JOIN_FACTS)
    assert [r for r in body["data/edges/GOVERNS__Policy__Col.csv"] if r[0] == "restricted-personal-data"] == sorted(
        ["restricted-personal-data", ".".join(c), str(n)] for n, c in enumerate(spec.RESTRICTED_COLUMNS, 1))
    defines = body["data/edges/DEFINES__Document__Term.csv"]
    assert len(defines) == 12 and defines == sorted([doc, g["name"]] for g in pack["glossary"] for doc in g.get("defined_by", []))
    aliases = body["data/nodes/Alias.csv"]
    assert len(aliases) == 146 and ["digital engagement score", "candidate", "Digital Engagement Score", "22"] in aliases
    assert sorted(int(a[3]) for a in aliases) == list(range(1, 147)), "each alias's place in the pack's list"
    assert sorted(body["data/edges/ALIAS_OF__Alias__Unit.csv"]) == sorted([[u, u] for u in spec.UNITS] + [["advisory", "wealth"],
                                                                                                          ["deposits", "retail"]])


def test_the_role_templates_are_compiled_for_load_and_are_not_a_graph_table(artifact):
    """Each role's reads resolved (all is every source, in the pack's order), its writes and processes; the deployment's
    identities bind principals to them at load."""
    from kge.estate import spec
    roles = json.loads((artifact / "access" / "roles.json").read_text())
    assert roles["purposes"] == spec.PURPOSES
    assert [r["id"] for r in roles["roles"]] == sorted(r["id"] for r in roles["roles"])
    by_principal = {p["principal"]: (p["role"], p["reads"], p.get("writes", []), p["processes"]) for p in spec.PRINCIPALS}
    assert {r["id"]: (r["label"], r["reads"], r["writes"], r["processes"]) for r in roles["roles"]} == by_principal
    assert "Role" not in (artifact / "ddl" / "kuzu.cypher").read_text()


@pytest.mark.parametrize("process,allowed", [
    ("tagging", True), ("certification", True), ("querying", True),
    ("drafting", True), ("building", True), ("improving", True), ("accepting", True),
    ("future_graph_process", True), ("deploying", False)])
def test_role_processes_come_from_the_meta_model_or_platform(process, allowed):
    from kge.compiler.checks import roles
    from kge.compiler.pack import Pack
    meta = yaml.safe_load(META_MODEL.read_text())
    meta["node_types"]["Process"]["constants"]["future_graph_process"] = "A future graph process"
    pack = Pack("invented", CLIENTS / "invented", data={
        "sources": [], "access": {"roles": [{"id": "operator", "reads": "all", "processes": [process]}]}})
    findings_ = roles(pack, meta)
    if allowed:
        assert findings_ == []
    else:
        assert len(findings_) == 1 and findings_[0].rule == "S14"


def test_accepting_process_compiles_into_role_artifact(tmp_path):
    def grant(pack, lock):
        pack["access"]["roles"].append({"id": "reviewer", "label": "Reviewer", "reads": "all",
                                        "processes": ["accepting"]})

    clients = scratch(tmp_path, grant)
    folder = printed(run(*compile_args(tmp_path / "artifacts", clients)))
    roles = json.loads((folder / "access/roles.json").read_text())["roles"]
    assert next(role for role in roles if role["id"] == "reviewer")["processes"] == ["accepting"]


def test_the_post_load_gates_are_compiled_for_the_check_and_an_edit_to_them_is_another_hash(tmp_path, artifact):
    """manifest.gates, the thresholds the post-load check holds a loaded candidate to (kge/eval/margin.py, V01 and V02), is
    compiled into the artifact as gates.json, canonical JSON hashed like every other file, so the check reads the
    candidate's own thresholds, never the working pack's and never code's (issue #40, step 7). Meridian's are the
    template's (docs/use-cases/kge/platform/pack.example.yaml). A pack that declares none compiles to no gates.json."""
    from kge.compiler import checks
    from kge.eval import report
    pack, _ = committed()
    template = yaml.safe_load((DEMO.parents[1] / "docs" / "use-cases" / "kge" / "platform" / "pack.example.yaml").read_text())["manifest"]["gates"]
    assert pack["manifest"]["gates"] == template == {"golden_min_pass": {"within": 1.0, "cross": 1.0, "shelf": 1.0, "abstain": 1.0},
                                                     "control_margin": {"cross": 0.5, "shelf": 0.5}}
    assert checks.KINDS == tuple(report.KINDS), "the golden scorer's four kinds of question"
    text = (artifact / "gates.json").read_text()
    assert json.loads(text) == pack["manifest"]["gates"] and text == json.dumps(pack["manifest"]["gates"], indent=1, sort_keys=True) + "\n"
    assert "gates.json" in manifest_of(artifact)["files"]
    edited = printed(run(*compile_args(tmp_path / "out", scratch(tmp_path, lambda p, l: p["manifest"]["gates"]["control_margin"].update(cross=0.6)))))
    assert edited.name != artifact.name, "an edit to the gates is another model hash"
    one, two = tree(artifact), tree(edited)
    assert sorted(p for p in set(one) | set(two) if one.get(p) != two.get(p)) == ["gates.json", "graph-model.yaml", "manifest.json"]
    assert yaml.safe_load(one["graph-model.yaml"]) | {"pack": None} == yaml.safe_load(two["graph-model.yaml"]) | {"pack": None}
    assert json.loads(two["gates.json"])["control_margin"] == {"cross": 0.6, "shelf": 0.5}
    none = printed(run(*compile_args(tmp_path / "none", scratch(tmp_path / "x", lambda p, l: p["manifest"].pop("gates")))))
    assert "gates.json" not in tree(none) and "gates.json" not in manifest_of(none)["files"]


def test_the_checks_it_cannot_make_are_printed_as_not_checked_here(tmp_path):
    r = run(*compile_args(tmp_path))
    (line,) = [x for x in r.stderr.splitlines() if "not checked here" in x]
    assert all(rule in line for rule in NOT_CHECKED) and "documents" in line, line
    r = run(*compile_args(tmp_path / "bad", scratch(tmp_path, lambda p, l: p["manifest"].update(client="Meridian"))))
    assert r.returncode == 2 and "not checked here" in r.stderr


def _fk(lock: dict) -> dict:
    return table(lock, "retail", "account")["foreign_keys"][0]


def _drop_binding(pack: dict, name: str) -> None:
    pack["period_bindings"] = [b for b in pack["period_bindings"] if b["table"] != name]


MUTATIONS = [
    ("client-id-not-lower-case", "S00", "pack.yaml:manifest.client", lambda p, l: p["manifest"].update(client="Meridian")),
    ("client-not-the-folder", "S00", "pack.yaml:manifest.client", lambda p, l: p["manifest"].update(client="someone-else")),
    ("meta-model-version-missing", "S00", "pack.yaml:manifest.meta_model_version", lambda p, l: p["manifest"].pop("meta_model_version")),
    ("meta-model-version-not-semver", "S00", "pack.yaml:manifest.meta_model_version",
     lambda p, l: p["manifest"].update(meta_model_version="1.0")),
    ("unknown-section", "S01", "pack.yaml:glosary", lambda p, l: p.update(glosary=[])),
    ("missing-field", "S01", "pack.yaml:glossary[0].steward", lambda p, l: p["glossary"][0].pop("steward")),
    ("unknown-field", "S01", "pack.yaml:glossary[0].owner", lambda p, l: p["glossary"][0].update(owner="Finance")),
    ("not-in-the-enum", "S01", "pack.yaml:measures[0].grain", lambda p, l: p["measures"][0].update(grain="weekly")),
    ("not-a-whole-number", "S01", "pack.yaml:calendars[1].year_start_month", lambda p, l: p["calendars"][1].update(year_start_month="4")),
    ("not-a-date", "S01", "pack.yaml:no_join[0].asserted_on", lambda p, l: p["no_join"][0].update(asserted_on="12 March 2024")),
    ("the-null-marker-as-a-value", "S01", "pack.yaml:glossary[2].definition", lambda p, l: p["glossary"][2].update(definition=NULL)),
    ("not-plain-yaml", "S01", "pack.yaml:manifest.gates.1", lambda p, l: p["manifest"].update(gates={1: "a key that is not text"})),
    ("alias-with-two-kinds", "S01", "pack.yaml:terminology.aliases[0]", lambda p, l: p["terminology"]["aliases"][0].update(source="retail")),
    ("gate-kind-unknown", "S01", "pack.yaml:manifest.gates.golden_min_pass.trading",
     lambda p, l: p["manifest"]["gates"]["golden_min_pass"].update(trading=1.0)),
    ("gate-share-above-one", "S01", "pack.yaml:manifest.gates.control_margin.cross",
     lambda p, l: p["manifest"]["gates"]["control_margin"].update(cross=1.5)),
    ("gate-share-below-zero", "S01", "pack.yaml:manifest.gates.control_margin.shelf",
     lambda p, l: p["manifest"]["gates"]["control_margin"].update(shelf=-0.1)),
    ("gate-share-not-a-number", "S01", "pack.yaml:manifest.gates.golden_min_pass.within",
     lambda p, l: p["manifest"]["gates"]["golden_min_pass"].update(within="all")),
    ("gate-share-true", "S01", "pack.yaml:manifest.gates.golden_min_pass.abstain",
     lambda p, l: p["manifest"]["gates"]["golden_min_pass"].update(abstain=True)),
    ("gate-unknown-field", "S01", "pack.yaml:manifest.gates.min_pass", lambda p, l: p["manifest"]["gates"].update(min_pass={"within": 1.0})),
    ("lock-of-another-client", "S01", "pack.lock:client", lambda p, l: l.update(client="someone-else")),
    ("lock-missing-a-harvested-source", "S01", "pack.lock:sources", lambda p, l: l["sources"].pop("sme")),
    ("lock-column-without-description", "S01", "pack.lock:sources.retail.account.columns",
     lambda p, l: column(l, "retail", "account", "account_id").pop("description")),
    ("sources-not-a-list", "S01", "pack.yaml:sources", lambda p, l: p.update(sources=1)),
    ("source-id-not-text", "S01", "pack.yaml:sources[0].id", lambda p, l: p["sources"][0].update(id=None)),
    ("source-id-a-list", "S01", "pack.yaml:sources[0].id", lambda p, l: p["sources"][0].update(id=["retail"])),
    ("duplicate-term-id", "S02", "pack.yaml:glossary[1].id", lambda p, l: p["glossary"][1].update(id=p["glossary"][0]["id"])),
    ("term-id-not-a-slug", "S02", "pack.yaml:glossary[1].id", lambda p, l: p["glossary"][1].update(id="Net Interest Income")),
    ("duplicate-term-name", "S02", "pack.yaml:glossary[1].name", lambda p, l: p["glossary"][1].update(name=p["glossary"][0]["name"])),
    ("duplicate-measure-id", "S02", "pack.yaml:measures[1].id", lambda p, l: p["measures"][1].update(id=p["measures"][0]["id"])),
    ("duplicate-role-id", "S02", "pack.yaml:access.roles[1].id",
     lambda p, l: p["access"]["roles"][1].update(id=p["access"]["roles"][0]["id"])),
    ("meta-model-range-2", "S03", "pack.yaml:manifest.platform.meta_model", lambda p, l: p["manifest"]["platform"].update(meta_model="^2.0")),
    ("compiler-range", "S03", "pack.yaml:manifest.platform.compiler", lambda p, l: p["manifest"]["platform"].update(compiler=">=9")),
    ("written-for-meta-model-2", "S03", "pack.yaml:manifest.meta_model_version",
     lambda p, l: p["manifest"].update(meta_model_version="2.0.0")),
    ("schema-pins-another-version", "S03", "pack.yaml:schema.meta_model", lambda p, l: p["schema"].update(meta_model="1.0")),
    ("extension-not-namespaced", "S04", "pack.yaml:schema.extensions[0].node_type",
     lambda p, l: p["schema"]["extensions"].append({"node_type": "Desk", "key": "id", "properties": {"id": "STRING"}})),
    ("extension-named-as-a-meta-type", "S04", "pack.yaml:schema.extensions[0].node_type",
     lambda p, l: p["schema"]["extensions"].append({"node_type": "Unit", "key": "id", "properties": {"id": "STRING"}})),
    ("extension-type-not-a-meta-model-type", "S04", "pack.yaml:schema.extensions[0].properties.floor",
     lambda p, l: p["schema"]["extensions"].append({"node_type": "x_meridian_Desk", "key": "id",
                                                    "properties": {"id": "STRING", "floor": "TEXT"}})),
    ("extension-on-the-runtime-layer", "S04", "pack.yaml:schema.extensions[0].extends",
     lambda p, l: p["schema"]["extensions"].append({"property": "x_meridian_note", "extends": "Chunk", "type": "STRING"})),
    ("extension-retypes-a-property", "S04", "pack.yaml:schema.extensions[0].property",
     lambda p, l: p["schema"]["extensions"].append({"property": "definition", "extends": "Term", "type": "INT64"})),
    ("extension-key-not-text", "S04", "pack.yaml:schema.extensions[0].key",
     lambda p, l: p["schema"]["extensions"].append({"node_type": "x_meridian_Desk", "key": ["id"], "properties": {"id": "STRING"}})),
    ("extension-end-not-text", "S04", "pack.yaml:schema.extensions[0].from",
     lambda p, l: p["schema"]["extensions"].append({"edge_type": "x_meridian_SITS_IN", "from": ["Table"], "to": "Table"})),
    ("extension-extends-not-text", "S04", "pack.yaml:schema.extensions[0].extends",
     lambda p, l: p["schema"]["extensions"].append({"property": "x_meridian_note", "extends": ["Term"], "type": "STRING"})),
    ("undeclared-extension-property", "S04", "pack.yaml:glossary[0].x_meridian_owner",
     lambda p, l: p["glossary"][0].update(x_meridian_owner="Finance")),
    ("fk-to-a-missing-column", "S05", "pack.lock:sources.retail.account.foreign_keys[0]", lambda p, l: _fk(l).update(ref_columns=["nope"])),
    ("fk-to-a-missing-table", "S05", "pack.lock:sources.retail.account.foreign_keys[0]", lambda p, l: _fk(l).update(ref_table="person")),
    ("fk-types-incompatible", "S05", "pack.lock:sources.retail.account.foreign_keys[0]",
     lambda p, l: column(l, "retail", "account", "customer_id").update(type="DATE")),
    ("composite-fk", "S05", "pack.lock:sources.retail.account.foreign_keys[0]",
     lambda p, l: _fk(l).update(columns=["customer_id", "product_id"], ref_columns=["customer_id", "branch_id"])),
    ("no-join-undeclared-source", "S06", "pack.yaml:no_join[0].between", lambda p, l: p["no_join"][0].update(between=["retail", "treasury"])),
    ("no-join-one-source", "S06", "pack.yaml:no_join[0].between", lambda p, l: p["no_join"][0].update(between=["retail", "retail"])),
    ("no-join-pair-twice", "S06", "pack.yaml:no_join[1].between", lambda p, l: p["no_join"][1].update(between=["sme", "retail"])),
    ("mapping-to-a-missing-column", "S07", "pack.yaml:mappings[22].column",
     lambda p, l: mapping(p, "retail.complaint.complaint_id").update(column="retail.complaint.nope")),
    ("mapping-to-an-undeclared-term", "S07", "pack.yaml:mappings[0].term", lambda p, l: p["mappings"][0].update(term="takings")),
    ("mapping-twice", "S07", "pack.yaml:mappings[1]", lambda p, l: p["mappings"].insert(1, dict(p["mappings"][0]))),
    ("measure-two-statements", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(monthly_revenue.total_revenue_usd); drop table monthly_revenue")),
    ("measure-a-query", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="select sum(total_revenue_usd) from monthly_revenue")),
    ("measure-not-an-aggregate", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="monthly_revenue.total_revenue_usd + 1")),
    ("measure-column-missing", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(monthly_revenue.total_revenue_usd) + sum(monthly_revenue.nope)")),
    ("measure-unknown-function", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(do_something(monthly_revenue.total_revenue_usd))")),
    ("measure-reads-a-csv-file", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(monthly_revenue.total_revenue_usd + length(read_csv('/etc/passwd')))")),
    ("measure-reads-a-parquet-file", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(monthly_revenue.total_revenue_usd) + count(read_parquet('rows.parquet'))")),
    ("measure-reads-another-table", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(monthly_revenue.total_revenue_usd + customer.customer_id)")),
    ("measure-reads-another-source", "S08", "pack.yaml:measures[0].expression",
     lambda p, l: p["measures"][0].update(expression="sum(monthly_revenue.total_revenue_usd) + sum(sme.monthly_client_revenue.revenue_usd)")),
    ("measure-aggregation-disagrees", "S08", "pack.yaml:measures[0].aggregation", lambda p, l: p["measures"][0].update(aggregation="avg")),
    ("measure-undeclared-term", "S08", "pack.yaml:measures[0].term", lambda p, l: p["measures"][0].update(term="takings")),
    ("measure-column-not-in-the-lock", "S08", "pack.yaml:measures[0].column",
     lambda p, l: p["measures"][0].update(column="retail.monthly_revenue.nope")),
    ("two-measures-on-a-column", "S08", "pack.yaml:measures[1].column", lambda p, l: p["measures"][1].update(column=p["measures"][0]["column"])),
    ("measure-undeclared-calendar", "S09", "pack.yaml:measures[0].calendar", lambda p, l: p["measures"][0].update(calendar="lunar")),
    ("binding-to-a-missing-column", "S09", "pack.yaml:period_bindings[0].column", lambda p, l: p["period_bindings"][0].update(column="nope")),
    ("binding-rollup-missing", "S09", "pack.yaml:period_bindings[0].rollup.table",
     lambda p, l: p["period_bindings"][0]["rollup"].update(table="retail.nope")),
    ("binding-twice", "S09", "pack.yaml:period_bindings[1].table", lambda p, l: p["period_bindings"][1].update(table="retail.calendar_month")),
    ("measure-table-without-a-binding", "S09", "pack.yaml:measures[0].column", lambda p, l: _drop_binding(p, "retail.monthly_revenue")),
    ("year-start-month-13", "S09", "pack.yaml:calendars[1].year_start_month", lambda p, l: p["calendars"][1].update(year_start_month=13)),
    ("currency-not-iso", "S10", "pack.yaml:measures[0].currency", lambda p, l: p["measures"][0].update(currency="USX")),
    ("conversion-from-its-own-currency", "S10", "pack.yaml:measures[0].conversion",
     lambda p, l: p["measures"][0].update(conversion="period_end_rate_from_USD")),
    ("alias-undeclared-term", "S11", "pack.yaml:terminology.aliases[0].term", lambda p, l: p["terminology"]["aliases"][0].update(term="takings")),
    ("alias-undeclared-source", "S11", "pack.yaml:terminology.aliases[22].source",
     lambda p, l: p["terminology"]["aliases"][22].update(source="treasury")),
    ("one-phrase-two-targets", "S11", "pack.yaml:terminology.aliases[1].phrase",
     lambda p, l: p["terminology"]["aliases"][1].update(phrase="Revenue")),
    ("candidate-already-a-term", "S11", "pack.yaml:terminology.aliases[21].candidate",
     lambda p, l: p["terminology"]["aliases"][21].update(candidate="Revenue")),
    ("role-reads-undeclared-source", "S14", "pack.yaml:access.roles[0].reads[0]", lambda p, l: p["access"]["roles"][0].update(reads=["treasury"])),
    ("role-writes-the-glossary", "S14", "pack.yaml:access.roles[3].writes[0]", lambda p, l: p["access"]["roles"][3].update(writes=["glossary"])),
    ("role-performs-an-unknown-process", "S14", "pack.yaml:access.roles[0].processes[0]",
     lambda p, l: p["access"]["roles"][0].update(processes=["deploying"])),
    ("restricted-column-not-in-the-lock", "S22", "pack.yaml:policies.restricted_attributes[0].columns[0]",
     lambda p, l: p["policies"]["restricted_attributes"][0]["columns"].__setitem__(0, "retail.customer.nickname")),
    ("restricted-phrase-for-a-column-it-does-not-restrict", "S22", "pack.yaml:policies.restricted_attributes[0].phrases[0].column",
     lambda p, l: p["policies"]["restricted_attributes"][0]["phrases"][0].update(column="retail.customer.customer_id")),
    ("restricted-phrase-twice", "S22", "pack.yaml:policies.restricted_attributes[0].phrases[1].phrases[0]",
     lambda p, l: p["policies"]["restricted_attributes"][0]["phrases"][1]["phrases"].__setitem__(0, "birthday")),
    ("restricted-phrase-empty", "S22", "pack.yaml:policies.restricted_attributes[0].phrases[0].phrases[0]",
     lambda p, l: p["policies"]["restricted_attributes"][0]["phrases"][0]["phrases"].__setitem__(0, " - ")),
    ("vocabulary-column-not-in-the-lock", "S29", "pack.yaml:terminology.value_vocabularies[0].column",
     lambda p, l: p["terminology"]["value_vocabularies"][0].update(column="retail.branch.nope")),
    ("vocabulary-values-and-a-pattern", "S29", "pack.yaml:terminology.value_vocabularies[4]",
     lambda p, l: p["terminology"]["value_vocabularies"][4].update(pattern="upper_2")),
    ("vocabulary-neither-values-nor-a-pattern", "S29", "pack.yaml:terminology.value_vocabularies[2]",
     lambda p, l: p["terminology"]["value_vocabularies"][2].pop("pattern")),
    ("vocabulary-pattern-unknown", "S01", "pack.yaml:terminology.value_vocabularies[0].pattern",
     lambda p, l: p["terminology"]["value_vocabularies"][0].update(pattern="[A-Z]+")),
    ("vocabulary-prefix-with-values", "S29", "pack.yaml:terminology.value_vocabularies[4].prefix",
     lambda p, l: p["terminology"]["value_vocabularies"][4].update(prefix="X-")),
    ("predicate-column-not-in-the-lock", "S29", "pack.yaml:terminology.predicates[0].column",
     lambda p, l: p["terminology"]["predicates"][0].update(column="retail.complaint.closed_on")),
    ("predicate-without-its-value", "S29", "pack.yaml:terminology.predicates[1].value", lambda p, l: p["terminology"]["predicates"][1].pop("value")),
    ("predicate-compares-with-text", "S29", "pack.yaml:terminology.predicates[2].value",
     lambda p, l: p["terminology"]["predicates"][2].update(value="thirty")),
    ("predicate-value-it-does-not-take", "S29", "pack.yaml:terminology.predicates[0].value",
     lambda p, l: p["terminology"]["predicates"][0].update(value="x")),
    ("predicate-operator-unknown", "S01", "pack.yaml:terminology.predicates[0].op", lambda p, l: p["terminology"]["predicates"][0].update(op="like")),
    ("predicate-on-a-restricted-column", "S29", "pack.yaml:terminology.predicates[0].column",
     lambda p, l: p["terminology"]["predicates"][0].update(column="retail.customer.email")),
    ("intent-twice", "S29", "pack.yaml:terminology.intents[2].intent", lambda p, l: p["terminology"]["intents"][2].update(intent="count")),
    ("intent-unknown", "S01", "pack.yaml:terminology.intents[0].intent", lambda p, l: p["terminology"]["intents"][0].update(intent="compare")),
    ("phrase-empty", "S01", "pack.yaml:terminology.intents[1].phrases[0]", lambda p, l: p["terminology"]["intents"][1]["phrases"].__setitem__(0, " ")),
    ("mailbox-not-a-local-part", "S29", "pack.yaml:terminology.functional_mailboxes[0]",
     lambda p, l: p["terminology"]["functional_mailboxes"].__setitem__(0, "Records Team")),
    ("mailbox-twice", "S29", "pack.yaml:terminology.functional_mailboxes[1]",
     lambda p, l: p["terminology"]["functional_mailboxes"].__setitem__(1, "records")),
]


@pytest.mark.parametrize("rule, where, edit", [pytest.param(*m[1:], id=m[0]) for m in MUTATIONS])
def test_each_check_names_its_rule_exits_2_and_writes_nothing(tmp_path, capsys, rule, where, edit):
    out = tmp_path / "artifacts"
    code, stdout, err = compile_in(capsys, scratch(tmp_path, edit), out)
    assert code == 2 and stdout == "", err
    assert (rule, where) in [(r, p) for r, p, _ in findings(err)], err
    assert "nothing was written" in err and not out.exists()


MEASURE_FORMS = [
    "sum(monthly_revenue.total_revenue_usd)", "sum(total_revenue_usd)", "sum(retail.monthly_revenue.total_revenue_usd)",
    "sum(monthly_revenue.total_revenue_usd) FILTER (WHERE monthly_revenue.fee_income > 0 AND NOT monthly_revenue.account_id IS NULL)",
    "count(DISTINCT monthly_revenue.account_id)", "count(*)", "max(monthly_revenue.total_revenue_usd)",
    "avg(-(monthly_revenue.fee_income * 2) - 1)",
    "sum(CASE WHEN monthly_revenue.month_start >= '2025-01-01' THEN monthly_revenue.fee_income ELSE 0 END)",
    "sum(coalesce(CAST(monthly_revenue.fee_income AS DECIMAL(18, 2)), 0)) / nullif(sum(monthly_revenue.interest_income), 0)",
    "sum(if(monthly_revenue.account_id IN (1, 2), monthly_revenue.fee_income, 0)) FILTER (WHERE monthly_revenue.revenue_id BETWEEN 1 AND 9)",
]
MEASURE_REFUSED = [
    "sum(monthly_revenue.total_revenue_usd + length(read_csv('/etc/passwd')))",
    "sum(monthly_revenue.total_revenue_usd) + count(read_parquet('rows.parquet'))",
    "sum(monthly_revenue.total_revenue_usd) + count(read_json('rows.json'))",
    "sum(length(getenv('HOME')))",
    "sum(length(CAST(monthly_revenue.fee_income AS VARCHAR)))",
    "sum(monthly_revenue.total_revenue_usd) OVER ()",
    "sum(monthly_revenue.total_revenue_usd) FILTER (WHERE monthly_revenue.account_id IN (SELECT 1))",
    "sum(monthly_revenue.total_revenue_usd) AS revenue",
]
MEASURE_OTHER_TABLES = [
    "sum(monthly_revenue.total_revenue_usd) + sum(account.currency_code)",
    "sum(monthly_revenue.total_revenue_usd + customer.customer_id)",
    "sum(monthly_revenue.total_revenue_usd) FILTER (WHERE customer.customer_id > 0)",
    "sum(retail.customer.customer_id) + sum(monthly_revenue.total_revenue_usd)",
    "sum(monthly_revenue.total_revenue_usd) + sum(sme.monthly_client_revenue.revenue_usd)",
    "sum(meridian.retail.monthly_revenue.total_revenue_usd)",
]


def test_a_measure_s_expression_reads_its_rows_through_the_forms_it_may_use_and_nothing_else():
    """A measure's expression reads the rows of its own table through aggregates, arithmetic and comparisons, FILTER
    (WHERE ...), CASE and IF, CAST, COALESCE and NULLIF; any other function is refused, whatever sqlglot calls it,
    and so is a column of any other table."""
    from kge.compiler import checks
    _, lock = committed()
    for text in MEASURE_FORMS:
        assert checks.expression(text, "retail.monthly_revenue.total_revenue_usd", lock)[0] == [], text
    for text in MEASURE_REFUSED:
        problems = checks.expression(text, "retail.monthly_revenue.total_revenue_usd", lock)[0]
        assert len(problems) == 1 and problems[0].startswith("reads only"), (text, problems)


def test_a_measure_s_expression_reads_no_other_table_of_its_source_or_of_another():
    """The expression has no FROM: it is one value over the rows of the measure's own table, the table of its column,
    so a column of any other table has no rows to be read from, and would give the measure a COMPUTED_FROM edge to a
    table it cannot read."""
    from kge.compiler import checks
    _, lock = committed()
    for text in MEASURE_OTHER_TABLES:
        problems, cols, _ = checks.expression(text, "retail.monthly_revenue.total_revenue_usd", lock)
        assert len(problems) == 1 and "a measure reads the rows of its own table, retail.monthly_revenue" in problems[0], (text, problems)
        assert set(cols) <= {"retail.monthly_revenue.total_revenue_usd"}, (text, cols)


MEASURE_NESTED = [
    "sum(sum(monthly_revenue.total_revenue_usd))",
    "sum(monthly_revenue.total_revenue_usd) FILTER (WHERE sum(monthly_revenue.fee_income) > 0)",
]


def test_a_measure_s_expression_is_one_aggregate_with_none_in_its_arguments_or_its_filter(tmp_path):
    """One read-only aggregate: an aggregate inside another, in its arguments or in its FILTER (WHERE ...), is not one
    value over the rows, and DuckDB refuses either ("aggregate function calls cannot be nested"). Each is one S08
    finding, in process and as a changed pack through the command, which exits 2 and writes nothing."""
    from kge.compiler import checks
    _, lock = committed()
    for i, text in enumerate(MEASURE_NESTED):
        problems = checks.expression(text, "retail.monthly_revenue.total_revenue_usd", lock)[0]
        assert len(problems) == 1 and problems[0].endswith("is an aggregate inside an aggregate"), (text, problems)
        out = tmp_path / str(i) / "artifacts"
        r = run(*compile_args(out, scratch(tmp_path / str(i), lambda p, l: p["measures"][0].update(expression=text))))
        assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr, r.stderr
        assert [f[:2] for f in findings(r.stderr)] == [("S08", "pack.yaml:measures[0].expression")], r.stderr
        assert "nothing was written" in r.stderr and not out.exists()


ACCEPTANCE = [
    ("a-mapping-to-a-missing-column", "S07", lambda p, l: mapping(p, "retail.complaint.complaint_id").update(column="retail.complaint.nope")),
    ("client-id-Meridian", "S00", lambda p, l: p["manifest"].update(client="Meridian")),
    ("an-extension-without-x_meridian_", "S04",
     lambda p, l: p["schema"]["extensions"].append({"node_type": "Desk", "key": "id", "properties": {"id": "STRING"}})),
    ("meta_model-^2.0", "S03", lambda p, l: p["manifest"]["platform"].update(meta_model="^2.0")),
]


@pytest.mark.parametrize("rule, edit", [pytest.param(*a[1:], id=a[0]) for a in ACCEPTANCE])
def test_the_four_scratch_edits_each_name_their_rule_through_the_command(tmp_path, rule, edit):
    r = run(*compile_args(tmp_path / "artifacts", scratch(tmp_path, edit)))
    assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr, r.stderr
    assert rule in {f[0] for f in findings(r.stderr)}, r.stderr
    assert not (tmp_path / "artifacts").exists()


def test_every_finding_is_listed_one_per_line(tmp_path, capsys):
    def edit(p, l):
        p["mappings"][0]["term"] = "takings"
        p["measures"][0]["currency"] = "USX"
        p["access"]["roles"][0]["processes"] = ["deploying"]
    code, _, err = compile_in(capsys, scratch(tmp_path, edit), tmp_path / "out")
    lines = err.splitlines()
    assert code == 2 and [x for x in lines if not x.startswith("compile: ")] == [x for x in lines if FINDING.match(x)]
    assert [r for r, _, _ in findings(err)] == ["S07", "S10", "S14"], err
    assert "3 findings" in err


def test_a_wrong_client_id_or_meta_model_version_hides_no_other_finding(tmp_path, capsys):
    """S00 is listed with the rest: the shapes (S01), and when they hold, the later checks, which go by the folder's
    name. Only a shape that does not hold stops the checks that read it."""
    def later(p, l):
        p["manifest"].update(client="Meridian", meta_model_version="1.0")
        mapping(p, "retail.complaint.complaint_id")["column"] = "retail.complaint.nope"
    code, _, err = compile_in(capsys, scratch(tmp_path / "later", later), tmp_path / "out")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == [
        ("S00", "pack.yaml:manifest.client"), ("S00", "pack.yaml:manifest.meta_model_version"),
        ("S07", "pack.yaml:mappings[22].column")], err

    def shapes(p, l):
        p["manifest"].update(client="Meridian")
        p["glossary"][0].pop("steward")
    code, _, err = compile_in(capsys, scratch(tmp_path / "shapes", shapes), tmp_path / "out")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == [
        ("S00", "pack.yaml:manifest.client"), ("S01", "pack.yaml:glossary[0].steward")], err

    def no_manifest(p, l):
        p.pop("manifest")
    code, _, err = compile_in(capsys, scratch(tmp_path / "none", no_manifest), tmp_path / "out")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == [("S00", "pack.yaml:manifest")], err
    assert not (tmp_path / "out").exists()


def _missing_column(p: dict) -> None:
    mapping(p, "retail.complaint.complaint_id")["column"] = "retail.complaint.nope"


STOPS = [
    ("a-stray-field-in-the-manifest", lambda p, l: (p["manifest"].update(colour="blue"), _missing_column(p)),
     [("S01", "pack.yaml:manifest.colour"), ("S07", "pack.yaml:mappings[22].column")]),
    ("an-empty-title", lambda p, l: (p["manifest"].update(title=""), _missing_column(p)),
     [("S01", "pack.yaml:manifest.title"), ("S07", "pack.yaml:mappings[22].column")]),
    ("ids-section-by-section", lambda p, l: (p["manifest"].update(colour="blue"), p["measures"][1].update(id=p["measures"][0]["id"])),
     [("S01", "pack.yaml:manifest.colour"), ("S02", "pack.yaml:measures[1].id")]),
    ("a-glossary-entry-short-a-field", lambda p, l: (p["glossary"][0].pop("steward"), _missing_column(p),
                                                     p["measures"][0].update(currency="USX")),
     [("S01", "pack.yaml:glossary[0].steward"), ("S10", "pack.yaml:measures[0].currency")]),
    ("a-lock-of-another-client", lambda p, l: (l.update(client="someone-else"), _missing_column(p),
                                               p["measures"][0].update(currency="USX")),
     [("S01", "pack.lock:client"), ("S10", "pack.yaml:measures[0].currency")]),
    ("a-source-id-not-text", lambda p, l: (p["sources"][0].update(id=None), p["measures"][0].update(currency="USX")),
     [("S01", "pack.yaml:sources[0].id"), ("S10", "pack.yaml:measures[0].currency")]),
]


@pytest.mark.parametrize("edit, listed", [pytest.param(*s[1:], id=s[0]) for s in STOPS])
def test_a_shape_finding_stops_only_the_checks_that_read_it(tmp_path, capsys, edit, listed):
    """One S01 finding hides no finding in a part of the pack it is not in: a stray field in the manifest hides no
    mapping's. A check that reads the section or the lock the shape finding is in waits for it."""
    code, _, err = compile_in(capsys, scratch(tmp_path, edit), tmp_path / "out")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == listed, err
    assert f"{len(listed)} findings" in err and not (tmp_path / "out").exists()


def test_the_lock_must_be_there(tmp_path, capsys):
    clients = scratch(tmp_path)
    (clients / CLIENT / "pack.lock").unlink()
    code, _, err = compile_in(capsys, clients, tmp_path / "out")
    assert code == 2 and ("S01", "pack.lock") in [(r, p) for r, p, _ in findings(err)] and "make lock" in err, err
    clients = scratch(tmp_path / "and", lambda p, l: (_missing_column(p), p["measures"][0].update(currency="USX")))
    (clients / CLIENT / "pack.lock").unlink()
    code, _, err = compile_in(capsys, clients, tmp_path / "out")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == [("S01", "pack.lock"), ("S10", "pack.yaml:measures[0].currency")], err


INCLUDE_FAULTS = [
    ("outside-the-folder", ["../elsewhere.yaml"], {}, "pack.yaml:manifest.includes[0]"),
    ("absolute", ["/etc/hostname"], {}, "pack.yaml:manifest.includes[0]"),
    ("missing", ["nope.yaml"], {}, "pack.yaml:manifest.includes[0]"),
    ("not-yaml", ["notes.txt"], {"notes.txt": "x"}, "pack.yaml:manifest.includes[0]"),
    ("holds-the-manifest", ["more.yaml"], {"more.yaml": "manifest: {client: meridian}\n"}, "more.yaml:manifest"),
    ("a-section-in-two-files", ["more.yaml"], {"more.yaml": "calendars: []\n"}, "more.yaml:calendars"),
    ("not-a-mapping", ["more.yaml"], {"more.yaml": "- calendars\n"}, "more.yaml"),
]


@pytest.mark.parametrize("includes, files, where", [pytest.param(*f[1:], id=f[0]) for f in INCLUDE_FAULTS])
def test_an_include_is_a_yaml_file_of_sections_inside_the_client_s_folder(tmp_path, capsys, includes, files, where):
    clients = scratch(tmp_path, lambda p, l: p["manifest"].update(includes=includes))
    (tmp_path / "elsewhere.yaml").write_text("calendars: []\n")
    for name, text in files.items():
        (clients / CLIENT / name).write_text(text)
    code, _, err = compile_in(capsys, clients, tmp_path / "out")
    assert code == 2 and ("S01", where) in [(r, p) for r, p, _ in findings(err)], err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("bad", ["Meridian", "../meridian", "meridian/x", ""])
def test_a_client_that_is_not_an_id_names_nothing_on_disk(tmp_path, bad):
    r = run(*compile_args(tmp_path / "out", client=bad))
    assert r.returncode == 2 and r.stderr.startswith("S00 --client: ") and "Traceback" not in r.stderr, r.stderr
    assert not (tmp_path / "out").exists()


def test_a_client_with_no_pack(tmp_path, capsys):
    code, _, err = compile_in(capsys, tmp_path, tmp_path / "out")
    assert code == 2 and ("S01", "pack.yaml") in [(r, p) for r, p, _ in findings(err)], err


@pytest.mark.parametrize("text", ["{}\n", "", "- manifest\n"], ids=["no-sections", "empty", "a-list"])
def test_a_pack_with_no_sections_is_findings_not_a_traceback(tmp_path, text):
    clients = scratch(tmp_path)
    (clients / CLIENT / "pack.yaml").write_text(text)
    r = run(*compile_args(tmp_path / "out", clients))
    assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr, r.stderr
    assert findings(r.stderr) and "nothing was written" in r.stderr and not (tmp_path / "out").exists(), r.stderr


def meta_model_copy(folder: Path, change) -> Path:
    meta = yaml.safe_load(META_MODEL.read_text())
    change(meta)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "meta-model.yaml"
    path.write_text(yaml.safe_dump(meta, sort_keys=False))
    return path


def test_a_pack_compiles_against_a_newer_minor_meta_model_and_not_a_newer_major(tmp_path, capsys):
    """Minor versions only add, so the 1.3.0 pack compiles against a 1.4.0, named in the manifest; never against 2.0.0.
    (The 1.0.0 pack against 1.1.0 is tests/unit/test_query_semantics.py's, and the 1.1.0 pack against 1.2.0
    tests/integration/test_vocabulary.py's.)"""
    newer = meta_model_copy(tmp_path / "minor", lambda m: m.update(version="1.4.0"))
    code, out, err = compile_in(capsys, CLIENTS, tmp_path / "out", meta=newer)
    assert code == 0, err
    assert manifest_of(Path(out.strip()))["meta_model_version"] == "1.4.0"
    major = meta_model_copy(tmp_path / "major", lambda m: m.update(version="2.0.0"))
    code, _, err = compile_in(capsys, CLIENTS, tmp_path / "out2", meta=major)
    assert code == 2 and ("S03", "pack.yaml:manifest.platform.meta_model") in [(r, p) for r, p, _ in findings(err)], err


def test_a_meta_model_type_this_compiler_cannot_fill_stops_the_compile(tmp_path, capsys):
    def change(m):
        m["version"] = "1.4.0"
        m["node_types"]["Hypothesis"] = {"loaded_as": "Hypothesis", "key": "id", "layer": "curated", "origin": "pack",
                                         "properties": {"id": "STRING"}}
    code, _, err = compile_in(capsys, CLIENTS, tmp_path / "out", meta=meta_model_copy(tmp_path, change))
    assert code == 2 and any(r == "S03" and "Hypothesis" in m for r, _, m in findings(err)), err


EXTENSIONS = [{"node_type": "x_meridian_Desk", "key": "id", "properties": {"id": "STRING", "floor": "INT64"}},
              {"edge_type": "x_meridian_SITS_IN", "from": "Table", "to": "x_meridian_Desk", "properties": {"since": "STRING"}},
              {"property": "x_meridian_owner", "extends": "Term", "type": "STRING"}]


def with_extensions(p: dict, l: dict) -> None:
    p["schema"]["extensions"] = [dict(e) for e in EXTENSIONS]
    p["glossary"][0]["x_meridian_owner"] = "Finance Desk"


def test_an_extension_adds_namespaced_types_and_properties_and_nothing_else(tmp_path, artifact):
    from kge.graph import schema
    art = printed(run(*compile_args(tmp_path / "out", scratch(tmp_path, with_extensions))))
    assert art.name != artifact.name
    ddl = (art / "ddl" / "kuzu.cypher").read_text().splitlines()
    desk = "CREATE NODE TABLE x_meridian_Desk(id STRING, floor INT64, PRIMARY KEY (id));"
    sits = "CREATE REL TABLE x_meridian_SITS_IN(FROM Tbl TO x_meridian_Desk, since STRING);"
    meta = [f"{s};".replace("CREATE NODE TABLE Term(name STRING, domain STRING, steward STRING, definition STRING, id STRING, ",
                            "CREATE NODE TABLE Term(name STRING, domain STRING, steward STRING, definition STRING, id STRING, "
                            "x_meridian_owner STRING, ") for s in schema.DDL]
    assert sorted(ddl) == sorted(meta + [desk, sits]) and any("x_meridian_owner" in s for s in meta)
    first_rel = next(i for i, s in enumerate(ddl) if s.startswith("CREATE REL"))
    assert ddl.index(desk) < first_rel < ddl.index(sits), "node tables first, then rel tables"
    header, *terms = rows(art, "data/nodes/Term.csv")
    assert header[-1] == "x_meridian_owner"
    first = yaml.safe_load((CLIENTS / CLIENT / "pack.yaml").read_text())["glossary"][0]["name"]
    assert {r[0]: r[-1] for r in terms if r[-1] != NULL} == {first: "Finance Desk"}
    assert rows(art, "data/nodes/x_meridian_Desk.csv") == [["id", "floor"]]
    assert rows(art, "data/edges/x_meridian_SITS_IN__Tbl__x_meridian_Desk.csv") == [["from", "to", "since"]]
    gm = yaml.safe_load((art / "graph-model.yaml").read_text())
    assert gm["extensions"] == EXTENSIONS and list(gm["node_types"])[-1] == "x_meridian_Desk"
    assert [p["name"] for p in gm["node_types"]["Term"]["properties"]][-1] == "x_meridian_owner"


def written(tmp_path: Path, extensions: str) -> Path:
    """A scratch pack whose schema.extensions is the YAML text given, as a person writes it: not through safe_dump,
    which quotes what YAML would read as true or false."""
    clients = scratch(tmp_path)
    path = clients / CLIENT / "pack.yaml"
    text = path.read_text()
    assert text.count("\n  extensions: []\n") == 1
    path.write_text(text.replace("\n  extensions: []\n", "\n  extensions:\n" + extensions))
    return clients


def test_an_extension_written_as_yaml_reads_as_written(tmp_path, capsys):
    """A property extension names the type it extends with extends, which YAML reads as text. A key YAML reads as true
    or false (on, off, yes or no, unquoted) is named for what it is, and the finding says to quote it."""
    clients = written(tmp_path / "ok", "  - {property: x_meridian_owner, extends: Term, type: STRING}\n")
    art = printed(run(*compile_args(tmp_path / "out", clients)))
    gm = yaml.safe_load((art / "graph-model.yaml").read_text())
    assert gm["extensions"] == [EXTENSIONS[2]]
    assert [p["name"] for p in gm["node_types"]["Term"]["properties"]][-1] == "x_meridian_owner"
    clients = written(tmp_path / "plain", "  - {property: owner, extends: Term, type: STRING}\n")
    code, _, err = compile_in(capsys, clients, tmp_path / "out2")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == [("S04", "pack.yaml:schema.extensions[0].property")], err
    assert "x_meridian_" in err
    clients = written(tmp_path / "on", "  - {property: x_meridian_owner, on: Term, type: STRING}\n")
    code, _, err = compile_in(capsys, clients, tmp_path / "out3")
    assert code == 2 and [(r, p) for r, p, _ in findings(err)] == [("S01", "pack.yaml:schema.extensions[0].True")], err
    assert "quote it" in err and "'on'" in err, err


def load(art: Path, graph: Path):
    """A fresh graph on this run's store: the artifact's DDL, then COPY of each file load_order stages for copy."""
    from kge.adapters.graphstore import connect, open_db
    db = open_db(graph)
    conn = connect(db)
    for stmt in (art / "ddl" / "kuzu.cypher").read_text().splitlines():
        conn.execute(stmt.removesuffix(";"))
    for step in manifest_of(art)["load_order"]:
        if step["stage"] == "copy":
            ends = f", from='{step['from']}', to='{step['to']}'" if "from" in step else ""
            conn.execute(f"COPY {step['table']} FROM '{(art / step['path']).as_posix()}' "
                         f"(header=true, parallel=false, null_strings=['\\\\N']{ends})")
    return db, conn


def test_the_artifact_loads_on_this_store_and_keeps_empty_apart_from_null(tmp_path):
    """With null_strings, both stores keep '' as '' and read \\N as NULL in a text column; any other column reads an
    empty field as NULL. One column the catalogue gave no comment loads with an empty description, not a NULL."""
    def edit(p, l):
        column(l, "retail", "branch", "region_code")["description"] = None
    art = printed(run(*compile_args(tmp_path / "out", scratch(tmp_path, edit))))
    man = manifest_of(art)
    db, conn = load(art, tmp_path / "graph")

    def one(q: str):
        return conn.execute(q).get_all()[0][0]
    try:
        for t, n in man["counts"]["nodes"].items():
            assert one(f"MATCH (x:{t}) RETURN count(x)") == n, t
        for t, n in man["counts"]["edges"].items():
            assert one(f"MATCH ()-[r:{t}]->() RETURN count(r)") == (0 if t == "DEFINES" else n), t
        assert one("MATCH (c:Col) WHERE c.description IS NULL RETURN count(c)") == 0
        assert conn.execute("MATCH (c:Col) WHERE c.description = '' RETURN c.id").get_all() == [["retail.branch.region_code"]]
        assert one("MATCH (c:Col) WHERE c.id = 'corporate.contact.role_code' RETURN c.description") == "TREASURER, CFO, OPERATIONS"
        assert one("MATCH (c:Col) WHERE c.grain = '' RETURN count(c)") == 912 - 12
        assert one("MATCH (c:Col) WHERE c.is_key RETURN count(c)") == 74
        assert one("MATCH (m:Measure) WHERE m.snapshot IS NULL AND m.population IS NULL RETURN count(m)") == 12
        assert one("MATCH ()-[p:PERIOD_COLUMN]->() WHERE p.rollup_table IS NULL RETURN count(p)") == 23
        assert one("MATCH (k:Calendar) WHERE k.id = 'fiscal_april' RETURN k.year_start_month") == 4
        assert one("MATCH ()-[m:MAPS_TO]->() WHERE m.certified AND m.confidence = 1.0 RETURN count(m)") == 24
        assert one("MATCH (t:Term) WHERE t.id IS NULL OR t.id = '' RETURN count(t)") == 0
        groups = conn.execute("MATCH (:Alias)-[:ALIAS_OF]->(x) RETURN label(x) AS l, count(*) AS n ORDER BY l").get_all()
        assert groups == [["Col", 103], ["Entity", 3], ["Term", 30], ["Unit", 6]]
    finally:
        conn.close()
        db.close()


def test_an_extended_artifact_loads_on_this_store(tmp_path):
    art = printed(run(*compile_args(tmp_path / "out", scratch(tmp_path, with_extensions))))
    db, conn = load(art, tmp_path / "graph")
    try:
        assert conn.execute("MATCH (t:Term) WHERE t.x_meridian_owner IS NOT NULL RETURN t.x_meridian_owner").get_all() == [["Finance Desk"]]
        assert conn.execute("MATCH (d:x_meridian_Desk) RETURN count(d)").get_all() == [[0]]
    finally:
        conn.close()
        db.close()


def recipe(make: str, target: str) -> str:
    return make.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]


def test_make_compile_prints_the_artifact_path(tmp_path, artifact, monkeypatch):
    """The profile names the client, the clients folder and the artifacts folder (#39 item 9), so the Makefile names
    none of them. The printed path may be relative when the scratch folder is inside the demo."""
    from tests.scratch import write_registry
    make = (DEMO / "Makefile").read_text()
    assert re.search(r"^\.PHONY:.*\bcompile\b", make, re.M)
    assert recipe(make, "compile").split() == ["$(KGE)", "compile", "$(KGE_ARGS)"], recipe(make, "compile")
    from tests.unit.test_cli import plan
    assert plan(monkeypatch, "compile") == (0, [["kge.compiler.compile"]])
    registry = write_registry(tmp_path)
    r = subprocess.run(["make", "-s", "compile", f"PY={sys.executable}"], capture_output=True, text=True, cwd=DEMO, timeout=120,
                       env={**os.environ, "PROFILES": str(registry), "PROFILE": ""})
    assert (DEMO / printed(r)).resolve() == tmp_path / "artifacts" / CLIENT / artifact.name


def test_the_readme_says_what_the_compiler_checks_and_writes():
    readme = (DEMO / "README.md").read_text()
    para = " ".join(readme.split("**The compiler.**", 1)[1].split("\n\n", 1)[0].split())
    for needle in ("`make compile`", "`kge/compiler/compile.py`", "`meta-model.yaml`", "`clients/meridian/pack.yaml`",
                   "`clients/meridian/pack.lock`", "`<rule> <path>: <message>`", "exits 2 and writes nothing",
                   "`build/artifacts/meridian/<model_hash>/`", "`graph-model.yaml`", "`ddl/kuzu.cypher`", "`data/nodes/<Type>.csv`",
                   "`data/edges/<EDGE>__<From>__<To>.csv`", "`access/roles.json`", "`manifest.json`", "`x_meridian_`",
                   "pytest tests/unit/test_compiler.py -q", "S12", "S13", "S15", "S16",
                   "stops only the checks that read it", "over the rows of the measure's own table",
                   "so it reads no file and no other table (S08)"):
        assert needle in para, needle
    rng = yaml.safe_load((DEMO / "clients" / CLIENT / "pack.yaml").read_text())["manifest"]["platform"]["meta_model"]
    assert f'such as `meta_model: "{rng}"` (S03)' in para, rng
    running = readme.split("## Running it", 1)[1].split("\n## ", 1)[0]
    assert "make compile" in running
