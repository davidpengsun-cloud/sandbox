from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from kge.services.identity import routes
from kge.services.identity.service import Throttle


def test_locked_pair_never_hashes_password_and_can_sign_in_after_expiry(monkeypatch):
    now = [1000]
    throttle = Throttle(clock=lambda: now[0])
    checks = []
    ids = SimpleNamespace(known={"reader"}, check_password=lambda user, password: checks.append(password) or password == "right",
                          demo_session=lambda user: ("session", user))
    monkeypatch.setattr(routes, "served", SimpleNamespace(PASSWORDS=True, THROTTLE=throttle, ids=lambda: ids,
                        engine=lambda: SimpleNamespace(g=SimpleNamespace(processes=lambda user: ["querying"]))))
    monkeypatch.setattr(routes, "client_address", lambda req: "client")
    monkeypatch.setattr(routes, "signed_in", lambda req, sid, who: who)
    request = Request({"type": "http", "headers": []})
    def attempt(password):
        return routes.password_sign_in(routes.PasswordSignIn(username="reader", password=password), request)
    for _ in range(throttle.FAILURES):
        with pytest.raises(HTTPException) as caught:
            attempt("wrong")
        assert caught.value.status_code == 401
    assert caught.value.headers == {"Retry-After": "300"}
    for password in ("wrong", "right"):
        with pytest.raises(HTTPException) as caught:
            attempt(password)
        assert caught.value.status_code == 429 and caught.value.headers == {"Retry-After": "300"}
    assert checks == ["wrong"] * throttle.FAILURES
    now[0] += throttle.LOCKOUT
    assert attempt("right") == "reader"
