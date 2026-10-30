"""The image build's shared-library check (Dockerfile): load every shared object the Python environment installed.

    /app/.venv/bin/python /app/kge/cli/check_libraries.py

It opens each file under the running interpreter's site-packages whose name is a shared object (`*.so` or `*.so.<n>`)
with the system's dynamic loader (ctypes), as the first use of each package would. A pinned wheel may need a system
library its base does not ship (libz.so.1 for numpy's bundled libgfortran, libssl.so.3 for ladybug), and `import` alone
misses some: ladybug opens its extension only at first use. A file that cannot be opened because a library it needs is
missing is reported by the library's name and the distribution whose RECORD lists the file (its dist-info), and the check exits 1. A
file that opens with another loader error (a symbol another extension provides once loaded) is no missing library and is
not counted. It exits 0 when every library is present.

The files are opened in a child process, one after another, each named before it is opened. A file whose own start-up
code ends that process (an extension opened outside the interpreter that expects it) has found every library it needs,
since the loader resolves them before it runs any code: it is reported as opened, and the next child goes on from the
file after it.
"""
from __future__ import annotations

import csv
import ctypes
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

SHARED = re.compile(r".+\.so(?:\.\d+)*$")
MISSING = re.compile(r"(?P<library>[^\s:]+): cannot open shared object file")
CHILD = "--open"


def site_packages() -> list[Path]:
    """The running interpreter's installation folders for pure and compiled distributions, each once."""
    found = []
    for key in ("platlib", "purelib"):
        path = Path(sysconfig.get_paths()[key]).resolve()
        if path.is_dir() and path not in found:
            found.append(path)
    return found


def owners(roots: list[Path]) -> dict[Path, str]:
    """Each installed file, resolved, mapped to the name of the distribution whose RECORD lists it. The dist-info
    folders are read directly, not through importlib.metadata, which imports zipfile and so binascii, which a base's
    Python may link against the very libz.so.1 that is missing."""
    out: dict[Path, str] = {}
    for root in roots:
        for info in sorted(root.glob("*.dist-info")):
            name = info.name.split("-", 1)[0]
            try:
                for line in (info / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("Name:"):
                        name = line.partition(":")[2].strip()
                        break
                    if not line:
                        break
                rows = list(csv.reader((info / "RECORD").read_text(encoding="utf-8").splitlines()))
            except OSError:
                continue
            for row in rows:
                if row and row[0]:
                    out[(root / row[0]).resolve()] = name
    return out


def shared_objects(roots: list[Path]) -> list[Path]:
    """Every shared object under the given folders, sorted, so a run reports in the same order each time."""
    return sorted({path.resolve() for root in roots for path in root.rglob("*") if SHARED.match(path.name) and path.is_file()})


def open_each(paths: list[str]) -> int:
    """The child's work: name each file, open it, and name the library it misses, if any, one line each."""
    for path in paths:
        print(f"open\t{path}", flush=True)
        try:
            ctypes.CDLL(path)
        except OSError as error:
            match = MISSING.search(str(error))
            if match:
                print(f"missing\t{match.group('library')}\t{path}", flush=True)
    return 0


def run_children(files: list[Path]) -> tuple[dict[Path, str], list[Path]]:
    """Open the files in child processes, starting a new one after a file that ends one. Returns each file that misses a
    library, with the library, and the files that ended a child."""
    missing: dict[Path, str] = {}
    ended: list[Path] = []
    pending = [str(path) for path in files]
    while pending:
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), CHILD, *pending], capture_output=True,
                               text=True)
        last = None
        done = 0
        for line in child.stdout.splitlines():
            kind, _, rest = line.partition("\t")
            if kind == "open":
                last, done = rest, done + 1
            elif kind == "missing":
                library, _, path = rest.partition("\t")
                missing[Path(path)] = library
        if child.returncode == 0:
            break
        if last is None:
            raise RuntimeError(f"the check's child process failed before opening a file: {child.stderr.strip()}")
        ended.append(Path(last))
        pending = pending[done:]
    return missing, ended


def missing_libraries(roots: list[Path], files: list[Path]) -> tuple[dict[str, dict[str, list[Path]]], list[Path]]:
    """For each library some file could not find, the distributions whose files need it and those files; and the files
    whose own start-up code ended a child."""
    held = owners(roots)
    found, ended = run_children(files)
    out: dict[str, dict[str, list[Path]]] = {}
    for path, library in sorted(found.items()):
        out.setdefault(library, {}).setdefault(held.get(path, "no distribution"), []).append(path)
    return out, ended


def main() -> int:
    """Check the environment, print what is missing and by whom, and return the exit code."""
    if sys.argv[1:2] == [CHILD]:
        return open_each(sys.argv[2:])
    roots = site_packages()
    files = shared_objects(roots)
    missing, ended = missing_libraries(roots, files)
    for path in ended:
        print(f"shared libraries: {path} found every library, then ended the process that opened it outside an "
              "interpreter that imports it")
    if not missing:
        print(f"shared libraries: all {len(files)} shared objects under {', '.join(map(str, roots))} found every library")
        return 0
    for library, needed in sorted(missing.items()):
        for dist, paths in sorted(needed.items()):
            print(f"missing system library {library}, needed by {dist} ({len(paths)} file(s), first {paths[0]})",
                  file=sys.stderr)
    print("the base image lacks a system library a pinned wheel needs: use a base that has it, or copy it from an image "
          "of the same OS release (the install guide, INSTALL.md)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
