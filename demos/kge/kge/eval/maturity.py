"""Pure shape maturity from per-release evidence.

See question-shapes.md sections 4.3, 4.4, 5, 8.1 and 11 (Q2, Q6, Q10, Q11, Q14, Q15). The 99% agreement and
100-question volume thresholds reflect the wrong-figure cost ratio and volume floor; they are planning estimates until
measured on the pilot, and changing them is outside this slice. No request path reads these verdicts.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import ClassVar

from kge.compiler.standards import STAGES
from kge.ports import SHADOW_COMPARISONS, SHADOW_GRAPH_WRONG, SHADOW_OUTCOMES, SHADOW_REVIEW_OUTCOMES
from kge.services.ask.engine.shapestage import (
    REVIEW_EVENTS, Declarations, Evidence, ShapeDeclaration, ShapeEvidence, Suspension, lifted,
)

M3_AGREEMENT = (99, 100)
M5_VOLUME = 100
M7_EVENTS = MappingProxyType({"graph_first": 1, "retired": 2})
METRICS = ("tests", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "Q2", "Q15")
BUCKETS = ("live", "new_phrase", "via_terms")
REVIEW_COUNTER_KEYS = ("queued", "reviewed", "open", *SHADOW_REVIEW_OUTCOMES, "graph_wrong")


def review_counters(data: Mapping[str, int]) -> Mapping[str, int]:
    """Read review counts from the fold, treating counters not yet written by the review slice as zero."""
    counters = {key: data.get(key, 0) for key in REVIEW_COUNTER_KEYS}
    if not all(_count(value) for value in counters.values()):
        raise ValueError("review counts must be non-negative integers")
    return MappingProxyType(counters)


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class Event:
    """One release history event, with a distinct id even for a repeated release."""
    id: str
    release: str
    rollback: bool


@dataclass(frozen=True)
class Tests:
    """The build's scores for a shape; compared excludes non-baseline phrasings."""
    passed: int
    total: int
    wrong: int
    agreed: int
    compared: int
    examples_replayed: bool
    drafts: int = 0
    items: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not all(_count(n) for n in (self.passed, self.total, self.wrong, self.agreed, self.compared, self.drafts)):
            raise ValueError("test counts must be non-negative integers")
        if self.passed > self.total or self.wrong > self.total or self.agreed > self.compared or self.compared > self.total:
            raise ValueError("test counts exceed their totals")
        object.__setattr__(self, "items", frozenset(self.items))


@dataclass(frozen=True)
class Live:
    """A single disjoint bucket's counters; the live bucket excludes new phrases and via-terms observations."""
    compared: int = 0
    agreed: int = 0
    plans_differ: int = 0
    propose_decline: int = 0
    declines_differ: int = 0
    graph_declines: int = 0
    coded_declines: int = 0
    fallbacks: int = 0
    new_phrase: int = 0
    takeovers: int = 0
    KEYS: ClassVar[tuple[str, ...]]
    ZERO: ClassVar[Live]

    def __post_init__(self) -> None:
        if not all(_count(getattr(self, f.name)) for f in fields(self)):
            raise ValueError("live counts must be non-negative integers")
        if self.agreed + self.disagreements != self.compared:
            raise ValueError("comparison counts do not add up")
        if max(self.graph_declines, self.coded_declines, self.fallbacks, self.takeovers) > self.compared:
            raise ValueError("live count exceeds compared")

    @classmethod
    def of(cls, data: Mapping[str, int]) -> Live:
        """Read a complete counter mapping and reject malformed counters."""
        if set(data) != set(cls.KEYS):
            raise ValueError("counter keys do not match")
        return cls(**data)

    @property
    def disagreements(self) -> int:
        """The observations in the three disagreement categories."""
        return self.plans_differ + self.propose_decline + self.declines_differ


Live.KEYS = tuple(f.name for f in fields(Live))
Live.ZERO = Live()


