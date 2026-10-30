"""The platform drafts' claims about the demo, held to the code (docs/use-cases/kge/platform/).

    .venv/bin/python -m pytest tests/unit/test_platform_docs.py -q

runtime-pipeline.md, implementation-plan.md, graph-model-spec.md and
pack-contract.md say what the demo does and what stays planned for the
platform. Every demo file they cite exists, by its path (ui/index.html,
deploy/profiles.yaml) or its name (spec.py), and so does every name they cite
by its file (kge/services/ask/engine/core.py:Engine), its module (sqlguard.gate) or its demo class
(Graph.retrieve), in the platform's packages and the demo's (compiler and
landing among them), every route they name (GET /presets, /ask) and every make
target. A bare name in backticks is not looked up: the drafts give the
platform's planned names (ContextBundle, intent) the same backticks, and a
pack's section path (schema.extensions) is no module's name. The run config,
the arms, the run trace and the margin they describe are the code's; the
figures they quote are the committed baselines' and what certification landed;
the refused joins the plan's query set expects are the golden set's the harness
runs; and the A/B report names the certification stand-in, as the plan says it
does.

Issue #39's table of doc updates is held here too: graph-model-spec.md v0.3's
profiles and layout section is the artifact the compiler writes, the registry,
the graph store and the runtime overlay as built, with R1's revision of its
readiness and layers and the meta-model as built; pack-contract.md v0.3 requires
the client and its meta-model version, and lists every section the compiler
reads and every check it makes; implementation-plan.md v0.3 says what #39 built
in Phases 3 and 5, records the two decisions and names golden_wealth_v1 as the
baseline ran it; runtime-pipeline.md's run config has the profile; #37's
templates name the client and Meridian's artifact paths and change in nothing
else; and the index points at the profiles section.

Issue #40's table of doc updates too: pack-contract.md v0.4's onboarding section
has a row per step, 1 to 9, each naming the make target that runs it or shows
it done, and the data rules it states are the code's; implementation-plan.md
v0.4 has the second-client exit gate in Phase 5 as built, with no Phase 5b; the
index points at the onboarding section; and graph-model-spec.md, still v0.3,
says the no-client-nouns gate allows no line.

Reads files only: the build's landed release and question sets, the packs and
the meta-model (compiled in memory), and the templates as git holds them at
#39's base; nothing that opens the graph.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import CONTEXT_CLOSE, CONTEXT_OPEN, PROMPTS, RETRY, Agent
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, STAGES
from kge.compiler.checks import SECTIONS
from kge.eval import harness, report, sets
from tests.scratch import BUILD

DEMO = Path(__file__).resolve().parents[2]
REPO = DEMO.parents[1]
PLATFORM = REPO / "docs" / "use-cases" / "kge" / "platform"
DOCS = {name: (PLATFORM / name).read_text()
        for name in ("runtime-pipeline.md", "implementation-plan.md", "graph-model-spec.md", "pack-contract.md")}
PACKAGES = ("services", "semantics", "compiler", "estate", "eval", "graph", "improve", "landing", "pipeline")
FENCE = re.compile(r"^```.*?^```", re.M | re.S)
CODE = re.compile(r"`([^`\n]+)`")
FILE_REF = re.compile(r"^((?:[\w-]+/)*[\w-]+\.py)(?::([\w.]+))?$")
CLASS_REF = re.compile(r"^([A-Z]\w+)\.(\w+(?:/\w+)*)(?:\(.*\))?$")
MODULE_REF = re.compile(r"^([a-z]\w*)\.(\w+)(?:\(.*\))?$")
PATH_REF = re.compile(r"^((?:[\w.-]+/)+[\w.-]+\.\w+)$")
ROUTE_REF = re.compile(r"^(?:(GET|POST) )?(/[a-z][\w/{}<>.-]*)$")
EXTENSIONS = {"md", "html", "json", "jsonl", "csv", "yaml", "txt", "lock", "cypher"}
ANY_CLIENT = SimpleNamespace(e=SimpleNamespace(UNIT_ORDER=["alpha", "beta"], w=SimpleNamespace(query_dialect="DuckDB")))


def spans(text: str) -> list[str]:
    """The inline code in text, outside fenced blocks."""
    return CODE.findall(FENCE.sub("", text))


def flat(text: str) -> str:
    """The text with its line breaks and runs of spaces as single spaces, as a reader reads it."""
    return " ".join(text.split())


def section(text: str, heading: str) -> str:
    """The text under a heading, up to the next heading of the same level or higher."""
    level = len(heading) - len(heading.lstrip("#"))
    body = text.split(heading + "\n", 1)[1]
    return re.split(rf"\n#{{1,{level}}} ", body, maxsplit=1)[0]


def demo_files() -> list[Path]:
    return sorted([DEMO / "kge" / "app.py", *(p for pkg in PACKAGES for p in (DEMO / "kge" / pkg).rglob("*.py"))])


def names(path: Path) -> dict[str, set[str]]:
    """What a module defines: its top-level functions, classes and assignments under "", each class's members under its name."""
    out: dict[str, set[str]] = {"": set()}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[""].add(node.name)
        if isinstance(node, ast.ClassDef):
            out[node.name] = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            out[node.name] |= {t.id for n in node.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                               for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            out[""] |= {t.id for t in (node.targets if isinstance(node, ast.Assign) else [node.target]) if isinstance(t, ast.Name)}
    return out


def routes() -> set[tuple[str, str]]:
    """The API's routes as (method, path), from the decorators in each service's routes (kge/services/<service>/routes.py),
    onboarding's under its /onboard prefix."""
    out = set()
    for path in sorted((DEMO / "kge" / "services").glob("*/routes.py")):
        prefix = "/onboard" if path.parent.name == "onboard" else ""
        out |= {(m.upper(), prefix + route) for m, route in re.findall(r'^@router\.(get|post)\("([^"]*)"', path.read_text(), re.M)}
    return out


def resolve(ref: str) -> Path | None:
    """A demo file by its path from the demo folder or the repository's root, or by its bare name when exactly one file
    has it."""
    if "/" in ref:
        p = next((p for p in (DEMO / ref, REPO / ref) if p.is_file() and p.resolve().is_relative_to(DEMO)), None)
        return p.resolve() if p else None
    found = [p for p in demo_files() if p.name == ref]
    return found[0] if len(found) == 1 else None


@pytest.mark.parametrize("doc", DOCS)
def test_every_demo_file_and_name_the_draft_cites_exists(doc):
    """A method may be cited by its file alone (kge/services/ask/engine/core.py:plan_unit); a planned type (Pipeline, Measure) is no demo
    class, and a pack's section path (schema.extensions) is no module's name."""
    classes = {cls: (p, members) for p in demo_files() for cls, members in names(p).items() if cls}
    served = routes()
    missing = []
    for ref in sorted(set(spans(DOCS[doc]))):
        m = FILE_REF.match(ref)
        if m:
            path = resolve(m.group(1))
            if path is None:
                missing.append(f"{ref}: no such demo file")
                continue
            if m.group(2):
                defined = names(path)
                head, _, tail = m.group(2).partition(".")
                ok = head in defined[""] and (not tail or tail in defined.get(head, set()))
                if not ok and not tail:
                    ok = any(head in members for cls, members in defined.items() if cls)
                if not ok:
                    missing.append(f"{ref}: {m.group(2)} is not defined in {path.relative_to(DEMO)}")
            continue
        m = CLASS_REF.match(ref)
        if m:
            if m.group(1) in classes:
                path, members = classes[m.group(1)]
                missing += [f"{ref}: {m.group(1)} in {path.relative_to(DEMO)} has no {name}" for name in m.group(2).split("/")
                            if name not in members]
            continue
        m = MODULE_REF.match(ref)
        if m and m.group(2) not in EXTENSIONS and m.group(1) not in SECTIONS and resolve(f"{m.group(1)}.py"):
            path = resolve(f"{m.group(1)}.py")
            if m.group(2) not in names(path)[""]:
                missing.append(f"{ref}: {m.group(2)} is not defined in {path.relative_to(DEMO)}")
            continue
        m = PATH_REF.match(ref)
        if m:
            if not ((DEMO / ref).is_file() or (REPO / ref).is_file()):
                missing.append(f"{ref}: no such file")
            continue
        m = ROUTE_REF.match(ref)
        if m:
            said = re.sub(r"<\w+>", "{}", m.group(2))
            if not any(re.sub(r"{\w+}", "{}", path) == said and m.group(1) in (None, method) for method, path in served):
                missing.append(f"{ref}: kge/app.py serves no {ref}")
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("doc", DOCS)
def test_every_make_target_the_draft_names_is_in_the_makefile(doc):
    targets = set(re.findall(r"^([a-z][\w-]*):", (DEMO / "Makefile").read_text(), re.M))
    named = {m.group(1) for ref in spans(DOCS[doc]) if (m := re.match(r"make ([a-z][\w-]*)", ref))}
    assert named, "the draft names the make targets that run what it describes"
    assert named <= targets, sorted(named - targets)


AB = "## 4. The A/B as built in the demo (issue #38)"


def ab(sub: str) -> str:
    return section(section(DOCS["runtime-pipeline.md"], AB), f"### {sub}")


def row(table: str, first: str) -> str:
    """The table row whose first cell opens with `first`, in backticks."""
    found = [line for line in table.splitlines() if re.match(rf"\| `{re.escape(first)}`[^|`]*\|", line)]
    assert len(found) == 1, f"one row for {first}"
    return found[0]


def test_the_run_config_section_lists_every_stage_and_its_values():
    text = ab("Run config")
    for stage, values in STAGES.items():
        line = row(text, stage)
        for v in values:
            assert f"`{v}`" in line, f"{stage}: {v}"
    arm = row(text, "arm")
    for name in [*PRESETS, *AGENT_ARMS]:
        assert f"`{name}`" in arm, name
    said = flat(text)
    assert "RunConfig.problems" in said and "`stages`" in said and "400" in said and "GET /presets" in said


def test_the_arms_are_the_agents_run_configs_with_the_presets_kept_as_the_baseline():
    text = ab("Arms")
    for arm, cfg in AGENT_ARMS.items():
        line = row(text, arm)
        for stage, value in cfg.stages().items():
            assert f"{stage} `{value}`" in line, f"{arm}: {stage} {value}"
    said = flat(text)
    for preset in PRESETS:
        assert f"`{preset}`" in said, preset
    assert "/answer" in said and "baseline" in said


def test_the_merge_point_names_the_shared_steps():
    text = flat(ab("The merge point"))
    for step in (PROMPTS["sql"], PROMPTS["phrase"], "read_reply", "join_access", "sqlguard.gate", "Agent.cite", "Agent.render",
                 "Agent.evidence", "number_check", "rules"):
        assert step in text, step


def test_the_run_trace_fields_are_the_traces():
    text = ab("Run trace")
    fields = set(runtrace.Trace("answer").finish({}))
    table = [line for line in text.splitlines() if line.startswith("| `")]
    listed = {f for line in table for f in CODE.findall(line.split(" | ")[0])}
    assert fields <= listed, sorted(fields - listed)
    for said in ("PROFILE", "meridian", "sha256", "never the key", "ContextVar"):
        assert said in flat(text), said


def test_the_number_check_row_says_the_trace_keeps_counts():
    t = runtrace.Trace("answer")
    t.number_check(False, stray_numbers=2, bad_markers=1)
    assert t.finish({})["number_check"] == {"passed": False, "stray_numbers": 2, "bad_markers": 1}, "counts, not what was thrown out"
    said = flat(row(ab("Run trace"), "number_check"))
    assert "how many numbers not in the result" in said and "markers naming no citation" in said, said
    assert "the numbers and markers it threw out" not in said


def test_the_prompt_hash_claim_holds_for_the_first_sql_call_and_says_what_a_retry_adds():
    """The SQL prompt ends with the question after the context block, and a retry appends the gate's reasons after it,
    outside the block (Agent.answer), so the prompt less the block carries them (tests/integration/test_agent_mock.py)."""
    assert Agent.prompt(ANY_CLIENT, "sql", "Q", "B").endswith(f"{CONTEXT_CLOSE}\n\nQuestion: Q")
    assert "{reasons}" in RETRY and CONTEXT_OPEN not in RETRY and CONTEXT_CLOSE not in RETRY
    held = [line for line in section(DOCS["implementation-plan.md"], "## 5. Arms").splitlines() if line.startswith("- Held equal:")]
    assert len(held) == 1, "one Held equal line under Built (#38)"
    for where, text in (("runtime-pipeline.md, the merge point", flat(ab("The merge point"))), ("implementation-plan.md §5", flat(held[0]))):
        assert "first SQL" in text and "outside the block" in text and "same reasons" in text, where
        assert not re.search(r"(?<!first )SQL prompts differ only", text), where


def test_the_plans_headline_says_what_stays_planned_in_the_phases_it_calls_built():
    text = DOCS["implementation-plan.md"]
    head = flat(text.split("**v0.2, 11 Oct 2026 (issue #38).**", 1)[1].split("\n\n", 1)[0])
    status = text.split(STATUS, 1)[1].split("\n\n", 2)[1]
    planned = {cells[0]: cells[2] for line in status.splitlines() if re.match(r"\| \d", line)
               for cells in [[c.strip() for c in line.strip("|").split("|")]]}
    assert "D5" in planned["0"]
    for said in ("§4 query set", "the SQL diff", "win/loss/tie", "git sha"):
        assert said in planned["2"], f"the status table's Phase 2 Planned cell: {said}"
    assert "Planned: the SQL diff and win/loss/tie per question, and the git sha" in flat(section(text, "## 6. Scoring and reporting"))
    assert "Phases 0 and 2 are built" not in head
    for said in ("D5 stays the owner's call", "the §4 query set as a set of its own", "the SQL diff", "win/loss/tie", "git sha"):
        assert said in head, said


def test_the_certification_counts_the_plan_quotes_are_what_landed():
    from kge.landing.zone import Zone
    rows = Zone(BUILD / "landing", "meridian").open().certification()
    ready = [r for r in rows if r["ai_ready"] == "yes"]
    tiers = {t: sum(1 for r in ready if r["tier"] == t) for t in ("gold", "silver", "bronze")}
    said = (f"{len(ready)} of the {len(rows)} documents land AI-ready: {tiers['gold']} gold, {tiers['silver']} silver and "
            f"{tiers['bronze']} bronze")
    assert said in flat(DOCS["implementation-plan.md"])


def test_the_margin_the_plan_quotes_is_the_committed_baselines():
    text = flat(DOCS["implementation-plan.md"])
    g = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())["margin"]["golden"]["all"]
    pts = {m: round(g[m]["mean"] * 100) for m in ("pass_rate", "figure_correct", "behaviour_class", "tier_correct")}
    assert (f"ahead by {pts['pass_rate']} points on pass rate, {pts['figure_correct']} on figure correct, {pts['behaviour_class']} "
            f"on behaviour class and {pts['tier_correct']} on tier-correct citations") in text


