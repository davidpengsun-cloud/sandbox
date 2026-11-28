"""Allow concurrent planner reads while steward changes refresh shadow inputs."""
from contextlib import contextmanager
from threading import Condition, RLock, get_ident

from kge.adapters.deadline import request_remaining
from kge.pipeline.trace import check_cancelled


class ShadowLock:
    def __init__(self):
        self._condition = Condition(RLock())
        self._readers = {}
        self._writer = None
        self._write_depth = 0
        self._waiting_writers = 0

    def _wait(self):
        check_cancelled()
        remaining = request_remaining()
        self._condition.wait(min(remaining, 0.1) if remaining is not None else 0.1)
        check_cancelled()
        request_remaining()

    @contextmanager
    def read(self):
        thread = get_ident()
        with self._condition:
            while self._writer not in (None, thread) or (
                    self._waiting_writers and thread not in self._readers and self._writer != thread):
                self._wait()
            self._readers[thread] = self._readers.get(thread, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                self._readers[thread] -= 1
                if not self._readers[thread]:
                    del self._readers[thread]
                self._condition.notify_all()

    @contextmanager
    def write(self):
        thread = get_ident()
        with self._condition:
            if self._writer == thread:
                self._write_depth += 1
            else:
                if thread in self._readers:
                    raise RuntimeError('cannot upgrade a shadow read lock')
                self._waiting_writers += 1
                try:
                    while self._writer is not None or self._readers:
                        self._wait()
                    self._writer, self._write_depth = thread, 1
                finally:
                    self._waiting_writers -= 1
                    self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._write_depth -= 1
                if not self._write_depth:
                    self._writer = None
                    self._condition.notify_all()
