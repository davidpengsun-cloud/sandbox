"""The pack check reports onboarding's input findings with a file and a line, and builds no graph."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from html.parser import HTMLParser

import pytest
import yaml

from kge.estate import check_pack, upfront
from kge.pipeline.profile import Profile
from kge.pipeline.settings import ROOT

EXAMPLE = ROOT / "skills/author-pack/examples"
PACK = EXAMPLE / "clients/meridian"
SOURCES = EXAMPLE / "sources"


class Sheets(HTMLParser):
    """The workbook's tables, keyed by the heading above each one."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = ""
        self.tables = {}
        self._heading = False
        self._rows = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self._heading = True
            self.heading = ""
        elif tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "h2":
            self._heading = False
        elif tag in {"td", "th"} and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables[self.heading.strip()] = _records(self._rows)
            self._rows = None

    def handle_data(self, data):
        if self._heading:
            self.heading += data
        elif self._cell is not None:
            self._cell.append(data)


def _records(rows):
    header, *body = rows
    return [dict(zip(header, row)) for row in body]


def _sheets():
    parser = Sheets()
    parser.feed((SOURCES / "catalogue.html").read_text())
    return parser.tables


def _profile(tmp_path, folder):
    prof = Profile("meridian", "meridian", folder.parent, tmp_path / "artifacts", tmp_path / "stores",
                   tmp_path / "landing", tmp_path / "warehouses", tmp_path / "identities.yaml", None,
                   tmp_path / "profiles.yaml")
    for path in (prof.artifacts, prof.store, prof.landing, prof.warehouses):
        path.mkdir(parents=True)
    return prof


def _copy(tmp_path):
    folder = tmp_path / "clients/meridian"
    shutil.copytree(PACK, folder)
    return folder


def _identity(findings):
    return [(finding.rule, finding.path, finding.message) for finding in findings]


def test_author_pack_documents_each_source_and_the_check():
    text = (ROOT / "skills/author-pack/SKILL.md").read_text()
    assert text.startswith("---\n")
    meta = yaml.safe_load(text.split("---", 2)[1])
    assert meta["name"] == "author-pack"
    for marker in ("catalogue.html", "schema.sql", "policy.md", "glossary.md", "questions-v1.json",
                   "changes_from", "check-pack", "unmapped.md", "issue 331", "APM-PLACEHOLDER", "placeholder"):
        assert marker in text, marker
    assert "Do not guess" in text
    assert "does not compile, load or release a graph" in text
    assert "kge check-pack PACK=<client-folder>" in text
    assert "check-pack PACK=<client-folder>" in text
    assert "set only in the environment is not read" in text
    assert "PACK=<client-folder> kge check-pack" not in text
    lowered = text.lower()
    for action in ("accept", "certify", "sign-off", "release"):
        assert f"does not run onboarding, {action}" in lowered or "does not run onboarding" in lowered
    assert "sign-off" in text and "does not run onboarding" in text
    assert "kge.graph" not in (ROOT / "kge/estate/check_pack.py").read_text()


