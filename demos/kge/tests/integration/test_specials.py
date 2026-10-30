"""The specials as data: no domain logic in Python (issue #40, the owner's Question 18 default).

    .venv/bin/python -m pytest tests/integration/test_specials.py -q

Two answers kge/services/ask/engine/core.py once wrote out for Meridian's questions are read from
facts the graph already holds, and worded from them:

  a join on a key's name   fires on "join" and the name of a column the pack
                           declares not joinable (not_joinable, loaded as
                           NOT_JOINABLE_TO). The pair is the column's unit and
                           the table's; the refusal is their no-join fact,
                           worded from the units' labels and the fact's note;
                           a definitional question cites the two units' data
                           dictionaries, found by their units. A pack with no
                           such column never fires it.
  a currency no figure is  fires on "in <code>", the code typed in capitals and
  in                       an ISO 4217 one, where no certified measure is in it
                           or converts from it, and says which are and which
                           convert; never where the pack declares no measure,
                           and never on a word ("in all units").

Meridian's G024, G032 and G042 answer as before on every field make parity
holds (only the reason's words move). A fixture client (Thornbury's pack,
copied, with one not-joinable column added) is refused on its own fact and
cites its own dictionary; the pack as committed, with none, never fires it;
and its measures, USD only, make a question in EUR abstain, where Meridian's,
which include EUR, do not.

Opens the demo's graph through the eng fixture, and builds the fixture client
into a temporary folder. Stop the API first.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, Engine, parse
from kge.eval import sets
from tests.scratch import BUILD, DEMO, script

GOLDEN = {i["id"]: i for i in sets.items(BUILD / "golden.json")}
TOY = "thornbury"
NOT_JOINABLE = {"column": "claims.claim.policy_ref", "table": "underwriting.policy", "asserted_by": "Claims and Underwriting Data Council",
                "asserted_on": "2025-02-03", "note": "claims.claim.policy_ref is the policy number as the claimant reported it, never "
                                                     "checked against underwriting.policy."}
JOIN = "Join claims to underwriting policies on policy_ref and count the matches"
CAN_JOIN = "What is the result of joining claims to underwriting policies on policy_ref?"
GWP = "Gross written premium for June 2025"


def ok(r):
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return r.stdout


@pytest.fixture(scope="module")
def toy(tmp_path_factory):
    """The fixture client built from its committed pack and released, then the same pack with one not-joinable column
    added, compiled and loaded beside it: (the profile, the committed pack's hash, the edited pack's hash). The
    committed pack's release is ungated, as make graph releases it."""
    from kge.pipeline.profile import read
    root = tmp_path_factory.mktemp("specials")
    shutil.copytree(DEMO / "clients" / TOY, root / "clients" / TOY)
    registry = root / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": TOY, "profiles": {TOY: {
        "client": TOY, "clients": "clients", "artifacts": "build/artifacts", "store": "build/stores", "landing": "build/landing",
        "warehouses": f"build/{TOY}/warehouses", "identities": str(DEMO / "deploy" / TOY / "identities.yaml")}}}, sort_keys=False))
    env = {"PROFILES": str(registry), "PROFILE": TOY}
    for step in (("kge/estate/from_files.py",), ("kge/estate/certify.py",), ("kge/compiler/lock.py", "--check")):
        ok(script(*step, env=env))
    hashes = []
    for edit in (False, True):
        if edit:
            pack = root / "clients" / TOY / "pack.yaml"
            pack.write_text(pack.read_text() + "\n" + yaml.safe_dump({"not_joinable": [NOT_JOINABLE]}, sort_keys=False, width=200))
        art = ok(script("kge/compiler/compile.py", env=env)).strip()
        h = Path(art).name
        for step in (("kge/graph/load.py", "--artifact", art), ("kge/pipeline/run.py", "--hash", h), ("kge/graph/release.py", "--hash", h, "--mark-loaded")):
            ok(script(*step, env=env))
        if not edit:
            ok(script("kge/graph/release.py", "--hash", h, "--ungated", env=env))
        hashes.append(h)
    _, held = read(registry)
    return held[TOY], hashes[0], hashes[1]


