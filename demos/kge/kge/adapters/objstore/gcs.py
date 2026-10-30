"""A Google Cloud Storage bucket as the object store: the ObjectStore port (kge/ports.py) on Google Cloud, STORE_URL
gs://bucket[/prefix].

On GCS it speaks the JSON API with urllib: as the runtime's service account
(google-auth's application default credentials), or with no credentials at
STORAGE_EMULATOR_HOST, an emulator, where a missing bucket is made on the
first put."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from kge.adapters.objstore.keys import PROBE, check, check_prefix
from kge.pipeline.settings import settings
from kge.ports import StoreError

SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
TRIES = 4
BACKOFF = 0.5


class GcsStore:
    """A bucket, through the JSON API: the runtime's service account's token, or none at STORAGE_EMULATOR_HOST."""

    def __init__(self, url: str, bucket: str, prefix: str = ""):
        self.url, self.bucket, self.prefix = url, bucket, prefix.strip("/")
        host = (settings().STORAGE_EMULATOR_HOST or "").strip()
        self.emulator = bool(host)
        self.base = ((host if "://" in host else f"http://{host}") if host else "https://storage.googleapis.com").rstrip("/")
        self.creds = None

    def name(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def headers(self) -> dict:
        """The bearer token header, none at the emulator. A failure to find or refresh credentials is named by its
        type, never shown."""
        if self.emulator:
            return {}
        try:
            if self.creds is None:
                import google.auth
                self.creds, _ = google.auth.default(scopes=[SCOPE])
            if not self.creds.valid:
                from google.auth.transport.requests import Request
                self.creds.refresh(Request())
        except Exception as exc:
            raise StoreError(f"no Google Cloud credentials for {self.url} (application default credentials): "
                             f"{type(exc).__name__}") from None
        return {"Authorization": f"Bearer {self.creds.token}"}

    def call(self, method: str, url: str, key: str, data: bytes | None = None, json_body: bool = False) -> tuple[int, bytes]:
        """(status, body) of one request, retried three times with backoff on 429, 5xx or a connection error; any
        other HTTP status is the caller's to judge."""
        why = ""
        for attempt in range(TRIES):
            if attempt:
                time.sleep(BACKOFF * 2 ** (attempt - 1))
            head = self.headers()
            if data is not None:
                head["Content-Type"] = "application/json" if json_body else "application/octet-stream"
            req = urllib.request.Request(url, data=data, method=method, headers=head)
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as exc:
                exc.read()
                if exc.code != 429 and exc.code < 500:
                    return exc.code, b""
                why = f"HTTP {exc.code}"
            except (urllib.error.URLError, OSError) as exc:
                why = f"{type(getattr(exc, 'reason', exc)).__name__}"
        raise StoreError(f"{method} {key} at {self.url} failed {TRIES} times: {why}")

    def get(self, key: str) -> bytes | None:
        name = urllib.parse.quote(self.name(check(key)), safe="")
        status, body = self.call("GET", f"{self.base}/storage/v1/b/{self.bucket}/o/{name}?alt=media", key)
        if status == 404:
            return None
        if status != 200:
            raise StoreError(f"cannot read {key} at {self.url}: HTTP {status}")
        return body

    def probe(self) -> str:
        """A list of one page, then a read: a 404 on the list is a bucket that does not exist, except at the emulator,
        which makes a missing bucket on its first put, so a probe there says nothing of a real bucket."""
        query = urllib.parse.urlencode({"maxResults": "1", **({"prefix": f"{self.prefix}/"} if self.prefix else {})})
        status, _ = self.call("GET", f"{self.base}/storage/v1/b/{self.bucket}/o?{query}", "/")
        if status == 404 and not self.emulator:
            raise StoreError(f"bucket {self.bucket} does not exist")
        if status not in (200, 404):
            raise StoreError(f"cannot list {self.url}: HTTP {status}")
        self.get(PROBE)
        if self.emulator:
            return ("read, list, at the Cloud Storage emulator, which makes a missing bucket on its first write: "
                    "this says nothing of the pilot's buckets")
        return "read, list"

    def bucket_metadata(self) -> str:
        status, _ = self.call("GET", f"{self.base}/storage/v1/b/{self.bucket}", "/")
        if status == 404:
            raise StoreError(f"bucket {self.bucket} does not exist")
        if status != 200:
            raise StoreError(f"cannot read the metadata of bucket {self.bucket}: HTTP {status}")
        return "bucket metadata"

    def put(self, key: str, data: bytes) -> None:
        query = urllib.parse.urlencode({"uploadType": "media", "name": self.name(check(key))})
        url = f"{self.base}/upload/storage/v1/b/{self.bucket}/o?{query}"
        status, _ = self.call("POST", url, key, data)
        if status == 404 and self.emulator:
            made, _ = self.call("POST", f"{self.base}/storage/v1/b?project=kge", key, json.dumps({"name": self.bucket}).encode(), True)
            if made not in (200, 409):
                raise StoreError(f"cannot make bucket {self.bucket} at {self.url}: HTTP {made}")
            status, _ = self.call("POST", url, key, data)
        if status != 200:
            raise StoreError(f"cannot put {key} at {self.url}: HTTP {status}")

    def get_version(self, key: str) -> tuple[bytes | None, str]:
        checked = check(key)
        name = urllib.parse.quote(self.name(checked), safe="")
        status, body = self.call("GET", f"{self.base}/storage/v1/b/{self.bucket}/o/{name}", checked)
        if status == 404:
            return None, "0"
        if status != 200:
            raise StoreError(f"cannot read {key} at {self.url}: HTTP {status}")
        generation = str(json.loads(body)["generation"])
        query = urllib.parse.urlencode({"alt": "media", "generation": generation})
        status, data = self.call("GET", f"{self.base}/storage/v1/b/{self.bucket}/o/{name}?{query}", checked)
        if status != 200:
            raise StoreError(f"cannot read {key} at {self.url}: HTTP {status}")
        return data, generation

    def put_version(self, key: str, data: bytes, version: str) -> str:
        """Put key only if its generation is still version, and return the new one; some emulators return no resource
        body, so the generation is then read again from the store."""
        checked = check(key)
        query = urllib.parse.urlencode({"uploadType": "media", "name": self.name(checked),
                                        "ifGenerationMatch": version})
        status, body = self.call("POST", f"{self.base}/upload/storage/v1/b/{self.bucket}/o?{query}", checked, data)
        if status == 412:
            raise StoreError(f"cannot put {key} at {self.url}: object changed concurrently")
        if status != 200:
            raise StoreError(f"cannot put {key} at {self.url}: HTTP {status}")
        try:
            return str(json.loads(body)["generation"])
        except (ValueError, KeyError):
            return self.get_version(checked)[1]

    def list(self, prefix: str = "") -> list[str]:
        check_prefix(prefix)
        full, out, page = self.name(prefix) if prefix else (f"{self.prefix}/" if self.prefix else ""), [], None
        while True:
            query = urllib.parse.urlencode({"prefix": full, **({"pageToken": page} if page else {})})
            status, body = self.call("GET", f"{self.base}/storage/v1/b/{self.bucket}/o?{query}", prefix or "/")
            if status == 404:
                return []
            if status != 200:
                raise StoreError(f"cannot list {prefix or '/'} at {self.url}: HTTP {status}")
            listed = json.loads(body or b"{}")
            cut = len(self.prefix) + 1 if self.prefix else 0
            out += [i["name"][cut:] for i in listed.get("items") or []]
            page = listed.get("nextPageToken")
            if not page:
                return sorted(out)

    def delete(self, key: str) -> None:
        checked = check(key)
        name = urllib.parse.quote(self.name(checked), safe="")
        status, _ = self.call("DELETE", f"{self.base}/storage/v1/b/{self.bucket}/o/{name}", checked)
        if status not in (200, 204, 404):
            raise StoreError(f"cannot delete {key} at {self.url}: HTTP {status}")

    def source_uri(self, key: str) -> str | None:
        """gs://<bucket>/<prefix>/<key>: where a BigQuery load job reads the object, as the account that runs it."""
        return f"gs://{self.bucket}/{self.name(check(key))}"
