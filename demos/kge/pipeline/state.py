"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): pipeline.state is kge.pipeline.state, the same module.
Run as a script (python pipeline/state.py, or python -m pipeline.state), it runs kge.pipeline.state."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.pipeline.state", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.pipeline.state")
