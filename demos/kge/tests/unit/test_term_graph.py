"""Meta-model 1.6.0's term edges in the compiled artifact (kge/compiler/emit.py, kge/compiler/terms.py; issue #511).

    .venv/bin/python -m pytest tests/unit/test_term_graph.py -q

A scratch copy of the Thornbury sample pack, raised to 1.6.0, gains two class terms and four member terms that name only
Thornbury's own columns, and one code/name pair; the data is synthetic, no question set is added, and nothing of the
Meridian sample is read. The pack is built straight into an artifact, without the checks, so what is asserted is what
emit writes: IS_A and BROADER between terms by name, CODED_AS with its code (two codes on one column are two rows, F7; a
member with no code is legal, F8), ON_COLUMN for each value, predicate and code/name-pair entry with its role and for no
other kind (F6), and a Term file whose header the new fields leave alone. A reference to a term the glossary does not
declare, which S37 refuses as a finding, stops a build that skips the checks with a ValueError naming the term. The
artifact then loads on a scratch graph on this run's store.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import pytest
import yaml

from kge.compiler import emit, terms
from kge.compiler import pack as packs
from kge.compiler.compile import VERSION
from kge.graph.schema import meta_model

DEMO = Path(__file__).resolve().parents[2]
SAMPLE = "thornbury"
LINE, SEGMENT = "underwriting.policy.line_code", "underwriting.insured.segment_code"


def term(i: str, name: str, **fields) -> dict:
    return {"id": i, "name": name, "domain": "Underwriting", "steward": "Underwriting Data Steward",
            "definition": f"{name}: a synthetic term of the test.", "status": "certified", **fields}


MEMBERS = [
    term("line-of-business", "line of business"),
    term("segment", "segment"),
    term("property-line", "Property (line)", is_a=["line-of-business"], codes=[{"column": LINE, "code": "PROP"}]),
    term("liability-line", "General Liability (line)", is_a=["line-of-business"],
         codes=[{"column": LINE, "code": "GL"}, {"column": LINE, "code": "GLI"}]),
    term("small-segment", "Small (segment)", is_a=["segment"], broader=["property-line"],
         codes=[{"column": SEGMENT, "code": "SMALL"}]),
    term("middle-segment", "Middle (segment)", is_a=["segment"]),
]
PAIR = {"code": "underwriting.insured.insured_id", "name": "underwriting.insured.insured_name",
        "members": [{"code": "1", "name": "Synthetic Insured"}]}


def scratch(tmp_path: Path, members: list[dict]) -> packs.Pack:
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / SAMPLE, clients / SAMPLE)
    path = clients / SAMPLE / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["manifest"]["meta_model_version"] = "1.6.0"
    data["manifest"]["platform"]["meta_model"] = "^1.6"
    data["schema"]["meta_model"] = "1.6"
    data["glossary"] += json.loads(json.dumps(members))
    data["terminology"]["code_name_pairs"] = [dict(PAIR)]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    pack = packs.read(clients, SAMPLE)
    assert not pack.findings, pack.findings
    return pack


@pytest.fixture
def pack(tmp_path) -> packs.Pack:
    return scratch(tmp_path, MEMBERS)


@pytest.fixture
def artifact(pack) -> emit.Artifact:
    return emit.build(pack, meta_model(), VERSION)


def table(art: emit.Artifact, path: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(art.files[path].decode())))


def pairs(art: emit.Artifact, path: str, *fields: str) -> list[tuple]:
    return [(r["from"], r["to"], *(r[f] for f in fields)) for r in table(art, path)]


def test_the_readers_give_each_declaration_with_its_place(pack):
    """kge/compiler/terms.py: the records #512's S37 and #513's F13 read, by glossary id, each with its place."""
    base = len(pack.data["glossary"]) - len(MEMBERS)
    assert terms.is_a(pack.data) == [
        terms.IsA("property-line", "line-of-business", f"glossary[{base + 2}].is_a[0]"),
        terms.IsA("liability-line", "line-of-business", f"glossary[{base + 3}].is_a[0]"),
        terms.IsA("small-segment", "segment", f"glossary[{base + 4}].is_a[0]"),
        terms.IsA("middle-segment", "segment", f"glossary[{base + 5}].is_a[0]")]
    assert terms.broader(pack.data) == [terms.Broader("small-segment", "property-line", f"glossary[{base + 4}].broader[0]")]
    assert terms.codes(pack.data) == [
        terms.Code("property-line", "Property (line)", LINE, "PROP", f"glossary[{base + 2}].codes[0].code"),
        terms.Code("liability-line", "General Liability (line)", LINE, "GL", f"glossary[{base + 3}].codes[0].code"),
        terms.Code("liability-line", "General Liability (line)", LINE, "GLI", f"glossary[{base + 3}].codes[1].code"),
        terms.Code("small-segment", "Small (segment)", SEGMENT, "SMALL", f"glossary[{base + 4}].codes[0].code")]


