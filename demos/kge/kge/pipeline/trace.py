"""The run trace's recording side: the trace a run has open, and the calls that write into it, which do nothing when no
trace is open; and the JSON-lines files run records are kept in. The trace itself, what it holds and how a run opens one,
is kge/services/ask/engine/runtrace.py's: it sets _current here, so a model call (kge/adapters/model/) or a vector search
(kge/adapters/retrieval/) recorded from below the engine lands in the run's own trace."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

_current: ContextVar[Any] = ContextVar("run_trace", default=None)
_watch: ContextVar[list[str] | None] = ContextVar("transient_watch", default=None)


def current() -> Any:
    """The open trace (kge/services/ask/engine/runtrace.py Trace), or None."""
    return _current.get()


def data_read(principal: str, unit: str, tables: list[str], kind: str, row_count: int,
              refused: bool, reason: str | None = None) -> None:
    """Record metadata for an attempted read, including refusals, without returned rows."""
    t = current()
    if t is None:
        return
    record = {"principal": principal, "unit": unit, "tables": sorted(tables), "kind": kind,
              "row_count": row_count, "refused": refused, **({"reason": reason} if reason else {})}
    t.data_reads.append(record)
    profile = getattr(t.graph, "profile", None)
    if profile is not None:
        append_jsonl(profile.run_dir() / "data-reads.jsonl", {"run_id": t.run_id, "started_at": t.started_at, **record})



def stage(name: str) -> None:
    t = _current.get()
    if t:
        t.stage(name)


def use(config, llm=None) -> None:
    t = _current.get()
    if t:
        t.use(config, llm)


def llm_call(model: str, served_model: str | None, tokens_in: int | None, tokens_out: int | None, ms: float,
             ok: bool = True, error: str | None = None) -> None:
    t = _current.get()
    if t:
        t.llm_call(model, served_model, tokens_in, tokens_out, ms, ok, error)


def agent(arm: str, mode: str, prompt_versions: dict | None = None) -> None:
    t = _current.get()
    if t:
        t.agent_run(arm, mode, prompt_versions)


@contextmanager
def prompting(version: str, prompt_hash: str, context_hash: str | None = None) -> Iterator[None]:
    """A model call made inside this block is recorded with the prompt's version and
    hashes (never the prompt), so two arms can be shown to have sent the same prompt."""
    t = _current.get()
    before = t.prompt if t else None
    if t:
        t.prompt = {"prompt_version": version, "prompt_hash": prompt_hash, **({"context_hash": context_hash} if context_hash else {})}
    try:
        yield
    finally:
        if t:
            t.prompt = before


def retrieved(rows: list[dict], via: str) -> None:
    t = _current.get()
    if t:
        t.retrieved(rows, via)


def transient_failure(exc: BaseException) -> None:
    """A failure the answer stepped around that may pass if asked again (kge/adapters/model/errors.py transient): a
    warehouse query or a model call BigQuery or Vertex AI turned away for now, which the answer reports as a warehouse
    problem or answers without. Recorded, by kind and never by its message, in the list a runner that asks again watches
    (watching, kge/eval/asking.py), so no such answer is scored; nothing is recorded with no runner watching, or for any
    other failure."""
    watch = _watch.get()
    if watch is None:
        return
    from kge.adapters.model.errors import transient
    if transient(exc):
        watch.append(type(exc).__name__)


@contextmanager
def watching() -> Iterator[list[str]]:
    """The failures that may pass if asked again (transient_failure) the code run inside this block stepped around, by
    kind, whether or not it opens a trace of its own."""
    seen: list[str] = []
    token = _watch.set(seen)
    try:
        yield seen
    finally:
        _watch.reset(token)


def number_check(passed: bool, stray_numbers: int = 0, bad_markers: int = 0, bad_documents: int | None = None) -> None:
    t = _current.get()
    if t:
        t.number_check(passed, stray_numbers, bad_markers, bad_documents)


_write = threading.Lock()


def append_jsonl(path: Path, record: dict) -> None:
    """One record, one line. Lines from threads writing at once stay whole."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str) + "\n"
    with _write, path.open("a") as f:
        f.write(line)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