def tally(observations: Iterable[Mapping]) -> dict[str, Live]:
    """Count observations in disjoint live, new-phrase and via-terms buckets, without mutating input."""
    counts = {bucket: {key: 0 for key in Live.KEYS} for bucket in BUCKETS}
    for row in observations:
        bucket = "new_phrase" if row.get("new_phrase", False) else "via_terms" if row.get("via_terms", False) else "live"
        comparison = row.get("comparison")
        if comparison not in SHADOW_COMPARISONS:
            raise ValueError("unknown comparison")
        graph, coded = row.get("graph_outcome"), row.get("coded_outcome")
        if graph not in SHADOW_OUTCOMES or coded not in SHADOW_OUTCOMES:
            raise ValueError("unknown planner outcome")
        if any(type(row.get(flag, False)) is not bool for flag in ("new_phrase", "via_terms", "fallback", "takeover")):
            raise ValueError("observation flags must be booleans")
        if graph == "propose" and coded == "propose":
            valid = comparison in ("agree", "plans_differ")
        elif (graph == "propose") != (coded == "propose"):
            valid = comparison == "propose_decline"
        else:
            valid = comparison == ("agree" if graph == coded else "declines_differ")
        if not valid or (row.get("takeover", False) and not (graph == "propose" and coded != "propose")):
            raise ValueError("comparison and outcomes disagree")
        current = counts[bucket]
        current["compared"] += 1
        current["agreed" if comparison == "agree" else comparison] += 1
        current["graph_declines"] += row.get("graph_outcome") != "propose"
        current["coded_declines"] += row.get("coded_outcome") != "propose"
        current["fallbacks"] += bool(row.get("fallback", False))
        current["takeovers"] += bool(row.get("takeover", False))
    return {bucket: Live.of(values) for bucket, values in counts.items()}


def total(buckets: Mapping[str, Live]) -> Live:
    """Combine disjoint buckets for the born-shape and review denominators."""
    if set(buckets) != set(BUCKETS):
        raise ValueError("all observation buckets are required")
    return Live.of({key: sum(getattr(bucket, key) for bucket in buckets.values()) for key in Live.KEYS})


@dataclass(frozen=True)
class Review:
    """A review-required disagreement or takeover and its steward's outcome and reproduction."""
    observation: str
    event: str
    shapes: tuple[str, ...]
    takeover: bool
    outcome: str | None
    item: str | None

    def __post_init__(self) -> None:
        if self.outcome is not None and self.outcome not in SHADOW_REVIEW_OUTCOMES:
            raise ValueError("unknown review outcome")
        object.__setattr__(self, "shapes", tuple(self.shapes))


@dataclass(frozen=True)
class ShapeEvent:
    """A shape's declared stage, test result and live counters for one release event.

    total_live includes all disjoint buckets for review counting; mirrors use live alone for M3, M5 and M6.
    """
    event: Event
    stage: str
    tests: Tests | None
    live: Live
    total_live: Live | None = None
    reviews: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError("unknown stage")
        if self.total_live is None:
            raise ValueError("total live counters across all buckets are required")
        if any(getattr(self.total_live, key) < getattr(self.live, key) for key in Live.KEYS):
            raise ValueError("total live counters cannot be smaller than the live bucket")
        object.__setattr__(self, "reviews", review_counters(self.reviews or {}))


@dataclass(frozen=True)
class Blocker:
    """One named metric that blocks a shape or coupled group."""
    shape: str
    metric: str
    detail: str


@dataclass(frozen=True)
class EventResult:
    """One event's failures, qualification, progress and stage."""
    event: str
    stage: str
    number: int
    qualifies: bool
    pending: bool
    failures: tuple[Blocker, ...]


@dataclass(frozen=True)
class ShapeMaturity:
    """One shape's event verdicts, consecutive run and takeover readiness."""
    shape: str
    born: bool
    declared: str
    events: tuple[EventResult, ...]
    held: int
    event_number: int
    enough: bool
    suspensions: tuple[Suspension, ...]
    suspended: bool
    takeover_ready: bool
    untested_reviews: tuple[str, ...]
    tests: Tests | None


@dataclass(frozen=True)
class Readiness:
    """The next coupled-group stage and the blockers preventing it."""
    group: str
    declared: str
    next: str | None
    ready: bool
    blockers: tuple[Blocker, ...]


def _sort(blockers: Iterable[Blocker]) -> tuple[Blocker, ...]:
    return tuple(sorted(blockers, key=lambda b: (METRICS.index(b.metric), b.detail)))


def _shape_evidence(event: str, t: Tests, number: int) -> ShapeEvidence:
    return ShapeEvidence(event, t.passed, t.total, t.wrong, t.agreed, t.examples_replayed, t.items, number)


