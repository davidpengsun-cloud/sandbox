"""The probe of Ask's structured model calls (scripts/probe_model_contract.py, #561), offline.

    .venv/bin/python -m pytest tests/unit/test_probe_model_contract.py -q

A dry run prints the bodies the model adapter would send, as Ask builds them, with no network and no credentials. The
live path is run against a stand-in for Vertex AI: the probe's own POST is replaced, so nothing leaves the process. The
owner's GCP agent runs the probe for real; no test does.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from kge.adapters.model import LLM
from kge.adapters.model.providers import JSON_MODE_SCHEMA, Chat

DEMO = Path(__file__).resolve().parents[2]
PRO = "gemini-3.1-pro-preview"
CLEARED = (*(f"ASK_{call}_{part}" for call in ("PLAN", "CLASSIFY", "WORDING")
             for part in ("MAX_TOKENS", "THINKING_TOKENS", "THINKING_LEVEL")),
           *(f"MODEL{tier}_THINKING_{part}" for tier in ("", "_HIGH", "_MEDIUM", "_LOW") for part in ("TOKENS", "LEVEL")),
           "ASK_UNKNOWN_WORD", "ASK_MODEL_CORRECTIONS", "MODEL_MAX_TOKENS", "MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL",
           "VERTEX_REGION", "VERTEX_PROJECT", "CONTEXT_MODEL_TIER",
           *(f"MODEL_{tier}_{part}" for tier in ("HIGH", "MEDIUM", "LOW")
             for part in ("PROVIDER", "NAME", "API_KEY", "BASE_URL", "MAX_TOKENS")))


def no_network(*a, **k):
    raise AssertionError("the probe reached the network")


@pytest.fixture
def probe(monkeypatch):
    """The script as a module, its environment the pilot's high tier alone, and no way off the machine."""
    spec = importlib.util.spec_from_file_location("probe_model_contract", DEMO / "scripts" / "probe_model_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_HIGH_NAME", PRO)
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: {})
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    monkeypatch.setattr("google.auth.default", no_network)
    monkeypatch.setattr(module, "post", no_network)
    return module


def printed_bodies(out: str) -> dict[tuple[str, str], dict]:
    """The bodies a dry run printed, by (call, arm)."""
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return {(line["call"], line["arm"]): line["body"] for line in lines}


def test_a_dry_run_prints_the_bodies_ask_sends_in_json_mode_and_each_arms_thinking_and_no_temperature(probe, capsys):
    """#561: on Gemini 3 and later the planner is sent in JSON mode, its schema in the system instruction."""
    assert probe.main(["--dry-run", "--calls", "plan", "--arms", "default,500,low"]) == 0
    out = capsys.readouterr().out
    bodies = printed_bodies(out)
    assert set(bodies) == {("plan", "default"), ("plan", "500"), ("plan", "low")}
    thinking = {arm: body["generationConfig"]["thinkingConfig"] for (_, arm), body in bodies.items()}
    assert thinking == {"default": {"thinkingLevel": "low"}, "500": {"thinkingBudget": 500}, "low": {"thinkingLevel": "low"}}
    for body in bodies.values():
        config = body["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert "responseJsonSchema" not in config
        assert config["maxOutputTokens"] == 16384
        assert "temperature" not in config
        instruction = body["systemInstruction"]["parts"][0]["text"]
        assert "You read one business question into a plan" in instruction and JSON_MODE_SCHEMA in instruction
        content = body["contents"][0]["parts"][0]["text"]
        assert content.endswith("## Question\nTotal revenue for June 2025?")
        assert "recording_key" not in content and "turn_id" not in content
    assert "| plan | low | level:low | 16384 |" in out


def test_a_dry_run_puts_every_arm_back_as_it_was(probe, monkeypatch, capsys):
    monkeypatch.setenv("ASK_CLASSIFY_THINKING_LEVEL", "medium")
    assert probe.main(["--dry-run", "--calls", "classify", "--arms", "700,default"]) == 0
    bodies = printed_bodies(capsys.readouterr().out)
    assert bodies[("classify", "700")]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 700}
    assert bodies[("classify", "default")]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}
    assert os.environ["ASK_CLASSIFY_THINKING_LEVEL"] == "medium" and "ASK_CLASSIFY_THINKING_TOKENS" not in os.environ


