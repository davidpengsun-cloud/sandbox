"""Landing the certified units in the pack and their warehouses (build), an update landed over the live files,
and a unit taken back out (reset)."""
from __future__ import annotations

import copy
import datetime as dt
import json
import html
import os
import re
import shutil
import sys

import yaml

from kge.estate import certify as certification, ddl
from kge import packstore
from kge.adapters import appstate
from kge.pipeline import warehouse_versions
from kge.pipeline.objstore import read_proposal_record, proposal_pending
from kge.estate.onboard.common import (
    CLIENT_MARKER,
    OnboardError,
    PROPOSAL,
    SUB_GRAPHS,
    UPDATE,
    _accepted_items,
    _commit_manifest,
    _dependents,
    _estate,
    _history_entry,
    _inputs,
    _item,
    _land,
    _manifest,
    _pack,
    _parsed,
    _read_record,
    _record_bytes,
    _semantic_files,
    _state,
    _unit,
    _update_inputs,
    ensure_warehouse,
    log,
)
from kge.estate.onboard.status import show


def _resolve_unit_questions(con, unit_name: str, model: dict, raw_items: list[dict]) -> list[dict]:
    date_cols = {}
    for table in model.get("tables", []):
        dates = [c["name"] for c in table.get("columns", []) if c["type"].upper() == "DATE"]
        if dates:
            date_cols[table["name"]] = dates[0]
    out = []
    for item in raw_items:
        if "expect" in item:
            out.append({"id": item["id"], "kind": item.get("kind", "within"), "question": item["question"], "expect": item["expect"]})
            continue
        col_ref = item["column"].removeprefix(f"{unit_name}.")
        table, col = col_ref.split(".", 1)
        bound = date_cols[table]
        month_str = item["month"]
        start = dt.date.fromisoformat(f"{month_str}-01" if len(month_str) == 7 else month_str)
        end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        sql = f'SELECT round(sum("{col}"), 2) FROM "{table}" WHERE "{bound}" >= DATE \'{start}\' AND "{bound}" < DATE \'{end}\''
        val = con.execute(sql).fetchone()[0]
        out.append({
            "id": item["id"],
            "kind": item.get("kind", "within"),
            "question": item["question"],
            "expect": {"figure": f"{val:.2f}", "unit": unit_name, "columns": [f"{table}.{col}"], "join_path": []},
        })
    return out


