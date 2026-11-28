"""The join-time access rule, admin against steward, on the same questions.

    python3 scripts/access_demo.py [--profile <id>]           # opens the profile's graph directly (API stopped)
    python3 scripts/access_demo.py --api http://127.0.0.1:8000

A join across warehouses proceeds only if the requester can read every warehouse
in it. admin reads all four; steward reads retail and wealth. The first question
spans retail and SME, which sums cleanly for admin; the second spans retail and
wealth, both of which steward may read. Without --api the
script opens the graph itself and falls back to the running API if the graph is
locked by it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BLOCKED_Q = "Combined Retail and SME revenue for June 2025"
ALLOWED_Q = "Combined Retail and Wealth revenue for Q2 2025"
BANK_Q = "Total revenue across the bank for the quarter to 30 June 2025"
QUESTIONS = [BLOCKED_Q, ALLOWED_Q, BANK_Q]
USERS = ["admin", "steward"]


class Client:
    """Asks a question either in-process or through the running API, where the identity is the principal's token, not
    a field in the body."""

    def __init__(self, profile, api: str | None = None):
        self.api = api.rstrip("/") if api else None
        self.profile = profile
        self.engine = None
        if self.api is None:
            from kge.services.ask.engine.core import Engine
            from kge.adapters.graphstore import GraphLocked
            try:
                self.engine = Engine.serving(profile)
            except GraphLocked:
                self.api = "http://127.0.0.1:8000"
                print(f"graph is locked by the running API; asking {self.api} instead", file=sys.stderr)

    def ask(self, question: str, principal: str) -> dict:
        if self.engine is not None:
            return self.engine.answer(question, principal=principal)
        from kge.services.identity.service import token_for
        req = urllib.request.Request(f"{self.api}/answer", data=json.dumps({"question": question}).encode(),
                                     headers={"content-type": "application/json", "authorization": f"Bearer {token_for(self.profile.run_dir(), principal)}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())


def summarise(a: dict) -> str:
    b = a.get("behaviour")
    if b == "blocked_access":
        return f"BLOCKED  denied={a['denied_warehouses']}  {a['reason']}"
    if b in ("sum_with_verdict", "sum_without_verdict"):
        return f"{b}  total={a['figure']:,.2f}  parts={a['parts']}"
    if b == "parts_with_reason":
        return f"{b}  parts={a['parts']}  ({a['reason']})"
    if b == "figure":
        return f"figure  {a['figure']:,.2f}  ({a['unit']})"
    return f"{b}  {a.get('reason', '')}"


def main() -> int:
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose graph, or whose tokens for the API (default: PROFILE, else the registry's default)")
    ap.add_argument("--api", help="base URL of the running demo API, e.g. http://127.0.0.1:8000")
    args = ap.parse_args()
    try:
        client = Client(resolve(args.profile), args.api)
    except ProfileError as exc:
        print(f"access_demo: {exc}", file=sys.stderr)
        return 2
    for q in QUESTIONS:
        print(f"\nQ: {q}")
        for user in USERS:
            print(f"  {user:8} {summarise(client.ask(q, user))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
