"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): pipeline.llm is kge.adapters.model.providers, the same module.
Run as a script (python pipeline/llm.py, or python -m pipeline.llm), it runs kge.adapters.model.providers."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.adapters.model.providers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.adapters.model.providers")