def _land_update(prof, store, unit: str, by: str = "onboarding") -> str | None:
    """Land a unit's pending update over its live files, as its next version, once every item it adds, changes or
    removes is certified and no other unit's question uses a term it removes; otherwise say why and leave the live
    version as it is. The new version's warehouse is staged first, with its rows (the estate's warehouse port); the
    manifest's compare-and-swap comes before any file is written; the staged warehouse is published as the new version's
    own only once the files have landed, and the draft the pack compiles is the last of those files, so a landing that stops part-way never compiles the new
    version under the old manifest (the live files it overwrote are put back), and running build again lands it.

    The new version's rows go in a warehouse of their own, so the released graph (and any process serving it) reads the
    live version's rows until a release names this one, and a rollback reads them again. Retention keeps the versions a
    release still names, and this one; the live files the new version no longer names, and the staged update, are
    read by nothing, so removing them is housekeeping."""
    with appstate.for_profile(prof, store).lock():
        try:
            meta = _manifest(prof, store, unit)
        except FileNotFoundError:
            return None
        update = meta.get("update")
        if not update:
            return None
        version, live = update["version"], meta.get("version", 1)
        if not update.get("drafted"):
            return f"{unit} v{version} (update) not landed: it is not drafted yet; v{live} stays live"
        record = _read_record(store, unit, update=True)
        waiting = [item["id"] for item in record["items"] if item.get("change") and not item.get("certified_by")]
        if waiting:
            return (f"{unit} v{version} (update) not landed: {len(waiting)} item(s) await acceptance or certification "
                    f"({', '.join(waiting)}); v{live} stays live")
        blocked = _dependents(store, unit, record)
        if blocked:
            return (f"{unit} v{version} (update) not landed: " + "; ".join(
                f"question {b['question']} of unit {b['unit']} uses {b['term']}, which it removes" for b in blocked) +
                f"; v{live} stays live")
        keep = warehouse_versions.kept(prof, unit, live)
        estate = _estate(prof)
        if version in keep and version in estate.versions(unit) and estate.stamped(unit, version) != _inputs(update):
            return (f"{unit} v{version} (update) not landed: a release still names warehouse v{version}, made from other "
                    f"inputs; upload it again to number it afresh; v{live} stays live")
        text, files, semantic_files = _update_inputs(store, unit, update)
        model = ddl.parse(text, unit)
        published = False
        ddl.stage(model, estate, {"csv": ddl.read_csvs(model, files)} if files is not None else "invented", version).close()
        try:
            landed = {key: value for key, value in record.items() if key != "update"}
            landed["warehouse"] = {"version": version, "inputs": _inputs(update)}
            landed["items"] = [{key: value for key, value in item.items() if key != "change"} for item in record["items"]
                               if (item.get("change") or {}).get("kind") != "removed"]
            changes = update.get("changes") or {}
            before = copy.deepcopy(meta)
            now = {key: value for key, value in meta.items() if key not in ("update", "semantic")}
            now.update(version=version, ddl_sha256=update["ddl_sha256"], rows=update["rows"],
                       table_rows=update["table_rows"], inputs=update["inputs"],
                       golden=update.get("golden") or meta.get("golden"), items={name: [] for name in SUB_GRAPHS},
                       updated={"version": version, "from": update["from"], "by": update["by"], "on": update["on"],
                                **{kind: len(changes.get(kind, [])) for kind in ("changed", "added", "removed")}})
            if update.get("semantic"):
                now["semantic"] = update["semantic"]
            _accepted_items(now, landed)
            writes = {_state(store, unit, "schema.sql"): text.encode()}
            writes.update({_state(store, unit, f"data/{name}"): data for name, data in (files or {}).items()})
            writes.update({_state(store, unit, f"semantic/{name}"): data for name, data in (semantic_files or {}).items()})
            if semantic_files is not None:
                writes[_state(store, unit, PROPOSAL)] = store.read(_state(store, unit, f"{UPDATE}/{PROPOSAL}"))
            writes[_state(store, unit, "draft.yaml")] = _record_bytes(landed)
            restore = {}
            for name in writes:
                try:
                    restore[name] = store.read(name)
                except FileNotFoundError:
                    restore[name] = None
            history = [_history_entry(by, "update", version, {
                "from": update["from"], **{kind: changes.get(kind, []) for kind in ("changed", "added", "removed")},
                "details": changes.get("details", {})})]
            committed = _commit_manifest(prof, store, unit, now, landed, by, history)
            _land(prof, store, unit, committed, before, writes, by, restore)
            estate.publish(unit, version, _inputs(update), keep=frozenset(keep))
            published = True
        finally:
            if not published:
                estate.discard(unit, version)
        dropped = warehouse_versions.prune(prof, unit, version)
        if dropped:
            log.info("unit %s: warehouse version(s) %s removed: no release names them", unit, dropped)
        keep = set(writes)
        for name in store.list(f"units/{unit}/data/") + store.list(f"units/{unit}/semantic/") + \
                store.list(f"units/{unit}/{UPDATE}/") + ([_state(store, unit, PROPOSAL)] if semantic_files is None else []):
            if name not in keep:
                try:
                    if store.exists(name):
                        store.delete(name)
                except Exception:   # noqa: BLE001
                    log.warning("unit %s: %s could not be removed", unit, name)
    counts = ", ".join(f"{len(changes.get(kind, []))} {kind}" for kind in ("changed", "added", "removed"))
    return (f"{unit} v{version} landed ({counts}) in a warehouse of its own; v{live}'s release, and its rows, stay current "
            "until the checks pass and a steward signs off and releases it")


def _live_units(prof, store) -> list[dict]:
    """show's units with their live draft's items: what build writes is the live version, never a pending update."""
    units = []
    for unit in show(prof)["units"]:
        if unit.get("update"):
            live = _read_record(store, unit["unit"]) if store.exists(_state(store, unit["unit"], "draft.yaml")) else None
            unit = {**unit, "items": [_item(x) for x in live["items"]] if live else []}
        units.append(unit)
    return units


