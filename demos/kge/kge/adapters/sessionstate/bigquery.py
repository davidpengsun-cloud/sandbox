"""Optional BigQuery session storage, reusing the existing transactional app-state records."""
from kge.adapters.appstate import identity_records
from kge.adapters.sessionstate.cache import IdentityCache


class BigQuerySessionState(IdentityCache):
    def __init__(self, prof):
        super().__init__()
        self._records = identity_records(prof)

    def _prepare_identity(self):
        self._records._prepare_identity()

    def _identity_snapshot(self) -> dict:
        return self._records._identity_snapshot()

    def _identity_token(self, principal: str, candidate: str) -> str:
        return self._records._identity_token(principal, candidate)

    def _identity_sign_out(self, sid: str, expires: int) -> None:
        self._records._identity_sign_out(sid, expires)
