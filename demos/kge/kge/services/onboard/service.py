"""The onboard service's logic that needs no request: the onboarding core (kge/estate/onboard/, loaded when first
used), the job's commands and the lines it reads from their output, a drafted item and GET /onboard's answer as the page
shows them, and the client that starts the onboarding job on Cloud Run."""
from __future__ import annotations

import importlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kge.pipeline.onboard_steps import ONBOARD_STEPS
from kge.pipeline.settings import settings



class _LazyCore:
    """Keep the API layer importable without pulling the estate layer into every runtime."""

    def __getattr__(self, name):
        return getattr(importlib.import_module("kge.estate.onboard"), name)


core = _LazyCore()
DDL_LIMIT = 256 * 1024
MAX_UPLOAD_BYTES = 1024 * 1024
JOB_STALE_SECONDS = 185 * 60
HASH = re.compile(r"onboard: candidate ([0-9a-f]{64})")
V_LINE = re.compile(r"^  V0[1-5] ")
PROGRESS = re.compile(rf"onboard: \[(\d+)/{ONBOARD_STEPS}\] (\S+)")
IMPROVE_LINE = re.compile(r"^(gather|diagnose|propose|build|test|keep|judge|hand_over)\b[^:]*: (.*)")
IMPROVE_STEPS = ("gather", "diagnose", "propose", "build", "test", "keep", "judge", "hand_over")
COMMANDS = {
    "improve": [sys.executable, "-m", "kge.improve.flow"],
    "checks": [sys.executable, "-m", "kge.cli", "onboard", "REPEATS=1", f"PY={sys.executable}"],
    "sign-off": [sys.executable, "-m", "kge.graph.release", "--sign-off", "--as", "{who}", "--hash", "{hash}"],
    "rollback": [sys.executable, "-m", "kge.graph.release", "--previous"],
    "release": [sys.executable, "-m", "kge.graph.release", "--hash", "{hash}"],
}
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"


def _upload(body) -> None:
    csv_bytes = sum(len(value.encode()) for value in (body.csv or {}).values())
    question_bytes = len((body.questions or "").encode())
    heldout_bytes = len((body.heldout or "").encode())
    if csv_bytes > MAX_UPLOAD_BYTES:
        raise core.OnboardError(413, "CSV files are larger than 1 MB in all")
    if csv_bytes + question_bytes + heldout_bytes > MAX_UPLOAD_BYTES:
        raise core.OnboardError(413, "uploaded CSV and question files are larger than 1 MB in all")
    for label, text in (("questions", body.questions), ("heldout", body.heldout)):
        if text is not None:
            core.validate_question_bytes(text.encode(), label)


def _ddl(body) -> None:
    if len(body.ddl.encode()) > DDL_LIMIT:
        raise core.OnboardError(413, "DDL is larger than 256 KB")
    _upload(body)


def _writeable(prof) -> None:
    ok, why = core.writable(prof)
    if not ok:
        raise core.OnboardError(409, why)


def _edit(app, operation, *args, **kwargs):
    from kge.services.onboard import jobs
    with jobs.editing(app):
        _writeable(app.PROFILE)
        return operation(app.PROFILE, *args, **kwargs)


def status(app, who: str) -> dict:
    from kge.services.onboard import jobs
    prof = jobs.current_profile(app)
    writable = core.writable(prof)
    job = jobs.load(app)
    jobs.follow_release(app, prof, job)
    return page_status(app, prof, who, writable, job, core.show(prof))


def tables(app, body, who: str) -> dict:
    _ddl(body)
    return core.tables(app.PROFILE, body.unit, label=body.label, rows=body.rows,
                       csv={k: v.encode() for k, v in (body.csv or {}).items()} or None, ddl_text=body.ddl)


def source(app, body, who: str) -> dict:
    _ddl(body)
    made = _edit(app, core.create, body.unit, label=body.label, rows=body.rows,
                       csv={k: v.encode() for k, v in (body.csv or {}).items()} or None, ddl_text=body.ddl, as_=who,
                       questions=body.questions.encode() if body.questions else None,
                       heldout=body.heldout.encode() if body.heldout else None)
    return {key: value for key, value in made.items() if key != "warehouse"}


def add_rows(app, body, who: str) -> dict:
    if body.rows == "csv" and not body.csv:
        raise core.OnboardError(422, "rows csv needs CSV files")
    _upload(body)
    return _edit(app, core.rows, body.unit, body.rows,
                     csv={k: v.encode() for k, v in (body.csv or {}).items()} or None, as_=who,
                     questions=body.questions.encode() if body.questions else None,
                     heldout=body.heldout.encode() if body.heldout else None)


def draft(app, body, who: str) -> dict:
    return page_items(_edit(app, core.draft, body.unit, as_=who))


