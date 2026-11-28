"""The shadow log (#516, #450 requirements 6 and 7): append-only, behind the ShadowLog port, as JSON lines under the run
state's shadow/ folder or one object an observation under shadow/<client>/<release>/ in the object store, each
observation its fourteen fields, checked before anything is written. Synthetic observations only."""
from __future__ import annotations

import json
import stat
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog import shadow_log
from kge.adapters.shadowlog.folder import FolderShadowLog
from kge.adapters.shadowlog.objstore import PREFIX, StoreShadowLog
from kge.adapters.shadowlog.record import MAX_LINE, line_of
from kge.ports import StoreConflict, ShadowLog, ShadowObservation

KEPT = {"id", "time", "release", "graph_shapes", "coded_shapes", "shapes", "graph_outcome", "coded_outcome",
        "comparison", "served", "fallback", "takeover", "new_phrase", "evidence"}

SIDE = {"outcome": "propose", "plan": {"measures": [{"kind": "measure", "id": "#abc"}]}, "candidates": [],
        "applied": [[0, 4]]}
EVIDENCE = {"graph": SIDE, "coded": {**SIDE, "outcome": "abstain", "plan": None},
            "ledger": [{"kind": "name", "operation": None, "start": 0, "end": 4}]}


def observation(**overrides):
    base = dict(id="ab" * 16, time="2026-11-18T09:00:00.000+00:00", release="rel-1", graph_shapes=("shape.a",),
                coded_shapes=("planshapes.compose:range",), shapes=("planshapes.compose:range", "shape.a"),
                graph_outcome="propose", coded_outcome="propose", comparison="agree", served="coded",
                fallback=False, takeover=False, new_phrase=False, evidence=None)
    return ShadowObservation(**{**base, **overrides})


def disagreement(**overrides):
    return observation(**{"coded_outcome": "abstain", "comparison": "propose_decline", "evidence": EVIDENCE,
                          **overrides})


def test_an_observation_holds_the_fourteen_fields_and_no_question_or_principal():
    assert {f.name for f in fields(ShadowObservation)} == KEPT | {"via_terms"}
    with pytest.raises(TypeError):
        ShadowObservation(question="who?", **asdict(observation()))
    with pytest.raises(TypeError):
        ShadowObservation(principal="ana", **asdict(observation()))


def test_member_marker_is_optional_on_the_wire():
    assert set(json.loads(line_of(observation()))) == KEPT
    assert json.loads(line_of(observation(via_terms=True)))["via_terms"] is True
    with pytest.raises(ValueError, match="via_terms"):
        line_of(observation(via_terms="true"))


