"""The onboarding command line (python -m kge.estate.onboard): each command is the package's function of that name, its
help the package's docstring."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kge.estate import ddl
from kge.adapters import appstate
from kge.adapters.model import ModelSettingError
from kge.pipeline.profile import resolve
from kge.estate.onboard.common import OnboardError


def main(argv=None) -> int:
    package = sys.modules[__package__]
    ap = argparse.ArgumentParser(prog="python -m kge.estate.onboard", description=package.__doc__)
    ap.add_argument("--profile")
    subs = ap.add_subparsers(dest="command", required=True)
    for name in ("tables", "create"):
        p = subs.add_parser(name); p.add_argument("--unit", required=True); source = p.add_mutually_exclusive_group(required=True); source.add_argument("--ddl"); source.add_argument("--semantic", dest="semantic_dir"); p.add_argument("--label")
        p.add_argument("--rows", choices=("csv", "invented", "none")); p.add_argument("--csv", dest="csv_dir")
        if name == "create":
            p.add_argument("--questions"); p.add_argument("--heldout")
        p.add_argument("--as", dest="as_"); p.add_argument("--json", action="store_true")
    p = subs.add_parser("rows"); p.add_argument("--unit", required=True); p.add_argument("--rows", required=True, choices=("csv", "invented"))
    p.add_argument("--csv", dest="csv_dir"); p.add_argument("--as", dest="as_", required=True); p.add_argument("--json", action="store_true")
    p = subs.add_parser("draft"); p.add_argument("--unit", required=True); p.add_argument("--as", dest="as_", required=True); p.add_argument("--on"); p.add_argument("--json", action="store_true")
    for name in ("accept", "certify"):
        p = subs.add_parser(name); p.add_argument("--unit"); p.add_argument("--proposal")
        p.add_argument("--item", default="all"); p.add_argument("--as", dest="as_", required=True)
        p.add_argument("--on"); p.add_argument("--json", action="store_true")
    for name in ("show", "build", "status"):
        p = subs.add_parser(name); p.add_argument("--json", action="store_true")
    p = subs.add_parser("reset"); p.add_argument("--unit", required=True); p.add_argument("--json", action="store_true")
    p.add_argument("--force", action="store_true", help="allow reset to discard pending or unreadable proposals")
    p = subs.add_parser("record-onboard"); p.add_argument("--hash", dest="model_hash", required=True)
    p.add_argument("--passed", required=True, choices=("true", "false")); p.add_argument("--as", dest="as_"); p.add_argument("--json", action="store_true")
    p = subs.add_parser("client"); p.add_argument("--client", required=True); p.add_argument("--label", required=True)
    source = p.add_mutually_exclusive_group(required=True); source.add_argument("--ddl"); source.add_argument("--db")
    row_source = p.add_mutually_exclusive_group(); row_source.add_argument("--csv", dest="csv_dir"); row_source.add_argument("--rows", choices=("invented", "none"))
    p.add_argument("--as", dest="as_", required=True); p.add_argument("--json", action="store_true")
    p = subs.add_parser("reset-client"); p.add_argument("--client", required=True); p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv); data = vars(args); command = data.pop("command").replace("-", "_"); as_json = data.pop("json", False); profile_id = data.pop("profile")
    if "ddl" in data:
        data["ddl_file"] = data.pop("ddl")
    for k in ("questions", "heldout"):
        if data.get(k) is not None:
            data[k] = Path(data[k])
    try:
        if command == "status":
            command = "status_lines"
        result = (getattr(package, command)(**{k: v for k, v in data.items() if v is not None}) if command in {"client", "reset_client"}
                  else getattr(package, command)(resolve(profile_id), **{k: v for k, v in data.items() if v is not None}))
        print(json.dumps({"ok": True, **result}) if as_json else result.get("said", json.dumps(result)))
        return 0
    except (OnboardError, ddl.DdlError, appstate.StateUnavailable, ModelSettingError) as exc:
        status, message = ((exc.status, exc.message) if isinstance(exc, OnboardError) else
                           (503, str(exc)) if isinstance(exc, appstate.StateUnavailable) else (400, str(exc)))
        if as_json:
            print(json.dumps({"ok": False, "status": status, "error": message}))
        else:
            print(f"onboard: {message}", file=sys.stderr)
        return 2

