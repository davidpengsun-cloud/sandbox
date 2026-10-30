"""The artifact loader: make graph checks the lock, compiles the pack and loads what the compile printed.

    .venv/bin/python -m pytest tests/integration/test_artifact_load.py -q

kge/graph/load.py --artifact <dir> checks the artifact whole before it touches the
graph: every file's sha256 against the manifest, no file the manifest does not
list, the model hash, a meta-model version this platform loads, the encoder's
embedding dimension and the deployment's client. Anything wrong, it stops (exit
2), saying what, and the graph on disk stays as it was. Then a fresh graph: the
artifact's DDL, its files copied in load order, nodes then edges; the landed
documents with what certification landed for them (fail closed), and the
DEFINES edges of those that landed (S12: a document the pack names that never
landed is a warning and no edge); the access mirror, the deployment's
identities bound to the artifact's role templates, also written to
principals.json in the profile's run state for kge/services/identity/service.py; and the
GraphModel node naming the artifact. It prints the counts it printed before, and the model hash.
tests/integration/test_parity.py holds the graph it loads to the one the per-row loader,
kge/graph/legacy.py, builds on the same build.

Loads into scratch builds, on the store this run is on: run the suite once per
store. Opens no demo graph.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kge.landing.zone import Zone
from kge.adapters.model import DIM
from tests.scratch import BUILD, CLIENT, DEMO, compile_pack, graph_of, load_artifact, run_of, scratch_build, script, write_registry

COUNTS = ["units", "tables", "columns", "joins", "terms", "mappings", "no_join", "principals", "documents", "ai_ready", "defines",
          "curated_tables", "contexts", "declared_joins", "matches_for_review", "release", "client", "model_hash"]


def query(build: Path, cypher: str) -> list[list]:
    from kge.adapters.graphstore import connect, open_db
    db = open_db(graph_of(build))
    conn = connect(db)
    try:
        return conn.execute(cypher).get_all()
    finally:
        conn.close()
        db.close()


def manifest_of(folder: Path) -> dict:
    return json.loads((folder / "manifest.json").read_text())


def platform_version() -> tuple[int, int, int]:
    return tuple(int(x) for x in yaml.safe_load((DEMO / "meta-model.yaml").read_text())["version"].split("."))


@pytest.fixture(scope="module")
def loaded(tmp_path_factory, compiled):
    """A build with the demo's landing and no graph, loaded from the committed pack's artifact as make graph loads it."""
    build = scratch_build(tmp_path_factory.mktemp("loaded") / "build", graph=False)
    r = load_artifact(build, compiled)
    assert r.returncode == 0, r.stderr
    return build, r


def test_it_prints_the_counts_it_printed_before_and_the_model_hash(loaded, compiled):
    build, r = loaded
    counts = json.loads(r.stdout)
    assert list(counts) == COUNTS
    assert {k: counts[k] for k in COUNTS[:8] + ["defines"]} == {"units": 4, "tables": 59, "columns": 899, "joins": 44, "terms": 49,
                                                                "mappings": 24, "no_join": 5, "principals": 10, "defines": 12}
    assert {k: counts[k] for k in ("curated_tables", "contexts", "declared_joins")} == {"curated_tables": 1, "contexts": 3, "declared_joins": 15}
    release = Zone(BUILD / "landing", CLIENT).open()
    assert counts["matches_for_review"] == sum(len(r["matches"]) for r in release.reviews().values()) > 0
    assert counts["documents"] == len(release.documents()) and 0 < counts["ai_ready"] < counts["documents"]
    assert (counts["release"], counts["client"], counts["model_hash"]) == (release.id, CLIENT, compiled.name)
    assert "Traceback" not in r.stderr and "WARNING" not in r.stderr, r.stderr


def test_the_graph_model_node_names_the_artifact_it_was_loaded_from(loaded, compiled):
    build, _ = loaded
    man = manifest_of(compiled)
    assert query(build, "MATCH (m:GraphModel) RETURN m.id, m.client, m.pack_version, m.meta_model_version") == [
        [man["model_hash"], CLIENT, man["pack"]["version"], man["meta_model_version"]]]
    assert len(query(build, "MATCH (r:Release) RETURN r.id")) == 1


def test_every_file_the_artifact_stages_is_loaded_row_for_row(loaded, compiled):
    """Nodes and edges copied in load order, each pair of a rel group to its own ends; DEFINES once its documents landed."""
    build, _ = loaded
    for step in manifest_of(compiled)["load_order"]:
        if "from" in step:
            n = query(build, f"MATCH (:{step['from']})-[r:{step['table']}]->(:{step['to']}) RETURN count(r)")[0][0]
        else:
            n = query(build, f"MATCH (x:{step['table']}) RETURN count(x)")[0][0]
        assert n == step["rows"], step
    assert query(build, "MATCH (t:Term) WHERE t.id IS NULL OR t.id = '' RETURN count(t)") == [[0]], "each term with its id slug"
    assert query(build, "MATCH (p:Policy)-[:GOVERNS]->(c:Col) WHERE p.kind = 'restricted_attribute' RETURN count(c)") == [[25]]


def test_the_access_mirror_is_the_deployment_s_identities_bound_to_the_artifact_s_roles(loaded, compiled):
    """principals.json in the profile's run state, which kge/services/identity/service.py reads, is written as the estate writes it,
    never through a link."""
    build, _ = loaded
    ids = yaml.safe_load((DEMO / "deploy" / CLIENT / "identities.yaml").read_text())["principals"]
    roles = {r["id"]: r for r in json.loads((compiled / "access" / "roles.json").read_text())["roles"]}
    assert sorted(query(build, "MATCH (p:Principal) RETURN p.id, p.role")) == sorted([b["principal"], roles[b["role"]]["label"]] for b in ids)
    reads = query(build, "MATCH (p:Principal)-[:CAN_READ]->(u:Unit) RETURN p.id, u.id")
    assert sorted(u for p, u in reads if p == "analyst_retail") == ["retail"] and not [u for p, u in reads if p == "pipeline_tagger"]
    assert sorted(u for p, u in reads if p == "analyst_enterprise") == ["corporate", "retail", "sme", "wealth"]
    writes = query(build, "MATCH (p:Principal)-[:CAN_WRITE]->(t:Term) RETURN p.id, count(t)")
    assert sorted(writes) == [["admin", 49], ["business_team", 49], ["steward", 49], ["steward_glossary", 49]]
    from kge.pipeline.access import process_vocabulary
    processes = process_vocabulary(yaml.safe_load((DEMO / "meta-model.yaml").read_text())["node_types"]["Process"]["constants"])
    assert sorted(query(build, "MATCH (x:Process) RETURN x.id, x.description")) == sorted(map(list, processes.items()))
    assert sorted(query(build, "MATCH (p:Principal)-[:MAY_PERFORM]->(x:Process) WHERE p.id = 'pipeline_tagger' RETURN x.id")) == [["tagging"]]
    assert sorted(query(build, "MATCH (p:Principal)-[:MAY_PERFORM]->(x:Process) RETURN p.id, x.id")) == sorted(
        [b["principal"], process] for b in ids for process in roles[b["role"]]["processes"])
    written = run_of(build) / "principals.json"
    assert not written.is_symlink() and written.read_text() == (BUILD / "principals.json").read_text()


def test_identity_reads_the_principals_the_load_wrote(loaded):
    build, _ = loaded
    r = script("kge/services/identity/cli.py", build=build)
    assert r.returncode == 0, r.stderr
    assert sorted(line.split()[0] for line in r.stdout.splitlines()) == sorted(p["principal"] for p in json.loads((run_of(build) / "principals.json").read_text()))
    assert not re.search(r"^\s*(?:from|import)\s+(?:kge\.)?estate\b", (DEMO / "kge" / "services" / "identity" / "service.py").read_text(), re.M)


def scratch_pack(tmp_path: Path, edit) -> Path:
    """A clients folder holding a copy of the client's pack, edited, and its lock."""
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    pack = yaml.safe_load((clients / CLIENT / "pack.yaml").read_text())
    edit(pack)
    (clients / CLIENT / "pack.yaml").write_text(yaml.safe_dump(pack, sort_keys=False, allow_unicode=True))
    return clients


