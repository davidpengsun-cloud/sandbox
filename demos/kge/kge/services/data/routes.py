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
    """Run the signed-in person's read-only SQL against one readable unit, returning at most 1,000 rows.

    The SQL gate allows the query only when every rule below holds. It is within the complexity limit:
    32,768 characters, 4,096 tokens, 128 OR or UNION terms, 32 parenthesis levels and 192 syntax-tree
    levels, checked within 5 seconds. One statement, and it reads:
    SELECT or WITH ... SELECT, nothing that writes. A second statement is refused. Every table is in
    this unit's warehouse. A CTE or subquery may not borrow a warehouse table's name, and a source may
    not rename its columns by position. Name every column: SELECT * is refused, as are COLUMNS(...) and
    #n, and a whole row. Each named column must be on the unit's allowlist. Restricted personal data is
    refused. Joins are inner joins only. JOIN ON is a conjunction (AND) of one column equal to another
    (table.column = table.column): OR, a constant or any other expression in ON is refused. Two tables
    meet only on a declared join, and a composite declared join needs every column pair together. A
    declared join that carries conditions of its own is used with those conditions, including its version
    window. Server-information functions and memory-expanding functions are refused. The warehouse then
    dry-runs the query and runs it only when that plan is accepted.
    """
    return service_call(service.query, who, body.unit, body.sql)
