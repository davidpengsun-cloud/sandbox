"""kge, the steps' one definition: its settings, as make read them, and each command's dispatch, with stand-ins for the
modules it runs. No make, no shell, no socket and nothing built."""
import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from kge import cli
from kge.pipeline.profile import Profile

CLI = Path(cli.__file__).parent
DEMO = Path(__file__).resolve().parents[2]
SETTINGS = ("BASELINE", "PROFILES", "PROFILE", "UNIT", "PRINCIPAL", "PROPOSAL", "NAME", "RUN", "HASH", "DDL", "SEMANTIC",
            "LABEL", "PREVIEW", "ROWS", "CSV", "CLIENT", "DB", "ITEM", "ESTATE", "BEFORE", "MARGIN_STATUS", "API",
            "KGE_URL", "PORTAL_PORT", "PACK", "STORE", "BUILD", "PORTAL_SYNC_TOKEN", "RELOAD", "DEMO_MODE", "SUITE")


def stand_in_onboard(monkeypatch):
    """Keep command dispatch tests from writing markers for their simulated loads."""
    from kge.graph import release, leftovers
    from kge.estate import upfront
    empty = leftovers.Held(frozenset(), frozenset(), frozenset())
    monkeypatch.setattr(leftovers, "held", lambda prof: empty)
    monkeypatch.setattr(leftovers, "sweep", lambda prof: empty)
    monkeypatch.setattr(leftovers, "drop_new", lambda prof, before: empty)
    monkeypatch.setattr(upfront, "check", lambda prof: upfront.Report(False, [], [], None, {}, "digest", None))
    monkeypatch.setattr(release, "gate", lambda graph: {"passed": True, "model_hash": graph.parent.name, "run_id": "prior"})
    monkeypatch.setattr(release, "mark_changes", lambda graph, record: None)


def onboard_output(args):
    if args[2:4] == ["kge.estate.onboard", "build"]:
        return '{"units": []}'
    if args[2] == "kge.compiler.lock" and "--changes" in args:
        Path(args[args.index("--changes") + 1]).write_text(json.dumps({"written": False, "changes": []}))
    return ""


def plan(monkeypatch, *argv: str, compiled: str = "", codes: dict | None = None, **env: str):
    """kge <argv> from the demo's folder, as make -n showed a target: (its exit code, each module it runs with its
    arguments, in order, and what it printed), with a stand-in for every module, so nothing runs. compile prints compiled
    (an artifact's folder); a module named in codes exits with its code. The backends and a parent make's variables are
    set aside, then env set; the margin's status is written to a folder of the test's own unless env names one."""
    monkeypatch.chdir(DEMO)
    env.setdefault("MARGIN_STATUS", str(Path(tempfile.mkdtemp()) / "margin.status"))
    for name in ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "GRAPH_STORE", "PROFILE", "PROFILES", "HASH"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    ran = []
    stand_in_onboard(monkeypatch)
    from kge.estate import from_files
    monkeypatch.setattr(from_files, "build", lambda *args, **kwargs: {
        "rebuilt": False, "why": "unchanged", "version": None, "fixture_sha256": None})

    def run(args, **kwargs):
        assert args[:2] == [sys.executable, "-m"] and "shell" not in kwargs
        ran.append(list(args[2:]))
        printed = compiled if args[2] == "kge.compiler.compile" else onboard_output(args)
        return subprocess.CompletedProcess(args, (codes or {}).get(args[2], 0), printed)

    with monkeypatch.context() as m:
        m.setattr(cli.subprocess, "run", run)
        code = cli.main(list(argv))
    return code, ran


def planned(*argv: str, compiled: str = f"build/artifacts/meridian/{'c' * 64}/", **env: str) -> str:
    """What make -n printed for a target, as kge would run it: each module's command line, python -m and its arguments,
    one a line, in order, with a stand-in for each, so nothing runs; it must succeed."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        code, ran = plan(monkeypatch, *argv, compiled=compiled, **env)
    assert code == 0, ran
    return "\n".join(" ".join([sys.executable, "-m", *step]) for step in ran) + "\n"


@pytest.fixture
def shop(monkeypatch, tmp_path):
    """A profile under tmp_path, the folder the commands run from, with nothing built."""
    monkeypatch.chdir(tmp_path)
    for name in (*cli.DEFAULTS, *SETTINGS):
        monkeypatch.delenv(name, raising=False)
    p = Profile("shop", "shop", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                tmp_path / "estate/warehouses", tmp_path / "identities.yaml", tmp_path / "baseline.json",
                tmp_path / "profiles.yaml")
    monkeypatch.setattr(cli.profile, "resolve", lambda *args, **kwargs: p)
    return p


@pytest.fixture
def runner(shop, monkeypatch):
    """(commands, calls): each module the commands run, as its name and arguments; compile prints an artifact's path."""
    calls = []
    stand_in_onboard(monkeypatch)

    def run(args, **kwargs):
        assert args[:2] == [sys.executable, "-m"]
        assert "shell" not in kwargs
        calls.append(args[2:])
        stdout = "artifacts/shop/abc/\n" if args[2] == "kge.compiler.compile" else onboard_output(args)
        return subprocess.CompletedProcess(args, 0, stdout)

    monkeypatch.setattr(cli.subprocess, "run", run)
    return cli.Commands({}), calls


