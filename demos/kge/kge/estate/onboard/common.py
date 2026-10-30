"""What every onboarding step shares: the names and formats of a unit's files, the refusal (OnboardError), roles
and the write check, a unit's manifest and history, landing a step's writes, and the unit's warehouse, reached
through the estate's warehouse port (_estate, the factory's), never a backend."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import yaml

from kge.compiler import pack as packs, standards as preloaded
from kge.estate import ddl, semantic
from kge import packstore
from kge.adapters import appstate
from kge.adapters.warehouse import open_estate
from kge.pipeline import access, warehouse_versions
from kge.adapters.objstore import StoreConflict


HEADER = "# Machine-written by onboarding; edit nothing by hand.\n"
MANIFEST_FORMAT = 2
SUB_GRAPHS = ("physical", "glossary", "metrics", "ontology", "documents", "governance")
ITEM_SUB_GRAPH = {"source": "physical", "term": "glossary", "question": "metrics", "mapping": "ontology", "policy": "governance"}
CLIENT_MARKER = ".made-by-client.json"
NEEDS_ROWS = ("unit {unit} has no rows: onboarding stops at the drafted pack. The checks need its rows: "
              "CSVs beside the DDL (one <table>.csv per table), or invented rows from the DDL "
              "(ROWS=invented), labelled invented")
log = logging.getLogger(__package__)
PROPOSAL = "proposal.json"
UPDATE = "update"


def _semantic_files(store, unit: str, inputs: dict | None = None, update: bool = False) -> dict[str, bytes]:
    """The unit's kept semantic files, as create read them from the folder. With inputs (a manifest's), only the files
    it names: an update that dropped a file may leave it behind for a moment, and it must not be read back."""
    prefix = f"units/{unit}/{UPDATE + '/' if update else ''}semantic/"
    files = {name.removeprefix(prefix): name for name in store.list(prefix)}
    if inputs is not None:
        files = {name: path for name, path in files.items() if f"semantic/{name}" in inputs}
    return {name: store.read(path) for name, path in files.items()}


def _parsed(store, unit: str, files: dict[str, bytes], update: bool = False) -> dict:
    """The semantic files' proposal: the one cached at create when it still matches the files and passes its checks
    again (kge/estate/semantic.py restore), so drafting and build never need a live model; else the files parsed afresh."""
    try:
        held = store.read(_state(store, unit, f"{UPDATE}/{PROPOSAL}" if update else PROPOSAL))
    except FileNotFoundError:
        held = None
    restored = semantic.restore(held, files, unit) if held is not None else None
    return restored if restored is not None else semantic.parse(files, unit)


def _proposal_json(value):
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode()}
    raise TypeError(type(value).__name__)


class OnboardError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


def writable(prof) -> tuple[bool, str]:
    """A pilot writes only through its configured store; the demo keeps its local behaviour."""
    if os.getenv("K_SERVICE") and not os.getenv("STORE_URL", "").strip():
        return False, "this pilot has no STORE_URL; configure its object store before onboarding"
    root = prof.run_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".onboard-probe-", dir=root)
        os.close(fd)
        Path(name).unlink()
        return True, ""
    except OSError as exc:
        return False, f"this deployment's pack is read-only: {exc}"


def _pack(prof, without_unit: str | None = None):
    """The client's pack, read from the store itself, since API workers must not depend on the mirror entrypoint's
    `before serve` normally makes. without_unit leaves out, on purpose, another unit's question naming one of its terms,
    which is checked where it matters, as an open request on the update that would remove the term."""
    store = packstore.for_profile(prof)
    for name in ("pack.yaml", "pack.lock"):
        if os.getenv("STORE_URL", "").strip():
            try:
                store.read(name)
            except FileNotFoundError:
                if name == "pack.yaml":
                    raise
    found = packs.read(prof.clients, prof.client, lock=False, without_unit=without_unit)
    findings = found.findings
    if without_unit:
        findings = [f for f in findings if f.message != "question names an unknown drafted term"]
    if findings:
        raise OnboardError(409, "; ".join(map(str, findings)))
    return found.data


def _roles(prof) -> dict[str, dict]:
    p = _pack(prof)
    roles = {r["id"]: r for r in (p.get("access") or {}).get("roles", [])}
    try:
        identities = yaml.safe_load(prof.identities.read_text())["principals"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("the deployment's identities cannot be read: %s", exc)
        raise OnboardError(409, "the deployment's identities cannot be read") from None
    return {i["principal"]: roles.get(i["role"], {}) for i in identities}


def permissions(prof, who: str, writable_: bool = True) -> dict[str, bool]:
    """Every onboarding action the principal may take, from the command core's pack roles."""
    return access.permissions(_roles(prof).get(who, {}), writable_)


