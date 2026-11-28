"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): api.runtrace is kge.services.ask.engine.runtrace, the same module.
Run as a script (python api/runtrace.py, or python -m api.runtrace), it runs kge.services.ask.engine.runtrace."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.services.ask.engine.runtrace", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.services.ask.engine.runtrace")
