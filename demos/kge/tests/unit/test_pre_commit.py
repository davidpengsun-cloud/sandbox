"""Local hooks keep CI's checks; the push hooks are the local gate's stamp and the source guards, run every time."""

import os
import re
import shlex
import subprocess

import pytest
import yaml

from kge.pipeline.settings import ROOT


REPO = ROOT.parents[1]
CONFIG = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
HOOKS = {hook["id"]: hook for repo in CONFIG["repos"] for hook in repo["hooks"]}


def test_hooks_match_ci_scripts_and_pins():
    workflow = yaml.safe_load((REPO / ".github/workflows/checks.yml").read_text())
    entries = [step["run"] for step in workflow["jobs"]["repository"]["steps"]
               if step.get("run", "").startswith("python3 tools/")]
    assert len(entries) == 8
    for entry in entries:
        assert any(hook["entry"] == entry for hook in HOOKS.values() if "entry" in hook), entry
    pins = dict(line.split("==") for line in (ROOT / "constraints.txt").read_text().splitlines()
                if "==" in line and not line.startswith("#"))
    repos = {repo["repo"].rsplit("/", 1)[-1]: repo for repo in CONFIG["repos"]}
    assert repos["ruff-pre-commit"]["rev"] == f"v{pins['ruff']}"
    assert HOOKS["ruff"]["args"] == ["--config", "demos/kge/ruff.toml"]
    assert "ruff-format" not in HOOKS
    assert repos["gitleaks"]["rev"] == "v8.30.1"
    assert CONFIG["default_stages"] == ["pre-commit"]


@pytest.mark.parametrize("path", [
    ".fixture/art.html",
    "docs/proposals/kge/agent-ready-document-tagging.html",
    "docs/reference/diagrams/gcp-document-tagging.svg",
    "demos/kge/tests/test_context.py",
    "demos/kge/tests/test_agent_loop.py",
    "demos/kge/tests/fixtures/backend_branch/kge/estate/evade.py",
    "demos/kge/portal/server/test/fixtures/logo-probes.json",
    "demos/kge/clients/meridian/fixture/golden.json",
    "demos/kge/THIRD_PARTY_NOTICES.md",
    "demos/kge/portal/THIRD_PARTY_NOTICES.md",
    "demos/kge/ui/FONT_NOTICES.md",
    "demos/kge/standards/NOTICES.md",
])
def test_fixers_preserve_byte_compared_files(path):
    for name in ("end-of-file-fixer", "trailing-whitespace"):
        assert re.search(HOOKS[name]["exclude"], path), (name, path)
    assert not CONFIG.get("exclude"), "security checks must still see byte-compared files"


@pytest.mark.parametrize("name", ["check-figures", "check-disclosure", "check-links", "check-prose", "check-no-comments"])
def test_checks_that_accept_paths_receive_staged_files(name):
    assert HOOKS[name].get("pass_filenames", True)


@pytest.mark.parametrize("name", ["check-structure", "check-decks", "check-diagrams"])
def test_whole_repository_checks_run_without_filename_arguments(name):
    assert HOOKS[name]["pass_filenames"] is False
    assert HOOKS[name]["always_run"] is True


def test_links_skip_the_intentionally_broken_checker_fixtures():
    exclude = HOOKS["check-links"]["exclude"]
    assert re.search(exclude, ".fixture/source.md")
    assert re.search(exclude, ".fixture/art.html")
    assert not re.search(exclude, "docs/reference/new-document.md")


def test_push_hooks_are_the_source_guards_alone():
    pushed = [name for name, hook in HOOKS.items() if "pre-push" in hook.get("stages", [])]
    assert pushed == ["pytest-source-guards"], pushed
    text = (REPO / ".pre-commit-config.yaml").read_text()
    assert "testmon" not in text
    assert "gate_stamp.py check" not in text


@pytest.mark.parametrize("name,planner,status", [
    ("pytest-source-guards", False, 0),
    ("pytest-source-guards", True, 0),
    ("pytest-source-guards", True, 1),
])
def test_push_hooks_run_from_demo_offline_and_propagate_failures(tmp_path, name, planner, status):
    demo = tmp_path / "demos/kge"
    unit = demo / "tests/unit"
    unit.mkdir(parents=True)
    if planner:
        (unit / "test_planner_nouns.py").touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$MODEL_PROVIDER" "$@" > "$HOOK_LOG"\nexit "$HOOK_STATUS"\n')
    python.chmod(0o755)
    log = tmp_path / "hook-log"
    hook = HOOKS[name]
    assert hook["stages"] == ["pre-push"]
    assert hook["always_run"] and hook["pass_filenames"] is False
    assert not CONFIG.get("fail_fast", False)
    done = subprocess.run(shlex.split(hook["entry"]), cwd=tmp_path, capture_output=True, text=True, timeout=10,
                          env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MODEL_PROVIDER": "mock",
                               "HOOK_LOG": str(log), "HOOK_STATUS": str(status)})
    assert done.returncode == status, done.stderr
    cwd, provider, *args = log.read_text().splitlines()
    assert os.path.samefile(cwd, demo)
    assert provider == "none"
    assert args[:2] == ["-m", "pytest"]
    assert args[2:] == [f"tests/unit/test_{guard}.py" for guard in
                        ("scope_paths", "layers", "no_comments", "api_surface", *(["planner_nouns"] if planner else []))]


def test_dev_tooling_is_pinned_and_old_testmon_data_stays_ignored():
    requirements = (ROOT / "requirements-dev.txt").read_text().splitlines()
    constraints = (ROOT / "constraints.txt").read_text().splitlines()
    pin = next(line for line in constraints if line.startswith("pre-commit=="))
    assert pin in requirements
    assert not any(line.startswith("pytest-testmon") for line in requirements + constraints)
    assert ".testmondata*" in (REPO / ".gitignore").read_text().splitlines()
    for path in (REPO / "CONTRIBUTING.md", REPO / ".claude/skills/kge-checks/SKILL.md"):
        assert "pre-commit install --hook-type pre-commit --hook-type pre-push" in path.read_text()
