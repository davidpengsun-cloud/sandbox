"""How two glossary terms differ (#617), on a small synthetic pack of its own and stubs: no client's pack, set or fixture.

    .venv/bin/python -m pytest tests/unit/test_term_difference.py -q

The pack declares two terms over different facts, filters, row selectors, period rules and versions, a third defined
only by a document of a unit the reader may not read, and a context of the first term on that unit. Every name in it
comes from NAMES, so the same pack with every term, table, column, label and catalogue name renamed (RENAMED) must give
the same answer in its own words: nothing about the terms lives in the engine. The period ranges are read from rows in
an in-memory DuckDB, each query checked by the SQL gate on its context's allowlist first."""
from __future__ import annotations

import datetime
import json
from dataclasses import replace
from types import SimpleNamespace

import duckdb
import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.query import Context, Join, Rule, Semantics
from kge.semantics.vocabulary import intent_pattern
from kge.services.ask.engine import difference
from kge.services.ask.engine.core import Engine
from kge.services.ask.engine.question import Request, parse

NAMES = {
    "frozen": "Frozen Total", "live": "Live Total", "hidden": "Hidden Total", "pnl": "Profit and Loss Total",
    "frozen_def": "The ledger's month-end total, frozen at close.", "live_def": "The ledger's running total, with every correction.",
    "hidden_def": "A total only the other desk may read.",
    "steward_a": "Ledger Steward", "steward_b": "Desk Steward",
    "unit": "ledger", "unit_label": "General Ledger", "other": "desk", "other_label": "Trading Desk",
    "snap": "snapshot_rows", "event": "event_rows", "months": "month_rows", "accounts": "account_rows", "secret": "secret_rows",
    "snap_name": "Month-end balances", "event_name": "Booked entries", "accounts_name": "Accounts", "secret_name": "Desk book",
    "as_of_name": "Balance date",
    "published": "Published months only", "eligible": "Eligible entries", "not_test": "Not on a test account",
    "currency": "EUR", "live_doc": "DOC-LIVE", "hidden_doc": "DOC-HIDDEN",
}
RENAMED = {
    "frozen": "Closing Stock", "live": "Rolling Stock", "hidden": "Vault Stock", "pnl": "Wear and Tear Stock",
    "frozen_def": "Stock as counted at each close.", "live_def": "Stock as it moves, adjusted for returns.",
    "hidden_def": "Stock only the vault team may read.",
    "steward_a": "Warehouse Steward", "steward_b": "Vault Steward",
    "unit": "depot", "unit_label": "Depot Operations", "other": "vault", "other_label": "Vault Operations",
    "snap": "count_lines", "event": "move_lines", "months": "period_lines", "accounts": "bin_lines", "secret": "vault_lines",
    "snap_name": "Stock counts", "event_name": "Stock moves", "accounts_name": "Bins", "secret_name": "Vault ledger",
    "as_of_name": "Count date",
    "published": "Closed periods only", "eligible": "Movable stock", "not_test": "Not a training bin",
    "currency": "GBP", "live_doc": "SOP-MOVES", "hidden_doc": "SOP-VAULT",
}
WHO = "reader"


