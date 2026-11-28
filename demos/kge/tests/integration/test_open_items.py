"""#707 slice 707.1 (#712) on Meridian's build: get_context's bindings and open items, map_term's aliases and search's
filter phrases, opt-in, from ask's own rules reading, with ask's answers unchanged.

    make graph pipeline, then .venv/bin/python -m pytest tests/integration/test_open_items.py -q

The evaluation suite (portal/server/test/fixtures/cerebro-evaluation-suite.json, #707 requirement 16) holds every
wording of the #697-#700 hotfix reviews, the golden 20 and #707's acceptance wordings, each with the terms ask's plan
binds and the open items KGE reports. The lane runs its kge_sample (the golden 20, the acceptance wordings and every
25th review wording); SUITE=all runs every wording (some 70 minutes). With MODEL_PROVIDER=none the terms in the
bindings are the terms ask's plan reads, wording by wording.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from kge.pipeline.settings import ROOT
from tests.scratch import profile_of, scratch_build

SUITE = json.loads((ROOT / "portal" / "server" / "test" / "fixtures" / "cerebro-evaluation-suite.json").read_text())
ASKER = "analyst_enterprise.meridian@example.com"
ENGINEER = "knowledge_engineer.meridian@example.com"
NO_WEALTH = "analyst_retail.meridian@example.com"
WORDINGS = [w for w in SUITE["wordings"] if os.environ.get("SUITE") == "all" or w["kge_sample"]]
UNGRADED = {"reading"}
"""The keys of a wording's recorded kge that test_each_suite_wording... does not grade: the whole reading, which the
portal's suite replays (portal/server/test/cerebro-suite.test.js) and tests/cerebro_suite.py records. Every other key
the fixture holds is graded; a key the report no longer gives, or a new one the fixture gains, fails the test."""
NNA_TERMS = ["Net New Assets", "Branch Sales NNA", "Certified NNA", "Client-Directed NNA"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import kge.app as appmod
    build = scratch_build(tmp_path_factory.mktemp("open_items") / "build")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MODEL_PROVIDER", "none")
        for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
            patch.setattr(appmod, name, value)
        with TestClient(appmod.app) as c:
            from kge.services.identity.service import token_for
            c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
            c.token = lambda who: token_for(profile_of(build).run_dir(), who)
            yield c
        appmod._engine.close() if appmod._engine else None


def context(client, question, who=ASKER, **body):
    r = client.post("/context", json={"question": question, **body}, headers=client.as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def items(package, kind):
    return [item for item in package.get("open_items", []) if item["item"] == kind]


def asks_plan_terms(eng, question, monkeypatch):
    """The terms of the plan ask executes for question on its default arm, read from the run trace's ledger block."""
    from kge.services.ask.engine import planledger
    held = {}
    block = planledger.block

    def recorded(question, turn, vocabulary, reply, result, follow_up):
        held["read"] = (vocabulary, result)
        return block(question, turn, vocabulary, reply, result, follow_up)
    monkeypatch.setattr(planledger, "block", recorded)
    eng.answer(question, ASKER)
    monkeypatch.setattr(planledger, "block", block)
    if "read" not in held:
        return None
    vocabulary, result = held["read"]
    if not isinstance(result, dict) or result.get("kind") != "result" or not isinstance(result.get("plan"), dict):
        return None
    return sorted({vocabulary.contexts[m["context"]["id"]].term for m in result["plan"].get("measures", [])
                   if m["context"]["id"] in vocabulary.contexts})


def test_the_terms_in_the_bindings_are_the_terms_asks_plan_reads(eng, monkeypatch):
    from kge.services.ask.engine.openitems import report
    compared, differ = 0, []
    for question in [*(q for q, _ in golden_questions()), *(w["question"] for w in WORDINGS)]:
        planned = asks_plan_terms(eng, question, monkeypatch)
        if planned is None:
            continue
        bound = sorted({b["term"] for b in report(eng, question, ASKER)["bindings"] if b.get("term")})
        compared += 1
        if bound != planned:
            differ.append((question, planned, bound))
    assert compared >= 80 and not differ, differ[:5]


