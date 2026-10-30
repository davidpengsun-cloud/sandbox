"""An installed-client diagnostic that leaves a released starting baseline."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

import yaml

from kge.adapters.objstore import ObjectStore, StoreError, open_store
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.branding import SAMPLE as SAMPLE_BRAND, display_name
from kge.pipeline.settings import ROOT, settings

CONFIG = yaml.safe_load((ROOT / "kge/services/onboard/test_drive.yaml").read_text())
SAMPLE = SAMPLE_BRAND["client"]
PEOPLE = " ".join(CONFIG["people"])
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
    identities = kept.get(f"deploy/{client()}/identities.yaml")
    if identities is not None:
        expected_people = {tuple(person.split(":")) for person in CONFIG["people"]}
        try:
            saved = yaml.safe_load(identities)
            principals = saved.pop("principals")
            matches = (
                saved == {"client": client(), "asker": CONFIG["asker"]}
                and isinstance(principals, list)
                and all(set(person) == {"principal", "role"} for person in principals)
                and {(person["principal"], person["role"]) for person in principals} == expected_people
            )
        except (yaml.YAMLError, AttributeError, TypeError, KeyError):
            matches = False
        if not matches or installed is None:
            raise Refused("existing identities are not the sample setup: the test drive never replaces the client's people")
    if kept.get(f"work/{client()}.tar.gz") is not None:
        raise Refused(f"the store already holds work/{client()}.tar.gz: remove the existing data before a test drive")
    for prefix in (f"releases/{client()}/", f"clients/{client()}/", f"landing/{client()}/"):
        if kept.list(prefix):
            raise Refused(f"the store already holds {prefix}: remove the existing data before a test drive")
    landing = (settings().LANDING_URL or "").strip()
    if landing and open_store(landing).list():
        raise Refused("the landing store already holds data: remove the existing data before a test drive")


def secret_values() -> list[str]:
    names = [*SECRET_NAMES, *(f"MODEL_{tier}_API_KEY" for tier in ("HIGH", "MEDIUM", "LOW"))]
    return [os.getenv(name, "") for name in names if os.getenv(name, "")]


def filtered(value: object) -> str:
    text = str(value)
    for secret in secret_values():
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(https?://)([^/@\s]+@)?([^/\s:]+)(?::\d+)?[^\s]*", r"\1\3", text)
    text = re.sub(r"gs://[^/\s]+", "gs://[bucket]", text)
    return text


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