def test_the_planner_is_shown_every_synthetic_id_in_its_data_and_none_in_its_schema(probe, monkeypatch, tmp_path, capsys):
    """#561: the ids are plain strings in the schema, so it is the same size whatever the vocabulary's; the data holds
    them. Gemini 2.5 is still sent the planner's schema as structured output."""
    monkeypatch.setenv("MODEL_HIGH_NAME", "gemini-2.5-pro")
    out = tmp_path / "bodies.json"
    assert probe.main(["--dry-run", "--calls", "plan", "--arms", "default", "--ids", "400", "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    rows = {row["call"]: row for row in json.loads(out.read_text())["rows"]}
    body = json.loads(out.read_text())["bodies"][0]["body"]
    schema = body["generationConfig"]["responseJsonSchema"]
    content = body["contents"][0]["parts"][0]["text"]
    assert all(f'"{i}"' in content for i in probe.synthetic_ids(400))
    assert not any(f'"{i}"' in json.dumps(schema) for i in probe.synthetic_ids(400))
    assert "VocabularyId" not in schema.get("$defs", {}) and "discriminator" not in json.dumps(schema)
    assert rows["plan"]["mode"] == "schema" and rows["plan"]["largest_enum"] < 20
    assert rows["plan"]["schema_bytes"] < rows["plan"]["inlined_bytes"] < 100_000
    assert "maxItems" not in json.dumps(schema) and "minItems" not in json.dumps(schema)
    assert f"| schema | {rows['plan']['schema_bytes']} | {rows['plan']['inlined_bytes']} |" in printed


def test_inlined_replaces_every_ref_as_vertex_flattens_a_schema(probe):
    schema = {"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}, "b": {"$ref": "#/$defs/A"}},
              "$defs": {"A": {"type": "string", "enum": ["x", "y", "z"]}}}
    assert probe.inlined(schema) == {"type": "object", "properties": {"a": {"type": "string", "enum": ["x", "y", "z"]},
                                                                       "b": {"type": "string", "enum": ["x", "y", "z"]}}}
    assert probe.largest_enum(probe.inlined(schema)) == 3


def gemini_reply(text: str, thoughts: int = 400) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": "considering", "thought": True}, {"text": text}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2100, "thoughtsTokenCount": thoughts, "candidatesTokenCount": 60}}


def test_live_it_reports_each_arm_and_fails_when_the_planners_schema_is_rejected(probe, monkeypatch, capsys):
    monkeypatch.setattr(Chat, "token", lambda self: ("ya29.stand-in", "pilot-project"))
    sent = []

    def vertex(url, headers, body, timeout):
        sent.append((url, body))
        config = body["generationConfig"]["thinkingConfig"]
        if "thinkingLevel" in config:
            return 400, None, "Invalid JSON payload received. Unknown name \"anyOf\" at 'generation_config.response_json_schema'"
        return 200, gemini_reply('{"decision": "abstain", "unread": ["revenue"]}'), ""

    monkeypatch.setattr(probe, "post", vertex)
    assert probe.main(["--calls", "plan", "--arms", "500,low"]) == 1
    out = capsys.readouterr().out
    assert [url.endswith(f"/projects/pilot-project/locations/global/publishers/google/models/{PRO}:generateContent")
            for url, _ in sent] == [True, True]
    assert "| plan | 500 | tokens:500 | 16384 | 200 |  | STOP | 2100 | 400 | 60 |" in out
    assert "| yes | " in out
    assert "a planner's contract schema was rejected" in out
    assert "thinkingBudget not accepted" not in out, "a schema refusal is never taken for a thinking one"
    assert "ya29" not in out


def test_live_a_refused_thinking_budget_names_the_tiers_level_setting(probe, monkeypatch, capsys):
    monkeypatch.setattr(Chat, "token", lambda self: ("ya29.stand-in", "pilot-project"))
    monkeypatch.setattr(probe, "post", lambda url, headers, body, timeout: (400, None, "thinking_budget is not supported"))
    assert probe.main(["--calls", "plan", "--arms", "500"]) == 0
    assert f"thinkingBudget not accepted on {PRO}: set MODEL_HIGH_THINKING_LEVEL instead" in capsys.readouterr().out


def test_it_refuses_a_model_it_cannot_probe(probe, monkeypatch, capsys):
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_HIGH_NAME", "gpt-5")
    assert probe.main(["--calls", "wording", "--arms", "default"]) == 2
    assert "needs a gemini or vertex-gemini model" in capsys.readouterr().err
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "none")
    assert probe.main(["--dry-run"]) == 2
    with pytest.raises(SystemExit):
        probe.main(["--dry-run", "--arms", "lowest"])


def test_the_model_first_planners_whole_plan_reply_is_probed_in_json_mode(probe, tmp_path, capsys):
    """#561: Vertex AI could not flatten the whole PlanReply's schema, so the planner sends it in JSON mode: no
    responseJsonSchema, the schema in the system instruction."""
    from kge.services.ask.engine.planmodel import INSTRUCTIONS
    out = tmp_path / "bodies.json"
    assert probe.main(["--dry-run", "--calls", "plan_full", "--arms", "default,low", "--json", str(out)]) == 0
    assert "| plan_full | default | level:low | 16384 |" in capsys.readouterr().out
    saved = json.loads(out.read_text())
    bodies = {row["arm"]: row["body"] for row in saved["bodies"]}
    assert bodies["low"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    body = bodies["default"]
    instruction = body["systemInstruction"]["parts"][0]["text"]
    assert instruction.startswith(INSTRUCTIONS) and '"Obligation"' in instruction
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json" and "responseJsonSchema" not in config
    assert {row["mode"] for row in saved["rows"]} == {"json"} and {row["inlined_bytes"] for row in saved["rows"]} == {None}


def test_live_a_refused_budget_arm_names_thinking_only_when_googles_message_does(probe, monkeypatch, capsys):
    monkeypatch.setattr(Chat, "token", lambda self: ("ya29.stand-in", "pilot-project"))
    monkeypatch.setattr(probe, "post", lambda url, headers, body, timeout: (400, None, "responseJsonSchema too complex"))
    assert probe.main(["--calls", "plan_full,classify", "--arms", "500"]) == 1
    out = capsys.readouterr().out
    assert "thinkingBudget not accepted" not in out and "a planner's contract schema was rejected" in out


def test_live_a_refused_schema_is_retried_once_in_json_mode_as_production_does(probe, monkeypatch, capsys):
    """#561: Vertex AI's bare "invalid argument" on a call that sent a schema falls back to JSON mode, and the probe
    says so; the retry's row is what it reports."""
    monkeypatch.setattr(Chat, "token", lambda self: ("ya29.stand-in", "pilot-project"))
    sent = []

    def vertex(url, headers, body, timeout):
        sent.append(body)
        if "responseJsonSchema" in body["generationConfig"]:
            return 400, None, "Request contains an invalid argument."
        return 200, gemini_reply('{"terms": ["Total revenue"]}'), ""

    monkeypatch.setattr(probe, "post", vertex)
    assert probe.main(["--calls", "classify", "--arms", "default"]) == 0
    out = capsys.readouterr().out
    assert ["responseJsonSchema" in body["generationConfig"] for body in sent] == [True, False]
    assert JSON_MODE_SCHEMA in sent[1]["systemInstruction"]["parts"][0]["text"]
    assert "schema refused on classify default (HTTP 400): retried once in JSON mode" in out
    row = next(line for line in out.splitlines() if line.startswith("| classify | default |"))
    assert "| 200 |" in row and "| yes | json |" in row and row.rstrip().endswith("| yes |")


def test_live_a_generic_400_on_a_json_mode_call_is_not_retried_and_fails_the_planner(probe, monkeypatch, capsys):
    monkeypatch.setattr(Chat, "token", lambda self: ("ya29.stand-in", "pilot-project"))
    sent = []

    def vertex(url, headers, body, timeout):
        sent.append(body)
        return 400, None, "Request contains an invalid argument."

    monkeypatch.setattr(probe, "post", vertex)
    assert probe.main(["--calls", "plan", "--arms", "default"]) == 1
    out = capsys.readouterr().out
    assert len(sent) == 1 and "retried once in JSON mode" not in out
    assert "a planner's contract schema was rejected" in out


def test_live_the_full_plan_reply_is_validated_with_no_legacy_attribute_error(probe, monkeypatch, capsys):
    """#561: FullReply is not a Contract and has no legacy(); the probe validates the whole PlanReply instead."""
    monkeypatch.setattr(Chat, "token", lambda self: ("ya29.stand-in", "pilot-project"))
    reply = '{"kind": "abstain", "spans": [], "missing": ["no declaration"]}'
    monkeypatch.setattr(probe, "post", lambda url, headers, body, timeout: (200, gemini_reply(reply), ""))
    assert probe.main(["--calls", "plan_full", "--arms", "default"]) == 0
    out = capsys.readouterr().out
    assert "AttributeError" not in out and "| yes | json |" in out
    case = probe.plan_full_case()
    assert probe.validates("plan_full", case, gemini_reply('{"kind": "nonsense"}')).startswith("no: ")