def test_each_suite_wording_gives_the_bound_terms_and_open_items_it_records(eng):
    from kge.services.ask.engine.openitems import report
    moved = []
    for wording in WORDINGS:
        read = report(eng, wording["question"], ASKER)
        got = {"bound_terms": sorted({b["term"] for b in read["bindings"] if b.get("term")}),
               "open_items": [item["item"] for item in read["open_items"]]}
        assert set(wording["kge"]) == set(got) | UNGRADED, wording["question"]
        recorded = {key: wording["kge"][key] for key in got}
        if got != recorded:
            moved.append((wording["question"], recorded, got))
    assert not moved, moved[:5]


def test_without_the_flag_context_is_unchanged_and_with_it_bindings_validate(client):
    from kge.services.ask.models import ContextResponse
    for question in ("What was NNA for June 2025?", "Show me NNA by segment", "What was current FY25 NNA?"):
        for compact in (False, True):
            plain = context(client, question, compact=compact)
            assert "bindings" not in plain and "open_items" not in plain
            flagged = context(client, question, compact=compact, open_items=True)
            assert {k: v for k, v in flagged.items() if k not in ("bindings", "open_items")} == plain
            ContextResponse.model_validate(flagged)
            assert all(b["kind"] for b in flagged["bindings"])


def test_nna_for_june_2025_lists_nnas_readable_meanings_with_the_alias_default(client):
    package = context(client, "What was NNA for June 2025?", open_items=True)
    [term] = items(package, "term")
    assert term["resolution"] == "default" and term["text"] == "NNA"
    assert [o["label"] for o in term["options"]] == NNA_TERMS
    assert [o["default_alias"] for o in term["options"]] == [True, False, False, False]
    assert all(o["definition"] for o in term["options"]) and "suggested" not in json.dumps(term)
    [nna] = [b for b in package["bindings"] if b["kind"] == "term"]
    assert (nna["term"], nna["by"]) == ("Net New Assets", "default_alias")
    assert items(package, "period") == []


def test_a_qualified_mention_binds_explicitly_and_opens_no_meaning(client):
    for question in ("certified Q2 2025 NNA", "certified Investor Services NNA for Q2 2024", "certified Q2-2024 NNA"):
        package = context(client, question, open_items=True)
        assert items(package, "term") == [], question
        assert {b["term"] for b in package["bindings"] if b["kind"] == "term"} == {"Certified NNA"}, question
        assert all(b["by"] == "explicit" for b in package["bindings"] if b["kind"] == "term"), question
    filtered = context(client, "certified Investor Services NNA for Q2 2024", open_items=True)
    assert [b["text"] for b in filtered["bindings"] if b["kind"] == "filter"] == ["Investor Services"]


def test_periods_a_default_newest_first_and_one_after_the_latest_covered(client):
    [default] = items(context(client, "Show me NNA", open_items=True), "period")
    assert default["resolution"] == "default" and default["grain"] == "month"
    months = [o["value"] for o in default["options"]]
    assert months == sorted(months, reverse=True) and months[0] == "2025-06"
    for question in ("What was NNA for Q3 2025?", "What was certified NNA for Q1 2026?"):
        [after] = items(context(client, question, open_items=True), "period")
        assert after["resolution"] == "unanswerable" and after["latest"] == {"value": "2025-06", "label": "June 2025"}, question


def test_by_segment_names_the_declared_segment_dimensions(client):
    [grouping] = items(context(client, "Show me NNA by segment", open_items=True), "grouping")
    assert grouping["text"] == "segment" and grouping["resolution"] == "clarify" and len(grouping["options"]) > 1
    assert all(any("segment" in p for p in o["phrases"]) for o in grouping["options"])