def test_the_plan_states_the_post_load_check_as_built():
    text = flat(DOCS["implementation-plan.md"])
    words = {"pass_rate": "pass rate", "figure_correct": "figure correct", "behaviour_class": "behaviour class",
             "gate_first_try": "gate first try", "tier_correct": "tier-correct citations", "numbers_caught": "unsupported numbers"}
    assert set(report.MARGIN) == set(words) | {"numbers_shown"}, "a new margin score is named in the plan"
    for said in words.values():
        assert said in text, said
    for said in ("`make margin`", "`kge/eval/margin.py`", "spread across repeats", "exit 1", "exit 2",
                 "`clients/meridian/baselines/issue-38.json`", "`clients/meridian/baselines/issue-39.json`", "not a threshold",
                 "without a baseline it can read with a margin on rules, or with one of another profile's, on a profile with no "
                 "estate or no graph"):
        assert said in text, said


def test_the_plan_says_the_ab_report_names_the_certification_stand_in_as_the_report_does():
    risk = flat(DOCS["implementation-plan.md"]).split("**Tier producer is a stub.**", 1)[1].split("- **", 1)[0]
    assert "today it does not" not in risk and "the A/B report" in risk and "`kge/eval/report.py`" in risk, risk
    assert "`kge/estate/certify.py`" in report.STAND_IN and "a stand-in for upstream AI certification" in report.STAND_IN


