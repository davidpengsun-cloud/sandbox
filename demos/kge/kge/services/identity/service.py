"""Who is asking, decided by the server, never by the request body.

    python3 -m kge.services.identity.cli [--profile <id>]               # print the demo tokens (make tokens)
    python3 -m kge.services.identity.cli --passwords [--profile <id>]   # new pilot sign-in passwords (make passwords): none printed

Both files are the profile's, in its run state (kge/pipeline/profile.py:
Profile.run_dir, build/stores/<client>/run/ in the demo), so a token of one
profile's is no token on another's. The principals are the ones the graph load
wrote to principals.json there: the deployment's identities bound to the
compiled pack's role templates.

Each principal gets a random bearer token, generated on first use into
tokens.json beside it (git-ignored, mode 600). A client proves who it is with
`Authorization: Bearer <token>`, or signs in once with POST /session and
carries an HttpOnly session cookie, named after the profile (session_cookie):
a browser keeps cookies per host, not per port, so two profiles served side by
side to one browser each keep a session of their own. A body that names a
principal is refused.

This is a demo mechanism, not an identity provider: whoever can read
tokens.json can act as anyone, which on a laptop is the operator. In a
real deployment the principal comes from the platform's identity (IAP, OIDC)
and this module is where it would be mapped to a Principal node.

Pilot sign-in (kge/app.py, SIGNIN=passwords) gives each person a password: a
principal bound to querying or certification (person). A machine principal,
bound to neither (the pipeline's tagging identity), gets none and proves who
it is with its token alone. make passwords makes them (make_passwords): a
salted scrypt hash of each in passwords.json, and the passwords themselves,
once, in passwords.txt, which only the user can read (mode 600), both in the
run state beside tokens.json; it prints neither. A password opens the
session through the principal's own token, as demo mode does, and the session
is then a cookie the server signs (SignedSessions), so it outlives the
process. Throttle is sign-in's limits, per username and per client address.
The scrypt cost (SCRYPT) is some 16 MiB and 50 ms a hash. Sessions are signed
with SESSION_SECRET, else a key in session_secret in the run state that only
the user can read; signed_out.json holds the signed sessions signed out before
they expire, each until it would have.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from kge.pipeline.settings import settings

COOKIE = "demo_session"
PASSWORDS = "passwords.json"
PLAIN = "passwords.txt"
SECRET = "session_secret"
SIGNED_OUT = "signed_out.json"
PEOPLE = ("querying", "certification")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def readable_units(engine, who: str) -> list[str]:
    return sorted(engine.g.readable_units(who))


def session_cookie(profile_id: str) -> str:
    """The session cookie of the process serving profile_id: a name of its own per profile, so signing in on one profile's
    page does not replace the session of another's served beside it on the same host."""
    return f"{COOKIE}_{profile_id}"


def person(processes) -> bool:
    """A principal bound to querying or certification: a person, who signs in with a password. A machine principal, bound
    to neither, has its token alone."""
    return any(p in PEOPLE for p in processes)


