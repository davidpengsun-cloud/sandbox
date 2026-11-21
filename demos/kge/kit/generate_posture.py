#!/usr/bin/env python3
"""Write the kit's posture statement, docs/POSTURE.md, from what the image itself says.

    docker run --rm --network none --entrypoint python3 kge:<version> -c "$(python3 kit/generate_posture.py --code)" \
        | python3 kit/generate_posture.py <version>

kit/build.sh runs it. POSTURE is the code the image runs, with no setting given: the onboard service's posture()
(kge/services/onboard/test_drive.py), which reads each value from the image's own settings and constants, the same
function the test drive's report attests an install with. This script adds no value of its own: it lays them out, each
beside the test that proves it, and stops on a value it has no proof for, so a new switch cannot reach a kit unproven.
It needs Python's standard library alone, as the kit's build does."""
from __future__ import annotations

import json
import sys

POSTURE = "import json; from kge.services.onboard.test_drive import posture; print(json.dumps(posture()))"

TELEMETRY = "tests/unit/test_telemetry.py"
MODEL_DEFAULT = ("tests/unit/test_posture.py::test_with_no_model_setting_no_agent_has_a_model",)
TIER_FALLBACK = (*MODEL_DEFAULT,
                 "tests/unit/test_model_path.py::test_every_agent_is_exactly_the_default_without_tier_settings")
PROOFS = {
    "MODEL_PROVIDER": MODEL_DEFAULT,
    "MODEL_HIGH_PROVIDER": TIER_FALLBACK,
    "MODEL_MEDIUM_PROVIDER": TIER_FALLBACK,
    "MODEL_LOW_PROVIDER": TIER_FALLBACK,
    "TRACING": ("tests/unit/test_tracing.py::test_off_is_the_default_and_records_nothing",),
    "TRACE_PROPAGATE_TO": (f"{TELEMETRY}::test_no_outgoing_call_carries_trace_context_by_default",
                           f"{TELEMETRY}::test_only_a_listed_destination_carries_the_traceparent"),
    "EGRESS_ALLOWED": ("tests/unit/test_posture.py::test_the_egress_limit_is_reported_as_given_and_unset_by_default",
                       "tests/unit/test_infra.py::test_the_pilot_s_egress_reaches_the_listed_google_apis_alone_and_qa_and_prod_keep_theirs"),
    **dict.fromkeys(("OTEL_PYTHON_TRACER_PROVIDER", "OTEL_PYTHON_METER_PROVIDER", "OTEL_PYTHON_LOGGER_PROVIDER",
                     "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS"),
                    (f"{TELEMETRY}::test_every_third_party_switch_is_off_at_start_up",
                     "tests/unit/test_posture.py::test_the_opentelemetry_provider_settings_are_unset")),
    "OpenTelemetry global propagator": (f"{TELEMETRY}::test_every_third_party_switch_is_off_at_start_up",
                                        f"{TELEMETRY}::test_no_outgoing_call_carries_trace_context_by_default"),
}
PREFIXED = {
    "FastAPI telemetry ": ("tests/unit/test_posture.py::test_the_app_is_built_with_fastapi_telemetry_off",),
    "Anthropic SDK open_telemetry ": ("tests/unit/test_posture.py::test_the_anthropic_client_is_built_with_its_telemetry_off",),
}
SWITCH = (f"{TELEMETRY}::test_every_third_party_switch_is_off_at_start_up",)

GROUPS = (
    ("Model provider per tier", "Where a model call goes. none: no model is called and every step that could use one "
     "falls back to rules. A tier left unset falls back to the default model, MODEL_PROVIDER.",
     lambda name: name.startswith("MODEL_")),
    ("Tracing", "KGE's own tracing, OpenTelemetry, is the only tracing. off: no span is recorded or exported. "
     "TRACE_PROPAGATE_TO lists the only origins an outgoing call carries trace context to (the traceparent alone, never "
     "tracestate or baggage); none by default, so no model provider, BigQuery, Secret Manager, Cloud Storage or other "
     "third party is sent one.",
     lambda name: name in ("TRACING", "TRACE_PROPAGATE_TO", "OpenTelemetry global propagator")),
    ("Third-party telemetry switches", "Each library's own telemetry, tracing or auto-instrumentation switch, set off "
     "as every process starts, whatever the environment gave it; OpenTelemetry's settings that name a provider class "
     "for it to load (OTEL_PYTHON_*_PROVIDER), or headers for an OTLP exporter to send (OTEL_EXPORTER_OTLP_HEADERS, "
     "OTEL_EXPORTER_OTLP_TRACES_HEADERS), unset, so only KGE's own tracing sets one and adds only its own headers.",
     lambda name: name.upper() == name and not name.startswith("MODEL_") and name not in ("TRACING", "TRACE_PROPAGATE_TO", "EGRESS_ALLOWED")),
    ("Network egress", "The Google APIs a process's outbound traffic is limited to. The image limits none by itself: "
     "the pilot's Terraform sends the service's, the job's and the portal's traffic through a network that reaches only "
     "the APIs it lists and the client's KGE and portal, refuses every other address, and gives the job the list.",
     lambda name: name == "EGRESS_ALLOWED"),
    ("In-code switches", "Telemetry turned off where the client is built, in the code: FastAPI's for the app, and the "
     "Anthropic SDK's for Claude on Vertex AI.",
     lambda name: name.startswith(tuple(PREFIXED))),
)


