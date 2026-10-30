"""Version history, kept packs, and rollback support for released graphs.

Each release appends one JSON line to <artifacts>/<client>/versions.jsonl (beside
current), and snapshots the released pack in <pack store>/<client>/versions/<hash>/
when the store's pack hash matches the artifact's compiled pack hash. Where the
state backend keeps the units' manifests out of units/, a snapshot holds them in
appstate.json.
"""
from __future__ import annotations

from collections.abc import Callable
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

import yaml

from kge.pipeline.settings import ROOT
from kge.pipeline.trace import append_jsonl, read_jsonl
from kge.compiler import pack as packs
from kge.eval import sets as setfiles
from kge import packstore
from kge.adapters import appstate
from kge.pipeline import warehouse_versions
from kge.pipeline.profile import Profile

VERSIONS_FILE = "versions.jsonl"


def versions_path(prof: Profile) -> Path:
    return prof.artifacts / prof.client / VERSIONS_FILE


def _read_artifact_manifest(prof: Profile, model_hash: str) -> dict[str, Any]:
    path = prof.artifacts / prof.client / model_hash / "manifest.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _current_pack_hash(prof: Profile) -> str | None:
    store = packstore.for_profile(prof)
    if os.getenv("STORE_URL", "").strip():
        for name in ("pack.yaml", "pack.lock"):
            if store.exists(name):
                store.read(name)
        for name in store.list("units"):
            if name.endswith("/draft.yaml"):
                store.read(name)
    found = packs.read(prof.clients, prof.client, lock=True, identities=prof.identities)
    if found.findings:
        return None
    return found.hash


def _find_run_for_hash(prof: Profile, model_hash: str, gate_run_id: str | None) -> Path | None:
    runs_root = prof.run_dir() / "runs"
    if gate_run_id:
        candidate = runs_root / gate_run_id
        if (candidate / "summary.json").is_file():
            return candidate
    if not runs_root.is_dir():
        return None
    best: tuple[str, Path] | None = None
    for folder in sorted(runs_root.iterdir()):
        man_path = folder / "manifest.json"
        sum_path = folder / "summary.json"
        if not man_path.is_file() or not sum_path.is_file():
            continue
        try:
            man = json.loads(man_path.read_text())
        except (OSError, ValueError):
            continue
        graph_meta = man.get("graph") or {}
        if (graph_meta.get("artifact") or graph_meta.get("model_hash") or graph_meta.get("model")) != model_hash:
            continue
        arms = {a.get("arm"): a.get("mode") for a in man.get("arms") or [] if isinstance(a, dict)}
        if arms.get("treatment") != "rules":
            continue
        stamp = str(man.get("finished_at") or man.get("started_at") or folder.name)
        if best is None or stamp >= best[0]:
            best = (stamp, folder)
    return best[1] if best else None


def compute_scores(prof: Profile, model_hash: str, gate_run_id: str | None) -> tuple[str | None, dict[str, Any]]:
    """Compute treatment (graph on) scores per set, learnable total, and held-out total."""
    run_dir = _find_run_for_hash(prof, model_hash, gate_run_id)
    if run_dir is None:
        return gate_run_id, {
            "scored": False,
            "label": "not scored",
            "sets": {},
            "learnable": None,
            "heldout": None,
        }
    try:
        summary = json.loads((run_dir / "summary.json").read_text())
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return gate_run_id, {
            "scored": False,
            "label": "not scored",
            "sets": {},
            "learnable": None,
            "heldout": None,
        }
    sets_summary = summary.get("sets") or {}
    sets_manifest = manifest.get("sets") or {}
    per_set: dict[str, dict[str, int]] = {}
    learn_pass = learn_total = 0
    held_pass = held_total = 0
    for sname, by_arm in sets_summary.items():
        treatment = (by_arm or {}).get("treatment") or {}
        passed_obj = ((treatment.get("all") or {}).get("passed")) or {}
        passed = int(round(float(passed_obj.get("mean", 0))))
        total = int((sets_manifest.get(sname) or {}).get("items") or (sets_manifest.get(sname) or {}).get("size") or 0)
        per_set[sname] = {"passed": passed, "total": total}
        if setfiles.is_heldout(sname):
            held_pass += passed
            held_total += total
        else:
            learn_pass += passed
            learn_total += total
    return run_dir.name, {
        "scored": True,
        "label": f"learnable {learn_pass}/{learn_total}, held-out {held_pass}/{held_total}",
        "sets": per_set,
        "learnable": {"passed": learn_pass, "total": learn_total},
        "heldout": {"passed": held_pass, "total": held_total},
    }


