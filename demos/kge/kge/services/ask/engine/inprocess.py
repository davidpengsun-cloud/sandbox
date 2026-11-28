"""InProcessTools: the KgeTools port (kge/ports.py) in the reference agent's own process, what an Agent asked with no
tools reaches KGE through: eval, quality, batch and golden runs, make agent and the A/B harness.

get_context is POST /context's chain without the ASGI hop: the package the agent's context workflow builds as the
principal the tools were opened for, with its products, history and plan vocabulary only where the plan vocabulary is
asked for (as service.context passes them), as_json, client_result, and the JSON the wire carries. ask is the treatment
arm's plan as the agent made it before the port: on rules, Engine.ruled; with a model, the coordinator on the agent's own
model (Combined.governed) held to the plan vocabulary the last get_context gave, as the agent's context step fetched it;
both on the turn's run config, and its answer as the coordinator gives it, with no client view. Pipeline events already
reach the request's stream in this process, so on_event is not called."""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from kge.services.ask.engine.context import as_json
from kge.services.ask.engine.planpublic import client_result

TREATMENT = "treatment"


class InProcessTools:
    """The KGE tools for one turn of agent, as principal, on cfg (the turn's stamped run config; None for the treatment
    arm's)."""

    def __init__(self, agent, principal: str, cfg=None):
        self.agent = agent
        self.principal = principal
        self.cfg = cfg
        self.plan_vocabulary: dict | None = None

    def get_context(self, question: str, *, purpose: str, arm: str, products: list[str] | None = None,
                    plan_vocabulary: bool = False, history: Iterable[Any] = (), runtimes: list[str] | None = None) -> dict:
        """The package as POST /context gives it, as JSON."""
        if plan_vocabulary:
            pkg = self.agent.context(question, self.principal, purpose, arm, products=products, plan_vocabulary=True,
                                     history=history, runtimes=runtimes)
        else:
            pkg = self.agent.context(question, self.principal, purpose, arm, runtimes=runtimes)
        wire = json.loads(json.dumps(client_result(as_json(pkg))))
        self.plan_vocabulary = wire.get("plan_vocabulary")
        return wire

    def ask(self, question: str, *, purpose: str, arm: str, stages: Any = None, products: list[str] | None = None,
            history: Iterable[Any] = (), runtimes: list[str] | None = None,
            on_event: Callable[[dict], None] | None = None) -> dict:
        """The treatment arm's plan, as the agent's own coordinator or rules make it."""
        agent = self.agent
        cfg = self.cfg or agent.e.stamped(agent.config(TREATMENT))
        if agent.llm is None:
            return agent.e.ruled(question, self.principal, purpose, config=cfg, products=products, history=history,
                                 runtimes=runtimes)
        from kge.services.ask.engine.combined import Combined
        combined = Combined(agent.e, llm=agent.llm)
        combined.plan_vocabulary = self.plan_vocabulary
        return combined.governed(question, self.principal, purpose, config=cfg, products=products, history=history,
                                 runtimes=runtimes)
