"""Synthetic release histories and observations exercise the shape evidence fold."""

import json
import random
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pytest

from kge.adapters.objstore.folder import LocalStore
from kge.adapters.shadowlog import record as shadow_record
from kge.adapters.shadowlog.seals import StoreShadowSeals
from kge.eval import maturity, shapefold
from kge.ports import ShadowObservation


def observation(at="2026-01-01T12:00:00.000+00:00", release="r1", shapes=("s.a",), **changes):
    values = dict(
        id="1" * 32, time=at, release=release, graph_shapes=shapes, coded_shapes=(), shapes=shapes,
        graph_outcome="propose", coded_outcome="propose", comparison="agree", served="coded",
        fallback=False, takeover=False, new_phrase=False, evidence=None,
    )
    values.update({key: value for key, value in changes.items() if key != "via_terms"})
    if "evidence" not in changes and (values["comparison"] != "agree" or values["takeover"]):
        def side(outcome):
            return {"outcome": outcome, "plan": {} if outcome == "propose" else None, "candidates": [], "applied": []}
        values["evidence"] = {"graph": side(values["graph_outcome"]), "coded": side(values["coded_outcome"]), "ledger": []}
    row = json.loads(json.dumps(asdict(ShadowObservation(**values))))
    if "via_terms" in changes:
        row["via_terms"] = changes["via_terms"]
    return row


def release_history():
    return shapefold.events([
        {"release": "r1", "model_hash": "same-hash", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "model_hash": "same-hash", "released_at": "2026-01-02T00:00:00Z"},
        {"release": "r1", "model_hash": "same-hash", "released_at": "2026-01-03T00:00:00Z", "rollback_of": "r1"},
    ])


def by_id(rows):
    return {(row.shape, row.release_event): json.loads(row.counters) for row in rows}


