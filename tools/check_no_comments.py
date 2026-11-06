#!/usr/bin/env python3
"""No comments in the demos' Python.

The owner's rule from issue #83: the Python under ``demos/*/`` carries no ``#``
comments. What a comment would explain belongs in a name, a docstring, or the
demo's CONTRIBUTING.md, where it is read with the code it describes and does not
drift from it unseen.

Tool directives are not comments in that sense and stay, each exactly as written:

* lint and type directives: ``# noqa`` with or without its codes,
  ``# type: ignore``, ``# fmt: off``, ``# fmt: on`` and ``# fmt: skip``;
* coverage pragmas: ``# pragma: no cover`` and its kin;
* the secret scanner's ``# gitleaks:allow``;
* a shebang on the first line, and a ``# -*- coding`` line on the first two;
* in the comment block that opens a file, before any code: an SPDX tag line
  (``# SPDX-License-Identifier: MIT``) or a copyright notice with its year
  (``# Copyright 2026 …``, ``# (c) 2026 …``); a ``# (c) note`` is a comment.

A directive is kept bare: words after one, such as the reason for a ``noqa``,
are a comment and are reported, and the reason goes in the docstring.

Two files are exempt by name: ``demos/kge/tests/test_context.py`` and
``demos/kge/tests/test_agent_loop.py``, frozen-snapshot tests, edited only by
the owner.

Comments are found with ``tokenize``, so a ``#`` inside a string is never one.
Virtual environments, build output, ``node_modules`` and dot-directories are not
walked.

Findings print as ``path:line: message`` so GitHub annotates the offending
line. Exit 0 when clean, 1 otherwise. Standard library only, no network.

Usage::

    python3 tools/check_no_comments.py           # every demo's Python
    python3 tools/check_no_comments.py PATH...   # named files or directories
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

REPO = Path(__file__).resolve().parents[1]
DEMOS = REPO / "demos"

EXEMPT = {
    "demos/kge/tests/test_context.py": "frozen-snapshot tests, edited only by the owner",
    "demos/kge/tests/test_agent_loop.py": "frozen-snapshot tests, edited only by the owner",
}

SKIP_DIRS = {"build", "node_modules", "__pycache__", "venv", "site-packages"}

DIRECTIVE = re.compile(
    r"#\s*(?:"
    r"noqa(?::\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?"
    r"|type:\s*ignore(?:\[[\w\-, ]+\])?"
    r"|pragma:\s*no\s+(?:cover|branch)"
    r"|gitleaks:allow"
    r"|fmt:\s*(?:off|on|skip)"
    r")"
)
HEADER = re.compile(
    r"#\s*(?:"
    r"SPDX-(?:License-Identifier|FileCopyrightText|FileContributor):\s*\S"
    r"|Copyright\s+(?:(?:\(c\)|©)\s*)?\d{4}\b"
    r"|(?:\(c\)|©)\s*\d{4}\b"
    r")"
)
CODING = re.compile(r"#.*?coding[:=]")


def bare_directive(text: str) -> str | None:
    """The tool directive a comment carries, without any prose after it, or None.

    A directive with words after it is a comment in substance, so the check asks
    for the bare directive alone.
    """
    match = DIRECTIVE.match(text)
    return match.group(0) if match else None


def is_directive(text: str, line: int, in_header: bool = False) -> bool:
    """Whether a comment token is a tool directive the rule keeps, exactly as written.

    A shebang counts on line 1, a coding line on lines 1 and 2, and an SPDX tag
    line or a dated copyright notice only in the comment block that opens the file.
    """
    if line == 1 and text.startswith("#!"):
        return True
    if line <= 2 and CODING.match(text):
        return True
    if in_header and HEADER.match(text):
        return True
    return bare_directive(text) == text.rstrip()


def python_files(roots: list[Path]):
    """The .py files under each root, skipping environments, build output and dot-directories."""
    for root in roots:
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(root).parts[:-1]
            if any(p.startswith(".") or p in SKIP_DIRS for p in parts):
                continue
            yield path


def comments(path: Path):
    """Every comment token in a file that is not a directive, as (line, text)."""
    source = path.read_bytes()
    in_header = True
    for tok in tokenize.tokenize(io.BytesIO(source).readline):
        if tok.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING):
            in_header = False
        if tok.type == tokenize.COMMENT and not is_directive(tok.string, tok.start[0], in_header):
            yield tok.start[0], tok.string


def relative(path: Path) -> str:
    """A path as the findings print it: relative to the repository where it can be."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", type=Path, help="files or directories (default: demos/)")
    args = parser.parse_args()
    roots = args.paths or [DEMOS]

    found = checked = exempt = 0
    for path in python_files(roots):
        if relative(path) in EXEMPT:
            exempt += 1
            continue
        checked += 1
        try:
            for line, text in comments(path):
                print(f"{relative(path)}:{line}: comment: {text[:80]}")
                found += 1
        except (tokenize.TokenError, SyntaxError) as exc:
            print(f"{relative(path)}:1: could not tokenize: {exc}")
            found += 1

    print(f"{checked} Python files checked, {exempt} exempt, {found} comments", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
