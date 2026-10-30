"""The Meridian pack: kge/estate/spec.py reads it, and its values are those before issue #39.

    .venv/bin/python -m pytest tests/unit/test_spec_port.py -q

clients/meridian/pack.yaml holds the client's own data as plain YAML: its
sources, no-join facts, glossary, the parser's phrases, calendars and period
bindings, measures, mappings, restricted columns, role templates, and the
names of its question sets. deploy/meridian/identities.yaml binds the
deployment's principals to the pack's roles. kge/estate/spec.py reads both and
gives the estate the names and values it always had, which are frozen from
4d9d5c9, the merge of #38, in tests/fixtures/spec_4d9d5c9.json: each value as
JSON keeps it, in its order, so a failure diffs readably. Only a change the
owner stated may move one (STATED below), and each is named there.

The pack is also held to the code it was ported from: the parser's aliases and
unit words as kge/services/ask/engine/core.py spelled them at 4d9d5c9 (frozen below: the runtime now
reads them from the graph, tests/integration/test_domain.py), the period column the name
heuristic finds (Engine.period_by_name, which the arms without the graph context
keep), the loader's DEFINES rule, the stewards and date generate.py certifies
mappings with, and the harness's sets.

Reads files only; opens no graph.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
import yaml

from kge.services.ask.engine.core import Engine, Period
from kge.estate import names, spec

DEMO = Path(__file__).resolve().parents[2]
PACK = DEMO / "clients" / "meridian" / "pack.yaml"
IDENTITIES = DEMO / "deploy" / "meridian" / "identities.yaml"
FROZEN = json.loads((DEMO / "tests" / "fixtures" / "spec_4d9d5c9.json").read_text())
SECTIONS = {"manifest", "schema", "sources", "no_join", "glossary", "terminology", "calendars", "period_bindings",
            "measures", "mappings", "policies", "access", "tests"}
SECTIONS_7C = {"curated_sources", "certified_joins", "not_joinable", "acronyms", "entities", "relations", "lineage", "filters",
               "row_selectors", "joins", "contexts"}

TRANSACTION_7B = ["counterparty_bank", "counterparty_account"]


def physical(value):
    """A value with each of Wealth's kept tables and its columns under the physical names they took in pack 1.5.0
    (kge/estate/names.py): a [wealth, table, column] triple, wherever it stands."""
    if isinstance(value, list):
        if len(value) >= 3 and value[0] == "wealth" and value[1] in names.MERIDIAN and value[2] in names.MERIDIAN[value[1]][1]:
            table, columns = names.MERIDIAN[value[1]]
            return ["wealth", table, columns[value[2]], *value[3:]]
        return [physical(x) for x in value]
    if isinstance(value, dict):
        return {k: physical(x) for k, x in value.items()}
    return value


def mapped(rows: list) -> list:
    """The certified mappings, [term, unit, table, column], Wealth's under their physical names."""
    return [[term, *physical(rest)] for term, *rest in rows]


def wealth_expansion(schemas: dict) -> dict:
    """4d9d5c9's SCHEMAS with the expansion in and nothing else moved: Wealth is its fifteen Meridian tables, each under
    the physical names kge/estate/names.py gives it (4d9d5c9's eight among them, their columns and keys as they were,
    renamed), and SAGE's seventeen, as spec.py has them; and the counterparty columns after retail.transaction's. What
    they hold is tests/integration/test_estate_wealth.py's."""
    out = json.loads(json.dumps(schemas))
    wealth = plain(spec.SCHEMAS["wealth"])
    for business, table in schemas["wealth"].items():
        assert wealth[names.table(business)] == plain(names.physical_schemas({business: table})[names.table(business)]), business
    assert set(wealth) == set(names.tables().values())
    out["wealth"] = wealth
    out["retail"]["transaction"]["columns"] += [plain(c) for c in spec.SCHEMAS["retail"]["transaction"]["columns"] if c[0] in TRANSACTION_7B]
    return out


