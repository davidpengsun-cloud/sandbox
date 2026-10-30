"""The onboarding job: one long-running job per profile, its state kept where a page refresh still finds it (the app-state
store, the object store, or a file in the profile's run folder), the checks a step passes before it starts, and the
thread that runs its command and records what the command printed.

Every function takes the app (its PROFILE, engine and switch), which the routes pass in. A refusal is a JobRefused,
its status and a message safe to show; the routes turn it into the HTTP answer. A job this process runs is in active
from before its first save until after its last, and load reads active before the job file, so a job that starts or
finishes while it is read is never taken for one the process no longer runs."""
from __future__ import annotations

import io
import json
import logging
import os
import secrets
import subprocess
import sys
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from kge.adapters import appstate
from kge.adapters.objstore import StoreError
from kge.graph.load import Unloadable, read_artifact
from kge.graph.release import state
from kge.pipeline.objstore import HASH as STORE_HASH, Refused, fetch, pull_config, pull_work, released_identity, the_store
from kge.pipeline.settings import ROOT, settings
from kge.services.onboard.service import (
    COMMANDS,
    HASH,
    IMPROVE_LINE,
    IMPROVE_STEPS,
    JOB_STALE_SECONDS,
    PROGRESS,
    RunClient,
    RunError,
    V_LINE,
    _cloud,
    _stale,
    core,
    now,
)

log = logging.getLogger(__name__)

locks: dict[str, threading.Lock] = {}
processes: dict[str, subprocess.Popen] = {}
active: set[str] = set()
guard = threading.Lock()


class JobRefused(Exception):
    """A job that cannot start or be read, with the HTTP status the routes answer and a message safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def store_failure(action: str, exc: Exception) -> JobRefused:
    """Log a store's address and return only a stable, public reason."""
    log.warning("the onboarding store could not %s: %s", action, exc)
    return JobRefused(503, f"the onboarding store could not {action}")


def job_dir(app) -> Path:
    return app.PROFILE.run_dir() / "onboard"


def job_path(app) -> Path:
    return job_dir(app) / "job.json"


def job_backend(app, action: str):
    """The app state that keeps the job's record (None where it is a file), its construction's failure the same safe 503
    as a failed read or write: a misconfigured or unreachable backend never escapes as a 500."""
    try:
        return appstate.job_backend(app.PROFILE)
    except (RuntimeError, ValueError, OSError) as exc:
        raise store_failure(action, exc) from None


def save(app, job: dict) -> None:
    kept = job_backend(app, "save the onboarding job state")
    if kept is not None:
        try:
            kept.save_job(job)
        except (RuntimeError, ValueError, OSError) as exc:
            raise store_failure("save the onboarding job state", exc) from None
        return
    if (settings().STORE_URL or "").strip():
        the_store().put(f"clients/{app.PROFILE.client}/onboard/job.json",
                        (json.dumps(job, indent=2) + "\n").encode())
        return
    root = job_dir(app); root.mkdir(parents=True, exist_ok=True)
    path, part = job_path(app), root / f".job.{os.getpid()}.{secrets.token_hex(4)}"
    part.write_text(json.dumps(job, indent=2) + "\n")
    os.replace(part, path)


def load(app) -> dict | None:
    """The onboarding job's state, or None. A store that cannot be read is a failure, not "no job": a job may be
    running that this request cannot see. A local job file that says running for a job this process does not run is
    interrupted, the process that ran it gone; which jobs run is read before the file, so a job that finishes while it is
    read (its last save, then its id let go) is never taken for one."""
    kept = job_backend(app, "read the onboarding job state")
    if kept is not None:
        try:
            job = kept.job()
        except (RuntimeError, ValueError, OSError) as exc:
            raise store_failure("read the onboarding job state", exc) from None
    elif (settings().STORE_URL or "").strip():
        raw = the_store().get(f"clients/{app.PROFILE.client}/onboard/job.json")
        try:
            job = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            return None
    else:
        running = set(active)
        try:
            job = json.loads(job_path(app).read_text())
        except (OSError, ValueError):
            return None
        if job.get("state") == "running" and job.get("id") not in running:
            job.update(state="interrupted", finished_at=now(), message="the process ended before the job finished")
            save(app, job)
    if job and job.get("state") == "running" and not job.get("progress") and _stale(job):
        job.update(state="stale", finished_at=now(),
                   message=f"the job has exceeded {JOB_STALE_SECONDS // 60} minutes; it may be superseded")
        save(app, job)
    return job


