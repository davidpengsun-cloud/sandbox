"""Pilot sign-in (issue #48 item 4): a username and password per person, beside the demo's token sign-in and demo mode.

    .venv/bin/python -m pytest tests/integration/test_signin.py -q

SIGNIN=passwords turns it on (kge/app.py); unset, sign-in is today's: a token, or a name in demo mode from this machine,
and sessions held in the process. make passwords (kge/services/identity/service.py --passwords) makes a password for each person, a
member of the access table bound to querying or certification, and none for a machine principal (pipeline_tagger):
a salted hash of each in the run state's passwords.json, the passwords themselves once in passwords.txt beside it (mode
600), and neither printed. A password opens the session through the principal's own token, as demo mode does. Sign-in
limits attempts per address and principal, including correct passwords while locked; the cookie is HttpOnly,
SameSite and, over HTTPS, Secure; and the session is a cookie the server signs, so it outlives the process, until it is
signed out. The question Q is one enterprise reader may answer across both units and steward is refused, since steward does not
read SME (tests/integration/test_env_access.py).

Each test runs on a scratch copy of the build: the app in this process, and the restart in uvicorn
processes of their own, each on a socket (no port).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from kge.services.identity.service import token_for
from tests.scratch import DEMO, profile_of, registry, run_of, scratch_build, script

Q = "Combined Retail and SME revenue for June 2025"
PEOPLE = {"analyst_retail.meridian@example.com", "analyst_enterprise.meridian@example.com", "finance_reporting.meridian@example.com", "steward_glossary.meridian@example.com", "business_team.meridian@example.com", "advisor_wealth.meridian@example.com", "steward.meridian@example.com", "knowledge_engineer.meridian@example.com", "admin.meridian@example.com"}
MACHINE = "pipeline_tagger.meridian@example.com"
SECRETS = ("tokens.json", "passwords.json", "passwords.txt", "session_secret", "signed_out.json")


@pytest.fixture()
def build(tmp_path):
    from tests.access_fixture import seed_access
    build = scratch_build(tmp_path / "build")
    seed_access(profile_of(build))
    return build


def read_plain(run: Path) -> dict[str, str]:
    """The passwords make passwords wrote to the run state's passwords.txt, by principal, as their owners are handed them."""
    rows = [line.split() for line in (run / "passwords.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
    return {who: password for who, password in rows}


def passwords(build: Path) -> dict[str, str]:
    """make passwords ROTATE=all on build, and the passwords it wrote: every call here is a first generation or a rotation."""
    r = script("kge/services/identity/cli.py", "--passwords", "--rotate-all", build=build)
    assert r.returncode == 0, r.stderr
    return read_plain(run_of(build))


def app_on(build: Path, monkeypatch, passwords: bool = True, demo: bool = False):
    """kge.app serving build's profile as a process starting afresh would: SIGNIN=passwords if passwords, DEMO_MODE=1 if
    demo, and SESSION_SECRET as the test sets it (unset, the run state's key)."""
    import kge.app as appmod
    monkeypatch.setattr(appmod, "PROFILE", profile_of(build))
    monkeypatch.setattr(appmod, "PASSWORDS", passwords)
    monkeypatch.setattr(appmod, "DEMO_MODE", demo)
    restart(appmod, monkeypatch)
    return appmod


def restart(appmod, monkeypatch) -> None:
    """What a restart of the API leaves: nothing it held in memory, its engine, its identities and its sign-in limits."""
    from kge.services.identity.service import Throttle
    for name, value in (("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None), ("THROTTLE", Throttle())):
        monkeypatch.setattr(appmod, name, value)


@contextmanager
def client(appmod, address: str = "203.0.113.7", https: bool = False):
    """The app, its lifespan run, called from address, over HTTPS if https."""
    with TestClient(appmod.app, base_url="https://localhost" if https else "http://localhost", client=(address, 50000)) as c:
        yield c


def also_from(appmod, address: str) -> TestClient:
    """Another caller, from address, on the app a client() already started."""
    return TestClient(appmod.app, client=(address, 50000))


def attributes(set_cookie: str) -> set[str]:
    """A Set-Cookie header's attributes, lower-cased, without the cookie itself."""
    return {a.strip().lower() for a in set_cookie.split(";")[1:]}


@pytest.fixture(autouse=True)
def no_session_secret(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("SIGNIN_PASSWORDS", raising=False)


def test_make_passwords_keeps_salted_hashes_and_writes_the_passwords_once_for_you_alone(build):
    r = script("kge/services/identity/cli.py", "--passwords", "--rotate-all", build=build)
    assert r.returncode == 0, r.stderr
    run = run_of(build)
    plain = read_plain(run)
    assert set(plain) == PEOPLE, "a password for each person, and none for a machine principal"
    for password in plain.values():
        assert len(password) >= 16 and password not in r.stdout and password not in r.stderr, "a password printed"
    assert "passwords.txt" in r.stdout and MACHINE in r.stdout, "it says where they are, and who got none"
    hashes = json.loads((run / "passwords.json").read_text())
    assert set(hashes) == PEOPLE
    for who, stored in hashes.items():
        assert stored.startswith("scrypt$") and plain[who] not in stored
    assert len({stored.split("$")[4] for stored in hashes.values()}) == len(hashes), "each salted on its own"
    for name in ("passwords.txt", "passwords.json"):
        assert stat.S_IMODE((run / name).stat().st_mode) == 0o600, name
    from kge.services.identity.service import password_matches
    assert password_matches(hashes["steward.meridian@example.com"], plain["steward.meridian@example.com"]) and not password_matches(hashes["steward.meridian@example.com"], plain["analyst_enterprise.meridian@example.com"])
    again = passwords(build)
    assert set(again) == PEOPLE and not set(again.values()) & set(plain.values())


def test_a_person_signs_in_with_the_right_password_and_not_with_a_wrong_one(build, monkeypatch):
    """The session is opened through steward's own token, so the rules are steward's; a token still opens a session, and
    a bearer token still answers, as before."""
    pw = passwords(build)
    appmod = app_on(build, monkeypatch)
    with client(appmod) as c:
        assert c.get("/mode").json() == {"demo_mode": False, "passwords": True, "released": True, "message": None}
        assert c.get("/principals").status_code == 401
        for wrong in ({"username": "steward.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]}, {"username": "steward.meridian@example.com", "password": ""},
                      {"username": "nobody@example.com", "password": pw["steward.meridian@example.com"]}):
            r = c.post("/session", json=wrong)
            assert r.status_code == 401 and "set-cookie" not in r.headers, wrong
            assert c.get("/whoami").status_code == 401
        r = c.post("/session", json={"username": "steward.meridian@example.com", "password": pw["steward.meridian@example.com"]})
        assert r.status_code == 200 and r.json()["id"] == "steward.meridian@example.com"
        assert c.get("/whoami").json()["id"] == "steward.meridian@example.com"
        assert {p["id"] for p in c.get("/principals").json()} == PEOPLE | {MACHINE}
        assert c.post("/answer", json={"question": Q}).json()["behaviour"] == "blocked_access"
        assert c.post("/answer", json={"question": Q, "principal": "analyst_enterprise.meridian@example.com"}).status_code == 422, "a body still names no principal"
        assert c.post("/session", json={"token": token_for(run_of(build), "analyst_enterprise.meridian@example.com")}).json()["id"] == "analyst_enterprise.meridian@example.com"
        assert c.get("/whoami", headers={"authorization": f"Bearer {token_for(run_of(build), 'analyst_retail.meridian@example.com')}"}).json()["id"] == "analyst_retail.meridian@example.com"


def test_new_passwords_take_effect_with_no_restart(build, monkeypatch):
    old = passwords(build)
    appmod = app_on(build, monkeypatch)
    with client(appmod) as c:
        new = passwords(build)
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": old["analyst_enterprise.meridian@example.com"]}).status_code == 401
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": new["analyst_enterprise.meridian@example.com"]}).status_code == 200


def test_signin_passwords_from_the_environment_stand_in_for_the_run_state_s(build, monkeypatch):
    """SIGNIN_PASSWORDS, where set, is passwords.json as make passwords wrote it, from the deployment's secret store (the
    pilot's Secret Manager, infra/): the hashes the API checks in its place. Empty, the run state's file, as before."""
    first = passwords(build)
    held = (run_of(build) / "passwords.json").read_text()
    second = passwords(build)
    appmod = app_on(build, monkeypatch)
    monkeypatch.setenv("SIGNIN_PASSWORDS", held)
    with client(appmod) as c:
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": second["analyst_enterprise.meridian@example.com"]}).status_code == 401
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": first["analyst_enterprise.meridian@example.com"]}).status_code == 200
    monkeypatch.setenv("SIGNIN_PASSWORDS", "")
    restart(appmod, monkeypatch)
    with client(appmod) as c:
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": second["analyst_enterprise.meridian@example.com"]}).status_code == 200


def test_a_machine_principal_has_no_password_and_signs_in_with_none(build, monkeypatch):
    """A principal bound to neither querying nor certification is no person, whatever passwords.json holds, so even a
    hash put in by hand for it opens nothing; its token is how it proves who it is."""
    pw = passwords(build)
    run = run_of(build)
    appmod = app_on(build, monkeypatch)
    with client(appmod) as c:
        for password in (pw["analyst_enterprise.meridian@example.com"], ""):
            assert c.post("/session", json={"username": MACHINE, "password": password}).status_code == 401
        from kge.services.identity.service import hash_password
        (run / "passwords.json").write_text(json.dumps({**json.loads((run / "passwords.json").read_text()), MACHINE: hash_password("by-hand")}))
        assert c.post("/session", json={"username": MACHINE, "password": "by-hand"}).status_code == 401
        assert c.get("/whoami").status_code == 401
        assert c.get("/whoami", headers={"authorization": f"Bearer {token_for(run, MACHINE)}"}).json()["id"] == MACHINE


@pytest.mark.parametrize("https", [False, True], ids=["http", "https"])
def test_the_session_cookie_is_httponly_samesite_and_secure_over_https(build, monkeypatch, https):
    pw = passwords(build)
    appmod = app_on(build, monkeypatch)
    with client(appmod, https=https) as c:
        r = c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]})
        assert r.status_code == 200
        flags = attributes(r.headers["set-cookie"])
        assert {"httponly", "samesite=strict"} <= flags and ("secure" in flags) == https, flags
        assert c.get("/whoami").json()["id"] == "analyst_enterprise.meridian@example.com"
        flags = attributes(c.delete("/session").headers["set-cookie"])
        assert ("secure" in flags) == https, "signing out clears the cookie it set"


def test_failures_lock_the_address_principal_pair_including_correct_passwords(build, monkeypatch):
    from kge.services.identity.service import Throttle
    pw = passwords(build)
    appmod = app_on(build, monkeypatch)
    with client(appmod, "198.51.100.1") as c:
        for _ in range(Throttle.FAILURES):
            r = c.post("/session", json={"username": "steward.meridian@example.com", "password": "wrong"})
            assert r.status_code == 401
        assert c.post("/session", json={"username": "steward.meridian@example.com", "password": "wrong"}).status_code == 429
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": "wrong"}).status_code == 401
        assert also_from(appmod, "198.51.100.2").post("/session", json={"username": "steward.meridian@example.com", "password": "wrong"}).status_code == 401
        assert c.post("/session", json={"username": "steward.meridian@example.com", "password": pw["steward.meridian@example.com"]}).status_code == 429
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]}).status_code == 200