@pytest.mark.parametrize("raw", ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.000+00:00"])
def test_parse_time_accepts_utc_z_and_offset(raw):
    assert shapefold.parse_time(raw) == datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw", [None, "", "not a time", "2026-01-01T00:00:00", "2026-01-01T00:00:00+03:00"])
def test_parse_time_refuses_invalid_or_non_utc_times(raw):
    assert shapefold.parse_time(raw) is None


def test_release_events_keep_history_order_same_hash_and_rollback_identity():
    events = release_history()
    assert len(events) == 3
    assert [event.release for event in events] == ["r1", "r2", "r1"]
    assert len({event.id for event in events}) == 3
    assert [event.rollback for event in events] == [False, False, True]
    assert [event.open for event in events] == [False, False, True]
    assert events[0].end == events[1].start
    assert events[1].end == events[2].start


def test_out_of_order_times_produce_empty_window_without_reordering_history():
    events = shapefold.events([
        {"release": "first", "released_at": "2026-01-02T00:00:00Z"},
        {"release": "second", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "third", "released_at": "2026-01-03T00:00:00Z"},
    ])
    assert [event.release for event in events] == ["first", "second", "third"]
    assert events[0].start == events[0].end == events[1].start
    assigned = shapefold.assign([observation(at="2026-01-02T00:00:00.000+00:00", release="second")], events)
    assert len(assigned.by_event[1]) == 1


def test_assignment_observes_event_boundaries_and_matches_served_release():
    events = release_history()
    before = observation(at="2025-12-31T23:59:59.999+00:00")
    just_before = observation(at="2026-01-01T23:59:59.999+00:00")
    at_boundary = observation(at="2026-01-02T00:00:00.000+00:00", release="r2")
    mismatch = observation(at="2026-01-02T00:00:00.000+00:00", release="r1")
    rollback = observation(at="2026-01-03T00:00:00.000+00:00", release="r1")
    assigned = shapefold.assign([before, just_before, at_boundary, mismatch, rollback], events)
    assert assigned.unassigned == 1
    assert assigned.malformed == 0
    assert assigned.by_event[0] == [just_before]
    assert assigned.by_event[1] == [at_boundary]
    assert assigned.mismatched[1] == [mismatch]
    assert assigned.by_event[2] == [rollback]


@pytest.mark.parametrize("change", [
    {"time": None}, {"time": "bad"}, {"release": None}, {"shapes": "s.a"},
    {"shapes": [7]}, {"comparison": "unknown"},
])
def test_malformed_observation_is_counted_and_never_attributed(change):
    row = observation()
    row.update(change)
    assigned = shapefold.assign([row], release_history())
    assert assigned.malformed == 1
    assert not any(assigned.by_event.values())
    assert not any(assigned.mismatched.values())


def test_fold_counts_disjoint_buckets_per_shape_and_excludes_mismatches_and_text():
    events = release_history()
    base = observation(shapes=("s.a", "s.b"))
    new_phrase = observation(shapes=("s.a",), new_phrase=True, via_terms=True)
    via_terms = observation(shapes=("s.a",), via_terms=True)
    disagree = observation(shapes=("s.a",), comparison="plans_differ")
    disagree["evidence"]["graph"]["candidates"] = ["private-evidence"]
    mismatched = observation(at="2026-01-02T01:00:00.000+00:00", release="r1")
    unattributed = observation(shapes=())
    assigned = shapefold.assign([base, new_phrase, via_terms, disagree, mismatched, unattributed], events)
    rows, no_shape = shapefold.fold(events, assigned, {0: {"s.a": "shadow", "s.b": "shadow"}, 1: {"s.a": "shadow"}})
    data = by_id(rows)
    a = data[("s.a", events[0].id)]
    b = data[("s.b", events[0].id)]
    assert (a["live"]["compared"], a["live"]["agreed"], a["live"]["plans_differ"]) == (2, 1, 1)
    assert a["new_phrase"]["compared"] == a["via_terms"]["compared"] == 1
    assert b["live"]["compared"] == 1
    assert data[("s.a", events[1].id)]["release_mismatch"] == 1
    assert data[("s.a", events[1].id)]["live"]["compared"] == 0
    assert no_shape == 1
    assert "private-evidence" not in repr(rows)


def test_zero_observation_staged_shape_exists_and_events_are_separate():
    events = release_history()
    assigned = shapefold.assign([], events)
    stages = {0: {"s.a": "specify"}, 1: {"s.a": "shadow"}, 2: {"s.a": "graph_first"}}
    rows, _ = shapefold.fold(events, assigned, stages)
    data = by_id(rows)
    assert ("s.a", events[0].id) not in data
    assert data[("s.a", events[1].id)]["event_number"] == 1
    assert data[("s.a", events[2].id)]["event_number"] == 2
    assert data[("s.a", events[2].id)]["live"]["compared"] == 0


def test_fold_is_deterministic_and_does_not_mutate_inputs():
    events = release_history()
    obs = [observation(shapes=("s.a", "s.b")), observation(new_phrase=True)]
    before = deepcopy(obs)
    assigned = shapefold.assign(obs, events)
    first = shapefold.fold(events, assigned, {0: {"s.a": "shadow", "s.b": "shadow"}})
    second = shapefold.fold(events, assigned, {0: {"s.a": "shadow", "s.b": "shadow"}})
    assert first == second
    assert obs == before
    rows, _ = first
    assert [(row.shape, row.release_event) for row in rows] == sorted((row.shape, row.release_event) for row in rows)
    for row in rows:
        assert row.counters == json.dumps(json.loads(row.counters), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_maturity_total_receives_each_disjoint_bucket():
    observations = [observation(), observation(new_phrase=True), observation(via_terms=True)]
    buckets = maturity.tally(observations)
    assert maturity.total(buckets).compared == 3
    assert buckets["live"].compared == buckets["new_phrase"].compared == buckets["via_terms"].compared == 1


def test_same_hash_release_events_count_separately_and_rollback_breaks_run():
    events = release_history()
    observations = [
        observation(),
        observation(at="2026-01-02T12:00:00.000+00:00", release="r2"),
        observation(at="2026-01-03T12:00:00.000+00:00", release="r1"),
    ]
    assigned = shapefold.assign(observations, events)
    stages = {event.index: {"s.a": "shadow"} for event in events}
    rows, _ = shapefold.fold(events, assigned, stages)
    data = by_id(rows)
    assert [data[("s.a", event.id)]["live"]["compared"] for event in events] == [1, 1, 1]
    assert shapefold.runs(events, rows, lambda counts: counts["live"]["compared"] >= 1)["s.a"] == 0


def test_insufficient_closed_event_breaks_run_but_open_event_still_accruing_does_not():
    events = shapefold.events([
        {"release": "r1", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "released_at": "2026-01-02T00:00:00Z"},
        {"release": "r3", "released_at": "2026-01-03T00:00:00Z"},
    ])
    assigned = shapefold.assign([
        observation(),
        observation(at="2026-01-02T12:00:00.000+00:00", release="r2"),
    ], events)
    stages = {event.index: {"s.a": "shadow"} for event in events}
    rows, _ = shapefold.fold(events, assigned, stages)
    assert shapefold.runs(events, rows, lambda counts: counts["live"]["compared"] >= 1)["s.a"] == 2
    interrupted = shapefold.assign([observation()], events)
    rows, _ = shapefold.fold(events, interrupted, stages)
    assert shapefold.runs(events, rows, lambda counts: counts["live"]["compared"] >= 1)["s.a"] == 0


@pytest.mark.parametrize("changes,expected", [
    ({}, {"compared": 1, "agreed": 1}),
    ({"comparison": "plans_differ"}, {"compared": 1, "plans_differ": 1}),
    ({"graph_outcome": "clarify", "comparison": "propose_decline", "fallback": True},
     {"compared": 1, "propose_decline": 1, "graph_declines": 1, "fallbacks": 1}),
    ({"coded_outcome": "abstain", "comparison": "propose_decline", "served": "graph", "takeover": True},
     {"compared": 1, "propose_decline": 1, "coded_declines": 1, "takeovers": 1}),
    ({"graph_outcome": "clarify", "coded_outcome": "abstain", "comparison": "declines_differ"},
     {"compared": 1, "declines_differ": 1, "graph_declines": 1, "coded_declines": 1}),
])
def test_each_comparison_and_outcome_moves_its_own_counter(changes, expected):
    events = release_history()
    assigned = shapefold.assign([observation(**changes)], events)
    rows, _ = shapefold.fold(events, assigned, {0: {"s.a": "shadow"}})
    actual = by_id(rows)[("s.a", events[0].id)]["live"]
    assert {key: actual[key] for key in expected} == expected


def test_seeded_observations_match_direct_per_shape_per_bucket_recount():
    rng = random.Random(729)
    observations = []
    expected = {}
    for n in range(500):
        shapes = tuple(shape for shape in ("s.a", "s.b", "s.c") if rng.choice((True, False)))
        new_phrase, via_terms = rng.choice((True, False)), rng.choice((True, False))
        row = observation(shapes=shapes, id=f"{n:032x}", new_phrase=new_phrase, via_terms=via_terms)
        observations.append(row)
        bucket = "new_phrase" if new_phrase else "via_terms" if via_terms else "live"
        for shape in shapes:
            key = (shape, bucket)
            expected[key] = expected.get(key, 0) + 1
    events = release_history()
    rows, unattributed = shapefold.fold(events, shapefold.assign(observations, events), {})
    data = by_id(rows)
    assert unattributed == sum(not row["shapes"] for row in observations)
    for shape in ("s.a", "s.b", "s.c"):
        for bucket in ("live", "new_phrase", "via_terms"):
            actual = data[(shape, events[0].id)][bucket]
            assert actual["compared"] == actual["agreed"] == expected.get((shape, bucket), 0)


def test_malformed_outcomes_and_flags_are_not_evidence():
    rows = [observation(), observation(), observation(), observation()]
    del rows[0]["coded_outcome"]
    rows[1]["graph_outcome"] = "invented"
    rows[2]["new_phrase"] = "false"
    rows[3]["comparison"] = "declines_differ"
    assigned = shapefold.assign(rows, release_history())
    assert assigned.malformed == 4
    assert not assigned.by_event


def test_invalid_shape_name_cannot_put_question_text_into_status_rows():
    row = observation(shapes=("private question text",))
    assigned = shapefold.assign([row], release_history())
    assert assigned.malformed == 1
    assert not assigned.by_event


@pytest.mark.parametrize("change", [
    {"graph_shapes": ["s.a", "s.a"]},
    {"graph_shapes": ["s.a"], "coded_shapes": ["s.b"], "shapes": ["s.a"]},
    {"graph_shapes": ["s.a"], "coded_shapes": ["s.b"], "shapes": ["s.b", "s.a"]},
    {"served": "unknown"},
    {"served": "model", "fallback": True},
    {"graph_outcome": "propose", "coded_outcome": "abstain", "comparison": "propose_decline", "served": "coded", "takeover": True},
    {"comparison": "plans_differ", "evidence": None},
    {"comparison": "agree", "evidence": {"question": "PRIVATE_QUESTION"}},
    {"question": "PRIVATE_QUESTION"},
    {"via_terms": "yes"},
    {"id": "not-an-id"},
])
def test_corrupt_persisted_record_never_counts_as_evidence(change):
    row = observation()
    row.update(change)
    assigned = shapefold.assign([row], release_history())
    assert assigned.malformed == 1
    assert not assigned.by_event
    assert "PRIVATE_QUESTION" not in repr(assigned)


@pytest.mark.parametrize("via_terms", [True, False])
def test_forward_compatible_via_terms_boolean_survives_validation(via_terms):
    row = observation(via_terms=via_terms)
    history = release_history()
    assigned = shapefold.assign([row], history)
    assert assigned.malformed == 0
    rows, _ = shapefold.fold(history, assigned, {0: {"s.a": "shadow"}})
    data = by_id(rows)[("s.a", history[0].id)]
    assert data["via_terms" if via_terms else "live"]["compared"] == 1


def test_future_observation_default_accepts_old_logs_and_preserves_via_terms(monkeypatch):
    @dataclass(frozen=True)
    class FutureObservation(ShadowObservation):
        via_terms: bool = False

    monkeypatch.setattr(shapefold, "ShadowObservation", FutureObservation)
    monkeypatch.setattr(shadow_record, "ShadowObservation", FutureObservation)
    old = observation(id="1" * 32)
    explicit = observation(id="2" * 32, via_terms=True)
    missing_required = observation(id="3" * 32)
    del missing_required["served"]
    events = release_history()
    assigned = shapefold.assign([old, explicit, missing_required], events)
    assert assigned.malformed == 1
    assert assigned.by_event[0] == [old, explicit]
    rows, _ = shapefold.fold(events, assigned, {0: {"s.a": "shadow"}})
    data = by_id(rows)[("s.a", events[0].id)]
    assert data["live"]["compared"] == 1
    assert data["via_terms"]["compared"] == 1


def test_invalid_evidence_cannot_persist_private_values():
    row = observation(comparison="plans_differ")
    row["evidence"]["graph"]["plan"]["question"] = "PRIVATE_QUESTION"
    history = release_history()
    assigned = shapefold.assign([row], history)
    assert assigned.malformed == 1
    rows, _ = shapefold.fold(history, assigned, {0: {"s.a": "shadow"}})
    assert "PRIVATE_QUESTION" not in repr(rows)


def test_duplicate_shape_id_on_one_observation_is_malformed():
    row = observation(shapes=("s.a", "s.a", "s.b"))
    assigned = shapefold.assign([row], release_history())
    assert assigned.malformed == 1
    assert not assigned.by_event


class MemorySeals:
    def __init__(self):
        self.records = {}
        self.versions = {}
        self.windows = {}
        self.known = frozenset()
        self.writes = 0

    def events(self):
        return list(self.records)

    def read(self, release, event):
        key = (release, event)
        return deepcopy(self.records.get(key)), self.versions.get(key, "absent")

    def write(self, release, event, snapshot, version):
        key = (release, event)
        assert version == self.versions.get(key, "absent")
        self.records[key] = deepcopy(snapshot)
        self.writes += 1
        self.versions[key] = str(self.writes)

    def snapshot(self):
        return deepcopy(self.records), deepcopy(self.windows), str(self.writes), self.known

    def commit(self, records, windows, version, known):
        assert version == str(self.writes)
        self.records, self.windows = deepcopy(records), deepcopy(windows)
        self.known = known
        self.writes += 1


@pytest.mark.parametrize("declared", [{}, {"s.a": "shadow"}])
@pytest.mark.parametrize("partial", [False, True])
def test_missing_checkpoint_never_overwrites_or_deletes_existing_history(tmp_path, monkeypatch, declared, partial):
    class Profile:
        client = "synthetic"
        artifacts = tmp_path

        def run_dir(self):
            return tmp_path / "run"

    class Log:
        def releases(self):
            return ["r1"] if partial else []

        def read(self, _release):
            return [observation(id="a" * 32)]

    history = tmp_path / "versions.jsonl"
    history.write_text("\n".join(json.dumps(row) for row in [
        {"release": "r1", "model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "model_hash": "h2", "released_at": "2026-01-02T00:00:00Z"},
    ]) + "\n")
    monkeypatch.setattr(shapefold, "versions_path", lambda _profile: history)
    monkeypatch.setattr(shapefold, "stages_of", lambda _profile, _hash: declared)
    monkeypatch.setattr(shapefold, "_existing_statuses", lambda _conn: {"s.a@0000-r1"})
    monkeypatch.setattr(shapefold, "write", lambda *_args: pytest.fail("must preserve the existing graph"))
    seals = MemorySeals()
    with pytest.raises(shapefold.NotFolded):
        shapefold.run(Profile(), log=Log(), conn=object(), seals=seals)
    assert not seals.records
    assert shapefold.last_report(Profile())["written"] is False


def test_open_event_checkpoint_preserves_counts_across_append_and_close():
    first = shapefold.events([{"release": "r1", "model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"}])
    stages = {0: {"s.a": "shadow"}}
    seals = MemorySeals()
    row = observation(id="1" * 32)
    initial, _ = shapefold._durable(first, shapefold.assign([row], first), stages, seals)
    assert by_id(initial)[("s.a", first[0].id)]["live"]["compared"] == 1
    assert ("r1", first[0].id) in seals.records
    next_row = observation(id="2" * 32)
    more, _ = shapefold._durable(first, shapefold.assign([row, next_row], first), stages, seals,
                                 frozenset({initial[0].id}))
    assert by_id(more)[("s.a", first[0].id)]["live"]["compared"] == 2
    closed = shapefold.events([
        {"release": "r1", "model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "model_hash": "h2", "released_at": "2026-01-02T00:00:00Z"},
    ])
    closed_rows, _ = shapefold._durable(closed, shapefold.assign([], closed), stages, seals,
                                        frozenset({initial[0].id}))
    assert by_id(closed_rows)[("s.a", first[0].id)]["live"]["compared"] == 2
    assert by_id(closed_rows)[("s.a", first[0].id)]["event"]["open"] is False
    again, _ = shapefold._durable(closed, shapefold.assign([], closed), {}, seals,
                                  frozenset({initial[0].id}))
    assert by_id(again)[("s.a", first[0].id)]["event"]["open"] is False
    assert by_id(again)[("s.a", first[0].id)]["live"]["compared"] == 2


def test_release_less_bootstrap_and_zero_observations_can_be_folded_twice():
    history = shapefold.events([{"model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"}])
    seals = MemorySeals()
    stages = {0: {"s.a": "shadow"}}
    empty = shapefold.assign([], history)
    rows, _ = shapefold._durable(history, empty, stages, seals)
    assert ("bootstrap", history[0].id) in seals.records
    assert by_id(rows)[("s.a", history[0].id)]["live"]["compared"] == 0
    again, _ = shapefold._durable(history, empty, stages, seals, frozenset({rows[0].id}))
    assert again == rows


def test_bootstrap_namespace_does_not_collide_with_a_real_bootstrap_release():
    history = shapefold.events([
        {"model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "bootstrap", "model_hash": "h2", "released_at": "2026-01-02T00:00:00Z"},
    ])
    seals = MemorySeals()
    rows, _ = shapefold._durable(history, shapefold.assign([], history),
                                 {0: {"s.a": "shadow"}, 1: {"s.a": "shadow"}}, seals)
    assert {event for release, event in seals.events() if release == "bootstrap"} == {event.id for event in history}
    assert len(rows) == 2
    again, _ = shapefold._durable(history, shapefold.assign([], history), {}, seals,
                                  frozenset(row.id for row in rows))
    assert len(again) == 2


def test_sealed_event_preserves_counts_across_retries_pruning_and_late_arrivals():
    events = release_history()
    stage = {event.index: {"s.a": "shadow"} for event in events}
    first = observation(id="1" * 32)
    second = observation(id="2" * 32, new_phrase=True)
    late = observation(id="3" * 32, via_terms=True)
    seals = MemorySeals()

    def run(records):
        return shapefold._durable(events, shapefold.assign(records, events), stage, seals)[0]

    original = by_id(run([first, second]))[("s.a", events[0].id)]
    assert (original["live"]["compared"], original["new_phrase"]["compared"]) == (1, 1)
    num_writes = seals.writes
    assert by_id(run([first, second]))[("s.a", events[0].id)] == original
    assert seals.writes == num_writes
    assert by_id(run([]))[("s.a", events[0].id)] == original

    after_partial_prune = by_id(run([first, late]))[("s.a", events[0].id)]
    assert (after_partial_prune["live"]["compared"], after_partial_prune["new_phrase"]["compared"],
            after_partial_prune["via_terms"]["compared"]) == (1, 1, 1)
    assert by_id(run([]))[("s.a", events[0].id)] == after_partial_prune


def test_seals_distinguish_rollback_events_that_reuse_a_release_id():
    history = shapefold.events([
        {"release": "r1", "model_hash": "same-hash", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "model_hash": "same-hash", "released_at": "2026-01-02T00:00:00Z"},
        {"release": "r1", "model_hash": "same-hash", "released_at": "2026-01-03T00:00:00Z", "rollback_of": "r1"},
        {"release": "r3", "model_hash": "later-hash", "released_at": "2026-01-04T00:00:00Z"},
    ])
    stage = {event.index: {"s.a": "shadow"} for event in history}
    observations = [observation(id="1" * 32), observation(id="2" * 32, at="2026-01-03T01:00:00.000+00:00")]
    seals = MemorySeals()
    first_rows, _ = shapefold._durable(history, shapefold.assign(observations, history), stage, seals)
    assert {event for release, event in seals.events() if release == "r1"} == {history[0].id, history[2].id}
    pruned_rows, _ = shapefold._durable(history, shapefold.assign([], history), stage, seals)
    first, after = by_id(first_rows), by_id(pruned_rows)
    for index in (0, 2):
        key = ("s.a", history[index].id)
        assert first[key]["live"]["compared"] == after[key]["live"]["compared"] == 1


def test_duplicate_observation_ids_are_refused_before_sealing():
    history = release_history()
    rows = [observation(id="1" * 32), observation(id="1" * 32, new_phrase=True)]
    seals = MemorySeals()
    with pytest.raises(ValueError, match="distinct"):
        shapefold._durable(history, shapefold.assign(rows, history), {0: {"s.a": "shadow"}}, seals)
    assert seals.records == {}


def test_reused_observation_id_with_changed_content_is_refused_after_sealing():
    history = release_history()
    original = observation(id="1" * 32)
    changed = observation(id="1" * 32, new_phrase=True)
    seals = MemorySeals()
    stages = {event.index: {"s.a": "shadow"} for event in history}
    rows, _ = shapefold._durable(history, shapefold.assign([original], history), stages, seals)
    assert by_id(rows)[("s.a", history[0].id)]["live"]["compared"] == 1
    before = deepcopy(seals.records)
    with pytest.raises(ValueError, match="observation|digest|different"):
        shapefold._durable(history, shapefold.assign([changed], history), stages, seals)
    assert seals.records == before


def test_corrupt_persisted_counter_row_fails_closed(tmp_path):
    history = release_history()
    event = history[0]
    store = LocalStore(str(tmp_path), tmp_path)
    seals = StoreShadowSeals(store, "synthetic")
    corrupt = {"version": 1, "release": event.release, "event": event.id,
               "observation_digests": {}, "unattributed": 0,
               "rows": [{"id": f"s.a@{event.id}", "shape": "s.a", "release_event": event.id, "counters": "{}"}]}
    store.put(seals.key(event.release, event.id), json.dumps(corrupt).encode())
    with pytest.raises(ValueError, match="sealed|counter"):
        shapefold._durable(history, shapefold.assign([], history), {0: {"s.a": "shadow"}}, seals)


def test_history_truncation_cannot_discard_or_reassign_a_sealed_event():
    history = release_history()
    seals = MemorySeals()
    rows = [observation(id="1" * 32), observation(id="2" * 32, at="2026-01-02T01:00:00.000+00:00", release="r2")]
    stages = {event.index: {"s.a": "shadow"} for event in history}
    shapefold._durable(history, shapefold.assign(rows, history), stages, seals)
    before = deepcopy(seals.records)
    shortened = history[:1]
    with pytest.raises(ValueError, match="history|event|sealed"):
        shapefold._durable(shortened, shapefold.assign([], shortened), {}, seals)
    assert seals.records == before


def test_reordered_history_cannot_reuse_a_seal_for_another_release():
    history = release_history()
    seals = MemorySeals()
    stages = {event.index: {"s.a": "shadow"} for event in history}
    shapefold._durable(history, shapefold.assign([observation(id="1" * 32)], history), stages, seals)
    before = deepcopy(seals.records)
    reordered = shapefold.events([
        {"release": "r2", "model_hash": "same-hash", "released_at": "2026-01-02T00:00:00Z"},
        {"release": "r1", "model_hash": "same-hash", "released_at": "2026-01-01T00:00:00Z"},
    ])
    with pytest.raises(ValueError, match="history|event|sealed"):
        shapefold._durable(reordered, shapefold.assign([], reordered), {}, seals)
    assert seals.records == before


def test_graph_write_failure_keeps_seal_and_retry_does_not_double_count(tmp_path, monkeypatch):
    class Profile:
        client = "synthetic"
        artifacts = tmp_path

        def run_dir(self):
            return tmp_path / "run"

    class Log:
        def __init__(self):
            self.values = [observation(id="1" * 32)]

        def releases(self):
            return ["r1"]

        def read(self, release):
            assert release == "r1"
            return deepcopy(self.values)

    source = tmp_path / "versions.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in [
        {"release": "r1", "model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "model_hash": "h2", "released_at": "2026-01-02T00:00:00Z"},
    ]) + "\n")
    monkeypatch.setattr(shapefold, "versions_path", lambda _profile: source)
    monkeypatch.setattr(shapefold, "stages_of", lambda _profile, _hash: {"s.a": "shadow"})
    monkeypatch.setattr(shapefold, "_existing_statuses", lambda _conn: set())
    calls = []

    def write(_conn, rows):
        calls.append(deepcopy(rows))
        if len(calls) == 1:
            raise shapefold.GraphLocked("busy")
        return len(rows)

    monkeypatch.setattr(shapefold, "write", write)
    log, seals = Log(), MemorySeals()
    with pytest.raises(shapefold.NotFolded):
        shapefold.run(Profile(), log=log, conn=object(), seals=seals)
    assert seals.records
    report = shapefold.last_report(Profile())
    assert report["written"] is False
    assert report["skipped"] == "the graph is open in another process (the API): stop it before folding"
    assert shapefold.run(Profile(), log=log, conn=object(), seals=seals).written is True
    rows = by_id(calls[-1])
    event = shapefold.events(json.loads(line) for line in source.read_text().splitlines())[0]
    assert rows[("s.a", event.id)]["live"]["compared"] == 1
    log.values.clear()
    assert shapefold.run(Profile(), log=log, conn=object(), seals=seals).written is True
    assert by_id(calls[-1])[("s.a", event.id)]["live"]["compared"] == 1


def test_report_distinguishes_retained_observations_from_sealed_unattributed(tmp_path, monkeypatch):
    class Profile:
        client = "synthetic"
        artifacts = tmp_path

        def run_dir(self):
            return tmp_path / "run"

    class Log:
        def __init__(self):
            self.values = [observation(id="1" * 32, shapes=()), observation(id="2" * 32, release="r2")]

        def releases(self):
            return ["r1", "r2"]

        def read(self, release):
            return [deepcopy(row) for row in self.values if row["release"] == release]

    source = tmp_path / "versions.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in [
        {"release": "r1", "model_hash": "h1", "released_at": "2026-01-01T00:00:00Z"},
        {"release": "r2", "model_hash": "h2", "released_at": "2026-01-02T00:00:00Z"},
    ]) + "\n")
    monkeypatch.setattr(shapefold, "versions_path", lambda _profile: source)
    monkeypatch.setattr(shapefold, "stages_of", lambda _profile, _hash: {"s.a": "shadow"})
    monkeypatch.setattr(shapefold, "_existing_statuses", lambda _conn: set())
    monkeypatch.setattr(shapefold, "write", lambda _conn, rows: len(rows))
    log, seals = Log(), MemorySeals()
    first = shapefold.run(Profile(), log=log, conn=object(), seals=seals)
    assert (first.observations, first.release_mismatch, first.unattributed) == (2, 1, 1)
    assert "retained observation" in first.line()
    log.values.clear()
    after_pruning = shapefold.run(Profile(), log=log, conn=object(), seals=seals)
    assert (after_pruning.observations, after_pruning.release_mismatch, after_pruning.unattributed) == (0, 0, 1)
    assert "0 retained observation" in after_pruning.line()