def current_profile(app):
    """The profile the page shows: on Cloud Run, the configuration and work the store holds, pulled first, under the
    guard a switch to a release holds (follow_release), so no poll rewrites build/ while another opens the release."""
    prof = app.PROFILE
    if _cloud():
        try:
            with guard:
                prof = pull_config(the_store(), ROOT)
                pull_work(the_store(), ROOT, prof)
        except StoreError as exc:
            raise store_failure("load the current state", exc) from None
    return prof


FETCHED: dict[str, str | None] = {}


def follow_release(app, prof, job: dict | None) -> None:
    """On Cloud Run, a release the job finished is served here too: the store's current release is fetched and switched
    to once, under the guard, when it is not the one this process serves, by its identity (hash, graph generation and
    chunks table, Profile.identity, as the release record names them), so a release of the same hash at a new
    generation is followed too. Where the store holds no identity for it, or one that cannot be read (Grok m1 on #97),
    a release of the hash this process serves is fetched and compared by the identity its release record names, never
    by the hash alone, so a service never stays on a generation the next releases' prunes drop; once per release job
    (FETCHED, by client: the job last fetched and followed for, recorded only once any switch is done, so a failed
    switch is tried again on the next poll), never again on each status poll."""
    if not (_cloud() and job and job.get("step") == "release" and job.get("state") == "done"):
        return
    store = the_store()

    def moved(released: str, named: dict | None) -> bool:
        engine = app.engine()
        return named != engine.identity if named is not None else released != engine.profile.model_hash()
    try:
        released = (store.get(f"releases/{prof.client}/current") or b"").decode().strip()
        if not STORE_HASH.fullmatch(released):
            return
        named = released_identity(store, prof, released)
        if named is None and not moved(released, None):
            with guard:
                if FETCHED.get(prof.client) == job.get("id"):
                    return
                fetch(store, ROOT, prof)
                if prof.serve().at(released).identity() != app.engine().identity:
                    app.switch()
                FETCHED[prof.client] = job.get("id")
            return
        if moved(released, named):
            with guard:
                if moved(released, named):
                    fetch(store, ROOT, prof)
                    app.switch()
    except (Refused, StoreError, OSError, UnicodeError, ValueError) as exc:
        raise store_failure("load the released graph", exc) from None


def stored_candidate(app, model_hash: str) -> bool:
    """Whether the job's persisted work contains this candidate artifact.

    The service and job do not share a filesystem.  This is only the page's
    preflight; the job pulls the archive and applies the release gate again.
    """
    data = the_store().get(f"work/{app.PROFILE.client}.tar.gz")
    if data is None:
        return False
    name = f"build/artifacts/{app.PROFILE.client}/{model_hash}/manifest.json"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            return name in archive.getnames()
    except (OSError, tarfile.TarError):
        return False


def candidate(app, job: dict | None) -> tuple[str | None, dict | None]:
    model_hash = job.get("hash") if job else None
    if not model_hash:
        return None, None
    try:
        return model_hash, state(app.PROFILE, model_hash)
    except (OSError, ValueError):
        return model_hash, None


def shown_candidate(app, prof, job: dict | None, proposal: dict | None) -> tuple[str | None, dict | None]:
    """The candidate the page shows: the job's, or else the open proposal's."""
    model_hash, found = candidate(app, job)
    if not model_hash and proposal and proposal.get("candidate_hash"):
        model_hash = proposal["candidate_hash"]
        try:
            found = state(prof, model_hash)
        except (OSError, ValueError):
            found = None
    return model_hash, found


