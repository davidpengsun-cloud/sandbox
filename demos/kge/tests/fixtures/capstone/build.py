"""The capstone client (issue #474): a second test client for the held-out golden-20 capstone, built into a folder of its
own. Test-only, invented data.

It is Meridian's generated estate with another seed and more dimension values: two more regions (South, Mountain), three
more branches (DEN, ATL, DAL) whose advisors take some of the households, two more leaf segments (GROWTH, LEGACY) under
the existing parents, a new business line (CHARITY) with two leaf segments of its own, each leaf with its weight and the
existing asset tier it takes (LEAVES), two more level-4 segments (LEVEL_4: Private Clients and the new business line
each under one of their own) and a second year of history (HISTORY_YEARS: July 2022 on, so 2023 is whole). Its pack is
Meridian's with values only: the Heartland region is another group of regions, two more region groups, the new branches
and segments named, the new members declared, and some existing codes given other names. Nothing in kge/ is changed for
the client: the generator's own settings are set for the run.

    python tests/fixtures/capstone/build.py <folder>

writes <folder>/clients (the capstone pack and its lock), <folder>/build (warehouses, artifacts, graph store, landing
and its own profiles.yaml) and runs the estate, certification, lock, graph and pipeline steps there, as make graph
pipeline does, for KGE_TEST_BUILD=<folder>/build.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import yaml

DEMO = Path(__file__).resolve().parents[3]
SEED = int(os.environ.get("CAPSTONE_SEED", 4242))
REGIONS = [("SOUTH", "South", "EAST"), ("MOUNTAIN", "Mountain", "WEST")]
OFFICES = [("DEN", "Denver Larimer Square", "MOUNTAIN", dt.date(2012, 4, 1), None),
           ("ATL", "Atlanta Peachtree", "SOUTH", dt.date(2013, 1, 1), None),
           ("DAL", "Dallas Uptown", "SOUTH", dt.date(2015, 5, 1), None)]
SEGMENTS = [("GROWTH", "Growth Affluent", "S4100", 2), ("LEGACY", "Legacy Wealth", "PRIVATE", 2),
            ("CHARITY", "Foundations and Endowments", None, 1),
            ("ENDOW", "Endowments", "CHARITY", 2), ("FOUNDN", "Family Foundations", "CHARITY", 2)]
LEAVES = {"GROWTH": (3, 1001), "LEGACY": (2, 1002), "ENDOW": (2, 1002), "FOUNDN": (2, 1003)}
LEVEL_4 = {"PRIVATE": ("S3300", "Private Banking"), "CHARITY": ("S3400", "Nonprofit Services")}
HISTORY_YEARS = 2
MEMBERS = ["SEGMT_LVL_ID4", "SEGMT_LVL_ID5", "SEGMT_LVL_NM5", "SEGMT_LVL_ID6", "SEGMT_LVL_NM6", "CLNT_CD", "SEGMT_CD", "SEGMT_NM",
           "RGN_CD", "ACCT_DOMCL_BRCH_CD"]
ADVISOR_BRANCH = {"DEN": (3, 9, 15, 21, 27), "ATL": (5, 11, 17, 23), "DAL": (6, 12, 18, 24, 30)}

HEARTLAND = "    - {phrases: [Heartland region], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['CEO', 'NER']}\n"
ADDED = """    - {phrases: [Heartland region], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['MIDWEST', 'MOUNTAIN']}
    - {phrases: [Sunbelt region], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['SOUTH', 'NER']}
    - {phrases: [Atlantic region], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['CEO', 'SOUTH']}
    - {phrases: [Frontier region], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['MIDWEST', 'NER', 'MOUNTAIN']}
    - {phrases: [region SOUTH, SOUTH region], column: wealth.BRCH_DIM_1.RGN_CD, op: eq, value: 'SOUTH'}
    - {phrases: [region MOUNTAIN, MOUNTAIN region], column: wealth.BRCH_DIM_1.RGN_CD, op: eq, value: 'MOUNTAIN'}
    - {phrases: [Denver branch, branch Denver], column: wealth.BRCH_DIM_1.BRCH_CD, op: eq, value: 'DEN'}
    - {phrases: [Atlanta branch, branch Atlanta], column: wealth.BRCH_DIM_1.BRCH_CD, op: eq, value: 'ATL'}
    - {phrases: [Dallas branch, branch Dallas], column: wealth.BRCH_DIM_1.BRCH_CD, op: eq, value: 'DAL'}
    - {phrases: [Chicago branch, branch Chicago], column: wealth.BRCH_DIM_1.BRCH_CD, op: eq, value: 'CHI'}
    - {phrases: [Sausalito branch, branch Sausalito], column: wealth.BRCH_DIM_1.BRCH_CD, op: eq, value: 'SFO'}
    - {phrases: [Wealth Builders], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6, op: eq, value: 'GROWTH'}
    - {phrases: [Legacy Clients], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6, op: eq, value: 'LEGACY'}
    - {phrases: [Premier Clients], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6, op: eq, value: 'HNW'}
    - {phrases: [Next Generation], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6, op: eq, value: 'EMERGING'}
    - {phrases: [Private Wealth], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5, op: eq, value: 'PRIVATE'}
    - {phrases: [segment level 5 CHARITY, Charitable Investor], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5, op: eq, value: 'CHARITY'}
    - {phrases: [segment level 6 ENDOW, Endowment Clients], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6, op: eq, value: 'ENDOW'}
    - {phrases: [segment level 6 FOUNDN, Foundation Clients], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6, op: eq, value: 'FOUNDN'}
    - {phrases: [segment level 4 PBNK, Fiduciary Services], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID4, op: eq, value: 'S3300'}
    - {phrases: [segment level 4 NPRF, Nonprofit Advisory], column: wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID4, op: eq, value: 'S3400'}
