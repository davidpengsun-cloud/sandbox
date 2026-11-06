"""GoogleIdTokens: a caller proves itself with a Google ID token, which this verifies itself (google-auth): its
signature against Google's published keys, its expiry, its audience (KGE's own address, TRUSTED_CALLER_AUDIENCE), its
issuer (Google's accounts) and its e-mail (the service account trusted, TRUSTED_CALLER_EMAIL), verified by Google."""
from __future__ import annotations

from typing import Any, Callable

ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"


class GoogleIdTokens:
    def __init__(self, audience: str, email: str, request: Callable[..., Any] | None = None, certs_url: str = CERTS_URL):
        """audience and email as above; request, google-auth's transport, fetches the keys at certs_url (a stand-in's
        in the tests), else google-auth's own over requests."""
        self.audience = audience
        self.email = email
        self.request = request
        self.certs_url = certs_url

    def trusts(self, token: str) -> bool:
        from google.oauth2 import id_token
        try:
            if self.request is None:
                from google.auth.transport.requests import Request
                self.request = Request()
            claims = id_token.verify_token(token, self.request, audience=self.audience, certs_url=self.certs_url)
        except Exception:
            return False
        return (claims.get("iss") in ISSUERS and claims.get("email") == self.email
                and claims.get("email_verified") is True)
