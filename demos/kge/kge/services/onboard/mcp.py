"""MCP adapters for onboarding capabilities that do not require a human decision."""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kge.services.http import mcp_server as capability_server
from kge.services.onboard import routes, service

MCP_UPLOAD_BYTES = 256 * 1024
SERVICE_FUNCTIONS = {
    "onboard_status": service.status,
    "onboard_tables": service.tables,
    "onboard_source": service.source,
    "onboard_rows": service.add_rows,
    "onboard_draft": service.draft,
    "onboard_checks": service.run,
    "onboard_improve": service.run,
    "onboard_margin": service.margin,
}


def _upload_size(ddl: str | None, csv: dict[str, str] | None, questions: str | None, heldout: str | None) -> int:
    return (len((ddl or "").encode()) + sum(len(value.encode()) for value in (csv or {}).values())
            + len((questions or "").encode()) + len((heldout or "").encode()))


def _small(ddl: str | None, csv: dict[str, str] | None, questions: str | None, heldout: str | None) -> None:
    try:
        size = _upload_size(ddl, csv, questions, heldout)
    except UnicodeError:
        raise ToolError("422: source text must contain valid Unicode characters") from None
    if size > MCP_UPLOAD_BYTES:
        raise ToolError("413: the MCP upload is larger than 256 KB; use the HTTP onboarding upload instead (4 MiB request ceiling; 1 MiB CSV and question files in all; 256 KiB DDL)")


def mcp_server(invoke) -> FastMCP:
    server = capability_server("onboard", {
        "onboard_status": routes.participant,
        "onboard_tables": routes.drafting,
        "onboard_source": routes.drafting,
        "onboard_rows": routes.drafting,
        "onboard_draft": routes.drafting,
        "onboard_checks": routes.building,
        "onboard_improve": routes.improving,
        "onboard_margin": routes.building,
    })
    @server.tool(name="onboard_status", structured_output=True, meta={"route": "GET /onboard", "tags": ["internal: onboarding"], "role": "participant"})
    async def onboard_status(ctx: Context) -> routes.StatusResponse:
        """The onboarding page's current status. As GET /onboard."""
        return await invoke(ctx, lambda who: routes.service_call(service.status, who), routes.participant)

    @server.tool(name="onboard_tables", structured_output=True, meta={"route": "POST /onboard/tables", "tags": ["internal: onboarding"], "role": "drafting"})
    async def onboard_tables(unit: str, label: routes.UnitLabel, ddl: str, ctx: Context,
                             rows: Literal["csv", "invented", "none"] | None = None,
                             csv: dict[str, str] | None = None, questions: str | None = None,
                             heldout: str | None = None) -> routes.TablesResponse:
        """Preview tables for a source. As POST /onboard/tables."""
        _small(ddl, csv, questions, heldout)
        body = routes.SourceBody(unit=unit, label=label, ddl=ddl, rows=rows, csv=csv, questions=questions, heldout=heldout)
        return await invoke(ctx, lambda who: routes.service_call(service.tables, body, who), routes.drafting)

    @server.tool(name="onboard_source", structured_output=True, meta={"route": "POST /onboard/source", "tags": ["internal: onboarding"], "role": "drafting"})
    async def onboard_source(unit: str, label: routes.UnitLabel, ddl: str, ctx: Context,
                             rows: Literal["csv", "invented", "none"] | None = None,
                             csv: dict[str, str] | None = None, questions: str | None = None,
                             heldout: str | None = None) -> routes.SourceResponse:
        """Create an onboarding source. As POST /onboard/source."""
        _small(ddl, csv, questions, heldout)
        body = routes.SourceBody(unit=unit, label=label, ddl=ddl, rows=rows, csv=csv, questions=questions, heldout=heldout)
        return await invoke(ctx, lambda who: routes.service_call(service.source, body, who), routes.drafting)

    @server.tool(name="onboard_rows", structured_output=True, meta={"route": "POST /onboard/rows", "tags": ["internal: onboarding"], "role": "drafting"})
    async def onboard_rows(unit: str, rows: Literal["csv", "invented"], ctx: Context,
                           csv: dict[str, str] | None = None, questions: str | None = None,
                           heldout: str | None = None) -> routes.RowsResponse:
        """Add rows to an onboarding source. As POST /onboard/rows."""
        _small(None, csv, questions, heldout)
        body = routes.RowsBody(unit=unit, rows=rows, csv=csv, questions=questions, heldout=heldout)
        return await invoke(ctx, lambda who: routes.service_call(service.add_rows, body, who), routes.drafting)

    @server.tool(name="onboard_draft", structured_output=True, meta={"route": "POST /onboard/draft", "tags": ["internal: onboarding"], "role": "drafting"})
    async def onboard_draft(unit: str, ctx: Context) -> routes.DraftResponse:
        """Draft a source's governance items. As POST /onboard/draft."""
        return await invoke(ctx, lambda who: routes.service_call(service.draft, routes.UnitBody(unit=unit), who), routes.drafting)

    async def run_step(step: str, ctx: Context, hash: str | None, unit: str | None = None) -> routes.JobResponse:
        body = routes.RunBody(step=step, hash=hash, unit=unit)
        return await invoke(ctx, lambda who: routes.service_call(service.run, body, who), routes.RUN_CHECKS[step])

    @server.tool(name="onboard_checks", structured_output=True,
                 meta={"route": "POST /onboard/run", "tags": ["internal: onboarding"], "step": "checks", "role": "building"})
    async def onboard_checks(ctx: Context, hash: str | None = None) -> routes.JobResponse:
        """Start the candidate build and checks. As POST /onboard/run with step checks."""
        return await run_step("checks", ctx, hash)

    @server.tool(name="onboard_improve", structured_output=True,
                 meta={"route": "POST /onboard/run", "tags": ["internal: onboarding"], "step": "improve", "role": "improving"})
    async def onboard_improve(ctx: Context, hash: str | None = None,
                              unit: str | None = None) -> routes.JobResponse:
        """Start an improvement run. As POST /onboard/run with step improve."""
        return await run_step("improve", ctx, hash, unit)

    @server.tool(name="onboard_margin", structured_output=True,
                 meta={"route": "GET /onboard/margin", "tags": ["internal: onboarding"], "role": "building"})
    async def onboard_margin(hash: str, ctx: Context) -> routes.MarginResponse:
        """A candidate's gate and run summary. As GET /onboard/margin."""
        return await invoke(ctx, lambda who: routes.service_call(service.margin, hash, who), routes.building)

    return server
