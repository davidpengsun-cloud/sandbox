"""kge, the steps' one definition: its settings, as make read them, and each command's dispatch, with stand-ins for the
modules it runs. No make, no shell, no socket and nothing built."""
import ast
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
            "KGE_URL", "PORTAL_PORT", "PACK", "STORE", "BUILD", "PORTAL_SYNC_TOKEN", "RELOAD", "DEMO_MODE")


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

    def run(args, **kwargs):
        assert args[:2] == [sys.executable, "-m"] and "shell" not in kwargs
        ran.append(list(args[2:]))
        printed = compiled if args[2] == "kge.compiler.compile" else ""
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

    def run(args, **kwargs):
        assert args[:2] == [sys.executable, "-m"]
        assert "shell" not in kwargs
        calls.append(args[2:])
        stdout = "artifacts/shop/abc/\n" if args[2] == "kge.compiler.compile" else ""
        return subprocess.CompletedProcess(args, 0, stdout)

    monkeypatch.setattr(cli.subprocess, "run", run)
    return cli.Commands({}), calls


def test_parse_settings_and_multiple_commands():
    assert cli.parse(["check", "compile", "LABEL=two words='fine'", "EMPTY=", "HASH=old", "HASH=new", "Q=a=b"]) == (
        ["check", "compile"], {"LABEL": "two words='fine'", "EMPTY": "", "HASH": "new", "Q": "a=b"})


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
    ("sign-off", "make sign-off PRINCIPAL=<steward> [HASH=<hash>]", "err"),
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
        assert steps == [["kge.graph.release", "--hash", model_hash], *synced]
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
    assert calls[:3] == [["kge.estate.onboard", "build"], ["kge.estate.certify"], ["kge.compiler.lock"]]
    assert out.startswith("onboard: [1/7] units      -> kge/estate/onboard/units.py build\n"
                          "onboard: [2/7] certify    -> kge/estate/certify.py\n"
                          "onboard: [3/7] lock       -> kge/compiler/lock.py\n")
    assert "onboard: candidate abc\n" in out
    assert any(call[0] == "kge.graph.load" for call in calls) is (not loaded and not current)
    if current:
        assert out.endswith("onboard: current already\n")
        assert not any(call[0] == "kge.estate.onboard" and call[1] == "record-onboard" for call in calls)
        return
    assert ("onboard: candidate already loaded=abc" in out) is loaded
    assert calls[-1] == ["kge.estate.onboard", "record-onboard", "--hash", "abc", "--passed", "false" if code else "true"]
    assert Path(commands.get("MARGIN_STATUS")).read_text() == f"{code}\n"
    assert out.endswith({0: "onboard: gate passed\n", 1: "onboard: gate failed\n", 2: "onboard: not checked\n"}[code])


def test_onboard_extends_an_existing_lock(runner):
    commands, calls = runner
    lock = Path(commands.get("PACK")) / "pack.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    commands.explicit.update(CURRENT="abc", CURRENT_LOADED="yes")
    commands.onboard()
    assert calls[2] == ["kge.compiler.lock", "--add-new"]


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
