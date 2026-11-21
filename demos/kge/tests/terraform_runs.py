"""One way for tests to run the real Terraform binary.

Terraform starts each provider as a plugin process and waits for its handshake. Two Terraform runs starting the Google
providers at once on a loaded machine (other pytest workers, or another worktree's tests) can miss that handshake, and
the run fails before Terraform has checked anything. So every run holds one lock shared by all processes on the
machine, and a run that fails on the plugin handshake alone is tried once more. Any other failure is returned as it is.
"""

from __future__ import annotations

import fcntl
import subprocess
import tempfile
from pathlib import Path

LOCK = Path(tempfile.gettempdir()) / "kge-tests-terraform.lock"
HANDSHAKE = ("Failed to negotiate", "Unrecognized remote plugin message", "plugin exited before we could connect")


def handshake_failed(result: subprocess.CompletedProcess) -> bool:
    return result.returncode != 0 and any(sign in result.stdout + result.stderr for sign in HANDSHAKE)


def run(argv: list[str], *, cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    with LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            if handshake_failed(result):
                result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def terraform(*args: str, cwd: Path, env: dict, timeout: int = 600) -> subprocess.CompletedProcess:
    return run(["terraform", *args, "-no-color"], cwd=cwd, env=env, timeout=timeout)
