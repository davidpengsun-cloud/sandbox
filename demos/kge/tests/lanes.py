"""The test lanes: a test file's folder is its lane, tests/unit/, tests/integration/ or tests/e2e/.

unit needs no built demo, integration needs the build (make graph pipeline), and e2e drives the demo end to end (the
Demo script, tests/e2e/demo_script.cjs, and the test that reads it beside the README). The conftest marks every collected
test with its lane, so pytest -m unit, -m integration or -m e2e runs one lane. A test file anywhere else in tests/, or
deeper inside a lane's folder, stops the collection, as does one name in two lanes, so every new file is placed in a lane
by the folder it is written in. The two frozen snapshot tests, tests/test_context.py and tests/test_agent_loop.py, stay
in tests/ itself, since their bytes are the owner's and they name their own path (python3 -m tests.test_context
--snapshot): FROZEN gives their lane. INTEGRATION_TESTS names the tests that run in the integration lane though their
file is in tests/unit/. make test-unit runs the unit lane one file to a process: python tests/lanes.py unit prints the
files with a test in that lane.

make test-unit SHARD=i/N runs the i-th of N shards of the unit lane (the KGE tests job and the full check run the
shards side by side), and make test-integration SHARD=i/N the i-th of N of the integration lane (the full check's
integration legs): python tests/lanes.py <lane> --shard i/N prints that shard's files. Files are dealt by their time in
the lane's table, SECONDS, the longest first, each to the shard with the least time so far (the lower index on a tie), so
the same table and lane give the same shards everywhere; a file the table does not name counts as DEFAULT_SECONDS. The
shards of one N hold every file of the lane, each once. UNIT_SECONDS is the per-file times of one make test-unit run,
INTEGRATION_SECONDS those of the integration lane's tests in one make test-integration run, rounded; refresh a table when
a file's time moves by much, since only the balance depends on it. make lane-times refreshes them from a run under the
same conditions as CI's (coverage on): python tests/lanes.py <lane> --times <junit.xml>... rewrites the lane's table in
this file from the JUnit reports of a run (each test's setup, call and teardown, summed by file).
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LANES = ("unit", "integration", "e2e")
TESTS = Path(__file__).resolve().parent

FROZEN = {"test_context.py": "integration", "test_agent_loop.py": "integration"}

INTEGRATION_TESTS = {
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


UNIT_SECONDS = {
    "test_portal_metadata.py": 115, "test_kit.py": 90, "test_compiler.py": 72, "test_onboard_unit.py": 72,
    "test_objstore.py": 37, "test_upgrade_reload.py": 28, "test_knowledge_engineer.py": 27, "test_infra.py": 23,
    "test_onboard_safety.py": 21, "test_improve_flow.py": 19, "test_layers.py": 18, "test_query_semantics.py": 16,
    "test_onboarding.py": 14, "test_image.py": 13, "test_improve_unit.py": 12, "test_onboard_round3.py": 12,
    "test_versions.py": 10, "test_client_onboard.py": 9, "test_data_governance.py": 8, "test_from_files.py": 8,
    "test_bigquery_estate.py": 7, "test_data_limits.py": 6, "test_catalogue.py": 5, "test_no_comments.py": 4,
    "test_onboard_standards.py": 4, "test_test_drive.py": 4, "test_bigquery_staging.py": 3, "test_gcp_native.py": 3,
    "test_improve_page.py": 3, "test_model_limits.py": 3, "test_onboard_api.py": 3, "test_platform_docs.py": 3,
    "test_released_generation.py": 3, "test_client_nouns.py": 2, "test_graphstore_bigquery.py": 2,
    "test_model_path.py": 2, "test_release_record_unpack.py": 2, "test_request_budget.py": 2, "test_standards.py": 2,
    "test_api_surface.py": 1, "test_appstate.py": 1, "test_ask_currencies.py": 1, "test_baselines.py": 1,
    "test_bigquery.py": 1, "test_bigquery_sync_order.py": 1, "test_bigquery_vectors.py": 1, "test_brand.py": 1,
    "test_candidate_cleanup.py": 1, "test_cli.py": 1, "test_cli_servers.py": 1, "test_client_id.py": 1,
    "test_composite_joins.py": 1, "test_data.py": 1, "test_ddl.py": 1, "test_definition_scope.py": 1,
    "test_draftflow.py": 1, "test_drafts.py": 1, "test_endpoint_tags.py": 1, "test_engineer_skills.py": 1,
    "test_external.py": 1, "test_forced_rules_env.py": 1, "test_heldout_sets.py": 1, "test_identity_routes.py": 1,
    "test_improve_guards.py": 1, "test_improve_model.py": 1, "test_improve_rules.py": 1, "test_ladybug_limit.py": 1,
    "test_lanes.py": 1, "test_lock.py": 1, "test_lock_versions.py": 1, "test_meta_model.py": 1,
    "test_model_period.py": 1, "test_onboarding_guide.py": 1, "test_packstore.py": 1, "test_page.py": 1,
    "test_password_bearer.py": 1, "test_pilot_fixes.py": 1, "test_portal_image.py": 1, "test_principal_ids.py": 1,
    "test_process_vocabulary.py": 1, "test_prose_paths.py": 1, "test_public_api_docs.py": 1, "test_query_budget.py": 1,
    "test_query_dialect.py": 1, "test_question_contract.py": 1, "test_question_periods.py": 1,
    "test_request_limits.py": 1, "test_retrieval_order.py": 1, "test_roles_docs.py": 1, "test_search.py": 1,
    "test_semantic.py": 1, "test_service_basics.py": 1, "test_signin_limits.py": 1, "test_spec_port.py": 1,
    "test_third_party_notices.py": 1, "test_trusted_caller.py": 1, "test_ui_tokens.py": 1,
}
INTEGRATION_SECONDS = {
    "test_margin.py": 246, "test_bigquery_estate_build.py": 188, "test_overlay.py": 117, "test_agent_loop.py": 116,
    "test_release.py": 58, "test_catalogue_reads.py": 55, "test_parity.py": 42, "test_artifact_load.py": 41,
    "test_landing.py": 38, "test_graphstore_twin.py": 36, "test_context.py": 33, "test_pack_lock.py": 32,
    "test_present.py": 31, "test_ask_model_period.py": 29, "test_concurrent.py": 28, "test_test_drive_golden.py": 28,
    "test_agent.py": 27, "test_agent_contexts.py": 25, "test_served_landed_tables.py": 25, "test_domain.py": 24,
    "test_signin.py": 24, "test_profiles.py": 23, "test_answer_text.py": 22, "test_ask_budget.py": 17,
    "test_cross_unit.py": 17, "test_gate_answer.py": 17, "test_estate_wealth.py": 16, "test_data_responsiveness.py": 14,
    "test_env_access.py": 14, "test_gcp_native.py": 13, "test_agent_mock.py": 12, "test_specials.py": 12,
    "test_ask_parsing.py": 11, "test_definitions.py": 11, "test_engineer_compatibility.py": 11, "test_harness.py": 11,
    "test_tiers.py": 11, "test_service_basics.py": 10, "test_golden_wealth.py": 9, "test_ask_scope.py": 8,
    "test_fixes.py": 8, "test_model_path.py": 6, "test_bigquery.py": 5, "test_bigquery_pipeline.py": 5,
    "test_profile.py": 5, "test_run_trace.py": 5, "test_search_acceptance.py": 5, "test_serve_preview.py": 5,
    "test_combined.py": 4, "test_image.py": 4, "test_run_config.py": 4, "test_golden_v2.py": 3,
    "test_request_limits_surface.py": 3, "test_semantics.py": 3, "test_vocabulary.py": 3,
    "test_ask_period_contract.py": 1, "test_baselines.py": 1, "test_external.py": 1, "test_join_access.py": 1,
    "test_meta_model.py": 1, "test_password_bearer_api.py": 1, "test_platform_docs.py": 1,
    "test_principal_lookup.py": 1, "test_query_semantics.py": 1, "test_standards.py": 1, "test_store.py": 1,
    "test_unmatched.py": 1,
}
SECONDS = {"unit": UNIT_SECONDS, "integration": INTEGRATION_SECONDS, "e2e": {}}
DEFAULT_SECONDS = 5


def shards(files: list[str], count: int, seconds: dict[str, int] = UNIT_SECONDS) -> list[list[str]]:
    """The files dealt into count shards by their seconds, the longest first, each to the least-loaded shard."""
    if count < 1:
        raise ValueError("a lane splits into one shard or more")
    held: list[list[str]] = [[] for _ in range(count)]
    load = [0] * count
    def cost(path: str) -> int:
        return seconds.get(Path(path).name, DEFAULT_SECONDS)
    for path in sorted(files, key=lambda p: (-cost(p), Path(p).name)):
        at = min(range(count), key=lambda i: (load[i], i))
        held[at].append(path)
        load[at] += cost(path)
    return [sorted(shard) for shard in held]


TABLES = {"unit": "UNIT_SECONDS", "integration": "INTEGRATION_SECONDS"}


def measured(reports: list[Path]) -> dict[str, int]:
    """Each test file's seconds in the JUnit reports, by its file name, rounded, and at least 1."""
    seconds: dict[str, float] = {}
    for report in reports:
        for case in ET.parse(report).iter("testcase"):
            module = next((part for part in case.get("classname", "").split(".") if part.startswith("test_")), None)
            if module:
                name = f"{module}.py"
                seconds[name] = seconds.get(name, 0.0) + float(case.get("time") or 0)
    return {name: max(1, round(t)) for name, t in seconds.items()}