def sage_restricted(columns: list) -> list:
    added = {
        "ACCT_DIM2_1": ["PRIM_TXPYR_ID", "TXPYR_CUST_ID", "TXPYR_CORP_CUST_ID", "ACCT_NM", "POSTL_CD"],
        "BRCH_DIM_1": ["EMAIL_ADDR_TX", "ADDR_LINE1_TX", "ADDR_LINE2_TX"],
        "BANK_DEP_ACCT_ADDR_1": ["MAIL_ADDR_LINE_1_TX", "MAIL_ADDR_LINE_2_TX", "MAIL_ADDR_LINE_3_TX",
                                 "MAIL_ADDR_LINE_4_TX", "MAIL_CITY_NM", "MAIL_POSTL_CD"],
        "LGCY_ACCT_1": ["TXPYR_ID", "TXPYR_BIRTH_DATE_TX", "POSTL_CD", "POSTL_PLUS_4_CD"],
    }
    return [*columns, *(["wealth", table, column] for table, cols in added.items() for column in cols)]


GLOSSARY_7C = ["Net New Assets", "Client-Directed NNA", "Test Account", "NNA Eligibility", "Exclusion", "Month-End Release / Freeze",
               "Source System", "Division"]


def glossary_additions(glossary: list) -> list:
    """The glossary's Wealth additions (the #39 addendum), appended in pack 1.1.0 (#39 item 7c); AUM came after them, in
    pack 1.3.0 (issue #40's pack-only onboarding test), and is stated on its own."""
    pack = yaml.safe_load(PACK.read_text())
    return [*glossary, *({"term": g["name"], "domain": g["domain"], "steward": g["steward"], "definition": g["definition"]}
                         for g in pack["glossary"] if g["name"] in GLOSSARY_7C)]


def certified_join(facts: list) -> list:
    """retail-wealth's no-join fact is replaced by a certified join fact (tests/unit/test_query_semantics.py); the other five
    stay as they were."""
    return [f for f in facts if {f["a"], f["b"]} != {"retail", "wealth"}]


def nna_measure(metadata: list) -> list:
    return [*metadata, [["wealth", "TRAN_FACT_1", "TRAN_AM"], {"grain": "day", "currency": "USD", "calendar": "fiscal_april", "conversion": "none"}]]


def aum_term(glossary: list) -> list:
    pack = yaml.safe_load(PACK.read_text())
    return [*glossary, *({"term": g["name"], "domain": g["domain"], "steward": g["steward"], "definition": g["definition"]}
                         for g in pack["glossary"] if g["id"] == "assets-under-management")]


def aum_measure(metadata: list) -> list:
    return [*metadata, [["wealth", "AUM_MNTH_END_FACT_1", "AUM_AM"],
                        {"grain": "quarter_end", "currency": "USD", "calendar": "fiscal_april", "conversion": "none"}]]


def business_principal(principals: list) -> list:
    """Issue #52's business-team deployment identity, after the glossary steward as identities.yaml declares it."""
    added = {"principal": "business_team", "role": "Business team", "reads": ["retail", "sme", "corporate", "wealth"],
             "writes": ["mappings", "terms"], "processes": ["querying"]}
    return [*principals[:4], added, *principals[4:]]


def engineering_principals(principals: list) -> list:
    return [*principals, {"principal": "knowledge_engineer", "role": "Knowledge engineer",
                          "reads": ["retail", "sme", "corporate", "wealth"],
                          "processes": ["drafting", "building", "improving", "querying"]}]


def operational_processes(processes: dict) -> dict:
    """The four platform operations added alongside the three unchanged model constants."""
    return {**processes,
            "drafting": "Platform drafting: /onboard/tables, /onboard/source, /onboard/rows, /onboard/draft",
            "building": "Platform building: /onboard/run, /onboard/margin",
            "improving": "Platform improving: /onboard/run",
            "accepting": "Platform accepting: /onboard/accept"}


