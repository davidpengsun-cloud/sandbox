"""Onboard one business unit from safe DDL, for both commands and the API.

All pack-state I/O goes through estate.packstore, the object-store seam.  The
warehouse is derived data in the profile's estate and is created only from the
validated model; uploaded SQL itself is never executed.

Each kind of drafted item fills one sub-graph (ITEM_SUB_GRAPH): a mapping to a
preloaded industry concept is the ontology's, a candidate restricted-data policy
(with its mapping to a data-privacy concept) governance's. The pilot has no live
source connection yet (D1): rows come from CSVs or the labelled DDL fallback.
The checked proposal (PROPOSAL) sits beside the unit's inputs, outside
semantic/, where uploads land. CLIENT_MARKER, in a client's pack folder, says
make client made it and make reset-client may remove it.

Onboarding is continuous: the knowledge engineer sends a live unit's sources again,
by the same command or page upload as the first time. What differs from the
live inputs is staged as the unit's next version under units/<unit>/update/
(its inputs, proposal and draft), and nothing there is merged into the pack
yet. Its draft is compared with the live draft item by item, and only what
changed (added, changed, or a proposed removal) needs accepting and certifying
again. build lands it over the live files once every such item is certified;
the checks and a steward's sign-off then gate its release, as for a new unit,
and the previous release stays current until then.

A client's reference figure is bounded by FIGURE_LIMIT, which no ledger
reaches and which sits far enough below Decimal's 28-digit context that
quantizing and formatting it is cheap; its query gets CLIENT_SQL_SECONDS over
one unit's rows, since a longer one is a self-join blow-up, not a figure.

This CLI has no authentication boundary: --as is a self-asserted principal,
used to exercise the same role checks as the authenticated API. A refusal
(including a refused semantic folder, a state backend that cannot be signed in
to, or a model setting that names no model) is one line, never a traceback.

The package holds one module per step: common.py (what every step shares), source.py (tables, create, rows),
figures.py (a client's reference figures), drafting.py (draft), review.py (accept, certify), status.py (show,
status, record-onboard), units.py (build, reset), clients.py (client, reset-client) and cli.py (the command line,
run as python -m kge.estate.onboard).
"""
from __future__ import annotations

from kge.estate.onboard.common import (
    CLIENT_MARKER,
    HEADER,
    ITEM_SUB_GRAPH,
    MANIFEST_FORMAT,
    NEEDS_ROWS,
    PROPOSAL,
    SUB_GRAPHS,
    UPDATE,
    OnboardError,
    _manifest,
    _pack,
    bump_manifest,
    ensure_warehouse,
    permissions,
    require_process,
    require_improve_unit,
    writable,
)
from kge.estate.onboard.figures import FIGURE_LIMIT, ClientFigureError, _client_figure, _golden_allowlist
from kge.estate.onboard.source import create, rows, tables, validate_question_bytes, validate_unit_label
from kge.estate.onboard.drafting import draft
from kge.estate.onboard.review import accept, certify, require_no_pending_proposal
from kge.estate.onboard.status import record_onboard, record_stage, show, status_lines
from kge.estate.onboard.units import _land_update, build, reset
from kge.estate.onboard.clients import client, reset_client
from kge.estate.onboard.cli import main

__all__ = ["CLIENT_MARKER", "HEADER", "ITEM_SUB_GRAPH", "MANIFEST_FORMAT", "NEEDS_ROWS", "PROPOSAL", "SUB_GRAPHS",
           "UPDATE", "OnboardError", "_manifest", "_pack", "bump_manifest", "ensure_warehouse", "permissions",
           "writable", "require_process", "require_improve_unit", "FIGURE_LIMIT", "ClientFigureError", "_client_figure", "_golden_allowlist", "create", "rows", "tables",
           "require_no_pending_proposal", "validate_unit_label", "validate_question_bytes", "draft", "accept", "certify", "record_onboard", "record_stage", "show",
           "status_lines", "_land_update", "build", "reset", "client", "reset_client", "main"]
