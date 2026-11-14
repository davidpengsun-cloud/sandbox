"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): api.context is kge.services.ask.engine.context, the same module.
Run as a script (python api/context.py, or python -m api.context), it runs kge.services.ask.engine.context."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.services.ask.engine.context", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.services.ask.engine.context")
