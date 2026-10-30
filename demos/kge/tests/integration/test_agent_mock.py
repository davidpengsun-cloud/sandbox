"""The model path end to end on the mock model: MODEL_PROVIDER=mock.

    .venv/bin/python -m pytest tests/integration/test_agent_mock.py -q

No model key is configured for this demo, so the reference agent's model path
is proven with the mock (kge/adapters/model/mock.py): offline, deterministic, reading
only the prompt it is sent. Through Agent.ask, in both arms, it shows that the
arms differ only by their context, and that each adversarial mode is caught by
the seam: the gate refuses and the retry is told why, a reply that is not JSON
is an abstention, the number check throws invented wording away, a citation
that names no source is rejected. Every answer and trace says mock. This proves
the seam, not a model's quality.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import CONTEXT_CLOSE, CONTEXT_OPEN, PROMPTS, Agent
from kge.eval import sets
from kge.adapters.model import mock as mockllm
from kge.adapters.model import LLM, PROVIDERS, make_llm
from tests.scratch import BUILD, profile_of, scratch_build

FIG = "Total retail revenue for June 2025"
CROSS = "Combined Retail and SME revenue for June 2025"
BANK = "Total revenue across the bank for the quarter to 30 June 2025"
ACTIVE = "How many retail customers were active in June 2025?"
DEFINE = "What is the definition of an active customer?"
EMAIL = "What is the email address of customer 118?"
SCORE = "What was the Digital Engagement Score for June 2025?"
GROUP = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
ARMS = ("control", "treatment")
GOLDEN = {i["id"]: i for i in sets.items(BUILD / "golden.json")}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """The mock never posts: any HTTP call fails the test."""
    def no_network(*a, **k):
        raise AssertionError("the mock made a network call")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    monkeypatch.delenv("MODEL_SEED", raising=False)


def mock(mode: str = "careful") -> LLM:
    return LLM("mock", "mock", mode=mode)


def ask(eng, question: str, arm: str, mode: str = "careful", principal: str = "analyst_enterprise") -> dict:
    agent = Agent(eng, llm=mock(mode))
    return runtrace.run("agent", lambda: agent.ask(question, principal, arm=arm), graph=eng.g)


def without_context(prompt: str) -> str:
    head, rest = prompt.split(CONTEXT_OPEN, 1)
    return head + rest.split(CONTEXT_CLOSE, 1)[1]


def test_mock_is_a_provider_and_make_llm_returns_it(monkeypatch):
    assert "mock" in PROVIDERS
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("MOCK_MODE", raising=False)
    llm = make_llm()
    assert llm is not None and llm.provider == "mock" and llm.name == "mock:mock" and llm.mode == "careful"
    ident = llm.identity()
    assert ident["provider"] == "mock" and ident["mode"] == "careful" and ident["seed"] == 7 and ident["temperature"] == 0
    monkeypatch.setenv("MOCK_MODE", "bad_marker")
    assert make_llm().identity()["config_hash"] != ident["config_hash"], "the mode shapes the replies, so it is in the hash"


def test_the_mock_is_named_mock_whatever_model_name_is_left_set(eng, monkeypatch):
    """Switching a configured model's .env to MODEL_PROVIDER=mock leaves MODEL_NAME set, and the mock never takes it
    on."""
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_NAME", "claude-opus-4")
    monkeypatch.delenv("MOCK_MODE", raising=False)
    llm = make_llm()
    assert llm.name == "mock:mock" and llm.identity()["model"] == "mock"
    monkeypatch.delenv("MODEL_NAME")
    assert make_llm().identity() == llm.identity(), "nor does it change the mock's config hash"
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a["agent"]["sql_source"] == a["phrased_by"] == a["versions"]["model"] == a["run_trace"]["llm"]["name"] == "mock:mock"
    assert "claude-opus-4" not in json.dumps(a)
    t = a["run_trace"]
    assert a["agent"]["mode"] == t["agent"]["mode"] == "mock" and t["llm"]["provider"] == "mock"
    assert t["config"]["sql_source"] == "model" and "sql_source=model" in t["config"]["label"], t["config"]


def test_an_unknown_mock_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_MODE", "sneaky")
    with pytest.raises(ValueError, match="MOCK_MODE must be one of careful, "):
        make_llm()


def test_the_mock_answers_only_the_agents_prompts():
    with pytest.raises(RuntimeError, match="only the reference agent's"):
        mock().complete("Glossary terms: Revenue\nReturn JSON only: {\"terms\": [str]}")


def test_the_tagger_stays_on_rules_under_the_mock(monkeypatch):
    from kge.pipeline.tagger import ModelTagger, make_tagger
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    assert not isinstance(make_tagger(["Revenue"]), ModelTagger)


@pytest.fixture(scope="module")
def prompts(eng):
    """(context block, plan prompt) per question and arm."""
    agent = Agent(eng, llm=None)
    out = {}
    for q in (FIG, CROSS):
        for arm in ARMS:
            ctx = agent.context(q, "analyst_enterprise", "analysis", arm)
            out[q, arm] = (ctx["block"], agent.prompt("sql", q, ctx["block"]))
    return out


def test_the_reply_is_a_function_of_the_prompt_and_differs_only_by_context(prompts):
    (cb, cp), (tb, tp) = prompts[CROSS, "control"], prompts[CROSS, "treatment"]
    assert without_context(cp) == without_context(tp)
    assert mockllm.reply(cp) == mockllm.reply(cp), "deterministic"
    assert mockllm.reply(cp) != mockllm.reply(tp)
    assert mockllm.reply(cp.replace(cb, tb)) == mockllm.reply(tp), "the control's prompt with the treatment's context is the treatment's"
    assert mockllm.reply(tp.replace(tb, cb)) == mockllm.reply(cp)


def test_the_control_reply_guesses_from_the_schema_and_the_treatment_reads_the_package(prompts):
    c, t = (json.loads(mockllm.reply(prompts[FIG, arm][1])) for arm in ARMS)
    assert c["behaviour"] == t["behaviour"] == "figure"
    assert c["columns"] == t["columns"] == {"retail": "monthly_revenue.total_revenue_usd"}
    for r in (c, t):
        assert r["sql"]["retail"].startswith("SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue")
        assert "DATE '2025-06-01'" in r["sql"]["retail"]
    c, t = (json.loads(mockllm.reply(prompts[CROSS, arm][1])) for arm in ARMS)
    assert t["joins_refused"] == ["retail-sme"] and c["joins_refused"] == [], "only the graph says the units do not join"
    assert t["reason"].startswith("Same grain, currency and calendar") and c["reason"] == "The question asks for their total."


@pytest.mark.parametrize("arm", ARMS)
def test_a_figure_on_the_mock_is_labelled_mock_everywhere(eng, arm):
    a = ask(eng, FIG, arm)
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67 and a["column"] == "monthly_revenue.total_revenue_usd"
    assert a["agent"]["mode"] == "mock" and a["agent"]["sql_source"] == "mock:mock" and a["phrased_by"] == "mock:mock"
    assert "model:" not in json.dumps({k: v for k, v in a.items() if k != "run_trace"}), "a mock run is never labelled a model's"
    assert a["versions"]["model"] == "mock:mock" and a["answer_text"].startswith("Retail Banking: 13,287.67")
    rt = a["run_trace"]
    assert rt["agent"] == {"arm": arm, "mode": "mock", "prompt_versions": PROMPTS}
    assert rt["llm"]["provider"] == "mock" and rt["llm"]["mode"] == "careful" and rt["llm"]["seed"] == 7
    assert [(c["stage"], c["prompt_version"], c["served_model"]) for c in rt["llm_calls"]] == [
        ("plan", PROMPTS["sql"], "mock"), ("render", PROMPTS["phrase"], "mock")]
    assert rt["number_check"] == {"passed": True, "stray_numbers": 0, "bad_markers": 0, "bad_documents": 0}


def test_the_tokens_are_counted_from_the_text(eng, prompts):
    rt = ask(eng, FIG, "treatment")["run_trace"]
    plan = rt["llm_calls"][0]
    assert plan["tokens_in"] == len(prompts[FIG, "treatment"][1].split()) and plan["tokens_out"] > 0
    assert rt["tokens"]["in"] == sum(c["tokens_in"] for c in rt["llm_calls"])


def test_the_arms_cite_what_their_context_lets_them(eng):
    c, t = ask(eng, FIG, "control"), ask(eng, FIG, "treatment")
    assert t["cite"] == ["MD-REVENUE"] and t["citations"][0]["tier"] == "gold"
    assert c["cite"] and "MD-REVENUE" not in c["cite"], "the control cites what plain search found"
    rc, rt = c["run_trace"]["llm_calls"][0], t["run_trace"]["llm_calls"][0]
    assert rc["prompt_hash"] == rt["prompt_hash"] and rc["context_hash"] != rt["context_hash"]


def test_across_units_the_treatment_reads_the_verdict_and_the_control_cannot(eng):
    t, c = ask(eng, CROSS, "treatment"), ask(eng, CROSS, "control")
    assert t["behaviour"] == "sum_with_verdict" and t["figure"] == float(GOLDEN["G022"]["expect"]["figure"])
    assert t["joins_refused"] == ["retail-sme"] and t["commensurable"] is True
    assert c["joins_refused"] == [], "the schema says nothing about joins between units"
    bank = ask(eng, BANK, "treatment")
    assert bank["behaviour"] == "parts_with_reason" and bank["commensurable"] is False
    assert {u: round(v, 2) for u, v in bank["parts"].items()} == {u: float(v) for u, v in GOLDEN["G021"]["expect"]["parts"].items()}


def test_across_units_each_treatment_figure_carries_what_its_context_says_of_its_column_and_the_controls_nothing(eng):
    """The page's parts table shows each part's grain and calendar beside a reason that speaks of them; graph off
    nothing gives them, so nothing is filled in rather than a guess."""
    meta = ("grain", "currency", "calendar", "conversion")
    t, c = ask(eng, CROSS, "treatment"), ask(eng, CROSS, "control")
    assert [(f["unit"], f["grain"], f["calendar"], f["conversion"]) for f in t["figures"]] == [
        ("retail", "month", "calendar", "none"), ("sme", "month", "calendar", "none")]
    assert all(f["currency"] for f in t["figures"]) and t["reason"].startswith("Same grain, currency and calendar")
    assert not any(k in f for f in c["figures"] for k in meta), "graph off, a figure's grain or calendar would be a guess"
    bank = {f["unit"]: f for f in ask(eng, BANK, "treatment")["figures"]}
    assert (bank["corporate"]["grain"], bank["corporate"]["calendar"]) == ("quarter", "fiscal_april")
    assert bank["corporate"]["conversion"].startswith("period_end_rate")


@pytest.mark.parametrize("who, denied", [("analyst_retail", ["sme", "corporate", "wealth"]), ("steward", ["sme", "corporate"])])
@pytest.mark.parametrize("question", [BANK, "Bank revenue for calendar Q1 2025", "Total revenue for June 2025",
                                      "How many customers does the bank have?"])
def test_graph_on_a_question_needing_a_unit_the_principal_cannot_read_gets_no_partial_figure(eng, who, denied, question):
    """With no unit named, the question needs every unit its term is certified in, as the rules and /answer decide; the
    seam holds it all or nothing, with no query run."""
    ctx = Agent(eng, llm=None).context(question, who, "analysis", "treatment")
    assert f"You may not read {', '.join(denied)}" in ctx["block"]
    rules, a = Agent(eng, llm=None).ask(question, who, arm="treatment"), ask(eng, question, "treatment", principal=who)
    assert a["behaviour"] == rules["behaviour"] == "blocked_access" and a["denied_warehouses"] == rules["denied_warehouses"] == denied
    assert a["attempts"] == [] and "figure" not in a and "figures" not in a


@pytest.mark.parametrize("arm", ARMS)
def test_a_count_measures_its_flag(eng, arm):
    a = ask(eng, ACTIVE, arm)
    assert a["behaviour"] == "figure" and a["column"] is not None
    if arm == "treatment":
        assert a["figure"] == GOLDEN["G003"]["expect"]["figure"] and a["column"] == "customer_activity.is_active_flag"


def test_the_treatment_refuses_restricted_data_before_any_query_the_control_is_stopped_by_the_gate(eng):
    t = ask(eng, EMAIL, "treatment")
    assert t["behaviour"] == "refuse" and t["attempts"] == [] and "restricted personal data" in t["reason"]
    c = ask(eng, EMAIL, "control")
    assert [(x["try"], x["passed"]) for x in c["attempts"]] == [(1, False)]
    assert any("restricted personal data" in p for p in c["attempts"][0]["problems"])
    assert c["behaviour"] == "refuse" and "figure" not in c
    assert "(POL-DATA-CLASS)" in c["answer_text"] and c["phrased_by"] == "mock:mock" and c["run_trace"]["number_check"]["passed"]


def test_the_treatment_routes_a_new_term_and_cites_definitions_by_tier(eng):
    a = ask(eng, SCORE, "treatment")
    assert a["behaviour"] == "abstain_route_steward" and "steward" in a["answer_text"] and a["attempts"] == []
    d = ask(eng, DEFINE, "treatment")
    assert d["behaviour"] == "cite_certified" and "MD-ACTIVE-CUSTOMER" in d["cite"] and d["phrased_by"] == "mock:mock"
    assert {c["tier"] for c in d["citations"]} <= {"gold", "silver"} and not d["citations_rejected"]


def test_once_the_steward_accepts_the_new_term_the_package_has_it_once_and_routes_it_nowhere(tmp_path):
    """The README's walkthrough, step 1: accepted, the term is in the glossary with no certified column, so the
    treatment abstains on that, as its rules do."""
    from kge.services.ask.engine.core import Engine, parse
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        cand = next(r for r in e.g.steward_queue() if r["family"] == "candidate_term" and r["doc"] == "EM-004")
        e.g.steward_decide(cand["tag"], "steward_glossary", "accept", definition="Logins per active customer per month, capped at 30")
        e.refresh()
        assert parse(SCORE, e.term_names, e).terms_found == ["Digital Engagement Score"]
        block = Agent(e).context(SCORE, "analyst_enterprise", "analysis", "treatment")["block"]
        assert block.count("Digital Engagement Score: Logins per active customer per month") == 1
        assert "Not glossary terms" not in block and "steward queue" not in block
        rules = Agent(e, llm=None).ask(SCORE, "analyst_enterprise", arm="treatment")
        assert rules["behaviour"] == "abstain" and "no certified implementation" in rules["reason"]
        a = ask(e, SCORE, "treatment")
        assert a["behaviour"] == "abstain" and "not certified in" in a["answer_text"] and "steward" not in a["answer_text"]
    finally:
        e.close()


def test_a_document_the_context_names_is_a_name_in_the_wording_not_a_number(eng):
    """G041's package names EM-004, the term's only definition, outside its numbered sources: 004 is no figure."""
    from kge.eval import metrics
    a = ask(eng, SCORE, "treatment")
    assert "EM-004" in a["answer_text"] and a["phrased_by"] == "mock:mock" and a["run_trace"]["number_check"]["passed"]
    assert "EM-004" in next(t for t in a["trace"] if t["step"] == "context")["named"] and metrics.numbers(a)["shown"] == 0


def test_two_measures_are_refused_by_the_treatment_as_by_its_rules(eng):
    q = GOLDEN["G029"]
    a = ask(eng, q["question"], "treatment")
    assert a["behaviour"] == "refuse" and a["attempts"] == [] and "figure" not in a
    assert a["joins_refused"] == q["expect"]["joins_refused"] == ["corporate-wealth"]
    assert "never added together" in a["reason"]


@pytest.mark.parametrize("question, says", [
    (GROUP, "Not answered: the question asks about one Retail Banking customer group: how many meet Active Customer, "
            "how many of those had a Complaint, and their Average Balance and Fee Income."),
    (GROUP.replace("Retail", "SME"), "Not certified in SME Banking: Average Balance, Complaint. The answer would rest on a guess."),
])
def test_the_treatment_reads_a_customer_group_from_the_package_and_gives_no_one_figure(eng, question, says):
    """These are the page's first two chips; graph off nothing says the question is a group, so the control gives one
    figure, which is the gap the A/B measures."""
    t = ask(eng, question, "treatment")
    assert t["behaviour"] == "abstain" and t["reason"].startswith(says) and t["answer_text"].startswith(says), t["reason"]
    assert t["attempts"] == [] and not {"figure", "figures"} & set(t) and t["cite"] == []
    assert t["agent"]["mode"] == "mock" and t["phrased_by"] == "mock:mock"
    c = ask(eng, question, "control")
    assert c["behaviour"] == "figure" and c["agent"]["mode"] == "mock"


@pytest.mark.parametrize("arm", ARMS)
def test_a_unit_out_of_scope_is_refused_and_nothing_runs(eng, arm):
    a = ask(eng, "Total SME revenue for June 2025", arm, principal="analyst_retail")
    assert a["behaviour"] == "refuse" and a["attempts"] == [] and "figure" not in a


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("mode, says", [
    ("restricted_column", "restricted personal data"),
    ("undeclared_join", "is not a declared join"),
    ("constant_figure", "aggregate"),
    ("scaled_figure", "aggregate"),
])
def test_a_bad_first_query_is_refused_by_the_gate_and_the_retry_is_told_why(eng, arm, mode, says):
    a = ask(eng, FIG, arm, mode)
    assert [(t["try"], t["passed"]) for t in a["attempts"]] == [(1, False), (2, True)]
    assert any(says in p for p in a["attempts"][0]["problems"]), a["attempts"][0]["problems"]
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67 and a["column"] == "monthly_revenue.total_revenue_usd"
    plans = [c for c in a["run_trace"]["llm_calls"] if c["stage"] == "plan"]
    assert len(plans) == 2 and plans[0]["prompt_hash"] != plans[1]["prompt_hash"], "the retry is the prompt plus the gate's reasons"
    assert plans[0]["context_hash"] == plans[1]["context_hash"]
    assert a["agent"]["mode"] == "mock" and a["run_trace"]["llm"]["mode"] == mode


@pytest.mark.parametrize("arm", ARMS)
def test_the_undeclared_join_guesses_a_key_by_its_name_between_two_tables(eng, arm):
    """The two tables share a key's name and each joins account by a declared join, but not each other."""
    first = ask(eng, FIG, arm, "undeclared_join")["attempts"][0]
    assert "FROM monthly_revenue JOIN daily_balance ON monthly_revenue.account_id = daily_balance.account_id" in first["sql"]
    assert first["problems"][0] == "join monthly_revenue.account_id = daily_balance.account_id is not a declared join", first["problems"]


