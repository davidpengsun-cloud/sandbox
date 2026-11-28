"""Telemetry views and service callers use the shared factory, never concrete providers."""
import ast
from pathlib import Path


def test_telemetry_callers_do_not_choose_or_import_a_provider():
    demo = Path(__file__).resolve().parents[2]
    callers = list((demo / "kge/services/telemetry").rglob("*.py"))
    views = list((demo / "portal/ui/src/pages/Context").glob("Telemetry*.js"))
    assert callers
    for path in callers:
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else [item.name for item in node.names] if isinstance(node, ast.Import) else []
            assert not any(name.startswith("kge.adapters.telemetry.") for name in modules), path
            if isinstance(node, ast.ImportFrom) and node.module == "kge.adapters.telemetry":
                assert not any(item.name in {"store", "cloud", "aggregate"} for item in node.names), path
        assert not any(token in source for token in ("cloudtrace", "TRACING", "telemetry.store", "telemetry.cloud")), path
    for path in views:
        assert not any(token in path.read_text() for token in ("cloudtrace", "TRACING", "telemetry.store", "telemetry.cloud")), path
