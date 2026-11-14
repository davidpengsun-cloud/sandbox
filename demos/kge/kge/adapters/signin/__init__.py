"""Password hashes: the operator's live pointer, else the existing startup source."""
from __future__ import annotations

import json
import threading
import time
from typing import Protocol

from kge.adapters.deadline import ExternalCallError
from kge.adapters.objstore import open_store
from kge.pipeline.settings import settings
from kge.ports import StoreError

REFRESH_SECONDS = 60


class PasswordHashes(Protocol):
    """Read the current username-to-hash mapping, or refuse without stale data."""

    def hashes(self) -> dict[str, str]: ...


class SecretReader(Protocol):
    """Read only the latest bytes of a fully named secret."""

    def latest(self, resource: str) -> bytes: ...


def decoded(data: bytes | str) -> dict[str, str]:
    value = json.loads(data)
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError("password hashes must be an object of strings")
    return value


class ReloadingPasswords:
    """Cache successful pointer/secret reads only; an expired failure never serves old hashes."""

    def __init__(self, store, pointer: str, reader: SecretReader, fallback, *, clock=time.monotonic):
        self.store, self.pointer, self.reader = store, pointer, reader
        self.fallback, self.clock = fallback, clock
        self._hashes = None
        self._at = 0.0
        self._live = False
        self._lock = threading.Lock()

    def hashes(self) -> dict[str, str]:
        with self._lock:
            now = self.clock()
            if self._hashes is None or now - self._at >= REFRESH_SECONDS:
                try:
                    pointer = self.store.get(self.pointer)
                    live = pointer is not None
                    hashes = decoded(self.reader.latest(pointer.decode().strip())) if live else self.fallback()
                except ExternalCallError:
                    raise
                except (StoreError, OSError, ValueError, TypeError):
                    raise ExternalCallError("Sign-in passwords unavailable: cannot read password source") from None
                self._hashes, self._at, self._live = hashes, now, live
            if not self._live:
                try:
                    return dict(self.fallback())
                except (OSError, ValueError, TypeError):
                    raise ExternalCallError("Sign-in passwords unavailable: cannot read password source") from None
            return dict(self._hashes)


def for_profile(prof, given: str | None = None) -> PasswordHashes:
    from kge.adapters.signin.manager import SecretManagerReader

    path = prof.run_dir() / "passwords.json"
    startup = (given or "").strip()

    def fallback():
        return decoded(startup) if startup else decoded(path.read_bytes()) if path.is_file() else {}

    store = open_store(settings().STORE_URL or str(prof.registry.parent.parent.resolve()))
    return ReloadingPasswords(store, f"deploy/{prof.client}/passwords-secret", SecretManagerReader(), fallback)
