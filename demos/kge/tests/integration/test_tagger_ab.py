"""The prepared answering A/B runs on the local stack and records V01–V04."""
import json

from kge.eval import tagger
from kge.adapters.model import LLM


def test_tagger_ab_on_local_stack(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("an offline evaluation attempted a model network call")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    status = tagger.main(["--ab", "--out", str(tmp_path)])
    assert status in (0, 1)
    result = json.loads((tmp_path / "result.json").read_text())
    assert set(result["ab"]) == {"rules", "tiered"}
    baseline, tiered = result["ab"]["rules"], result["ab"]["tiered"]
    assert baseline["passed"], baseline["gates"]
    assert baseline["manifest"]["llm"] is None
    assert tiered["manifest"]["llm"]["provider"] == "mock"
    assert baseline["manifest"]["graph"] == tiered["manifest"]["graph"]
    assert baseline["manifest"]["sets"] == tiered["manifest"]["sets"]
    for run in result["ab"].values():
        assert {g["rule"] for g in run["gates"]} == {"V01", "V02", "V03", "V04"}
        assert next(g for g in run["gates"] if g["rule"] == "V03")["ok"] is None
    assert status == int(any(not run["passed"] for run in
                             [*result["ab"].values(), *result["arms"].values()]))


def test_image_job_publishes_the_complete_answering_ab(tmp_path):
    from tests.tagger_job import image
    run, store, app = image(tmp_path, built=True)
    import hashlib
    data = tagger.read_set(tagger.DATA / "set.json")
    targets = {"agreed_by": "test only", "agreed_at": "test only",
               "set_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
               "repeats": 1, "candidates": {"min_found": 0, "max_wrong": 9},
               "mentions": {"min_found": 0, "max_wrong": 9}}
    upload = store / "uploads/meridian/targets.json"
    upload.parent.mkdir(parents=True)
    upload.write_text(json.dumps(targets))
    done = run("LIVE=yes", "AB=yes", "TARGETS=uploads/meridian/targets.json")
    assert done.returncode in (0, 1), done.stdout + done.stderr
    folders = list((store / "runs/meridian/tagger-eval").iterdir())
    assert len(folders) == 1
    folder = folders[0]
    result = json.loads((folder / "result.json").read_text())
    assert "not model quality" in result["purpose"]
    assert set(result["ab"]) == {"rules", "tiered"}
    baseline, tiered = result["ab"]["rules"], result["ab"]["tiered"]
    assert baseline["passed"], baseline["gates"]
    assert baseline["manifest"]["graph"] == tiered["manifest"]["graph"]
    assert baseline["manifest"]["sets"] == tiered["manifest"]["sets"]
    assert baseline["manifest"]["llm"] is None
    assert tiered["manifest"]["llm"]["provider"] == "mock"
    for arm in result["ab"].values():
        assert {gate["rule"] for gate in arm["gates"]} == {"V01", "V02", "V03", "V04"}
        for name in ("manifest.json", "records.jsonl", "report.md"):
            assert (folder / arm["run"] / name).is_file()
    assert done.returncode == int(any(not arm["passed"] for arm in
                                     [*result["ab"].values(), *result["arms"].values()]))
    assert json.loads((folder / "status.json").read_text())["exit_code"] == done.returncode
    assert json.loads((store / "jobs/meridian/lease.json").read_text()) == {}
