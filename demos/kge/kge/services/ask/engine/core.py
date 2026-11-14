"""Public rules baseline, access decisions and governed definitions.

Numeric requests share the typed interpreter, coverage, plan check and compiler
with Combined. A configured context model proposes data through planmodel; the
rules baseline emits the same contract. The warehouse gate executes only the
checked compiler's statements. Existing scalar, grouped and unit-parts answers
are presented alongside the additive typed result and bounded history.

Each process serves one profile. Domain meaning, access, restricted attributes,
source sets, calendars, measures and relationships come from its released pack.
The reference Agent's existing context-package helpers remain available until
its plan/contract migration; they do not authorize a public numeric bypass.
"""
from __future__ import annotations

from kge.services.ask.engine.contextpath import recorded

import datetime
import re
from contextlib import contextmanager
from dataclasses import replace

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.scope import ProductScope
from kge.adapters.deadline import RequestDeadlineExceeded
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, warehouse_guard
from kge.adapters.warehouse import open_warehouses, written_in
from kge.compiler.checks import ISO_4217
from kge.semantics.query import Semantics, restricted_decision
from kge.pipeline.sqlguard import gate, schema_allowlist
from kge.semantics.vocabulary import compiled as vocabulary
from kge.ports import AUTHORED, WarehouseSet
from kge.pipeline.profile import Profile
from kge.pipeline.privacy import assess_privacy, redact
from kge.pipeline.trace import transient_failure
from kge.services.ask.engine.question import (
    ABBREVIATED,
    CATALOGUE_ABSTENTION,
    catalogue_question,
    ASKED_CURRENCY,
    UNPLACED_RANGE,
    asked_window,
    calendar_from_fiscal,
    CANDIDATE,
    COMMON,
    currency_asked,
    definition_request,
    fiscal_from_calendar,
    fiscal_quarter,
    fiscal_start,
    in_force,
    MONTH,
    month_name,
    MONTHS,
    NAMED_FISCAL_START,
    no_conversion,
    not_joinable_named,
    OUTSIDE_PACK,
    grounded,
    pair,
    parse,
    Period,
    quarter_label,
    question_words,
    Request,
    requested_currencies,
    restricted_asked,
    restricted_named,
    spoken,
    WORDS,
    YEAR,
)
from kge.services.ask.engine.graphview import CITE_AS, DECISIONS, GLOSSARY, Graph, TIERS, UPSTREAM, WRITES
from kge.services.ask.engine.config import AGENT_ARMS, ARMS, MODEL, PATHS, PRESETS, PROFILE_SET, RunConfig, STAGES

__all__ = ["CATALOGUE_ABSTENTION", "catalogue_question", "ABBREVIATED", "AGENT_ARMS", "ARMS", "ASKED_CURRENCY", "asked_window", "assess_privacy", "blocked",
           "calendar_from_fiscal", "CANDIDATE", "CITE_AS", "COMMON", "currency_asked", "DECISIONS", "Engine",
           "fiscal_from_calendar", "fiscal_quarter", "fiscal_start", "GLOSSARY", "Graph", "in_force", "MODEL",
           "MONTH", "month_name", "MONTHS", "NAMED_FISCAL_START", "no_conversion", "not_joinable_named", "pair",
           "parse", "PATHS", "Period", "PRESETS", "PROFILE_SET", "quarter_label", "question_words", "redact",
           "Request", "restricted_asked", "restricted_named", "RunConfig", "spoken", "STAGES", "TIERS", "UPSTREAM",
           "WORDS", "WRITES", "YEAR"]


def blocked(principal: str, access: dict) -> dict:
    """The answer to a principal who may not read a unit the question needs: across warehouses, the join-time rule's
    blocked_access, which names every warehouse denied and queries none; within one, a refusal."""
    denied = access["denied_warehouses"]
    if len(access["warehouses_involved"]) > 1:
        return {"behaviour": "blocked_access", "denied_warehouses": denied, "allowed_warehouses": access["allowed_warehouses"],
                "warehouses_involved": access["warehouses_involved"], "sql_run": [],
                "reason": f"Cross-warehouse join blocked: {principal} may not read {', '.join(denied)}. A join across warehouses "
                          f"proceeds only if the requester can read every warehouse in it "
                          f"({', '.join(access['warehouses_involved'])}). No warehouse was queried."}
    return {"behaviour": "refuse", "denied_warehouses": denied, "reason": f"{principal} may not read {', '.join(denied)}."}


