"""Each of Ask's structured calls sends a schema Vertex AI can take (#561), checked offline.

    .venv/bin/python -m pytest tests/unit/test_vertex_schema_bounds.py -q

Vertex AI inlines every $ref of a responseJsonSchema before it counts a request's input tokens and builds its decoding
grammar. On the pilot's Gemini 3.1 Pro the planner's schema, whose 400-id vocabulary enum was named by every id field,
inlined to 3,571,917 input tokens, over the 1,048,576 limit, and the whole PlanReply's could not be flattened at all. So
each call's body is captured here as the vertex-gemini adapter would send it, on a Gemini 3 model and on Gemini 4 Argon,
with a vocabulary of 400 ids (and a glossary of 400 terms), and the schema it sends is inlined as Vertex inlines it: its
size and its largest enum are bounded, so a schema Vertex would refuse fails here first. A call sent in JSON mode sends
no schema: the full plan, and since Vertex AI answered the planner's schema with a bare "invalid argument" on both
models, the planner on Gemini too (#561); its schema, as it would be sent, is bounded all the same, since it is what
every other provider is sent. Gemini is sent no maxItems or minItems. The bounds are planning estimates, not Vertex's published limits: INLINED_LIMIT bytes is under a tenth of the
token limit at the ratio the refusal showed (about 0.8 tokens a byte), and no enum may list a vocabulary.
"""
from __future__ import annotations

import json

import pytest

from kge.adapters.model import providers
from kge.services.ask.engine import planmodel
from kge.services.ask.engine.combined import (GLOSSARY_INSTRUCTIONS, GLOSSARY_VERSION, WORDING_INSTRUCTIONS,
                                              WORDING_VERSION, GlossaryContract, WordingContract, glossary_prompt,
                                              wording_prompt)
from kge.services.ask.engine.modelcall import contract_schema, structured_call
from kge.services.ask.engine.plancontract import PlanContract
from tests.plan_fixtures import vocabulary

IDS = 400
INLINED_LIMIT = 100_000
LARGEST_ENUM = 64
MODELS = ("gemini-3.1-pro-preview", "gemini-4-argon")
STEERING = ("MODEL_MAX_TOKENS", "MODEL_HIGH_MAX_TOKENS", "ASK_PLAN_MAX_TOKENS", "ASK_PLAN_THINKING_TOKENS",
            "ASK_PLAN_THINKING_LEVEL", "ASK_CLASSIFY_THINKING_TOKENS", "ASK_CLASSIFY_THINKING_LEVEL",
            "ASK_WORDING_THINKING_TOKENS", "ASK_WORDING_THINKING_LEVEL", "MODEL_THINKING_TOKENS", "MODEL_THINKING_LEVEL",
            "MODEL_HIGH_THINKING_TOKENS", "MODEL_HIGH_THINKING_LEVEL", "ASK_MODEL_CORRECTIONS", "WAREHOUSE_BACKEND",
            "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE")


@pytest.fixture(autouse=True)
def plain(monkeypatch):
    for name in STEERING:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(providers.Chat, "token", lambda self: ("stand-in", "your-project-id"))


def synthetic_ids(n: int = IDS) -> tuple[str, ...]:
    shapes = ("u.measure_{i}", "u.context_{i}", "u.table.column_{i}", "cal.calendar_{i}")
    return tuple(shapes[i % len(shapes)].format(i=i) for i in range(n))


def gemini(model: str, reply: dict):
    """A vertex-gemini model whose transport records each body and answers it with reply."""
    llm = providers.LLM("vertex-gemini", model, project="your-project-id", region="global", tier="high")
    sent: list[dict] = []

    def post(url, headers, body):
        sent.append(json.loads(json.dumps(body)))
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(reply)}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 20, "thoughtsTokenCount": 100}}
    llm._post = post
    return llm, sent


def inlined(schema):
    """schema with every $ref replaced by the definition it names, and no $defs, as Vertex AI flattens one."""
    defs = schema.get("$defs") or {}

    def walk(node, depth):
        assert depth < 256, "the schema refers to itself: Vertex cannot flatten it"
        if isinstance(node, list):
            return [walk(n, depth + 1) for n in node]
        if not isinstance(node, dict):
            return node
        if isinstance(node.get("$ref"), str):
            named = defs[node["$ref"].rsplit("/", 1)[-1]]
            return walk({**named, **{k: v for k, v in node.items() if k != "$ref"}}, depth + 1)
        return {k: walk(v, depth + 1) for k, v in node.items() if k != "$defs"}
    return walk(schema, 0)


def largest_enum(node) -> int:
    if isinstance(node, dict):
        own = len(node["enum"]) if isinstance(node.get("enum"), list) else 0
        return max([own, *(largest_enum(v) for k, v in node.items() if k != "enum")])
    if isinstance(node, list):
        return max([0, *(largest_enum(v) for v in node)])
    return 0


def sent_schema(body: dict):
    return body["generationConfig"].get("responseJsonSchema")