def built(n: dict) -> SimpleNamespace:
    """The synthetic pack, as the engine reads it: its Semantics, its glossary and its documents, under the names n."""
    u, v = n["unit"], n["other"]
    snap, event, months, accounts = (f"{u}.{n[k]}" for k in ("snap", "event", "months", "accounts"))
    secret = f"{v}.{n['secret']}"
    sem = Semantics.__new__(Semantics)
    sem.d = SimpleNamespace(RESTRICTED_COLUMNS=[], GROUP_BY={}, ACRONYMS={}, BUSINESS={},
                            PERIODS={snap: {"column": "as_of", "kind": "snapshot"}, event: {"column": "happened", "kind": "event_date"},
                                     secret: {"column": "as_of", "kind": "snapshot"}})
    sem.tables = {snap: {"amount": "DECIMAL", "as_of": "DATE", "account_id": "INTEGER"},
                  event: {"amount": "DECIMAL", "happened": "DATE", "account_id": "INTEGER", "ok": "BOOLEAN",
                          "valid_from": "DATE", "valid_to": "DATE", "is_current": "BOOLEAN"},
                  months: {"month_end": "DATE", "released": "BOOLEAN", "release_date": "DATE"},
                  accounts: {"account_id": "INTEGER", "is_test": "BOOLEAN"},
                  secret: {"amount": "DECIMAL", "as_of": "DATE"}}
    rollup = Join("snap_month", "calendar_rollup", f"{n['snap']}.as_of = {n['months']}.month_end", None, None, snap, months, True)
    frozen = Context(f"ctx_{n['snap']}", n["frozen"], "", n["frozen"], f"{u}.frozen", f"sum({n['snap']}.amount)", "sum", "month", snap,
                     (f"{snap}.amount",), (Rule("published", n["published"], f"{n['months']}.released", months),), (), (rollup,),
                     version_dates={accounts: f"{snap}.as_of"})
    live = Context(f"ctx_{n['event']}", n["live"], "", n["live"], f"{u}.live", f"sum({n['event']}.amount)", "sum", "day", event,
                   (f"{event}.amount",), (Rule("eligible", n["eligible"], f"{n['event']}.ok", event),),
                   (Rule("not_test", n["not_test"], f"{n['accounts']}.account_id = {n['event']}.account_id AND {n['accounts']}.is_test",
                         accounts, "exclude"),), ())
    hidden = Context(f"ctx_{n['secret']}", n["frozen"], "", n["frozen"], f"{v}.secret", f"sum({n['secret']}.amount)", "sum", "month",
                     secret, (f"{secret}.amount",), (), (), ())
    sem.contexts = {c.id: c for c in (frozen, live, hidden)}
    sem.measure_declarations = {f"{u}.frozen": {"grain": "month", "currency": n["currency"]},
                                f"{u}.live": {"grain": "day", "currency": n["currency"]},
                                f"{v}.secret": {"grain": "month", "currency": "JPY"}}
    sem.scd2 = {event: {"table": event, "is_current": "is_current", "valid_from": "valid_from", "valid_to": "valid_to",
                        "valid_to_inclusive": True}}
    sem.releases = {months: {"table": months, "period": "month_end", "freeze_date": "release_date", "release_date": "release_date",
                             "released": "released"}}
    sem.entities, sem.cites, sem.certified, sem.partitioned = {}, {}, [], {}
    sem.declared = {rollup.id: rollup}
    sem.types = {f"{t}.{c}": k for t, cols in sem.tables.items() for c, k in cols.items()}
    glossary = {n["frozen"]: (n["frozen_def"], n["steward_a"]), n["live"]: (n["live_def"], n["steward_a"]),
                n["hidden"]: (n["hidden_def"], n["steward_b"]), n["pnl"]: (n["frozen_def"], n["steward_b"])}
    documents = {n["live"]: [{"id": n["live_doc"], "unit": u}], n["hidden"]: [{"id": n["hidden_doc"], "unit": v}]}
    products = {snap: {"name": n["snap_name"], "fields": [{"name": "as_of", "business_name": n["as_of_name"]}]},
                event: {"name": n["event_name"]}, accounts: {"name": n["accounts_name"]}, secret: {"name": n["secret_name"]}}
    return SimpleNamespace(n=n, sem=sem, glossary=glossary, documents=documents, products=products)


def warehouse(n: dict) -> duckdb.DuckDBPyConnection:
    """The unit's rows: month-ends released to June 2025 and one not yet; balances from March 2024, one in the unreleased
    month; entries from 2 January 2025 to 20 June 2025, the last ineligible, one on a test account in December 2024; and the
    other unit's one row."""
    con = duckdb.connect(":memory:")
    con.execute(f"CREATE TABLE {n['months']}(month_end DATE, released BOOLEAN, release_date DATE)")
    con.execute(f"INSERT INTO {n['months']} VALUES ('2024-03-31', TRUE, '2024-04-05'), ('2025-06-30', TRUE, '2025-07-05'), "
                "('2025-07-31', FALSE, '2025-08-05')")
    con.execute(f"CREATE TABLE {n['snap']}(amount DECIMAL(18,2), as_of DATE, account_id INT)")
    con.execute(f"INSERT INTO {n['snap']} VALUES (1, '2024-03-31', 1), (2, '2025-06-30', 1), (3, '2025-07-31', 1)")
    con.execute(f"CREATE TABLE {n['accounts']}(account_id INT, is_test BOOLEAN)")
    con.execute(f"INSERT INTO {n['accounts']} VALUES (1, FALSE), (9, TRUE)")
    con.execute(f"CREATE TABLE {n['event']}(amount DECIMAL(18,2), happened DATE, account_id INT, ok BOOLEAN, valid_from DATE, "
                "valid_to DATE, is_current BOOLEAN)")
    con.execute(f"INSERT INTO {n['event']} VALUES (5, '2024-12-01', 9, TRUE, '2024-12-01', NULL, TRUE), "
                "(6, '2025-01-02', 1, TRUE, '2025-01-02', NULL, TRUE), (7, '2025-05-31', 1, TRUE, '2025-05-31', NULL, TRUE), "
                "(8, '2025-06-20', 1, FALSE, '2025-06-20', NULL, TRUE)")
    con.execute(f"CREATE TABLE {n['secret']}(amount DECIMAL(18,2), as_of DATE)")
    con.execute(f"INSERT INTO {n['secret']} VALUES (4, '2023-01-31')")
    return con


