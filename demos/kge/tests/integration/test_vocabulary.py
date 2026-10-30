"""The client's own words are its pack's: the parser's vocabulary, the role mailboxes and the title (#39 item 11).

    .venv/bin/python -m pytest tests/integration/test_vocabulary.py -q

Until item 11, platform code held the last of the client's wording: kge/services/ask/engine/core.py
the question parser's filter phrases and values (FILTER_PATTERNS), the openings
that mark a definitional question (DEFINITIONAL, one of them naming a unit) and
the words that mark a count or an average; kge/pipeline/privacy.py the role
mailboxes; the API and the page the client's name in their titles. Meridian's
pack 1.2.0 holds them in the template's slots (terminology.value_vocabularies,
predicates, intents and functional_mailboxes; the manifest's title). The
compiler checks them (S29) and emits them as meta-model 1.2.0's Vocabulary
nodes, the title on the compiled model's GraphModel node, and the runtime reads
them from the graph, once, as it reads the rest of the client's domain.

The matching algorithms are the ones the code had, on the same arms: on every
golden, held-out and golden_wealth question, and on probes around each phrase
and pattern, the parser finds the filters, the definitional opening and the
aggregate the code found before (the code is kept below, verbatim, as it was at
4739731), each Wealth column under the physical name pack 1.5.0 gives it
(kge/estate/names.py). #39's review round added a value vocabulary for the
source system a Wealth flow is booked on, as written (W06, "NNA from
BROKERAGE"); pack 1.5.0 took it out with Meridian's flow table, whose code it
named, and gives two predicates after the code's four instead, since SAGE's
flows hold the source system's id: BROKERAGE is 100, and "from BANK" or "on
BANK" 200, so the word bank alone is never one. The patterns are a closed set the platform knows, and a phrase is text,
never a regular expression. Certification masks with the pack's mailboxes, so
the landing is what it was; privacy keeps no mailbox of its own, so a client
with none has every address masked. The pipeline keeps a landed finding's
evidence as certification redacted it. The agent's SQL prompt names the
engine's units, the text it was for Meridian's four, and the mock reads them
from it, as a model would.

Opens the demo's graph through the eng fixture (stop the API first); the rest
compiles scratch packs and reads files.
"""
from __future__ import annotations

import json
import re
import shutil
from types import SimpleNamespace

import pytest
import yaml

from kge.services.ask.engine.core import parse
from kge.eval import sets
from tests.scratch import BUILD, CLIENT, DEMO, compile_pack

PACK = DEMO / "clients" / CLIENT / "pack.yaml"
FIXTURES = DEMO / "tests" / "fixtures"

BEFORE_FILTERS = [
    (re.compile(r"\bregion (?:is )?([A-Z]{2,3})\b"), "region_code", "=", 1, None),
    (re.compile(r"\b(TEAM-[A-Z])\b"), "team_code", "=", 1, None),
    (re.compile(r"\bincorporated in ([A-Z]{2})\b"), "country_code", "=", 1, None),
    (re.compile(r"\b([A-Z]{4,}) risk profile\b"), "risk_profile", "=", 1, None),
    (re.compile(r"\b(Everyday Current|Premier Current|Instant Saver|Fixed Saver|Personal Loan|Rewards Card)\b"), "product_name", "=", 1, None),
    (re.compile(r"\brated ([A-C][12])\b"), "internal_grade", "=", 1, None),
    (re.compile(r"(?:\bopen\b.*\bcomplaints?\b|\bcomplaints?\b.*\b(?:open|unresolved)\b|\bunresolved\b.*\bcomplaints?\b)"), "resolved_on", "IS NULL", None, None),
    (re.compile(r"\bescalated\b", re.I), "outcome_code", "=", None, "ESCALATED"),
    (re.compile(r"\bmore than 30 days past due\b", re.I), "days_past_due", ">", None, 30),
    (re.compile(r"\bin covenant breach\b", re.I), "breached_flag", "= TRUE", None, None),
]
BEFORE_DEFINITIONAL = re.compile(r"^(what is|what makes|what does|does a|is total|which calendar|how is|where is|may restricted|which version|can retail|define)\b", re.I)
BEFORE_MAILBOXES = {"records", "info", "support", "privacy", "helpdesk", "noreply", "no-reply", "admin", "enquiries",
                    "complaints", "retention", "dpo", "compliance", "service", "contact"}
