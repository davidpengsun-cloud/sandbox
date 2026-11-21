"""Baselines compare only the same role that the candidate's profile asks as."""
import json

from kge.eval import margin
from tests.unit.test_set_changes import margin_setup


def test_baseline_for_another_existing_role_is_refused_before_asking(tmp_path, monkeypatch, capsys):
    baseline, prof, _ = margin_setup(tmp_path, monkeypatch, roles=("reader", "other_reader"))
    baseline["principal"] = "other_reader"
    prof.baseline.write_text(json.dumps(baseline))
    monkeypatch.setattr(margin, "chunks", lambda _: False)
    assert margin.check([]) == 2
    err = capsys.readouterr().err
    assert "B01" in err
    assert "other_reader" in err and "reader" in err
    assert "differs" in err
    assert margin.BASELINE_FIX in err
