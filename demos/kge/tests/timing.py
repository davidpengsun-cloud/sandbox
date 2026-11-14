"""Report per-test budgets from pytest's JUnit report."""
import json
import os
import subprocess
import time
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


EXCEPTIONS = Path(__file__).with_name("timing_exceptions.json")


def report(lane, report_path):
    budget = {"unit": 0.1, "integration": 10.0}[lane]
    exceptions = json.loads(EXCEPTIONS.read_text()) if lane == "integration" else {}
    for case in ET.parse(report_path).iter("testcase"):
        seconds = float(case.get("time", "0"))
        if seconds > budget:
            name = f'{case.get("classname")}::{case.get("name")}'
            reason = exceptions.get(name)
            print(f'{lane} test over {budget:g}s: {name} ({seconds:.3f}s)' + (f' — {reason}' if reason else ""))


def leg(name, command):
    cap = 900 if name.startswith("integration-") else 600 if name.startswith(("unit-", "e2e-")) else None
    report_path = os.environ.get("KGE_TIMING_REPORT")
    if report_path:
        Path(report_path).unlink(missing_ok=True)
    started = time.monotonic()
    result = subprocess.run(command, check=False).returncode
    elapsed = time.monotonic() - started
    print(f"{name} leg took {elapsed:.1f}s" + (f" (cap {cap}s)" if cap else ""), flush=True)
    if cap is not None and elapsed > cap:
        cure = "Refresh the durations (make test-durations) or add a leg."
        if name.startswith("e2e-"):
            cure = "Deal the walkthrough's steps afresh (PARTS in tests/e2e/demo_script.cjs), or into one more part."
        slowest = ""
        if report_path and Path(report_path).is_file():
            cases = sorted(ET.parse(report_path).iter("testcase"), key=lambda case: float(case.get("time", "0")), reverse=True)
            names = [f'{case.get("classname")}::{case.get("name")} ({float(case.get("time", "0")):.3f}s)' for case in cases[:10]]
            if names:
                slowest = " Its slowest tests: " + ", ".join(names)
        print(f"::warning title=KGE full check leg over its cap::{name} exceeded its {cap}s cap. {cure}{slowest}", flush=True)
    return result


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "tests":
        report(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 4 and sys.argv[1] == "leg":
        sys.exit(leg(sys.argv[2], sys.argv[3:]))
    else:
        sys.exit("usage: timing.py tests unit|integration <junit.xml> | leg <name> <command>...")
