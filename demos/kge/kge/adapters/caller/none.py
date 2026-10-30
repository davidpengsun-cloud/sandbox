"""TrustNoCaller: no caller may name another address; every sign-in counts its caller's own."""
from __future__ import annotations


class TrustNoCaller:
    def trusts(self, token: str) -> bool:
        return False