def assess(declaration: ShapeDeclaration, history: Sequence[ShapeEvent], reviews: Iterable[Review]) -> ShapeMaturity:
    """Assess each release event, retaining a still-accruing last event without breaking an earlier run."""
    if not history or len({e.event.id for e in history}) != len(history):
        raise ValueError("history must contain distinct events")
    relevant = tuple(r for r in reviews if declaration.id in r.shapes)
    if len({r.observation for r in relevant}) != len(relevant):
        raise ValueError("reviews must have unique observation ids")
    if any(r.event not in {e.event.id for e in history} for r in relevant):
        raise ValueError("review has no matching event")
    sid, born = declaration.id, declaration.born
    results: list[EventResult] = []
    number = 0
    for index, row in enumerate(history):
        if row.stage != "specify":
            number += 1
        event_number = number if row.stage != "specify" else 0
        records = [r for r in relevant if r.event == row.event.id]
        count_live = row.total_live
        due = max(len(records), count_live.disagreements, count_live.takeovers, row.reviews["queued"])
        reviewed = sum(r.outcome not in (None, "needs_test") for r in records)
        t, live = row.tests, row.live
        failures: list[Blocker] = []
        def fail(metric: str, detail: str) -> None:
            failures.append(Blocker(sid, metric, detail))
        if born:
            if t is None or not t.total:
                fail("tests", "no test run in this release event")
            elif t.passed != t.total:
                fail("tests", f"{t.passed} of {t.total} tests pass; all needed")
            if 0 < event_number <= REVIEW_EVENTS and reviewed < due:
                fail("Q2", f"{reviewed} of {due} live answers reviewed")
        else:
            if t is None or not t.compared:
                fail("M1", "no test run in this release event")
            elif t.agreed != t.compared:
                fail("M1", f"{t.agreed} of {t.compared} tests agree; 100% needed")
            if not live.compared or live.agreed * 100 < 99 * live.compared:
                fail("M3", f"{live.agreed} of {live.compared} live questions agree; 99% needed")
            if reviewed < due:
                fail("M4", f"{reviewed} of {due} disagreements reviewed")
            if live.compared < M5_VOLUME:
                fail("M5", f"not enough evidence: {live.compared} live questions; {M5_VOLUME} needed")
            if live.graph_declines > live.coded_declines:
                fail("M6", f"{live.graph_declines} graph declines exceed {live.coded_declines} coded declines")
        if t is not None and t.wrong:
            fail("M2", f"{t.wrong} wrong figures; 0 needed")
        if row.event.rollback:
            fail("M7", "a rollback breaks the run")
        if row.reviews["graph_wrong"] or any(r.outcome in SHADOW_GRAPH_WRONG for r in records):
            fail("M7", "a disagreement where the graph was wrong resets the run")
        ordered = _sort(failures)
        live_metrics = {"Q2"} if born else {"M3", "M4", "M5", "M6"}
        pending = index == len(history) - 1 and bool(ordered) and all(b.metric in live_metrics for b in ordered)
        results.append(EventResult(row.event.id, row.stage, event_number, not ordered, pending, ordered))
    held = 0
    for result in reversed(results[:-1] if results[-1].pending else results):
        if not result.qualifies:
            break
        held += 1
    last = history[-1]
    last_tests = last.tests
    ss = tuple(Suspension(sid, r.observation, None if r.event == last.event.id else r.item)
               for r in relevant if born and r.takeover and r.outcome in SHADOW_GRAPH_WRONG)
    evidence = Evidence(last.event.id, {sid: _shape_evidence(last.event.id, last_tests, results[-1].number)} if last_tests else {}, ss)
    suspended = any(not lifted(s, evidence) for s in ss)
    untested = tuple(sorted(r.observation for r in relevant if r.outcome not in (None, "needs_test") and
                            (last_tests is None or r.item is None or r.item not in last_tests.items)))
    takeover = bool(last_tests and last_tests.total and not last_tests.wrong and
                    ((last_tests.passed == last_tests.total and not suspended) if born else
                     (last_tests.agreed == last_tests.total and last_tests.examples_replayed)))
    effective = history[-2] if results[-1].pending and len(history) > 1 else last
    return ShapeMaturity(sid, born, declaration.stage, tuple(results), held, results[-1].number,
                         effective.live.compared >= M5_VOLUME, ss, suspended, takeover, untested, last_tests)