class TrustsOne:
    """A trusted caller that trusts one caller token alone: the discovery portal's, as the pilot's verifies its Google ID
    token (tests/unit/test_trusted_caller.py)."""

    def trusts(self, token: str) -> bool:
        return token == "portal"


def test_behind_a_trusted_caller_failures_count_against_the_address_it_names(build, monkeypatch):
    """Every sign-in reaches KGE from the portal's one address. With a caller token KGE trusts, failures count against
    the browser's address the portal names, so one browser's lock leaves another's alone; without one, or with one it
    does not trust, they count against the portal's own address, as before."""
    from kge.services.identity.service import Throttle
    pw = passwords(build)
    appmod = app_on(build, monkeypatch)
    monkeypatch.setattr(appmod, "TRUSTED_CALLER", TrustsOne())
    one, two = ({"x-caller-token": "portal", "x-client-address": a} for a in ("198.51.100.20", "198.51.100.21"))
    wrong = {"username": "steward.meridian@example.com", "password": "wrong"}
    with client(appmod, "192.0.2.5") as portal:
        for _ in range(Throttle.FAILURES):
            assert portal.post("/session", headers=one, json=wrong).status_code == 401
        assert portal.post("/session", headers=one, json=wrong).status_code == 429
        assert portal.post("/session", headers=two, json=wrong).status_code == 401
        assert portal.post("/session", headers=one, json={"username": "steward.meridian@example.com", "password": pw["steward.meridian@example.com"]}).status_code == 429
        untrusted = {**one, "x-caller-token": "forged"}
        for _ in range(Throttle.FAILURES):
            assert portal.post("/session", headers=untrusted, json=wrong).status_code == 401
        assert portal.post("/session", json=wrong).status_code == 429
        assert portal.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]}).status_code == 200