def test_the_rules_items_stand_with_a_model_and_beside_another_decline(client, monkeypatch):
    """The grouping, period and term items the rules' reading finds are reported whether or not a model is configured,
    and beside a decline on another ground: the period one names past the latest covered month, and the default
    period where it names none."""
    import kge.app as appmod
    latest = {"value": "2025-06", "label": "June 2025"}
    for question in ("What was the retail NNA for Q1 2026 by segment?", "What was NNA for Q3 2025 by segment?"):
        package = context(client, question, open_items=True)
        assert [i["item"] for i in package["open_items"] if i["resolution"] == "unanswerable"] == ["question", "period"], question
        [period] = items(package, "period")
        assert period["latest"] == latest and "reason" not in period and len(items(package, "grouping")) == 1, question
    for question in ("Show me NNA by Finance", "Show me NNA by segment"):
        [period] = items(context(client, question, open_items=True), "period")
        assert period["resolution"] == "default" and period["options"][0] == latest, question
    for question in ("NNA for calendar Q1 2026 by segment", "What was NNA for calendar Q1 2026?"):
        [period] = items(context(client, question, open_items=True), "period")
        assert period["resolution"] == "unanswerable" and period["latest"] == latest, question
    assert items(context(client, "NNA for calendar Q1 2025 by segment", open_items=True), "period") == []
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setattr(appmod, "_combined", None)
    assert appmod.combined().llm is not None
    for question, expected in (("Show me NNA by segment", ["term", "period", "grouping"]),
                               ("What was the retail NNA for Q1 2025 by segment?", ["grouping"]),
                               ("Show me certified NNA by sub segment for Q1 2025", ["grouping"]),
                               ("What was the retail NNA for Q1 2026 by segment?", ["period", "grouping"])):
        assert [i["item"] for i in context(client, question, open_items=True)["open_items"]] == expected, question


def test_a_declared_grouping_spelt_otherwise_names_its_one_dimension(client):
    declared = context(client, "Show me certified NNA by sub-segment for Q1 2025", open_items=True)
    assert items(declared, "grouping") == [] and [b["kind"] for b in declared["bindings"]].count("grouping") == 1
    for spelling in ("sub segment", "subsegment"):
        package = context(client, f"Show me certified NNA by {spelling} for Q1 2025", open_items=True)
        [grouping] = items(package, "grouping")
        assert grouping["resolution"] == "clarify", spelling
        assert len(grouping["options"]) == 1 and "by sub-segment" in grouping["options"][0]["phrases"], spelling


def test_a_person_who_may_not_read_the_unit_gets_the_refusal_alone_in_asks_words(client):
    question = "What was NNA for June 2025?"
    package = context(client, question, who=NO_WEALTH, open_items=True)
    assert [(i["item"], i["resolution"]) for i in package["open_items"]] == [("access", "unanswerable")]
    asked = client.post("/ask", json={"question": question}, headers=client.as_(NO_WEALTH)).json()
    assert package["open_items"][0]["reason"] == asked["reason"] and "wealth" not in package["open_items"][0]["reason"]


def test_a_decline_is_asks_own_reason(client):
    for question in ("Show me NNA by segment", "What was NNA for FY0001?", "What was NNA for Q3 2025?", "current quarter to date"):
        [decline] = [i for i in context(client, question, open_items=True)["open_items"] if i["resolution"] == "unanswerable"]
        asked = client.post("/ask", json={"question": question}, headers=client.as_(ASKER)).json()
        assert decline["reason"] == asked["reason"], question


def test_a_restricted_column_question_is_asks_refusal(client):
    for who in (NO_WEALTH, ASKER):
        question = "Show me customer first names in retail"
        package = context(client, question, who=who, open_items=True)
        asked = client.post("/ask", json={"question": question}, headers=client.as_(who)).json()
        assert asked["behaviour"] in ("refuse", "blocked_access"), asked["behaviour"]
        assert [(i["item"], i["reason"]) for i in package["open_items"]] == [("access", asked["reason"])], who