def build(prof) -> dict:
    """Land pending updates and write each live unit's question sets and documents. A unit's documents are written
    afresh from its current files, since an update may have dropped one. Only a client make client made owns its
    estate's golden set and summary; any other client's (Meridian's golden v2, its estate summary) belong to its estate
    generator, and a unit onboarded there adds its own golden_<unit>_v1.json beside them. The platform minimum is an
    other-currency abstention for every client, and a no-join "A plus B" refusal where a second unit drafted a measure,
    its pair id in the graph's stable unit order."""
    store, built = packstore.for_profile(prof), []
    updates = [said for unit in appstate.for_profile(prof, store).units()
               for said in [_land_update(prof, store, unit)] if said]
    for unit in _live_units(prof, store):
        if unit["rows"]["origin"] == "none":
            continue
        version = ensure_warehouse(prof, unit["unit"])
        questions = [x for x in unit["items"] if x["kind"] == "question" and x.get("certified_by")]
        if questions:
            content = {"name": f"golden_{unit['unit']}", "version": "v1", "items": [
                {"id": q["id"], "kind": "within", "question": q["question"], "expect": q["expect"]} for q in questions]}
            target = prof.estate() / f"golden_{unit['unit']}_v1.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(content, indent=2) + "\n")
        con = _estate(prof).open(unit["unit"], version)
        try:
            model = ddl.parse(store.read(_state(store, unit["unit"], "schema.sql")).decode(), unit["unit"])
            cat = ddl.catalogue(con, model)
            for fname, prefix in (("questions.json", f"team_{unit['unit']}"), ("heldout.json", f"heldout_{unit['unit']}")):
                state_key = _state(store, unit["unit"], fname)
                if store.exists(state_key):
                    raw_q = json.loads(store.read(state_key).decode())
                    q_list = raw_q["items"] if isinstance(raw_q, dict) else raw_q
                    resolved_q = _resolve_unit_questions(con, unit["unit"], model, q_list)
                    q_target = prof.estate() / f"{prefix}_v1.json"
                    q_target.parent.mkdir(parents=True, exist_ok=True)
                    q_target.write_text(json.dumps({"name": prefix, "version": "v1", "items": resolved_q}, indent=2) + "\n")
        finally:
            con.close()
        title = f"{unit['label']} data dictionary (drafted)"
        metadata = {"title": title, "kind": "data_dictionary", "unit": unit["unit"],
                    "owner": f"{unit['label']} Data Steward", "version": "draft"}
        heading = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(title, quote=False))
        lines = ["---", yaml.safe_dump(metadata, sort_keys=False).rstrip(), "---", "", f"# {heading}", ""]
        for table in cat["tables"]:
            lines += [f"## {table['name']}", "", table.get("comment") or "No table comment.", "",
                      "| Column | Type | Key | Comment |", "|---|---|---|---|"]
            for col in table["columns"]:
                lines.append(f"| {col['name']} | {col['type']} | {col.get('key') or ''} | {col.get('comment') or ''} |")
            lines.append("")
        doc_id = f"DD-{unit['unit'].upper()}"
        target = prof.estate() / "docs/uncertified" / f"{doc_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text("\n".join(lines))
        index_path = prof.estate() / "documents.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else []
        entry = {"doc_id": doc_id, "shelf": "uncertified", "title": title, "kind": "data_dictionary",
                 "unit": unit["unit"], "owner": f"{unit['label']} Data Steward", "version": "draft",
                 "published_at": None, "superseded_by": None, "reviewed_at": None, "planted": [],
                 "path": f"docs/uncertified/{doc_id}.md"}
        index = [d for d in index if d.get("doc_id") != doc_id] + [entry]
        index_path.write_text(json.dumps(index, indent=2) + "\n")
        if unit.get("semantic") and next((x for x in unit["items"] if x["kind"] == "source" and x.get("accepted_by")), None):
            parsed = _parsed(store, unit["unit"], _semantic_files(store, unit["unit"], unit.get("inputs")))
            stale = f"DOC-{unit['unit'].upper()}"
            index = [d for d in index if str(d.get("doc_id", "")).rsplit("-", 1)[0] != stale]
            for shelf in ("certified", "uncertified"):
                for path in (prof.estate() / "docs" / shelf).glob(f"{stale}-*.md"):
                    if path.stem.rsplit("-", 1)[0] == stale:
                        path.unlink()
            for n, document in enumerate(parsed["documents"], 1):
                client_id = f"DOC-{unit['unit'].upper()}-{n}"
                meta = document["meta"]
                candidate = {**meta, "doc_id": client_id, "shelf": "certified"}
                upstream, _ = certification.upstream_certification(candidate, [document["body"]])
                shelf = "certified" if upstream is not None and upstream["ai_ready"] == "yes" else "uncertified"
                path = prof.estate() / "docs" / shelf / f"{client_id}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\n" + yaml.safe_dump({**meta, "unit": unit["unit"]}, sort_keys=False) + "---\n\n" + document["body"] + "\n")
                entry = {"doc_id": client_id, "shelf": shelf, "title": meta["title"], "kind": meta["kind"],
                         "unit": unit["unit"], "owner": meta.get("owner"), "version": meta.get("version"), "published_at": None,
                         "superseded_by": None, "reviewed_at": None, "certification": meta.get("certification"), "planted": [],
                         "path": f"docs/{shelf}/{client_id}.md"}
                index = [d for d in index if d.get("doc_id") != client_id] + [entry]
            index_path.write_text(json.dumps(index, indent=2) + "\n")
        built.append(unit["unit"])
    if built and store.exists(CLIENT_MARKER):
        all_questions = []
        held_units = _live_units(prof, store)
        for held in held_units:
            all_questions += [{"id": q["id"], "kind": "within", "question": q["question"], "expect": q["expect"]}
                              for q in held["items"] if q["kind"] == "question"]
        drafted_terms = []
        for held in held_units:
            term = next((item for item in held["items"] if item["kind"] == "term"), None)
            question = next((item for item in held["items"] if item["kind"] == "question" and item.get("terms") == [term["id"]]), None) \
                if term else None
            if term and question:
                measure = term["pack"]["measures"][0]
                phrase, _, month = question["question"].rpartition(" for ")
                drafted_terms.append({"unit": held["unit"], "term": term["id"], "phrase": phrase,
                                      "month": month, "currency": measure["currency"]})
        if len(drafted_terms) > 1:
            first, second = drafted_terms[:2]
            pair = "-".join(sorted((first["unit"], second["unit"])))
            all_questions.append({"id": "PLATFORM-CROSS-01", "kind": "cross",
                                  "question": f"{first['phrase']} plus {second['phrase'].lower()} for {first['month']}",
                                  "expect": {"behaviour": "refuse", "joins_refused": [pair], "no_figure": True}})
        if drafted_terms:
            first = drafted_terms[0]
            other = "EUR" if first["currency"] != "EUR" else "GBP"
            all_questions.append({"id": "PLATFORM-ABSTAIN-01", "kind": "abstain",
                                  "question": f"{first['phrase']} for {first['month']} in {other}",
                                  "expect": {"behaviour": "abstain", "no_figure": True}})
        (prof.estate() / "golden.json").write_text(json.dumps(all_questions, indent=2) + "\n")
        summary = prof.estate() / "summary.json"
        summary.write_text(json.dumps({"client": prof.client, "units": built}, indent=2) + "\n")
    return {"units": built, **({"updates": updates} if updates else {})}