def test_parse_settings_and_multiple_commands():
    assert cli.parse(["check", "compile", "LABEL=two words='fine'", "EMPTY=", "HASH=old", "HASH=new", "Q=a=b"]) == (
        ["check", "compile"], {"LABEL": "two words='fine'", "EMPTY": "", "HASH": "new", "Q": "a=b"})


@pytest.mark.parametrize("force", ["", "0", "1"])
def test_reset_onboard_only_discards_pending_proposals_when_explicit(runner, force):
    commands, calls = runner
    commands.explicit.update(UNIT="leasing", FORCE=force)
    commands.reset_onboard()
    assert calls[0] == ["kge.estate.onboard", "reset", "--unit", "leasing", *(["--force"] if force == "1" else [])]


def test_settings_may_come_before_the_command():
    assert cli.parse(["PRINCIPAL=steward", "sign-off"]) == (["sign-off"], {"PRINCIPAL": "steward"})


@pytest.mark.parametrize("args", [[], ["unknown"], ["HASH=x"], ["check", "=x"], ["check", "A B=x"], ["check", "--shell"]])
def test_parse_refuses_bad_arguments(args):
    with pytest.raises(SystemExit) as exc:
        cli.parse(args)
    assert exc.value.code == 2


def test_every_phase_one_command_and_prerequisite_is_offered():
    wanted = ("estate check compile load margin onboard sign-off release passwords serve improve ab baseline reset-overlay "
              "source rows draft accept certify-draft onboard-status reset-onboard client reset-client demo graph "
              "pipeline lock certify check-estate eval profile portal portal-build seed").split()
    assert set(wanted) <= set(cli.COMMANDS)
    for command in cli.COMMANDS:
        assert callable(getattr(cli.Commands, command.replace("-", "_")))


def test_main_applies_settings_to_the_environment_and_restores_it(shop, monkeypatch):
    monkeypatch.setenv("UNIT", "environment")
    seen = []
    monkeypatch.setattr(cli.Commands, "run", lambda self, command: seen.append((command, self.get("UNIT"), os.environ["UNIT"])))
    assert cli.main(["check", "compile", "UNIT=argument"]) == 0
    assert seen == [("check", "argument", "argument"), ("compile", "argument", "argument")]
    assert os.environ["UNIT"] == "environment"


def test_command_line_then_environment_then_default(shop, monkeypatch):
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("REPEATS", "5")
    commands = cli.Commands({"REPEATS": "1"})
    assert commands.get("PORT") == "8123"
    assert commands.get("REPEATS") == "1"
    assert commands.get("PORTAL_PORT") == "8213"
    assert commands.get("API") == "http://127.0.0.1:8123"
    assert commands.get("KGE_URL") == "http://127.0.0.1:8123"
    assert commands.get("ARMS") == "all"
    assert commands.get("HASH") == ""
    assert commands.get("BASELINE") == "baseline.json"
    monkeypatch.setenv("PORT", "not-a-number")
    assert cli.Commands({}).get("PORTAL_PORT") == ""


def test_profile_paths_are_read_in_process_and_the_environment_does_not_override_them(shop, monkeypatch):
    monkeypatch.setenv("PACK", "elsewhere")
    monkeypatch.setenv("BUILD", "elsewhere")
    commands = cli.Commands({})
    assert Path(commands.get("PACK")) == Path("clients/shop")
    assert commands.get("BUILD") == "build"
    assert commands.get("LANDED") == str(Path("landing/shop/current"))
    assert Path(commands.get("SUMMARY")) == Path("estate/summary.json")
    assert Path(commands.get("GRAPH")) == Path("artifacts/shop/current")
    assert Path(commands.get("PASSWORDS_FILE")) == Path("stores/shop/run/passwords.json")
    assert Path(commands.get("PORTAL_SYNC_FILE")) == Path("stores/shop/run/portal_sync_token")
    assert Path(commands.get("ESTATE")) == Path("clients/shop/baselines/estate-4d9d5c9.json")
    assert Path(commands.get("BEFORE")) == Path("clients/shop/baselines/issue-38.json")
    assert Path(commands.get("MARGIN_STATUS")) == Path("stores/shop/run/margin.status")
    assert cli.Commands({"PACK": "given"}).get("PACK") == "given"


