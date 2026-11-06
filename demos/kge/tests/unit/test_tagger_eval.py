"""The tagger scorer's errors and the offline command, exercised in every CI run."""
import copy
import json
import os
import re
from types import SimpleNamespace

import pytest

from kge.eval import tagger
from kge.adapters.model.errors import ExternalCallError
from kge.adapters.model.providers import ModelSettingError
from kge.pipeline.tagger import Proposal, make_tagger, RuleTagger


def test_scoring_is_per_passage_not_occurrence_or_fuzzy_match():
    assert tagger.score([" Digital Engagement Score ", "Other"],
                        ["digital  engagement score", "Digital Engagement Score", "Others"]) == {
        "expected": 2, "found": 1, "wrong": 1, "missed": 1,
        "matched": ["digital engagement score"], "false_positives": ["others"], "false_negatives": ["other"]}


def test_offline_command_uses_every_tier_and_never_ambient_real_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("TAGGER_MODEL_TIER", "high")
    assert tagger.main(["--out", str(tmp_path), "--repeats", "2"]) == 1
    result = json.loads((tmp_path / "result.json").read_text())
    assert set(result["arms"]) == {"RuleTagger", "low", "medium", "high"}
    assert result["targets"]["agreed_at"] == "2026-11-02"
    assert "not model quality" in result["purpose"]
    for tier in tagger.TIERS:
        row = result["arms"][tier]
        assert row["model"]["provider"] == "mock"
        assert row["model"]["tier"] == tier
        assert len(row["rows"]) == 18
        assert row["totals"]["candidates"] == {"expected": 10, "found": 4, "wrong": 4, "missed": 6}
        assert row["totals"]["mentions"] == {"expected": 16, "found": 16, "wrong": 0, "missed": 0}
        assert any(p["generator"] == "auto" for r in row["rows"] for p in r["proposals"])
    assert os.environ["TAGGER_MODEL_TIER"] == "high"
    assert os.environ["MODEL_PROVIDER"] == "vertex-gemini"


def test_production_mock_tagger_still_uses_rules():
    with tagger.environment(tagger.configuration(False)):
        assert type(make_tagger(["Active Customer"])) is RuleTagger


def test_model_error_is_not_scored_as_a_quality_miss():
    data = tagger.read_set(tagger.DATA / "set.json")
    broken = SimpleNamespace(propose=lambda *a: [Proposal("business_meaning", "__model_error__", 0, "failed")])
    with pytest.raises(ValueError, match="model failed"):
        tagger.evaluate(data, broken, 1)


def test_mentions_in_two_passages_count_twice_and_duplicates_once():
    data = {"passages": [{"id": str(i), "document": "d", "text": "Active Customer", "shelf": "certified",
                          "kind": "memo", "candidates": [], "mentions": ["Active Customer"]} for i in range(2)]}
    duplicate = SimpleNamespace(propose=lambda *a: [Proposal("business_meaning", "Active Customer", .8, "x")] * 2)
    assert tagger.evaluate(data, duplicate, 1)["totals"]["mentions"]["found"] == 2


@pytest.mark.parametrize("change", ["duplicate", "unknown", "existing"])
def test_bad_labels_refused(tmp_path, change):
    data = copy.deepcopy(tagger.read_set(tagger.DATA / "set.json"))
    if change == "duplicate":
        data["passages"].append(data["passages"][0])
    elif change == "unknown":
        data["passages"][0]["mentions"] = ["Unknown"]
    else:
        data["passages"][0]["candidates"] = ["Active Customer"]
    path = tmp_path / "set.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        tagger.read_set(path)


def test_live_without_agreement_never_calls_a_model(monkeypatch):
    monkeypatch.setattr(tagger, "model_for", lambda *a: pytest.fail("called before agreement"))
    with pytest.raises(SystemExit) as error:
        tagger.main(["--live"])
    assert error.value.code == 2


