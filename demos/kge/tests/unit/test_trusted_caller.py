"""Who may name the address a sign-in came from (the TrustedCaller port, kge/adapters/caller/): a Google ID token KGE
verifies itself, signature, expiry, audience, issuer and e-mail, here against a key of the test's own whose public half a
stand-in transport serves as Google's; the factory's settings; and the address pilot sign-in counts
(kge/services/identity/routes.py client_address): the named one for a trusted token, the caller's own otherwise."""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt
from starlette.requests import Request

from kge.adapters.caller import TrustedCallerSettingError, trusted_caller
from kge.adapters.caller.google_idtoken import GoogleIdTokens
from kge.adapters.caller.none import TrustNoCaller
from kge.ports import TrustedCaller

AUDIENCE = "https://kge-meridian-42.europe-west2.run.app"
EMAIL = "kge-meridian@example.com"
CERTS = "https://certs.example.invalid/oauth2/v1/certs"


def _key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return crypt.RSASigner.from_string(pem, key_id="k1"), public.decode()


SIGNER, PUBLIC = _key()
OTHER_SIGNER, _ = _key()


class Response:
    status = 200

    def __init__(self, body: bytes):
        self.data = body


class Transport:
    """Google's keys, as google-auth fetches them: the test's public key under its id."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, method="GET", **_kwargs):
        self.calls.append(url)
        return Response(json.dumps({"k1": PUBLIC}).encode())


def token(signer=SIGNER, **changes) -> str:
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": AUDIENCE, "email": EMAIL, "email_verified": True,
              "sub": "1", "iat": now, "exp": now + 3600, **changes}
    return jwt.encode(signer, {k: v for k, v in claims.items() if v is not None}).decode()


def verifier() -> GoogleIdTokens:
    return GoogleIdTokens(AUDIENCE, EMAIL, request=Transport(), certs_url=CERTS)


def test_a_valid_token_for_kges_address_signed_for_the_trusted_account_is_trusted():
    v = verifier()
    assert isinstance(v, TrustedCaller)
    assert v.trusts(token()) and v.trusts(token(iss="accounts.google.com"))
    assert v.request.calls == [CERTS, CERTS], "the keys fetched from the URL given, for each token"


@pytest.mark.parametrize("bad", [
    {"aud": "https://kge-thornbury-42.europe-west2.run.app"},
    {"email": "someone-else@example.com"},
    {"iss": "https://issuer.example.invalid"},
    {"email_verified": False},
    {"email_verified": None},
    {"email": None},
    {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
])
def test_a_token_for_another_audience_account_or_issuer_unverified_or_expired_is_not_trusted(bad):
    assert not verifier().trusts(token(**bad))


def test_a_token_signed_by_another_key_tampered_or_not_a_token_is_not_trusted():
    v = verifier()
    assert not v.trusts(token(signer=OTHER_SIGNER))
    head, body, signature = token().split(".")
    forged = json.dumps({"iss": "https://accounts.google.com", "aud": AUDIENCE, "email": EMAIL, "email_verified": True,
                         "exp": int(time.time()) + 3600, "iat": int(time.time())}).encode()
    assert not v.trusts(".".join([head, base64.urlsafe_b64encode(forged).decode().rstrip("="), signature]))
    assert not v.trusts("") and not v.trusts("not a token")


def test_the_factory_trusts_no_caller_unless_both_settings_are_set_and_refuses_one_alone(monkeypatch):
    monkeypatch.delenv("TRUSTED_CALLER_AUDIENCE", raising=False)
    monkeypatch.delenv("TRUSTED_CALLER_EMAIL", raising=False)
    nobody = trusted_caller()
    assert isinstance(nobody, TrustNoCaller) and not nobody.trusts(token())
    monkeypatch.setenv("TRUSTED_CALLER_AUDIENCE", AUDIENCE)
    with pytest.raises(TrustedCallerSettingError, match="TRUSTED_CALLER_EMAIL is unset"):
        trusted_caller()
    monkeypatch.setenv("TRUSTED_CALLER_EMAIL", EMAIL)
    google = trusted_caller()
    assert isinstance(google, GoogleIdTokens) and (google.audience, google.email) == (AUDIENCE, EMAIL)
    monkeypatch.delenv("TRUSTED_CALLER_AUDIENCE")
    with pytest.raises(TrustedCallerSettingError, match="TRUSTED_CALLER_AUDIENCE is unset"):
        trusted_caller()


class StandIn:
    """A trusted caller that trusts one token alone."""

    def trusts(self, token: str) -> bool:
        return token == "good"


def request(peer: str, **headers: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/session", "client": (peer, 50000),
                    "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]})


@pytest.mark.parametrize("trusted, headers, counted", [
    (StandIn(), {"x_caller_token": "good", "x_client_address": "198.51.100.9"}, "198.51.100.9"),
    (StandIn(), {"x_caller_token": "bad", "x_client_address": "198.51.100.9"}, "192.0.2.2"),
    (StandIn(), {"x_client_address": "198.51.100.9"}, "192.0.2.2"),
    (StandIn(), {"x_caller_token": "good"}, "192.0.2.2"),
    (StandIn(), {"x_caller_token": "good", "x_client_address": "x" * 65}, "192.0.2.2"),
    (StandIn(), {}, "192.0.2.2"),
    (TrustNoCaller(), {"x_caller_token": "good", "x_client_address": "198.51.100.9"}, "192.0.2.2"),
])
def test_sign_in_counts_the_named_address_only_for_a_trusted_token(monkeypatch, trusted, headers, counted):
    import kge.app as appmod
    from kge.services.identity.routes import client_address
    monkeypatch.setattr(appmod, "TRUSTED_CALLER", trusted)
    assert client_address(request("192.0.2.2", **headers)) == counted
