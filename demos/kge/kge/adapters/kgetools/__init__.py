"""KGE's question tools as an agent reaches them, the KgeTools port (kge/ports.py): McpKgeTools, over KGE's own MCP
tools as the signed-in person (mcp.py). The in-process tools, which eval, quality and batch runs use, live in the
engine: nothing here imports it. Callers take one from kge_tools, never from a backend's module."""
from __future__ import annotations

from kge.adapters.kgetools.mcp import AccessRefused, McpKgeTools, SignInRefused, ToolRefused
from kge.ports import KgeTools

__all__ = ["AccessRefused", "KgeTools", "KgeToolsSettingError", "SignInRefused", "ToolRefused", "TRANSPORTS", "kge_tools"]

TRANSPORTS = {"mcp": McpKgeTools}


class KgeToolsSettingError(ValueError):
    """A transport kge_tools does not offer."""


def kge_tools(transport: str, **kw) -> KgeTools:
    """The tools over transport, opened with kw as its adapter takes them (mcp: url, bearer, and app and loop for a
    call in this process)."""
    try:
        adapter = TRANSPORTS[transport]
    except KeyError:
        raise KgeToolsSettingError(f"no KGE tools over {transport!r}: the transports are {', '.join(TRANSPORTS)}") from None
    return adapter(**kw)
