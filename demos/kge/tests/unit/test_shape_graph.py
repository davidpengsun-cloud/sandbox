"""Meta-model 1.6.0's question-shape types in the compiled artifact (kge/compiler/emit.py).

    .venv/bin/python -m pytest tests/unit/test_shape_graph.py -q

A scratch copy of the Thornbury sample pack, raised to 1.6.0, gains the six optional sections, written here and naming
only Thornbury's own declared ids and one synthetic question set; a synthetic preload adds one base phrase and one base
clarification rule to the platform's own. The pack is built straight into an artifact, without the checks, so what is
asserted is what emit writes: the preload's rows first, then the pack's, each structured field as canonical JSON, the
golden items a shape is tested by as ids only, and a worked example's draft flag. The artifact then loads on a scratch
graph on this run's store. The Thornbury sample pack as it is, with the platform's own preload, shows baseline v1 in
every graph: each coded phrase a Phrase row, and each that names an operation a MARKS edge, the four words the coded
table reads both ways included. No question set, fixture or data of the Meridian sample is read.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from kge.compiler import emit
from kge.compiler import pack as packs
from kge.compiler import standards
from kge.compiler.compile import VERSION
from kge.compiler.pack import canonical
from kge.graph.schema import meta_model

DEMO = Path(__file__).resolve().parents[2]
SAMPLE = "thornbury"
SET = {"name": "shapes_synthetic", "version": "1", "items": ["s1", "s2", "s3"]}


def name(i: str) -> dict:
    return {"id": i, "evidence": []}


def month(value: str) -> dict:
    return {"kind": "month", "value": value, "calendar": name("calendar")}


PREMIUM = {"kind": "measure", "id": "m", "measure": name("gross-written-premium"), "context": name("gross_written_premium"),
           "mode": "current", "filters": []}
PLAN = {"schema": "query-plan/v1", "layout": "aligned", "measures": [PREMIUM], "dimensions": [], "filters": [],
        "time": {"kind": "single", "period": month("2025-01")},
        "comparisons": [{"kind": "explicit_period", "id": "cmp", "inputs": ["m"], "other": {"kind": "single", "period": month("2025-02")},
                         "evidence": []}],
        "derived": [{"kind": "difference", "id": "d", "numerator": "cmp/m/base", "denominator": "cmp/m/other", "evidence": []}],
        "order": [], "limit": None, "obligations": []}
ONE = {**PLAN, "comparisons": [], "derived": [], "limit": 1}
WHEN = {"requires": ["comparison"], "slots": {"context": 2, "period": 2}, "constraints": [{"before": ["comparison", "period"]}]}
SECTIONS = {
    "phrases": [{"phrase": "minus", "operation": "difference"}, {"phrase": "kindly", "filler": True}],
    "recipes": [{"id": "period-difference", "blocks": [
                    {"block": "measure", "id": "m", "measure": "$measure", "context": "$context", "mode": "current", "filters": []},
                    {"block": "single", "id": "t", "period": "$period[0]"},
                    {"block": "explicit_period", "id": "cmp", "inputs": ["m"], "other": "$period[1]"},
                    {"block": "difference", "id": "d", "numerator": "cmp/m/base", "denominator": "cmp/m/other"}]},
                {"id": "one-result", "blocks": [{"block": "limit", "id": "top", "value": 1}]}],
    "shapes": [{"id": "period-difference", "label": "A measure in two periods and their difference",
                "slots": [{"name": "measure", "kind": "measure"}, {"name": "context", "kind": "context"},
                          {"name": "period", "kind": "period", "count": 2}],
                "requires": ["comparison", "difference"], "forbids": ["from_to"], "recipe": "period-difference",
                "replaces": "planshapes.compose:explicit_period", "stage": "shadow"},
               {"id": "one-result", "label": "One result", "slots": [], "requires": ["asks_one"], "recipe": "one-result",
                "stage": "specify"}],
    "shape_tests": [{"shape": "period-difference", "tests": {"set": "shapes_synthetic", "items": ["s1", "s2"]}},
                    {"shape": "period-difference", "tests": {"set": "shapes_synthetic", "items": ["s2"]}},
                    {"shape": "one-result", "tests": {"set": "shapes_synthetic", "items": ["s3"]}}],
    "examples": [{"id": "premium-january-vs-february", "shape": "period-difference",
                  "question": "gross written premium for January 2025 vs February 2025, difference", "plan": PLAN,
                  "approved_by": "underwriting-data-steward", "approved_on": "2026-11-18"},
                 {"id": "premium-one", "shape": "one-result", "question": "gross written premium for January 2025, one figure",
                  "plan": ONE}],
    "clarifications": [{"id": "contexts-or-periods-2", "when": WHEN, "message": "contexts-or-periods"}],
}
BASE_PHRASE = {"phrase": "versus", "operation": "comparison", "baseline": True}
BASE_RULE = {"id": "one-comparison-period", "when": {"requires": ["previous_period", "previous_year"]},
             "message": "one-comparison-period"}


@pytest.fixture
def preload(monkeypatch) -> standards.Preload:
    """The platform's preload with one base phrase and one base clarification rule added: what emit and the coupled groups
    read through standards.load."""
    base = standards.load()
    synthetic = base._replace(phrases={"versus": BASE_PHRASE}, clarifications={"one-comparison-period": BASE_RULE})
    monkeypatch.setattr(standards, "load", lambda *a, **k: synthetic)
    return synthetic


@pytest.fixture
def artifact(tmp_path, preload) -> emit.Artifact:
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / SAMPLE, clients / SAMPLE)
    path = clients / SAMPLE / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["manifest"]["meta_model_version"] = "1.6.0"
    data["manifest"]["platform"]["meta_model"] = "^1.6"
    data["schema"]["meta_model"] = "1.6"
    data["tests"]["sets"].append(dict(SET))
    data.update(json.loads(json.dumps(SECTIONS)))
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    pack = packs.read(clients, SAMPLE)
    assert not pack.findings, pack.findings
    return emit.build(pack, meta_model(), VERSION)


@pytest.fixture(scope="module")
def real_artifact() -> emit.Artifact:
    """The sample pack as it is, built with the platform's own preload."""
    pack = packs.read(DEMO / "clients", SAMPLE)
    assert not pack.findings, pack.findings
    return emit.build(pack, meta_model(), VERSION)


