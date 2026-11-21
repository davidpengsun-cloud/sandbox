"""Each leg of the local gate stamps the inputs it passed on, on a clean commit, and the push check accepts a commit only
when every leg its change needs, of those the commit's own Makefile lists, holds a stamp for that commit's inputs."""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from kge.pipeline.settings import ROOT
from tests import gate_stamp
from tests.unit.test_checks import SCOPE_ROWS, scope

SCRIPT = ROOT / "tests" / "gate_stamp.py"


def run(repo, action, *args, **env):
    base = {key: value for key, value in os.environ.items() if key not in {"GATE_OVERRIDES", "PYTEST_ADDOPTS", "MAKEFLAGS", "MFLAGS"}}
    return subprocess.run([sys.executable, str(SCRIPT), action, *args], cwd=repo, capture_output=True, text=True, timeout=30,
                          env={**base, **env})


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


LEGS = "LOCAL_GATE_LEGS := lint:a.txt,shared whole:.\n"


def gate(cwd, *legs, **env):
    """Every leg run and passed, as make local-gate brackets each one; the legs of the fixture's Makefile by default."""
    for leg in legs or ("lint", "whole"):
        run(cwd, "start", leg, **env)
        run(cwd, "finish", leg, **env)


def stamps(cwd):
    return sorted(str(path.relative_to(cwd / "build/gate-stamps")) for path in (cwd / "build/gate-stamps").glob("*/*")
                  if path.parent.name != ".pending")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "gate@example.invalid")
    git(tmp_path, "config", "user.name", "Gate")
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "Makefile").write_text(LEGS)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "one")
    return tmp_path


def test_each_leg_started_and_finished_on_a_clean_commit_stamps_its_inputs_and_the_check_needs_every_leg(repo):
    assert run(repo, "check").returncode == 1
    assert run(repo, "start", "lint").returncode == 0
    assert run(repo, "finish", "lint").returncode == 0
    head = git(repo, "rev-parse", "HEAD")
    [lint] = stamps(repo)
    record = json.loads((repo / "build/gate-stamps" / lint).read_text())
    assert lint.startswith("lint/") and record["key"] == lint.split("/")[1] and record["leg"] == "lint"
    assert record["commit"] == head and record["inputs"] == ["a.txt", "shared"] and record["passed"]
    done = run(repo, "check")
    assert done.returncode == 1 and "whole" in done.stderr and "lint" not in done.stderr.split("for ", 1)[1], \
        "one leg of two is not the gate"
    gate(repo, "whole")
    done = run(repo, "check")
    assert done.returncode == 0 and head in done.stdout


def test_a_change_inside_a_legs_inputs_needs_a_fresh_stamp_and_a_change_outside_keeps_it(repo):
    gate(repo)
    (repo / "b.txt").write_text("outside the lint leg's paths\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "two")
    done = run(repo, "check")
    assert done.returncode == 1 and done.stderr.rstrip().split("for ", 1)[1].startswith("whole:"), done.stderr
    gate(repo, "whole")
    assert run(repo, "check").returncode == 0, "the lint leg's stamp still holds: nothing under its paths changed"
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "three")
    assert run(repo, "check").returncode == 1
    gate(repo, "whole")
    assert run(repo, "check").returncode == 1, "the lint leg's input changed, so it needs a run of its own"
    gate(repo, "lint")
    assert run(repo, "check").returncode == 0


def test_a_new_file_at_a_declared_path_that_was_missing_needs_a_fresh_stamp(repo):
    gate(repo)
    (repo / "shared").mkdir()
    (repo / "shared" / "c.txt").write_text("new\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "shared")
    gate(repo, "whole")
    assert run(repo, "check").returncode == 1


def test_a_commit_with_the_same_inputs_keeps_its_stamps_and_another_tree_does_not(repo):
    gate(repo)
    git(repo, "commit", "-q", "--amend", "-m", "the same tree, another commit, as after a rebase")
    assert run(repo, "check").returncode == 0
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "two")
    assert run(repo, "check").returncode == 1


def test_the_required_legs_are_the_pushed_commits_own_list(repo):
    gate(repo)
    (repo / "Makefile").write_text("LOCAL_GATE_LEGS := lint:a.txt,shared whole:. extra:a.txt\n")
    git(repo, "commit", "-q", "-am", "a third leg")
    gate(repo, "lint", "whole")
    done = run(repo, "check")
    assert done.returncode == 1 and "extra" in done.stderr
    gate(repo, "extra")
    assert run(repo, "check").returncode == 0


@pytest.mark.parametrize("line", ["", "LOCAL_GATE_LEGS :=\n", "LOCAL_GATE_LEGS := lint whole:.\n",
                                  "LOCAL_GATE_LEGS := lint:../x whole:.\n", "LOCAL_GATE_LEGS := lint:a.txt lint:.\n",
                                  "LOCAL_GATE_LEGS := ../lint:a.txt\n"])
def test_a_list_that_does_not_read_vouches_for_nothing(repo, line):
    (repo / "Makefile").write_text(line)
    git(repo, "commit", "-q", "-am", "list")
    gate(repo, "lint", "whole")
    assert run(repo, "check").returncode == 1


def test_a_leg_not_on_heads_list_or_not_a_name_is_never_stamped(repo):
    gate(repo, "other")
    assert stamps(repo) == []
    assert run(repo, "start", "../escape").returncode == 2
    assert run(repo, "finish", "../escape").returncode == 2


@pytest.mark.parametrize("change", ["modified", "untracked"])
def test_a_dirty_tree_is_never_stamped_and_fails_the_check(repo, change):
    target = repo / ("a.txt" if change == "modified" else "b.txt")
    target.write_text("dirty\n")
    gate(repo)
    assert stamps(repo) == []
    target.unlink() if change == "untracked" else git(repo, "checkout", "-q", "a.txt")
    gate(repo)
    assert run(repo, "check").returncode == 0
    target.write_text("dirty again\n")
    assert run(repo, "check").returncode == 1, "a stamped commit with changes on top is not what passed"


def test_a_tree_changed_during_a_leg_or_a_commit_moved_is_not_stamped(repo):
    run(repo, "start", "lint")
    (repo / "a.txt").write_text("mid-run\n")
    run(repo, "finish", "lint")
    assert stamps(repo) == []
    git(repo, "checkout", "-q", "a.txt")
    run(repo, "start", "lint")
    (repo / "a.txt").write_text("three\n")
    git(repo, "commit", "-q", "-am", "three")
    run(repo, "finish", "lint")
    assert stamps(repo) == []


def test_finish_without_start_or_after_another_legs_start_stamps_nothing(repo):
    run(repo, "finish", "lint")
    run(repo, "start", "whole")
    run(repo, "finish", "lint")
    assert stamps(repo) == []