def test_the_query_sets_refused_joins_are_the_golden_sets_the_harness_runs():
    """§4 says its expected behaviours are from kge/estate/golden.py; golden v2 (#39, Option B) dropped retail-wealth from
    G021's refused joins, so a row that counts refused joins counts the set the harness runs, and names its version
    where the set declares that item changed."""
    golden = sets.read(BUILD / "golden.json")
    assert golden["version"] == harness.SETS["golden"], "the build's golden set is the version the harness runs: make golden"
    expect = {i["id"]: i["expect"] for i in golden["items"]}
    changed = set((golden["changes_from"] or {}).get("items", []))
    said = {}
    for line in section(DOCS["implementation-plan.md"], "## 4. Experiment query set (16 core + 3 stretch)").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and re.fullmatch(r"G\d{3}", cells[1]) and "joins refused" in cells[5]:
            said[cells[1]] = cells[5]
    assert "G021" in said, "Q10's Expected cell counts G021's refused joins"
    for gid, cell in said.items():
        e = expect[gid]
        n = re.search(r"\b(\d+) joins refused", cell)
        assert cell.startswith(e["behaviour"]) and n and int(n.group(1)) == len(e["joins_refused"]), (gid, cell, e["joins_refused"])
        if gid in changed:
            assert f"golden {golden['version']}" in cell, (gid, cell)


