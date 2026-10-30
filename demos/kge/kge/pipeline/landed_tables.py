"""Record tables landed in a release; serve the record from the released graph's files."""
from pathlib import Path

from kge.adapters import landed_tables as store


PREDATES = "this release predates the landed-tables record; release again"


def write_landed_tables(folder: Path, release) -> None:
    """The load job records exactly the tables in this release's landed structured catalogues."""
    tables = sorted({f"{unit}.{table['name']}" for unit in release.sources()
                     for table in release.structured(unit)["tables"]})
    store.write(folder, release.client, release.id, tables)


def landed_tables(profile, release: dict | None) -> frozenset[str] | None:
    """Read the served graph's landed tables; None means it carries no usable record."""
    if not release or release.get("client") != profile.client or not isinstance(release.get("id"), str):
        return None
    try:
        held = store.read(profile.graph_path().parent, release["id"])
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict) or held.get("client") != profile.client or held.get("release") != release["id"]:
        return None
    tables = held.get("tables")
    if not isinstance(tables, list) or not all(isinstance(table, str) for table in tables):
        return None
    return frozenset(tables)