def test_targets_are_bound_to_the_set_and_repeats(tmp_path, capsys):
    tagger.main(["--out", str(tmp_path)])
    result = json.loads((tmp_path / "result.json").read_text())
    targets = {"agreed_by": "test only", "agreed_at": "test only", "set_sha256": result["set_sha256"],
               "repeats": 1, "candidates": {"min_found": 5, "max_wrong": 0},
               "mentions": {"min_found": 8, "max_wrong": 0}}
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(targets))
    assert tagger.main(["--out", str(tmp_path), "--targets", str(path)]) == 1
    assert not json.loads((tmp_path / "result.json").read_text())["arms"]["low"]["passed"]
    assert tagger.main(["--out", str(tmp_path), "--targets", str(path), "--repeats", "2"]) == 2
    assert "targets must agree this repeat count" in capsys.readouterr().err
    targets["set_sha256"] = "different set"
    path.write_text(json.dumps(targets))
    assert tagger.main(["--out", str(tmp_path), "--targets", str(path)]) == 2
    assert "targets need agreed_by, agreed_at and this set's set_sha256" in capsys.readouterr().err


def test_pilot_model_configuration_matches_terraform():
    config = json.loads((tagger.DATA / "pilot.json").read_text())
    text = (tagger.ROOT / "infra/pilot/variables.tf").read_text()
    for key, value in config.items():
        if key not in {"ANSWER_MODEL_TIER", "CONTEXT_MODEL_TIER"}:
            assert re.search(rf'{key}\s*=\s*"{re.escape(value)}"', text)


def test_one_make_command_from_a_clean_build_directory(tmp_path):
    import subprocess
    import sys
    command = subprocess.run(["make", "tagger-eval", f"PY={sys.executable}",
                              f"TAGGER_EVAL_ARGS=--out {tmp_path / 'result'}"],
                             cwd=tagger.ROOT, capture_output=True, text=True)
    assert command.returncode != 0, command.stdout + command.stderr
    assert (tmp_path / "result" / "result.json").is_file()


def test_image_job_saves_mock_results_and_failure_records(tmp_path):
    from tests.tagger_job import image
    run, store, app = image(tmp_path)
    done = run()
    assert done.returncode == 1, done.stdout + done.stderr
    records = list((store / "runs/meridian/tagger-eval").glob("*/status.json"))
    assert len(records) == 1
    folder = records[0].parent
    result = json.loads((folder / "result.json").read_text())
    assert set(result["arms"]) == {"RuleTagger", "low", "medium", "high"}
    assert all(result["arms"][tier]["model"]["provider"] == "mock" for tier in tagger.TIERS)
    assert (folder / "report.md").is_file()
    assert json.loads(records[0].read_text())["exit_code"] == 1
    status = json.loads(records[0].read_text())
    assert status["verdict"] == "fail"
    assert status["quality_evidence"] is False
    assert status["arms"] == {name: row["passed"] for name, row in result["arms"].items()}
    assert str(folder) in done.stdout
    assert (store / "work/meridian.tar.gz").is_file()
    assert json.loads((store / "jobs/meridian/lease.json").read_text()) == {}
    targets = {"agreed_by": "test only", "agreed_at": "test only", "set_sha256": result["set_sha256"],
               "repeats": 1, "candidates": {"min_found": 5, "max_wrong": 0},
               "mentions": {"min_found": 8, "max_wrong": 0}}
    upload = store / "uploads/meridian/tagger-targets.json"
    upload.parent.mkdir(parents=True)
    upload.write_text(json.dumps(targets))
    done = run("TARGETS=uploads/meridian/tagger-targets.json")
    assert done.returncode == 1, done.stdout + done.stderr
    records = sorted((store / "runs/meridian/tagger-eval").glob("*/status.json"))
    assert len(records) == 2
    assert json.loads(records[-1].read_text())["exit_code"] == 1
    assert (records[-1].parent / "targets.json").is_file()
    assert not json.loads((records[-1].parent / "result.json").read_text())["arms"]["low"]["passed"]
    done = run("TARGETS=uploads/meridian/missing.json")
    assert done.returncode == 2, done.stdout + done.stderr
    records = sorted((store / "runs/meridian/tagger-eval").glob("*/status.json"))
    assert len(records) == 3
    assert json.loads(records[-1].read_text())["complete"] is False
    assert json.loads(records[-1].read_text())["error"] + "\n" in done.stderr
    assert "targets object is missing" in done.stderr
    assert not (records[-1].parent / "result.json").exists()
    assert json.loads((store / "jobs/meridian/lease.json").read_text()) == {}


