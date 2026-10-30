"""Platform code names no client: the issue's layout keeps the platform "shared, no client nouns".

    .venv/bin/python -m pytest tests/unit/test_client_nouns.py -q

The client is chosen by config at deployment, and the dependency runs one way,
client to platform. A client's nouns are its id and its sources' ids, its
curated sources' among them (since pack 1.1.0), read from every pack under
clients/, its pack's role ids and the principals its deployment binds to them
(deploy/<client>/identities.yaml), and the two Meridian nouns the earlier checks
named (household, revenue). A role or principal id that is a word of the
platform's own is no client's noun (PLATFORM_WORDS, each with why). The
platform's meta-model, its compiler and its landing zone name no client at all,
nor a client's schema: none of its acronyms (NNA), its qualified columns
(TRAN_FACT_1.DW_TRAN_DT) or its tables whose name is no plain word (MONTH_DIM_1), read
from every pack and its lock; a plain word such as flow or holding is English
the platform may use.

Since #39 item 11 no other platform file (api, eval, graph, pipeline, ui) names
one either, and since issue #40 on no line at all: the specials owner question 5's
default had left in kge/services/ask/engine/core.py (the Retail and Corporate join refusal, resting on
DD-CORPORATE and DD-RETAIL, and the GBP and STD-FX abstention) are read from the
graph's facts, owner question 18's default, and the allowance for their lines,
SPECIALS, is empty, which a test holds. No platform file names one inside an identifier
either (golden_<source>_v1, a client's principal as a default), but the
platform's own identifiers that hold one (OWN_IDENTIFIERS, each with why). So a
command asks as the querying identity the profile's deployment names, never as
one of a client's named in code. Nor does platform code hold the wording the
nouns do not catch: the parser's filter phrases and values, the openings that
mark a definitional question, the role mailboxes. They are the pack's
(terminology.value_vocabularies, predicates, intents, functional_mailboxes), read
from the graph (tests/integration/test_vocabulary.py), and the README says so.

Reads files only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

DEMO = Path(__file__).resolve().parents[2]
PLATFORM = ("app.py", "services", "semantics", "compiler", "eval", "graph", "landing", "pipeline", "ui")
STRICT = ("compiler", "landing")
CODE = {".py", ".html", ".js", ".css"}
SPECIALS: dict[str, list[str]] = {}
NOW = ("no platform line names a client", "`NOT_JOINABLE_TO`", "the owner's Question 18 default", "until the owner answers",
       "naive reader", "the owner's Question 19 default")

PLATFORM_WORDS = {
    "steward": "English: the steward queue and a steward's decision are the platform's",
    "pipeline_tagger": "the tagging identity the pipeline runs as when it names none: every deployment binds it (tested below)",
}
OWN_IDENTIFIERS = {
    "xref_match": ("the steward queue's tag family for a cross-reference match, the platform's", None),
}


def principals() -> set[str]:
    """Every pack's role ids, and every principal a deployment binds to one."""
    out = set()
    for pack in sorted((DEMO / "clients").glob("*/pack.yaml")):
        out |= {r["id"] for r in (yaml.safe_load(pack.read_text()).get("access") or {}).get("roles") or []}
    for ids in sorted((DEMO / "deploy").glob("*/identities.yaml")):
        out |= {b["principal"] for b in yaml.safe_load(ids.read_text())["principals"]}
    return out


def nouns() -> list[str]:
    out = {"household", "revenue"}
    for pack in sorted((DEMO / "clients").glob("*/pack.yaml")):
        p = yaml.safe_load(pack.read_text())
        out |= {p["manifest"]["client"], *(s["id"] for s in [*(p.get("sources") or []), *(p.get("curated_sources") or [])])}
    return sorted(out | (principals() - set(PLATFORM_WORDS)))


NOUN = re.compile(r"\b(" + "|".join(map(re.escape, nouns())) + r")\b", re.I)
INSIDE = re.compile(r"(?<![a-z])(" + "|".join(map(re.escape, nouns())) + r")(?![a-z])", re.I)


def schema_names() -> list[str]:
    """A client's schema as its pack and lock name it: each acronym as written, each table.column, and each table whose
    name is no plain word (month_end; not flow or holding)."""
    out = set()
    for pack in sorted((DEMO / "clients").glob("*/pack.yaml")):
        p, lock = yaml.safe_load(pack.read_text()), json.loads((pack.parent / "pack.lock").read_text())
        out |= {a["id"] for a in p.get("acronyms") or []}
        for t in (t for s in lock["sources"].values() for t in s["tables"]):
            out |= {f"{t['name']}.{c['name']}" for c in t["columns"]} | ({t["name"]} if "_" in t["name"] else set())
    return sorted(out)


SCHEMA = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, sorted(schema_names(), key=len, reverse=True))) + r")(?![\w])")


