"""The run config (RunConfig): an arm and one value per stage (STAGES), the presets the A/B arms are (PRESETS) and the
reference agent's two configs (AGENT_ARMS)."""
from __future__ import annotations

from dataclasses import dataclass, replace


MODEL = "+model"
PROFILE_SET = "profile is no stage: the process sets it, chosen at deployment (PROFILE); no request names one"
STAGES = {
    "resolution": ("none", "words", "names", "glossary", "glossary" + MODEL),
    "context": ("schema", "graph"),
    "sql_source": ("planner", "planner" + MODEL, "model"),
    "citation_rule": ("retrieval", "shelves", "tiers"),
    "renderer": ("template", "template" + MODEL),
}
PATHS = ("answer", "ask", "agent")


@dataclass(frozen=True)
class RunConfig:
    """What each stage of a run may use (STAGES). A value ending in +model has the model take part in that stage; it
    needs a configured model and is never quietly dropped. resolution words maps no term: the planner guesses a column
    from the question's own words against the warehouse schema. sql_source model has the reference agent's model write
    every query, through the same gate as the planner's. citation_rule tiers retrieves only what upstream certification
    landed as AI-ready: gold is cited as the authority, silver with attribution, bronze listed and never cited, each
    for the period it was in force. renderer template+model has the model phrase the answer, and a number not in the
    result discards it."""
    arm: str
    resolution: str
    context: str
    sql_source: str
    citation_rule: str
    renderer: str
    profile: str = ""

    def stages(self) -> dict[str, str]:
        return {s: getattr(self, s) for s in STAGES}

    def with_stages(self, **override: str | None) -> RunConfig:
        """This config with some stages changed; a stage given as None keeps its value. A name that is no stage is a
        ValueError, the profile among them: the process sets it (PROFILE_SET)."""
        if "profile" in override:
            raise ValueError(PROFILE_SET)
        bad = [k for k in override if k not in STAGES]
        if bad:
            raise ValueError(f"{', '.join(bad)}: not a stage; the stages are {', '.join(STAGES)}")
        return replace(self, **{s: v for s, v in override.items() if v is not None})

    @property
    def label(self) -> str:
        """The preset's name, or arm[k=v,...] for each stage that differs from it, in stage order."""
        base = PRESETS.get(self.arm) or AGENT_ARMS.get(self.arm)
        changed = [f"{s}={v}" for s, v in self.stages().items() if base is None or getattr(base, s) != v]
        return f"{self.arm}[{','.join(changed)}]" if changed else self.arm

    @property
    def glossary(self) -> bool:
        return self.resolution in ("glossary", "glossary" + MODEL)

    @property
    def graph(self) -> bool:
        return self.context == "graph"

    def uses_model(self, stage: str) -> bool:
        value = getattr(self, stage)
        return value == "model" or value.endswith(MODEL)

    def model_stages(self) -> list[str]:
        return [s for s in STAGES if self.uses_model(s)]

    def problems(self, path: str, has_model: bool) -> list[str]:
        """Why this config cannot run on path (answer, ask, or agent: the reference agent,
        kge/services/ask/engine/agent.py); empty if it can. The agent's arms are not presets, and its rule-based
        fallback runs through Engine.answer as the baseline does."""
        if path not in PATHS:
            raise ValueError(f"path must be one of {', '.join(PATHS)}, not {path!r}")
        out = [f"{s} must be one of {', '.join(STAGES[s])}, not {v!r}" for s, v in self.stages().items() if v not in STAGES[s]]
        arms = AGENT_ARMS if path == "agent" else {**PRESETS, **AGENT_ARMS}
        if self.arm not in arms:
            out.append(f"arm must be one of {', '.join(arms)}, not {self.arm!r}")
        if out:
            return out
        model = ", ".join(f"{s}={getattr(self, s)}" for s in self.model_stages())
        if self.sql_source == "model" and path != "agent":
            out.append("sql_source=model is the reference agent's (kge/services/ask/engine/agent.py); /answer and /ask plan with the planner")
        elif model and path == "answer":
            out.append(f"/answer is the rule-based baseline and uses no model ({model}); /ask takes model stages")
        elif model and not has_model:
            out.append(f"{model} needs a model and no model is configured (MODEL_PROVIDER); nothing falls back to rules")
        if self.graph and not self.glossary:
            out.append(f"context=graph gates SQL on certified columns, which needs resolution=glossary, not {self.resolution}")
        if path == "ask":
            if not self.graph:
                out.append("/ask answers on the graph context only; use /answer for the control arms")
            if self.citation_rule != "shelves":
                out.append("/ask cites by shelf: citation_rule must be shelves")
        if path == "agent" and self.sql_source == "planner+model":
            out.append("the agent's model writes every query (sql_source=model) or none (planner)")
        return out


PRESETS = {
    "schema": RunConfig("schema", resolution="names", context="schema", sql_source="planner", citation_rule="retrieval", renderer="template"),
    "catalog": RunConfig("catalog", resolution="glossary", context="schema", sql_source="planner", citation_rule="retrieval", renderer="template"),
    "graph": RunConfig("graph", resolution="glossary", context="graph", sql_source="planner", citation_rule="shelves", renderer="template"),
}
ARMS = PRESETS
AGENT_ARMS = {
    "control": RunConfig("control", resolution="words", context="schema", sql_source="planner", citation_rule="retrieval", renderer="template"),
    "treatment": RunConfig("treatment", resolution="glossary", context="graph", sql_source="planner", citation_rule="tiers", renderer="template"),
}

