"""The run trace: what each stage of one answer took, read, asked and bound.

    .venv/bin/python -m pytest tests/integration/test_run_trace.py -q

Every /answer and /ask response carries run_trace: the profile, the graph's
model and overlay hashes, the model's config hash (or null), the run config,
milliseconds and tokens per stage, the chunks retrieved and what each figure is
bound to. Ids only: never chunk text, never a prompt. A trace lives in a
ContextVar, so two requests at once never write into each other's.

Offline: no model is called. The LLM client is exercised with its HTTP post
replaced by a fake that answers in each provider's shape.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import urllib.error
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS, Engine
from kge.adapters.model import LLM, make_llm
from tests.scratch import profile_of, scratch_build
from tests.integration.test_combined import Q

DEFINE = "What is the definition of an active customer?"
BANK = "Total revenue across the bank for the quarter to 30 June 2025"
HEX = re.compile(r"^[0-9a-f]{64}$")
ROW_KEYS = {"doc", "chunk", "via", "tier", "ai_ready", "score"}


class Clock:
    """A clock the test moves by hand, in seconds."""
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_a_stage_runs_until_the_next_one_starts_and_repeats_add_up():
    clock = Clock()
    t = runtrace.Trace("answer", clock=clock)
    t.stage("understand")
    clock.t += 0.002
    t.stage("plan")
    clock.t += 0.010
    t.stage("gate")
    clock.t += 0.001
    t.stage("plan")
    clock.t += 0.005
    rt = t.finish({})
    assert [(s["stage"], s["ms"]) for s in rt["stages"]] == [("understand", 2.0), ("plan", 15.0), ("gate", 1.0)]
    assert rt["total_ms"] == 18.0


def test_an_unknown_stage_name_is_an_error_not_a_new_stage():
    with pytest.raises(ValueError, match="stage must be one of"):
        runtrace.Trace("answer").stage("telepathy")


def test_without_a_trace_recording_is_a_no_op():
    assert runtrace.current() is None
    runtrace.stage("plan")
    runtrace.retrieved([{"doc": "D", "chunk": "D#0", "score": 0.5}], "vector")
    runtrace.llm_call("m", "m", 1, 1, 1.0)
    runtrace.number_check(passed=True)
    assert runtrace.current() is None


def test_two_threads_at_once_keep_their_own_traces():
    both = threading.Barrier(2)
    got: dict[str, dict] = {}

    def run(name: str):
        with runtrace.start("answer") as t:
            both.wait()
            runtrace.stage("retrieve")
            runtrace.retrieved([{"doc": name, "chunk": f"{name}#0", "score": 0.9}], "vector")
            both.wait()
            runtrace.llm_call(name, name, 3, 4, 1.0)
            both.wait()
            got[name] = t.finish({})

    threads = [threading.Thread(target=run, args=(n,)) for n in ("A", "B")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    for name in ("A", "B"):
        assert [r["doc"] for r in got[name]["retrieved"]] == [name]
        assert [c["served_model"] for c in got[name]["llm_calls"]] == [name]
    assert got["A"]["run_id"] != got["B"]["run_id"]


def test_two_tasks_at_once_keep_their_own_traces():
    async def run(name: str) -> dict:
        with runtrace.start("ask") as t:
            for i in range(3):
                runtrace.stage("retrieve")
                runtrace.retrieved([{"doc": name, "chunk": f"{name}#{i}"}], "vector")
                await asyncio.sleep(0)
            return t.finish({})

    async def both():
        return await asyncio.gather(run("A"), run("B"))

    a, b = asyncio.run(both())
    assert {r["doc"] for r in a["retrieved"]} == {"A"} and {r["doc"] for r in b["retrieved"]} == {"B"}
    assert runtrace.current() is None


def test_a_model_call_counts_toward_the_stage_it_ran_in():
    t = runtrace.Trace("ask")
    t.stage("understand")
    t.llm_call("p:m", "m-served", 10, 2, 5.0)
    t.stage("render")
    t.llm_call("p:m", "m-served", 30, 8, 7.0)
    t.llm_call("p:m", "m-served", 5, 1, 2.0)
    rt = t.finish({})
    by = {s["stage"]: s for s in rt["stages"]}
    assert (by["understand"]["tokens_in"], by["understand"]["tokens_out"], by["understand"]["llm_calls"]) == (10, 2, 1)
    assert (by["render"]["tokens_in"], by["render"]["tokens_out"], by["render"]["llm_calls"]) == (35, 9, 2)
    assert rt["tokens"] == {"in": 45, "out": 11}
    assert [c["stage"] for c in rt["llm_calls"]] == ["understand", "render", "render"]


def test_tokens_a_provider_did_not_report_are_unknown_not_zero():
    t = runtrace.Trace("ask")
    t.stage("render")
    t.llm_call("p:m", "m", None, None, 5.0)
    t.stage("plan")
    rt = t.finish({})
    by = {s["stage"]: s for s in rt["stages"]}
    assert by["render"]["tokens_in"] is None and by["render"]["tokens_out"] is None
    assert by["plan"]["tokens_in"] == 0 and by["plan"]["llm_calls"] == 0
    assert rt["tokens"] == {"in": None, "out": None}


def test_a_trace_names_the_profile_of_the_graph_it_ran_on_and_of_the_config(eng):
    assert runtrace.Trace("answer").finish({})["profile"] is None, "no graph, no config: nothing names one"
    t = runtrace.Trace("answer", graph=eng.g)
    assert t.finish({})["profile"] == eng.profile.id == "meridian", "the graph's, the profile the process serves"
    t.use(replace(PRESETS["graph"], profile="harbour"))
    assert t.finish({})["profile"] == "harbour" and "profile" not in t.config, "the config carries it, and is no stage"


def test_jsonl_appends_one_record_per_line(tmp_path):
    path = tmp_path / "runs" / "r1" / "records.jsonl"
    runtrace.append_jsonl(path, {"id": "G001", "run_trace": {"stages": []}})
    runtrace.append_jsonl(path, {"id": "G002"})
    assert path.read_text().count("\n") == 2
    assert [r["id"] for r in runtrace.read_jsonl(path)] == ["G001", "G002"]


def test_jsonl_lines_stay_whole_when_threads_write_at_once(tmp_path):
    path = tmp_path / "records.jsonl"
    threads = [threading.Thread(target=lambda i=i: [runtrace.append_jsonl(path, {"i": i, "n": n, "pad": "x" * 2000}) for n in range(20)])
               for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    rows = runtrace.read_jsonl(path)
    assert len(rows) == 80 and {(r["i"], r["n"]) for r in rows} == {(i, n) for i in range(4) for n in range(20)}


def test_identity_has_a_config_hash_and_never_the_key(monkeypatch):
    monkeypatch.delenv("MODEL_SEED", raising=False)
    llm = LLM("openai", "gpt-test", "sk-very-secret", None)
    ident = llm.identity()
    assert HEX.match(ident["config_hash"]) and ident["seed"] == 7 and ident["temperature"] == 0
    assert ident["provider"] == "openai" and ident["model"] == "gpt-test"
    assert "sk-very-secret" not in json.dumps(ident)
    same = LLM("openai", "gpt-test", "another-key", None).identity()
    assert same["config_hash"] == ident["config_hash"], "the key is not part of the config"
    for other in (LLM("openai", "gpt-other", "k"), LLM("openai", "gpt-test", "k", "http://localhost:9/v1"),
                  LLM("openai", "gpt-test", "k", seed=8), LLM("ollama", "gpt-test", "k")):
        assert other.identity()["config_hash"] != ident["config_hash"]


def test_the_seed_comes_from_model_seed(monkeypatch):
    monkeypatch.setenv("MODEL_SEED", "11")
    assert LLM("gemini", "g", "k").identity()["seed"] == 11
    monkeypatch.setenv("MODEL_SEED", "eleven")
    with pytest.raises(ValueError, match="MODEL_SEED"):
        LLM("gemini", "g", "k")


def test_anthropic_takes_no_seed_and_the_identity_says_so(monkeypatch):
    monkeypatch.delenv("MODEL_SEED", raising=False)
    assert LLM("anthropic", "claude-test", "k").identity()["seed"] is None


def test_make_llm_carries_the_seed(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_NAME", "llama-test")
    monkeypatch.setenv("MODEL_SEED", "3")
    assert make_llm().identity()["seed"] == 3


REPLIES = {
    "gemini": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}], "modelVersion": "gemini-test-001",
               "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3, "thoughtsTokenCount": 2}},
    "anthropic": {"content": [{"text": "ok"}], "model": "claude-test-20260101", "usage": {"input_tokens": 12, "output_tokens": 5}},
    "openai": {"choices": [{"message": {"content": "ok"}}], "model": "gpt-test-2026", "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
    "ollama": {"choices": [{"message": {"content": "ok"}}], "model": "llama-test:latest", "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
}


@pytest.mark.parametrize("provider", list(REPLIES))
def test_every_call_sends_temperature_zero_and_a_seed_where_taken_and_records_usage(provider, monkeypatch):
    monkeypatch.delenv("MODEL_SEED", raising=False)
    llm = LLM(provider, "m", "k")
    sent: list[dict] = []
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: sent.append(body) or REPLIES[provider])
    with runtrace.start("ask") as t:
        runtrace.stage("render")
        assert llm.complete("hello") == "ok"
        rt = t.finish({})
    body = sent[0]
    knobs = body["generationConfig"] if provider == "gemini" else body
    assert knobs["temperature"] == 0
    if provider == "anthropic":
        assert "seed" not in knobs
    else:
        assert knobs["seed"] == 7
    call = rt["llm_calls"][0]
    served = {"gemini": "gemini-test-001", "anthropic": "claude-test-20260101", "openai": "gpt-test-2026", "ollama": "llama-test:latest"}[provider]
    assert (call["stage"], call["model"], call["served_model"], call["ok"]) == ("render", f"{provider}:m", served, True)
    assert (call["tokens_in"], call["tokens_out"]) == (12, 5)


def test_a_reply_without_usage_records_unknown_tokens(monkeypatch):
    llm = LLM("openai", "m", "k")
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: {"choices": [{"message": {"content": "ok"}}]})
    with runtrace.start("ask") as t:
        runtrace.stage("render")
        llm.complete("hello")
        rt = t.finish({})
    assert rt["llm_calls"][0]["tokens_in"] is None and rt["llm_calls"][0]["served_model"] is None


def test_a_failed_call_is_recorded_and_still_raised(monkeypatch):
    llm = LLM("openai", "m", "k")

    def down(url, headers, body):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(llm, "_post", down)
    with runtrace.start("ask") as t:
        runtrace.stage("render")
        with pytest.raises(urllib.error.URLError):
            llm.complete("hello")
        rt = t.finish({})
    assert rt["llm_calls"][0]["ok"] is False and rt["llm_calls"][0]["error"] == "URLError"


def test_the_fingerprint_is_stable_and_separates_model_from_overlay(eng):
    a, b = runtrace.fingerprint(eng.g), runtrace.fingerprint(eng.g)
    assert a == b and HEX.match(a["model"]) and HEX.match(a["overlay"]) and a["model"] != a["overlay"]


def test_the_model_hash_covers_the_schema_ddl(eng, monkeypatch):
    import kge.graph.schema
    before = runtrace.fingerprint(eng.g)
    monkeypatch.setattr(kge.graph.schema, "DDL", kge.graph.schema.DDL + ["CREATE NODE TABLE Extra(id STRING, PRIMARY KEY (id))"])
    after = runtrace.fingerprint(eng.g)
    assert after["model"] != before["model"] and after["overlay"] == before["overlay"]


def test_a_steward_decision_changes_the_overlay_and_a_new_term_the_model(tmp_path):
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        start = runtrace.fingerprint(e.g)
        queue = e.g.steward_queue()
        retention = next(r for r in queue if r["doc"] == "POL-RETENTION" and r["family"] == "privacy")
        e.g.steward_decide(retention["tag"], "steward_glossary", "reject")
        rejected = runtrace.fingerprint(e.g)
        assert rejected["overlay"] != start["overlay"] and rejected["model"] == start["model"]
        candidate = next(r for r in queue if r["family"] == "candidate_term")
        e.g.steward_decide(candidate["tag"], "steward_glossary", "accept", definition="logins per active customer per month")
        accepted = runtrace.fingerprint(e.g)
        assert accepted["overlay"] != rejected["overlay"] and accepted["model"] != rejected["model"], "a new term is a new vocabulary"
    finally:
        e.close()


def test_a_fingerprint_taken_while_a_write_runs_does_not_outlive_it(tmp_path):
    """Another request's trace may finish after q() has begun a write but before it lands: what it read then is cached
    under the stamp it saw, and the write, once done, makes the next read fresh. A write that fails still counts, since
    what it read may have changed."""
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    g = e.g
    try:
        before = runtrace.fingerprint(g)
        real = g.conn

        class Conn:
            def execute(self, cypher, params=None):
                if "SET d.tier" in cypher:
                    runtrace.fingerprint(g)
                return real.execute(cypher, params or {})
        g.conn = Conn()
        try:
            g.q("MATCH (d:Document {id: 'STD-FX'}) SET d.tier = 'silver'")
        finally:
            g.conn = real
        after = runtrace.fingerprint(g)
        runtrace._layers.pop(g, None)
        assert after == runtrace.fingerprint(g) and after["model"] != before["model"]
        stamp = g.writes
        with pytest.raises(RuntimeError):
            g.q("MATCH (d:Document {id: 'STD-FX'}) SET d.no_such_property = 1")
        assert g.writes == stamp + 1
    finally:
        e.close()


@pytest.mark.parametrize("question", [BANK, DEFINE, Q, "How many active customers in Retail in June 2025?"])
def test_a_traced_answer_is_the_answer_plus_its_trace(eng, question):
    for cfg in PRESETS.values():
        traced = runtrace.run("answer", lambda: eng.answer(question, "analyst_enterprise", config=cfg), graph=eng.g)
        rt = traced.pop("run_trace")
        assert traced == eng.answer(question, "analyst_enterprise", config=cfg)
        assert rt["config"] == {"arm": cfg.arm, "label": cfg.label, **cfg.stages()} and rt["path"] == "answer"


@pytest.fixture(scope="module")
def asked(eng):
    c = Combined(eng, llm=None)
    return runtrace.run("ask", lambda: c.ask(Q, "analyst_retail"), graph=eng.g)


def test_the_combined_answer_times_every_stage(asked):
    names = [s["stage"] for s in asked["run_trace"]["stages"]]
    assert set(names) <= set(runtrace.STAGE_NAMES)
    assert {"understand", "resolve", "access", "plan", "gate", "execute", "retrieve", "bind", "render"} <= set(names)
    assert all(s["ms"] >= 0 and s["tokens_in"] == 0 for s in asked["run_trace"]["stages"])


def test_each_figure_is_bound_to_the_chunk_it_cites_and_that_chunk_was_retrieved(asked):
    rt = asked["run_trace"]
    cites = {c["n"]: c for c in asked["citations"]}
    assert [b["figure"] for b in rt["bound"]] == ["c1", "c2", "m1", "m2"]
    for f, b in zip(asked["figures"], rt["bound"]):
        assert (b["doc"], b["chunk"]) == (cites[f["cite"]]["doc"], cites[f["cite"]]["chunk"])
    got = {(r["doc"], r["chunk"]) for r in rt["retrieved"]}
    assert all((b["doc"], b["chunk"]) in got for b in rt["bound"])
    assert [(c["n"], c["doc"], c["chunk"]) for c in rt["citations"]] == [(c["n"], c["doc"], c["chunk"]) for c in asked["citations"]]


def test_retrieved_rows_are_ids_and_scores_only(asked):
    rows = asked["run_trace"]["retrieved"]
    assert rows and all(set(r) == ROW_KEYS for r in rows)
    assert {r["via"] for r in rows} >= {"definition", "mention", "vector:certified"}
    assert all(r["score"] is None or isinstance(r["score"], float) for r in rows)


def test_the_trace_carries_no_chunk_text(asked):
    body = json.dumps(asked["run_trace"])
    excerpts = [c["excerpt"] for c in asked["citations"] if c.get("excerpt")] + [n["excerpt"] for n in asked["not_cited"] if n.get("excerpt")]
    assert excerpts and not any(x[:60] in body for x in excerpts)
    assert "POL-COMPLAINTS" not in {r["doc"] for r in asked["run_trace"]["retrieved"]}, "a withheld chunk is not retrieved"


def test_no_model_no_model_identity_and_no_number_check(asked):
    rt = asked["run_trace"]
    assert rt["llm"] is None and rt["llm_calls"] == [] and rt["number_check"] is None and rt["tokens"] == {"in": 0, "out": 0}


def fake_model(monkeypatch, answer: str, sql: list[str] = ()) -> LLM:
    llm = LLM("openai", "gpt-test", "sk-very-secret")
    queue = list(sql)

    def post(url, headers, body):
        prompt = body["messages"][0]["content"]
        if prompt.startswith("Glossary terms"):
            content = json.dumps({"terms": []})
        elif prompt.startswith("Write one DuckDB"):
            content = json.dumps({"sql": queue.pop(0) if queue else ""})
        else:
            content = json.dumps({"answer": answer})
        return {"choices": [{"message": {"content": content}}], "model": "gpt-test-2026",
                "usage": {"prompt_tokens": len(prompt.split()), "completion_tokens": len(content.split())}}
    monkeypatch.setattr(llm, "_post", post)
    return llm


def test_a_model_run_records_each_call_its_tokens_and_the_number_check(eng, monkeypatch):
    said = "In June 2025, 176 Retail customers were active [1]; 3 of them complained [4]. Their average balance was 6,400.62 [2] and fee income USD 42.64 [3]."
    llm = fake_model(monkeypatch, said)
    a = runtrace.run("ask", lambda: Combined(eng, llm=llm).ask(Q, "analyst_retail"), graph=eng.g)
    rt = a["run_trace"]
    assert a["phrased_by"] == "model:openai:gpt-test"
    assert rt["llm"] == llm.identity() and rt["config"]["label"] == a["arm"]
    assert [(c["stage"], c["served_model"]) for c in rt["llm_calls"]] == [("understand", "gpt-test-2026"), ("render", "gpt-test-2026")]
    by = {s["stage"]: s for s in rt["stages"]}
    assert by["understand"]["tokens_in"] > 0 and by["render"]["tokens_in"] > 0
    assert rt["tokens"]["in"] == sum(c["tokens_in"] for c in rt["llm_calls"])
    assert rt["number_check"] == {"passed": True, "stray_numbers": 0, "bad_markers": 0}
    body = json.dumps(rt)
    assert "sk-very-secret" not in body and "Rewrite these facts" not in body and "Glossary terms" not in body


def test_the_number_check_records_what_it_threw_away(eng, monkeypatch):
    llm = fake_model(monkeypatch, "176 customers were active, of whom 4 complained [9].")
    rt = runtrace.run("ask", lambda: Combined(eng, llm=llm).ask(Q, "analyst_retail"), graph=eng.g)["run_trace"]
    assert rt["number_check"] == {"passed": False, "stray_numbers": 1, "bad_markers": 1}


def test_model_sql_is_timed_as_plan_and_gate(eng, monkeypatch):
    good = Combined(eng, llm=None).ask(Q, "analyst_retail")["sql"]
    llm = fake_model(monkeypatch, "", sql=["SELECT customer.email FROM customer", good])
    c = Combined(eng, llm=llm)
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    a = runtrace.run("ask", lambda: c.ask(Q, "analyst_retail"), graph=eng.g)
    assert a["sql_source"] == "model:openai:gpt-test"
    assert [c["stage"] for c in a["run_trace"]["llm_calls"]] == ["understand", "plan", "plan", "render"]


def test_a_fallback_to_answer_records_the_config_it_ran_on_and_the_model_it_asked(eng, monkeypatch):
    llm = fake_model(monkeypatch, "")
    a = runtrace.run("ask", lambda: Combined(eng, llm=llm).ask("How many active customers in Retail in June 2025?", "analyst_retail"), graph=eng.g)
    rt = a["run_trace"]
    assert a["combined"] is False and rt["config"]["label"] == a["arm"] == "graph"
    assert [c["stage"] for c in rt["llm_calls"]] == ["understand"] and rt["llm"] == llm.identity()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.delenv("PROFILE", raising=False)
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        c.app_module = appmod
        yield c
    appmod._engine.close() if appmod._engine else None


def test_answer_and_ask_return_a_run_trace(client):
    fp = runtrace.fingerprint(client.app_module.engine().g)
    for path, who, question in (("/answer", "analyst_enterprise", DEFINE), ("/ask", "analyst_retail", Q)):
        r = client.post(path, json={"question": question}, headers=client.as_(who))
        rt = r.json()["run_trace"]
        assert rt["path"] == path.strip("/") and rt["profile"] == "meridian" and rt["graph"] == fp
        assert rt["llm"] is None and rt["config"] == {"arm": "graph", "label": "graph", **PRESETS["graph"].stages()}
        assert rt["stages"] and rt["retrieved"] and all(set(x) == ROW_KEYS for x in rt["retrieved"])


def test_answer_cites_by_id_in_its_trace(client):
    a = client.post("/answer", json={"question": DEFINE}, headers=client.as_("analyst_enterprise")).json()
    assert a["behaviour"] == "cite_certified"
    assert [c["doc"] for c in a["run_trace"]["citations"]] == a["cite"] and a["run_trace"]["bound"] == []
    body = json.dumps(a["run_trace"])
    assert not any(u["excerpt"][:60] in body for u in a["uncertified_context_not_cited"] if u.get("excerpt"))


def test_the_profile_is_the_process_s_never_what_the_environment_says_mid_run(client, monkeypatch):
    """PROFILE is read once, at import: the process serves the profile it started with."""
    monkeypatch.setenv("PROFILE", "harbour")
    a = client.post("/answer", json={"question": BANK}, headers=client.as_("analyst_enterprise")).json()
    assert a["run_trace"]["profile"] == "meridian"


def test_an_override_is_traced_as_the_config_it_ran_on(client):
    a = client.post("/answer", json={"question": DEFINE, "arm": "catalog", "stages": {"citation_rule": "shelves"}},
                    headers=client.as_("analyst_enterprise")).json()
    assert a["run_trace"]["config"] == {"arm": "catalog", "label": "catalog[citation_rule=shelves]", **PRESETS["catalog"].with_stages(citation_rule="shelves").stages()}


def test_a_refused_config_runs_nothing_and_has_no_trace(client):
    r = client.post("/answer", json={"question": BANK, "stages": {"resolution": "telepathy"}}, headers=client.as_("analyst_enterprise"))
    assert r.status_code == 400 and "run_trace" not in r.json()


def test_the_page_shows_the_run_trace():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text()
    assert "run_trace" in html


def test_the_env_example_names_the_seed_and_the_profile():
    from pathlib import Path
    from kge.pipeline.envfile import parse
    values = parse((Path(__file__).resolve().parents[2] / ".env.example").read_text())
    assert values["MODEL_SEED"] == "7" and values["PROFILE"] == "", "the registry's default unless the deployment names one"


def _readme_vias() -> set[str]:
    """The via values the README's run trace table lists for `retrieved`."""
    from pathlib import Path
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    row = next(line for line in readme.splitlines() if line.startswith("| `retrieved` |"))
    return set(re.findall(r"`([a-z_:]+)`", row.split("how it was found (", 1)[1].split(")", 1)[0]))