def table(art: emit.Artifact, path: str) -> list[dict]:
    import csv
    import io
    return list(csv.DictReader(io.StringIO(art.files[path].decode())))


def test_operations_are_the_preloads_closed_list_in_every_graph(artifact, preload):
    assert [r["id"] for r in table(artifact, "data/nodes/Operation.csv")] == sorted(preload.operations)


def test_phrases_are_the_preloads_then_the_packs_and_filler_marks_nothing(artifact):
    phrases = {r["phrase"]: r for r in table(artifact, "data/nodes/Phrase.csv")}
    assert phrases == {
        "versus": {"phrase": "versus", "operation": "comparison", "filler": "false", "baseline": "true", "defined_in": "platform"},
        "minus": {"phrase": "minus", "operation": "difference", "filler": "false", "baseline": "", "defined_in": "pack"},
        "kindly": {"phrase": "kindly", "operation": emit.NULL, "filler": "true", "baseline": "", "defined_in": "pack"}}
    marks = [(r["from"], r["to"]) for r in table(artifact, "data/edges/MARKS__Phrase__Operation.csv")]
    assert marks == [("minus", "difference"), ("versus", "comparison")]


DUAL = {"which": "count", "in": "in", "over": "gt", "their": "membership"}


def test_every_graph_holds_baseline_v1(real_artifact):
    phrases = {r["phrase"]: r for r in table(real_artifact, "data/nodes/Phrase.csv")}
    assert len(phrases) == 364
    assert all(r["baseline"] == "true" and r["defined_in"] == "platform" for r in phrases.values())
    for word, operation in DUAL.items():
        assert (phrases[word]["operation"], phrases[word]["filler"]) == (operation, "true"), word
    marks = {(r["from"], r["to"]) for r in table(real_artifact, "data/edges/MARKS__Phrase__Operation.csv")}
    assert len(marks) == 302 == sum(1 for r in phrases.values() if r["operation"] != emit.NULL)
    assert set(DUAL.items()) <= marks


def test_a_shape_holds_its_json_fields_and_its_coupled_group_and_uses_its_recipe(artifact):
    shapes = {r["id"]: r for r in table(artifact, "data/nodes/Shape.csv")}
    assert set(shapes) == {"period-difference", "one-result"}
    shape = shapes["period-difference"]
    declared = SECTIONS["shapes"][0]
    assert json.loads(shape["slots"]) == declared["slots"] and shape["slots"] == canonical(declared["slots"])
    assert shape["requires"] == canonical(["comparison", "difference"]) and shape["forbids"] == canonical(["from_to"])
    assert shape["constraints"] == "[]" and shapes["one-result"]["forbids"] == "[]"
    assert (shape["stage"], shape["replaces"], shape["defined_in"]) == ("shadow", "planshapes.compose:explicit_period", "pack")
    assert shapes["one-result"]["replaces"] == emit.NULL
    assert {r["id"]: r["coupled_group"] for r in shapes.values()} == {"period-difference": "one-result", "one-result": "one-result"}, \
        "they can match one question, contribute disjoint plan nodes, and no rule separates them: one group, named by its first id"
    model = yaml.safe_load(artifact.files["graph-model.yaml"])
    assert model["shape_groups"] == [["one-result", "period-difference"]]
    recipes = {r["id"]: r for r in table(artifact, "data/nodes/Recipe.csv")}
    assert json.loads(recipes["period-difference"]["blocks"]) == SECTIONS["recipes"][0]["blocks"]
    assert sorted((r["from"], r["to"]) for r in table(artifact, "data/edges/USES__Shape__Recipe.csv")) == [
        ("one-result", "one-result"), ("period-difference", "period-difference")]