def test_the_held_out_results_the_drafts_quote_are_the_baselines():
    """The v0.1 text quoted 01836f6's README, whose held-out count was already out of date: the parser has read "still
    unresolved" and a date of birth since #29, so each draft line that quotes it carries today's result, and Stage 1's
    gaps name the README's misses, one per item the graph preset misses."""
    b = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())
    held, graph = b["items"]["heldout"], b["passed"]["heldout"]["graph"]["all"]
    plan = DOCS["implementation-plan.md"]
    risk = [line for line in section(plan, "## 7. Risks").splitlines() if "**Rule-parser overfit.**" in line]
    assert len(risk) == 1 and f"{graph:.0f} of the {len(held)} held-out questions" in risk[0].split("**Built (#38):**", 1)[1], risk
    rows = {}
    for line in section(plan, "## 4. Experiment query set (16 core + 3 stretch)").splitlines():
        m = re.match(r"\| (Q\d+) \| [^|]*?\b(H\d{3})\b", line)
        if m:
            rows[m.group(2)] = line
    passing = sorted(h for h, r in held.items() if all(r[a["arm"]] == 1.0 for a in b["arms"]))
    assert {"H006", "H015"} <= set(passing) and {"H006", "H015"} <= set(rows)
    for h in passing:
        if h in rows:
            note = flat(rows[h].split("**Built (#38):**", 1)[-1]) if "**Built (#38):**" in rows[h] else ""
            assert "every arm" in note and "tie" in note, f"{h}: {rows[h]}"
    readme = (DEMO / "README.md").read_text()
    misses = re.findall(r'"([^"]+)"', readme.split("The misses are the parser's:", 1)[1].split(". ", 1)[0])
    assert len(misses) == len(held) - graph, misses
    stage1 = section(DOCS["runtime-pipeline.md"], "### Stage 1: Intent & entity resolution")
    gap = [line for line in stage1.splitlines() if line.startswith("- Held-out misses")]
    assert len(gap) == 1 and "**Built (#38):**" in gap[0], gap
    now = gap[0].split("**Built (#38):**", 1)[1]
    for miss in misses:
        assert f'"{miss}"' in now, miss
    assert "still unresolved" in now and "date of birth" in now, "the two the parser reads, said to be read"


def test_the_docs_say_only_an_sql_call_records_a_context_hash():
    """The phrase prompt has no context block, so its call's trace entry has the prompt's hash and no context hash."""
    class Stub:
        def json(self, prompt, max_tokens):
            runtrace.llm_call("m", "m", 1, 1, 0.0)
            return {}
    agent = SimpleNamespace(llm=Stub())
    with runtrace.start("agent") as t:
        Agent.call(agent, "sql", Agent.prompt(ANY_CLIENT, "sql", "Q", "B"), "B")
        Agent.call(agent, "phrase", Agent.prompt(None, "phrase", "Q", facts={}))
    sql, phrase = t.calls
    assert CONTEXT_OPEN not in Agent.prompt(None, "phrase", "Q", facts={})
    assert {"prompt_hash", "context_hash"} <= set(sql) and "prompt_hash" in phrase and "context_hash" not in phrase
    readme = flat((DEMO / "README.md").read_text())
    trace = row(ab("Run trace"), "llm_calls")
    agent_para = readme.split("The run trace names the arm and what ran it (`agent`)", 1)[1].split(". A retry", 1)[0]
    llm_calls = readme.split("| `llm_calls` |", 1)[1].split(" | `", 1)[0]
    for where, said in (("README, llm_calls", llm_calls), ("README, the reference agent", agent_para),
                        ("runtime-pipeline.md, llm_calls", flat(trace))):
        assert "two hashes" not in said and "phrase call" in said and "no context block" in said, (where, said)


def test_the_plan_says_the_report_shows_the_mean_and_range_and_summary_json_the_standard_deviation():
    s = report.spread([1.0, 2.0, 4.0])
    assert s["sd"] > 0 and report.show(s, "count") == f"{report.num(s['mean'])} ({report.num(s['min'])}–{report.num(s['max'])})"
    scoring = flat(section(DOCS["implementation-plan.md"], "## 6. Scoring and reporting"))
    built = scoring.split("**Built (#38)** in `kge/eval/metrics.py`", 1)[1].split("**The A/B margin", 1)[0]
    assert "lowest, highest and standard deviation" not in built, "the report shows no standard deviation"
    assert "the mean with the lowest and highest" in built and "`summary.json` the standard deviation" in built, built


SPEC, CONTRACT, PLAN, PIPELINE = (DOCS[n] for n in ("graph-model-spec.md", "pack-contract.md", "implementation-plan.md",
                                                     "runtime-pipeline.md"))
PROFILES = "## 8. Profiles and layout"
STATUS = "**Status in the demo (issues #38, #39 and #40):**"
ONBOARDING = "## 7. Onboarding"
TEMPLATES = ("pack.example.yaml", "graph-model.example.yaml")
BASE = "4d9d5c9"
V02_SECTIONS = {"manifest", "schema", "sources", "no_join", "glossary", "terminology", "calendars", "period_bindings", "measures",
                "mappings", "policies", "access", "tests"}


def slug(heading: str) -> str:
    """A heading's anchor, as GitHub makes it: lower case, punctuation dropped, each space a hyphen."""
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading.lstrip("#").strip().lower()))


def rule_ids(text: str) -> set[str]:
    """The structural rules text names, a range (S16 to S21, S16–S21, S16-S21) as every rule in it."""
    out = set()
    for a, b in re.findall(r"\bS(\d\d)(?:\s*(?:–|-|to)\s*S(\d\d))?\b", text):
        out |= {f"S{n:02d}" for n in range(int(a), int(b or a) + 1)}
    return out


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def compiled():
    """Meridian's pack compiled in memory, as make compile compiles it: it reads files and writes nothing."""
    from kge.compiler import compile as compile_cli
    from kge.compiler import emit
    from kge.compiler import pack as packs
    meta = packs.meta_model(DEMO / "meta-model.yaml")
    return emit.build(packs.read(DEMO / "clients", "meridian"), meta, compile_cli.VERSION)


def meta_models() -> list[dict]:
    """The meta-model as 1.0.0, 1.1.0 and 1.2.0 had it (the fixtures #39 froze, and 1.2.0's when 1.3.0 came), then as it is."""
    import yaml
    return [yaml.safe_load(p.read_text()) for p in (DEMO / "tests" / "fixtures" / "meta-model-1.0.0.yaml",
                                                     DEMO / "tests" / "fixtures" / "meta-model-1.1.0.yaml",
                                                     DEMO / "tests" / "fixtures" / "meta-model-1.2.0.yaml", DEMO / "meta-model.yaml")]


