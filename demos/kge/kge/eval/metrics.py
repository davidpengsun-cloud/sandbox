"""What the A/B harness measures on each answer, beside the golden scorer.

The scorer (score in kge/eval/run_golden.py) stays strict on what the golden set
states and decides pass or fail. These measures sit beside it, one set per
record (kge/eval/harness.py), and kge/eval/report.py sums them per arm and kind:

  figure_ok    the figure, or each part, or each row of a grouped figure (and a
               ratio beside the parts), equals the agreed one, whatever its path
               or column; null when the item agrees no figure
  behaviour    the behaviour the item expects and the one given, matched
               exactly and by class, the owner's four: answer (a figure, a sum,
               a citation, a definition from the pack's glossary), parts
               (reported apart, not added), refuse (refused,
               or blocked by access) and abstain (abstained, said why a period
               is not released yet, or routed to the steward). A sum where the
               parts must not be added is an answer, so against an item that
               expects parts it is the wrong class: the failure the A/B exists
               to show. An abstain item takes refuse or
               abstain as its class, and exactly only the way it names
  gate         the SQL gate on the queries the answer ran: whether every one
               passed on the first try, how many tries, whether they passed in
               the end; null when no query was gated
  citations    the documents cited, and how many the tier rule may cite: those
               upstream certification landed AI-ready, gold or silver, in the
               landed release the graph was loaded from (load_landing)
  numbers      numbers in a model's wording that the result does not hold:
               caught, as the number check counted them, and shown, the ones
               that reached the reader all the same; null when no model wording
               was checked (a template has none)
  ms, tokens   from the run trace: the run's milliseconds, tokens in and out
  cost_usd     tokens at MODEL_PRICE_IN and MODEL_PRICE_OUT, US dollars per
               million tokens. A run that called no model costs nothing; one
               that did is unknown (null) without a price or a token count,
               never free.
"""
from __future__ import annotations

import os
from pathlib import Path

from kge.eval.run_golden import RATIO, close, same

CLASSES = {"figure": "answer", "sum_with_verdict": "answer", "sum_without_verdict": "answer", "combined": "answer",
           "cite_certified": "answer", "cite": "answer", "glossary_definition": "answer",
           "parts_with_reason": "parts",
           "refuse": "refuse", "blocked_access": "refuse",
           "abstain": "abstain", "abstain_with_reason": "abstain", "abstain_route_steward": "abstain"}
DECLINES = ("refuse", "abstain")
DECLINE = tuple(b for b, c in CLASSES.items() if c in DECLINES)
TIER_CITABLE = ("gold", "silver")
PER_TOKENS = 1_000_000


def load_landing(landing: Path, release: dict | None) -> dict[str, dict]:
    """Upstream certification's rows, by document, from the landed release the graph was loaded from (release: its
    Release node, {id, client}, as Graph.release gives it), in the landing zone (the profile's); empty if the graph
    names none."""
    if not release:
        return {}
    from kge.landing.zone import Zone
    return {r["asset_id"]: r for r in Zone(Path(landing), release["client"]).open(release["id"]).certification()}


def expected_behaviour(item: dict) -> str | None:
    """What the item expects; None for an abstain item that takes any way of declining."""
    kind, exp = item["kind"], item["expect"]
    if kind == "within":
        return "figure"
    if kind == "shelf":
        return exp.get("behaviour", "cite_certified")
    return exp.get("behaviour")


def behaviour_class(b: str | None) -> str:
    return CLASSES.get(b or "", "other")


def behaviour(item: dict, ans: dict) -> dict:
    want, got = expected_behaviour(item), ans.get("behaviour")
    exact = got == want if want else got in DECLINE
    same = behaviour_class(got) in DECLINES if item["kind"] == "abstain" else behaviour_class(got) == behaviour_class(want)
    return {"expected": want, "got": got, "exact": exact, "class": same}


def figure_ok(item: dict, ans: dict) -> bool | None:
    exp = item["expect"]
    if not {"figure", "parts", "rows"} & set(exp):
        return None
    ok = True
    if "figure" in exp:
        ok = close(ans.get("figure"), exp["figure"])
    if "parts" in exp:
        ok = ok and same(ans.get("parts"), exp["parts"])
    if "rows" in exp:
        ok = ok and same(ans.get("rows"), exp["rows"])
    if "ratio" in exp:
        ok = ok and close(ans.get("ratio"), exp["ratio"], RATIO)
    return ok