def table(name: str, seconds: dict[str, int], width: int = 120) -> str:
    """The table as this file writes it: the longest first, then by name, wrapped at width."""
    items = [f'"{file}": {t},' for file, t in sorted(seconds.items(), key=lambda kv: (-kv[1], kv[0]))]
    lines, line = [], "   "
    for item in items:
        if len(line) + 1 + len(item) > width:
            lines.append(line)
            line = "   "
        line += " " + item
    lines.append(line)
    return f"{name} = {{\n" + "\n".join(lines) + "\n}"


def rewrite(lane: str, seconds: dict[str, int], source: Path = Path(__file__)) -> None:
    """The lane's table in source replaced by seconds."""
    name = TABLES[lane]
    text = source.read_text()
    found = re.search(rf"^{name} = {{\n.*?^}}", text, re.M | re.S)
    if not found:
        raise ValueError(f"{source} holds no {name}")
    source.write_text(text[:found.start()] + table(name, seconds) + text[found.end():])


def shard_of(spec: str) -> tuple[int, int]:
    """i/N, 1 <= i <= N, as a pair; anything else is refused."""
    index, _, count = spec.partition("/")
    if not (index.isdigit() and count.isdigit()) or not 1 <= int(index) <= int(count):
        raise ValueError(f"a shard is i/N with 1 <= i <= N, not {spec!r}")
    return int(index), int(count)


