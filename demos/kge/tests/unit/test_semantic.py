from pathlib import Path

import pytest

from kge.estate import semantic

DEMO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("folder,format_", [
    ("bancassurance", "dbt"), ("bancassurance-lookml", "lookml"),
    ("bancassurance-dictionary", "dictionary"),
    ("bancassurance-osi", "osi"), ("bancassurance-osi-json", "osi"),
])
def test_each_semantic_format_returns_the_same_interface(folder, format_):
    parsed = semantic.parse(semantic.read_folder(DEMO / "examples" / folder), "bancassurance")
    assert set(parsed) == {"format", "model", "seeds", "documents", "golden", "hints", "ignored", "not_drafted"}
    assert parsed["format"] == format_
    assert any(m["column"] == "premium_amount" for m in parsed["hints"]["measures"])


def test_dbt_declared_metrics_replace_suffix_inference_and_sql_is_not_read():
    parsed = semantic.parse(semantic.read_folder(DEMO / "examples/bancassurance"), "bancassurance")
    assert [m["term"] for m in parsed["hints"]["measures"]] == ["written_premium", "in_force_premium"]
    assert parsed["hints"]["measures"][0]["aliases"] == ["gross written premium"]
    assert any(x.get("metric") == "loss_ratio" for x in parsed["not_drafted"])
    assert "models/premium.sql" in parsed["ignored"]
    assert parsed["documents"][0]["meta"]["certification"] == {
        "ai_ready": "yes", "tier": "gold", "policy_id": "invented-m365-certification", "policy_version": "1.0",
        "effective_from": "2026-10-18"}


def test_templates_in_read_yaml_are_refused_with_file_and_line():
    with pytest.raises(semantic.SemanticError, match=r"models/schema.yml:1: templates are refused"):
        semantic.parse(semantic.read_folder(DEMO / "examples/bancassurance-unsafe"), "bancassurance")


def test_aliases_and_anchors_are_refused():
    files = {"dbt_project.yml": b"name: x\n", "models/schema.yml": b"models: &m []\ncopy: *m\n"}
    with pytest.raises(semantic.SemanticError, match=r"models/schema.yml:1: anchors are refused"):
        semantic.parse(files, "bancassurance")


def test_ambiguous_folder_is_refused():
    files = {"dbt_project.yml": b"name: x\n", "x.view.lkml": b"view: x {}\n"}
    with pytest.raises(semantic.SemanticError, match="expected exactly one"):
        semantic.parse(files, "bancassurance")


def test_non_numeric_dictionary_measure_is_not_drafted():
    files = {"data-dictionary.yaml": b"""tables:\n- name: t\n  columns:\n  - name: c\n    type: string\n    description: text\n    measure: {currency: GBP, period_column: d}\n  - {name: d, type: date, description: day}\n"""}
    parsed = semantic.parse(files, "bancassurance")
    assert parsed["hints"]["measures"] == []
    assert parsed["not_drafted"][0]["why"] == "measure column is non-numeric"


def test_unknown_lookml_join_is_refused():
    files = {"a.view.lkml": b"view: a {\n sql_table_name: a ;;\n dimension: id { type: number sql: ${TABLE}.id ;; }\n}\n",
             "x.model.lkml": b"explore: a { join: missing { sql_on: ${a.id} = ${missing.id} ;; } }\n"}
    with pytest.raises(semantic.SemanticError, match="unknown view"):
        semantic.parse(files, "bancassurance")


def test_markdown_rules_read_the_documented_table_and_the_labelled_mock_reads_prose(monkeypatch):
    files = semantic.read_folder(DEMO / "examples/bancassurance-markdown")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    rules = semantic.parse(files, "bancassurance")
    assert rules["model"]["tables"][0]["name"] == "premium"
    assert rules["hints"]["measures"] == []
    assert all(outcome["made_by"] == "rules" for outcome in rules["hints"]["outcomes"])
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    parsed = semantic.parse(files, "bancassurance")
    assert parsed["format"] == "model" and parsed["hints"]["measures"][0]["currency"] == "GBP"
    recipes = next(outcome for outcome in parsed["hints"]["outcomes"] if outcome["node"] == "recipes")
    assert recipes["made_by"] == "model" and recipes["tries"] == 2


def test_osi_and_dbt_are_ambiguous():
    files = semantic.read_folder(DEMO / "examples/bancassurance-osi")
    files["dbt_project.yml"] = b"name: also_dbt\n"
    with pytest.raises(semantic.SemanticError, match="unambiguous"):
        semantic.parse(files, "bancassurance")


def test_osi_recipes_are_typed_ast_and_refused_recipe_is_retained():
    parsed = semantic.parse(semantic.read_folder(DEMO / "examples/bancassurance-osi"), "bancassurance")
    written, point = parsed["hints"]["measures"]
    assert written["filters"] == [{"table":"policy","column":"status","op":"=","value":"active"}]
    assert point["grain"] == "month_end"
    assert {x.get("metric") for x in parsed["not_drafted"]} >= {"ambiguous_premium", "loss_ratio"}


@pytest.mark.parametrize("folder,table,column", [
    ("bancassurance", "policy", "status"),
    ("bancassurance-lookml", "premium", "status"),
    ("bancassurance-dictionary", "premium", "status"),
])
def test_each_plain_recipe_format_maps_to_the_same_ast(folder, table, column):
    parsed = semantic.parse(semantic.read_folder(DEMO / "examples" / folder), "bancassurance")
    assert parsed["hints"]["measures"][0]["filters"] == [
        {"table":table,"column":column,"op":"=","value":"active"}]


