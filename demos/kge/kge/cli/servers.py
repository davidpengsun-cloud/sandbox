"""kge serve, portal, portal-build and demo: the API and the discovery portal, built and run in the foreground.

Each server runs in a process group of its own (a new session on POSIX, CREATE_NEW_PROCESS_GROUP on Windows), so Ctrl-C
or a stop reaches this process alone, which then stops each server: SIGTERM to its group on POSIX, CTRL_BREAK_EVENT (or
terminate) on Windows, and a kill after the grace period. Either server ending stops the other. The exit code is 128
plus the signal's number where a signal stopped it, else that of the server that ended first.
"""
from __future__ import annotations

import base64
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from kge.pipeline import profile

GRACE = 20.0
POLL = 0.2
PORTAL_UI_FILES = ("index.html", "vite.config.mjs", "package.json")
WINDOWS = os.name == "nt"
NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
CTRL_BREAK = getattr(signal, "CTRL_BREAK_EVENT", 1)


def say(*args, err: bool = False):
    print(*args, file=sys.stderr if err else sys.stdout, flush=True)


def token(commands) -> str:
    """PORTAL_SYNC_TOKEN where it is given, else the run state's token file, made once (owner-only) where missing."""
    supplied = commands.get("PORTAL_SYNC_TOKEN")
    if supplied.strip():
        return supplied
    path = Path(commands.get("PORTAL_SYNC_FILE"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(base64.b64encode(os.urandom(36)).decode("ascii") + "\n")
    return path.read_text().replace("\n", "")


def needs() -> str:
    """What the portal lacks to run here (npm, or Node.js 22.12 or later), or nothing."""
    if not shutil.which("npm"):
        return "needs npm, with Node.js 22.12 or later: https://nodejs.org"
    version = ""
    try:
        result = subprocess.run([shutil.which("node") or "node", "--version"], capture_output=True, text=True)
        version = result.stdout.strip().removeprefix("v")
        major, minor = (int(part) for part in version.split(".")[:2])
        if (major, minor) >= (22, 12):
            return ""
    except (OSError, ValueError):
        pass
    return f"needs Node.js 22.12 or later (found: {version or 'none'}): https://nodejs.org"


def npm_command() -> list[str]:
    """npm, as an argument list: on Windows npm is a batch file, so its JavaScript entry point is run with Node where
    it sits beside it."""
    executable = shutil.which("npm")
    if not executable:
        raise FileNotFoundError("needs npm, with Node.js 22.12 or later: https://nodejs.org")
    if WINDOWS:
        script = Path(executable).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if script.is_file():
            return [shutil.which("node") or "node", str(script)]
    return [executable]


def npm(*args: str, cwd: Path):
    sys.stdout.flush()
    result = subprocess.run([*npm_command(), *args], cwd=cwd)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ["npm", *args])


def outdated(target: Path, sources: list[Path]) -> bool:
    """As make decides: the target is missing, or a source is newer."""
    if not target.exists():
        return True
    built = target.stat().st_mtime
    return any(source.stat().st_mtime > built for source in sources if source.exists())


def build(commands):
    """kge portal-build: each npm ci only where its lockfile is newer than the install it made, or there is none; the UI
    rebuilt only where a source of it changed."""
    why = needs()
    commands.need(not why, f"make portal {why}")
    ui, server = Path("portal") / "ui", Path("portal") / "server"
    marker = Path("node_modules") / ".package-lock.json"
    if outdated(ui / marker, [ui / "package-lock.json"]):
        npm("ci", "--no-audit", "--no-fund", cwd=ui)
        (ui / marker).touch()
    sources = [ui / name for name in PORTAL_UI_FILES] + [ui / marker]
    sources += [path for folder in (ui / "src", ui / "public") if folder.is_dir() for path in folder.rglob("*") if path.is_file()]
    if outdated(ui / "build" / "index.html", sources):
        npm("run", "build", cwd=ui)
    if outdated(server / marker, [server / "package-lock.json"]):
        npm("ci", "--no-audit", "--no-fund", "--omit=dev", cwd=server)
        (server / marker).touch()


def api_spec(commands, *, signin: bool = False):
    """make serve's command: the token made where needed, the profile said in one line, then uvicorn."""
    secret = token(commands)
    sys.stdout.flush()
    code = profile.main(["--check"])
    if code:
        raise subprocess.CalledProcessError(code, "profile")
    env = {**os.environ, "PORTAL_SYNC_TOKEN": secret, "DEMO_MODE": commands.get("DEMO_MODE"),
           "SERVE_HASH": commands.get("HASH"), "API_URL": commands.get("API")}
    if signin:
        env["SIGNIN"] = "passwords"
    args = [sys.executable, "-m", "uvicorn", "kge.app:app", "--app-dir", ".", "--host", commands.get("HOST"),
            "--port", commands.get("PORT"), *commands.get("RELOAD").split()]
    return args, Path.cwd(), env


def portal_spec(commands):
    """make portal's command: the server on PORTAL_PORT, calling KGE at KGE_URL, its SQLite database in build/portal/."""
    catalogue = commands.get("PORTAL_CATALOGUE_FILE").strip()
    secret = "" if catalogue else token(commands)
    folder = Path(commands.get("BUILD")) / "portal"
    folder.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PORTAL_SYNC_TOKEN": secret, "PORT": commands.get("PORTAL_PORT"),
           "KGE_URL": commands.get("KGE_URL"), "PORTAL_BACKEND": "sqlite", "PORTAL_UI_PATH": "../ui/build",
           "PORTAL_SQLITE_PATH": os.path.abspath(folder / "portal.db")}
    if catalogue:
        env["PORTAL_CATALOGUE_FILE"] = os.path.abspath(catalogue)
    return [shutil.which("node") or "node", "--experimental-sqlite", "src/index.js"], Path("portal") / "server", env


