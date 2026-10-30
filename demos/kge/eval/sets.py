"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): eval.sets is kge.eval.sets, the same module.
Run as a script (python eval/sets.py, or python -m eval.sets), it runs kge.eval.sets."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.eval.sets", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.eval.sets")