def test_the_drafts_are_the_versions_issues_39_and_40_made_them_and_every_reference_names_a_drafts_version():
    """Issue #39 made three drafts v0.3 and issue #40 (its owner question 25's default) makes the two it updates v0.4;
    the issues give runtime-pipeline.md no version of its own."""
    versions = {"graph-model-spec.md": "v0.3", "pack-contract.md": "v0.4", "implementation-plan.md": "v0.4"}
    readme = (PLATFORM / "README.md").read_text()
    for name, text in DOCS.items():
        version = re.search(r"draft (v\d+\.\d+)", text.splitlines()[0]).group(1)
        if name in versions:
            assert version == versions[name], (name, version)
        listed = [line for line in readme.splitlines() if line.startswith(f"| [`{name}`]")]
        assert len(listed) == 1 and listed[0].rstrip(" |").endswith(f"Draft {version}"), (name, listed)
        for other, said in DOCS.items():
            for cited in re.findall(rf"\[{re.escape(name)}\]\({re.escape(name)}\) (v\d+\.\d+)", said):
                assert cited == version, f"{other} cites {name} {cited}; it is {version}"


def test_the_platform_index_points_at_the_profiles_section():
    readme = (PLATFORM / "README.md").read_text()
    assert PROFILES in SPEC.splitlines(), "graph-model-spec.md has the section"
    assert f"(graph-model-spec.md#{slug(PROFILES)})" in readme


def test_the_specs_profiles_section_holds_the_artifact_the_compiler_writes():
    text = section(SPEC, PROFILES)
    art = compiled()
    for key in art.manifest:
        assert f"`{key}`" in text, f"manifest.json's {key}"
    for path in art.tree():
        held = path.rsplit("/", 1)[0] + "/" if "/" in path else path
        assert held in text, held
    for said in ("artifacts/<client>/<model_hash>/", "artifacts/<client>/current", "data/nodes/<Type>.csv",
                 "data/edges/<EDGE>__<From>__<To>.csv", "ddl/kuzu.cypher", "`kge/compiler/compile.py`", "`make compile`"):
        assert said in text, said
    assert art.manifest["client"] == "meridian" and art.manifest["target"] == "kuzu"
    assert "data/<Type>.csv" not in SPEC and "data/<EDGE>" not in SPEC, "§6 names the folders the artifact has"


def test_the_specs_profiles_section_holds_the_registry_the_store_and_the_layout_as_built():
    """Certification's store is in the client's estate, which for a fixture profile is not build/ itself
    (kge/estate/certify.py)."""
    from kge.pipeline import profile
    from kge.adapters.graphstore import STORE
    text = section(SPEC, PROFILES)
    said = flat(text)
    for field in profile.FIELDS:
        assert f"`{field}`" in text, f"a profile's {field}"
    for path in ("`deploy/profiles.yaml`", "`kge/pipeline/profile.py`", "`PROFILE`", "`PROFILES`", "`meta-model.yaml`",
                 "clients/<client>/", "deploy/<client>/identities.yaml", "<estate>/certification/<client>/runs/",
                 "`build/certification/meridian/runs/`", "`build/thornbury/certification/thornbury/runs/`",
                 "landing/<client>/releases/<release_id>/", "stores/<client>/<model_hash>/graph.ladybug", "stores/<client>/overlay/",
                 "stores/<client>/run/", "`LOADED`", "`make release`", "`kge/graph/release.py`", "`tests/integration/test_profiles.py`"):
        assert path in text, path
    assert STORE == "ladybug" and "Ladybug" in said and "`GRAPH_STORE`" not in text
    assert "`kuzu` dialect" in said, "the DDL's dialect, which Ladybug loads"
    assert "one runtime process per client" in said and "R8" in said, "the two decisions the layout rests on"
    assert "build/certification/<client>/" not in SPEC, "certification's store is <estate>/certification/<client>/runs/"


def test_r1_revises_the_specs_readiness_and_layers_and_certification_is_never_in_the_runtime_overlay():
    s33, s4 = section(SPEC, "### 3.3 Readiness and tiers"), section(SPEC, "## 4. Layers and who writes what")
    for where, text in (("§3.3", s33), ("§4", s4)):
        assert "R1" in text and "landing" in text, where
    outcomes = [line for line in s33.splitlines() if line.startswith("| **Outcomes**")]
    assert len(outcomes) == 1 and "runtime" not in cells(outcomes[0])[2] and "own store" in outcomes[0], outcomes
    runtime = [line for line in s4.splitlines() if line.startswith("| runtime |")]
    assert len(runtime) == 1 and "certification" not in cells(runtime[0])[1], runtime
    assert "discards steward decisions" not in flat(s4)
    overlay = [p for p in section(SPEC, PROFILES).split("\n\n") if p.startswith("**The runtime overlay")]
    assert len(overlay) == 1
    said = flat(overlay[0])
    for needle in ("`kge/pipeline/overlay.py`", "decisions.jsonl", "steward decisions", "tags", "never certification", "R1",
                   "`make reset-overlay`"):
        assert needle in said, needle


def test_the_spec_gives_the_meta_model_as_built_and_the_certified_join_that_replaced_a_no_join_fact():
    import yaml
    then, mid, late, now = meta_models()
    text = section(SPEC, PROFILES)
    said = flat(text)
    assert f"`meta-model.yaml` {now['version']}" in said
    assert f"{len(now['node_types'])} node types and {len(now['edge_types'])} edge types" in said
    for older, newer in ((then, mid), (mid, late), (late, now)):
        for kind in ("node_types", "edge_types"):
            for name in newer[kind]:
                if name not in older[kind]:
                    assert f"`{name}`" in text, f"{newer['version']} adds {name}"
    built = flat(section(SPEC, "### 3.2 Meta-model v1 (proposal): 15 node types, 25 edge types"))
    assert f"{len(then['node_types'])} node types and {len(then['edge_types'])} edge types" in built, "1.0.0, as built"
    pack = yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())
    (joined,) = pack["certified_joins"]
    assert "-".join(joined["between"]) in said and joined["note"] in said and "`NO_JOIN`" in text
    assert len(pack["no_join"]) == 5 and "five" in said


