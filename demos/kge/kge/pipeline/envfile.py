"""Load the demo's .env, if there is one. No dependency; no override.

Variables already set in the environment win, so a one-off
`MODEL_PROVIDER=none python3 -m kge.pipeline.run` still does what it says. Lines
are KEY=VALUE; blank lines, full-line comments and trailing ` # comments` are
ignored, and matching quotes around a value are stripped.
"""
from __future__ import annotations

import os
from pathlib import Path

from kge.pipeline.settings import ROOT

DEMO = ROOT
_loaded: set[Path] = set()


def parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif value.startswith("#"):
            value = ""
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def load_env(path: Path | None = None) -> dict[str, str]:
    path = (path or DEMO / ".env").resolve()
    if path in _loaded or not path.exists():
        return {}
    _loaded.add(path)
    values = parse(path.read_text())
    for k, v in values.items():
        os.environ.setdefault(k, v)
    return values