def require_process(prof, who: str, process: str) -> None:
    if not access.has_process(_roles(prof).get(who, {}), process):
        raise OnboardError(403, f"{who} is not {'an' if process[:1] in 'aeiou' else 'a'} {process} identity")


def require_improve_unit(prof, unit: str, who: str | None = None) -> None:
    """Validate a selected unit before starting any improvement work."""
    if not ddl.UNIT.fullmatch(unit):
        raise OnboardError(422, "unit is not valid")
    if unit not in {source["id"] for source in _pack(prof).get("sources", [])}:
        raise OnboardError(404, f"unknown unit: {unit}")
    if who is not None and not access.may_improve_unit(_roles(prof).get(who, {}), unit):
        raise OnboardError(403, f"{who} may not improve unit {unit}")


def _business(prof, who: str) -> None:
    if not permissions(prof, who)["accept"]:
        raise OnboardError(403, f"{who} is not a business-team principal")


def _steward(prof, who: str) -> None:
    if not permissions(prof, who)["certify"]:
        raise OnboardError(403, f"{who} is not a steward whose role performs certification")


def _state(store, unit: str, name: str) -> str:
    return f"units/{unit}/{name}"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest(prof, store, unit: str) -> dict:
    """Read the unit manifest, upgrading the old creation record in memory."""
    raw = appstate.for_profile(prof, store).manifest(unit)
    if raw.get("format") == MANIFEST_FORMAT:
        return raw
    if raw.get("format") != 1:
        raise OnboardError(409, f"unit {unit} has an unsupported manifest format")
    inputs = {raw.get("ddl", "schema.sql"): raw.get("ddl_sha256")}
    inputs.update({f"data/{name}.csv": digest for name, digest in (raw.get("rows") or {}).get("files", {}).items()})
    inputs.update({f"semantic/{name}": digest for name, digest in (raw.get("semantic") or {}).get("files", {}).items()})
    return {**raw, "format": MANIFEST_FORMAT, "version": 1,
            "inputs": {name: digest for name, digest in inputs.items() if digest},
            "items": {name: [] for name in SUB_GRAPHS}, "standards": [],
            "golden": {"name": f"golden_{unit}", "version": "v1"}, "release": None,
            "requests": []}


def bump_manifest(manifest: dict) -> dict:
    """Return an accepted-update revision; the update flow will call this later."""
    return {**manifest, "format": MANIFEST_FORMAT, "version": int(manifest.get("version", 1)) + 1}


def _history(prof, store, unit: str) -> list[dict]:
    return appstate.for_profile(prof, store).history(unit)


def _history_entry(by: str, step: str, version: int, detail) -> dict:
    return {"at": _now(), "by": by, "step": step, "version": version, "detail": detail}


def _request_id(unit: str, sub_graph: str, source: str, ask: str) -> str:
    seed = f"{unit}\0{sub_graph}\0{source}\0{ask}".encode()
    return hashlib.sha256(seed).hexdigest()[:16]