def test_an_unreadable_profile_leaves_its_paths_empty(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def refuse(*args, **kwargs):
        raise cli.profile.ProfileError("no profile 'x'")

    monkeypatch.setattr(cli.profile, "resolve", refuse)
    commands = cli.Commands({})
    assert commands.words == {}
    assert commands.get("PACK") == commands.get("GRAPH") == commands.get("SUMMARY") == ""
    assert commands.get("PORTAL_SYNC_FILE") == "build/portal/run/portal_sync_token"
    assert commands.get("MARGIN_STATUS") == "margin.status"


def test_the_profile_is_exported_to_every_step(shop, monkeypatch):
    seen = []
    monkeypatch.setattr(cli.Commands, "run", lambda self, command: seen.append(os.environ.get("PROFILE")))
    assert cli.main(["check"]) == 0
    assert seen == ["shop"] and "PROFILE" not in os.environ


def test_dotenv_is_the_steps_own_not_a_setting_of_kge(shop, monkeypatch, tmp_path):
    from kge.pipeline import envfile
    (tmp_path / ".env").write_text("PORT=9000\n")
    monkeypatch.setattr(envfile, "DEMO", tmp_path)
    monkeypatch.setattr(envfile, "_loaded", set())
    seen = []

    def run(self, command):
        envfile.load_env()
        seen.append(self.get("PORT"))

    monkeypatch.setattr(cli.Commands, "run", run)
    assert cli.main(["serve"]) == 0
    assert seen == ["8000"]


@pytest.mark.parametrize(("command", "settings", "expected"), [
    ("compile", {}, [["kge.compiler.compile"]]),
    ("check", {}, [["kge.compiler.lock", "--check"], ["kge.compiler.compile", "--check"]]),
    ("passwords", {}, [["kge.services.identity.cli", "--passwords"]]),
    ("reset-overlay", {}, [["kge.pipeline.overlay", "--reset"]]),
    ("draft", {"UNIT": "unit", "PRINCIPAL": "author"}, [["kge.estate.onboard", "draft", "--unit", "unit", "--as", "author"]]),
    ("source", {"UNIT": "unit", "DDL": "file with space.sql"},
     [["kge.estate.onboard", "create", "--unit", "unit", "--ddl", "file with space.sql", "--as", ""]]),
    ("rows", {"UNIT": "unit", "ROWS": "csv", "CSV": "some folder", "PRINCIPAL": "author"},
     [["kge.estate.onboard", "rows", "--unit", "unit", "--rows", "csv", "--csv", "some folder", "--as", "author"]]),
    ("accept", {"UNIT": "unit"}, [["kge.estate.onboard", "accept", "--unit", "unit", "--item", "all", "--as", ""]]),
    ("accept", {}, [["kge.estate.onboard", "accept", "--item", "all", "--as", ""]]),
    ("certify-draft", {"PROPOSAL": "p", "UNIT": "ignored", "ITEM": "one"},
     [["kge.estate.onboard", "certify", "--proposal", "p", "--item", "one", "--as", ""]]),
    ("improve", {"PROPOSAL": "p", "UNIT": "unit", "PRINCIPAL": "author"},
     [["kge.improve.flow", "--id", "p", "--unit", "unit", "--as", "author"]]),
    ("improve", {}, [["kge.improve.flow"]]),
    ("onboard-status", {}, [["kge.estate.onboard", "status"]]),
    ("reset-client", {"CLIENT": "shop"}, [["kge.estate.onboard", "reset-client", "--client", "shop"]]),
    ("client", {"CLIENT": "shop", "LABEL": "The Shop", "DDL": "source.sql"},
     [["kge.estate.onboard", "client", "--client", "shop", "--label", "The Shop", "--ddl", "source.sql", "--as", "platform"]]),
    ("eval", {"PRINCIPAL": "asker"}, [["kge.eval.run_golden", "--arm", "all", "--as", "asker"]]),
    ("eval", {"SUITE": "uat/sage.json", "ARM": "treatment"},
     [["kge.eval.run_golden", "--arm", "treatment", "--suite", "uat/sage.json"]]),
    ("eval", {}, [["kge.eval.run_golden", "--arm", "all"]]),
    ("ab", {"BASELINE": ""}, [["kge.eval.harness", "--arms", "all", "--sets", "all", "--repeats", "3"]]),
    ("ab", {"PRINCIPAL": "asker"}, [["kge.eval.harness", "--arms", "all", "--sets", "all", "--repeats", "3", "--as", "asker",
                                     "--baseline", "baseline.json"]]),
    ("baseline", {"RUN": "run", "BASELINE": "base.json", "NAME": "My name"},
     [["kge.eval.report", "run", "--save-baseline", "base.json", "--name", "My name"]]),
    ("check-estate", {}, [["kge.estate.check_estate", "--snapshot", "clients/shop/baselines/estate-4d9d5c9.json",
                           "--baseline", "clients/shop/baselines/issue-38.json"]]),
    ("estate", {}, [["kge.estate.generate"], ["kge.estate.onboard", "build"], ["kge.estate.certify"]]),
    ("seed", {}, [["kge.estate.generate"], ["kge.estate.onboard", "build"], ["kge.estate.certify"]]),
])
def test_command_dispatch(runner, command, settings, expected):
    commands, calls = runner
    commands.explicit.update(settings)
    commands.run(command)
    assert calls == expected


@pytest.mark.parametrize(("command", "message", "stream"), [
    ("source", "make source UNIT=<unit> DDL=<file>|SEMANTIC=<folder>", "err"),
    ("rows", "make rows UNIT=<unit> ROWS=csv|invented", "err"),
    ("reset-onboard", "make reset-onboard UNIT=<unit>", "err"),
    ("client", "make client CLIENT=<id> LABEL=<label>", "err"),
    ("reset-client", "make reset-client CLIENT=<id>", "err"),
    ("sign-off", "exactly one of PRINCIPAL=<username> or ROLE=<certifying role>", "err"),
    ("baseline", "make baseline RUN=<run_id>: the run in stores/shop/run/runs to record as baseline.json", "out"),
])
def test_required_settings_refuse_before_dispatch(runner, capsys, command, message, stream):
    commands, calls = runner
    with pytest.raises(subprocess.CalledProcessError) as exc:
        commands.run(command)
    assert exc.value.returncode == 2
    assert calls == []
    assert message in getattr(capsys.readouterr(), stream)


def test_source_preview_and_semantic_take_precedence(runner):
    commands, calls = runner
    commands.explicit.update(UNIT="unit", DDL="ignored", SEMANTIC="semantic folder", PREVIEW="1", LABEL="Label",
                             ROWS="csv", CSV="csv")
    commands.source()
    assert calls == [["kge.estate.onboard", "tables", "--unit", "unit", "--semantic", "semantic folder", "--label", "Label",
                      "--rows", "csv", "--csv", "csv"]]


def test_client_db_takes_precedence(runner):
    commands, calls = runner
    commands.explicit.update(CLIENT="shop", LABEL="Shop", DB="database file", DDL="ignored", ROWS="invented", CSV="csv")
    commands.client()
    assert "--db" in calls[0] and "--ddl" not in calls[0]
    assert calls[0][-6:] == ["--csv", "csv", "--rows", "invented", "--as", "platform"]


@pytest.mark.parametrize("fixture", [False, True])
def test_estate_generator_or_fixture(runner, fixture):
    commands, calls = runner
    if fixture:
        schema = Path(commands.get("PACK")) / "fixture/schema.yaml"
        schema.parent.mkdir(parents=True)
        schema.touch()
    commands.estate()
    assert calls == [["kge.estate.from_files" if fixture else "kge.estate.generate"], ["kge.estate.onboard", "build"],
                     ["kge.estate.certify"]]


@pytest.mark.parametrize("existing", [False, True])
def test_certify_builds_the_estate_only_without_a_summary(runner, existing):
    commands, calls = runner
    if existing:
        summary = Path(commands.get("SUMMARY"))
        summary.parent.mkdir(parents=True)
        summary.touch()
    commands.certify()
    assert calls == ([] if existing else [["kge.estate.generate"], ["kge.estate.onboard", "build"]]) + [["kge.estate.certify"]]


def slashed(calls):
    """Each call's arguments with Windows separators written as POSIX ones, so the plan reads the same on both."""
    return [[argument.replace("\\", "/") for argument in call] for call in calls]


@pytest.mark.parametrize("landed", [False, True])
@pytest.mark.parametrize("command", ["load", "graph", "lock"])
def test_landed_prerequisite_and_graph_steps(runner, command, landed):
    commands, calls = runner
    if landed:
        Path(commands.get("LANDED")).mkdir(parents=True)
    commands.run(command)
    first = [] if landed else [["kge.estate.generate"], ["kge.estate.onboard", "build"], ["kge.estate.certify"]]
    assert calls[:len(first)] == first
    rest = slashed(calls[len(first):])
    if command == "lock":
        assert rest == [["kge.compiler.lock"]]
    elif command == "load":
        assert rest == [["kge.compiler.compile", "--check"], ["kge.graph.load", "--artifact", "artifacts/shop/abc/"],
                        ["kge.pipeline.run", "--hash", "abc"], ["kge.graph.release", "--hash", "abc", "--mark-loaded"]]
    else:
        assert rest == [["kge.compiler.lock", "--check"], ["kge.compiler.compile"],
                        ["kge.graph.load", "--artifact", "artifacts/shop/abc/"], ["kge.pipeline.run", "--hash", "abc"],
                        ["kge.graph.release", "--hash", "abc", "--mark-loaded"],
                        ["kge.graph.release", "--hash", "abc", "--ungated"]]


@pytest.mark.parametrize("sync", ["", "kge.estate.load_bigquery"])
@pytest.mark.parametrize("command", ["load", "release", "graph"])
@pytest.mark.parametrize("given", [False, True])
def test_hash_and_the_optional_bigquery_sync(runner, monkeypatch, sync, command, given):
    commands, calls = runner
    commands.words["sync"] = sync
    monkeypatch.setattr(commands, "ensure_landed", lambda: None)
    if given:
        commands.explicit["HASH"] = "def"
    commands.run(command)
    model_hash = "def" if given and command != "graph" else "abc"
    compiled = [call for call in calls if call[0] == "kge.compiler.compile"]
    assert compiled == ([] if given and command != "graph" else [["kge.compiler.compile"] + ([] if command == "graph" else ["--check"])])
    steps = slashed([call for call in calls if call[0] not in {"kge.compiler.compile", "kge.compiler.lock"}])
    synced = [[sync, "--hash", model_hash]] if sync else []
    if command == "release":
        assert steps == [["kge.graph.release", "--hash", model_hash, "--defer-retention"], *synced]
    else:
        assert steps[:2] == [["kge.graph.load", "--artifact", f"artifacts/shop/{model_hash}/"], ["kge.pipeline.run", "--hash", model_hash]]
        assert steps[2:2 + len(synced)] == synced
        assert steps[2 + len(synced)] == ["kge.graph.release", "--hash", model_hash, "--mark-loaded"]


@pytest.mark.parametrize("given", [False, True])
def test_signoff_candidate(runner, given):
    commands, calls = runner
    commands.explicit["PRINCIPAL"] = "steward"
    if given:
        commands.explicit["HASH"] = "def"
    commands.sign_off()
    assert calls[-1] == ["kge.graph.release", "--sign-off", "--as", "steward", "--hash", "def" if given else "abc"]
    assert len(calls) == (1 if given else 2)


@pytest.mark.parametrize("given", [False, True])
@pytest.mark.parametrize("command", ["pipeline", "margin"])
def test_graph_prerequisite_only_without_hash(runner, monkeypatch, given, command):
    commands, calls = runner
    needed = []
    monkeypatch.setattr(commands, "ensure_graph", lambda: needed.append(True))
    if given:
        commands.explicit["HASH"] = "abc"
    commands.run(command)
    assert needed == ([] if given else [True])
    assert ("--hash" in calls[-1]) is given
    if command == "margin":
        assert Path(commands.get("MARGIN_STATUS")).read_text() == "0\n"


def test_the_graph_prerequisite_runs_once_and_only_where_missing(runner):
    commands, calls = runner
    Path(commands.get("LANDED")).mkdir(parents=True)
    commands.run("pipeline")
    commands.run("pipeline")
    assert [call[0] for call in calls].count("kge.compiler.compile") == 1
    Path("served.graph").touch()
    calls.clear()
    cli.Commands({"GRAPH": "served.graph"}).run("pipeline")
    assert calls == [["kge.pipeline.run"]]


@pytest.mark.parametrize(("baseline", "shown"), [("base.json", "base.json"), ("", "''")])
def test_margin_says_its_command_and_writes_its_exit_code(runner, monkeypatch, capsys, baseline, shown):
    commands, calls = runner
    commands.explicit.update(HASH="abc", BASELINE=baseline, REPEATS="1", PY="python3")
    original = commands.module
    monkeypatch.setattr(commands, "module", lambda name, *a, **k: 1 if name == "kge.eval.margin" else original(name, *a, **k))
    with pytest.raises(subprocess.CalledProcessError) as exc:
        commands.margin()
    assert exc.value.returncode == 1
    assert capsys.readouterr().out == f"python3 -m kge.eval.margin --repeats 1 --baseline {shown} --hash abc\n"
    assert Path(commands.get("MARGIN_STATUS")).read_text() == "1\n"


@pytest.mark.parametrize(("lock", "compiled", "expected"), [(2, 0, 2), (0, 3, 3), (2, 3, 3), (0, 0, 0)])
def test_check_always_runs_both_and_keeps_the_exit_code(runner, monkeypatch, lock, compiled, expected):
    commands, _ = runner
    statuses = iter([lock, compiled])
    monkeypatch.setattr(commands, "module", lambda *args, **kwargs: next(statuses))
    if expected:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            commands.check()
        assert exc.value.returncode == expected
    else:
        commands.check()
    assert next(statuses, None) is None


@pytest.mark.parametrize(("current", "loaded", "code"), [(True, True, 0), (False, True, 0), (False, False, 1), (False, False, 2)])
def test_onboard_skips_loaded_candidates_and_records_margin(runner, monkeypatch, capsys, current, loaded, code):
    commands, calls = runner
    Path(commands.get("LANDED")).mkdir(parents=True)
    commands.explicit.update(CURRENT="abc" if current else "", CURRENT_LOADED="yes" if current else "")
    monkeypatch.setattr(cli, "profile_words", lambda model_hash=None: {"loaded": "yes"} if loaded else {})
    original = commands.module
    monkeypatch.setattr(commands, "module", lambda name, *a, **k: code if name == "kge.eval.margin" else original(name, *a, **k))
    if code:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            commands.onboard()
        assert exc.value.returncode == code
    else:
        commands.onboard()
    out = capsys.readouterr().out
    assert calls[0] == ["kge.estate.onboard", "build", "--json"]
    assert calls[1][:3] == ["kge.compiler.lock", "--refresh", "--changes"]
    assert out.startswith(f"onboard: [1/{cli.ONBOARD_STEPS}] inputs")
    assert f"onboard: [7/{cli.ONBOARD_STEPS}] lock" in out
    assert "onboard: candidate abc\n" in out
    assert any(call[0] == "kge.graph.load" for call in calls) is (not loaded and not current)
    if loaded:
        assert out.endswith("onboard: current already\n" if current else "onboard: candidate already loaded and checked=abc\n")
        assert not any(call[0] == "kge.estate.onboard" and call[1] == "record-onboard" for call in calls)
        return
    assert not any(call[0] == "kge.estate.onboard" and call[1] == "record-onboard" for call in calls)
    record = json.loads((cli.profile.resolve().run_dir() / "onboard-run.json").read_text())
    assert record["outcome"] == {1: "gate failed", 2: "not checked"}[code]
    assert Path(commands.get("MARGIN_STATUS")).read_text() == f"{code}\n"
    assert "onboard: dropped nothing" in out


def test_onboard_refreshes_an_existing_lock(runner):
    commands, calls = runner
    Path(commands.get("LANDED")).mkdir(parents=True)
    lock = Path(commands.get("PACK")) / "pack.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    commands.explicit.update(CURRENT="abc", CURRENT_LOADED="yes")
    commands.onboard()
    assert calls[1][:3] == ["kge.compiler.lock", "--refresh", "--changes"]


@pytest.mark.parametrize("reset", ["", "restored"])
def test_reset_onboard_only_rebuilds_after_reset(runner, monkeypatch, capsys, reset):
    commands, _ = runner
    commands.explicit["UNIT"] = "unit"
    monkeypatch.setattr(commands, "module", lambda *args, **kwargs: reset)
    steps = []
    for name in ("certify", "lock", "graph"):
        monkeypatch.setattr(cli.Commands, name, lambda self, name=name: steps.append(name))
    commands.reset_onboard()
    assert steps == (["certify", "lock", "graph"] if reset else [])
    assert ("no onboarded unit to restore a graph for" in capsys.readouterr().out) is (not reset)


def test_profile_is_in_process(runner, monkeypatch):
    commands, calls = runner
    args = []
    monkeypatch.setattr(cli.profile, "main", lambda argv: args.append(argv) or 0)
    commands.profile()
    assert args == [[]] and calls == []


@pytest.mark.parametrize(("command", "function"), [("serve", "serve"), ("portal", "portal"), ("portal-build", "build"),
                                                   ("demo", "demo")])
def test_server_dispatch(runner, monkeypatch, command, function):
    from kge.cli import servers
    commands, _ = runner
    calls = []
    monkeypatch.setattr(servers, function, lambda value: calls.append(value))
    commands.run(command)
    assert calls == [commands]


def test_a_failed_step_stops_the_sequence_with_its_code(runner, monkeypatch):
    commands, calls = runner

    def fail(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 7)

    monkeypatch.setattr(cli.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        commands.load_steps("abc")
    assert exc.value.returncode == 7
    assert len(calls) == 1


def test_py_is_a_setting_never_run(runner):
    commands, calls = runner
    commands.explicit["PY"] = "python; sh unsafe"
    commands.compile()
    assert calls == [["kge.compiler.compile"]]


def test_main_keeps_the_step_exit_code(shop, monkeypatch):
    def fail(self, command):
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(cli.Commands, "run", fail)
    assert cli.main(["check"]) == 7


def test_main_stops_at_the_first_failed_command(shop, monkeypatch):
    seen = []

    def run(self, command):
        seen.append(command)
        raise cli.Failed(3, command)

    monkeypatch.setattr(cli.Commands, "run", run)
    assert cli.main(["check", "compile"]) == 3
    assert seen == ["check"]


def test_the_cli_never_runs_make_or_a_shell():
    """No subprocess call names make, sh, bash, cmd or PowerShell, and none takes shell=True or os.system."""
    for path in sorted(CLI.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"make", "sh", "bash", "cmd", "cmd.exe", "powershell", "pwsh", "/bin/sh"}, path
            if isinstance(node, ast.keyword):
                assert not (node.arg == "shell" and isinstance(node.value, ast.Constant) and node.value.value), path
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"system", "popen", "spawnl", "execvp", "execl"}, path


def test_the_pages_run_checks_runs_kge_onboard_with_one_repeat():
    from kge.services.onboard.service import COMMANDS
    assert COMMANDS["checks"] == [sys.executable, "-m", "kge.cli", "onboard", "REPEATS=1", f"PY={sys.executable}"]
    assert cli.parse(COMMANDS["checks"][3:]) == (["onboard"], {"REPEATS": "1", "PY": sys.executable})


def test_onboard_progress_total_is_shared_from_the_pipeline():
    from kge.pipeline import onboard_steps
    assert cli.ONBOARD_STEPS == onboard_steps.ONBOARD_STEPS
    tree = ast.parse(Path(cli.__file__).read_text())
    assert not any(isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "ONBOARD_STEPS" for target in node.targets) for node in tree.body)


