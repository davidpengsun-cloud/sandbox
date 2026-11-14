"""Two commands side by side, in the foreground, until either ends: make demo's page and its discovery portal.

    python3 scripts/side_by_side.py "<command>" "<command>"

Each command runs in a shell, in a session of its own. Ctrl-C (SIGINT) or SIGTERM sent here, or either command ending,
stops the other: SIGINT to each process group still running, then SIGTERM, then SIGKILL, each after its grace period
(GRACE). The exit status is the signal's (128 plus its number) when one stopped it, else that of the command that
ended first.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

GRACE = ((signal.SIGINT, 20.0), (signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0))
POLL = 0.2


def stop(procs: list[subprocess.Popen]) -> None:
    """Stop every process group still running, gently first."""
    for sig, wait in GRACE:
        live = [p for p in procs if p.poll() is None]
        if not live:
            return
        for p in live:
            try:
                os.killpg(p.pid, sig)
            except ProcessLookupError:
                pass
        until = time.monotonic() + wait
        while time.monotonic() < until and any(p.poll() is None for p in live):
            time.sleep(POLL)


def run(commands: list[str]) -> int:
    """Start each command, wait until a signal arrives or one ends, stop the rest, and say how it ended."""
    received: list[int] = []

    def note(signum, _frame):
        received.append(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, note)
    procs = [subprocess.Popen(command, shell=True, start_new_session=True) for command in commands]
    ended: list[subprocess.Popen] = []
    while not received and not ended:
        ended = [p for p in procs if p.poll() is not None]
        if not ended:
            time.sleep(POLL)
    stop(procs)
    return 128 + received[0] if received else ended[0].returncode


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('usage: python3 scripts/side_by_side.py "<command>" "<command>"', file=sys.stderr)
        return 2
    return run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