def find_proposal_for_hash(prof: Profile, model_hash: str) -> tuple[str | None, dict[str, Any] | None]:
    store = packstore.for_profile(prof)
    for name in sorted(store.list("proposals")):
        if not name.endswith("/proposal.yaml") or name.count("/") != 2:
            continue
        pid = name.split("/")[1]
        try:
            prop = yaml.safe_load(store.read(name))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if isinstance(prop, dict) and prop.get("candidate_hash") == model_hash:
            return pid, prop
    return None, None


def review_proposals(prof: Profile, model_hash: str, released_before: bool) -> None:
    """Refuse releasing a proposal candidate whose items have not been accepted and certified."""
    if released_before:
        return
    from kge.graph.release import Unreleasable
    pid, prop = find_proposal_for_hash(prof, model_hash)
    if prop is None:
        return
    items = prop.get("items") or []
    waiting = [
        f"{x.get('id')} ({'accepted' if x.get('accepted_by') else 'drafted'})"
        for x in items
        if not x.get("accepted_by") or not x.get("certified_by")
    ]
    if waiting:
        raise Unreleasable(
            f"the artifact {model_hash} is proposal {pid} with uncertified item(s): {', '.join(waiting)}: "
            "accept as the business owner and certify as a steward before releasing"
        )


def _snapshot_dir(prof: Profile, model_hash: str) -> Path:
    return prof.artifacts / prof.client / "versions" / model_hash


def has_kept_pack(prof: Profile, model_hash: str) -> bool:
    if not model_hash or not hasattr(prof, "artifacts") or not hasattr(prof, "client"):
        return False
    snap = _snapshot_dir(prof, model_hash)
    if (snap / "pack.yaml").is_file():
        return True
    if os.getenv("STORE_URL", "").strip():
        store = packstore.for_profile(prof)
        return store.exists(f"versions/{model_hash}/pack.yaml")
    return False


def check_rollback_allowed(prof: Profile, target_hash: str, was_hash: str | None) -> None:
    """When releasing an earlier version again, refuse if the store's pack holds unreleased changes or target has no kept pack."""
    from kge.graph.release import Unreleasable
    if not was_hash or was_hash == target_hash:
        return
    cur_pack_hash = _current_pack_hash(prof)
    if cur_pack_hash is not None:
        was_man = _read_artifact_manifest(prof, was_hash)
        target_man = _read_artifact_manifest(prof, target_hash)
        was_pack_hash = (was_man.get("pack") or {}).get("hash")
        target_pack_hash = (target_man.get("pack") or {}).get("hash")
        allowed = {h for h in (was_pack_hash, target_pack_hash) if h}
        if allowed and cur_pack_hash not in allowed:
            raise Unreleasable(
                "the pack in the store holds changes not yet released: release or discard those edits before releasing an earlier version again"
            )
    if not has_kept_pack(prof, target_hash):
        raise Unreleasable(
            f"the artifact {target_hash} has no kept pack in versions/{target_hash[:12]}: release is refused so the store and graph stay in step"
        )


def _lock_holds(data: bytes | None, lock_hash: str | None) -> bool:
    """Whether a pack.lock's bytes are the lock an artifact was compiled with (its manifest's lock_hash). An artifact whose
    manifest names no lock takes any lock, as before lock_hash was compared."""
    if not lock_hash:
        return True
    return data is not None and hashlib.sha256(data).hexdigest() == lock_hash