def _onboarded_units(prof, store) -> list[str]:
    """The units with a manifest, from the state backend (BigQuery's table, or the files' unit.yaml): a unit folder
    with no manifest is a create stopped before it, not an onboarded unit."""
    state = appstate.for_profile(prof, store)
    units = state.units()
    if not state.tables:
        units = [name for name in units if store.exists(f"units/{name}/unit.yaml")]
    return units


def reset(prof, unit: str, force: bool = False) -> dict:
    """Remove one onboarding-owned unit and restore the graph inputs it changed.

    Onboarding's own markers are its state under units/<unit>/ (nothing else writes there) and its staged warehouse.
    create() writes its reset record before it changes anything else, so a unit without one left only those. Reset
    refuses before deleting anything while another onboarding draft names the source, since recompiling that draft
    could not produce a valid graph without it. Every warehouse version goes, since no release of the restored graph
    names the unit; the client-wide job record goes only when no other onboarding unit remains.
    Pending or unreadable proposals refuse reset unless force explicitly permits discarding them."""
    _unit(unit)
    store = packstore.for_profile(prof)
    estate = _estate(prof)
    with store.lock():
        held = store.list(f"units/{unit}")
        if not held:
            pack = _pack(prof)
            ids = {s["id"] for s in pack.get("sources", [])} | {s["id"] for s in pack.get("curated_sources", [])}
            if unit in ids or estate.exists(unit):
                raise OnboardError(409, f"unit {unit} was not made by onboarding")
            estate.discard(unit, 1)
            return {"unit": unit, "current": None, "said": "", "reset": "nothing"}
        if f"units/{unit}/original.json" not in held:
            if estate.exists(unit) or estate.versions(unit):
                raise OnboardError(409, f"unit {unit} has a warehouse but no reset record; not removed")
            store.delete(f"units/{unit}/")
            units_dir = prof.clients / prof.client / "units"
            if units_dir.is_dir() and not any(units_dir.iterdir()):
                units_dir.rmdir()
            estate.discard(unit, 1)
            return {"unit": unit, "current": None, "said": "", "reset": "partial"}
        if not force:
            for name in store.list("proposals"):
                if not name.endswith("/proposal.yaml") or name.count("/") != 2:
                    continue
                pid = name.split("/")[1]
                try:
                    record = read_proposal_record(store, name)
                except ValueError:
                    raise OnboardError(409, f"proposal {pid} could not be read; repair it or use reset --force to discard it") from None
                if proposal_pending(record, prof.model_hash()):
                    raise OnboardError(409, f"proposal {pid} is awaiting review or release; finish it or use reset --force to discard it")
        original = json.loads(store.read(_state(store, unit, "original.json")))
        if original.get("format") != 1 or not isinstance(original.get("lock"), bool):
            raise OnboardError(409, f"unit {unit} has no usable reset record")
        current = original.get("current")
        if not isinstance(current, str) or not re.fullmatch(r"[0-9a-f]{64}", current):
            raise OnboardError(409, f"unit {unit} was created before a graph was released")
        def names_target(value) -> bool:
            if isinstance(value, str):
                return value == unit or value.startswith(f"{unit}.")
            if isinstance(value, list):
                return any(names_target(item) for item in value)
            if isinstance(value, dict):
                return any(names_target(item) for item in value.values())
            return False
        for name in store.list("units"):
            if not name.endswith("/draft.yaml") or name.startswith(f"units/{unit}/"):
                continue
            other = yaml.safe_load(store.read(name))
            if names_target([item.get("pack") for item in other.get("items", []) if isinstance(item, dict)]):
                other_unit = name.split("/")[1]
                raise OnboardError(409, f"unit {unit} is named by onboarded unit {other_unit}; reset that unit first")
        other_units_before = [name for name in _onboarded_units(prof, store) if name != unit]
        if not other_units_before:
            from kge.graph.release import reset_marks
            reset_marks(prof, current)
            for name in ("pack.yaml", "pack.lock"):
                if f"units/{unit}/original-{name}" not in held:
                    continue
                kept = store.read(_state(store, unit, f"original-{name}"))
                if not store.exists(name) or store.read(name) != kept:
                    store.write(name, kept)
        store.delete(f"units/{unit}/")
        appstate.for_profile(prof, store).delete(unit)
        units_dir = prof.clients / prof.client / "units"
        if units_dir.is_dir() and not any(units_dir.iterdir()):
            units_dir.rmdir()
        if not other_units_before:
            for prefix in ("proposals/", "versions/"):
                if store.list(prefix.rstrip("/")):
                    store.delete(prefix)
                folder = prof.clients / prof.client / prefix.rstrip("/")
                if folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)


    estate.remove(unit)
    for stem in (f"golden_{unit}_v1.json", f"team_{unit}_v1.json", f"heldout_{unit}_v1.json"):
        (prof.estate() / stem).unlink(missing_ok=True)
    doc_id = f"DD-{unit.upper()}"
    (prof.estate() / "docs" / "uncertified" / f"{doc_id}.md").unlink(missing_ok=True)
    for shelf in ("certified", "uncertified"):
        for path in (prof.estate() / "docs" / shelf).glob(f"DOC-{unit.upper()}-*.md"):
            if path.stem.rsplit("-", 1)[0] == f"DOC-{unit.upper()}":
                path.unlink()
    index_path = prof.estate() / "documents.json"
    if index_path.exists():
        index = json.loads(index_path.read_text())
        kept = [entry for entry in index if entry.get("doc_id") != doc_id and
                entry.get("doc_id", "").rsplit("-", 1)[0] != f"DOC-{unit.upper()}"]
        part = index_path.with_name(f".{index_path.name}.reset")
        part.write_text(json.dumps(kept, indent=2))
        os.replace(part, index_path)

    other_units = _onboarded_units(prof, store)
    if not other_units:
        appstate.for_profile(prof, store).clear_job()
        for sub in ("onboard", "improve"):
            job = prof.run_dir() / sub
            if job.is_dir():
                shutil.rmtree(job)
        (prof.run_dir() / "unmatched.jsonl").unlink(missing_ok=True)
        if hasattr(prof, "artifacts"):
            (prof.artifacts / prof.client / "versions.jsonl").unlink(missing_ok=True)
            shutil.rmtree(prof.artifacts / prof.client / "versions", ignore_errors=True)
            try:
                from kge.graph import history
                man = history._read_artifact_manifest(prof, current)
                history.snapshot_pack(prof, current, (man.get("pack") or {}).get("hash"))
            except Exception as exc:
                print(f"reset: snapshot_pack failed: {exc}", file=sys.stderr)
        remote_job = "onboard/job.json"
        if store.exists(remote_job):
            store.delete(remote_job)
        remote_request = "onboard/request.json"
        if store.exists(remote_request):
            store.delete(remote_request)
    return {"unit": unit, "current": current, "said": current, "reset": "unit"}