def test_a_document_the_pack_names_that_never_landed_is_a_warning_and_no_edge(tmp_path):
    """S12, which the compiler cannot check, is checked at load: the documents are the landing's."""
    def edit(p):
        next(g for g in p["glossary"] if g["name"] == "Revenue")["defined_by"].append("MD-NEVER-LANDED")
    art = compile_pack(tmp_path / "out", scratch_pack(tmp_path, edit))
    build = scratch_build(tmp_path / "build", graph=False)
    r = load_artifact(build, art)
    assert r.returncode == 0, r.stderr
    warned = [line for line in r.stderr.splitlines() if "MD-NEVER-LANDED" in line]
    assert len(warned) == 1 and "S12" in warned[0] and "WARNING" in warned[0] and "Revenue" in warned[0], r.stderr
    assert json.loads(r.stdout)["defines"] == 12
    assert query(build, "MATCH (d:Document)-[:DEFINES]->(t:Term) WHERE t.name = 'Revenue' RETURN d.id") == [["MD-REVENUE"]]
    assert query(build, "MATCH (d:Document) WHERE d.id = 'MD-NEVER-LANDED' RETURN count(d)") == [[0]]


def rehash(folder: Path, man: dict) -> None:
    """man written as folder's manifest."""
    (folder / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")


def edited(field: str, value):
    def edit(folder: Path) -> None:
        man = manifest_of(folder)
        keys = field.split(".")
        at = man
        for k in keys[:-1]:
            at = at[k]
        at[keys[-1]] = value(at[keys[-1]]) if callable(value) else value
        rehash(folder, man)
    return edit


def newer_minor(_):
    major, minor, _patch = platform_version()
    return f"{major}.{minor + 1}.0"


def outside(folder: Path) -> None:
    man = manifest_of(folder)
    man["files"]["../outside.csv"] = man["files"]["data/nodes/Unit.csv"]
    rehash(folder, man)


REFUSED = [
    ("a file whose bytes changed", lambda f: (f / "data" / "nodes" / "Term.csv").write_text((f / "data" / "nodes" / "Term.csv").read_text() + "X,y,z,w,x\n"),
     "data/nodes/Term.csv"),
    ("a listed file that is missing", lambda f: (f / "data" / "edges" / "NO_JOIN__Unit__Unit.csv").unlink(), "data/edges/NO_JOIN__Unit__Unit.csv"),
    ("a file the manifest does not list", lambda f: (f / "data" / "nodes" / "Extra.csv").write_text("id\n"), "data/nodes/Extra.csv"),
    ("a listed path outside the artifact", outside, "../outside.csv"),
    ("a model hash that is not its files'", edited("model_hash", "0" * 64), "model hash"),
    ("no manifest", lambda f: (f / "manifest.json").unlink(), "manifest.json"),
    ("a manifest that is not JSON", lambda f: (f / "manifest.json").write_text("{not json"), "manifest.json"),
    ("another format", edited("format", "graph-model/v9"), "graph-model/v9"),
    ("a newer major meta-model", edited("meta_model_version", lambda v: f"{platform_version()[0] + 1}.0.0"), "meta-model"),
    ("a newer minor meta-model", edited("meta_model_version", newer_minor), "meta-model"),
    ("a meta-model version that is not semver", edited("meta_model_version", "one"), "meta-model"),
    ("another embedding dimension", edited("params.embedding_dim", DIM * 2), f"embedding dimension {DIM * 2}"),
    ("another client's artifact", edited("client", "acme"), "acme"),
]


@pytest.mark.parametrize("edit,said", [(e, s) for _, e, s in REFUSED], ids=[n for n, _, _ in REFUSED])
def test_an_artifact_that_is_not_whole_or_not_this_deployment_s_is_refused_and_nothing_is_touched(tmp_path, compiled, edit, said):
    art = tmp_path / "artifact"
    shutil.copytree(compiled, art)
    edit(art)
    build = scratch_build(tmp_path / "build")
    before = graph_files(build)
    r = load_artifact(build, art)
    assert r.returncode == 2 and said in r.stderr and "Nothing was loaded" in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert graph_files(build) == before, "the graph and the access mirror are as they were"


def graph_files(build: Path, graph: Path | None = None) -> dict:
    """The graph's files, with its sidecars and its LOADED marker, and the access mirror the load writes (principals.json in
    the run state): each one's inode and time, as a load that wrote nothing leaves them."""
    from kge.adapters.graphstore import sidecars
    graph = graph or graph_of(build)
    held = [graph, *sidecars(graph), graph.parent / "LOADED", graph.parent.parent / "run" / "principals.json"]
    return {p.name: (os.lstat(p).st_ino, os.lstat(p).st_mtime_ns) for p in held if p.exists()}


def test_an_artifact_of_the_first_minor_of_the_platform_s_major_loads(tmp_path, compiled):
    """A minor version only adds, so an artifact compiled against an older minor of the same major still loads."""
    art = tmp_path / "artifact"
    shutil.copytree(compiled, art)
    edited("meta_model_version", f"{platform_version()[0]}.0.0")(art)
    r = load_artifact(scratch_build(tmp_path / "build", graph=False), art)
    assert r.returncode == 0, r.stderr


def test_the_artifact_must_be_the_profile_s_client_s(tmp_path, compiled):
    build = scratch_build(tmp_path / "build")
    graph = graph_of(build)
    before = graph_files(build, graph)
    write_registry(build, client="acme")
    r = load_artifact(build, compiled)
    assert r.returncode == 2 and "this profile is acme's" in r.stderr and CLIENT in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert graph_files(build, graph) == before and not (build / "stores" / "acme").exists()


@pytest.mark.parametrize("identities,said", [({"client": "acme", "principals": [{"principal": "a", "role": "admin"}]}, "acme"),
                                             ({"client": CLIENT, "principals": [{"principal": "a", "role": "no_such_role"}]}, "no_such_role"),
                                             ({"client": CLIENT}, "principals"),
                                             ({"client": CLIENT, "asker": "nobody", "principals": [{"principal": "a", "role": "analyst_enterprise"}]},
                                              "name nobody as the identity a command asks as"),
                                             ({"client": CLIENT, "asker": "a", "principals": [{"principal": "a", "role": "pipeline_tagger"}]},
                                              "name a as the identity a command asks as, which is not one of their querying identities (none)")],
                         ids=["another client's", "an unknown role", "no principals", "an asker it does not bind",
                              "an asker that does not query"])
def test_identities_that_are_not_this_deployment_s_or_name_no_role_of_the_artifact_are_refused(tmp_path, compiled, identities, said):
    path = tmp_path / "identities.yaml"
    path.write_text(yaml.safe_dump(identities))
    build = scratch_build(tmp_path / "build")
    write_registry(build, identities=path)
    before = graph_files(build)
    r = load_artifact(build, compiled)
    assert r.returncode == 2 and said in r.stderr and "Nothing was loaded" in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert graph_files(build) == before


def test_with_no_artifact_it_stops_naming_make_graph(tmp_path):
    build = scratch_build(tmp_path / "build")
    before = graph_files(build)
    r = script("kge/graph/load.py", build=build)
    assert r.returncode == 2 and "--artifact" in r.stderr and "make graph" in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert graph_files(build) == before


def recipe(make: str, target: str) -> str:
    """The target's recipe as make runs it: each canned recipe it expands (define NAME ... endef, as $(NAME)) put in
    its place, so the steps a target shares with others (LOAD_STEPS) are read where they run."""
    body = make.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    for name, text in re.findall(r"^define (\w+)\n(.*?)\nendef$", make, re.M | re.S):
        body = body.replace(f"$({name})", text)
    return body


def test_make_graph_checks_the_lock_compiles_the_pack_and_loads_what_the_compile_printed():
    """The artifact is then released --ungated: make graph is the developer's bootstrap, without the onboarding gate
    (make margin HASH=, make sign-off), and it says so."""
    from tests.unit.test_cli import planned
    make = (DEMO / "Makefile").read_text()
    for target in ("graph", "$(GRAPH)"):
        assert recipe(make, target).split() == ["$(KGE)", "graph", "$(KGE_ARGS)"], target
    dry = planned("graph")
    steps = [dry.index(s) for s in ("kge.compiler.lock --check", "kge.compiler.compile", "kge.graph.load --artifact",
                                    "kge.pipeline.run --hash", "--mark-loaded")]
    assert steps == sorted(steps) and dry.rstrip().endswith("--ungated"), dry
    assert "--client" not in dry and "--build" not in dry, "each step reads the profile's paths, PROFILE exported"


def test_make_graph_on_a_build_loads_the_compiled_pack(tmp_path, compiled):
    build = scratch_build(tmp_path / "build", graph=False)
    registry = write_registry(build, artifacts=tmp_path / "artifacts")
    env = {k: v for k, v in os.environ.items() if k != "PROFILE"} | {"PROFILES": str(registry)}
    r = subprocess.run(["make", "-s", "graph", f"PY={sys.executable}"],
                       capture_output=True, text=True, cwd=DEMO, env=env, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("lock: clients/meridian/pack.lock holds the catalogue"), r.stdout
    counts, _ = json.JSONDecoder().raw_decode(r.stdout, r.stdout.index("{"))
    assert counts["model_hash"] == compiled.name and (tmp_path / "artifacts" / CLIENT / compiled.name / "manifest.json").is_file()
    assert os.readlink(tmp_path / "artifacts" / CLIENT / "current") == compiled.name, "released"
    assert "released without the onboarding gate" in r.stdout, "the developer's bootstrap, and it says so"
    assert query(build, "MATCH (m:GraphModel) RETURN m.id") == [[compiled.name]] and query(build, "MATCH (c:Chunk) RETURN count(c)")[0][0] > 0


def test_the_readme_says_make_graph_loads_the_compiled_pack():
    """The loader prints the counts it printed before #39 (the units' tables and columns), then pack 1.1.0's (#39 item
    7c: the curated sources' tables, the contexts and the declared joins), then since item 7d the cross-reference's
    matches held for a steward's review, then the release, the client and the model hash, in COUNTS' order."""
    readme = (DEMO / "README.md").read_text()
    graph = " ".join(readme.split("## The graph", 1)[1].split("\n## ", 1)[0].split())
    for needle in ("`make graph`", "`kge/graph/load.py --artifact`", "`kge/graph/legacy.py`", "pytest tests/integration/test_parity.py -q", "pytest tests/integration/test_artifact_load.py -q", "`GraphModel`",
                   "S12", "`build/stores/meridian/run/principals.json`", "`deploy/meridian/identities.yaml`", "`null_strings`"):
        assert needle in graph, needle
    assert "does not load the artifact yet" not in graph and "not filled yet" not in graph
    assert "still builds the physical layer from the landed catalogue" not in graph
    assert "The graph's physical layer is the compiled lock's" in graph
    running = " ".join(readme.split("## Running it", 1)[1].split("\n## ", 1)[0].split())
    assert "checks the lock, compiles the pack and loads" in running
    printed = graph.split("It prints the counts it always printed", 1)[1].split(". ", 1)[0]
    said = [printed.index(s) for s in ("definitions", "the curated source's tables", "the contexts", "the declared joins",
                                       "`matches_for_review`", "the release, the client and the model hash")]
    assert said == sorted(said), printed
    assert COUNTS[COUNTS.index("declared_joins") + 1:] == ["matches_for_review", "release", "client", "model_hash"]