def test_live_runner_builds_each_configured_vertex_tier_without_calling_it(tmp_path, monkeypatch):
    from kge.adapters.model import LLM
    from langchain_core.language_models.chat_models import BaseChatModel
    def no_call(*args, **kwargs):
        pytest.fail("Vertex construction attempted a call or credential lookup")
    monkeypatch.setattr(LLM, "complete", no_call)
    monkeypatch.setattr(LLM, "_post", staticmethod(no_call))
    monkeypatch.setattr("google.auth.default", no_call)
    config = json.loads((tagger.DATA / "pilot.json").read_text())
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "operator-selected-medium")
    seen = []
    def evaluate(data, candidate, repeats):
        if hasattr(candidate, "llm"):
            llm = candidate.llm
            assert isinstance(llm.chat, BaseChatModel)
            seen.append((llm.tier, llm.identity()))
        return {"totals": {family: {"expected": 0, "found": 0, "wrong": 0, "missed": 0}
                           for family in (*tagger.FAMILIES, "surface_mentions")}, "rows": []}
    monkeypatch.setattr(tagger, "evaluate", evaluate)
    data = tagger.read_set(tagger.DATA / "set.json")
    import hashlib
    targets = {"agreed_by": "test only", "agreed_at": "test only",
               "set_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
               "repeats": 1, "candidates": {"min_found": 0, "max_wrong": 0},
               "mentions": {"min_found": 0, "max_wrong": 0}}
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(targets))
    assert tagger.main(["--live", "--targets", str(path), "--out", str(tmp_path)]) == 0
    assert [tier for tier, _ in seen] == list(tagger.TIERS)
    for tier, identity in seen:
        assert identity["provider"] == "vertex-gemini"
        assert identity["model"] == ("operator-selected-medium" if tier == "medium"
                                     else config[f"MODEL_{tier.upper()}_NAME"])
    assert os.environ["TAGGER_MODEL_TIER"] == "medium"


