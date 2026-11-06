"""Records of tables landed in each release, owned by the released graph."""
import json
import os
from pathlib import Path


def write(folder: Path, client: str, release: str, tables: list[str]) -> None:
    target = folder / "landed-tables" / f"{release}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".json.{os.getpid()}")
    temporary.write_text(json.dumps({"client": client, "release": release, "tables": tables}) + "\n")
    temporary.replace(target)


def read(folder: Path, release: str) -> object:
    return json.loads((folder / "landed-tables" / f"{release}.json").read_text())
