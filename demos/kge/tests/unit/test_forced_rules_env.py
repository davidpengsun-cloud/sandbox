"""A forced-rules step of the improvement job (kge/improve/abtest.py _sub_env, force_rules=True) is an intentional
rules run: under the pilot's cloud path, with TAGGER_PROVIDER=model and a model configured, its child environment
builds the rule tagger and names no model for any agent, never the cloud path's refusal."""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters.model import model_for
from kge.improve import abtest
from kge.pipeline.tagger import RuleTagger, make_tagger

CLOUD = {
    "GRAPH_STORE": "bigquery",
    "WAREHOUSE_BACKEND": "bigquery",
    "RETRIEVAL_BACKEND": "bigquery",
    "APP_STATE_BACKEND": "bigquery",
    "TAGGER_PROVIDER": "model",
    "MODEL_PROVIDER": "gemini",
    "MODEL_NAME": "gemini-2.5-flash",
    "MODEL_API_KEY": "not-a-key",
}
TIERED = {"MODEL_LOW_PROVIDER": "gemini", "MODEL_LOW_NAME": "gemini-2.5-flash",
          "MODEL_HIGH_PROVIDER": "gemini", "MODEL_HIGH_NAME": "gemini-2.5-pro"}
PROFILE = SimpleNamespace(registry=None, id="meridian")
PILOT = Path(__file__).resolve().parents[2] / "infra" / "pilot" / "variables.tf"


def pilot_env() -> dict[str, str]:
    """The pilot's configuration, pilot_env's defaults as its Terraform gives them to the service and the job."""
    block = re.search(r'variable "pilot_env" \{.*?default = \{(.*?)\n  \}', PILOT.read_text(), re.S).group(1)
    return dict(re.findall(r'^\s*([A-Z_]+)\s*=\s*"([^"]*)"', block, re.M))


def _enter(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name in list(os.environ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("extra", [{}, TIERED], ids=["global-model", "tier-models"])
def test_forced_rules_child_builds_the_rule_tagger_on_the_cloud_path(monkeypatch, extra):
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)
    _enter(monkeypatch, {**CLOUD, **extra})
    child = abtest._sub_env(PROFILE, force_rules=True)
    _enter(monkeypatch, child)
    tagger = make_tagger(["fleet"])
    assert type(tagger) is RuleTagger
    for agent in ("onboard", "enhance", "answer", "context", "tagger"):
        assert model_for(agent) is None, agent


def test_unforced_child_keeps_the_model_settings(monkeypatch):
    _enter(monkeypatch, {**CLOUD, **TIERED})
    child = abtest._sub_env(PROFILE)
    assert child["TAGGER_PROVIDER"] == "model"
    assert child["MODEL_PROVIDER"] == "gemini"
    assert child["MODEL_LOW_PROVIDER"] == "gemini"


def test_forced_rules_on_the_pilot_s_whole_configuration_builds_no_model(monkeypatch):
    """Verifier V2 on #97: with every pilot_env value, the forced-rules child names no model for any agent, on any tier,
    builds the rule tagger, and drops each tier's model name with its provider. Its embedder is the pilot's, Vertex AI's,
    on purpose: the candidate's chunks are embedded into its own table and searched by the questions the margin check
    asks, so a candidate measured with another embedder would measure the embedder, not the pack's edit, and its
    vectors would not be the width the BigQuery chunks table holds (EMBEDDING_DIM)."""
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)
    pilot = pilot_env()
    assert pilot["TAGGER_PROVIDER"] == "model" and pilot["MODEL_LOW_PROVIDER"] == "vertex-gemini"
    _enter(monkeypatch, {**pilot, "GRAPH_DATASET": "candidates"})
    child = abtest._sub_env(PROFILE, force_rules=True)
    assert not [n for n in child if re.fullmatch(r"MODEL_(HIGH|MEDIUM|LOW)_(PROVIDER|NAME)", n)]
    _enter(monkeypatch, child)
    assert type(make_tagger(["fleet"])) is RuleTagger
    for agent in ("onboard", "enhance", "answer", "context", "tagger"):
        assert model_for(agent) is None, agent
    assert child["EMBEDDING_PROVIDER"] == "vertex" and child["GRAPH_DATASET"] == "candidates"
