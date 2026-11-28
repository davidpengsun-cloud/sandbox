"""The graph read: the graph's nodes and links a person may read, paged, each link with its status and the reason it
exists, and no figures (docs/use-cases/kge/platform/cerebro.md §5)."""


def read(*args, **kwargs):
    from kge.services.graph.service import read as _read
    return _read(*args, **kwargs)


def node_id(kind: str, identifier: str) -> str:
    """A node's id in the graph read: <kind>:<identifier>, the kind in lower case."""
    from kge.services.graph.service import node_id as _node_id
    return _node_id(kind, identifier)


def term_paths(*args, **kwargs):
    """A term's explained paths, as the person may read them: map_term's per-term view (#628)."""
    from kge.services.graph.service import term_paths as _term_paths
    return _term_paths(*args, **kwargs)


__all__ = ["node_id", "read", "term_paths"]