PHYSICAL = {"risk_profile": "RISK_PRFL_CD", "outcome_code": "RVW_OUTCM_CD"}
RENAMED = [(rx, PHYSICAL.get(col, col), op, group, literal) for rx, col, op, group, literal in BEFORE_FILTERS]
SINCE = [(re.compile(r"(?<!\w)BROKERAGE(?!\w)", re.I), "ACCT_SRCE_SYS_ID", "=", None, 100),
         (re.compile(r"(?<!\w)(?:from BANK|on BANK)(?!\w)", re.I), "ACCT_SRCE_SYS_ID", "=", None, 200)]
NOW_FILTERS = RENAMED + SINCE


def before(question: str, filters_then: list = BEFORE_FILTERS) -> tuple[list, bool, str]:
    """What the parser found before: (filters, definitional, aggregate); with RENAMED, each Wealth column under its
    physical name, and with NOW_FILTERS, pack 1.5.0's source system predicates too."""
    q = question.strip()
    ql = q.lower()
    filters = []
    for rx, col, op, group, literal in filters_then:
        mm = rx.search(q if group else ql)
        if mm:
            filters.append((col, op, mm.group(group) if group else literal))
    agg = "count" if re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", ql) else "avg" if re.search(r"\baverage\b", ql) else "sum"
    return filters, bool(BEFORE_DEFINITIONAL.match(q)), agg


PROBES = [
    "Revenue in region NW", "Revenue in region is NW", "Revenue in region IS NW", "Revenue in region isX NW", "Revenue in Region NW",
    "Revenue in regions NW", "Revenue in region NWXY", "Revenue in region nw", "region NW and region SE",
    "SME revenue for TEAM-A", "SME revenue for TEAM-AB", "team-a revenue", "revenue for TEAM-Z.",
    "Counterparties incorporated in DE", "incorporated in DEU", "Incorporated in DE", "incorporated in de",
    "Households with a GROWTH risk profile", "GROW risk profile", "growth risk profile", "BALANCED risk profiles",
    "Fees on Everyday Current", "fees on everyday current", "Premier Currents", "Rewards Card and Fixed Saver",
    "Counterparties rated C1", "rated D1", "rated C3", "rated C12", "Rated C1", "unrated A1",
    "open complaints", "complaints still open", "unresolved complaint", "complaint unresolved", "Open Complaints",
    "reopened complaints", "complaints opened", "complaints open in June", "unresolved issues", "an open question",
    "Suitability reviews escalated", "Escalated reviews", "escalation of reviews",
    "Repayments more than 30 days past due", "More Than 30 Days Past Due", "more than 300 days past due",
    "Facilities in covenant breach", "In Covenant Breach", "in covenant breaches",
    "What is revenue?", "what makes a column certified", "  What does it mean", "Does a login count?", "Is total revenue fine",
    "Which calendar applies", "How is it converted", "Where is it", "May restricted data be used", "Which version applies",
    "Can retail customers join", "Can retailers join", "Define revenue", "Defined revenue", "Revenue: what is it?",
    "How many customers", "Number of accounts", "count the matches", "counted", "a recount", "the average balance",
    "averages", "Average balance", "How many on average",
    "NNA from BROKERAGE", "NNA from BANK and BROKERAGE", "NNA from brokerage", "Revenue across the bank", "BANKS", "the Bank",
]


@pytest.fixture(scope="module")
def pack() -> dict:
    return yaml.safe_load(PACK.read_text())


def questions() -> list[str]:
    out = []
    for name in ("golden.json", "heldout.json", "golden_wealth_v1.json"):
        out += [i["question"] for i in sets.items(BUILD / name)]
    return list(dict.fromkeys(out + PROBES))


