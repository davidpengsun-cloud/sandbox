"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): api.identity is kge.services.identity.service, the same module.
Run as a script (python api/identity.py, or python -m api.identity), it runs kge.services.identity.cli, its command line."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.services.identity.cli", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.services.identity.service")