@pytest.mark.parametrize("sync", ["", "sync.module"])
def test_release_previous_uses_only_the_ledger(runner, sync):
    commands, calls = runner
    commands.explicit.update(PREVIOUS="yes", HASH="ignored", SYNC=sync)
    commands.release()
    assert calls == [["kge.graph.release", "--previous", "--defer-retention"]]


def test_failed_ledger_rollback_does_not_sync(runner, monkeypatch):
    commands, calls = runner
    commands.explicit.update(PREVIOUS="yes", SYNC="sync.module")
    monkeypatch.setattr(cli.subprocess, "run", lambda args, **kwargs: calls.append(args[2:]) or subprocess.CompletedProcess(args, 2))
    with pytest.raises(cli.Failed) as error:
        commands.release()
    assert error.value.returncode == 2 and calls == [["kge.graph.release", "--previous", "--defer-retention"]]


def test_release_named_rollback_skips_sync_and_discards_stale_rollback(runner, shop, monkeypatch):
    commands, calls = runner
    commands.explicit.update(HASH="held", SYNC="sync.module")
    record = shop.run_dir() / "rollback.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("stale")

    def rolled_back(name, *args, **kwargs):
        assert not record.exists()
        calls.append([name, *args])
        record.write_text('{"hash":"held"}')
        return 0

    monkeypatch.setattr(commands, "module", rolled_back)
    commands.release()
    assert calls == [["kge.graph.release", "--hash", "held", "--defer-retention"]]