STATED: list[tuple[str, str, Callable[[object], object]]] = [
    ("PROCESSES", "PR #127: drafting, building, improving and accepting extend the model's three processes", operational_processes),
    ("SCHEMAS", "the Wealth expansion and SAGE reference: Wealth tables and SAGE columns appended, and "
                "counterparty columns appended to retail.transaction", wealth_expansion),
    ("RESTRICTED_COLUMNS", "SAGE names, taxpayer identifiers, addresses, and dates of birth are governed personal data",
     sage_restricted),
    ("GLOSSARY", "the Wealth expansion's glossary additions (#39 addendum), appended in pack 1.1.0", glossary_additions),
    ("NO_JOIN_FACTS", "Option B (the #39 update): retail-wealth's no-join fact replaced by a certified join fact, the other "
                      "pairs keeping theirs", certified_join),
    ("MEASURE_METADATA", "net new assets, the measure the NNA contexts specialise (#39 query-semantics addendum), appended",
     nna_measure),
    ("GLOSSARY", "assets under management, issue #40's pack-only onboarding test (the plan's Phase 3), appended in pack 1.3.0",
     aum_term),
    ("MEASURE_METADATA", "assets under management's measure, issue #40's pack-only onboarding test, appended in pack 1.3.0",
     aum_measure),
    ("PRINCIPALS", "issue #52's business-team principal, whose role writes terms and mappings but cannot certify",
     business_principal),
    ("PRINCIPALS", "issue #126 adds the knowledge engineer with operational process grants",
     engineering_principals),
    ("CERTIFIED_MAPPINGS", "pack 1.5.0: Wealth's kept tables and columns under physical names in SAGE's style (kge/estate/names.py)",
     mapped),
    ("MEASURE_METADATA", "pack 1.5.0: Wealth's kept tables and columns under physical names in SAGE's style", physical),
    ("RESTRICTED_COLUMNS", "pack 1.5.0: Wealth's kept tables and columns under physical names in SAGE's style", physical),
    ("UNIT_KEY", "pack 1.5.0: Wealth's household key under its physical name, HSHLD_DK, as ACCT_DIM2_1 keys it",
     lambda keys: {**keys, "wealth": names.column("household", keys["wealth"])}),
]
ALIASES_4D9D5C9 = {
    "revenue": "Revenue", "net interest income": "Net Interest Income", "fee income": "Fee Income",
    "active customer": "Active Customer", "active customers": "Active Customer", "active clients": "Active Customer",
    "active in": "Active Customer", "assets under advice": "Assets Under Advice", "credit exposure": "Credit Exposure",
    "delinquency rate": "Delinquency Rate", "attrition": "Customer Attrition", "average ledger balance": "Average Balance",
    "average balance": "Average Balance", "complaints": "Complaint", "covenant breach": "Covenant Breach",
    "past due": "Days Past Due", "rated": "Internal Rating", "suitability reviews": "Suitability Review",
    "customer active": "Active Customer", "converted": "Period-End FX Rate", "calendar does": "Fiscal Year",
    "digital engagement score": "__candidate__:Digital Engagement Score",
}
UNIT_WORDS_4D9D5C9 = {u: u for u in ("retail", "sme", "corporate", "wealth")}


def plain(v):
    """A spec value as JSON keeps it: tuples as lists, a dict keyed by tuples as [key, value] pairs, every order kept."""
    if isinstance(v, dict):
        if any(isinstance(k, tuple) for k in v):
            return [[list(k), plain(x)] for k, x in v.items()]
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return v


def dumps(v) -> str:
    return json.dumps(v, indent=1, ensure_ascii=False)


def expected(name: str):
    value = FROZEN[name]
    for n, _, change in STATED:
        if n == name:
            value = change(value)
    return value


@pytest.fixture(scope="module")
def pack() -> dict:
    return yaml.safe_load(PACK.read_text())


@pytest.mark.parametrize("name", list(FROZEN))
def test_spec_gives_the_values_it_gave_at_4d9d5c9(name):
    """Order included: the generator writes the build's curated files in these orders."""
    assert dumps(plain(getattr(spec, name))) == dumps(expected(name)), f"spec.{name} moved from 4d9d5c9"


def test_each_stated_change_names_a_value_and_moves_it():
    for name, why, change in STATED:
        assert name in FROZEN and why, name
        assert dumps(change(FROZEN[name])) != dumps(FROZEN[name]), f"{name}: the stated change ({why}) changes nothing"


