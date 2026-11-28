"""The repository's no-comments check, tools/check_no_comments.py: what it keeps as a directive and what it reports.

A licence header is kept only where it is one: an SPDX tag line or a dated copyright notice in the comment block that
opens the file, before any code. Prose dressed as a header, such as a (c) note, is a comment wherever it stands.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CHECK = REPO / "tools" / "check_no_comments.py"


def run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), *map(str, paths)], cwd=REPO, capture_output=True, text=True,
                          timeout=120)


@pytest.mark.parametrize("header, kept", [
    ("# SPDX-License-Identifier: MIT", True),
    ("# SPDX-FileCopyrightText: 2026 Example Ltd", True),
    ("# Copyright 2026 Example Ltd", True),
    ("# Copyright (c) 2026 Example Ltd", True),
    ("# (c) 2026 Example Ltd", True),
    ("# © 2026 Example Ltd", True),
    ("# (c) note", False),
    ("# (C) see below", False),
    ("# © the team", False),
    ("# Copyright notice follows", False),
    ("# copyright 2026", False),
    ("# Licensed to explain the loop", False),
    ("# SPDX-whatever", False),
    ("# SPDX-License-Identifier:", False),
])
def test_a_header_line_opening_the_file_is_kept_only_when_it_is_a_genuine_licence_header(tmp_path, header, kept):
    path = tmp_path / "mod.py"
    path.write_text(f"#!/usr/bin/env python3\n{header}\n\nVALUE = 1\n")
    done = run(path)
    assert (done.returncode == 0) is kept, done.stdout
    assert (f"mod.py:2: comment: {header}" in done.stdout) is not kept, done.stdout


def test_a_licence_line_after_code_is_a_comment(tmp_path):
    path = tmp_path / "mod.py"
    path.write_text('"""Doc."""\n# SPDX-License-Identifier: MIT\nVALUE = 1\n')
    done = run(path)
    assert done.returncode == 1 and "mod.py:2: comment: # SPDX-License-Identifier: MIT" in done.stdout, done.stdout


def test_the_demos_python_has_no_comments():
    done = run()
    assert done.returncode == 0, done.stdout
    assert " 0 comments" in done.stderr, done.stderr
