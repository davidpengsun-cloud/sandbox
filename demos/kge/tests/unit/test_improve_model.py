"""Model-assisted improvement proposals and mock adversarial modes."""
import pytest

from kge.improve import edits, rules
from kge.adapters.model import mock as mockllm
from kge.adapters.model import LLM, MOCK


@pytest.fixture
def evidence():
    return {
        "certified_terms": {
            "lease-rentals": {"id": "lease-rentals", "name": "Lease Rentals", "status": "certified"},
        },
        "existing_aliases": {
            "lease rentals": "lease-rentals",
        },
        "candidate_phrases": [],
        "columns": {
            "leasing.rental.lease_rentals_usd": {
                "column": "leasing.rental.lease_rentals_usd",
                "unit": "leasing",
                "table": "rental",
                "name": "lease_rentals_usd",
                "type": "DECIMAL(14,2)",
                "description": "Lease rentals billed, in USD; the business calls this lease income",
                "is_key": False,
                "certified_terms": ["lease-rentals"],
            }
        },
        "term_columns": {"lease-rentals": ["leasing.rental.lease_rentals_usd"]},
    }


@pytest.fixture
def ab_diagnoses(evidence):
    miss = {
        "id": "TEAM-LEASING-01",
        "set": "team_leasing_v1",
        "question": "Lease income for June 2025",
        "behaviour": "abstain",
        "reason": "No glossary term recognised in the question.",
    }
    return [rules.diagnose_miss(miss, evidence)]


def test_careful_mock_proposes_valid_alias(ab_diagnoses, evidence):
    out = edits.propose(ab_diagnoses, [], evidence, llm=LLM(MOCK, MOCK, mode="careful"))
    assert out["proposer"] == "mock"
    assert not out["refused"]
    assert len(out["edits"]) == 1
    assert out["edits"][0]["phrase"] == "lease income"
    assert out["edits"][0]["term"] == "lease-rentals"
    assert out["edits"][0]["proposer"] == "mock"


@pytest.mark.parametrize("mode,expected_reason", [
    ("unallowlisted_section", "outside the allowlist"),
    ("certified_true", "may not set certified: true"),
    ("unknown_column", "does not exist in pack.lock"),
    ("invented_alias", "not backed by a single matching comment"),
    ("duplicate_alias", "already an alias"),
    ("malformed_json", "refused:"),
])
def test_adversarial_mock_modes_are_refused_by_code(ab_diagnoses, evidence, mode, expected_reason):
    out = edits.propose(ab_diagnoses, [], evidence, llm=LLM(MOCK, MOCK, mode=mode))
    assert out["proposer"] == "mock"
    assert out["edits"] == []
    assert len(out["refused"]) == 1
    assert expected_reason in out["refused"][0]["reason"]


def test_improve_modes_do_not_change_agent_modes():
    assert "unallowlisted_section" in mockllm.IMPROVE_MODES
    assert "unallowlisted_section" not in mockllm.MODES


TIER_ENV = ("MODEL_PROVIDER", "MODEL_NAME", "MOCK_MODE", "ENHANCE_MODEL_TIER", "MODEL_HIGH_PROVIDER", "MODEL_HIGH_NAME",
            "MODEL_MEDIUM_PROVIDER", "MODEL_MEDIUM_NAME", "MODEL_LOW_PROVIDER", "MODEL_LOW_NAME")


def _improve_model(monkeypatch, **env):
    """The model run_improve hands its workflow when asked for "auto", under env alone."""
    from kge.improve import flow
    for name in TIER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    seen = {}

    class Workflow:
        def invoke(self, state):
            return state

    def build(prof, llm=None, on_step=None):
        seen["llm"] = llm
        return Workflow()
    monkeypatch.setattr(flow, "build_workflow", build)
    monkeypatch.setattr(flow, "require_no_pending_proposal", lambda prof: None)
    flow.run_improve(object())
    return seen["llm"]


def test_the_improvement_job_takes_the_default_model_with_no_tier_set(monkeypatch):
    from kge.adapters.model import make_llm
    assert _improve_model(monkeypatch, MODEL_PROVIDER="none") is None
    llm = _improve_model(monkeypatch, MODEL_PROVIDER="mock")
    assert llm.identity() == make_llm().identity() and llm.tier == "default"


def test_the_improvement_job_takes_the_enhance_tier(monkeypatch):
    llm = _improve_model(monkeypatch, MODEL_PROVIDER="none", MODEL_HIGH_PROVIDER="mock")
    assert llm.provider == MOCK and llm.tier == "high", "enhancement prefers the high tier where one is set"
    llm = _improve_model(monkeypatch, MODEL_PROVIDER="none", MODEL_HIGH_PROVIDER="mock", ENHANCE_MODEL_TIER="default")
    assert llm is None, "ENHANCE_MODEL_TIER=default keeps MODEL_PROVIDER's model"
    llm = _improve_model(monkeypatch, MODEL_PROVIDER="none", MODEL_LOW_PROVIDER="mock", ENHANCE_MODEL_TIER="low")
    assert llm.provider == MOCK and llm.tier == "low"
    with pytest.raises(ValueError, match="ENHANCE_MODEL_TIER must be one of"):
        _improve_model(monkeypatch, ENHANCE_MODEL_TIER="fastest")