def accept(app, body, who: str) -> dict:
    return page_items(_edit(app, core.accept, body.unit, item=body.items, as_=who, proposal=body.proposal))


def certify(app, body, who: str) -> dict:
    return page_items(_edit(app, core.certify, body.unit, item=body.items, as_=who, proposal=body.proposal))


def run(app, body, who: str) -> dict:
    from kge.services.onboard import jobs
    if body.step not in COMMANDS:
        raise core.OnboardError(422, "step must be improve, checks, sign-off, release or rollback")
    if body.step == "rollback" and body.hash is not None:
        raise core.OnboardError(422, "rollback uses the previous release, not a hash")
    _writeable(app.PROFILE)
    shown = core.show(app.PROFILE) if body.step == "checks" else {}
    if body.unit is not None:
        if body.step != "improve":
            raise core.OnboardError(422, "unit is only supported for improve")
        core.require_improve_unit(app.PROFILE, body.unit, who)
    if body.step == "improve":
        core.require_no_pending_proposal(app.PROFILE)
    if body.step == "checks":
        empty = next((unit for unit in shown.get("units", []) if unit.get("rows", {}).get("origin") == "none"), None)
        if empty:
            raise core.OnboardError(409, core.NEEDS_ROWS.format(unit=empty["unit"]))
    options = {"unit": body.unit} if body.unit is not None else {}
    return jobs.start(app, body.step, body.hash, who, shown, **options)


def margin(app, model_hash: str, who: str) -> dict:
    return margin_report(app.PROFILE, model_hash)


def margin_report(prof, model_hash: str) -> dict[str, Any]:
    """A candidate's post-load gate and the set summaries from the run it names."""
    from kge.graph.release import GATE, marked
    from kge.adapters import released
    if not re.fullmatch(r"[0-9a-f]{64}", model_hash):
        raise core.OnboardError(404, f"candidate {model_hash} was not found")
    graph = prof.at(model_hash).graph_path()
    folder = graph.parent
    pre = released.pre_evaluation(prof.at(model_hash))
    if not folder.exists() and pre is None:
        raise core.OnboardError(404, f"candidate {model_hash} was not found")
    gate = marked(graph, GATE)
    if pre is not None and (gate or {}).get("run_id") != pre["manifest"]["run_id"]:
        gate = {"passed": pre["passed"], "run_id": pre["manifest"]["run_id"], "model_hash": model_hash}
    run_id = gate.get("run_id") if gate else None
    summary_path = Path(prof.run_dir()) / "runs" / str(run_id) / "summary.json" if run_id else None
    summary: dict[str, Any] | None = pre.get("summary") if pre else None
    if summary_path is not None and summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text())
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, UnicodeError, ValueError):
            summary = None
    verdict = ("passed" if gate and gate.get("passed") is True else
               "gate failed" if gate and run_id is None else "fell" if gate else "not checked")
    sets = []
    for name, result in sorted((summary or {}).get("sets", {}).items()):
        outcome = (gate.get("sets") or {}).get(name) if gate else None
        if outcome is None and gate and gate.get("passed") is True:
            outcome = "passed"
        outcome = outcome or "not checked"
        sets.append({"name": name, "result": outcome, "summary": result})
    return {"hash": model_hash, "gate": gate, "run_id": run_id, "result": verdict, "sets": sets,
            "eval_pre": ({k: pre[k] for k in ("passed", "report", "model_graded", "must_pass")} if pre else None)}