def _try_snapshot_base_pack(prof: Profile, snap: Path, prefix: str, remote: bool, artifact_pack_hash: str,
                            lock_hash: str | None = None) -> bool:
    """Try reconstructing a pre-unit pack snapshot from original-pack.yaml, store pack.yaml without units, or committed client pack.
    A candidate counts only when its pack hashes to the artifact's and its lock is the artifact's lock_hash."""
    import tempfile
    store = packstore.for_profile(prof)
    candidates: list[tuple[bytes, bytes]] = []
    unit_origs = sorted(n for n in store.list("units") if n.endswith("/original-pack.yaml"))
    for orig_yaml_key in unit_origs:
        unit_prefix = orig_yaml_key.rsplit("/", 1)[0]
        orig_lock_key = f"{unit_prefix}/original-pack.lock"
        lock_bytes = store.read(orig_lock_key) if store.exists(orig_lock_key) else (store.read("pack.lock") if store.exists("pack.lock") else b"")
        if lock_bytes:
            candidates.append((store.read(orig_yaml_key), lock_bytes))
    if store.exists("pack.yaml") and store.exists("pack.lock"):
        candidates.append((store.read("pack.yaml"), store.read("pack.lock")))
    repo_client = ROOT / "clients" / prof.client
    if (repo_client / "pack.yaml").is_file() and (repo_client / "pack.lock").is_file():
        candidates.append(((repo_client / "pack.yaml").read_bytes(), (repo_client / "pack.lock").read_bytes()))
    for pyaml, plock in candidates:
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / prof.client
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "pack.yaml").write_bytes(pyaml)
            (cdir / "pack.lock").write_bytes(plock)
            try:
                found = packs.read(Path(tmp), prof.client, lock=True, identities=prof.identities)
            except Exception:
                continue
            if not found.findings and found.hash == artifact_pack_hash and _lock_holds(plock, lock_hash):
                snap.mkdir(parents=True, exist_ok=True)
                (snap / "pack.yaml").write_bytes(pyaml)
                (snap / "pack.lock").write_bytes(plock)
                if remote:
                    store.write(f"{prefix}/pack.yaml", pyaml)
                    store.write(f"{prefix}/pack.lock", plock)
                return True
    return False


APPSTATE = "appstate.json"
_CAPTURED = "\0appstate"


def _table_state(prof: Profile):
    """The onboarding state backend when it keeps the units' manifests and history in tables (BigQuery,
    kge/adapters/appstate/) rather than in units/<unit>/unit.yaml and history.jsonl, which a snapshot's files carry; None
    for the files backend."""
    state = appstate.for_profile(prof, packstore.for_profile(prof))
    return state if state.tables else None


def _manifests(state) -> dict[str, dict]:
    out = {}
    for unit in state.units():
        try:
            manifest = state.manifest(unit)
        except FileNotFoundError:
            continue
        out[unit] = {k: v for k, v in manifest.items() if k != "_app_revision"}
    return out


def _snapshot_manifests(prof: Profile, model_hash: str) -> dict[str, dict] | None:
    """The manifests snapshotted with model_hash's pack; None if its snapshot holds none (taken on the files backend)."""
    local = _snapshot_dir(prof, model_hash) / APPSTATE
    store = packstore.for_profile(prof)
    name = f"versions/{model_hash}/{APPSTATE}"
    if local.is_file():
        data = local.read_bytes()
    elif os.getenv("STORE_URL", "").strip() and store.exists(name):
        data = store.read(name)
    else:
        return None
    return json.loads(data)


