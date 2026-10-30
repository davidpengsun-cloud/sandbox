"""Preset parity (kge/eval/parity.py, make parity): the 183 results beside the snapshot frozen before #39.

    .venv/bin/python -m pytest tests/integration/test_parity.py -q

The three presets on the 45 golden and 16 held-out items keep the same pass,
behaviour, figure, parts and refused joins as in the snapshot taken on Kuzu at
4d9d5c9 (clients/meridian/baselines/parity-4d9d5c9.json). It holds on
Ladybug. A move is named, result by result, and
fails, unless the owner stated it; the snapshot is never rewritten. Since #39
item 7d two results move as the owner stated
(clients/meridian/baselines/parity-stated.json): G021's and G030's refused
joins on the graph preset no longer list retail-wealth, under Option B.

The graph the compiled artifact loads (kge/graph/load.py --artifact) is the graph
the per-row loader built before it (kge/graph/legacy.py, kept as the parity
oracle), on the same build: every row of each of the 9 node tables and 16 rel
tables the graph had before #39, every property, '' told apart from NULL. It
holds as loaded, and again once the pipeline has run on each. The per-row
loader loads the units' tables only: the curated source pack 1.1.0 adds (the
client cross-reference, #39 item 7c) has tables of its own, which the two
graphs are compared without; nor did it load the cross-reference's matches
certification lands for a steward (#39 item 7d), tags on no chunk, which are
set aside too.

Opens the demo's graph through the eng fixture; the end-to-end check runs on a
scratch copy, and the two loaders on scratch builds. Stop the API first.
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.services.ask.engine import runtrace
from kge.eval import parity
from tests import before
from tests.scratch import BUILD, graph_of, load_artifact, registry, running_as, scratch_build, script

DEMO = Path(__file__).resolve().parents[2]
SNAPSHOT = DEMO / "clients" / "meridian" / "baselines" / "parity-4d9d5c9.json"
STATED = DEMO / "clients" / "meridian" / "baselines" / "parity-stated.json"


def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text())


def test_the_snapshot_holds_the_183_results_in_figures_and_ids_only():
    snap = snapshot()
    assert snap["sets"] == {"golden": 45, "heldout": 16} and snap["arms"] == ["schema", "catalog", "graph"]
    assert len(snap["results"]) == 183 and snap["principal"] == "analyst_enterprise"
    assert all(set(r) == {"kind", *parity.FIELDS} for r in snap["results"].values())
    assert "4d9d5c9" in snap["taken_on"] and "Kuzu 0.11.3" in snap["taken_on"]
    assert not re.search(r"[0-9a-f]{16,}", SNAPSHOT.read_text()), "no hex digests in a committed file"


def test_the_183_are_unchanged_on_ladybug_but_the_two_the_owner_stated(eng):
    snap = snapshot()
    now = parity.results(eng, BUILD, snap["principal"])
    moved, as_stated = parity.compare(snap["results"], now, parity.read_stated(STATED))
    assert moved == [], "\n".join(moved)
    option_b = [line.split(":", 3)[:3] for line in as_stated if "Option B" in line]
    assert option_b == [["golden", "G021", "graph joins_refused"], ["golden", "G030", "graph joins_refused"]]
    assert all("pack 1.5.0" in line and line.split(":", 3)[2].split()[0] in ("schema", "catalog") for line in as_stated if "Option B" not in line), \
        "pack 1.5.0's physical names move the graph-off presets alone"
    assert all("retail-wealth" not in now[f"golden:{q}:graph"]["joins_refused"] for q in ("G021", "G030"))
    assert len(now) == 183


def test_the_owner_s_stated_changes_are_g021_and_g030_dropping_retail_wealth_and_say_why():
    stated = parity.read_stated(STATED)
    snap = snapshot()["results"]
    option_b = {k: c for k, c in stated.items() if "Option B" in c["why"]}
    assert set(option_b) == {("golden:G021:graph", "joins_refused"), ("golden:G030:graph", "joins_refused")}
    assert all(k[0].split(":")[2] in ("schema", "catalog") and "pack 1.5.0" in c["why"] for k, c in stated.items() if k not in option_b)
    for (key, _), change in option_b.items():
        assert change["to"] == [p for p in snap[key]["joins_refused"] if p != "retail-wealth"], key
        assert "#39" in change["why"] and "Option B" in change["why"], change["why"]


def test_a_moved_result_is_named_field_by_field():
    then = {"golden:G001:graph": {"kind": "within", "ok": True, "behaviour": "figure", "figure": 1.0, "parts": None, "joins_refused": None},
            "golden:G021:graph": {"kind": "cross", "ok": True, "behaviour": "parts", "figure": None, "parts": {"retail": 1.0},
                                  "joins_refused": ["retail-sme", "retail-wealth"]}}
    now = json.loads(json.dumps(then))
    now["golden:G001:graph"]["figure"] = 2.0
    now["golden:G021:graph"]["joins_refused"] = ["retail-sme"]
    now["heldout:H001:graph"] = then["golden:G001:graph"]
    moved, as_stated = parity.compare(then, now)
    assert moved == ["golden:G001:graph figure: 1.0 → 2.0",
                     'golden:G021:graph joins_refused: ["retail-sme", "retail-wealth"] → ["retail-sme"]',
                     "heldout:H001:graph: run now and not in the snapshot"] and as_stated == []
    del now["heldout:H001:graph"]
    stated = {("golden:G021:graph", "joins_refused"): {"to": ["retail-sme"], "why": "the owner's update"}}
    moved, as_stated = parity.compare(then, now, stated)
    assert moved == ["golden:G001:graph figure: 1.0 → 2.0"]
    assert as_stated == ['golden:G021:graph joins_refused: ["retail-sme", "retail-wealth"] → ["retail-sme"], as stated: the owner\'s update']
    moved, _ = parity.compare(then, then, stated)
    assert moved == ['golden:G021:graph joins_refused: ["retail-sme", "retail-wealth"] → ["retail-sme", "retail-wealth"]; '
                     'the stated change is to ["retail-sme"]'], "a stated change that did not happen fails too"


def test_a_stated_change_names_its_result_field_value_and_why(tmp_path):
    path = tmp_path / "stated.json"
    path.write_text(json.dumps({"changes": [{"result": "golden:G021:graph", "field": "joins_refused", "to": ["retail-sme"]}]}))
    try:
        parity.read_stated(path)
    except ValueError as exc:
        assert "why" in str(exc)
    else:
        raise AssertionError("a stated change with no reason was taken")
    path.write_text(json.dumps({"changes": [{"result": "golden:G021:graph", "field": "joins_refused", "to": ["retail-sme"], "why": "asked"}]}))
    assert parity.read_stated(path) == {("golden:G021:graph", "joins_refused"): {"to": ["retail-sme"], "why": "asked"}}


def no_graph(dest: Path) -> Path:
    """A copy of the build with its estate and sets and no graph."""
    return scratch_build(dest, graph=False)


def test_it_cannot_check_with_no_snapshot_or_no_graph_and_never_overwrites_a_snapshot(tmp_path, capsys):
    build = no_graph(tmp_path / "build")
    with running_as(build):
        assert parity.main(["--snapshot", str(tmp_path / "none.json")]) == 2
        assert f"no snapshot {tmp_path / 'none.json'}" in capsys.readouterr().err
        assert parity.main(["--snapshot", str(SNAPSHOT)]) == 2
        err = capsys.readouterr().err
        assert f"no graph {graph_of(build)}: make graph, then make pipeline, then make parity" in err, err
        assert not graph_of(build).exists(), "it never opens a graph that is not there"
        before = SNAPSHOT.read_bytes()
        assert parity.main(["--snapshot", str(SNAPSHOT), "--record", "anything"]) == 2
    assert "exists, and a snapshot is frozen" in capsys.readouterr().err and SNAPSHOT.read_bytes() == before


def test_make_parity_names_each_move_and_fails_unless_stated(tmp_path):
    """End to end on a scratch copy: a snapshot with two results changed by hand, one of them stated by the owner, beside
    the owner's stated changes make parity passes."""
    snap = snapshot()
    snap["results"]["golden:G001:graph"]["figure"] = 1.0
    snap["results"]["golden:G021:graph"]["ok"] = False
    path, stated = tmp_path / "parity.json", tmp_path / "stated.json"
    path.write_text(json.dumps(snap))
    owner = json.loads(STATED.read_text())["changes"]
    stated.write_text(json.dumps({"changes": [*owner, {"result": "golden:G021:graph", "field": "ok", "to": True, "why": "asked by the owner"}]}))
    build = scratch_build(tmp_path / "build")
    r = script("kge/eval/parity.py", "--snapshot", path, "--stated", stated, build=build)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAILED: 1 of 183 results moved:" in r.stdout and "golden:G001:graph figure: 1.0 → 13287.67" in r.stdout
    assert "stated: golden:G021:graph ok: false → true, as stated: asked by the owner" in r.stdout
    assert "stated: golden:G021:graph joins_refused:" in r.stdout and "stated: golden:G030:graph joins_refused:" in r.stdout
    assert "golden   graph    passed 44 of 45 in the snapshot, 45 now" in r.stdout


