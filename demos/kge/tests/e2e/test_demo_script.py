"""The README's Demo script and the run that drives it, tests/e2e/demo_script.cjs (make demo-script), say the same things.

The run is Playwright for Node in Chromium: it builds, serves, rebuilds and restarts the demo as a presenter does, so it is
not part of make test. These tests read the files only: the Makefile's target, the README's pointer to it, and that every
figure and every question or button the Demo script names is one the run checks or clicks, and no other figure, so the
README and the run cannot drift apart. With Node installed, the script must also parse.
"""

import re
import shutil
import subprocess
import shutil as file_shutil
from types import SimpleNamespace
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2]
SCRIPT = DEMO / "tests" / "e2e" / "demo_script.cjs"
FIGURE = re.compile(r"(?<![\d.])(?<!\d,)\d{1,3}(?:,\d{3})*\.\d\d(?!\d)(?!\.\d)")


def section():
    readme = (DEMO / "README.md").read_text()
    return readme.split("\n## Demo script\n", 1)[1].split("\n## ", 1)[0]


def steps():
    return re.findall(r"^\d+\. \*\*([^*]+)\*\*", section(), re.M)


def test_make_demo_script_runs_the_playwright_script_and_make_test_does_not():
    make = (DEMO / "Makefile").read_text()
    assert re.search(r"^\.PHONY:.*\bdemo-script\b", make, re.M)
    recipe = make.split("\ndemo-script:", 1)[1].split("\n\n", 1)[0]
    assert "node tests/e2e/demo_script.cjs" in recipe
    assert "PORT2" in recipe and "playwright@$(PLAYWRIGHT)" in recipe
    assert re.search(r"^PLAYWRIGHT \?= \d+\.\d+\.\d+$", make, re.M), "an exact version"
    assert not SCRIPT.name.startswith("test_") and SCRIPT.suffix == ".cjs", "pytest never collects it"


def test_make_screenshots_writes_the_quick_start_views():
    make = (DEMO / "Makefile").read_text()
    assert re.search(r"^screenshots:\n\t\$\(MAKE\) demo-script SCREENSHOTS=1$", make, re.M)
    src = SCRIPT.read_text()
    assert "setViewportSize({ width: 1280" in src
    assert {"sign-in", "ask", "map", "onboard"} == set(re.findall(r"quickstartShot\([^,]+, '([^']+)'", src))


def test_the_demo_script_section_names_its_run():
    s = section()
    assert "`make demo-script`" in s and "tests/e2e/demo_script.cjs" in s
    readme = (DEMO / "README.md").read_text()
    running = readme.split("\n## Running it\n", 1)[1].split("\n## ", 1)[0]
    assert "make demo-script" in running


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
        pytest.skip("no Node: make demo-script needs it, make test does not")
    r = subprocess.run([node, "--check", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_onboarding_cleanup_uses_the_unit_reset_target():
    src = SCRIPT.read_text()
    cleanup = src.split("async function cleanUp()", 1)[1].split("async function main()", 1)[0]
    assert "make('reset-onboard', 'UNIT=leasing')" in cleanup
    assert "make('reset-onboard', 'UNIT=bancassurance')" in cleanup
    assert "rmSync(state.onboarding" not in cleanup and "writeFileSync(state.onboarding" not in cleanup


def test_step_13_uses_the_osi_yaml_example():
    src = SCRIPT.read_text()
    step = src.split("async function semanticBusinessUnit()", 1)[1].split("const STEPS", 1)[0]
    assert "SEMANTIC=examples/bancassurance-osi" in step
    assert "SEMANTIC=examples/bancassurance'" not in step


def test_the_script_resets_a_crashed_runs_unit_before_step_one():
    src = SCRIPT.read_text()
    start = src.split("async function main()", 1)[1].split("for (const [name, step] of STEPS)", 1)[0]
    assert "make('reset-onboard', 'UNIT=leasing')" in start
    assert "make('reset-onboard', 'UNIT=bancassurance')" in start


def test_the_readme_s_onboarding_figure_is_the_one_draft_computes(tmp_path):
    from kge.estate import onboard
    clients = tmp_path / "clients"
    file_shutil.copytree(DEMO / "clients" / "meridian", clients / "meridian", ignore=file_shutil.ignore_patterns("units"))
    prof = SimpleNamespace(clients=clients, client="meridian", warehouses=tmp_path / "warehouses",
                           identities=DEMO / "deploy" / "meridian" / "identities.yaml",
                           run_dir=lambda: tmp_path / "run")
    ddl = (DEMO / "examples" / "leasing" / "schema.sql").read_text()
    csv = {p.name: p.read_bytes() for p in (DEMO / "examples/leasing/data").glob("*.csv")}
    onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=csv, ddl_text=ddl, as_="knowledge_engineer")
    drafted = onboard.draft(prof, "leasing", as_="knowledge_engineer", on="2026-10-17")
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
    prof = SimpleNamespace(clients=clients, client="meridian", warehouses=tmp_path / "warehouses",
                           identities=DEMO / "deploy" / "meridian" / "identities.yaml",
                           run_dir=lambda: tmp_path / "run", model_hash=lambda: None)
    onboard.create(prof, "bancassurance", label="Bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi",
                   as_="knowledge_engineer")
    onboard.draft(prof, "bancassurance", as_="knowledge_engineer", on="2026-10-17")
    unit_history.record(prof, "release", "steward_glossary", {"model_hash": "a" * 64}, ["bancassurance"])
    onboard.create(prof, "bancassurance", label="Bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi-v2",
                   as_="knowledge_engineer")
    drafted = onboard.draft(prof, "bancassurance", as_="knowledge_engineer", on="2026-10-18")
    figures = {i["id"]: i["expect"]["figure"] for i in drafted["items"] if i["kind"] == "question"}
    changes = drafted["changes"]
    s = section()
    assert f"{len(changes['changed'])} changed, {len(changes['added'])} added, {len(changes['removed'])} removed" in s
    assert f"{float(figures['BANCASSURANCE-D01']):,.2f}" == "1,650.00" and "1,650.00" in s
    assert f"{float(figures['BANCASSURANCE-D03']):,.2f}" == "245.05" and "245.05" in s
    assert "SEMANTIC=examples/bancassurance-osi-v2" in s and "SEMANTIC=examples/bancassurance-osi-v2" in SCRIPT.read_text()


def test_onboarding_walkthrough_uses_unit_cards_and_the_account_menu():
    src = SCRIPT.read_text()
    assert "page.locator('#account-menu').evaluate" in src
    assert "document.querySelector('#onboard').dataset.drawn" in src
    for selector in ("#rows-mode-leasing", "#rows-csv-leasing", "#ob-proposal-card", "#next-step", "#onboard-count"):
        assert selector in src
    assert "unitCard.locator('.more')" in src and "'Signed off'" in src
    assert "No map for a refused answer" in src and "needs a unit you may not read" in src
    assert "#ob-items" not in src and "Read DDL" not in src
    assert "page.locator('#ob-proposal-card').isHidden()" in src
    improvement = src.split("async function improveJob() {", 1)[1].split("  const versions = ", 1)[0]
    for label, endpoint in (("Accept all", "accept"), ("Certify all", "certify"), ("Run checks", "run"), ("Sign off", "run"), ("Release", "run")):
        assert f"onboardClick(page, '{label}', '/onboard/{endpoint}', null, '#ob-proposal-card')" in improvement
