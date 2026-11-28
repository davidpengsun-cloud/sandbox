"""The test lanes: a test file's folder is its lane, tests/unit/, tests/integration/, tests/e2e/ or tests/eval/.

unit needs no built demo, integration needs the build (make graph pipeline), and e2e drives the demo end to end (the
Demo script, tests/e2e/demo_script.cjs, and the test that reads it beside the README); eval measures the grounding gate.
The conftest marks every collected test with its lane, so pytest -m selects one lane. A test file anywhere else in tests/, or
deeper inside a lane's folder, stops the collection, as does one name in two lanes, so every new file is placed in a lane
by the folder it is written in. The two frozen snapshot tests, tests/test_context.py and tests/test_agent_loop.py, stay
in tests/ itself, since their bytes are the owner's and they name their own path (python3 -m tests.test_context
--snapshot): FROZEN gives their lane. INTEGRATION_TESTS names the tests that run in the integration lane though their
file is in tests/unit/. Lane selection is per test; pytest-split assigns the selected tests to duration-balanced groups.
The integration lane has two tiers (owner, 2026-11-16). The gate tier, the must-have integration tests, is the full
check's one integration leg (make test-integration, -m "integration and not extended") and runs on every pull request.
EXTENDED names the files whose integration-lane tests are the extended tier instead: the conftest marks them extended,
and make extended runs them (-m extended) on the nightly job, not on a pull request. A file moves between the tiers by
its name in EXTENDED alone; an integration test file named nowhere is in the gate tier. INTEGRATION_RUNS are the -m
selections that run integration-lane tests, each of which gives every xdist worker a build of its own.
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

INTEGRATION_RUNS = ("integration", "integration and not extended", "extended")

EXTENDED = frozenset({
    "test_agent.py", "test_agent_loop.py", "test_agent_person_http.py", "test_answer_modes.py", "test_answer_shape_declarations.py",
    "test_answer_shape_rendering.py", "test_artifact_load.py", "test_ask_budget.py", "test_ask_gates.py",
    "test_ask_live_trace.py", "test_ask_model_period.py", "test_ask_parsing.py", "test_ask_path.py",
    "test_ask_period_contract.py", "test_ask_round13.py", "test_ask_scope.py", "test_ask_stream_live.py",
    "test_assist.py", "test_assist_joins_bigquery.py", "test_baselines.py", "test_bigquery.py",
    "test_bigquery_estate_build.py", "test_bigquery_lifecycle.py", "test_bigquery_pipeline.py",
    "test_bigquery_retention_revisit.py", "test_branch_sales_release.py", "test_calendar_attributes.py",
    "test_calendar_spine_meridian.py", "test_capability_matrix.py", "test_catalogue_questions.py",
    "test_catalogue_reads.py", "test_certified_figures.py", "test_checked_output_sample.py", "test_combined.py",
    "test_compact.py", "test_concurrent.py", "test_context.py", "test_context_scope.py", "test_control_currency.py",
    "test_cross_unit.py", "test_crosspack_round9d.py", "test_data_responsiveness.py",
    "test_declared_metrics_sample.py", "test_declared_window_policy.py", "test_definitions.py",
    "test_derived_shapes_meridian.py", "test_document_reader_acceptance.py", "test_domain.py",
    "test_engineer_compatibility.py", "test_entity_hierarchy_meridian.py", "test_env_access.py",
    "test_estate_wealth.py", "test_eval_concurrency.py", "test_eval_runtime_truth.py", "test_examples.py", "test_external.py", "test_external_suggestion_lifecycle.py",
    "test_figure_tense.py", "test_filter_grammar_meridian.py", "test_first_client_release.py", "test_fixes.py",
    "test_fixture_document_reload.py", "test_gate_walkthrough_fixes.py", "test_gcp_native.py", "test_golden_v2.py",
    "test_golden_wealth.py", "test_governed_join_meridian.py", "test_graph_path_meridian.py",
    "test_graphstore_twin.py", "test_grounding_ask_safety.py", "test_grounding_empty_spans.py",
    "test_grounding_plans.py", "test_grounding_public_review.py", "test_group_onboarding.py", "test_harness.py",
    "test_identity_instances.py", "test_image.py", "test_in_filters_meridian.py", "test_join_cardinality_meridian.py",
    "test_join_declarations_meridian.py", "test_margin.py", "test_measure_capabilities.py",
    "test_mention_suggestions.py", "test_meridian_patterns.py", "test_meridian_version_execution.py",
    "test_meta_model.py", "test_model_path.py", "test_nna_reference_definitions.py", "test_offset_comparisons.py",
    "test_onboard_recovery.py", "test_open_items.py", "test_overlay.py", "test_pack_lock.py", "test_pack_wordings.py", "test_page_scope.py",
    "test_parity.py", "test_parts_meridian.py", "test_password_bearer_api.py", "test_period_ranges_meridian.py",
    "test_plan_vocabulary_round3.py", "test_plan_wording_round2.py", "test_planner_fix_round.py",
    "test_planner_request_jobs.py", "test_planner_round9f.py", "test_platform_docs.py", "test_portal_ask_contract.py",
    "test_portal_starters.py", "test_present.py", "test_principal_lookup.py", "test_product_scope.py",
    "test_profile.py", "test_profiles.py", "test_public_round9d.py", "test_query_semantics.py",
    "test_question_runs_acceptance.py", "test_rank_windows_meridian.py", "test_readable_answers.py",
    "test_release_evaluation.py", "test_request_limits_surface.py", "test_role_playbooks_acceptance.py",
    "test_rules_shape_gates.py", "test_rules_shapes.py", "test_rules_sql_bigquery_forms.py", "test_run_config.py",
    "test_run_trace.py",
    "test_runtimes_execution.py", "test_sage_names.py", "test_sample_fixture_loop.py",
    "test_sandbox_shapes_meridian.py", "test_scoped_vocabulary_answers.py", "test_search_acceptance.py",
    "test_semantics.py", "test_serve_preview.py", "test_served_landed_tables.py", "test_service_basics.py",
    "test_shadow_lifecycle.py", "test_shared_plan_contract.py", "test_signin.py", "test_size_threshold_meridian.py",
    "test_source_set_alias.py",
    "test_specials.py", "test_standards.py", "test_store.py", "test_stored_role_playbooks.py",
    "test_structural_vocabulary_meridian.py", "test_tagger_ab.py", "test_test_drive_golden.py",
    "test_textband_declarations.py", "test_tiers.py", "test_vocabulary.py", "test_wealth_context_filters.py",
    "test_wealth_history_yoy.py", "test_xref_join_meridian.py", "test_ytd_abstention_public.py",
})


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


def extended(path: Path, test: str) -> bool:
    """Whether one test is in the integration lane's extended tier: an integration-lane test of a file EXTENDED names."""
    return path.name in EXTENDED and lane_of(path, test) == "integration"


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
    out += [f"{name} is in EXTENDED but is in no lane" for name in sorted(EXTENDED) if name not in held]
    out += [f"{name} is in EXTENDED but has no integration-lane test" for name in sorted(EXTENDED) if name in held
            and file_lane(Path(held[name])) != "integration" and name not in INTEGRATION_TESTS]
    return out


if __name__ == "__main__":
    print(" ".join(files_in(sys.argv[1])))
