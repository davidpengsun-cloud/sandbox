"""A client's reference figures: the figures a drafted question states, checked by running the client's own SQL
under the SQL gate, with FIGURE_LIMIT and CLIENT_SQL_SECONDS bounding it."""
from __future__ import annotations

import datetime as dt
import decimal
import re

from kge.compiler import drafts
from kge.pipeline import sqlguard
from kge.estate.onboard.common import log
from kge.ports import FigureTimeout, WarehouseRefused


def _figures(con, proposed: dict, unit: str) -> dict:
    figures = {}
    for item in [x for x in proposed["items"] if x["kind"] == "term"]:
        measure = item["pack"]["measures"][0]
        table, column = measure["column"].split(".")[1:]
        binding = next(b for b in proposed["items"][0]["pack"]["period_bindings"] if b["table"] == f"{unit}.{table}")
        latest = con.execute(f'SELECT max("{binding["column"]}") FROM "{table}"').fetchone()[0]
        if latest is None:
            continue
        start = latest.replace(day=1)
        end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        pack = item["pack"]
        where = [f'"{table}"."{binding["column"]}" >= DATE \'{start}\'',
                 f'"{table}"."{binding["column"]}" < DATE \'{end}\'']
        where += [f["predicate"] for f in pack.get("filters", [])]
        for selector in pack.get("row_selectors", []):
            remote = selector["table"].split(".", 1)[1]
            where.append(f'EXISTS (SELECT 1 FROM "{remote}" WHERE {selector["predicate"]})')
        if measure["grain"] in {"month_end", "period_end", "quarter_end", "as_of"}:
            where.append(f'"{table}"."{binding["column"]}" = (SELECT max("{binding["column"]}") FROM "{table}" '
                         f'WHERE "{binding["column"]}" >= DATE \'{start}\' AND "{binding["column"]}" < DATE \'{end}\')')
        sql = f'SELECT round(sum("{table}"."{column}"), 2) FROM "{table}" WHERE ' + " AND ".join(where)
        value = con.execute(sql).fetchone()[0]
        figures[item["id"]] = {"month": start.strftime("%B %Y"), "figure": f"{value:.2f}", "sql": sql}
    return figures


def _golden_allowlist(pack: dict, catalogue: dict, unit: str) -> sqlguard.Allowlist:
    """The runtime gate's unit allowlist, built before this unit has a compiled graph."""
    tables = {table["name"] for table in catalogue["tables"]}
    schema = {table["name"]: {column["name"] for column in table["columns"]} for table in catalogue["tables"]}
    restricted = set()
    for policy in (pack.get("policies") or {}).get("restricted_attributes", []):
        for column in policy.get("columns", []):
            parts = str(column).split(".")
            if len(parts) == 3 and parts[0] == unit:
                restricted.add((parts[1], parts[2]))
    columns = {table: {column for column in values if (table, column) not in restricted}
               for table, values in schema.items()}
    joins = set()
    for table in catalogue["tables"]:
        for foreign in table.get("foreign_keys", []):
            joins.update(frozenset({(table["name"], left), (foreign["ref_table"], right)})
                         for left, right in zip(foreign["columns"], foreign["ref_columns"]))
    measures = {(table, column) for table, values in columns.items() for column in values}
    return sqlguard.Allowlist(unit, tables, columns, joins, restricted, schema, measures)


