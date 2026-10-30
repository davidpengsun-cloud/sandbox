"""A unit's sources: its tables read from safe DDL or a semantic folder (tables), the unit created or its update
staged (create), its question files, and its rows (rows)."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import unicodedata
from pathlib import Path

from kge.pipeline.settings import ROOT
from kge.estate import ddl, semantic
from kge import packstore
from kge.adapters import appstate
from kge.ports import WarehouseRefused
from kge.estate.onboard.common import (
    MANIFEST_FORMAT,
    NEEDS_ROWS,
    OnboardError,
    PROPOSAL,
    SUB_GRAPHS,
    UPDATE,
    _accepted_items,
    require_process,
    _commit_manifest,
    _current_record,
    _estate,
    _history,
    _history_entry,
    _inputs,
    _land,
    _manifest,
    _pack,
    _parsed,
    _proposal_json,
    _read_record,
    _record_bytes,
    _released,
    _state,
    _unit,
    _warehouse,
    _warehouse_entry,
    log,
    writable,
)
from kge.estate.onboard.figures import _figures


def _catalogue(prof, text: str, unit: str, row_data=None) -> tuple[dict, dict]:
    try:
        model = ddl.parse(text, unit)
        cat = ddl.preview(model, row_data, _estate(prof))
    except ddl.DdlReferenceError as exc:
        raise OnboardError(422, str(exc)) from None
    except ddl.DdlError as exc:
        raise OnboardError(400, str(exc)) from None
    except (WarehouseRefused, AttributeError) as exc:
        raise OnboardError(422, str(exc).splitlines()[0]) from None
    return model, cat


def _model(text: str, unit: str) -> dict:
    try:
        return ddl.parse(text, unit)
    except ddl.DdlReferenceError as exc:
        raise OnboardError(422, str(exc)) from None
    except ddl.DdlError as exc:
        raise OnboardError(400, str(exc)) from None
    except AttributeError as exc:
        raise OnboardError(422, str(exc)) from None


def _csv_files(model: dict, ddl_file, csv_dir=None, csv=None) -> dict[str, bytes] | None:
    if csv is not None:
        return csv
    if ddl_file is None and csv_dir is None:
        return None
    base = Path(csv_dir) if csv_dir else Path(ddl_file).parent
    candidates = [base]
    if not csv_dir:
        candidates.insert(0, base / model["unit"])
    names = [f'{t["name"]}.csv' for t in model["tables"]]
    for folder in candidates:
        present = [folder / name for name in names]
        if any(p.exists() for p in present):
            if not all(p.is_file() for p in present):
                missing = next(p.name for p in present if not p.is_file())
                raise OnboardError(400, f"missing table {missing}")
            return {p.name: p.read_bytes() for p in present}
    return None


def _row_source(model, rows, ddl_file=None, csv_dir=None, csv=None):
    files = _csv_files(model, ddl_file, csv_dir, csv)
    origin = rows or ("csv" if files is not None else "invented")
    if origin not in {"csv", "invented", "none"}:
        raise OnboardError(400, "rows must be csv, invented or none")
    if origin == "csv" and files is None:
        raise OnboardError(400, "rows csv needs one <table>.csv per table")
    try:
        parsed = ddl.read_csvs(model, files) if origin == "csv" else None
    except ddl.DdlError as exc:
        raise OnboardError(400, str(exc)) from None
    return origin, files, parsed


def validate_unit_label(label: str) -> str:
    """A small, printable, single-line display label, shared by API and command onboarding."""
    if not isinstance(label, str) or not 1 <= len(label) <= 80 or not label.strip():
        raise OnboardError(422, "label must contain 1 to 80 characters")
    def allowed_joiner(index: int, char: str) -> bool:
        return (char in "\u200c\u200d" and 0 < index < len(label) - 1
                and all(unicodedata.category(neighbour).startswith("L")
                        or unicodedata.category(neighbour) in {"Mn", "Mc"}
                        for neighbour in (label[index - 1], label[index + 1])))

    if any((unicodedata.category(char).startswith("C") or char in "\u2028\u2029")
           and not allowed_joiner(index, char) for index, char in enumerate(label)):
        raise OnboardError(422, "label must be one line without control characters")
    return label


def tables(prof, unit: str, ddl_file=None, label: str | None = None, rows: str | None = None, csv_dir=None,
           csv: dict[str, bytes] | None = None, ddl_text: str | None = None, semantic_dir=None) -> dict:
    _unit(unit)
    if label is not None:
        validate_unit_label(label)
    parsed_semantic = semantic.parse(semantic.read_folder(Path(semantic_dir)), unit) if semantic_dir else None
    text = ddl.render(parsed_semantic["model"]) if parsed_semantic else ddl_text if ddl_text is not None else Path(ddl_file).read_text()
    model = _model(text, unit)
    origin, files, parsed = _row_source(model, rows, ddl_file, csv_dir, csv)
    _, cat = _catalogue(prof, text, unit, {"csv": parsed} if origin == "csv" else "invented" if origin == "invented" else None)
    store = packstore.for_profile(prof)
    existing = None
    if store.exists(_state(store, unit, "schema.sql")):
        existing = "same" if store.read(_state(store, unit, "schema.sql")) == text.encode() else "different"
    shown = cat["tables"]
    count = sum(table["rows"] for table in shown)
    label_ = (f"from {len(files)} CSV {'file' if len(files) == 1 else 'files'}" if origin == "csv" else
              "invented from the DDL (seed 7)" if origin == "invented" else "none: drafting only")
    return {**cat, "tables": shown, "label": label,
            "rows": {"origin": origin, "count": count, "label": label_},
            "exists": existing}


def validate_question_bytes(data: bytes, label: str = "questions") -> None:
    try:
        parsed = json.loads(data.decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise OnboardError(422, f"{label} is not valid JSON: {exc}") from None
    items = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list) or not items:
        raise OnboardError(422, f"{label} must be a non-empty JSON list of question items (or an object with an 'items' list)")
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise OnboardError(422, f"{label}[{idx}] must be a JSON object")
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            raise OnboardError(422, f"{label}[{idx}] must have a non-empty string 'id'")
        if not isinstance(item.get("question"), str) or not item["question"].strip():
            raise OnboardError(422, f"{label}[{idx}] must have a non-empty string 'question'")
        if "expect" in item:
            if not isinstance(item["expect"], dict):
                raise OnboardError(422, f"{label}[{idx}].expect must be an object")
        else:
            col = item.get("column")
            month = item.get("month")
            if not isinstance(col, str) or "." not in col or not isinstance(month, str) or not month.strip():
                raise OnboardError(422, f"{label}[{idx}] must include 'expect' or both 'column' (<table>.<col>) and 'month'")


def _question_files(unit: str, text: str, ddl_file=None, csv_dir=None, questions: bytes | Path | None = None,
                    heldout: bytes | Path | None = None) -> tuple[bytes | None, bytes | None]:
    q_bytes = questions.read_bytes() if isinstance(questions, Path) else questions
    h_bytes = heldout.read_bytes() if isinstance(heldout, Path) else heldout
    if q_bytes is not None:
        validate_question_bytes(q_bytes, "questions")
    if h_bytes is not None:
        validate_question_bytes(h_bytes, "heldout")
    folder = Path(ddl_file).parent if ddl_file is not None else None
    if folder is None and csv_dir is not None:
        cdir = Path(csv_dir)
        folder = cdir if (cdir / "questions.json").is_file() or (cdir / "heldout.json").is_file() else cdir.parent
    if folder is None:
        kit_examples = Path(os.getenv("FROM", "").strip() or "/kit/examples") / unit
        for candidate in (ROOT / "examples" / unit, kit_examples):
            if (candidate / "schema.sql").is_file() and (candidate / "schema.sql").read_text() == text:
                folder = candidate
                break
    if folder is not None:
        if q_bytes is None and (folder / "questions.json").is_file():
            q_bytes = (folder / "questions.json").read_bytes()
            validate_question_bytes(q_bytes, "questions")
        if h_bytes is None and (folder / "heldout.json").is_file():
            h_bytes = (folder / "heldout.json").read_bytes()
            validate_question_bytes(h_bytes, "heldout")
    return q_bytes, h_bytes


def create(prof, unit: str, ddl_file=None, label: str | None = None, rows: str | None = None, csv_dir=None,
           csv: dict[str, bytes] | None = None, as_: str = "", ddl_text: str | None = None,
           questions: bytes | Path | None = None, heldout: bytes | Path | None = None, semantic_dir=None) -> dict:
    """Onboard a new unit, or stage a released unit's new upload as an update; a unit still in its first review keeps
    its one upload. A unit's established warehouse is an estate's (or one onboarded before warehouses were versioned),
    since onboarding writes its own per version (kge/pipeline/warehouse_versions.py): version 1 is staged, and published
    only once the manifest and the unit's files have landed."""
    _unit(unit)
    require_process(prof, as_, "drafting")
    label = validate_unit_label(label if label is not None else unit)
    ok, why = writable(prof)
    if not ok:
        raise OnboardError(409, why)
    store = packstore.for_profile(prof)
    semantic_files = semantic.read_folder(Path(semantic_dir)) if semantic_dir else None
    proposal_name = _state(store, unit, PROPOSAL)
    proposal_hashes = semantic.proposal_inputs(semantic_files) if semantic_files else None
    parsed_semantic = _parsed(store, unit, semantic_files) if semantic_files else None
    text = ddl.render(parsed_semantic["model"]) if parsed_semantic else ddl_text if ddl_text is not None else Path(ddl_file).read_text()
    if parsed_semantic and csv is None:
        csv = parsed_semantic["seeds"] or None
    model = _model(text, unit)
    origin, files, parsed = _row_source(model, rows, ddl_file, csv_dir, csv)
    q_bytes, h_bytes = _question_files(unit, text, ddl_file, csv_dir, questions, heldout)
    pack = _pack(prof)
    existing_ids = {s["id"] for s in pack.get("sources", [])} | {s["id"] for s in pack.get("curated_sources", [])}
    estate = _estate(prof)
    try:
        appstate.for_profile(prof, store).manifest(unit)
        made_here = True
    except FileNotFoundError:
        made_here = False
    if (unit in existing_ids and not made_here) or (not made_here and estate.exists(unit)):
        raise OnboardError(409, f"{unit} is already a source, curated source or warehouse not made by onboarding")
    schema_name = _state(store, unit, "schema.sql")
    with appstate.for_profile(prof, store).lock():
        if store.exists(schema_name):
            old_meta = _manifest(prof, store, unit)
            proposed_semantic = ({"format": parsed_semantic["format"], "files": {n: hashlib.sha256(d).hexdigest()
                                  for n,d in semantic_files.items()}} if parsed_semantic else None)
            proposed = _rows_record(origin, files)
            upload = {"ddl_sha256": hashlib.sha256(text.encode()).hexdigest(), "semantic": proposed_semantic,
                      "rows": proposed}
            pending = old_meta.get("update")
            if not _same_upload(old_meta, upload, store.read(schema_name) == text.encode()):
                if pending and _same_upload(pending, upload):
                    return {**tables(prof, unit, label=label, rows=origin, csv=files, ddl_text=text), "created": False,
                            "said": f"update v{pending['version']} exists already; nothing changed",
                            "warehouse": str(_warehouse(prof, unit, old_meta))}
                if not _released(old_meta):
                    if store.read(schema_name) != text.encode():
                        raise OnboardError(409, f"unit {unit} exists with different DDL")
                    if old_meta.get("semantic") != proposed_semantic:
                        raise OnboardError(409, f"unit {unit} exists with different semantic files")
                    raise OnboardError(409, f"unit {unit} exists with different data")
                version = _stage_update(prof, store, unit, old_meta, text, model, origin, files, parsed, upload,
                                        semantic_files if parsed_semantic else None,
                                        parsed_semantic, proposal_hashes, as_)
                return {**tables(prof, unit, label=label, rows=origin, csv=files, ddl_text=text), "created": False,
                        "updated": True, "version": version,
                        "said": f"update v{version} of {unit} staged; the live version is unchanged: draft it to see what changed",
                        "warehouse": str(_warehouse(prof, unit, old_meta))}
            record = _read_record(store, unit) if store.exists(_state(store, unit, "draft.yaml")) else None
            _accepted_items(old_meta, record)
            _commit_manifest(prof, store, unit, old_meta, _current_record(store, unit, old_meta, record), as_)
            said = "source exists already; nothing changed" + (
                f" (update v{pending['version']} is still pending)" if pending else "")
            return {**tables(prof, unit, label=label, rows=origin, csv=files, ddl_text=text), "created": False, "said": said,
                    "warehouse": str(_warehouse(prof, unit, old_meta))}
        original = {"format": 1, "current": prof.model_hash() if hasattr(prof, "model_hash") else None,
                    "lock": store.exists("pack.lock")}
        staged = {_state(store, unit, "original.json"): (json.dumps(original, indent=2) + "\n").encode()}
        if original["lock"]:
            staged[_state(store, unit, "original-pack.lock")] = store.read("pack.lock")
        if store.exists("pack.yaml"):
            staged[_state(store, unit, "original-pack.yaml")] = store.read("pack.yaml")
        held = False
        try:
            mode = {"csv": parsed} if origin == "csv" else "invented" if origin == "invented" else None
            session = ddl.stage(model, estate, mode, 1)
            held = True
            try:
                created_catalogue = ddl.catalogue(session, model)
            finally:
                session.close()
            rows_record = ({"origin": "csv", "files": {name.removesuffix(".csv"): hashlib.sha256(data).hexdigest()
                            for name, data in sorted(files.items())}} if origin == "csv" else
                           {"origin": "invented", "seed": 7} if origin == "invented" else {"origin": "none"})
            record = {"format": MANIFEST_FORMAT, "version": 1, "unit": unit, "label": label, "ddl": "schema.sql",
                      "ddl_sha256": hashlib.sha256(text.encode()).hexdigest(), "rows": rows_record,
                      "table_rows": {table["name"]: table["rows"] for table in created_catalogue["tables"]},
                      "created": {"by": as_, "on": dt.datetime.now(dt.timezone.utc).date().isoformat()},
                      "inputs": {"schema.sql": hashlib.sha256(text.encode()).hexdigest()},
                      "items": {name: [] for name in SUB_GRAPHS}, "standards": [],
                      "golden": {"name": f"golden_{unit}", "version": "v1"}, "release": None,
                      "requests": []}
            if parsed_semantic:
                record["semantic"] = {"format": parsed_semantic["format"], "files": {n: hashlib.sha256(d).hexdigest() for n,d in semantic_files.items()}}
                record["inputs"].update({f"semantic/{n}": hashlib.sha256(d).hexdigest() for n, d in semantic_files.items()})
                for name, data in semantic_files.items():
                    if not name.endswith(".sql"):
                        staged[_state(store, unit, f"semantic/{name}")] = data
                staged[proposal_name] = json.dumps({"inputs": proposal_hashes, "proposal": parsed_semantic},
                                                   sort_keys=True, default=_proposal_json).encode()
            staged[schema_name] = text.encode()
            if origin == "csv":
                for name, data in files.items():
                    staged[_state(store, unit, f"data/{name.removesuffix('.csv')}.csv")] = data
                    record["inputs"][f"data/{name.removesuffix('.csv')}.csv"] = hashlib.sha256(data).hexdigest()
            if q_bytes is not None:
                staged[_state(store, unit, "questions.json")] = q_bytes
            if h_bytes is not None:
                staged[_state(store, unit, "heldout.json")] = h_bytes
            history = [_history_entry(as_, "source", record["version"],
                                      {"inputs": sorted(record["inputs"]), "rows": origin})]
            if parsed_semantic:
                history.extend(_history_entry(as_, f"draft-{outcome['node']}", record["version"], outcome)
                               for outcome in parsed_semantic["hints"].get("outcomes", []))
            committed = _commit_manifest(prof, store, unit, record, by=as_, history=history)
            _land(prof, store, unit, committed, None, staged, as_)
            estate.publish(unit, record["version"], _inputs(record))
            held = False
        except BaseException as exc:
            if held:
                estate.discard(unit, 1)
            if isinstance(exc, ddl.DdlError):
                raise OnboardError(400, str(exc)) from None
            if isinstance(exc, WarehouseRefused):
                raise OnboardError(422, str(exc).splitlines()[0]) from None
            raise
    made = tables(prof, unit, label=label, rows=origin, csv=files, ddl_text=text)
    return {**made, "created": True, "said": "source created", "warehouse": estate.label(unit, 1),
            "rows": {t["name"]: t["rows"] for t in created_catalogue["tables"]}}