def test_the_contract_requires_the_client_and_its_meta_model_version_in_the_manifest_and_namespaces_extensions():
    manifest = flat(section(CONTRACT, "### 4.1 manifest"))
    for field in ("client", "meta_model_version"):
        assert f"`{field}`" in manifest, field
    assert "required" in manifest and "S00" in manifest
    layout = flat(section(CONTRACT, "## 2. Layout"))
    assert "clients/<client>/" in layout and "`deploy/profiles.yaml`" in layout
    for doc in (CONTRACT, SPEC):
        assert "x_<pack>_" not in doc and "`x_<client>_" in doc
    access = flat(section(CONTRACT, "### 4.10 access (optional) — split decision"))
    assert "deploy/<client>/identities.yaml" in access and "access/roles.json" in access


def test_the_contract_lists_every_check_the_compiler_makes_and_says_which_it_does_not():
    from kge.compiler import checks
    runs = set(re.findall(r"^  (S\d\d)  ", checks.__doc__, re.M))
    assert {"S00", "S29"} <= runs
    rules = section(CONTRACT, "### 5.1 Structural (pre-compile; any failure ⇒ not compilable)")
    listed = set(re.findall(r"^- (S\d\d) ", rules, re.M))
    assert runs <= listed, sorted(runs - listed)
    built = [p for p in rules.split("\n\n") if p.startswith("**Built (#39)")]
    assert len(built) == 1, "one Built (#39) paragraph under 5.1"
    done, _, undone = flat(built[0]).partition("Not checked there")
    assert runs <= rule_ids(done), sorted(runs - rule_ids(done))
    assert rule_ids(undone) == rule_ids(checks.NOT_CHECKED), (sorted(rule_ids(undone)), checks.NOT_CHECKED)


def test_the_contract_names_every_section_a_pack_may_hold_and_which_are_required():
    """V02_SECTIONS are the sections the contract had before #39 (v0.2); every other section the compiler reads is
    #39's query semantics."""
    from kge.compiler.checks import REQUIRED
    table = [cells(line) for line in section(CONTRACT, "## 3. Required vs optional").splitlines() if line.startswith("| `")]
    for name in SECTIONS:
        rows = [r for r in table if re.search(rf"`{name}(`|\.|\[)", r[0])]
        assert rows, f"§3 has no row for {name}"
        required = [bool(re.match(r"R\b(?!\*| if)", r[1])) for r in rows]
        assert any(required) if name in REQUIRED else not any(required), (name, [r[1] for r in rows])
    named = {re.split(r"[.\[]", name, maxsplit=1)[0] for r in table for name in CODE.findall(r[0])}
    assert named <= set(SECTIONS), sorted(named - set(SECTIONS))
    query = section(CONTRACT, "### 4.13 query semantics (optional; meta-model 1.1)")
    for name in sorted(set(SECTIONS) - V02_SECTIONS):
        assert f"`{name}" in query, name
    assert "`scd2`" in query and "`release_driver`" in query
    assert {"S23", "S24", "S25", "S26", "S27", "S28"} <= rule_ids(query)
    assert "`certified_joins" in flat(section(CONTRACT, "### 4.3 sources")), "a certified join replaces a pair's no-join fact"


@pytest.mark.parametrize("after", ["manifest", "certified_joins", "entities", "contexts"])
def test_the_contract_rejects_an_unknown_section_after_any_name_in_a_row(monkeypatch, after):
    table = section(CONTRACT, "## 3. Required vs optional")
    changed = table.replace(f"`{after}`", f"`{after}`, `documents`", 1)
    assert changed != table
    monkeypatch.setitem(globals(), "CONTRACT", CONTRACT.replace(table, changed, 1))
    with pytest.raises(AssertionError, match="documents"):
        test_the_contract_names_every_section_a_pack_may_hold_and_which_are_required()


def plan_status() -> dict[str, list[str]]:
    status = PLAN.split(STATUS, 1)[1].split("\n\n", 2)[1]
    return {c[0]: c for line in status.splitlines() if re.match(r"\| \d", line) for c in [cells(line)]}


def test_the_plan_says_what_issue_39_built_in_phases_3_and_5():
    rows = plan_status()
    assert set(rows) == {"0", "1", "2", "3", "4", "5"}
    p3, p5 = flat(rows["3"][1]), flat(rows["5"][1])
    assert p3.startswith("**#39:**") and "**#39:**" in p5
    for said in ("`meta-model.yaml`", "`kge/compiler/compile.py`", "`clients/meridian/pack.yaml`", "`clients/meridian/pack.lock`",
                 "`build/artifacts/meridian/<model_hash>/`", "`make parity`", "`tests/integration/test_parity.py`"):
        assert said in p3, said
    for said in ("`deploy/profiles.yaml`", "`PROFILE`", "`build/stores/<client>/`", "`make release`", "`tests/integration/test_profiles.py`",
                 "`tests/unit/test_client_nouns.py`"):
        assert said in p5, said
    built = [p for p in section(PLAN, "## 3. Phases").split("\n\n") if p.startswith("**Built (#39):**")]
    assert len(built) == 1, "one Built (#39) paragraph under the phases"
    said = flat(built[0])
    for needle in ("`build/artifacts/<client>/<model_hash>/`", "`data/nodes/`", "`data/edges/`", "`build/artifacts/<client>/current`",
                   "`tests/unit/test_client_nouns.py`", "`tests/integration/test_profiles.py`", "#40"):
        assert needle in said, needle


