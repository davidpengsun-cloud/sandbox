"""Rate-limited, engine-owned recovery for failed shadow input preparation."""
from __future__ import annotations

import logging
import threading

from kge.adapters.deadline import request_window
from kge.pipeline.trace import cancellable, trigger_cancel


class ShadowRetry:
    def __init__(self, attempt, *, delays=(1, 2, 4, 8, 16, 30, 60), attempt_seconds=10, join_seconds=1):
        if not delays or any(delay <= 0 for delay in delays):
            raise ValueError('shadow retry delays must be positive')
        self._attempt, self._delays = attempt, tuple(delays)
        self._attempt_seconds, self._join_seconds = attempt_seconds, join_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._hooks = None
        self._cancelling = False
        self._rerun = False
        self._close = None
        self._closed = False

    def start(self):
        """Start at most one rate-limited retry worker; a stopped owner cannot restart."""
        with self._lock:
            if self._stop.is_set():
                return
            if self._thread is not None:
                self._rerun = True
                return
            worker = threading.Thread(target=self._run, name='shadow-input-retry', daemon=True)
            self._thread = worker
            try:
                worker.start()
            except Exception:
                self._thread = None
                logging.getLogger(__name__).warning('shadow input retry could not be started')

    def _run(self):
        try:
            with cancellable(self._stop) as (_, hooks):
                with self._lock:
                    self._hooks = hooks
                attempt = 0
                while True:
                    delay = self._delays[min(attempt, len(self._delays) - 1)]
                    if self._stop.wait(delay):
                        break
                    attempt += 1
                    try:
                        with request_window(self._attempt_seconds):
                            if self._attempt():
                                break
                    except Exception:
                        if not self._stop.is_set():
                            logging.getLogger(__name__).warning('shadow input retry failed')
        finally:
            close = None
            restart = False
            with self._lock:
                self._hooks = None
                self._thread = None
                restart = self._rerun and not self._stop.is_set()
                self._rerun = False
                if self._close is not None and not self._cancelling:
                    close, self._close = self._close, None
            if close is not None:
                close()
            if restart:
                self.start()

    def stop(self, close):
        """Cancel retries; defer resource closure if a backend call has not stopped yet."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._close = close
            worker, hooks = self._thread, self._hooks
            self._stop.set()
            if worker is None:
                self._close = None
            elif hooks is not None:
                self._cancelling = True
        if worker is None:
            close()
            return
        if hooks is not None:
            def cancel():
                try:
                    trigger_cancel(self._stop, hooks)
                finally:
                    finish = None
                    with self._lock:
                        self._cancelling = False
                        if self._thread is None and self._close is not None:
                            finish, self._close = self._close, None
                    if finish is not None:
                        finish()
            try:
                threading.Thread(target=cancel, name='shadow-retry-cancel', daemon=True).start()
            except Exception:
                with self._lock:
                    self._cancelling = False
                    finish = self._close if self._thread is None else None
                    if finish is not None:
                        self._close = None
                if finish is not None:
                    finish()
        if worker is not threading.current_thread():
            worker.join(self._join_seconds)
        if worker.is_alive():
            logging.getLogger(__name__).warning('shadow input retry is still stopping; resource closure is deferred')
