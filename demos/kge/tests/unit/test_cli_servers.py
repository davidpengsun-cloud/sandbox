"""kge serve, portal, portal-build and demo: the commands they run, and how they stop on POSIX and on Windows, with
stand-ins for the servers. No make, no shell and no socket: the one real child is this interpreter, asleep."""
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kge import cli
from kge.cli import servers
from kge.pipeline.profile import Profile


@pytest.fixture
def commands(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("PORT", "HOST", "HASH", "API", "RELOAD", "DEMO_MODE", "PORTAL_SYNC_TOKEN", "PORTAL_CATALOGUE_FILE",
                 "KGE_URL", "PORTAL_PORT", "SIGNIN", "PROFILE"):
        monkeypatch.delenv(name, raising=False)
    p = Profile("shop", "shop", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                tmp_path / "estate/warehouses", tmp_path / "identities.yaml", None, tmp_path / "profiles.yaml")
    monkeypatch.setattr(cli.profile, "resolve", lambda *args, **kwargs: p)
    monkeypatch.setattr(servers.profile, "main", lambda argv: 0)
    return cli.Commands({})


class Child:
    """A stand-in for a server's Popen: it ends with code after ticks polls, or never, and records what it was sent."""

    def __init__(self, code=None, ticks=0, on_poll=None, stubborn=False):
        self.pid, self.code, self.ticks, self.on_poll, self.stubborn = 4242, code, ticks, on_poll, stubborn
        self.returncode, self.sent = None, []

    def poll(self):
        if self.on_poll:
            self.on_poll()
        if self.returncode is None and self.code is not None:
            if self.ticks <= 0:
                self.returncode = self.code
            self.ticks -= 1
        return self.returncode

    def send_signal(self, number):
        self.sent.append(("signal", number))
        if not self.stubborn:
            self.returncode = -number

    def terminate(self):
        self.sent.append(("terminate",))

    def kill(self):
        self.sent.append(("kill",))
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_token_is_made_once_owner_only_and_a_given_one_wins(commands, monkeypatch):
    path = Path(commands.get("PORTAL_SYNC_FILE"))
    first = servers.token(commands)
    assert len(first) == 48 and path.read_text() == first + "\n"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert servers.token(commands) == first
    commands.explicit["PORTAL_SYNC_TOKEN"] = "given"
    assert servers.token(commands) == "given"


@pytest.mark.parametrize(("npm", "version", "why"), [
    (None, "v22.12.0", "needs npm, with Node.js 22.12 or later: https://nodejs.org"),
    ("/bin/npm", "v22.12.0", ""),
    ("/bin/npm", "v23.0.1", ""),
    ("/bin/npm", "v22.11.9", "needs Node.js 22.12 or later (found: 22.11.9): https://nodejs.org"),
    ("/bin/npm", None, "needs Node.js 22.12 or later (found: none): https://nodejs.org"),
])
def test_needs_names_what_the_portal_lacks(monkeypatch, npm, version, why):
    monkeypatch.setattr(servers.shutil, "which", lambda name: npm if name == "npm" else "/bin/node")

    def run(args, **kwargs):
        if version is None:
            raise FileNotFoundError(args[0])
        return subprocess.CompletedProcess(args, 0, version + "\n", "")

    monkeypatch.setattr(servers.subprocess, "run", run)
    assert servers.needs() == why


def test_npm_on_windows_runs_its_script_with_node(monkeypatch, tmp_path):
    script = tmp_path / "node_modules/npm/bin/npm-cli.js"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(servers.shutil, "which", lambda name: str(tmp_path / "npm.cmd") if name == "npm" else "node.exe")
    monkeypatch.setattr(servers, "WINDOWS", True)
    assert servers.npm_command() == ["node.exe", str(script)]
    monkeypatch.setattr(servers, "WINDOWS", False)
    assert servers.npm_command() == [str(tmp_path / "npm.cmd")]


def test_build_refuses_without_node(commands, monkeypatch, capsys):
    monkeypatch.setattr(servers, "needs", lambda: "needs npm, with Node.js 22.12 or later: https://nodejs.org")
    with pytest.raises(subprocess.CalledProcessError) as exc:
        servers.build(commands)
    assert exc.value.returncode == 2
    assert "make portal needs npm" in capsys.readouterr().err


def test_build_installs_and_builds_only_what_is_out_of_date(commands, monkeypatch):
    monkeypatch.setattr(servers, "needs", lambda: "")
    ran = []
    def npm(*args, cwd):
        ran.append((Path(cwd).as_posix(), args))
        if args[0] == "ci":
            (Path(cwd) / "node_modules").mkdir(exist_ok=True)

    monkeypatch.setattr(servers, "npm", npm)
    for folder in ("portal/ui/src", "portal/server"):
        Path(folder).mkdir(parents=True)
    for lock in ("portal/ui/package-lock.json", "portal/server/package-lock.json", "portal/ui/src/main.js"):
        Path(lock).touch()
    servers.build(commands)
    assert ran == [("portal/ui", ("ci", "--no-audit", "--no-fund")), ("portal/ui", ("run", "build")),
                   ("portal/server", ("ci", "--no-audit", "--no-fund", "--omit=dev"))]
    Path("portal/ui/build").mkdir()
    Path("portal/ui/build/index.html").touch()
    later = time.time() + 10
    os.utime("portal/ui/build/index.html", (later, later))
    for marker in ("portal/ui/node_modules/.package-lock.json", "portal/server/node_modules/.package-lock.json"):
        os.utime(marker, (later, later))
    ran.clear()
    servers.build(commands)
    assert ran == []
    newer = later + 10
    os.utime("portal/ui/src/main.js", (newer, newer))
    servers.build(commands)
    assert ran == [("portal/ui", ("run", "build"))]


def test_api_command_and_environment(commands):
    commands.explicit.update(PORT="8971", HASH="abc", DEMO_MODE="1", RELOAD="--reload  --reload-dir kge")
    args, cwd, env = servers.api_spec(commands, signin=True)
    assert args == [sys.executable, "-m", "uvicorn", "kge.app:app", "--app-dir", ".", "--host", "127.0.0.1", "--port", "8971",
                    "--reload", "--reload-dir", "kge"]
    assert cwd == Path.cwd()
    assert env["SIGNIN"] == "passwords" and env["DEMO_MODE"] == "1" and env["SERVE_HASH"] == "abc"
    assert env["API_URL"] == "http://127.0.0.1:8971"
    assert env["PORTAL_SYNC_TOKEN"] == Path(commands.get("PORTAL_SYNC_FILE")).read_text().strip()
    assert "SIGNIN" not in servers.api_spec(commands)[2]


def test_api_refuses_an_unknown_profile(commands, monkeypatch):
    monkeypatch.setattr(servers.profile, "main", lambda argv: 2)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        servers.api_spec(commands)
    assert exc.value.returncode == 2


@pytest.mark.parametrize("catalogue", ["", "examples/catalogue.yaml"])
def test_portal_command_and_environment(commands, monkeypatch, catalogue):
    monkeypatch.setattr(servers.shutil, "which", lambda name: None)
    commands.explicit.update(PORT="8970", PORTAL_CATALOGUE_FILE=catalogue, KGE_URL="" if catalogue else "http://127.0.0.1:8970")
    args, cwd, env = servers.portal_spec(commands)
    assert args == ["node", "--experimental-sqlite", "src/index.js"]
    assert cwd == Path("portal") / "server"
    assert env["PORT"] == "9060" and env["PORTAL_BACKEND"] == "sqlite" and env["PORTAL_UI_PATH"] == "../ui/build"
    assert env["PORTAL_SQLITE_PATH"] == os.path.abspath(Path("build/portal/portal.db"))
    assert Path("build/portal").is_dir()
    if catalogue:
        assert env["PORTAL_SYNC_TOKEN"] == "" and env["KGE_URL"] == ""
        assert env["PORTAL_CATALOGUE_FILE"] == os.path.abspath(catalogue)
        assert not Path(commands.get("PORTAL_SYNC_FILE")).exists()
    else:
        assert env["PORTAL_SYNC_TOKEN"] and "PORTAL_CATALOGUE_FILE" not in env


def test_each_server_gets_a_group_of_its_own(monkeypatch):
    monkeypatch.setattr(servers, "WINDOWS", False)
    assert servers.group_options() == {"start_new_session": True}
    monkeypatch.setattr(servers, "WINDOWS", True)
    assert servers.group_options() == {"creationflags": servers.NEW_PROCESS_GROUP}
    assert servers.NEW_PROCESS_GROUP == getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)