class Engine:
    @classmethod
    def serving(cls, profile: Profile) -> "Engine":
        """The engine that answers as the release: on the generation its hash's last release recorded (Profile.serve),
        never what a later load of the same hash repoints. Every entry point that answers as the release opens its
        engine here, so none can forget: the API, make eval, make ab (the job's eval), make agent, make parity and the
        access demo."""
        return cls(profile.serve())

    @classmethod
    def candidate(cls, profile: Profile) -> "Engine":
        """The engine on what the profile's hash's folder names now, its last load: a candidate's check (make margin),
        which measures the generation that load made, never the one a release of the same hash recorded before."""
        return cls(profile)

    @property
    def identity(self) -> dict:
        """The release identity the engine opened (Graph.identity, Profile.identity): its model hash, its graph's
        generation, chunks table and fixture warehouse versions, pinned together as it opened."""
        return {**self.g.identity, "warehouses": self.g.identity.get("warehouses", {})}

    def __init__(self, profile: Profile):
        """The engine on the profile's graph and warehouses: the graph of the artifact the client's current pointer names
        as the engine starts, or of the hash the profile is pinned to (Profile.at), and no other until it restarts. A graph
        loaded for another client than the profile's is a RuntimeError: a profile reads its own client's data only.
        An engine that fails to start lets the graph go: a process that cannot serve must not hold its lock. The graph's
        domain, its access mirror (Graph.rights) and its catalogue (Graph.catalogue) are read as it opens, so a request's
        access check and a governed read's allowlist send the graph store no query."""
        self.profile = profile = profile.pin()
        self.g = Graph(profile)
        try:
            d = self.g.domain()
            self.g.rights()
            self.g.catalogue()
            rel = self.g.release()
            if rel is not None and rel["client"] != profile.client:
                raise RuntimeError(f"the graph {profile.graph_path()} was loaded for {rel['client']}, and profile {profile.id} is "
                                   f"{profile.client}'s: make graph loads the profile's own")
            self.UNIT_ORDER: list[str] = d["units"]
            self.UNIT_LABEL: dict[str, str] = d["labels"]
            self.UNIT_KEY: dict[str, str] = d["keys"]
            self.KEY_TABLE: dict[str, str] = d["key_tables"]
            self.RESTRICTED_COLUMNS: list[tuple[str, str, str]] = d["restricted"]
            self.ALIASES: dict[str, str] = d["aliases"]
            self.SOURCE_SETS = {phrase: units for phrase, units in d["unit_words"].items() if isinstance(units, tuple)}
            self.UNIT_WORDS: dict[str, str] = {phrase: unit for phrase, unit in d["unit_words"].items() if isinstance(unit, str)}
            self.CALENDARS: dict[str, dict] = d["calendars"]
            self.PERIODS: dict[str, dict] = d["periods"]
            self._baseline_bindings = {table: dict(binding) for table, binding in self.PERIODS.items()}
            self.FISCAL_START = fiscal_start(self.CALENDARS)
            self.TIME_MODES: dict[str, str] = d["modes"]
            self.NAMED_PERIODS: dict[str, str] = d["named_periods"]
            self.GROUP_BY: dict[str, str] = d["group_by"]
            self.ENTITY_WORDS: dict[str, str] = d["entity_words"]
            self.ACRONYMS: dict[str, list[str]] = d["acronyms"]
            self.CURATED: list[str] = d["curated"]
            words = vocabulary(d["vocabulary"])
            self.VOCABULARY = d["vocabulary"]
            self.FILTERS = words.filters
            self.FILTER_COLUMNS = words.columns
            self.INTENTS = words.intents
            self.MAILBOXES: frozenset[str] = self.g.mailboxes()
            self.RESTRICTED_CITES: dict[str, str] = d["cites"]
            self.RESTRICTED_PHRASES: dict[str, list[str]] = d["restricted_phrases"]
            self.TITLE: str | None = d["title"]
            self.NOT_JOINABLE: list[dict] = d["not_joinable"]
            self.CURRENCIES: list[str] = d["currencies"]
            self.CONVERSIONS: list[tuple[str, str, str]] = d["conversions"]
            self.TERM_CURRENCIES: dict[str, list[str]] = d["term_currencies"]
            self.ONBOARDED: frozenset[str] = frozenset(d["onboarded"])
            self.BUSINESS: dict[str, str] = {col.split(".", 1)[1]: name for col, name in d.get("business", {}).items()}
            self.ASKER: str | None = profile.asker()
            versions = self.g.warehouse_versions()
            self.w: WarehouseSet = open_warehouses(profile, self.UNIT_ORDER, self.CURATED, versions, self.g.warehouse_inputs())
            self._semantics: Semantics | None = None
            self.w.configure_runtimes({
                **self.g.runtime_subgraph(),
                "reference_allowlists": {
                    u: replace(schema_allowlist(self.g, u, self.RESTRICTED_COLUMNS), windows={
                        (table.split('.', 1)[1], policy['valid_from'], policy['valid_to'])
                        for table, policy in self.semantics.scd2.items() if table.split('.', 1)[0] == u
                    }) for u in self.UNIT_ORDER
                },
            })
            self.term_names = self.g.terms()
            self.semantics.graph_vocabulary = self.g.vocabulary()
            self.g.warm_release_periods(self.semantics, self.w)
        except BaseException:
            self.g.close()
            raise

    @property
    def semantics(self) -> Semantics:
        """The graph's query-semantics layer (kge/semantics/query.py), read once for this engine's release."""
        if self._semantics is None:
            self._semantics = Semantics(self.g, self)
        return self._semantics

    def resolve(self, term: str, cfg: RunConfig) -> list[dict]:
        """The columns a term maps to. Resolution by names guesses a column from its name, which is what an agent does
        with bare INFORMATION_SCHEMA, and it is wrong in interesting ways."""
        if cfg.glossary:
            return self.g.implementations(term)
        if cfg.resolution in ("none", "words"):
            return []
        return self.guess([w for w in re.split(r"\W+", term.lower()) if w])

    def guess(self, words: list[str]) -> list[dict]:
        """The column each unit's schema names best by these words: a word in the column's
        name scores one, any word in its table's name one more. No glossary, no mapping."""
        out = []
        for unit in self.UNIT_ORDER:
            best = None
            for t, c, d in self.w.columns(unit):
                score = sum(1 for w in words if w in c.lower()) + (1 if any(w in t.lower() for w in words) else 0)
                if score and (best is None or score > best[0]):
                    best = (score, t, c, d)
            if best:
                out.append({"unit": unit, "tbl": best[1], "col": best[2], "dtype": best[3], "grain": "", "currency": "", "calendar": "", "conversion": "", "certified_by": "", "certified_on": ""})
        return out

    def period_clause(self, unit: str, table: str, period: Period | None, cfg: RunConfig, agg: str = "sum",
                      dialect: str = AUTHORED) -> tuple[str | None, list[str], str | None]:
        """Returns (where, joins, note) or (None, [], reason) when the period cannot be applied.

        On the graph context the table's period binding says which column carries its period, of what kind, on which
        calendar, and what it rolls up through: the client's pack, read from the graph. Every other context finds the
        column by its name, as before the bindings (period_by_name), so the control is handed no curated metadata.
        A year is always bounded on both sides. On a snapshot table a sum or count over a year is read as the year's
        last snapshot, because balances at several dates do not add; an average may range over them.
        dialect is AUTHORED for Engine.plan_unit, whose SQL rewrite_sql carries to the warehouse; the prompts (Agent.roles,
        Combined.generate) pass the warehouses' sql_dialect, so a model is shown the clause in the SQL it writes
        (written_in: BigQuery's FORMAT_DATE, never DuckDB's strftime)."""
        if not cfg.graph:
            return self.period_by_name(unit, table, period, agg, dialect)
        if period is None:
            return "", [], None
        b = self.PERIODS.get(f"{unit}.{table}")
        if b is None:
            return "", [], "no period column; period ignored"
        cal, kind, v = self.CALENDARS[b["calendar"]], b["kind"], period.value
        col = f"{table}.{b['column']}"
        roll = b["rollup_table"].split(".", 1)[1] if b["rollup_table"] else None
        joins = [f"JOIN {roll} ON {col} = {roll}.{b['column']}"] if roll else []
        rolled = f"{roll}.{b['rollup_column']}" if roll else None
        start = cal["year_start_month"]
        if period.kind in ("calendar_quarter", "fiscal_quarter") or (kind == "fiscal_quarter" and period.kind == "as_of"):
            if period.kind == "as_of":
                d = datetime.date.fromisoformat(v)
                cq = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
            else:
                cq = v if period.kind == "calendar_quarter" else calendar_from_fiscal(v, self.FISCAL_START)
            label = quarter_label(cq, cal)
        if kind == "month":
            if period.kind == "month":
                return f"{col} = DATE '{v}'", [], None
            if period.kind in ("calendar_quarter", "fiscal_quarter"):
                if rolled is None:
                    return None, [], f"{table} rolls up to no quarter"
                if label == v:
                    return f"{rolled} = '{label}'", joins, None
                return f"{rolled} = '{label}'", joins, (f"{v} read as calendar {cq} for a calendar-year unit" if start == 1
                                                        else f"{v} read as {label} on the {month_name(start)} fiscal year")
            if period.kind == "year":
                return f"{col} >= DATE '{v}-01-01' AND {col} < DATE '{int(v) + 1}-01-01'", [], None
            if period.kind == "as_of":
                d = datetime.date.fromisoformat(v)
                return f"{col} = DATE '{d.year}-{d.month:02d}-01'", [], "as-at date read as its calendar month"
        if kind == "fiscal_quarter":
            if period.kind == "month":
                return None, [], "quarterly grain: a single month is not available"
            if period.kind == "year":
                if rolled is None:
                    return None, [], f"{table} rolls up to no year"
                return (f"{rolled} = {int(v) + (start > 1)}", joins,
                        f"calendar year read as the fiscal year starting that {month_name(start)}")
            note = {"calendar_quarter": f"calendar {v} is {label} on the {month_name(start)} fiscal year (STD-FISCAL-CAL)",
                    "fiscal_quarter": f"{v} read as {label}", "as_of": "as-at date read as its fiscal quarter"}[period.kind]
            return f"{col} = '{label}'", [], None if label == v else note
        if kind == "snapshot":
            if period.kind == "as_of":
                return f"{col} = DATE '{v}'", [], None
            if period.kind == "month":
                d = datetime.date.fromisoformat(v)
                nxt = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
                return f"{col} >= DATE '{d.isoformat()}' AND {col} < DATE '{nxt.isoformat()}'", [], None
            if period.kind == "calendar_quarter":
                y, n = int(v[:4]), int(v[-1])
                end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[n]
                return f"{col} = DATE '{y}-{end}'", [], "quarter read as its end date"
            if period.kind == "year":
                lo, hi = f"DATE '{v}-01-01'", f"DATE '{int(v) + 1}-01-01'"
                if agg == "avg":
                    return f"{col} >= {lo} AND {col} < {hi}", [], None
                return (f"{col} = (SELECT max({b['column']}) FROM {table} WHERE {b['column']} >= {lo} AND {b['column']} < {hi})", [],
                        "snapshots do not add across dates; the year is read as its last snapshot")
        if kind == "event_date":
            if period.kind == "year":
                return f"{col} >= DATE '{v}-01-01' AND {col} < DATE '{int(v) + 1}-01-01'", [], None
            if period.kind == "month":
                if getattr(self, "_semantics", None) and f"{unit}.{table}" in self._semantics.partitioned:
                    d = datetime.date.fromisoformat(v)
                    nxt = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
                    return f"{col} >= DATE '{d.isoformat()}' AND {col} < DATE '{nxt.isoformat()}'", [], None
                return written_in(f"strftime({col}, '%Y-%m-01') = '{v}'", dialect), [], None
        return "", [], "no period column; period ignored"

    def period_columns(self, unit: str, table: str) -> list[tuple[str, str]]:
        """The columns a table's period binding reads, (table, column): its period column, and the column of the table it
        rolls up through; none for a table with no binding."""
        b = self.PERIODS.get(f"{unit}.{table}")
        if b is None:
            return []
        roll = [(b["rollup_table"].split(".", 1)[1], col) for col in (b["column"], b["rollup_column"])] if b["rollup_table"] else []
        return [(table, b["column"]), *roll]

    def baseline_periods(self, tables):
        """Period bindings whose declared columns occur in the baseline catalogue."""
        bindings = getattr(self, '_baseline_bindings', self.PERIODS)
        return {table: dict(bindings[table]) for table, columns in tables.items()
                if table in bindings and bindings[table]['column'] in columns
                and (bindings[table]['column'] in ('month_start', 'fiscal_quarter', 'as_of')
                     or bindings[table]['column'].endswith('_on'))}

    def period_by_name(self, unit: str, table: str, period: Period | None, agg: str = "sum",
                       dialect: str = AUTHORED) -> tuple[str | None, list[str], str | None]:
        """The baseline's naive calendar reading, with physical names supplied by period declarations."""
        if period is None:
            return "", [], None
        binding = Engine.baseline_periods(self, {f'{unit}.{table}': self.g.table_columns(unit, table)}).get(f'{unit}.{table}')
        if binding is None:
            return "", [], "no period column; period ignored"
        col = f"{table}.{binding['column']}"
        roll = binding.get('rollup_table')
        roll_table = roll.split('.', 1)[1] if roll else None
        rolled = f"{roll_table}.{binding.get('rollup_column')}" if roll_table else None
        roll_join = [f"JOIN {roll_table} ON {col} = {roll_table}.{binding['column']}"] if roll_table else []
        if binding["kind"] == "month":
            if period.kind == "month":
                return f"{col} = DATE '{period.value}'", [], None
            if period.kind == "calendar_quarter":
                return f"{rolled} = '{period.value}'", roll_join, None
            if period.kind == "fiscal_quarter":
                cq = calendar_from_fiscal(period.value, NAMED_FISCAL_START)
                return f"{rolled} = '{cq}'", roll_join, f"{period.value} read as calendar {cq} for a calendar-year unit"
            if period.kind == "year":
                return f"{col} >= DATE '{period.value}-01-01' AND {col} < DATE '{int(period.value) + 1}-01-01'", [], None
            if period.kind == "as_of":
                d = datetime.date.fromisoformat(period.value)
                return f"{col} = DATE '{d.year}-{d.month:02d}-01'", [], "as-at date read as its calendar month"
        if binding["kind"] == "fiscal_quarter":
            if period.kind == "fiscal_quarter":
                return f"{col} = '{period.value}'", [], None
            if period.kind == "calendar_quarter":
                return f"{col} = 'FY{period.value[2:4]}-Q{period.value[-1]}'", [], "quarter label taken as written"
            if period.kind == "month":
                return None, [], "quarterly grain: a single month is not available"
            if period.kind == "year":
                return f"{rolled} = {int(period.value) + 1}", roll_join, "calendar year read as the fiscal year starting that April"
            if period.kind == "as_of":
                d = datetime.date.fromisoformat(period.value)
                cq = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
                return f"{col} = '{fiscal_from_calendar(cq, NAMED_FISCAL_START)}'", [], "as-at date read as its fiscal quarter"
        if binding["kind"] == "snapshot":
            if period.kind == "as_of":
                return f"{col} = DATE '{period.value}'", [], None
            if period.kind == "month":
                d = datetime.date.fromisoformat(period.value)
                nxt = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
                return f"{col} >= DATE '{d.isoformat()}' AND {col} < DATE '{nxt.isoformat()}'", [], None
            if period.kind == "calendar_quarter":
                y, n = int(period.value[:4]), int(period.value[-1])
                end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[n]
                return f"{col} = DATE '{y}-{end}'", [], "quarter read as its end date"
            if period.kind == "year":
                lo, hi = f"DATE '{period.value}-01-01'", f"DATE '{int(period.value) + 1}-01-01'"
                if agg == "avg":
                    return f"{col} >= {lo} AND {col} < {hi}", [], None
                return (f"{col} = (SELECT max({binding['column']}) FROM {table} WHERE {binding['column']} >= {lo} AND {binding['column']} < {hi})", [],
                        "snapshots do not add across dates; the year is read as its last snapshot")
        if binding['kind'] == 'event_date' and period.kind == "year":
            return f"{col} >= DATE '{period.value}-01-01' AND {col} < DATE '{int(period.value) + 1}-01-01'", [], None
        if binding['kind'] == 'event_date' and period.kind == "month":
            return written_in(f"strftime({col}, '%Y-%m-01') = '{period.value}'", dialect), [], None
        return "", [], "no period column; period ignored"

    def plan_unit(self, req: Request, impl: dict, cfg: RunConfig) -> dict:
        """A compatibility part, interpreted and executed through the same checked public path."""
        out = self.ruled(req.question, config=cfg, _request=req)
        unit = impl["unit"]
        column = impl["tbl"] + "." + impl["col"]
        if out["behaviour"] != "figure" or out.get("unit") != unit or out.get("column") != column or out.get("rows"):
            return {"unit": unit, "column": column, "status": "refused" if out["behaviour"] in ("refuse", "blocked_access") else "abstain",
                    "reason": out.get("reason") or "The complete question does not resolve to this scalar implementation."}
        return {**{k: impl.get(k, "") for k in ("grain", "currency", "calendar", "conversion")},
                **{k: out[k] for k in ("unit", "column", "sql", "guard", "join_path", "notes")},
                "status": "ok", "value": out["figure"]}

    def key_name_fact(self, req: Request) -> tuple[str, str, dict] | None:
        """The pair a join on a not-joinable column's name is about, in the pack's order, and the no-join fact between them:
        the first column the question names whose unit and table's unit have one, and are the units it names first, if it
        names two. None for none."""
        for x in not_joinable_named(req.question.lower(), self):
            a, b = sorted((x["unit"], x["table_unit"]), key=self.UNIT_ORDER.index)
            fact = self.g.no_join(a, b)
            if fact and (len(req.units) < 2 or set(req.units[:2]) == {a, b}):
                return a, b, fact
        return None

    def commensurable(self, parts: list[dict], req: Request, cfg: RunConfig) -> tuple[bool | None, str]:
        if not cfg.graph:
            return None, "no measure metadata in this arm; summed as written"
        ok = [p for p in parts if p["status"] == "ok"]
        if len(ok) < 2:
            return True, "single figure"
        if req.agg == "count" or all(p["column"].split(".")[-1].lower().endswith("flag") for p in ok):
            return False, "counts of different populations under one term are reported as parts, not summed"
        keys = {(p["grain"], p["currency"], p["calendar"]) for p in ok}
        conv = {p["conversion"] for p in ok}
        if len(keys) == 1 and conv == {"none"}:
            return True, "same grain, currency and calendar; no conversion"
        reasons = []
        if len({p["grain"] for p in ok}) > 1:
            reasons.append("grain differs")
        if len({p["calendar"] for p in ok}) > 1:
            reasons.append("calendar differs")
        if len({p["currency"] for p in ok}) > 1:
            reasons.append("currency differs")
        if conv != {"none"}:
            reasons.append("a conversion at a period-end rate is involved")
        return False, "; ".join(reasons)

    def refresh(self) -> None:
        self.term_names = self.g.terms()

    def stamped(self, cfg: RunConfig) -> RunConfig:
        """cfg as this engine runs it: as its profile's. ValueError for a config another profile's process set."""
        if cfg.profile and cfg.profile != self.profile.id:
            raise ValueError(f"this process serves profile {self.profile.id}, not {cfg.profile}: a config runs as the process's profile")
        return replace(cfg, profile=self.profile.id)

    def close(self) -> None:
        self.g.close()
        self.w.close()

    def one_unit(self, req: Request, cfg: RunConfig) -> str | None:
        """The one unit a question resolves to, else None: the units it names, with the unit of the context that answers
        its term on the graph context; naming none, the units its term is certified in."""
        units = set(req.units)
        contexts = self.semantics.contexts_of(req.term) if cfg.graph else []
        if contexts:
            units |= {c.fact.split(".", 1)[0] for c in contexts}
        elif not units:
            units = {i["unit"] for i in self.g.implementations(req.term)}
        return next(iter(units)) if len(units) == 1 else None

    def request_abstention(self, req: Request, reason: str) -> dict:
        """Keep the context planner's unread-period refusal while stopping before models or warehouse queries.

        Impossible dates and currency problems retain their specific diagnostics. The
        parser keeps its problem on the request so every answering surface stops early.
        """
        if req.problem == reason == UNPLACED_RANGE:
            return {"behaviour": "abstain_with_reason", "reason": self.semantics.unplaced([req.question])}
        if (req.problem == reason and reason.startswith("Please give a valid period:")
                and any(any(j.required for j in c.joins) for c in self.semantics.contexts_of(req.term))):
            said = reason.removeprefix("Please give a valid period:").strip().removesuffix(".")
            return {"behaviour": "abstain_with_reason", "reason": reason + " " + self.semantics.unplaced([said])}
        return {"behaviour": "abstain", "reason": reason}

    def request_problem(self, req: Request, terms: list[str] | None = None) -> str | None:
        """Reject unreadable periods and currencies absent from the requested measures in the named units.
        Existing pack-wide currency refusals keep their wording; a currency held by another measure grants no conversion."""
        if req.problem:
            return req.problem
        codes = requested_currencies(req.question)
        if not codes or req.special == "no_conversion_standard" or req.definitional:
            return None
        for term in terms if terms is not None else req.terms_found:
            impls = [i for i in self.g.implementations(term) if not req.units or i["unit"] in req.units]
            held = {i["currency"] for i in impls if i.get("currency")}
            contexts = [c for c in self.semantics.contexts_of(term) if not req.units or c.unit in req.units]
            if contexts:
                measures = self.semantics.measure_declarations
                held |= {m['currency'] for c in contexts if (m := measures.get(c.measure)) and m.get('currency')}
            for code in codes:
                if held and code not in held and code in ISO_4217:
                    return f"No {code} conversion standard exists for {term} in the requested units: certified figures are in {', '.join(sorted(held))}."
        return None

    @recorded
    def answer(self, question: str, principal: str | None = None, purpose: str = "analysis", arm_name: str = "graph",
               config: RunConfig | None = None, *, _request: Request | None = None, products: list[str] | None = None,
               history=(), runtimes: list[str] | None = None) -> dict:
        """The rule-based answer (ruled), each column it names by its business name (business)."""
        answer = self.ruled(question, principal, purpose, arm_name, config, _request=_request, products=products,
                            history=history, runtimes=runtimes)
        answer.pop("combined", None)
        return self.business(ProductScope(self, products).documents(answer))

    def business(self, answer: dict) -> dict:
        """The answer with each column it names under its business name, where the pack gives the column one (BUSINESS,
        <table>.<column> -> <business table>.<business column>): its measure column, each step of its join path, and each
        figure's column. The SQL keeps the warehouse's physical names; a column with no business name keeps its own."""
        names = getattr(self, "BUSINESS", None) or {}
        if not names:
            return answer

        def named(text):
            if not isinstance(text, str):
                return text
            return re.sub(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", lambda m: names.get(m.group(0), m.group(0)), text)
        out = dict(answer)
        if "column" in out:
            out["column"] = named(out["column"])
        if isinstance(out.get("join_path"), list):
            out["join_path"] = [named(p) for p in out["join_path"]]
        if isinstance(out.get("figures"), list):
            out["figures"] = [{**f, "column": named(f.get("column"))} if isinstance(f, dict) and "column" in f else f for f in out["figures"]]
        return out

    def ruled(self, question: str, principal: str | None = None, purpose: str = "analysis", arm_name: str = "graph",
              config: RunConfig | None = None, *, _request: Request | None = None, products: list[str] | None = None,
              history=(), _combined=None, runtimes: list[str] | None = None) -> dict:
        out = self._ruled(question, principal, purpose, arm_name, config, _request=_request,
                          products=products, history=history, _combined=_combined, runtimes=runtimes)
        out.setdefault("history", [])
        return out

    def _ruled(self, question: str, principal: str | None = None, purpose: str = "analysis", arm_name: str = "graph",
               config: RunConfig | None = None, *, _request: Request | None = None, products: list[str] | None = None,
               history=(), _combined=None, runtimes: list[str] | None = None) -> dict:
        """The rule-based answer, asked as principal, else as the querying identity the profile's deployment names
        (ASKER). config, if given, replaces the preset arm_name names;
        a config that cannot run here, or that another profile's process set, raises ValueError before anything is
        asked. _request carries the combined engine's parsed request, including a
        validated model period; direct callers keep the original rules-only trace.
        Two units a certified join links are read together only by a principal who may read both, checked before
        anything is resolved, so a principal who reads one of them gets no partial figure from it. A term's figure asked
        with one of its unit's restricted columns named by the column's own name is refused under the column's policy
        rather than answered with the term's total."""
        principal = principal or self.ASKER
        cfg = self.stamped(config or PRESETS[arm_name])
        bad = cfg.problems("agent" if cfg.arm in AGENT_ARMS else "ask" if _combined is not None else "answer",
                           has_model=_combined is not None and _combined.llm is not None)
        if bad:
            raise ValueError("; ".join(bad))
        runtrace.use(cfg)
        runtrace.stage("understand")
        req = _request or parse(question, self.term_names if cfg.glossary else [], self, aliases=cfg.resolution not in ("none", "words"))
        if cfg.graph:
            req = definition_request(req)
        trace = [{"step": "parse", "term": req.term, "candidate": req.candidate, "units": req.units or ("all" if req.all_units else "unspecified"),
                  "period": req.period.label() if req.period else None, **({"period_by": req.period_by} if _request is not None else {}),
                  "filters": req.filters, "agg": req.agg, "special": req.special}]
        out = {"question": question, "principal": principal, "purpose": purpose, "arm": cfg.label, "trace": trace,
               "versions": {"parser": "rules-v1", "embedding_model": self.g.embedder.name}}
        scope = ProductScope(self, products)
        out.update(scope.metadata())
        runtrace.stage("access")
        if not principal:
            return {**out, "behaviour": "refuse", "reason": f"No principal named, and profile {self.profile.id} names none to ask as."}
        if decision := scope.check(principal, req.units):
            return {**out, **decision}
        if not self.g.may(principal, "querying"):
            return {**out, "behaviour": "refuse", "reason": f"{principal} is not a querying identity."}
        if runtimes is not None:
            rt_check = self.w.select_runtime([], runtimes=runtimes, readable_units=self.g.readable_units(principal))
            if not rt_check.get("ok"):
                return {**out, "behaviour": "refuse", "reason": rt_check.get("reason") or "no runtime available",
                        **({"_skipped_runtimes": rt_check["skipped"]} if rt_check.get("skipped") else {})}
        portal_card = scope.portal_only()
        if portal_card:
            return {**out, "behaviour": "abstain", "facts": [], "figures": [],
                    "reason": "Not in the knowledge graph. No figure was read."}
        if catalogue_question(question, parse(question, self.term_names, self).terms_found):
            return {**out, "behaviour": "abstain", "reason": CATALOGUE_ABSTENTION}
        if not cfg.graph and not req.definitional:
            implementations = (self.guess(question_words(req.question, self)) if cfg.resolution == 'words'
                               else self.resolve(req.term, cfg) if req.term is not None else [])
            lookup = bool(req.restricted)
            if not lookup and not any(not req.units or item['unit'] in req.units for item in implementations):
                return {**out, 'behaviour': 'abstain', 'reason': 'No column resolves the requested sources in this arm. No figure is given.'}
        data_units = [] if req.definitional else list(req.units)
        if cfg.graph and not req.definitional:
            from kge.services.ask.engine.planrules import cohort_terms, matched_contexts, snapshot
            for term in req.terms_found:
                contexts = self.semantics.contexts_of(term)
                if req.units:
                    contexts = matched_contexts(self, req, contexts)
                elif scope.products is not None and not req.all_units:
                    readable = self.g.readable_units(principal)
                    contexts = [c for c in contexts if c.unit in readable] or contexts
                    contexts = [c for c in contexts if scope.contains(c.fact)] or [c for c in contexts if c.unit in scope.units] or contexts
                data_units.extend(c.unit for c in contexts)
                if not contexts and len(req.units) < 2:
                    mappings = [i for i in snapshot(self).mapping_declarations if i['term'] == term]
                    if scope.products is not None and not req.units and not req.all_units:
                        readable = self.g.readable_units(principal)
                        mappings = [i for i in mappings if i['unit'] in readable] or mappings
                    scoped = [i for i in mappings if i['unit'] in req.units] if req.units else mappings
                    if not scoped and len(req.units) == 1 and len(req.terms_found) > 1 and cohort_terms(self, req):
                        mappings = []
                    data_units.extend(i['unit'] for i in (scoped or mappings))
            data_units = list(dict.fromkeys(data_units))
        access = self.g.join_access(principal, data_units)
        if not access['allowed']:
            return {**out, **blocked(principal, access), **({} if cfg.graph else {'behaviour': 'refuse'})}
        if req.restricted and data_units and req.term and not req.definitional and req.special is None:
            independent = not req.units and not any(j.through for term in req.terms_found
                for c in self.semantics.contexts_of(term) for j in c.joins)
            if len(data_units) == 1 or independent:
                req = replace(req, restricted=list(dict.fromkeys(c for unit in data_units for c in restricted_named(question, self, unit))))
        if req.restricted:
            readable = self.g.readable_units(principal)
            visible_restricted = [c for c in req.restricted if c.split('.')[0] in readable]
            scoped_restricted = [c for c in visible_restricted if c.split('.')[0] in req.units]
            visible_restricted = scoped_restricted or visible_restricted
            if cfg.graph and visible_restricted:
                privacy_units = data_units or req.units or list(dict.fromkeys(c.split('.')[0] for c in visible_restricted))
                context_units = list(dict.fromkeys(c.unit for term in req.terms_found
                    for c in self._semantics.contexts_of(term) if any(j.required for j in c.joins))) if self._semantics else []
                units = list(dict.fromkeys([*context_units, *privacy_units])) if len(context_units) == 1 else privacy_units
                tables = {c.rsplit('.', 1)[0] for c in visible_restricted}
                alternatives = [f"{entity['label']} keys ({', '.join(entity['table'] + '.' + key for key in entity['key'])})"
                    for entity in self.semantics.entities.values()
                    if entity['table'] in tables and entity['table'].split('.')[0] in readable
                    and (not req.units or entity['table'].split('.')[0] in req.units)
                    and entity['key'] and all(entity['table'] + '.' + key not in self.RESTRICTED_CITES for key in entity['key'])]
                decision = restricted_decision(visible_restricted, units, self.RESTRICTED_CITES, alternatives)
                if len(units) > 1 and set(units) <= readable and (data_units or req.units or req.special == 'key_name_join'):
                    cached = getattr(self._semantics, 'plan_snapshot', None) if self._semantics else None
                    facts = cached.no_join_declarations if cached is not None and cached.plan_writes == self.g.writes else self.g.q(
                        'MATCH (x:Unit)-[n:NO_JOIN]->(y:Unit) RETURN x.id AS a, y.id AS b, n.asserted_by AS asserted_by, n.asserted_on AS asserted_on, n.note AS note')
                    facts = sorted((f for f in facts if {f['a'], f['b']} <= set(units)), key=lambda f: pair(f['a'], f['b'], self))
                    if facts:
                        decision.update(joins_refused=[pair(f['a'], f['b'], self) for f in facts],
                                        fact={k: v for k, v in facts[0].items() if k not in ('a', 'b')})
                return {**out, **decision}
            return {**out, "behaviour": "refuse", "restricted": [c if c.split('.')[0] in readable else '(withheld)' for c in req.restricted],
                    "reason": "Restricted personal data. Not retrievable by the assistant under any purpose. No warehouse was queried."}
        if req.problem and (reason := self.request_problem(replace(req, problem=None))):
            return {**out, **self.request_abstention(req, reason)}
        if reason := self.request_problem(req):
            if (not req.problem or req.definitional or reason.startswith("That date does not exist")
                    or (not req.model_period_allowed and reason.startswith("Please give a valid period:")
                        and re.search(r"\b\d{4}\.\d+\b", reason))):
                return {**out, **self.request_abstention(req, reason)}
        joined = []
        if cfg.graph:
            certified_join = self._semantics.certified_join if self._semantics is not None else self.g.certified_join
            joined = [(a, b) for i, a in enumerate(req.units) for b in req.units[i + 1:] if certified_join(a, b)]
        linked = joined if not req.definitional else []
        if linked:
            access = self.g.join_access(principal, [u for p in linked for u in p])
            trace.append({"step": "access", "rule": "join_requires_all_warehouses", "principal": principal, "units": access["warehouses_involved"],
                          "denied": access["denied_warehouses"], "allowed": access["allowed"], "certified_join": ["-".join(p) for p in linked]})
            if not access["allowed"]:
                return {**out, **blocked(principal, access)}

        runtrace.stage("resolve")
        if req.special == "key_name_join" and cfg.graph and len(req.units) == 2 and (cj := self.g.certified_join(*req.units)):
            a, b = (self.UNIT_LABEL.get(u, u) for u in req.units)
            return {**out, "behaviour": "refuse", "fact": cj,
                    "reason": f"No join on a key's name between {a} and {b}: they are joined only through the certified join "
                              f"{cj['declared_join']} ({cj['note']}). No warehouse was queried."}
        if req.special == "key_name_join" and cfg.graph and (held := self.key_name_fact(req)):
            a, b, fact = held
            why = f"No certified join path between {self.UNIT_LABEL.get(a, a)} and {self.UNIT_LABEL.get(b, b)}. {fact['note']}"
            dds = self.g.data_dictionaries([a, b])
            if req.definitional and cfg.citation_rule == "tiers":
                runtrace.stage("retrieve")
                return {**out, **self.cite_tiers(req, principal, purpose, rests_on=tuple(dds)), "answer": "no", "fact": fact, "reason": why}
            if req.definitional:
                runtrace.stage("retrieve")
                return {**out, "behaviour": "cite_certified", "answer": "no", "cite": dds, "fact": fact, "reason": why,
                        "uncertified_context_not_cited": [{"id": h["doc"], "title": h["title"], "excerpt": h["excerpt"]} for h in self.g.retrieve(question, principal, purpose, k=2, shelf="uncertified")["exact"]]}
            return {**out, "behaviour": "refuse", "reason": why, "joins_refused": [pair(a, b, self)], "fact": fact}
        if req.special == "no_conversion_standard" and cfg.graph:
            return {**out, "behaviour": "abstain", "reason": no_conversion(currency_asked(question, self, req.term), self, req.term)}
        if req.special == "candidate_term":
            sources = self.g.candidate_sources(req.candidate) if cfg.graph else []
            return {**out, "behaviour": "abstain_route_steward" if sources else "abstain",
                    "reason": f"'{req.candidate}' is not a glossary term." + (" A definition exists only on the uncertified shelf; routed to the steward queue." if sources else ""),
                    "candidate_sources": [s["id"] for s in sources], "queue": sources}
        if req.definitional:
            runtrace.stage("retrieve")
            return {**out, **self.definition_answer(req, principal, purpose, cfg)}

        if not cfg.graph:
            readable = self.g.readable_units(principal)
            trace.append({"step": "map", "term": WORDS if cfg.resolution == 'words' else req.term,
                          "implementations": [f"{i['unit']}.{i['tbl']}.{i['col']}" for i in implementations
                                              if i['unit'] in readable],
                          "certified": cfg.glossary, **({"words": question_words(question, self)} if cfg.resolution == 'words' else {})})
        from kge.services.ask.engine.planpublic import answer
        return answer(self, req, principal, purpose, cfg, scope, out, history, _combined, runtimes=runtimes)

    @staticmethod
    def needs_scope_fallback(answer: dict) -> bool:
        """An absent answer can use another readable source; a refusal cannot."""
        if answer.get("result") is not None:
            return False
        if (answer.get("guard") or {}).get("failed_at") == "static":
            return False
        if answer["behaviour"] == "parts_with_reason":
            parts = answer.get("parts") or {}
            return bool(parts) and all(value is None for value in parts.values()) and not any(
                part.get("status") == "refused" for part in answer.get("plan") or [])
        if answer["behaviour"] == "combined":
            figures = answer.get("figures") or []
            if figures:
                return all(figure.get("value") is None for figure in figures)
            result = answer.get("result") or {}
            return bool(result.get("columns")) and not any(
                cell["value"]["kind"] != "null" for row in result.get("rows") or [] for cell in row["cells"])
        return (answer["behaviour"] in ("abstain", "abstain_with_reason")
                or answer["behaviour"] == "figure" and answer.get("figure") is None
                and not any(value is not None for value in (answer.get("rows") or {}).values()))

    @contextmanager
    def session(self, plan, dialect: str = AUTHORED):
        """Where a plan's queries run: its unit's warehouse, on a cursor of its own; or, for two units, a session that
        attaches both (Warehouses.attached). Opened only once the access rule has passed on every unit. dialect: the
        SQL's, the code's own (AUTHORED) unless a model wrote it (the warehouses' sql_dialect)."""
        if plan.attached:
            with self.w.attached(plan.units, dialect) as con:
                yield con
        else:
            with self.w.cursor(plan.unit, dialect) as cur:
                yield cur

    def run_plan(self, plan, sql: str) -> tuple[dict, list[tuple] | None]:
        """The gate on the context's allowlist, then the query if it passed: (the gate's verdict, the rows or None)."""
        try:
            with self.session(plan) as con:
                runtrace.stage("gate")
                guard = warehouse_guard(gate(con, sql, plan.allow))
                if not guard["passed"]:
                    return guard, None
                runtrace.stage("execute")
                return guard, con.execute(sql).fetchall()
        except RequestDeadlineExceeded:
            raise
        except Exception as exc:
            transient_failure(exc)
            return {"passed": False, "failed_at": "execute", "problems": [WAREHOUSE_PROBLEM]}, None

    def context_query(self, plan) -> dict:
        """A plan's query once the release rule allows it: {sql, months, ran}, ran the release driver's query if it took
        one; or {decision}: abstain_with_reason for a month not released or not booked, or what the gate said of the
        release driver's query. Only once the access rule has passed on every unit the plan reads."""
        ran, months = [], None
        release = plan.release_query()
        if release:
            guard, rows = self.run_plan(plan, release)
            ran.append(release)
            if rows is None:
                return {"decision": {"behaviour": "refuse" if guard["failed_at"] == "static" else "abstain", "reason": "SQL gate: " +
                                     "; ".join(guard["problems"]), "sql": release, "guard": guard}}
            months, why = plan.released(rows)
            if why:
                return {"decision": {"behaviour": "abstain_with_reason", "reason": why, "sql_run": ran}}
        return {"sql": plan.query(months), "months": months, "ran": ran}

    def context_answer(self, req: Request, principal: str, out: dict, trace: list[dict], scope: ProductScope | None = None) -> dict:
        """Retained callers get the public coverage, policy, compiler and execution contract."""
        return self.ruled(req.question, principal, out.get("purpose", "analysis"),
                          _request=req, products=scope.products if scope is not None else None)

    def linked_plan(self, req: Request):
        """The plan of a question that asks which of one unit's customers the other has too (two units): through the
        certified join between them, the unit the question names first counted (Semantics.linked); refused if the question
        asks for restricted personal data by name. None with no certified join between them."""
        first = lambda u: min(m.start() for w, x in self.UNIT_WORDS.items() if x == u  # noqa: E731
                              for m in re.finditer(r"\b" + w + r"\b", req.question.lower()))
        return self.semantics.linked(*sorted(req.units, key=first), req.restricted)

    def linked_answer(self, plan, principal: str, out: dict, trace: list[dict]) -> dict:
        """A legacy linked plan cannot authorize an unchecked numeric answer."""
        return self.ruled(str(out.get("question") or ""), principal, out.get("purpose", "analysis"))

    def definition_in_scope(self, question: str, principal: str) -> bool:
        """Whether a question names a readable term or pack alias. The glossary itself is
        shared; a term defined by documents needs at least one definition in a unit the asker may read, or a shared
        document. Unmatched questions instead need grounding in the passages offered for citation."""
        req = parse(question, self.term_names, self)
        terms = [t for t in req.terms_found if t in self.term_names]
        readable = self.g.readable_units(principal)
        for term in terms:
            docs = self.g.defining_docs(term)
            if not docs:
                return True
            cert = self.g.certification([d["id"] for d in docs])
            if any(not c["unit"] or c["unit"] in readable for c in cert.values()):
                return True
        return False

    def definition_answer(self, req: Request, principal: str, purpose: str, cfg: RunConfig) -> dict:
        """Answer from documents under the chosen citation rule, grounding questions with no readable term.
        This is a document-only operation: no model fallback and no warehouse query."""
        needs_grounding = not self.definition_in_scope(req.question, principal)
        if needs_grounding:
            req = replace(req, term=None, terms_found=[])
        if cfg.citation_rule == "shelves":
            return self.cite(req, principal, purpose, ground=needs_grounding)
        if cfg.citation_rule == "tiers":
            return self.cite_tiers(req, principal, purpose, ground=needs_grounding)
        hits = {h["doc"]: h for h in self.g.retrieve(req.question, principal, purpose, k=3)["exact"]}
        if needs_grounding and not grounded(req.question, list(hits.values()), self):
            return {"behaviour": "abstain", "reason": OUTSIDE_PACK}
        return {"behaviour": "cite", "cite": sorted(hits), "reason": "no shelf distinction in this arm",
                "cited_docs": [{"doc": d, "title": hits[d]["title"], "shelf": hits[d]["shelf"]} for d in sorted(hits)]}

    def undefined(self, req: Request) -> dict | None:
        """The term a definitional question resolves to, if no document defines it on either shelf: a term of the pack's
        glossary alone (a term a steward accepts keeps the document it came from), as {term, definition, steward,
        contexts}. None for any other question, and for one that resolves no glossary term."""
        t = req.term
        if not req.definitional or t is None or t not in self.term_names or self.g.defining_docs(t):
            return None
        row = self.g.q("MATCH (t:Term) WHERE t.name = $t RETURN t.definition AS definition, t.steward AS steward", {"t": t})
        held = row[0] if row else {}
        return {"term": t, "definition": held.get("definition") or "", "steward": held.get("steward") or "",
                "contexts": [c.id for c in self.semantics.contexts_of(t)]}

    def from_glossary(self, u: dict, hits: list[dict], kind: str) -> dict:
        """A definitional answer on a term no document defines (undefined): its definition in the pack's glossary and its
        steward, labelled so, citing nothing. What the question retrieved (hits) is listed as references, each by its
        shelf or tier (kind), never cited as the term's definition or as the authority. A term the glossary gives no
        definition either is not answered."""
        refs, seen = [], set()
        for h in hits:
            if h["doc"] not in seen:
                seen.add(h["doc"])
                refs.append({"doc": h["doc"], "title": h["title"], kind: h[kind], "chunk": h["chunk"], "excerpt": h["excerpt"],
                             "why": f"{h[kind]}{' shelf' if kind == 'shelf' else ''}: retrieved by the question; no document defines "
                                    f"{u['term']}, so it is never cited as its definition"})
        out = {"cite": [], "citations": [], "references": refs}
        if not u["definition"]:
            return {**out, "behaviour": "abstain", "reason": f"No document defines {u['term']}, and the pack's glossary gives it no definition."}
        steward = f" (steward: {u['steward']})" if u["steward"] else ""
        through = f"; it is certified through its context {', '.join(u['contexts'])}" if u["contexts"] else ""
        if u["contexts"]:
            out["definition_citation"] = {"kind": "pack_glossary", "source": GLOSSARY, "term": u["term"],
                                          "contexts": u["contexts"], "client": self.profile.client,
                                          "model_hash": self.identity["hash"]}
        return {**out, "behaviour": "glossary_definition", "term": u["term"], "definition": u["definition"], "definition_from": GLOSSARY,
                "steward": u["steward"], "contexts": u["contexts"],
                "reason": f"No document defines {u['term']}: this definition is {GLOSSARY}'s{steward}{through}. "
                          "What the question retrieved is listed as a reference, never cited as its definition."}

    def cite(self, req: Request, principal: str, purpose: str, ground: bool = False) -> dict:
        """Citation by shelf. A definition a steward accepted from an email makes a term, not a certified source: it is
        shown, never cited. A historical question is the one case where the superseded document is the right
        citation."""
        undefined = self.undefined(req)
        if undefined is not None:
            return self.from_glossary(undefined, self.g.retrieve(req.question, principal, purpose, k=4)["exact"], "shelf")
        cites: list[str] = []
        superseded: list[str] = [r["d.id"] for r in self.g.q("MATCH (d:Document)-[:SUPERSEDED_BY]->() RETURN d.id")]
        uncertified_definers: list[dict] = []
        for term in [t for t in req.terms_found if t]:
            for d in self.g.defining_docs(term):
                if d["shelf"] != "certified":
                    uncertified_definers.append({"term": term, "id": d["id"], "title": d["title"], "shelf": d["shelf"]})
                elif d["id"] not in superseded and d["id"] not in cites:
                    cites.append(d["id"])
        if req.term and uncertified_definers and not cites and any(u["term"] == req.term for u in uncertified_definers):
            src = ", ".join(u["id"] for u in uncertified_definers if u["term"] == req.term)
            return {"behaviour": "abstain", "cite": [], "uncertified_definition_not_cited": uncertified_definers,
                    "reason": f"'{req.term}' is a glossary term defined only on the uncertified shelf ({src}). "
                              f"There is no certified definition to cite."}
        hits = self.g.retrieve(req.question, principal, purpose, k=6, shelf="certified")
        extra = 0
        for h in hits["exact"]:
            if h["doc"] not in cites and h["doc"] not in superseded and extra < (2 if cites else 3):
                cites.append(h["doc"])
                extra += 1
        if re.search(r"\b(2023|2024)\b|\bversion\b", req.question.lower()):
            cites += superseded
        if ground and not grounded(req.question, [h for h in hits["exact"] if h["doc"] in cites], self):
            return {"behaviour": "abstain", "reason": OUTSIDE_PACK}
        conflicts = self.g.uncertified_mentions(req.term) if req.term else []
        runtrace.retrieved([{"doc": c["id"], "chunk": c["chunk"], "tier": c["tier"], "ai_ready": c["ai_ready"]} for c in conflicts], "uncertified_mention")
        unc = self.g.retrieve(req.question, principal, purpose, k=3, shelf="uncertified")["exact"]
        seen = {c["id"] for c in conflicts}
        for h in unc:
            if h["doc"] not in seen:
                conflicts.append({"id": h["doc"], "title": h["title"], "kind": "", "tier": h["tier"], "ai_ready": h["ai_ready"],
                                  "chunk": h["chunk"], "excerpt": h["excerpt"]})
        return {"behaviour": "cite_certified", "cite": cites, "superseded_not_cited": [s for s in superseded if s not in cites],
                "uncertified_definition_not_cited": uncertified_definers,
                "uncertified_context_not_cited": conflicts[:4], "retrieval": {"index_returned": hits["index_returned"], "post_filter_lost": hits["post_filter_lost"]}}

    def cite_tiers(self, req: Request, principal: str, purpose: str, rests_on: tuple[str, ...] = (), ground: bool = False) -> dict:
        """The tier rule: what upstream AI certification landed decides what is cited.

        Only documents landed as AI-ready are retrieved. Gold is cited as the
        authority; silver is cited with attribution, its owner and version;
        bronze is listed as a reference and never cited. A document is cited for
        the time it was in force: the current one always, and one superseded
        since when the question asks about a period inside its effective window.
        rests_on: documents a graph fact rests on, placed by their tiers; the
        fact is cited by them alone, not by what the question retrieves. One
        of a unit the principal may not read is not cited, only named as held
        back (not_readable): the fact is the graph's, the document the unit's.
        A term defined only by sources that may not be cited is not answered,
        and one no document defines is answered from the pack's glossary
        (from_glossary): what the question retrieves is then a reference, never
        cited as its definition or as the authority."""
        undefined = None if rests_on else self.undefined(req)
        if undefined is not None:
            return {**self.from_glossary(undefined, self.g.retrieve(req.question, principal, purpose, k=4, tiers=TIERS)["exact"], "tier"),
                    "superseded_not_cited": [], "not_ai_ready": [], "not_readable": []}
        window = asked_window(req, self)
        citations: list[dict] = []
        references: list[dict] = []
        not_in_force: list[str] = []
        not_ready: list[dict] = []
        not_readable: list[dict] = []
        reference_definers: list[dict] = []
        placed: set[str] = set()
        readable = self.g.readable_units(principal)

        def place(c: dict, why: str, chunk: str | None = None, term: str | None = None) -> None:
            """One document, by its tier: cited, a reference, not in force for the period asked, or not AI-ready;
            or, of a unit the principal may not read, held back by its id alone, with no title, owner or version."""
            if c["doc"] in placed:
                return
            placed.add(c["doc"])
            if c["unit"] and c["unit"] not in readable:
                not_readable.append({"doc": c["doc"], "unit": c["unit"], "why": f"a {c['unit']} document, and {principal} may not read {c['unit']}: not cited"})
            elif not c["ai_ready"]:
                not_ready.append({"doc": c["doc"], "title": c["title"], "why": "landed as not AI-ready: never retrieved or cited"})
            elif c["tier"] not in CITE_AS:
                references.append({"doc": c["doc"], "title": c["title"], "tier": c["tier"], "why": f"{c['tier'] or 'no tier'}: for reasoning and reference, never cited"})
                if term:
                    reference_definers.append({"term": term, "doc": c["doc"]})
            elif not (in_force(c, None) or (window and in_force(c, window))):
                not_in_force.append(c["doc"])
            else:
                entry = {"n": len(citations) + 1, "doc": c["doc"], "title": c["title"], "tier": c["tier"], "cited_as": CITE_AS[c["tier"]],
                         "owner": c["owner"], "version": c["version"], "in_force": f"{c['cert_from']} to {c['cert_to'] or 'now'}", "chunk": chunk, "why": why}
                if c["tier"] == "silver":
                    entry["attribution"] = f"{c['owner']}, version {c['version']}"
                citations.append(entry)

        pinned = self.g.certification(list(rests_on))
        for doc in rests_on:
            if doc in pinned:
                place(pinned[doc], "the graph's fact rests on it")
        for term in [t for t in req.terms_found if t]:
            defs = self.g.defining_docs(term)
            cert = self.g.certification([d["id"] for d in defs])
            for d in defs:
                c = cert[d["id"]]
                chunk = None
                if c["ai_ready"] and c["tier"] in CITE_AS:
                    first = self.g.q("MATCH (d:Document {id: $d})-[:HAS_CHUNK]->(c:Chunk) WHERE c.readiness <> 'embed-prohibited' AND $purpose IN c.purposes "
                                     "RETURN c.id AS chunk ORDER BY c.ordinal LIMIT 1", {"d": d["id"], "purpose": purpose})
                    chunk = first[0]["chunk"] if first else None
                    if chunk:
                        runtrace.retrieved([{"doc": d["id"], "chunk": chunk, "tier": c["tier"], "ai_ready": c["ai_ready"]}], "definition")
                place(c, f"certified definition of {term}", chunk, term)
        if req.term and not citations and any(r["term"] == req.term for r in reference_definers):
            src = ", ".join(r["doc"] for r in reference_definers if r["term"] == req.term)
            return {"behaviour": "abstain", "cite": [], "citations": [], "references": references, "superseded_not_cited": not_in_force,
                    "not_ai_ready": not_ready, "not_readable": not_readable,
                    "reason": f"'{req.term}' is defined only by sources landed as bronze ({src}): for reasoning and reference, never cited. "
                              f"There is no certified definition to cite."}
        hits = {"exact": [], "index_returned": 0, "post_filter_lost": 0}
        if not rests_on:
            hits = self.g.retrieve(req.question, principal, purpose, k=6, tiers=("gold", "silver"))
        cert = self.g.certification([h["doc"] for h in hits["exact"]])
        extra = 0
        for h in hits["exact"]:
            if h["doc"] in placed:
                continue
            c = cert[h["doc"]]
            if in_force(c, None) or (window and in_force(c, window)):
                if extra >= (2 if citations else 3):
                    continue
                extra += 1
            place(c, "retrieved by the question", h["chunk"])
        if ground and not grounded(req.question, [h for h in hits["exact"] if h["chunk"] in {c["chunk"] for c in citations}], self):
            return {"behaviour": "abstain", "reason": OUTSIDE_PACK}
        context = [{"doc": h["doc"], "title": h["title"], "tier": h["tier"], "why": "bronze: for reasoning and reference, never cited",
                    "chunk": h["chunk"], "excerpt": h["excerpt"]}
                   for h in self.g.retrieve(req.question, principal, purpose, k=3, tiers=("bronze",))["exact"]]
        if req.term:
            mentions = self.g.q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(t:Term {name: $t}) WHERE d.ai_ready AND d.tier = 'bronze' "
                                "AND c.readiness <> 'embed-prohibited' AND $purpose IN c.purposes AND (d.unit = '' OR d.unit IN $units) "
                                "RETURN d.id AS doc, d.title AS title, d.tier AS tier, d.ai_ready AS ai_ready, c.id AS chunk, left(c.text, 160) AS excerpt "
                                "ORDER BY d.id, c.id LIMIT 8",
                                {"t": req.term, "purpose": purpose, "units": sorted(self.g.readable_units(principal))})
            runtrace.retrieved(mentions, "mention")
            context = [{**{k: m[k] for k in ("doc", "title", "tier", "chunk", "excerpt")}, "why": "bronze: mentions the term; for reasoning and reference, never cited"}
                       for m in mentions] + context
        shown = 0
        for r in context:
            if r["doc"] not in placed and shown < 4:
                placed.add(r["doc"])
                references.append(r)
                shown += 1
        return {"behaviour": "cite_certified", "cite": [c["doc"] for c in citations], "citations": citations, "references": references,
                "superseded_not_cited": not_in_force, "not_ai_ready": not_ready, "not_readable": not_readable,
                "in_force_for": {"from": window[0], "to": window[1]} if window else "now",
                "retrieval": {"index_returned": hits["index_returned"], "post_filter_lost": hits["post_filter_lost"]}}
