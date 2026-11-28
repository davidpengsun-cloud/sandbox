"""DocumentReader factory and content-addressed readings through the existing object-store port."""
from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import asdict
from pathlib import Path

from kge.adapters.objstore import open_store, under
from kge.pipeline.settings import settings
from kge.ports import DocumentPage, DocumentReader, DocumentReading, ObjectStore, StoreError

READERS = {"text": ("kge.adapters.docreader.text", "TextReader"), "gemini": ("kge.adapters.docreader.gemini", "GeminiReader")}


def reader(client: str = "") -> DocumentReader:
    named = (settings().DOCUMENT_READER or "text").strip().lower()
    if named not in READERS:
        raise ValueError("DOCUMENT_READER must be text or gemini")
    module, cls = READERS[named]
    return getattr(importlib.import_module(module), cls)(client)


def readings(out: Path, client: str) -> ObjectStore:
    url = (settings().STORE_URL or "").strip()
    return open_store(under(url, "readings", client) if url else str((out / "stores" / "readings").resolve()))


def read_once(reader: DocumentReader, store: ObjectStore, content: bytes, format: str, name: str = "") -> DocumentReading:
    identity = hashlib.sha256(reader.identity().encode()).hexdigest()
    digest = hashlib.sha256(content).hexdigest()
    key = f"{identity}/{format}/{digest}.json"
    kept, version = store.get_version(key)
    if kept is None:
        reading = reader.read(content, format, name)
        encoded = json.dumps(asdict(reading), ensure_ascii=False).encode()
        try:
            store.put_version(key, encoded, version)
            kept = encoded
        except StoreError:
            kept = store.get(key)
            if kept is None:
                raise
    data = json.loads(kept)
    return DocumentReading(tuple(DocumentPage(**p) for p in data["pages"]))
