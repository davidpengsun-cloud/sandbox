"""The pilot's shadow log: one object an observation under shadow/<client>/<release>/ in the object store, each written
only where no object of its key exists (version "0"), so none is ever overwritten. Keys are per release, so the job that
folds the log (B3, #451) prunes a release by its prefix; the service only adds."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from kge.adapters.shadowlog.record import line_of, release_segment
from kge.ports import ObjectStore, ShadowObservation

PREFIX = "shadow"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoreShadowLog:
    """shadow/<client>/<release>/<UTC time>-<observation id>.json in store."""

    def __init__(self, store: ObjectStore, client: str, clock: Callable[[], datetime] = utc_now):
        self.store = store
        self.prefix = f"{PREFIX}/{release_segment(client)}/"
        self.clock = clock

    def append(self, observation: ShadowObservation) -> None:
        data = line_of(observation)
        now = self.clock()
        key = f"{self.prefix}{observation.release}/{now:%Y%m%dT%H%M%S%fZ}-{observation.id}.json"
        self.store.put_version(key, data, "0")

    def releases(self) -> list[str]:
        return sorted({key[len(self.prefix):].split("/", 1)[0] for key in self.store.list(self.prefix)
                       if "/" in key[len(self.prefix):]})

    def read(self, release: str) -> list[dict]:
        keys = sorted(self.store.list(f"{self.prefix}{release_segment(release)}/"))
        return [json.loads(data) for key in keys if (data := self.store.get(key)) is not None]