def test_worked_sources_map_without_inventing_facts():
    sheets = _sheets()
    described = [row for row in sheets["Columns"] if row["description"]]
    blank = [row for row in sheets["Columns"] if not row["description"]]
    schema = yaml.safe_load((PACK / "fixture/schema.yaml").read_text())
    columns = schema["warehouses"]["teller"]["tables"][0]["columns"]
    assert [column["name"] for column in columns] == [row["column"] for row in described]
    assert blank == [row for row in sheets["Columns"] if row["column"] == "branch_code"]
    assert "branch_code" not in (PACK / "fixture/schema.yaml").read_text()
    catalogue = yaml.safe_load((PACK / "catalogue.yaml").read_text())
    product = catalogue["products"][0]
    assert product["id"] == "teller.ledger"
    assert product["description"] == sheets["Tables"][0]["description"]
    assert [field["name"] for field in product["fields"]] == [row["column"] for row in described]
    entry, amount = product["fields"][:2]
    assert "fail_percent" not in entry["quality"]
    assert amount["quality"]["fail_percent"] == 0
    notes = {row["key"]: row["value"] for row in sheets["Notes"]}
    assert product["product_owner"] == notes["product_owner"]
    assert product["product_owner_email"] == ""
    assert product["APM_ID"] == "APM-PLACEHOLDER"
    assert product["path"] == "placeholder" and product["schema"] == "placeholder"
    catalogue_text = (PACK / "catalogue.yaml").read_text()
    assert "teller.rates_api" in catalogue_text and "product_type: API/MCP" in catalogue_text
    unmapped = (EXAMPLE / "unmapped.md").read_text()
    for phrase in ("branch_code", "fail percentage", "no email", "APM-PLACEHOLDER", "Float", "scan.docx",
                   "was not written", "mappings", "access.purposes", "Rates API states no description"):
        assert phrase in unmapped, phrase
    assert "issue 331" not in unmapped
    pack_text = "\n".join(path.read_text() for path in PACK.rglob("*") if path.is_file())
    assert "Float" not in pack_text
    assert "branch_code" not in pack_text
    assert "scan.docx" not in pack_text
    assert "http://" not in pack_text and "https://" not in pack_text and "gs://" not in pack_text
    pack = yaml.safe_load((PACK / "pack.yaml").read_text())
    assert pack["glossary"] == []
    assert pack["mappings"] == []
    assert pack["access"]["purposes"] == []
    assert "certified" not in (PACK / "pack.yaml").read_text()
    assert "ledger-entry" not in (PACK / "pack.yaml").read_text()
    assert (PACK / "fixture/heldout.json").read_bytes() == (SOURCES / "heldout.json").read_bytes()
    assert (PACK / "fixture/docs/certified/rates.csv").read_bytes() == (SOURCES / "documents/rates.csv").read_bytes()
    golden = json.loads((PACK / "fixture/golden.json").read_text())
    assert golden["changes_from"]["sha256"] == "a" * 64
    assert golden["changes_from"]["items"] == ["T02"]
    assert golden["changes_from"]["why"] == "The person restated the second question."
    assert list((PACK / "fixture/docs").rglob("scan.pdf")) == []


def test_the_worked_rates_api_is_a_portal_only_card(tmp_path):
    """The worked Rates API is a portal-only card, and a catalogue without that card fails.

    The Assets row states the name, the kind and the unit. It states no description, so the card has none.
    """
    from kge.compiler import catalogue
    from kge.compiler.pack import read
    assert hasattr(catalogue, "PORTAL_TYPES")
    worked = yaml.safe_load((PACK / "catalogue.yaml").read_text())
    schema = yaml.safe_load((PACK / "fixture/schema.yaml").read_text())
    tables = {unit: [table["name"] for table in house["tables"]] for unit, house in schema["warehouses"].items()}
    cards = [product for product in worked["products"] if product.get("table") not in tables.get(product.get("unit"), [])]
    assert [card["id"] for card in cards] == ["teller.rates_api"], "the worked catalogue has no portal-only card"
    card = cards[0]
    assert card["name"] == "Rates API" and card["unit"] == "teller" and card["table"] == "rates_api"
    assert catalogue.vocabulary_value("product_type", card.get("product_type")) == "API/MCP"
    assert card["product_type"] in catalogue.PORTAL_TYPES
    assert "description" not in card
    assert not {"lineage", "fields", "quality_score", "documents"} & set(card)
    folder = _copy(tmp_path)
    pack = read(folder.parent, "meridian", lock=False)
    pack.lock = {"sources": {unit: {"tables": house["tables"]} for unit, house in schema["warehouses"].items()}}
    data = yaml.safe_load((folder / "catalogue.yaml").read_text())
    findings = [finding for finding in catalogue.validate(data, pack) if finding.rule == "C01" and "unknown table" in finding.message]
    assert findings == []
    prof = _profile(tmp_path, folder)
    assert [finding for finding in check_pack.report(prof) if "unknown table" in finding.message] == []


