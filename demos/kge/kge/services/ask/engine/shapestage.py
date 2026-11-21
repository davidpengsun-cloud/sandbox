"""The stage selector: which whole plan the rules path serves, the coded planner's or the graph's, at the stage of the
coupled group of the shapes the question used (docs/use-cases/kge/platform/question-shapes.md, §4.3 and decisions Q2,
Q5, Q14 and Q15 in §11).

`select` is pure. It reads both planners' replies, the shapes each used, the clarification rules the graph side found
firing, the shapes' declarations (`Declarations`, built once per release from the graph's `Shape` rows) and the release
event's evidence (`Evidence`, built once per release by the fold), and picks one reply, never a copy and never a
synthesised one:

| Group's stage | Served |
|---|---|
| none (no declared shape used) | coded |
| `specify` | coded, or its decline |
| `shadow` | coded, except a decline takeover: graph, when `takeover_holds` gives nothing |
| `graph_first` | graph when it proposes and coded agrees or declines; otherwise coded, the fallback marked (Q5): `disagree` when both propose different plans, `graph_declined` when graph declines or did not run |
| `retired` | graph, whatever its reply; coded, marked `graph_not_run`, only when the graph side did not run |

A question's stage is the lowest, in `STAGES` order, over the groups of the shapes it used, and a group's stage the
lowest over its shapes, so input that mixes groups or stages is served at its most cautious stage (Q14), and a question
whose shapes span several groups never takes over. A shape id with no declaration counts as one at `specify`, so a
question that used one beside declared shapes serves coded at every stage; with no declared shape used, there is no
stage.

Shapes from several groups that are all at `graph_first` or above are served at that lowest stage, with the group
None; S33 makes such input impossible.

The decline takeover at `shadow` (Q15) needs all four conditions. `takeover_holds` names each unmet one in this fixed
order, and `Selection.held` carries them when a question is served at `shadow`: `coded_proposed` (1: coded declined);
`graph_not_run` or `graph_declined`; `clarification` (4: no rule fires); `unknown_shape:<id>` (from `takeover_holds`
alone, since an unknown id makes the stage `specify`) and `mixed_groups`; `no_born_shape` (2: a born-in-graph shape used); for each born shape
`no_evidence:<id>`, `born_tests:<id>` (every test passed, more than none) and `born_wrong:<id>` (0 wrong figures), in
this release event (2); for each mirror `no_evidence:<id>`, `mirror_agreement:<id>` (M1 = 100%), `mirror_wrong:<id>`
(M2 = 0) and `mirror_examples:<id>` (V06) (3); then `suspended:<id>` for a shape whose takeovers a graph-wrong review
suspended, until a release event whose tests include that question (`lifted`).

`review` marks a graph-served answer while some born-in-graph shape it used is in its first `REVIEW_EVENTS` release
events, or has no evidence in this one (Q2). A coded-served answer is never marked here.

With `Evidence.NONE` no takeover is ever possible, so `specify` and `shadow` always serve coded; and with no declared
shape used, every stage serves coded. Evidence from another release event counts as none."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Literal

from kge.compiler.standards import STAGES
from kge.semantics.plan import PlanReply, Propose, QueryPlan

Side = Literal["coded", "graph"]
Fallback = Literal["graph_declined", "disagree", "graph_not_run"]
REVIEW_EVENTS = 2
RANK = {stage: n for n, stage in enumerate(STAGES)}


@dataclass(frozen=True)
class ShapeDeclaration:
    """One declared shape: its id, its stage, its coupled group (the group's first id) and whether it was born in the
    graph (it `replaces` no coded builder) rather than a mirror of one."""
    id: str
    stage: str
    group: str
    born: bool


@dataclass(frozen=True)
class Declarations:
    """Every declared shape by id, read from the graph's `Shape` rows (`Declarations.of`)."""
    shapes: Mapping[str, ShapeDeclaration]
    EMPTY: ClassVar[Declarations]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shapes", MappingProxyType(dict(self.shapes)))

    @classmethod
    def of(cls, rows: Iterable[Mapping]) -> Declarations:
        """The declarations from `Shape` rows, as `emit.shape_rows` writes them: `id`, `stage`, `replaces` and
        `coupled_group`, other keys ignored. A stage outside `STAGES`, a repeated id or a row without its group is a
        build fault (S33 checks the pack), so it raises `ValueError`."""
        shapes: dict[str, ShapeDeclaration] = {}
        for row in rows:
            sid, stage, group = row["id"], row["stage"], row.get("coupled_group")
            if stage not in RANK:
                raise ValueError(f"shape {sid!r} has stage {stage!r}, not one of {', '.join(STAGES)}")
            if sid in shapes:
                raise ValueError(f"shape {sid!r} is declared twice")
            if not group:
                raise ValueError(f"shape {sid!r} has no coupled_group")
            shapes[sid] = ShapeDeclaration(sid, stage, group, row.get("replaces") is None)
        return cls(shapes)

    def group_stage(self, group: str) -> str:
        """The group's stage: the lowest, in `STAGES` order, over its shapes, so a group whose shapes disagree (before
        V07 refuses that) is served at its most cautious stage. A group with no shape raises `KeyError`."""
        ranks = [RANK[s.stage] for s in self.shapes.values() if s.group == group]
        if not ranks:
            raise KeyError(group)
        return STAGES[min(ranks)]


Declarations.EMPTY = Declarations({})


@dataclass(frozen=True)
class ShapeEvidence:
    """One shape's results in one release event: its tests passed of all, its wrong figures against approved
    `reference_sql` (M2), how many of its tests the graph's plan agreed with coded's (M1's numerator, read for mirrors),
    whether its worked examples replayed (V06), the test item ids that ran, and the event's 1-based number among the
    release events in which the shape stood above `specify`."""
    release_event: str
    tests_passed: int
    tests_total: int
    wrong_figures: int
    agreed: int
    examples_replayed: bool
    items: frozenset[str]
    event_number: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", frozenset(self.items))


@dataclass(frozen=True)
class Suspension:
    """A graph-wrong review of takeover `observation` suspends `shape`'s takeovers until a release event whose tests
    include `item`, the test that reproduces the question; with no item yet, it never lifts."""
    shape: str
    observation: str
    item: str | None


@dataclass(frozen=True)
class Evidence:
    """The release event in force, each shape's evidence and the suspensions. `Evidence.NONE` has none of them."""
    release_event: str | None
    shapes: Mapping[str, ShapeEvidence]
    suspensions: tuple[Suspension, ...]
    NONE: ClassVar[Evidence]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shapes", MappingProxyType(dict(self.shapes)))
        object.__setattr__(self, "suspensions", tuple(self.suspensions))

    def current(self, shape: str) -> ShapeEvidence | None:
        """The shape's evidence when it is from the release event in force, else None."""
        found = self.shapes.get(shape)
        if found is None or self.release_event is None or found.release_event != self.release_event:
            return None
        return found


Evidence.NONE = Evidence(None, {}, ())


@dataclass(frozen=True)
class Selection:
    """The served side and its reply (one of the inputs, itself); the stage and group it was chosen at (None when no
    declared shape was used, and the group None too when several were); the fallback marked (`graph_first`, or
    `retired` with no graph reply); whether it was a decline takeover; whether it is marked for review; and, at
    `shadow` without a takeover, every unmet takeover condition."""
    served: Side
    reply: PlanReply
    stage: str | None
    group: str | None
    fallback: Fallback | None
    takeover: bool
    review: bool
    held: tuple[str, ...]


def lifted(suspension: Suspension, evidence: Evidence) -> bool:
    """Whether a release event whose tests include the suspension's question is in force."""
    if suspension.item is None:
        return False
    current = evidence.current(suspension.shape)
    return current is not None and suspension.item in current.items


def _ordered(ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))