def test_spec_reads_the_pack_and_the_identities_and_holds_none_of_their_data():
    """It is a reader: no term, steward, note or principal is spelled in it. SCHEMAS stays literal, the simulated
    client's warehouse DDL."""
    src = (DEMO / "kge" / "estate" / "spec.py").read_text()
    for literal in ("Group Finance Data Steward", "Data Architecture Board", "Net Interest Income", "analyst_retail",
                    "POL-DATA-CLASS", "Retail Banking", "supervision"):
        assert literal not in src, literal
    assert "SCHEMAS: dict" in src and "\"monthly_revenue\": {" in src


def test_spec_follows_an_edit_to_the_pack(tmp_path, pack):
    """make estate builds from the pack: spec.read takes whatever pack and identities it is given."""
    edited = json.loads(json.dumps(pack))
    edited["glossary"][0]["definition"] = "An edited definition."
    edited["access"]["roles"][0]["reads"] = ["retail", "sme"]
    p = tmp_path / "pack.yaml"
    p.write_text(yaml.safe_dump(edited, sort_keys=False))
    ids = yaml.safe_load(IDENTITIES.read_text())
    ids["principals"] = ids["principals"][:2]
    i = tmp_path / "identities.yaml"
    i.write_text(yaml.safe_dump(ids, sort_keys=False))
    got = spec.read(pack=p, identities=i)
    assert got["GLOSSARY"][0]["definition"] == "An edited definition."
    assert [x["principal"] for x in got["PRINCIPALS"]] == ["analyst_retail", "analyst_enterprise"]
    assert got["PRINCIPALS"][0]["reads"] == ["retail", "sme"]
    assert spec.read() == spec.read(pack=PACK, identities=IDENTITIES)