class Graph:
    """The graph's reads the answer makes: the glossary, the documents that define a term with their units and tiers, the
    contexts, and what the reader may read."""

    def __init__(self, pack, readable, tier="gold"):
        self.pack, self.readable, self.tier = pack, set(readable), tier

    def join_access(self, principal, units):
        return {"allowed": set(units) <= self.readable}

    def definition_documents(self, purpose):
        return {d["id"] for docs in self.pack.documents.values() for d in docs}

    def readable_units(self, principal):
        return set(self.readable)

    def defining_docs(self, term):
        return [{"id": d["id"], "shelf": "certified", "title": d["id"]} for d in self.pack.documents.get(term, [])]

    def certification(self, ids):
        units = {d["id"]: d["unit"] for docs in self.pack.documents.values() for d in docs}
        return {i: {"unit": units[i]} for i in ids}

    def q(self, cypher, params=None):
        glossary = self.pack.glossary
        if "DEFINES" in cypher:
            return [{"term": t, "definition": d, "doc": doc["id"] if doc else None, "unit": doc["unit"] if doc else None,
                     "shelf": "certified", "ai_ready": self.tier != "bronze", "tier": self.tier, "superseded_by": None}
                    for t, (d, _) in glossary.items() for doc in (self.pack.documents.get(t) or [None])]
        if "RESOLVES_TO" in cypher:
            return [{"id": c.id, "term": c.term, "measure": c.measure, "fact": c.fact} for c in self.pack.sem.contexts.values()]
        if "$t" not in cypher:
            return [{"term": t, "definition": d} for t, (d, _) in glossary.items()]
        held = glossary.get((params or {}).get("t"))
        return [{"definition": held[0], "steward": held[1]}] if held else []


def engine(pack, readable=None, ran=None, tier="gold") -> SimpleNamespace:
    """A stand-in for the Engine over the pack: its glossary for the parser, the one-term definitions a question on each
    term gives today (the glossary's, or its document's), and the warehouse, every query through the gate first."""
    n = pack.n
    readable = {n["unit"]} if readable is None else readable
    con = warehouse(n)
    e = SimpleNamespace(term_names=list(pack.glossary), ALIASES={}, UNIT_WORDS={}, PERIODS=pack.sem.d.PERIODS,
                        INTENTS={"definitional": intent_pattern("definitional", ("what is",))},
                        UNIT_LABEL={n["unit"]: n["unit_label"], n["other"]: n["other_label"]},
                        semantics=pack.sem, g=Graph(pack, readable, tier), profile=None, identity={"hash": "h1"})
    pack.sem.d.UNIT_LABEL = e.UNIT_LABEL

    def one_definition(req, principal, purpose, cfg):
        term = req.term
        if term in pack.documents:
            return {"behaviour": "cite_certified", "cite": [d["id"] for d in pack.documents[term]] + ["DOC-RETRIEVED"]}
        definition, steward = pack.glossary[term]
        return {"behaviour": "glossary_definition", "term": term, "definition": definition, "steward": steward,
                "definition_from": "the pack's glossary", "contexts": [c.id for c in pack.sem.contexts.values() if c.term == term]}

    def run_plan(plan, sql):
        problems = check(sql, plan.allow)
        assert problems == [], problems
        (ran if ran is not None else []).append(sql)
        return {"passed": True}, con.execute(sql).fetchall()

    e.one_definition, e.run_plan = one_definition, run_plan
    e.term_readable = lambda term, units: Engine.term_readable(e, term, units)
    return e


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    held = {}
    monkeypatch.setattr(difference, "artifact_products", lambda profile: held["products"])
    return held


