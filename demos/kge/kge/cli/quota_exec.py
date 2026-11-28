"""Run an explicitly launched CLI module with a verified model caller when quotas are enabled."""
from __future__ import annotations

import runpy
import sys

from kge.adapters.model import quota_caller
from kge.pipeline.profile import resolve
from kge.pipeline.quota_job import caller_for_job, service_caller
from kge.pipeline.settings import settings


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("quota worker needs a module")
    module, args = sys.argv[1], sys.argv[2:]
    prof = resolve()
    marker = settings().KGE_QUOTA_JOB_CAP
    if marker is not None:
        caller_for_job(prof, marker)
        def caller():
            return caller_for_job(prof, marker)
    else:
        caller = service_caller(prof)
    sys.argv = [module, *args]
    with quota_caller(caller):
        runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