def _put_manifests(prof: Profile, manifests: dict[str, dict], step: str, detail: dict,
                   histories: dict[str, list[dict]] | None = None) -> None:
    """Make the state backend's manifests these (a unit not among them loses its manifest, request and history rows, as
    the files backend loses its units/<unit>/ folder), appending a history entry to each unit changed. histories: each
    unit's history as a failed rollback found it (_capture_store), written back first for a unit the rollback dropped,
    as the files backend puts its history.jsonl back. A manifest that carries its _app_revision (_capture_store's) comes
    back above that revision (BigQueryState.restore), so a page read before the rollback is still refused. Nothing for
    the files backend, whose manifests are the units/ files restored."""
    state = _table_state(prof)
    if state is None:
        return
    now = _manifests(state)
    at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bare = lambda m: {k: v for k, v in m.items() if k != "_app_revision"}  # noqa: E731
    changed = {unit: manifest for unit, manifest in manifests.items() if now.get(unit) != bare(manifest)}
    removed = set(now) - set(manifests)
    additions = {unit: [*((histories or {}).get(unit, []) if unit not in now else []),
                        {"at": at, "by": "release", "step": step, "version": manifest.get("version"), "detail": dict(detail)}]
                 for unit, manifest in changed.items()}
    state.restore(changed, additions, removed)


def _restore_manifests(prof: Profile, model_hash: str, after_units: set[str]) -> None:
    """A rollback's manifests: those snapshotted with model_hash, in the state backend, before onboard.build reads them."""
    if _table_state(prof) is None:
        return
    saved = _snapshot_manifests(prof, model_hash)
    if saved is None:
        if after_units:
            print(f"restore_pack: {model_hash[:12]}'s snapshot holds no unit manifests (taken on the files backend); "
                  "the state backend's are left as they are", file=sys.stderr)
            return
        saved = {}
    _put_manifests(prof, saved, "rolled-back", {"to": model_hash})


def snapshot_pack(prof: Profile, model_hash: str, artifact_pack_hash: str | None) -> bool:
    """Keep a snapshot of the pack and units for model_hash if its hash matches the artifact's.

    The pack's hash leaves its lock out, so the lock is held to the artifact manifest's lock_hash besides: a snapshot is
    taken only of the lock the artifact was compiled with, and a kept snapshot whose lock is not that one has its lock
    replaced by the store's when the store's is, else is rebuilt from a candidate whose lock is. A rollback restores the
    snapshot, so a stale lock kept here would be the store's lock after it (issue #83: make demo-script's second run)."""
    snap = _snapshot_dir(prof, model_hash)
    remote = bool(os.getenv("STORE_URL", "").strip())
    store = packstore.for_profile(prof)
    prefix = f"versions/{model_hash}"
    lock_hash = _read_artifact_manifest(prof, model_hash).get("lock_hash")
    current_lock = store.read("pack.lock") if store.exists("pack.lock") else None
    if (snap / "pack.yaml").is_file() or (remote and store.exists(f"{prefix}/pack.yaml")):
        if (snap / "pack.lock").is_file():
            kept_lock = (snap / "pack.lock").read_bytes()
        else:
            kept_lock = store.read(f"{prefix}/pack.lock") if remote and store.exists(f"{prefix}/pack.lock") else None
        if _lock_holds(kept_lock, lock_hash):
            return True
        if current_lock is not None and _lock_holds(current_lock, lock_hash):
            snap.mkdir(parents=True, exist_ok=True)
            (snap / "pack.lock").write_bytes(current_lock)
            if remote:
                store.write(f"{prefix}/pack.lock", current_lock)
            return True
        if artifact_pack_hash and _try_snapshot_base_pack(prof, snap, prefix, remote, artifact_pack_hash, lock_hash):
            return True
        print(f"snapshot_pack: {model_hash[:12]}'s kept pack.lock is not the lock it was compiled with, and no lock "
              "at hand is: the snapshot is left as it was", file=sys.stderr)
        return True
    if not artifact_pack_hash:
        return False
    cur_hash = _current_pack_hash(prof)
    if cur_hash != artifact_pack_hash or not _lock_holds(current_lock, lock_hash):
        return _try_snapshot_base_pack(prof, snap, prefix, remote, artifact_pack_hash, lock_hash)
    snap.mkdir(parents=True, exist_ok=True)
    names = ["pack.yaml", "pack.lock", *sorted(store.list("units"))]
    for name in names:
        if store.exists(name):
            data = store.read(name)
            dest = snap / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if remote:
                store.write(f"{prefix}/{name}", data)
    state = _table_state(prof)
    if state is not None:
        data = json.dumps(_manifests(state), sort_keys=True, ensure_ascii=False).encode()
        (snap / APPSTATE).write_bytes(data)
        if remote:
            store.write(f"{prefix}/{APPSTATE}", data)
    return True