def asked(pack, catalogue, question, readable=None, ran=None, e=None, tier="gold"):
    """The answer the engine gives the question, read as the parser reads it and made definitional as Ask makes it."""
    catalogue["products"] = pack.products
    e = e or engine(pack, readable, ran, tier)
    req = difference.request(parse(question, e.term_names, e), e)
    pair = difference.asked(req, e)
    assert pair is not None
    out = difference.answer(e, req, WHO, "analysis", SimpleNamespace(graph=True), pair)
    return {**out, "question": question, "principal": WHO}, e


def question(n):
    return f"What is the difference between {n['frozen']} and {n['live']}?"


def test_both_definitions_with_steward_and_source(catalogue):
    n = NAMES
    out, _ = asked(built(n), catalogue, question(n))
    assert out["behaviour"] == "cite_certified" and out["terms"] == [n["frozen"], n["live"]]
    first, second = out["difference"]["terms"]
    assert (first["definition"], first["steward"], first["source"], first["documents"]) == (
        n["frozen_def"], n["steward_a"], "the pack's glossary", [])
    assert (second["definition"], second["steward"], second["documents"]) == (n["live_def"], n["steward_a"], [n["live_doc"]])
    assert out["cite"] == [n["live_doc"]], "a document the one-term question merely retrieved is not cited as a definition"
    assert out["citations"] == [{"n": 1, "doc": n["live_doc"], "title": n["live_doc"], "why": f"defines {n['live']}"}]
    assert "[1]" in difference.worded(out).splitlines()[1]
    assert [d["term"] for d in out["certified_definitions"]] == [n["frozen"], n["live"]]


@pytest.mark.parametrize("wording", ["How is {frozen} different from {live}?", "{frozen} vs {live} definition",
                                     "What's the difference between {frozen} and {live}?", "Difference between {frozen} and {live}",
                                     "Explain the difference between {frozen} and {live}.", "Definition of {frozen} versus {live}",
                                     "How does {frozen} differ from {live}?"])
def test_each_way_of_asking_is_answered_with_both(wording, catalogue):
    out, _ = asked(built(NAMES), catalogue, wording.format(**NAMES))
    assert out["terms"] == [NAMES["frozen"], NAMES["live"]] and out["difference"]["rows"]


@pytest.mark.parametrize("wording", ["Compare mean {frozen} vs {live}", "{frozen} vs {live} by defined segment",
                                     "{frozen} vs {live}", "Difference between {frozen} and the weather",
                                     "{frozen} vs {live} definition for June 2025"])
def test_a_figures_question_is_never_made_a_definition(wording):
    e = engine(built(NAMES))
    req = parse(wording.format(**NAMES), e.term_names, e)
    assert not difference.request(req, e).definitional


def test_a_term_whose_name_holds_and_is_read_whole(catalogue):
    n = NAMES
    out, _ = asked(built(n), catalogue, f"What is the difference between {n['pnl']} and {n['live']}?")
    assert out["terms"] == [n["pnl"], n["live"]]


def test_the_delta_lists_only_the_rows_that_differ(catalogue):
    n = NAMES
    out, _ = asked(built(n), catalogue, question(n))
    rows = {r["attribute"]: r["values"] for r in out["difference"]["rows"]}
    assert list(rows) == ["source", "filters", "period", "versions"], "currency and unit agree, so they are not shown"
    assert rows["source"] == [n["snap_name"], n["event_name"]]
    assert rows["filters"] == [n["published"], f"{n['eligible']} and {n['not_test']}"]
    assert rows["period"] == ["monthly, as at each snapshot date, over released months only", "daily, by the date each row happened"]
    assert rows["versions"] == [f"{n['accounts_name']} as at {n['as_of_name']}", f"{n['event_name']} as current now"]
    summary = out["difference"]["summary"]
    assert summary.startswith(f"{n['frozen']}: from {n['snap_name']}, ") and f". {n['live']}: from {n['event_name']}, " in summary
    assert n["currency"] not in summary and n["unit_label"] not in summary


def test_a_definer_the_rules_may_not_show_defines_nothing_here(catalogue):
    n = NAMES
    e = engine(built(n), tier="bronze")
    req = difference.request(parse(question(n), e.term_names, e), e)
    catalogue["products"] = built(n).products
    out = difference.answer(e, req, WHO, "analysis", SimpleNamespace(graph=True), difference.asked(req, e))
    assert "rows" not in (out.get("difference") or {}) and n["live_def"] not in json.dumps(out)


