"""Every mounted HTTP route and MCP server stays discoverable in both API guides."""

import re

import pytest
from fastapi import FastAPI
from starlette.routing import Mount

from kge import app as api
from kge.pipeline.settings import ROOT


def mounted_paths(router, prefix=""):
    paths = set()
    for route in router.routes:
        if hasattr(route, "original_router"):
            paths.update(mounted_paths(route.original_router, prefix))
            continue
        path = route.path if isinstance(route, Mount) else getattr(route, "path_format", getattr(route, "path", None))
        if path is None:
            continue
        paths.add(prefix + path)
        if hasattr(route, "routes"):
            paths.update(mounted_paths(route, prefix + path))
    return paths


def documented_paths(text):
    return set(re.findall(r"`(?:[A-Z]+ )?(/[^`\s]*)`", text))


def require_documented(router, text):
    missing = mounted_paths(router) - documented_paths(text)
    assert not missing, f"Mounted routes missing from the API guide: {sorted(missing)}"


@pytest.mark.parametrize("doc", ["readme", "skill"])
def test_every_mounted_route_and_mcp_server_is_documented(doc):
    if doc == "readme":
        text = (ROOT / "README.md").read_text().split("## The public API\n", 1)[1].split("\n## ", 1)[0]
    else:
        text = (ROOT.parents[1] / ".claude/skills/kge-code/SKILL.md").read_text()
    require_documented(api.app, text)


@pytest.mark.parametrize("path", ["/future-route", "/mcp/future-server"])
def test_new_mount_is_not_covered_by_a_parent_path(path):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    if path.startswith("/mcp/"):
        app.mount(path, FastAPI(openapi_url=None, docs_url=None, redoc_url=None))
    else:
        app.add_api_route(path, lambda: {})
    with pytest.raises(AssertionError, match=re.escape(path)):
        require_documented(app, "`/` and `/mcp`")
    require_documented(app, f"`{path}`")
