"""One private JSON object per client, merged with object-store generation preconditions."""
from __future__ import annotations

import json
import time

from kge.adapters.sessionstate.cache import IdentityCache
from kge.ports import ObjectStore, StateConflict, StoreConflict


class StoreSessionState(IdentityCache):
    KEY = "session-state.json"

    def __init__(self, store: ObjectStore, *, cache_seconds=IdentityCache.SECONDS, legacy=False):
        super().__init__(cache_seconds)
        self.store = store
        self._legacy = legacy

    @staticmethod
    def _checked(held):
        if (not isinstance(held, dict) or set(held) != {"tokens", "signed_out"}
                or not isinstance(held["tokens"], dict) or not isinstance(held["signed_out"], dict)
                or any(not isinstance(who, str) or not isinstance(token, str) for who, token in held["tokens"].items())
                or any(not isinstance(sid, str) or type(exp) is not int for sid, exp in held["signed_out"].items())):
            raise ValueError("invalid session state")
        return {"tokens": held["tokens"],
                "signed_out": {sid: exp for sid, exp in held["signed_out"].items() if exp > time.time()}}

    def _read(self):
        for _ in range(8):
            data, version = self.store.get_version(self.KEY)
            if data is not None:
                held = self._checked(json.loads(data))
                self._keep_local_tokens(held["tokens"])
                return held, version
            held = {"tokens": {}, "signed_out": {}}
            if self._legacy:
                previous = {name: self.store.get(f"{name}.json") for name in held}
                if any(value is not None for value in previous.values()):
                    held = self._checked({name: json.loads(value) if value is not None else {}
                                          for name, value in previous.items()})
                    try:
                        version = self.store.put_version(self.KEY, json.dumps(held).encode(), version)
                    except StoreConflict:
                        continue
            return held, version
        raise StateConflict("session state changed concurrently; retry")

    def _keep_local_tokens(self, tokens):
        """Retain the local run-folder contract without losing a peer's newer tokens."""
        if not self._legacy or not tokens:
            return
        for _ in range(8):
            data, version = self.store.get_version("tokens.json")
            previous = self._checked({"tokens": json.loads(data) if data is not None else {}, "signed_out": {}})["tokens"]
            merged = previous | tokens
            if data is not None and merged == previous:
                return
            try:
                self.store.put_version("tokens.json", json.dumps(merged).encode(), version)
                return
            except StoreConflict:
                pass
        raise StateConflict("local tokens changed concurrently; retry")

    def _identity_snapshot(self) -> dict:
        return self._read()[0]

    def _update(self, change):
        for _ in range(8):
            held, version = self._read()
            result = change(held)
            try:
                self.store.put_version(self.KEY, json.dumps(held).encode(), version)
                self._keep_local_tokens(held["tokens"])
                return result
            except StoreConflict:
                pass
        raise StateConflict("session state changed concurrently; retry")

    def _identity_token(self, principal: str, candidate: str) -> str:
        return self._update(lambda held: held["tokens"].setdefault(principal, candidate))

    def _identity_sign_out(self, sid: str, expires: int) -> None:
        self._update(lambda held: held["signed_out"].update({sid: expires}))