def test_the_throttle_waits_out_its_lockout_and_its_rate():
    from kge.services.identity.service import Throttle
    now = [1000.0]
    t = Throttle(clock=lambda: now[0])
    for _ in range(Throttle.FAILURES - 1):
        assert t.attempt("user", "here") == 0 and t.failed("user", "here") == 0
    assert t.attempt("user", "here") == 0 and t.failed("user", "here") == Throttle.LOCKOUT
    assert t.attempt("user", "there") == Throttle.LOCKOUT, "the username is locked from any address"
    assert t.attempt("other", "here") == Throttle.LOCKOUT, "the address is locked for any username"
    now[0] += Throttle.LOCKOUT - 10
    assert t.attempt("user", "there") == 10
    now[0] += 10
    assert t.attempt("user", "there") == 0, "the wait is over"
    assert t.failed("user", "there") == Throttle.LOCKOUT, "a failure within the window locks it again at once"
    now[0] += Throttle.LOCKOUT
    t.passed("user")
    assert t.attempt("user", "elsewhere") == 0 and t.failed("user", "elsewhere") == 0
    rate = Throttle(clock=lambda: now[0])
    for _ in range(Throttle.RATE):
        assert rate.attempt("busy", "here") == 0
    assert rate.attempt("busy", "here") == 60, "RATE attempts a minute, failed or not"
    now[0] += 60
    assert rate.attempt("busy", "here") == 0