def golden_questions():
    from kge.pipeline.settings import ROOT as root
    out = []
    for name in ("golden", "heldout", "golden_wealth_v1", "sage_v1", "golden_figures_v1", "golden_documents_v1", "heldout_documents_v1"):
        path = root / "build" / f"{name}.json"
        if not path.exists():
            continue
        held = json.loads(path.read_text())
        items = held if isinstance(held, list) else held.get("items") or held.get("questions") or []
        out += [(item["question"], item.get("principal")) if isinstance(item, dict) else (item, None) for item in items
                if isinstance(item, (dict, str))]
    return out


def test_no_open_item_declines_what_ask_answers_and_a_shared_decline_is_in_asks_words(client):
    """707.3's invariant, over every golden set and the suite's sample: an unanswerable or clarify item only where ask
    declines, and the item's reason equal to ask's wherever both decline."""
    from kge.services.ask.engine.openitems import DECLINES
    asked = [*golden_questions(), *((w["question"], None) for w in WORDINGS)]
    assert len(asked) > 100
    wrong = []
    for question, principal in asked:
        who = principal if principal and "@" in principal else ASKER
        answer = client.post("/ask", json={"question": question}, headers=client.as_(who))
        if answer.status_code >= 500:
            continue
        answer = answer.json()
        package = context(client, question, who=who, open_items=True)
        declines = [i for i in package["open_items"] if i["resolution"] in ("unanswerable", "clarify") and i["item"] != "grouping"]
        if answer.get("behaviour") not in DECLINES:
            if declines:
                wrong.append(("declines an answer", question, answer.get("behaviour"), declines[0]["item"]))
        else:
            for item in declines:
                if item.get("reason") is not None and item["reason"] != answer.get("reason"):
                    wrong.append(("reason", question, item["reason"], answer.get("reason")))
    assert not wrong, wrong[:5]


def test_with_a_model_no_open_item_declines_what_ask_answers(client, monkeypatch):
    """The invariant with MODEL_PROVIDER=mock, ask's default arm on the deployment's model: the open items run ask dry
    on the served Combined, so no unanswerable or clarify item stands where ask answers, its model's answers above all,
    and a shared decline is in ask's words."""
    import kge.app as appmod
    from kge.services.ask.engine.openitems import DECLINES
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setattr(appmod, "_combined", None)
    assert appmod.combined().llm is not None
    asked = ["Total retail revenue for June 2025 thanks", *(q for q, _ in golden_questions()[::3]),
             *(w["question"] for w in WORDINGS)]
    wrong, by_model = [], 0
    for question in asked:
        answer = client.post("/ask", json={"question": question}, headers=client.as_(ASKER))
        if answer.status_code >= 500:
            continue
        answer = answer.json()
        by_model += answer.get("answered_by") == "model"
        package = context(client, question, open_items=True)
        declines = [i for i in package["open_items"] if i["resolution"] in ("unanswerable", "clarify") and i["item"] != "grouping"]
        if answer.get("behaviour") not in DECLINES:
            if declines:
                wrong.append(("declines an answer", question, answer.get("answered_by"), declines[0]["item"]))
        else:
            for item in declines:
                if item.get("reason") is not None and item["reason"] != answer.get("reason"):
                    wrong.append(("reason", question, item["reason"], answer.get("reason")))
    assert by_model > 0
    assert not wrong, wrong[:5]


def test_a_turned_qualifier_gets_no_figure_from_ask_with_or_without_a_model(client, monkeypatch):
    """Review of PR #728, finding 3 (#714, the coordinator's decision 1): a wording that negates or hedges the
    qualifier it names ("not certified", "never certified", "non-current", "uncertified"; the suite marks each as guarded) gets
    no card from Cerebro where KGE reports no open meaning, so ask must give it no figure: it declines, with
    MODEL_PROVIDER none and mock alike. The one wording ask does answer is the suite's named exception to #707
    acceptance 16, held here by name so a second one fails."""
    import kge.app as appmod
    from kge.services.ask.engine.openitems import DECLINES
    guarded = [w for w in SUITE["wordings"] if w["verdicts"].get("guarded")]
    excepted = sorted(w["question"] for w in guarded if w["verdicts"]["hotfix"] == "pass" and w["verdicts"]["now"] == "fail")
    assert len(guarded) == 194 and excepted == ["What was the still to be certified Q2 2025 NNA?"]
    for provider in ("none", "mock"):
        monkeypatch.setenv("MODEL_PROVIDER", provider)
        monkeypatch.setattr(appmod, "_combined", None)
        assert (appmod.combined().llm is not None) == (provider == "mock")
        answered = []
        for wording in guarded:
            answer = client.post("/ask", json={"question": wording["question"]}, headers=client.as_(ASKER))
            assert answer.status_code == 200, (provider, wording["question"], answer.text)
            answer = answer.json()
            if answer.get("behaviour") not in DECLINES or answer.get("result"):
                answered.append(wording["question"])
        assert sorted(answered) == excepted, (provider, answered[:5])