@pytest.mark.parametrize("condition,why", [
    ("t.name = 'x' OR t.amount > 1", "OR"),
    ("lower(t.name) = 'x'", "function"),
    ("missing = 1", "unknown"),
    ("t.amount = 'one'", "numeric"),
    ("t.amount IN (SELECT amount FROM t)", "subqueries"),
    ("other.amount = 1", "unknown"),
])
def test_recipe_refusals(condition, why):
    tables = [{"name":"t","columns":[{"name":"name","type":"VARCHAR"},{"name":"amount","type":"DECIMAL(9,2)"}]}]
    with pytest.raises(semantic.SemanticError, match=why):
        semantic.parse_conditions(condition, tables)


def test_structured_filter_value_is_quoted_data_not_sql():
    tables = [{"name":"t","columns":[{"name":"name","type":"VARCHAR"}]}]
    assert semantic.parse_conditions([{"column":"t.name","op":"=","value":"x' OR 1=1 --"}], tables) == [
        {"table":"t","column":"name","op":"=","value":"x' OR 1=1 --"}]


def _cached(folder: str = "bancassurance-osi") -> tuple[dict, dict]:
    import json
    files = semantic.read_folder(DEMO / "examples" / folder)
    parsed = semantic.parse(files, "bancassurance")
    return files, json.loads(json.dumps({"inputs": semantic.proposal_inputs(files), "proposal": parsed}, default=str))


def test_an_uploaded_proposal_json_is_refused_by_name(tmp_path):
    import shutil
    folder = tmp_path / "semantic"
    shutil.copytree(DEMO / "examples/bancassurance-osi", folder)
    (folder / "proposal.json").write_text("{}")
    with pytest.raises(semantic.SemanticError, match=r"^proposal.json: the name is reserved"):
        semantic.read_folder(folder)
    with pytest.raises(semantic.SemanticError, match=r"^proposal.json: the name is reserved"):
        semantic.parse({**semantic.read_folder(DEMO / "examples/bancassurance-osi"), "proposal.json": b"{}"}, "bancassurance")


def test_a_forged_cached_condition_is_checked_again_and_never_trusted():
    import json
    files, held = _cached()
    assert semantic.restore(json.dumps(held).encode(), files, "bancassurance")["hints"]["measures"][0]["filters"] == [
        {"table": "policy", "column": "status", "op": "=", "value": "active"}]
    held["proposal"]["hints"]["measures"][0]["filters"][0]["op"] = "= 'x' OR 1=1 OR 'a' ="
    assert semantic.restore(json.dumps(held).encode(), files, "bancassurance") is None, "the OR is refused, the files parsed again"
    files2, held2 = _cached()
    held2["proposal"]["hints"]["measures"][0]["currency"] = "XXQ"
    assert semantic.restore(json.dumps(held2).encode(), files2, "bancassurance") is None
    files3, held3 = _cached()
    held3["proposal"]["golden"] = [{"id": "FORGED", "question": "q", "expect": {"figure": "1"}, "sql": "SELECT 1"}]
    restored = semantic.restore(json.dumps(held3).encode(), files3, "bancassurance")
    assert [g["id"] for g in restored["golden"]] == ["BR-01", "BR-02", "BR-03"], "golden items come from the files"
    assert semantic.restore(json.dumps(held3).encode(), {**files3, "model.yaml": files3["model.yaml"] + b"\n"},
                            "bancassurance") is None, "a changed file is not the cached one"


def _golden(figure: str, tolerance: str | None = None) -> bytes:
    extra = f"\n  tolerance: {tolerance}" if tolerance is not None else ""
    return (f"- id: G1\n  question: Written premium for June 2025\n  expect: {{figure: {figure}}}\n"
            f"  sql: SELECT 1\n  source: board{extra}\n").encode()


def test_a_bare_yaml_figure_keeps_every_digit():
    items = semantic._golden({"golden/a.yaml": _golden("12345678901234567.89")})
    assert items[0]["expect"]["figure"] == "12345678901234567.89"
    import json
    items = semantic._golden({"golden/a.json": json.dumps([{"id": "G1", "question": "q", "source": "s", "sql": "SELECT 1"}])
                              .replace('"sql"', '"expect": {"figure": 12345678901234567.89}, "sql"').encode()})
    assert items[0]["expect"]["figure"] == "12345678901234567.89"


HUGE = "1e" + "9" * 9


@pytest.mark.parametrize("figure, tolerance", [("1e30", None), (HUGE, "1"), ("1", HUGE),
                                               ("0.0000001", None), (".inf", None), ("'12'x", None)])
def test_an_absurd_figure_or_tolerance_is_refused_in_one_line(figure, tolerance):
    """HUGE is an exponent of a billion, which formatted would be a gigabyte of digits."""
    with pytest.raises(semantic.SemanticError) as refused:
        semantic._golden({"golden/a.yaml": _golden(figure, tolerance)})
    assert "\n" not in str(refused.value) and str(refused.value).startswith(("golden/a.yaml", "YAML line"))


def test_lookml_null_filters_and_a_negation_that_keeps_nulls():
    example = (DEMO / "examples/bancassurance-lookml/premium.view.lkml").read_text()
    def view(value: str) -> dict:
        return {"premium.view.lkml": example.replace('filters: [status: "active"]', f'filters: [status: "{value}"]').encode()}
    _, measures, _, not_drafted = semantic._lookml(view("-NULL"), "bancassurance")
    assert measures[0]["filters"] == [{"table": "premium", "column": "status", "op": "IS NOT NULL", "value": None}]
    _, measures, _, not_drafted = semantic._lookml(view("-lapsed"), "bancassurance")
    assert measures == [] and not_drafted == [
        {"metric": "written_premium", "why": "LookML's -lapsed keeps NULL rows (an OR condition) and is not drafted"}]
