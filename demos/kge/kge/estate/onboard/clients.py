"""A new client from a DDL or database (make client), and the client taken back out (make reset-client)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import sqlglot
import yaml

from kge.pipeline.settings import ROOT
from kge.adapters.warehouse import open_estate
from kge.estate import ddl
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.ports import WarehouseRefused
from kge.pipeline.profile import ProfileError, read as read_profiles, resolve
from kge.estate.onboard.common import CLIENT_MARKER, HEADER, OnboardError
from kge.estate.onboard.source import create
from kge.estate.onboard.drafting import draft
from kge.estate.onboard.status import show


def _client_pack(client: str, label: str, units: list[str]) -> dict:
    """A new client's pack. Its no-join fact is proposed by the second unit's ordinary draft, and becomes a steward
    assertion only when that draft is certified."""
    version = yaml.safe_load((ROOT / "meta-model.yaml").read_text())["version"]
    minor = ".".join(version.split(".")[:2])
    return {
        "manifest": {"client": client, "meta_model_version": version, "id": f"{client}-governance",
                     "version": "1.0.0", "title": f"{label} governance graph", "owners": ["data-steward"],
                     "platform": {"meta_model": f"^{minor}", "compiler": ">=0.4"},
                     "compile": {"store": "kuzu", "embedding_dim": 128, "id_scheme": "v1"},
                     "gates": {"golden_min_pass": {"within": 1.0, "cross": 1.0, "shelf": 1.0, "abstain": 1.0},
                               "control_margin": {"cross": 0.5, "shelf": 0.5}}},
        "schema": {"meta_model": minor, "extensions": []}, "sources": [],
        "no_join": [],
        "glossary": [], "terminology": {"aliases": [], "intents": [
            {"phrases": ["how many", "count"], "intent": "count"}, {"phrases": ["average"], "intent": "avg"}]},
        "calendars": [{"id": "calendar", "year_start_month": 1, "quarter_label": "{yyyy}-Q{n}"}],
        "period_bindings": [], "measures": [], "mappings": [],
        "policies": {"restricted_attributes": []},
        "access": {"purposes": ["analysis", "assistant", "supervision"], "roles": [
            {"id": "analyst", "label": "Analyst", "reads": "all", "processes": ["querying"]},
            {"id": "business_team", "label": "Business team", "reads": "all", "writes": ["terms", "mappings"],
             "processes": ["accepting", "querying"]},
            {"id": "knowledge_engineer", "label": "Knowledge engineer", "reads": "all",
             "processes": ["drafting", "building", "improving", "querying"]},
            {"id": "steward", "label": "Steward", "reads": "all", "writes": ["mappings", "terms"],
             "processes": ["certification", "querying"]},
            {"id": "pipeline_tagger", "label": "Pipeline", "reads": [], "processes": ["tagging"]}]},
        "tests": {"sets": [{"name": "golden", "version": "v1"}]}}


def _model_ddl(model: dict) -> str:
    """Render an already validated model in the unquoted input dialect."""
    unit, lines = model["unit"], [f"CREATE SCHEMA {model['unit']}"]
    for table in model["tables"]:
        parts = [f"{c['name']} {c['type']} {' '.join(c.get('constraints', []))}".rstrip() for c in table["columns"]]
        parts += table.get("constraints", [])
        lines.append(f"CREATE TABLE {unit}.{table['name']} ({', '.join(parts)})")
    for table in model["tables"]:
        if table.get("comment") is not None:
            lines.append(f"COMMENT ON TABLE {unit}.{table['name']} IS {sqlglot.exp.Literal.string(table['comment']).sql()}")
        for col in table["columns"]:
            if col.get("comment") is not None:
                lines.append(f"COMMENT ON COLUMN {unit}.{table['name']}.{col['name']} IS {sqlglot.exp.Literal.string(col['comment']).sql()}")
    return ";\n".join(lines) + ";\n"


def client(client: str, label: str, ddl_file=None, csv_dir=None, rows: str | None = None,
           db=None, as_: str = "") -> dict:
    """Create a client and draft each schema through ordinary unit onboarding. The registry is read back after the
    profile is appended, so even a syntactically valid but unsafe profile is rolled back. A client's database file
    (db) is read through the estate's warehouse port, as its backend reads one: DuckDB's, on the local stack alone."""
    if as_ != "platform":
        raise OnboardError(403, "only the platform principal may create a client")
    if not CLIENT_ID.fullmatch(client):
        raise OnboardError(400, f"client {client!r} is refused: {CLIENT_ID_RULE}")
    registry = ROOT / "deploy/profiles.yaml"
    before = registry.read_bytes()
    remote = None
    remote_registry = remote_version = None
    if os.getenv("STORE_URL", "").strip():
        from kge.adapters import objstore
        remote = objstore.open_store(os.environ["STORE_URL"])
        remote_registry, remote_version = remote.get_version("deploy/profiles.yaml")
        if remote_registry is None:
            raise OnboardError(409, "the object store has no deploy/profiles.yaml")
        try:
            remote_profiles = yaml.safe_load(remote_registry).get("profiles", {})
        except (AttributeError, yaml.YAMLError):
            raise OnboardError(409, "the object store's deploy/profiles.yaml cannot be read") from None
        if client in remote_profiles:
            raise OnboardError(409, f"client {client} already exists in the object-store registry")
        if remote.list(f"clients/{client}/") or remote.list(f"deploy/{client}/"):
            raise OnboardError(409, f"client {client} already exists in the object store")
    try:
        _, known = read_profiles(registry)
        if (client in known or (registry.parent.parent / "clients" / client).exists()
                or (registry.parent / client).exists()):
            raise OnboardError(409, f"client {client} already exists")
    except ProfileError as exc:
        raise OnboardError(409, str(exc)) from None
    if db:
        try:
            models, csvs = open_estate(Path(db).parent, client).read_database(Path(db))
            for m in models:
                ddl.validate_unit(m["unit"])
        except (WarehouseRefused, ddl.DdlError, OSError) as exc:
            raise OnboardError(400, str(exc).splitlines()[0]) from None
        texts = {m["unit"]: _model_ddl(m) for m in models}
    else:
        text = Path(ddl_file).read_text()
        try:
            models = ddl.parse_client(text)
        except ddl.DdlError as exc:
            raise OnboardError(400, str(exc)) from None
        texts = {m["unit"]: _model_ddl(m) for m in models}
        csvs = {}
    units = [m["unit"] for m in models]
    root = registry.parent.parent
    pack_dir, identities = root / "clients" / client, registry.parent / client / "identities.yaml"
    marker = pack_dir / CLIENT_MARKER
    entry = (f"  {client}:\n    client: {client}\n    clients: ../clients\n    artifacts: ../build/artifacts\n"
             f"    store: ../build/stores\n    landing: ../build/landing\n    warehouses: ../build/{client}/warehouses\n"
             f"    identities: {client}/identities.yaml\n    baseline: null\n")
    made = []
    try:
        pack_dir.mkdir(parents=True)
        pack_text = HEADER + yaml.safe_dump(_client_pack(client, label, units), sort_keys=False, width=4096)
        (pack_dir / "pack.yaml").write_text(pack_text)
        identities.parent.mkdir(parents=True)
        identities.write_text(yaml.safe_dump({"client": client, "asker": "analyst", "principals": [
            {"principal": "knowledge_engineer", "role": "knowledge_engineer"},
            {"principal": "analyst", "role": "analyst"}, {"principal": "business_team", "role": "business_team"},
            {"principal": "steward", "role": "steward"}, {"principal": "pipeline_tagger", "role": "pipeline_tagger"}]}, sort_keys=False))
        appended = (b"" if before.endswith(b"\n") else b"\n") + entry.encode()
        registry.write_bytes(before + appended)
        read_profiles(registry)
        marker_data = (json.dumps({"format": 2, "profile_entry": appended.decode(), "units": units}) + "\n").encode()
        marker.write_bytes(marker_data)
        if remote is not None:
            remote_before = remote_registry or b""
            remote_appended = (b"" if remote_before.endswith(b"\n") else b"\n") + entry.encode()
            with tempfile.TemporaryDirectory() as folder:
                remote_check = Path(folder) / "deploy/profiles.yaml"
                remote_check.parent.mkdir()
                remote_check.write_bytes(remote_before + remote_appended)
                try:
                    read_profiles(remote_check)
                except ProfileError as exc:
                    raise OnboardError(409, f"the object-store registry would be invalid: {exc}") from None
            remote.put(f"deploy/{client}/identities.yaml", identities.read_bytes())
            remote.put_version("deploy/profiles.yaml", remote_before + remote_appended, remote_version)
            remote_marker = (json.dumps({"format": 2, "profile_entry": remote_appended.decode(), "units": units}) + "\n").encode()
            remote.put(f"clients/{client}/pack.yaml", pack_text.encode())
            remote.put(f"clients/{client}/.made-by-client.json", remote_marker)
        prof = resolve(client, registry)
        for model in models:
            unit = model["unit"]
            files = csvs.get(unit)
            selected = "csv" if files is not None else rows
            made.append(create(prof, unit, label=unit.replace("_", " ").title(), rows=selected,
                               csv_dir=(Path(csv_dir) / unit if csv_dir else None), csv=files,
                               as_="knowledge_engineer", ddl_text=texts[unit]))
            draft(prof, unit, "knowledge_engineer")
        drafted_units = {unit["unit"] for unit in show(prof)["units"]
                         if any(item["kind"] == "term" for item in unit["items"])}
        if not drafted_units:
            raise OnboardError(400, "a new client needs at least one drafted measure for the platform golden minimum")
        return {"client": client, "units": units, "said": f"client {client} drafted: {', '.join(units)}"}
    except BaseException:
        registry.write_bytes(before)
        if remote is not None:
            for key in remote.list(f"clients/{client}/") + remote.list(f"deploy/{client}/"):
                remote.delete(key)
            if remote_registry is not None:
                _, current_version = remote.get_version("deploy/profiles.yaml")
                remote.put_version("deploy/profiles.yaml", remote_registry, current_version)
        shutil.rmtree(pack_dir, ignore_errors=True); shutil.rmtree(identities.parent, ignore_errors=True)
        shutil.rmtree(root / "build" / client, ignore_errors=True)
        raise


def reset_client(client: str) -> dict:
    root = ROOT
    pack, registry = root / "clients" / client, root / "deploy/profiles.yaml"
    marker = pack / CLIENT_MARKER
    remote = None
    if os.getenv("STORE_URL", "").strip():
        from kge.adapters.objstore import open_store
        remote = open_store(os.environ["STORE_URL"])
        data, _ = remote.get_version(f"clients/{client}/.made-by-client.json")
        if data is not None:
            marker.parent.mkdir(parents=True, exist_ok=True); marker.write_bytes(data)
    if not marker.is_file():
        raise OnboardError(409, f"client {client} was not made by make client")
    state = json.loads(marker.read_text())
    if state.get("format") != 2 or not isinstance(state.get("profile_entry"), str):
        raise OnboardError(409, f"client {client} has no usable reset record")
    entry = state["profile_entry"].encode()
    current = registry.read_bytes()
    if current.count(entry) != 1:
        raise OnboardError(409, f"client {client}'s registry entry is no longer present verbatim; nothing changed. Restore the entry to the text in clients/{client}/.made-by-client.json, or remove the client by hand")
    updated = current.replace(entry, b"", 1)
    if remote is not None:
        try:
            remote_state = json.loads(remote.get(f"clients/{client}/.made-by-client.json"))
        except (TypeError, ValueError):
            raise OnboardError(409, f"client {client}'s object-store reset record is no longer usable; nothing changed") from None
        remote_entry = remote_state.get("profile_entry", "").encode()
        remote_current, version = remote.get_version("deploy/profiles.yaml")
        if remote_state.get("format") != 2 or remote_current is None or remote_current.count(remote_entry) != 1:
            raise OnboardError(409, f"client {client}'s object-store registry entry is no longer present verbatim; nothing changed. Restore the entry to the text in clients/{client}/.made-by-client.json, or remove the client by hand")
        remote.put_version("deploy/profiles.yaml", remote_current.replace(remote_entry, b"", 1), version)
    registry.write_bytes(updated)
    if remote is not None:
        for prefix in (f"clients/{client}/", f"deploy/{client}/", f"releases/{client}/"):
            for key in remote.list(prefix): remote.delete(key)
        remote.delete(f"work/{client}.tar.gz")
        if hasattr(remote, "root"):
            for relative in (f"clients/{client}", f"deploy/{client}", f"releases/{client}"):
                shutil.rmtree(remote.root / relative, ignore_errors=True)
    shutil.rmtree(pack); shutil.rmtree(root / "deploy" / client, ignore_errors=True)
    for path in (root / "build/artifacts" / client, root / "build/stores" / client,
                 root / "build/landing" / client, root / "build" / client):
        shutil.rmtree(path, ignore_errors=True)
    return {"client": client, "said": f"client {client} removed"}
