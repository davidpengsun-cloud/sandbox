"""Stream the model port's events without exposing provider failures or replay in logs."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from contextlib import nullcontext
from contextvars import copy_context
from dataclasses import asdict
from typing import Any, AsyncIterator

from kge.adapters.model import ExternalCallError
from kge.pipeline.trace import QueryCancelled, cancellable, check_cancelled, trigger_cancel
from kge.ports import ChatEvent, ChatRequest, ModelProvider
from kge.services.modelgateway.models import valid_continuation


def _sse(name: str, value: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


def _event(item: ChatEvent) -> bytes:
    if item.part is not None:
        metadata = item.part.metadata
        if metadata is not None and not valid_continuation(metadata):
            raise ValueError("unsupported continuation metadata")
        return _sse("part", {k: v for k, v in asdict(item.part).items() if v is not None})
    return _sse("final", {k: v for k, v in asdict(item).items() if k != "part"})


def stream(model: ModelProvider, request: ChatRequest, is_disconnected, *, scope_factory=nullcontext, error_of=None) -> AsyncIterator[bytes]:
    """Stream one port call; a disconnected reader cancels it and provisional calls never imply success."""
    items: queue.Queue = queue.Queue(maxsize=32)
    cancelled = threading.Event()
    hooks: list = []
    done = object()
    finished = threading.Event()
    context = copy_context()

    def emit(item):
        while not cancelled.is_set():
            try:
                items.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def worker():
        nonlocal hooks
        final = None
        try:
            with scope_factory(), cancellable(cancelled) as (_cancel, held):
                hooks = held
                for event in model.stream_chat(request):
                    check_cancelled()
                    if not isinstance(event, ChatEvent) or final is not None:
                        raise ValueError("invalid model stream")
                    if event.part is None:
                        if event.finish != "STOP":
                            raise ValueError("incomplete model stream")
                        final = event
                    else:
                        emit(_event(event))
                if final is None:
                    raise ValueError("model stream ended without final event")
            emit(_event(final))
        except QueryCancelled:
            pass
        except ExternalCallError:
            emit(_sse("error", {"code": "model_unavailable", "message": "The model is unavailable. Please try again."}))
        except ValueError:
            emit(_sse("error", {"code": "invalid_input", "message": "The model could not process this request."}))
        except Exception as exc:
            try:
                detail = error_of(exc) if error_of is not None else None
            except Exception:
                detail = None
            emit(_sse("error", detail or {"code": "model_failed", "message": "The model could not respond. Please try again."}))
        finally:
            finished.set()
            emit(done)

    threading.Thread(target=context.run, args=(worker,), name="model-gateway", daemon=True).start()

    async def body():
        try:
            while True:
                try:
                    item = items.get_nowait()
                except queue.Empty:
                    if finished.is_set() or await is_disconnected():
                        break
                    await asyncio.sleep(0.01)
                    continue
                if item is done:
                    break
                yield item
        finally:
            if not finished.is_set():
                await asyncio.to_thread(trigger_cancel, cancelled, hooks)
    return body()