def make_out(*args, **env):
    base = {key: value for key, value in os.environ.items() if key not in {"LEG", "MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
    return subprocess.run(["make", "-s", *args], cwd=ROOT, capture_output=True, text=True, timeout=60, env={**base, **env})


def test_the_makefile_lists_the_local_gates_legs_once_and_brackets_each_with_its_stamp():
    make = (ROOT / "Makefile").read_text()
    assert len(re.findall(r"^LOCAL_GATE_LEGS\b", make, re.M)) == 1 and "LOCAL_GATE_LEGS +=" not in make
    entries = make_out("gate-legs").stdout.split()
    sys.path.insert(0, str(ROOT / "tests"))
    import gate_stamp
    listed = gate_stamp.LEGS.search(make).group(1).replace("\\\n", " ").split()
    assert entries == listed, "what make expands is what the push check reads"
    full = re.search(r"^FULL_LEGS := (.+)$", make, re.M).group(1).split()
    assert [entry.split(":")[0] for entry in entries] == ["lint", "repository-checks", "portal-tests", *full, "gate-coverage"], \
        "the stamped gate is the whole local gate: lint, repository checks, portal tests, every leg of the full check " \
        "and its coverage gate"
    inputs = dict(entry.split(":") for entry in entries)
    assert all(inputs[leg] == "." for leg in full if not leg.startswith("e2e-")), \
        "the unit and integration tests and parity read files across the repository"
    assert all(inputs[leg] == "demos/kge" for leg in full if leg.startswith("e2e-"))
    assert inputs["repository-checks"] == "." and inputs["gate-coverage"] == "."
    assert inputs["lint"] == "demos/kge" and inputs["portal-tests"] == "demos/kge"
    recipe = make.split("\nlocal-gate:\n", 1)[1].split("\n\n", 1)[0]
    assert "for leg in $(GATE_RUN_LEGS); do \\\n\t  $(PY) tests/gate_stamp.py start $$leg || exit 1; \\\n" in recipe, \
        "a leg notes its start before its build, so a build of a dirty tree stamps nothing"
    assert "\t  $(PY) tests/timing.py leg local-$$leg $(MAKE) --no-print-directory $$target && \\\n" \
           "\t  $(PY) tests/gate_stamp.py finish $$leg $$data || exit 1; \\\n\tdone" in recipe, \
        "a leg is stamped only straight after it passed, and a failing leg stops the run"
    assert 'target="full-check-leg LEG=$$leg GATE_BUILT=$$built"' in recipe and "data=.coverage" in recipe
    assert 'if [ "$$built" != "$$head" ]; then fresh=; $(PY) tests/gate_stamp.py clean && fresh=$$head; \\\n' \
           "\t        $(MAKE) --no-print-directory graph pipeline || exit 1; built=$$fresh; fi" in recipe, \
        "one build per run, made again when HEAD moved, and never reused when it was made from a dirty tree"
    assert ".PHONY:" in make and {"gate-legs", "gate-coverage", "parity-margin"} <= \
        set(re.search(r"^\.PHONY: (.+)$", make, re.M).group(1).split())
    leg = make.split("\n_full-check-leg:\n", 1)[1].split("\n\n", 1)[0]
    assert 'test "$(GATE_BUILT)" = "$$(git rev-parse HEAD)" && test -f "$(GRAPH)"' in leg
    coverage = make.split("\ngate-coverage:\n", 1)[1].split("\n\n", 1)[0]
    assert "tests/gate_stamp.py data $(filter-out parity-margin,$(FULL_LEGS))" in coverage
    assert "coverage-check MIN=$(MIN)" in coverage
    refused = make_out("local-gate", LEG="bogus")
    assert refused.returncode == 2 and "not 'bogus'" in refused.stderr
    narrow = gate_stamp.NARROW.search(make).group(1).replace("\\\n", " ").split()
    assert [entry.split(":")[0] for entry in narrow] == ["infra-tests", "kit-tests", "doc-tests", "portal-unit-tests"] and all(entry.endswith(":.") for entry in narrow)
    assert "GATE_RUN_LEGS = $(or $(strip $(LEG)),$(GATE_LEG_NAMES))" in make, "make local-gate alone runs the gate legs, never the narrow ones"
    assert "case ' $(GATE_LEG_NAMES) $(GATE_NARROW_NAMES) ' in" in recipe, "LEG may name a narrow leg"
    for name, files in (("infra-tests", "INFRA_TESTS"), ("kit-tests", "KIT_TESTS"), ("doc-tests", "DOC_TESTS"),
                        ("portal-unit-tests", "PORTAL_UNIT_TESTS")):
        assert f"\n{name}:\n\tMODEL_PROVIDER=none $(PY) -m pytest $({files}) -q -m unit -n auto\n" in make
        assert name in re.search(r"^\.PHONY: (.+)$", make, re.M).group(1).split()
    assert "gate-stamp-check:\n\t@$(PY) tests/gate_stamp.py check" in make
    checks = make.split("\nrepository-checks:\n", 1)[1].split("\n\n", 1)[0]
    for script in ("figures", "structure", "disclosure", "links", "decks", "prose", "no_comments"):
        assert script in checks
    assert "tools/drawio_to_svg.py --check" in checks
    portal = make.split("\nportal-tests:\n", 1)[1].split("\n\n", 1)[0]
    assert "npm test --prefix portal/server" in portal and "npm test --prefix portal/ui" in portal


def test_a_list_over_several_lines_with_sharded_legs_reads_whole_and_each_shard_is_its_own_leg(repo):
    (repo / "Makefile").write_text("LOCAL_GATE_LEGS := lint:a.txt \\\n  unit-1/2:. unit-2/2:. \\\n  cover:.\nOTHER := x\n")
    git(repo, "commit", "-q", "-am", "shards")
    gate(repo, "lint", "unit-1/2", "cover")
    done = run(repo, "check")
    assert done.returncode == 1 and done.stderr.split("for ", 1)[1].startswith("unit-2/2:"), done.stderr
    gate(repo, "unit-2/2")
    assert run(repo, "check").returncode == 0
    assert stamps(repo)[0].startswith("cover/") and any(stamp.startswith("unit-1_2/") for stamp in stamps(repo))


def test_a_leg_keeps_its_coverage_data_and_data_lists_it_only_for_its_inputs_in_head(repo):
    (repo / "Makefile").write_text("LOCAL_GATE_LEGS := unit-1/2:a.txt unit-2/2:. cover:.\n")
    git(repo, "commit", "-q", "-am", "shards")
    (repo / "build").mkdir(exist_ok=True)
    (repo / "build" / "one.cov").write_text("one\n")
    run(repo, "start", "unit-1/2")
    run(repo, "finish", "unit-1/2", "build/one.cov")
    assert run(repo, "data", "unit-1/2", "unit-2/2").returncode == 1, "a leg with no pass has no data"
    run(repo, "start", "unit-2/2")
    run(repo, "finish", "unit-2/2")
    assert run(repo, "data", "unit-2/2").returncode == 1, "a pass that kept no data has none to give"
    run(repo, "start", "unit-2/2")
    run(repo, "finish", "unit-2/2", "build/one.cov")
    done = run(repo, "data", "unit-1/2", "unit-2/2")
    assert done.returncode == 0
    kept = done.stdout.split()
    assert len(kept) == 2 and all((repo / path).read_text() == "one\n" for path in kept)
    (repo / "b.txt").write_text("outside unit-1/2's input\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "two")
    assert run(repo, "data", "unit-1/2").returncode == 0
    assert run(repo, "data", "unit-2/2").returncode == 1, "data kept on other inputs is not this commit's"
    assert run(repo, "data", "nope").returncode == 1


def test_clean_says_whether_a_build_made_now_may_serve_later_legs(repo):
    assert run(repo, "clean").returncode == 0
    assert run(repo, "clean", GATE_OVERRIDES="SHARD").returncode == 1
    assert run(repo, "clean", MAKEFLAGS="n").returncode == 1
    (repo / "a.txt").write_text("dirty\n")
    assert run(repo, "clean").returncode == 1


def test_a_leg_given_a_gate_build_of_another_commit_refuses_before_any_test(tmp_path):
    done = make_out("_full-check-leg", "LEG=parity-margin", f"GATE_BUILT={'0' * 40}", f"PY={sys.executable}",
                    f"COVERAGE_FILE={tmp_path / '.coverage'}")
    assert done.returncode != 0 and "the local gate's build is not of HEAD" in done.stderr, done.stderr


@pytest.mark.parametrize("env", [{"GATE_OVERRIDES": "SHARD"}, {"GATE_OVERRIDES": "MIN PART"}, {"PYTEST_ADDOPTS": "-k parity"}])
def test_a_narrowed_run_stamps_nothing(repo, env):
    gate(repo, **env)
    assert stamps(repo) == []
    run(repo, "start", "lint", **env)
    done = run(repo, "finish", "lint")
    assert run(repo, "check").returncode == 1, "a run that started narrowed is not stamped, whatever finish sees"
    assert "not stamped" in done.stderr


@pytest.mark.parametrize("args, named", [([], ""), (["SHARD=1/2"], "SHARD"), (["MIN=10"], "MIN"), (["PART=1/4"], "PART"),
                                         (["PY=python"], ""), (["FROM=x", "PY=python"], "FROM"),
                                         (["LEG=lint"], ""), (["LEG=unit-1/6", "SHARD=1/2"], "SHARD"),
                                         (["GATE_BUILT=abc"], "GATE_BUILT")])
def test_the_makefile_names_each_override_of_a_local_gate_run(args, named):
    watched = re.search(r"^GATE_WATCHED := (.+)$", (ROOT / "Makefile").read_text(), re.M).group(1).split()
    env = {key: value for key, value in os.environ.items() if key not in {*watched, "MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
    done = subprocess.run(["make", "-s", "gate-overrides", *args], cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == named.split()
    env["PART"] = "2/4"
    assert "PART" in subprocess.run(["make", "-s", "gate-overrides"], cwd=ROOT, capture_output=True, text=True, timeout=60,
                                    env=env).stdout.split(), "an override from the environment counts too"
    make = (ROOT / "Makefile").read_text()
    assert "local-gate: export GATE_OVERRIDES := $(GATE_OVERRIDES)" in make


@pytest.mark.parametrize("name", ["INFRA_TESTS", "KIT_TESTS", "DOC_TESTS", "PORTAL_UNIT_TESTS"])
def test_a_narrow_legs_file_list_from_the_environment_under_make_e_narrows_the_run(name):
    """Named in GATE_OVERRIDES, and make -e stamps nothing in any case (gate_stamp.lax_make), since the environment
    could replace GATE_OVERRIDES itself."""
    watched = re.search(r"^GATE_WATCHED := (.+)$", (ROOT / "Makefile").read_text(), re.M).group(1).split()
    env = {key: value for key, value in os.environ.items()
           if key not in {*watched, "GATE_OVERRIDES", "MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
    env[name] = "tests/unit/test_layers.py"
    plain = subprocess.run(["make", "-s", "gate-overrides"], cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
    assert plain.stdout.split() == [], "without -e the Makefile's own list wins"
    forced = subprocess.run(["make", "-e", "-s", "gate-overrides"], cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
    assert forced.stdout.split() == [name], "make -e lets the environment replace the list, which narrows the leg"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_a_tracked_file_git_is_told_not_to_look_at_fails_the_check_and_is_never_stamped(repo, flag):
    gate(repo)
    assert run(repo, "check").returncode == 0
    git(repo, "update-index", flag, "a.txt")
    (repo / "a.txt").write_text("changed but hidden\n")
    assert git(repo, "status", "--porcelain") == "", "git status cannot see the change"
    done = run(repo, "check")
    assert done.returncode == 1 and "assume-unchanged or skip-worktree" in done.stderr
    shutil.rmtree(repo / "build/gate-stamps")
    gate(repo)
    assert stamps(repo) == []


def test_check_takes_the_pushed_commit_and_a_deleted_ref_passes(repo):
    gate(repo)
    stamped = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "two")
    assert run(repo, "check", stamped).returncode == 0, "the pushed commit, not HEAD, is the one checked"
    assert run(repo, "check", git(repo, "rev-parse", "HEAD")).returncode == 1
    assert run(repo, "check", "0" * 40).returncode == 0, "a branch deletion pushes no commit"


def test_a_commit_already_in_origin_develop_passes_the_push_check_without_a_stamp(repo):
    develop = git(repo, "rev-parse", "HEAD")
    assert run(repo, "check", develop).returncode == 1
    git(repo, "update-ref", "refs/remotes/origin/develop", develop)
    done = run(repo, "check", develop)
    assert done.returncode == 0 and "already in origin/develop" in done.stdout, \
        "a branch restarted from develop after a squash pushes develop's tip, which its ready run checked"
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "two")
    assert run(repo, "check").returncode == 1, "a commit beyond develop still needs its own stamp"


@pytest.mark.parametrize("flags, lax", [("is", True), ("ks", True), ("iks", True), ("s -k", True), ("--keep-going", True),
                                        ("s --ignore-errors", True), ("s", False), ("", False),
                                        ("s -j2 --jobserver-auth=fifo:/tmp/x", False), ("w --no-print-directory", False),
                                        ("s -- FLAG=-k", False), ("n", True), ("ns", True), ("t", True), ("q", True),
                                        ("s --just-print", True), ("--dry-run", True), ("--touch", True), ("--question", True),
                                        ("e", True), ("es", True), ("s -e", True), ("--environment-overrides", True)])
def test_make_with_i_or_k_stamps_nothing(repo, flags, lax):
    gate(repo, MAKEFLAGS=flags)
    assert (run(repo, "check").returncode == 1) is lax, "make -i or -k would go on past a failing full check"


@pytest.mark.parametrize("option", ["-i", "-k", "-n", "-t"])
def test_a_real_make_i_k_n_or_t_run_stamps_nothing(repo, option):
    """The legs run as the gate runs them, in one + line with the recursive make, which make runs even under -n or -t."""
    (repo / "build").mkdir(exist_ok=True)
    loop = (f"\t+@for leg in lint whole; do {sys.executable} {SCRIPT} start $$leg && $(MAKE) -s -f build/Makefile $$leg && "
            f"{sys.executable} {SCRIPT} finish $$leg || exit 1; done\n")
    (repo / "build" / "Makefile").write_text(f".PHONY: gate lint whole\ngate:\n{loop}lint whole:\n\t@{{step}}\n")
    env = {key: value for key, value in os.environ.items() if key not in {"MAKEFLAGS", "MFLAGS", "GATE_OVERRIDES"}}
    makefile = (repo / "build" / "Makefile").read_text()
    (repo / "build" / "Makefile").write_text(makefile.format(step="false"))
    subprocess.run(["make", option, "-s", "-f", "build/Makefile", "gate"], cwd=repo, capture_output=True, timeout=60, env=env)
    assert stamps(repo) == []
    (repo / "build" / "Makefile").write_text(makefile.format(step="true"))
    subprocess.run(["make", "-s", "-f", "build/Makefile", "gate"], cwd=repo, capture_output=True, timeout=60, env=env)
    assert run(repo, "check").returncode == 0, "the same steps under a plain make stamp"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_a_hidden_file_outside_the_demos_folder_fails_the_check_run_from_inside_it(repo, flag):
    """The gate runs in demos/kge, where git ls-files lists that folder's files alone: the root's files are checked too."""
    demo = repo / "demos" / "kge"
    demo.mkdir(parents=True)
    (demo / "b.txt").write_text("demo\n")
    (demo / "Makefile").write_text(LEGS)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "demo")
    gate(demo)
    assert run(demo, "check").returncode == 0
    git(repo, "update-index", flag, "a.txt")
    (repo / "a.txt").write_text("changed outside the demo, hidden\n")
    done = run(demo, "check")
    assert done.returncode == 1 and "a.txt" in done.stderr
    shutil.rmtree(demo / "build/gate-stamps")
    gate(demo)
    assert stamps(demo) == []


def test_a_change_outside_the_demos_folder_fails_the_check_run_from_inside_it(repo):
    demo = repo / "demos" / "kge"
    demo.mkdir(parents=True)
    (demo / "Makefile").write_text(LEGS)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "demo")
    gate(demo)
    assert run(demo, "check").returncode == 0
    (repo / "a.txt").write_text("changed outside the demo\n")
    assert run(demo, "check").returncode == 1


def test_the_check_holds_in_a_pre_push_hooks_environment_in_a_linked_worktree(repo, tmp_path):
    """git gives a hook GIT_DIR; with it, git would take demos/kge for the work tree's root and see everything else as
    deleted. The check asks git without it, so a clean, stamped commit passes in the hook as at a prompt."""
    (repo / "demos" / "kge").mkdir(parents=True)
    (repo / "demos" / "kge" / "b.txt").write_text("demo\n")
    (repo / "demos" / "kge" / "Makefile").write_text(LEGS)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "demo")
    worktree = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(worktree), "-b", "linked")
    demo = worktree / "demos" / "kge"
    gate(demo)
    assert run(demo, "check").returncode == 0
    gitdir = git(worktree, "rev-parse", "--absolute-git-dir")
    hook = {"GIT_DIR": gitdir, "GIT_INDEX_FILE": f"{gitdir}/index", "GIT_PREFIX": "demos/kge/"}
    assert subprocess.run(["git", "status", "--porcelain"], cwd=demo, capture_output=True, text=True,
                          env={**os.environ, "GIT_DIR": gitdir}).stdout, "with GIT_DIR alone git sees the root's files as gone"
    done = run(demo, "check", **hook)
    assert done.returncode == 0, done.stderr
    (worktree / "a.txt").write_text("changed\n")
    assert run(demo, "check", **hook).returncode == 1, "a real change still fails it in the hook"


def test_a_legs_stamp_and_data_carried_from_a_clone_at_the_same_commit_serve_this_worktree(repo, tmp_path_factory):
    """A lane's worktree detached at the same commit runs a leg; its stamp and kept data, copied into this worktree's
    build/gate-stamps/<leg>/, are this commit's pass, and a clone at another tree's are not."""
    (repo / "Makefile").write_text("LOCAL_GATE_LEGS := unit-1/2:. unit-2/2:.\n")
    git(repo, "commit", "-q", "-am", "shards")
    lane = tmp_path_factory.mktemp("lanes") / "gate-l4"
    git(repo, "worktree", "add", "-q", "--detach", str(lane), "HEAD")
    (lane / "build").mkdir()
    (lane / "build" / "two.cov").write_text("two\n")
    run(lane, "start", "unit-2/2")
    run(lane, "finish", "unit-2/2", "build/two.cov")
    gate(repo, "unit-1/2")
    assert run(repo, "data", "unit-2/2").returncode == 1
    shutil.copytree(lane / "build/gate-stamps/unit-2_2", repo / "build/gate-stamps/unit-2_2")
    done = run(repo, "data", "unit-2/2")
    assert done.returncode == 0 and (repo / done.stdout.strip()).read_text() == "two\n"
    assert run(repo, "check").returncode == 0
    for worktree in (repo, lane):
        shutil.rmtree(worktree / "build/gate-stamps/unit-2_2")
    (lane / "a.txt").write_text("another tree\n")
    git(lane, "commit", "-q", "-am", "other")
    run(lane, "start", "unit-2/2")
    run(lane, "finish", "unit-2/2", "build/two.cov")
    shutil.copytree(lane / "build/gate-stamps/unit-2_2", repo / "build/gate-stamps/unit-2_2")
    assert run(repo, "data", "unit-2/2").returncode == 1 and run(repo, "check").returncode == 1


def test_coverage_measured_in_another_checkout_of_the_demo_combines_once_onto_this_one(tmp_path):
    """The demo's .coveragerc maps a lane clone's absolute paths onto this folder at combine, so make gate-coverage
    counts each file once, its lines from every leg together; a path with no file here stays as recorded."""
    from coverage import CoverageData

    here = tmp_path / "main" / "demos" / "kge"
    (here / "kge").mkdir(parents=True)
    (here / "kge" / "m.py").write_text("a = 1\nb = 2\nc = 3\n")
    lane = tmp_path / "gate-l4" / "demos" / "kge"
    for name, root, lines in (("one", here, [1]), ("two", lane, [2, 3])):
        data = CoverageData(basename=str(here / f".coverage.{name}"))
        data.add_lines({str(root / "kge" / "m.py"): lines, str(root / "kge" / "gone.py"): [1]})
        data.write()
    env = {**{key: value for key, value in os.environ.items() if not key.startswith("COVERAGE")},
           "KGE_COVERAGE_SOURCE": str(here), "COVERAGE_FILE": str(here / ".coverage")}
    subprocess.run([sys.executable, "-m", "coverage", "combine", "-q", f"--rcfile={ROOT / '.coveragerc'}"], cwd=here,
                   env=env, check=True, capture_output=True, timeout=60)
    combined = CoverageData(basename=str(here / ".coverage"))
    combined.read()
    mapped = str(here / "kge" / "m.py")
    assert sorted(combined.measured_files()) == sorted([mapped, str(here / "kge" / "gone.py"), str(lane / "kge" / "gone.py")])
    assert sorted(combined.lines(mapped)) == [1, 2, 3]


SELECTING = ("LOCAL_GATE_LEGS := lint:. repository-checks:. unit-1/2:. unit-2/2:. parity-margin:. e2e-smoke:. gate-coverage:.\n"
             "FULL_LEGS := unit-1/2 unit-2/2 parity-margin e2e-smoke\n"
             "GATE_PATHS := full:ci.yml docs:docs/* shared:tools/* kit:kit/* partial:part/* \\\n  docs:*.md\n"
             "GATE_CLASSES := kit:lint,unit-1/2,unit-2/2,e2e-smoke,gate-coverage \\\n"
             "  shared:lint,repository-checks,unit-1/2,unit-2/2 docs:lint,repository-checks partial:lint,unit-1/2,gate-coverage\n"
             "GATE_ALONE := kit:kit/*\n")
EVERY = ["lint", "repository-checks", "unit-1/2", "unit-2/2", "parity-margin", "e2e-smoke", "gate-coverage"]


def commit(repo, files, message="change"):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def develop(repo):
    """A repository whose origin/develop holds the selection table, with nothing on top yet."""
    commit(repo, {"Makefile": SELECTING}, "the table")
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    return repo


def chosen(repo, *args):
    done = run(repo, "select", *args)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0].startswith("rule: ") and lines[1].startswith("legs: ") and all(line.startswith("why:  ") for line in lines[2:])
    assert len([line for line in lines if line.startswith("legs:")]) == 1, "one legs line, whatever the paths are called"
    return lines[0][6:], lines[1][6:], lines[2:]


@pytest.mark.parametrize("files, rule, legs", [
    (["docs/a.md"], "docs", "lint repository-checks"),
    (["README.md", "docs/b.svg"], "docs", "lint repository-checks"),
    (["tools/x.py"], "shared", "lint repository-checks unit-1/2 unit-2/2"),
    (["tools/x.py", "docs/a.md"], "shared+docs", "lint repository-checks unit-1/2 unit-2/2"),
    (["kit/a.tf"], "kit", "lint unit-1/2 unit-2/2 e2e-smoke gate-coverage"),
    (["part/a"], "partial", "lint unit-1/2"),
    (["kit/a.tf", "docs/a.md"], "full (kit+docs)", "all"),
    (["ci.yml"], "full", "all"),
    (["unknown.txt"], "full", "all"),
    (["unknown.txt", "docs/a.md"], "full (full+docs)", "all"),
])
def test_select_names_the_legs_of_the_classes_a_change_touches_in_the_gates_order(develop, files, rule, legs):
    commit(develop, {name: "x\n" for name in files})
    assert chosen(develop)[:2] == (rule, legs)


def test_gate_coverage_is_dropped_unless_every_leg_it_measures_runs(develop):
    commit(develop, {"part/a": "x\n"})
    rule, legs, why = chosen(develop)
    assert "gate-coverage" not in legs.split() and any("gate-coverage runs only with every leg it measures" in line for line in why)
    git(develop, "update-ref", "refs/remotes/origin/develop", "HEAD")
    commit(develop, {"kit/a": "x\n"})
    assert chosen(develop)[1] == "lint unit-1/2 unit-2/2 e2e-smoke gate-coverage"


def test_select_fails_safe_to_every_leg(repo, develop):
    assert chosen(develop)[:2] == ("full", "all"), "an empty change, develop's own tip"
    assert "no changed paths" in chosen(develop)[2][0]
    assert chosen(develop, "no-such-commit")[:2] == ("full", "all")
    git(develop, "update-ref", "-d", "refs/remotes/origin/develop")
    commit(develop, {"docs/a.md": "x\n"})
    rule, legs, why = chosen(develop)
    assert (rule, legs) == ("full", "all") and "no merge base" in why[0]


@pytest.mark.parametrize("name, line", [("GATE_PATHS", None), ("GATE_CLASSES", None), ("GATE_ALONE", None), ("FULL_LEGS", None),
                                        ("GATE_CLASSES", "GATE_CLASSES := docs:lint,nope"),
                                        ("GATE_CLASSES", "GATE_CLASSES := full:lint"),
                                        ("GATE_CLASSES", "GATE_CLASSES := docs:"),
                                        ("GATE_PATHS", "GATE_PATHS := other:docs/*"),
                                        ("GATE_PATHS", "GATE_PATHS := docs:"),
                                        ("GATE_ALONE", "GATE_ALONE := other:x/*"),
                                        ("FULL_LEGS", "FULL_LEGS :=")])
def test_a_base_table_that_does_not_read_selects_every_leg(repo, name, line):
    lines = [line if row.startswith(f"{name} :=") else row for row in SELECTING.replace("\\\n", "").splitlines()]
    commit(repo, {"Makefile": "\n".join(row for row in lines if row) + "\n"}, "a broken table")
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    commit(repo, {"docs/a.md": "x\n"})
    rule, legs, why = chosen(repo)
    assert (rule, legs) == ("full", "all") and "no selection table that reads" in why[0]


def test_a_base_with_no_table_selects_every_leg_so_a_commit_beyond_develop_needs_every_stamp(repo):
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    commit(repo, {"docs/a.md": "x\n"})
    assert chosen(repo)[:2] == ("full", "all")
    gate(repo, "lint")
    assert run(repo, "check").returncode == 1


@pytest.mark.parametrize("path", ["demos/kge/Makefile", "demos/kge/tests/gate_stamp.py", ".pre-commit-config.yaml",
                                  ".github/workflows/checks.yml", ".github/workflows/kit.yml", ".github/workflows/kit-demo.yml"])
def test_a_change_to_the_gates_own_definition_selects_every_leg_whatever_the_table_says(repo, path):
    demo = repo / "demos" / "kge"
    commit(repo, {"demos/kge/Makefile": SELECTING.replace("full:ci.yml", "docs:*")}, "a table that classes everything as docs")
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    commit(repo, {"Makefile": "not the demo's\n"})
    assert chosen(demo)[:2] == ("docs", "lint repository-checks"), "the guard names the demo's own files, not the root's"
    commit(repo, {path: "changed\n"})
    rule, legs, why = chosen(demo)
    assert (rule, legs) == ("full (full+docs)", "all") and path in why[0]


def test_the_table_is_read_from_the_merge_base_so_a_change_cannot_widen_its_own_class(develop, monkeypatch):
    monkeypatch.chdir(develop)
    monkeypatch.setattr(gate_stamp, "GUARDED", ())
    widened = commit(develop, {"Makefile": SELECTING.replace("full:ci.yml", "docs:*"), "x.txt": "x\n"})
    result = gate_stamp.selection(widened)
    assert result["rule"] == "full" and result["legs"] is None, "the base's table knows neither path"
    git(develop, "update-ref", "refs/remotes/origin/develop", "HEAD")
    narrowed = commit(develop, {"Makefile": SELECTING, "y.txt": "y\n"})
    result = gate_stamp.selection(narrowed)
    assert result["rule"] == "docs" and result["legs"] == ["lint", "repository-checks"], \
        "the base's table classes everything as docs, whatever the commit's says"
    monkeypatch.undo()
    assert chosen(develop)[:2] == ("full (full+docs)", "all"), "and the Makefile itself is guarded"


def test_check_needs_the_selected_legs_alone_and_fails_on_any_one_missing(develop):
    commit(develop, {"docs/a.md": "x\n"})
    gate(develop, "lint")
    done = run(develop, "check")
    assert done.returncode == 1 and done.stderr.split("for ", 1)[1].startswith("repository-checks:"), done.stderr
    assert "needs lint repository-checks, by rule docs" in done.stdout
    gate(develop, "repository-checks")
    done = run(develop, "check")
    assert done.returncode == 0 and "the legs its change needs" in done.stdout, done.stdout + done.stderr
    commit(develop, {"tools/x.py": "x\n"})
    gate(develop, "lint", "repository-checks", "unit-1/2")
    done = run(develop, "check")
    assert done.returncode == 1 and "unit-2/2" in done.stderr and "by rule shared+docs" in done.stdout
    gate(develop, "unit-2/2")
    assert run(develop, "check").returncode == 0
    commit(develop, {"unknown.txt": "x\n"})
    gate(develop, "lint", "repository-checks", "unit-1/2", "unit-2/2")
    done = run(develop, "check")
    assert done.returncode == 1 and "needs every leg" in done.stdout
    gate(develop, *EVERY)
    assert run(develop, "check").returncode == 0


def test_finish_records_the_rule_the_legs_selected_and_the_merge_base(develop, repo):
    base = git(develop, "rev-parse", "HEAD")
    commit(develop, {"docs/a.md": "x\n"})
    gate(develop, "lint")
    [lint] = stamps(develop)
    record = json.loads((develop / "build/gate-stamps" / lint).read_text())
    assert (record["rule"], record["selected"], record["base"]) == ("docs", ["lint", "repository-checks"], base)
    git(develop, "update-ref", "-d", "refs/remotes/origin/develop")
    shutil.rmtree(develop / "build/gate-stamps")
    gate(develop, "lint")
    [lint] = stamps(develop)
    record = json.loads((develop / "build/gate-stamps" / lint).read_text())
    assert (record["rule"], record["selected"], record["base"]) == ("full", "all", "")


def test_an_empty_selection_needs_every_leg(develop, monkeypatch, capsys):
    monkeypatch.chdir(develop)
    commit(develop, {"docs/a.md": "x\n"})
    monkeypatch.setattr(gate_stamp, "selection", lambda commit: {"rule": "docs", "legs": [], "why": [], "base": ""})
    assert gate_stamp.check() == 1 and "needs every leg" in capsys.readouterr().out


def test_a_link_added_or_turned_into_a_file_selects_every_leg(develop):
    (develop / "docs").mkdir()
    os.symlink("../tools/x", develop / "docs" / "link.md")
    git(develop, "add", "-A")
    git(develop, "commit", "-q", "-m", "a link")
    rule, legs, why = chosen(develop)
    assert (rule, legs) == ("full", "all") and "docs/link.md" in why[0]
    git(develop, "update-ref", "refs/remotes/origin/develop", "HEAD")
    (develop / "docs" / "link.md").unlink()
    commit(develop, {"docs/link.md": "a file now\n"})
    assert chosen(develop)[:2] == ("full", "all"), "a link turned into a file"
    git(develop, "update-ref", "refs/remotes/origin/develop", "HEAD")
    commit(develop, {"docs/a.md": "x\n"})
    assert chosen(develop)[:2] == ("docs", "lint repository-checks"), "a plain file beside it stays narrow"


def test_a_path_with_a_newline_cannot_add_a_legs_line(develop):
    commit(develop, {"docs/a\nlegs: lint.md": "x\n"})
    rule, legs, why = chosen(develop)
    assert (rule, legs) == ("docs", "lint repository-checks") and "docs/a\\nlegs: lint.md" in why[0]


NARROWING = ("LOCAL_GATE_LEGS := lint:. repository-checks:. unit-1/2:. unit-2/2:. e2e-smoke:. gate-coverage:.\n"
             "LOCAL_GATE_NARROW := tf-tests:. kit-tests:.\n"
             "FULL_LEGS := unit-1/2 unit-2/2 e2e-smoke\n"
             "GATE_PATHS := full:ci.yml tf:tf/* kit:kit/* ui:ui/* shared:tools/* docs:*.md\n"
             "GATE_CLASSES := tf:lint,repository-checks,tf-tests kit:lint,repository-checks,kit-tests ui:lint,e2e-smoke \\\n"
             "  shared:lint,repository-checks,unit-1/2,unit-2/2 docs:lint,repository-checks\n"
             "GATE_ALONE :=\n")


@pytest.fixture
def narrowing(repo):
    """origin/develop holds a table with narrow legs and no GATE_ALONE, so a change across classes runs their union."""
    commit(repo, {"Makefile": NARROWING}, "the table")
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    return repo


@pytest.mark.parametrize("files, rule, legs", [
    (["tf/a.tf"], "tf", "lint repository-checks tf-tests"),
    (["kit/a"], "kit", "lint repository-checks kit-tests"),
    (["ui/a.tsx"], "ui", "lint e2e-smoke"),
    (["tf/a.tf", "kit/a"], "tf+kit", "lint repository-checks tf-tests kit-tests"),
    (["ui/a.tsx", "tf/a.tf", "a.md"], "tf+ui+docs", "lint repository-checks e2e-smoke tf-tests"),
    (["tf/a.tf", "tools/x"], "tf+shared", "lint repository-checks unit-1/2 unit-2/2"),
    (["ui/a.tsx", "tools/x", "kit/a"], "kit+ui+shared", "lint repository-checks unit-1/2 unit-2/2 e2e-smoke"),
    (["ui/a.tsx", "tools/x", "unknown"], "full (full+ui+shared)", "all"),
    (["tf/a.tf", "unknown"], "full (full+tf)", "all"),
])
def test_a_change_across_types_runs_the_union_and_the_narrow_legs_only_without_the_unit_legs(narrowing, files, rule, legs):
    commit(narrowing, {name: "x\n" for name in files})
    assert chosen(narrowing)[:2] == (rule, legs)


def test_a_narrow_leg_stamps_and_is_needed_only_where_selected(narrowing):
    commit(narrowing, {"tf/a.tf": "x\n"})
    gate(narrowing, "lint", "repository-checks")
    done = run(narrowing, "check")
    assert done.returncode == 1 and "tf-tests" in done.stderr and "needs lint repository-checks tf-tests" in done.stdout
    gate(narrowing, "tf-tests")
    assert any(name.startswith("tf-tests/") for name in stamps(narrowing))
    assert run(narrowing, "check").returncode == 0
    commit(narrowing, {"unknown": "x\n"})
    gate(narrowing, "lint", "repository-checks", "unit-1/2", "unit-2/2", "e2e-smoke", "gate-coverage")
    done = run(narrowing, "check")
    assert done.returncode == 0 and "needs every leg" in done.stdout, "every leg is LOCAL_GATE_LEGS, never the narrow legs"


def test_narrow_legs_that_clash_with_the_gates_or_do_not_parse_are_not_legs(tmp_path):
    clash = NARROWING.replace("tf-tests:. kit-tests:.", "lint:. kit-tests:.")
    assert gate_stamp.narrow_legs(clash) == {} and gate_stamp.table(clash) is None
    assert gate_stamp.narrow_legs(NARROWING.replace("tf-tests:.", "../tf:.")) == {}
    assert gate_stamp.narrow_legs(SELECTING) == {} and gate_stamp.table(SELECTING)["narrow"] == []


def demo_table():
    return gate_stamp, gate_stamp.table((ROOT / "Makefile").read_text())


UNITS = " ".join(f"unit-{i}/6" for i in range(1, 7))


@pytest.mark.parametrize("paths, rule, legs", [
    (["docs/proposals/a.md", "docs/diagrams/a.svg", "README.md", "AGENTS.md"], "docs", "lint repository-checks"),
    (["docs/use-cases/kge/platform/pack-contract.md", ".claude/skills/kge-checks/SKILL.md", "CONTRIBUTING.md"],
     "read-docs", "lint repository-checks doc-tests"),
    (["demos/kge/CONTRIBUTING.md", "demos/kge/REPORT.md", "demos/kge/docs/playbooks/steward.md", "demos/kge/docs/img/a.png"],
     "read-docs", "lint repository-checks doc-tests"),
    (["demos/kge/README.md"], "demo-readme", "lint repository-checks portal-tests e2e-smoke doc-tests"),
    (["demos/kge/README.md", "CONTRIBUTING.md"], "read-docs+demo-readme", "lint repository-checks portal-tests e2e-smoke doc-tests"),
    (["tools/check_prose.py", "tools/README.md", ".gitignore", ".github/workflows/release.yml", ".github/dependabot.yml"],
     "shared", f"lint repository-checks {UNITS}"),
    (["demos/kge/clients/meridian/fixture/docs/certified/DD-SME.md"], "full", None),
    (["demos/kge/ui/guides/steward.md"], "full", None),
    (["demos/kge/skills/ask/SKILL.md"], "full", None),
    (["demos/kge/THIRD_PARTY_NOTICES.md"], "full", None),
    (["CONTRIBUTING.md", "tools/check_prose.py"], "read-docs+shared", f"lint repository-checks {UNITS}"),
    (["CONTRIBUTING.md", "demos/kge/kit/INSTALL.md", "demos/kge/infra/README.md"], "infra+kit+read-docs",
     "lint repository-checks portal-tests infra-tests kit-tests doc-tests"),
    (["demos/kge/portal/ui/src/a.tsx"], "portal-ui", "lint portal-tests e2e-smoke portal-unit-tests"),
    (["demos/kge/portal/server/src/a.js"], "portal-server", "lint portal-tests e2e-smoke portal-unit-tests"),
    (["demos/kge/portal/Dockerfile"], "portal", f"lint repository-checks portal-tests {UNITS} e2e-smoke gate-coverage"),
    (["demos/kge/infra/prod/main.tf", "demos/kge/infra/README.md", "demos/kge/infra/pilot/main.tf",
      "demos/kge/infra/modules/x/main.tf", "demos/kge/infra/tests/pilot.tftest.hcl"], "infra", "lint repository-checks infra-tests"),
    (["demos/kge/kit/VERSION", "demos/kge/kit/INSTALL.md", "demos/kge/kit/build.sh"], "kit",
     "lint repository-checks portal-tests kit-tests"),
    (["demos/kge/portal/ui/src/a.tsx", "demos/kge/portal/server/src/a.js"], "portal-ui+portal-server",
     "lint portal-tests e2e-smoke portal-unit-tests"),
    (["demos/kge/portal/ui/src/a.tsx", "README.md"], "portal-ui+docs", "lint repository-checks portal-tests e2e-smoke portal-unit-tests"),
    (["demos/kge/infra/pilot/main.tf", "demos/kge/kit/VERSION"], "infra+kit", "lint repository-checks portal-tests infra-tests kit-tests"),
    (["demos/kge/portal/ui/src/a.tsx", "demos/kge/infra/pilot/main.tf", "demos/kge/kit/VERSION"], "portal-ui+infra+kit",
     "lint repository-checks portal-tests e2e-smoke infra-tests kit-tests portal-unit-tests"),
    (["demos/kge/infra/prod/main.tf", "docs/a.md"], "infra+docs", "lint repository-checks infra-tests"),
    (["demos/kge/infra/pilot/main.tf", ".claude/skills/x/SKILL.md"], "infra+read-docs", "lint repository-checks infra-tests doc-tests"),
    (["demos/kge/kit/VERSION", "demos/kge/portal/Dockerfile"], "portal+kit",
     f"lint repository-checks portal-tests {UNITS} e2e-smoke gate-coverage"),
    (["docs/a.md", "CONTRIBUTING.md"], "read-docs+docs", "lint repository-checks doc-tests"),
    (["demos/kge/kge/app.py"], "full", None),
    (["demos/kge/tests/unit/test_x.py"], "full", None),
    (["demos/kge/kge/a.md"], "full", None),
    (["demos/kge/portal/ui/src/a.tsx", "demos/kge/kge/app.py"], "full (full+portal-ui)", None),
    (["demos/kge/infra/pilot/main.tf", "demos/kge/kge/app.py"], "full (full+infra)", None),
    (["demos/kge/requirements.txt"], "full", None),
    (["demos/kge-lite/a.py"], "full", None),
    (["demos/kge-lite/main.tf"], "full", None),
    (["deploy/main.tf"], "infra", "lint repository-checks infra-tests"),
    ([".claude/settings.json"], "full", None),
    (["Dockerfile"], "full", None),
    (["demos/kge/Makefile"], "full", None),
    (["demos/kge/tests/gate_stamp.py"], "full", None),
    ([".pre-commit-config.yaml"], "full", None),
    ([".github/workflows/checks.yml"], "full", None),
    ([".github/workflows/kit.yml"], "full", None),
    ([".github/workflows/kit-demo.yml"], "full", None),
])
def test_the_demos_table_classes_each_kind_of_change(paths, rule, legs):
    gate_stamp, spec = demo_table()
    assert spec is not None, "the demo's Makefile holds a table that reads"
    got_rule, got_legs, _ = gate_stamp.classify(paths, spec, gate_stamp.guarded("demos/kge/"))
    assert (got_rule, " ".join(got_legs) if got_legs else None) == (rule, legs)


def test_the_demos_table_runs_gate_coverage_only_with_every_leg_it_measures_and_guards_the_gate():
    gate_stamp, spec = demo_table()
    make = (ROOT / "Makefile").read_text()
    full = re.search(r"^FULL_LEGS := (.+)$", make, re.M).group(1).split()
    assert spec["measured"] == [leg for leg in full if leg != "parity-margin"]
    for name, chosen_legs in spec["classes"].items():
        assert ("gate-coverage" in chosen_legs) == (set(spec["measured"]) <= set(chosen_legs)), name
        assert chosen_legs[0] == "lint", name
    assert {f"full:{path}" for path in gate_stamp.guarded("demos/kge/")} <= set(gate_stamp.block(make, "GATE_PATHS")), \
        "the table shows the guard too, for its mirror"
    assert 'if len(sys.argv) in (2, 3) and sys.argv[1] == "select":' in (ROOT / "tests" / "gate_stamp.py").read_text(), \
        "gate-run.sh detects select support by this line"
    assert "gate-select:\n\t@$(PY) tests/gate_stamp.py select" in make
    assert "gate-select" in re.search(r"^\.PHONY: (.+)$", make, re.M).group(1).split()


CROSS = [["demos/kge/infra/prod/main.tf", "docs/a.md"], ["demos/kge/infra/README.md", "README.md"],
         ["demos/kge/infra/pilot/main.tf", ".claude/skills/x/SKILL.md"], ["demos/kge/infra/pilot/main.tf", "docs/a.md"],
         ["demos/kge/portal/ui/a.tsx", "demos/kge/infra/pilot/main.tf"], ["demos/kge/portal/ui/a.tsx", "CONTRIBUTING.md"],
         ["demos/kge/portal/ui/a.tsx", "docs/a.md"], ["tools/check_prose.py", "docs/a.md"],
         [".pre-commit-config.yaml"], [".gitignore"], [".github/dependabot.yml"], ["Dockerfile"]]


LEFT_TO_CI = {"portal-ui": {"repository-checks", *UNITS.split(), "parity-margin", "gate-coverage"},
              "portal-server": {"repository-checks", *UNITS.split(), "parity-margin", "gate-coverage"},
              "infra": {*UNITS.split(), "parity-margin", "e2e-smoke", "gate-coverage"},
              "kit": {*UNITS.split(), "parity-margin", "e2e-smoke", "gate-coverage"},
              "read-docs": {*UNITS.split(), "parity-margin", "e2e-smoke", "gate-coverage"},
              "demo-readme": {*UNITS.split(), "parity-margin", "gate-coverage"}}


@pytest.mark.parametrize("paths", [row[0] for row in SCOPE_ROWS] + CROSS)
def test_the_local_selection_runs_every_leg_cis_scope_runs_for_the_same_paths(tmp_path, paths):
    """CI's scope step, run as test_checks runs it, against the local table: the local gate proves what CI attests, but
    for the owner's change types (2026-11-17), each of which leaves no more than its LEFT_TO_CI to the ready run: the six
    unit legs only where a narrow leg runs the unit test files that read the change's paths instead."""
    gate_stamp, spec = demo_table()
    make = (ROOT / "Makefile").read_text()
    result = scope(tmp_path, paths)
    due = {"repository-checks"}
    if result["unit"] == "yes":
        due |= {"lint", *UNITS.split()}
    if result["portal"] == "yes":
        due.add("portal-tests")
    if result["run"] == "yes":
        due |= {*gate_stamp.block(make, "WALKTHROUGH_LEGS" if result["mode"] == "walkthrough" else "READY_LEGS"), "gate-coverage"}
    rule, legs, _ = gate_stamp.classify(paths, spec, gate_stamp.guarded("demos/kge/"))
    gap = due - set(legs or spec["order"])
    narrowed = set(rule.split("+")) & set(LEFT_TO_CI)
    if narrowed:
        allowed = set().union(*(LEFT_TO_CI[name] for name in narrowed))
        assert gap <= allowed, f"{rule} misses {sorted(gap - allowed)} that CI runs"
        assert not gap & set(UNITS.split()) or set(legs) & set(spec["narrow"]), f"{rule} skips the unit legs with no narrow leg"
    else:
        assert not gap, f"{rule} misses {sorted(gap)} that CI runs"


def readers_of(reads):
    """The unit test files whose source matches reads, and every one that imports from such a module, transitively."""
    sources = {path.stem: path.read_text() for path in (ROOT / "tests" / "unit").glob("test_*.py")}
    found = {name for name, text in sources.items() if re.search(reads, text)}
    while True:
        more = {name for name, text in sources.items() if name not in found
                and any(f"from tests.unit.{module} import" in text for module in found)}
        if not more:
            return sorted(f"tests/unit/{name}.py" for name in found)
        found |= more


@pytest.mark.parametrize("variable, reads", [
    ("INFRA_TESTS", r"""["'/]infra["'/]|\binfra/[A-Za-z_]|terraform_runs"""),
    ("KIT_TESTS", r"""["'/]kit["'/]|\bkit/[A-Za-z_]"""),
    ("DOC_TESTS", r"""\.claude|["'/]skills["'/]|SKILL\.md|CONTRIBUTING|use-cases|README|REPORT\.md|playbooks|["'/]docs["'/]|\*\.md"""),
    ("PORTAL_UNIT_TESTS", r"""["'/]portal["'/]|portal/(ui|server)"""),
])
def test_each_narrow_leg_runs_every_unit_test_file_that_reads_its_folder(variable, reads):
    """Every unit test that names the leg's folder or files, or imports from one that does (test_compose_acceptance_fixes
    runs the kit's helper through test_kge_helper). The e2e tests that read the documentation read the demo's README
    alone, and a README change runs e2e-smoke."""
    listed = gate_stamp.block((ROOT / "Makefile").read_text(), variable)
    assert listed and all((ROOT / path).is_file() for path in listed), f"{variable} names files that exist"
    readers = readers_of(reads)
    if variable == "KIT_TESTS":
        assert "tests/unit/test_compose_acceptance_fixes.py" in readers
    if variable == "DOC_TESTS":
        e2e = [path.name for path in (ROOT / "tests" / "e2e").glob("test_*.py") if re.search(reads, path.read_text())]
        assert all("README" in (ROOT / "tests" / "e2e" / name).read_text() for name in e2e)
        assert not re.search(reads.replace("README|", ""), "".join((ROOT / "tests" / "e2e" / name).read_text() for name in e2e)), \
            "an e2e test reads documentation beyond the README, which only e2e-smoke covers"
        assert "e2e-smoke" in gate_stamp.classify(["demos/kge/README.md"], demo_table()[1], set())[1]
    assert sorted(set(readers) - set(listed)) == [], f"add these to {variable} in the Makefile"


def test_every_path_outside_the_portal_its_own_tests_read_runs_the_portals_tests():
    """The portal's server and UI tests read files beyond portal/ (the demo's README, the kit's INSTALL.md and Compose
    file, examples and fixtures): a change to any of them selects portal-tests, or every leg."""
    gate_stamp, spec = demo_table()
    portal = ROOT / "portal"
    tests = [path for path in (portal / "server" / "test").rglob("*.js")] + \
            [path for path in (portal / "ui" / "src").rglob("*.test.js")]
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split())
    outside = set()
    for test in tests:
        for literal in re.findall(r"""["'`]([A-Za-z0-9_./-]+)["'`]""", test.read_text()):
            for base in (test.parent, ROOT):
                target = (base / literal).resolve()
                if not target.is_relative_to(ROOT) or target.is_relative_to(portal):
                    continue
                rel = target.relative_to(ROOT).as_posix()
                if rel in tracked or any(name.startswith(rel + "/") for name in tracked):
                    outside.add(rel)
    assert {"README.md", "kit/INSTALL.md", "kit/compose/compose.yaml"} <= outside, sorted(outside)
    for rel in sorted(outside):
        rule, legs, _ = gate_stamp.classify([f"demos/kge/{rel}"], spec, gate_stamp.guarded("demos/kge/"))
        assert legs is None or "portal-tests" in legs, f"a change to demos/kge/{rel} ({rule}) skips the portal's tests that read it"