CLIENT_INFO = ("Client-info headers", "Google's client libraries name themselves, their versions and the runtime on "
               "the calls they make: the x-goog-api-client header, and a User-Agent naming each library.", (
    "The portal: its calls to Google APIs (BigQuery, Vertex AI, Cloud Trace and their token requests) carry no "
    "x-goog-api-client or other x-goog-*-client header, and the portal's own User-Agent, kge-portal; what "
    "authentication needs (authorization, x-goog-user-project, Metadata-Flavor) still passes.",
    "KGE: KGE's Python Google clients still send Google's client-info header (x-goog-api-client) to Google APIs; "
    "tracked for a later kit. Claude on Vertex AI is called with KGE's own User-Agent and none of the Anthropic SDK's "
    "X-Stainless-* headers.",
    "Cloud Trace: KGE's and the portal's trace exporters, where TRACING=cloudtrace, send OpenTelemetry's own "
    "User-Agent (OTel-OTLP-Exporter, its language and version).",
))
"""A statement about the images' outward headers, not a setting: laid out as written, after the settings."""


def proof(name: str) -> tuple[str, ...]:
    """The tests that prove a value: its own, else its prefix's, else, for an upper-case environment switch, the
    start-up test that holds every switch off."""
    if name in PROOFS:
        return PROOFS[name]
    for prefix, tests in PREFIXED.items():
        if name.startswith(prefix):
            return tests
    if name.upper() == name and name not in PROOFS:
        return SWITCH
    raise SystemExit(f"generate_posture: no test proves {name!r}: name one in kit/generate_posture.py")


def cell(text: str) -> str:
    return f"`{text}`".replace("|", "\\|")


def render(version: str, values: dict[str, str]) -> str:
    """The statement, values in the order posture() gives them, grouped; every value in exactly one group."""
    proofs = {name: proof(name) for name in values}
    placed = {name: [g for g, _, member in GROUPS if member(name)] for name in values}
    for name, groups in placed.items():
        if len(groups) != 1:
            raise SystemExit(f"generate_posture: {name!r} falls in {len(groups)} groups, not one")
    refused = [f"{name}: {value}" for name, value in values.items() if value.startswith("refused")]
    if refused:
        raise SystemExit("generate_posture: the image refused its own default: " + "; ".join(refused))
    lines = [f"# Telemetry and model posture, kit {version}", "",
             "The KGE image's effective defaults, read from the image itself as this kit was built, with no setting given. "
             "Generated by the kit's build, never written by hand. A deployment changes a default only by setting it; "
             "the test drive's report, under Telemetry and model posture, gives the same values as an installed job "
             "reads them. Each value names the tests that prove it.", ""]
    for title, about, _ in GROUPS:
        lines += [f"## {title}", "", about, "", "| Setting | Default | Proven by |", "|---|---|---|"]
        lines += [f"| {cell(name)} | {cell(value)} | {', '.join(cell(t) for t in proofs[name])} |"
                  for name, value in values.items() if placed[name] == [title]]
        lines.append("")
    title, about, items = CLIENT_INFO
    lines += [f"## {title}", "", about, "", *(f"- {item}" for item in items), ""]
    return "\n".join(lines)


def main() -> int:
    """--code prints POSTURE, the code build.sh runs in the image; a version reads its JSON and writes the statement."""
    if sys.argv[1:] == ["--code"]:
        print(POSTURE)
        return 0
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_posture.py --code | generate_posture.py <version> < posture.json")
    values = json.load(sys.stdin)
    if not isinstance(values, dict) or not values or not all(isinstance(v, str) for v in values.values()):
        raise SystemExit("generate_posture: expected posture()'s JSON object of strings on standard input")
    sys.stdout.write(render(sys.argv[1], values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
