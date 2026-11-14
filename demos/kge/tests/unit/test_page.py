"""Golden samples remain derived from the baseline after the standalone page is retired."""
from pathlib import Path

def _set(tmp, name: str, items: list[dict], version: str | None = None) -> Path:
    import json
    path = tmp / f"{name}.json"
    path.write_text(json.dumps({"version": version, "items": items} if version else items))
    return path


def test_a_set_s_samples_are_the_questions_its_baseline_run_answered_on_the_arm(tmp_path):
    """kge/eval/sets.py golden: a question is a sample if every item that asks it as the run's principal passed on the arm in
    the baseline, which ran that very file; an item asked as another principal is not the page's to ask. No baseline, one
    of another file or another profile, or one in which nothing passed: every question. The known misses are the
    questions the baseline records an item of failing on the arm, asked as the run's principal."""
    import hashlib
    from kge.eval import sets
    one = [{"id": "A1", "kind": "k", "question": "first", "expect": {}}, {"id": "A2", "kind": "k", "question": "second", "expect": {}},
           {"id": "A3", "kind": "k", "question": "third", "expect": {}},
           {"id": "A3-R", "kind": "k", "question": "third", "expect": {}, "principal": "someone_else"},
           {"id": "A4", "kind": "k", "question": "fourth", "expect": {}}, {"id": "A4b", "kind": "k", "question": "fourth", "expect": {}}]
    path = _set(tmp_path, "extra_v1", one)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    base = {"profile": "p", "sets": {"extra_v1": {"file": path.name, "sha256": sha}},
            "items": {"extra_v1": {"A1": {"graph": 1.0}, "A2": {"graph": 0.0}, "A3": {"graph": 1.0}, "A3-R": {"graph": 0.0},
                                   "A4": {"graph": 1.0}, "A4b": {"graph": 0.0}}}}
    (got,) = sets.golden(tmp_path, base, "graph", "p")
    assert got["questions"] == ["first", "second", "third", "fourth"] and got["samples"] == ["first", "third"]
    assert got["misses"] == ["second", "fourth"], "a question another principal's item failed is not the page's miss"
    for other in ({**base, "sets": {"extra_v1": {"file": path.name, "sha256": "0" * 64}}}, {**base, "profile": "q"}, None,
                  {**base, "items": {"extra_v1": {k: {"graph": 0.0} for k in base["items"]["extra_v1"]}}}):
        assert sets.golden(tmp_path, other, "graph", "p")[0]["samples"] == got["questions"], other
    for other in ({**base, "sets": {"extra_v1": {"file": path.name, "sha256": "0" * 64}}}, {**base, "profile": "q"}, None,
                  {**base, "items": {}}, {**base, "items": {"extra_v1": {"A2": {"control": 0.0}}}}):
        assert sets.golden(tmp_path, other, "graph", "p")[0]["misses"] == [], f"no record of a miss on the arm: none ({other})"