def test_a_period_decline_reads_only_a_pinned_current_calendar(client, monkeypatch):
    """A period ask has not released is reported from the calendar the engine pinned only where the chosen runtime's
    was pinned and holds the release's own data: on a runtime whose calendar was not pinned, or holds live data, ask
    would query the warehouse for it, so there is no period item and no covered months."""
    import kge.app as appmod
    graph = appmod.engine().g
    question = "What was NNA for Q3 2025?"
    assert [i["resolution"] for i in items(context(client, question, open_items=True), "period")] == ["unanswerable"]
    monkeypatch.setattr(graph, "_release_periods", {})
    assert items(context(client, question, open_items=True), "period") == []
    assert items(context(client, "Show me NNA", open_items=True), "period") == []
    monkeypatch.undo()
    listed = appmod.engine().w.list_runtimes
    monkeypatch.setattr(appmod.engine().w, "list_runtimes",
                        lambda readable_units=None: [{**r, "kind": "external"} for r in listed(readable_units)])
    assert items(context(client, question, open_items=True), "period") == []


def test_history_products_and_runtimes_reach_the_reading(client, monkeypatch):
    from kge.services.ask import service
    first = client.post("/ask", json={"question": "What was NNA for June 2025?"}, headers=client.as_(ASKER)).json()
    seen = {}
    report = service.report

    def recorded(engine, question, principal, purpose, history, products, runtimes, combined):
        seen.update(purpose=purpose, history=list(history), products=products, runtimes=runtimes, combined=combined)
        return report(engine, question, principal, purpose, history, products, runtimes, combined)
    monkeypatch.setattr(service, "report", recorded)
    body = {"question": "And for May 2025?", "purpose": "analysis", "history": first["history"],
            "products": ["wealth.TRAN_FACT_1"], "runtimes": ["reference"], "open_items": True}
    package = context(client, **body)
    assert seen["products"] == ["wealth.TRAN_FACT_1"] and seen["runtimes"] == ["reference"] and seen["purpose"] == "analysis"
    assert len(seen["history"]) == len(first["history"]) > 0
    import kge.app as appmod
    assert seen["combined"] is appmod.combined()
    assert {"bindings", "open_items"} <= set(package)


def test_fiscal_year_wordings_get_a_package(client):
    for question in ("What was current FY25 NNA?", "Show current FY25 NNA", "What was certified FY2025 NNA?",
                     "What was NNA for FY0000?", "What was NNA for FY0001?", "What was NNA for FY00?", "What was NNA for FY1850?",
                     "Revenue for FY25", "What was revenue for fiscal year 2025?", "Revenue for FY00", "What was revenue for FY2100?"):
        for flag in (False, True):
            r = client.post("/context", json={"question": question, "open_items": flag}, headers=client.as_(ASKER))
            assert r.status_code == 200, (question, flag, r.text[:200])


def test_a_fiscal_year_on_a_fiscal_quarter_table_is_its_twelve_months(client):
    """Revenue's period column holds fiscal quarters: the context path says the year's months from the pack's April
    start, and gives no clause of its own; ask's period clause is untouched."""
    block = context(client, "Revenue for FY25")["block"]
    assert "FY25 read as 2024-04-01 to 2025-03-31" in block


