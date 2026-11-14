"""Optional transactional run records in the client's existing state dataset."""
import uuid
import time

from kge.adapters.appstate import identity_records
from kge.adapters.deadline import ExternalCallError, request_cap, request_io, request_window
from kge.adapters.questionstate.store import checked
from kge.pipeline.questionrun import dumps, loads, read_record, remaining
from kge.ports import StateConflict, StateUnavailable


_prepared = set()


def _rows(data):
    fields = [field["name"] for field in data.get("schema", {}).get("fields", [])]
    return [dict(zip(fields, [cell.get("v") for cell in row.get("f", [])])) for row in data.get("rows", [])]


class BigQueryQuestionRuns:
    def __init__(self, prof):
        self.records = identity_records(prof)

    def table(self, run_id):
        return f"`{self.records._project()}.{self.records.dataset}.question_run_{checked(run_id)}`"

    @request_io
    def query(self, sql, **params):
        for attempt in range(4):
            try:
                return self.records._query(sql, **params)
            except ExternalCallError:
                raise
            except RuntimeError as exc:
                if "stale revision" in str(exc):
                    raise StateConflict("Question run changed concurrently; retry.") from None
                if self.records._aborted(exc):
                    if attempt == 3:
                        raise StateConflict("Question run changed concurrently; retry.") from None
                    self.records._sleep(request_cap(0.05 * 2 ** attempt))
                    continue
                raise StateUnavailable("Question state is temporarily unavailable; retry.") from None

    def prepare(self, run_id):
        table = self.table(run_id)
        if table not in _prepared:
            self.query(f"CREATE TABLE IF NOT EXISTS {table} (id STRING, revision STRING, record STRING) "
                       "AS SELECT @id AS id, '' AS revision, '' AS record", id=checked(run_id))
            if len(_prepared) >= 1024:
                _prepared.pop()
            _prepared.add(table)

    def get(self, run_id):
        with request_window(remaining()):
            self.prepare(run_id)
            rows = _rows(self.query(f"SELECT revision, record FROM {self.table(run_id)} WHERE id=@id AND revision!=''", id=checked(run_id)))
        if len(rows) > 1:
            raise StateConflict("Question run is not unique; retry.")
        return (read_record(loads(rows[0]["record"])), rows[0]["revision"]) if rows else (None, None)

    def put(self, run_id, record, revision):
        with request_window(min(remaining(), record.get("expires_at", time.time() + remaining()) - time.time())):
            self.prepare(run_id)
            new = uuid.uuid4().hex
            self.query(f"""
BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) FROM {self.table(run_id)} WHERE id=@id AND revision=@revision)=1
  AND (SELECT COUNT(*) FROM {self.table(run_id)})=1 AS 'stale revision';
UPDATE {self.table(run_id)} SET revision=@new, record=@record WHERE id=@id AND revision=@revision;
COMMIT TRANSACTION;
""", id=checked(run_id), revision=revision or "", new=new, record=dumps(record))
            return new