def test_the_period_ranges_are_read_once_a_release(catalogue):
    n, ran = NAMES, []
    pack = built(n)
    _, e = asked(pack, catalogue, question(n), ran=ran)
    asked(pack, catalogue, question(n), e=e)
    assert len(ran) == 2
    e.identity = {"hash": "h2"}
    asked(pack, catalogue, question(n), e=e)
    assert len(ran) == 4


def test_compact_drops_the_ids_and_redaction_reaches_every_text(catalogue):
    from kge.services.ask.service import compact_projection
    from kge.services.ask.engine.wording import withheld_all
    out, _ = asked(built(NAMES), catalogue, question(NAMES))
    compact = compact_projection(out)
    assert "difference_ids" not in compact and compact["difference"] == out["difference"]
    assert "ctx_" not in json.dumps(compact["difference"])
    hidden = SimpleNamespace(withheld=lambda text, protected: text.replace(NAMES["event_name"], "(withheld)"))
    redacted = json.dumps(withheld_all(out["difference"], hidden, []))
    assert NAMES["event_name"] not in redacted and "(withheld)" in redacted


def test_agreeing_declarations_show_no_row():
    pair = [difference.Side("a", "A"), difference.Side("b", "B")]
    same = {"values": {"source": "x"}}
    assert difference.summary(pair, [], [same, same]) == "A and B differ in nothing the pack declares beyond their definitions."


def test_each_terms_available_period_range_is_carried(catalogue):
    n, ran = NAMES, []
    out, _ = asked(built(n), catalogue, question(n), ran=ran)
    first, second = (t["period_range"] for t in out["difference"]["terms"])
    assert first == {"first": "2024-03-31", "latest": "2025-06-30", "released_months": True, "words": "March 2024 to June 2025"}, (
        "only released months: July 2025's balance is not released yet")
    assert second == {"first": "2025-01-02", "latest": "2025-05-31", "released_months": False, "words": "2025-01-02 to 2025-05-31"}, (
        "through the context's own filter and row selector: the test account's and the ineligible entry are left out")
    assert len(ran) == 2 and all(s.startswith("SELECT min(") for s in ran)


def test_the_text_has_business_words_and_no_ids(catalogue):
    n = NAMES
    out, _ = asked(built(n), catalogue, question(n))
    text = difference.worded(out)
    for said in (n["frozen_def"], n["live_def"], n["steward_a"], n["snap_name"], n["event_name"], "March 2024 to June 2025",
                 out["difference"]["summary"]):
        assert said in text
    for table in ("snap", "event", "months", "accounts"):
        assert n[table] not in text
    assert not any(said in text for said in ("ctx_", "as_of", "published", "not_test", "eligible", "snap_month", f"{n['unit']}."))
    ids = json.dumps(out["difference_ids"])
    assert f"{n['unit']}.{n['snap']}" in ids and '"published"' in ids and '"not_test"' in ids, "the engineer's view keeps ids"


def test_the_same_pack_renamed_gives_the_same_answer_in_its_own_names(catalogue):
    before, _ = asked(built(NAMES), catalogue, question(NAMES))
    after, _ = asked(built(RENAMED), catalogue, question(RENAMED))
    held = json.dumps(before, sort_keys=True)
    for key in sorted(NAMES, key=lambda k: -len(NAMES[k])):
        held = held.replace(NAMES[key], f"\0{key}\0")
    for key in NAMES:
        held = held.replace(f"\0{key}\0", RENAMED[key])
    assert json.loads(held) == after
    assert not any(NAMES[k] in json.dumps(after) for k in NAMES if NAMES[k] not in json.dumps(RENAMED))


def test_unreadable_contexts_and_filters_are_left_out(catalogue):
    n = NAMES
    out, _ = asked(built(n), catalogue, question(n), readable={n["unit"]})
    assert n["secret_name"] not in json.dumps(out) and "JPY" not in json.dumps(out), "the other unit's context is not shown"
    out, e = asked(built(n), catalogue, question(n), readable={n["unit"], n["other"]})
    assert n["secret_name"] in out["difference"]["terms"][0]["attributes"]["source"], "a reader of both units sees both"


@pytest.mark.parametrize("asking", ["What is the difference between {frozen} and {hidden}?",
                                    "What is the difference between {hidden} and {frozen}?"])
