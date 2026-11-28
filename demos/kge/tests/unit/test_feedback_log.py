"""The feedback log (#464, requirement 9): append-only, behind the FeedbackLog port, as JSON lines under the run state's
feedback/ folder or one object a record under feedback/<release>/ in the object store, each record its seven fields and
never a question, a principal or a word."""
from __future__ import annotations

import json
import stat
from dataclasses import fields

import pytest

from kge.adapters.feedbacklog import feedback_log
from kge.adapters.feedbacklog.folder import FolderFeedbackLog
from kge.adapters.feedbacklog.objstore import StoreFeedbackLog
from kge.adapters.objstore import open_store
from kge.ports import FeedbackLog, FeedbackRecord, StoreConflict

KEPT = {"release", "time", "verdict", "answer_mode", "answered_by", "plan", "receipt_id"}


def record(verdict="confirmed", release="rel-1"):
    return FeedbackRecord(release=release, time="2026-11-15T10:00:00+00:00", verdict=verdict,
                          answer_mode="ai_generated", answered_by="gemini",
                          plan={"measure": "nna", "op": "minus", "filters": [{"column": "client_id", "value": "<client>"}]},
                          receipt_id="0f" * 16)


def test_a_record_holds_the_seven_fields_and_no_question_word_or_principal():
    assert {f.name for f in fields(FeedbackRecord)} == KEPT
    with pytest.raises(TypeError):
        FeedbackRecord(question="who?", **{k: "x" for k in KEPT})
    with pytest.raises(TypeError):
        FeedbackRecord(principal="ana", **{k: "x" for k in KEPT})
    with pytest.raises(TypeError):
        FeedbackRecord(word="minus", **{k: "x" for k in KEPT})


def test_the_factory_gives_the_folder_log_without_a_store(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    log = feedback_log(tmp_path)
    assert isinstance(log, FeedbackLog) and isinstance(log, FolderFeedbackLog)
    assert log.folder == tmp_path / "feedback"


def test_the_factory_gives_the_store_log_with_a_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    log = feedback_log(tmp_path / "state")
    assert isinstance(log, FeedbackLog) and isinstance(log, StoreFeedbackLog)


def test_the_folder_log_appends_json_lines_mode_600(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    log = feedback_log(tmp_path)
    log.append(record("confirmed"))
    log.append(record("not_what_i_meant"))
    path = tmp_path / "feedback" / "rel-1.jsonl"
    lines = path.read_text().splitlines()
    assert [json.loads(line)["verdict"] for line in lines] == ["confirmed", "not_what_i_meant"]
    assert all(set(json.loads(line)) == KEPT for line in lines)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    log.append(record("confirmed"))
    assert path.read_text().splitlines()[:2] == lines


def test_the_store_log_writes_one_object_a_record_and_overwrites_none(tmp_path):
    store = open_store(str(tmp_path / "store"))
    log = StoreFeedbackLog(store)
    log.append(record("confirmed"))
    log.append(record("not_what_i_meant"))
    log.append(record("confirmed", release="rel-2"))
    keys = store.list("feedback/")
    assert len(keys) == 3
    assert sum(k.startswith("feedback/rel-1/") for k in keys) == 2
    assert sum(k.startswith("feedback/rel-2/") for k in keys) == 1
    for key in keys:
        assert set(json.loads(store.get(key))) == KEPT
    with pytest.raises(StoreConflict):
        store.put_version(keys[0], b"{}", "0")


@pytest.mark.parametrize("bad", [
    {"question": "what was NNA for Acme?"}, {"verdict": "maybe"}, {"release": "../escape"}, {"release": ""},
    {"plan": {"x": float("nan")}}, {"plan": object()}])
def test_both_logs_refuse_what_is_not_a_record(tmp_path, bad):
    logs = [FolderFeedbackLog(tmp_path / "feedback"), StoreFeedbackLog(open_store(str(tmp_path / "store")))]
    for log in logs:
        with pytest.raises((ValueError, TypeError)):
            if "question" in bad:
                log.append(bad)
            else:
                log.append(FeedbackRecord(**{**record().__dict__, **bad}))
    assert not (tmp_path / "feedback").exists() or not any((tmp_path / "feedback").iterdir())


def test_neither_log_offers_a_read_rewrite_or_delete():
    for cls in (FolderFeedbackLog, StoreFeedbackLog):
        public = {name for name in dir(cls) if not name.startswith("_")}
        assert public == {"append"}
