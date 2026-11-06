"""HTTP onboarding: small synchronous edits and one long-running job per profile.

The estate module remains the authority for DDL and draft transitions.  This
layer authenticates against the graph being served, applies deployment write
policy, and answers for the job the onboard service runs (jobs.py), whose state
a page refresh does not lose. A re-check supersedes the earlier evidence for the
page flow: if it fails or is interrupted, sign-off and release stay blocked.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from kge.services.http import served
from kge.services.onboard import jobs
from kge.services.onboard.service import (
    COMMANDS,
    core,
)
from kge.services.onboard import service
from kge.adapters.objstore import StoreConflict, StoreError
from kge.ports import StateUnavailable

router = APIRouter(prefix="/onboard")
log = logging.getLogger(__name__)
SERVICE_FUNCTIONS = {
    "status": service.status,
    "tables": service.tables,
    "source": service.source,
    "rows": service.add_rows,
    "draft": service.draft,
    "run:checks": service.run,
    "run:improve": service.run,
    "margin": service.margin,
}



class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unit_label(value: str) -> str:
    try:
        return core.validate_unit_label(value)
    except core.OnboardError as exc:
        raise ValueError(str(exc)) from None


UnitLabel = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_unit_label)]


class SourceBody(Body):
    unit: str
    label: UnitLabel
    ddl: str
    rows: Literal["csv", "invented", "none"] | None = None
    csv: dict[str, str] | None = None
    questions: str | None = None
    heldout: str | None = None



class RowsBody(Body):
    unit: str
    rows: Literal["csv", "invented"]
    csv: dict[str, str] | None = None
    questions: str | None = None
    heldout: str | None = None


class UnitBody(Body):
    unit: str


class TransitionBody(Body):
    unit: str | None = None
    proposal: str | None = None
    items: str | list[str] = "all"


class RunBody(Body):
    step: str
    hash: str | None = None
    unit: str | None = None


class FlexibleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class JobRecord(FlexibleResponse):
    unit: str | None = None


class StatusResponse(FlexibleResponse):
    writable: bool
    why: str
    can: dict[str, bool]
    units: list[dict[str, Any]]
    proposal: dict[str, Any] | None
    proposal_errors: list[dict[str, Any]] = Field(default_factory=list)
    job: JobRecord | None
    changes: dict[str, Any] | None = None
    change_lines: list[str] = Field(default_factory=list)
    candidate: dict[str, Any] | None
    current: str | None
    serving: str
    eval_pre: dict[str, Any] | None = None
    eval_post: dict[str, Any] | None = None


class TablesResponse(FlexibleResponse):
    unit: str | None = None
    tables: list[dict[str, Any]] = []


class SourceResponse(FlexibleResponse):
    unit: str | None = None


class RowsResponse(FlexibleResponse):
    unit: str | None = None


class DraftResponse(FlexibleResponse):
    items: list[dict[str, Any]] = []


class JobResponse(FlexibleResponse):
    job: JobRecord


class MarginSet(BaseModel):
    name: str
    result: Literal["passed", "fell", "not checked"]
    summary: dict[str, Any]


class MarginResponse(BaseModel):
    hash: str
    gate: dict[str, Any] | None
    run_id: str | None
    result: Literal["passed", "fell", "not checked", "gate failed"]
    sets: list[MarginSet]
    eval_pre: dict[str, Any] | None = None


def _app():
    """kge.app's state, read through the served accessor when a request is served, so this router never imports the app
    that mounts it; a test puts a stand-in app here."""
    return served


def who(request) -> str:
    return _app().principal(request)


def _principal(request):
    return who(request)


def signed_in(request: Request) -> str:
    return _principal(request)


def _role(who_: str, business: bool = False, steward: bool = False) -> str:
    try:
        allowed = core.permissions(_app().PROFILE, who_, reader=jobs.access_reader(_app()))
    except (StoreError, StateUnavailable) as exc:
        raise _store_failure("check the role", exc) from None
    if business and not allowed["accept"]:
        raise HTTPException(403, f"{who_} is not a business-team principal")
    if steward and not allowed["certify"]:
        raise HTTPException(403, f"{who_} is not a steward whose role performs certification")
    if not business and not steward and not allowed["participant"]:
        raise HTTPException(403, f"{who_} may not onboard a business unit")
    return who_


def business(who_: str = Depends(signed_in)) -> str:
    return _role(who_, business=True)


def steward(who_: str = Depends(signed_in)) -> str:
    return _role(who_, steward=True)


def released_steward(who_: str = Depends(signed_in)) -> str:
    """Rollback is authorised by the release being served, not an uploaded pack."""
    return _role(who_, steward=True)


def participant(who_: str = Depends(signed_in)) -> str:
    try:
        return _role(who_)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        app = _app()
        job = _job(jobs.load, app)
        if job and job.get("hash") and _job(jobs.candidate_permissions, app.PROFILE, job["hash"], who_, jobs.access_reader(app))["participant"]:
            return who_
        raise


def _process(who_: str, process: str) -> None:
    try:
        core.require_process(_app().PROFILE, who_, process, reader=jobs.access_reader(_app()))
    except core.OnboardError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except (StoreError, StateUnavailable) as exc:
        raise _store_failure("check the role", exc) from None


def drafting(who_: str = Depends(signed_in)) -> str:
    _process(who_, "drafting")
    return who_


def building(who_: str = Depends(signed_in)) -> str:
    _process(who_, "building")
    return who_


def improving(who_: str = Depends(signed_in)) -> str:
    _process(who_, "improving")
    return who_


RUN_CHECKS = {"checks": building, "improve": improving, "sign-off": steward, "release": steward, "rollback": released_steward}


def run_principal(body: RunBody, who_: str = Depends(signed_in)) -> str:
    """Check the step's role before the service can refuse its prerequisites."""
    if body.step not in COMMANDS:
        raise HTTPException(422, "step must be improve, checks, sign-off, release or rollback")
    if body.step in {"sign-off", "release"}:
        app = _app()
        job = service_call(jobs.load)
        model_hash = job.get("hash") if job else None
        if model_hash:
            permission = "sign_off" if body.step == "sign-off" else "release"
            if not _job(jobs.candidate_permissions, app.PROFILE, model_hash, who_, jobs.access_reader(app))[permission]:
                raise HTTPException(403, f"{who_} may not sign off {model_hash[:12]}: its roles do not let them certify")
            return who_
    RUN_CHECKS[body.step](who_)
    return who_


