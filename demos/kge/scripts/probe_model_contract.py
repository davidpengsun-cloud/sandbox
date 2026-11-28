"""Probe Ask's structured model calls on Vertex AI: is each contract's schema accepted, and how many thinking and
contract tokens does each thinking setting spend?

    python scripts/probe_model_contract.py [--agent context] [--calls plan,plan_full,classify,wording]
        [--arms default,500,1000,low,medium] [--ids 400] [--dry-run] [--json PATH]

Run from the demo's folder (demos/kge), with the deployment's model settings in the environment or in .env. For each
call (plan, the planner; plan_full, the planner under ASK_ORDER=model_first, whose contract is the whole
query-plan-v1 PlanReply; classify, the glossary classifier; wording, the answer's wording) and each arm, the probe sets
the arm, builds the call's request through Ask's own helper (kge/services/ask/engine/modelcall.py) and captures the body
the model adapter would send, so the body is exactly what Ask sends. An arm is default (the settings as they are), a
number (ASK_<CALL>_THINKING_TOKENS, ASK_PLAN_ for plan_full) or a level, low, medium or high (ASK_<CALL>_THINKING_LEVEL); the other setting of the
call's own scope is unset for the arm, and every setting is put back after it. The planner is shown --ids synthetic
vocabulary ids in its data, as a pack's names are; since #561 its schema names no id (each is a plain string), so the
schema is the same size whatever the vocabulary's. The question is 'Total revenue for June 2025?'. plan_full is sent in
JSON mode, its schema in the system instruction and none as the structured output (#561), so its row shows mode json
and no schema size; so is plan on Gemini 3 and later, as the adapter sends it (GEMINI_JSON_MODE_CALLS).

Each row gives the schema's size in bytes as sent (schema_bytes) and with every $ref inlined (inlined_bytes), as
Vertex AI inlines them before it counts the request's input tokens, and the largest enum left in it (largest_enum).

With --dry-run the probe prints each body (the schema as its size in bytes), one JSON object a line, and a table, and
stops: no network, no credentials. It runs on any provider whose requests the adapter makes itself (gemini,
vertex-gemini, anthropic, openai, ollama) and on vertex-claude, whose SDK call it captures instead.

Otherwise the model must be gemini or vertex-gemini. The probe POSTs each captured body itself, to the URL the adapter
uses, signed in as the adapter signs in (google-auth's application default credentials, cloud-platform scope, for
vertex-gemini), so a refusal prints Google's own message, never the token. It reports one row per call and arm: the HTTP
status, the error's first 300 characters, finishReason, promptTokenCount, thoughtsTokenCount, candidatesTokenCount, the
time taken, whether the reply validates against the contract, and the schema's size. As production does
(LLM.structured), a call whose schema the provider refuses (kge/adapters/model/errors.py schema_refused: a 400 naming
the schema or the input token count, or the generic invalid argument on a request that sent a schema) is sent again
once in JSON mode; its row then reports the retry, says fallback yes, and a line names the call and arm. A 400 on an arm that sends a
thinking budget, whose message names thinking, says so, naming the tier's level setting to use instead. The exit
status is 1 when a plan or plan_full arm is refused, after any fallback, with a 400 that does not name thinking, that is when a planner's
contract schema is rejected; 2 when the probe cannot run as asked (no model, or one it cannot probe); else 0.

Cost: calls times arms model calls, 20 by default. A planning estimate, not measured: typically under $0.70 at the
Gemini 3 Pro preview list prices (about $2 in and $12 out per million tokens), and at most about $2.60 should every call
run to its full budget. The owner's GCP agent runs it, with the pilot's credentials; it is never a gate and never run in
a test against a real provider.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

DEMO = Path(__file__).resolve().parent.parent
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

CALLS = ("plan", "plan_full", "classify", "wording")
MODEL_CALL = {"plan": "plan", "plan_full": "plan", "classify": "classify", "wording": "wording"}
"""The structured call each probe call is, whose settings it reads."""
PLANS = ("plan", "plan_full")
DEFAULT_ARMS = "default,500,1000,low,medium"
LEVELS = ("low", "medium", "high")
QUESTION = "Total revenue for June 2025?"
GEMINI = ("gemini", "vertex-gemini")
CAPTURED = ("gemini", "vertex-gemini", "anthropic", "openai", "ollama", "vertex-claude")
ERROR_CHARS = 300
STUB_TEXT = "{}"
COLUMNS = (("call", False), ("arm", False), ("thinking", False), ("max_tokens", True), ("status", True), ("error", False),
           ("finish", False), ("prompt", True), ("thoughts", True), ("contract", True), ("ms", True), ("valid", False),
           ("mode", False), ("schema_bytes", True), ("inlined_bytes", True), ("largest_enum", True), ("fallback", False))


class Refusal(Exception):
    """A probe that cannot run as asked: no model, a model it cannot probe, or an arm it does not know. Exit status 2."""


class DryRunCredentials:
    """Credentials that are always valid, for a dry run: no sign-in, no network."""

    valid = True
    token = "dry-run"


def synthetic_ids(n: int) -> list[str]:
    """n vocabulary ids of the shapes a pack's are: measures, contexts, columns and calendars, in turn."""
    shapes = ("u.measure_{i}", "u.context_{i}", "u.table.column_{i}", "cal.calendar_{i}")
    return [shapes[i % len(shapes)].format(i=i) for i in range(max(n, len(shapes)))]


