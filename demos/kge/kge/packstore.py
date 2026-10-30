"""The persistence seam for a client's onboarding pack additions.

Without STORE_URL this is the original local-folder store. With STORE_URL the
objects under clients/<client>/ are authoritative and the local folder is a
read-through mirror. Conditional puts protect mutations across service
instances; the local lock still serialises mutations inside one process.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import stat
from pathlib import Path

from kge.adapters.objstore import StoreConflict, open_store

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    while True:
        temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            break
        except FileExistsError:
            continue
    try:
        with os.fdopen(fd, "wb") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


class Store:
    def __init__(self, root: Path, run_dir: Path | None = None, remote=None, prefix: str = ""):
        self.root = Path(root)
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.remote, self.prefix = remote, prefix
        self._versions: dict[str, str] = {}

    def path(self, name: str) -> Path:
        path = self.root / name
        if self.root.resolve() not in path.resolve().parents:
            raise ValueError(f"{name!r} leads outside the pack store")
        return path

    def read(self, name: str) -> bytes:
        path = self.path(name)
        if self.remote is None:
            return path.read_bytes()
        data, version = self.remote.get_version(self.prefix + name)
        self._versions[name] = version
        if data is None:
            raise FileNotFoundError(path)
        self._mirror(path, data)
        return data

    def _mirror(self, path: Path, data: bytes) -> None:
        _write(path, data)

    def write(self, name: str, data: bytes) -> None:
        """Atomically replace a pack file.

        The temporary must be beside its target for os.replace to stay atomic.
        The finally block removes it after every success or failure, including
        exceptions raised while the bytes are being written. A remote write is made against the generation
        this process read, or one fetched just now for a file it never read; a write the store refuses is never forced,
        and the caller reports a conflict (HTTP 409), not a store failure.
        """
        path = self.path(name)
        if self.remote is not None:
            version = self._versions.get(name)
            if version is None:
                _, version = self.remote.get_version(self.prefix + name)
            try:
                self._versions[name] = self.remote.put_version(self.prefix + name, data, version)
            except Exception as exc:
                if "changed concurrently" in str(exc):
                    raise StoreConflict(f"{name} changed by someone else; reload") from None
                raise
            self._mirror(path, data)
            return
        _write(path, data)

    def list(self, name: str = "") -> list[str]:
        if self.remote is not None:
            lead = self.prefix + (name.rstrip("/") + "/" if name else "")
            return [key[len(self.prefix):] for key in self.remote.list(lead)]
        root = self.path(name) if name else self.root
        return [p.relative_to(self.root).as_posix() for p in sorted(root.rglob("*")) if p.is_file()] if root.exists() else []

    def exists(self, name: str) -> bool:
        if self.remote is not None:
            data, version = self.remote.get_version(self.prefix + name)
            self._versions[name] = version
            if data is not None:
                self._mirror(self.path(name), data)
            return data is not None
        return self.path(name).exists()

    def delete(self, name: str) -> None:
        """Delete one object, or an owned prefix when name ends in a slash."""
        prefix = name.rstrip("/")
        names = self.list(prefix) if name.endswith("/") else [prefix]
        if self.remote is not None:
            for held in names:
                self.remote.delete(self.prefix + held)
        path = self.path(prefix)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    @contextlib.contextmanager
    def lock(self):
        """The local flock, which serialises this container's own requests (and the part files beside its warehouses)
        whether or not the store is remote. It cannot protect another Cloud Run container: remote files are protected
        by the generation compared in write()."""
        if self.run_dir is None:
            if self.remote is not None:
                yield
                return
            raise RuntimeError("a run directory is required for a mutating pack store")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / "onboard.lock").open("a+b") as held:
            exclusive(held, True)
            try:
                yield
            finally:
                exclusive(held, False)


def exclusive(held, take: bool) -> None:
    """Take or release an exclusive lock on an open file: flock on POSIX, msvcrt's byte lock on Windows."""
    if fcntl is not None:
        fcntl.flock(held, fcntl.LOCK_EX if take else fcntl.LOCK_UN)
        return
    held.seek(0)
    msvcrt.locking(held.fileno(), msvcrt.LK_LOCK if take else msvcrt.LK_UNLCK, 1)


def for_profile(prof) -> Store:
    url = os.getenv("STORE_URL", "").strip()
    return Store(prof.clients / prof.client, prof.run_dir(), open_store(url) if url else None,
                 f"clients/{prof.client}/" if url else "")
