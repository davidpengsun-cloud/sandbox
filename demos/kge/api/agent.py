"""Compatibility shim for the frozen snapshot tests (see this folder's __init__.py): api.agent is kge.services.ask.engine.agent, the same module.
Run as a script (python api/agent.py, or python -m api.agent), it runs kge.services.ask.engine.agent."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("kge.services.ask.engine.agent", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("kge.services.ask.engine.agent")
