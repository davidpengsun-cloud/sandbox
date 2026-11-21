"""The golden-20 baseline: a snapshot of each of the owner's 20 golden NNA questions' reference SQL
(kge/estate/golden_sage.py, golden_20) on Meridian's sample, committed as clients/meridian/baselines/golden20.json and
checked for staleness. It is not the golden-20 gate's truth: the gate judges each answer against the item's reference SQL
run on the runtime the answer used, through the runtime layer and the SQL gate (kge/eval/run_golden.py, judge).

Per item it holds two things, apart:

  gate        "passed" where the SQL gate passes the SQL as written on the data-query path (the schema allowlist with
              the pack's declared joins, as /data/query reads it); else "held: needs ..." naming the optional joins
              and validity windows the pack would have to declare. The owner's SQL is never rewritten to pass: its
              LEFT joins and validity windows reflect real data (owner, 2026-11-14)
  rows        the SQL's result on the sample, read as the set's expectations are (golden_wealth.read on the estate's
              wealth session, outside the gate): its columns, its row count, its rows in the SQL's order and its
              headline figure, the first measure of the first row

A number other than a whole one is a string rounded to the cent, the tolerance the set's scorer (kge/eval/run_golden.py,
table_score) compares within; a date is its ISO text. Keys are sorted, so a fresh run gives the same bytes:

    python -m kge.estate.golden20 --write     make golden20-baseline
    python -m kge.estate.golden20 --check     exit 1 if the committed file is not what a fresh run gives

tests/integration/test_golden20.py fails if this file is stale, as python -m kge.estate.golden20 --check does. Where an
answer does not match its truth, difference() names the first way its SQL departs from the reference SQL: a filter, a
join, the period, the grouping or the measure, in that order.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

import sqlglot
from sqlglot import exp

from kge.estate.golden_sage import golden_20
from kge.estate.golden_wealth import read
from kge.pipeline.settings import ROOT
from kge.ports import AUTHORED

BASELINE = ROOT / "clients/meridian/baselines/golden20.json"
UNIT = "wealth"
CENT = Decimal("0.01")


def value(v):
    """A result cell as the baseline holds it."""
    if v is None or isinstance(v, (bool, str, int)):
        return v
    if isinstance(v, (float, Decimal)):
        return str(Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP))
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def gate(engine, sql: str) -> str:
    """"passed" where the SQL gate passes the query as written on the data-query path (the unit's schema allowlist with
    the pack's declared joins, as /data/query reads it); else "held:", naming what the pack would need to declare for
    the query to pass as written (an optional join for each LEFT join, a validity window for each table read by its
    effective and expiry dates), then the gate's first problem. The SQL is never rewritten to pass."""
    from kge.pipeline.sqlguard import schema_allowlist
    from kge.services.data.engine.governance import declared_allowlist
    allow = declared_allowlist(engine, UNIT, schema_allowlist(engine.g, UNIT, engine.RESTRICTED_COLUMNS))
    got = engine.w.gate(UNIT, sql, allow, dialect=AUTHORED)
    if got["passed"]:
        return "passed"
    needs = held_for(sql)
    first = (got["problems"] or ["refused"])[0]
    return "held: " + (f"{needs}; " if needs else "") + f"gate: {first}"


def held_for(sql: str) -> str:
    """What the pack would need to declare for the gate to pass a query as written: its LEFT joins as optional joins,
    and each table it reads by a validity window (one column BETWEEN another table's two dates) as a window."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    names = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    left = sorted({names.get(j.this.alias_or_name, j.this.name) for j in tree.find_all(exp.Join) if j.side == "LEFT"})
    windows = sorted({names.get(b.args["low"].table, b.args["low"].table) for b in tree.find_all(exp.Between)
                      if isinstance(b.args.get("low"), exp.Column) and isinstance(b.this, exp.Column)
                      and b.args["low"].table and b.this.table and b.this.table != b.args["low"].table})
    parts_ = ([f"optional joins declared (LEFT JOIN {', '.join(left)})"] if left else []) + \
             ([f"validity windows declared ({', '.join(windows)})"] if windows else [])
    return "needs " + " and ".join(parts_) if parts_ else ""


def run(engine, estate) -> dict:
    """Every item's gate verdict and its rows on the sample, by id."""
    session = estate.open(UNIT)
    out = {}
    try:
        for item, measures, _, _ in golden_20():
            entry = {"id": item.id, "gate": gate(engine, item.sql)}
            try:
                got = read(session, item, item.sql)["table"]
            except Exception as exc:
                out[item.id] = {**entry, "error": f"{type(exc).__name__}: {exc}"}
                continue
            rows = [[value(v) for v in row] for row in got["rows"]]
            first = got["columns"].index(measures[0])
            out[item.id] = {**entry, "columns": got["columns"], "row_count": len(rows), "rows": rows,
                            "headline": {"column": measures[0], "value": rows[0][first] if rows else None}}
    finally:
        session.close()
    return out


def text(engine, estate) -> str:
    return json.dumps({"items": run(engine, estate)}, indent=2, sort_keys=True) + "\n"


def table(entry: dict) -> dict:
    """An item's baseline as the set's scorer reads a table expectation: its numbers as numbers."""
    def number(v):
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return v
        return float(v) if isinstance(v, int) and not isinstance(v, bool) else v
    return {"columns": entry["columns"], "rows": [[number(v) for v in row] for row in entry["rows"]]}


DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
AGGREGATES = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)
BOUNDS = (exp.LT, exp.LTE, exp.GT, exp.GTE, exp.EQ, exp.Between)