@pytest.mark.parametrize("code", [0, 2])
@pytest.mark.parametrize("profile_set", [False, True])
def test_cli_renews_the_job_lease_until_the_command_finishes(shop, monkeypatch, code, profile_set):
    from kge.pipeline import lease, objstore
    shop.run_dir().mkdir(parents=True, exist_ok=True)
    (shop.run_dir() / lease.LEASE_FILE).write_text(json.dumps({"client": "shop", "command": "onboard",
        "token": "test-holder", "started_at": "start", "expires_at": "end", "version": "1"}))
    monkeypatch.setenv("STORE_URL", str(shop.run_dir() / "objects"))
    monkeypatch.setattr(cli, "profile_words", lambda: {"id": "shop"})
    if profile_set:
        monkeypatch.setenv("PROFILE", "explicit")

    def resolve():
        assert os.environ["PROFILE"] == ("explicit" if profile_set else "shop")
        return shop

    monkeypatch.setattr(cli.profile, "resolve", resolve)
    events = []

    class Stop:
        def set(self):
            events.append("stop")

    def heartbeat(store, held):
        assert held.client == "shop" and held.command == "onboard"
        events.append("heartbeat")
        return Stop()

    def run(self, command):
        assert events == ["heartbeat"]
        events.append(command)
        if code:
            raise cli.Failed(code, "onboard")

    monkeypatch.setattr(objstore, "the_store", lambda: object())
    monkeypatch.setattr(lease, "heartbeat", heartbeat)
    monkeypatch.setattr(cli.Commands, "run", run)
    assert cli.main(["onboard"]) == code
    assert events == ["heartbeat", "onboard", "stop"]


