"""The quality service's routes: the golden sets, the A/B harness's runs and reports, and the eval."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from kge.services.http import needs, served
from kge.services.quality import service

router = APIRouter()


@router.get("/golden", tags=["internal: quality"])
def golden():
    """Internal: the profile's golden sets, the page's sample questions: each set's name, version and questions, as text alone;
    never an expectation, a figure or SQL, and never the held-out set; and the samples the page shows first, the
    questions the default arm answered as expected in the profile's A/B baseline, and the misses it marks as known
    misses, those the baseline records the default arm failing (kge/eval/sets.py golden)."""
    result = {"sets": service.golden(served.PROFILE, served.engine(), served.DEFAULT_ARM)}
    if served.SERVE_HASH:
        result["preview"] = {"hash": served.SERVE_HASH, "released": served.SERVE_RELEASED,
                             "banner": f"Artifact {served.SERVE_HASH}, released" if served.SERVE_RELEASED else f"Candidate {served.SERVE_HASH}, not released"}
    return result


@router.get("/ab", tags=["internal: quality"])
def ab():
    """Internal: the A/B harness's runs (make ab, with the API stopped), newest first: what ran each arm, and where its report is;
    and the profile's A/B baseline, by its file's name, or None for a profile with none (a run is then reported alone)."""
    return service.ab(served.PROFILE)


@router.get("/ab/{run_id}/report.html", tags=["internal: quality"])
def ab_report(run_id: str):
    """Internal: one run's report, as kge/eval/report.py wrote it from the run's records."""
    found = service.report(served.PROFILE, run_id)
    if found is None:
        raise HTTPException(404, f"no A/B run {run_id}; make ab writes one (stop the API first)")
    return FileResponse(found, media_type="text/html")


@router.post("/eval", tags=["internal: quality"])
def run_eval(who: str = Depends(needs("querying"))):
    """Internal: run the profile's evaluation as the caller."""
    return service.evaluate(served.PROFILE, served.engine(), who)
