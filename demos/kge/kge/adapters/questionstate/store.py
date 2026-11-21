"""Private run objects with generation preconditions, shared through the store bucket."""
import re

from kge.adapters.deadline import request_io, request_window
from kge.pipeline.questionrun import dumps, loads, read_record, remaining
from kge.ports import StateConflict, StoreConflict


def checked(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("invalid question run id")
    return run_id


class StoreQuestionRuns:
    def __init__(self, store):
        self.store = store

    def get(self, run_id):
        with request_window(remaining()):
            data, revision = request_io(self.store.get_version)(checked(run_id) + ".json")
        return read_record(loads(data) if data is not None else None), revision

    def put(self, run_id, record, revision):
        import time
        try:
            with request_window(min(remaining(), record.get("expires_at", time.time() + remaining()) - time.time())):
                return request_io(self.store.put_version)(checked(run_id) + ".json", dumps(record).encode(), revision or "0")
        except StoreConflict:
            raise StateConflict("Question run changed concurrently; retry.") from None
