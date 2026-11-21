"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): api.app is kge.app, the same module.
Run as a script (python api/app.py, or python -m api.app), it runs kge.app."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.app", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.app")