def write_private(path: Path, text: str) -> None:
    """text into path, which only this user can read or write (mode 600) from the moment it exists: written beside it,
    then renamed over it. The mode is set again on the open file, since one left by a process that stopped part way
    keeps the mode it had."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)
    os.replace(tmp, path)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    """password's salted hash, as passwords.json keeps it: scrypt$<n>$<r>$<p>$<salt>$<hash>, a new random salt unless
    one is given."""
    salt = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return "$".join(["scrypt", str(SCRYPT["n"]), str(SCRYPT["r"]), str(SCRYPT["p"]), _b64(salt), _b64(digest)])


def password_matches(stored: str | None, password: str) -> bool:
    """Whether password is the one stored is the hash of. With none stored, or one unreadable, a hash is made all the
    same, so a name with no password takes as long to refuse as a wrong password."""
    parts = (stored or "").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        hash_password(password, b"\0" * 16)
        return False
    _, n, r, p, salt, digest = parts
    want = _unb64(digest)
    got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(want))
    return hmac.compare_digest(got, want)


def make_passwords(folder: Path, principals: list[dict]) -> tuple[list[str], list[str]]:
    """A new random password for each person among principals (principals.json's rows: principal and processes), every
    earlier one replaced: the passwords into folder/passwords.txt, once, and their salted hashes into
    folder/passwords.json, both mode 600. (The people, the machine principals, who get none.)"""
    people = [r["principal"] for r in principals if person(r["processes"])]
    machines = [r["principal"] for r in principals if not person(r["processes"])]
    plain = {p: secrets.token_urlsafe(12) for p in people}
    made = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_private(Path(folder) / PLAIN, f"# Pilot sign-in passwords, made by make passwords at {made}, one per person. The server keeps\n"
                                        f"# only a salted hash of each ({PASSWORDS}). Hand each its owner, then delete this file.\n"
                                        + "".join(f"{p:20} {pw}\n" for p, pw in plain.items()))
    write_private(Path(folder) / PASSWORDS, json.dumps({p: hash_password(pw) for p, pw in plain.items()}, indent=2) + "\n")
    return people, machines


def session_key(folder: Path) -> bytes:
    """The key sessions are signed with: SESSION_SECRET, 32 characters or more, else the run state's session_secret, made
    on first use (mode 600), so a restarted process reads the sessions its predecessor signed."""
    given = (settings().SESSION_SECRET or "").strip()
    if given:
        if len(given) < 32:
            raise ValueError("SESSION_SECRET is too short to sign sessions with: give 32 characters or more, or leave it "
                             f"empty for the run state's own ({SECRET})")
        return given.encode()
    path = Path(folder) / SECRET
    if not path.is_file():
        write_private(path, secrets.token_urlsafe(32) + "\n")
    return path.read_text().strip().encode()


class Sessions:
    """Sessions held in this process, as the demo has them: a random id each, gone when the process stops."""

    def __init__(self):
        self.held: dict[str, str] = {}

    def open(self, principal: str) -> str:
        sid = secrets.token_urlsafe(24)
        self.held[sid] = principal
        return sid

    def get(self, sid: str | None) -> str | None:
        return self.held.get(sid or "")

    def close(self, sid: str | None) -> None:
        self.held.pop(sid or "", None)


class SignedSessions:
    """Sessions that outlive the process, for pilot sign-in: the cookie is the session, its principal, the profile, an id
    and when it expires (HOURS after it opened), signed with HMAC-SHA256 under session_key, so a restarted process, or
    another with the same key, reads it, and nothing else does: a cookie of another profile's, or one changed by a byte,
    is no session. Signing out records its id in folder/signed_out.json until it would have expired, so a copy of the
    cookie kept after signing out opens nothing, after a restart too."""
    HOURS = 12

    def __init__(self, folder: Path, scope: str):
        self.scope = scope
        self.key = session_key(folder)
        self.out = Path(folder) / SIGNED_OUT
        held = json.loads(self.out.read_text()) if self.out.is_file() else {}
        self.signed_out = {sid: exp for sid, exp in held.items() if exp > time.time()}
        self.lock = threading.Lock()

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())

    def open(self, principal: str) -> str:
        fields = {"p": principal, "s": self.scope, "id": secrets.token_urlsafe(16), "exp": int(time.time()) + int(self.HOURS * 3600)}
        body = _b64(json.dumps(fields, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def said(self, sid: str | None) -> dict | None:
        """What sid says, if this key signed it for this profile and it is neither expired nor signed out; else None."""
        body, _, sig = (sid or "").partition(".")
        if not body or not hmac.compare_digest(sig.encode(), self._sign(body).encode()):
            return None
        try:
            c = json.loads(_unb64(body))
        except ValueError:
            return None
        if not isinstance(c, dict) or c.get("s") != self.scope or not isinstance(c.get("exp"), int) or c["exp"] <= time.time():
            return None
        return None if c.get("id") in self.signed_out else c

    def get(self, sid: str | None) -> str | None:
        c = self.said(sid)
        return c["p"] if c else None

    def close(self, sid: str | None) -> None:
        c = self.said(sid)
        if c is None:
            return
        with self.lock:
            now = time.time()
            self.signed_out = {s: exp for s, exp in self.signed_out.items() if exp > now} | {c["id"]: c["exp"]}
            write_private(self.out, json.dumps(self.signed_out) + "\n")


class SignedBearer(SignedSessions):
    """A fifteen-minute bearer credential under the session key, scoped separately from browser cookies."""
    SECONDS = 900
    HOURS = SECONDS / 3600

    def __init__(self, folder: Path, scope: str):
        super().__init__(folder, f"bearer:{scope}")


class Throttle:
    """Sign-in's limits, per key: a username, and the client's address. At most RATE attempts a minute; FAILURES failed
    ones within WINDOW seconds lock the key for LOCKOUT seconds from the last, and each further failure while they are
    in the window locks it again. Held in this process: a restart forgets them."""
    RATE, FAILURES, WINDOW, LOCKOUT = 10, 5, 900, 300
    KEEP = 10_000

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.tries: dict[str, list[float]] = {}
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def _wait(self, key: str, now: float) -> float:
        tries = [t for t in self.tries.pop(key, []) if now - t < 60]
        fails = [t for t in self.fails.pop(key, []) if now - t < self.WINDOW]
        if tries:
            self.tries[key] = tries
        if fails:
            self.fails[key] = fails
        rate = tries[-self.RATE] + 60 - now if len(tries) >= self.RATE else 0
        locked = fails[-1] + self.LOCKOUT - now if len(fails) >= self.FAILURES else 0
        return max(rate, locked, 0)

    def attempt(self, *keys: str) -> int:
        """0, and the attempt counted against each key, if keys may try now; else the seconds to wait, nothing counted."""
        with self.lock:
            now = self.clock()
            if len(self.tries) + len(self.fails) > self.KEEP:
                for k in set(self.tries) | set(self.fails):
                    self._wait(k, now)
            wait = max(self._wait(k, now) for k in keys)
            if wait == 0:
                for k in keys:
                    self.tries.setdefault(k, []).append(now)
            return math.ceil(wait)

    def failed(self, *keys: str) -> int:
        """A failed attempt, counted against each key: the seconds they must now wait (0 while none is locked)."""
        with self.lock:
            now = self.clock()
            for k in keys:
                self.fails.setdefault(k, []).append(now)
            return math.ceil(max(self._wait(k, now) for k in keys))

    def passed(self, key: str) -> None:
        """A sign-in that passed: this address-principal pair’s failed attempts forgotten."""
        with self.lock:
            self.fails.pop(key, None)
            self.tries.pop(key, None)


class Identities:
    def __init__(self, folder: Path, principals: list[str], sessions: Sessions | SignedSessions | None = None):
        """The tokens of principals, kept in folder/tokens.json: folder is the profile's run state. Its sessions are held
        in the process unless sessions says otherwise (SignedSessions, for pilot sign-in)."""
        self.folder = Path(folder)
        self.path = self.folder / "tokens.json"
        tokens = json.loads(self.path.read_text()) if self.path.exists() else {}
        missing = [p for p in principals if p not in tokens]
        for p in missing:
            tokens[p] = secrets.token_urlsafe(24)
        if missing or not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(tokens, indent=2))
            os.chmod(self.path, 0o600)
        self.by_token = {t: p for p, t in tokens.items() if p in principals}
        self.known = set(self.by_token.values())
        self.sessions = Sessions() if sessions is None else sessions
        self.bearers = SignedBearer(self.folder, sessions.scope) if isinstance(sessions, SignedSessions) else None

    def from_token(self, token: str | None) -> str | None:
        who = self.by_token.get(token or "")
        if who is None and self.bearers is not None:
            who = self.bearers.get(token)
        return who if who in self.known else None

    def bearer_token(self, principal: str) -> dict | None:
        """A password-verified person's portable credential, never a password or a browser session."""
        if self.bearers is None or principal not in self.known:
            return None
        return {"access_token": self.bearers.open(principal), "token_type": "Bearer", "expires_in": self.bearers.SECONDS}

    def open_session(self, token: str) -> tuple[str, str] | None:
        principal = self.by_token.get(token)
        if principal not in self.known:
            return None
        return self.sessions.open(principal), principal

    def demo_session(self, principal: str) -> tuple[str, str] | None:
        """Sign in by name: demo mode's, and pilot sign-in's once the password
        is checked. The server fetches the principal's own token and opens the
        session through it, so every rule downstream still resolves from a
        token. The caller never sees the token."""
        token = next((t for t, p in self.by_token.items() if p == principal), None)
        return self.open_session(token) if token else None

    def check_password(self, username: str, password: str) -> bool:
        """Whether password is the one make passwords made for username, read from the run state's passwords.json at
        each call, so new passwords take effect with no restart, or from SIGNIN_PASSWORDS where it is set: that file's
        content, from the deployment's secret store (the pilot's Secret Manager, infra/). False for a name with none, an
        unknown one or a machine principal's, which takes as long."""
        given = (settings().SIGNIN_PASSWORDS or "").strip()
        path = self.folder / PASSWORDS
        hashes = json.loads(given) if given else json.loads(path.read_text()) if path.is_file() else {}
        return password_matches(hashes.get(username), password)

    def close_session(self, sid: str | None) -> None:
        self.sessions.close(sid)

    def resolve(self, authorization: str | None, cookie: str | None) -> str | None:
        """The principal of a bearer token, else of the session cookie: a signed session's principal only while the
        deployment still has it."""
        if authorization and authorization.lower().startswith("bearer "):
            return self.from_token(authorization[7:].strip())
        who = self.sessions.get(cookie)
        return who if who in self.known else None


def token_for(folder: Path, principal: str) -> str:
    """principal's token, from folder/tokens.json: folder is the profile's run state."""
    return json.loads((Path(folder) / "tokens.json").read_text())[principal]


def whoami(engine, who: str) -> dict:
    """A principal as the graph holds it (its id, its roles and what each lets it do), or its id alone for one it does not
    hold."""
    return engine.g.principal(who)
