"""A local folder as the object store: the ObjectStore port (kge/ports.py) on this machine, STORE_URL /abs/path or
file:///abs/path."""
from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from kge.adapters.objstore.keys import PROBE, check, check_prefix
from kge.ports import StoreError


class LocalStore:
    """A folder: each key a file under it, written whole (a temporary file, then os.replace)."""

    def __init__(self, url: str, root: Path):
        self.url, self.root = url, Path(root)

    def get(self, key: str) -> bytes | None:
        path = self.root / check(key)
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError as exc:
            raise StoreError(f"cannot read {key} at {self.url}: {type(exc).__name__}") from None

    def probe(self) -> str:
        """A read and a list; a folder not made yet is made by the first put, as the emulator makes a bucket."""
        self.get(PROBE)
        self.list()
        return "read, list" if self.root.is_dir() else "read, list, of a folder its first write makes"

    def bucket_metadata(self) -> str:
        try:
            if not self.root.is_dir():
                raise StoreError(f"folder {self.url} does not exist")
            self.root.stat()
        except OSError as exc:
            raise StoreError(f"cannot read the metadata of {self.url}: {type(exc).__name__}") from None
        return "bucket metadata"

    def put(self, key: str, data: bytes) -> None:
        path = self.root / check(key)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StoreError(f"cannot put {key} at {self.url}: {type(exc).__name__}") from None

    @staticmethod
    def _version(path: Path) -> str:
        if not path.is_file():
            return "0"
        stat = path.stat()
        return f"{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}:{hashlib.sha256(path.read_bytes()).hexdigest()}"

    def get_version(self, key: str) -> tuple[bytes | None, str]:
        path = self.root / check(key)
        try:
            return (path.read_bytes(), self._version(path)) if path.is_file() else (None, "0")
        except OSError as exc:
            raise StoreError(f"cannot read {key} at {self.url}: {type(exc).__name__}") from None

    def put_version(self, key: str, data: bytes, version: str) -> str:
        import fcntl
        path = self.root / check(key)
        lock = self.root / ".locks" / hashlib.sha256(key.encode()).hexdigest()
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+b") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            if self._version(path) != version:
                raise StoreError(f"cannot put {key} at {self.url}: object changed concurrently")
            self.put(key, data)
            return self._version(path)

    def list(self, prefix: str = "") -> list[str]:
        check_prefix(prefix)
        out = []
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel = Path(root).relative_to(self.root)
            out += [(rel / f).as_posix() for f in files if not f.startswith(".")]
        return sorted(k for k in out if k.startswith(prefix))

    def delete(self, key: str) -> None:
        path = self.root / check(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StoreError(f"cannot delete {key} at {self.url}: {type(exc).__name__}") from None

    def source_uri(self, key: str) -> str | None:
        """None: no Google Cloud service reads a local folder."""
        check(key)
        return None
