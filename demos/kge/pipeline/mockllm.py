"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): pipeline.mockllm is kge.adapters.model.mock, the same module.
Run as a script (python pipeline/mockllm.py, or python -m pipeline.mockllm), it runs kge.adapters.model.mock."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.adapters.model.mock", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.adapters.model.mock")