def test_the_plan_names_the_no_client_nouns_gate_and_that_it_allows_no_line():
    from tests.unit.test_client_nouns import SPECIALS
    built = flat(next(p for p in section(PLAN, "## 3. Phases").split("\n\n") if p.startswith("**Built (#39):**")))
    gate = built.split("`tests/unit/test_client_nouns.py`", 1)[1].split("Not built", 1)[0]
    for said in ("no platform line names a client", "`kge/services/ask/engine/core.py`", "the owner's Question 18 default", "until the owner answers",
                 "allows no line"):
        assert said in gate, said
    assert SPECIALS == {}


def test_the_plan_records_issue_39s_two_open_decisions_and_who_made_them():
    rows = {c[0]: c for line in section(PLAN, "## 8. Open decisions").splitlines() if re.match(r"\| D\d", line) for c in [cells(line)]}
    d7, d8 = flat(" | ".join(rows["D7"])), flat(" | ".join(rows["D8"]))
    assert "One runtime process per profile" in d7 and "multi-profile router" in d7 and "**Decided (owner, in the working session; not yet recorded on #39):**" in d7
    assert "one process per client" in d7
    assert "deployment config" in d8 and "`deploy/profiles.yaml`" in d8 and "R8" in d8
    assert "**Default built (#39), from R8, until the owner confirms:**" in d8 and "Decided" not in d8


def test_the_query_set_names_golden_wealth_v1_and_its_results_are_the_baselines():
    gw = sets.read(BUILD / "golden_wealth_v1.json")
    b = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-40.json").read_text())
    passed = b["passed"]["golden_wealth_v1"]
    n, kinds = len(gw["items"]), len({i["category"] for i in gw["items"]})
    q = flat(section(PLAN, "## 4. Experiment query set (16 core + 3 stretch)"))
    assert "`golden_wealth_v1`" in q and f"{n} questions in {kinds} categories" in q
    assert (f"the agent's treatment passes {passed['treatment']['all']:.0f} of the {n} and its control "
            f"{passed['control']['all']:.0f}") in q
    assert "`clients/meridian/baselines/issue-40.json`" in q


def test_the_drafts_say_the_parsers_aliases_are_the_packs_since_issue_39():
    stage1 = flat(section(PIPELINE, "### Stage 1: Intent & entity resolution"))
    arms = [line for line in section(PLAN, "## 5. Arms").splitlines() if line.startswith("- Stage 1:")]
    assert len(arms) == 1
    for where, said in (("runtime-pipeline.md, Stage 1", stage1), ("implementation-plan.md §5", flat(arms[0]))):
        assert "still hardcoded" not in said and "`Alias`" in said and "pack" in said, where


RULES = "**What a client's data must meet that no check states**"


def onboarding_steps() -> dict[str, list[str]]:
    """The onboarding table's rows, by step: step, who, run, done when."""
    return {c[0]: c[1:] for line in section(CONTRACT, ONBOARDING).splitlines() if re.match(r"\| \d \|", line) for c in [cells(line)]}


def test_the_contracts_onboarding_section_has_a_row_per_step_each_naming_the_make_target_that_runs_it():
    targets = set(re.findall(r"^([a-z][\w-]*):", (DEMO / "Makefile").read_text(), re.M))
    steps = onboarding_steps()
    assert list(steps) == [str(n) for n in range(1, 10)], list(steps)
    for n, row in steps.items():
        assert len(row) == 4 and all(row), (n, row)
        named = {m.group(1) for ref in spans(" | ".join(row)) if (m := re.match(r"make ([a-z][\w-]*)", ref))}
        assert named and named <= targets, (n, sorted(named - targets))
    for n, target in (("3", "estate"), ("3", "lock"), ("4", "check"), ("5", "compile"), ("6", "load"), ("7", "margin"),
                      ("8", "sign-off"), ("8", "release")):
        assert f"`make {target}" in steps[n][2], (n, target)
    said = flat(section(CONTRACT, ONBOARDING))
    for needle in ("R8", "`clients/fellmoor/`", "`tests/unit/test_onboarding.py`", "`gates.json`", "`LOADED`", "`GATE`", "`SIGNOFF`",
                   "released without the onboarding gate", "`clients/<client>/fixture/heldout.json`"):
        assert needle in said, needle


def test_the_onboarding_rules_the_contract_states_are_the_codes():
    """Candidate terms are two to four capitalised words and a colon, as Fellmoor's uncertified note writes its
    candidate."""
    import yaml
    from kge.estate import certify
    from kge.pipeline.tagger import CANDIDATE_RE
    rules = flat(section(CONTRACT, ONBOARDING).split(RULES, 1)[1])
    assert CANDIDATE_RE.pattern == r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,3}):\s" and "two to four capitalised words followed by a colon" in rules
    pack = yaml.safe_load((DEMO / "clients" / "fellmoor" / "pack.yaml").read_text())
    (candidate,) = {a["candidate"] for a in pack["terminology"]["aliases"] if "candidate" in a}
    notes = [p.read_text() for p in (DEMO / "clients" / "fellmoor" / "fixture" / "docs" / "uncertified").glob("*.md")]
    assert f"*{candidate}:*" in rules and any(candidate in [m.group(1) for m in CANDIDATE_RE.finditer(n)] for n in notes)
    definitional = next(i["phrases"] for i in pack["terminology"]["intents"] if i["intent"] == "definitional")
    assert "can journeys" in definitional and "*can journeys*" in rules
    assert certify.REVIEW_WINDOW_DAYS == 90 and "within 90 days" in rules
    assert 'default="pipeline_tagger"' in (DEMO / "kge" / "pipeline" / "run.py").read_text() and "`pipeline_tagger`" in rules
    assert "fixture/schema.yaml),-m kge.estate.from_files,-m kge.estate.generate)" in (DEMO / "Makefile").read_text()
    assert "`ESTATE_FROM`" in rules and "`kge/estate/generate.py`" in rules


def test_the_platform_index_points_at_the_onboarding_section():
    readme = (PLATFORM / "README.md").read_text()
    assert ONBOARDING in CONTRACT.splitlines() and slug(ONBOARDING) == "7-onboarding"
    assert f"(pack-contract.md#{slug(ONBOARDING)})" in readme


