"""The test lanes: a test file's folder is its lane, tests/unit/, tests/integration/, tests/e2e/ or tests/eval/.

unit needs no built demo, integration needs the build (make graph pipeline), and e2e drives the demo end to end (the
Demo script, tests/e2e/demo_script.cjs, and the test that reads it beside the README); eval measures the grounding gate.
The conftest marks every collected test with its lane, so pytest -m selects one lane. A test file anywhere else in tests/, or
deeper inside a lane's folder, stops the collection, as does one name in two lanes, so every new file is placed in a lane
by the folder it is written in. The two frozen snapshot tests, tests/test_context.py and tests/test_agent_loop.py, stay
in tests/ itself, since their bytes are the owner's and they name their own path (python3 -m tests.test_context
--snapshot): FROZEN gives their lane. INTEGRATION_TESTS names the tests that run in the integration lane though their
file is in tests/unit/. Lane selection is per test; pytest-split assigns the selected tests to duration-balanced groups.
"""

from pathlib import Path
import sys

LANES = ("unit", "integration", "e2e", "eval")
TESTS = Path(__file__).resolve().parent

FROZEN = {"test_context.py": "integration", "test_agent_loop.py": "integration"}

INTEGRATION_TESTS = {
    "test_ask_path.py": {
        "test_golden_figure_with_definitional_opening_needs_all_units_with_real_parser",
        "test_path_nodes_and_edges_on_meridian_and_compact_omission",
        "test_path_refused_access_marks_access_red_and_following_nodes_waiting",
        "test_path_withholds_restricted_and_unreadable_columns",
    },
    "test_catalogue_questions.py": {
        "test_engine_and_combined_abstain_without_model_calls",
        "test_catalogue_paraphrases",
        "test_literal_wording_and_score",
        "test_figure_detection_and_unchanged_sets",
        "test_catalogue_access_order",
        "test_evaluate_scores_catalogue_answers",
        "test_catalogue_over_http_and_mcp",
        "test_shelf_definitions_and_mixed_figure_stay_on_figure_path",
    },
    "test_baselines.py": {
        "test_the_39_baseline_is_one_rules_run_of_every_arm_on_the_three_sets",
    },
    "test_bigquery.py": {
        "test_default_warehouses_remains_duckdb",
        "test_bigquery_warehouses_gate_dry_run",
        "test_engine_activates_bigquery_warehouses_when_configured",
        "test_graph_retrieve_executes_bigquery_vector_search",
        "test_load_bigquery_dry_run",
        "test_load_bigquery_takes_the_dataset_prefix_terraform_sets",
        "test_bigquery_vector_search_injection_safety_and_readiness_filter",
        "test_bigquery_vector_search_adc_project_fallback_and_live_readiness_filter",
        "test_engine_passes_the_artifacts_warehouse_versions_to_bigquery",
        "test_both_backends_offer_the_warehouse_seam",
        "test_duckdb_catalogue_methods_read_the_warehouse_s_own_catalogue",
    },
    "test_external.py": {
        "test_an_embedding_failure_reaching_the_api_is_a_503",
        "test_an_embedding_failure_through_mcp_is_the_tool_s_503",
    },
    "test_gcp_native.py": {
        "test_a_question_is_answered_end_to_end_with_the_graph_in_bigquery_and_no_ladybug_loaded",
    },
    "test_image.py": {
        "test_serve_answers_with_a_bearer_token_and_a_password_as_the_container_serves",
    },
    "test_meta_model.py": {
        "test_the_readme_says_term_s_id_and_the_new_types_are_filled_from_the_compiled_pack",
    },
    "test_model_path.py": {
        "test_a_mistyped_model_setting_is_one_line_and_exit_2_from_every_command_that_resolves_a_model",
        "test_the_agents_plan_node_calls_the_model_through_the_chat_model",
        "test_the_agent_abstains_on_a_refused_plan_and_keeps_the_template_on_a_refused_wording",
    },
    "test_platform_docs.py": {
        "test_the_certification_counts_the_plan_quotes_are_what_landed",
        "test_the_query_sets_refused_joins_are_the_golden_sets_the_harness_runs",
        "test_the_query_set_names_golden_wealth_v1_and_its_results_are_the_baselines",
    },
    "test_query_semantics.py": {
        "test_the_demo_graph_holds_the_layer_and_no_unit_query_sees_the_curated_source",
        "test_the_runtime_refuses_only_the_pairs_a_no_join_fact_keeps_apart",
        "test_across_retail_and_wealth_the_certified_join_answers_and_a_no_join_pair_quotes_its_fact",
    },
    "test_service_basics.py": {
        "test_real_ask_and_context_responses_are_the_engine_s_fields_through_the_model",
    },
    "test_standards.py": {
        "test_the_map_of_nna_and_aum_lists_their_industry_standards",
    },
}

OVERRIDES = {"integration": INTEGRATION_TESTS}


def placed(folder: Path = TESTS) -> dict[str, str]:
    """Each test file in a lane, by name, with its path from the demo's folder: the lane folders' files and the frozen two."""
    held = {p.name: f"tests/{lane}/{p.name}" for lane in LANES for p in sorted((folder / lane).glob("test_*.py"))}
    held.update({name: f"tests/{name}" for name in FROZEN if (folder / name).is_file()})
    return held


def file_lane(path: Path) -> str:
    """The lane a test file's place gives it: its folder's, or FROZEN's for the two in tests/ itself."""
    return path.parent.name if path.parent.name in LANES else FROZEN[path.name]


def lane_of(path: Path, test: str) -> str:
    """The lane of one test: an override's, else its file's. A file in no lane is a KeyError."""
    for lane, tests in OVERRIDES.items():
        if test in tests.get(path.name, ()):
            return lane
    return file_lane(path)


def files_in(lane: str, folder: Path = TESTS) -> list[str]:
    """The test files with at least one test in the lane, by their path from the demo's folder."""
    held = placed(folder)
    names = {name for name, at in held.items() if file_lane(Path(at)) == lane}
    names |= set(OVERRIDES.get(lane, {})) & set(held)
    return sorted(held[name] for name in names)


def misplaced(folder: Path = TESTS) -> list[str]:
    """What is out of place in the folder: a test file in no lane's folder, one name in two lanes, a frozen file missing,
    or an override for a file that is not there."""
    out = []
    found = sorted(p.relative_to(folder) for p in folder.rglob("test_*.py") if "fixtures" not in p.relative_to(folder).parts)
    for rel in found:
        lane_file = len(rel.parts) == 2 and rel.parts[0] in LANES
        if not lane_file and not (len(rel.parts) == 1 and rel.name in FROZEN):
            out.append(f"tests/{rel} is in no lane: move it to tests/unit/, tests/integration/, tests/e2e/ or tests/eval/")
    names = [rel.name for rel in found]
    out += [f"{name} is in more than one lane" for name in sorted({n for n in names if names.count(n) > 1})]
    out += [f"tests/{name} is frozen in tests/ but not there" for name in sorted(FROZEN) if not (folder / name).is_file()]
    held = placed(folder)
    out += [f"{name} has overrides but is in no lane" for tests in OVERRIDES.values() for name in tests if name not in held]
    return out


if __name__ == "__main__":
    print(" ".join(files_in(sys.argv[1])))