"""


def clients(out: Path) -> Path:
    """The capstone clients folder: Meridian's pack with values only changed, no fixture (the estate is generated)."""
    folder = out / "clients" / "meridian"
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(DEMO / "clients" / "meridian", folder, ignore=shutil.ignore_patterns("fixture"))
    pack = folder / "pack.yaml"
    text = pack.read_text()
    assert text.count(HEARTLAND) == 1, "the Heartland filter moved in Meridian's pack"
    pack.write_text(text.replace(HEARTLAND, ADDED))
    return out / "clients"


def segment_names(out: Path) -> None:
    """Each client segment the pack filters on by its code is also declared by its name as the warehouse holds it, as a
    knowledge engineer onboarding the client would: the bare name the data carries is a phrase of that segment."""
    con = duckdb.connect(str(out / "build" / "warehouses" / "wealth.duckdb"), read_only=True)
    try:
        names = {(str(n), code): name for n in range(1, 8)
                 for code, name in con.execute(f"SELECT DISTINCT SEGMT_LVL_ID{n}, SEGMT_LVL_NM{n} FROM CLIENT_CD_ROLLUP_1 "
                                               f"WHERE SEGMT_LVL_ID{n} IS NOT NULL AND SEGMT_LVL_NM{n} IS NOT NULL").fetchall()}
    finally:
        con.close()
    pack = out / "clients" / "meridian" / "pack.yaml"
    line = re.compile(r"(\{phrases: \[)([^\]]*)(\], column: wealth\.CLIENT_CD_ROLLUP_1\.SEGMT_LVL_ID(\d), op: eq, value: '([^']+)'\})")

    def named(m):
        phrases = [p.strip() for p in m.group(2).split(",")]
        name = names.get((m.group(4), m.group(5)))
        if name is None or name.lower() in {p.lower() for p in phrases}:
            return m.group(0)
        return m.group(1) + ", ".join([*phrases, name]) + m.group(3)
    text, count = line.subn(named, pack.read_text())
    assert count, "no segment filter by code in the capstone pack"
    pack.write_text(text)


def members(out: Path) -> None:
    """Each new member the warehouse holds is declared in the pack's member lists and value lists, as a knowledge
    engineer onboarding the client would: a code and its name where the pack pairs them, the value where it lists them."""
    con = duckdb.connect(str(out / "build" / "warehouses" / "wealth.duckdb"), read_only=True)
    tables = {"CLIENT_CD_ROLLUP_1", "CLNT_SEGMT_DIM_1", "BRCH_DIM_1", "ACCT_DIM2_1"}

    def held(table: str, column: str, name: str | None = None) -> list[tuple]:
        return con.execute(f"SELECT DISTINCT {column}, {name or column} FROM {table} WHERE {column} IS NOT NULL "
                           "ORDER BY 1").fetchall()
    pack = out / "clients" / "meridian" / "pack.yaml"
    lines = pack.read_text().split("\n")
    try:
        for i, text in enumerate(lines):
            m = re.match(r"(\s*)- \{column: wealth\.(\w+)\.(\w+), values: \[(.*)\]\}$", text)
            if m and m.group(2) in tables and m.group(3) in MEMBERS:
                listed = re.findall(r"'([^']*)'", m.group(4))
                values = listed + [str(v) for v, _ in held(m.group(2), m.group(3)) if str(v) not in listed]
                lines[i] = f"{m.group(1)}- {{column: wealth.{m.group(2)}.{m.group(3)}, values: [{', '.join(repr(v) for v in values)}]}}"
            m = re.match(r"(\s*)members: \[(.*)\]$", text)
            code = re.match(r"\s*- code: wealth\.(\w+)\.(\w+)$", lines[i - 2]) if m else None
            if code and code.group(1) in tables and code.group(2) in MEMBERS:
                name = re.match(r"\s*name: wealth\.\w+\.(\w+)$", lines[i - 1]).group(1)
                listed = re.findall(r"\{code: ([^,]+), name: ([^}]+)\}", m.group(2))
                known = {c for c, _ in listed}
                pairs = listed + [(str(c), str(n)) for c, n in held(code.group(1), code.group(2), name) if str(c) not in known]
                lines[i] = f"{m.group(1)}members: [{', '.join(f'{{code: {c}, name: {n}}}' for c, n in pairs)}]"
    finally:
        con.close()
    pack.write_text("\n".join(lines))