def test_the_plan_has_the_second_client_exit_gate_in_phase_5_as_built_and_no_phase_5b():
    phases = section(PLAN, "## 3. Phases")
    table = {c[0]: c for line in phases.splitlines() if re.match(r"\| \d", line) for c in [cells(line)] if len(c) == 5}
    assert set(table) == {"0", "1", "2", "3", "4", "5"}, "Phase 5b goes: the second client is Phase 5's gate"
    gate = flat(table["5"][4])
    assert "A second client onboarded end to end (steps 1 to 8) with zero platform code changes" in gate
    assert "5b" not in phases, "no Phase 5b row, diagram node or status row"
    built = [p for p in phases.split("\n\n") if p.startswith("**Built (#40):**")]
    assert len(built) == 1, "one Built (#40) paragraph under the phases"
    said = flat(built[0])
    for needle in ("`clients/fellmoor/`", "`tests/unit/test_onboarding.py`", "`make margin HASH=<model_hash>`", "`kge/eval/margin.py`",
                   "`make sign-off PRINCIPAL=<steward>`", "`kge/graph/release.py`", "`GATE`", "`SIGNOFF`", "(pack-contract.md#7-onboarding)",
                   "Phase 3's onboarding test", "pack 1.3.0", "`make margin HASH=<model_hash> BASELINE=`",
                   "`clients/meridian/baselines/issue-40.json`"):
        assert needle in said, needle
    assert "and AUM, issue" not in PLAN, "AUM is built, #40's"
    p39 = flat(next(p for p in phases.split("\n\n") if p.startswith("**Built (#39):**")))
    assert "once the candidate has passed its post-load checks and been signed off" in p39, "#39's release, gated since #40"
    p5 = flat(plan_status()["5"][1])
    assert "**#40:**" in p5 and "`tests/unit/test_onboarding.py`" in p5
    assert "**Planned.** On the platform the same check gates" not in PLAN, "§6's gate line is built"


def test_the_specs_exit_gate_says_the_nouns_gate_allows_no_line_and_points_at_onboarding():
    gate = flat(next(p for p in section(SPEC, PROFILES).split("\n\n") if p.startswith("**The exit gate.**")))
    assert "but for the domain logic" not in gate and "no platform line names a client" in gate
    assert "(pack-contract.md#7-onboarding)" in gate and "`tests/unit/test_onboarding.py`" in gate


def test_the_run_config_has_a_row_for_every_field_and_the_profile_is_the_processs():
    import dataclasses
    from kge.services.ask.engine.core import RunConfig
    text = ab("Run config")
    for field in dataclasses.fields(RunConfig):
        row(text, field.name)
    said = flat(row(text, "profile"))
    for needle in ("deployment", "never per request", "400", "run trace", "manifest", "`PROFILE`", "`deploy/profiles.yaml`"):
        assert needle in said, needle


def test_the_run_traces_graph_row_names_every_hash_the_trace_records():
    keys = re.search(r"\{([^}]*)\}", runtrace.fingerprint.__doc__).group(1).replace(" ", "").split(",")
    assert keys == ["model", "legacy_model", "artifact", "overlay", "store"]
    said = row(ab("Run trace"), "graph")
    for key in keys:
        assert f"`{key}`" in said, key


def test_the_templates_name_the_client_and_meridians_artifact_paths():
    import yaml
    pack_text, model_text = ((PLATFORM / name).read_text() for name in TEMPLATES)
    pack, model = yaml.safe_load(pack_text), yaml.safe_load(model_text)
    assert pack["manifest"]["client"] == model["manifest"]["client"] == "meridian"
    keys = list(pack["manifest"])
    assert keys[keys.index("client") + 1] == "meta_model_version", "meta_model_version beside client"
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(pack["manifest"]["meta_model_version"]))
    for text in (pack_text, model_text):
        assert "artifacts/meridian/" in text
    assert "data/nodes/" in model_text and "data/edges/" in model_text
    assert "data/<Type>.csv" not in model_text and "data/<EDGE>" not in model_text


def test_the_pack_template_compiles_without_findings():
    import yaml
    review = BUILD / "review" / "pack-template"
    shutil.rmtree(review, ignore_errors=True)
    client = review / "clients" / "meridian"
    client.mkdir(parents=True)
    shutil.copyfile(PLATFORM / "pack.example.yaml", client / "pack.yaml")
    pack = yaml.safe_load((client / "pack.yaml").read_text())
    lock = json.loads((DEMO / "clients" / "meridian" / "pack.lock").read_text())
    harvested = {s["id"] for section_name in ("sources", "curated_sources") for s in pack.get(section_name, [])
                   if s.get("harvest")}
    lock["sources"] = {name: value for name, value in lock["sources"].items() if name in harvested}
    (client / "pack.lock").write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")
    run = subprocess.run([sys.executable, "-m", "kge.compiler.compile", "--check", "--client", "meridian",
                          "--clients", review / "clients", "--out", review / "out"], cwd=DEMO,
                         capture_output=True, text=True)
    try:
        assert run.returncode == 0, run.stderr
        assert not re.search(r"^S\d\d .*: ", run.stderr, re.M), run.stderr
    finally:
        shutil.rmtree(review, ignore_errors=True)


@pytest.mark.parametrize("name", ("graph-model.example.yaml",))
def test_nothing_else_in_the_graph_model_template_changed_since_the_base(name):
    """BASE is develop as #39 branched from it, which #37's templates were at."""
    import difflib
    import subprocess
    rel = f"docs/use-cases/kge/platform/{name}"
    old = subprocess.run(["git", "show", f"{BASE}:{rel}"], cwd=REPO, capture_output=True, text=True)
    if old.returncode != 0:
        pytest.skip(f"no git history holding {BASE} here")
    diff = [line for line in difflib.unified_diff(old.stdout.splitlines(), (PLATFORM / name).read_text().splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    assert diff, "the issue changes each template"
    added = re.compile(r"client: meridian|meta_model_version|artifacts/meridian/|data/nodes/|data/edges/")
    for line in diff:
        if line.startswith("+"):
            assert added.search(line), f"{name} adds {line!r}: only the client, its meta-model version and the artifact's paths"
        else:
            assert "data/<" in line, f"{name} drops {line!r}: only the artifact's old paths"
