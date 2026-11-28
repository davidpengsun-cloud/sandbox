"""The evaluation suite's generator (#707 requirement 16; PR #724's follow-up 1): it rebuilds
portal/server/test/fixtures/cerebro-evaluation-suite.json from Meridian's build.

The fixture itself is the generator's input for what no build can give again: each wording, the review files it came
from, the hotfix's recorded outcomes and the 0.14.58 card. Everything KGE says is recorded afresh, in two steps, from
the demo's folder after `make graph pipeline`, with the API stopped:

    python -m tests.cerebro_suite record <work>/part0.jsonl 0 3    (one process a part, side by side)
    python -m tests.cerebro_suite assemble <work>/part0.jsonl <work>/part1.jsonl <work>/part2.jsonl

`record` asks, for its share of the wordings, as the suite's asker and with no model: the terms ask's plan reads, and
get_context's bindings and open items (openitems.report), each on a copy of the build of its own. `assemble` writes the
fixture: each wording with KGE's whole reading (kge.reading), and, once for the suite, what Cerebro's one step before
ask reads beside a reading (kge_tools): map_term's meanings of each name a term was bound by, and search's filter
phrases for the words written with such a name.

What Cerebro does with a reading is not KGE's to say, so it is not written here: each wording's expected outcome, the
reading of its recorded hotfix outcome and its verdicts are the portal suite's (PORTAL_SUITE below:
portal/server/test/cerebro-suite.test.js, whose oracle derives the expected outcome from #707's requirements, writes
them with SUITE_RECORD=1 and grades them without it), and assemble keeps them as they stand.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from kge.pipeline.settings import ROOT

FIXTURE = ROOT / "portal" / "server" / "test" / "fixtures" / "cerebro-evaluation-suite.json"
ASKER = "analyst_enterprise.meridian@example.com"
PORTAL_SUITE = ("expected", "hotfix_reading", "verdicts")
"""The keys of a wording the portal's suite writes; assemble carries them over unchanged."""


def suite() -> dict:
    return json.loads(FIXTURE.read_text())


def scratch_engine():
    """An engine on a copy of the demo's build, so parts run side by side: the graph store lets one process open a
    graph at a time."""
    from tests.scratch import open_engine, scratch_build
    return open_engine(scratch_build(Path(tempfile.mkdtemp(prefix="cerebro-suite-")) / "build"))


def asks_plan_terms(engine, question) -> list[str] | None:
    """The terms of the plan ask executes for question, read from the run trace's ledger block; None where ask
    executes no plan."""
    from kge.services.ask.engine import planledger
    held = {}
    block = planledger.block

    def recorded(question, turn, vocabulary, reply, result, follow_up):
        held["read"] = (vocabulary, result)
        return block(question, turn, vocabulary, reply, result, follow_up)
    planledger.block = recorded
    try:
        engine.answer(question, ASKER)
    finally:
        planledger.block = block
    if "read" not in held:
        return None
    vocabulary, result = held["read"]
    if not isinstance(result, dict) or result.get("kind") != "result" or not isinstance(result.get("plan"), dict):
        return None
    return sorted({vocabulary.contexts[m["context"]["id"]].term for m in result["plan"].get("measures", [])
                   if m["context"]["id"] in vocabulary.contexts})


def record(out: Path, part: int, parts: int) -> None:
    """One line a wording of this part: its question, ask's plan terms and KGE's reading."""
    os.environ["MODEL_PROVIDER"] = "none"
    from kge.services.ask.engine.openitems import report
    engine = scratch_engine()
    with out.open("w") as lines:
        for at, wording in enumerate(suite()["wordings"]):
            if at % parts != part:
                continue
            question = wording["question"]
            lines.write(json.dumps({"question": question, "ask_terms": asks_plan_terms(engine, question),
                                    "reading": report(engine, question, ASKER)}, ensure_ascii=False) + "\n")
            lines.flush()
    engine.close()


def words(text: str) -> list[str]:
    """A text's words, as the portal's step splits them: runs of letters and digits."""
    return re.findall(r"[^\W_]+", text)


def named_by(reading: dict) -> tuple[set[str], set[str]]:
    """The names and the qualifiers the step may ask KGE about for one reading: the last word a term was bound by
    explicitly and each open term's own text (map_term), and the other words written with that name (search)."""
    names = {item["text"] for item in reading["open_items"] if item["item"] == "term" and item.get("text")}
    qualifiers = set()
    said: dict[str, list[str]] = {}
    for binding in sorted((b for b in reading["bindings"] if b.get("start") is not None), key=lambda b: b["start"]):
        if binding["kind"] == "term" and binding.get("by") == "explicit" and binding.get("term"):
            said.setdefault(binding["term"], []).extend(words(binding["text"]))
    for spoken in said.values():
        if not spoken:
            continue
        head = spoken[-1]
        names.add(head)
        qualifier = " ".join(word for word in spoken if word.lower() != head.lower())
        if qualifier:
            qualifiers.add(qualifier)
    return names, qualifiers


def compact(reading: dict, options: dict[str, int], table: list) -> dict:
    """A reading with each option list kept once for the suite (kge_options) and named by its place there; a
    binding's own options, which the portal does not read, are left out."""
    def kept(listed):
        key = json.dumps(listed, sort_keys=True, ensure_ascii=False)
        if key not in options:
            options[key] = len(table)
            table.append(listed)
        return options[key]
    return {"bindings": [{k: v for k, v in binding.items() if k != "options"} for binding in reading["bindings"]],
            "open_items": [{**{k: v for k, v in item.items() if k != "options"}, "options": kept(item.get("options") or [])}
                           for item in reading["open_items"]]}