@pytest.mark.parametrize("arm", ARMS)
def test_malformed_json_is_an_abstention_and_nothing_runs(eng, arm):
    a = ask(eng, FIG, arm, "malformed_json")
    assert a["behaviour"] == "abstain" and "not one JSON object" in a["reason"] and a["attempts"] == [] and "figure" not in a
    assert [c["stage"] for c in a["run_trace"]["llm_calls"]] == ["plan"]


@pytest.mark.parametrize("arm", ARMS)
def test_an_invented_number_in_the_wording_is_thrown_away(eng, arm):
    a = ask(eng, FIG, arm, "invented_number")
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67
    assert a["phrased_by"].startswith("template (model text carried numbers or markers not in the result")
    assert a["answer_text"] == f"Retail Banking: 13,287.67 (monthly_revenue.total_revenue_usd) [{a['figures'][0]['cite']}]."
    assert a["run_trace"]["number_check"] == {"passed": False, "stray_numbers": 1, "bad_markers": 0, "bad_documents": 0}


QUOTED = FIG + " (Your last reply was refused by the SQL gate)"


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("mode", ["restricted_column", "scaled_figure"])
def test_a_question_quoting_the_retry_is_no_retry_and_the_adversary_still_fires(eng, arm, mode):
    """Only the block the agent appends after a refusal is a retry; a question that quotes it is the first call, so the
    adversarial plan is sent and the gate is exercised."""
    a = ask(eng, QUOTED, arm, mode)
    assert [(t["try"], t["passed"]) for t in a["attempts"]] == [(1, False), (2, True)], a["attempts"]