@pytest.fixture()
def sockets():
    """A folder for the APIs' sockets. A socket's path is limited to about a hundred bytes, which a test's temporary folder
    can pass, so it is one of the system's temporary folder's own (as tests/integration/test_profiles.py has it)."""
    d = Path(tempfile.mkdtemp(prefix="kge"))
    if len(str(d / "api.sock")) > 100:
        shutil.rmtree(d)
        d = Path(tempfile.mkdtemp(prefix="kge", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@contextmanager
def served(build: Path, sock: Path, **env):
    """The API for build's profile in a uvicorn process of its own on the socket sock, env beside the build's; a client
    of it. Stopped by its own handle as the block ends."""
    log = sock.with_suffix(".log").open("a")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kge.app:app", "--app-dir", ".", "--uds", str(sock)], cwd=DEMO,
                            env={**os.environ, "PROFILES": str(registry(build)), "PROFILE": "",
                                 "MODEL_PROVIDER": "none", "DEMO_MODE": "", "API_URL": "", "SESSION_SECRET": "", **env},
                            stdout=log, stderr=subprocess.STDOUT)
    http = httpx.Client(transport=httpx.HTTPTransport(uds=str(sock)), base_url="http://served", timeout=120)
    try:
        deadline = time.time() + 120
        while True:
            if proc.poll() is not None:
                pytest.fail(f"the API stopped: {sock.with_suffix('.log').read_text()[-2000:]}", pytrace=False)
            try:
                if sock.exists() and http.get("/profile").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.time() > deadline:
                pytest.fail(f"the API did not start: {sock.with_suffix('.log').read_text()[-2000:]}", pytrace=False)
            time.sleep(0.3)
        yield http
    finally:
        http.close()
        proc.terminate()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def test_a_session_survives_a_restart_of_the_api_until_it_is_signed_out(build, sockets):
    pw = passwords(build)
    sock = sockets / "api.sock"
    with served(build, sock, SIGNIN="passwords") as http:
        assert http.post("/session", json={"username": "analyst_retail.meridian@example.com", "password": pw["analyst_retail.meridian@example.com"]}).status_code == 200
        cookie = dict(http.cookies)
        assert http.get("/whoami").json()["id"] == "analyst_retail.meridian@example.com"
    assert stat.S_IMODE((run_of(build) / "session_secret").stat().st_mode) == 0o600, "its key, in the run state, for you alone"
    with served(build, sock, SIGNIN="passwords") as http:
        http.cookies.update(cookie)
        assert http.get("/whoami").json()["id"] == "analyst_retail.meridian@example.com"
        assert http.post("/answer", json={"question": Q}).json()["behaviour"] == "blocked_access", "still analyst_retail's rules"
        assert http.delete("/session").json() == {"signed_out": True}
        assert http.get("/whoami").status_code == 401
        http.cookies.update(cookie)
        assert http.get("/whoami").status_code == 401
    with served(build, sock, SIGNIN="passwords") as http:
        http.cookies.update(cookie)
        assert http.get("/whoami").status_code == 401


def test_signing_out_ends_the_session_and_a_copy_of_its_cookie_opens_nothing(build, monkeypatch):
    """The copy put back by hand is cleared before signing in again, or it would sit beside the new cookie in the
    client's jar."""
    pw = passwords(build)
    appmod = app_on(build, monkeypatch)
    with client(appmod) as c:
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]}).status_code == 200
        cookie = dict(c.cookies)
        assert c.delete("/session").json() == {"signed_out": True}
        assert c.get("/whoami").status_code == 401
        c.cookies.update(cookie)
        assert c.get("/whoami").status_code == 401
        c.cookies.clear()
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]}).status_code == 200, "signing in again opens a new one"
        assert c.get("/whoami").json()["id"] == "analyst_enterprise.meridian@example.com"


