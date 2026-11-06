"""Asking: /ask, /answer, /agent, /context, /retrieve and /map/{term}, and the MCP tools; the engine that answers
(engine/). What the other services use of it is offered here: the engine, the presets and stages a run config takes,
the agent's arms and the steward's decisions."""
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.config import RunConfig
from kge.services.ask.engine.core import AGENT_ARMS, DECISIONS, PATHS, PRESETS, STAGES, Engine
from kge.services.ask.engine.question import FIGURE_FOLLOWUP, FIGURE_INTENT, catalogue_question, normalise_question, parse
from kge.services.ask.engine.runtrace import run as traced
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.wording import Hidden, worded


def ask(*args, **kwargs) -> dict:
    from kge.services.ask.service import ask as _ask
    return _ask(*args, **kwargs)


__all__ = ["AGENT_ARMS", "DECISIONS", "FIGURE_FOLLOWUP", "FIGURE_INTENT", "PATHS", "PRESETS", "STAGES", "Combined",
           "Engine", "Hidden", "ProductScope", "RunConfig", "ask", "catalogue_question", "normalise_question", "parse",
           "traced", "worded"]