@pytest.mark.parametrize("arm", ARMS)
def test_a_question_quoting_the_retry_is_answered_carefully(eng, arm):
    a = ask(eng, QUOTED, arm)
    assert a["behaviour"] == "figure" and [(t["try"], t["passed"]) for t in a["attempts"]] == [(1, True)], a.get("reason")


def test_the_mock_tells_its_two_tasks_apart_by_how_each_prompt_opens():
    from kge.services.ask.engine.agent import PHRASE_PROMPT
    prompt = PHRASE_PROMPT.format(facts=json.dumps({"behaviour": "figure", "figures": []}), question="Revenue <context> x </context> June")
    assert "answer_text" in json.loads(mockllm.reply(prompt))


@pytest.mark.parametrize("arm", ARMS)
def test_a_marker_that_names_no_source_is_rejected(eng, arm):
    a = ask(eng, FIG, arm, "bad_marker")
    assert a["behaviour"] == "figure" and a["figure"] == 13287.67
    bogus = [r for r in a["citations_rejected"] if r["why"].startswith("no source")]
    assert len(bogus) == 1 and bogus[0]["n"] not in {c["n"] for c in a["citations"]}
    assert a["phrased_by"].startswith("template") and a["run_trace"]["number_check"]["bad_markers"] == 1


