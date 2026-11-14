"""Product scope code may refuse access but cannot create an abstention."""
import ast

import pytest

from kge.pipeline.settings import ROOT


def scope_abstentions(source, scope_module=False):
    tree = ast.parse(source)
    found = []

    def scope_name(node):
        return any(isinstance(child, ast.Name) and (child.id == "scope" or child.id.endswith("_scope"))
                   for child in ast.walk(node))

    def unscoped_condition(test):
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            return any(unscoped_condition(value) for value in test.values)
        return (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)
                and scope_name(test.left) and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None)

    def abstention_dict(node):
        return isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "behaviour"
            and isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.startswith("abstain")
            for key, value in zip(node.keys, node.values))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"abstain", "abstention", "scope_abstain"}:
                found.append(node.lineno)
        if scope_module and isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("abstain"):
            found.append(node.lineno)
        if isinstance(node, (ast.If, ast.IfExp)) and scope_name(node.test):
            branches = [node.orelse] if unscoped_condition(node.test) else [node.body, node.orelse]
            for branch in branches:
                statements = branch if isinstance(branch, list) else [branch]
                for statement in statements:
                    found.extend(child.lineno for child in ast.walk(statement) if abstention_dict(child))
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
    ("if scope.missing(tables):\n    return {'behaviour': 'abstain'}", False),
    ("if scope.products is not None:\n    return {**out, 'behaviour': 'abstain_with_reason'}", False),
    ("return {'behaviour': 'figure'} if page_scope.contains(table) else {'behaviour': 'abstain'}", False),
    ("if scope.products is None:\n    return {'behaviour': 'figure'}\nelse:\n    return {'behaviour': 'abstain'}", False),
])
def test_scope_path_scan_catches_planted_abstentions(source, scope_module):
    assert scope_abstentions(source, scope_module)


@pytest.mark.parametrize("source", [
    "if not req.term:\n    return {'behaviour': 'abstain'}",
    "if scope.denied_units(principal, []):\n    return {'behaviour': 'refuse'}",
    "return {'behaviour': 'abstain', 'reason': 'No certified implementation'}",
    "if req.term is None and scope.products is None:\n    return {'behaviour': 'abstain'}",
])
def test_scope_path_scan_allows_governance_abstentions_and_access_refusals(source):
    assert not scope_abstentions(source)