@pytest.mark.parametrize("error,detail", [
    (ExternalCallError("Vertex AI error (HTTP 403): model unavailable"),
     "Vertex AI error (HTTP 403): model unavailable"),
    (ModelSettingError("MODEL_LOW_PROVIDER names an unknown provider"),
     "MODEL_LOW_PROVIDER names an unknown provider"),
    (tagger.EvaluationError("no model configured for tier low"), "no model configured for tier low"),
    (RuntimeError("transport echoed secret-key"), "RuntimeError"),
    (ValueError("transport echoed secret-key"), "ValueError"),
])
@pytest.mark.parametrize("stage", ["command", "run", "job"])
def test_failure_diagnostics_keep_safe_details_and_hide_other_messages(
        tmp_path, monkeypatch, capsys, error, detail, stage):
    def fail(*args):
        raise error
    monkeypatch.setattr(tagger, "load_env", lambda: None)
    options = ["--out", str(tmp_path / "output")]
    if stage == "command":
        monkeypatch.setattr(tagger, "model_for", fail)
    else:
        store = tagger.open_store(str(tmp_path / "store"))
        monkeypatch.setattr(tagger, "open_store", lambda url: store)
        monkeypatch.setattr(tagger, "settings", lambda: SimpleNamespace(STORE_URL=store.url))
        monkeypatch.setattr(tagger, "resolve", lambda name: SimpleNamespace(
            client="meridian", run_dir=lambda: tmp_path / "runs"))
        options += ["--job"]
        if stage == "job":
            monkeypatch.setattr(store, "get", fail)
            options += ["--targets", "uploads/meridian/targets.json"]
        else:
            monkeypatch.setattr(tagger, "model_for", fail)
    assert tagger.main(options) == 2
    captured = capsys.readouterr()
    context = "tagger evaluation" + (" job" if stage != "command" else "")
    line = f"{context} could not complete ({detail}); no quality verdict"
    assert captured.err == line + "\n"
    assert "secret-key" not in captured.out + captured.err
    assert not list(tmp_path.rglob("result.json"))
    if stage != "command":
        records = list((tmp_path / "store/runs/meridian/tagger-eval").glob("*/status.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text())
        assert record["exit_code"] == 2
        assert record["complete"] is False
        assert record["error"] == line
        local = next((tmp_path / "runs/tagger-eval").glob("*/status.json"))
        assert local.read_bytes() == records[0].read_bytes()


@pytest.mark.parametrize("bar,mentions,surfaces,candidates,wrong_m,wrong_c,identifiers,passed", [
    ("rules", 65, 95, 20, 0, 0, 0, True),
    ("rules", 64, 95, 20, 0, 0, 0, False),
    ("rules", 65, 94, 20, 0, 0, 0, False),
    ("rules", 65, 95, 19, 0, 0, 0, False),
    ("rules", 100, 100, 100, 6, 0, 0, False),
    ("rules", 100, 100, 100, 0, 6, 0, False),
    ("model", 85, 90, 75, 15, 2, 0, True),
    ("model", 84, 90, 75, 0, 0, 0, False),
    ("model", 85, 89, 75, 0, 0, 0, False),
    ("model", 85, 90, 74, 0, 0, 0, False),
    ("model", 85, 90, 75, 16, 0, 0, False),
    ("model", 85, 90, 75, 0, 3, 0, False),
    ("model", 85, 90, 75, 0, 0, 1, False),
])
def test_agreed_rate_bars(bar, mentions, surfaces, candidates, wrong_m, wrong_c, identifiers, passed):
    targets = json.loads((tagger.DATA / "targets.json").read_text())[bar]
    row = {"totals": {"mentions": {"expected": 100, "found": mentions, "wrong": wrong_m},
                      "surface_mentions": {"expected": 100, "found": surfaces},
                      "candidates": {"expected": 100, "found": candidates, "wrong": wrong_c}},
           "candidate_identifiers": identifiers}
    assert all(tagger.verdict(row, targets).values()) is passed


def test_candidate_wrong_rate_and_absolute_limit_are_independent():
    targets = json.loads((tagger.DATA / "targets.json").read_text())["model"]
    row = {"totals": {"mentions": {"expected": 1, "found": 1, "wrong": 0},
                      "surface_mentions": {"expected": 1, "found": 1},
                      "candidates": {"expected": 3, "found": 3, "wrong": 1}}}
    checks = tagger.verdict(row, targets)
    assert checks["max_wrong_candidates"]
    assert not checks["candidate_wrong_rate"]


def test_surface_recall_excludes_semantic_mentions_and_identifiers_are_counted():
    data = {"passages": [{"id": "p", "document": "d", "text": "Active Customer and clients; branch_id and Portfolio",
                          "shelf": "uncertified", "kind": "notes", "candidates": [],
                          "mentions": ["Active Customer", "Customer Group"], "identifiers": ["Portfolio"]}]}
    proposals = [Proposal("business_meaning", "Customer Group", .8, "semantic"),
                 Proposal("candidate_term", "branch_id", .8, "column"),
                 Proposal("candidate_term", "Portfolio", .8, "table")]
    row = tagger.evaluate(data, SimpleNamespace(propose=lambda *a: proposals), 1)
    assert row["totals"]["mentions"]["found"] == 1
    assert row["totals"]["surface_mentions"]["expected"] == 1
    assert row["totals"]["surface_mentions"]["found"] == 0
    assert row["candidate_identifiers"] == 2
