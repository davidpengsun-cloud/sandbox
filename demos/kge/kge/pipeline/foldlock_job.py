"""Inspect the configured client's fold mutex from the image job, without exposing its token."""
from __future__ import annotations

from contextlib import redirect_stdout
import json
import sys

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog.foldlock import FoldBusy, fold_mutex
from kge.pipeline.objstore import APP, Refused, pull_config
from kge.pipeline.profile import ProfileError, resolve
from kge.pipeline.settings import settings
from kge.ports import StoreError


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv) != ["ACTION=inspect"]:
        print("fold-lock: only ACTION=inspect is supported", file=sys.stderr)
        return 2
    try:
        if (settings().STORE_URL or "").strip():
            with redirect_stdout(sys.stderr):
                prof = pull_config(open_store(settings().STORE_URL), APP)
        else:
            prof = resolve()
        mutex = fold_mutex(prof.run_dir(), prof.client)
        print(json.dumps(mutex.inspect_observed(), sort_keys=True))
        return 0
    except FoldBusy as exc:
        print(f"fold-lock: {exc}", file=sys.stderr)
    except (Refused, ProfileError, StoreError, OSError, ValueError):
        print("fold-lock: the configured profile or mutex store could not be read or updated", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