@pytest.fixture()
def opened(toy):
    """Opens the fixture client's engine on one of its two graphs, and closes it after the test."""
    held = []

    def at(model_hash: str) -> Engine:
        e = Engine(toy[0].at(model_hash))
        held.append(e)
        return e
    yield at
    for e in held:
        e.close()


def no_warehouse(eng, monkeypatch) -> list:
    ran = []
    monkeypatch.setattr(eng.w, "run", lambda *a, **k: ran.append(a) or pytest.fail(f"a warehouse was queried: {a}"))
    return ran


def test_the_engine_reads_the_not_joinable_columns_and_the_currencies_from_the_graph(eng):
    pack = yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())
    assert [(x["column"], x["table"]) for x in eng.NOT_JOINABLE] == [(x["column"], x["table"]) for x in pack["not_joinable"]]
    (nj,) = eng.NOT_JOINABLE
    assert (nj["name"], nj["unit"], nj["table_unit"]) == ("customer_id", "corporate", "retail")
    assert eng.CURRENCIES == sorted({m["currency"] for m in pack["measures"]}) == ["EUR", "USD"]
    assert eng.CONVERSIONS == [("EUR", "USD", "period_end_rate_from_EUR")]


def test_g024_is_refused_with_the_no_join_fact_between_the_column_s_unit_and_the_table_s(eng, monkeypatch):
    ran = no_warehouse(eng, monkeypatch)
    for a in (eng.answer(GOLDEN["G024"]["question"], "analyst_enterprise"),
              eng.answer(GOLDEN["G024"]["question"], "analyst_enterprise", config=AGENT_ARMS["treatment"])):
        assert a["behaviour"] == "refuse" and a["joins_refused"] == ["retail-corporate"] and a.get("figure") is None
        assert a["fact"] == eng.g.no_join("retail", "corporate")
        assert a["reason"] == f"No certified join path between Retail Banking and Corporate & Institutional. {a['fact']['note']}"
    assert ran == []


@pytest.mark.parametrize("arm", ["graph", "treatment"])
def test_g032_cites_the_two_units_data_dictionaries_the_graph_finds_by_their_units(eng, arm):
    cfg = PRESETS["graph"] if arm == "graph" else AGENT_ARMS["treatment"]
    a = eng.answer(GOLDEN["G032"]["question"], "analyst_enterprise", config=cfg)
    assert a["behaviour"] == "cite_certified" and a["answer"] == "no" and a["cite"] == ["DD-CORPORATE", "DD-RETAIL"]
    assert a["reason"].startswith("No certified join path between Retail Banking and Corporate & Institutional.")
    assert eng.g.data_dictionaries(["retail", "corporate"]) == ["DD-CORPORATE", "DD-RETAIL"]


def test_g042_abstains_saying_which_currencies_are_certified_and_which_convert(eng, monkeypatch):
    ran = no_warehouse(eng, monkeypatch)
    a = eng.answer(GOLDEN["G042"]["question"], "analyst_enterprise")
    assert a["behaviour"] == "abstain" and a.get("figure") is None
    assert a["reason"] == ("No GBP conversion standard exists: certified figures are in EUR and USD, and the only conversion is "
                           "EUR to USD (period_end_rate_from_EUR).")
    assert ran == []


@pytest.mark.parametrize("q", ["Corporate revenue in USD for fiscal quarter FY26-Q1",
                               "Corporate credit exposure in EUR at 30 June 2025",
                               "Total revenue in all units for June 2025"])