def page_item(raw: dict) -> dict:
    """A drafted item as the page shows it: its record's fields and the detail its kind gives."""
    pack = raw.get("pack") or {}
    kind = raw.get("kind")
    if kind == "source":
        source = (pack.get("sources") or [{}])[0]
        detail = {"tables": raw.get("tables", []), "no_join": pack.get("no_join", []),
                  "period_bindings": pack.get("period_bindings", []), "aliases": pack.get("terminology.aliases", []),
                  "entity_key": source.get("entity_key")}
    elif kind == "term":
        glossary, measure, mapping = (pack.get("glossary") or [{}])[0], (pack.get("measures") or [{}])[0], (pack.get("mappings") or [{}])[0]
        detail = {"term": glossary.get("id"), "name": glossary.get("name"), "definition": glossary.get("definition"),
                  "measure": measure.get("expression"), "column": mapping.get("column"), "currency": measure.get("currency"),
                  "period": measure.get("calendar"), "aliases": pack.get("terminology.aliases", [])}
    elif kind == "alias":
        phrase = raw.get("phrase") or ""
        term = raw.get("term") or ""
        term_name = raw.get("term_name") or term
        detail = {
            "section": raw.get("section") or "terminology.aliases",
            "phrase": phrase,
            "term": term,
            "term_name": term_name,
            "evidence": raw.get("evidence"),
            "evidence_column": raw.get("evidence_column"),
            "why": raw.get("why"),
            "proposer": raw.get("proposer"),
            "items": raw.get("items") or [],
            "questions": raw.get("questions") or [],
            "also_answers": raw.get("also_answers") or [],
        }
        return {
            **raw,
            "title": raw.get("title") or f"{term_name} ({term}) ← '{phrase}'",
            "from": raw.get("from") or f"improve job · {raw.get('proposer') or 'rules'}",
            "detail": detail,
        }
    elif kind == "mapping":
        detail = {k: raw.get(k) for k in ("term", "concept", "iri", "relation", "standard", "standard_version")}
    elif kind == "policy":
        policy = (pack.get("policies.restricted_attributes") or [{}])[0]
        mapping = (pack.get("ontology_mappings") or [{}])[0]
        detail = {"column": raw.get("column"), "family": raw.get("family"), "policy": policy.get("id"),
                  "iri": mapping.get("iri"), "relation": mapping.get("relation")}
    else:
        detail = {k: raw.get(k) for k in ("question", "sql", "set", "terms")}
        detail["figure"] = (raw.get("expect") or {}).get("figure")
    return {k: raw.get(k) for k in ("id", "kind", "title", "from", "state", "made_by", "model",
                                      "accepted_by", "accepted_name", "accepted_as", "accepted_on",
                                      "certified_by", "certified_name", "certified_as", "certified_on", "change")} | {"detail": detail}


def page_items(result: dict) -> dict:
    """An onboarding answer with each of its items as the page shows it."""
    if "items" in result:
        result = {**result, "items": [page_item(x) for x in result["items"]]}
    return result


def change_lines(record: dict | None) -> list[str]:
    """Plain-English schema differences for the steward; the page escapes these as text."""
    def value(raw):
        return "none" if raw is None else str(raw)

    def columns(names):
        return "(" + ", ".join(names) + ")" if names else "none"

    def foreign(key):
        return f"{columns(key['columns'])} referencing {key['ref_table']} {columns(key['ref_columns'])}"

    lines = []
    for change in ((record or {}).get("lock") or {}).get("changes", []):
        table = f"{change['source']}.{change['table']}"
        column = f"{table}.{change.get('column')}"
        before, after = change.get("was"), change.get("now")
        was, now = value(before), value(after)
        if change["change"] == "key-changed":
            before, after = before or {}, after or {}
            if before.get("primary_key") != after.get("primary_key"):
                lines.append(f"Primary key of {table} changed from {columns(before.get('primary_key'))} to {columns(after.get('primary_key'))}")
            old, new = before.get("foreign_keys", []), after.get("foreign_keys", [])
            lines.extend(f"Foreign key of {table} {foreign(key)} removed" for key in old if key not in new)
            lines.extend(f"Foreign key of {table} {foreign(key)} added" for key in new if key not in old)
            if old != new and all(key in new for key in old) and all(key in old for key in new):
                lines.append(f"Foreign keys of {table} reordered from {'; '.join(map(foreign, old))} to {'; '.join(map(foreign, new))}")
            continue
        if change["change"] in {"column-added", "column-removed"}:
            was, now = (value(raw.get("type")) if isinstance(raw, dict) else value(raw) for raw in (before, after))
        lines.append({
            "table-added": f"Table {table} added", "table-removed": f"Table {table} removed",
            "column-added": f"Column {column} added ({now})", "column-removed": f"Column {column} removed (was {was})",
            "column-retyped": f"Column {column} retyped from {was} to {now}",
            "column-described": f"Description of column {column} changed from {was} to {now}",
            "column-moved": f"Column {column} moved from position {was} to {now}",
        }.get(change["change"], f"Change to {table}: {change['change']}"))
    for doc in ((record or {}).get("estate") or {}).get("unread_documents", []):
        lines.append(f"{doc['path']}: {doc['evidence']}")
    inputs = (record or {}).get("inputs") or {}
    lines.extend(f"Ignored: {path}" for path in inputs.get("ignored", []))
    if suggestion := inputs.get("suggestion"):
        lines.append(f"Declarations to add\n{suggestion}")
    if upload := inputs.get("upload"):
        lines.append(f"Upload {upload.get('sha256', '')[:12]}")
    if (record or {}).get("rechecked"):
        lines.append("Checked again: the last check did not pass")
    return lines


