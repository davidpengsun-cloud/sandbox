"""The one way an Ask model call is made: a structured call against a Pydantic contract (#561).

Every Ask model call (the planner, the glossary classifier and the answer's wording) goes through structured_call, or
structured_retry, which corrects a failed reply. A call is one ModelRequest (kge/ports.py): a system instruction, the
content (the data as titled sections, then the question, last), the contract's JSON schema, and the conversation's
correction turns. An id in a schema is a plain string, never an enum of the ids shown (contract_schema): the ids are in
the data the model reads, and validation and the corrections enforce them. A model that offers structured() (the language model adapter, the mock among its providers) is sent
the request; a model that offers json() alone (a test's stand-in, the grounding replay) is sent the request's legacy
single-text form, the prompt as it was before structured calls, so its recordings still replay. A recording's key never
depends on the prompt's layout.

The reply is validated against the contract, the ids it may name checked against those it was shown. A reply that fails
is answered with a correction that continues the conversation: the previous contract as the model's turn, then the
errors, each with the choices allowed where the schema lists them or the field names an id, as the user's turn. A reply cut off at its token
budget is answered with a note to think less, and is retried only when thinking less would leave the contract more room.
How many corrections a call may take is ASK_MODEL_CORRECTIONS (0 to 2, default 2).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, RLock, get_ident
from difflib import SequenceMatcher
from typing import Annotated, Any, Callable, Iterable, Mapping

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, ValidationInfo

from kge.ports import ModelRequest, ModelTurn
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.dryrun import stop_before_model


class _DispatchWitness:
    """Evidence from the current concrete Ask pass, not a conclusion drawn from an absent trace."""

    def __init__(self):
        self.thread = get_ident()
        self.entered = Event()
        self.active = False

    def no_dispatch(self) -> bool:
        with _dispatch_lock:
            current = _dispatch_witnesses.get()
            return self.active and self.thread == get_ident() and bool(current) and current[0] is self and not self.entered.is_set()


_dispatch_witnesses: ContextVar[tuple[_DispatchWitness, ...]] = ContextVar("ask_dispatch_witnesses", default=())
_dispatch_lock = RLock()
_active_dispatch_witnesses: set[_DispatchWitness] = set()


@contextmanager
def observe_model_dispatch():
    """Observe all synchronous model dispatch in an Ask pass; nested passes mark every active observer."""
    witness = _DispatchWitness()
    with _dispatch_lock:
        witness.active = True
        _active_dispatch_witnesses.add(witness)
    token = _dispatch_witnesses.set((*_dispatch_witnesses.get(), witness))
    try:
        yield witness
    finally:
        _dispatch_witnesses.reset(token)
        with _dispatch_lock:
            witness.active = False
            _active_dispatch_witnesses.discard(witness)


def _mark_model_dispatch() -> None:
    """A dispatch without propagated context makes every overlapping pass uncertain, never falsely model-free."""
    with _dispatch_lock:
        for witness in _dispatch_witnesses.get() or tuple(_active_dispatch_witnesses):
            witness.entered.set()


FILLER = "filler"
MAX_CONTRACT_ITEMS = 64
MAX_ALLOWED = 50
MAX_ERRORS = 20
NOT_ONE_OBJECT = "The reply is not one JSON object."
TRUNCATED_NOTE = ("Your previous reply was cut off at its token budget before the contract was complete. Think less, "
                  "and reply with the whole contract.")
FAILED_CHECKS = "Your previous reply did not pass these checks. Reply with the whole contract again, corrected."
MARKER = "x-ids"
ID_KINDS = ("vocabulary", "reading")
NAMED_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def declared(value: str, info: ValidationInfo) -> str:
    """An id must be one of the ids the model was shown, when the validation names them (context 'ids')."""
    ids = (info.context or {}).get("ids") if isinstance(info.context, Mapping) else None
    if ids and value not in ids:
        raise ValueError("not a declared id")
    return value


def read_as(value: str, info: ValidationInfo) -> str:
    """A reading's id: a declared id, or filler for words that set nothing."""
    return value if value == FILLER else declared(value, info)


VocabId = Annotated[str, Field(min_length=1, json_schema_extra={MARKER: "vocabulary"}), AfterValidator(declared)]
ReadingId = Annotated[str, Field(min_length=1, json_schema_extra={MARKER: "reading"}), AfterValidator(read_as)]