def test_signoff_operator_role_is_explicit(runner):
    commands, calls = runner
    commands.explicit.update(ROLE="certifier", HASH="abc")
    commands.sign_off()
    assert calls[-1] == ["kge.graph.release", "--sign-off", "--role", "certifier", "--hash", "abc"]
    commands.explicit["PRINCIPAL"] = "steward@example.com"
    with pytest.raises(cli.Failed):
        commands.sign_off()


@pytest.mark.parametrize("command,module,args", [
    ("access", "kge.services.access.cli", []),
    ("setup-client", "kge.pipeline.registry", ["setup-client"]),
])
def test_access_and_registry_dispatch(runner, command, module, args):
    commands, calls = runner
    commands.run(command)
    assert calls[-1] == [module, *args]


@pytest.mark.parametrize("stored", [False, True])
def test_release_post_eval_failure_still_syncs_before_retention(runner, monkeypatch, stored):
    from kge.graph import release
    commands, calls = runner
    commands.explicit.update(HASH="abc", SYNC="sync.module")
    if stored:
        commands.explicit["STORE_URL"] = "/unused-test-store"
    else:
        monkeypatch.delenv("STORE_URL", raising=False)
    events = []

    def module(name, *args, **kwargs):
        events.append(name)
        return 3 if name == "kge.graph.release" else 0

    monkeypatch.setattr(commands, "module", module)
    monkeypatch.setattr(release, "finish", lambda _: events.append("retire"))
    with pytest.raises(cli.Failed) as error:
        commands.release()
    assert error.value.returncode == 3
    assert events == ["kge.graph.release", "sync.module"] + ([] if stored else ["retire"])


