"""Held-out sets per business unit (kge/eval/sets.py, kge/eval/margin.py, kge/eval/report.py).

    .venv/bin/python -m pytest tests/unit/test_heldout_sets.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

from kge.eval import margin, report, sets as setfiles


def _rec(arm: str, id_: str, set_name: str, ok: bool = True) -> dict:
    return {
        "run_id": "r",
        "repeat": 1,
        "arm": arm,
        "mode": "rules",
        "config": arm,
        "set": set_name,
        "id": id_,
        "kind": "within",
        "question": f"Question {id_}",
        "ok": ok,
        "why": "",
        "measures": {
            "figure_ok": ok,
            "behaviour": {"expected": "figure", "got": "figure", "exact": ok, "class": ok},
            "gate": {"queries": 1, "first_try": True, "tries": 1, "passed": True},
            "citations": {"cited": 0, "tier_correct": 0, "off_tier": []},
            "numbers": {"checked": False, "caught": None, "bad_markers": None, "shown": None},
            "ms": 1.0,
            "tokens": {"in": 0, "out": 0},
            "llm_calls": 0,
            "cost_usd": 0.0,
        },
    }


def test_is_heldout_recognises_heldout_and_heldout_unit_sets(tmp_path: Path):
    assert setfiles.is_heldout("heldout")
    assert setfiles.is_heldout("heldout_leasing")
    assert setfiles.is_heldout("heldout_leasing_v1")
    assert not setfiles.is_heldout("golden")
    assert not setfiles.is_heldout("golden_leasing_v1")
    assert not setfiles.is_heldout("team_leasing_v1")

    for name in ("golden.json", "heldout.json", "team_leasing_v1.json", "heldout_leasing_v1.json"):
        (tmp_path / name).write_text(json.dumps([{"id": f"Q-{name}", "kind": "within", "question": f"Ask {name}", "expect": {}}]))

    got = setfiles.golden(tmp_path)
    names = [g["name"] for g in got]
    assert names == ["golden", "team_leasing"]
    assert all("heldout" not in n for n in names)


def test_v03_reports_every_heldout_set_and_never_gates():
    records = [
        _rec("control", "G1", "golden", True),
        _rec("treatment", "G1", "golden", True),
        _rec("control", "H1", "heldout", False),
        _rec("treatment", "H1", "heldout", True),
        _rec("control", "HL1", "heldout_leasing_v1", False),
        _rec("treatment", "HL1", "heldout_leasing_v1", False),
    ]
    manifest = {
        "sets": {
            "golden": {"version": "v2"},
            "heldout": {"version": "v1"},
            "heldout_leasing_v1": {"version": "v1"},
        }
    }
    checks = margin.post_load({}, records, manifest)
    v03 = [c for c in checks if c.rule == "V03"]
    assert len(v03) == 2
    assert all(c.ok is None for c in v03)
    assert "V03 heldout v1, held-out:" in v03[0].line
    assert "V03 heldout_leasing_v1 v1, held-out:" in v03[1].line


def test_report_labels_unit_heldout_as_paraphrases():
    records = [
        _rec("control", "HL1", "heldout_leasing_v1", False),
        _rec("treatment", "HL1", "heldout_leasing_v1", True),
    ]
    manifest = {
        "run_id": "r",
        "started_at": "2026-10-18T00:00:00Z",
        "finished_at": "2026-10-18T00:01:00Z",
        "principal": "analyst_enterprise",
        "repeats": 1,
        "profile": "meridian",
        "arms": [
            {"arm": "control", "side": "agent, graph off", "mode": "rules", "config": "control"},
            {"arm": "treatment", "side": "agent, graph on", "mode": "rules", "config": "treatment"},
        ],
        "sets": {"heldout_leasing_v1": {"items": 1, "size": 1, "version": "v1", "file": "heldout_leasing_v1.json", "sha256": "a" * 64}},
    }
    blocks = report.blocks(manifest, records)
    text = "\n".join(str(b) for b in blocks)
    assert "Held-out paraphrases (heldout_leasing_v1): passed, by kind" in text
    assert report.ABOUT["heldout"] in text