def test_the_readme_names_every_via_the_code_records():
    """A via is a literal, or the vector search's "vector" plus ":<shelf>" when one shelf is asked for; /retrieve keeps
    no trace, so its shelf from the request never reaches one."""
    import ast
    from pathlib import Path
    recorded = set()
    for src in sorted((Path(__file__).resolve().parents[2] / "kge" / "services").rglob("*.py")):
        for node in ast.walk(ast.parse(src.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "retrieved" and getattr(node.func.value, "id", None) == "runtrace":
                via = node.args[1]
                if isinstance(via, ast.Constant):
                    recorded.add(via.value)
                else:
                    assert src.name in ("core.py", "bigquery.py") and ast.unparse(via).startswith("'vector' + "), ast.unparse(via)
            elif node.func.attr == "retrieve" and src.name != "app.py":
                shelf = next((k.value for k in node.keywords if k.arg == "shelf"), None)
                assert shelf is None or isinstance(shelf, ast.Constant), ast.unparse(node)
                recorded.add("vector" if shelf is None else f"vector:{shelf.value}")
    assert {"vector", "vector:certified", "vector:uncertified"} <= recorded
    assert _readme_vias() == recorded


def test_every_via_a_run_records_is_one_the_readme_names(asked):
    assert {r["via"] for r in asked["run_trace"]["retrieved"]} <= _readme_vias()