def plan_case(ids: list[str]):
    """The planner's call: its system instruction, a vocabulary naming every synthetic id, and the contract over them."""
    from kge.services.ask.engine.plancontract import PlanContract
    from kge.services.ask.engine.planmodel import ON_VERSION, on_instructions
    labels = ("Total revenue", "Retail", "Region", "Calendar month")
    vocabulary = {"names": [{"id": identifier, "label": f"{labels[i % len(labels)]} {i}"} for i, identifier in enumerate(ids)]}
    legacy = ON_VERSION + "\n" + json.dumps({"question": QUESTION, "vocabulary": vocabulary}, sort_keys=True)
    return dict(version=ON_VERSION, instructions=on_instructions(), data={"Vocabulary": vocabulary}, question=QUESTION,
                contract=PlanContract, ids=tuple(ids), legacy_prompt=legacy)


def plan_full_case():
    """The planner's call under ASK_ORDER=model_first: query-plan-v1's instructions and its whole PlanReply schema, the
    largest the planner has (removals allowed), sent in JSON mode as the planner sends it, and validated by the planner
    itself."""
    from kge.services.ask.engine.planmodel import INSTRUCTIONS, VERSION, FullReply, reply_schema
    vocabulary = {"measures": [{"id": "u.measure_0", "label": "Total revenue"}]}
    legacy = VERSION + "\n" + json.dumps({"question": QUESTION, "vocabulary": vocabulary}, sort_keys=True)
    return dict(version=VERSION, instructions=INSTRUCTIONS, data={"Vocabulary": vocabulary}, question=QUESTION,
                contract=FullReply, ids=(), schema=reply_schema(removals=True), validate=False, legacy_prompt=legacy,
                json_mode=True)


def classify_case():
    """The glossary classifier's call, over 30 term names."""
    from kge.services.ask.engine.combined import GLOSSARY_INSTRUCTIONS, GLOSSARY_VERSION, GlossaryContract, glossary_prompt
    terms = ["Total revenue", "Net revenue", "Gross margin", *(f"Term {i}" for i in range(3, 30))]
    return dict(version=GLOSSARY_VERSION, instructions=GLOSSARY_INSTRUCTIONS, data={"Glossary terms": terms, "Calendars": {}},
                question=QUESTION, contract=GlossaryContract, ids=tuple(terms), legacy_prompt=glossary_prompt(terms, {}, QUESTION))


def wording_case():
    """The answer's wording, over three facts."""
    from kge.services.ask.engine.combined import WORDING_INSTRUCTIONS, WORDING_VERSION, WordingContract, wording_prompt
    facts = {"unit_label": "Retail", "period": "2025-06", "figures": [{"measure": "Total revenue", "value": 1250000, "citation": 1}]}
    return dict(version=WORDING_VERSION, instructions=WORDING_INSTRUCTIONS, data={"Facts": facts}, question=QUESTION,
                contract=WordingContract, ids=(), legacy_prompt=wording_prompt(facts))


def case_for(call: str, ids: list[str]) -> dict:
    """The fields of one call's structured request, as Ask builds it."""
    if call == "plan_full":
        return plan_full_case()
    return plan_case(ids) if call == "plan" else classify_case() if call == "classify" else wording_case()