def test_a_term_defined_only_on_an_unreadable_unit_is_not_defined(asking, catalogue):
    n = NAMES
    out, e = asked(built(n), catalogue, asking.format(**n))
    assert out["difference"] == {"terms": [n["frozen"]], "unresolved": [n["hidden"]], "clarification": out["difference"]["clarification"]}
    assert n["hidden_def"] not in json.dumps(out) and n["hidden_doc"] not in json.dumps(out)


def test_one_term_unknown_gives_todays_definition_and_a_clarification(catalogue):
    n = NAMES
    out, e = asked(built(n), catalogue, f"What is the difference between {n['frozen']} and the weather?")
    assert out["term"] == n["frozen"]
    today = e.one_definition(Request(question="", term=n["frozen"]), WHO, "analysis", None)
    assert {k: v for k, v in out.items() if k not in ("difference", "question", "principal")} == today
    assert out["difference"]["unresolved"] == ["the weather"]
    assert out["difference"]["clarification"] == (f"“the weather” names no glossary term you may read, so only {n['frozen']} is "
                                                  f"defined. Which term did you mean to compare {n['frozen']} with?")
    assert difference.worded(out) == out["difference"]["clarification"]


def test_a_question_naming_no_term_or_one_term_twice_is_no_difference_question():
    e = engine(built(NAMES))
    for q in ("What is the difference between apples and pears?", f"What is the difference between {NAMES['frozen']} and {NAMES['frozen']}?",
              f"What is {NAMES['frozen']}?"):
        assert difference.asked(Request(question=q, definitional=True), e) is None


def test_a_period_keeps_a_question_on_figures():
    e = engine(built(NAMES))
    said = f"{NAMES['frozen']} vs {NAMES['live']} definition"
    assert difference.request(Request(question=said), e).definitional
    dated = replace(Request(question=said), period=SimpleNamespace(kind="month", value="2025-06"))
    assert not difference.request(dated, e).definitional


def test_the_engine_answers_a_difference_on_the_graph_alone(monkeypatch):
    calls = []
    e = SimpleNamespace(one_definition=lambda *a: calls.append("one") or {"behaviour": "cite"})
    monkeypatch.setattr(difference, "asked", lambda req, eng: ("a", "b"))
    monkeypatch.setattr(difference, "answer", lambda *a: calls.append("both") or {"behaviour": "cite_certified"})
    Engine.definition_answer(e, Request(question="q"), WHO, "analysis", SimpleNamespace(graph=False))
    Engine.definition_answer(e, Request(question="q"), WHO, "analysis", SimpleNamespace(graph=True))
    assert calls == ["one", "both"]


def test_a_query_the_gate_or_the_warehouse_refused_is_tried_again(catalogue):
    n = NAMES
    _, e = asked(built(n), catalogue, question(n))
    vars(e).pop("_period_ranges")
    good, failed = e.run_plan, [0]

    def failing(plan, sql):
        failed[0] += 1
        return {"passed": False, "failed_at": "execute", "problems": ["busy"]}, None
    e.run_plan = failing
    out, _ = asked(built(n), catalogue, question(n), e=e)
    assert [t["period_range"] for t in out["difference"]["terms"]] == [None, None] and failed[0] == 2
    e.run_plan = good
    out, _ = asked(built(n), catalogue, question(n), e=e)
    assert all(t["period_range"] for t in out["difference"]["terms"])


def test_a_unit_the_reader_may_not_join_reads_no_period(catalogue):
    n, ran = NAMES, []
    pack = built(n)
    e = engine(pack, ran=ran)
    e.g.join_access = lambda principal, units: {"allowed": False}
    out, _ = asked(pack, catalogue, question(n), e=e)
    assert ran == [] and [t["period_range"] for t in out["difference"]["terms"]] == [None, None]


def rebuilt(c: Context, **changes) -> Context:
    """The context with some of its declarations changed."""
    held = {"id": c.id, "label": c.label, "description": c.description, "term": c.term, "measure": c.measure,
            "expression": c.expression, "aggregation": c.aggregation, "grain": c.grain, "fact": c.fact, "values": c.values,
            "filters": c.filters, "selectors": c.selectors, "joins": c.joins, "ytd_calendar": c.ytd_calendar,
            "bindings": c.bindings, "version_dates": c.version_dates}
    return Context(**{**held, **changes})