def _rows_record(origin: str, files: dict[str, bytes] | None) -> dict:
    return ({"origin": "csv", "files": {name.removesuffix(".csv"): hashlib.sha256(data).hexdigest()
                                        for name, data in sorted(files.items())}} if origin == "csv" else
            {"origin": "invented", "seed": 7} if origin == "invented" else {"origin": "none"})


def _same_upload(held: dict, upload: dict, same_ddl: bool | None = None) -> bool:
    """Whether an upload is the one a manifest (or its pending update) already holds: DDL, semantic files and rows."""
    if same_ddl is None:
        same_ddl = held.get("ddl_sha256") == upload["ddl_sha256"]
    return same_ddl and held.get("semantic") == upload["semantic"] and held.get("rows", {}) == upload["rows"]


def _input_changes(was: dict, now: dict) -> dict:
    return {"added": sorted(set(now) - set(was)), "removed": sorted(set(was) - set(now)),
            "changed": sorted(name for name in set(was) & set(now) if was[name] != now[name])}


def _next_version(prof, store, unit: str, meta: dict) -> int:
    """The number of a unit's next version: above its live one, every version it landed (its history's), and every
    version on disk, which holds each one a release keeps (warehouse_versions.prune). A rollback restores an older
    manifest (kge/graph/history.py restore_pack), so the live version + 1 can be one a released hash still reads, whose
    warehouse landing would replace."""
    landed = [entry.get("version") for entry in _history(prof, store, unit) if entry.get("step") == "update"]
    return max([int(meta.get("version", 1)), *_estate(prof).versions(unit),
                *(v for v in landed if isinstance(v, int) and not isinstance(v, bool))]) + 1


