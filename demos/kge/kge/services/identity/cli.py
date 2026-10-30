"""The identity service's command line: the demo tokens, or new pilot sign-in passwords (kge/services/identity/service.py
says what each is).

    python3 -m kge.services.identity.cli [--profile <id>]               # print the demo tokens (make tokens)
    python3 -m kge.services.identity.cli --passwords [--profile <id>]   # new pilot sign-in passwords (make passwords): none printed

A module of its own, so that running it never runs service.py a second time beside the copy the package imports."""
from __future__ import annotations

import argparse
import json
import sys

from kge.services.identity.service import PASSWORDS, PLAIN, Identities, make_passwords


def main() -> int:
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose tokens (default: PROFILE, else the registry's default)")
    ap.add_argument("--passwords", action="store_true", help="make a new pilot sign-in password for each person (make passwords)")
    args = ap.parse_args()
    try:
        run = resolve(args.profile).run_dir()
    except ProfileError as exc:
        print(f"identity: {exc}", file=sys.stderr)
        return 2
    if not (run / "principals.json").is_file():
        print(f"identity: no {run / 'principals.json'}: make graph writes it", file=sys.stderr)
        return 2
    principals = json.loads((run / "principals.json").read_text())
    if args.passwords:
        people, machines = make_passwords(run, principals)
        print(f"passwords: a new one for each of {len(people)} people ({', '.join(people)}), a salted hash of each in {run / PASSWORDS}")
        print(f"passwords: the passwords in {run / PLAIN}, which only you can read: hand each its owner, then delete it")
        if machines:
            print(f"passwords: none for {', '.join(machines)}: a machine principal (no querying or certification) signs in with its token")
        return 0
    ids = Identities(run, [p["principal"] for p in principals])
    for token, principal in sorted(ids.by_token.items(), key=lambda kv: kv[1]):
        print(f"{principal:20} {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