DDL = re.compile(r"^CREATE (NODE|REL) TABLE (?:IF NOT EXISTS )?(\w+)\((.*)\)$")


def legacy_tables() -> tuple[dict, dict]:
    """The tables the graph had before #39, read off their frozen DDL (kge/graph/legacy.py): each node table's key and
    properties, and each rel table's ends and properties."""
    from kge.graph import legacy
    nodes, rels = {}, {}
    for stmt in legacy.LEGACY_DDL:
        kind, name, body = DDL.match(stmt).groups()
        parts = body.split(", ")
        if kind == "NODE":
            key = re.fullmatch(r"PRIMARY KEY \((\w+)\)", parts[-1]).group(1)
            nodes[name] = (key, [x.split()[0] for x in parts[:-1]])
        else:
            src, dst = re.fullmatch(r"FROM (\w+) TO (\w+)", parts[0]).groups()
            rels[name] = (src, dst, [x.split()[0] for x in parts[1:]])
    return nodes, rels


def curated() -> set[str]:
    """The ids of the curated sources the pack declares: their tables are no unit's, and the per-row loader never had
    them."""
    import yaml
    return {s["id"] for s in yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text()).get("curated_sources", [])}


def units_only(name: str, rows: list) -> list:
    """A legacy table's rows without the curated sources' tables and columns: Tbl, Col and HAS_COLUMN key them by
    <source>.<table>; and without the matches landed for review, Tags of family xref_match."""
    if name == "Tag":
        return [r for r in rows if r[1] != "xref_match"]
    if name not in ("Tbl", "Col", "HAS_COLUMN"):
        return rows
    held = curated()
    return [r for r in rows if r[0].split(".")[0] not in held]


