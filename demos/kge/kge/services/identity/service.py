"""Live people, password checks and profile-scoped credentials.

The access table controls people independently of releases. Every credential is
checked against its current membership. Role-scoped in-process agents can use
explicit role credentials without reading the people table. Password generation
keeps unchanged hashes and writes only new plaintext, using the existing private
store files. A deployed service can reload hashes through its password-source port.
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

from kge.pipeline.access_table import ACCESS_ADMIN_LABEL, normalize_username
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


def make_passwords(folder: Path, principals: list[dict], previous: dict[str, str] | None = None,
                   rotate_all: bool = False) -> dict:
    """Keep unchanged people's hashes, add newcomers and remove departed people. With rotate_all, make every person a
    new password. Only new passwords go into passwords.txt; the merged hashes go into passwords.json, both mode 600."""
    previous = previous or {}
    people = [r["principal"] for r in principals if r.get("admin") or person(r["processes"])]
    machines = [r["principal"] for r in principals if not r.get("admin") and not person(r["processes"])]
    added = [p for p in people if rotate_all or p not in previous]
    kept = [p for p in people if p not in added]
    removed = sorted(set(previous) - set(people))
    plain = {p: secrets.token_urlsafe(12) for p in added}
    made = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_private(Path(folder) / PLAIN, f"# Pilot sign-in passwords, made by make passwords at {made}, one per person. The server keeps\n"
                                        f"# only a salted hash of each ({PASSWORDS}). Hand each its owner, then delete this file.\n"
                                        + "".join(f"{p:20} {pw}\n" for p, pw in plain.items()))
    merged = {**{p: previous[p] for p in kept}, **{p: hash_password(pw) for p, pw in plain.items()}}
    write_private(Path(folder) / PASSWORDS, json.dumps(merged, indent=2) + "\n")
    return {"added": added, "kept": kept, "removed": removed, "machines": machines}


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
    def __init__(self, folder: Path, people, sessions: Sessions | SignedSessions | None = None,
                 *, access=None, passwords=None, role_ids=()):
        """Resolve credentials against a live people callback on every request.

        A fixed iterable remains useful for role-scoped agents and isolated callers.
        """
        self.folder = Path(folder)
        self.path = self.folder / "tokens.json"
        self.people = people if callable(people) else lambda: set(people)
        self.role_ids = frozenset(role_ids)
        self.access = access
        self.passwords = passwords
        self.by_token = {}
        self.lock = threading.RLock()
        self._read_tokens()
        for role in role_ids:
            self.token_for(role)
        if not callable(people):
            for who in people:
                self.token_for(who)
        self.sessions = Sessions() if sessions is None else sessions
        self.bearers = SignedBearer(self.folder, sessions.scope) if isinstance(sessions, SignedSessions) else None

    @property
    def known(self) -> set[str]:
        return self.role_ids | set(self.people())

    def allows(self, who: str | None) -> bool:
        return who in self.role_ids or who in self.known

    def _read_tokens(self):
        tokens = json.loads(self.path.read_text()) if self.path.is_file() else {}
        self.by_token = {token: who for who, token in tokens.items()}

    def token_for(self, principal: str) -> str | None:
        if not self.allows(principal):
            return None
        with self.lock:
            token = token_for(self.folder, principal)
            self._read_tokens()
            return token

    def is_admin(self, username: str) -> bool:
        return self.access is not None and self.access.is_admin(username)

    def from_token(self, token: str | None) -> str | None:
        self._read_tokens()
        who = self.by_token.get(token or "")
        if who is None and self.bearers is not None:
            who = self.bearers.get(token)
        return who if self.allows(who) else None

    def bearer_token(self, principal: str) -> dict | None:
        """A password-verified person's portable credential, never a password or a browser session."""
        if self.bearers is None or not self.allows(principal):
            return None
        return {"access_token": self.bearers.open(principal), "token_type": "Bearer", "expires_in": self.bearers.SECONDS}

    def open_session(self, token: str) -> tuple[str, str] | None:
        self._read_tokens()
        principal = self.by_token.get(token)
        if not self.allows(principal):
            return None
        return self.sessions.open(principal), principal

    def demo_session(self, principal: str) -> tuple[str, str] | None:
        """Sign in by name: demo mode's, and pilot sign-in's once the password
        is checked. The server fetches the principal's own token and opens the
        session through it, so every rule downstream still resolves from a
        token. The caller never sees the token."""
        try:
            principal = normalize_username(principal)
        except ValueError:
            return None
        token = self.token_for(principal)
        return self.open_session(token) if token else None

    def check_password(self, username: str, password: str) -> bool:
        """A current member's password, through the reload port or the legacy source."""
        if self.passwords is not None:
            hashes = self.passwords.hashes()
        else:
            given = (settings().SIGNIN_PASSWORDS or "").strip()
            path = self.folder / PASSWORDS
            hashes = json.loads(given) if given else json.loads(path.read_text()) if path.is_file() else {}
        try:
            username = normalize_username(username)
        except ValueError:
            password_matches(None, password)
            return False
        matched = password_matches(hashes.get(username), password)
        return matched and self.allows(username)

    def close_session(self, sid: str | None) -> None:
        self.sessions.close(sid)

    def resolve(self, authorization: str | None, cookie: str | None) -> str | None:
        """The principal of a bearer token, else of the session cookie: a signed session's principal only while the
        deployment still has it."""
        if authorization and authorization.lower().startswith("bearer "):
            return self.from_token(authorization[7:].strip())
        who = self.sessions.get(cookie)
        return who if self.allows(who) else None


_TOKENS_LOCK = threading.RLock()


def token_for(folder: Path, principal: str) -> str:
    """Mint a missing token lazily, preserving every existing person's credential."""
    with _TOKENS_LOCK:
        path = Path(folder) / "tokens.json"
        tokens = json.loads(path.read_text()) if path.is_file() else {}
        if principal not in tokens:
            tokens[principal] = secrets.token_urlsafe(24)
            write_private(path, json.dumps(tokens, indent=2) + "\n")
        return tokens[principal]


def whoami(engine, who: str, identities=None) -> dict:
    """The graph's effective permissions with live display name and memberships."""
    who = normalize_username(who)
    row = engine.g.principal(who)
    people = getattr(identities, "access", None)
    if people is None:
        return {**row, "id": who}
    if people.is_admin(who) and not row.get("role"):
        row = {**row, "role": ACCESS_ADMIN_LABEL}
    return {"reads": [], "processes": [], "writes_terms": False, **row,
            "id": who,
            "name": people.name_of(who), "roles": sorted(people.roles_of(who)),
            "admin": people.is_admin(who)}


def principals(engine, identities) -> list[dict]:
    """Only live people appear in the sign-in picker, including bootstrap admins."""
    if getattr(identities, "access", None) is None:
        return engine.g.principals()
    return [whoami(engine, who, identities) for who in sorted(identities.known)]
