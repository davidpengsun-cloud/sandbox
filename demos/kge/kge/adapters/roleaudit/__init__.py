"""The role-audit log, the RoleAuditLog port (kge/ports.py): JSON lines in the run state's role-audit.jsonl (folder.py),
or, with STORE_URL set, one object an entry under access/<client>/role-audit/<UTC day>/ in that object store, beside the
access audit (objstore.py). Callers take a log from role_audit_log, never from a backend's module."""
from __future__ import annotations

from pathlib import Path

from kge.adapters.objstore import open_store
from kge.pipeline.settings import settings
from kge.ports import RoleAuditEntry, RoleAuditLog

__all__ = ["RoleAuditEntry", "RoleAuditLog", "role_audit_log"]


def role_audit_log(state_dir: Path, client: str) -> RoleAuditLog:
    """The log this deployment keeps: the object store STORE_URL names, else <state_dir>/role-audit.jsonl."""
    url = (settings().STORE_URL or "").strip()
    if url:
        from kge.adapters.roleaudit.objstore import StoreRoleAuditLog
        return StoreRoleAuditLog(open_store(url, private=True), client)
    from kge.adapters.roleaudit.folder import FolderRoleAuditLog
    return FolderRoleAuditLog(Path(state_dir) / "role-audit.jsonl")
