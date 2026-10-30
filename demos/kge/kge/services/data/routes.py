import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from kge.services.data import service
from kge.services.data.models import ExportResponse, OwnQuery, ProductExport, ProductRows, RowsResponse
from kge.services.http import needs, served

router = APIRouter(prefix="/data")
querying = needs("querying")


def service_call(call, *args):
    try:
        return call(served.engine(), *args)
    except service.DataRefused as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.post("/preview", tags=["external: query"], response_model=RowsResponse, response_model_exclude_unset=True)
def preview(body: ProductRows, who: str = Depends(querying)) -> RowsResponse:
    """Preview the first rows of a governed product the signed-in person may read."""
    return service_call(service.preview, who, body.unit, body.table, body.limit)


@router.post("/export", tags=["external: query"], response_model=ExportResponse, response_model_exclude_unset=True,
             responses={200: {"content": {"text/csv": {}}}})
def export(body: ProductExport, who: str = Depends(querying)) -> Response:
    """Export a capped CSV of a governed product the signed-in person may read."""
    result = service_call(service.export, who, body.unit, body.table)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=result.columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(result.model_dump(mode="json")["rows"])
    headers = {"Content-Disposition": f'attachment; filename="{result.filename}"'}
    if result.row_count >= result.capped_at:
        headers.update({"X-Export-Capped-At": str(result.capped_at),
                        "X-Export-Note": f"Export limited to {result.capped_at} rows; it may be incomplete."})
    return Response(output.getvalue(), media_type="text/csv", headers=headers)


@router.post("/query", tags=["external: query"], response_model=RowsResponse, response_model_exclude_unset=True)
def query(body: OwnQuery, who: str = Depends(querying)) -> RowsResponse:
    """Run the signed-in person's read-only SQL against one readable unit, with a row cap."""
    return service_call(service.query, who, body.unit, body.sql)