def arm_settings(call: str, arm: str) -> dict[str, str | None]:
    """The environment an arm sets: nothing for default; the call's thinking tokens for a number, its level for a level,
    with the other of the two unset."""
    stem = f"ASK_{MODEL_CALL[call].upper()}_THINKING"
    if arm == "default":
        return {}
    if arm.isdigit():
        return {f"{stem}_TOKENS": arm, f"{stem}_LEVEL": None}
    if arm in LEVELS:
        return {f"{stem}_LEVEL": arm, f"{stem}_TOKENS": None}
    raise Refusal(f"--arms: {arm!r} is not default, a whole number of thinking tokens, or low, medium or high")


@contextmanager
def environment(values: dict[str, str | None]):
    """os.environ with values set (None unsets), put back as it was afterwards."""
    before = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def stub_reply(provider: str) -> dict:
    """A short, complete reply in the provider's own shape, so a captured call ends without reaching the model."""
    if provider in GEMINI:
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": STUB_TEXT}]}, "finishReason": "STOP"}],
                "usageMetadata": {}}
    if provider in ("anthropic", "vertex-claude"):
        return {"content": [{"type": "text", "text": STUB_TEXT}], "stop_reason": "end_turn", "usage": {}}
    return {"choices": [{"message": {"content": STUB_TEXT}, "finish_reason": "stop"}], "usage": {}}


def capture(llm, call: str, case: dict) -> tuple[dict, dict, str]:
    """The (headers, body, url) the adapter sends for one structured call, captured in place of the call."""
    from kge.adapters.model import providers
    from kge.services.ask.engine.modelcall import structured_call
    sent: list[tuple[str, dict, dict]] = []

    def transport(url: str, headers: dict, body: dict) -> dict:
        sent.append((url, dict(headers), json.loads(json.dumps(body))))
        return stub_reply(llm.provider)

    def sdk(chat, **fields) -> dict:
        sent.append(("", {}, json.loads(json.dumps({"model": chat.model, **fields}, default=str))))
        return stub_reply(llm.provider)

    llm.chat.transport = transport
    with mock.patch.object(providers.Chat, "vertex_create", sdk):
        structured_call(llm, MODEL_CALL[call], **case)
    if not sent:
        raise RuntimeError("the adapter sent no request")
    url, headers, body = sent[-1]
    return headers, body, url


def schema_of(body: dict) -> object:
    """The contract's schema as the body carries it, in whichever provider's place."""
    config = body.get("generationConfig") or {}
    if "responseJsonSchema" in config:
        return config["responseJsonSchema"]
    output = body.get("output_config") or (body.get("extra_body") or {}).get("output_config") or {}
    if "format" in output:
        return output["format"].get("schema")
    return ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")


def inlined(schema: object) -> object:
    """schema with every $ref replaced by the definition it names, and no $defs, as Vertex AI flattens a schema."""
    defs = schema.get("$defs") or {} if isinstance(schema, dict) else {}

    def walk(node: object, depth: int) -> object:
        if depth > 256:
            raise RecursionError("the schema refers to itself")
        if isinstance(node, list):
            return [walk(n, depth + 1) for n in node]
        if not isinstance(node, dict):
            return node
        if isinstance(node.get("$ref"), str):
            named = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
            return walk({**named, **{k: v for k, v in node.items() if k != "$ref"}}, depth + 1)
        return {k: walk(v, depth + 1) for k, v in node.items() if k != "$defs"}
    return walk(schema, 0)


def largest_enum(schema: object) -> int:
    """The most values any enum in schema lists."""
    if isinstance(schema, dict):
        own = len(schema["enum"]) if isinstance(schema.get("enum"), list) else 0
        return max([own, *(largest_enum(v) for k, v in schema.items() if k != "enum")])
    if isinstance(schema, list):
        return max([0, *(largest_enum(v) for v in schema)])
    return 0


def summary(body: dict) -> dict:
    """The body as the dry run prints it: every field, the schema shown as its size in bytes."""
    shown = json.loads(json.dumps(body))
    config = shown.get("generationConfig")
    size = f"<{len(json.dumps(schema_of(body)).encode())} bytes>"
    if isinstance(config, dict) and "responseJsonSchema" in config:
        config["responseJsonSchema"] = size
    for holder in (shown.get("output_config"), (shown.get("extra_body") or {}).get("output_config")):
        if isinstance(holder, dict) and "format" in holder:
            holder["format"]["schema"] = size
    form = (shown.get("response_format") or {}).get("json_schema")
    if isinstance(form, dict):
        form["schema"] = size
    return shown