def _scope(used: Sequence[str], declarations: Declarations) -> tuple[str | None, str | None, tuple[str, ...]]:
    """The stage and group of the shapes used, and the holds their declarations give: each unknown id, and several
    groups. An unknown id beside declared ones counts as a shape at `specify`, so it is never served from the graph."""
    holds = [f"unknown_shape:{s}" for s in used if s not in declarations.shapes]
    groups = sorted({declarations.shapes[s].group for s in used if s in declarations.shapes})
    if not groups:
        return None, None, tuple(holds)
    ranks = [RANK[declarations.group_stage(g)] for g in groups]
    stage = STAGES[min(ranks + [RANK["specify"]] * bool(holds))]
    if len(groups) > 1:
        holds.append("mixed_groups")
        return stage, None, tuple(holds)
    return stage, groups[0], tuple(holds)


def _born(graph_shapes: Sequence[str], declarations: Declarations) -> tuple[str, ...]:
    return tuple(s for s in _ordered(graph_shapes) if s in declarations.shapes and declarations.shapes[s].born)


def _mirrors(graph_shapes: Sequence[str], declarations: Declarations) -> tuple[str, ...]:
    return tuple(s for s in _ordered(graph_shapes) if s in declarations.shapes and not declarations.shapes[s].born)


def takeover_holds(coded: PlanReply, graph: PlanReply | None, *, graph_shapes: Sequence[str], coded_shapes: Sequence[str],
                   rules_fired: Sequence[str], declarations: Declarations, evidence: Evidence) -> tuple[str, ...]:
    """Every unmet condition of the decline takeover (Q15), in the fixed order the module states; empty when the
    graph's plan may be served at `shadow`. It does not check the stage."""
    held: list[str] = []
    if isinstance(coded, Propose):
        held.append("coded_proposed")
    if graph is None:
        held.append("graph_not_run")
    elif not isinstance(graph, Propose):
        held.append("graph_declined")
    if rules_fired:
        held.append("clarification")
    held.extend(_scope(_ordered((*graph_shapes, *coded_shapes)), declarations)[2])
    born, mirrors = _born(graph_shapes, declarations), _mirrors(graph_shapes, declarations)
    if not born:
        held.append("no_born_shape")
    for shape in born:
        current = evidence.current(shape)
        if current is None:
            held.append(f"no_evidence:{shape}")
            continue
        if not (current.tests_total > 0 and current.tests_passed == current.tests_total):
            held.append(f"born_tests:{shape}")
        if current.wrong_figures != 0:
            held.append(f"born_wrong:{shape}")
    for shape in mirrors:
        current = evidence.current(shape)
        if current is None:
            held.append(f"no_evidence:{shape}")
            continue
        if not (current.tests_total > 0 and current.agreed == current.tests_total):
            held.append(f"mirror_agreement:{shape}")
        if current.wrong_figures != 0:
            held.append(f"mirror_wrong:{shape}")
        if not current.examples_replayed:
            held.append(f"mirror_examples:{shape}")
    for shape in _ordered(graph_shapes):
        if any(s.shape == shape and not lifted(s, evidence) for s in evidence.suspensions):
            held.append(f"suspended:{shape}")
    return tuple(held)