def test_release_sync_failure_does_not_retire(runner, monkeypatch):
    from kge.graph import release
    commands, calls = runner
    commands.explicit.update(HASH="abc", SYNC="sync.module")
    events = []

    def module(name, *args, **kwargs):
        events.append(name)
        if name == "sync.module":
            raise cli.Failed(2, name)
        return 0

    monkeypatch.setattr(commands, "module", module)
    monkeypatch.setattr(release, "finish", lambda _: events.append("retire"))
    with pytest.raises(cli.Failed):
        commands.release()
    assert events == ["kge.graph.release", "sync.module"]


@pytest.mark.parametrize("command,settings,expected", [
    ("agent", {"AGENT_ARM": "treatment", "Q": "A question", "PRINCIPAL": "person@example.com"},
     ["kge.services.ask.engine.agent", "--arm", "treatment", "--as", "person@example.com", "A question"]),
    ("eval-api", {"API": "http://localhost:8123", "ARM": "graph", "PRINCIPAL": "person@example.com"},
     ["kge.eval.run_golden", "--api", "http://localhost:8123", "--arm", "graph", "--as", "person@example.com"]),
    ("golden", {"PACK": "no-fixture"}, ["kge.estate.generate", "--golden-only"]),
    ("principals", {}, ["kge.graph.load", "--principals-only"]),
    ("tokens", {}, ["kge.services.identity.cli"]),
])
def test_readme_commands_dispatch_to_the_existing_modules(runner, command, settings, expected):
    commands, calls = runner
    commands.explicit.update(settings)
    commands.run(command)
    assert calls == [expected]