def bare(node: exp.Expression) -> str:
    """An expression with every column's table dropped, as text."""
    return node.copy().transform(lambda n: exp.column(n.name) if isinstance(n, exp.Column) else n).sql(dialect="duckdb")


def months(tree: exp.Expression) -> set[str]:
    """The months the date literals of a query's conditions name; a first-of-month exclusive upper bound (<) is the
    month before it, as a half-open range ends."""
    out = set()
    for lit in tree.find_all(exp.Literal):
        m = DATE.match(lit.this) if lit.is_string else None
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        bound = lit.parent
        while bound is not None and not isinstance(bound, BOUNDS):
            bound = bound.parent
        if isinstance(bound, exp.LT) and m.group(3) == "01":
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        out.add(f"{year:04d}-{month:02d}")
    return out


def parts(sql: str) -> dict[str, set[str]]:
    """What a query reads, across every select of it (its CTEs too): its filters (WHERE conditions other than its
    period's), the tables it joins, the months its period names, its grouping columns and its measures' columns."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    filters, groups, measures = set(), set(), set()
    for select in tree.find_all(exp.Select):
        where = select.args.get("where")
        if where is not None:
            for conjunct in (where.this.flatten() if isinstance(where.this, exp.And) else [where.this]):
                if not months(conjunct):
                    filters.add(bare(conjunct))
        aliases = {e.alias: e.this for e in select.expressions if isinstance(e, exp.Alias)}
        group = select.args.get("group")
        for g in (group.expressions if group is not None else []):
            target = aliases.get(g.name, g) if isinstance(g, exp.Column) and not g.table else g
            groups |= {c.name for c in target.find_all(exp.Column)}
        for agg in select.find_all(*AGGREGATES):
            if not isinstance(agg.parent, exp.Window) and not any(isinstance(p, AGGREGATES) for p in agg.iter_expressions()):
                measures |= {c.name for c in agg.find_all(exp.Column)}
    tables = {t.name for t in tree.find_all(exp.Table) if t.name not in ctes}
    return {"filter": filters, "join": tables, "period": months(tree), "grouping": groups, "measure": measures}


def difference(asked: str | None, reference: str) -> str:
    """The first way the answer's SQL departs from the reference SQL: filter, join, period, grouping or measure, each
    with what one has and the other lacks; "no SQL generated" where the answer ran none."""
    if not asked:
        return "no SQL generated"
    try:
        a, r = parts(asked), parts(reference)
    except sqlglot.errors.SqlglotError as exc:
        return f"not compared: {exc}"
    for what in ("filter", "join", "period", "grouping", "measure"):
        lacks, extra = sorted(r[what] - a[what]), sorted(a[what] - r[what])
        if lacks or extra:
            said = [f"the reference has {', '.join(lacks)}" if lacks else "", f"the answer has {', '.join(extra)}" if extra else ""]
            return f"{what}: " + "; ".join(s for s in said if s)
    return "no difference found in filters, joins, period, grouping or measures"


def main(argv: list[str] | None = None) -> int:
    from kge.adapters.graphstore import GraphLocked
    from kge.estate.golden import warehouses
    from kge.pipeline.profile import ProfileError, resolve
    from kge.services.ask.engine.core import Engine
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose estate to read the reference SQL on (default: PROFILE, else the registry's default)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help=f"write {BASELINE.relative_to(ROOT)}")
    mode.add_argument("--check", action="store_true", help="exit 1 if the committed baseline is stale")
    args = ap.parse_args(argv)
    try:
        prof = resolve(args.profile)
        engine = Engine.serving(prof)
    except (ProfileError, GraphLocked) as exc:
        print(f"golden20: {exc}", file=sys.stderr)
        return 2
    try:
        got = text(engine, warehouses(prof.estate(), prof.client))
    finally:
        engine.close()
    if args.write:
        BASELINE.write_text(got)
        items = json.loads(got)["items"]
        passed = [i for i in items.values() if i["gate"] == "passed"]
        print(f"golden20: wrote {BASELINE.relative_to(ROOT)}: {len(items)} items, {len(passed)} pass the SQL gate as written")
        for i in items.values():
            print(f"  {i['id']}: {i.get('row_count', 0)} rows, headline {(i.get('headline') or {}).get('value')}, gate {i['gate'][:120]}")
        return 0
    if not BASELINE.is_file() or BASELINE.read_text() != got:
        print(f"golden20: {BASELINE.relative_to(ROOT)} is stale: run make golden20-baseline", file=sys.stderr)
        return 1
    print("golden20: the baseline is what the reference SQL gives now")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