def test_the_pack_holds_the_parser_s_vocabulary_ported_one_for_one(pack):
    """The vocabulary is 1.2.0's, unchanged since: pack 1.3.0 added AUM's terms (issue #40), and 1.4.0, for meta-model
    1.3.0, the mappings to the platform's preloaded standards; neither added vocabulary, and 1.5.0 renamed Wealth's columns
    physically and read the review round's source system as SAGE's id, two predicates after the code's."""
    t = pack["terminology"]
    cols = [v["column"].rsplit(".", 1)[1] for v in t["value_vocabularies"]] + [p["column"].rsplit(".", 1)[1] for p in t["predicates"]]
    assert cols == [c for _, c, _, _, _ in NOW_FILTERS], "a filter for each the code had, in its order, and the source system's"
    assert not any("source_system" in v["column"] for v in t["value_vocabularies"])
    assert t["predicates"][4:] == [
        {"phrases": ["BROKERAGE"], "column": "wealth.TRAN_FACT_1.ACCT_SRCE_SYS_ID", "op": "eq", "value": 100},
        {"phrases": ["from BANK", "on BANK"], "column": "wealth.TRAN_FACT_1.ACCT_SRCE_SYS_ID", "op": "eq", "value": 200}]
    assert t["value_vocabularies"][4]["values"] == ["Everyday Current", "Premier Current", "Instant Saver", "Fixed Saver",
                                                    "Personal Loan", "Rewards Card"]
    assert {i["intent"]: i["phrases"] for i in t["intents"]} == {
        "definitional": BEFORE_DEFINITIONAL.pattern[2:-3].split("|"), "count": ["how many", "number of", "count"], "avg": ["average"]}
    assert set(t["functional_mailboxes"]) == BEFORE_MAILBOXES and len(t["functional_mailboxes"]) == len(BEFORE_MAILBOXES)
    m = pack["manifest"]
    assert (m["version"], m["meta_model_version"], m["title"]) == ("1.5.0", "1.3.0", "Meridian Union Bank governance graph")


def test_the_graph_holds_the_vocabulary_and_the_compiled_model_the_title(eng, pack):
    kinds = {r["kind"]: r["n"] for r in eng.g.q("MATCH (v:Vocabulary) RETURN v.kind AS kind, count(*) AS n")}
    assert kinds == {"value": 6, "predicate": 6, "intent": 3, "mailbox": 15}
    rows = eng.g.q("MATCH (v:Vocabulary) WHERE v.kind = 'predicate' RETURN v.id AS id, v.params AS params ORDER BY v.ordinal")
    assert [r["id"] for r in rows] == [f"predicate-{n}" for n in range(1, 7)]
    assert json.loads(rows[2]["params"]) == {"column": "sme.repayment.days_past_due", "op": "gt", "phrases": ["more than 30 days past due"],
                                             "value": 30}
    assert eng.g.q("MATCH (m:GraphModel) RETURN m.title AS title") == [{"title": pack["manifest"]["title"]}]


def test_the_engine_holds_them_read_once_from_the_graph(eng, pack):
    assert eng.MAILBOXES == frozenset(BEFORE_MAILBOXES) and eng.TITLE == pack["manifest"]["title"]
    assert [(c, op) for _, c, op, _, _ in eng.FILTERS] == [(c, op) for _, c, op, _, _ in NOW_FILTERS]
    assert set(eng.INTENTS) == {"definitional", "count", "avg"}
    assert eng.RESTRICTED_CITES == {f"{u}.{t}.{c}": "POL-DATA-CLASS" for u, t, c in eng.RESTRICTED_COLUMNS}


@pytest.mark.parametrize("aliases", [True, False], ids=["resolving", "no-resolution"])
def test_the_parser_finds_on_every_question_what_the_code_found(eng, aliases):
    """The same filters, values and order, the same definitional openings and the same aggregate, on every set's questions
    and on the probes: whether the run resolves terms or not, as the code read them on every arm, each Wealth column under
    its physical name. The source system predicates move only the questions that name one: W06 among the sets', and
    three probes."""
    moved, added = [], []
    for q in questions():
        r = parse(q, eng.term_names, eng, aliases=aliases)
        got = (r.filters, r.definitional, r.agg)
        if got != before(q, NOW_FILTERS):
            moved.append(f"{q!r}: {before(q, NOW_FILTERS)} -> {got}")
        if before(q, NOW_FILTERS) != before(q, RENAMED):
            added.append(q)
    assert moved == []
    assert len(questions()) > 150
    assert added == ["NNA from BROKERAGE in FY26-Q1", "NNA from BROKERAGE", "NNA from BANK and BROKERAGE", "NNA from brokerage"], added