def _requests(prof, store, unit: str, manifest: dict, record: dict | None) -> list[dict]:
    """The unit's open requests. A term with no adequate match among the preloaded industry concepts asks the steward
    to confirm there is none, or to name the nearest, until a mapping of the term is certified; and of the asserted
    certification blocks landing ignored, only the unit's own documents are requests here."""
    requests = []
    update = manifest.get("update") or {}
    if update.get("drafted") and (record or {}).get("update"):
        manifest = {**manifest, "rows": update["rows"], "table_rows": update["table_rows"]}
    meta_rows = manifest.get("rows") or {}
    if meta_rows.get("origin") == "none":
        ask = f"Please provide rows for {unit}'s tables."
        requests.append(("physical", ask, "schema.sql"))
    else:
        for table, count in sorted((manifest.get("table_rows") or {}).items()):
            if count == 0:
                requests.append(("physical", f"Please provide rows for table {table}.", table))
    items = (record or {}).get("items", [])
    mapped = {x.get("term") for x in items if x.get("kind") == "mapping" and x.get("certified_by")}
    for gap in (record or {}).get("not_drafted", []):
        if gap.get("sub_graph") == "ontology":
            if gap.get("term") not in mapped:
                requests.append(("ontology", str(gap["ask"]), str(gap["term"])))
            continue
        source = str(gap.get("metric") or gap.get("column") or gap.get("alias") or gap.get("document") or
                     gap.get("source") or gap.get("golden") or "draft")
        why = str(gap.get("why") or "it could not be drafted")
        if gap.get("ask"):
            ask = str(gap["ask"]); graph = "metrics"
        elif "definition" in why:
            ask = f"Please provide a definition for {source}."; graph = "glossary"
        elif "currency" in why:
            ask = f"Please provide the currency for {source}."; graph = "metrics"
        else:
            ask = (f"Define {source} as plain conditions, or confirm it isn't needed."
                   if any(word in why for word in ("condition", "filter", "subquer", "function", "column", "literal", "OR"))
                   else f"Please clarify {source}: {why}.")
            graph = "metrics"
        requests.append((graph, ask, source))
    for item in items:
        if item.get("kind") != "term":
            continue
        glossary = ((item.get("pack") or {}).get("glossary") or [{}])[0]
        measure = ((item.get("pack") or {}).get("measures") or [{}])[0]
        if not str(glossary.get("definition") or "").strip():
            source = str(glossary.get("id") or item.get("id"))
            requests.append(("glossary", f"Please provide a definition for {source}.", source))
        if not str(measure.get("currency") or "").strip():
            source = str(measure.get("id") or item.get("id"))
            requests.append(("metrics", f"Please provide the currency for {source}.", source))
    term_ids = {item["id"] for item in items if item.get("kind") == "term"}
    questioned = {term for item in items if item.get("kind") == "question" for term in item.get("terms", [])}
    for term in sorted(term_ids - questioned):
        requests.append(("metrics", f"Please provide a golden question for {term}.", term))
    paths = prof.landing / prof.client / "current" / "certification-paths.json" if hasattr(prof, "landing") else None
    if paths is not None and paths.is_file():
        try:
            for doc_id, path in json.loads(paths.read_text()).items():
                if doc_id.rsplit("-", 1)[0] == f"DOC-{unit.upper()}" and isinstance(path, dict) and path.get("ignored"):
                    ask = f"Please correct the certification block for {doc_id}: {path['ignored']}."
                    requests.append(("documents", ask, doc_id))
        except (OSError, ValueError):
            pass
    if (record or {}).get("update"):
        live = _read_record(store, unit) if store.exists(_state(store, unit, "draft.yaml")) else None
        for question, term, text in _released_dependents(unit, live, record):
            requests.append(("metrics", f"Released golden question {question} ('{text}') depends on {term}, which this "
                             f"update removes: accept and certify its removal too, or keep {term}.", question))
        for found in _dependents(store, unit, record):
            requests.append(("metrics", f"Question {found['question']} of unit {found['unit']} uses {found['term']}, which "
                             f"this update removes: keep the term, or remove that question first.",
                             f"{found['unit']}/{found['question']}"))
    return [{"id": _request_id(unit, graph, source, ask), "sub_graph": graph, "ask": ask, "source": source}
            for graph, ask, source in requests]