def rows(path: Path) -> dict[str, list]:
    """Every row of every legacy table in the graph at path, every property, sorted: a rel's rows by its ends' keys; the
    curated sources' tables left out (units_only)."""
    from kge.adapters.graphstore import connect, open_db
    nodes, rels = legacy_tables()
    queries = {name: f"MATCH (x:{name}) RETURN {', '.join(f'x.{p}' for p in props)}" for name, (_, props) in nodes.items()}
    queries |= {name: f"MATCH (a:{a})-[r:{name}]->(b:{b}) RETURN {', '.join([f'a.{nodes[a][0]}', f'b.{nodes[b][0]}', *(f'r.{p}' for p in props)])}"
                for name, (a, b, props) in rels.items()}
    db = open_db(path)
    conn = connect(db)
    try:
        return {name: units_only(name, sorted(conn.execute(q).get_all(), key=repr)) for name, q in queries.items()}
    finally:
        conn.close()
        db.close()


class Opened:
    """A graph opened directly, as the run trace reads one: a connection per read, and no write stamp."""
    writes = None

    def __init__(self, db):
        self.db = db

    def reader(self):
        from kge.adapters.graphstore import connect
        return connect(self.db)


def fingerprint(path: Path) -> dict:
    """The run trace's graph block for the graph at path."""
    from kge.adapters.graphstore import open_db
    db = open_db(path)
    try:
        return runtrace.fingerprint(Opened(db))
    finally:
        db.close()


def legacy_model_before(path: Path) -> str:
    """The legacy model digest of the graph at path over 4d9d5c9's tables and columns (tests/before.py)."""
    from kge.adapters.graphstore import open_db
    db = open_db(path)
    try:
        return before.legacy_model(Opened(db))
    finally:
        db.close()


def pipeline(build: Path) -> None:
    """make pipeline on build, in this process, on a clock that reads one fixed time: a tag records when it was made.
    kge.pipeline.state reads the clock too, since readiness is stamped where it is derived, and the run's graph handle is
    collected before the graph is read."""
    import kge.pipeline.run as run
    import kge.pipeline.state as state
    clock = SimpleNamespace(datetime=SimpleNamespace(now=lambda tz=None: dt.datetime(2026, 1, 1, tzinfo=tz)), timezone=dt.timezone)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run, "dt", clock)
        mp.setattr(state, "dt", clock)
        mp.setattr(sys, "argv", ["kge/pipeline/run.py"])
        mp.setenv("PROFILES", str(registry(build)))
        mp.setenv("PROFILE", "")
        assert run.main() == 0
    gc.collect()