def _cleanup_removed_units(prof: Profile, removed_units: set[str]) -> None:
    """Remove what each unit a restore leaves out had: its pack folder, warehouses, sets and document. Of its versioned
    warehouses, those no release still names go (an unreleased candidate's); a version a released hash names stays, so
    rolling forward to that hash again reads the rows it was released on."""
    if not removed_units:
        return
    if hasattr(prof, "clients"):
        units_dir = prof.clients / prof.client / "units"
        for unit in removed_units:
            shutil.rmtree(units_dir / unit, ignore_errors=True)
        if units_dir.is_dir() and not any(units_dir.iterdir()):
            units_dir.rmdir()
    for unit in sorted(removed_units):
        if hasattr(prof, "warehouses"):
            (prof.warehouses / f"{unit}.duckdb").unlink(missing_ok=True)
            (prof.warehouses / f".onboard-{unit}.duckdb").unlink(missing_ok=True)
            if hasattr(prof, "artifacts"):
                warehouse_versions.prune(prof, unit, None)
        if hasattr(prof, "estate"):
            est = prof.estate()
            for stem in (f"golden_{unit}_v1.json", f"team_{unit}_v1.json", f"heldout_{unit}_v1.json"):
                (est / stem).unlink(missing_ok=True)
            doc_id = f"DD-{unit.upper()}"
            (est / "docs" / "uncertified" / f"{doc_id}.md").unlink(missing_ok=True)
            index_path = est / "documents.json"
            if index_path.is_file():
                try:
                    index = json.loads(index_path.read_text())
                    kept = [entry for entry in index if entry.get("doc_id") != doc_id]
                    if len(kept) != len(index):
                        part = index_path.with_name(f".{index_path.name}.restore")
                        part.write_text(json.dumps(kept, indent=2))
                        os.replace(part, index_path)
                except (OSError, ValueError):
                    pass


def _capture_store(prof: Profile) -> dict[str, bytes]:
    store = packstore.for_profile(prof)
    names = ["pack.yaml", "pack.lock", *sorted(store.list("units"))]
    captured = {name: store.read(name) for name in names if store.exists(name)}
    state = _table_state(prof)
    if state is not None:
        held = {"manifests": {}, "history": {}}
        for unit in state.units():
            try:
                held["manifests"][unit], held["history"][unit] = state.snapshot(unit)
            except FileNotFoundError:
                continue
        captured[_CAPTURED] = json.dumps(held, ensure_ascii=False).encode()
    return captured


def _restore_captured_store(prof: Profile, captured: dict[str, bytes]) -> None:
    store = packstore.for_profile(prof)
    captured = dict(captured)
    manifests = captured.pop(_CAPTURED, None)
    for name, data in captured.items():
        store.write(name, data)
    for existing in list(store.list("units")):
        if existing not in captured:
            store.delete(existing)
            if hasattr(prof, "clients"):
                (prof.clients / prof.client / existing).unlink(missing_ok=True)
    if hasattr(prof, "clients"):
        units_dir = prof.clients / prof.client / "units"
        if units_dir.is_dir():
            for sub in list(units_dir.iterdir()):
                if sub.is_dir() and not any(sub.rglob("*")):
                    shutil.rmtree(sub, ignore_errors=True)
            if not any(units_dir.iterdir()):
                units_dir.rmdir()
    if manifests is not None:
        held = json.loads(manifests)
        _put_manifests(prof, held["manifests"], "rollback-undone", {}, held["history"])


