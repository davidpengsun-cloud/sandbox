"""The identity service's command line: the demo tokens, or merged pilot sign-in passwords (kge/services/identity/service.py
says what each is).

    python3 -m kge.services.identity.cli [--profile <id>]               # print the demo tokens (make tokens)
    python3 -m kge.services.identity.cli --passwords [--rotate-all] [--profile <id>]   # pilot passwords: none printed

A module of its own, so that running it never runs service.py a second time beside the copy the package imports."""
from __future__ import annotations

import argparse
import json
import sys

from kge.pipeline.access_table import Access
from kge.adapters.sessionstate import identity_refresher, for_profile as identity_state
from kge.pipeline.roles import authorization_roles
from kge.ports import StoreError
from kge.services.identity.service import PASSWORDS, PLAIN, Identities, make_passwords


def main() -> int:
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose tokens (default: PROFILE, else the registry's default)")
    ap.add_argument("--passwords", action="store_true", help="merge pilot sign-in passwords from the access table")
    ap.add_argument("--rotate-all", action="store_true", help="replace every person's password")
    args = ap.parse_args()
    try:
        prof = resolve(args.profile)
        run = prof.run_dir()
    except ProfileError as exc:
        print(f"identity: {exc}", file=sys.stderr)
        return 2
    try:
        access = Access.for_profile(prof)
        roles = authorization_roles(prof)
        principals = [{"principal": who, "processes": access.role_of_person(who, roles)["processes"],
                       "admin": access.is_admin(who)} for who in sorted(access.people())]
    except (ProfileError, StoreError, OSError, ValueError) as exc:
        print(f"identity: access cannot be read ({type(exc).__name__})", file=sys.stderr)
        return 2
    if args.passwords:
        path = run / PASSWORDS
        if not path.is_file() and not args.rotate_all:
            print(f"passwords: no earlier hashes in {path}: copy the secret's current version there (the guide), or run passwords ROTATE=all",
                  file=sys.stderr)
            return 2
        try:
            previous = json.loads(path.read_text()) if path.is_file() else None
            if path.is_file() and (not isinstance(previous, dict) or
                                   any(not isinstance(v, str) for v in previous.values())):
                raise ValueError("earlier hashes must be a mapping of principals to hashes")
        except (OSError, ValueError) as exc:
            print(f"passwords: earlier hashes in {path} cannot be read ({type(exc).__name__})", file=sys.stderr)
            return 2
        result = make_passwords(run, principals, previous, args.rotate_all)
        print(f"passwords: {len(result['added'])} new, {len(result['kept'])} kept, {len(result['removed'])} removed; hashes in {path}")
        print(f"passwords: the passwords in {run / PLAIN}, which only you can read: hand each its owner, then delete it")
        machines = result["machines"]
        if machines:
            print(f"passwords: none for {', '.join(machines)}: a machine principal (no querying or certification) signs in with its token")
        print("passwords: add passwords.json as the current secret’s next version, hand out passwords.txt, then delete both store files")
        return 0
    state = identity_state(prof)
    with identity_refresher(state):
        ids = Identities(run, [p["principal"] for p in principals], state=state)
        for token, principal in sorted(((t, p) for t, p in ids.by_token.items() if p in ids.known), key=lambda kv: kv[1]):
            print(f"{principal:20} {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
