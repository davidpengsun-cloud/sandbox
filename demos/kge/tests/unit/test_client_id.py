"""One client-id rule at every boundary that accepts one."""
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from kge.adapters.objstore import open_scratch
from kge.compiler import checks, compile as compiler
from kge.compiler.pack import Pack
from kge.estate import onboard
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE, LandingError, Zone
from kge.pipeline import objstore
from kge.pipeline.objstore import Refused
from kge.pipeline.overlay import Journal, OverlayError
from kge.pipeline.profile import ProfileError, read

DEMO = Path(__file__).resolve().parents[2]
ROOT = DEMO.parents[1]
GOOD = "a" + "b" * 15
LONG = "a" + "b" * 16


def registry(path: Path, client: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"default": "one", "profiles": {"one": {
        "client": client, "clients": "../clients", "artifacts": "../artifacts", "store": "../store",
        "landing": "../landing", "warehouses": "../warehouses", "identities": "identities.yaml",
        "baseline": None}}}, sort_keys=False))


def setup_client(monkeypatch, tmp_path: Path, client: str) -> None:
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    monkeypatch.setenv("CLIENT", client)
    monkeypatch.setenv("PEOPLE", "analyst:analyst")
    objstore.setup_client()


def assert_rule(exc: BaseException) -> None:
    assert CLIENT_ID_RULE in str(exc)


def test_sixteen_is_accepted_and_seventeen_is_refused_at_every_python_boundary(monkeypatch, tmp_path, capsys):
    setup_client(monkeypatch, tmp_path / "setup-good", GOOD)
    with pytest.raises(Refused) as refused:
        setup_client(monkeypatch, tmp_path / "setup-long", LONG)
    assert_rule(refused.value)

    monkeypatch.setattr(onboard.clients, "ROOT", tmp_path / "missing-root")
    with pytest.raises(FileNotFoundError):
        onboard.client(GOOD, "Good", as_="platform")
    with pytest.raises(onboard.OnboardError) as refused:
        onboard.client(LONG, "Long", as_="platform")
    assert_rule(refused.value)

    good_registry = tmp_path / "registry-good/deploy/profiles.yaml"
    registry(good_registry, GOOD)
    assert read(good_registry)[1]["one"].client == GOOD
    long_registry = tmp_path / "registry-long/deploy/profiles.yaml"
    registry(long_registry, LONG)
    with pytest.raises(ProfileError) as refused:
        read(long_registry)
    assert_rule(refused.value)

    assert Zone(tmp_path / "landing", GOOD).client == GOOD
    with pytest.raises(LandingError) as refused:
        Zone(tmp_path / "landing", LONG)
    assert_rule(refused.value)

    assert Journal(tmp_path / "journal", GOOD).client == GOOD
    with pytest.raises(OverlayError) as refused:
        Journal(tmp_path / "journal", LONG)
    assert_rule(refused.value)

    accepted_pack = Pack(GOOD, tmp_path / GOOD, data={"manifest": {"client": GOOD, "meta_model_version": "1.3.0"}})
    assert not [finding for finding in checks.identity(accepted_pack) if CLIENT_ID_RULE in finding.message]
    refused_pack = Pack(LONG, tmp_path / LONG, data={"manifest": {"client": LONG, "meta_model_version": "1.3.0"}})
    assert_rule(RuntimeError(checks.identity(refused_pack)[0].message))

    args = ["--client", GOOD, "--clients", str(tmp_path), "--out", str(tmp_path / "out"),
            "--meta-model", str(DEMO / "meta-model.yaml")]
    assert compiler.main(args) == 2
    assert CLIENT_ID_RULE not in capsys.readouterr().err
    args[1] = LONG
    assert compiler.main(args) == 2
    assert CLIENT_ID_RULE in capsys.readouterr().err

    assert open_scratch(GOOD)
    with pytest.raises(ValueError) as refused:
        open_scratch(LONG)
    assert_rule(refused.value)


def test_python_and_terraform_hold_the_same_rule():
    terraform = [DEMO / "infra/pilot/variables.tf", DEMO / "infra/prod/variables.tf",
                 DEMO / "infra/modules/identity/variables.tf"]
    expected = f"^{CLIENT_ID.pattern}$"
    for path in terraform:
        text = path.read_text()
        assert f'regex("{expected}", c)' in text
        assert f'error_message = "{CLIENT_ID_RULE}"' in text


def test_only_the_shared_rule_and_profile_slug_define_the_pattern():
    allowed = {
        Path("kge/landing/zone.py"): 'CLIENT_ID = re.compile(r"[a-z][a-z0-9-]{0,15}")',
        Path("kge/pipeline/profile.py"): 'SLUG = re.compile(r"[a-z][a-z0-9-]*")',
    }
    for path in (DEMO / "kge").rglob("*.py"):
        relative = path.relative_to(DEMO)
        lines = [line for line in path.read_text().splitlines() if "[a-z][a-z0-9-]" in line]
        assert lines == ([allowed[relative]] if relative in allowed else []), relative


@pytest.mark.parametrize("path, anchor", [
    (DEMO / "kit/INSTALL.md", "| `CLIENT` | `meridian` |"),
    (DEMO / "kit/INSTALL.md", "| `clients` |"),
    (ROOT / ".claude/skills/gcp-pilot-ops/SKILL.md", "Terraform checks the list alone"),
    (DEMO / "infra/README.md", '**The pilot** calls every module once, for `clients` (default `["meridian"]`'),
    (DEMO / "infra/README.md", "The tests ([`pilot/tests/`]"),
    (DEMO / "README.md", "The registry is checked whole before anything runs:"),
    (DEMO / "README.md", "**The compiler.**"),
    (ROOT / "docs/use-cases/kge/platform/graph-model-spec.md", "It checks the registry whole first:"),
    (ROOT / "docs/use-cases/kge/platform/pack-contract.md", "`client` (required, v0.3:"),
    (ROOT / "docs/use-cases/kge/platform/pack-contract.md", "- S00 (v0.3)"),
    (ROOT / "docs/use-cases/kge/platform/pack-contract.md", "Changelog:"),
    (DEMO / "kge/compiler/checks.py", "  S00  the manifest's client"),
])
def test_each_documented_client_id_boundary_states_the_rule(path, anchor):
    lines = [line for line in path.read_text().splitlines() if anchor in line]
    assert len(lines) == 1, (path, anchor, lines)
    assert "a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most" in lines[0], path
    if anchor == "Changelog:":
        assert "Issue #117" in lines[0]


OLD_LIMIT = re.compile(r"\b(?:20[\s-]+characters?\s+(?:at\s+most|max(?:imum)?\b)|(?:at\s+most|up\s+to)\s+20[\s-]+characters?)", re.I)


def test_documentation_has_no_old_twenty_character_limit():
    """Scan source documentation, excluding ignored build outputs and review quotations."""
    paths = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
         "demos/kge", "docs/use-cases/kge", ".claude/skills"],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\0")
    assert not [path for path in paths if path.endswith(".md") and OLD_LIMIT.search((ROOT / path).read_text())]


@pytest.mark.parametrize("text", ["20 characters at most", "20 characters max", "20 characters maximum",
                                  "up to 20 characters", "at most 20 characters", "20-character maximum"])
def test_old_limit_guard_catches_wording_variants(text):
    assert OLD_LIMIT.search(text)
