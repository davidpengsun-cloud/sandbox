"""The guide interpreter checks actual command output and stops at a broken step."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

from tests.unit.test_kge_helper import Helper

DEMO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("playbook", DEMO / "kit/scripts/playbook.py")
playbook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(playbook)


def guide(tmp_path, content):
    path = tmp_path / "example.md"
    path.write_text(content)
    return path


GOOD = '## 1. Read [read-only]\n```kge\nkge config\n```\n```expect\nCLIENT=meridian\nregex:^TARGET=local$\n```\n'


def test_real_helper_runs_commands_and_expectations_without_a_shell(tmp_path, capsys):
    helper = Helper(tmp_path)
    helper.write("local")
    playbook.run(guide(tmp_path, GOOD), DEMO / "kit/scripts/kge", "local/meridian", {}, dry_run=True)
    assert "+ kge config" in capsys.readouterr().out
    done = helper.run("playbook", "operator", "--from-step", "2", "--through-step", "2")
    assert "playbook operator: passed" in done.stdout and "TARGET=local" in done.stdout


@pytest.mark.parametrize("replacement", ["not-there", "regex:^NOT_THERE$"])
def test_broken_expectation_names_the_step_and_stops(tmp_path, monkeypatch, replacement):
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "CLIENT=meridian\nTARGET=local\n")
    monkeypatch.setattr(playbook.subprocess, "run", execute)
    text = GOOD.replace("CLIENT=meridian", replacement) + GOOD.replace("1. Read", "2. Never")
    with pytest.raises(ValueError, match="step 1. Read: expected"):
        playbook.run(guide(tmp_path, text), "helper", "local/meridian", {})
    assert len(calls) == 1


def test_failed_command_is_not_hidden_by_matching_output(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 7, "CLIENT=meridian\nTARGET=local\n"))
    with pytest.raises(ValueError, match=r"step 1. Read: command failed \(7\)"):
        playbook.run(guide(tmp_path, GOOD), "helper", "local/meridian", {})


def test_safe_skips_mutations_and_never_auto_connects(tmp_path):
    helper = Helper(tmp_path)
    helper.write("local")
    done = helper.run("playbook", "business", "--safe", ok=False)
    assert "skipped (mutating)" in done.stdout
    assert "existing token" in done.stderr
    assert not helper.calls()


def test_all_guides_parse_and_dry_run_without_side_effects(tmp_path):
    helper = Helper(tmp_path)
    helper.write("local")
    for path in sorted(p for p in (DEMO / "docs/playbooks").glob("*.md") if p.stem != "developer"):
        parsed = playbook.steps(path.read_text())
        assert parsed[0]["commands"][0][0] == "connect"
        assert parsed[0]["commands"][0][1].endswith(".{{client}}@example.com")
        assert parsed[0]["mutating"]
        result = helper.run("playbook", path.stem, "--dry-run")
        assert "dry-run" in result.stdout
    assert not helper.calls()


def test_arguments_are_literal_and_preserve_spaces(tmp_path, monkeypatch):
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "ok")
    monkeypatch.setattr(playbook.subprocess, "run", execute)
    text = '## 1. Upload [mutating]\n```kge\nkge upload {{upload}}\n```\n```expect\nok\n```'
    value = '/tmp/a folder/$(touch bad)'
    playbook.run(guide(tmp_path, text), "helper", "local/meridian", {"upload": value})
    assert calls == [["bash", "helper", "upload", value, "ENV=local/meridian"]]


@pytest.mark.parametrize("text", ["```kge\nkge config\n```", "## bad\n", GOOD.replace("kge config", "curl somewhere"), GOOD.replace("kge config", "kge playbook operator"), GOOD.replace("```expect", "```text"), GOOD[:-4]])
def test_malformed_guides_refuse_before_commands(text):
    with pytest.raises(ValueError):
        playbook.steps(text)


def test_helper_review_and_decisions_use_existing_http_routes_no_body_identity(tmp_path):
    helper = Helper(tmp_path)
    helper.write("local")
    helper.token()
    for command, args, path in (("review", (), "/onboard"), ("accept", ("UNIT=retail",), "/onboard/accept"),
                                ("certify", ("PROPOSAL=p1",), "/onboard/certify")):
        helper.run(command, *args)
        call = helper.calls("curl")[-1]
        assert call["url"].endswith(path) and call["bearer"]
        assert "principal" not in (call["body"] or {})
    helper.run("accept", "UNIT=retail", "PRINCIPAL=steward.meridian@example.com", ok=False)
    helper.run("accept", "UNIT=retail", "PROPOSAL=p1", ok=False)


@pytest.mark.parametrize("state,ok", [("done", True), ("failed", False), ("interrupted", False)])
def test_wait_checks_job_terminal_state(tmp_path, state, ok):
    helper = Helper(tmp_path)
    helper.write("local")
    helper.token()
    curl = helper.bin / "curl"
    curl.write_text(curl.read_text().replace('value = {"ok": True}', 'value = {"job": {"id": "job-1", "state": "' + state + '"}}'))
    result = helper.run("wait", ok=ok)
    assert "job: " + state in result.stdout + result.stderr


def test_compose_request_is_executed_by_job_before_waiting(tmp_path):
    helper = Helper(tmp_path)
    helper.write("compose")
    helper.token("compose")
    curl = helper.bin / "curl"
    curl.write_text(curl.read_text().replace('value = {"job": {"id": "job-1", "state": "running"}}', 'value = {"command": "docker compose run --rm job onboard-request", "job": {"id": "job-1", "state": "running"}}'))
    helper.run("checks")
    assert helper.calls("docker")[-1]["args"] == ["compose", "run", "--rm", "job", "onboard-request"]


def test_wait_refuses_job_replaced_before_first_poll(tmp_path):
    helper = Helper(tmp_path)
    helper.write("local")
    helper.token()
    helper.run("checks")
    curl = helper.bin / "curl"
    curl.write_text(curl.read_text().replace('value = {"ok": True}', 'value = {"job": {"id": "replacement", "state": "done"}}'))
    done = helper.run("wait", ok=False)
    assert "replaced while waiting" in done.stderr


@pytest.mark.parametrize("target", ["compose", "gcp"])
def test_put_example_dispatches_to_store_job(tmp_path, target):
    helper = Helper(tmp_path)
    helper.write(target)
    helper.run("put-example")
    assert "put-example" in str(helper.calls("docker" if target == "compose" else "gcloud"))


def test_put_example_refuses_local_without_running_anything(tmp_path):
    helper = Helper(tmp_path)
    helper.write("local")
    result = helper.run("put-example", ok=False)
    assert "already includes the sample" in result.stderr
    assert not helper.calls()


@pytest.mark.parametrize("path", sorted(p for p in (DEMO / "docs/playbooks").glob("*.md") if p.stem != "developer"), ids=lambda p: p.stem)
def test_every_expect_block_rejects_recorded_failed_or_empty_results(path):
    records = (DEMO / "tests/fixtures/playbook-failures.txt").read_text().split("\n---\n")
    for step in playbook.steps(path.read_text()):
        for output in records:
            with pytest.raises(ValueError, match="expected"):
                playbook.check_expectations(step["expect"], output, {"client": "meridian"})


def test_captured_release_hashes_must_differ_and_missing_values_fail():
    values = {}
    playbook.check_expectations([r"capture:before=^served: ([a-f0-9]{64})$"], "served: " + "a" * 64, values)
    with pytest.raises(ValueError):
        playbook.check_expectations(["different:before,candidate"], "", values)
    values["candidate"] = values["before"]
    with pytest.raises(ValueError):
        playbook.check_expectations(["different:before,candidate"], "", values)
    values["candidate"] = "b" * 64
    playbook.check_expectations(["different:before,candidate"], "", values)


def int8_failures():
    records = (DEMO / "tests/fixtures/playbook-failures.txt").read_text().split("\n---\n")
    return [tuple(record.removeprefix("INT8: ").split("\n", 1)) for record in records if record.startswith("INT8: ")]


@pytest.mark.parametrize("name,output", int8_failures(), ids=[name for name, _ in int8_failures()])
def test_compose_rerun_bad_outputs_fail_the_relevant_step(name, output):
    role, number = "steward", 3
    if name.startswith("connect-"):
        role, number = name.removeprefix("connect-"), 1
    elif name == "teardown-usage":
        role, number = "operator", 6
    elif name == "restart-dry":
        role, number = "operator", 4
        output = "passwords/compose/meridian.ABC\n" + output
    elif name.startswith("publish-"):
        number = 5
    elif name.startswith("rollback-"):
        number = 6
    step = playbook.steps((DEMO / f"docs/playbooks/{role}.md").read_text())[number - 1]
    values = {"client": "meridian", "before": "a" * 64, "candidate": "b" * 64, "published": "b" * 64}
    with pytest.raises(ValueError, match="expected"):
        playbook.check_expectations(step["expect"], output, values)


def test_steward_expectations_accept_gate_and_exact_release_transitions():
    import json

    steps = playbook.steps((DEMO / "docs/playbooks/steward.md").read_text())
    values = {"before": "a" * 64}
    candidate = {"hash": "b" * 64, "gate": {"nested": {"passed": False}, "passed": True}, "eval_pre": {"passed": True}}
    playbook.check_expectations(steps[2]["expect"], 'job: done (checks)\n' + json.dumps({"candidate": candidate, "eval_pre": {"passed": True}}), values)
    for step, h in [(steps[4], "b" * 64), (steps[5], "a" * 64)]:
        output = f'job: done (release)\nserved: {h}\n'
        output += json.dumps({"eval_post": {"identity": {"hash": h}, "passed": True}})
        playbook.check_expectations(step["expect"], output, values)
    assert values["before"] == values["restored"] != values["published"]


@pytest.mark.parametrize("role", ["operator", "access-admin", "knowledge-engineer", "business", "steward"])
def test_connect_expectations_require_role_ids(role):
    step = playbook.steps((DEMO / f"docs/playbooks/{role}.md").read_text())[0]
    email = step["commands"][0][1].replace("{{client}}", "meridian")
    for roles in ("analyst_enterprise", "access_admin, steward_glossary"):
        output = f"connected: compose/meridian as {email} ({roles})"
        playbook.check_expectations(step["expect"], output, {"client": "meridian"})
    for roles in ("", "None", "null", "None, steward", "steward, None"):
        with pytest.raises(ValueError, match="expected"):
            playbook.check_expectations(step["expect"], f"connected: compose/meridian as {email} ({roles})", {"client": "meridian"})


def test_teardown_inventory_can_be_empty_but_usage_cannot_pass():
    step = playbook.steps((DEMO / "docs/playbooks/operator.md").read_text())[5]
    output = "teardown: clients/meridian/: 0 object(s)\nteardown: landing: 0 object(s)\nteardown: BigQuery: 0 object(s)\nteardown: preview only: nothing removed\n"
    playbook.check_expectations(step["expect"], output, {"client": "meridian"})


@pytest.mark.parametrize("actual", ['{"eval_post":{"passed":1}}', '{"eval_post":{"nested":{"passed":true}}}', '{"eval_post":{"passed":false},"passed":true}'])
def test_json_expectations_require_the_exact_field_and_type(actual):
    with pytest.raises(ValueError, match="expected"):
        playbook.check_expectations(["json:eval_post.passed=true"], actual, {})


def test_json_expectations_do_not_accept_an_earlier_success():
    actual = '{"eval_post":{"passed":true}}\n{"eval_post":{"passed":false}}'
    with pytest.raises(ValueError, match="expected"):
        playbook.check_expectations(["json:eval_post.passed=true"], actual, {})
    with pytest.raises(ValueError, match="expected"):
        playbook.check_expectations(["json:eval_post.passed=true"], actual + '\n{}', {})


@pytest.mark.parametrize("client", ["meridian", "aster-bank"])
@pytest.mark.parametrize("path", sorted(p for p in (DEMO / "docs/playbooks").glob("*.md") if p.stem != "developer"), ids=lambda p: p.stem)
def test_shipped_connect_steps_use_selected_client(tmp_path, monkeypatch, path, client):
    step = playbook.steps(path.read_text())[0]
    email = step["commands"][0][1].replace("{{client}}", client)
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, f"connected: compose/{client} as {email} (steward_glossary)\n")
    monkeypatch.setattr(playbook.subprocess, "run", execute)
    playbook.run(path, "helper", f"compose/{client}", {"client": client}, last=1)
    assert calls == [["bash", "helper", "connect", email, f"ENV=compose/{client}"]]
    with pytest.raises(ValueError, match="expected"):
        playbook.check_expectations(step["expect"], f"connected: compose/other as {email} (steward_glossary)", {"client": client})


def test_expectation_values_are_literal_and_missing_values_fail():
    playbook.check_expectations(["client={{client}}", r"regex:^client={{client}}$"], "client=a.b", {"client": "a.b"})
    with pytest.raises(ValueError, match="expected"):
        playbook.check_expectations([r"regex:^client={{client}}$"], "client=aXb", {"client": "a.b"})
    with pytest.raises(ValueError, match="expected value 'client'"):
        playbook.check_expectations(["client={{client}}"], "client=a.b", {})