def platform_files() -> list[Path]:
    return sorted(p for d in PLATFORM for p in ([DEMO / "kge" / d] if d.endswith(".py") else (DEMO / (d if d == "ui" else f"kge/{d}")).rglob("*"))
                  if p.suffix in CODE and "__pycache__" not in p.parts)


def named(path: Path) -> list[str]:
    return NOUN.findall(path.read_text())


def test_the_nouns_are_the_clients_ids_and_their_sources():
    assert {"meridian", "retail", "sme", "corporate", "wealth", "household", "revenue", "xref"} <= set(nouns())


@pytest.mark.parametrize("path", [DEMO / "meta-model.yaml", *(p for p in platform_files() if p.parts[len(DEMO.parts)] in STRICT)],
                         ids=lambda p: p.relative_to(DEMO).as_posix())
def test_the_meta_model_the_compiler_and_the_landing_name_no_client(path):
    assert named(path) == [], f"{path.relative_to(DEMO)} names {sorted(set(named(path)))}"
    schema = sorted(set(SCHEMA.findall(path.read_text())))
    assert schema == [], f"{path.relative_to(DEMO)} names a client's schema: {schema}"


def inside(path: Path) -> list[str]:
    """The client nouns path names inside an identifier or a file name, on its lines but the specials, the platform's own
    identifiers (OWN_IDENTIFIERS) left out."""
    rel = path.relative_to(DEMO).as_posix()
    allowed = [re.compile(x) for x in SPECIALS.get(rel, [])]
    own = re.compile(r"\b(" + "|".join(re.escape(w) for w, (_, where) in OWN_IDENTIFIERS.items() if where in (None, rel)) + r")\b")
    found = set()
    for line in path.read_text().splitlines():
        if not any(a.search(line) for a in allowed):
            found |= {m.lower() for m in INSIDE.findall(own.sub("", line))}
    return sorted(found)


@pytest.mark.parametrize("path", platform_files(), ids=lambda p: p.relative_to(DEMO).as_posix())
def test_no_platform_file_names_a_client_inside_an_identifier(path):
    """A client's set, file, table or principal named in code or in what it prints (golden_<source>_v1,
    build/<client set>.json, a client's principal as the identity a command asks as) names the client as surely as the
    bare word."""
    found = inside(path)
    assert found == [], f"{path.relative_to(DEMO)} names {found} inside an identifier or a file name"


def test_a_noun_inside_an_identifier_is_caught_and_a_word_that_holds_one_is_not():
    assert INSIDE.findall("build/golden_wealth_v1.json, RETAIL_KEY") == ["wealth", "RETAIL"]
    assert NOUN.findall("build/golden_wealth_v1.json") == [], "the word-bounded match misses it: hence INSIDE"
    assert INSIDE.findall("an assessment of corporately smeared revenues") == []


def test_a_client_s_principal_is_a_noun_and_a_word_of_the_platform_s_own_is_not():
    """The gate missed a client's principal in platform code: the commands asked as Meridian's analyst_enterprise by
    default, which Thornbury does not have. Each pack's roles and each deployment's principals are nouns now."""
    assert {"analyst_enterprise", "analyst_retail", "advisor_wealth", "finance_reporting", "steward_glossary", "admin",
            "actuary", "claims_steward"} <= set(nouns())
    assert NOUN.findall('default="analyst_enterprise"') == ["analyst_enterprise"]
    assert INSIDE.findall("ASKER = analyst_retail_v1") == ["analyst_retail"]
    assert not set(PLATFORM_WORDS) & set(nouns()) and set(PLATFORM_WORDS) <= principals()


def test_every_deployment_binds_the_tagging_identity_the_pipeline_runs_as():
    """pipeline_tagger is the platform's word (PLATFORM_WORDS) only while every deployment binds it."""
    for ids in sorted((DEMO / "deploy").glob("*/identities.yaml")):
        assert "pipeline_tagger" in {b["principal"] for b in yaml.safe_load(ids.read_text())["principals"]}, ids


def test_each_of_the_platform_s_own_identifiers_is_still_where_it_is_said_to_be():
    """The allowance is for those identifiers alone, each in the files it names: drop one no file holds."""
    for word, (_, where) in OWN_IDENTIFIERS.items():
        held = [p for p in platform_files() if re.search(rf"\b{word}\b", p.read_text())]
        assert held, f"no platform file says {word}: drop it from OWN_IDENTIFIERS"
        if where is not None:
            assert [p.relative_to(DEMO).as_posix() for p in held] == [where], (word, held)


def test_a_client_s_schema_names_are_its_acronyms_columns_and_compound_tables():
    names = set(schema_names())
    assert {"NNA", "SRC_SYS_CD", "TRAN_FACT_1.DW_TRAN_DT", "MONTH_DIM_1.CLDR_EOM_DT", "MONTH_DIM_1", "client_xref"} <= names
    assert not {"flow", "holding", "client", "customer"} & names, "a plain word is English the platform may use"


