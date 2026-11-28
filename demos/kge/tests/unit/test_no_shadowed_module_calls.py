"""No function in kge/ binds a name of a module-level function that the same function calls.

PR #489: coverage() bound `operations` as a comprehension variable and later called the module function operations().
Under PEP 709 (inlined comprehensions) some CPython 3.12 builds, the image's among them, make the comprehension
variable a local of the whole function, so the call raised UnboundLocalError and /ask answered 500. Newer patch
releases compile it as a global load, so a runtime test on the developer's interpreter can pass while the image fails.
The check is therefore static: symtable for names the interpreter makes local, and the syntax tree for comprehension
targets, which some 3.12 builds inline into the enclosing function.
"""
import ast
import symtable
from pathlib import Path

import pytest

KGE = Path(__file__).resolve().parents[2] / "kge"
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def module_functions(tree):
    """The names the module binds to functions: its own top-level defs and the names it imports."""
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def own_scope(function):
    """Every node of the function's own scope, comprehensions included (inlined since 3.12), nested scopes excluded."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, NESTED):
            stack.extend(ast.iter_child_nodes(node))


def comprehension_targets(function):
    return {name.id for node in own_scope(function) if isinstance(node, ast.comprehension)
            for name in ast.walk(node.target) if isinstance(name, ast.Name)}


def statement_bound(function):
    """Names the function binds on purpose by a statement of its own: a local import or a nested def or class."""
    names = set()
    for node in own_scope(function):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def called(function):
    return {node.func.id for node in own_scope(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def function_tables(table):
    for child in table.get_children():
        if child.get_type() == "function":
            yield child
        yield from function_tables(child)


def shadowed_calls(source, filename):
    """(function, line, name) for every function that binds and calls a module-level function's name."""
    tree = ast.parse(source, filename)
    functions = module_functions(tree)
    defs = {(node.name, node.lineno): node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found = []
    for table in function_tables(symtable.symtable(source, filename, "exec")):
        node = defs.get((table.get_name(), table.get_lineno()))
        if node is None:
            continue
        local = {s.get_name() for s in table.get_symbols() if s.is_local()} - statement_bound(node)
        for name in sorted((local | comprehension_targets(node)) & functions & called(node)):
            found.append((table.get_name(), table.get_lineno(), name))
    return found


@pytest.mark.parametrize("path", sorted(KGE.rglob("*.py")), ids=lambda p: str(p.relative_to(KGE)))
def test_no_function_shadows_a_module_function_it_calls(path):
    assert shadowed_calls(path.read_text(), str(path)) == []


def test_the_check_catches_the_coverage_shadow():
    source = (
        "def operations(node):\n    return {node}\n\n"
        "def coverage(requested, node):\n"
        "    owners = {c for c, operations in requested.items() if 'sum' in operations}\n"
        "    return owners, operations(node)\n"
    )
    assert shadowed_calls(source, "<coverage>") == [("coverage", 4, "operations")]


def test_the_check_catches_the_planpublic_shadow():
    source = (
        "from question import pair\n\n"
        "def selected_answer(pairs, a, b):\n"
        "    named = [pair for pair in pairs if pair]\n"
        "    return named, pair(a, b)\n"
    )
    assert shadowed_calls(source, "<planpublic>") == [("selected_answer", 3, "pair")]