def _commit_manifest(prof, store, unit: str, manifest: dict, record: dict | None = None,
                     by: str = "onboarding", history: list[dict] | None = None) -> dict:
    additions = list(history or [])
    previous = {r.get("id"): r for r in manifest.get("requests", [])}
    requests = _requests(prof, store, unit, manifest, record)
    open_ids = {r["id"] for r in requests}
    version = (manifest.get("update") or {}).get("version") or manifest["version"]
    for request_id, request in previous.items():
        if request_id not in open_ids:
            additions.append(_history_entry(by, "request-closed", version, request))
    manifest = {**manifest, "requests": requests}
    if record is not None:
        manifest["standards"] = _standards_used(record)
    try:
        return appstate.for_profile(prof, store).commit(unit, manifest, additions)
    except (appstate.StateConflict, StoreConflict) as exc:
        raise OnboardError(409, str(exc)) from None


LAND_TRIES = 3


def _land(prof, store, unit: str, committed: dict, previous: dict | None, writes: dict[str, bytes], by: str,
          restore: dict[str, bytes | None] | None = None) -> None:
    """Write the files a just-committed manifest names. The manifest is committed first, so a writer that lost the race
    writes nothing (the compare-and-swap refuses it); the files follow. A write that fails is retried; if it still fails,
    the manifest is put back as it was (a new unit's state is removed), so it never names files that are not there, and
    the command can simply be run again. restore: what each overwritten live file held before (None: it did not exist),
    written back on that rollback, so an update that could not land leaves the live version's files as they were.
    Any store failure counts, since the manifest must not stay ahead of its files, except that a conflict (someone
    else's write won) is not retried; the restore and the rollback are best effort, and the store failure is what the
    caller sees."""
    pending, failure = dict(writes), None
    for attempt in range(LAND_TRIES):
        for name in list(pending):
            try:
                store.write(name, pending[name])
                del pending[name]
            except Exception as exc:   # noqa: BLE001
                failure = exc
        if not pending:
            return
        if isinstance(failure, StoreConflict):
            break
        if attempt + 1 < LAND_TRIES:
            time.sleep(0.1 * 2 ** attempt)
    log.warning("unit %s: %d file(s) could not be written after its manifest (%s); rolling the manifest back",
                unit, len(pending), failure)
    state = appstate.for_profile(prof, store)
    for name, data in (restore or {}).items():
        try:
            store.write(name, data) if data is not None else store.delete(name)
        except Exception:   # noqa: BLE001
            log.exception("unit %s: %s could not be restored", unit, name)
    try:
        if previous is None:
            state.delete(unit)
            store.delete(f"units/{unit}/")
        else:
            revision = {"_app_revision": committed["_app_revision"]} if "_app_revision" in committed else {}
            state.commit(unit, {**previous, **revision},
                         [_history_entry(by, "rolled-back", previous.get("version", 0), {"unwritten": sorted(pending)})])
    except Exception:   # noqa: BLE001
        log.exception("unit %s: the manifest could not be rolled back", unit)
    if isinstance(failure, StoreConflict):
        raise OnboardError(409, f"unit {unit} changed by someone else; reload")
    raise OnboardError(503, f"unit {unit}'s files could not be written ({type(failure).__name__}); nothing changed: run it again")


def _standards_used(record: dict) -> list[dict]:
    """The reference standards, and their versions, that the unit's drafted mappings name: what its ontology and
    governance sub-graphs rest on, with the preload's version."""
    iris = [m["iri"] for x in record.get("items", []) if x.get("kind") in ("mapping", "policy")
            for m in (x.get("pack") or {}).get("ontology_mappings", [])]
    return preloaded.load().used(iris)