def test_worked_pack_passes_the_onboard_input_check(tmp_path, monkeypatch):
    folder = _copy(tmp_path)
    before = {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    calls = []
    monkeypatch.setattr(upfront.graphstore, "store_info", lambda: calls.append("store") or {"engine": "here"})
    prof = _profile(tmp_path, folder)
    reported = check_pack.report(prof)
    onboard = upfront.check(prof)
    assert reported == []
    assert onboard.findings == []
    assert calls == []
    assert not list(tmp_path.rglob("graph.ladybug"))
    assert not list(tmp_path.rglob("graph.bigquery"))
    assert before == {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    done = subprocess.run([sys.executable, "-m", "kge.estate.check_pack", "--pack", str(folder)],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "check-pack: no findings" in done.stdout
    cli = subprocess.run([sys.executable, "-m", "kge.cli", "check-pack", f"PACK={folder}"],
                         cwd=ROOT, capture_output=True, text=True)
    assert cli.returncode == 0, cli.stderr + cli.stdout
    assert "check-pack: no findings" in cli.stdout


def test_planted_inputs_match_onboarding_and_name_file_and_line(tmp_path, monkeypatch):
    folder = _copy(tmp_path)
    monkeypatch.setattr(upfront.graphstore, "store_info", lambda: {"engine": "here"})
    schema = folder / "fixture/schema.yaml"
    schema.write_text(schema.read_text().replace("description: Amount in minor units.", "description:"))
    rows = folder / "fixture/data/teller/ledger.csv"
    rows.write_text(rows.read_text().replace("1,100,2025-01-01,9999-12-31,true\n", "1,100,2025-01-01,9999-12-31,true,extra\n"))
    (folder / "fixture/docs/certified/policy.md").write_text("no front matter\n")
    catalogue = folder / "catalogue.yaml"
    catalogue.write_text(catalogue.read_text().replace("classification: Internal", "classification: PLANTED-SECRET", 1))
    golden = json.loads((folder / "fixture/golden.json").read_text())
    golden["items"][1]["question"] = ""
    (folder / "fixture/golden.json").write_text(json.dumps(golden, indent=2) + "\n")
    prof = _profile(tmp_path, folder)
    reported = check_pack.report(prof)
    assert _identity(reported) == _identity(upfront.check(prof).findings)
    rules = {finding.rule for finding in reported}
    assert {"F02", "F05", "F07", "F08", "F09"} <= rules
    for finding in reported:
        if not finding.line:
            continue
        lines = (folder / finding.file).read_text().splitlines()
        assert 1 <= finding.line <= len(lines)
    secret = next(finding for finding in reported if finding.rule == "F02" and "PLANTED-SECRET" in (folder / finding.file).read_text().splitlines()[finding.line - 1])
    assert "PLANTED-SECRET" in (folder / secret.file).read_text().splitlines()[secret.line - 1]
    extra = next(finding for finding in reported if finding.rule == "F07")
    assert (folder / extra.file).read_text().splitlines()[extra.line - 1].endswith(",extra")
    assert not list(tmp_path.rglob("graph.ladybug"))
    done = subprocess.run([sys.executable, "-m", "kge.estate.check_pack", "--pack", str(folder)],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 2
    assert "F02 catalogue.yaml:" in done.stdout
    assert "must be one of Public, Internal, Confidential, Highly Confidential, Restricted" in done.stdout
    assert "graph" not in done.stdout.lower()


def test_real_onboard_passes_its_input_check(tmp_path):
    """`kge onboard` is the local workflow. Its check step is the input check, and it must not refuse this pack."""
    _copy(tmp_path)
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "profiles.yaml").write_text(
        "default: author\nprofiles:\n  author:\n    client: meridian\n"
        "    clients: ../clients\n    artifacts: ../artifacts\n    store: ../stores\n"
        "    landing: ../landing\n    warehouses: ../estate/warehouses\n    baseline: null\n")
    for name in ("artifacts", "stores", "landing", "estate/warehouses"):
        (tmp_path / name).mkdir(parents=True)
    env = os.environ.copy()
    env.update(PROFILE="author", PROFILES=str(registry / "profiles.yaml"), MODEL_PROVIDER="none", TAGGER_PROVIDER="rules")
    env.pop("STORE_URL", None)
    env.pop("LANDING_URL", None)
    done = subprocess.run([sys.executable, "-m", "kge.cli", "onboard"], cwd=ROOT, env=env, capture_output=True, text=True)
    output = done.stdout + done.stderr
    assert "onboard: [3/10] check" in output
    assert "onboard: refused" not in output
    assert "onboard: [4/10] fixture" in output
    assert "onboard: [10/10] margin" in output
    record = json.loads((tmp_path / "stores/meridian/run/onboard-run.json").read_text())
    assert record["findings"] == []
    assert record["outcome"] == "not checked"
    assert done.returncode == 2
    assert "nothing to check against" in output


def _bad_pack(tmp_path):
    folder = tmp_path / "bad"
    shutil.copytree(PACK, folder)
    schema = folder / "fixture/schema.yaml"
    schema.write_text(schema.read_text().replace("description: Amount in minor units.", "description:"))
    return folder


def _refused_empty(done):
    output = done.stdout + done.stderr
    assert done.returncode == 2, output
    assert "PACK is empty" in output
    assert "no findings" not in done.stdout


def test_an_exported_pack_is_ignored_and_a_blank_pack_is_refused(tmp_path):
    """The image, the command, the helper and the plugin treat PACK the same way.

    An exported PACK is not a pack argument. A blank PACK= is refused, rather than the profile or the current directory.
    """
    folder = _bad_pack(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["PACK"] = str(folder)
    env["PROFILES"] = str(tmp_path / "no-registry.yaml")
    env["KGE_STATE"] = str(tmp_path / "state")
    env.pop("PROFILE", None)
    env.pop("STORE_URL", None)
    for command in (
        ["sh", str(ROOT / "entrypoint.sh"), "check-pack"],
        [sys.executable, "-m", "kge.cli", "check-pack"],
        ["bash", str(ROOT / "kit/scripts/kge"), "check-pack"],
        ["sh", str(ROOT / "kit/plugin/bin/check-pack")],
    ):
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        output = done.stdout + done.stderr
        assert done.returncode == 2, (command[0], output)
        assert "F05" not in done.stdout, command
        assert "no findings" not in done.stdout, command
        assert "no profiles registry" in output, command
    for command in (
        ["sh", str(ROOT / "entrypoint.sh"), "check-pack", "PACK="],
        [sys.executable, "-m", "kge.cli", "check-pack", "PACK="],
        ["bash", str(ROOT / "kit/scripts/kge"), "check-pack", "PACK="],
        ["sh", str(ROOT / "kit/plugin/bin/check-pack"), "PACK="],
        [sys.executable, "-m", "kge.estate.check_pack", "--pack", ""],
        [sys.executable, "-m", "kge.estate.check_pack", "PACK=   "],
    ):
        _refused_empty(subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True))


def test_entrypoint_forwards_the_named_pack_and_a_bad_pack_is_refused(tmp_path):
    folder = _bad_pack(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env['PATH']}"
    env["PROFILES"] = str(tmp_path / "no-registry.yaml")
    env.pop("PROFILE", None)
    env.pop("STORE_URL", None)
    done = subprocess.run(["sh", str(ROOT / "entrypoint.sh"), "check-pack", f"PACK={folder}"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "no findings" not in done.stdout
    assert "no profiles registry" not in done.stdout + done.stderr
    assert "F05" in done.stdout


def test_helper_rejects_an_unknown_argument_and_checks_a_named_bad_pack(tmp_path):
    env = os.environ.copy()
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["KGE_STATE"] = str(tmp_path / "state")
    helper = str(ROOT / "kit/scripts/kge")
    unknown = subprocess.run(["bash", helper, "check-pack", "NOT_A_SETTING"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert unknown.returncode == 2
    assert "unknown argument" in unknown.stderr
    folder = _bad_pack(tmp_path)
    checked = subprocess.run(["bash", helper, "check-pack", f"PACK={folder}"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert "F05" in checked.stdout
    assert "no findings" not in checked.stdout


def _bash_3_2() -> str | None:
    """A Bash 3.2 binary on this machine, or none.

    macOS ships Bash 3.2 at ``/bin/bash``. CI's bash is newer. A path that is not 3.2 is not used,
    and a missing binary is a skip, not a failure.
    """
    candidates = ["/bin/bash", "/usr/bin/bash"]
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen or not os.path.isfile(candidate):
            continue
        seen.add(candidate)
        version = subprocess.run([candidate, "-c", "echo ${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"],
                                 capture_output=True, text=True)
        if version.returncode == 0 and version.stdout.strip() == "3.2":
            return candidate
    return None


def test_check_pack_argument_parsing_is_safe_on_bash_3_2(tmp_path):
    """Bash 3.2 runs the helper's check-pack, rejects an unknown argument, and checks the named pack.

    The helper's other commands still refuse Bash before 4.4. The empty-array guard is what lets a
    check with no extra argument reach Python instead of aborting under set -u.
    """
    bash32 = _bash_3_2()
    if bash32 is None:
        pytest.skip("no Bash 3.2 on this machine; macOS /bin/bash is 3.2 and this check runs there")
    version = subprocess.run([bash32, "-c", "echo ${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"], capture_output=True, text=True)
    assert version.stdout.strip() == "3.2"
    empty = subprocess.run([bash32, "-c", "set -u; args=(); if [[ ${#args[@]} -gt 0 ]]; then echo has; else echo empty; fi"],
                           capture_output=True, text=True)
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout.strip() == "empty"
    env = os.environ.copy()
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["KGE_STATE"] = str(tmp_path / "state")
    helper = str(ROOT / "kit/scripts/kge")
    other = subprocess.run([bash32, helper, "serve"], env=env, capture_output=True, text=True)
    assert other.returncode == 2
    assert "Bash 4.4" in other.stderr
    bare = subprocess.run([bash32, helper, "check-pack"], env=env, capture_output=True, text=True)
    assert "unbound" not in (bare.stderr + bare.stdout).lower()
    assert "Bash 4.4" not in bare.stderr
    unknown = subprocess.run([bash32, helper, "check-pack", "NOT_A_SETTING"], env=env, capture_output=True, text=True)
    assert unknown.returncode == 2
    assert "unknown argument" in unknown.stderr
    assert "unbound" not in unknown.stderr.lower()
    folder = _bad_pack(tmp_path)
    checked = subprocess.run([bash32, helper, "check-pack", f"PACK={folder}"], env=env, capture_output=True, text=True)
    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert "F05" in checked.stdout
    assert "no findings" not in checked.stdout
    assert "unbound" not in checked.stderr.lower()
    plugin = ROOT / "kit/plugin/bin/check-pack"
    via_plugin = subprocess.run([bash32, str(plugin), f"PACK={folder}"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert via_plugin.returncode == 2, via_plugin.stdout + via_plugin.stderr
    assert "F05" in via_plugin.stdout


def test_the_plugin_checks_the_named_pack_and_an_exported_pack_is_not_read(tmp_path):
    folder = _bad_pack(tmp_path)
    plugin = ROOT / "kit/plugin/bin/check-pack"
    env = os.environ.copy()
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["PACK"] = str(folder)
    env["PROFILES"] = str(tmp_path / "no-registry.yaml")
    env.pop("PROFILE", None)
    env.pop("STORE_URL", None)
    named = subprocess.run(["sh", str(plugin), f"PACK={folder}"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert named.returncode == 2, named.stdout + named.stderr
    assert "F05" in named.stdout
    assert "no findings" not in named.stdout
    assert "no profiles registry" not in named.stdout + named.stderr
    exported = subprocess.run(["sh", str(plugin)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert exported.returncode == 2, exported.stdout + exported.stderr
    assert "F05" not in exported.stdout
    assert "no findings" not in exported.stdout
    assert "no profiles registry" in exported.stdout
    cli = subprocess.run([sys.executable, "-m", "kge.cli", "check-pack"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert cli.returncode == 2, cli.stdout + cli.stderr
    assert "F05" not in cli.stdout
    assert "no profiles registry" in cli.stdout + cli.stderr


def test_helper_check_pack_uses_the_same_command(tmp_path):
    env = os.environ.copy()
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["KGE_STATE"] = str(tmp_path / "state")
    done = subprocess.run(["bash", str(ROOT / "kit/scripts/kge"), "check-pack", f"PACK={PACK}"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "check-pack: no findings" in done.stdout


def test_author_runtime_proposes_mappings_from_catalog_and_check_pack_validates_runtimes(tmp_path):
    from kge.estate import author_runtime

    catalog = json.loads((SOURCES / "runtime-catalog.json").read_text())
    ref = author_runtime._reference_tables(PACK)
    unconfirmed = author_runtime.draft_from_catalog(catalog, ref, confirmed=False)
    assert unconfirmed.entry["connection"]["location"] == "placeholder"
    assert unconfirmed.placeholders
    assert "tables" not in unconfirmed.entry
    assert [(p.kind, p.reference, p.target, p.target_type) for p in unconfirmed.proposals] == [
        ("table", "teller.ledger", "teller_ledger_mart", None),
        ("column", "teller.ledger.entry_id", "entry_id", "INT64"),
        ("column", "teller.ledger.amount", "amount_minor", "INT64"),
    ]
    assert unconfirmed.extra_tables == ["teller_audit_log"]
    assert unconfirmed.extra_columns == ["teller_ledger_mart.etl_loaded_at"]

    confirmed = author_runtime.draft_from_catalog(catalog, ref, confirmed=True)
    assert confirmed.entry["tables"] == {
        "teller.ledger": {
            "name": "teller_ledger_mart",
            "columns": {
                "entry_id": {"type": "INT64"},
                "amount": {"name": "amount_minor", "type": "INT64"},
            },
        }
    }

    sim_catalog = {
        "id": "pg_replica",
        "label": "Postgres Replica",
        "location": "gcp-live-proj-123:dataset_prod",
        "tables": [
            {
                "unit": "teller",
                "name": "ledger_tbl",
                "columns": [
                    {"name": "entry_id", "type": "INTEGER"},
                    {"name": "amount", "type": "INTEGER"},
                    {"name": "amount_minor", "type": "INTEGER", "derived_from": "teller.ledger.amount"},
                ],
            }
        ],
    }
    sim_draft = author_runtime.draft_from_catalog(
        sim_catalog,
        {**ref, "teller.missing_ref": {"id": "INTEGER"}},
        confirmed=True,
    )
    assert sim_draft.entry["connection"]["dialect"] == "placeholder"
    assert sim_draft.entry["connection"]["location"] == "placeholder"
    assert any("connection.dialect" in p for p in sim_draft.placeholders)
    assert sim_draft.absent == ["teller.missing_ref"]
    assert sim_draft.entry["tables"] == {
        "teller.ledger": {
            "name": "ledger_tbl",
            "columns": {"amount": "amount_minor"},
        }
    }

    from kge.graph import schema as graph_schema

    meta_ver = graph_schema.meta_model()["version"]
    meta_short = ".".join(meta_ver.split(".")[:2])
    folder = _copy(tmp_path)
    pack_data = yaml.safe_load((folder / "pack.yaml").read_text())
    pack_data["manifest"]["meta_model_version"] = meta_ver
    pack_data["manifest"]["platform"]["meta_model"] = f"^{meta_short}"
    pack_data["schema"]["meta_model"] = meta_short
    pack_data["runtimes"] = [confirmed.entry]
    pack_data["default_runtimes"] = ["bq_teller", "reference"]
    (folder / "pack.yaml").write_text(yaml.safe_dump(pack_data, sort_keys=False))
    _, reported = check_pack.check_folder(folder)
    assert reported == []

    pack_data["runtimes"][0]["tables"]["teller.no_such_table"] = "bad_tbl"
    (folder / "pack.yaml").write_text(yaml.safe_dump(pack_data, sort_keys=False))
    _, bad_reported = check_pack.check_folder(folder)
    assert any(r.rule == "S31" and "teller.no_such_table" in r.message for r in bad_reported)


def test_worked_version_policy_names_the_source_validity_columns():
    pack = yaml.safe_load((PACK / 'pack.yaml').read_text())
    policy, = pack['policies']['scd2']
    assert policy == {'id': 'scd2-ledger', 'table': 'teller.ledger', 'valid_from': 'valid_from',
                      'valid_to': 'valid_to', 'is_current': 'is_current', 'valid_to_inclusive': True}
    columns = {c['name']: c['type'] for c in yaml.safe_load((PACK / 'fixture/schema.yaml').read_text())
               ['warehouses']['teller']['tables'][0]['columns']}
    assert columns[policy['valid_from']] == columns[policy['valid_to']] == 'DATE'
    assert columns[policy['is_current']] == 'BOOLEAN'
    source = (SOURCES / 'schema.sql').read_text()
    for name in ('valid_from DATE', 'valid_to DATE', 'is_current BOOLEAN'):
        assert name in source
    assert 'A versioned table needs a `policies.scd2` entry' in (ROOT / 'skills/author-pack/SKILL.md').read_text()