def _units_diff_for_restore(prof: Profile, model_hash: str) -> tuple[set[str], set[str]]:
    snap = _snapshot_dir(prof, model_hash)
    remote = bool(os.getenv("STORE_URL", "").strip())
    store = packstore.for_profile(prof)
    prefix = f"versions/{model_hash}"
    before_units = {p.split("/")[1] for p in store.list("units") if p.count("/") >= 2}
    if (snap / "pack.yaml").is_file():
        snap_units = snap / "units"
        after_units = (
            {fp.relative_to(snap_units).parts[0] for fp in snap_units.rglob("*") if fp.is_file() and len(fp.relative_to(snap_units).parts) >= 2}
            if snap_units.is_dir()
            else set()
        )
        return before_units - after_units, after_units - before_units
    if remote and store.exists(f"{prefix}/pack.yaml"):
        after_units = {
            vname.removeprefix(f"{prefix}/").split("/")[1]
            for vname in store.list(f"{prefix}/units")
            if vname.removeprefix(f"{prefix}/").count("/") >= 2
        }
        return before_units - after_units, after_units - before_units
    return set(), set()


def removed_units_for_restore(prof: Profile, model_hash: str) -> set[str]:
    return _units_diff_for_restore(prof, model_hash)[0]


def added_units_for_restore(prof: Profile, model_hash: str) -> set[str]:
    return _units_diff_for_restore(prof, model_hash)[1]


def _build_onboarded_units(prof: Profile, build_units: Callable[[Profile], Any] | None = None) -> None:
    builder = build_units or getattr(importlib.import_module("kge.estate.onboard"), "build")
    builder(prof)


def restore_pack(
    prof: Profile,
    model_hash: str,
    cleanup_removed: bool = True,
    build_units: Callable[[Profile], Any] | None = None,
) -> bool:
    """Restore the snapshotted pack.yaml, pack.lock, and units for model_hash into the store on rollback. It is one
    transaction: if it fails, the manifests are as they were and the files are put back."""
    from kge.graph.release import Unreleasable
    snap = _snapshot_dir(prof, model_hash)
    remote = bool(os.getenv("STORE_URL", "").strip())
    store = packstore.for_profile(prof)
    prefix = f"versions/{model_hash}"
    before_units = {p.split("/")[1] for p in store.list("units") if p.count("/") >= 2}
    prev_store = _capture_store(prof)
    if (snap / "pack.yaml").is_file():
        for name in ("pack.yaml", "pack.lock"):
            src = snap / name
            if src.is_file():
                store.write(name, src.read_bytes())
        snap_units = snap / "units"
        kept_unit_files: set[str] = set()
        if snap_units.is_dir():
            for fp in sorted(snap_units.rglob("*")):
                if fp.is_file():
                    rel_name = "units/" + fp.relative_to(snap_units).as_posix()
                    kept_unit_files.add(rel_name)
                    store.write(rel_name, fp.read_bytes())
        for existing_unit_file in list(store.list("units")):
            if existing_unit_file not in kept_unit_files:
                store.delete(existing_unit_file)
                if hasattr(prof, "clients"):
                    (prof.clients / prof.client / existing_unit_file).unlink(missing_ok=True)
        after_units = {p.split("/")[1] for p in kept_unit_files if p.count("/") >= 2}
        try:
            _restore_manifests(prof, model_hash, after_units)
        except Exception as exc:
            _restore_captured_store(prof, {k: v for k, v in prev_store.items() if k != _CAPTURED})
            raise Unreleasable(f"failed to restore the onboarded units' manifests for {model_hash[:12]}: {exc}") from None
        if after_units:
            try:
                _build_onboarded_units(prof, build_units)
            except Exception as exc:
                print(f"restore_pack: onboard.build failed: {exc}", file=sys.stderr)
                _restore_captured_store(prof, prev_store)
                _cleanup_removed_units(prof, after_units - before_units)
                raise Unreleasable(f"failed to rebuild onboarded unit(s) for {model_hash[:12]}: {exc}") from None
        if cleanup_removed:
            _cleanup_removed_units(prof, before_units - after_units)
        return True
    if remote and store.exists(f"{prefix}/pack.yaml"):
        store.write("pack.yaml", store.read(f"{prefix}/pack.yaml"))
        if store.exists(f"{prefix}/pack.lock"):
            store.write("pack.lock", store.read(f"{prefix}/pack.lock"))
        kept_unit_files = set()
        for vname in sorted(store.list(f"{prefix}/units")):
            rel_name = vname.removeprefix(f"{prefix}/")
            kept_unit_files.add(rel_name)
            store.write(rel_name, store.read(vname))
        for existing_unit_file in list(store.list("units")):
            if existing_unit_file not in kept_unit_files:
                store.delete(existing_unit_file)
        after_units = {p.split("/")[1] for p in kept_unit_files if p.count("/") >= 2}
        try:
            _restore_manifests(prof, model_hash, after_units)
        except Exception as exc:
            _restore_captured_store(prof, {k: v for k, v in prev_store.items() if k != _CAPTURED})
            raise Unreleasable(f"failed to restore the onboarded units' manifests for {model_hash[:12]}: {exc}") from None
        if after_units:
            try:
                _build_onboarded_units(prof, build_units)
            except Exception as exc:
                print(f"restore_pack: onboard.build failed: {exc}", file=sys.stderr)
                _restore_captured_store(prof, prev_store)
                _cleanup_removed_units(prof, after_units - before_units)
                raise Unreleasable(f"failed to rebuild onboarded unit(s) for {model_hash[:12]}: {exc}") from None
        if cleanup_removed:
            _cleanup_removed_units(prof, before_units - after_units)
        return True
    return False