def scalars(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from scalars(x)
    elif isinstance(v, list):
        for x in v:
            yield from scalars(x)
    else:
        yield v


def test_the_pack_is_plain_yaml_holding_its_sections(pack):
    """Strings, whole numbers and booleans only: a date is quoted, so it reads back as the string it was. The one place a
    share is written, the post-load gates (#40), holds numbers from 0 to 1. Beside the sections above are pack 1.1.0's
    query-semantics sections (#39 item 7c, held by tests/unit/test_query_semantics.py) and pack 1.4.0's mappings to the
    preloaded standards; 1.5.0 adds the SAGE wealth data's acronyms, joins and restricted columns to those sections."""
    assert set(pack) == SECTIONS | SECTIONS_7C | {"ontology_mappings"}
    shares = pack["manifest"]["gates"]
    rest = {**pack, "manifest": {k: v for k, v in pack["manifest"].items() if k != "gates"}}
    assert all(isinstance(s, (str, bool, int)) for s in scalars(rest)), "an unquoted date or a float in the pack"
    assert shares == {"golden_min_pass": {"within": 1.0, "cross": 1.0, "shelf": 1.0, "abstain": 1.0}, "control_margin": {"cross": 0.5, "shelf": 0.5}}
    m = pack["manifest"]
    assert (m["client"], m["meta_model_version"], m["id"], m["version"]) == ("meridian", "1.3.0", "meridian-governance", "1.5.0")
    assert m["title"] and m["owners"] and m["platform"] == {"meta_model": "^1.3", "compiler": ">=0.4"}
    assert m["compile"] == {"store": "kuzu", "embedding_dim": 128, "id_scheme": "v1"}
    assert pack["schema"] == {"meta_model": "1.3", "extensions": []}
    for s in pack["sources"]:
        assert set(s) == {"id", "label", "entity_key", "connection", "harvest"} and s["harvest"] is True, s["id"]
        assert s["connection"] == {"dialect": "duckdb", "ref": f"warehouses/{s['id']}.duckdb"}
    assert [len(pack[k]) for k in ("no_join", "glossary", "period_bindings", "measures", "mappings")] == [5, 49, 33, 12, 24]
    for g in pack["glossary"]:
        assert set(g) - {"defined_by"} == {"id", "name", "domain", "steward", "definition", "status"} and g["status"] == "certified"
        assert g["id"] == re.sub(r"[^a-z0-9]+", "-", g["name"].lower()).strip("-")
    assert len(pack["terminology"]["aliases"]) == 49 + len(names.columns()) and len(pack["calendars"]) == 2
    assert [len(pack["terminology"][k]) for k in ("value_vocabularies", "predicates", "intents", "functional_mailboxes")] == [6, 6, 3, 15], \
        "pack 1.2.0's: the rest of the parser's vocabulary (the review round's source system is two predicates on SAGE's id since 1.5.0)"
    (restricted,) = pack["policies"]["restricted_attributes"]
    assert (restricted["action"], restricted["cite"], len(restricted["columns"])) == ("refuse", "POL-DATA-CLASS", 25)
    assert restricted["version"] == "1.2.0", "1.0.0's columns, and since #39's review round the phrases people use for them"
    assert [x["column"] for x in restricted["phrases"]] == ["retail.customer.date_of_birth", "retail.customer.email", "retail.customer.first_name"]
    assert len(pack["access"]["roles"]) == 10 and pack["access"]["purposes"] == ["analysis", "assistant", "supervision"]


def test_the_embedding_dimension_and_the_meta_model_version_are_the_platform_s(pack):
    from kge.graph.schema import meta_model
    from kge.adapters.model import DIM
    assert pack["manifest"]["compile"]["embedding_dim"] == DIM
    assert pack["manifest"]["meta_model_version"] == meta_model()["version"]


def test_the_pack_s_aliases_are_the_parser_s(pack):
    """The 22 aliases in their order (Digital Engagement Score a candidate, not a term) and the 4 unit words; pack
    1.1.0's follow them (tests/unit/test_query_semantics.py)."""
    name = {g["id"]: g["name"] for g in pack["glossary"]}
    terms, units = {}, {}
    for a in pack["terminology"]["aliases"][:26]:
        (kind,) = set(a) - {"phrase"}
        if kind == "source":
            units[a["phrase"]] = a["source"]
        else:
            terms[a["phrase"]] = name[a["term"]] if kind == "term" else f"__candidate__:{a['candidate']}"
    assert list(terms.items()) == list(ALIASES_4D9D5C9.items())
    assert units == UNIT_WORDS_4D9D5C9 and list(units.values()) == [s["id"] for s in pack["sources"]]


def period_clause(unit: str, table: str, kind: str, value: str):
    """What the name heuristic does with a period on a table, its columns as 4d9d5c9's warehouse DDL had them (Wealth's
    kept tables under their business names, which were their names then)."""
    cols = [c[0] for c in FROZEN["SCHEMAS"][unit][table]["columns"]]
    stub = SimpleNamespace(g=SimpleNamespace(table_columns=lambda u, t: cols))
    return Engine.period_by_name(stub, unit, table, Period(kind, value))


def business(table: str) -> str:
    """A bound table as 4d9d5c9 named it: a Wealth table under its business name (kge/estate/names.py)."""
    unit, name = table.split(".", 1)
    back = {p: b for b, (p, _) in names.MERIDIAN.items()}
    return f"{unit}.{back.get(name, name)}" if unit == "wealth" else table


def logical(b: dict) -> dict:
    """A binding with its tables and columns as 4d9d5c9 named them."""
    def column(table: str, col: str) -> str:
        unit, name = business(table).split(".", 1)
        return {p: c for c, p in names.MERIDIAN[name][1].items()}.get(col, col) if unit == "wealth" and name in names.MERIDIAN else col
    out = {**b, "table": business(b["table"]), "column": column(b["table"], b["column"])}
    if b.get("rollup"):
        out["rollup"] = {"table": business(b["rollup"]["table"]), "column": column(b["rollup"]["table"], b["rollup"]["column"])}
    return out


def test_the_period_bindings_are_the_columns_period_clause_finds_by_name(pack):
    """For each of the 35 tables of 4d9d5c9: the column, its kind and its rollup are what the name heuristic uses, and a
    table with no binding is one it finds no period column on. Including corporate.deal, on maturity_on, the first of its
    dates. (tests/integration/test_domain.py holds the graph context's clause, read from the bindings, to the heuristic's.) The
    Wealth expansion's tables (item 7b) have none, but wealth.flow, the fact of the net new assets measure (7c), on its
    flow date, and wealth.aum_month_end, assets under management's (pack 1.3.0, issue #40), on its month end: their time
    semantics are the pack's policies and joins (tests/unit/test_query_semantics.py)."""
    calendars = {c["id"]: c for c in pack["calendars"]}
    bound = {b["table"]: b for b in pack["period_bindings"]}
    assert bound.pop("wealth.TRAN_FACT_1") == {"table": "wealth.TRAN_FACT_1", "column": "DW_TRAN_DT", "kind": "event_date",
                                               "calendar": "calendar"}
    assert bound.pop("wealth.AUM_MNTH_END_FACT_1") == {"table": "wealth.AUM_MNTH_END_FACT_1", "column": "CLDR_EOM_DT", "kind": "snapshot",
                                                       "calendar": "calendar"}
    bound = {business(k): logical(b) for k, b in bound.items()}
    assert set(bound) <= {f"{u}.{t}" for u in spec.UNITS for t in FROZEN["SCHEMAS"][u]}
    for unit in spec.UNITS:
        for t in FROZEN["SCHEMAS"][unit]:
            b = bound.get(f"{unit}.{t}")
            if b is None:
                assert period_clause(unit, t, "year", "2025") == ("", [], "no period column; period ignored"), f"{unit}.{t}"
                continue
            c, kind, roll = b["column"], b["kind"], b.get("rollup")
            if roll:
                source, roll = roll["table"].split(".")[0], {**roll, "table": roll["table"].split(".", 1)[1]}
                assert source == unit, b
            assert b["calendar"] in calendars and (b["calendar"] == "fiscal_april") == (kind == "fiscal_quarter"), b
            if kind == "month":
                assert period_clause(unit, t, "month", "2025-06-01") == (f"{t}.{c} = DATE '2025-06-01'", [], None)
                where, joins, _ = period_clause(unit, t, "calendar_quarter", "2025-Q2")
                assert joins == [f"JOIN {roll['table']} ON {t}.{c} = {roll['table']}.{c}"] and where.startswith(f"{roll['table']}.{roll['column']} = ")
            elif kind == "fiscal_quarter":
                assert period_clause(unit, t, "fiscal_quarter", "FY26-Q1") == (f"{t}.{c} = 'FY26-Q1'", [], None)
                where, joins, _ = period_clause(unit, t, "year", "2025")
                assert joins == [f"JOIN {roll['table']} ON {t}.{c} = {roll['table']}.{c}"] and where.startswith(f"{roll['table']}.{roll['column']} = ")
            elif kind == "snapshot":
                assert period_clause(unit, t, "as_of", "2025-06-30") == (f"{t}.{c} = DATE '2025-06-30'", [], None) and roll is None
            else:
                assert kind == "event_date" and roll is None, b
                assert period_clause(unit, t, "year", "2025")[0] == f"{t}.{c} >= DATE '2025-01-01' AND {t}.{c} < DATE '2026-01-01'"
    assert bound["corporate.deal"]["column"] == "maturity_on"
    assert calendars == {"calendar": {"id": "calendar", "year_start_month": 1, "quarter_label": "{yyyy}-Q{n}"},
                         "fiscal_april": {"id": "fiscal_april", "year_start_month": 4, "quarter_label": "FY{yy}-Q{n}"}}


def test_defined_by_gives_the_loader_s_twelve_defines_edges(pack):
    """The per-row loader (kge/graph/legacy.py, the parity oracle) links a certified metric definition to the term its id spells
    (MD-<NAME>, a -V1 too); the artifact's DEFINES rows are the pack's defined_by."""
    from kge.estate.documents import all_documents
    by_id = {f"MD-{g['term'].upper().replace(' ', '-')}": g["term"] for g in spec.GLOSSARY}
    today = {(d.doc_id, by_id.get(d.doc_id) or by_id.get(d.doc_id.replace("-V1", ""))) for d in all_documents(7)
             if d.shelf == "certified" and d.kind == "metric_definition"}
    today.discard((None, None))
    ported = {(doc, g["name"]) for g in pack["glossary"] for doc in g.get("defined_by", [])}
    assert len(today) == 12 and ported == today


def test_measures_and_mappings_are_the_certified_ones_as_generate_fills_them(pack):
    """A mapping is certified by its term's steward on 2024-11-15; each of the ten ported measures is a sum over a
    certified column of its term. Net new assets, pack 1.1.0's, is certified through its contexts and their golden
    questions, and its column maps to no term: no arm finds it by a mapping until a context resolves it. Assets under
    management, pack 1.3.0's (issue #40's pack-only test), likewise, through its context."""
    term = {g["id"]: g for g in pack["glossary"]}
    mapped = {m["column"]: m["term"] for m in pack["mappings"]}
    for m in pack["mappings"]:
        assert (m["certified"], m["certified_by"], m["certified_on"]) == (True, term[m["term"]]["steward"], "2024-11-15"), m
    calendars = {c["id"] for c in pack["calendars"]}
    *ported, nna, aum = pack["measures"]
    assert (nna["id"], nna["column"]) == ("wealth.net-new-assets", "wealth.TRAN_FACT_1.TRAN_AM") and nna["column"] not in mapped
    assert (aum["id"], aum["column"]) == ("wealth.assets-under-management", "wealth.AUM_MNTH_END_FACT_1.AUM_AM") and aum["column"] not in mapped
    for x in ported:
        source, table, col = x["column"].split(".")
        assert mapped[x["column"]] == x["term"] and x["id"] == f"{source}.{x['term']}", x
        assert (x["expression"], x["aggregation"]) == (f"sum({table}.{col})", "sum") and x["calendar"] in calendars


def test_the_identities_bind_the_deployment_s_principals_to_the_pack_s_roles(pack):
    """The deployment owns identities, the pack role templates and the platform the processes (pack-contract 4.10)."""
    from kge.graph.schema import meta_model
    from kge.pipeline.access import process_vocabulary
    ids = yaml.safe_load(IDENTITIES.read_text())
    assert ids["client"] == pack["manifest"]["client"] and all(set(p) == {"principal", "role"} for p in ids["principals"])
    roles = {r["id"]: r for r in pack["access"]["roles"]}
    assert {p["role"] for p in ids["principals"]} == set(roles)
    sources = {s["id"] for s in pack["sources"]}
    processes = set(process_vocabulary(meta_model()["node_types"]["Process"]["constants"]))
    for r in roles.values():
        assert r["reads"] == "all" or set(r["reads"]) <= sources, r
        assert set(r.get("writes", [])) <= {"terms", "mappings"} and set(r["processes"]) <= processes, r


def test_the_pack_names_the_versioned_sets_the_harness_runs(pack):
    """Named, never restated (the owner's Question 6): the sets stay files the baselines are compared on. Pack 1.1.0
    names golden_wealth v1 too, by its items' ids alone, which its contexts' golden questions are among; its file,
    build/golden_wealth_v1.json, is kge/estate/golden_wealth.py's (#39 item 7e), and the harness runs it under all as a
    further set the build holds, the platform naming no client's set."""
    from kge.eval.harness import SETS, set_version
    named = {s["name"]: s["version"] for s in pack["tests"]["sets"]}
    assert {k: named[k] for k in SETS} == SETS and named["golden_wealth"] == "v1" == set_version("golden_wealth_v1")
    assert [set(s) for s in pack["tests"]["sets"]] == [{"name", "version"}, {"name", "version"}, {"name", "version", "items"}]
    assert set(pack["tests"]) == {"sets"}


def test_pyyaml_is_a_requirement():
    req = (DEMO / "requirements.txt").read_text()
    assert re.search(r"^PyYAML>=6\.0,<7\b", req, re.M)


def test_the_readme_and_the_makefile_say_where_the_client_s_data_is():
    readme, make = (DEMO / "README.md").read_text(), (DEMO / "Makefile").read_text()
    for needle in ("clients/meridian/pack.yaml", "deploy/meridian/identities.yaml", "meta-model.yaml", "tests/unit/test_spec_port.py",
                   "`kge/estate/spec.py` reads the pack and the identities"):
        assert needle in readme, needle
    assert "clients/meridian/pack.yaml" in make and "deploy/<client>/identities.yaml" in make
