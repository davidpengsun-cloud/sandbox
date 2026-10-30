"""The model and embeddings adapters: the language model (providers.py, a ModelProvider of kge/ports.py), its offline
stand-in (mock.py), the embedders (embed.py, each an Embeddings) and the one-line error a failed outside call raises
(errors.py). The factories choose the backend from the settings: make_llm() and model_for(agent) from MODEL_PROVIDER and
the tiers, make_embedder() from EMBEDDING_PROVIDER.

Callers import what they use from this package, never from a backend's module. Each name is read from its module when
first used, so a caller that needs only DIM (the graph's schema) loads neither the model client nor the mock."""
import importlib

OFFERED = {
    "providers": {"LLM", "MOCK", "PROVIDERS", "PREFERRED_TIER", "TIERS", "ModelSettingError", "NotJSON", "Refused",
                  "check_settings", "make_llm", "model_for", "tagger_model"},
    "mock": {"ChatMock", "IMPROVE_MODES", "IMPROVE_TASK", "MISS_CLOSE", "MISS_OPEN", "MODES"},
    "embed": {"DIM", "HashEmbedder", "LocalEmbedder", "VertexEmbedder", "make_embedder", "vectors_in_bigquery"},
    "budget": {"request_budget"},
    "errors": {"ExternalCallError", "NOT_JSON", "NoFallback", "no_fallback", "post_json", "service_of", "transient"},
}
HOME = {name: module for module, names in OFFERED.items() for name in names}


def __getattr__(name: str):
    if name in HOME:
        return getattr(importlib.import_module(f"{__name__}.{HOME[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