def one_line(value) -> str:
    """A value as the portal's own JSON.stringify writes it, so either side rewrites the fixture to the same bytes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write(fixture: dict) -> None:
    """The fixture, a line a top-level key and a line a wording."""
    head = [f"{one_line(key)}:{one_line(value)}" for key, value in fixture.items() if key != "wordings"]
    lines = ",\n".join(one_line(wording) for wording in fixture["wordings"])
    FIXTURE.write_text("{\n" + ",\n".join(head) + ",\n\"wordings\":[\n" + lines + "\n]\n}\n")


ABOUT = ("#707 requirement 16: the evaluation suite. Every wording collected in the #697-#700 hotfix reviews (r4-r9), the golden 20 "
         "and #707's acceptance wordings, with the sources it came from, the hotfix's recorded outcomes at its head (file: outcome, "
         "as the review recorded it), the terms ask's plan reads with MODEL_PROVIDER=none (null where ask executes no plan), and what "
         "KGE's get_context reports with open_items=true for analyst_enterprise: the terms its bindings name, the kinds of its open "
         "items, and the whole reading (kge.reading: every binding and open item, an item's options named by their place in "
         "kge_options). kge_tools holds what Cerebro's one step before ask reads beside a reading: map_term's resolves_to for a name, "
         "and the titles of search's filter hits for a qualifier. KGE's tests (tests/integration/test_open_items.py) grade the "
         "kge_sample, or every wording with SUITE=all. Through Cerebro (portal/server/test/cerebro-suite.test.js), every wording is "
         "graded against expected: what Cerebro does before its first ask (none: no card, the question goes on to ask; card, with "
         "its groups in order; no_data; refused), which the suite's oracle derives from #707's requirements over the reading and "
         "not from the code under test (portal/server/test/fixtures/suite-oracle.js). hotfix_reading says how the hotfix's recorded "
         "outcome reads against it, and verdicts gives a pass or a fail against it for the hotfix's outcome and for the outcome now "
         "(guarded: the wording negates or hedges its qualifier, so no card passes only where ask gives no figure). "
         "tests/cerebro_suite.py rebuilds what KGE says; the portal suite writes expected, hotfix_reading and verdicts.")
ORDER = ("#707 decision 18: Cerebro lists the suggested default first and marked, then the other meanings in KGE's order, then "
         "Something else. The marker is the portal's named, temporary rule for the \"certified\" option (decisions 8 and 17), until "
         "#709's declared family default; KGE marks none. #714 decision 1 (B): the card shows KGE's own label, so the 0.14.58 "
         "label \"Net New Assets (current)\" reads \"Net New Assets\"; expected holds the card as 707.3 shows it.")


def assembled(wording: dict, entry: dict, options: dict[str, int], table: list) -> dict:
    """One wording of the fixture: what the fixture alone holds of it (its question, sources, hotfix records and
    sample mark), what KGE said in this recording (entry), and the portal suite's keys as they stand."""
    reading = entry["reading"]
    return {"question": wording["question"], "sources": wording["sources"], "hotfix": wording["hotfix"],
            "ask_terms": entry["ask_terms"],
            "kge": {"bound_terms": sorted({b["term"] for b in reading["bindings"] if b.get("term")}),
                    "open_items": [item["item"] for item in reading["open_items"]],
                    "reading": compact(reading, options, table)},
            "kge_sample": wording["kge_sample"],
            **{key: wording[key] for key in PORTAL_SUITE if key in wording}}


def assemble(parts: list[Path]) -> None:
    """The fixture from the recorded parts: every wording must have been recorded."""
    os.environ["MODEL_PROVIDER"] = "none"
    from kge.services.ask.engine.openitems import resolved
    from kge.services.search.models import Search
    from kge.services.search.service import search
    held = suite()
    recorded = {}
    for part in parts:
        for line in part.read_text().splitlines():
            entry = json.loads(line)
            recorded[entry["question"]] = entry
    missing = [w["question"] for w in held["wordings"] if w["question"] not in recorded]
    assert not missing, missing[:3]
    options, table, names, qualifiers, wordings = {}, [], set(), set(), []
    for wording in held["wordings"]:
        entry = recorded[wording["question"]]
        more_names, more_qualifiers = named_by(entry["reading"])
        names |= more_names
        qualifiers |= more_qualifiers
        wordings.append(assembled(wording, entry, options, table))
    engine = scratch_engine()
    tools = {"map_term": {name: resolved(engine, name, ASKER) for name in sorted(names)},
             "search_filter": {query: [hit.title for hit in search(engine, ASKER, Search(query=query, kinds=["filter"])).hits]
                               for query in sorted(qualifiers)}}
    engine.close()
    card = {**held["card_0_14_58"], "order": ORDER}
    write({"about": ABOUT, "card_0_14_58": card, "kge_tools": tools, "kge_options": table, "wordings": wordings})
    print(len(wordings), "wordings,", sum(w["kge_sample"] for w in wordings), "in the KGE sample,", len(table), "option lists,",
          len(tools["map_term"]), "names and", len(tools["search_filter"]), "qualifiers;", FIXTURE.stat().st_size, "bytes")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("record", "assemble") or (sys.argv[1] == "record" and len(sys.argv) != 5):
        sys.exit("usage: python -m tests.cerebro_suite record <out.jsonl> <part> <parts> | assemble <part.jsonl> ...")
    command, *rest = sys.argv[1:]
    if command == "record":
        record(Path(rest[0]), int(rest[1]), int(rest[2]))
    else:
        assemble([Path(each) for each in rest])