def meets(m: ShapeMaturity, stage: str) -> tuple[Blocker, ...]:
    """Return the blockers to increasing a shape's stage; holding or lowering needs no evidence."""
    if stage not in STAGES:
        raise ValueError("unknown stage")
    if STAGES.index(stage) <= STAGES.index(m.declared):
        return ()
    blockers: list[Blocker] = []
    if stage == "shadow":
        if m.tests is None or not m.tests.total or m.tests.drafts:
            blockers.append(Blocker(m.shape, "tests", "approved tests needed"))
    elif m.born:
        blockers.extend(b for b in m.events[-1].failures if b.metric in {"tests", "M2"})
        blockers.extend(b for event in m.events if 0 < event.number <= REVIEW_EVENTS for b in event.failures if b.metric == "Q2")
        if m.suspended:
            blockers.append(Blocker(m.shape, "Q15", "takeovers suspended after a wrong takeover"))
        if stage == "retired" and m.event_number < REVIEW_EVENTS:
            blockers.append(Blocker(m.shape, "Q2", "in its first two release events"))
    else:
        needed = M7_EVENTS[stage]
        if m.held < needed:
            if m.held == 0:
                blocker_event = m.events[-1]
                if blocker_event.pending and len(m.events) > 1:
                    blocker_event = m.events[-2]
                if blocker_event.qualifies:
                    blocker_event = next((e for e in reversed(m.events) if not e.qualifies), blocker_event)
                blockers.extend(blocker_event.failures)
            if not blockers or m.held:
                blockers.append(Blocker(m.shape, "M7", f"{m.held} of {needed} consecutive qualifying release events"))
    if STAGES.index(stage) > STAGES.index("shadow"):
        blockers.extend(Blocker(m.shape, "M4", f"reviewed disagreement {obs} is not yet a test") for obs in m.untested_reviews)
    return _sort(blockers)


def readiness(declarations: Declarations, group: str, maturities: Mapping[str, ShapeMaturity], *, signed_off: bool = False) -> Readiness:
    """Evaluate the next stage for every member of a coupled group."""
    members = sorted(s.id for s in declarations.shapes.values() if s.group == group)
    if not members or any(s not in maturities for s in members):
        raise ValueError("group has missing declarations or maturity")
    declared = declarations.group_stage(group)
    all_born = all(declarations.shapes[s].born for s in members)
    target = {"specify": "graph_first" if all_born else "shadow", "shadow": "graph_first",
              "graph_first": None if all_born else "retired", "retired": None}[declared]
    blockers = tuple(b for s in members for b in meets(maturities[s], target)) if target else ()
    if target == "retired" and not signed_off:
        blockers += (Blocker(group, "M8", "owner sign-off, recorded in the retirement change"),)
    return Readiness(group, declared, target, target is not None and not blockers, blockers)


def suspensions(reviews: Iterable[Review], declarations: Declarations) -> tuple[Suspension, ...]:
    """Collect graph-wrong takeover suspensions for declared born shapes."""
    found = [Suspension(shape, r.observation, r.item) for r in reviews if r.takeover and r.outcome in SHADOW_GRAPH_WRONG
             for shape in r.shapes if shape in declarations.shapes and declarations.shapes[shape].born]
    return tuple(sorted(found, key=lambda s: (s.shape, s.observation)))


def selector_evidence(maturities: Mapping[str, ShapeMaturity], histories: Mapping[str, Sequence[ShapeEvent]],
                      reviews: Iterable[Review], declarations: Declarations) -> Evidence:
    """Build selector evidence without wiring it into serving; this slice changes no answers."""
    if not histories or any(not h for h in histories.values()):
        raise ValueError("histories must not be empty")
    events = {h[-1].event.id for h in histories.values()}
    if len(events) != 1:
        raise ValueError("histories end at different events")
    event = next(iter(events))
    shapes = {s: _shape_evidence(event, h[-1].tests, maturities[s].event_number)
              for s, h in histories.items() if h[-1].tests is not None}
    current_reviews = tuple(reviews)
    current = {r.observation for r in current_reviews if r.event == event}
    holds = tuple(Suspension(s.shape, s.observation, None if s.observation in current else s.item)
                  for s in suspensions(current_reviews, declarations))
    return Evidence(event, shapes, holds)
