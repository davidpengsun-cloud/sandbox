"""The rules-planner port (docs/use-cases/kge/platform/question-shapes.md §4.3, #450 requirement 1).

Public Ask asks one port for the rules reading of a question and never branches on which adapter gives it. An adapter
takes a `RulesQuestion` and returns a `Propose`, a `Clarify` or an `Abstain` (`kge.semantics.plan.PlanReply`), and
nothing else: `planned` refuses any other reply, so a new adapter cannot hand the caller a shape it does not know.
The reply then goes where the coded reading always went: the plan checks, the compiler and the SQL gate, through the
`Planner` (`planner.py`); an adapter calls neither the model nor the warehouse.

Adapters:

| Adapter | Where | Reads |
|---|---|---|
| `coded` | `planrules.Coded` | `planrules.followup`, else `planrules.read` on the vocabulary a clarification's choice narrows: the coded shapes in `planshapes` |

The coded adapter is the only one, so `rules_planner` gives it on every question and every served answer is the coded
one. The graph adapter (#518) is the second adapter of this port; the stage selector (`shapestage.select`)
then picks which reply is served.

Neither the port nor its adapters live in `kge/ports.py` or `kge/adapters/`: the coded adapter, and the graph one to
come, are CPU-only planning code in the `core` layer, reaching no backend, and the port names the plan types of
`kge.semantics.plan`, which `kge/ports.py`, the lowest layer, cannot import.

`RulesQuestion.engine` hands an adapter the whole engine, which the coded adapter reads its release through. That an
adapter asks neither the model nor the warehouse is its own duty, not one the port enforces: the graph adapter's
tests should hold it to that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from kge.semantics.plan import Abstain, Clarify, HistoryEntry, PlanReply, Propose
from kge.semantics.planvocabulary import Vocabulary
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import Request

REPLIES = (Propose, Clarify, Abstain)


@dataclass(frozen=True)
class RulesQuestion:
    """What a rules adapter reads: the engine whose release it plans against, the parsed request, the words as the
    caller asks them (`question`, which a clarification's choice is matched against), the release's plan vocabulary
    for this principal and scope, the run config, the turn id, the conversation so far and whether the request reads a
    cohort."""
    engine: Any
    request: Request
    question: str
    vocabulary: Vocabulary
    cfg: Any
    turn: str
    history: tuple[HistoryEntry, ...] = ()
    cohort: bool = False


@runtime_checkable
class RulesPlanner(Protocol):
    """One rules adapter: `name` says which, for a trace or an observation; `plan` reads one question, CPU only."""
    name: str

    def plan(self, question: RulesQuestion) -> PlanReply: ...


def planned(planner: RulesPlanner, question: RulesQuestion) -> PlanReply:
    """The adapter's reply to one question, refused with `TypeError` unless it is a `Propose`, `Clarify` or `Abstain`."""
    reply = planner.plan(question)
    if not isinstance(reply, REPLIES):
        raise TypeError(f'The {planner.name} rules planner replied {type(reply).__name__}, not a plan reply.')
    return reply


def rules_planner() -> RulesPlanner:
    """The adapter that plans the rules path: the coded one, the only adapter until #518 adds the graph one."""
    return planrules.CODED