def lines_naming(path: Path) -> list[tuple[int, str]]:
    return [(n, line) for n, line in enumerate(path.read_text().splitlines(), 1) if NOUN.search(line)]


def test_no_platform_file_names_a_client():
    found = {}
    for p in platform_files():
        rel = p.relative_to(DEMO).as_posix()
        allowed = [re.compile(x) for x in SPECIALS.get(rel, [])]
        bad = [f"{n}: {sorted(set(NOUN.findall(line)))} {line.strip()[:100]}" for n, line in lines_naming(p)
               if not any(a.search(line) for a in allowed)]
        if bad:
            found[rel] = bad
    assert found == {}, f"client nouns in platform code (file: line, nouns, text): {found}"


def test_no_line_of_a_platform_file_is_allowed_to_name_a_client():
    """Issue #40 read the specials #39 left in code from the graph's facts: no allowance remains, and none is added back."""
    assert SPECIALS == {}


def test_the_wording_the_nouns_do_not_catch_is_the_pack_s_too():
    """The parser's filter phrases and values, and the role mailboxes, are no longer code: every value, prefix and
    phrase of more than one word a pack's terminology gives is in no platform file, nor a list of a pack's mailboxes, nor
    any of the constants that held them. A code as short as A1 is no one's wording, and the mailboxes count only as a
    list of them, since a word such as "privacy" on its own is English."""
    said, prefixes, mailboxes = set(), set(), set()
    for pack in sorted((DEMO / "clients").glob("*/pack.yaml")):
        t = yaml.safe_load(pack.read_text())["terminology"]
        for v in t.get("value_vocabularies") or []:
            said |= set(v.get("values") or [])
            prefixes |= {v["prefix"]} if v.get("prefix") else set()
            said |= {x for x in [*(v.get("after") or []), *(v.get("before") or [])] if " " in x}
        for x in t.get("predicates") or []:
            said |= {w for w in x["phrases"] if " " in w}
        mailboxes |= set(t.get("functional_mailboxes") or [])
    assert {"Everyday Current", "incorporated in", "risk profile", "more than 30 days past due"} <= said and "TEAM-" in prefixes
    said = {x for x in said if len(x) > 3 or " " in x}
    found = {}
    for p in platform_files():
        text = p.read_text()
        hits = sorted(x for x in said if re.search(rf"(?<!\w){re.escape(x)}(?!\w)", text))
        hits += sorted(x for x in prefixes if re.search(rf"(?<!\w){re.escape(x)}", text))
        quoted = sorted(m for m in mailboxes if re.search(rf"[\"']{re.escape(m)}[\"']", text))
        hits += [f"mailboxes {quoted}"] if len(quoted) > 3 else []
        hits += [name for name in ("FILTER_PATTERNS", "DEFINITIONAL", "ROLE_MAILBOXES", "UNITS") if re.search(rf"^{name}\s*=", text, re.M)]
        if hits:
            found[p.relative_to(DEMO).as_posix()] = hits
    assert found == {}, found


def test_the_readme_says_that_no_platform_line_names_the_client():
    """The README's account of the runtime's domain says no platform line names a client, the specials read from the
    graph's facts by owner question 18's default, and what the platform still reads by a name, no client's (owner
    question 19's default); and no longer that domain logic names the client, nor that the client's wording is still in
    platform code. A default holds until the owner answers, and the README never calls it a decision the owner made."""
    section = (DEMO / "README.md").read_text().split("### The runtime's domain", 1)[1].split("\n## ", 1)[0]
    section = " ".join(section.split())
    assert "still in platform code" not in section and "until later in issue #39" not in section
    assert "What names the client still" not in section and "the owner's Question 5 default" not in section
    for said in NOW:
        assert said in section, f"the README does not say {said!r}"
    for slot in ("value_vocabularies", "predicates", "intents", "functional_mailboxes"):
        assert f"`{slot}`" in section, slot


def test_the_readme_and_the_plan_call_question_18_a_default_until_the_owner_answers():
    """Question 18 is open: the specials are read from the graph's facts by its default, until the owner answers. The
    plan's grep gate says no platform line names a client; neither it nor the README says the owner decided."""
    plan = " ".join((DEMO.parents[1] / "docs" / "use-cases" / "kge" / "platform" / "implementation-plan.md").read_text().split())
    gate = plan.split("The plan's grep gate", 1)[1].split("Not built", 1)[0]
    assert "no platform line names a client" in gate, gate
    assert "the owner's Question 18 default" in gate and "until the owner answers" in gate, gate
    for name, text in (("README", (DEMO / "README.md").read_text()), ("plan", plan)):
        said = " ".join(text.split())
        assert "Question 5 leaves" not in said and "Question 18 decided" not in said, name