def test_a_phrase_is_text_never_a_pattern_and_the_patterns_are_the_platform_s():
    from kge.semantics.vocabulary import INTENTS, OPS, PATTERNS, compiled
    assert set(PATTERNS) == {"upper_2", "upper_2_3", "upper_4_plus", "upper_letter"} and set(INTENTS) == {"definitional", "count", "avg"}
    assert set(OPS) == {"eq", "gt", "lt", "is_null", "is_true"}
    v = compiled([{"kind": "predicate", "params": {"phrases": ["a.b (c)"], "column": "u.t.flag", "op": "is_true"}},
                  {"kind": "value", "params": {"column": "u.t.code", "after": ["code"], "values": ["X+1"]}}])
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, FILTERS=v.filters, INTENTS=v.intents)
    assert parse("rows where a.b (c) holds, code X+1", [], domain).filters == [("code", "=", "X+1"), ("flag", "= TRUE", None)]
    assert parse("rows where aXb (c) holds, code XX1", [], domain).filters == [], "a dot, brackets and a plus are themselves"
    assert parse("What is it", [], domain).definitional is False and parse("How many", [], domain).agg == "sum", \
        "a client with no intents declares none: the platform holds none of its own"


def test_a_pack_s_vocabulary_is_read_with_no_code_change(tmp_path):
    """One more predicate in a scratch copy of the pack, compiled: its Vocabulary row is the parser's next filter."""
    from kge.semantics.vocabulary import compiled
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    path = clients / CLIENT / "pack.yaml"
    p = yaml.safe_load(path.read_text())
    p["terminology"]["predicates"].append({"phrases": ["written off"], "column": "sme.repayment.days_past_due", "op": "gt", "value": 90})
    path.write_text(yaml.safe_dump(p, sort_keys=False, allow_unicode=True))
    art = compile_pack(tmp_path / "artifacts", clients)
    with (art / "data" / "nodes" / "Vocabulary.csv").open() as f:
        import csv
        rows = list(csv.DictReader(f))
    assert len(rows) == 31 and rows[-1]["id"] == "value-6" and json.loads(next(r for r in rows if r["id"] == "predicate-7")["params"])["value"] == 90
    v = compiled([{"kind": r["kind"], "params": json.loads(r["params"])} for r in sorted(rows, key=lambda r: (r["kind"], int(r["ordinal"])))])
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, FILTERS=v.filters, INTENTS=v.intents)
    assert parse("SME repayments written off in 2025", [], domain).filters == [("days_past_due", ">", 90)]


def test_meta_model_1_2_0_only_adds_to_1_1_0():
    """The 1.2.0 fixture is frozen as it was when 1.3.0 added the preloaded standards (tests/unit/test_standards.py holds
    1.3.0 to it)."""
    old, new = (yaml.safe_load((FIXTURES / f"meta-model-{v}.yaml").read_text()) for v in ("1.1.0", "1.2.0"))
    assert (old["version"], new["version"]) == ("1.1.0", "1.2.0")
    for kind in ("node_types", "edge_types"):
        assert list(new[kind])[:len(old[kind])] == list(old[kind])
        for name, t in old[kind].items():
            n = new[kind][name]
            assert {k: v for k, v in n.items() if k != "properties"} == {k: v for k, v in t.items() if k != "properties"}, name
            assert list(n["properties"] or {})[:len(t["properties"] or {})] == list(t["properties"] or {}), name
    assert list(new["node_types"]) == [*old["node_types"], "Vocabulary"] and list(new["edge_types"]) == list(old["edge_types"])
    assert list(new["node_types"]["GraphModel"]["properties"])[-1] == "title"
    assert new["node_types"]["Vocabulary"]["origin"] == "pack" and list(new["node_types"]["Vocabulary"]["properties"]) == [
        "id", "kind", "params", "ordinal"]


def test_the_pack_written_for_1_1_0_compiles_against_today_s_meta_model_with_no_vocabulary(tmp_path):
    clients = tmp_path / "clients"
    (clients / CLIENT).mkdir(parents=True)
    shutil.copyfile(FIXTURES / "meridian-pack-1.1.0.yaml", clients / CLIENT / "pack.yaml")
    lock = json.loads((DEMO / "clients" / CLIENT / "pack.lock").read_text())
    lock["sources"]["wealth"] = json.loads((FIXTURES / "wealth-lock-36bc02f.json").read_text())["wealth"]
    (clients / CLIENT / "pack.lock").write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")
    art = compile_pack(tmp_path / "artifacts", clients)
    man = json.loads((art / "manifest.json").read_text())
    assert (man["pack"]["version"], man["meta_model_version"]) == ("1.1.0", "1.3.0")
    assert man["counts"]["nodes"]["Vocabulary"] == 0 and man["title"] == "Meridian Union Bank governance graph"


