"""The README's Demo script and the run that drives it, tests/e2e/demo_script.cjs (kge demo-script), say the same things.

The run is Playwright for Node in Chromium: it builds, serves, rebuilds and restarts the demo as a presenter does, so it is
not part of make test. These tests read the files only: the Makefile's target, the README's pointer to it, and that every
figure and every question or button the Demo script names is one the run checks or clicks, and no other figure, so the
README and the run cannot drift apart. With Node installed, the script must also parse.
"""

import os
import re
import shutil
import subprocess
import shutil as file_shutil
from dataclasses import replace
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2]
SCRIPT = DEMO / "tests" / "e2e" / "demo_script.cjs"
FIGURE = re.compile(r"(?<![\d.])(?<!\d,)\d{1,3}(?:,\d{3})*\.\d\d(?!\d)(?!\.\d)")


def section():
    readme = (DEMO / "README.md").read_text()
    return readme.split("\n## Demo script\n", 1)[1].split("\n## ", 1)[0]


def steps():
    return re.findall(r"^\d+\. \*\*([^*]+)\*\*", section(), re.M)


def test_walkthrough_builds_current_portal_before_loading_browser_fixtures():
    main = SCRIPT.read_text().split("async function main() {", 1)[1]
    install = main.index("run('npm', ['ci', '--prefix', 'portal/ui', '--no-audit', '--no-fund'])")
    build = main.index("run('npm', ['run', 'build', '--prefix', 'portal/ui'])")
    assert install < build < main.index("playwright.chromium.launch")
    assert build < main.index("require('./mention_suggestions.cjs')")
    assert build < main.index("require('./ask_planner.cjs')")
    assert build < main.index("require('./ask_layout.cjs')")


def test_make_demo_script_runs_the_playwright_script_and_make_test_does_not():
    make = (DEMO / "Makefile").read_text()
    assert re.search(r"^\.PHONY:.*\bdemo-script\b", make, re.M)
    recipe = make.split("\ndemo-script:", 1)[1].split("\n\n", 1)[0]
    assert "node tests/e2e/demo_script.cjs" in recipe
    assert "node --test tests/e2e/demo_ports.test.cjs" in make
    assert "PORT2" in recipe and "playwright@$(PLAYWRIGHT)" in recipe
    assert re.search(r"^PLAYWRIGHT \?= \d+\.\d+\.\d+$", make, re.M), "an exact version"
    assert not SCRIPT.name.startswith("test_") and SCRIPT.suffix == ".cjs", "pytest never collects it"


def demo_script_line(**ports):
    env = {k: v for k, v in os.environ.items() if k not in ("PORTAL_PORT", "TEST_PORT", "MAKEFLAGS", "MFLAGS")}
    out = subprocess.run([shutil.which("make") or "make", "-n", "-s", "demo-script", *(f"{k}={v}" for k, v in ports.items())],
                         cwd=DEMO, env=env, text=True, capture_output=True, timeout=60).stdout
    return next(line for line in out.splitlines() if "node tests/e2e/demo_script.cjs" in line)


def test_make_demo_script_passes_the_runner_ports_only_when_they_are_set():
    unset = demo_script_line()
    assert "PORTAL_PORT=" not in unset and "TEST_PORT=" not in unset
    lane = demo_script_line(PORTAL_PORT="8310", TEST_PORT="8215")
    assert 'PORTAL_PORT="8310"' in lane and 'TEST_PORT="8215"' in lane


def test_make_screenshots_writes_the_quick_start_views():
    make = (DEMO / "Makefile").read_text()
    assert re.search(r"^screenshots:\n\t\$\(MAKE\) demo-script SCREENSHOTS=1$", make, re.M)
    src = SCRIPT.read_text()
    assert "setViewportSize({ width: 1280" in src
    assert {"sign-in", "ask", "map", "onboard"} == set(re.findall(r"quickstartShot\([^,]+, '([^']+)'", src))


def test_the_demo_script_section_names_its_run():
    s = section()
    assert "`kge demo-script`" in s and "tests/e2e/demo_script.cjs" in s
    readme = (DEMO / "README.md").read_text()
    running = readme.split("\n## Running it\n", 1)[1].split("\n## ", 1)[0]
    assert "kge demo-script" in running


def test_the_run_takes_the_steps_in_the_readme_s_order():
    """Fifteen steps since #57, whose step 15 is industry standards and data policies."""
    src = SCRIPT.read_text()
    named = steps()
    assert len(named) == 15, named
    at = [src.index(f"'{n.rstrip('.')}'") for n in named]
    assert at == sorted(at), "the run's steps are the README's, in its order"


