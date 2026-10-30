"""The object store, the ObjectStore port (kge/ports.py): a local folder (folder.py) or a Google Cloud Storage bucket
(gcs.py), as STORE_URL names it; s3:// is refused, not supported yet. Callers take a store from open_store, never from a
backend's module, and a client's scratch area, the narrower ScratchArea port with no list, from open_scratch: tmp/<client>/ in the tmp bucket TMP_URL names (the
pilot's <project>-kge-tmp, whose objects are deleted 7 days after they were made, each client granted its own prefix
alone), or, with TMP_URL unset, a folder of the client's in this machine's temporary folder."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from kge.adapters.objstore.gcs import GcsStore
from kge.adapters.objstore.keys import check, check_prefix
from kge.adapters.objstore.folder import LocalStore
from kge.adapters.objstore.scratch import Scratch
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.settings import settings
from kge.ports import ObjectStore, ScratchArea, StoreConflict, StoreError

__all__ = ["FORMS", "ObjectStore", "ScratchArea", "StoreConflict", "StoreError", "check", "check_prefix", "open_scratch",
           "open_store", "open_store_if_url", "under"]

FORMS = "/abs/path, file:///abs/path or gs://bucket[/prefix]"


def open_store(url: str) -> ObjectStore:
    """The store url names; ValueError naming the forms it takes."""
    url = (url or "").strip()
    if url.startswith("s3://"):
        raise ValueError(f"{url}: s3:// is not supported yet; a store is {FORMS}")
    if url.startswith("gs://"):
        bucket, _, prefix = url[5:].partition("/")
        try:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,221}[a-z0-9]", bucket):
                raise ValueError(bucket)
            if prefix.strip("/"):
                check(prefix.strip("/"))
        except ValueError:
            raise ValueError(f"{url}: not a bucket and a prefix; a store is {FORMS}") from None
        return GcsStore(url, bucket, prefix)
    path = url[7:] if url.startswith("file://") else url
    if not path.startswith("/"):
        raise ValueError(f"{url!r} is not a store: a store is {FORMS}")
    return LocalStore(url, Path(path))


def open_store_if_url(url: str) -> ObjectStore | None:
    """A store when url uses an explicit store URL form, otherwise None for a filesystem folder."""
    return open_store(url) if url.startswith(("gs://", "file://")) else None


def under(url: str, *parts: str) -> str:
    """A store URL for a folder under url."""
    return "/".join([url.rstrip("/"), *parts])


SCRATCH = "tmp"


def open_scratch(client: str) -> ScratchArea:
    """The client's scratch area: tmp/<client>/ in the store TMP_URL names (the tmp bucket), or, with TMP_URL unset,
    kge-tmp/<client>/ in this machine's temporary folder; put, get, delete and source_uri, and no list. client follows
    CLIENT_ID and CLIENT_ID_RULE, so one client's area never holds another's; anything else is a ValueError naming it, as is a
    TMP_URL that is not a store."""
    if not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
        raise ValueError(f"scratch-area client {client!r} is refused: {CLIENT_ID_RULE}")
    url = (settings().TMP_URL or "").strip()
    if not url:
        folder = Path(tempfile.gettempdir()) / "kge-tmp" / client
        return Scratch(LocalStore(str(folder), folder))
    try:
        return Scratch(open_store(under(url, SCRATCH, client)))
    except ValueError as exc:
        raise ValueError(f"TMP_URL: {exc}") from None