def test_privacy_keeps_no_mailbox_of_its_own():
    from kge.pipeline import privacy
    assert not hasattr(privacy, "ROLE_MAILBOXES")
    assert privacy.redact("personal_data:email", "records@example.com") == "r******@example.com", "none given: every address is a person's"
    assert privacy.redact("personal_data:email", "records@example.com", {"records"}) == "records@example.com"
    assert privacy.assess_privacy("personal_data:email", "records@example.com") == "likely_personal"
    assert privacy.assess_privacy("personal_data:email", "records@example.com", {"records"}) == "likely_functional"
    text = "Write to records@example.com or jane.doe@example.com."
    assert privacy.mask(text)[0] == "Write to r******@example.com or j*******@example.com."
    assert privacy.mask(text, role_mailboxes={"records"})[0] == "Write to records@example.com or j*******@example.com."


def test_certification_masks_with_the_pack_s_mailboxes():
    from kge.estate import certify
    assert certify.mailboxes(PACK) == frozenset(BEFORE_MAILBOXES)
    landed = (BUILD / "landing" / CLIENT / "current" / "documents" / "chunks.jsonl").read_text()
    assert "records@example.com" in landed, "the role mailbox lands in clear, as before"


def test_the_pipeline_keeps_landed_evidence_and_redacts_its_own_with_the_client_s_mailboxes():
    from kge.pipeline.tagger import RuleTagger
    landed = [{"family": "privacy", "value": "personal_data:email", "evidence": "records@example.com", "confidence": 0.9}]
    for mailboxes in ((), {"records"}):
        t = RuleTagger(["Revenue"], role_mailboxes=mailboxes)
        got = [p.evidence for p in t.propose("Revenue, from records@example.com", "certified", "policy", findings=landed) if p.family == "privacy"]
        assert got == ["records@example.com"], "as certification landed it"
    own = lambda mailboxes: [p.evidence for p in RuleTagger([], role_mailboxes=mailboxes).propose(  # noqa: E731
        "from records@example.com", "certified", "policy") if p.family == "privacy"]
    assert own({"records"}) == ["records@example.com"] and own(()) == ["r******@example.com"]


def test_the_queue_reads_the_client_s_mailboxes(eng):
    """The steward queue redacts, and assesses an address as likely functional, with the client's mailboxes, the graph's."""
    assert eng.g.mailboxes() == frozenset(BEFORE_MAILBOXES)
    assessed = {r["tag"].split("#")[0]: (r["evidence"], r["assessment"]) for r in eng.g.steward_queue("admin")
                if r["value"] == "personal_data:email"}
    assert assessed["POL-RETENTION"] == ("records@example.com", "likely_functional"), assessed
    assert assessed["POL-COMPLAINTS"][1] == "likely_personal", assessed


OPENING = ("You are a text-to-SQL agent for a bank's four business units: retail, sme, corporate and wealth. Each unit has\n"
           "its own DuckDB warehouse. Answer the analyst's question from the context below and nothing else.\n")


def test_the_sql_prompt_names_the_engine_s_units_and_is_the_text_it_was_for_meridian_s(eng):
    from kge.services.ask.engine.agent import Agent, sql_prompt
    assert sql_prompt(eng.UNIT_ORDER, eng.w.query_dialect).startswith(OPENING)
    assert sql_prompt(["alpha", "beta"], "DuckDB").startswith("You are a text-to-SQL agent for a bank's two business units: alpha and beta. Each")
    assert sql_prompt(["alpha"], "DuckDB").startswith("You are a text-to-SQL agent for a bank's one business unit: alpha. Each")
    agent = Agent(eng, llm=None)
    assert agent.prompt("sql", "Q?", "block").startswith(OPENING)


def test_the_mock_reads_the_units_from_the_prompt():
    from kge.services.ask.engine.agent import sql_prompt
    from kge.adapters.model import mock as mockllm
    assert not hasattr(mockllm, "UNITS")
    assert mockllm.prompt_units(sql_prompt(["retail", "sme", "corporate", "wealth"], "DuckDB")) == ["retail", "sme", "corporate", "wealth"]
    assert mockllm.prompt_units(sql_prompt(["alpha", "beta"], "DuckDB")) == ["alpha", "beta"]
    assert mockllm.prompt_units(sql_prompt(["alpha"], "DuckDB")) == ["alpha"]



