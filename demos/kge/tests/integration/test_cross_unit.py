"""Option B in the runtime: Retail and Wealth join through the certified client cross-reference (#39 item 7d).

    .venv/bin/python -m pytest tests/integration/test_cross_unit.py -q

Everywhere the runtime shows a join between units:

  joins_refused  lists only the pairs a no-join fact keeps apart: G021 and G030
                 drop retail-wealth, the owner's stated change, which make parity
                 names (clients/meridian/baselines/parity-stated.json); G024, G028,
                 G029 and XB6 are still refused
  which ... also a question that asks which of one unit's customers the other
                 has too counts them through the certified join; one across a
                 pair with a no-join fact is refused as before
  /ask           a customer group across Retail and Wealth is built within one
                 unit, as before, and no longer said to have no certified join
  /map           a term's map lists the five no-join facts and the certified join

Access: a Retail and Wealth result goes only to a principal who reads both
units, with no partial figure (the owner's cross-unit questions XB1 to XB5, as
analyst_retail and advisor_wealth, are refused; the five principals who read
both answer XB1; XB5 is asked at the threshold owner question 2's default gives
it, so that it has an answer on the generated data), and the cross-reference is read by no one on its own. The
units are read together only in a session that attaches them, read-only, once
access has passed. A question across the certified join that asks for
restricted personal data by name (the owner's "any cross-BU question asking for
email or date_of_birth"; the names are the graph's restricted columns) is
refused under the policy that governs the column, with no figure and no
warehouse queried. Those questions are worded so that the parser's
restricted-lookup phrases (email address, date of birth) do not catch them first.

The gate: a context's query may use the certified join only with its
certification filter and its window; a key-name join, a date of birth as a
match key and a table the session does not hold are refused.

The steward queue: certification lands the matches it did not certify (proposed,
or below its confidence floor) as ids, methods, confidences and upstream
statuses only; the loader makes each a Tag of family xref_match, its evidence
saying whether upstream proposed or rejected it; a steward who reads both units
sees and decides them, and nothing is written back.

Opens the demo's graph; the steward's decision runs on a scratch build. Stop the API first.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.core import Engine, Period
from kge.semantics.query import Asked
from kge.pipeline.sqlguard import gate
from kge.eval import sets
from kge.landing.zone import Zone
from tests.scratch import BUILD, CLIENT, DEMO, profile_of, scratch_build

W = BUILD / "warehouses"
GOLDEN = {i["id"]: i for i in sets.items(BUILD / "golden.json")}
XB3 = "NNA in FY26-Q1 from clients who also bank with Retail"
XB = {"XB1": "How many clients hold both retail deposits and advisory AUM at 31 Mar 2025?",
      "XB2": "For dual clients, deposits vs AUM and AUM share of total, 31 Mar 2025",
      "XB3": XB3,
      "XB4": "Of FY26-Q1 advisory inflows, how much came from the client's own retail accounts?",
      "XB5": "Retail customers with average balance above $8,000 in June 2025: how many are covered by a wealth advisor, by advisor office?",
      "XB6": "Which corporate counterparty contacts are also wealth households?"}
REFUSED = ("refuse", "blocked_access")
BOTH = ["analyst_enterprise", "finance_reporting", "steward_glossary", "admin", "steward"]
FIVE = ["retail-corporate", "sme-corporate", "corporate-wealth", "retail-sme", "sme-wealth"]


def attached():
    con = duckdb.connect(":memory:")
    for u in ("retail", "wealth", "xref"):
        con.execute(f"ATTACH '{W / u}.duckdb' AS {u} (READ_ONLY)")
    return con


@pytest.mark.parametrize("gid", ["G021", "G030"])
def test_g021_and_g030_drop_retail_wealth_and_keep_the_other_pairs(eng, gid):
    a = eng.answer(GOLDEN[gid]["question"], "analyst_enterprise")
    assert a["behaviour"] == "parts_with_reason" and a["joins_refused"] == FIVE
    assert set(GOLDEN[gid]["expect"]["joins_refused"]) <= set(a["joins_refused"]), "golden v2 still passes"


@pytest.mark.parametrize("gid, pair", [("G024", "retail-corporate"), ("G028", "sme-wealth"), ("G029", "corporate-wealth")])
def test_the_refusals_stay(eng, gid, pair):
    a = eng.answer(GOLDEN[gid]["question"], "analyst_enterprise")
    assert a["behaviour"] == "refuse" and pair in a["joins_refused"], a


def test_xb6_is_still_refused(eng):
    a = eng.answer(XB["XB6"], "analyst_enterprise")
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["corporate-wealth"] and "figure" not in a


def test_make_parity_names_the_stated_change():
    stated = json.loads((DEMO / "clients" / CLIENT / "baselines" / "parity-stated.json").read_text())["changes"]
    snap = json.loads((DEMO / "clients" / CLIENT / "baselines" / "parity-4d9d5c9.json").read_text())["results"]
    option_b, physical = stated[:2], stated[2:]
    assert [(c["result"], c["field"]) for c in option_b] == [("golden:G021:graph", "joins_refused"), ("golden:G030:graph", "joins_refused")]
    for c in option_b:
        assert c["to"] == [p for p in snap[c["result"]]["joins_refused"] if p != "retail-wealth"] and "Option B" in c["why"]
    assert physical and all(c["result"].split(":")[2] in ("schema", "catalog") and "pack 1.5.0" in c["why"] for c in physical), \
        "pack 1.5.0's physical names move the graph-off presets alone"
    assert "--stated $(PARITY_STATED)" in (DEMO / "Makefile").read_text()


@pytest.mark.parametrize("q, unit, key, count", [
    ("Which retail customers are also wealth households?", "retail", "customer.customer_id", "count(DISTINCT c.customer_id)"),
    ("Which wealth households are also retail customers?", "wealth", "household.household_id", "count(DISTINCT h.HSHLD_DK)")])
def test_which_customers_are_also_the_other_unit_s_counts_them_through_the_certified_join(eng, q, unit, key, count):
    a = eng.answer(q, "analyst_enterprise")
    con = attached()
    try:
        want = con.execute(f"SELECT {count} FROM xref.client_xref x JOIN retail.customer c ON c.customer_id = x.retail_customer_id "
                           "JOIN wealth.HSHLD_DIM_1 h ON h.HSHLD_DK = x.household_id WHERE x.certification_status = 'CERTIFIED' "
                           "AND x.is_current").fetchone()[0]
    finally:
        con.close()
    assert a["behaviour"] == "figure" and a["figure"] == want > 0 and (a["unit"], a["column"]) == (unit, key)
    assert a["certified_join"]["declared_join"] == "client_xref" and a["join_path"] == ["client_xref"]
    assert "certified join client_xref" in a["reason"] and a["warehouses_involved"] == ["retail", "wealth"]


def test_which_customers_across_a_no_join_pair_is_refused_as_before(eng):
    a = eng.answer(GOLDEN["G028"]["question"], "analyst_enterprise")
    assert (a["behaviour"], a["joins_refused"]) == ("refuse", ["sme-wealth"])


ASKS_RESTRICTED = {"Which retail customers are also wealth households, and what are their emails?": ["retail.customer.email"],
                   "Which retail customers are also wealth households? Show their dates of birth": ["retail.customer.date_of_birth"],
                   XB3 + ", with their email": ["retail.customer.email"]}


ASKS_ANOTHER_WAY = {"Which retail customers are also wealth households? List their e-mail addresses": ["retail.customer.email"],
                    "Which retail customers are also wealth households? List their e-mails": ["retail.customer.email"],
                    "Which retail customers are also wealth households? Give their birthdates": ["retail.customer.date_of_birth"],
                    "Which retail customers are also wealth households? Their birth dates": ["retail.customer.date_of_birth"],
                    "Which retail customers are also wealth households? Give their birthdays": ["retail.customer.date_of_birth"],
                    "Which retail customers are also wealth households? When were they born?": ["retail.customer.date_of_birth"],
                    XB3 + ", with their e-mail": ["retail.customer.email"],
                    XB3 + ", with their birthdates": ["retail.customer.date_of_birth"],
                    "NNA in FY26-Q1 from retail clients born before 1970": ["retail.customer.date_of_birth"],
                    XB3 + ", aged over 60": ["retail.customer.date_of_birth"],
                    XB3 + " by age": ["retail.customer.date_of_birth"],
                    XB3 + " by year of birth": ["retail.customer.date_of_birth"],
                    "Which retail customers are also wealth households older than 60?": ["retail.customer.date_of_birth"],
                    "Which wealth households are also retail customers younger than 30?": ["retail.customer.date_of_birth"],
                    "Which retail customers are also wealth households? Show their names": ["retail.customer.first_name"],
                    "Which retail customers are also wealth households? List the customer names": ["retail.customer.first_name"],
                    XB3 + ", by client name": ["retail.customer.first_name"]}


@pytest.mark.parametrize("q, named", [*ASKS_RESTRICTED.items(),
                                      ("Which wealth households are also retail customers? Their DOB please", ["retail.customer.date_of_birth"]),
                                      ("Which wealth households are also retail customers? List their household names",
                                       ["wealth.HSHLD_DIM_1.HSHLD_NM"]),
                                      ("Which retail customers are also wealth households? Their date_of_birth and e-mails",
                                       ["retail.customer.date_of_birth", "retail.customer.email"]),
                                      *ASKS_ANOTHER_WAY.items(),
                                      (XB3, []), ("Which retail customers are also wealth households?", []),
                                      ("Which retail customers are also wealth households? Their average balance and their usernames", [])])
def test_the_parser_reads_a_restricted_column_by_its_name_from_the_graph(eng, q, named):
    """A restricted column (the graph's: a restricted-attribute Policy GOVERNS it) is asked for by its name, its words
    apart or joined, each singular or plural, by its initials where it has three words or more, or by a phrase its
    policy gives for it (e-mail; birth, birthdate, birthday, born, age, aged, older than, younger than; customer name,
    client name, their name), each word of it the same way. A word that holds a phrase's word is not it: average is no
    age, a username no name."""
    from kge.services.ask.engine.core import parse
    assert parse(q, eng.term_names, eng).restricted == named, q


def test_the_restricted_names_are_the_graph_s_not_the_code_s():
    from types import SimpleNamespace

    from kge.services.ask.engine.core import parse
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, RESTRICTED_COLUMNS=[("u", "member", "postal_code"), ("u", "member", "tax_id_number")],
                             ACRONYMS={"PSTCD": ["u.member.postal_code"]})
    assert parse("their postal codes", [], domain).restricted == ["u.member.postal_code"]
    assert parse("their PSTCD", [], domain).restricted == ["u.member.postal_code"]
    assert parse("each TIN, and their emails", [], domain).restricted == ["u.member.tax_id_number"]
    domain.RESTRICTED_PHRASES = {"u.member.postal_code": ["zip code"]}
    assert parse("their zip codes", [], domain).restricted == ["u.member.postal_code"]
    assert parse("their zip-code", [], domain).restricted == ["u.member.postal_code"]
    assert parse("their zipper", [], domain).restricted == []


def no_warehouse(eng, monkeypatch) -> list:
    ran = []
    for name in ("run", "row", "rows", "cursor", "attached", "gate"):
        monkeypatch.setattr(eng.w, name, lambda *a, _n=name, **k: ran.append((_n, a)))
    return ran


@pytest.mark.parametrize("q", list(ASKS_RESTRICTED))
def test_a_question_across_the_certified_join_that_asks_for_restricted_data_is_refused(eng, monkeypatch, q):
    """Before any answer through the certified join: refused under the policy that governs the column, with no figure,
    and no warehouse queried; on the graph preset and the reference agent's treatment alike."""
    ran = no_warehouse(eng, monkeypatch)
    for a in (eng.answer(q, "analyst_enterprise"), Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment")):
        assert a["behaviour"] == "refuse", (q, a["behaviour"], a.get("reason"))
        assert "Restricted personal data" in a["reason"] and "(POL-DATA-CLASS)" in a["reason"] and ASKS_RESTRICTED[q][0] in a["reason"]
        assert a.get("figure") is None and "rows" not in a and "parts" not in a and "certified_join" not in a
        assert a["restricted"] == ASKS_RESTRICTED[q]
    assert ran == []


def test_a_term_s_figure_asked_with_restricted_data_in_one_unit_is_refused_not_answered_with_the_total(eng, monkeypatch):
    """One unit, a term's figure asked for with a restricted column by name ("with" its customers' names): the term's total
    is not what was asked and the column is never read, so it is refused under the column's policy, with no warehouse
    queried, rather than answered with the total; the same figure asked alone is answered as before."""
    plain = eng.answer("Total retail revenue for June 2025", "analyst_enterprise")
    assert plain["behaviour"] == "figure", plain.get("reason")
    ran = no_warehouse(eng, monkeypatch)
    q = "Total retail revenue for June 2025 with customer last names"
    for a in (eng.answer(q, "analyst_enterprise"), Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment")):
        assert a["behaviour"] == "refuse", (a["behaviour"], a.get("reason"))
        assert "Restricted personal data (retail.customer.last_name)" in a["reason"] and "(POL-DATA-CLASS)" in a["reason"]
        assert a.get("figure") is None and a["restricted"] == ["retail.customer.last_name"]
    assert ran == []


NEAR_MISSES = {
    "Complaints received by email in June 2025": ("figure", 3.0, None),
    "Number of complaints in June 2025 older than 30 days": ("figure", 3.0, None),
    "Credit exposure for June 2025 on facilities older than 5 years": ("figure", 440185374.74, None),
    "Days past due older than 90 days in June 2025": ("figure", 597.0, None),
    "Fee income for June 2025 by age band": ("sum_with_verdict", 51141.68, "same grain, currency and calendar; no conversion"),
    "NNA for March 2025 as reported, and the age of the data": ("figure", 5868354.54, None),
}


@pytest.mark.parametrize("q", list(NEAR_MISSES))
def test_a_policy_phrase_or_a_one_word_column_in_another_sense_answers_as_before(eng, q):
    """One unit's restricted column is refused only where the question names that column itself: a policy's phrase (the
    date of birth's age, older than) or a one-word column's name (email) said in another sense refuses nothing. The
    expected answers are e3737d6's, from a git archive of it on this build."""
    for a in (eng.answer(q, "analyst_enterprise"), Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment")):
        assert (a["behaviour"], a.get("figure"), a.get("reason")) == NEAR_MISSES[q], (a["behaviour"], a.get("reason"))


def test_an_onboarded_unit_s_restricted_column_named_is_refused_and_another_unit_s_phrase_refuses_nothing(tmp_path, monkeypatch):
    """Bancassurance onboarded (examples/bancassurance-osi), compiled and loaded on a scratch build: its policyholder
    names asked for with written premium are refused under the policy on bancassurance.policyholder.name, with no figure;
    "older than", a phrase of Retail's date-of-birth policy, does not refuse a bancassurance question. The rules draft
    the unit whatever the developer's .env says, and onboarding and certify write to copies, never the demo's own."""
    from kge.estate import onboard
    from tests.scratch import compile_pack, load_artifact, script, write_registry
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    build = scratch_build(tmp_path / "build")
    clients, warehouses, landing = tmp_path / "clients", tmp_path / "warehouses", tmp_path / "landing"
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    shutil.copytree(BUILD / "warehouses", warehouses)
    shutil.copytree(BUILD / "landing", landing, symlinks=True)
    write_registry(build, clients=clients, warehouses=warehouses, landing=landing)
    prof = profile_of(build)
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi", label="Bancassurance",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-18")
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-18")
    onboard.build(prof)
    for step in (("kge/estate/certify.py",), ("kge/compiler/lock.py", "--add-new")):
        done = script(*step, build=build)
        assert done.returncode == 0, done.stderr
    assert load_artifact(build, compile_pack(tmp_path / "out", clients)).returncode == 0
    e = Engine(profile_of(build))
    try:
        assert ("bancassurance", "policyholder", "name") in e.RESTRICTED_COLUMNS
        plain = e.answer("Written premium in June 2025", "analyst_enterprise")
        assert plain["behaviour"] == "figure", plain.get("reason")
        named = e.answer("Policyholder names with written premium in June 2025", "analyst_enterprise")
        assert named["behaviour"] == "refuse" and named.get("figure") is None, named.get("reason")
        assert named["restricted"] == ["bancassurance.policyholder.name"] and "(POL-DATA-CLASS)" in named["reason"]
        older = e.answer("Written premium in June 2025 for policies older than one year", "analyst_enterprise")
        assert (older["behaviour"], older.get("figure")) == ("figure", plain["figure"]), older.get("reason")
    finally:
        e.close()


@pytest.mark.parametrize("q", list(ASKS_ANOTHER_WAY))
def test_a_question_across_the_certified_join_that_asks_for_restricted_data_in_other_words_is_refused(eng, monkeypatch, q):
    """The owner's "any cross-BU question asking for email or date_of_birth", in the words people use for them: refused
    as by the columns' names, with no figure and no warehouse queried, on the graph preset and the agent's treatment."""
    ran = no_warehouse(eng, monkeypatch)
    for a in (eng.answer(q, "analyst_enterprise"), Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment")):
        assert a["behaviour"] == "refuse", (q, a["behaviour"], a.get("reason"))
        assert "Restricted personal data" in a["reason"] and "(POL-DATA-CLASS)" in a["reason"]
        assert a.get("figure") is None and "rows" not in a and "parts" not in a and a["restricted"] == ASKS_ANOTHER_WAY[q]
    assert ran == []


def test_a_question_across_a_no_join_pair_that_asks_for_restricted_data_keeps_its_refused_join(eng):
    a = eng.answer("Which SME clients are also wealth households, and what are their emails?", "analyst_enterprise")
    assert (a["behaviour"], a["joins_refused"]) == ("refuse", ["sme-wealth"])
    a = eng.answer("NNA for SME clients who are also wealth households, with their email", "analyst_enterprise")
    assert (a["behaviour"], a["joins_refused"]) == ("refuse", ["sme-wealth"])


ASKS_AS_A_KEY = {"Match retail customers to wealth household members on email: how many match?": ["retail.customer.email"],
                 "Join retail customers to wealth households on email and count the matches": ["retail.customer.email"],
                 "Count retail customers who are also wealth households, matched by email": ["retail.customer.email"],
                 "List the dates of birth of retail customers who are also wealth households": ["retail.customer.date_of_birth"],
                 "Can retail customers be matched to wealth households by email?": ["retail.customer.email"],
                 "Can retail customers be matched to wealth households by dates of birth?": ["retail.customer.date_of_birth"],
                 "What is the email of retail customers who are also wealth households?": ["retail.customer.email"]}


@pytest.mark.parametrize("q", list(ASKS_AS_A_KEY))
def test_a_question_across_the_certified_join_that_matches_on_restricted_data_or_lists_it_is_refused(eng, monkeypatch, q):
    """The owner's "any cross-BU question asking for email or date_of_birth", whatever path the question would take (a
    definitional one too): a question that names two units the certified join links and asks for a restricted column (as
    a match key, on email or by email, or dates of birth listed) is refused under the column's policy before any term is
    resolved, with no figure and no warehouse queried, on the graph preset and the reference agent's treatment alike.
    In round 2's UAT these reached neither the certified join's count nor a context, and abstained instead."""
    ran = no_warehouse(eng, monkeypatch)
    for a in (eng.answer(q, "analyst_enterprise"), Agent(eng, llm=None).ask(q, "analyst_enterprise", arm="treatment")):
        assert a["behaviour"] == "refuse", (q, a["behaviour"], a.get("reason"))
        assert a["reason"].startswith("Restricted personal data") and "(POL-DATA-CLASS)" in a["reason"]
        assert ASKS_AS_A_KEY[q][0] in a["reason"] and a["restricted"] == ASKS_AS_A_KEY[q]
        assert a.get("figure") is None and "rows" not in a and "parts" not in a
    assert ran == []


@pytest.mark.parametrize("q", ["Can retail customers be joined to wealth households on customer_id?",
                               "Join retail customers to wealth households on customer_id and count the matches"])
def test_a_key_name_join_is_answered_for_the_pair_the_question_names(eng, monkeypatch, q):
    """Round 2's UAT: a key-name join between Retail and Wealth was answered with the Retail and Corporate fact. The pair the
    question names decides it: Retail and Wealth are joined only through their certified join, so a join on a key's name
    is refused, saying so, with no warehouse queried; G024 (Retail and Corporate) is refused with its own fact as before."""
    ran = no_warehouse(eng, monkeypatch)
    a = eng.answer(q, "analyst_enterprise")
    assert a["behaviour"] == "refuse" and a.get("figure") is None, (q, a)
    assert "Corporate" not in a["reason"] and "retail-corporate" not in (a.get("joins_refused") or [])
    assert "only through the certified join client_xref" in a["reason"] and a["fact"]["declared_join"] == "client_xref"
    assert ran == []
    g024 = eng.answer(GOLDEN["G024"]["question"], "analyst_enterprise")
    assert g024["behaviour"] == "refuse" and g024["joins_refused"] == ["retail-corporate"]
    assert g024["reason"].startswith("No certified join path between Retail Banking and Corporate & Institutional.")


GROUP = "How many active customers in Retail and Wealth in June 2025 raised a complaint that month?"


def test_a_customer_group_across_retail_and_wealth_is_built_within_one_unit_and_the_certified_join_is_said(eng):
    from kge.services.ask.engine.combined import Combined
    a = Combined(eng, llm=None).ask(GROUP, "analyst_enterprise")
    assert a["behaviour"] == "abstain" and a["joins_refused"] == [] and "fact" not in a
    assert "no certified join" not in a["reason"] and "certified join client_xref" in a["reason"]
    assert a["certified_joins"][0]["pair"] == "retail-wealth"
    three = Combined(eng, llm=None).ask(GROUP.replace("Retail and Wealth", "Retail, SME and Wealth"), "analyst_enterprise")
    assert three["behaviour"] == "refuse" and three["joins_refused"] == ["retail-sme", "sme-wealth"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        c.build = build
        yield c
    appmod._engine.close() if appmod._engine else None


def test_the_map_lists_the_five_no_join_facts_and_the_certified_join(client):
    m = client.get("/map/Revenue", headers=client.as_("analyst_enterprise")).json()
    assert [n["pair"] for n in m["no_join"]] == ["retail-sme", "retail-corporate", "sme-corporate", "sme-wealth", "corporate-wealth"]
    assert [(c["pair"], c["declared_join"], c["note"]) for c in m["certified_joins"]] == [
        ("retail-wealth", "client_xref", "certified client cross-reference")]
    page = (DEMO / "ui" / "index.html").read_text()
    assert "m.certified_joins" in page and "Joined only through a certified join" in page


def test_the_map_of_a_term_a_context_answers_gives_the_context_and_the_certified_join_it_reaches(client):
    """Net new assets maps to no column: it is certified through its contexts. Its map gives each context the term resolves
    to, as the graph declares it (the measure and fact, the filters, row selectors and joins, by label), and the certified
    join its joins reach, so the page's Map tab shows what the answer was built from, never an empty table."""
    m = client.get("/map/Net New Assets", headers=client.as_("analyst_enterprise")).json()
    assert m["implementations"] == [] and m["defined_by"] == []
    [c] = m["contexts"]
    assert (c["id"], c["fact"], c["unit"]) == ("nna", "wealth.TRAN_FACT_1", "wealth") and c["label"] and c["measure"]
    assert c["currency"] == "USD", "the page labels the figure with its measure's currency"
    assert [f["id"] for f in c["filters"]] == ["eligible", "released"] and all(f["label"] and f["table"] for f in c["filters"])
    assert [s["id"] for s in c["row_selectors"]] == ["not_test_account", "not_excluded", "banks_with_retail"]
    assert [j["id"] for j in c["joins"] if j["required"]] == ["flow_month_end"], "the joins it always makes; the rest when asked"
    assert "client_xref" in [j["id"] for j in c["joins"]] and c["units"] == ["retail", "wealth"]
    assert [(x["pair"], x["declared_join"]) for x in m["certified_joins"]] == [("retail-wealth", "client_xref")] and m["no_join"] == []
    revenue = client.get("/map/Revenue", headers=client.as_("analyst_enterprise")).json()
    assert revenue["contexts"] == [] and len(revenue["implementations"]) == 4, "a term with certified columns maps as before"


def test_the_map_of_a_term_whose_name_holds_a_slash(client):
    """Round 2: the page asks for the term's map once a question names it, and a slash in its name gave a 404."""
    r = client.get("/map/Month-End%20Release%20%2F%20Freeze", headers=client.as_("analyst_enterprise"))
    assert r.status_code == 200 and r.json()["term"] == "Month-End Release / Freeze" and r.json()["defined_by"] == []


@pytest.mark.parametrize("who", ["analyst_retail", "advisor_wealth"])
@pytest.mark.parametrize("xb", ["XB1", "XB2", "XB3", "XB4", "XB5"])
def test_xb1_to_xb5_are_refused_to_a_principal_who_reads_one_unit(eng, monkeypatch, xb, who):
    """The join-time rule, unchanged: a Retail and Wealth result only to a principal who reads both, with no partial
    figure, and no warehouse queried; on the graph preset and the reference agent's treatment alike. XB1 to XB5 are read
    across Retail and Wealth, and refused for the one of the two the principal does not read. XB2 is too since pack 1.3.0
    (issue #40's pack-only test): AUM is Wealth's term, and deposits Retail's word, where before it named no unit and was
    read across all four."""
    ran = []
    monkeypatch.setattr(eng.w, "run", lambda unit, sql, *rest: ran.append(sql))
    monkeypatch.setattr(eng.w, "row", lambda unit, sql, *rest: ran.append(sql))
    monkeypatch.setattr(eng.w, "attached", lambda units: ran.append(units))
    other = {"analyst_retail": "wealth", "advisor_wealth": "retail"}[who]
    for a in (eng.answer(XB[xb], who), Agent(eng, llm=None).ask(XB[xb], who, arm="treatment")):
        assert a["behaviour"] in REFUSED, (xb, who, a["behaviour"], a.get("reason"))
        assert a.get("figure") is None and "parts" not in a and "rows" not in a
        involved, denied = a.get("warehouses_involved"), a.get("denied_warehouses")
        assert involved == ["retail", "wealth"] and denied == [other], (xb, who, involved, denied)
    assert ran == []


@pytest.mark.parametrize("who", BOTH)
def test_each_principal_who_reads_both_units_is_answered_on_xb1(eng, who):
    """Not refused, as the one-unit principals are. Since pack 1.3.0 (issue #40) XB1 is read through AUM's context, which
    counts no entity the pack declares, so it gets no figure, saying why: a known miss, never another question's figure."""
    a = eng.answer(XB["XB1"], who)
    assert a["behaviour"] not in REFUSED and a.get("figure") is None, (who, a["behaviour"], a.get("reason"))
    assert a["context"]["context"] == "aum" and "counts nothing the pack declares an entity" in a["reason"], a.get("reason")


def test_the_cross_reference_is_readable_by_no_one_on_its_own(eng, xb3):
    """No principal reads the cross-reference: it has no CAN_READ, no unit's warehouse holds it, a session attaches it only
    beside two units whose access has passed, and no unit's gate lets a query read it. Inside such a session, a query
    that reads the cross-reference alone is refused by the gate as a figure, as the reference agent gates every model's
    query: it measures nothing the question asks."""
    assert eng.g.q("MATCH (p:Principal)-[:CAN_READ]->(u:Unit) WHERE u.id IN $c RETURN count(*) AS n", {"c": eng.CURATED}) == [{"n": 0}]
    assert "xref" not in eng.w.con and "xref" not in eng.UNIT_ORDER
    with pytest.raises(ValueError, match="never on its own"):
        with eng.w.attached(["xref"]):
            pass
    with pytest.raises(ValueError, match="two or more"):
        with eng.w.attached(["retail"]):
            pass
    from kge.pipeline.sqlguard import schema_allowlist
    for unit in eng.UNIT_ORDER:
        g = eng.w.gate(unit, "SELECT count(*) FROM xref.client_xref", schema_allowlist(eng.g, unit, eng.RESTRICTED_COLUMNS))
        assert not g["passed"] and "outside this unit's warehouse" in g["problems"][0]
    plan, _ = xb3
    for alone in ("SELECT count(*) FROM xref.client_xref",
                  "SELECT count(client_xref.household_id) FROM xref.client_xref AS client_xref "
                  "WHERE client_xref.certification_status = 'CERTIFIED' AND client_xref.is_current"):
        g = gated(eng, plan, alone, figure=True)
        assert not g["passed"] and g["failed_at"] == "static", (alone, g)


def test_the_session_attaches_the_units_and_the_cross_reference_read_only(eng):
    with eng.w.attached(["wealth", "retail"]) as con:
        held = {r[0] for r in con.execute("SELECT database_name FROM duckdb_databases() WHERE NOT internal").fetchall()}
        assert {"retail", "wealth", "xref"} <= held and "sme" not in held
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE wealth.x (a INTEGER)")
    with pytest.raises(duckdb.Error):
        con.execute("SELECT 1")


@pytest.fixture(scope="module")
def xb3(eng):
    plan = eng.semantics.plan(eng.semantics.contexts["nna"], Asked(units=["retail"], period=Period("fiscal_quarter", "FY26-Q1")))
    import datetime as dt
    sql = plan.query([dt.date(2025, 4, 30), dt.date(2025, 5, 31), dt.date(2025, 6, 30)])
    return plan, sql


def gated(eng, plan, sql, figure=False):
    with eng.session(plan) as con:
        return gate(con, sql, plan.allow, figure)


def test_the_assembled_query_passes_the_gate_as_a_query_and_as_a_figure(eng, xb3):
    plan, sql = xb3
    for figure in (False, True):
        g = gated(eng, plan, sql, figure)
        assert g["passed"], g["problems"]
    assert gated(eng, plan, sql, True)["measured"] == ["wealth.TRAN_FACT_1.TRAN_AM"]


@pytest.mark.parametrize("edit, says", [
    (lambda s: s[:s.index("EXISTS (SELECT 1 FROM xref")] + "EXISTS (SELECT 1 FROM retail.customer AS customer WHERE customer.customer_id = "
     "HSHLD_DIM_1.HSHLD_DK)" + s[s.index(") AND MONTH_DIM_1.CLDR_EOM_DT"):][1:], "is not a declared join"),
    (lambda s: s.replace(" AND client_xref.certification_status = 'CERTIFIED'", ""), "used without its condition"),
    (lambda s: s.replace(" AND client_xref.is_current", ""), "without its window"),
    (lambda s: s.replace(" AND client_xref.is_current", " AND client_xref.valid_from IS NOT NULL AND (client_xref.valid_to IS NULL OR TRUE)"),
     "without its window"),
    (lambda s: s.replace(" AND client_xref.is_current", " AND client_xref.valid_from <= DATE '2025-06-30' AND (client_xref.valid_to IS NULL "
                                                          "OR client_xref.valid_to >= DATE '2024-06-30')"), "without its window"),
    *[(lambda s, o=o: s.replace(" AND client_xref.is_current", f" AND (client_xref.valid_from <= DATE '2025-06-30' OR {o}) AND "
                                                                f"(client_xref.valid_to IS NULL OR client_xref.valid_to >= DATE '2025-06-30' OR {o})"),
       "without its window") for o in ("TRUE", "1 = 1", "HSHLD_DIM_1.HSHLD_DK IS NOT NULL")],
    (lambda s: s.replace("customer.customer_id = client_xref.retail_customer_id", "customer.date_of_birth = client_xref.valid_from"),
     "restricted personal data"),
    (lambda s: s.replace("JOIN retail.customer AS customer", "JOIN sme.client AS customer"), "is not in this session"),
    (lambda s: s.replace("FROM wealth.TRAN_FACT_1 AS TRAN_FACT_1", "FROM TRAN_FACT_1"), "is not named <unit>.<table>"),
    (lambda s: s.replace("client_xref.household_id = HSHLD_DIM_1.HSHLD_DK", "client_xref.household_id + 0 = HSHLD_DIM_1.HSHLD_DK"),
     "is not a declared join"),
])
def test_the_gate_refuses_what_the_certified_join_does_not_allow(eng, xb3, edit, says):
    plan, sql = xb3
    bad = edit(sql)
    assert bad != sql
    for figure in (False, True):
        g = gated(eng, plan, bad, figure)
        assert not g["passed"] and any(says in p for p in g["problems"]), (says, g["problems"])


THROUGH = ("SELECT count(DISTINCT c.customer_id) FROM retail.customer AS c JOIN xref.client_xref AS x ON x.retail_customer_id = c.customer_id "
           "JOIN wealth.HSHLD_DIM_1 AS h ON h.HSHLD_DK = x.household_id WHERE x.certification_status = 'CERTIFIED' AND ")
ON_A_DATE = "x.valid_from <= DATE '2025-03-31' AND (x.valid_to IS NULL OR x.valid_to >= DATE '2025-03-31')"


@pytest.mark.parametrize("always", ["TRUE", "1 = 1", "h.HSHLD_DK IS NOT NULL", "x.valid_from IS NOT NULL"])
def test_a_window_with_something_always_true_ored_into_it_is_refused(eng, linked, always):
    """The window reads one version of each match: the version current now, or the one valid on a date, valid_from <= X
    and (valid_to IS NULL OR valid_to >= X). With something always true ORed into each half it reads every version, the
    closed ones too, and counts what the window exists to leave out, as a query with no window does."""
    plan, _ = linked
    good = THROUGH + ON_A_DATE
    bad = (THROUGH + f"(x.valid_from <= DATE '2025-03-31' OR {always}) AND "
           f"(x.valid_to IS NULL OR x.valid_to >= DATE '2025-03-31' OR {always})")
    assert counted(bad) == counted(THROUGH + "TRUE") != counted(good)
    for figure in (False, True):
        assert gated(eng, plan, good, figure)["passed"]
        g = gated(eng, plan, bad, figure)
        assert not g["passed"] and any("without its window" in p for p in g["problems"]), (always, g["problems"])


@pytest.mark.parametrize("window", [
    ON_A_DATE,
    "DATE '2025-03-31' >= x.valid_from AND (x.valid_to IS NULL OR DATE '2025-03-31' <= x.valid_to)",
    "x.valid_from <= CURRENT_DATE AND (x.valid_to IS NULL OR x.valid_to >= CURRENT_DATE)",
    "x.is_current"])
def test_a_window_on_one_date_or_the_current_version_passes(eng, linked, window):
    plan, _ = linked
    for figure in (False, True):
        g = gated(eng, plan, THROUGH + window, figure)
        assert g["passed"], (window, g["problems"])


LINKED_JOIN = ("JOIN wealth.HSHLD_DIM_1 AS HSHLD_DIM_1 ON client_xref.household_id = HSHLD_DIM_1.HSHLD_DK WHERE customer.customer_id = "
               "client_xref.retail_customer_id AND client_xref.certification_status = 'CERTIFIED' AND client_xref.is_current)")
CERTIFIED_ON = ("wealth.HSHLD_DIM_1 AS HSHLD_DIM_1 ON client_xref.household_id = HSHLD_DIM_1.HSHLD_DK AND client_xref.certification_status "
                "= 'CERTIFIED' AND client_xref.is_current WHERE customer.customer_id = client_xref.retail_customer_id)")


@pytest.fixture(scope="module")
def linked(eng):
    plan = eng.semantics.linked("retail", "wealth")
    sql = plan.query(None)
    assert LINKED_JOIN in sql, sql
    return plan, sql


def counted(sql: str) -> float:
    con = attached()
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("kind", ["LEFT", "FULL", "ANTI", "SEMI", "RIGHT"])
def test_a_join_that_is_not_inner_is_refused_with_the_certified_join_s_conditions_in_its_on(eng, linked, kind):
    """In the ON of an outer join the certification filter and the window filter nothing, and in an anti join they
    select exactly the matches that are not certified or not current: a context's query joins its tables with inner
    joins only, so its conditions select the rows it reads."""
    plan, sql = linked
    bad = sql.replace(LINKED_JOIN, f"{kind} JOIN {CERTIFIED_ON}")
    if kind in ("LEFT", "FULL", "ANTI"):
        assert counted(bad) != counted(sql)
    for figure in (False, True):
        g = gated(eng, plan, bad, figure)
        assert not g["passed"] and any("inner joins only" in p for p in g["problems"]), (kind, g["problems"])


def test_an_outer_join_through_the_cross_reference_outside_a_semi_join_is_refused(eng, linked):
    plan, sql = linked
    bad = ("SELECT count(DISTINCT customer.customer_id) FROM retail.customer AS customer LEFT JOIN xref.client_xref AS client_xref ON "
           "customer.customer_id = client_xref.retail_customer_id AND client_xref.certification_status = 'CERTIFIED' AND "
           "client_xref.is_current LEFT JOIN wealth.HSHLD_DIM_1 AS HSHLD_DIM_1 ON client_xref.household_id = HSHLD_DIM_1.HSHLD_DK")
    for figure in (False, True):
        g = gated(eng, plan, bad, figure)
        assert not g["passed"] and any("inner joins only" in p for p in g["problems"]), g["problems"]


def test_a_figure_across_the_units_refuses_an_in_subquery_that_joins_them_on_a_key_no_declared_join_links(eng, linked):
    """The join rule sees a join between two tables of one scope, so `IN (SELECT ...)` across the units is no equality
    it reads. Every query of two units a model writes is gated as a figure (kge/services/ask/engine/agent.py Agent.gate), and a figure holds
    no subquery but an EXISTS whose every condition the context declared: a Retail customer's id matched to a household's
    id is refused there. The platform's own queries of two units write no IN (SELECT ...) (kge/semantics/query.py)."""
    plan, sql = linked
    assert "IN (SELECT" not in sql.upper()
    bad = ("SELECT count(DISTINCT customer.customer_id) FROM retail.customer AS customer WHERE customer.customer_id IN "
           "(SELECT HSHLD_DIM_1.HSHLD_DK FROM wealth.HSHLD_DIM_1 AS HSHLD_DIM_1)")
    g = gated(eng, plan, bad, True)
    assert not g["passed"] and any("no CTE, subquery" in p for p in g["problems"]), g["problems"]


def test_the_xb3_query_with_its_semi_join_left_joined_is_refused(eng, xb3):
    plan, sql = xb3
    inner = "JOIN retail.customer AS customer ON customer.customer_id = client_xref.retail_customer_id WHERE "
    conditions = "AND client_xref.certification_status = 'CERTIFIED' AND client_xref.is_current"
    assert inner in sql and conditions in sql
    bad = sql.replace(conditions, "").replace(inner, "LEFT JOIN retail.customer AS customer ON customer.customer_id = "
                                                     f"client_xref.retail_customer_id {conditions} WHERE ")
    g = gated(eng, plan, bad, True)
    assert not g["passed"] and any("inner joins only" in p for p in g["problems"]), g["problems"]


@pytest.mark.parametrize("conditions, says", [
    ("x2.certification_status = 'CERTIFIED' AND x2.is_current", ["used without its condition", "without its window"]),
    ("x1.certification_status = 'CERTIFIED' AND x2.is_current", ["without its window"]),
])
def test_the_certified_join_s_conditions_hold_on_the_alias_it_joins_through(eng, linked, conditions, says):
    """A second alias of the cross-reference cannot carry the conditions for the one the match goes through: the
    customer joined to the household through an unfiltered x1 counts matches that are not certified or not current."""
    plan, _ = linked
    bad = ("SELECT count(DISTINCT customer.customer_id) FROM retail.customer AS customer WHERE EXISTS (SELECT 1 FROM xref.client_xref "
           "AS x1 JOIN wealth.HSHLD_DIM_1 AS HSHLD_DIM_1 ON x1.household_id = HSHLD_DIM_1.HSHLD_DK JOIN xref.client_xref AS x2 ON "
           f"x2.household_id = HSHLD_DIM_1.HSHLD_DK WHERE customer.customer_id = x1.retail_customer_id AND {conditions})")
    for figure in (False, True):
        g = gated(eng, plan, bad, figure)
        assert not g["passed"], g
        for s in says:
            assert any(s in p and "x1" in p for p in g["problems"]), (s, g["problems"])
    good = bad.replace("x2.", "x1.").replace(" JOIN xref.client_xref AS x2 ON x1.household_id = HSHLD_DIM_1.HSHLD_DK", "")
    assert "x2" not in good and gated(eng, plan, good, True)["passed"], "an alias of its own, with the conditions on it, passes"


def test_xb3_with_a_second_alias_carrying_the_conditions_is_refused(eng, xb3):
    plan, sql = xb3
    near = "WHERE client_xref.household_id = HSHLD_DIM_1.HSHLD_DK AND client_xref.certification_status = 'CERTIFIED' AND client_xref.is_current"
    assert near in sql
    bad = sql.replace(near, "JOIN xref.client_xref AS x2 ON x2.retail_customer_id = customer.customer_id WHERE client_xref.household_id = "
                            "HSHLD_DIM_1.HSHLD_DK AND x2.certification_status = 'CERTIFIED' AND x2.is_current")
    g = gated(eng, plan, bad, True)
    assert not g["passed"] and any("used without its condition" in p for p in g["problems"]), g["problems"]


def test_a_window_alone_does_not_join_a_table_across_units(eng, xb3):
    """A version's window is a condition on a table its key joins already: on its own it joins nothing. The account joined
    on its window alone, and the cross-reference on its window with no match key, are each refused."""
    plan, sql = xb3
    account = ("JOIN wealth.ACCT_DIM2_1 AS ACCT_DIM2_1 ON TRAN_FACT_1.DW_ACCT_ID = ACCT_DIM2_1.DW_ACCT_ID AND "
               "TRAN_FACT_1.ACCT_SRCE_SYS_ID = ACCT_DIM2_1.SRCE_SYS_ID AND ACCT_DIM2_1.CURR_IN <> 0")
    assert account in sql
    bad = sql.replace(account, "JOIN wealth.ACCT_DIM2_1 AS ACCT_DIM2_1 ON ACCT_DIM2_1.DW_EFF_DT <= MONTH_DIM_1.FIN_MTRC_RLSE_DT AND "
                               "ACCT_DIM2_1.CURR_IN <> 0")
    g = gated(eng, plan, bad, True)
    assert not g["passed"] and any("wealth.ACCT_DIM2_1 is not joined" in p for p in g["problems"]), g["problems"]
    start = sql.index(" AND EXISTS (SELECT 1 FROM xref")
    end = sql.index(" AND MONTH_DIM_1.CLDR_EOM_DT BETWEEN")
    bad = (sql[:start] + sql[end:]).replace(" WHERE TRAN_FACT_1.CURR_IN <> 0", " JOIN xref.client_xref AS client_xref ON client_xref.valid_from <= "
                                            "MONTH_DIM_1.FIN_MTRC_RLSE_DT AND client_xref.certification_status = 'CERTIFIED' AND "
                                            "client_xref.is_current JOIN retail.customer AS customer ON customer.customer_id = "
                                            "client_xref.retail_customer_id WHERE TRAN_FACT_1.CURR_IN <> 0", 1)
    assert bad.count("xref.client_xref") == 1
    g = gated(eng, plan, bad, True)
    assert not g["passed"] and any("is not joined to the others by a declared join" in p for p in g["problems"]), g["problems"]


def test_an_attach_in_sql_is_refused(eng, xb3):
    plan, _ = xb3
    g = gated(eng, plan, "ATTACH 'build/warehouses/sme.duckdb' AS sme (READ_ONLY)")
    assert not g["passed"] and "read-only" in g["problems"][0]


def test_a_figure_may_hold_only_the_exists_the_context_declares(eng, xb3):
    plan, sql = xb3
    narrowed = sql.replace(" AND MONTH_DIM_1.CLDR_EOM_DT", " AND EXISTS (SELECT 1 FROM wealth.ACCT_DIM2_1 AS a2 WHERE a2.DW_ACCT_ID = "
                                                             "TRAN_FACT_1.DW_ACCT_ID AND a2.HSHLD_DK = 5001) AND MONTH_DIM_1.CLDR_EOM_DT")
    assert gated(eng, plan, narrowed, False)["passed"], "a planner's query may"
    g = gated(eng, plan, narrowed, True)
    assert not g["passed"] and any("the warehouse's own tables" in p for p in g["problems"])
    assert "EXISTS (SELECT 1 FROM retail.account AS account WHERE" in sql and gated(eng, plan, sql, True)["passed"]
    closed = sql.replace("account.status_code = 'OPEN'", "account.status_code = 'CLOSED'")
    assert closed != sql and gated(eng, plan, closed, False)["passed"], "a planner's query may"
    g = gated(eng, plan, closed, True)
    assert not g["passed"] and any("the warehouse's own tables" in p for p in g["problems"])


def proposed() -> list[tuple]:
    """The current matches certification leaves to a steward, as the warehouse holds them: (id, method, confidence, status)."""
    con = duckdb.connect(str(W / "xref.duckdb"), read_only=True)
    try:
        return con.execute("SELECT xref_id, match_method, match_confidence, certification_status FROM client_xref WHERE is_current AND "
                           "(certification_status = 'PROPOSED' OR match_confidence < 0.9) ORDER BY 1").fetchall()
    finally:
        con.close()


def test_certification_lands_the_matches_for_review_as_ids_methods_confidences_and_statuses_only(eng):
    release = Zone(BUILD / "landing", CLIENT).open(eng.g.release()["id"])
    held = release.reviews()
    assert list(held) == ["xref"] and (held["xref"]["table"], held["xref"]["floor"]) == ("client_xref", 0.9)
    assert [(m["id"], m["method"], m["confidence"], m["status"]) for m in held["xref"]["matches"]] == \
        [(i, m, float(c), s) for i, m, c, s in proposed()]
    assert all(set(m) == {"id", "method", "confidence", "status"} for m in held["xref"]["matches"]), "no customer or household id"
    text = (release.path / "review" / "xref.json").read_text()
    assert "customer" not in text and "household" not in text
    con = duckdb.connect(str(W / "xref.duckdb"), read_only=True)
    try:
        statuses = dict(con.execute("SELECT certification_status, count(*) FROM client_xref WHERE is_current AND "
                                    "(certification_status = 'PROPOSED' OR match_confidence < 0.9) GROUP BY 1").fetchall())
    finally:
        con.close()
    assert statuses == {"PROPOSED": 5, "REJECTED": 4}, "the queue holds matches upstream rejected, too: low-confidence ones"


def test_the_loader_makes_each_a_tag_and_a_steward_who_reads_both_units_sees_them(eng, monkeypatch):
    tags = eng.g.q("MATCH (t:Tag) WHERE t.family = 'xref_match' AND t.generator = 'certification' RETURN t.id AS id, t.value AS v ORDER BY id")
    assert sorted(int(t["v"].rsplit(":", 1)[1]) for t in tags) == [i for i, *_ in proposed()]
    assert not eng.g.q("MATCH (c:Chunk)-[:TAGGED]->(t:Tag) WHERE t.family = 'xref_match' RETURN t.id"), "on no chunk"
    for who in ("steward", "steward_glossary", "admin"):
        q = [r for r in eng.g.steward_queue(who) if r["family"] == "xref_match"]
        assert len(q) == len(tags) and all(r["units"] == ["retail", "wealth"] and r["doc"] == "xref.client_xref" for r in q)
    assert not [r for r in eng.g.steward_queue() if r["family"] == "xref_match"], "no principal, no matches"
    assert not [r for r in eng.g.steward_queue("analyst_enterprise") if r["family"] == "xref_match"], "not a certification identity"
    real = eng.g.readable_units
    monkeypatch.setattr(eng.g, "readable_units", lambda p: real(p) - {"wealth"})
    assert not [r for r in eng.g.steward_queue("steward") if r["family"] == "xref_match"], "a certifier who reads one unit sees none"


def test_the_queue_says_of_each_match_whether_upstream_proposed_or_rejected_it(eng):
    """Four of the nine are rejected upstream with low confidence: a steward sees which before deciding, and the queue
    does not repeat on every row what a decision says once, that nothing is written back."""
    rows = {int(r["value"].rsplit(":", 1)[1]): r for r in eng.g.steward_queue("steward") if r["family"] == "xref_match"}
    held = {i: (m, float(c), s) for i, m, c, s in proposed()}
    assert set(rows) == set(held)
    for i, (method, confidence, status) in held.items():
        r = rows[i]
        assert r["upstream"] == status.lower(), (i, r)
        assert r["evidence"].startswith(f"{status.lower()} upstream"), r["evidence"]
        assert ("below the floor 0.9" in r["evidence"]) == (confidence < 0.9), r["evidence"]
        assert method in r["evidence"] and "written back" not in r["evidence"], r["evidence"]
    assert sorted(i for i, r in rows.items() if r["upstream"] == "rejected") == [3, 15, 20, 42]
    assert all("upstream" not in r for r in eng.g.steward_queue("steward") if r["family"] != "xref_match")


def xref_digest(build: Path) -> str:
    con = duckdb.connect(str(build / "warehouses" / "xref.duckdb"), read_only=True)
    try:
        return hashlib.sha256(repr(con.execute("SELECT * FROM client_xref ORDER BY ALL").fetchall()).encode()).hexdigest()
    finally:
        con.close()


def test_a_steward_s_decision_is_the_graph_s_record_and_never_written_back(tmp_path):
    build = scratch_build(tmp_path / "build")
    before = xref_digest(build)
    e = Engine(profile_of(build))
    try:
        figure = e.answer(XB3, "analyst_enterprise")["figure"]
        tag = next(r["tag"] for r in e.g.steward_queue("steward") if r["family"] == "xref_match")
        with pytest.raises(PermissionError):
            e.g.steward_decide(tag, "analyst_enterprise", "accept")
        with pytest.raises(ValueError, match="no value"):
            e.g.steward_decide(tag, "steward", "accept", value="something")
        r = e.g.steward_decide(tag, "steward", "accept")
        assert r["superseded"] == tag and r["written_back"] is False and "certified again upstream" in r["note"]
        assert tag not in {q["tag"] for q in e.g.steward_queue("steward")}
        assert e.g.q("MATCH (n:Tag)-[:SUPERSEDES]->(o:Tag) WHERE o.id = $t RETURN n.generator AS g", {"t": tag}) == [{"g": "human"}]
        with pytest.raises(LookupError):
            e.g.steward_decide(tag, "steward", "reject")
        assert e.answer(XB3, "analyst_enterprise")["figure"] == figure, "the figure moves only when the cross-reference re-certifies"
    finally:
        e.close()
    assert xref_digest(build) == before


def test_the_queue_shows_the_matches_through_the_api(client):
    for who in ("steward", "admin"):
        q = [r for r in client.get("/steward/queue", headers=client.as_(who)).json() if r["family"] == "xref_match"]
        assert len(q) == len(proposed()) and all(r["confidence"] < 0.9 or r["evidence"] for r in q)
        assert {r["upstream"] for r in q} == {"proposed", "rejected"}
    assert client.get("/steward/queue", headers=client.as_("analyst_enterprise")).status_code == 403
    tag = q[0]["tag"]
    r = client.post("/steward/decide", json={"tag": tag, "decision": "reject"}, headers=client.as_("steward")).json()
    assert r["superseded"] == tag and r["written_back"] is False
    rejected = next(x for x in q if x["upstream"] == "rejected")
    r = client.post("/steward/decide", json={"tag": rejected["tag"], "decision": "accept"}, headers=client.as_("steward")).json()
    assert r["written_back"] is False and "rejected upstream" in r["note"] and "certified again upstream" in r["note"], r["note"]


NEW_LAYERS = ["measures", "calendars", "aliases", "periods", "policies", "curated_sources", "contexts", "context_conditions",
              "context_joins", "declared_joins", "conflicts", "acronyms", "entities", "relations", "certified_joins", "not_joinable",
              "lineage", "golden", "matches", "vocabulary"]


def test_the_fingerprint_covers_the_new_curated_types(eng):
    """Among them meta-model 1.2.0's vocabulary, the parser's words the pack gives (#39 item 11), each kind of it."""
    assert [n for n, _ in runtrace.MODEL_LAYERS if n not in runtrace.LEGACY_LAYERS] == ["release", *NEW_LAYERS]
    with eng.g.reader() as conn:
        rows = {n: runtrace.held(conn, c) for n, c in runtrace.MODEL_LAYERS if n in NEW_LAYERS}
    assert all(rows[n] for n in NEW_LAYERS), [n for n in NEW_LAYERS if not rows[n]]
    assert {r[1] for r in rows["vocabulary"]} == {"intent", "value", "predicate", "mailbox"}
    assert rows["certified_joins"] == [["retail", "wealth", "Data Architecture Board", "2026-10-10", "certified client cross-reference",
                                        "client_xref"]]