def record_release(
    prof: Profile,
    model_hash: str,
    was_hash: str | None,
    released_before: bool,
    ungated: bool,
    signed: dict[str, Any] | None,
    at: str,
    pack_restored: bool = False,
) -> dict[str, Any]:
    """Record a release in versions.jsonl, snapshot or restore its pack, and consume any matching open proposal. When
    versions.jsonl is still empty (an ungated bootstrap before the first recorded release), the hash served before is
    backfilled once, so the history holds the release before a proposal or rollback."""

    man = _read_artifact_manifest(prof, model_hash)
    pack_meta = man.get("pack") or {}
    version = str(pack_meta.get("version") or "0.0.0")
    artifact_pack_hash = pack_meta.get("hash")

    vpath = versions_path(prof)
    existing = read_jsonl(vpath) if vpath.is_file() else []
    if not existing and was_hash and was_hash != model_hash:
        was_man = _read_artifact_manifest(prof, was_hash)
        was_pack = was_man.get("pack") or {}
        from kge.graph.release import GATE, RELEASED, marked
        was_graph = prof.at(was_hash).graph_path()
        was_rel = marked(was_graph, RELEASED) or {}
        was_gate = marked(was_graph, GATE) or {}
        was_run_id = was_rel.get("run_id") or was_gate.get("run_id")
        resolved_was_run, was_scores = compute_scores(prof, was_hash, was_run_id)
        was_kept = snapshot_pack(prof, was_hash, was_pack.get("hash"))
        was_signed_by = was_rel.get("signed_off_by")
        append_jsonl(
            vpath,
            {
                "version": str(was_pack.get("version") or "0.0.0"),
                "model_hash": was_hash,
                "pack_hash": was_pack.get("hash"),
                "released_at": was_rel.get("at") or at,
                "signed_off_by": was_signed_by,
                "run_id": resolved_was_run,
                "scores": was_scores,
                "note": f"signed off by {was_signed_by}" if was_signed_by else "released by hand",
                "kept_pack": was_kept,
                "rollback_of": None,
            },
        )

    pid, prop = find_proposal_for_hash(prof, model_hash)
    is_rollback = bool(released_before and was_hash and was_hash != model_hash)

    if is_rollback:
        restored = True if pack_restored else restore_pack(prof, model_hash)
        kept = restored or snapshot_pack(prof, model_hash, artifact_pack_hash)
    else:
        kept = snapshot_pack(prof, model_hash, artifact_pack_hash)

    gate_run_id = signed.get("run_id") if signed else (prop.get("after_run_id") if prop else None)
    if not gate_run_id:
        from kge.graph.release import GATE, marked
        gate_entry = marked(prof.at(model_hash).graph_path(), GATE) or {}
        gate_run_id = gate_entry.get("run_id")

    resolved_run_id, scores = compute_scores(prof, model_hash, gate_run_id)

    if is_rollback:
        was_man = _read_artifact_manifest(prof, was_hash) if was_hash else {}
        was_ver = (was_man.get("pack") or {}).get("version") or (was_hash[:12] if was_hash else "previous")
        note = f"rollback from {was_ver} ({was_hash[:12]}) to {version} ({model_hash[:12]})"
    elif prop and prop.get("change_note"):
        note = str(prop["change_note"])
    elif signed and signed.get("principal") and not ungated:
        note = f"signed off by {signed['principal']}"
    else:
        note = "released by hand"

    entry = {
        "version": version,
        "model_hash": model_hash,
        "pack_hash": artifact_pack_hash,
        "released_at": at,
        "signed_off_by": signed.get("principal") if signed else None,
        "run_id": resolved_run_id,
        "scores": scores,
        "note": note,
        "kept_pack": kept,
        "rollback_of": was_hash if is_rollback else None,
        **({"proposer": prop.get("proposer"), "proposal_id": pid} if prop else {}),
    }
    append_jsonl(vpath, entry)

    if pid:
        store = packstore.for_profile(prof)
        store.delete(f"proposals/{pid}/")
        if hasattr(prof, "clients"):
            pdir = prof.clients / prof.client / "proposals" / pid
            shutil.rmtree(pdir, ignore_errors=True)
            parent = pdir.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()

    return entry


