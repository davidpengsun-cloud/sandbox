"""A client's scratch area (the ScratchArea port, kge/ports.py) over a store: put, get, delete and source_uri, passed to
the store beneath, and nothing else. Live, a client's grant on the tmp bucket is conditioned on its tmp/<client>/ prefix,
and Cloud Storage checks storage.objects.list against the bucket, so no list is offered."""
from __future__ import annotations

from kge.ports import ObjectStore


class Scratch:
    """The scratch area on store: the store's url, and its put, get, delete and source_uri alone."""

    __slots__ = ("_store", "url")

    def __init__(self, store: ObjectStore):
        self._store = store
        self.url = store.url

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def put(self, key: str, data: bytes) -> None:
        self._store.put(key, data)

    def delete(self, key: str) -> None:
        self._store.delete(key)

    def source_uri(self, key: str) -> str | None:
        return self._store.source_uri(key)