def _stage_update(prof, store, unit: str, meta: dict, text: str, model: dict, origin: str, files, parsed,
                  upload: dict, semantic_files: dict | None, parsed_semantic: dict | None, proposal_hashes, as_: str) -> int:
    """Stage a released unit's new upload as its next version. The rows are checked against the DDL in a throwaway
    warehouse first; the manifest's compare-and-swap comes before any file is written, so a lost race leaves nothing;
    the live inputs, draft and warehouse are not touched. A second, different upload replaces a pending update that has
    not landed: it is drafted afresh against the live version. A replaced update's leftovers are never read, since the
    manifest names what the update holds, so removing them is housekeeping."""
    if origin == "none":
        raise OnboardError(409, f"unit {unit} is live: an update needs its rows (one <table>.csv per table, or invented)")
    before = copy.deepcopy(meta)
    try:
        made = ddl.preview(model, {"csv": parsed} if origin == "csv" else "invented", _estate(prof))
    except ddl.DdlError as exc:
        raise OnboardError(400, str(exc)) from None
    except WarehouseRefused as exc:
        raise OnboardError(422, str(exc).splitlines()[0]) from None
    version = _next_version(prof, store, unit, meta)
    inputs = {"schema.sql": upload["ddl_sha256"]}
    staged = {_state(store, unit, f"{UPDATE}/schema.sql"): text.encode()}
    if semantic_files is not None:
        inputs.update({f"semantic/{n}": hashlib.sha256(d).hexdigest() for n, d in semantic_files.items()})
        staged.update({_state(store, unit, f"{UPDATE}/semantic/{n}"): d for n, d in semantic_files.items() if not n.endswith(".sql")})
        staged[_state(store, unit, f"{UPDATE}/{PROPOSAL}")] = json.dumps(
            {"inputs": proposal_hashes, "proposal": parsed_semantic}, sort_keys=True, default=_proposal_json).encode()
    if origin == "csv":
        for name, data in files.items():
            inputs[f"data/{name.removesuffix('.csv')}.csv"] = hashlib.sha256(data).hexdigest()
            staged[_state(store, unit, f"{UPDATE}/data/{name.removesuffix('.csv')}.csv")] = data
    replaced = meta.get("update")
    update = {"version": version, "from": int(meta.get("version", 1)), "by": as_, "on": dt.date.today().isoformat(),
              **upload, "table_rows": {table["name"]: table["rows"] for table in made["tables"]},
              "inputs": inputs, "drafted": False, "changes": None}
    if update["semantic"] is None:
        update.pop("semantic")
    detail = {"inputs": _input_changes(meta.get("inputs", {}), inputs), "rows": origin}
    if replaced:
        detail["replaces"] = f"the pending upload of {replaced.get('on')}, not landed"
    staged_meta = {**meta, "update": update}
    live = _read_record(store, unit) if store.exists(_state(store, unit, "draft.yaml")) else None
    committed = _commit_manifest(prof, store, unit, staged_meta, live, as_,
                                 [_history_entry(as_, "update-source", version, detail)])
    _land(prof, store, unit, committed, before, staged, as_)
    for name in store.list(f"units/{unit}/{UPDATE}/"):
        if name not in staged:
            try:
                store.delete(name)
            except Exception:   # noqa: BLE001
                log.warning("unit %s: %s could not be removed", unit, name)
    return version


