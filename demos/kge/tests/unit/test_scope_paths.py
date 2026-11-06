"""Product scope code may refuse access but cannot create an abstention."""
import ast

import pytest

from kge.pipeline.settings import ROOT


def scope_abstentions(source, scope_module=False):
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"abstain", "abstention", "scope_abstain"}:
                found.append(node.lineno)
        if scope_module and isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("abstain"):
            found.append(node.lineno)
    return found


def test_scope_check_paths_cannot_abstain():
    targets = [ROOT / "kge/services/ask/engine/scope.py", ROOT / "kge/services/ask/engine/core.py",
               ROOT / "kge/services/ask/engine/combined.py", ROOT / "kge/services/assist/service.py"]
    for path in targets:
        assert not scope_abstentions(path.read_text(), path.name == "scope.py"), f"scope abstention in {path}"


@pytest.mark.parametrize("source,scope_module", [
    ("scope.abstain(['wealth.x'])", False),
    ("scope.abstention(['wealth.x'])", False),
    ("scope.scope_abstain(['wealth.x'])", False),
    ("{'behaviour': 'abstain_with_reason'}", True),
])
def test_scope_path_scan_catches_planted_abstentions(source, scope_module):
    assert scope_abstentions(source, scope_module)