def bounded(schema, ids=()) -> None:
    flat = inlined(schema)
    size = len(json.dumps(flat).encode())
    assert size <= INLINED_LIMIT, f"the schema inlines to {size:,} bytes, over {INLINED_LIMIT:,}"
    assert largest_enum(flat) <= LARGEST_ENUM, f"an enum lists {largest_enum(flat)} values, over {LARGEST_ENUM}"
    text = json.dumps(flat)
    assert not any(json.dumps(i) in text for i in ids), "an id shown the model is in the schema"


def plan_call(model):
    """The planner's call as read_on makes it, over IDS ids: JSON mode on Gemini, the ids in the data."""
    llm, sent = gemini(model, {"decision": "abstain", "unread": ["revenue"]})
    ids = synthetic_ids()
    names = [{"id": i} for i in ids]
    structured_call(llm, "plan", version=planmodel.ON_VERSION, instructions=planmodel.on_instructions(),
                    data={"Vocabulary": {"names": names}}, question="Total revenue for June 2025?", contract=PlanContract,
                    ids=ids, legacy_prompt="unused")
    return sent[0], ids


def shown_schema(body: dict) -> dict:
    """The schema a JSON-mode body shows in its system instruction."""
    text = body["systemInstruction"]["parts"][0]["text"]
    return json.loads(text.split("\n\n" + providers.JSON_MODE_SCHEMA + "\n", 1)[1])


@pytest.mark.parametrize("model", MODELS)
def test_the_planner_is_sent_in_json_mode_and_its_schema_inlines_within_the_bound_on_a_400_id_vocabulary(model):
    body, ids = plan_call(model)
    assert sent_schema(body) is None, "Gemini is sent the planner in JSON mode"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    shown = json.dumps(shown_schema(body))
    assert not any(json.dumps(i) in shown for i in ids), "an id shown the model is in the schema"
    schema = providers.provider_schema(contract_schema(PlanContract, ids), "vertex-gemini")
    bounded(schema, ids)
    assert "maxItems" not in json.dumps(schema) and "minItems" not in json.dumps(schema)
    assert all(json.dumps(i) in body["contents"][0]["parts"][0]["text"] for i in ids), "the ids are in the data"


@pytest.mark.parametrize("model", MODELS)
def test_read_on_sends_the_same_schema_whatever_the_vocabulary(model):
    llm, sent = gemini(model, {"decision": "abstain", "unread": ["revenue"]})
    planmodel.read_on(llm, "Metric", vocabulary())
    assert sent_schema(sent[0]) is None and shown_schema(sent[0]) == shown_schema(plan_call(model)[0])


@pytest.mark.parametrize("model", MODELS)
def test_the_full_plan_is_sent_in_json_mode_with_no_schema_to_flatten(model):
    llm, sent = gemini(model, {"kind": "abstain", "spans": [], "missing": ["no declaration"]})
    planmodel.read(llm, "Metric", vocabulary())
    config = sent[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    schema = sent_schema(sent[0])
    if schema is not None:
        bounded(schema)
    assert '"Obligation"' in sent[0]["systemInstruction"]["parts"][0]["text"], "JSON mode shows the schema"


@pytest.mark.parametrize("model", MODELS)
def test_the_glossary_classifiers_schema_inlines_within_the_bound_on_400_terms(model):
    llm, sent = gemini(model, {"terms": [], "period": None})
    terms = [f"Term {i}" for i in range(IDS)]
    structured_call(llm, "classify", version=GLOSSARY_VERSION, instructions=GLOSSARY_INSTRUCTIONS,
                    data={"Glossary terms": terms, "Calendars": {}}, question="What does Term 7 mean?",
                    contract=GlossaryContract, ids=terms, legacy_prompt=glossary_prompt(terms, {}, "What does Term 7 mean?"))
    bounded(sent_schema(sent[0]), terms)


@pytest.mark.parametrize("model", MODELS)
def test_the_wordings_schema_inlines_within_the_bound(model):
    llm, sent = gemini(model, {"answer": "Total revenue was 1,250,000."})
    facts = {"figures": [{"measure": "Total revenue", "value": 1250000, "citation": 1}]}
    structured_call(llm, "wording", version=WORDING_VERSION, instructions=WORDING_INSTRUCTIONS, data={"Facts": facts},
                    question="Total revenue?", contract=WordingContract, legacy_prompt=wording_prompt(facts))
    bounded(sent_schema(sent[0]))


def test_the_bound_refuses_a_schema_that_names_an_id_enum_at_every_id_field():
    """The check itself: the schema the planner sent before #561, a 400-id enum behind a $ref at each id field, fails."""
    from kge.adapters.model.providers import provider_schema
    ids = synthetic_ids()

    def enumerated(node):
        if isinstance(node, list):
            return [enumerated(n) for n in node]
        if not isinstance(node, dict):
            return node
        if node.get("x-ids") == "vocabulary":
            return {"$ref": "#/$defs/VocabularyId"}
        return {k: enumerated(v) for k, v in node.items()}
    before = enumerated(contract_schema(PlanContract, ids))
    before["$defs"]["VocabularyId"] = {"type": "string", "enum": list(ids)}
    with pytest.raises(AssertionError):
        bounded(provider_schema(before, "vertex-gemini"))