def test_a_shapes_test_items_are_golden_ids_only_and_cited_once(artifact):
    golden = table(artifact, "data/nodes/Golden.csv")
    assert list(golden[0]) == ["id", "item", "set", "version"], "never a question, a figure or SQL"
    assert [r["id"] for r in golden] == ["shapes_synthetic_1:s1", "shapes_synthetic_1:s2", "shapes_synthetic_1:s3"]
    tested = [(r["from"], r["to"]) for r in table(artifact, "data/edges/TESTED_BY__Shape__Golden.csv")]
    assert tested == [("one-result", "shapes_synthetic_1:s3"), ("period-difference", "shapes_synthetic_1:s1"),
                      ("period-difference", "shapes_synthetic_1:s2")]


def test_a_worked_example_is_a_draft_only_at_specify_without_its_approval(artifact):
    examples = {r["id"]: r for r in table(artifact, "data/nodes/WorkedExample.csv")}
    approved, draft = examples["premium-january-vs-february"], examples["premium-one"]
    assert (approved["draft"], approved["approved_by"], approved["approved_on"]) == ("false", "underwriting-data-steward", "2026-11-18")
    assert (draft["draft"], draft["approved_by"], draft["approved_on"]) == ("true", emit.NULL, emit.NULL)
    assert approved["plan"] == canonical(PLAN) and approved["question"] == SECTIONS["examples"][0]["question"]
    assert sorted((r["from"], r["to"]) for r in table(artifact, "data/edges/EXAMPLE_OF__WorkedExample__Shape.csv")) == [
        ("premium-january-vs-february", "period-difference"), ("premium-one", "one-result")]


def test_a_clarification_rule_holds_its_when_as_condition_and_the_platforms_text(artifact, preload):
    rules = {r["id"]: r for r in table(artifact, "data/nodes/ClarificationRule.csv")}
    assert list(rules) == ["contexts-or-periods-2", "one-comparison-period"]
    pack, base = rules["contexts-or-periods-2"], rules["one-comparison-period"]
    assert (pack["condition"], pack["message"], pack["defined_in"]) == (canonical(WHEN), "contexts-or-periods", "pack")
    assert pack["text"] == preload.clarification_texts["contexts-or-periods"]
    assert (base["defined_in"], base["text"]) == ("platform", preload.clarification_texts["one-comparison-period"])


def test_the_pipelines_maturity_record_has_no_file_and_every_edge_is_copied(artifact):
    assert not any("ShapeStatus" in path or "STATUS_OF" in path for path in artifact.files)
    model = yaml.safe_load(artifact.files["graph-model.yaml"])
    assert model["node_types"]["ShapeStatus"]["filled_by"] == "pipeline" and model["edge_types"]["STATUS_OF"]["filled_by"] == "pipeline"
    assert all(step["stage"] == "copy" for step in artifact.manifest["load_order"] if step["table"] != "DEFINES")


def load(art: Path, graph: Path):
    """A fresh graph on this run's store: the artifact's DDL, then COPY of each file load_order stages for copy."""
    from kge.adapters.graphstore import connect, open_db
    db = open_db(graph)
    conn = connect(db)
    for stmt in (art / "ddl" / "kuzu.cypher").read_text().splitlines():
        conn.execute(stmt.removesuffix(";"))
    for step in json.loads((art / "manifest.json").read_text())["load_order"]:
        if step["stage"] == "copy":
            ends = f", from='{step['from']}', to='{step['to']}'" if "from" in step else ""
            conn.execute(f"COPY {step['table']} FROM '{(art / step['path']).as_posix()}' "
                         f"(header=true, parallel=false, null_strings=['\\\\N']{ends})")
    return db, conn


def test_the_artifact_loads_on_this_store(tmp_path, artifact):
    folder, _ = emit.write(artifact, tmp_path / "out", SAMPLE)
    db, conn = load(folder, tmp_path / "graph")
    try:
        counts = json.loads((folder / "manifest.json").read_text())["counts"]
        for t in ("Operation", "Phrase", "Shape", "Recipe", "WorkedExample", "ClarificationRule", "Golden"):
            assert conn.execute(f"MATCH (x:{t}) RETURN count(x)").get_all()[0][0] == counts["nodes"][t], t
        for t in ("MARKS", "USES", "TESTED_BY", "EXAMPLE_OF"):
            assert conn.execute(f"MATCH ()-[r:{t}]->() RETURN count(r)").get_all()[0][0] == counts["edges"][t], t
        [[condition]] = conn.execute("MATCH (r:ClarificationRule) WHERE r.id = 'contexts-or-periods-2' RETURN r.condition").get_all()
        assert json.loads(condition) == WHEN
        assert conn.execute("MATCH (x:ShapeStatus) RETURN count(x)").get_all()[0][0] == 0
    finally:
        conn.close()
        db.close()
    assert (folder / "data" / "nodes" / "Operation.csv").read_text().splitlines()[0] == "id"