def misplaced(folder: Path = TESTS) -> list[str]:
    """What is out of place in the folder: a test file in no lane's folder, one name in two lanes, a frozen file missing,
    or an override for a file that is not there."""
    out = []
    found = sorted(p.relative_to(folder) for p in folder.rglob("test_*.py") if "fixtures" not in p.relative_to(folder).parts)
    for rel in found:
        lane_file = len(rel.parts) == 2 and rel.parts[0] in LANES
        if not lane_file and not (len(rel.parts) == 1 and rel.name in FROZEN):
            out.append(f"tests/{rel} is in no lane: move it to tests/unit/, tests/integration/ or tests/e2e/")
    names = [rel.name for rel in found]
    out += [f"{name} is in more than one lane" for name in sorted({n for n in names if names.count(n) > 1})]
    out += [f"tests/{name} is frozen in tests/ but not there" for name in sorted(FROZEN) if not (folder / name).is_file()]
    held = placed(folder)
    out += [f"{name} has overrides but is in no lane" for tests in OVERRIDES.values() for name in tests if name not in held]
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    usage = (f"usage: python tests/lanes.py {{{','.join(LANES)}}} [--shard i/N]\n"
             f"       python tests/lanes.py {{{','.join(TABLES)}}} --times <junit.xml>...")
    if len(args) >= 3 and args[0] in TABLES and args[1] == "--times":
        times = measured([Path(report) for report in args[2:]])
        rewrite(args[0], times)
        print(f"{TABLES[args[0]]}: {len(times)} files, {sum(times.values())} s")
        sys.exit(0)
    if not args or args[0] not in LANES or len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--shard"):
        sys.exit(usage)
    problems = misplaced()
    if problems:
        sys.exit("\n".join(problems))
    chosen = files_in(args[0])
    if len(args) == 3:
        try:
            index, count = shard_of(args[2])
        except ValueError as refused:
            sys.exit(f"{refused}\n{usage}")
        chosen = shards(chosen, count, SECONDS[args[0]])[index - 1]
    print("\n".join(chosen))