def page_status(app, prof, who: str, writable: tuple[bool, str], job: dict | None, shown: dict) -> dict:
    """GET /onboard's answer: the units and proposal with their items as the page shows them, what the principal may
    do, the graph's versions, the job and its candidate, and the graph current and served."""
    from kge.graph.history import list_versions
    from kge.services.onboard import jobs
    ok, why = writable
    raw_prop = shown.get("proposal")
    proposal = {**raw_prop, "items": [page_item(x) for x in raw_prop.get("items", [])]} if raw_prop else None
    model_hash, candidate = jobs.shown_candidate(app, prof, job, proposal)
    if job and job.get("step") == "improve":
        job = dict(job)
        for field in ("message", "log_tail"):
            if isinstance(job.get(field), str):
                job[field] = re.sub(
                    r"(?:improve: )?no A/B rules run on the served release ([0-9a-f]{64}): "
                    r"stop the API and run make margin HASH=\1 first",
                    "Run the checks first: this release has no margin yet.", job[field])
    changes = None
    stored = bool((settings().STORE_URL or "").strip())
    awaiting = jobs.awaiting_changes(job, model_hash) if stored else candidate and candidate.get("loaded") and not candidate.get("released")
    if job:
        no_change = "onboard: current already" in f"{job.get('message', '')}\n{job.get('log_tail', '')}"
        awaiting = (awaiting and not no_change and job.get("step") != "rollback"
                    and not (job.get("step") == "checks" and job.get("state") != "done")
                    and not (job.get("step") == "release" and job.get("state") == "done"))
    if awaiting:
        changes = (job or {}).get("changes") if stored else candidate.get("changes")
    if candidate and "changes" in candidate:
        candidate = {**candidate, "changes": changes}
    if job and "changes" in job:
        job = {**job, "changes": changes}
    current = prof.model_hash()
    from kge.adapters import released
    post = released.post_evaluation(prof.serve()) if current else None
    if post is not None:
        post = {k: value for k, value in post.items() if k != "records"}
    pre = released.pre_evaluation(prof.at(model_hash)) if model_hash else None
    units = [{**unit, "items": [page_item(item) for item in unit.get("items", [])]} for unit in shown.get("units", [])]
    can = core.permissions(prof, who, ok, reader=jobs.access_reader(app))
    can["rollback"] = can["certify"]
    if model_hash and awaiting:
        candidate_can = jobs.candidate_permissions(prof, model_hash, who, jobs.access_reader(app))
        can = {**can, **{key: ok and candidate_can[key] for key in ("sign_off", "release")}}
    return {"writable": ok, "why": why, "can": can, "units": units,
            "proposal": proposal, "notes": shown.get("notes", []), "requests": shown.get("requests", []),
            "proposal_errors": shown.get("proposal_errors", []),
            "versions": list_versions(prof).get("versions", []),
            "changes": changes, "change_lines": change_lines(changes),
            "job": job, "candidate": ({"hash": model_hash, **(candidate or {})} if model_hash else None),
            "current": current, "serving": app.engine().profile.model_hash(), "eval_post": post,
            "eval_pre": ({"passed": pre["passed"], "run_id": pre["manifest"]["run_id"],
                          "report": pre["report"]} if pre else None)}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale(job: dict) -> bool:
    try:
        started = datetime.strptime(job["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() >= JOB_STALE_SECONDS


class RunError(RuntimeError):
    """A job API failure safe to show on the onboarding page."""


class RunClient:
    """The v2 Jobs API, signed with the service's application default credentials."""

    def __init__(self):
        self.base = (settings().CLOUD_RUN_API or "https://run.googleapis.com").rstrip("/")
        self.credentials = None

    def headers(self) -> dict[str, str]:
        if (settings().CLOUD_RUN_API or "").strip():
            return {"Content-Type": "application/json"}
        try:
            if self.credentials is None:
                import google.auth
                self.credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM])
            if not self.credentials.valid:
                from google.auth.transport.requests import Request
                self.credentials.refresh(Request())
        except Exception as exc:
            raise RunError(f"no Google Cloud credentials for the Cloud Run job: {type(exc).__name__}") from None
        return {"Authorization": f"Bearer {self.credentials.token}", "Content-Type": "application/json"}

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}/v2/{path}", method=method, data=data, headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise RunError(f"job service returned HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RunError(f"job service request failed: {type(getattr(exc, 'reason', exc)).__name__}") from None

    def run(self) -> str:
        project = (settings().VERTEX_PROJECT or "").strip()
        region = (settings().RUN_REGION or "").strip()
        name = (settings().K_SERVICE or "").strip()
        if not project or not region or not name:
            raise RunError("the job is not configured: VERTEX_PROJECT, RUN_REGION and K_SERVICE are required")
        job = f"projects/{project}/locations/{region}/jobs/{name}"
        operation = self.request("POST", f"{job}:run", {})
        operation_name = operation.get("name")
        if not operation_name:
            raise RunError("job service returned no operation name")
        return operation_name.rsplit("/", 1)[-1]


def _cloud() -> bool:
    current = settings()
    return all((getattr(current, name) or "").strip() for name in ("K_SERVICE", "VERTEX_PROJECT", "RUN_REGION"))