def test_a_proxy_to_a_private_page_sends_google_s_token_beside_the_page_s_own_sign_in(build, monkeypatch):
    """Issue #69 (the kit's guide, A private page): a proxy to a private Cloud Run page sends Google's identity token in
    X-Serverless-Authorization, which Cloud Run checks and the page never reads. In Authorization the page reads it as its
    own sign-in token, ahead of the session cookie: an unknown token, answered 401. Programmatic clients that must send a
    Google OIDC token in Authorization can pass the KGE token in X-KGE-Authorization."""
    pw = passwords(build)
    appmod = app_on(build, monkeypatch)
    google = "Bearer not-a-page-token"
    with client(appmod) as c:
        assert c.post("/session", json={"username": "steward.meridian@example.com", "password": pw["steward.meridian@example.com"]}).status_code == 200
        assert c.get("/whoami", headers={"x-serverless-authorization": google}).json()["id"] == "steward.meridian@example.com"
        assert c.get("/whoami", headers={"authorization": "Basic dXNlcjpwYXNz"}).json()["id"] == "steward.meridian@example.com"
        r = c.get("/whoami", headers={"authorization": google})
        assert r.status_code == 401, "the session cookie is not read beside a bearer token"
        kge_token = token_for(run_of(build), "analyst_enterprise.meridian@example.com")
        r_kge = c.get("/whoami", headers={"authorization": google, "x-kge-authorization": f"Bearer {kge_token}"})
        assert r_kge.status_code == 200 and r_kge.json()["id"] == "analyst_enterprise.meridian@example.com"


def test_session_secret_from_the_environment_signs_the_sessions(build, monkeypatch):
    from kge.services.identity.service import SignedSessions
    pw = passwords(build)
    monkeypatch.setenv("SESSION_SECRET", "k" * 40)
    appmod = app_on(build, monkeypatch)
    with client(appmod) as c:
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": pw["analyst_enterprise.meridian@example.com"]}).status_code == 200
        cookie = dict(c.cookies)
    assert not (run_of(build) / "session_secret").exists(), "the environment's key, so none is made in the run state"
    restart(appmod, monkeypatch)
    with client(appmod) as c:
        c.cookies.update(cookie)
        assert c.get("/whoami").json()["id"] == "analyst_enterprise.meridian@example.com", "the same key: the session holds"
    monkeypatch.setenv("SESSION_SECRET", "m" * 40)
    restart(appmod, monkeypatch)
    with client(appmod) as c:
        c.cookies.update(cookie)
        assert c.get("/whoami").status_code == 401, "another key: no session"
    monkeypatch.setenv("SESSION_SECRET", "short")
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        SignedSessions(run_of(build), profile_of(build).id)