def test_posix_stop_sends_sigterm_to_the_group_then_kills(monkeypatch):
    monkeypatch.setattr(servers, "WINDOWS", False)
    sent = []
    polite, stubborn = Child(), Child()
    polite.pid, stubborn.pid = 1, 2

    def killpg(pid, number):
        sent.append((pid, number))
        if pid == 1 and number == signal.SIGTERM:
            polite.returncode = 0
        if number == getattr(signal, "SIGKILL", 9):
            stubborn.returncode = -9

    monkeypatch.setattr(servers.os, "killpg", killpg, raising=False)
    servers.stop([polite, stubborn], grace=0.05)
    assert sent == [(1, signal.SIGTERM), (2, signal.SIGTERM), (2, getattr(signal, "SIGKILL", 9))]


def test_windows_stop_sends_ctrl_break_then_terminates_or_kills(monkeypatch):
    monkeypatch.setattr(servers, "WINDOWS", True)
    gentle, stubborn = Child(), Child(stubborn=True)
    servers.stop([gentle, stubborn], grace=0.05)
    assert gentle.sent == [("signal", servers.CTRL_BREAK)]
    assert stubborn.sent == [("signal", servers.CTRL_BREAK), ("kill",)]
    refusing = Child()
    refusing.send_signal = lambda number: (_ for _ in ()).throw(OSError("no console"))
    refusing.terminate = lambda: setattr(refusing, "returncode", 1)
    servers.stop([refusing], grace=0.05)
    assert refusing.returncode == 1