def thinking_sent(body: dict) -> str:
    """The thinking a body sends, as the trace records it: tokens:n, level:name, or none."""
    config = (body.get("generationConfig") or {}).get("thinkingConfig") or {}
    if "thinkingBudget" in config:
        return f"tokens:{config['thinkingBudget']}"
    if "thinkingLevel" in config:
        return f"level:{config['thinkingLevel']}"
    effort = (body.get("output_config") or (body.get("extra_body") or {}).get("output_config") or {}).get("effort")
    effort = effort or body.get("reasoning_effort")
    return f"level:{effort}" if effort else "none"


def max_tokens_of(body: dict) -> int | None:
    """The reply budget a body sends."""
    return (body.get("generationConfig") or {}).get("maxOutputTokens") or body.get("max_tokens")


def error_message(raw: bytes) -> str:
    """Google's own message from an error reply, else the reply's text, at most ERROR_CHARS characters on one line."""
    text = raw.decode(errors="replace")
    try:
        message = (json.loads(text).get("error") or {}).get("message") or text
    except (ValueError, AttributeError):
        message = text
    return " ".join(str(message).split())[:ERROR_CHARS]


def post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict | None, str]:
    """POST the captured body: (HTTP status, the reply's JSON or None, the error's message or '')."""
    from kge.adapters.http import open_http
    request = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body).encode())
    try:
        with open_http(request, timeout=timeout) as reply:
            return reply.status, json.loads(reply.read()), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, error_message(exc.read() if exc.fp is not None else b"")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        return 0, None, " ".join(f"unreachable: {reason}".split())[:ERROR_CHARS]


def validates(call: str, case: dict, data: dict | None) -> str:
    """Whether the reply's text is one JSON object that passes the call's contract: yes, no (with the first error), or
    legacy for a full plan reply the planner validates itself. A contract with no legacy form (plan_full's FullReply, the
    whole PlanReply) is validated as it stands."""
    from pydantic import ValidationError
    if data is None:
        return "no reply"
    candidate = next(iter(data.get("candidates") or []), {})
    text = "".join(p.get("text") or "" for p in (candidate.get("content") or {}).get("parts") or [] if not p.get("thought"))
    try:
        raw = json.loads(text)
    except ValueError:
        return "no: not JSON"
    if not isinstance(raw, dict):
        return "no: not one object"
    contract = case["contract"]
    legacy = getattr(contract, "legacy", None)
    if callable(legacy) and legacy(raw):
        return "legacy"
    try:
        contract.model_validate(raw, context={"ids": frozenset(case["ids"])})
    except ValidationError as exc:
        first = exc.errors()[0]
        return f"no: {'/'.join(map(str, first['loc']))}: {first['msg']}"[:120]
    return "yes"