def group_options() -> dict:
    """A process group of the child's own, so a stop sent to this one reaches it only through stop."""
    if WINDOWS:
        return {"creationflags": NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def signal_group(process, number: int):
    try:
        os.killpg(process.pid, number)
    except (ProcessLookupError, PermissionError):
        pass


def stop(processes, grace: float = GRACE):
    """Stop each server still running: SIGTERM to its group (CTRL_BREAK_EVENT, else terminate, on Windows), then a kill
    to whatever is left after the grace period."""
    live = [process for process in processes if process.poll() is None]
    for process in live:
        if WINDOWS:
            try:
                process.send_signal(CTRL_BREAK)
            except OSError:
                process.terminate()
        else:
            signal_group(process, signal.SIGTERM)
    until = time.monotonic() + grace
    while time.monotonic() < until and any(process.poll() is None for process in live):
        time.sleep(POLL)
    for process in live:
        if process.poll() is None:
            if WINDOWS:
                process.kill()
            else:
                signal_group(process, signal.SIGKILL)
            process.wait()


def supervise(specs, grace: float = GRACE) -> int:
    """Run each (args, cwd, env) until a signal arrives or one ends, stop the rest, and say how it ended."""
    received: list[int] = []
    handlers = {}

    def note(number, frame):
        received.append(number)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            handlers[number] = signal.signal(number, note)
    processes = []
    try:
        sys.stdout.flush()
        for args, cwd, env in specs:
            if received:
                break
            processes.append(subprocess.Popen(args, cwd=cwd, env=env, **group_options()))
        ended = []
        while not received and not ended:
            ended = [process for process in processes if process.poll() is not None]
            if not ended:
                time.sleep(POLL)
        stop(processes, grace)
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    if received:
        return 128 + int(received[0])
    return ended[0].returncode


def finish(code: int, what: str):
    if code:
        raise subprocess.CalledProcessError(code, what)


def serve(commands):
    """kge serve: the API and the page, until Ctrl-C or a stop."""
    finish(supervise([api_spec(commands)]), "serve")


def portal(commands):
    """kge portal: the UI built, then the discovery portal's server."""
    build(commands)
    finish(supervise([portal_spec(commands)]), "portal")


def demo(commands):
    """kge demo: the graph, the pipeline, the passwords and the token, then KGE and the portal side by side (Ctrl-C stops
    both), or KGE alone where the portal cannot run here, saying why."""
    commands.ensure_graph()
    commands.pipeline()
    commands.ensure_passwords()
    token(commands)
    why = needs()
    serving = commands.fresh()
    if why:
        say(f"make demo: KGE alone, the discovery portal skipped: it {why}", err=True)
        finish(supervise([api_spec(serving, signin=True)]), "serve")
        return
    build(commands)
    say(f"make demo: KGE on http://{commands.get('HOST')}:{commands.get('PORT')}, the discovery portal on "
        f"http://127.0.0.1:{commands.get('PORTAL_PORT')}; Ctrl-C stops both")
    finish(supervise([api_spec(serving, signin=True), portal_spec(serving)]), "demo")
