"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): pipeline.envfile is kge.pipeline.envfile, the same module.
Run as a script (python pipeline/envfile.py, or python -m pipeline.envfile), it runs kge.pipeline.envfile."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.pipeline.envfile", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.pipeline.envfile")