def test_a_bare_fiscal_year_is_the_packs_fiscal_year_in_the_context_package(client):
    """Fiscal year 2024 on Meridian's April year is April 2023 to March 2024 (decision 19): the definitions in force from
    November 2024 are not in its window, as they are in fiscal year 2025's. Develop read it as 2024-Q4."""
    assert context(client, "What was NNA for fiscal year 2024?")["in_window"] == []
    assert context(client, "What was certified NNA for fiscal year 2025?")["in_window"] != []


def test_map_term_resolves_an_alias_and_leaves_a_terms_own_map_unchanged(client):
    nna = client.get("/map/NNA", headers=client.as_(ASKER)).json()
    assert [t["label"] for t in nna["resolves_to"]] == NNA_TERMS and nna["resolves_to"][0]["default_alias"] is True
    assert all(set(t) == {"id", "label", "definition", "default_alias"} for t in nna["resolves_to"])
    assert "resolves_to" not in client.get("/map/Certified NNA", headers=client.as_(ASKER)).json()
    folded = client.get("/map/net new assets", headers=client.as_(ASKER)).json()["resolves_to"]
    assert [(t["label"], t["default_alias"]) for t in folded] == [("Net New Assets", False)]
    assert "resolves_to" not in client.get("/map/NNA", headers=client.as_(NO_WEALTH)).json()


def test_a_filter_phrase_on_a_restricted_column_is_never_found(client, monkeypatch):
    from kge.services.http import served
    graph = served.engine().g
    domain = graph.domain()
    restricted = [*domain["restricted"], ("wealth", "CLIENT_CD_ROLLUP_1", "SEGMT_LVL_ID5")]
    monkeypatch.setattr(graph, "domain", lambda: {**domain, "restricted": restricted})
    hits = client.post("/search", json={"query": "Retail Investor", "kinds": ["filter"]}, headers=client.as_(ENGINEER)).json()["hits"]
    assert not any(h["title"] == "Retail Investor" for h in hits)


def test_search_names_a_filter_phrase_only_when_asked(client):
    for who, column in ((ASKER, False), (ENGINEER, True)):
        hits = client.post("/search", json={"query": "Retail Investor", "kinds": ["filter"]}, headers=client.as_(who)).json()["hits"]
        [hit] = [h for h in hits if h["title"] == "Retail Investor"]
        assert hit["kind"] == "filter" and hit["value"] == "S4100" and hit["label"]
        assert ("column" in hit) is column and ("CLIENT_CD_ROLLUP" in json.dumps(hit)) is column
    plain = client.post("/search", json={"query": "Retail Investor"}, headers=client.as_(ASKER)).json()["hits"]
    assert not any(h["kind"] == "filter" or "label" in h for h in plain)
    unread = client.post("/search", json={"query": "Retail Investor", "kinds": ["filter"]}, headers=client.as_(NO_WEALTH)).json()
    assert unread == {"hits": []}


def test_mcp_gives_what_http_gives(client):
    import anyio.lowlevel

    import kge.app as appmod
    from kge.adapters.kgetools.mcp import McpKgeTools
    from kge.services.ask.models import ContextResponse
    loop = client.portal.call(anyio.lowlevel.current_token)
    tools = McpKgeTools("http://kge", client.token(ASKER), app=appmod.app, loop=loop)
    question = "What was NNA for June 2025?"
    over_mcp = tools.call("/mcp", "get_context", {"question": question, "open_items": True})
    ContextResponse.model_validate(over_mcp)
    over_http = context(client, question, open_items=True)
    assert over_mcp["bindings"] == over_http["bindings"] and over_mcp["open_items"] == over_http["open_items"]
    assert tools.call("/mcp", "map_term", {"term": "NNA"})["resolves_to"] == client.get("/map/NNA", headers=client.as_(ASKER)).json()["resolves_to"]
    searched = tools.call("/mcp/search", "search", {"query": "Retail Investor", "kinds": ["filter"]})
    assert searched == client.post("/search", json={"query": "Retail Investor", "kinds": ["filter"]}, headers=client.as_(ASKER)).json()