def test_every_figure_the_demo_script_names_is_one_the_run_checks_and_no_other():
    """A figure is a number with two decimals, such as 13,287.67, never the 1.56 of a version such as 1.56.1."""
    s, src = section(), SCRIPT.read_text()
    readme_figures = set(FIGURE.findall(s))
    assert readme_figures
    assert readme_figures == set(FIGURE.findall(src))
    assert "check('Lease Rentals" not in src, "figures must be read from the page, not compared inside a literal"
    assert "figure(leasing, '1,461.25')" in src and "figure(retail, '13,287.67')" in src
    assert "91.50" in src


def test_every_question_and_button_the_demo_script_names_is_one_the_run_asks_or_clicks():
    s, src = re.sub(r"\*\*[^*]+\*\*|`[^`]*`", "", section()), SCRIPT.read_text()
    italic = re.findall(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", s)
    assert len(italic) > 15, italic
    missing = [q for q in italic if q not in src]
    assert not missing, missing


def test_the_masking_step_runs_the_readme_s_command():
    s, src = section(), SCRIPT.read_text()
    grep = re.search(r"`(grep -rhoE [^`]+build/landing/meridian/current/documents[^`]*)`", s)
    assert grep, "step 1 shows what landed masked, with a command anyone can run"
    assert grep.group(1) in src.replace("\\\\", "\\")


def test_the_script_parses():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("no Node: kge demo-script needs it, make test does not")
    r = subprocess.run([node, "--check", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_onboarding_cleanup_uses_the_unit_reset_target():
    src = SCRIPT.read_text()
    cleanup = src.split("async function cleanUp()", 1)[1].split("async function main()", 1)[0]
    assert "kge('reset-onboard', 'UNIT=leasing', 'FORCE=1')" in cleanup
    assert "kge('reset-onboard', 'UNIT=bancassurance', 'FORCE=1')" in cleanup
    assert "rmSync(state.onboarding" not in cleanup and "writeFileSync(state.onboarding" not in cleanup


def test_step_13_uses_the_osi_yaml_example():
    src = SCRIPT.read_text()
    step = src.split("async function semanticBusinessUnit()", 1)[1].split("const STEPS", 1)[0]
    assert "SEMANTIC=examples/bancassurance-osi" in step
    assert "SEMANTIC=examples/bancassurance'" not in step


def test_the_script_resets_a_crashed_runs_unit_before_step_one():
    src = SCRIPT.read_text()
    start = src.split("async function main()", 1)[1].split("for (const [name, step] of STEPS)", 1)[0]
    assert "kge('reset-onboard', 'UNIT=leasing', 'FORCE=1')" in start
    assert "kge('reset-onboard', 'UNIT=bancassurance', 'FORCE=1')" in start


def review_profile(tmp_path, clients):
    import json
    from kge.pipeline.profile import resolve
    from kge.pipeline import access_table
    from kge.adapters import appstate
    prof = replace(resolve("meridian"), clients=clients, warehouses=tmp_path / "warehouses",
                   store=tmp_path / "store", artifacts=tmp_path / "artifacts", baseline=None)
    model_hash = "a" * 64
    folder = prof.artifacts / prof.client / model_hash / "access"
    folder.mkdir(parents=True)
    templates = access_table.served_roles(resolve("meridian"))
    (folder / "roles.json").write_text(json.dumps({"roles": list(templates.values())}))
    (prof.artifacts / prof.client / "current").symlink_to(model_hash)
    state = appstate.access_state(prof)
    for role in templates:
        state.access_put({"username": f"{role}.meridian@example.com", "name": role, "roles": [role]},
                         by="operator@example.com", via="job")
    return prof


def test_the_readme_s_onboarding_figure_is_the_one_draft_computes(tmp_path):
    from kge.estate import onboard
    clients = tmp_path / "clients"
    file_shutil.copytree(DEMO / "clients" / "meridian", clients / "meridian", ignore=file_shutil.ignore_patterns("units"))
    prof = review_profile(tmp_path, clients)
    ddl = (DEMO / "examples" / "leasing" / "schema.sql").read_text()
    csv = {p.name: p.read_bytes() for p in (DEMO / "examples/leasing/data").glob("*.csv")}
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=csv, ddl_text=ddl, as_="knowledge_engineer.meridian@example.com")
    drafted = onboard.draft(prof, "leasing", as_="knowledge_engineer.meridian@example.com", on="2026-10-17")
    figure = next(i["expect"]["figure"] for i in drafted["items"] if i["kind"] == "question")
    assert f"{float(figure):,.2f}" in section()


def test_the_readme_s_new_client_figure_is_the_one_draft_computes(tmp_path):
    from kge.estate import ddl
    models = ddl.parse_client((DEMO / "examples/brackenridge/schema.sql").read_text())
    taproom = next(m for m in models if m["unit"] == "taproom")
    files = {p.name: p.read_bytes() for p in (DEMO / "examples/brackenridge/data/taproom").glob("*.csv")}
    from kge.adapters.warehouse.duckdb import DuckDBEstate
    path = tmp_path / "taproom.duckdb"
    ddl.build(taproom, DuckDBEstate(tmp_path), {"csv": ddl.read_csvs(taproom, files)})
    import duckdb
    with duckdb.connect(str(path), read_only=True) as con:
        figure = con.execute("SELECT sum(takings_gbp) FROM pour").fetchone()[0]
    assert f"{figure:,.2f}" in section()


def test_the_readme_s_update_round_figures_are_the_ones_the_update_drafts(tmp_path):
    """Step 14's update round: the second folder's drafted golden figures, against the live first folder."""
    from kge.estate import onboard
    from kge.pipeline import unit_history
    clients = tmp_path / "clients"
    file_shutil.copytree(DEMO / "clients" / "meridian", clients / "meridian", ignore=file_shutil.ignore_patterns("units"))
    prof = review_profile(tmp_path, clients)
    onboard.create(prof, "bancassurance", label="Bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi",
                   as_="knowledge_engineer.meridian@example.com")
    onboard.draft(prof, "bancassurance", as_="knowledge_engineer.meridian@example.com", on="2026-10-17")
    unit_history.record(prof, "release", "steward_glossary.meridian@example.com", {"model_hash": "a" * 64}, ["bancassurance"])
    onboard.create(prof, "bancassurance", label="Bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2",
                   as_="knowledge_engineer.meridian@example.com")
    drafted = onboard.draft(prof, "bancassurance", as_="knowledge_engineer.meridian@example.com", on="2026-10-18")
    figures = {i["id"]: i["expect"]["figure"] for i in drafted["items"] if i["kind"] == "question"}
    changes = drafted["changes"]
    s = section()
    assert f"{len(changes['changed'])} changed, {len(changes['added'])} added, {len(changes['removed'])} removed" in s
    assert f"{float(figures['BANCASSURANCE-D01']):,.2f}" == "1,650.00" and "1,650.00" in s
    assert f"{float(figures['BANCASSURANCE-D03']):,.2f}" == "245.05" and "245.05" in s
    assert "SEMANTIC=examples/bancassurance-osi-v2" in s and "SEMANTIC=examples/bancassurance-osi-v2" in SCRIPT.read_text()


def test_onboarding_walkthrough_uses_unit_cards_and_the_account_menu():
    src = SCRIPT.read_text()
    assert "getByRole('button', { name: /^Account:/ })" in src
    assert "document.querySelector('#onboard').dataset.drawn" in src
    for selector in ("#ob-proposal-card", "#next-step", "#ob-versions", "#onboard"):
        assert selector in src
    assert "agentOnboard(page, 'rows'" in src and "'Signed off'" in src
    assert "No map for a refused answer" in src and "needs a unit you may not read" in src
    assert "#ob-items" not in src and "Read DDL" not in src
    assert "Onboard has no file handling controls" in src
    improvement = src.split("async function improveJob() {", 1)[1].split("  const versions = ", 1)[0]
    for label, endpoint in (("Accept all", "accept"), ("Certify all", "certify"), ("Run checks", "run"), ("Sign off", "run"), ("Release", "run")):
        assert f"onboardClick(page, '{label}', '/onboard/{endpoint}', null, '#ob-proposal-card')" in improvement


def test_the_browser_checks_unreleased_and_candidate_messages():
    src = SCRIPT.read_text()
    assert "await releaseMessages(state.page)" in src
    checks = src.split("async function releaseMessages(page) {", 1)[1].split("async function wealthQuestions", 1)[0]
    for phrase in ("mode.released === true", "banner.isVisible()", "banner.isHidden()", "banner.locator('img').count() === 0",
                   "Ignored: clients/c/<img src=x>", "Upload aaaaaaaaaaaa", "!text.includes('People reloaded:')",
                   "Checked again: the last check did not pass", "changes.locator('img,script').count() === 0",
                   "changes.locator('pre').textContent()"):
        assert phrase in checks


def test_the_gate_runs_only_the_portal():
    text = (DEMO / "Makefile").read_text()
    recipe = text.split("\ntest-e2e:", 1)[1].split("\n\n", 1)[0]
    assert "demo-script PART=$(PART)" in recipe
    assert "CONTEXT_TARGET" not in recipe
    script = SCRIPT.read_text()
    assert "CONTEXT_PAGE" not in script and "CONTEXT_TARGET" not in script
    assert "ui/index.html" not in script
    assert "the Context browser never requests KGE’s origin" in script


def split_js(text, delimiter=","):
    """Split arguments outside JavaScript strings and nested arrays or calls."""
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    for i, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == delimiter and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
            if delimiter == ")":
                return parts
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
    parts.append(text[start:].strip())
    return parts


def js_calls(text):
    """Every CLI call, including refusal, server, loop and spread arguments, in a function's source."""
    for match in re.finditer(r"\b(kge|refused|serve|run|spawnSync)\(", text):
        tail = text[match.end():]
        end = split_js(tail, ")")[0]
        yield match.group(1), split_js(end)


def js_words(expressions, source):
    words = []
    for expression in expressions:
        if expression.startswith("...at("):
            words += js_words(split_js(expression[6:-1]), source) + ["PROFILE=fellmoor"]
        elif expression == "...markdown":
            array = re.search(r"const markdown = \[(.*?)\];", source).group(1)
            words += js_words(split_js(array), source)
        elif expression.startswith("...("):
            words += ["HOST=<value>"]
        else:
            assert expression[:1] in ("'", '"', "`"), expression
            toy = re.search(r"const TOY = '([^']+)'", source).group(1)
            value = expression[1:-1].replace("${TOY}", toy)
            for name in ("PORT", "PORT2", "PORT3", "TEST_PORT", "PORT4"):
                declaration = re.search(rf"^const {name} = (.*?);", source, re.M).group(1)
                default = re.search(r"\b(\d+)\b", declaration).group(1)
                value = value.replace("${" + name + "}", default)
            words.append(re.sub(r"\$\{[^}]+\}", "<value>", value))
    return words


def command_words(text):
    import shlex
    return shlex.split(re.sub(r"<[^>]+>", "<value>", text))


def command_matches(actual, documented):
    """Settings are unordered; only documented placeholders may vary."""
    if len(actual) != len(documented):
        return False
    actual_commands = [word for word in actual if "=" not in word]
    documented_commands = [word for word in documented if "=" not in word]
    if actual_commands != documented_commands:
        return False
    for left, right in zip(sorted(actual), sorted(documented)):
        if right == left:
            continue
        pattern = re.escape(right).replace(re.escape("<value>"), ".+")
        if not re.fullmatch(pattern, left):
            return False
    return True


def walkthrough_commands(source):
    functions = list(re.finditer(r"^(?:async )?function (\w+)\(", source, re.M))
    bodies = {m.group(1): source[m.end():functions[i + 1].start() if i + 1 < len(functions) else len(source)]
              for i, m in enumerate(functions)}
    names = re.findall(r"^  \['[^\n]+', (\w+)\],$", source.split("const STEPS = [", 1)[1].split("];", 1)[0], re.M)
    assert len(names) == 15
    helpers = ["serve", "resumeMeridian", "arrive", "portalAlone", "portalContracts", "cleanUp", "main"]
    covered = set(names + helpers + ["semanticOsi", "semanticMarkdown", "run"])
    for name, body in bodies.items():
        if re.search(r"""\b(?:kge|refused)\(['"]|\bserve\(\[|\brun\(['"]kge['"]|spawnSync\(KGE, \[""", body):
            assert name in covered, f"commands in {name} need a README step"
    for step, name in [*enumerate(names, 1), *((0, name) for name in helpers)]:
        body = bodies[name]
        if step == 14:
            body = bodies["semanticOsi"] + bodies["semanticMarkdown"]
        for kind, args in js_calls(body):
            if kind == "spawnSync":
                if args[0] != "KGE":
                    continue
                words = js_words(split_js(args[1][1:-1]), source)
            elif kind == "run":
                if args[0] != "'kge'":
                    continue
                words = js_words(split_js(args[1][1:-1]), source)
                if len(args) > 2:
                    words += ["MODEL_PROVIDER=" + re.search(r"MODEL_PROVIDER: '([^']+)'", args[2]).group(1)]
            elif kind == "serve":
                if not args[0].startswith("["):
                    continue
                words = js_words(split_js(args[0][1:-1]), source)
            elif args == ["...args"]:
                assert step == 5
                loop = re.search(r"for \(const args of (\[\[.*?\]\])\)", body).group(1)
                for array in split_js(loop[1:-1]):
                    yield step, js_words(split_js(array[1:-1]), source)
                continue
            else:
                words = js_words(args, source)
            if any("${unit}" in expression for expression in args):
                units = re.search(r"for \(const unit of \[(.*?)\]\)", body).group(1)
                for unit in js_words(split_js(units), source):
                    yield step, [word.replace("UNIT=<value>", f"UNIT={unit}") for word in words]
            else:
                yield step, words


def assert_documented_commands(source, readme_section):
    numbered = re.split(r"^\d+\. \*\*", readme_section, flags=re.M)[1:]
    shared = readme_section.split("1. **", 1)[0] + readme_section.split("The runner also checks", 1)[1]
    commands = list(walkthrough_commands(source))
    assert len(commands) >= 65
    for step, actual in commands:
        text = numbered[step - 1] if step else shared
        documented = [command_words(span) for span in re.findall(r"`([^`]+)`", text) if "kge " in span]
        documented = [[word for word in words if word != "kge"] for words in documented]
        if "the same for `UNIT=cellar`" in text:
            documented += [[word.replace("UNIT=taproom", "UNIT=cellar") for word in words] for words in documented]
        if "same two commands with `MODEL_PROVIDER=none`" in text:
            documented += [[word.replace("MODEL_PROVIDER=mock", "MODEL_PROVIDER=none") for word in words] for words in documented]
        if "add `HOST=<host>`" in text:
            documented += [words + ["HOST=<value>"] for words in documented]
        assert any(command_matches(actual, words) for words in documented), (step, actual, documented)


def test_the_walkthrough_has_no_make_process_and_pins_every_model_tier():
    source = SCRIPT.read_text()
    assert not re.search(r"(?:spawn(?:Sync)?|run)\(['\"]make['\"]", source)
    assert "const kge = (...args) => run('kge', args)" in source
    for name in ("MODEL_PROVIDER", "MODEL_HIGH_PROVIDER", "MODEL_MEDIUM_PROVIDER", "MODEL_LOW_PROVIDER"):
        assert f"{name}: 'none'" in source.split("function env(extra)", 1)[1].split("let logs", 1)[0]
        markdown = source.split("async function semanticMarkdown()", 1)[1].split("async function industryStandards", 1)[0]
        mock_calls = re.findall(r"run\('kge', [^\n]+, \{ (MODEL_PROVIDER: 'mock'[^}]+) \}\)", markdown)
        assert len(mock_calls) == 2 and all(f"{name}: 'mock'" in call for call in mock_calls)


def test_every_walkthrough_command_and_its_arguments_are_named_for_that_readme_step():
    assert_documented_commands(SCRIPT.read_text(), section())


def test_command_guard_rejects_changed_arguments_reordered_actions_and_wrong_steps():
    import pytest
    source = SCRIPT.read_text()
    mutations = [source.replace("kge('compile', 'load')", "kge('load', 'compile')", 1),
                 source.replace("'CSV=examples/brackenridge/data'", "'CSV=examples/leasing/data'", 1),
                 source.replace("PORT : 8001", "PORT : 9001", 1),
                 source.replace("['taproom', 'cellar']", "['taproom', 'other']", 1),
                 source.replace("[['sign-off', 'ROLE=steward_glossary']", "[['sign-off', 'ROLE=other']", 1),
                 source.replace("async function wealthQuestions() {", "async function wealthQuestions() { kge('compile');", 1)]
    for changed in mutations:
        assert changed != source
        with pytest.raises(AssertionError):
            assert_documented_commands(changed, section())


def test_every_kge_command_in_running_it_exists_without_make():
    from kge.cli import COMMANDS
    running = (DEMO / "README.md").read_text().split("\n## Running it\n", 1)[1].split("\n### Tests", 1)[0]
    named = re.findall(r"\bkge ([a-z][a-z-]*)", running)
    assert named and set(named) <= set(COMMANDS)
    for line in running.splitlines():
        if re.search(r"\bmake ", line):
            assert "contributor shortcut:" in line and "kge clean" in line
