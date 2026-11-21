"""An installed-client diagnostic that leaves a released starting baseline."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

import yaml
from opentelemetry import propagate

from kge.adapters.model.providers import TIERS
from kge.adapters.objstore import ObjectStore, StoreError, open_store
from kge.adapters.tracing import TracingSettingError, destinations, mode
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.branding import SAMPLE as SAMPLE_BRAND, display_name
from kge.pipeline.settings import ROOT, settings
from kge.pipeline.telemetry import ANTHROPIC_TELEMETRY_OFF, CLEARED, FASTAPI_TELEMETRY_OFF, NO_PROPAGATION, SWITCHED_OFF

CONFIG = yaml.safe_load((ROOT / "kge/services/onboard/test_drive.yaml").read_text())
SAMPLE = SAMPLE_BRAND["client"]
SECRET_NAMES = ("MODEL_API_KEY", "SESSION_SECRET", "SIGNIN_PASSWORDS", "TAGGER_API_KEY", "PORTAL_SYNC_TOKEN")


def client() -> str:
    return (settings().PROFILE or "").strip()


class Refused(RuntimeError):
    """A test drive that cannot safely start."""


@dataclass
class Result:
    name: str
    result: str
    duration: float = 0.0
    exercised: list[str] = field(default_factory=list)
    detail: str = ""
    what_to_do: str = ""


def store() -> ObjectStore:
    url = (settings().STORE_URL or "").strip()
    if not url:
        raise Refused("STORE_URL is unset: set the job's object store")
    return open_store(url)


def guard(kept: ObjectStore) -> None:
    if os.getenv("CONFIRM") != "test-drive":
        raise Refused("CONFIRM must be test-drive")
    if not CLIENT_ID.fullmatch(client()):
        raise Refused(f"PROFILE must name the install client: {CLIENT_ID_RULE}")
    display_name(client(), settings().NAME)
    if (settings().CLIENT or "").strip():
        raise Refused("test-drive takes no CLIENT setting")
    installed = None
    raw = kept.get("deploy/profiles.yaml")
    if raw is not None:
        try:
            profiles = yaml.safe_load(raw)["profiles"]
            if not isinstance(profiles, dict):
                raise ValueError("profiles is not a mapping")
            installed = profiles.get(client())
            if installed is not None and installed.get("client") != client():
                raise ValueError("the profile names a different client")
        except (yaml.YAMLError, AttributeError, TypeError, KeyError, ValueError):
            raise Refused("cannot read deploy/profiles.yaml for this client: repair the registry") from None
    if kept.get(f"work/{client()}.tar.gz") is not None:
        raise Refused(f"the store already holds work/{client()}.tar.gz: run teardown CONFIRM={client()} PEOPLE=remove before a fresh test drive")
    for prefix in (f"releases/{client()}/", f"clients/{client()}/", f"landing/{client()}/"):
        if kept.list(prefix):
            raise Refused(f"the store already holds {prefix}: run teardown CONFIRM={client()} PEOPLE=remove before a fresh test drive")
    landing = (settings().LANDING_URL or "").strip()
    if landing and open_store(landing).list():
        raise Refused(f"the landing store already holds data: run teardown CONFIRM={client()} PEOPLE=remove before a fresh test drive")


def secret_values() -> list[str]:
    names = [*SECRET_NAMES, *(f"MODEL_{tier}_API_KEY" for tier in ("HIGH", "MEDIUM", "LOW"))]
    return [os.getenv(name, "") for name in names if os.getenv(name, "")]


def filtered(value: object) -> str:
    """value with every secret's value redacted, an http or https address cut to its scheme, host and port, and a bucket's
    name hidden. An address ends at whitespace or a double quote, so the report's JSON, filtered whole, stays JSON."""
    text = str(value)
    for secret in secret_values():
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(https?://)([^/@\s\"]+@)?([^/\s:\"]+)(:\d+)?[^\s\"]*", r"\1\3\4", text)
    text = re.sub(r"gs://[^/\s\"]+", "gs://[bucket]", text)
    return text


def posture() -> dict[str, str]:
    """What this process would send to a third party, as values alone (no key, token or secret): the model provider of
    the default model (MODEL_PROVIDER) and of each tier (MODEL_<TIER>_PROVIDER, unset: the agents on it fall back as
    model_for says), the tracing mode (TRACING), the origins trace context is sent to (TRACE_PROPAGATE_TO), each SDK
    telemetry switch as this process's environment holds it, each OpenTelemetry provider setting telemetry.py unsets
    (CLEARED), whether OpenTelemetry's global propagator is the no-op,
    the in-code switches FastAPI and the Anthropic SDK are built with, and the Google APIs this process's network egress
    is limited to (EGRESS_ALLOWED, which the pilot's Terraform gives with the network that enforces it). The kit's
    posture statement is this, read in the image with no setting given; the test drive's report is this, read in the
    installed job. A setting that does not parse is reported as refused, with the reason, never raised."""
    def flag(value: object) -> str:
        return "off" if value is False else "on" if value is True else str(value)

    default = (settings().MODEL_PROVIDER or "").strip().lower() or "none"
    shown = {"MODEL_PROVIDER": default}
    own = {t.upper(): os.getenv(f"MODEL_{t.upper()}_PROVIDER", "").strip().lower() for t in TIERS if t != "default"}
    for tier, provider in own.items():
        fallback = f"falls back to MODEL_PROVIDER ({default})"
        if tier == "HIGH" and own.get("MEDIUM"):
            fallback = f"context and assist fall back to MODEL_MEDIUM_PROVIDER ({own['MEDIUM']}), the others to MODEL_PROVIDER ({default})"
        shown[f"MODEL_{tier}_PROVIDER"] = provider or f"unset: {fallback}"
    for name, read in (("TRACING", mode), ("TRACE_PROPAGATE_TO", lambda: ", ".join(sorted(destinations())) or "none")):
        try:
            shown[name] = read()
        except TracingSettingError as exc:
            shown[name] = f"refused: {exc}"
    allowed = sorted({host.strip() for host in (settings().EGRESS_ALLOWED or "").split(",") if host.strip()})
    shown["EGRESS_ALLOWED"] = (", ".join(allowed) + ", and the client's KGE and portal: every other address refused"
                               if allowed else "unset: no egress limit given to this process; the pilot's Terraform gives one")
    for name in SWITCHED_OFF:
        shown[name] = os.environ.get(name, "unset")
    for name in CLEARED:
        shown[name] = os.environ.get(name, "unset")
    textmap = propagate.get_global_textmap()
    shown["OpenTelemetry global propagator"] = "no-op" if textmap is NO_PROPAGATION else f"not the no-op: {type(textmap).__name__}"
    for key, value in FASTAPI_TELEMETRY_OFF.items():
        shown[f"FastAPI telemetry {key}"] = flag(value)
    for key, value in ANTHROPIC_TELEMETRY_OFF.items():
        shown[f"Anthropic SDK open_telemetry {key}"] = flag(value)
    return shown


def main() -> int:
    try:
        kept = store()
        guard(kept)
    except (Refused, StoreError, ValueError) as exc:
        print(f"test-drive: refused: {filtered(exc)}", file=sys.stderr)
        return 2
    from kge.services.onboard.test_drive_run import execute
    return execute(kept)


if __name__ == "__main__":
    raise SystemExit(main())