def run(app, job: dict, command: list[str]) -> None:
    """Run command as job, recording its progress. A hash printed before a later load or margin failure is not a
    checked candidate, so it cannot inherit an older RELEASED marker and turn the page's action into a rollback."""
    root = job_dir(app); log_file = root / f"{job['id']}.log"
    env = {**os.environ, "PROFILE": app.PROFILE.id}
    if hasattr(app.PROFILE, "registry") and app.PROFILE.registry:
        env["PROFILES"] = str(app.PROFILE.registry)
    try:
        with log_file.open("w") as stream:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, text=True)
            processes[job["id"]] = proc
            code = proc.wait()
        lines = log_file.read_text(errors="replace").splitlines()
        if job["step"] == "improve":
            progress = [
                {"n": IMPROVE_STEPS.index(m.group(1)) + 1, "of": len(IMPROVE_STEPS), "name": f"{m.group(1)}: {m.group(2)}", "state": "done"}
                for line in lines if (m := IMPROVE_LINE.match(line))
            ]
        else:
            progress = [{"n": int(m.group(1)), "of": 7, "name": m.group(2), "state": "done"}
                        for line in lines if (m := PROGRESS.search(line))]
        found = next((m.group(1) for line in lines if (m := HASH.search(line))), job.get("hash"))
        if job["step"] == "improve" and code == 0:
            try:
                shown = core.show(app.PROFILE)
                prop = shown.get("proposal")
                if prop and prop.get("candidate_hash"):
                    found = prop["candidate_hash"]
                    gate = state(app.PROFILE, found).get("gate") or {}
                    job["gate"] = {**gate, "lines": prop.get("gate_lines") or [line for line in lines if V_LINE.match(line)]}
                    job["report"] = prop.get("report")
            except Exception:
                pass
        job.update(state="done" if code == 0 else "failed", finished_at=now(), progress=progress, hash=found,
                   message=lines[-1] if lines else f"command exited {code}", log_tail="\n".join(lines[-40:]))
        if job["step"] == "checks" and found:
            try:
                gate = state(app.PROFILE, found).get("gate") or {}
            except (OSError, ValueError):
                gate = {}
            job["gate"] = {**gate, "lines": [line for line in lines if V_LINE.match(line)]}
            run_id = gate.get("run_id")
            job["report"] = f"/ab/{run_id}/report.html" if run_id else None
            if code == 0 and gate.get("passed") is True:
                job["checked_hash"] = found
                job["checks_id"] = job["id"]
        if code == 0 and job["step"] == "release":
            app.switch()
        save(app, job)
    except BaseException as exc:
        job.update(state="error", finished_at=now(), message=str(exc), log_tail="")
        save(app, job)
    finally:
        processes.pop(job["id"], None)
        active.discard(job["id"])