def test_a_signed_session_is_one_profile_s_and_one_principal_s(build):
    """A cookie signed for one profile opens nothing on another's under the same key, and one whose principal the
    deployment no longer has opens nothing; a cookie altered by a byte is no session."""
    from kge.services.identity.service import Identities, SignedSessions
    run = run_of(build)
    principals = sorted(PEOPLE | {MACHINE})
    here = Identities(run, principals, SignedSessions(run, "meridian"))
    sid, who = here.demo_session("analyst_enterprise.meridian@example.com")
    assert who == "analyst_enterprise.meridian@example.com" and here.resolve(None, sid) == "analyst_enterprise.meridian@example.com"
    assert Identities(run, principals, SignedSessions(run, "thornbury")).resolve(None, sid) is None
    assert Identities(run, [p for p in principals if p != "analyst_enterprise.meridian@example.com"], SignedSessions(run, "meridian")).resolve(None, sid) is None
    body, sig = sid.split(".")
    assert here.resolve(None, f"{body}.{sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}") is None
    assert here.resolve(None, "") is None and here.resolve(None, "no-dot") is None


def test_without_signin_sign_in_is_today_s(build, monkeypatch):
    """SIGNIN unset: no password sign-in, even with passwords made; /mode as before; and a session held in the process,
    gone when it restarts."""
    pw = passwords(build)
    appmod = app_on(build, monkeypatch, passwords=False)
    with client(appmod) as c:
        assert c.get("/mode").json() == {"demo_mode": False, "released": True, "message": None}
        assert c.post("/session", json={"username": "steward.meridian@example.com", "password": pw["steward.meridian@example.com"]}).status_code == 422
        r = c.post("/session", json={"token": token_for(run_of(build), "steward.meridian@example.com")})
        assert r.status_code == 200 and r.json()["id"] == "steward.meridian@example.com"
        assert {"httponly", "samesite=strict"} <= attributes(r.headers["set-cookie"]) and "secure" not in attributes(r.headers["set-cookie"])
        cookie = dict(c.cookies)
        assert c.get("/whoami").json()["id"] == "steward.meridian@example.com"
    restart(appmod, monkeypatch)
    with client(appmod) as c:
        c.cookies.update(cookie)
        assert c.get("/whoami").status_code == 401, "a session of today's is the process's"
    assert not (run_of(build) / "session_secret").exists() and not (run_of(build) / "signed_out.json").exists()


@pytest.mark.parametrize("pilot", [False, True], ids=["SIGNIN unset", "SIGNIN=passwords"])
def test_demo_mode_is_unchanged_and_still_refused_from_any_other_address(build, monkeypatch, pilot):
    appmod = app_on(build, monkeypatch, passwords=pilot, demo=True)
    with client(appmod, "127.0.0.1") as c:
        assert c.get("/mode").json() == {"demo_mode": True, **({"passwords": True} if pilot else {}), "released": True, "message": None}
        assert {p["id"] for p in c.get("/principals").json()} == PEOPLE | {MACHINE}
        foreign = {"Host": "evil.example", "Origin": "https://evil.example"}
        assert c.get("/principals", headers=foreign).status_code == 401
        assert c.get("/mode", headers=foreign).json()["demo_mode"] is False
        assert c.post("/session/demo", headers=foreign, json={"principal": "steward.meridian@example.com"}).status_code == 404
        r = c.post("/session/demo", json={"principal": "steward.meridian@example.com"})
        assert r.status_code == 200 and r.json()["id"] == "steward.meridian@example.com" and c.get("/whoami").json()["id"] == "steward.meridian@example.com"
        assert c.post("/answer", json={"question": Q}).json()["behaviour"] == "blocked_access"
        assert c.post("/session/demo", json={"principal": "nobody@example.com"}).status_code == 404
        remote = also_from(appmod, "203.0.113.9")
        assert remote.get("/mode").json() == {"demo_mode": False, **({"passwords": True} if pilot else {}), "released": True, "message": None}
        assert remote.post("/session/demo", json={"principal": "analyst_enterprise.meridian@example.com"}).status_code == 404


def test_signin_is_passwords_or_unset():
    r = subprocess.run([sys.executable, "-c", "import kge.app"], cwd=DEMO, capture_output=True, text=True,
                       env={**os.environ, "SIGNIN": "password", "PROFILES": "", "PROFILE": ""})
    assert r.returncode != 0 and "SIGNIN" in r.stderr and "passwords" in r.stderr, r.stderr[-2000:]