def registry(out: Path) -> Path:
    build = out / "build"
    build.mkdir(parents=True, exist_ok=True)
    demo = yaml.safe_load((DEMO / "deploy" / "profiles.yaml").read_text())["profiles"]["meridian"]
    entry = {"client": "meridian", "asker": demo["asker"], "clients": str(out / "clients"), "artifacts": "artifacts",
             "store": "stores", "landing": "landing", "warehouses": "warehouses",
             "identities": str(DEMO / "deploy" / demo["identities"]), "baseline": None}
    path = build / "profiles.yaml"
    path.write_text(yaml.safe_dump({"default": "meridian", "profiles": {"meridian": entry}}, sort_keys=False))
    return path


def question_sets(out: Path, estate=None) -> tuple[list, list, list, list]:
    """Meridian's committed question sets, as they are: their guards hold Meridian's sample to its examples, which another
    seed need not meet. The capstone reads only their questions and reference SQL; its figures are its own."""
    import json
    fixture = DEMO / "clients" / "meridian" / "fixture"
    for f in fixture.glob("*.json"):
        shutil.copy(f, Path(out) / f.name)

    def items(name: str) -> list:
        data = json.loads((fixture / name).read_text())
        return data["items"] if isinstance(data, dict) else data
    return (items("golden.json"), items("heldout.json"), items("golden_wealth_v1.json"), items("sage_v1.json"))


def generate() -> int:
    """Meridian's generator with the capstone's values, run in this process (PROFILES names the capstone registry)."""
    from kge.estate import generate as g
    from kge.estate import sage
    g.REGIONS = [*g.REGIONS, *REGIONS]
    g.OFFICES = [*g.OFFICES, *OFFICES]
    g.SEGMENTS = [*g.SEGMENTS, *SEGMENTS]
    g.LEAF_SEGMENTS = [*g.LEAF_SEGMENTS, *LEAVES]
    g.LEAF_WEIGHTS = [*g.LEAF_WEIGHTS, *(w for w, _ in LEAVES.values())]
    g.LEAF_TIERS = {**g.LEAF_TIERS, **{leaf: tier for leaf, (_, tier) in LEAVES.items()}}
    g.HISTORY_YEARS = HISTORY_YEARS
    sage.LEVEL_4 = LEVEL_4
    wealth = g.POPULATE["wealth"]

    def populate_wealth(con, rng):
        wealth(con, rng)
        for branch, ids in ADVISOR_BRANCH.items():
            con.execute(f"UPDATE ADV_DIM_1 SET BRCH_CD = '{branch}' WHERE ADV_ID IN ({', '.join(map(str, ids))})")
    g.POPULATE["wealth"] = populate_wealth
    g.write_question_sets = question_sets
    expand = g.expand

    def expanded(estate, seed):
        """The new regions' offices sell through the retail branches division, as the Northeast's do, so branch sales
        NNA has figures beyond the Northeast."""
        added = expand(estate, seed)
        con = estate.open("wealth", write=True)
        try:
            con.execute("UPDATE BRCH_DIM_1 SET BRCH_DIVSN_CD = 'BR', BRCH_DIVSN_NM = 'Retail Branches' "
                        f"WHERE RGN_CD IN ({', '.join(repr(code) for code, _, _ in REGIONS)})")
        finally:
            con.close()
        return added
    g.expand = expanded
    sys.argv = ["generate", "--seed", str(SEED)]
    return g.main()


def run(*args: str, env: dict) -> None:
    print("capstone:", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], cwd=DEMO, env=env, check=True)


def main(out: Path) -> int:
    out = out.resolve()
    clients(out)
    env = {**os.environ, "PROFILES": str(registry(out)), "PROFILE": "", "MODEL_PROVIDER": "none",
           "MODEL_HIGH_PROVIDER": "none", "MODEL_MEDIUM_PROVIDER": "none", "MODEL_LOW_PROVIDER": "none",
           "KGE_TEST_BUILD": str(out / "build")}
    run(__file__, "--generate", env=env)
    segment_names(out)
    members(out)
    run("-m", "kge.estate.onboard", "build", env=env)
    run("-m", "kge.estate.certify", env=env)
    run("-m", "kge.compiler.lock", env=env)
    run("-m", "kge.cli", "graph", "pipeline", env=env)
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--generate"]:
        sys.path.insert(0, str(DEMO))
        raise SystemExit(generate())
    raise SystemExit(main(Path(sys.argv[1])))