@pytest.fixture(scope="module")
def two_graphs(tmp_path_factory, compiled):
    """On the same build (the demo's landing and curated files, linked): one graph from kge/graph/legacy.py and one from the
    committed pack's artifact, each read as loaded and again after the pipeline."""
    out = {}
    for side in ("legacy", "artifact"):
        build = scratch_build(tmp_path_factory.mktemp(side) / "build", graph=False)
        r = script("kge/graph/legacy.py", build=build) if side == "legacy" else load_artifact(build, compiled)
        assert r.returncode == 0, r.stderr
        graph = graph_of(build)
        loaded = rows(graph)
        fp, then = fingerprint(graph), legacy_model_before(graph)
        pipeline(build)
        out[side] = SimpleNamespace(build=build, counts=json.loads(r.stdout), loaded=loaded, fingerprint=fp, before=then,
                                    piped=rows(graph), piped_fingerprint=fingerprint(graph))
    return out


def differences(then: dict, now: dict) -> list[str]:
    out = []
    for name in sorted(set(then) | set(now)):
        a, b = then.get(name), now.get(name)
        if a != b:
            gone, new = [r for r in a or [] if r not in (b or [])], [r for r in b or [] if r not in (a or [])]
            out.append(f"{name}: {len(a or [])} rows by kge/graph/legacy.py, {len(b or [])} from the artifact; "
                       f"only legacy {gone[:3]}, only artifact {new[:3]}")
    return out


def test_the_oracle_compares_the_9_node_tables_and_16_rel_tables_the_graph_had_before_39():
    nodes, rels = legacy_tables()
    assert len(nodes) == 9 and len(rels) == 16
    assert nodes["Col"] == ("id", ["id", "unit", "tbl", "name", "data_type", "description", "is_key", "certified", "grain", "currency",
                                  "calendar", "conversion"])
    assert rels["MAPS_TO"] == ("Col", "Term", ["certified", "certified_by", "certified_on", "generator", "confidence"])


def test_the_artifact_loads_the_graph_the_per_row_loader_builds(two_graphs):
    legacy, artifact = two_graphs["legacy"], two_graphs["artifact"]
    assert differences(legacy.loaded, artifact.loaded) == []
    held = legacy.loaded
    assert [len(held[t]) for t in ("Unit", "Tbl", "Col", "Principal", "Process")] == [4, 59, 899, 10, 7], (
        "Three meta-model processes plus drafting, building, improving and accepting, in both loaders")
    assert [len(held[t]) for t in ("JOINS", "MAPS_TO", "NO_JOIN", "DEFINES")] == [44, 24, 5, 12]
    assert len(held["Term"]) == 49, "pack 1.1.0's Wealth terms and 1.3.0's AUM, in both"
    assert held["Document"] and held["SUPERSEDED_BY"] and held["CAN_READ"] and held["CAN_WRITE"] and held["MAY_PERFORM"]
    for t in ("Chunk", "Tag", "HAS_CHUNK", "MENTIONS", "TAGGED", "SUPERSEDES", "PROPOSES"):
        assert held[t] == [], f"{t} is the pipeline's"
    cells = [v for table in held.values() for r in table for v in r]
    assert "" in cells and None in cells, "'' and NULL both stand in the graph, and are told apart"
    counts = {k: artifact.counts[k] for k in legacy.counts}
    assert counts == legacy.counts, "the same counts, the release and the client among them"
    assert set(artifact.counts) - set(legacy.counts) == {"model_hash", "curated_tables", "contexts", "declared_joins", "matches_for_review"}


def test_after_the_pipeline_the_two_graphs_still_hold_the_same_rows(two_graphs):
    legacy, artifact = two_graphs["legacy"], two_graphs["artifact"]
    assert differences(legacy.piped, artifact.piped) == []
    assert legacy.piped["Chunk"] and legacy.piped["Tag"] and legacy.piped["MENTIONS"] and legacy.piped["TAGGED"]


def test_the_trace_names_the_artifact_and_both_graphs_have_the_baseline_s_legacy_digest(two_graphs, compiled):
    """The two graphs have one legacy model digest, and, the Wealth expansion's tables, columns and terms left out and
    Option B's retail-wealth fact put back (#39 items 7b and 7c, tests/before.py), it is #38's A/B baseline's model hash;
    only the artifact's graph names an artifact, its manifest's model hash. Its full model hash differs from the
    baseline's, by the new DDL."""
    base = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())["graph"]
    legacy, artifact = two_graphs["legacy"], two_graphs["artifact"]
    assert legacy.fingerprint["legacy_model"] == artifact.fingerprint["legacy_model"] != base["model"]
    assert legacy.before == artifact.before == base["model"]
    assert artifact.fingerprint["artifact"] == compiled.name == json.loads((compiled / "manifest.json").read_text())["model_hash"]
    assert legacy.fingerprint["artifact"] is None, "a graph loaded with no artifact names none"
    assert artifact.fingerprint["model"] != base["model"]
    assert legacy.piped_fingerprint["overlay"] == artifact.piped_fingerprint["overlay"]