def _review(graph_shapes: Sequence[str], declarations: Declarations, evidence: Evidence) -> bool:
    """Rule R: a graph-served answer is reviewed while a born-in-graph shape it used has no evidence in this release
    event or is in its first `REVIEW_EVENTS` (Q2)."""
    for shape in _born(graph_shapes, declarations):
        current = evidence.current(shape)
        if current is None or current.event_number <= REVIEW_EVENTS:
            return True
    return False


def select(coded: PlanReply, graph: PlanReply | None, *, graph_shapes: Sequence[str], coded_shapes: Sequence[str],
           rules_fired: Sequence[str], declarations: Declarations, evidence: Evidence,
           agree: Callable[[QueryPlan, QueryPlan], bool]) -> Selection:
    """The whole plan to serve, as the module states. `graph` is None when the graph side did not run. `agree` is the
    caller's normalised-plan comparison, called at most once, at `graph_first` when both planners propose."""
    stage, group, _ = _scope(_ordered((*graph_shapes, *coded_shapes)), declarations)

    def from_coded(fallback: Fallback | None = None, held: tuple[str, ...] = ()) -> Selection:
        return Selection("coded", coded, stage, group, fallback, False, False, held)

    def from_graph(reply: PlanReply, takeover: bool = False) -> Selection:
        return Selection("graph", reply, stage, group, None, takeover, _review(graph_shapes, declarations, evidence), ())

    if stage is None or stage == "specify":
        return from_coded()
    if stage == "retired":
        return from_coded("graph_not_run") if graph is None else from_graph(graph)
    if stage == "graph_first":
        if isinstance(graph, Propose):
            if not isinstance(coded, Propose) or agree(coded.plan, graph.plan):
                return from_graph(graph)
            return from_coded("disagree")
        return from_coded("graph_declined")
    held = takeover_holds(coded, graph, graph_shapes=graph_shapes, coded_shapes=coded_shapes, rules_fired=rules_fired,
                          declarations=declarations, evidence=evidence)
    if held or graph is None:
        return from_coded(held=held)
    return from_graph(graph, takeover=True)
