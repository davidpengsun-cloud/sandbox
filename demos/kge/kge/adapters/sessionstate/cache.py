"""Identity snapshots refreshed off the request path every three seconds.

Requests share a synchronous refresh when the snapshot reaches thirty seconds,
including after idle CPU suspension. Local run-folder helpers retain uncached reads.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from kge.adapters.deadline import ExternalCallError
from kge.ports import StoreError


class IdentityCache:
    SECONDS = 3
    MAX_AGE = 30

    def __init__(self, cache_seconds=SECONDS):
        self.identity_clock = time.monotonic
        self._identity_seconds = cache_seconds
        self._identity_until = 0.0
        self._identity_held = None
        self._identity_attempt = 0
        self._identity_lock = threading.RLock()
        self._identity_refresh_lock = threading.RLock()
        self._identity_stop = threading.Event()
        self._identity_thread = None

    def identity_snapshot(self) -> dict:
        with self._identity_lock:
            stale = self._identity_held is None or self.identity_clock() >= self._identity_until
            attempt = self._identity_attempt
        if not self._identity_seconds or stale:
            try:
                with self._identity_refresh_lock:
                    with self._identity_lock:
                        stale = self._identity_held is None or self.identity_clock() >= self._identity_until
                        refresh = attempt == self._identity_attempt and stale
                    if not self._identity_seconds or refresh:
                        self.refresh_identity()
            except ExternalCallError:
                if not self._identity_seconds:
                    with self._identity_lock:
                        self._identity_until = self.identity_clock()
        with self._identity_lock:
            if self._identity_held is None or self.identity_clock() >= self._identity_until:
                raise ExternalCallError("Sign-in state unavailable: snapshot is missing or at least 30 seconds old; retry shortly",
                                        transient=True)
            return {key: dict(value) for key, value in self._identity_held.items()}

    def refresh_identity(self) -> None:
        """Read both shared collections once; publication never blocks memory-only readers."""
        with self._identity_refresh_lock, self._identity_errors():
            now = self.identity_clock()
            try:
                self._prepare_identity()
                snapshot = self._identity_snapshot()
            finally:
                with self._identity_lock:
                    self._identity_attempt += 1
            if self.identity_clock() >= now + self.MAX_AGE:
                raise ExternalCallError("Sign-in state unavailable: refresh took at least 30 seconds; retry shortly", transient=True)
            with self._identity_lock:
                self._identity_held = snapshot
                self._identity_until = now + self.MAX_AGE

    def start_identity_refresh(self) -> None:
        """Schedule one refresher for this process's identity state, independent of requests."""
        with self._identity_lock:
            if self._identity_thread is not None and self._identity_thread.is_alive():
                return
            self._identity_stop.clear()
            self._identity_thread = threading.Thread(target=self._identity_refresh_loop, name="identity-refresh", daemon=True)
            self._identity_thread.start()

    def _identity_refresh_loop(self) -> None:
        interval = self._identity_seconds or self.SECONDS
        pause = interval
        while True:
            with self._identity_lock:
                attempt = self._identity_attempt
            if self._identity_stop.wait(pause):
                break
            started = self.identity_clock()
            try:
                with self._identity_refresh_lock:
                    with self._identity_lock:
                        refresh = attempt == self._identity_attempt
                    if refresh:
                        self.refresh_identity()
            except ExternalCallError:
                pass
            pause = max(0, interval - (self.identity_clock() - started))

    def stop_identity_refresh(self) -> None:
        self._identity_stop.set()
        if self._identity_thread is not None:
            self._identity_thread.join(timeout=1)

    def _remember_identity(self, collection, key, value) -> None:
        """Apply a durable local write immediately without extending snapshot freshness."""
        with self._identity_lock:
            if self._identity_held is not None:
                self._identity_held[collection][key] = value

    def _prepare_identity(self):
        """Provision the shared collections once where a backend needs it."""
        pass

    def identity_token(self, principal: str, candidate: str) -> str:
        with self._identity_refresh_lock, self._identity_errors():
            held = self.identity_snapshot()["tokens"].get(principal)
            if held is not None:
                return held
            token = self._identity_token(principal, candidate)
            self._remember_identity("tokens", principal, token)
            return token

    def identity_sign_out(self, sid: str, expires: int) -> None:
        with self._identity_refresh_lock, self._identity_errors():
            self._identity_sign_out(sid, expires)
            self._remember_identity("signed_out", sid, expires)

    @contextmanager
    def _identity_errors(self):
        try:
            yield
        except (StoreError, RuntimeError, ValueError, OSError) as exc:
            if isinstance(exc, ExternalCallError):
                raise
            raise ExternalCallError("Sign-in state unavailable; retry shortly", transient=True) from None
