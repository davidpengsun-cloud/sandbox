"""AI-Generated mode's verified examples: each of the owner's 20 golden questions with the plan the rules certify for
it on the release, committed as clients/<client>/baselines/examples.json and given to the model in every on-mode prompt
(kge/services/ask/engine/planmodel.py, verified_examples). A plan's turn ids are written as "turn", so the file moves
only when a rules plan does:

    python -m kge.estate.examples --write     make examples
    python -m kge.estate.examples --check     exit 1 if the committed file is not what the rules give now
    python -m kge.estate.examples --release   kge verified-examples: the job's, after each release (entrypoint.sh)

On the pilot a release job writes them (--release): the golden questions the client's released rules certify, into the
profile's pack as baselines/examples.json, which the job puts in the store beside its baselines, where the on-mode
prompt reads it. A golden question the client's rules do not certify is left out, never an error there: none certified
is an empty list.
"""
from __future__ import annotations

import argparse
import json
import sys

from kge.estate.golden_sage import golden_20
from kge.pipeline.settings import ROOT
from kge.services.ask.engine.planmodel import EXAMPLES


def turnless(value, turn):
    if isinstance(value, dict):
        return {k: "turn" if k == "turn" and v == turn else turnless(v, turn) for k, v in value.items()}
    if isinstance(value, list):
        return [turnless(v, turn) for v in value]
    return value


def examples(engine, certified_only: bool = False) -> list[dict]:
    """Each golden question and its certified rules plan, asked as the profile's asker (Engine.ASKER, the registry's
    asker role, else the first querying role the load wrote), never a role named here, since a client's querying role
    is its own; a question the rules do not certify is an error, or, with certified_only, left out."""
    out = []
    for item, *_ in golden_20():
        answer = engine.answer(item.question)
        history = answer.get("history") or []
        if answer.get("answer_mode") != "certified" or not history or history[-1].get("plan") is None:
            if certified_only:
                continue
            raise ValueError(f"{item.id} ({item.question}): the rules do not certify a plan for it")
        out.append({"question": item.question, "plan": turnless(history[-1]["plan"], history[-1]["turn_id"])})
    return out


def text(engine, certified_only: bool = False) -> str:
    return json.dumps(examples(engine, certified_only), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    from kge.adapters.graphstore import GraphLocked
    from kge.pipeline.profile import ProfileError, resolve
    from kge.services.ask.engine.core import Engine
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose release to plan on (default: PROFILE, else the registry's default)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write clients/<client>/baselines/examples.json")
    mode.add_argument("--check", action="store_true", help="exit 1 if the committed examples are stale")
    mode.add_argument("--release", action="store_true",
                      help="write the certified ones into the profile's pack's baselines/examples.json (the release job)")
    args = ap.parse_args(argv)
    try:
        prof = resolve(args.profile)
        engine = Engine.serving(prof)
    except (ProfileError, GraphLocked) as exc:
        print(f"examples: {exc}", file=sys.stderr)
        return 2
    path = (prof.clients if args.release else ROOT / "clients") / prof.client / EXAMPLES
    try:
        got = text(engine, certified_only=args.release)
    finally:
        engine.close()
    if args.release:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
        print(f"examples: wrote baselines/examples.json: {len(json.loads(got))} verified examples")
        return 0
    if args.write:
        path.write_text(got)
        print(f"examples: wrote {path.relative_to(ROOT)}: {len(json.loads(got))} verified examples")
        return 0
    if not path.is_file() or path.read_text() != got:
        print(f"examples: {path.relative_to(ROOT)} is stale: run make examples", file=sys.stderr)
        return 1
    print("examples: the verified examples are what the rules certify now")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