def _client_golden(con, pack: dict, catalogue: dict, unit: str, supplied: list[dict],
                   measures: list[dict], proposed: list[dict]) -> tuple[list[dict], list[dict]]:
    """Gate and check client figures. A failed item stays visible as a request, never as a draft.

    proposed: the items drafted from the semantic files, whose ids a client's may not take and whose terms and aliases
    a client's question must be read as (the runtime's own term recognition, kge/services/ask/engine/question.py parse).

    A client id that is a drafted item's would fail the pack's duplicate-id check at accept and leave the unit stuck, so
    it is an open request, never renamed behind the client's back. A currency asked is read by the runtime's rule ("in"
    and a code in capitals, so "for all policies" names none), and a question the agent cannot read as one of this
    unit's terms could only fail the gate."""
    from kge.services.ask.engine.core import ASKED_CURRENCY
    allow = _golden_allowlist(pack, catalogue, unit)
    drafted, held = [], []
    taken = {item["id"] for item in proposed}
    reader, own = _term_reader(pack, proposed)
    for raw in supplied:
        question, expect = raw["question"], raw["expect"]

        def hold(why: str, ask: str) -> None:
            held.append({"golden": raw["id"], "question": question, "why": why, "source": raw["source"], "ask": ask})

        if raw["id"] in taken:
            hold(f"id {raw['id']} is a drafted item's id",
                 f"Your golden question '{question}' has the id {raw['id']}, which a drafted item already has — give it another id.")
            continue
        taken.add(raw["id"])
        if "behaviour" in expect:
            drafted.append({"id": raw["id"], "kind": "question", "title": question, "from": "client",
                            "client_source": raw["source"], "set": f"golden_{unit}", "terms": [],
                            "question": question, "expect": {"behaviour": expect["behaviour"], "no_figure": True}})
            continue
        result = sqlguard.gate(con, raw["sql"], allow, figure=True)
        if not result["passed"]:
            why = "; ".join(result["problems"])
            hold(why, f"Your SQL for '{question}' was refused: {why} — check it.")
            continue
        measured = result.get("measured", [])
        currencies = {(m["table"], m["column"]): str(m["currency"]).upper() for m in measures}
        declared = {currencies[(name.split(".", 1)[0], name.split(".", 1)[1])] for name in measured
                    if tuple(name.split(".", 1)) in currencies}
        named = {code for code in ASKED_CURRENCY.findall(question) if code in drafts.ISO_4217}
        if named and named != declared:
            why = f"question names {', '.join(sorted(named))}; SQL measures {', '.join(sorted(declared)) or 'no declared currency'}"
            hold(why, f"Your golden question '{question}' has the wrong currency: {why} — check it.")
            continue
        term = reader(question)
        if term not in own:
            phrase = re.split(r"\s+(?:for|at|as at|in|on|to|by|during)\s+", question.strip(), maxsplit=1)[0].strip().lower()
            if term is None:
                hold("no glossary term recognised",
                     f"We can't match '{question}' to a term: rephrase it, or add '{phrase}' as an alias.")
            else:
                hold(f"reads as the term {term}, not one of this unit's",
                     f"'{question}' reads as the term '{term}', which this unit does not define: rephrase it with one of "
                     f"{', '.join(repr(n) for n in sorted(own))}, or add '{phrase}' as an alias.")
            continue
        try:
            stated = decimal.Decimal(str(expect["figure"]))
            tolerance = decimal.Decimal(str(raw.get("tolerance", "0")))
            actual = _client_figure(con, raw["sql"], stated)
            matches = abs(actual - stated) <= tolerance if tolerance else actual.quantize(CENT) == stated.quantize(CENT)
            said = f"Your figure for '{question}' is {stated:,.2f}; your SQL returns {actual:,.2f} — check either."
        except ClientFigureError as exc:
            hold(exc.why, f"Your SQL for '{question}' could not be checked: {exc.why}.")
            continue
        except (decimal.InvalidOperation, ValueError, OverflowError):
            hold("figure out of range", f"Your figure or tolerance for '{question}' could not be compared: check that both are plain numbers.")
            continue
        if not matches:
            hold("reference SQL result differs", said)
            continue
        drafted.append({"id": raw["id"], "kind": "question", "title": question, "from": "client",
                        "client_source": raw["source"], "set": f"golden_{unit}", "terms": [],
                        "question": question,
                        "expect": {"figure": f"{stated:.2f}", "unit": unit,
                                   "columns": [name.removeprefix(f"{unit}.") for name in measured], "join_path": []},
                        "sql": raw["sql"], **({"tolerance": str(tolerance)} if tolerance else {})})
    return drafted, held


CENT = decimal.Decimal("0.01")
FIGURE_LIMIT = decimal.Decimal(10) ** 18
CLIENT_SQL_SECONDS = 10


class ClientFigureError(Exception):
    """Why a client's reference SQL gave no figure to compare, worded for the page (never the warehouse's own text)."""
    def __init__(self, why: str):
        self.why = why
        super().__init__(why)


def _client_figure(con, sql: str, stated: decimal.Decimal) -> decimal.Decimal:
    """The one figure a client's gated SQL returns, run with a time limit. A NULL is the sum over no rows: it compares as
    0 when the client states 0 (a figure for an empty period), and is otherwise no figure. The warehouse's error text can
    name server paths and settings, so it goes to the server log only."""
    try:
        row = con.figure(sql, CLIENT_SQL_SECONDS)
    except FigureTimeout:
        raise ClientFigureError(f"it ran for more than {CLIENT_SQL_SECONDS:g} seconds") from None
    except WarehouseRefused as exc:
        log.warning("client golden SQL failed: %s", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
        raise ClientFigureError("the warehouse could not run it") from None
    if row is None or len(row) != 1:
        raise ClientFigureError("it did not return one figure")
    value = row[0]
    if value is None:
        if stated == 0:
            return decimal.Decimal(0)
        raise ClientFigureError("it returned no figure (NULL: no rows matched)")
    if isinstance(value, bool) or not isinstance(value, (int, float, decimal.Decimal)):
        raise ClientFigureError(f"it returned {type(value).__name__} rather than a number")
    actual = decimal.Decimal(str(value))
    if not actual.is_finite() or abs(actual) >= FIGURE_LIMIT:
        raise ClientFigureError("it returned a figure out of range")
    return actual


def _term_reader(pack: dict, proposed: list[dict]):
    """The runtime's term recognition (kge/services/ask/engine/question.py parse) over the pack's glossary and aliases and the draft's own, as
    the graph will hold them once the draft is accepted: (read, own), read giving the term name a question resolves to
    (None for none, as for "customers" alone, which is the population), own the names of this draft's terms."""
    from types import SimpleNamespace
    from kge.services.ask.engine.core import CANDIDATE, parse
    names = {g["id"]: g["name"] for g in pack.get("glossary", []) if isinstance(g, dict)}
    aliases = list((pack.get("terminology") or {}).get("aliases") or [])
    own = set()
    for item in proposed:
        if item.get("kind") != "term":
            continue
        for g in item["pack"].get("glossary", []):
            names[g["id"]] = g["name"]
            own.add(g["name"])
        aliases += item["pack"].get("terminology.aliases", [])
    phrases = {}
    for a in aliases:
        if not isinstance(a, dict) or not isinstance(a.get("phrase"), str):
            continue
        if a.get("term") in names:
            phrases.setdefault(a["phrase"].lower(), names[a["term"]])
        elif isinstance(a.get("candidate"), str):
            phrases.setdefault(a["phrase"].lower(), CANDIDATE + a["candidate"])
    domain = SimpleNamespace(ALIASES=phrases, UNIT_WORDS={})
    glossary = sorted(names.values())

    def read(question: str) -> str | None:
        term = parse(question, glossary, domain).term
        return None if term == "__population__" else term
    return read, own