def test_a_refusal_to_sum_two_measures_says_their_currencies_differ_only_where_they_do(eng, monkeypatch):
    """The words of a refusal are the graph's facts, not one client's: two measures in one currency are not said to be in
    two (a client whose measures are all in USD)."""
    q = "Total assets under advice plus corporate credit exposure"
    a = eng.answer(q, "analyst_enterprise")
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["corporate-wealth"]
    assert a["reason"] == "Two different measures in different currencies with different meanings. The graph will not sum them."
    held = eng.g.implementations
    monkeypatch.setattr(eng.g, "implementations", lambda t: [{**i, "currency": "USD"} for i in held(t)])
    a = eng.answer(q, "analyst_enterprise")
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["corporate-wealth"]
    assert a["reason"] == "Two different measures with different meanings. The graph will not sum them."

@pytest.fixture()
def served(tmp_path, monkeypatch):
    """The API on a scratch copy of the demo's build, run as its profile."""
    from fastapi.testclient import TestClient

    import kge.app as appmod
    from tests.scratch import profile_of, scratch_build
    build = scratch_build(tmp_path / "build")
    p = profile_of(build)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", p), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        c.profile, c.app_module = p, appmod
        yield c
    appmod._engine.close() if appmod._engine else None


def test_the_api_s_title_and_the_profile_it_serves_are_the_artifact_s(served, pack):
    title = pack["manifest"]["title"]
    got = served.get("/profile").json()
    g = served.app_module.engine().g
    held = g.q("MATCH (m:GraphModel) RETURN m.id AS id")[0]["id"]
    release = g.q("MATCH (r:Release) RETURN r.id AS id, r.as_of AS as_of")[0]
    assert got == {"profile": served.profile.id, "client": CLIENT, "title": title, "units": [s["id"] for s in pack["sources"]],
                   "artifact": held, "release": release, "shell": {"logo": False, "portal_url": None}}, "the landed release the graph was loaded from, which the header names"
    assert served.get("/openapi.json").json()["info"]["title"] == title == served.app_module.app.title


def test_get_golden_gives_the_question_text_of_the_profile_s_golden_sets_and_nothing_more(served):
    """The page shows a few samples first: questions graph, the default arm, answered as expected in the profile's
    baseline, so none is one it abstains on (W11, an account's version on a date; XB5, coverage by office). It marks as
    known misses the six that #40's baseline records graph failing (Demo script, step 2), XB1 and XB2, whose figures
    answer another question, among them; the golden set's 45 all pass, so it marks none there. The SAGE set, sage v1, comes
    last."""
    got = served.get("/golden").json()
    assert [(s["name"], s["version"]) for s in got["sets"]] == [("golden", "v2"), ("golden_wealth", "v1"), ("sage", "v1")], \
        "never the held-out set"
    assert all(set(s) == {"name", "version", "questions", "samples", "misses"} for s in got["sets"])
    for s, name in zip(got["sets"], ("golden.json", "golden_wealth_v1.json", "sage_v1.json")):
        assert s["questions"] == list(dict.fromkeys(i["question"] for i in sets.items(BUILD / name))), name
        assert s["samples"] and [q for q in s["questions"] if q in s["samples"]] == s["samples"], "the questions, in their order"
    wealth = {i["id"]: i["question"] for i in sets.items(BUILD / "golden_wealth_v1.json")}
    samples = got["sets"][1]["samples"]
    assert wealth["XB3"] in samples and wealth["W13"] in samples and wealth["XB6"] in samples
    assert not {wealth[i] for i in ("W11", "XB5", "XB1", "XB4")} & set(samples)
    assert wealth["W16"] in samples and wealth["W17"] in samples, "AUM, released by #40's pack edit (Demo script, step 6)"
    misses = ("W08", "W11", "XB1", "XB2", "XB4", "XB5")
    assert got["sets"][1]["misses"] == [q for q in got["sets"][1]["questions"] if q in {wealth[i] for i in misses}]
    assert got["sets"][0]["misses"] == []
    assert got["sets"][2]["samples"] == got["sets"][2]["questions"] and got["sets"][2]["misses"] == [], \
        "sage v1, which #40's baseline did not run, offers every question and marks none"
    assert all(isinstance(q, str) for s in got["sets"] for q in s["questions"])
    held_out = {i["question"] for i in sets.items(BUILD / "heldout.json")}
    assert not held_out & {q for s in got["sets"] for q in s["questions"]}
    text = json.dumps(got)
    assert not re.search(r"expect|reference_sql|SELECT|13287|figure", text), "question text only: no expectation, figure or SQL"
