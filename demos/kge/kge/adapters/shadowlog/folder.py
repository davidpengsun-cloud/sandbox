"""The demo's shadow log: JSON lines, one file a release, under the run state's shadow/ folder, mode 600, each
observation appended by one write and none rewritten."""
from __future__ import annotations

import json
import os
from pathlib import Path

from kge.adapters.shadowlog.record import line_of, release_segment
from kge.ports import ShadowObservation


class FolderShadowLog:
    """<folder>/<release>.jsonl, appended to and read a release at a time. A line ends at a newline alone: a declared
    member may hold U+2028 or U+0085, which the JSON keeps unescaped."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def append(self, observation: ShadowObservation) -> None:
        line = line_of(observation)
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{release_segment(observation.release)}.jsonl"
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(handle, line)
        finally:
            os.close(handle)

    def releases(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(path.stem for path in self.folder.glob("*.jsonl") if path.is_file())

    def read(self, release: str) -> list[dict]:
        path = self.folder / f"{release_segment(release)}.jsonl"
        if not path.is_file():
            return []
        lines = path.read_bytes().split(b"\n")
        return [json.loads(line.decode("utf-8")) for line in lines if line.strip()]