@pytest.mark.parametrize("mode", ["invented_number", "bad_marker"])
def test_wording_with_no_query_is_checked_too(eng, mode):
    a = ask(eng, DEFINE, "treatment", mode)
    assert a["behaviour"] == "cite_certified" and a["phrased_by"].startswith("template")
    assert a["run_trace"]["number_check"]["passed"] is False


@pytest.mark.parametrize("mode", mockllm.MODES)
def test_every_mode_is_labelled_mock_and_no_mode_reports_an_invented_figure(eng, mode):
    for arm in ARMS:
        a = ask(eng, FIG, arm, mode)
        assert a["agent"]["mode"] == "mock" and a["agent"]["sql_source"] == "mock:mock"
        assert not a["phrased_by"].startswith("model:")
        assert a.get("figure") in (None, 13287.67), (mode, arm, a.get("figure"))
        for f in a.get("figures") or []:
            assert f["column"], "a figure that ran names the column it measures"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.delenv("MOCK_MODE", raising=False)
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_the_api_runs_the_agent_on_the_mock_and_ask_stays_on_rules(client):
    who = client.as_("analyst_enterprise")
    for arm in ARMS:
        a = client.post("/agent", json={"question": FIG, "arm": arm}, headers=who).json()
        assert a["agent"]["mode"] == "mock" and a["run_trace"]["agent"]["mode"] == "mock" and a["figure"] == 13287.67
    r = client.post("/ask", json={"question": FIG}, headers=who).json()
    assert r["run_trace"]["llm"] is None and r["run_trace"]["llm_calls"] == []
    assert client.get("/presets", headers=who).json()["model_configured"] is False


def test_the_readme_says_how_to_run_the_mock_and_plug_in_a_model():
    """The trace's run config names the model stage the mock stands in for, so it is no label of who ran."""
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text().split("## The reference agent", 1)[1].split("\n## ", 1)[0]
    assert "MODEL_PROVIDER=mock make agent" in readme and "MOCK_MODE=" in readme
    assert "proves the seam, not" in readme and "MODEL_PROVIDER" in readme and "MODEL_API_KEY" in readme
    for mode in mockllm.MODES:
        assert f"`{mode}`" in readme, mode
    said = " ".join(readme.split())
    assert ("the agent's `mode` is `mock` in the answer and its trace, the trace's `llm` names `mock:mock`, and the answer's "
            "`sql_source` and `phrased_by` read `mock:mock`, never `model:`") in said
    assert ("The trace's run config names the model stages the mock stands in for (`sql_source=model`), so it is the "
            "agent's `mode`, not the run config, that tells a mock run from a model's.") in said
