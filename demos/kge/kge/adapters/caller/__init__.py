"""Who may name the address a sign-in came from, the TrustedCaller port (kge/ports.py): GoogleIdTokens, a Google ID
token for TRUSTED_CALLER_AUDIENCE signed for TRUSTED_CALLER_EMAIL, where both are set (the pilot's, from Terraform), or
TrustNoCaller, where neither is (this machine, Docker Compose). One without the other is refused, naming the missing
one. Callers take one from trusted_caller, never from a backend's module."""
from __future__ import annotations

from kge.adapters.caller.google_idtoken import GoogleIdTokens
from kge.adapters.caller.none import TrustNoCaller
from kge.pipeline.settings import settings
from kge.ports import TrustedCaller

__all__ = ["TrustedCaller", "TrustedCallerSettingError", "trusted_caller"]


class TrustedCallerSettingError(ValueError):
    """TRUSTED_CALLER_AUDIENCE and TRUSTED_CALLER_EMAIL set apart: each needs the other."""


def trusted_caller() -> TrustedCaller:
    audience = (settings().TRUSTED_CALLER_AUDIENCE or "").strip()
    email = (settings().TRUSTED_CALLER_EMAIL or "").strip()
    if audience and email:
        return GoogleIdTokens(audience, email)
    if audience or email:
        missing = "TRUSTED_CALLER_EMAIL" if audience else "TRUSTED_CALLER_AUDIENCE"
        raise TrustedCallerSettingError(f"{missing} is unset: TRUSTED_CALLER_AUDIENCE and TRUSTED_CALLER_EMAIL are set together, or neither")
    return TrustNoCaller()
