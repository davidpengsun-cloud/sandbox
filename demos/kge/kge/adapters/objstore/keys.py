"""Object-store keys: what a key and a prefix may be, whichever backend holds them."""
from __future__ import annotations

PROBE = "kge-probe/absent"


def check(key: str) -> str:
    """key, if it is a relative POSIX path with no empty, . or .. part; ValueError otherwise."""
    if not isinstance(key, str) or not key or key.startswith("/") or "\\" in key or "\0" in key \
            or any(p in ("", ".", "..") for p in key.split("/")):
        raise ValueError(f"{key!r} is not a store key: a relative path, no .. and no leading /")
    return key


def check_prefix(prefix: str) -> str:
    """prefix, "" or a key's leading folders ending in /; ValueError otherwise."""
    if prefix:
        if not prefix.endswith("/"):
            raise ValueError(f"{prefix!r} is not a store prefix: folders ending in /")
        check(prefix[:-1])
    return prefix