def list_versions(prof: Profile) -> dict[str, Any]:
    """Return the version history for GET /versions, marking the latest row matching the currently served hash."""
    current = prof.model_hash() if hasattr(prof, "model_hash") else None
    if not hasattr(prof, "artifacts") or not hasattr(prof, "client"):
        return {"profile": getattr(prof, "id", ""), "client": getattr(prof, "client", ""), "current": current, "versions": []}
    vpath = versions_path(prof)
    rows = read_jsonl(vpath) if vpath.is_file() else []
    if not rows and current and hasattr(prof, "at") and hasattr(prof, "clients"):
        man = _read_artifact_manifest(prof, current)
        pack_meta = man.get("pack") or {}
        from kge.graph.release import GATE, RELEASED, marked
        graph = prof.at(current).graph_path()
        rel = marked(graph, RELEASED) or {}
        gate_entry = marked(graph, GATE) or {}
        run_id, scores = compute_scores(prof, current, rel.get("run_id") or gate_entry.get("run_id"))
        signed_by = rel.get("signed_off_by")
        rows = [
            {
                "version": str(pack_meta.get("version") or "0.0.0"),
                "model_hash": current,
                "pack_hash": pack_meta.get("hash"),
                "released_at": rel.get("at"),
                "signed_off_by": signed_by,
                "run_id": run_id,
                "scores": scores,
                "note": f"signed off by {signed_by}" if signed_by else "released by hand",
                "kept_pack": has_kept_pack(prof, current),
                "rollback_of": None,
            }
        ]
    last_current_idx: int | None = None
    for idx, r in enumerate(rows):
        if current and r.get("model_hash") == current:
            last_current_idx = idx
    items = [
        {
            **r,
            "kept_pack": has_kept_pack(prof, str(r.get("model_hash") or "")) if r.get("model_hash") else bool(r.get("kept_pack")),
            "current": (idx == last_current_idx),
        }
        for idx, r in enumerate(rows)
    ]
    return {"profile": prof.id, "client": prof.client, "current": current, "versions": items}