def test_no_sign_in_secret_is_in_any_committed_file():
    """Every secret of sign-in is in the run state, which git ignores: no file of it is committed, nor can be by accident,
    and no token, password, hash or key this build holds is in any committed file. .env.example sets none."""
    from kge.pipeline.envfile import parse
    run = profile_of().run_dir()
    paths = [str((run / name).relative_to(DEMO)) for name in SECRETS]
    ignored = subprocess.run(["git", "check-ignore", *paths], cwd=DEMO, capture_output=True, text=True)
    assert ignored.returncode == 0 and len(ignored.stdout.split()) == len(paths), ignored.stdout + ignored.stderr
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=DEMO, capture_output=True, text=True, check=True).stdout.strip()
    committed = subprocess.run(["git", "ls-files"], cwd=top, capture_output=True, text=True, check=True).stdout.split("\n")
    assert not [p for p in committed if Path(p).name in SECRETS]
    held = []
    if (run / "tokens.json").is_file():
        held += json.loads((run / "tokens.json").read_text()).values()
    if (run / "passwords.json").is_file():
        stored = json.loads((run / "passwords.json").read_text()).values()
        held += [s.split("$")[-1] for s in stored] + [s.split("$")[-2] for s in stored]
    if (run / "passwords.txt").is_file():
        held += read_plain(run).values()
    if (run / "session_secret").is_file():
        held.append((run / "session_secret").read_text().strip())
    if held:
        found = subprocess.run(["git", "grep", "-l", "-F", *[a for s in held for a in ("-e", s)]], cwd=top, capture_output=True, text=True)
        assert found.returncode == 1 and not found.stdout, f"a secret of the run state's is committed in: {found.stdout}"
    example = parse((DEMO / ".env.example").read_text())
    assert example.get("SIGNIN") == "" and example.get("SESSION_SECRET") == "" and example.get("SIGNIN_PASSWORDS") == ""


def test_make_passwords_and_pilot_sign_in_are_documented():
    import re
    mk = (DEMO / "Makefile").read_text()
    assert re.search(r"^\.PHONY:.*\bpasswords\b", mk, re.M)
    assert re.search(r'^passwords:\n\t\$\(KGE\) passwords \$\(KGE_ARGS\) \$\(if \$\(ROTATE\),"ROTATE=\$\(ROTATE\)"\)$', mk, re.M)
    from tests.unit.test_cli import planned
    assert planned("passwords").endswith(" -m kge.services.identity.cli --passwords\n")
    readme = (DEMO / "README.md").read_text()
    for said in ("make passwords", "make serve SIGNIN=passwords", "SESSION_SECRET", "passwords.txt", "passwords.json",
                 "tests/integration/test_signin.py"):
        assert said in readme, said


def test_live_pointer_refreshes_passwords_without_revision_and_returns_safe_503(build, monkeypatch):
    from kge.adapters import signin as adapter
    from kge.adapters.deadline import ExternalCallError
    from kge.adapters.signin.manager import SecretManagerReader
    from kge.services.identity.service import hash_password

    original = passwords(build)
    appmod = app_on(build, monkeypatch)
    now, remote, calls = [0.0], [{"analyst_enterprise.meridian@example.com": hash_password("replacement-password")}], []
    factory = adapter.for_profile

    def source(prof, given):
        held = factory(prof, given)
        held.clock = lambda: now[0]
        return held

    def latest(self, resource):
        calls.append(resource)
        if isinstance(remote[0], Exception):
            raise remote[0]
        return json.dumps(remote[0]).encode()

    monkeypatch.setattr(appmod, "password_source", source)
    monkeypatch.setattr(SecretManagerReader, "latest", latest)
    with client(appmod) as c:
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": original["analyst_enterprise.meridian@example.com"]}).status_code == 200
        pointer = profile_of(build).registry.parent.parent / "deploy" / profile_of(build).client / "passwords-secret"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("projects/test-project/secrets/test-passwords\n")
        now[0] = 60
        assert c.post("/session/token", json={"username": "analyst_enterprise.meridian@example.com", "password": "replacement-password"}).status_code == 200
        assert len(calls) == 1
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": original["analyst_enterprise.meridian@example.com"]}).status_code == 401
        remote[0] = ExternalCallError("Sign-in passwords unavailable: Secret Manager HTTP 403")
        now[0] = 120
        result = c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": "replacement-password"})
        assert result.status_code == 503 and result.json() == {"detail": str(remote[0])}
        remote[0] = {"analyst_enterprise.meridian@example.com": hash_password("next-password")}
        assert c.post("/session", json={"username": "analyst_enterprise.meridian@example.com", "password": "next-password"}).status_code == 200
        assert len(calls) == 3