class Contract(BaseModel):
    """A model call's contract: the decisions the model makes, and nothing the code can fill in itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def legacy(cls, raw: Any) -> bool:
        """True when raw is an older, fuller reply shape that the caller validates itself, not this contract."""
        return False


@dataclass(frozen=True)
class FieldError:
    """One reason a reply failed: the contract field it is at (a JSON pointer, '' for the whole reply), what is wrong,
    and the choices the schema allows there, if it lists them."""

    field: str
    error: str
    allowed: tuple[str, ...] = ()
    of: int = 0
    """How many choices there are when allowed holds only those nearest the reply (nearest), else 0."""

    def dump(self) -> dict:
        return {"field": self.field, "error": self.error, "allowed": list(self.allowed), **({"of": self.of} if self.of else {})}

    @classmethod
    def load(cls, raw: Mapping) -> "FieldError":
        return cls(str(raw.get("field") or ""), str(raw.get("error") or ""), tuple(str(a) for a in raw.get("allowed") or ()),
                   int(raw.get("of") or 0))

    @classmethod
    def choosing(cls, field: str, error: str, choices: Iterable[str], near: Any = None) -> "FieldError":
        """A field error with the choices allowed there: all of them when at most MAX_ALLOWED, else the MAX_ALLOWED nearest
        the value the reply gave (nearest), with how many there are."""
        choices = tuple(choices)
        return cls(field, error, nearest(choices, near), len(choices) if len(choices) > MAX_ALLOWED else 0)


def nearest(choices: Iterable[str], value: Any, limit: int = MAX_ALLOWED) -> tuple[str, ...]:
    """choices, at most limit: all of them when they fit, else those spelled most like value, nearest first. A vocabulary
    can hold hundreds of ids, so a correction offers the ones the model most likely meant rather than the first in
    alphabetical order (#561)."""
    choices = tuple(choices)
    if len(choices) <= limit:
        return choices
    if isinstance(value, Mapping):
        value = value.get("id")
    if not isinstance(value, str) or not value:
        return choices[:limit]
    wanted = value.casefold()
    return tuple(sorted(choices, key=lambda c: (-SequenceMatcher(None, wanted, c.casefold()).ratio(), c))[:limit])


@dataclass(frozen=True)
class Correction:
    """What the next attempt is told about the last: the contract it returned and the provider's record of that turn,
    with the errors found in it; or, truncated, that it was cut off at its budget."""

    previous: str = ""
    replay: Mapping | None = None
    errors: tuple[FieldError, ...] = ()
    truncated: bool = False
    json_mode: bool = False
    """The attempt was answered in JSON mode (ModelReply.json_mode), so every later attempt is sent in JSON mode too: a
    schema the provider refused is never sent again in the conversation, which keeps it within 1 + 2 calls plus the one
    JSON-mode retry of the refusal."""


@dataclass(frozen=True)
class Outcome:
    """One attempt's result: the validated contract (or the raw dict of a legacy or unvalidated reply), else the errors,
    the reply's text and replay record for a correction, whether it was cut off and whether thinking less leaves more
    room, and the first problem in one line. cause is the exception behind a failure, for naming it, never its text."""

    value: Contract | dict | None
    errors: tuple[FieldError, ...]
    text: str
    replay: Mapping | None
    truncated: bool
    more_room: bool
    problem: str
    cause: BaseException | None = field(default=None, compare=False)
    json_mode: bool = False
    """The attempt was answered in JSON mode (ModelReply.json_mode); a correction after it carries it on."""


class ContractFailure(ValueError):
    """The model's replies did not meet the contract within the corrections allowed, or one was cut off with no room
    left. Its text is the last problem, about the reply's shape, never its content; its cause names the failure."""

    def __init__(self, outcome: Outcome):
        self.outcome = outcome
        super().__init__(outcome.problem or "the reply did not meet the contract")


def _named(node: Any, mark: Callable[[dict], Any]) -> Any:
    """node with mark applied to every schema in it, leaves first; the keys of a properties or $defs map are names, not
    schema keywords."""
    if isinstance(node, list):
        return [_named(n, mark) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: ({n: _named(s, mark) for n, s in v.items()} if k in NAMED_MAPS and isinstance(v, dict) else _named(v, mark))
           for k, v in node.items()}
    return mark(out)


def contract_schema(contract: type[Contract], ids: Iterable[str] = ()) -> dict:
    """The contract's provider-neutral JSON schema. Each id field is a plain string, never an enum of the ids shown nor a
    $ref to one: Vertex AI inlines every $ref, so a 400-id enum named in every id field made the planner's schema millions
    of tokens long (#561). The ids stay in the data the model reads; the contract's validation (context 'ids') and the
    corrections, which list the choices allowed, enforce them. An id field keeps its x-ids marker, which no provider is
    sent (provider_schema drops it), so allowed_for can name its choices. Arrays hold at most MAX_CONTRACT_ITEMS; no
    titles. ids is accepted, and unused, so a caller states what it shows the model in one place."""
    def mark(schema: dict) -> dict:
        schema = {k: v for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
        if schema.get("type") == "array":
            schema["maxItems"] = min(int(schema.get("maxItems", MAX_CONTRACT_ITEMS)), MAX_CONTRACT_ITEMS)
        return schema

    return _named(contract.model_json_schema(ref_template="#/$defs/{model}"), mark)


def render(data: Mapping[str, object], question: str) -> str:
    """The first user turn: one titled section for each piece of data that holds something, in order, then the question,
    always last. Text is shown as it is, anything else as compact JSON."""
    sections = []
    for title, value in data.items():
        if value is None or (hasattr(value, "__len__") and len(value) == 0):
            continue
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        sections.append(f"## {title}\n{body}")
    sections.append("## Question\n" + question)
    return "\n\n".join(sections)


def _resolved(schema: Mapping, node: Any) -> Any:
    """node with its $ref followed, within schema's $defs."""
    seen = 0
    while isinstance(node, Mapping) and isinstance(node.get("$ref"), str) and seen < 32:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = (schema.get("$defs") or {}).get(name, {})
        seen += 1
    return node


def _enum_at(schema: Mapping, node: Any, ids: tuple[str, ...], depth: int = 0) -> tuple[str, ...]:
    node = _resolved(schema, node)
    if not isinstance(node, Mapping) or depth > 8:
        return ()
    if node.get(MARKER) in ID_KINDS and ids:
        return (*ids, FILLER) if node[MARKER] == "reading" else ids
    if isinstance(node.get("enum"), list):
        return tuple(str(v) for v in node["enum"])
    if "const" in node:
        return (str(node["const"]),)
    for branch in (*node.get("anyOf", ()), *node.get("oneOf", ())):
        if found := _enum_at(schema, branch, ids, depth + 1):
            return found
    return ()


def _walk(schema: Mapping, node: Any, path: list[str], ids: tuple[str, ...], depth: int = 0) -> tuple[str, ...]:
    node = _resolved(schema, node)
    if not isinstance(node, Mapping) or depth > 64:
        return ()
    if not path:
        return _enum_at(schema, node, ids)
    head, rest = path[0], path[1:]
    if head in (node.get("properties") or {}):
        return _walk(schema, node["properties"][head], rest, ids, depth + 1)
    if head.isdigit() and "items" in node:
        return _walk(schema, node["items"], rest, ids, depth + 1)
    if head.isdigit() and isinstance(node.get("prefixItems"), list) and int(head) < len(node["prefixItems"]):
        return _walk(schema, node["prefixItems"][int(head)], rest, ids, depth + 1)
    for branch in (*node.get("anyOf", ()), *node.get("oneOf", ())):
        if found := _walk(schema, branch, path, ids, depth + 1):
            return found
    return _walk(schema, node, rest, ids, depth + 1)


def choices_at(schema: Mapping, field: str, ids: Iterable[str] = ()) -> tuple[str, ...]:
    """Every choice allowed at a field, a JSON pointer such as /terms/0: the enum found there, through properties, items,
    $ref and union branches, or, at an id field (its x-ids marker), the ids shown (filler too for a reading); () when
    there are none. A pointer step the schema does not name (a union's tag in a validation error's location) is passed
    over."""
    path = [p for p in field.split("/") if p]
    return _walk(schema, schema, path, tuple(sorted(set(ids))))


def allowed_for(schema: Mapping, field: str, ids: Iterable[str] = (), near: Any = None) -> tuple[str, ...]:
    """The choices allowed at a field (choices_at), at most MAX_ALLOWED: those nearest near when there are more."""
    return nearest(choices_at(schema, field, ids), near)


def validation_errors(exc: ValidationError, schema: Mapping, ids: Iterable[str] = ()) -> tuple[FieldError, ...]:
    """A validation error as field errors, at most MAX_ERRORS, each with the choices allowed at its field, those nearest
    the value given when there are more than MAX_ALLOWED."""
    ids = tuple(ids)
    out = []
    for e in exc.errors()[:MAX_ERRORS]:
        where = "/" + "/".join(map(str, e.get("loc") or ()))
        out.append(FieldError.choosing(where if where != "/" else "", str(e.get("msg") or "invalid"),
                                       choices_at(schema, where, ids), e.get("input")))
    return tuple(out)


def correction_text(c: Correction) -> str:
    """The user's turn that answers a failed reply."""
    if c.truncated:
        return TRUNCATED_NOTE
    lines = [FAILED_CHECKS]
    for e in c.errors:
        line = f"- {e.field}: {e.error}" if e.field else f"- {e.error}"
        if e.allowed and e.of > len(e.allowed):
            line += (f" Allowed (the {len(e.allowed)} of {e.of} nearest your reply; every one is in the data shown): "
                     + ", ".join(e.allowed))
        elif e.allowed:
            line += " Allowed: " + ", ".join(e.allowed)
        lines.append(line)
    return "\n".join(lines)


def _conversation(content: str, corrections: Iterable[Correction]) -> tuple[str, tuple[ModelTurn, ...]]:
    """The first user turn and the turns after it. A failed reply adds the model's turn (the previous contract, or the
    provider's record of it) and the user's (its errors); a reply cut off, or one with no text and no record to show,
    adds no model turn, since a provider refuses an empty one: its note joins the user's text before it, so roles keep
    alternating."""
    turns: list[ModelTurn] = []
    for c in corrections:
        if c.truncated or (not c.previous.strip() and c.replay is None):
            if turns:
                last = turns[-1]
                turns[-1] = ModelTurn(last.role, last.text + "\n\n" + correction_text(c), last.replay)
            else:
                content = content + "\n\n" + correction_text(c)
            continue
        turns += [ModelTurn("model", c.previous, c.replay), ModelTurn("user", correction_text(c))]
    return content, tuple(turns)


def turns(corrections: Iterable[Correction]) -> tuple[ModelTurn, ...]:
    """The correction turns after the first user turn, model then user. A truncated correction before any other adds
    its note to the first user turn instead, which structured_call does."""
    return _conversation("", corrections)[1]


def _failed(errors: tuple[FieldError, ...], text: str, replay: Mapping | None, cause: BaseException | None,
            json_mode: bool = False) -> Outcome:
    first = errors[0] if errors else FieldError("", NOT_ONE_OBJECT)
    problem = f"{first.field}: {first.error}" if first.field else first.error
    return Outcome(None, errors, text, replay, False, False, problem, cause, json_mode)


def structured_call(model, call: str, *, version: str, instructions: str, data: Mapping[str, object], question: str,
                    contract: type[Contract], ids: Iterable[str] = (), schema: Mapping | None = None, legacy_prompt: str,
                    corrections: Iterable[Correction] = (), prepare: Callable[[Any], Any] | None = None,
                    validate: bool = True, json_mode: bool = False) -> Outcome:
    """One attempt at a contract. The model's structured() is sent the request; a model with json() alone is sent
    legacy_prompt, its one argument. A reply cut off at its budget, or that is not one JSON object, or that fails the
    contract, is an Outcome with no value; any other failure (a refusal, no plan available, a failed call) is raised.
    prepare, when given, is applied to the raw reply before it is validated; validate=False, or a reply the contract
    calls legacy, returns the raw reply for the caller to validate. json_mode sends the request with no schema, the
    provider's JSON mode (ModelRequest.json_mode), the reply validated all the same; so is every attempt after one a
    correction says was answered in JSON mode (Correction.json_mode). The Outcome says whether the reply was."""
    from kge.adapters.model import NotJSON, Truncated
    ids = tuple(ids)
    corrections = tuple(corrections)
    json_mode = json_mode or any(c.json_mode for c in corrections)
    schema = dict(schema) if schema is not None else contract_schema(contract, ids)
    content, conversation = _conversation(render(data, question), corrections)
    request = ModelRequest(call=call, version=version, instructions=instructions, content=content, schema=schema,
                           contract=contract.__name__, turns=conversation,
                           less_thinking=sum(1 for c in corrections if c.truncated), attempt=len(corrections),
                           legacy_prompt=legacy_prompt, json_mode=json_mode)
    replay = None
    answered_json = json_mode
    stop_before_model()
    try:
        _mark_model_dispatch()
        if callable(getattr(model, "structured", None)):
            reply = model.structured(request)
            raw, text, replay = reply.data, reply.text, reply.replay
            answered_json = json_mode or getattr(reply, "json_mode", False) is True
        else:
            raw = model.json(legacy_prompt)
            text = json.dumps(raw, sort_keys=True, default=str) if isinstance(raw, dict) else ""
    except Truncated as exc:
        return Outcome(None, (), "", None, True, bool(getattr(exc, "more_room", False)), str(exc), exc,
                       json_mode or getattr(exc, "json_mode", False) is True)
    except NotJSON as exc:
        return _failed((FieldError("", NOT_ONE_OBJECT),), getattr(exc, "reply_text", ""), getattr(exc, "replay", None), exc,
                       json_mode or getattr(exc, "json_mode", False) is True)
    if not isinstance(raw, dict):
        return _failed((FieldError("", NOT_ONE_OBJECT),), text, replay, NotJSON("model reply held no JSON object"), answered_json)
    if prepare is not None:
        raw = prepare(raw)
    if not validate or contract.legacy(raw):
        return Outcome(raw, (), text, replay, False, False, "", None, answered_json)
    try:
        value = contract.model_validate(raw, context={"ids": frozenset(ids)})
    except ValidationError as exc:
        return _failed(validation_errors(exc, schema, ids), text, replay, exc, answered_json)
    return Outcome(value, (), text, replay, False, False, "", None, answered_json)


def structured_retry(model, call: str, *, limit: int | None = None, **same) -> Contract | dict:
    """A contract, correcting a failed reply up to limit times (ASK_MODEL_CORRECTIONS when None), so at most 1 + limit
    calls; a model sent the single-text form alone (single_text) gets one call. A reply that fails the contract is
    corrected; one cut off at its budget is retried with less thinking only
    when that leaves the contract more room. Each failed attempt is traced (model_attempts). Raises ContractFailure when
    the attempts run out or a cut-off reply has no room, from the failure behind it; any other failure propagates."""
    if limit is None:
        from kge.pipeline.settings import ask_model_corrections
        limit = ask_model_corrections()
    if single_text(model):
        limit = 0
    done: list[Correction] = []
    outcome = None
    for attempt in range(1 + max(0, limit)):
        outcome = structured_call(model, call, corrections=tuple(done), **same)
        if outcome.value is not None and not outcome.truncated and not outcome.errors:
            return outcome.value
        runtrace.model_attempt(call, attempt, "truncated" if outcome.truncated else "contract", [outcome.problem])
        if outcome.truncated:
            if not outcome.more_room:
                break
            done.append(Correction(truncated=True, json_mode=outcome.json_mode))
        else:
            done.append(Correction(previous=outcome.text, replay=outcome.replay, errors=outcome.errors,
                                   json_mode=outcome.json_mode))
    raise ContractFailure(outcome) from outcome.cause


def single_text(model) -> bool:
    """Whether a model is sent a structured call's legacy single-text form alone: one with json() but no structured(),
    as a test double, or one that says so (the mock). That form carries no correction turns, so a correction would only
    repeat the identical prompt: such a model gets one call."""
    return not callable(getattr(model, "structured", None)) or getattr(model, "single_text", False) is True


def failure_of(exc: BaseException) -> BaseException:
    """The failure to name for a failed contract call: the one behind a ContractFailure (NotJSON, Truncated, a validation
    error), else the exception itself."""
    if isinstance(exc, ContractFailure) and exc.outcome.cause is not None:
        return exc.outcome.cause
    return exc
