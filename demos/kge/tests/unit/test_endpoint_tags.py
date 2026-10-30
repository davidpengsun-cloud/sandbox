"""Every registered API operation and MCP tool declares its audience."""

import anyio
import pytest
import re
from fastapi import FastAPI
from fastapi.routing import APIRoute

from kge import app as api
from kge.services.ask.mcp import MCPEndpoint


TAGS = {
    'external: knowledge', 'external: query', 'external: identity',
    'internal: onboarding', 'internal: governance', 'internal: quality', 'internal: platform',
}


def one_tag(tags):
    assert isinstance(tags, list) and len(tags) == 1, f'Expected exactly one tag: {tags}'
    assert tags[0] in TAGS, f'Unknown tag: {tags}'
    return tags[0]


def api_routes(router):
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router)


def check_routes(app):
    routes = list(api_routes(app))
    assert routes
    for route in routes:
        tag = one_tag(route.tags)
        if tag.startswith('internal:'):
            assert route.description.startswith('Internal:'), route.path
    schema = app.openapi()
    for operations in schema['paths'].values():
        for operation in operations.values():
            one_tag(operation.get('tags'))
    return {(method, route.path_format): route.tags for route in routes for method in route.methods}


def test_every_route_and_tool_has_one_matching_audience_tag():
    routes = check_routes(api.app)
    servers = [route.endpoint.server for route in api.app.routes if isinstance(getattr(route, "endpoint", None), MCPEndpoint)]
    assert servers
    for server in servers:
        tools = anyio.run(server.list_tools)
        assert tools
        for tool in tools:
            meta = tool.meta or {}
            one_tag(meta.get('tags'))
            method, path = meta['route'].split(' ', 1)
            assert meta['tags'] == routes[method, path], tool.name


@pytest.mark.parametrize('tags', [None, [], ['external: knowledge', 'external: query'], ['unknown']])
def test_rejects_invalid_tool_tags(tags):
    with pytest.raises(AssertionError):
        one_tag(tags)


@pytest.mark.parametrize('path', ['/search', '/future-capability'])
@pytest.mark.parametrize('tags', [[], ['external: knowledge', 'external: query'], ['unknown']])
def test_rejects_new_routes_without_exactly_one_known_tag(path, tags):
    app = FastAPI()
    app.add_api_route(path, lambda: {}, tags=tags)
    with pytest.raises(AssertionError):
        check_routes(app)


@pytest.mark.parametrize('path, tag', [
    ('/context', 'external: knowledge'), ('/retrieve', 'external: knowledge'),
    ('/map/{term}', 'external: knowledge'), ('/catalogue', 'external: knowledge'),
    ('/catalogue/release', 'internal: platform'),
    ('/ask', 'external: query'), ('/data/preview', 'external: query'),
    ('/data/export', 'external: query'), ('/data/query', 'external: query'),
    ('/session', 'external: identity'), ('/whoami', 'external: identity'),
    ('/readable-units', 'external: identity'),
    ('/onboard', 'internal: onboarding'), ('/onboard/tables', 'internal: onboarding'),
    ('/onboard/source', 'internal: onboarding'), ('/onboard/rows', 'internal: onboarding'),
    ('/onboard/draft', 'internal: onboarding'), ('/onboard/run', 'internal: onboarding'),
    ('/onboard/margin', 'internal: onboarding'),
    ('/onboard/accept', 'internal: governance'), ('/onboard/certify', 'internal: governance'),
    ('/steward/queue', 'internal: governance'), ('/steward/decide', 'internal: governance'),
    ('/eval', 'internal: quality'), ('/golden', 'internal: quality'),
    ('/ab', 'internal: quality'), ('/ab/{run_id}/report.html', 'internal: quality'),
    ('/answer', 'internal: platform'), ('/agent', 'internal: platform'),
    ('/catalogue/portal', 'internal: platform'), ('/', 'internal: platform'),
    ('/onboarding-guide', 'internal: platform'), ('/healthz', 'internal: platform'),
    ('/brand/logo', 'internal: platform'), ('/brand/logo.svg', 'internal: platform'),
    ('/mode', 'internal: platform'), ('/presets', 'internal: platform'),
    ('/profile', 'internal: platform'), ('/summary', 'internal: platform'),
    ('/versions', 'internal: platform'), ('/sql/check', 'internal: platform'),
    ('/principals', 'internal: platform'), ('/session/demo', 'internal: platform'),
])
def test_issue_audience_assignments(path, tag):
    for operation in api.app.openapi()['paths'][path].values():
        assert operation['tags'] == [tag]


def test_readme_lists_each_external_tag_and_endpoint():
    from kge.pipeline.settings import ROOT

    public = (ROOT / 'README.md').read_text().split('## The public API\n', 1)[1].split('\n## ', 1)[0]
    expected = {tag: set() for tag in TAGS if tag.startswith('external:')}
    for path, operations in api.app.openapi()['paths'].items():
        for method, operation in operations.items():
            tag = one_tag(operation.get('tags'))
            if tag.startswith('external:'):
                expected[tag].add(f'{method.upper()} {path}')
    for tag, endpoints in expected.items():
        row = next(line for line in public.splitlines() if line.startswith(f'| `{tag}` |'))
        assert set(re.findall(r'`([A-Z]+ /[^`]*)`', row)) == endpoints
