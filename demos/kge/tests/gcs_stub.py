"""A stand-in for Cloud Storage's JSON API, for the object store's tests (tests/unit/test_objstore.py) and the BigQuery
loader's staging (tests/unit/test_bigquery_staging.py): the calls GcsStore makes (kge/adapters/objstore/gcs.py), on
buckets held in memory. serve() starts one on 127.0.0.1, on a port the system gives, in a thread of this process; nothing
reaches the network."""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


class Stub(BaseHTTPRequestHandler):
    """The JSON API's calls the store makes, on buckets in memory; a page holds two names, so a list pages."""

    def log_message(self, *args):
        pass

    def answer(self, code: int, body: bytes = b"", kind: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_one(self, method: str):
        srv = self.server
        srv.seen.append((method, self.path, self.headers.get("Authorization")))
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if srv.fail:
            return self.answer(srv.fail.pop(0))
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        if method == "GET" and (m := re.fullmatch(r"/storage/v1/b/([^/]+)/o/([^/]+)", url.path)) and q.get("alt") == "media":
            data = srv.buckets.get(m[1], {}).get(unquote(m[2]))
            return self.answer(404) if data is None else self.answer(200, data, "application/octet-stream")
        if method == "GET" and (m := re.fullmatch(r"/storage/v1/b/([^/]+)/o/([^/]+)", url.path)):
            name = unquote(m[2])
            if name not in srv.buckets.get(m[1], {}):
                return self.answer(404)
            return self.answer(200, json.dumps({"name": name, "generation": str(srv.generations[(m[1], name)])}).encode())
        if method == "GET" and (m := re.fullmatch(r"/storage/v1/b/([^/]+)/o", url.path)):
            if q.get("prefix", "").startswith("tmp/"):
                return self.answer(403)
            if m[1] not in srv.buckets:
                return self.answer(404)
            names = sorted(k for k in srv.buckets[m[1]] if k.startswith(q.get("prefix", "")))
            at = int(q.get("pageToken") or 0)
            page = {"items": [{"name": k} for k in names[at:at + 2]]}
            if at + 2 < len(names):
                page["nextPageToken"] = str(at + 2)
            return self.answer(200, json.dumps(page).encode())
        if method == "GET" and (m := re.fullmatch(r"/storage/v1/b/([^/]+)", url.path)):
            if m[1] not in srv.buckets:
                return self.answer(404)
            return self.answer(200, json.dumps({"name": m[1]}).encode())
        if method == "POST" and (m := re.fullmatch(r"/upload/storage/v1/b/([^/]+)/o", url.path)) and q.get("uploadType") == "media":
            if m[1] not in srv.buckets:
                return self.answer(404)
            current = srv.generations.get((m[1], q["name"]), 0)
            if "ifGenerationMatch" in q and int(q["ifGenerationMatch"]) != current:
                return self.answer(412)
            srv.buckets[m[1]][q["name"]] = body
            srv.generations[(m[1], q["name"])] = current + 1
            return self.answer(200, json.dumps({"name": q["name"], "generation": str(current + 1)}).encode())
        if method == "DELETE" and (m := re.fullmatch(r"/storage/v1/b/([^/]+)/o/([^/]+)", url.path)):
            name = unquote(m[2])
            if srv.buckets.get(m[1], {}).pop(name, None) is None:
                return self.answer(404)
            srv.generations.pop((m[1], name), None)
            return self.answer(srv.delete_status)
        if method == "POST" and url.path == "/storage/v1/b":
            srv.buckets.setdefault(json.loads(body)["name"], {})
            return self.answer(200, body)
        return self.answer(400)

    def do_GET(self):
        self.handle_one("GET")

    def do_POST(self):
        self.handle_one("POST")

    def do_DELETE(self):
        self.handle_one("DELETE")


@contextmanager
def serve():
    """A stand-in server, its buckets empty, stopped when the block ends."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    srv.buckets, srv.generations, srv.fail, srv.seen = {}, {}, [], []
    srv.delete_status = 200
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
