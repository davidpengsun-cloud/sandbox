"""Knowledge discovery over the released graph, through HTTP and MCP."""


def Search(*args, **kwargs):
    from kge.services.search.models import Search as _Search
    return _Search(*args, **kwargs)


def search(*args, **kwargs):
    from kge.services.search.service import search as _search
    return _search(*args, **kwargs)


__all__ = ["Search", "search"]
