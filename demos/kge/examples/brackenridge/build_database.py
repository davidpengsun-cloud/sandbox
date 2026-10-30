"""Build the invented Brackenridge DuckDB from its reviewed DDL and CSVs."""
from pathlib import Path

from kge.estate import ddl

HERE = Path(__file__).resolve().parent


def main() -> None:
    target = HERE / "brackenridge.duckdb"
    target.unlink(missing_ok=True)
    models = ddl.parse_client((HERE / "schema.sql").read_text())
    con = ddl.connect(target)
    try:
        for model in models:
            for statement in ddl.statements(model):
                con.execute(statement)
            files = {p.name: p.read_bytes() for p in (HERE / "data" / model["unit"]).glob("*.csv")}
            rows = ddl.read_csvs(model, files)
            for table in model["tables"]:
                names = ", ".join(f'"{c["name"]}"' for c in table["columns"])
                marks = ", ".join("?" for _ in table["columns"])
                con.executemany(f'INSERT INTO "{model["unit"]}"."{table["name"]}" ({names}) VALUES ({marks})', rows[table["name"]])
    finally:
        con.close()


if __name__ == "__main__":
    main()