def test_rules_and_version_dates_on_an_unreadable_unit_are_left_out_ids_included():
    n = NAMES
    pack = built(n)
    secret = f"{n['other']}.{n['secret']}"
    frozen = pack.sem.contexts[f"ctx_{n['snap']}"]
    widened = rebuilt(frozen, filters=(*frozen.filters, Rule("desk_rule", "Desk rows only", f"{n['secret']}.amount > 0", secret)),
                      version_dates={**frozen.version_dates, secret: f"{secret}.as_of"})
    e = engine(pack)
    shown = difference.context_values(e, widened, {n["unit"]}, pack.products)
    held = json.dumps(shown)
    assert "Desk rows only" not in held and "desk_rule" not in held and secret not in held and n["secret_name"] not in held
    both = difference.context_values(e, widened, {n["unit"], n["other"]}, pack.products)
    assert "Desk rows only" in both["values"]["filters"] and secret in both["ids"]["version_dates"]
    assert n["secret_name"] in both["values"]["versions"]


def test_only_released_months_count_whatever_the_filters_say(catalogue):
    n = NAMES
    pack = built(n)
    frozen = pack.sem.contexts[f"ctx_{n['snap']}"]
    pack.sem.contexts[frozen.id] = rebuilt(frozen, filters=())
    out, _ = asked(pack, catalogue, question(n))
    assert out["difference"]["terms"][0]["period_range"]["latest"] == "2025-06-30", "July 2025 is booked, not released"


def test_a_clarification_beside_no_definition_does_not_say_one_is_given(catalogue):
    n = NAMES
    e = engine(built(n))
    e.one_definition = lambda req, principal, purpose, cfg: {"behaviour": "abstain", "reason": "nothing citable"}
    out, _ = asked(built(n), catalogue, f"What is the difference between {n['frozen']} and the weather?", e=e)
    assert out["difference"]["clarification"] == f"“the weather” names no glossary term you may read. Which term did you mean to compare {n['frozen']} with?"


def test_a_timestamp_period_is_read_as_its_date():
    assert difference.day(datetime.datetime(2025, 6, 30, 12)) == datetime.date(2025, 6, 30)
    assert difference.day(datetime.date(2025, 6, 30)) == datetime.date(2025, 6, 30)
    assert difference.day("2025-Q2") is None


def test_nothing_readable_is_said_so_never_that_nothing_differs():
    pair = [difference.Side("a", "A"), difference.Side("b", "B")]
    assert difference.summary(pair, [], [{"values": {}}, {"values": {"source": "x"}}]) == (
        "Nothing you may read declares how A is measured, so only the definitions are compared.")


def test_a_release_driver_a_pin_or_a_versioned_join_on_an_unreadable_unit_is_never_named():
    n = NAMES
    pack = built(n)
    snap, accounts, secret = (f"{n['unit']}.{n['snap']}", f"{n['unit']}.{n['accounts']}", f"{n['other']}.{n['secret']}")
    pack.sem.releases[secret] = {"table": secret, "period": "as_of", "freeze_date": "as_of", "release_date": "as_of", "released": "amount"}
    pack.sem.scd2[secret] = {"table": secret, "is_current": "amount", "valid_from": "as_of", "valid_to": "as_of"}
    desk = Join("desk_month", "calendar_rollup", f"{n['snap']}.as_of = {n['secret']}.as_of", None, None, snap, secret, True)
    crossing = rebuilt(pack.sem.contexts[f"ctx_{n['snap']}"], joins=(desk,), version_dates={accounts: f"{secret}.as_of"})
    e = engine(pack)
    shown = difference.context_values(e, crossing, {n["unit"]}, pack.products)
    held = json.dumps(shown)
    assert shown["ids"]["release_driver"] is None and "released months" not in shown["values"]["period"]
    assert shown["ids"]["version_dates"] == {} and shown["ids"]["current_versions"] == []
    assert secret not in held and n["secret_name"] not in held and n["accounts_name"] not in shown["values"]["versions"]
    both = difference.context_values(e, crossing, {n["unit"], n["other"]}, pack.products)
    assert both["ids"]["release_driver"] == secret and "over released months only" in both["values"]["period"]
    assert both["ids"]["version_dates"] == {accounts: f"{secret}.as_of"} and secret in both["ids"]["current_versions"]
    assert n["accounts_name"] in both["values"]["versions"] and n["secret_name"] in both["values"]["versions"]
