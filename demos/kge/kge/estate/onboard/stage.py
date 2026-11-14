"""Capture units before an onboard build and put their durable state back if its checks fail."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kge import packstore
from kge.adapters import appstate


@dataclass(frozen=True)
class Captured:
    units: tuple[str, ...]
    files: dict[str, bytes]
    state: object


def capture(prof, units: Iterable[str]) -> Captured:
    """Keep every owned key and the state backend's exact records, including absent units."""
    names = tuple(sorted(set(units)))
    store = packstore.for_profile(prof)
    state = appstate.for_profile(prof, store)
    with state.lock():
        files = {key: store.read(key) for unit in names for key in store.list(f"units/{unit}/")}
        return Captured(names, files, state.capture_units(names))


def restore(prof, captured: Captured) -> None:
    """Undo only the captured units, removing newly created keys and retaining unrelated units."""
    store = packstore.for_profile(prof)
    state = appstate.for_profile(prof, store)
    with state.lock():
        for unit in captured.units:
            for key in store.list(f"units/{unit}/"):
                if key not in captured.files:
                    store.delete(key)
        for key, data in captured.files.items():
            if not store.exists(key) or store.read(key) != data:
                store.write(key, data)
        state.restore_units(captured.state)