def start(app, step: str, wanted_hash: str | None, who: str, shown_data: dict, *, unit: str | None = None) -> dict:
    """Start step as the profile's one job, after the checks its candidate must pass: the response the page reads.
    shown_data is the onboarding state the routes read for a checks run, else empty."""
    cloud = _cloud()
    stored = bool((settings().STORE_URL or "").strip())
    with guard:
        lock = locks.setdefault(app.PROFILE.id, threading.Lock())
    with lock:
        previous = load(app)
        if previous and previous.get("state") == "running":
            raise JobRefused(409, "an onboarding job is already running for this profile")
        model_hash, _ = candidate(app, previous)
        checked_hash = previous.get("checked_hash") if previous else None
        checks_id = previous.get("checks_id") if previous else None
        is_rollback = False
        if step == "release" and wanted_hash:
            if not STORE_HASH.fullmatch(wanted_hash):
                raise JobRefused(422, "hash must be a 64-character lower-case hexadecimal hash")
            cand_state = state(app.PROFILE, wanted_hash)
            if cand_state.get("released") or wanted_hash == app.PROFILE.model_hash():
                from kge.graph.history import check_rollback_allowed
                from kge.graph.release import Unreleasable
                try:
                    check_rollback_allowed(app.PROFILE, wanted_hash, app.PROFILE.model_hash())
                except Unreleasable as exc:
                    raise JobRefused(409, str(exc)) from None
                is_rollback = True
                model_hash = wanted_hash
            else:
                model_hash = wanted_hash
        if step == "improve":
            core.require_no_pending_proposal(app.PROFILE)
            model_hash = checked_hash = checks_id = None
        elif step != "checks" and not is_rollback:
            if not model_hash:
                raise JobRefused(409, "run checks before sign-off or release")
            if checked_hash != model_hash or not checks_id:
                raise JobRefused(409, "run checks for this candidate before sign-off or release")
        if step == "release":
            if stored:
                try:
                    loadable = stored_candidate(app, model_hash)
                except StoreError as exc:
                    raise store_failure("validate the checked candidate", exc) from None
                if not loadable:
                    raise JobRefused(409, "the checked candidate is no longer available")
            else:
                folder = app.PROFILE.artifacts / app.PROFILE.client / model_hash
                try:
                    manifest = read_artifact(folder, app.PROFILE.client)
                except (OSError, Unloadable):
                    raise JobRefused(409, "the checked candidate is no longer available") from None
                if manifest.get("model_hash") != model_hash:
                    raise JobRefused(409, "the checked candidate is no longer available")
        command = [part.format(who=who, hash=model_hash or "") for part in COMMANDS[step]]
        if step == "improve" and unit is not None:
            command.extend(["--unit", unit, "--as", who])
        if step == "checks":
            if wanted_hash is not None:
                if not STORE_HASH.fullmatch(wanted_hash):
                    raise JobRefused(422, "hash must be a 64-character lower-case hexadecimal hash")
                known = {app.PROFILE.model_hash(), model_hash,
                         (shown_data.get("proposal") or {}).get("candidate_hash")}
                if wanted_hash not in known:
                    raise JobRefused(404, "candidate was not found")
            checked_hash = checks_id = None
            prop = shown_data.get("proposal")
            unit_items = [it for u in shown_data.get("units", []) for it in u.get("items", [])]
            if not stored and prop and prop.get("candidate_hash") and all(it.get("state") == "certified" for it in unit_items):
                cand_hash = prop["candidate_hash"]
                try:
                    if state(app.PROFILE, cand_hash).get("loaded"):
                        model_hash = cand_hash
                        baseline_arg = str(getattr(app.PROFILE, "baseline", "") or "")
                        command = [sys.executable, "-m", "kge.eval.margin", "--repeats", "1", "--baseline", baseline_arg, "--hash", cand_hash]
                except (OSError, ValueError):
                    pass
        job = {"id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3), "step": step,
               "by": who, "state": "running", "started_at": now(), "finished_at": None, "progress": [],
               "hash": model_hash, "checked_hash": checked_hash, "checks_id": checks_id, "gate": None,
               "message": "started", "report": None, "log_tail": ""}
        if step == "improve" and unit is not None:
            job["unit"] = unit
        if not stored:
            active.add(job["id"])
        try:
            save(app, job)
        except BaseException:
            active.discard(job["id"])
            raise
        if stored:
            request = {"id": job["id"], "command": {"checks": "onboard"}.get(step, step),
                       "principal": who, "hash": model_hash, "timestamp": job["started_at"],
                       "checked_hash": checked_hash, "checks_id": checks_id}
            if unit is not None:
                request["unit"] = unit
            try:
                request_store = the_store()
                request_key = f"clients/{app.PROFILE.client}/onboard/request.json"
                request_store.put(request_key, (json.dumps(request, indent=2) + "\n").encode())
                _, request_version = request_store.get_version(request_key)
            except StoreError as exc:
                public = store_failure("write the request", exc)
                job.update(state="failed", finished_at=now(), message=public.message)
                save(app, job)
                raise public from None
            if cloud:
                try:
                    job["execution"] = RunClient().run()
                    save(app, job)
                except RunError as exc:
                    try:
                        request_store.put_version(request_key, b"", request_version)
                    except StoreError:
                        log.warning("the failed job request could not be cleared")
                    job.update(state="failed", finished_at=now(), message=str(exc))
                    save(app, job)
                    raise JobRefused(409, str(exc)) from None
                except StoreError as exc:
                    public = store_failure("record the job execution", exc)
                    job.update(state="failed", finished_at=now(), message=public.message)
                    raise public from None
            else:
                requested = request["command"]
                shown = "docker compose run --rm job onboard-request"
                return {"job": job, "command": shown,
                        "said": f"Under Docker Compose run: {shown} (request: {requested})"}
        else:
            try:
                threading.Thread(target=run, args=(app, job, command), daemon=True).start()
            except BaseException:
                active.discard(job["id"])
                raise
    return {"job": job}
