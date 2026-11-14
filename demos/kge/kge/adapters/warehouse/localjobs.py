"""Local warehouse workers; handles survive independent adapter objects in this process."""
from concurrent.futures import Future
from threading import Lock, Thread, Timer
import time

from kge.pipeline.questionrun import remaining

_jobs = {}
_lock = Lock()


def submit(job_id, compute):
    started = time.time()
    handle = {"id": job_id, "expires": started + remaining()}
    with _lock:
        if job_id in _jobs:
            return {"id": job_id, "expires": _jobs[job_id][5].expires}
        future = Future()
        finished = [None]
        hooks, stopping = [], [False]
        watchdog = Timer(max(0, handle["expires"] - started), cancel, args=(handle,))
        watchdog.daemon = True
        watchdog.expires = handle["expires"]
        _jobs[job_id] = (future, started, finished, hooks, stopping, watchdog)
    watchdog.start()
    def run():
        if not future.set_running_or_notify_cancel():
            return
        try:
            rows = compute(hooks.append)
            finished[0] = time.time()
            future.set_result(rows)
        except BaseException as exc:
            finished[0] = time.time()
            future.set_exception(exc)
    Thread(target=run, daemon=True, name="warehouse-local").start()
    return handle


def poll(handle):
    if "result" in handle:
        return handle["result"]
    with _lock:
        held = _jobs.get(handle["id"])
    if held is None:
        if time.time() >= handle.get("expires", float("inf")):
            return {"state": "failed", "error": "The warehouse exceeded its time limit; ask a narrower question."}
        return {"state": "failed", "error": "The local warehouse restarted; ask the question again."}
    future, started, finished, _, _, _ = held
    progress = {"elapsed_ms": max(0, int(((finished[0] or time.time()) - started) * 1000)), "bytes_processed": None, "slot_ms": None}
    if not future.done():
        return {"state": "running", "progress": progress}
    if finished[0] >= handle["expires"]:
        result = {"state": "failed", "error": "The warehouse exceeded its time limit; ask a narrower question.", "progress": progress}
    else:
        try:
            rows = future.result()
            result = {"state": "done", "rows": rows, "progress": progress}
        except BaseException:
            result = {"state": "failed", "error": "The warehouse could not finish this question.", "progress": progress}
    handle["result"] = result
    return result


def release(handle):
    with _lock:
        held = _jobs.get(handle["id"])
        if held is not None and held[0].done():
            _jobs.pop(handle["id"])
            held[5].cancel()


def cancel(handle):
    with _lock:
        held = _jobs.pop(handle["id"], None)
        if held is None or held[4][0]:
            return
        held[4][0] = True
    future, _, finished, hooks, _, watchdog = held
    watchdog.cancel()
    if future.cancel():
        finished[0] = time.time()
        return
    def interrupt():
        while not future.done():
            for hook in list(hooks):
                try:
                    hook()
                except Exception:
                    pass
            time.sleep(.01)
    Thread(target=interrupt, daemon=True, name="warehouse-cancel").start()