def test_a_currency_the_graph_holds_or_a_word_is_no_abstention(eng, q):
    """G013's currency, a certified one, and words that only look like codes: all is a word where ALL is a code; a
    held code in lower case is the same code, and an unheld one, in either case, abstains (#157)."""
    assert parse(q, eng.term_names, eng).special != "no_conversion_standard", q


def test_no_platform_line_names_the_specials_documents_or_the_old_wording():
    import re
    from tests.unit.test_client_nouns import platform_files
    said = re.compile(r"DD-CORPORATE|DD-RETAIL|STD-FX|PROC-RECON|in gbp", re.I)
    found = [f"{p.relative_to(DEMO)}:{n}" for p in platform_files() for n, line in enumerate(p.read_text().splitlines(), 1)
             if said.search(line)]
    assert found == [], found


def test_a_pack_with_no_not_joinable_column_never_refuses_a_join_on_a_key_s_name(toy, opened):
    e = opened(toy[1])
    assert e.NOT_JOINABLE == []
    for q in (JOIN, CAN_JOIN):
        assert parse(q, e.term_names, e).special != "key_name_join", q
        assert "fact" not in e.answer(q), q


def test_a_join_on_a_column_the_pack_declares_not_joinable_is_refused_with_its_own_fact(toy, opened, monkeypatch):
    e = opened(toy[2])
    assert [(x["column"], x["unit"], x["table_unit"], x["name"]) for x in e.NOT_JOINABLE] == \
        [("claims.claim.policy_ref", "claims", "underwriting", "policy_ref")]
    ran = no_warehouse(e, monkeypatch)
    a = e.answer(JOIN)
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["underwriting-claims"] and a.get("figure") is None
    assert a["fact"] == e.g.no_join("underwriting", "claims") and "never checked against" in a["fact"]["note"]
    assert a["reason"] == f"No certified join path between Commercial Underwriting and Claims. {a['fact']['note']}"
    assert ran == []


def test_a_definitional_question_rests_on_the_fixture_client_s_own_data_dictionary(toy, opened):
    """The shelf rule cites the certified dictionary; the tier rule places it by what certification landed, which for the
    fixture's is not AI-ready: named as such, never cited."""
    e = opened(toy[2])
    for cfg in (PRESETS["graph"], AGENT_ARMS["treatment"]):
        a = e.answer(CAN_JOIN, config=cfg)
        assert a["behaviour"] == "cite_certified" and a["answer"] == "no", (cfg.label, a)
        assert a["reason"].startswith("No certified join path between Commercial Underwriting and Claims.")
        if cfg.citation_rule == "shelves":
            assert a["cite"] == ["DD-CLAIMS"], a
        else:
            assert a["cite"] == [] and [d["doc"] for d in a["not_ai_ready"]] == ["DD-CLAIMS"], a


def test_a_question_in_a_currency_no_measure_is_in_abstains_and_one_in_the_measures_currency_does_not(toy, opened, monkeypatch):
    e = opened(toy[1])
    assert e.CURRENCIES == ["USD"] and e.CONVERSIONS == []
    ran = no_warehouse(e, monkeypatch)
    a = e.answer("Gross written premium in EUR for June 2025")
    assert a["behaviour"] == "abstain" and a.get("figure") is None
    assert a["reason"] == "No EUR conversion standard exists: certified figures are in USD, and none is converted from another currency."
    assert ran == []
    monkeypatch.undo()
    assert e.answer(GWP.replace(" for", " in USD for"))["figure"] == e.answer(GWP)["figure"] == 37646.14


def test_a_domain_that_declares_no_currency_never_abstains_on_one():
    bare = SimpleNamespace(ALIASES={}, UNIT_WORDS={})
    assert parse("Total revenue in GBP for June 2025", [], bare).special is None
    held = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, CURRENCIES=["USD"], CONVERSIONS=[])
    assert parse("Total revenue in GBP for June 2025", [], held).special == "no_conversion_standard"
    assert parse("Total revenue in XYZ for June 2025", [], held).special is None, "an ISO 4217 code only"
