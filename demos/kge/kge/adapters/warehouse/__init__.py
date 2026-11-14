"""The warehouse adapters: a client's warehouses behind the warehouse seams (kge/ports.py), DuckDB files on this machine
(duckdb.py) or BigQuery datasets (bigquery.py, bigquery_estate.py). open_warehouses picks the engine's reader
(WarehouseSet) from WAREHOUSE_BACKEND, and open_estate (estate_of, for a profile) the estate's writer
(EstateWarehouses); a caller asks for a seam, never for a backend's class or module."""
from __future__ import annotations

import decimal
import re
from pathlib import Path

from kge.pipeline.profile import Profile
from kge.pipeline.settings import settings
from kge.ports import AUTHORED, EstateWarehouses, QueryRefused, WarehouseSet

SCHEMA_PLACEHOLDER = re.compile(
    r"`?\$\{(?:SCHEMA\.)?([A-Za-z_][A-Za-z0-9_]*)\}\.([A-Za-z_][A-Za-z0-9_]*)`?|\$\{(?:SCHEMA\.)?([A-Za-z_][A-Za-z0-9_]*)\}"
)


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


def strip_schema_placeholder(sql: str) -> str:
    """Replace ${SCHEMA}.TABLE, ${SCHEMA.KEY}.TABLE, and ${SCHEMA} placeholders so table names resolve in the served warehouse."""
    def replace(match: re.Match) -> str:
        table_name = match.group(2)
        return table_name if table_name else "main"

    return SCHEMA_PLACEHOLDER.sub(replace, sql)


def check_readonly_reference_sql(sql: str, dialect: str = "bigquery") -> None:
    """Enforce single-statement read-only SQL, forbidden functions, and internal-table restrictions on reference_sql."""
    import sqlglot
    from sqlglot import exp
    from kge.pipeline.sqlguard import WRITES

    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
    except sqlglot.errors.SqlglotError as exc:
        raise QueryRefused(f"does not parse: {str(exc).splitlines()[0][:120]}") from None
    if len(trees) != 1:
        raise QueryRefused(f"one statement only; got {len(trees)}")
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.Union)) or any(isinstance(n, WRITES) for n in tree.walk()):
        raise QueryRefused("read-only: SELECT or WITH ... SELECT, nothing that writes")
    server_functions = {"current_setting", "current_database", "current_schema", "current_schemas", "current_user",
                        "session_user", "user", "session_user_id", "current_query", "current_catalog", "version",
                        "getenv", "getvariable", "current_role", "current_version", "inet_client_addr", "inet_server_addr"}
    expanding_functions = {"repeat", "space", "pad", "lpad", "rpad", "range", "generate_series", "generate_array",
                           "generate_date_array", "generate_timestamp_array", "array_agg", "list", "listagg",
                           "string_agg", "group_concat", "array_concat_agg", "array_resize", "list_resize",
                           "array_concat", "list_concat", "list_zip", "array_zip", "printf", "format"}
    for function in tree.find_all(exp.Func):
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.lower() in server_functions:
            raise QueryRefused(f"server-information function {name.lower()} is not allowed")
        if name.lower() in expanding_functions:
            raise QueryRefused(f"memory-expanding function {name.lower()} is not allowed")
    ctes = {c.alias_or_name.casefold() for c in tree.find_all(exp.CTE)}
    forbidden_tables = {
        "access", "access_grants", "access_audit", "onboard_job",
        "unit_manifest", "unit_history", "unit_history_lock", "unit_requests",
        "audit", "passwords", "people", "sessions",
    }
    graph_table_re = re.compile(r"^g[0-9a-f]{16}_")
    for t in tree.find_all(exp.Table):
        if not t.db and not t.catalog and t.name.casefold() in ctes:
            continue
        bare_name = (t.name or "").rsplit(".", 1)[-1].strip("`").lower()
        if (
            bare_name.startswith(("_kge_", "unit_", "chunks_"))
            or bare_name in forbidden_tables
            or graph_table_re.match(bare_name)
        ):
            raise QueryRefused(f"table {t.name} is an internal or access table and may not be read")


def extract_figure(rows: list) -> float | int | decimal.Decimal | None:
    """Extract a scalar numeric figure from reference_sql rows when present."""
    if not rows:
        return None
    if len(rows) == 1:
        first = rows[0]
        vals = list(first.values()) if isinstance(first, dict) else list(first)
        for val in reversed(vals):
            if isinstance(val, (int, float, decimal.Decimal)) and not isinstance(val, bool):
                return val if isinstance(val, decimal.Decimal) else float(val)
    return None


def open_warehouses(profile: Profile, units: list[str], curated: list[str] = (),
                    versions: dict[str, int] | None = None, inputs: dict[str, str] | None = None) -> WarehouseSet:
    """The profile's warehouses, one per unit, in the pack's order, with the curated sources beside them. versions: the
    warehouse version of each onboarded unit the served artifact names; inputs: the digest of the inputs each named
    version's rows were made from, which only DuckDB's files carry a stamp of (kge/pipeline/warehouse_versions.py)."""
    if backend() == "bigquery":
        from kge.adapters.warehouse.bigquery import BigQueryWarehouses
        return BigQueryWarehouses(profile, units, curated, versions=versions)
    from kge.adapters.warehouse.duckdb import Warehouses
    model_hash = profile.model_hash() if hasattr(profile, "model_hash") else None
    return Warehouses(profile.warehouses, units, curated, versions, model_hash, inputs)


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
