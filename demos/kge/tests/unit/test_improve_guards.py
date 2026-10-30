"""Allowlist guards and version bumping for the improvement job."""
from pathlib import Path
import difflib
import json
import shutil

import pytest
import yaml

from kge.improve import gather as gather_mod
from kge.improve.edits import bump_minor, validate_edit, write_candidate_pack
from kge.pipeline.profile import Profile

DEMO = Path(__file__).resolve().parents[2]


@pytest.fixture
def evidence():
    return {
        "certified_terms": {
            "lease-rentals": {"id": "lease-rentals", "name": "Lease Rentals", "status": "certified"},
            "revenue": {"id": "revenue", "name": "Revenue", "status": "certified"},
        },
        "existing_aliases": {
            "lease rentals": "lease-rentals",
            "revenue": "revenue",
        },
        "candidate_phrases": ["customer satisfaction"],
        "columns": {
            "leasing.rental.lease_rentals_usd": {
                "column": "leasing.rental.lease_rentals_usd",
                "unit": "leasing",
                "table": "rental",
                "name": "lease_rentals_usd",
                "type": "DECIMAL(14,2)",
                "description": "Lease rentals billed, in USD; the business calls this lease income",
                "is_key": False,
                "certified_terms": ["lease-rentals"],
            }
        },
        "term_columns": {"lease-rentals": ["leasing.rental.lease_rentals_usd"]},
    }


def test_valid_alias_edit_passes_allowlist_and_comment_check(evidence):
    ok, reason = validate_edit(
        {"section": "terminology.aliases", "phrase": "lease income", "term": "lease-rentals"},
        evidence,
    )
    assert ok and reason == "ok"


@pytest.mark.parametrize("bad_edit,match", [
    ("not a dict", "must be a JSON object"),
    ({"section": "glossary", "id": "rogue", "phrase": "lease income", "term": "lease-rentals"},
     "outside the allowlist"),
    ({"section": "no_join", "between": ["a", "b"]},
     "outside the allowlist"),
    ({"section": "roles", "id": "admin"},
     "outside the allowlist"),
    ({"section": "terminology.aliases", "phrase": "lease income", "term": "lease-rentals", "certified": True},
     "may not set certified: true"),
    ({"section": "terminology.aliases", "phrase": "lease income", "term": "lease-rentals",
      "column": "nonexistent.table.missing_column"},
     "does not exist in pack.lock"),
    ({"section": "terminology.aliases", "phrase": "lease income", "term": "lease-rentals",
      "column": "leasing.rental.lease_rentals_usd"},
     "not an allowed field"),
    ({"section": "terminology.aliases", "phrase": "lease income", "term": "unknown-term"},
     "not a certified term"),
    ({"section": "terminology.aliases", "phrase": "lease rentals", "term": "lease-rentals"},
     "already an alias"),
    ({"section": "terminology.aliases", "phrase": "customer satisfaction", "term": "lease-rentals"},
     "already held in the steward queue"),
    ({"section": "terminology.aliases", "phrase": "invented ungrounded phrase", "term": "lease-rentals"},
     "not backed by a single matching comment"),
])
def test_disallowed_edits_are_refused(evidence, bad_edit, match):
    ok, reason = validate_edit(bad_edit, evidence)
    assert not ok
    assert match in reason


def test_bump_minor_increments_minor_and_resets_patch():
    assert bump_minor("1.2.0") == "1.3.0"
    assert bump_minor("1.3.4") == "1.4.0"
    with pytest.raises(ValueError, match="not semver"):
        bump_minor("invalid")