def test_the_readers_skip_what_s01_refuses():
    """The up-front checks read a pack before S01 has passed it: a malformed field is skipped, never raised on."""
    data = {"glossary": [
        {"id": "a", "name": "A", "is_a": "b", "broader": [5, "", "b"], "codes": [{"column": "u.t.c", "code": 5100},
                                                                          {"column": "u.t.c"}, "x", {"column": "u.t.c", "code": "K"}]},
        {"name": "no id", "is_a": ["a"]}, "not a mapping", {"id": "c", "codes": {"column": "u.t.c", "code": "Z"}}]}
    assert terms.is_a(data) == []
    assert terms.broader(data) == [terms.Broader("a", "b", "glossary[0].broader[2]")]
    assert terms.codes(data) == [terms.Code("a", "A", "u.t.c", "K", "glossary[0].codes[3].code")]
    assert terms.is_a({}) == terms.broader({"glossary": None}) == terms.codes({"glossary": "x"}) == []


def test_is_a_and_broader_join_terms_by_name(artifact):
    assert pairs(artifact, "data/edges/IS_A__Term__Term.csv") == [
        ("General Liability (line)", "line of business"), ("Middle (segment)", "segment"),
        ("Property (line)", "line of business"), ("Small (segment)", "segment")]
    assert pairs(artifact, "data/edges/BROADER__Term__Term.csv") == [("Small (segment)", "Property (line)")]


def test_two_codes_on_one_column_are_two_coded_as_rows(artifact):
    """F7: several codes on one member are alternatives, each its own edge; F8: a member with no code has none."""
    assert pairs(artifact, "data/edges/CODED_AS__Term__Col.csv", "code") == [
        ("General Liability (line)", LINE, "GL"), ("General Liability (line)", LINE, "GLI"),
        ("Property (line)", LINE, "PROP"), ("Small (segment)", SEGMENT, "SMALL")]


def test_on_column_names_each_value_predicate_and_code_name_pair_column(artifact):
    """F6: value and predicate entries give one ON_COLUMN each, a code/name pair two (code and name), and an intent, a
    mailbox or a measure's options none."""
    assert pairs(artifact, "data/edges/ON_COLUMN__Vocabulary__Col.csv", "role") == [
        ("code_name_pair-1", "underwriting.insured.insured_id", "code"),
        ("code_name_pair-1", "underwriting.insured.insured_name", "name"),
        ("predicate-1", "claims.claim.closed_on", "predicate"),
        ("value-1", LINE, "value"), ("value-2", SEGMENT, "value")]
    kinds = {r["id"]: r["kind"] for r in table(artifact, "data/nodes/Vocabulary.csv")}
    assert {"intent", "mailbox"} <= set(kinds.values())


def test_the_new_fields_are_edges_not_term_properties(artifact):
    header = artifact.files["data/nodes/Term.csv"].decode().splitlines()[0]
    assert header == "name,domain,steward,definition,id"
    counts = artifact.manifest["counts"]["edges"]
    assert {e: counts[e] for e in ("IS_A", "BROADER", "CODED_AS", "ON_COLUMN")} == {"IS_A": 4, "BROADER": 1, "CODED_AS": 4,
                                                                                   "ON_COLUMN": 5}