def _accepted_items(manifest: dict, record: dict | None) -> None:
    for entry in (record or {}).get("items", []):
        sub_graph = ITEM_SUB_GRAPH.get(entry.get("kind"))
        if sub_graph and entry.get("accepted_by") and entry["id"] not in manifest["items"][sub_graph]:
            manifest["items"][sub_graph].append(entry["id"])
        if entry.get("kind") == "source" and entry.get("accepted_by"):
            documents = [name.removeprefix("semantic/") for name in manifest.get("inputs", {})
                         if name.startswith("semantic/docs/")]
            manifest["items"]["documents"] = sorted(set(manifest["items"]["documents"]) | set(documents))


def _unit(unit: str) -> None:
    try:
        ddl.validate_unit(unit)
    except ddl.DdlError as exc:
        raise OnboardError(400, str(exc)) from None


def _read_record(store, unit: str, update: bool = False) -> dict:
    try:
        return yaml.safe_load(store.read(_state(store, unit, f"{UPDATE}/draft.yaml" if update else "draft.yaml")))
    except FileNotFoundError:
        raise OnboardError(409, f"unit {unit} has no draft: run draft first") from None


def _item(item: dict) -> dict:
    state = "certified" if item.get("certified_by") else "accepted" if item.get("accepted_by") else "drafted"
    return {**item, "state": state}


def _released(manifest: dict) -> bool:
    return bool((manifest.get("release") or {}).get("model_hash"))


def _current_record(store, unit: str, manifest: dict, live: dict | None) -> dict | None:
    """The draft the unit's requests and review read: a drafted pending update's, else the live one."""
    if (manifest.get("update") or {}).get("drafted"):
        try:
            return _read_record(store, unit, update=True)
        except OnboardError:
            return live
    return live


def _update_inputs(store, unit: str, update: dict) -> tuple[str, dict | None, dict | None]:
    """The pending update's schema, CSVs and semantic files, each checked against the hash its manifest records."""
    def read(name: str) -> bytes:
        try:
            data = store.read(_state(store, unit, f"{UPDATE}/{name}"))
        except FileNotFoundError:
            data = None
        if data is None or hashlib.sha256(data).hexdigest() != update["inputs"].get(name):
            raise OnboardError(409, f"unit {unit}'s update is incomplete ({name} is not as uploaded): upload it again")
        return data
    text = read("schema.sql").decode()
    files = ({f"{name}.csv": read(f"data/{name}.csv") for name in update["rows"]["files"]}
             if update["rows"]["origin"] == "csv" else None)
    semantic_files = ({name.removeprefix("semantic/"): read(name) for name in sorted(update["inputs"])
                       if name.startswith("semantic/") and not name.endswith(".sql")} if update.get("semantic") else None)
    return text, files, semantic_files


def _removed_terms(record: dict | None) -> dict[str, set[str]]:
    """Each term a pending update removes, with the columns its measures read (a client question names columns)."""
    return {x["id"]: {m.get("column", "") for m in (x.get("pack") or {}).get("measures", [])}
            for x in (record or {}).get("items", []) if x.get("kind") == "term" and (x.get("change") or {}).get("kind") == "removed"}


def _dependents(store, unit: str, record: dict | None) -> list[dict]:
    """Questions of other onboarded units, live or pending, that name a term this unit's update removes: the update
    cannot land while they do (each is an open request), or their golden checks would break unannounced."""
    removed = _removed_terms(record)
    found = []
    if not removed:
        return found
    for name in sorted(store.list("units")):
        if not name.endswith("/draft.yaml") or name.startswith(f"units/{unit}/"):
            continue
        try:
            other = yaml.safe_load(store.read(name)) or {}
        except (FileNotFoundError, yaml.YAMLError):
            continue
        for item in other.get("items", []):
            for term in sorted(set(item.get("terms") or []) & set(removed)) if item.get("kind") == "question" else []:
                found.append({"unit": name.split("/")[1], "question": item["id"], "term": term})
    return found