def test_write_candidate_pack_leaves_source_pack_untouched_and_bumps_candidate(tmp_path):
    clients = tmp_path / "clients"
    src = clients / "meridian"
    src.mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", src / "pack.yaml")
    shutil.copy(DEMO / "clients/meridian/pack.lock", src / "pack.lock")
    orig_bytes = (src / "pack.yaml").read_bytes()

    dest = tmp_path / "proposals" / "open"
    next_ver, kept, compiler_refused = write_candidate_pack(
        clients,
        "meridian",
        dest,
        [{"phrase": "top-line turnover", "term": "revenue", "section": "terminology.aliases", "proposer": "rules"}],
        identities=DEMO / "deploy/meridian/identities.yaml",
    )
    assert (src / "pack.yaml").read_bytes() == orig_bytes
    assert next_ver == "1.6.0"
    assert len(kept) == 1 and not compiler_refused
    cand_text = (dest / "meridian/pack.yaml").read_text()
    cand_raw = yaml.safe_load(cand_text)
    assert cand_raw["manifest"]["version"] == "1.6.0"
    assert {"phrase": "top-line turnover", "term": "revenue"} in cand_raw["terminology"]["aliases"]

    orig_text = orig_bytes.decode()
    orig_comments = [ln for ln in orig_text.splitlines() if "#" in ln]
    cand_comments = [ln for ln in cand_text.splitlines() if "#" in ln]
    assert len(orig_comments) > 100
    assert cand_comments == orig_comments

    diff_lines = [
        ln for ln in difflib.unified_diff(
            orig_text.splitlines(), cand_text.splitlines(), lineterm=""
        )
        if (ln.startswith("+") or ln.startswith("-"))
        and not ln.startswith("+++")
        and not ln.startswith("---")
    ]
    assert diff_lines == [
        "-  version: 1.5.0",
        "+  version: 1.6.0",
        "+    - {phrase: top-line turnover, term: revenue}",
    ]


def test_gather_never_reads_heldout(monkeypatch, tmp_path):
    clients = tmp_path / "clients"
    target = clients / "meridian"
    (target / "units" / "leasing").mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", target / "pack.yaml")
    shutil.copy(DEMO / "clients/meridian/pack.lock", target / "pack.lock")
    (target / "units" / "leasing" / "heldout.json").write_text(
        json.dumps([{"id": "HL-1", "question": "Secret unit heldout question"}])
    )

    artifacts = tmp_path / "artifacts"
    served = "a" * 64
    (artifacts / "meridian" / served).mkdir(parents=True)
    (artifacts / "meridian" / "current").symlink_to(served)

    prof = Profile(
        "meridian", "meridian", clients, artifacts, tmp_path / "stores", tmp_path / "landing",
        tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "profiles.yaml"
    )
    prof.estate().mkdir(parents=True, exist_ok=True)
    (prof.estate() / "heldout.json").write_text(
        json.dumps([{"id": "H-1", "question": "Secret estate heldout question"}])
    )
    (prof.estate() / "heldout_leasing_v1.json").write_text(
        json.dumps([{"id": "HL-2", "question": "Secret estate unit heldout question"}])
    )
    (prof.estate() / "questions.json").write_text(
        json.dumps([{"id": "Q-1", "question": "Public team question"}])
    )

    run_dir = prof.run_dir() / "runs" / "20261018T120000Z-aaaaaa"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "graph": {"artifact": served},
        "arms": [{"arm": "treatment", "mode": "rules"}],
        "sets": {"golden_v1": {}, "heldout_v1": {}, "heldout_leasing_v1": {}},
    }))
    (run_dir / "records.jsonl").write_text(json.dumps({
        "set": "golden_v1", "id": "G-01", "arm": "treatment", "ok": True, "question": "Q1",
    }) + "\n")

    opened_paths: list[str] = []
    real_open = Path.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def _record(p: Path):
        opened_paths.append(p.name)
        if p.name.startswith("heldout"):
            raise AssertionError(f"gather read forbidden held-out file: {p}")

    def spy_open(self, *args, **kwargs):
        _record(self)
        return real_open(self, *args, **kwargs)

    def spy_read_text(self, *args, **kwargs):
        _record(self)
        return real_read_text(self, *args, **kwargs)

    def spy_read_bytes(self, *args, **kwargs):
        _record(self)
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)

    out = gather_mod.gather(prof)
    assert not any(name.startswith("heldout") for name in opened_paths)
    assert out["evidence"]["certified_terms"]

