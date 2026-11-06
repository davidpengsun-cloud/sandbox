"""Calls to an outside service (a model provider, Vertex AI's embeddings), and the one error a failed call raises.

A failure reads as one line, "<service> error (HTTP <code>): <message>", the message from the service's JSON error body,
as kge/adapters/bigquery.py reports BigQuery's. It never quotes the request: its URL can carry a key (the Gemini API's ?key=) and
its headers a bearer token or an API key. Anything in the message that matches one of those is redacted besides, in case
a service echoes it back."""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from kge.adapters.deadline import ExternalCallError as ExternalCallError, RequestDeadlineExceeded as RequestDeadlineExceeded
from kge.pipeline.settings import settings

SERVICES = {
    "aiplatform.googleapis.com": "Vertex AI",
    "generativelanguage.googleapis.com": "Gemini API",
    "api.anthropic.com": "Anthropic API",
    "api.openai.com": "OpenAI API",
}
CREDENTIAL_HEADERS = {"authorization", "x-api-key", "x-goog-api-key", "api-key", "proxy-authorization"}
LONGEST = 400
SHORTEST_SECRET = 8


def vertex_url(region: str) -> str:
    """Vertex AI's endpoint for a location: the global one, or the region's."""
    return "https://aiplatform.googleapis.com/v1" if region == "global" else f"https://{region}-aiplatform.googleapis.com/v1"


BIGQUERY_BUSY = re.compile(r"BigQuery\b[^\n]*\(HTTP (?:429|5\d\d)\)")
BIGQUERY_RATE_LIMITED = re.compile(r"BigQuery\b[^\n]*\(HTTP 403\)[^\n]*(?:Exceeded rate limits|rateLimitExceeded)")


def transient(exc: BaseException) -> bool:
    """Whether a failed call may pass if made again: ExternalCallError.transient; a transport's timeout or dropped
    connection, or a host it could not reach; an HTTP 429 or 5xx; or BigQuery's own error line for one, as
    kge/adapters/bigquery.py raises it ("BigQuery error (HTTP 429): ..."), or for its rate limit, an HTTP 403 whose message
    says "Exceeded rate limits" (reason rateLimitExceeded). A quota spent (403 "Quota exceeded", quotaExceeded), a
    setting the model cannot be built from, a refusal or a step already stopped (NoFallback) is not."""
    if isinstance(exc, ExternalCallError):
        return exc.transient and not isinstance(exc, NoFallback)
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    if isinstance(exc, RuntimeError):
        return bool(BIGQUERY_BUSY.match(str(exc)) or BIGQUERY_RATE_LIMITED.match(str(exc)))
    return isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError))


def service_of(url: str) -> str:
    """The service a URL calls, by its host: a known one's name, else the host alone (never the path or query)."""
    host = urllib.parse.urlsplit(url).hostname or "the service"
    return next((name for suffix, name in SERVICES.items() if host == suffix or host.endswith("." + suffix) or host.endswith("-" + suffix)), host)


def secrets_of(url: str, headers: dict) -> list[str]:
    """Every value the request carries that could be a credential: a credential header's value, the part after its scheme
    word (Bearer), and each query parameter's value."""
    values = [str(v) for k, v in headers.items() if k.lower() in CREDENTIAL_HEADERS]
    values += [v.split(" ", 1)[1] for v in values if " " in v]
    values += [v for _, v in urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)]
    return sorted({v for v in values if len(v) >= SHORTEST_SECRET}, key=len, reverse=True)


def message_of(body: str) -> str:
    """The error's message from the service's body: error.message (Google, Anthropic, OpenAI), or error as a string
    (Ollama), else the body itself."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str) and error:
        return error
    return body


def one_line(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    text = " ".join(text.split())
    return text if len(text) <= LONGEST else text[:LONGEST - 1] + "…"


def model_timeout() -> float:
    """The attempt timeout in seconds, an image default of 180 with an environment override."""
    raw = (settings().MODEL_TIMEOUT or "").strip() or "180"
    try:
        value = float(raw)
    except ValueError:
        value = 0
    if not math.isfinite(value) or value <= 0:
        raise ValueError("MODEL_TIMEOUT must be a finite number of seconds above 0")
    return value


def model_total_timeout() -> float:
    """One attempt and its retry share a budget below Cloud Run's 300-second limit."""
    raw = (settings().MODEL_TOTAL_TIMEOUT or "").strip() or "270"
    try:
        value = float(raw)
    except ValueError:
        value = 0
    if not math.isfinite(value) or not 0 < value < 300:
        raise ValueError("MODEL_TOTAL_TIMEOUT must be a finite number of seconds above 0 and below 300")
    return value


class CallDeadline:
    """Use a monotonic deadline across both attempts, including time between them."""

    def __init__(self, timeout: float | None = None):
        self.timeout = model_timeout() if timeout is None else timeout
        self.expires = time.monotonic() + model_total_timeout()

    def remaining_timeout(self) -> float:
        from kge.adapters.deadline import MIN_CALL_SECONDS, request_remaining
        request_left = request_remaining(MIN_CALL_SECONDS)
        remaining = self.expires - time.monotonic()
        if remaining <= 0:
            raise ExternalCallError("Model call deadline exceeded")
        return min(self.timeout, remaining, request_left) if request_left is not None else min(self.timeout, remaining)


def post_json(url: str, headers: dict, body: dict, timeout: float | None = None, service: str | None = None) -> dict:
    """POST JSON with one retry for a transient failure; refusals are never retried."""
    deadline = CallDeadline(timeout)
    for attempt in range(2):
        try:
            return _post_json(url, headers, body, deadline.remaining_timeout(), service)
        except ExternalCallError as exc:
            if attempt or not transient(exc):
                raise


def _post_json(url: str, headers: dict, body: dict, timeout: float, service: str | None) -> dict:
    """POST body as JSON, the reply's JSON back. ExternalCallError, in one line, for an HTTP error or no answer."""
    name = service or service_of(url)
    req = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace") if exc.fp is not None else ""
        message = one_line(message_of(text) or str(exc.reason), secrets_of(url, headers))
        raise ExternalCallError(f"{name} error (HTTP {exc.code}): {message}", transient=exc.code == 429 or exc.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        raise ExternalCallError(f"{name} unreachable: {one_line(str(reason), secrets_of(url, headers))}", transient=True) from None


NOT_JSON = "the model's reply was not JSON"


class NoFallback(ExternalCallError):
    """On the pilot's cloud path (kge/pipeline/settings.py gcp_native), a configured model's call failed where the local
    stack would fall back to rules, a template or a hashing embedder: the step stops instead, a 503 from the API."""


def no_fallback(where: str, why: BaseException | str) -> None:
    """Raise NoFallback for a failed model step at where, on the cloud path; on the local stack return, and the caller
    falls back as before. why is the failure: a reason the caller already made safe to show, or the exception, named by
    its type, or by its own line for an ExternalCallError, which is safe to show; never another exception's text, which a
    transport can fill with the key."""
    if isinstance(why, RequestDeadlineExceeded):
        raise why
    from kge.pipeline.settings import gcp_native
    if not gcp_native():
        return
    if isinstance(why, BaseException):
        detail = f"the model call failed ({why if isinstance(why, ExternalCallError) else type(why).__name__})"
    else:
        detail = why
    raise NoFallback(f"{where}: {detail}, and the pilot on BigQuery does not fall back to rules") from (
        why if isinstance(why, BaseException) else None)
