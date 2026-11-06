import subprocess
import sys

from tests.scratch import DEMO


def test_prose_walk_skips_nested_npm_dependencies(tmp_path):
    (tmp_path / "README.md").write_text("# Project\n\nProject prose.\n")
    package = tmp_path / "portal" / "ui" / "node_modules" / "dependency"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# First\n\n#### Skipped heading  \n\n")
    result = subprocess.run([sys.executable, str(DEMO.parents[1] / "tools/check_prose.py"), str(tmp_path)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 file(s) checked: 0 failure(s)" in result.stdout