def rows(prof, unit: str, rows: str, csv=None, csv_dir=None, as_: str = "",
         questions: bytes | Path | None = None, heldout: bytes | Path | None = None) -> dict:
    """Give a unit its rows, into its version 1 warehouse, which no release reads while it is in its first review.
    The figures come from the draft's own terms and bindings: proposing again against the pack would see this unit's
    draft merged into it already, so its own phrase would look taken and the question be renamed. The rows are read for
    the figures in a scratch warehouse, then staged, and published in place of version 1 only once the manifest and the
    files have landed: a step that stops before leaves version 1 as it was, on either backend."""
    _unit(unit); require_process(prof, as_, "drafting")
    store = packstore.for_profile(prof)
    with appstate.for_profile(prof, store).lock():
        meta = _manifest(prof, store, unit)
        before = copy.deepcopy(meta)
        if meta["rows"]["origin"] != "none":
            if meta["rows"]["origin"] == "invented" and rows == "invented":
                return {"unit": unit, "changed": False, "said": "rows exist already; nothing changed"}
            if meta["rows"]["origin"] == "csv" and rows == "csv" and csv is not None:
                hashes = {n.removesuffix(".csv"): hashlib.sha256(d).hexdigest() for n, d in sorted(csv.items())}
                if hashes == meta["rows"].get("files"):
                    return {"unit": unit, "changed": False, "said": "rows exist already; nothing changed"}
            raise OnboardError(409, f"unit {unit} already has rows")
        record = _read_record(store, unit)
        if any(item.get("accepted_by") for item in record["items"]):
            raise OnboardError(409, f"unit {unit} has an accepted draft; rows may not change it")
        text = store.read(_state(store, unit, "schema.sql")).decode()
        model = ddl.parse(text, unit)
        origin, files, parsed = _row_source(model, rows, None, csv_dir, csv)
        q_bytes, h_bytes = _question_files(unit, text, None, csv_dir, questions, heldout)
        if origin == "none":
            raise OnboardError(409, NEEDS_ROWS.format(unit=unit))
        estate, version = _estate(prof), int(meta.get("version", 1))
        mode = {"csv": parsed} if origin == "csv" else "invented"
        try:
            with ddl.scratch(model, estate, mode) as session:
                table_rows = {table["name"]: table["rows"] for table in ddl.catalogue(session, model)["tables"]}
                meta["rows"] = ({"origin": "csv", "files": {n.removesuffix(".csv"): hashlib.sha256(d).hexdigest()
                                for n, d in sorted(files.items())}} if origin == "csv" else {"origin": "invented", "seed": 7})
                meta["table_rows"] = table_rows
                if origin == "csv":
                    for name, data in files.items():
                        meta["inputs"][f"data/{name.removesuffix('.csv')}.csv"] = hashlib.sha256(data).hexdigest()
                record["warehouse"] = _warehouse_entry(meta)
                figures = _figures(session, record, unit)
            ddl.stage(model, estate, mode, version).close()
        except ddl.DdlError as exc:
            raise OnboardError(400, str(exc)) from None
        except WarehouseRefused as exc:
            raise OnboardError(422, str(exc).splitlines()[0]) from None
        published = False
        for item in record["items"]:
            calculated = figures.get((item.get("terms") or [None])[0]) if item["kind"] == "question" else None
            if calculated:
                stem = item["question"].removesuffix(" for the latest month with rows")
                item["title"] = item["question"] = f"{stem} for {calculated['month']}"
                item["expect"] = {**item["expect"], "figure": calculated["figure"]}
                item["sql"] = calculated["sql"]
                item.pop("needs_rows", None)
        history = [_history_entry(as_, "source", meta["version"], {"rows": origin})]
        try:
            committed = _commit_manifest(prof, store, unit, meta, record, as_, history)
            writes = ({_state(store, unit, f"data/{name.removesuffix('.csv')}.csv"): data for name, data in files.items()}
                      if origin == "csv" else {})
            if q_bytes is not None:
                writes[_state(store, unit, "questions.json")] = q_bytes
            if h_bytes is not None:
                writes[_state(store, unit, "heldout.json")] = h_bytes
            _land(prof, store, unit, committed, before, {**writes, _state(store, unit, "draft.yaml"): _record_bytes(record)}, as_)
            estate.publish(unit, version, _inputs(meta))
            published = True
        finally:
            if not published:
                estate.discard(unit, version)
    return {"unit": unit, "changed": True, "said": "rows added", "rows": meta["rows"]}