def test_the_factory_gives_the_folder_log_without_a_store(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    log = shadow_log(tmp_path, "client-a")
    assert isinstance(log, ShadowLog) and isinstance(log, FolderShadowLog)
    assert log.folder == tmp_path / "shadow"


def test_the_factory_gives_the_store_log_with_a_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    log = shadow_log(tmp_path / "state", "client-a")
    assert isinstance(log, ShadowLog) and isinstance(log, StoreShadowLog)
    assert log.prefix == f"{PREFIX}/client-a/"


def test_the_folder_log_appends_json_lines_mode_600_and_reads_them_back(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    log = shadow_log(tmp_path, "client-a")
    assert log.releases() == [] and log.read("rel-1") == []
    log.append(observation())
    log.append(disagreement(id="cd" * 16))
    path = tmp_path / "shadow" / "rel-1.jsonl"
    lines = path.read_text().split("\n")[:-1]
    assert [json.loads(line)["comparison"] for line in lines] == ["agree", "propose_decline"]
    assert all(set(json.loads(line)) == KEPT for line in lines)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    log.append(observation(id="ef" * 16, release="rel-2"))
    log.append(observation(id="01" * 16))
    assert path.read_text().split("\n")[:-1][:2] == lines
    assert log.releases() == ["rel-1", "rel-2"]
    assert [o["id"] for o in log.read("rel-1")] == ["ab" * 16, "cd" * 16, "01" * 16]
    assert log.read("rel-1")[1] == json.loads(line_of(disagreement(id="cd" * 16)))
    assert [o["id"] for o in log.read("rel-2")] == ["ef" * 16]


def test_the_store_log_writes_one_object_an_observation_and_overwrites_none(tmp_path):
    store = open_store(str(tmp_path / "store"))
    ticks = iter(datetime(2026, 11, 18, 9, 0, second, tzinfo=timezone.utc) for second in range(10))
    log = StoreShadowLog(store, "client-a", clock=lambda: next(ticks))
    log.append(observation())
    log.append(disagreement(id="cd" * 16))
    log.append(observation(id="ef" * 16, release="rel-2"))
    StoreShadowLog(store, "client-b").append(observation(id="99" * 16, release="rel-9"))
    keys = store.list("shadow/client-a/")
    assert len(keys) == 3
    assert sum(k.startswith("shadow/client-a/rel-1/") for k in keys) == 2
    assert sum(k.startswith("shadow/client-a/rel-2/") for k in keys) == 1
    first = datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc)
    assert keys[0] == f"shadow/client-a/rel-1/{first:%Y%m%dT%H%M%S%fZ}-" + "ab" * 16 + ".json"
    for key in keys:
        assert set(json.loads(store.get(key))) == KEPT
    with pytest.raises(StoreConflict):
        store.put_version(keys[0], b"{}", "0")
    assert log.releases() == ["rel-1", "rel-2"]
    assert [o["id"] for o in log.read("rel-1")] == ["ab" * 16, "cd" * 16]
    assert [o["id"] for o in log.read("rel-2")] == ["ef" * 16]
    assert log.read("rel-9") == []


def test_the_store_log_refuses_a_client_that_is_not_one_segment(tmp_path):
    with pytest.raises(ValueError):
        StoreShadowLog(open_store(str(tmp_path / "store")), "../other")


@pytest.mark.parametrize("backend", ["folder", "store"])
def test_read_folds_retries_by_id_without_hiding_malformed_records(tmp_path, backend):
    store = open_store(str(tmp_path / "store"))
    ticks = iter(datetime(2026, 11, 18, 9, 0, second, tzinfo=timezone.utc) for second in range(5))
    folder = tmp_path / "shadow"
    log = FolderShadowLog(folder) if backend == "folder" else StoreShadowLog(store, "client-a", clock=lambda: next(ticks))
    first = observation()
    distinct = observation(id="cd" * 16)
    retry = disagreement(time="2026-11-18T09:00:02.000+00:00")
    log.append(first)
    log.append(distinct)
    original = log.read("rel-1")
    assert original == [json.loads(line_of(first)), json.loads(line_of(distinct))]
    log.append(retry)
    prefix = "shadow/client-a/rel-1/"
    malformed = {"id": first.id}
    unknown = json.loads(line_of(first)) | {"unexpected": True}
    for row in (malformed, unknown):
        raw = json.dumps(row).encode() + b"\n"
        if backend == "folder":
            with (folder / "rel-1.jsonl").open("ab") as stream:
                stream.write(raw)
        else:
            store.put_version(f"{prefix}{next(ticks):%Y%m%dT%H%M%S%fZ}-{first.id}.json", raw, "0")

    assert log.read("rel-1") == [*original, malformed, unknown]
    if backend == "folder":
        assert len((folder / "rel-1.jsonl").read_text().splitlines()) == 5
    else:
        assert len(store.list(prefix)) == 5


def test_a_truncated_side_and_a_clarification_are_kept():
    side = {"outcome": "clarify", "plan": None, "candidates": ["measure:x", "col:t.k"], "applied": [[2, 5]]}
    kept = disagreement(coded_outcome="clarify", evidence={**EVIDENCE, "graph": {**SIDE, "plan": None, "truncated": True},
                                                            "coded": side})
    assert json.loads(line_of(kept))["evidence"]["coded"]["candidates"] == ["measure:x", "col:t.k"]


SELECTOR = {
    "specify or below, coded served": ("coded", False, False, "propose", "clarify"),
    "shadow, takeover held, coded served": ("coded", False, False, "propose", "abstain"),
    "shadow, decline takeover": ("graph", False, True, "propose", "clarify"),
    "graph_first, both agree": ("graph", False, False, "propose", "propose"),
    "graph_first, graph over a coded decline": ("graph", False, False, "propose", "abstain"),
    "graph_first, disagree fallback": ("coded", True, False, "propose", "propose"),
    "graph_first, graph declined fallback": ("coded", True, False, "clarify", "propose"),
    "retired, graph proposes over a coded decline": ("graph", False, False, "propose", "clarify"),
    "retired, graph declines": ("graph", False, False, "abstain", "propose"),
    "retired, both decline": ("graph", False, False, "clarify", "abstain"),
}


@pytest.mark.parametrize("served, fallback, takeover, graph_outcome, coded_outcome", list(SELECTOR.values()),
                         ids=list(SELECTOR))
def test_every_answer_the_stage_selector_serves_is_kept(served, fallback, takeover, graph_outcome, coded_outcome):
    """The served side and flags shapestage.select gives at each stage (its from_coded and from_graph branches)."""
    proposals = (graph_outcome == "propose") + (coded_outcome == "propose")
    comparison = ("propose_decline" if proposals == 1 else "plans_differ" if proposals == 2
                  else "agree" if graph_outcome == coded_outcome else "declines_differ")
    sides = {name: {"outcome": outcome, "plan": SIDE["plan"] if outcome == "propose" else None, "candidates": [],
                    "applied": []} for name, outcome in (("graph", graph_outcome), ("coded", coded_outcome))}
    evidence = {**sides, "ledger": []} if comparison != "agree" or takeover else None
    kept = observation(served=served, fallback=fallback, takeover=takeover, graph_outcome=graph_outcome,
                       coded_outcome=coded_outcome, comparison=comparison, evidence=evidence)
    assert json.loads(line_of(kept))["served"] == served


def test_a_pack_shape_id_that_starts_with_a_digit_is_kept():
    kept = observation(graph_shapes=("2-period-change",), shapes=("2-period-change", "planshapes.compose:range"))
    assert json.loads(line_of(kept))["graph_shapes"] == ["2-period-change"]


def test_a_takeover_and_a_model_answer_are_kept():
    assert json.loads(line_of(disagreement(served="graph", takeover=True)))["takeover"] is True
    assert json.loads(line_of(observation(served="model")))["served"] == "model"
    assert json.loads(line_of(observation(served="coded", fallback=True)))["fallback"] is True
    assert json.loads(line_of(observation(graph_shapes=(), shapes=("planshapes.compose:range",))))["graph_shapes"] == []


def nested_forbidden():
    return disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "plan": {"question": "x"}}})