def _job(fn, *args):
    """Call the onboard service's job code, a refusal answered as its status and message."""
    try:
        return fn(*args)
    except jobs.JobRefused as exc:
        raise HTTPException(exc.status, exc.message) from None


def service_call(fn, *args):
    try:
        return fn(_app(), *args)
    except core.OnboardError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except jobs.JobRefused as exc:
        raise HTTPException(exc.status, exc.message) from None
    except StoreConflict:
        raise HTTPException(409, "the unit changed by someone else; reload") from None
    except (StoreError, StateUnavailable) as exc:
        raise _store_failure("perform the operation", exc) from None
    except (OSError, UnicodeError, ValueError) as exc:
        log.warning("the onboarding operation was invalid: %s", exc)
        raise HTTPException(422, "the onboarding input could not be processed") from None


def _store_failure(action: str, exc: StoreError | StateUnavailable) -> HTTPException:
    """Log a store's address and return only a stable, public reason: the object store's failure, or app state that
    cannot be signed in to (StateUnavailable, the BigQuery backend's with no credentials or no project)."""
    log.warning("the onboarding store could not %s: %s", action, exc)
    return HTTPException(503, f"the onboarding store could not {action}")


@router.get("", tags=["internal: onboarding"], response_model=StatusResponse, response_model_exclude_unset=True)
def status(who_: str = Depends(participant)):
    """Internal: the onboarding status and current candidate."""
    return service_call(service.status, who_)


@router.post("/tables", tags=["internal: onboarding"], response_model=TablesResponse)
def tables(body: SourceBody, who_: str = Depends(drafting)):
    """Internal: preview the tables of an onboarding source."""
    return service_call(service.tables, body, who_)


@router.post("/source", tags=["internal: onboarding"], response_model=SourceResponse)
def source(body: SourceBody, who_: str = Depends(drafting)):
    """Internal: create an onboarding source."""
    return service_call(service.source, body, who_)


@router.post("/draft", tags=["internal: onboarding"], response_model=DraftResponse)
def draft(body: UnitBody, who_: str = Depends(drafting)):
    """Internal: draft governance items for a source."""
    return service_call(service.draft, body, who_)


@router.post("/rows", tags=["internal: onboarding"], response_model=RowsResponse)
def add_rows(body: RowsBody, who_: str = Depends(drafting)):
    """Internal: add rows to an onboarding source."""
    return service_call(service.add_rows, body, who_)


@router.post("/accept", tags=["internal: governance"])
def accept(body: TransitionBody, who_: str = Depends(business)):
    """Internal: accept selected governance items as the business owner."""
    return service_call(service.accept, body, who_)


@router.post("/certify", tags=["internal: governance"])
def certify(body: TransitionBody, who_: str = Depends(steward)):
    """Internal: certify selected governance items as the steward."""
    return service_call(service.certify, body, who_)


@router.post("/run", tags=["internal: onboarding"], status_code=202, response_model=JobResponse, response_model_exclude_unset=True)
def run(body: RunBody, who_: str = Depends(run_principal)):
    """Internal: start an onboarding job under the step's role check."""
    return service_call(service.run, body, who_)


@router.get("/margin", tags=["internal: onboarding"], response_model=MarginResponse)
def margin(hash: str, who_: str = Depends(building)):
    """Internal: read a candidate's gate and run summary."""
    return service_call(service.margin, hash, who_)


@router.post("/rollback", tags=["internal: onboarding"], status_code=202, response_model=JobResponse, response_model_exclude_unset=True)
def rollback(who_: str = Depends(released_steward)):
    """Internal: roll back to the previous release in the ledger."""
    return service_call(service.run, RunBody(step="rollback"), who_)