def test_parity_keeps_its_snapshot_stated_changes_and_graph_prerequisite(runner, tmp_path, monkeypatch):
    commands, calls = runner
    stated = tmp_path / "stated.json"
    stated.write_text("{}")
    commands.explicit.update(PARITY="snapshot.json", PARITY_STATED=str(stated))
    built = []
    monkeypatch.setattr(commands, "ensure_graph", lambda: built.append("graph"))
    commands.parity()
    assert built == ["graph"]
    assert calls == [["kge.eval.parity", "--snapshot", "snapshot.json", "--stated", str(stated)]]
    calls.clear()
    stated.unlink()
    commands.parity()
    assert calls == [["kge.eval.parity", "--snapshot", "snapshot.json"]]


@pytest.mark.parametrize("installed,ci,code", [(True, False, 0), (False, False, 0), (False, True, 1)])
def test_demo_script_prepares_playwright_and_preserves_its_exit_code(shop, monkeypatch, installed, ci, code):
    from kge.cli import servers
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(servers, "needs", lambda: "")
    monkeypatch.setattr(servers, "npm_command", lambda: ["npm"])
    monkeypatch.setattr(servers.shutil, "which", lambda name: name)
    calls = []
    environments = []

    def run(args, **kwargs):
        calls.append(args)
        if "env" in kwargs:
            environments.append(kwargs["env"])
        result = 0 if installed or args[1:2] != ["-e"] else 1
        if args[-1] == "tests/e2e/demo_script.cjs":
            result = code
        return subprocess.CompletedProcess(args, result, "/global/modules\n" if args == ["npm", "root", "-g"] else "")

    monkeypatch.setattr(servers.subprocess, "run", run)
    values = {"PART": "2/4", "PORT": "8123", "PORTAL_PORT": "8213", "TEST_PORT": "8223", "HEADED": "1"}
    assert cli.main(["demo-script", *(f"{name}={value}" for name, value in values.items()),
                     *( ["CI=true"] if ci else [])]) == code
    assert calls[0] == ["npm", "root", "-g"]
    assert calls[1] == ["node", "-e", 'require("playwright")']
    assert calls[-1] == ["node", "tests/e2e/demo_script.cjs"]
    if not installed:
        assert calls[2] == ["npm", "install", "--prefix", "build/node", "--no-save", "--no-package-lock", "--no-audit",
                            "--no-fund", "playwright@1.56.1"]
        assert calls[3] == ["node", "build/node/node_modules/playwright/cli.js", "install",
                            *(["--with-deps"] if ci else []), "chromium"]
    assert all(all(env[name] == value for name, value in values.items()) for env in environments)
    assert all(env["PY"] == sys.executable for env in environments)
    assert all(env["NODE_PATH"] == f"{shop.registry.parent}/build/node/node_modules{os.pathsep}/global/modules" for env in environments)


def test_demo_script_refuses_missing_node_before_running_anything(shop, monkeypatch):
    from kge.cli import servers
    monkeypatch.setattr(servers, "needs", lambda: "needs Node.js 22.12 or later")
    monkeypatch.setattr(servers.subprocess, "run", lambda *args, **kwargs: pytest.fail("nothing may run"))
    assert cli.main(["demo-script"]) == 2


def test_clean_removes_every_profiles_build_and_no_pack(shop):
    build = shop.registry.parent / "build"
    for name in ("estate/rows", "stores/shop/overlay/decisions.jsonl", "stores/other/run/tokens.json", "artifacts/shop/current"):
        path = build / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("kept until clean")
    shop.clients.mkdir()
    cli.Commands({}).clean()
    assert not build.exists() and shop.clients.is_dir()
    cli.Commands({}).clean()
