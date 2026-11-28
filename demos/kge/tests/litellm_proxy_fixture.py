"""A synthetic, local-only LiteLLM-shaped proxy for future model-port contract tests.

The proxy is not a LiteLLM implementation and its token counts are scripted, not estimates. It records request bodies
in memory for assertions, never writes them or bearer values to logs, and can split or interrupt a response on any byte.
"""
from __future__ import annotations

import json
import socket
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Reply:
    """One scripted response. A partial stream closes its socket after the supplied bytes."""
    path: str
    status: int = 200
    json_body: Any = None
    raw_bytes: bytes | None = None
    events: tuple[dict | str, ...] = ()
    splits: tuple[int, ...] = ()
    partial: bool = False


def completion(*, content: str, model: str = "proxy-model", finish: str = "stop", usage: dict | None = None) -> dict:
    """The standard completion fields; usage is deliberately omitted when unknown."""
    return {"model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                         "finish_reason": finish}], **({"usage": usage} if usage is not None else {})}


def sse_events(events: tuple[dict | str, ...]) -> bytes:
    """Encode independent SSE data events, without interpreting or rewriting tool arguments."""
    return b"".join(b"data: " + (json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                               if isinstance(event, dict) else event.encode("utf-8")) + b"\n\n" for event in events)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, replies: list[Reply], token: str):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.pending = deque(replies)
        self.token = token
        self.records: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.disconnected = threading.Event()

    def handle_error(self, request, client_address):
        self.disconnected.set()


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            return self._send(401, b'{"error":"unauthorized"}', "application/json")
        path = urlsplit(self.path).path
        if path not in ("/v1/chat/completions", "/utils/token_counter"):
            return self._send(404, b'{"error":"unknown endpoint"}', "application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4 * 1024 * 1024:
                return self._send(400, b'{"error":"invalid body"}', "application/json")
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            return self._send(400, b'{"error":"invalid body"}', "application/json")
        with self.server.lock:
            self.server.records.append({"path": path, "query": urlsplit(self.path).query, "body": body,
                                        "authenticated": True})
            reply = self.server.pending.popleft() if self.server.pending else None
        if reply is None or reply.path != path:
            return self._send(500, b'{"error":"unexpected fixture call"}', "application/json")
        if reply.raw_bytes is not None:
            return self._send(reply.status, reply.raw_bytes, "application/json", reply.splits, reply.partial)
        if reply.events:
            return self._send(reply.status, sse_events(reply.events), "text/event-stream", reply.splits, reply.partial)
        return self._send(reply.status, json.dumps(reply.json_body, ensure_ascii=False).encode("utf-8"), "application/json")

    def _send(self, status: int, data: bytes, content_type: str, splits: tuple[int, ...] = (), partial: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data) + (1 if partial else 0)))
        self.send_header("Connection", "close")
        self.end_headers()
        position = 0
        try:
            for end in (*splits, len(data)):
                if not position <= end <= len(data):
                    raise ValueError("invalid fixture byte split")
                self.wfile.write(data[position:end])
                self.wfile.flush()
                position = end
            if partial:
                self.connection.shutdown(socket.SHUT_RDWR)
        except (BrokenPipeError, ConnectionError):
            self.server.disconnected.set()
        self.close_connection = True


@contextmanager
def litellm_proxy(replies: list[Reply], *, token: str = "offline-fixture-token") -> Iterator[_Server]:
    """Start an ephemeral loopback server and always release its thread and socket."""
    if not token:
        raise ValueError("the fixture requires a bearer")
    server = _Server(replies, token)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