def test_supervise_returns_the_first_to_end_and_stops_the_rest(monkeypatch):
    children = iter([Child(code=3, ticks=1), Child()])
    started, stopped = [], []
    monkeypatch.setattr(servers.subprocess, "Popen", lambda args, **kwargs: started.append((args, kwargs)) or next(children))
    monkeypatch.setattr(servers, "stop", lambda processes, grace: stopped.extend(processes))
    assert servers.supervise([(["a"], ".", {}), (["b"], ".", {})]) == 3
    assert [args for args, _ in started] == [["a"], ["b"]]
    assert all("shell" not in kwargs and kwargs.get("start_new_session") or kwargs.get("creationflags") for _, kwargs in started)
    assert len(stopped) == 2


@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM"])
def test_supervise_stops_both_on_ctrl_c_or_a_stop_and_says_which(monkeypatch, name):
    number = getattr(signal, name)
    before = signal.getsignal(number)

    def deliver():
        signal.getsignal(number)(number, None)

    monkeypatch.setattr(servers.subprocess, "Popen", lambda args, **kwargs: Child(on_poll=deliver))
    stopped = []
    monkeypatch.setattr(servers, "stop", lambda processes, grace: stopped.extend(processes))
    assert servers.supervise([(["a"], ".", {}), (["b"], ".", {})]) == 128 + number
    assert len(stopped) == 2
    assert signal.getsignal(number) is before


@pytest.mark.skipif(os.name == "nt", reason="the session and its group are POSIX's")
def test_a_real_child_in_its_own_session_is_stopped():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **servers.group_options())
    try:
        assert os.getpgid(child.pid) != os.getpgid(0)
        servers.stop([child], grace=10)
        assert child.returncode == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()


def test_serve_ends_with_the_server_code(commands, monkeypatch):
    monkeypatch.setattr(servers, "supervise", lambda specs: 0)
    servers.serve(commands)
    monkeypatch.setattr(servers, "supervise", lambda specs: 130)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        servers.serve(commands)
    assert exc.value.returncode == 130


@pytest.mark.parametrize("why", ["", "needs npm, with Node.js 22.12 or later: https://nodejs.org"])
def test_demo_builds_then_runs_both_or_kge_alone(commands, monkeypatch, capsys, why):
    steps, ran = [], []
    for name in ("ensure_graph", "pipeline", "ensure_passwords"):
        monkeypatch.setattr(cli.Commands, name, lambda self, name=name: steps.append(name))
    monkeypatch.setattr(servers, "needs", lambda: why)
    monkeypatch.setattr(servers, "build", lambda c: steps.append("portal-build"))
    monkeypatch.setattr(servers, "supervise", lambda specs: ran.extend(specs) or 0)
    monkeypatch.setattr(servers.shutil, "which", lambda name: None)
    commands.explicit["PORT"] = "8972"
    servers.demo(commands)
    out = capsys.readouterr()
    assert steps == ["ensure_graph", "pipeline", "ensure_passwords"] + ([] if why else ["portal-build"])
    assert Path(commands.get("PORTAL_SYNC_FILE")).is_file()
    assert ran[0][2]["SIGNIN"] == "passwords" and "8972" in ran[0][0]
    if why:
        assert len(ran) == 1
        assert f"make demo: KGE alone, the discovery portal skipped: it {why}" in out.err
    else:
        assert [spec[0][0] for spec in ran] == [sys.executable, "node"]
        assert ran[1][2]["PORT"] == "9062" and "SIGNIN" not in ran[1][2]
        assert ("make demo: KGE on http://127.0.0.1:8972, the discovery portal on http://127.0.0.1:9062; Ctrl-C stops both"
                in out.out)
