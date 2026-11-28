"""The evaluation suite's generator (tests/cerebro_suite.py; review of PR #728): its pure parts, held against the
committed fixture, with no build. What it records from KGE is the integration lane's
(tests/integration/test_open_items.py); what Cerebro does with it is the portal suite's."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests import cerebro_suite
from tests.cerebro_suite import ABOUT, FIXTURE, ORDER, PORTAL_SUITE, assembled, compact, named_by, one_line, words


@pytest.fixture(scope="module")
def suite():
    return json.loads(FIXTURE.read_text())


def test_words_are_runs_of_letters_and_digits_as_the_portal_splits_them():
    assert words("retail NNA, Q1-2025 (net_new) café") == ["retail", "NNA", "Q1", "2025", "net", "new", "café"]
    assert words("") == [] and words("—") == []


def test_named_by_gives_the_names_and_the_qualifiers_the_step_may_ask_about():
    reading = {"bindings": [{"kind": "term", "by": "explicit", "term": "Pooled AUM", "text": "AUM", "start": 20, "end": 23},
                            {"kind": "term", "by": "explicit", "term": "Pooled AUM", "text": "harbour", "start": 0, "end": 7},
                            {"kind": "term", "by": "default_alias", "term": "Spot FUM", "text": "FUM", "start": 30, "end": 33},
                            {"kind": "term", "by": "explicit", "term": "Vault AUM", "text": "vault AUM"},
                            {"kind": "period", "text": "March 2025", "start": 8, "end": 18}],
               "open_items": [{"item": "term", "text": "FUM"}, {"item": "period"}, {"item": "grouping", "text": "desk"}]}
    assert named_by(reading) == ({"AUM", "FUM"}, {"harbour"}), "in the question's order; a binding with no place is left out"
    assert named_by({"bindings": [], "open_items": []}) == (set(), set())
    same = {"bindings": [{"kind": "term", "by": "explicit", "term": "Spot AUM", "text": "AUM", "start": 0, "end": 3},
                         {"kind": "term", "by": "explicit", "term": "Spot AUM", "text": "aum", "start": 8, "end": 11}], "open_items": []}
    assert named_by(same) == ({"aum"}, set()), "a name written twice has no qualifier"


def test_compact_keeps_each_option_list_once_and_drops_a_bindings_own_options():
    options, table = {}, []
    months = [{"value": "2025-06", "label": "June 2025"}]
    reading = {"bindings": [{"kind": "term", "text": "AUM", "options": [{"label": "Spot AUM"}]}],
               "open_items": [{"item": "period", "resolution": "default", "options": months}, {"item": "question"},
                              {"item": "period", "resolution": "default", "options": list(months)}]}
    assert compact(reading, options, table) == {"bindings": [{"kind": "term", "text": "AUM"}],
                                                "open_items": [{"item": "period", "resolution": "default", "options": 0},
                                                               {"item": "question", "options": 1},
                                                               {"item": "period", "resolution": "default", "options": 0}]}
    assert table == [months, []]
    assert reading["open_items"][0]["options"] == months, "the reading given is left as it was"


def test_a_wording_is_assembled_from_the_fixture_kges_recording_and_the_portal_suites_keys(suite):
    held = {"question": "harbour AUM in March 2025", "sources": ["r1/a.txt"], "hotfix": ["r1/a.txt: clear clear -"], "kge_sample": True,
            "ask_terms": ["stale"], "kge": {"stale": True}, "expected": {"outcome": "none"}, "hotfix_reading": "agrees",
            "verdicts": {"hotfix": "pass", "now": "pass"}, "left_out": 1}
    entry = {"question": held["question"], "ask_terms": None,
             "reading": {"bindings": [{"kind": "term", "term": "Pooled AUM", "text": "harbour AUM", "options": []}, {"kind": "period", "text": "March 2025"}],
                         "open_items": [{"item": "question", "resolution": "unanswerable", "reason": "No figure is given."}]}}
    table = []
    wording = assembled(held, entry, {}, table)
    assert wording == {"question": held["question"], "sources": ["r1/a.txt"], "hotfix": ["r1/a.txt: clear clear -"], "ask_terms": None,
                       "kge": {"bound_terms": ["Pooled AUM"], "open_items": ["question"],
                               "reading": {"bindings": [{"kind": "term", "term": "Pooled AUM", "text": "harbour AUM"}, {"kind": "period", "text": "March 2025"}],
                                           "open_items": [{"item": "question", "resolution": "unanswerable", "reason": "No figure is given.", "options": 0}]}},
                       "kge_sample": True, "expected": {"outcome": "none"}, "hotfix_reading": "agrees", "verdicts": {"hotfix": "pass", "now": "pass"}}
    assert list(wording)[-len(PORTAL_SUITE):] == list(PORTAL_SUITE), "the portal suite's keys last, in its order"
    fresh = assembled({key: value for key, value in held.items() if key not in PORTAL_SUITE}, entry, {}, [])
    assert not set(PORTAL_SUITE) & set(fresh), "a wording the portal suite has not graded yet has none of its keys"
    assert all(list(each) == list(wording) for each in suite["wordings"]), "every wording of the fixture has these keys, in this order"


def test_the_fixture_is_written_as_the_portal_suite_writes_it_byte_for_byte(suite, tmp_path, monkeypatch):
    assert one_line({"a": [1, "é — “x”"], "b": None, "c": True}) == '{"a":[1,"é — “x”"],"b":null,"c":true}'
    monkeypatch.setattr(cerebro_suite, "FIXTURE", tmp_path / "suite.json")
    cerebro_suite.write(suite)
    assert (tmp_path / "suite.json").read_bytes() == FIXTURE.read_bytes(), "rewriting the fixture changes no byte of it"
    assert list(suite) == ["about", "card_0_14_58", "kge_tools", "kge_options", "wordings"]
    assert FIXTURE.read_text().count("\n") == 4 + len(suite["wordings"]) + 4, "a line a top-level key and a line a wording, between the brackets' own"


def test_the_fixture_says_of_itself_what_the_generator_writes(suite):
    assert suite["about"] == ABOUT and suite["card_0_14_58"]["order"] == ORDER
    assert all(name in ABOUT for name in PORTAL_SUITE)
    assert set(suite["kge_tools"]) == {"map_term", "search_filter"}
    names, qualifiers = set(), set()
    for wording in suite["wordings"]:
        reading = wording["kge"]["reading"]
        assert all(isinstance(item["options"], int) and 0 <= item["options"] < len(suite["kge_options"]) for item in reading["open_items"])
        assert all("options" not in binding for binding in reading["bindings"])
        more_names, more_qualifiers = named_by(reading)
        names |= more_names
        qualifiers |= more_qualifiers
    assert names == set(suite["kge_tools"]["map_term"]), "map_term is recorded for every name a reading gives, and no other"
    assert qualifiers == set(suite["kge_tools"]["search_filter"]), "search is recorded for every qualifier, and no other"


def test_the_command_refuses_a_call_it_cannot_run():
    for arguments in ([], ["record", "out.jsonl"], ["assemble"], ["replay", "x", "0", "1"]):
        ran = subprocess.run([sys.executable, "-m", "tests.cerebro_suite", *arguments], cwd=FIXTURE.parents[4], capture_output=True, text=True)
        assert ran.returncode == 1 and ran.stderr.startswith("usage: python -m tests.cerebro_suite record"), arguments
