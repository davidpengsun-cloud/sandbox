"""The warehouse adapters: a client's warehouses behind the warehouse seams (kge/ports.py), DuckDB files on this machine
(duckdb.py) or BigQuery datasets (bigquery.py, bigquery_estate.py). open_warehouses picks the engine's reader
(WarehouseSet) from WAREHOUSE_BACKEND, and open_estate (estate_of, for a profile) the estate's writer
(EstateWarehouses); a caller asks for a seam, never for a backend's class or module."""
from __future__ import annotations

from pathlib import Path

from kge.pipeline.profile import Profile
from kge.pipeline.settings import settings
from kge.ports import AUTHORED, EstateWarehouses, WarehouseSet


def written_in(sql: str, dialect: str) -> str:
    """sql, a fragment the code writes in AUTHORED (DuckDB's), as dialect writes it: unchanged in AUTHORED, so DuckDB's
    prompts and plans keep the text they had, else transpiled by sqlglot (strftime(col, '%Y-%m-01') is BigQuery's
    FORMAT_DATE('%Y-%m-01', col)). For SQL a model is shown, which the warehouses send as written."""
    if dialect == AUTHORED:
        return sql
    import sqlglot
    return sqlglot.transpile(sql, read=AUTHORED, write=dialect)[0]


def backend() -> str:
    """The warehouse backend WAREHOUSE_BACKEND names: bigquery, else duckdb."""
    return "bigquery" if (settings().WAREHOUSE_BACKEND or "duckdb").strip().lower() == "bigquery" else "duckdb"


def catalogue_format(kind: str = "TABLE") -> str:
    """Describe the configured warehouse storage honestly, without implying an interchange file format."""
    storage = "BigQuery native" if backend() == "bigquery" else "DuckDB"
    return f"{storage} {kind.lower()}"


def open_warehouses(profile: Profile, units: list[str], curated: list[str], versions: dict[str, int],
                    inputs: dict[str, str]) -> WarehouseSet:
    """The profile's warehouses, one per unit, in the pack's order, with the curated sources beside them. versions: the
    warehouse version of each onboarded unit the served artifact names; inputs: the digest of the inputs each named
    version's rows were made from, which only DuckDB's files carry a stamp of (kge/pipeline/warehouse_versions.py)."""
    if backend() == "bigquery":
        from kge.adapters.warehouse.bigquery import BigQueryWarehouses
        return BigQueryWarehouses(profile, units, curated, versions=versions)
    from kge.adapters.warehouse.duckdb import Warehouses
    return Warehouses(profile.warehouses, units, curated, versions, profile.model_hash(), inputs)


def open_estate(warehouses: Path, client: str) -> EstateWarehouses:
    """The client's warehouses as the estate and onboarding write and read them: the DuckDB files in the folder
    warehouses, or, with WAREHOUSE_BACKEND=bigquery, the tables of the client's BigQuery dataset (and no file at all)."""
    if backend() == "bigquery":
        from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
        return BigQueryEstate(client)
    from kge.adapters.warehouse.duckdb import DuckDBEstate
    return DuckDBEstate(Path(warehouses))


def estate_of(profile: Profile) -> EstateWarehouses:
    """open_estate for a profile: its warehouses folder and its client."""
    return open_estate(profile.warehouses, profile.client)
