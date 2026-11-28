"""Prove a subprocess belongs to an authorized, still-active server job before binding its model caller."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from kge.adapters.model import Caller, QuotaUnavailable
from kge.adapters.objstore import open_store, under
from kge.pipeline import access_table
from kge.pipeline.access import may_improve_unit
from kge.pipeline.settings import settings
from kge.ports import StoreError

JOB_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{40,64}")
CAP_SECONDS = 185 * 60
MARKER = "KGE_QUOTA_JOB_CAP"


def _store(prof):
    url = (settings().STORE_URL or "").strip()
    if url:
        return open_store(under(url, "clients", prof.client, "onboard", "model-quota"))
    return open_store(str(prof.run_dir() / "onboard" / "model-quota"), private=True)


def issue(prof, job_id: str, *, clock=time.time) -> str:
    """Issue an unguessable subprocess capability; only its digest is kept in the server-owned job area."""
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise QuotaUnavailable("The model request limit cannot verify the job.")
    secret = secrets.token_urlsafe(32)
    try:
        job = _job(prof, job_id)
        data = {"digest": hashlib.sha256(secret.encode()).hexdigest(), "binding": _binding(job),
                "expires": int(clock()) + CAP_SECONDS}
        _store(prof).put_version(job_id + ".json", json.dumps(data).encode(), "0", retry_ambiguous=False)
    except (StoreError, ValueError, OSError, RuntimeError, TypeError, KeyError, RecursionError):
        raise QuotaUnavailable("The model request limit cannot verify the job.") from None
    return f"{job_id}.{secret}"


def revoke(prof, marker: str) -> None:
    job_id, _, _ = marker.partition(".")
    if JOB_ID.fullmatch(job_id):
        _store(prof).delete(job_id + ".json")


def _job(prof, job_id: str) -> dict:
    """Read the server-owned job request, never an identity supplied by the subprocess environment."""
    if (settings().STORE_URL or "").strip():
        from kge.adapters import appstate
        store = open_store(settings().STORE_URL)
        raw = store.get(f"clients/{prof.client}/onboard/request.json")
        record = json.loads(raw) if raw else None
        if not isinstance(record, dict) or record.get("id") != job_id:
            raise ValueError("job not found")
        backend = appstate.job_backend(prof)
        if backend is None:
            stored = store.get(f"clients/{prof.client}/onboard/job.json")
            active = json.loads(stored) if stored else None
        else:
            active = backend.job()
        step = {"onboard": "checks"}.get(record.get("command"), record.get("command"))
        if (not isinstance(active, dict) or active.get("id") != job_id or active.get("state") != "running"
                or active.get("by") != record.get("principal") or active.get("step") != step
                or active.get("started_at") != record.get("timestamp")):
            raise ValueError("job not active")
        return {"id": record["id"], "who": record.get("principal"), "step": record.get("command"),
                "unit": record.get("unit"), "hash": record.get("hash"), "started": record.get("timestamp")}
    path = Path(prof.run_dir()) / "onboard" / "job.json"
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or record.get("id") != job_id or record.get("state") != "running":
        raise ValueError("job not found")
    return {"id": record["id"], "who": record.get("by"), "step": record.get("step"),
            "unit": record.get("unit"), "hash": record.get("hash"), "started": record.get("started_at")}


def _binding(job: dict) -> str:
    """Bind the capability to immutable server-owned job data, including its subject and requested action."""
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def caller_for_job(prof, marker: str, *, clock=time.time) -> Caller:
    """Verify the job capability and current access; no identity or roles are taken from the marker."""
    job_id, dot, secret = marker.partition(".") if isinstance(marker, str) else ("", "", "")
    if not dot or not JOB_ID.fullmatch(job_id) or not TOKEN.fullmatch(secret):
        raise QuotaUnavailable("The model request limit cannot verify the job.")
    try:
        raw = _store(prof).get(job_id + ".json")
        cap = json.loads(raw) if raw else None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if (not isinstance(cap, dict) or set(cap) != {"digest", "binding", "expires"} or not isinstance(cap["digest"], str)
                or not isinstance(cap["binding"], str)
                or not isinstance(cap["expires"], int) or isinstance(cap["expires"], bool) or cap["expires"] <= clock()
                or not hmac.compare_digest(cap["digest"], digest)):
            raise ValueError("invalid capability")
        job = _job(prof, job_id)
        if not hmac.compare_digest(cap["binding"], _binding(job)):
            raise ValueError("job changed")
        started = datetime.strptime(job.get("started") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        if started > clock() + 60 or clock() - started >= CAP_SECONDS:
            raise ValueError("job expired")
        who = job.get("who")
        if not isinstance(who, str) or access_table.check_username(who):
            raise ValueError("invalid principal")
        who = access_table.normalize_username(who)
        reader = access_table.Access.for_profile(prof)
        if who not in reader.people():
            raise ValueError("access revoked")
        role_ids = reader.roles_of(who)
        definitions = access_table.served_roles(prof)
        effective = reader.role_of_person(who, definitions)
        permission = reader.permissions(who, definitions)
        if job.get("step") in ("sign-off", "release") and job.get("hash"):
            from kge.pipeline.roles import candidate_permissions
            permission = candidate_permissions(prof, job["hash"], who, reader=reader)
        action = {"checks": "run", "onboard": "run", "eval": "run", "improve": "improve",
                  "sign-off": "sign_off", "release": "release", "rollback": "release"}.get(job.get("step"))
        if action is None or not permission.get(action):
            raise ValueError("access revoked")
        if job.get("unit") is not None and (job.get("step") != "improve" or not may_improve_unit(effective, job["unit"])):
            raise ValueError("access revoked")
        return Caller(prof.client, "person", who, tuple(sorted(role_ids)))
    except (StoreError, OSError, RuntimeError, ValueError, TypeError, KeyError, RecursionError):
        raise QuotaUnavailable("The model request limit cannot verify the job.") from None


def service_caller(prof) -> Caller:
    """The standalone CLI's caller is the operating-system user, never an environment-provided name."""
    return Caller(prof.client, "service", f"cli-uid-{os.geteuid()}")