def _released_dependents(unit: str, live: dict | None, record: dict | None) -> list[tuple[str, str, str]]:
    """This unit's certified golden questions that rest on a term the update removes: (question, term, question text).
    Such a question is itself proposed for removal (it can no longer be drafted); each stays an open request until the
    steward certifies that removal, so a released figure is never dropped without one."""
    removed = _removed_terms(record)
    if not removed:
        return []
    pending = {x["id"]: x for x in (record or {}).get("items", [])}
    out = []
    for item in (live or {}).get("items", []):
        if item.get("kind") != "question" or not item.get("certified_by"):
            continue
        columns = {f"{unit}.{c}" for c in (item.get("expect") or {}).get("columns", [])}
        for term, measured in removed.items():
            if (term in (item.get("terms") or []) or columns & measured) and not pending.get(item["id"], {}).get("certified_by"):
                out.append((item["id"], term, str(item.get("question") or item.get("title"))))
    return out


def _record_bytes(record: dict) -> bytes:
    return HEADER.encode() + yaml.safe_dump(record, sort_keys=False, width=4096).encode()


def _estate(prof):
    """The client's warehouses, as the factory gives them for WAREHOUSE_BACKEND (kge/adapters/warehouse/ open_estate).
    A profile that names no warehouses (a preview's caller may hold none) gets the current folder's, which a preview
    never writes to."""
    return open_estate(Path(getattr(prof, "warehouses", ".")), str(getattr(prof, "client", "") or ""))


def _warehouse(prof, unit: str, meta: dict) -> str:
    """Where the warehouse of the unit's live version (the manifest's) is, as a run names it
    (kge/pipeline/warehouse_versions.py)."""
    return _estate(prof).label(unit, int(meta.get("version", 1)))


def _inputs(meta: dict) -> str:
    """The digest of the inputs a version's rows are made from: its manifest's (or its update's) inputs and row source."""
    return warehouse_versions.digest(meta.get("inputs") or {}, meta.get("rows") or {})


def _warehouse_entry(meta: dict) -> dict:
    """What a draft records of its warehouse, and the artifact compiled from it names: the version and its inputs."""
    return {"version": int(meta.get("version", 1)), "inputs": _inputs(meta)}


def ensure_warehouse(prof, unit: str) -> int:
    """The live version's warehouse, built from the live inputs unless it is held with them; its version. Only the live
    version: an older version's inputs are gone once a newer one lands, so an older version is never rebuilt, from
    these or any."""
    store = packstore.for_profile(prof)
    meta = _manifest(prof, store, unit)
    version = int(meta.get("version", 1))
    estate = _estate(prof)
    if estate.stamped(unit, version) == _inputs(meta):
        return version
    model = ddl.parse(store.read(_state(store, unit, "schema.sql")).decode(), unit)
    origin = meta["rows"]["origin"]
    if origin == "csv":
        files = {f"{name}.csv": store.read(_state(store, unit, f"data/{name}.csv")) for name in meta["rows"]["files"]}
        mode = {"csv": ddl.read_csvs(model, files)}
    else:
        mode = "invented" if origin == "invented" else None
    ddl.build(model, estate, mode, version, _inputs(meta))
    return version


def _update_line(unit: str, update: dict, record: dict | None) -> str:
    """A pending update's one line: what it changes against the live version, and what it waits for."""
    version = update["version"]
    if not update.get("drafted") or not (record or {}).get("update"):
        return f"{unit} v{version} (update): uploaded — awaiting draft"
    changes = update.get("changes") or {}
    counts = ", ".join(f"{len(changes.get(kind, []))} {kind}" for kind in ("changed", "added", "removed"))
    review = [item for item in record["items"] if item.get("change")]
    stage = ("nothing to review, lands at the next checks" if not review else
             "awaiting acceptance" if not all(item.get("accepted_by") for item in review) else
             "awaiting certification" if not all(item.get("certified_by") for item in review) else
             "certified, lands at the next checks")
    return f"{unit} v{version} (update): {counts} — {stage}"