@pytest.mark.parametrize("field, edge", [("is_a", "IS_A"), ("broader", "BROADER")])
def test_a_term_the_glossary_does_not_declare_stops_the_build_naming_the_term(tmp_path, field, edge):
    """S37 refuses an unresolved reference as a finding; a build that skips the checks stops with a ValueError that
    names the term, never a bare KeyError."""
    stray = [*MEMBERS[:5], term("middle-segment", "Middle (segment)", **{field: ["no-such-term"]})]
    pack = scratch(tmp_path, stray)
    with pytest.raises(ValueError, match=rf"^{edge}: term middle-segment \(Middle \(segment\)\) names no-such-term at "
                                         rf"glossary\[\d+\]\.{field}\[0\], which is not a term the glossary declares$"):
        emit.build(pack, meta_model(), VERSION)


def test_a_code_on_a_column_the_lock_does_not_hold_stops_the_build_naming_the_term(tmp_path):
    stray = [*MEMBERS[:5], term("middle-segment", "Middle (segment)", codes=[{"column": "underwriting.insured.no_such", "code": "M"}])]
    pack = scratch(tmp_path, stray)
    with pytest.raises(ValueError, match=r"^CODED_AS: term middle-segment \(Middle \(segment\)\) codes M on "
                                         r"underwriting\.insured\.no_such at glossary\[\d+\]\.codes\[0\]\.code, which is not "
                                         r"a column in pack\.lock$"):
        emit.build(pack, meta_model(), VERSION)


def load(art: Path, graph: Path):
    """A fresh graph on this run's store: the artifact's DDL, then COPY of each file load_order stages for copy."""
    from kge.adapters.graphstore import connect, open_db
    db = open_db(graph)
    conn = connect(db)
    for stmt in (art / "ddl" / "kuzu.cypher").read_text().splitlines():
        conn.execute(stmt.removesuffix(";"))
    for step in json.loads((art / "manifest.json").read_text())["load_order"]:
        if step["stage"] == "copy":
            ends = f", from='{step['from']}', to='{step['to']}'" if "from" in step else ""
            conn.execute(f"COPY {step['table']} FROM '{(art / step['path']).as_posix()}' "
                         f"(header=true, parallel=false, null_strings=['\\\\N']{ends})")
    return db, conn


def test_the_artifact_loads_on_this_store(tmp_path, artifact):
    """IS_A is no reserved word in the store's Cypher, and two CODED_AS edges between one term and one column load apart."""
    folder, _ = emit.write(artifact, tmp_path / "out", SAMPLE)
    db, conn = load(folder, tmp_path / "graph")
    try:
        codes = conn.execute("MATCH (t:Term)-[c:CODED_AS]->(x:Col) WHERE t.name = 'General Liability (line)' "
                             "RETURN c.code ORDER BY c.code").get_all()
        assert codes == [["GL"], ["GLI"]]
        assert conn.execute("MATCH (a:Term)-[:IS_A]->(b:Term) RETURN count(*)").get_all() == [[4]]
        roles = conn.execute("MATCH (v:Vocabulary)-[r:ON_COLUMN]->(x:Col) RETURN v.id, x.id, r.role ORDER BY v.id, x.id").get_all()
        assert [r[2] for r in roles] == ["code", "name", "predicate", "value", "value"]
    finally:
        conn.close()
        db.close()


@pytest.mark.parametrize("edge", ["IS_A", "BROADER", "CODED_AS"])
def test_a_write_naming_only_a_term_edge_drops_the_cached_term_reads(edge):
    """F13i: graphview's term-read cache covers IS_A, BROADER and CODED_AS: a read naming only the edge is cached, and a
    write naming only the edge drops the cache (Graph.q clears it on a write TERMS matches)."""
    from kge.services.ask.engine import graphview
    read = f"MATCH (a)-[:{edge}]->(b) RETURN a.id, b.id"
    write = f"MATCH (a), (b) WHERE a.id = $a AND b.id = $b CREATE (a)-[:{edge}]->(b)"
    assert graphview.TERMS.search(read) and not graphview.NON_TERM_MUTABLE.search(read)
    assert graphview.WRITES.search(write) and graphview.TERMS.search(write)