def table(rows: list[dict]) -> str:
    """The rows as a Markdown table, numbers right-aligned."""
    head = "| " + " | ".join(name for name, _ in COLUMNS) + " |"
    rule = "|" + "|".join("---:" if right else "---" for _, right in COLUMNS) + "|"
    lines = [head, rule]
    for row in rows:
        cells = ["" if row.get(name) is None else str(row[name]).replace("|", "/") for name, _ in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Ask's structured model calls on Vertex AI.")
    parser.add_argument("--agent", default="context", help="the agent whose model Ask uses (default context)")
    parser.add_argument("--calls", default=",".join(CALLS), help="plan, plan_full, classify and wording, comma-separated")
    parser.add_argument("--arms", default=DEFAULT_ARMS, help="default, thinking tokens or levels, comma-separated")
    parser.add_argument("--ids", type=int, default=400, help="how many synthetic vocabulary ids the planner is shown")
    parser.add_argument("--dry-run", action="store_true", help="print the bodies and stop: no network")
    parser.add_argument("--json", help="also write the rows (and, with --dry-run, the whole bodies) to this file")
    args = parser.parse_args(argv)
    args.calls = [c.strip() for c in args.calls.split(",") if c.strip()]
    args.arms = [a.strip().lower() for a in args.arms.split(",") if a.strip()]
    unknown = [c for c in args.calls if c not in CALLS]
    if unknown or not args.calls:
        parser.error(f"--calls must name {', '.join(CALLS)}, not {', '.join(unknown) or 'nothing'}")
    if not args.arms:
        parser.error("--arms names no arm")
    for call in args.calls:
        for arm in args.arms:
            try:
                arm_settings(call, arm)
            except Refusal as exc:
                parser.error(str(exc))
    return args


def model(agent: str, dry_run: bool):
    """The agent's model, refused unless the probe can capture its requests (and, live, unless it is Gemini)."""
    from kge.adapters.model import model_for
    from kge.pipeline.settings import settings
    llm = model_for(agent)
    if llm is None:
        raise Refusal(f"no model for the {agent} agent: set MODEL_PROVIDER or its tier's MODEL_<TIER>_PROVIDER")
    allowed = CAPTURED if dry_run else GEMINI
    if llm.provider not in allowed:
        raise Refusal(f"the probe needs a {' or '.join(allowed)} model for the {agent} agent, not {llm.name}"
                         + ("" if dry_run else "; --dry-run prints the bodies of the other providers"))
    if dry_run and llm.provider in ("vertex-gemini", "vertex-claude"):
        llm.chat._account = (DryRunCredentials(), (settings().VERTEX_PROJECT or "").strip() or "dry-run-project")
    return llm


def json_mode_case(case: dict) -> dict:
    """The same call in JSON mode, as LLM.structured retries a refused schema."""
    return {**case, "json_mode": True}


def main(argv: list[str] | None = None) -> int:
    from kge.adapters.model.errors import model_timeout, schema_refused
    from kge.adapters.model.providers import BUDGET_TIERS
    from kge.pipeline.envfile import load_env
    args = arguments(argv)
    load_env()
    try:
        llm = model(args.agent, args.dry_run)
    except Refusal as exc:
        print(exc, file=sys.stderr)
        return 2
    ids = synthetic_ids(args.ids)
    tier_level = f"MODEL_{llm.tier.upper()}_THINKING_LEVEL" if llm.tier in BUDGET_TIERS else "MODEL_THINKING_LEVEL"
    rows: list[dict] = []
    bodies: list[dict] = []
    rejected = False
    print(f"model: {llm.name} (tier {llm.tier}, agent {args.agent}); {len(args.calls) * len(args.arms)} "
          f"{'bodies' if args.dry_run else 'model calls'}")
    for call in args.calls:
        case = case_for(call, ids)
        for arm in args.arms:
            row: dict = {"call": call, "arm": arm}
            rows.append(row)
            with environment(arm_settings(call, arm)):
                try:
                    headers, body, url = capture(llm, call, case)
                except Exception as exc:
                    row["error"] = " ".join(f"{type(exc).__name__}: {exc}".split())[:ERROR_CHARS]
                    continue
            schema = schema_of(body)
            flat = inlined(schema) if schema is not None else None
            row.update(thinking=thinking_sent(body), max_tokens=max_tokens_of(body), mode="schema" if schema is not None else "json",
                       schema_bytes=len(json.dumps(schema).encode()) if schema is not None else None,
                       inlined_bytes=len(json.dumps(flat).encode()) if flat is not None else None,
                       largest_enum=largest_enum(flat) if flat is not None else None)
            if args.dry_run:
                print(json.dumps({"call": call, "arm": arm, "body": summary(body)}, sort_keys=True))
                bodies.append({"call": call, "arm": arm, "body": body})
                continue
            started = time.perf_counter()
            status, data, error = post(url, headers, body, model_timeout())
            if schema is not None and schema_refused(status, error, schema_sent=True):
                print(f"schema refused on {call} {arm} (HTTP {status}): retried once in JSON mode, as production does")
                with environment(arm_settings(call, arm)):
                    headers, body, url = capture(llm, call, json_mode_case(case))
                started = time.perf_counter()
                status, data, error = post(url, headers, body, model_timeout())
                row.update(fallback="yes", mode="json", thinking=thinking_sent(body), max_tokens=max_tokens_of(body))
            use = (data or {}).get("usageMetadata") or {}
            candidate = next(iter((data or {}).get("candidates") or []), {})
            row.update(status=status, error=error or None, finish=candidate.get("finishReason"),
                       prompt=use.get("promptTokenCount"), thoughts=use.get("thoughtsTokenCount"),
                       contract=use.get("candidatesTokenCount"), ms=round((time.perf_counter() - started) * 1000),
                       valid=validates(call, case, data) if status == 200 else None)
            about_thinking = "thinking" in (error or "").lower()
            if status == 400 and row["thinking"].startswith("tokens:") and about_thinking:
                print(f"thinkingBudget not accepted on {llm.model}: set {tier_level} instead")
            if call in PLANS and status == 400 and not about_thinking:
                rejected = True
    print()
    print(table(rows))
    if args.json:
        Path(args.json).write_text(json.dumps({"model": llm.name, "tier": llm.tier, "rows": rows,
                                               **({"bodies": bodies} if args.dry_run else {})}, indent=2, sort_keys=True))
    if rejected:
        print("a planner's contract schema was rejected: see the error above")
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