def gate(ans: dict) -> dict | None:
    """The agent's attempts (one retry with the gate's reasons), or the planner's guards (no retry). The agent's
    tries are its plans, as the run trace counts its model calls in the plan stage: a retry that runs no query
    reaches no gate but is a try all the same, and refuses nothing."""
    attempts = ans.get("attempts") or []
    if attempts:
        calls = (ans.get("run_trace") or {}).get("llm_calls") or []
        tries = max(max(a["try"] for a in attempts), sum(1 for c in calls if c.get("stage") == "plan"))
        return {"queries": len({a["unit"] for a in attempts}), "first_try": all(a["passed"] for a in attempts if a["try"] == 1),
                "tries": tries, "passed": all(a["passed"] for a in attempts if a["try"] == tries)}
    planned = [p for key in ("parts", "plan") if isinstance(ans.get(key), list) for p in ans[key] if isinstance(p, dict) and p.get("guard")]
    if not planned:
        return None
    passed = all(p["guard"]["passed"] for p in planned)
    return {"queries": len(planned), "first_try": passed, "tries": 1, "passed": passed}


def citations(ans: dict, landing: dict[str, dict]) -> dict:
    cited = list(dict.fromkeys(ans.get("cite") or []))

    def citable(doc: str) -> bool:
        row = landing.get(doc)
        return bool(row) and row.get("ai_ready") == "yes" and row.get("tier") in TIER_CITABLE
    off = [d for d in cited if not citable(d)]
    return {"cited": len(cited), "tier_correct": len(cited) - len(off), "off_tier": off}


def numbers(ans: dict) -> dict:
    """Caught: what the number check threw away. Shown: numbers in model wording that reached the reader,
    checked again here by the agent's own rule, which should always be none."""
    rt = ans.get("run_trace") or {}
    check = rt.get("number_check")
    if not check:
        return {"checked": False, "caught": None, "bad_markers": None, "shown": None}
    shown = 0
    worded = (rt.get("agent") or {}).get("mode") in ("model", "mock") or rt.get("llm") is not None
    if worded and not str(ans.get("phrased_by") or "template").startswith("template"):
        from kge.services.ask.engine.agent import carried, supported, wording
        q = ans.get("question") or ""
        stray, bad = wording(ans.get("answer_text") or "", supported(ans, q), {c["n"] for c in ans.get("citations") or []}, carried(ans, q))
        shown = len(stray) + len(bad)
    return {"checked": True, "caught": check["stray_numbers"], "bad_markers": check["bad_markers"], "shown": shown}


def prices(llm=None) -> dict:
    """USD per million tokens, from MODEL_PRICE_IN and MODEL_PRICE_OUT; None where unset."""
    if llm is not None and hasattr(llm, "prices") and (getattr(llm, "tier", "default") != "default" or
                                                        any(v is not None for v in llm.prices.values() if isinstance(v, (float, type(None))))):
        return dict(llm.prices)
    out: dict = {"in": None, "out": None, "currency": "USD", "per_tokens": PER_TOKENS}
    for side in ("in", "out"):
        name = f"MODEL_PRICE_{side.upper()}"
        raw = os.getenv(name, "").strip()
        if raw:
            try:
                out[side] = float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a price in USD per million tokens, not {raw!r}") from None
    return out


def cost(trace: dict, price: dict) -> float | None:
    if not trace.get("llm_calls"):
        return 0.0
    tokens = trace.get("tokens") or {}
    if None in (tokens.get("in"), tokens.get("out"), price.get("in"), price.get("out")):
        return None
    return (tokens["in"] * price["in"] + tokens["out"] * price["out"]) / PER_TOKENS


def measure(item: dict, ans: dict, landing: dict[str, dict], price: dict) -> dict:
    rt = ans.get("run_trace") or {}
    tokens = rt.get("tokens") or {"in": None, "out": None}
    return {"figure_ok": figure_ok(item, ans), "behaviour": behaviour(item, ans), "gate": gate(ans), "citations": citations(ans, landing),
            "numbers": numbers(ans), "ms": rt.get("total_ms"), "tokens": {"in": tokens.get("in"), "out": tokens.get("out")},
            "llm_calls": len(rt.get("llm_calls") or []), "cost_usd": cost(rt, price)}
