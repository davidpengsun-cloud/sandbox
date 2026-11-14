"""Checks' scope, artifact consumers and required status fail closed without GitHub or a browser."""

import os
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml

from kge.pipeline.settings import ROOT


WORKFLOW = yaml.safe_load((ROOT.parents[1] / ".github/workflows/checks.yml").read_text())
JOBS = WORKFLOW["jobs"]


def shell(script, cwd, **env):
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=cwd,
                          env={**os.environ, **env}, capture_output=True, text=True, timeout=30)


def scope_run(tmp_path, paths=(), event="pull_request", ref="refs/pull/12/merge", base="develop", draft="false", audit="false",
              tip_present=True, up_to_date=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    changed = tmp_path / "paths"
    changed.write_bytes(b"".join(path.encode() + b"\0" for path in paths))
    git = bin_dir / "git"
    git.write_text('''#!/bin/sh
echo "git $*" >> "$CALLS"
case "$*" in
  "diff --name-only --no-renames -z HEAD^1...HEAD^2") cat "$PATHS" ;;
  "cat-file -e $TIP^{commit}") [ "$TIP_PRESENT" = true ] ;;
  "merge-base --is-ancestor $TIP HEAD^2") [ "$UP_TO_DATE" = true ] ;;
  *) exit 2 ;;
esac
''')
    git.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text('''#!/bin/sh
echo "gh $*" >> "$CALLS"
[ "$*" = "api repos/acme/kge/branches/develop --jq .commit.sha" ] || exit 2
echo "$TIP"
''')
    gh.chmod(0o755)
    output = tmp_path / "output"
    calls = tmp_path / "calls"
    calls.touch()
    done = shell(JOBS["kge-full-scope"]["steps"][1]["run"], tmp_path, EVENT=event, REF=ref, BASE=base, DRAFT=draft, AUDIT=audit,
                 RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(output), PATHS=str(changed),
                 GITHUB_REPOSITORY="acme/kge", GH_TOKEN="stand-in", CALLS=str(calls), TIP="a" * 40,
                 TIP_PRESENT=str(tip_present).lower(), UP_TO_DATE=str(up_to_date).lower(),
                 PATH=f"{bin_dir}:{os.environ['PATH']}")
    return done, output, calls.read_text().splitlines()


def scope(tmp_path, paths=(), event="pull_request", ref="refs/pull/12/merge", base="develop", draft="false", audit="false"):
    done, output, _ = scope_run(tmp_path, paths, event, ref, base, draft, audit)
    assert done.returncode == 0, done.stdout + done.stderr
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_ready_develop_pr_checks_the_current_tip_against_the_head_before_the_diff(tmp_path):
    done, output, calls = scope_run(tmp_path, ["demos/kge/kge/app.py"])
    assert done.returncode == 0, done.stdout + done.stderr
    tip = "a" * 40
    assert calls == ["gh api repos/acme/kge/branches/develop --jq .commit.sha",
                     f"git cat-file -e {tip}^{{commit}}", f"git merge-base --is-ancestor {tip} HEAD^2",
                     "git diff --name-only --no-renames -z HEAD^1...HEAD^2"]
    assert "run=yes" in output.read_text().splitlines()


@pytest.mark.parametrize("tip_present, up_to_date", [(True, False), (False, True)])
def test_ready_develop_pr_behind_or_missing_the_tip_fails_before_starting_checks(tmp_path, tip_present, up_to_date):
    done, output, calls = scope_run(tmp_path, ["demos/kge/kge/app.py"], tip_present=tip_present, up_to_date=up_to_date)
    assert done.returncode == 1
    assert "::error::This pull request is behind develop. Rebase it onto develop" in done.stdout
    assert not output.exists()
    tip = "a" * 40
    expected = ["gh api repos/acme/kge/branches/develop --jq .commit.sha", f"git cat-file -e {tip}^{{commit}}"]
    if tip_present:
        expected.append(f"git merge-base --is-ancestor {tip} HEAD^2")
    assert calls == expected


@pytest.mark.parametrize("event, ref, base, draft, audit", [
    ("pull_request", "refs/pull/12/merge", "develop", "true", "false"),
    ("pull_request", "refs/pull/12/merge", "main", "false", "false"),
    ("push", "refs/heads/develop", "", "", "false"),
    ("push", "refs/heads/main", "", "", "false"),
    ("schedule", "refs/heads/main", "", "", "false"),
    ("schedule", "refs/heads/develop", "", "", "true"),
])
def test_drafts_main_prs_pushes_and_schedules_never_query_the_develop_tip(tmp_path, event, ref, base, draft, audit):
    done, _, calls = scope_run(tmp_path, ["demos/kge/kge/app.py"], event, ref, base, draft, audit,
                               tip_present=False, up_to_date=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls == (["git diff --name-only --no-renames -z HEAD^1...HEAD^2"] if event == "pull_request" else [])


@pytest.mark.parametrize("paths, unit, portal, full, dependencies, terraform", [
    (["docs/proposals/a.md", "docs/reference/a.md", "README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "tools/README.md"], False, False, False, False, False),
    (["docs/diagrams/a.svg"], False, False, False, False, False),
    (["demos/kge/README.md"], True, False, True, False, False),
    (["demos/kge/kge/app.py"], True, False, True, False, False),
    (["demos/kge/portal/ui/src/a.tsx"], False, True, True, False, False),
    (["demos/kge/portal/README.md"], False, True, True, False, False),
    (["demos/kge/portal/ui/src/a.tsx", "README.md"], True, True, True, False, False),
    (["demos/kge/portal/server/package-lock.json"], False, True, True, True, False),
    (["demos/kge/requirements.txt"], True, False, True, True, False),
    (["demos/kge/constraints.txt"], True, False, True, True, False),
    (["demos/kge/pyproject.toml"], True, False, True, True, False),
    (["demos/kge/Dockerfile"], True, False, True, True, False),
    (["demos/kge/portal/Dockerfile"], False, True, True, True, False),
    (["demos/kge/infra/pilot/main.tf"], True, False, True, False, True),
    (["demos/kge/infra/pilot/.terraform.lock.hcl"], True, False, True, False, True),
    (["tools/check_prose.py"], True, False, False, False, False),
    ([".github/workflows/checks.yml"], True, False, True, False, False),
    ([".github/workflows/kit.yml"], True, False, True, False, False),
    ([".github/workflows/kit-demo.yml"], True, False, True, False, False),
    ([".github/workflows/release.yml"], True, False, False, False, False),
    (["demos/kge-lite/a.py"], True, False, False, False, False),
    (["docs/old.md", "demos/kge/new.py"], True, False, True, False, False),
    (["docs/a\nname.md", "docs/$(exit 1).md"], False, False, False, False, False),
])
def test_pr_scope_uses_one_null_delimited_merge_base_diff(tmp_path, paths, unit, portal, full, dependencies, terraform):
    result = scope(tmp_path, paths)
    flags = {"unit": unit, "portal": portal, "run": full, "dependencies": dependencies, "terraform": terraform,
             "security": True, "next": False, "build": full, "analysis": True}
    assert result == {key: "yes" if value else "no" for key, value in flags.items()}


@pytest.mark.parametrize("path", [
    "docs/use-cases/kge/platform/pack-contract.md",
    "docs/use-cases/kge/platform/pack.example.yaml",
    "docs/use-cases/kge/demo/eval/baseline.json",
    ".claude/skills/gcp-pilot-ops/SKILL.md",
    ".claude/skills/kge-code/SKILL.md",
    "tools/disclosure-allow.txt",
])
def test_unit_scope_includes_repository_inputs_read_by_unit_tests(tmp_path, path):
    result = scope(tmp_path, [path])
    assert result["unit"] == "yes"
    assert result["run"] == result["build"] == result["portal"] == "no"


@pytest.mark.parametrize("event, ref, base, draft, unit, next_python, portal, full", [
    ("push", "refs/heads/develop", "", "", True, True, True, True),
    ("push", "refs/heads/main", "", "", True, False, True, False),
    ("schedule", "refs/heads/main", "", "", False, False, False, False),
    ("pull_request", "refs/pull/12/merge", "develop", "true", True, False, False, False),
    ("pull_request", "refs/pull/12/merge", "main", "false", True, False, False, False),
])
def test_events_keep_next_python_on_develop_pushes_and_drafts_skip_full(tmp_path, event, ref, base, draft, unit, next_python, portal, full):
    result = scope(tmp_path, ["demos/kge/kge/app.py"], event, ref, base, draft)
    flags = {"unit": unit, "next": next_python, "portal": portal, "run": full, "build": full,
             "dependencies": event != "pull_request", "terraform": event != "pull_request", "security": True,
             "analysis": event == "pull_request" and base == "develop"}
    assert result == {key: "yes" if value else "no" for key, value in flags.items()}


SCOPED_RESULTS = {"KGE_BUILD": "BUILD_DUE", "KGE_UNIT": "UNIT_DUE", "KGE_NEXT": "NEXT_DUE", "KGE_TESTS": "UNIT_DUE",
                  "KGE_PORTAL": "PORTAL_DUE", "KGE_FULL_LEGS": "FULL_DUE", "KGE_FULL": "FULL_DUE",
                  "TRIVY_VULNERABILITIES": "DEPENDENCIES_DUE", "TRIVY_MISCONFIGURATIONS": "TERRAFORM_DUE",
                  "TRIVY_SECRETS": "SECURITY_DUE", "GITLEAKS": "SECURITY_DUE", "STATIC_ANALYSIS": "ANALYSIS_DUE"}


def gate_env():
    return {key: "yes" if key.endswith("_DUE") else "success" for key in JOBS["gate"]["steps"][0]["env"]}


@pytest.mark.parametrize("result_key", SCOPED_RESULTS)
@pytest.mark.parametrize("due, result, passes", [
    ("yes", "success", True), ("yes", "skipped", False), ("yes", "failure", False), ("yes", "cancelled", False),
    ("no", "skipped", True), ("no", "success", True), ("no", "failure", False), ("no", "cancelled", False), ("", "skipped", False),
])
def test_required_gate_accepts_only_scope_skips_and_never_failures_or_cancellations(tmp_path, result_key, due, result, passes):
    env = gate_env()
    env[SCOPED_RESULTS[result_key]] = due
    env[result_key] = result
    done = shell(JOBS["gate"]["steps"][0]["run"], tmp_path, **env)
    assert (done.returncode == 0) is passes, done.stdout + done.stderr


@pytest.mark.parametrize("key", ["CHECKS", "SCOPE", "SOURCE"])
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_repository_checks_scope_and_source_failures_are_required(tmp_path, key, result):
    env = {**gate_env(), key: result}
    assert shell(JOBS["gate"]["steps"][0]["run"], tmp_path, **env).returncode != 0


def test_docs_only_gate_accepts_all_intended_skips(tmp_path):
    env = gate_env()
    for result, due in SCOPED_RESULTS.items():
        if due != "SECURITY_DUE":
            env[result] = "skipped"
            env[due] = "no"
    env["SOURCE"] = "skipped"
    assert shell(JOBS["gate"]["steps"][0]["run"], tmp_path, **env).returncode == 0


@pytest.mark.parametrize("next_due, result, passes", [("no", "skipped", True), ("yes", "success", True),
                                                     ("yes", "skipped", False), ("no", "failure", False), ("no", "cancelled", False)])
def test_unit_aggregator_requires_next_python_only_when_due(tmp_path, next_due, result, passes):
    step = JOBS["kge-tests"]["steps"][-1]
    done = shell(step["run"], tmp_path, SHARDS="success", NEXT=result, NEXT_DUE=next_due)
    assert (done.returncode == 0) is passes


@pytest.mark.parametrize("unit_due, unit, legs, passes", [
    ("yes", "success", "success", True), ("no", "skipped", "success", True),
    ("yes", "skipped", "success", False), ("no", "failure", "success", False),
    ("no", "cancelled", "success", False), ("yes", "success", "failure", False),
    ("no", "skipped", "cancelled", False), ("no", "skipped", "skipped", False),
])
def test_full_aggregator_requires_all_due_lanes(tmp_path, unit_due, unit, legs, passes):
    done = shell(JOBS["kge-full"]["steps"][-1]["run"], tmp_path, UNIT_DUE=unit_due, UNIT=unit, LEGS=legs)
    assert (done.returncode == 0) is passes


@pytest.mark.parametrize("due, shards, legs, passes", [("yes", 6, 14, True), ("yes", 5, 14, False),
                                                     ("no", 0, 14, True), ("no", 0, 13, False)])
def test_full_coverage_requires_every_due_shard_and_measured_leg(tmp_path, due, shards, legs, passes):
    data = tmp_path / "data"
    for prefix, count in (("unit", shards), ("full", legs)):
        for i in range(count):
            path = data / f"kge-{prefix}-coverage-{i}"
            path.mkdir(parents=True)
            (path / ".coverage").write_text("data")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make = bin_dir / "make"
    make.write_text('#!/bin/sh\n[ "$*" = "coverage-check PY=python MIN=70" ]\n')
    make.chmod(0o755)
    step = next(s for s in JOBS["kge-full"]["steps"] if "make coverage-check" in s.get("run", ""))
    done = shell(step["run"], tmp_path, DATA=str(data), UNIT_DUE=due, PATH=f"{bin_dir}:{os.environ['PATH']}")
    assert (done.returncode == 0) is passes, done.stdout + done.stderr


def test_build_archive_preserves_links_modes_and_isolates_consumer_mutations(tmp_path):
    producer = tmp_path / "producer"
    graph = producer / "build/artifacts/hash/graph"
    graph.parent.mkdir(parents=True)
    graph.write_text("original")
    graph.chmod(0o600)
    (graph.parent.parent / "current").symlink_to("hash", target_is_directory=True)
    archive = next(s["run"] for s in JOBS["kge-build"]["steps"] if "tar -cf" in s.get("run", ""))
    assert shell(archive, producer, RUNNER_TEMP=str(tmp_path)).returncode == 0
    artifact = tmp_path / "kge-build"
    artifact.mkdir()
    shutil.move(tmp_path / "build.tar", artifact / "build.tar")
    for name in ("leg-1", "leg-2"):
        consumer = tmp_path / name
        consumer.mkdir()
        step = next(s["run"] for s in JOBS["kge-full-legs"]["steps"] if "tar -xf" in s.get("run", ""))
        assert shell(step, consumer, RUNNER_TEMP=str(tmp_path)).returncode == 0
        current = consumer / "build/artifacts/current"
        assert current.is_symlink() and (current / "graph").read_text() == "original"
        assert (current / "graph").stat().st_mode & 0o777 == 0o600
        (current / "graph").write_text(name)
    assert graph.read_text() == "original"


@pytest.mark.parametrize("leg", ["integration-1/10", "parity-margin", "e2e-1/4"])
@pytest.mark.parametrize("prebuilt", [False, True])
@pytest.mark.parametrize("ci", ["", "false", "true"])
def test_prebuilt_legs_skip_only_initial_build_and_local_and_kit_legs_still_build(tmp_path, leg, prebuilt, ci):
    graph = tmp_path / "graph"
    graph.touch()
    done = subprocess.run(["make", "-n", "_full-check-leg", f"LEG={leg}", f"PY={sys.executable}", f"GRAPH={graph}",
                           f"PREBUILT={'1' if prebuilt else ''}", f"CI={ci}"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert ("--no-print-directory graph pipeline" in done.stdout) is not (prebuilt and ci == "true")
    target = "test-integration SHARD=1/10" if leg.startswith("integration") else "test-e2e PART=1/4" if leg.startswith("e2e") else "margin REPEATS=1"
    assert target in done.stdout


MARKER_KEYS = {"kge-portal": "kge-portal-node22-${{ needs.kge-full-scope.outputs.portal-hash }}-${{ hashFiles('.github/workflows/checks.yml') }}",
               "kge-unit": "kge-unit-py312-${{ matrix.shard }}-${{ needs.kge-full-scope.outputs.unit-hash }}-${{ hashFiles('.github/workflows/checks.yml') }}",
               "kge-unit-next": "kge-unit-py313-${{ matrix.shard }}-${{ needs.kge-full-scope.outputs.unit-hash }}-${{ hashFiles('.github/workflows/checks.yml') }}",
               "kge-build": "kge-build-py312-${{ needs.kge-full-scope.outputs.build-hash }}-${{ hashFiles('.github/workflows/checks.yml') }}",
               "kge-full-legs": "kge-full-py312-${{ strategy.job-index }}-${{ needs.kge-full-scope.outputs.full-hash }}-${{ hashFiles('.github/workflows/checks.yml') }}"}


def marker_steps(name):
    steps = JOBS[name]["steps"]
    restore = next(s for s in steps if s.get("uses", "").startswith("actions/cache/restore@"))
    reuse = next(s for s in steps if s.get("name") == "Reuse the green result")
    record = next(s for s in steps if s.get("name") == "Record this successful run and its gate inputs")
    save = next(s for s in steps if s.get("uses", "").startswith("actions/cache/save@"))
    return restore, reuse, record, save


@pytest.mark.parametrize("name", sorted(MARKER_KEYS))
def test_markers_reuse_on_pull_requests_alone_and_are_saved_only_on_non_scheduled_success(name):
    assert WORKFLOW["env"] == {"REUSE_DISABLED": "${{ github.event_name != 'pull_request' }}"}, \
        "develop pushes seed afresh and the audit reuses nothing"
    restore, reuse, record, save = marker_steps(name)
    assert restore["id"] == "green" and restore["if"] == "${{ env.REUSE_DISABLED != 'true' }}"
    assert reuse["if"] == "${{ steps.green.outputs.cache-hit == 'true' }}"
    for step in (record, save):
        assert step["if"] == "${{ success() && !cancelled() && github.event_name != 'schedule' && steps.green.outputs.cache-hit != 'true' }}", \
            "no marker on a failed, cancelled or scheduled run"
    assert restore["with"] == {"path": "${{ runner.temp }}/kge-green", "key": MARKER_KEYS[name]}
    assert restore["with"]["key"].endswith("-${{ hashFiles('.github/workflows/checks.yml') }}")
    assert "github.workflow_sha" not in restore["with"]["key"]
    assert save["with"] == restore["with"], "the marker is saved under the key it was looked up by"
    assert record["env"]["SOURCE_RUN"] == \
        "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    assert "GITHUB_STEP_SUMMARY" in reuse["run"] and reuse["env"]["INPUT_HASH"].endswith("-hash }}")


@pytest.mark.parametrize("name", sorted(MARKER_KEYS))
def test_reuse_short_circuits_naming_the_source_run_and_hash_in_the_summary(tmp_path, name):
    green = tmp_path / "kge-green"
    green.mkdir()
    (green / "source").write_text("https://github.example/orca/fde/actions/runs/88\n")
    (green / "build.tar").write_text("tar")
    (green / ".coverage.1").write_text("data")
    summary = tmp_path / "summary"
    work = tmp_path / "work"
    work.mkdir()
    done = shell(marker_steps(name)[1]["run"], work, RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(summary),
                 INPUT_HASH="1234abcd")
    assert done.returncode == 0, done.stdout + done.stderr
    assert summary.read_text() == "reused: https://github.example/orca/fde/actions/runs/88 1234abcd\n"
    if name in ("kge-unit", "kge-full-legs"):
        assert (work / ".coverage.1").read_text() == "data", "restored coverage still reaches the combining jobs"
    if name == "kge-build":
        assert (tmp_path / "build.tar").read_text() == "tar", "the recorded build still reaches the artifact upload"


def test_reuse_without_a_recorded_source_fails_instead_of_passing_silently(tmp_path):
    green = tmp_path / "kge-green"
    green.mkdir()
    (green / "source").write_text("")
    summary = tmp_path / "summary"
    work = tmp_path / "work"
    work.mkdir()
    done = shell(marker_steps("kge-unit")[1]["run"], work, RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(summary),
                 INPUT_HASH="1234abcd")
    assert done.returncode != 0
    assert not summary.exists()


def test_the_weekly_audit_runs_every_family_and_is_named_on_develop(tmp_path):
    result = scope(tmp_path, [], event="schedule", ref="refs/heads/main", base="", draft="", audit="true")
    keys = ("unit", "next", "portal", "run", "build", "dependencies", "terraform", "security", "analysis")
    assert result == {key: "yes" for key in keys}
    assert WORKFLOW["run-name"] == ("${{ github.event_name == 'schedule' && (github.event.schedule == '23 6 * * 1' && "
                                    "'Weekly develop audit (reuse disabled)' || 'Daily security scans') || '' }}")
    assert JOBS["kge-full-scope"]["steps"][0]["with"]["ref"] == \
        "${{ github.event_name == 'schedule' && github.event.schedule == '23 6 * * 1' && 'develop' || '' }}"


def test_workflow_wiring_keeps_the_single_required_status_and_pr_only_cancellation():
    on = WORKFLOW.get("on", WORKFLOW.get(True))
    assert on["schedule"] == [{"cron": "23 6 * * *"}, {"cron": "23 6 * * 1"}]
    assert "paths" not in on["pull_request"]
    assert WORKFLOW["concurrency"] == {"group": "checks-${{ github.event_name }}-${{ github.event.pull_request.number || github.run_id }}",
                                       "cancel-in-progress": "${{ github.event_name == 'pull_request' }}"}
    for name, output in (("kge-unit", "unit"), ("kge-unit-next", "next"), ("kge-portal", "portal"),
                         ("kge-build", "build"), ("kge-full-legs", "run"), ("trivy-vulnerabilities", "dependencies"),
                         ("trivy-misconfigurations", "terraform"), ("trivy-secrets", "security"), ("gitleaks", "security"),
                         ("static-analysis", "analysis")):
        assert "kge-full-scope" in JOBS[name]["needs"]
        assert JOBS[name]["if"] == "${{ needs.kge-full-scope.outputs." + output + " == 'yes' }}"
    for name in ("kge-unit", "kge-unit-next"):
        assert JOBS[name]["needs"] == "kge-full-scope"
        assert not any(s.get("uses", "").startswith("actions/download-artifact@") or "tar -xf" in s.get("run", "")
                       for s in JOBS[name]["steps"]), "the unit lane stays build-free in drafts and ready PRs"
    assert JOBS["gate"]["name"] == "Checks passed" and JOBS["gate"]["if"] == "${{ always() }}"
    assert set(JOBS["gate"]["needs"]) == set(JOBS) - {"gate"}
    assert len(JOBS["check"]["strategy"]["matrix"]["check"]) == 8 and "if" not in JOBS["check"]
    assert "PREBUILT=1" in next(s["run"] for s in JOBS["kge-full-legs"]["steps"] if "full-check-leg" in s.get("run", ""))


def test_stale_runs_cancels_only_active_checks_for_behind_ready_develop_prs():
    workflow = yaml.safe_load((ROOT.parents[1] / ".github/workflows/stale-runs.yml").read_text())
    assert workflow.get("on", workflow.get(True)) == {"push": {"branches": ["develop"]}}
    assert workflow["permissions"] == {"actions": "write", "pull-requests": "read", "contents": "read"}
    assert set(workflow["jobs"]) == {"cancel"}
    job = workflow["jobs"]["cancel"]
    assert 0 < job["timeout-minutes"] <= 5
    assert "permissions" not in job and "env" not in job
    assert len(job["steps"]) == 1
    step = job["steps"][0]
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}", "SHA": "${{ github.sha }}"}
    run = step["run"]
    assert "${{" not in run, "event data reaches the shell through environment variables"
    assert 'gh pr list -R "$GITHUB_REPOSITORY" --state open --limit 200 --json number,headRefOid,isDraft,baseRefName' in run
    assert 'select(.isDraft == false and .baseRefName == "develop") | [.number, .headRefOid] | @tsv' in run
    assert "group_by(.headRefOid) | .[] | select(length > 1)" in run
    assert run.index('-ge 200 ]; then') < run.index("gh run cancel")
    assert run.index('grep -qx "$HEAD" <<< "$shared"') < run.index("gh run cancel")
    assert 'gh api "repos/$GITHUB_REPOSITORY/compare/$SHA...$HEAD" --jq .behind_by' in run
    assert 'if [ "$behind" = 0 ]; then continue; fi' in run
    assert 'gh run list -R "$GITHUB_REPOSITORY" --workflow checks.yml --commit "$HEAD" --json databaseId,status,event' in run
    assert 'select(.event == "pull_request")' in run
    assert 'select(.event == "pull_request") | select(.status != "completed") | .databaseId' in run
    assert 'gh run cancel "$run" -R "$GITHUB_REPOSITORY"' in run


def test_every_checkout_pins_the_event_sha_except_the_weekly_audits_develop_tree():
    for name, job in JOBS.items():
        for step in job.get("steps", []):
            if not step.get("uses", "").startswith("actions/checkout@"):
                continue
            ref = step.get("with", {}).get("ref")
            if name == "kge-full-scope":
                assert ref == "${{ github.event_name == 'schedule' && github.event.schedule == '23 6 * * 1' && 'develop' || '' }}"
            else:
                assert ref == "${{ github.event_name == 'schedule' && needs.kge-full-scope.outputs.sha || github.sha }}", name
    provenance = next(s for s in JOBS["kge-full-scope"]["steps"] if s.get("id") == "provenance")
    assert 'echo "sha=$(git rev-parse HEAD)"' in provenance["run"]
    assert JOBS["kge-full-scope"]["outputs"]["sha"] == "${{ steps.provenance.outputs.sha }}"


@pytest.mark.parametrize("directory", ["/demos/kge/portal/server", "/demos/kge/portal/ui"])
def test_dependabot_groups_each_portal_packages_weekly_updates_into_develop(directory):
    updates = yaml.safe_load((ROOT.parents[1] / ".github/dependabot.yml").read_text())["updates"]
    pip = next(update for update in updates if update["package-ecosystem"] == "pip")
    npm = next(update for update in updates if update["package-ecosystem"] == "npm" and update["directory"] == directory)
    for key in ("schedule", "groups", "target-branch", "commit-message", "labels"):
        assert npm[key] == pip[key]


@pytest.mark.parametrize("directory", ["/demos/kge", "/demos/kge/portal"])
def test_dependabot_covers_both_dockerfiles_weekly_into_develop(directory):
    updates = yaml.safe_load((ROOT.parents[1] / ".github/dependabot.yml").read_text())["updates"]
    entry = next(update for update in updates if update["package-ecosystem"] == "docker" and update["directory"] == directory)
    assert entry["schedule"] == {"interval": "weekly"}
    assert entry["target-branch"] == "develop"
    assert entry["groups"] == {"base-image": {"patterns": ["*"]}}


def test_static_analysis_fails_on_findings_and_uploads_both_reports_even_after_failure():
    job = JOBS["static-analysis"]
    steps = job["steps"]
    install = next(step["run"] for step in steps if "pip install" in step.get("run", ""))
    assert 'python -m venv "$RUNNER_TEMP/scanners"' in install
    assert '"$RUNNER_TEMP/scanners/bin/python" -m pip install bandit==1.9.4 semgrep==1.181.1' in install
    bandit = next(step for step in steps if '/bin/bandit"' in step.get("run", ""))
    semgrep = next(step for step in steps if '/bin/semgrep"' in step.get("run", ""))
    assert '-r demos/kge/kge -ll' in bandit["run"] and '-f json' in bandit["run"]
    assert '-c demos/kge/pyproject.toml' in bandit["run"]
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert config['tool']['bandit']['skips'] == ['B608', 'B610']
    for option in ("scan --error", "--config p/python --config p/javascript", "--severity WARNING --severity ERROR",
                   "--metrics off", "--json", "demos/kge/kge demos/kge/portal/server/src demos/kge/portal/ui/src"):
        assert option in semgrep["run"]
    assert "--exclude-rule python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1" in semgrep["run"]
    assert not any(step.get("continue-on-error") for step in steps)
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert semgrep["if"] == upload["if"] == "${{ !cancelled() }}"
    assert upload["with"]["path"].splitlines() == ["${{ runner.temp }}/bandit.json", "${{ runner.temp }}/semgrep.json"]
    assert upload["with"]["if-no-files-found"] == "error"