def oversize():
    return disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "plan": {"values": ["v" * MAX_LINE]}}})


BAD = {
    "a dict": lambda: asdict(observation()),
    "an unknown outcome": lambda: observation(graph_outcome="maybe"),
    "an escaping release": lambda: observation(release="../x"),
    "an empty release": lambda: observation(release=""),
    "an id that is not hex": lambda: observation(id="Z" * 32),
    "a time not in UTC": lambda: observation(time="2026-11-18T09:00:00.000+01:00"),
    "unsorted shapes": lambda: observation(shapes=("shape.a", "planshapes.compose:range")),
    "shapes not the union": lambda: observation(shapes=("shape.a",)),
    "a bad shape name": lambda: observation(graph_shapes=("Shape A",), shapes=("Shape A", "planshapes.compose:range")),
    "evidence on an agree": lambda: observation(evidence=EVIDENCE),
    "no evidence on a disagreement": lambda: disagreement(evidence=None),
    "agree beside a decline": lambda: observation(coded_outcome="abstain"),
    "plans differ beside two declines": lambda: disagreement(graph_outcome="clarify", comparison="plans_differ"),
    "declines differ beside two abstains": lambda: disagreement(graph_outcome="abstain", comparison="declines_differ"),
    "a takeover with coded propose": lambda: disagreement(coded_outcome="propose", comparison="plans_differ",
                                                         served="graph", takeover=True),
    "a fallback served by graph": lambda: observation(served="graph", fallback=True),
    "a takeover served by coded": lambda: disagreement(served="coded", takeover=True),
    "a takeover of a graph decline": lambda: disagreement(graph_outcome="clarify", coded_outcome="propose",
                                                          served="graph", takeover=True),
    "a shape name too long": lambda: observation(graph_shapes=("a" * 257,),
                                                 shapes=("a" * 257, "planshapes.compose:range")),
    "a forbidden key nested in evidence": nested_forbidden,
    "evidence with an extra key": lambda: disagreement(evidence={**EVIDENCE, "rows": []}),
    "a ledger piece with its text": lambda: disagreement(
        evidence={**EVIDENCE, "ledger": [{"kind": "name", "operation": None, "start": 0, "end": 4, "value": "x"}]}),
    "a NaN": lambda: disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "plan": {"n": float("nan")}}}),
    "an object that is not JSON": lambda: disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "plan": object()}}),
    "an oversize line": oversize,
    "a time without milliseconds": lambda: observation(time="2026-11-18T09:00:00+00:00"),
    "a time written with Z": lambda: observation(time="2026-11-18T09:00:00.000Z"),
    "a shape named twice": lambda: observation(graph_shapes=("shape.a", "shape.a")),
    "a side outcome unlike the observation's": lambda: disagreement(evidence={**EVIDENCE, "coded": {**SIDE}}),
    "a plan on a declining side": lambda: disagreement(
        evidence={**EVIDENCE, "coded": {**EVIDENCE["coded"], "plan": {"measures": []}}}),
    "a candidate holding a newline": lambda: disagreement(
        evidence={**EVIDENCE, "coded": {**EVIDENCE["coded"], "candidates": ["value:t.s:A\nB"]}}),
    "a candidate longer than an id": lambda: disagreement(
        evidence={**EVIDENCE, "coded": {**EVIDENCE["coded"], "candidates": ["x" * 513]}}),
    "applied that is text": lambda: disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "applied": "words"}}),
    "an applied span backwards": lambda: disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "applied": [[4, 0]]}}),
    "a ledger kind that is words": lambda: disagreement(
        evidence={**EVIDENCE, "ledger": [{"kind": "Some Words", "operation": None, "start": 0, "end": 4}]}),
    "a ledger operation that is a key": lambda: disagreement(
        evidence={**EVIDENCE, "ledger": [{"kind": "name", "operation": "ACC-0042", "start": 0, "end": 4}]}),
    "a ledger position that is text": lambda: disagreement(
        evidence={**EVIDENCE, "ledger": [{"kind": "name", "operation": None, "start": "0", "end": 4}]}),
    "a proposal's plan dropped without the truncated marker": lambda: disagreement(
        evidence={**EVIDENCE, "graph": {**SIDE, "plan": None}}),
    "a ledger piece that ends where it starts": lambda: disagreement(
        evidence={**EVIDENCE, "ledger": [{"kind": "name", "operation": None, "start": 4, "end": 4}]}),
    "truncated that is text": lambda: disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "truncated": "words"}}),
}


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_both_logs_refuse_what_breaks_a_rule_and_write_nothing(tmp_path, bad):
    store = open_store(str(tmp_path / "store"))
    logs = [FolderShadowLog(tmp_path / "shadow"), StoreShadowLog(store, "client-a")]
    for log in logs:
        with pytest.raises(ValueError):
            log.append(bad())
    assert not (tmp_path / "shadow").exists() or not any((tmp_path / "shadow").iterdir())
    assert store.list("") == []


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_a_member_holding_a_unicode_line_separator_reads_back_from_both_logs(tmp_path, separator):
    plan = {"filters": [{"kind": "compare", "value": {"kind": "text", "value": f"A{separator}B"}}]}
    kept = disagreement(evidence={**EVIDENCE, "graph": {**SIDE, "plan": plan}})
    store = open_store(str(tmp_path / "store"))
    for log in (FolderShadowLog(tmp_path / "shadow"), StoreShadowLog(store, "client-a")):
        log.append(kept)
        assert log.read("rel-1") == [json.loads(line_of(kept))]


def test_reading_an_escaping_release_is_refused(tmp_path):
    for log in (FolderShadowLog(tmp_path / "shadow"), StoreShadowLog(open_store(str(tmp_path / "store")), "c")):
        with pytest.raises(ValueError):
            log.read("../x")


def test_neither_log_offers_a_rewrite_or_delete():
    for cls in (FolderShadowLog, StoreShadowLog):
        public = {name for name in dir(cls) if not name.startswith("_")}
        assert public == {"append", "releases", "read"}
    assert {name for name in dir(ShadowLog) if not name.startswith("_")} == {"append", "releases", "read"}


def test_an_observation_is_frozen():
    with pytest.raises(AttributeError):
        observation().comparison = "plans_differ"
    assert replace(observation(), new_phrase=True).new_phrase is True
