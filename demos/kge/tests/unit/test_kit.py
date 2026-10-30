"""The release kit (issue #56): what a platform team installs the pilot from, with no repository.

    .venv/bin/python -m pytest tests/unit/test_kit.py -q

Builds the kit without its images (kit/build.sh, IMAGES=0, into a scratch folder) and checks it as an installer gets it:
the zip's files and nothing of the repository's working state; nothing in the zip or the install guide that links to,
reads from or names this repository; the zip the same bytes on a second build; SHA256SUMS over every file; the example
content the same pack as clients/meridian's, and its second version the one-line edit of the Demo script's step 4; the
Compose file's services, images and settings as the pilot's Terraform gives them; and the guide carrying the
acceptance journey. With Docker Compose on the PATH, docker compose config validates the zip's Compose file; with
Terraform on the PATH (and its providers reachable: the registry, or a mirror TF_CLI_CONFIG_FILE names), terraform fmt,
validate and test run from the extracted zip, outside the repository. The image's bill of materials (kit/sbom.py) is
checked on its parts; building the images is the integrator's (make kit VERSION=<x.y.z>).

The guard: build.sh, run with a stand-in docker on the PATH (no Docker), refuses an image that trusts a certificate
authority given at build time (what a build behind a TLS-intercepting proxy leaves in the image), naming the variable that says so, before it is saved; LOCAL_ONLY=1 lets it through into a
kit marked local-only, its image labelled kge.local-only and its bill of materials saying so; a clean image goes into
the kit unmarked. The Release kit workflow (.github/workflows/kit.yml), read as YAML with the pipeline it calls once per
demo with a kit (.github/workflows/kit-demo.yml): a demo with a kit is demos/<demo>/ with kit/VERSION and a kit rule in
its Makefile, and the demos a run builds are found by its discover step (run here in a scratch repository, under
GitHub's default shell and under pipefail: a pull request's or a merge's changed demos, by name and never by a longer
name's prefix, a file moved out of a demo included, every demo with a kit when either kit workflow file changes and none
for another workflow file, the one a kit tag names, the one a run by hand names; KGE the one demo with a kit in this
repository). Each demo's call is independent of the others and holds the demo's lock; the pipeline reads and only its
release job writes contents, on the demo's kit tag or a merge into develop that touches it, never marking its release
the repository's latest; a merge's kit is the next patch of the line in the demo's kit/VERSION, counted from the demo's
own tags alone (run here with a stand-in gh, for KGE and another demo), and each demo's publishing runs are serialised,
in the order of the merges; the release step tags a merge's kit itself, and refuses a version published already (run
here with a stand-in gh); only its prune job writes anything else, the repository's Actions artifacts, with no checkout
and no code of the repository, deleting the demo's <demo>-kit-* artifacts of every earlier run but none of this run's, a
later run's or another demo's (run here with a stand-in gh: older kits deleted, across pages, the current one, a later
run's, a kit whose run is still going, a tag's whose run failed, other demos' and other artifacts kept); a demo's
notices check, its kit-notices rule, runs where it has one and its release waits for it (KGE's the two commands it was),
no job's failure let through; it holds no secret, reaches no cloud and pushes no image (nor does any demo's kit rule or
kit script), no checkout keeping git's credentials; it runs make kit with the images on a pinned runner, after a step
that holds Docker to its containerd image store (run here with stand-ins), and uploads the kit folder; every action is
pinned to a major version tag.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import itertools
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml

DEMO = Path(__file__).resolve().parents[2]
KIT = DEMO / "kit"
WORKFLOWS = DEMO.parents[1] / ".github" / "workflows"
VERSION = "0.0.1"
REPOSITORY = re.compile(r"github\.com|demos/|docs/use-cases|\.\./\.\./|make |README\.md")


def build(out: Path) -> Path:
    env = {**os.environ, "VERSION": VERSION, "KIT_OUT": str(out), "IMAGES": "0"}
    done = subprocess.run(["sh", str(KIT / "build.sh")], cwd=DEMO, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return out / f"kge-{VERSION}"


@pytest.fixture(scope="module")
def kit(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("kit"))


@pytest.fixture(scope="module")
def unzipped(kit, tmp_path_factory) -> Path:
    """The zip extracted into a folder of its own, outside the repository, as an installer unzips it."""
    where = tmp_path_factory.mktemp("installer") / "kge"
    with zipfile.ZipFile(kit / f"kge-{VERSION}.zip") as z:
        z.extractall(where)
    return where


def names(kit: Path) -> list[str]:
    with zipfile.ZipFile(kit / f"kge-{VERSION}.zip") as z:
        return z.namelist()


def test_the_kit_holds_the_zip_the_guide_and_their_sums(kit):
    files = sorted(p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file())
    assert files == ["SHA256SUMS", "docs/INSTALL.md", "docs/ONBOARDING.md", f"kge-{VERSION}.zip",
                     "notices/KGE_THIRD_PARTY_NOTICES.md", "notices/PORTAL_THIRD_PARTY_NOTICES.md"], files
    sums = dict(reversed(line.split("  ", 1)) for line in (kit / "SHA256SUMS").read_text().splitlines())
    assert set(sums) == {"docs/INSTALL.md", "docs/ONBOARDING.md", f"kge-{VERSION}.zip",
                         "notices/KGE_THIRD_PARTY_NOTICES.md", "notices/PORTAL_THIRD_PARTY_NOTICES.md"}
    for name, digest in sums.items():
        assert hashlib.sha256((kit / name).read_bytes()).hexdigest() == digest, name


def test_a_version_is_three_numbers(tmp_path):
    env = {**os.environ, "VERSION": "1.2", "KIT_OUT": str(tmp_path), "IMAGES": "0"}
    done = subprocess.run(["sh", str(KIT / "build.sh")], cwd=DEMO, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "three numbers" in done.stderr
    assert not list(tmp_path.iterdir()), "nothing written"


def test_the_zip_holds_terraform_compose_examples_and_skills_and_nothing_else(kit):
    entries = names(kit)
    assert entries == sorted(entries), "entries sorted"
    infra = sorted(p.relative_to(DEMO / "infra").as_posix() for p in (DEMO / "infra").rglob("*")
                   if p.is_file() and p.relative_to(DEMO / "infra").parts[0] in ("pilot", "modules", "tests")
                   and ".terraform" not in p.relative_to(DEMO / "infra").parts[:-1]
                   and p.name != "README.md" and not re.search(r"\.tfstate|\.tfplan$|^crash", p.name))
    assert [e for e in entries if e.startswith("terraform/")] == [f"terraform/{n}" for n in infra]
    assert {e.split("/")[1] for e in entries if e.startswith("terraform/")} == {"pilot", "modules", "tests"}
    assert "terraform/pilot/.terraform.lock.hcl" in entries
    assert not [e for e in entries if e.startswith("terraform/prod")], "QA and PROD are not part of the kit"
    assert not [e for e in entries if re.search(r"(qa|prod)\.tfvars$|\.tfbackend$|onboard\.workflow\.yaml$", e)]
    assert not [e for e in entries if e.endswith("README.md") or "/.terraform/" in e or ".tfstate" in e]
    rest = [e for e in entries if not e.startswith("terraform/")]
    assert rest == ["VERSION", "compose/.env.example", "compose/compose.yaml",
                    "examples/leasing/heldout.json", "examples/leasing/questions.json", "examples/leasing/schema.sql",
                    "examples/meridian-portal/catalogue.yaml",
                    "examples/meridian-takings/clients/meridian/pack.yaml",
                    "examples/meridian/clients/meridian/baselines/issue-38.json",
                    "examples/meridian/clients/meridian/baselines/issue-39.json",
                    "examples/meridian/clients/meridian/baselines/issue-40.json",
                    "examples/meridian/clients/meridian/catalogue.yaml",
                    "examples/meridian/clients/meridian/pack.lock", "examples/meridian/clients/meridian/pack.yaml",
                    "skills/ask/SKILL.md", "skills/build-and-gate/SKILL.md", "skills/improve/SKILL.md",
                    "skills/land-and-draft/SKILL.md"], rest
    with zipfile.ZipFile(kit / f"kge-{VERSION}.zip") as z:
        assert z.read("VERSION").decode() == f"{VERSION}\n"
        assert z.read("examples/meridian-portal/catalogue.yaml") == (DEMO / "examples/meridian-portal/catalogue.yaml").read_bytes()
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}, "fixed timestamps"


def test_the_zip_is_the_same_bytes_on_a_second_build(kit, tmp_path):
    again = build(tmp_path)
    assert (again / f"kge-{VERSION}.zip").read_bytes() == (kit / f"kge-{VERSION}.zip").read_bytes()


def test_nothing_in_the_zip_or_the_guide_names_the_repository(kit, unzipped):
    found = []
    for path in [*sorted(p for p in unzipped.rglob("*") if p.is_file()), *sorted((kit / "docs").glob("*.md"))]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if REPOSITORY.search(line):
                found.append(f"{path.name}:{n}: {line.strip()[:120]}")
    assert not found, "\n".join(found)


QA_PROD = re.compile(r"terraform/prod|qa\.tfvars|prod\.tfvars|\.tfbackend|organization_id")


def qa_or_prod_named(paths) -> list[str]:
    return [f"{path.name}:{n}: {line.strip()[:120]}" for path in paths
            for n, line in enumerate(path.read_text().splitlines(), 1) if QA_PROD.search(line)]


def test_the_install_guide_names_no_qa_or_prod_step():
    """Grok m4 on #97: the kit carries the pilot's Terraform alone, so its guide never names QA or PROD's Terraform, their
    variable files, a backend file or an organisation; read from the guide's source, so it runs wherever the tests do."""
    assert not (found := qa_or_prod_named([KIT / "INSTALL.md", DEMO / "ui" / "onboarding.md"])), "\n".join(found)


def test_nothing_in_the_zip_or_its_docs_names_qa_or_prod(kit, unzipped):
    """Grok m4 on #97: the built kit, its zip and its docs, names no QA or PROD step either."""
    docs = [p for p in sorted(unzipped.rglob("*")) if p.is_file() and p.suffix == ".md"]
    assert not (found := qa_or_prod_named([*docs, *sorted((kit / "docs").glob("*.md"))])), "\n".join(found)


def test_the_onboarding_guide_is_the_pages_source(kit):
    assert (kit / "docs" / "ONBOARDING.md").read_bytes() == (DEMO / "ui" / "onboarding.md").read_bytes()
    assert "`docs/ONBOARDING.md`" in (kit / "docs" / "INSTALL.md").read_text()


def test_the_example_is_meridians_pack_and_the_second_its_one_line_edit(unzipped):
    example = unzipped / "examples" / "meridian" / "clients" / "meridian"
    pack = yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())
    assert yaml.safe_load((example / "pack.yaml").read_text()) == pack, "the same data, its comment lines left out"
    assert (example / "pack.lock").read_bytes() == (DEMO / "clients" / "meridian" / "pack.lock").read_bytes()
    for name in ("issue-38.json", "issue-39.json", "issue-40.json"):
        assert (example / "baselines" / name).read_bytes() == (DEMO / "clients" / "meridian" / "baselines" / name).read_bytes()
    plain = (example / "pack.yaml").read_text().splitlines()
    takings = (unzipped / "examples" / "meridian-takings" / "clients" / "meridian" / "pack.yaml").read_text().splitlines()
    i = plain.index("    - {phrase: revenue, term: revenue}")
    assert takings == plain[:i + 1] + ["    - {phrase: takings, term: revenue}"] + plain[i + 1:]
    assert not list((unzipped / "examples" / "meridian-takings").rglob("*.lock")), "the second version is the pack alone"


def test_compose_mirrors_the_pilots_terraform(unzipped):
    """The settings are those Terraform's run module gives (infra/modules/run: service_env and job_env), the buckets
    named as Terraform names them less the project, and the storage emulator stands in for credentials."""
    compose = yaml.safe_load((unzipped / "compose" / "compose.yaml").read_text())
    assert compose["name"] == "kge" and set(compose["services"]) == {"gcs", "app", "job", "portal"}
    gcs, app, job, portal = (compose["services"][s] for s in ("gcs", "app", "job", "portal"))
    assert gcs["image"] == ("fsouza/fake-gcs-server:1.56.1@sha256:"
                            "797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef"), "pinned by digest"
    assert gcs["command"] == ["-scheme", "http", "-port", "4443", "-backend", "filesystem", "-filesystem-root", "/storage"]
    assert gcs["ports"] == ["127.0.0.1:${GCS_PORT:-4443}:4443"] and gcs["volumes"] == ["gcs-data:/storage"]
    assert app["image"] == job["image"] == f"kge:{VERSION}", "the kit's own image, one for the service and the job"
    assert portal["image"] == f"kge-portal:{VERSION}"
    assert portal["ports"] == ["127.0.0.1:${PORTAL_PORT:-8090}:8080"]
    assert portal["environment"] == {"PORT": "8080", "PROFILE": "${CLIENT:-meridian}",
                                      "PORTAL_BACKEND": "sqlite", "PORTAL_SQLITE_PATH": "/data/portal.db",
                                      "KGE_URL": "${KGE_URL-http://app:8000}",
                                      "KGE_PUBLIC_URL": "http://127.0.0.1:${KGE_PORT:-8000}",
                                      "PORTAL_CATALOGUE_FILE": "${PORTAL_CATALOGUE_FILE:-}",
                                      "PORTAL_IDENTITIES_FILE": "${PORTAL_IDENTITIES_FILE:-}",
                                      "PORTAL_PASSWORDS_FILE": "${PORTAL_PASSWORDS_FILE:-}",
                                      "PORTAL_SESSION_SECRET": "${PORTAL_SESSION_SECRET:-}",
                                      "PORTAL_COOKIE_SECURE": "${PORTAL_COOKIE_SECURE:-false}",
                                      "PORTAL_SYNC_TOKEN": "${PORTAL_SYNC_TOKEN:-}",
                                      "PORTAL_ASK": "${PORTAL_ASK:-on}"}
    assert portal["volumes"] == ["portal-data:/data", "${PORTAL_IDENTITY_DIR_HOST:-../identity}:/identity:ro",
                                 "${PORTAL_CATALOGUE_FILE_HOST:-../examples/meridian-portal/catalogue.yaml}:/catalogue/catalogue.yaml:ro"]
    assert app["ports"] == ["127.0.0.1:${KGE_PORT:-8000}:8000"] and app["restart"] == "no" and "command" not in app
    assert job["profiles"] == ["job"] and job["volumes"] == ["../examples:/kit/examples:ro"]
    assert "volumes" not in app, "the service mounts nothing"
    assert app["environment"] == {"PROFILE": "${CLIENT:-meridian}", "STORE_URL": "gs://kge-store",
                                  "STORAGE_EMULATOR_HOST": "http://gcs:4443", "SIGNIN": "passwords",
                                  "SESSION_SECRET": "${SESSION_SECRET:-}",
                                  "PORTAL_SYNC_TOKEN": "${PORTAL_SYNC_TOKEN:-}",
                                  "API_URL": "http://127.0.0.1:${KGE_PORT:-8000}",
                                  "LADYBUG_MAX_DB_MB": "${LADYBUG_MAX_DB_MB:-128}",
                                  "PORTAL_URL": "http://127.0.0.1:${PORTAL_PORT:-8090}"}
    assert job["environment"] == {"PROFILE": "${CLIENT:-meridian}", "STORE_URL": "gs://kge-store",
                                  "LANDING_URL": "gs://kge-${CLIENT:-meridian}-landing",
                                  "STORAGE_EMULATOR_HOST": "http://gcs:4443",
                                  "LADYBUG_MAX_DB_MB": "${LADYBUG_MAX_DB_MB:-128}"}
    run = "\n".join(p.read_text() for p in sorted((DEMO / "infra" / "modules" / "run").glob("*.tf")))
    service_env = re.search(r"service_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, local\.trusted_env, var\.env\)", run).group(1)
    job_env = re.search(r"job_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, var\.env\)", run).group(1)
    assert set(re.findall(r"([A-Z_]+)\s*=", service_env)) <= set(app["environment"])
    assert set(re.findall(r"([A-Z_]+)\s*=", job_env)) <= set(job["environment"])
    assert "TMP_URL" not in app["environment"] and "TMP_URL" not in job["environment"], (
        "TMP_URL, the run module's opt-in tmp_env, is left unset in Compose: the scratch area is then a local folder, "
        "which is all the local stack needs, since no BigQuery load is made there")
    env = dict(line.split("=", 1) for line in (unzipped / "compose" / ".env.example").read_text().splitlines()
               if line and not line.startswith("#"))
    assert env == {"CLIENT": "meridian", "KGE_PORT": "8000", "PORTAL_PORT": "8090", "GCS_PORT": "4443", "SESSION_SECRET": "", "PORTAL_SYNC_TOKEN": "", "PORTAL_ASK": "on"}


def test_the_guide_carries_the_acceptance_journey(kit):
    guide = (kit / "docs" / "INSTALL.md").read_text()
    assert "@VERSION@" not in guide and f"kge-{VERSION}.zip" in guide
    journey = [
        "sha256sum -c SHA256SUMS",
        f"docker load -i images/kge-{VERSION}.tar",
        "docker load -i images/fake-gcs-server-1.56.1.tar",
        f"unzip kge-{VERSION}.zip -d kge && cd kge/compose && cp .env.example .env",
        "docker compose up -d gcs",
        'docker compose run --rm job setup-client CLIENT=meridian ASKER=analyst_enterprise BASELINE=baselines/issue-40.json '
        '"PEOPLE=analyst_retail:analyst_retail analyst_enterprise:analyst_enterprise finance_reporting:finance_reporting '
            'steward_glossary:steward_glossary business_team:business_team knowledge_engineer:knowledge_engineer advisor_wealth:advisor_wealth admin:admin steward:steward pipeline_tagger:pipeline_tagger"',
        "docker compose run --rm job put-example",
        "docker compose run --rm job seed",
        "docker compose run --rm job onboard",
        "docker compose run --rm job sign-off PRINCIPAL=steward_glossary",
        "docker compose run --rm job release",
        "docker compose run --rm job passwords",
        'curl -fsS -o passwords.txt "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/secrets%2Fmeridian%2Fpasswords.txt?alt=media"',
        "docker compose up -d app",
        "http://127.0.0.1:8000",
        "13,287.67",
        "docker compose run --rm job put-example EXAMPLE=meridian-takings",
        "docker compose restart app",
        "docker compose run --rm job releases",
        "docker compose run --rm job release HASH=<H1>",
    ]
    at = 0
    for step in journey:
        found = guide.find(step, at)
        assert found >= 0, f"not found, in order: {step}"
        at = found
    for heading in ("## Prerequisites", "## Install on your machine with Docker Compose", "## Install on Google Cloud with Terraform",
                    "## Configure", "## Upgrade and roll back", "## Troubleshooting"):
        assert heading in guide, heading
    for gcloud in ("-target=module.secrets", "gcloud run jobs update kge-meridian", "--image=$IMAGE@$DIGEST",
                   "gcloud run jobs execute kge-meridian", "gcloud storage rsync -r $KIT/examples/meridian $STORE",
                   "--update-env-vars=KGE_RELEASE=<hash>", "terraform test -var-file=example.tfvars"):
        assert gcloud in guide, gcloud
    assert "nothing released for meridian: run release" in guide and "run setup-client" in guide


def test_the_upgrade_upload_matches_the_examples_layout_and_recreates_the_compose_app(kit):
    guide = (kit / "docs" / "INSTALL.md").read_text()
    assert 'gcloud storage rsync -r "$KIT/examples/meridian" "$STORE"' in guide
    assert 'gcloud storage rsync -r "$KIT/examples/meridian" "$STORE/clients/meridian"' not in guide
    assert "examples/meridian/clients/meridian/pack.yaml" in __import__("zipfile").ZipFile(
        kit / f"kge-{VERSION}.zip").namelist()
    upgrade = guide.split("### Upgrading from 0.5.0", 1)[1].split("## Troubleshooting", 1)[0]
    assert "export KIT=<the newly unzipped" in upgrade
    assert 'docker compose up -d app' in upgrade
    assert 'docker compose restart app' not in upgrade


def test_the_guide_says_how_to_keep_the_page_private_and_what_each_model_needs_on_a_new_project():
    """Issue #69, from the pilot's trial on Google Cloud: a private page for an organisation that refuses allUsers, and a
    proxy's Google token in X-Serverless-Authorization; Claude's quota of 0 on a new project, Gemini's default one."""
    guide = (KIT / "INSTALL.md").read_text()
    for needle in ("constraints/iam.allowedPolicyMemberDomains", '-var="public=false"', "invokers=", "X-Serverless-Authorization",
                   "answered 401", "| `public` | `true` |", "| `invokers` | `[]` |", "### The model", "`MODEL_PROVIDER`", "`MODEL_NAME`",
                   "`VERTEX_REGION`", "`vertex-gemini`", "`vertex-claude`", "`claude-haiku-5-5`", "429", "roles/aiplatform.user",
                   "`gemini-2.5-flash-lite`"):
        assert needle in guide, needle
    assert "X-Serverless-Authorization" in (DEMO / "infra" / "README.md").read_text()


def test_switching_bigquery_off_or_rolling_back_to_0_7_clears_a_wide_embedding_dim():
    """The image's own graph holds 128-wide vectors and refuses any other EMBEDDING_DIM, so the steps that move the
    vectors back out of BigQuery bring it back to 128 before their apply (a review finding on #97). On the pilot that is
    EMBEDDING_DIM = "128" in app_env, since removing it there leaves pilot_env's 768 (Copilot on #97); rolling back to
    0.7's Terraform, which has no pilot_env, removing it is enough."""
    guide = (KIT / "INSTALL.md").read_text()
    bigquery = guide.split("### BigQuery", 1)[1].split("### Mounts and secrets", 1)[0]
    back = bigquery[bigquery.index("To switch back"):]
    assert 'on the pilot set `EMBEDDING_DIM = "128"` in `app_env`' in back
    assert "leaves `pilot_env`'s `768`" in back and "elsewhere remove it or set it to `\"128\"`" in back
    assert back.index("EMBEDDING_DIM") < back.index("and apply")
    assert "remove `EMBEDDING_DIM` or set it to" not in back
    upgrade = guide.split("### Upgrading from 0.7", 1)[1].split("### Upgrading from 0.6", 1)[0]
    rollback = upgrade[upgrade.index("To roll back"):]
    assert "`EMBEDDING_DIM`" in rollback and '`"128"`' in rollback
    assert "0.7's Terraform supplies no `EMBEDDING_DIM`" in rollback


def test_moving_the_app_state_back_to_files_resets_each_unit_while_bigquery_is_still_selected():
    """Reset deletes a unit's records from the backend selected, so a unit reset after the revert to files leaves its
    BigQuery manifest and history behind (Copilot on #97): record and reset first, revert and apply, then upload and
    onboard again; the switch back, the rollback to 0.7 and a rollback to 0.6 from a later line alike."""
    guide = (KIT / "INSTALL.md").read_text()
    bigquery = guide.split("### BigQuery", 1)[1].split("### Mounts and secrets", 1)[0]
    reverse = bigquery[bigquery.index("Switching back from BigQuery to files"):]
    assert (reverse.index("record every listed unit") < reverse.index('while `APP_STATE_BACKEND = "bigquery"` is still selected')
            < reverse.index("revert the setting and apply") < reverse.index("upload and onboard those units again"))
    back = bigquery[bigquery.index("To switch back"):]
    assert (back.index("reset each listed unit while BigQuery is still selected") < back.index('`APP_STATE_BACKEND = "files"`')
            < back.index("and apply") < back.index("upload and onboard again each unit you reset"))
    assert "after the reset steps above" not in bigquery
    upgrade = guide.split("### Upgrading from 0.7", 1)[1].split("### Upgrading from 0.6", 1)[0]
    rollback = upgrade[upgrade.index("To roll back"):]
    assert (rollback.index("reset each listed unit while 0.8 still runs") < rollback.index("return each client's job and service")
            < rollback.index("upload and onboard again each unit you reset"))
    assert "must be reset and onboarded again" not in rollback
    older = guide.split("### Upgrading from 0.6", 1)[1].split("### Upgrading from 0.5.0", 1)[0]
    back06 = older[older.index("To roll back"):]
    assert back06.index("first record and reset each unit") < back06.index("upload and onboard them again after")


def test_a_0_6_install_does_the_0_7_sections_first_steps_before_its_apply():
    """The 0.6 section's apply already brings in a later line's pilot settings, the BigQuery app state among them, so a
    unit must be reset before it, never after (a review finding on #97)."""
    guide = (KIT / "INSTALL.md").read_text()
    upgrade = guide.split("### Upgrading from 0.6", 1)[1].split("### Upgrading from 0.5.0", 1)[0]
    order = upgrade[upgrade.index("To go from 0.6"):]
    assert order.index("before anything") < order.index("then this section") < order.index("then the rest of")
    assert "`onboard-status`" in order and "reset while 0.6 still runs" in order


def test_every_delete_of_a_secret_from_the_store_on_google_cloud_deletes_every_version():
    """The store bucket keeps a deleted object's versions for 30 days (infra/modules/storage), so a delete of the passwords
    from it, in the guide or in the infra README's bootstrap, takes every version."""
    storage = (DEMO / "infra" / "modules" / "storage" / "main.tf").read_text()
    assert "versioning {\n    enabled = local.versioned[each.key]" in storage
    assert 'versioned = { for k in keys(local.buckets) : k => k != "_tmp" }' in storage, "every bucket but the tmp one"
    for doc in (KIT / "INSTALL.md", DEMO / "infra" / "README.md"):
        deletes = [m.group(0) for m in re.finditer(r"gcloud storage rm\b[^\n`]*", doc.read_text()) if "secrets/" in m.group(0)]
        assert deletes, f"{doc.name}: no delete of the passwords from the store"
        for rm in deletes:
            assert rm.startswith("gcloud storage rm --all-versions "), f"{doc.name}: {rm}"


def sbom():
    spec = importlib.util.spec_from_file_location("kit_sbom", KIT / "sbom.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bill_of_materials_holds_the_base_images_by_digest_and_each_licence_as_stated():
    module = sbom()
    bases = module.base_images(DEMO / "Dockerfile")
    assert [b["name"] for b in bases] == ["library/python"], "one base: the image copies nothing from another"
    assert bases[0]["properties"] == [{"name": "kge:dockerfile:stage", "value": "final"}]
    assert all(re.fullmatch(r"[0-9a-f]{64}", b["hashes"][0]["content"]) and b["purl"].startswith("pkg:docker/library/python@sha256%3A")
               for b in bases)
    assert module.licences({"license_expression": "MIT"}) == [{"expression": "MIT"}]
    assert module.licences({"license": "BSD-3-Clause"}) == [{"license": {"name": "BSD-3-Clause"}}]
    assert module.licences({"license": "a whole licence text\nspanning lines", "classifier": [
        "License :: OSI Approved :: Apache Software License", "Programming Language :: Python"]}) == \
        [{"license": {"name": "Apache Software License"}}]


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "portal/Dockerfile"])
def test_the_bill_of_materials_takes_the_base_given_as_a_build_argument(dockerfile):
    """Issue #173 requirement 9: built on another base (its BASE_IMAGE build argument), the bill names that base as
    given, by digest when it has one and marked unpinned when it has not; the default is the Dockerfile's, by digest."""
    module, path = sbom(), DEMO / dockerfile
    default = module.base_images(path)
    assert [b["name"] for b in default] == [default[0]["name"]] and all(b["hashes"] for b in default)
    assert not [p for b in default for p in b["properties"] if p["name"].startswith("kge:base:")]
    ubi = module.base_images(path, "registry.access.redhat.com/ubi9/python-312")
    assert [b["name"] for b in ubi] == ["registry.access.redhat.com/ubi9/python-312"]
    assert "hashes" not in ubi[0] and {"name": "kge:base:pinned", "value": "false: given without a digest"} in ubi[0]["properties"]
    digest = "a" * 64
    pinned = module.base_images(path, f"client.example/base:1@sha256:{digest}")
    assert pinned[0]["hashes"] == [{"alg": "SHA-256", "content": digest}] and pinned[0]["version"] == "1"
    assert {"name": "kge:base:given", "value": f"client.example/base:1@sha256:{digest}"} in pinned[0]["properties"]


def test_the_bill_of_materials_leaves_out_a_build_only_stage_and_refuses_a_base_it_cannot_take(tmp_path):
    module, digest = sbom(), "b" * 64
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"ARG BASE_IMAGE=python:3.12@sha256:{digest}\nARG UV_IMAGE=uv:1@sha256:{'c' * 64}\n"
                          "FROM ${UV_IMAGE} AS uv\nFROM ${BASE_IMAGE}\nRUN --mount=from=uv,source=/uv,target=/bin/uv uv\n")
    assert [b["properties"][0]["value"] for b in module.base_images(dockerfile)] == ["final"]
    fixed = tmp_path / "Fixed"
    fixed.write_text(f"FROM python:3.12@sha256:{digest}\n")
    with pytest.raises(SystemExit, match="no stage that reaches the image is FROM its ARG BASE_IMAGE"):
        module.base_images(fixed, "client.example/base:1")


def test_the_os_packages_are_read_with_dpkg_or_rpm_and_neither_is_said():
    module = sbom()
    deb, tool = module.os_components("debian-12\ndpkg\nzlib1g\t1:1.2.13.dfsg-1\tamd64\n")
    assert tool == "dpkg" and deb == [{"type": "library", "name": "zlib1g", "version": "1:1.2.13.dfsg-1",
                                       "bom-ref": "pkg:deb/debian/zlib1g@1%3A1.2.13.dfsg-1?arch=amd64&distro=debian-12",
                                       "purl": "pkg:deb/debian/zlib1g@1%3A1.2.13.dfsg-1?arch=amd64&distro=debian-12"}]
    rpm, tool = module.os_components("rhel-9.6\nrpm\nzlib\t(none)\t1.2.11-40.el9\tx86_64\n"
                                     "openssl-libs\t1\t3.2.2-6.el9_5\tx86_64\ngpg-pubkey\t(none)\tfd431d51-4ae0493b\t(none)\n")
    assert tool == "rpm" and [(c["name"], c["version"]) for c in rpm] == [("zlib", "1.2.11-40.el9"),
                                                                          ("openssl-libs", "1:3.2.2-6.el9_5")]
    assert rpm[1]["purl"] == "pkg:rpm/rhel/openssl-libs@3.2.2-6.el9_5?arch=x86_64&epoch=1&distro=rhel-9.6"
    assert module.os_components("rhel-9.6\nnone\n") == ([], "none") and module.os_components("") == ([], "none")


def rpm_header(tags: dict[int, str | int]) -> bytes:
    """A minimal rpm header as an rpm database row holds it: the index count, the data length, 16-byte index entries
    (tag, type, offset, count) and the data, strings NUL-terminated (type 6) and the epoch a big-endian int32 (type 4)."""
    index, data = b"", b""
    for tag, value in tags.items():
        if isinstance(value, int):
            data += b"\0" * (-len(data) % 4)
            index += struct.pack(">4i", tag, 4, len(data), 1)
            data += struct.pack(">i", value)
        else:
            index += struct.pack(">4i", tag, 6, len(data), 1)
            data += value.encode() + b"\0"
    return struct.pack(">2i", len(tags), len(data)) + index + data


@pytest.fixture
def rpmdb(tmp_path) -> Path:
    path = tmp_path / "rpmdb.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table Packages (hnum integer primary key, blob blob not null)")
        for tags in ({1000: "zlib", 1001: "1.2.11", 1002: "40.el9", 1022: "x86_64"},
                     {1000: "openssl-libs", 1003: 1, 1001: "3.2.2", 1002: "6.el9_5", 1022: "x86_64"},
                     {1000: "gpg-pubkey", 1001: "fd431d51", 1002: "4ae0493b"}):
            db.execute("insert into Packages (blob) values (?)", (rpm_header(tags),))
    db.close()
    return path


RPM_LISTED = [("zlib", "1.2.11-40.el9"), ("openssl-libs", "1:3.2.2-6.el9_5")]


def test_a_base_with_an_rpm_database_but_no_rpm_has_its_packages_read_from_the_database(rpmdb):
    """A RHEL base whose package manager was stripped keeps its rpm database: the image's python3 reads it as rpm -qa."""
    module = sbom()
    listed = subprocess.run([sys.executable, "-c", module.RPMDB, str(rpmdb)], capture_output=True, text=True, check=True)
    packages, tool = module.os_components("rhel-9.8\nrpmdb\n" + listed.stdout)
    assert tool == "rpmdb" and [(c["name"], c["version"]) for c in packages] == RPM_LISTED
    assert packages[1]["purl"] == "pkg:rpm/rhel/openssl-libs@3.2.2-6.el9_5?arch=x86_64&epoch=1&distro=rhel-9.8"
    assert "elif [ -r /var/lib/rpm/rpmdb.sqlite ]" in module.OS_PACKAGES and "rpm database" in module.NO_PACKAGE_TOOL


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on the PATH")
def test_the_portals_bill_reads_the_rpm_database_the_same_way(rpmdb):
    script = re.search(r"const RPMDB = `(.*?)`;", (KIT / "sbom_node.mjs").read_text(), re.S).group(1).replace("\\\\", "\\")
    listed = subprocess.run(["node", "--experimental-sqlite", "--no-warnings", "-e", script, str(rpmdb)],
                            capture_output=True, text=True, timeout=60)
    if listed.returncode and "node:sqlite" in listed.stderr:
        pytest.skip("this node has no node:sqlite (before 22.5)")
    assert listed.returncode == 0, listed.stderr
    packages, tool = sbom().os_components("rhel-9.8\nrpmdb\n" + listed.stdout)
    assert [(c["name"], c["version"]) for c in packages] == RPM_LISTED


def test_the_python_distributions_are_read_with_importlib_metadata_once_each():
    """The image's virtual environment has no pip: importlib.metadata, run by the environment's Python, lists it."""
    module = sbom()
    assert "importlib.metadata" in module.DISTRIBUTIONS and "pip" not in module.DISTRIBUTIONS
    assert module.PYTHON == "/app/.venv/bin/python3"
    found = module.python_components([
        {"name": "Typing_Extensions", "version": "4.12.2", "license_expression": "PSF-2.0", "license": None, "classifier": []},
        {"name": "typing-extensions", "version": "4.0.0", "license_expression": None, "license": None, "classifier": []}])
    assert found == [{"type": "library", "bom-ref": "pkg:pypi/typing-extensions@4.12.2", "name": "typing-extensions",
                      "version": "4.12.2", "purl": "pkg:pypi/typing-extensions@4.12.2",
                      "licenses": [{"expression": "PSF-2.0"}]}]


STAND_IN_DOCKER = r'''#!@PYTHON@
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["KGE_STAND_IN_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
image_id = "sha256:" + "0" * 64
labels = Path(os.environ["KGE_STAND_IN_LOG"] + ".labels")
if args[0] == "build":
    labels.write_text(json.dumps(dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label")))
elif args[:2] == ["image", "inspect"]:
    if "--format" not in args:
        print(json.dumps([{"Id": image_id, "Config": {"Labels": json.loads(labels.read_text())}}]))
    elif "Config.Env" in args[args.index("--format") + 1]:
        print(os.environ["KGE_STAND_IN_ENV"])
    elif ".Os" in args[args.index("--format") + 1]:
        print("linux/amd64")
    else:
        print(image_id)
elif args[0] == "save":
    Path(args[args.index("-o") + 1]).write_text("a stand-in image\n")
elif args[0] == "run":
    entry = args[args.index("--entrypoint") + 1]
    print("debian-12\ndpkg" if entry == "sh" else "[]" if "python3" in entry else "")
'''
TRUST = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT", "NODE_EXTRA_CA_CERTS")
CLEAN = "\n".join(["PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONDONTWRITEBYTECODE=1",
                   "PYTHONUNBUFFERED=1", "NOTE=no SSL_CERT_FILE here"])
CA = "/usr/local/share/sandbox-ca.crt"


def stand_in_build(tmp_path: Path, image_env: str, **settings: str) -> tuple[subprocess.CompletedProcess, Path, list[str]]:
    """build.sh with the images on and the stand-in docker first on the PATH; its result, its KIT_OUT and docker's calls.

    The stand-in's image settings are image_env's lines and its labels the build's --label flags; pull and tag do
    nothing, and the image holds no Python distribution and no package."""
    bin_dir, out, log = tmp_path / "bin", tmp_path / "out", tmp_path / "docker.log"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(STAND_IN_DOCKER.replace("@PYTHON@", sys.executable))
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PY": sys.executable, "VERSION": VERSION,
           "KIT_OUT": str(out), "IMAGES": "1", "BUILD_FLAGS": "", "LOCAL_ONLY": "", "KGE_STAND_IN_ENV": image_env,
           "KGE_STAND_IN_LOG": str(log), **settings}
    done = subprocess.run(["sh", str(KIT / "build.sh")], cwd=DEMO, env=env, capture_output=True, text=True, timeout=120)
    return done, out, log.read_text().splitlines() if log.exists() else []


def checked_sums(kit: Path) -> set[str]:
    """The files SHA256SUMS names, each checked against its sum; SHA256SUMS names every other file of the kit."""
    sums = dict(reversed(line.split("  ", 1)) for line in (kit / "SHA256SUMS").read_text().splitlines())
    for name, digest in sums.items():
        assert hashlib.sha256((kit / name).read_bytes()).hexdigest() == digest, name
    assert set(sums) == {p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    return set(sums)


def sbom_marks(kit: Path) -> list[dict]:
    bom = json.loads((kit / "images" / f"kge-{VERSION}.sbom.cdx.json").read_text())
    return [p for p in bom["metadata"]["component"]["properties"] if p["name"] == "kge:local-only"]


IMAGE_FILES = {"images/fake-gcs-server-1.56.1.digest", "images/fake-gcs-server-1.56.1.tar", f"images/kge-{VERSION}.digest",
               f"images/kge-{VERSION}.sbom.cdx.json", f"images/kge-{VERSION}.tar",
               f"images/kge-portal-{VERSION}.digest", f"images/kge-portal-{VERSION}.sbom.cdx.json",
               f"images/kge-portal-{VERSION}.tar"}

NOTICE_FILES = {"notices/KGE_THIRD_PARTY_NOTICES.md", "notices/PORTAL_THIRD_PARTY_NOTICES.md"}


@pytest.mark.parametrize("variable", TRUST)
def test_an_image_that_trusts_a_build_time_authority_is_refused_naming_it(variable, tmp_path):
    done, out, calls = stand_in_build(tmp_path, f"{CLEAN}\n{variable}={CA}")
    assert done.returncode == 2, done.stdout + done.stderr
    assert f"kge:{VERSION}:{variable}" in done.stderr and "trust a certificate authority given at build time" in done.stderr
    assert "for local tests only" in done.stderr and "LOCAL_ONLY=1" in done.stderr
    assert any(c.startswith("build ") for c in calls) and not any(c.startswith("save ") for c in calls), \
        "refused after the build, before the image is saved"
    assert not list(out.iterdir()), "no kit left behind"


def test_local_only_lets_it_through_into_a_kit_marked_local_only(tmp_path):
    sandbox = f"{CLEAN}\nSSL_CERT_FILE={CA}\nREQUESTS_CA_BUNDLE={CA}\nPIP_CERT={CA}"
    done, out, calls = stand_in_build(tmp_path, sandbox, LOCAL_ONLY="1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [p.name for p in out.iterdir()] == [f"kge-{VERSION}-local-only"], "never under the shippable kit's name"
    kit = out / f"kge-{VERSION}-local-only"
    note = (kit / "LOCAL-ONLY").read_text()
    assert note.startswith("LOCAL ONLY. This kit is for local tests: never ship it")
    assert "The images set:" in note
    assert checked_sums(kit) == {"LOCAL-ONLY", "docs/INSTALL.md", "docs/ONBOARDING.md", f"kge-{VERSION}.zip", *NOTICE_FILES, *IMAGE_FILES}
    mark = "kge.local-only=for local tests only, never shipped (LOCAL_ONLY=1)"
    assert any(c.startswith("build ") and f"--label {mark} " in c for c in calls), "the image itself says so"
    assert sbom_marks(kit) == [{"name": "kge:local-only", "value": mark.split("=", 1)[1]}], "and its bill of materials"


def test_a_clean_image_goes_into_the_kit_unmarked(tmp_path):
    done, out, calls = stand_in_build(tmp_path, CLEAN)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [p.name for p in out.iterdir()] == [f"kge-{VERSION}"]
    assert checked_sums(out / f"kge-{VERSION}") == {"docs/INSTALL.md", "docs/ONBOARDING.md", f"kge-{VERSION}.zip", *NOTICE_FILES, *IMAGE_FILES}
    assert f"save -o {out / f'kge-{VERSION}'}/images/kge-{VERSION}.tar kge:{VERSION}" in calls
    assert any(c.startswith("build ") and f"--build-arg KGE_VERSION={VERSION} " in c for c in calls)
    assert not any("kge.local-only" in c for c in calls) and sbom_marks(out / f"kge-{VERSION}") == []
    assert not any("BASE_IMAGE" in c for c in calls), "each Dockerfile's own default base"


def given_bases(kit: Path, image: str) -> list[str]:
    bom = json.loads((kit / "images" / f"{image}-{VERSION}.sbom.cdx.json").read_text())
    return [p["value"] for c in bom["components"] if c["type"] == "container" for p in c["properties"]
            if p["name"] == "kge:base:given"]


def test_each_image_is_built_on_its_own_base_and_its_bill_of_materials_names_it(tmp_path):
    """KGE_BASE_IMAGE and PORTAL_BASE_IMAGE each reach their own image's build alone, after BUILD_FLAGS so they win,
    and each bill of materials records its own image's base, not the other's."""
    kge_base, portal_base = "registry.example/ubi9/python-312:9.6", "registry.example/node:22.20.0"
    done, out, calls = stand_in_build(tmp_path, CLEAN, KGE_BASE_IMAGE=kge_base, PORTAL_BASE_IMAGE=portal_base,
                                      BUILD_FLAGS="--build-arg UV_IMAGE=uv:local")
    assert done.returncode == 0, done.stdout + done.stderr
    builds = [c for c in calls if c.startswith("build ")]
    kge_build, portal_build = [b for b in builds if b.endswith(" .")], [b for b in builds if b.endswith(" portal")]
    assert len(kge_build) == len(portal_build) == 1 and len(builds) == 2
    assert f"--build-arg BASE_IMAGE={kge_base}" in kge_build[0] and portal_base not in kge_build[0]
    assert f"--build-arg BASE_IMAGE={portal_base}" in portal_build[0] and kge_base not in portal_build[0]
    for build_ in builds:
        assert build_.index("UV_IMAGE=uv:local") < build_.index("BASE_IMAGE="), "the base given wins over BUILD_FLAGS"
    kit = out / f"kge-{VERSION}"
    assert given_bases(kit, "kge") == [kge_base] and given_bases(kit, "kge-portal") == [portal_base]


def test_build_flags_naming_a_base_is_refused_for_the_per_image_settings(tmp_path):
    done, out, calls = stand_in_build(tmp_path, CLEAN, BUILD_FLAGS="--build-arg BASE_IMAGE=x:1")
    assert done.returncode == 2 and "KGE_BASE_IMAGE and PORTAL_BASE_IMAGE" in done.stderr
    assert not calls and not out.exists()


@pytest.fixture(scope="module")
def workflow() -> tuple[str, dict]:
    """One demo's pipeline, .github/workflows/kit-demo.yml, which the Release kit workflow calls once per demo with a kit."""
    text = (WORKFLOWS / "kit-demo.yml").read_text()
    return text, yaml.safe_load(text)


@pytest.fixture(scope="module")
def caller() -> tuple[str, dict]:
    """The Release kit workflow, .github/workflows/kit.yml: its triggers, the demos a run builds, and a call for each."""
    text = (WORKFLOWS / "kit.yml").read_text()
    return text, yaml.safe_load(text)


def steps(workflow: dict) -> list[tuple[str, dict]]:
    return [(name, step) for name, job in workflow["jobs"].items() for step in job.get("steps", [])]


def triggers(workflow: dict) -> dict:
    """The workflow's on key, which YAML 1.1 reads as the boolean true when it is bare."""
    return workflow.get("on", workflow.get(True))


def writes(permissions, scope: str | None = None) -> bool:
    """Whether the permissions grant write, to the scope given or to any; write-all grants every scope."""
    if isinstance(permissions, str):
        return "write" in permissions
    return any(v == "write" for k, v in (permissions or {}).items() if scope in (None, k))


KIT_RULE = "^kit::?([[:space:]]|$)"
NOTICES_RULE = "^kit-notices::?([[:space:]]|$)"
WORKFLOW_FILES = [".github/workflows/kit.yml", ".github/workflows/kit-demo.yml"]
DEMOS = ("kge", "ledger-ops")
TITLE = {"kge": "KGE", "ledger-ops": "LEDGER-OPS"}
FULL_LEGS = re.search(r"^FULL_LEGS := (.+)$", (DEMO / "Makefile").read_text(), re.M).group(1).split()


def test_the_release_kit_workflow_runs_on_a_demos_change_its_kit_tag_or_a_merge_and_reads(caller):
    """GitHub evaluates no path filter on a tag's push, so a kit tag still builds whatever it tags."""
    text, wf = caller
    assert wf["name"] == "Release kit"
    on = triggers(wf)
    assert set(on) == {"pull_request", "push", "workflow_dispatch"}
    assert sorted(on["pull_request"]["branches"]) == ["develop", "main"]
    assert on["pull_request"]["paths"] == ["demos/**", *WORKFLOW_FILES], "any demo, or either workflow file"
    push = on["push"]
    assert set(push) == {"branches", "tags", "paths"}, push
    assert push["branches"] == ["develop"] and push["tags"] == ["*-kit-v*"], "a merge into develop, or a demo's kit tag"
    assert push["paths"] == on["pull_request"]["paths"], "a merge publishes where its pull request built"
    for tag, matched in (("kge-kit-v0.6.1", True), ("kge-lite-kit-v1.0.0", True), ("v1.4.0", False), ("kge-v1", False)):
        assert fnmatch.fnmatchcase(tag, push["tags"][0]) is matched, f"{tag}: the release train's v* tags are not a kit's"
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"demo", "version"}
    assert inputs["version"]["required"] is True and inputs["version"]["type"] == "string"
    assert inputs["demo"]["required"] is False and inputs["demo"]["type"] == "string", \
        "a run by hand with a version alone builds the one demo with a kit, as before"
    assert wf["permissions"] == {"contents": "read"} and "env" not in wf
    assert "concurrency" not in wf, "no lock on the whole run: its discover job holds the run's, each demo's call its own"
    assert set(wf["jobs"]) == {"discover", "demo"}
    discover = wf["jobs"]["discover"]
    assert "permissions" not in discover, "the demos are found with the run's read-only token"
    assert "needs" not in discover, "it joins the run's group when the run is created, so in the order of the pushes"
    assert discover["runs-on"] == "ubuntu-latest" and discover["timeout-minutes"] == 5
    assert discover["outputs"] == {"demos": "${{ steps.demos.outputs.demos }}"}
    checkout, find = discover["steps"]
    assert checkout == {"uses": "actions/checkout@v7", "with": {"fetch-depth": 0, "persist-credentials": False}}, \
        "the whole history, to diff a push or a pull request, and git's credentials not left in the workspace"
    assert find["id"] == "demos" and find["env"] == {
        "EVENT": "${{ github.event_name }}", "REF": "${{ github.ref }}", "BEFORE": "${{ github.event.before }}",
        "INPUT": "${{ inputs.demo }}"}, find["env"]
    assert "${{" not in find["run"] and "token" not in yaml.safe_dump(find).lower(), \
        "nothing reaches the shell by interpolation, and no token"


def test_each_demo_with_a_kit_is_one_call_of_its_pipeline_independent_of_the_others(caller):
    text, wf = caller
    assert wf["jobs"]["demo"] == {
        "name": "${{ matrix.demo }}",
        "needs": "discover",
        "if": "${{ needs.discover.outputs.demos != '[]' }}",
        "strategy": {"fail-fast": False, "matrix": {"demo": "${{ fromJSON(needs.discover.outputs.demos) }}"}},
        "concurrency": {"group": "kit-${{ matrix.demo }}-${{ github.event_name }}-${{ github.ref }}",
                        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}"},
        "permissions": {"contents": "write", "actions": "write"},
        "uses": "./.github/workflows/kit-demo.yml",
        "with": {"demo": "${{ matrix.demo }}", "version": "${{ inputs.version }}"},
    }, wf["jobs"]["demo"]


def test_one_demos_pipeline_reads_and_only_its_release_job_writes_contents_on_its_kit_tag_or_a_merge(workflow):
    """A called workflow's job with no permissions of its own takes the workflow's; without contents: read at the top it
    would take the call's, contents: write and actions: write, in the jobs that run the repository's code and PyPI's."""
    text, wf = workflow
    assert wf["name"] == "Release kit, one demo"
    on = triggers(wf)
    assert set(on) == {"workflow_call"} and set(on["workflow_call"]) == {"inputs"}, "only ever called, and no secret"
    inputs = on["workflow_call"]["inputs"]
    assert set(inputs) == {"demo", "version"}
    assert inputs["demo"]["required"] is True and inputs["demo"]["type"] == "string"
    assert inputs["version"]["type"] == "string" and inputs["version"]["default"] == "" and not inputs["version"].get(
        "required")
    assert wf["permissions"] == {"contents": "read"}
    assert "env" not in wf and not writes(wf["permissions"])
    jobs = wf["jobs"]
    assert set(jobs) == {"notices", "kit", "full", "full-coverage", "release", "prune"}
    assert all("permissions" not in jobs[name] for name in ("notices", "kit", "full", "full-coverage")), \
        "the notices, kit and full-check jobs read"
    assert {name for name, job in jobs.items() if writes(job.get("permissions"), "contents")} == {"release"}
    assert {name for name, job in jobs.items() if writes(job.get("permissions"), "actions")} == {"prune"}
    assert {name for name, job in jobs.items() if writes(job.get("permissions"))} == {"release", "prune"}
    release = jobs["release"]
    assert release["permissions"] == {"contents": "write"} and \
        release["needs"] == ["notices", "kit", "full", "full-coverage"], \
        "a kit is published once its notices are current and its demo's full check has passed too: a merge publishes " \
        "with no one to say when"
    condition = " ".join(release["if"].split())
    assert condition == ("${{ github.event_name == 'push' && (startsWith(github.ref, format('refs/tags/{0}-kit-v', "
                         "inputs.demo)) || github.ref == 'refs/heads/develop') }}"), \
        "the demo's own kit tag's push or a merge into develop, alone"
    prefix = re.search(r"format\('([^']+)', inputs\.demo\)", condition).group(1)
    for demo, ref, published in (("kge", "refs/tags/kge-kit-v0.6.1", True), ("kge", "refs/tags/kge-lite-kit-v1.0.0", False),
                                 ("kge-lite", "refs/tags/kge-lite-kit-v1.0.0", True),
                                 ("ledger-ops", "refs/tags/kge-kit-v0.6.1", False)):
        assert ref.startswith(prefix.replace("{0}", demo)) is published, (demo, ref)
    assert jobs["prune"]["permissions"] == {"actions": "write"}, "the repository's Actions artifacts, and nothing else"
    assert all(job.get("timeout-minutes") for job in jobs.values())


def test_the_runs_token_goes_to_three_steps_of_a_demos_pipeline_alone(caller, workflow):
    for (text, wf), tokened in ((caller, []), (workflow, [("kit", "version"),
                                                          ("release", "The kit's release, every kit file attached"),
                                                          ("prune", "Delete the older kits")])):
        assert [(name, step.get("id", step.get("name"))) for name, step in steps(wf)
                if "github.token" in yaml.safe_dump(step)] == tokened, \
            "the run's token goes to the version step (read-only: it lists tags), the release step and the prune step alone"


def test_each_demos_publishing_runs_are_serialised_and_never_cancelled(caller, workflow):
    """One group per demo, event and ref, held by the demo's call, so by its whole pipeline from counting its patch to
    tagging it: develop's merges that touch a demo publish its kits one at a time, and no demo's group holds another's.

    The discover job needs nothing, so it joins the run's group when the run is created and the calls join their
    demos' groups after it: develop's merges are taken in the order they were pushed, an older merge's call never
    after a newer one's. A tag's run and a run by hand each have their own group; a run by hand publishes nothing, so
    it never supersedes a merge's."""
    (_, outer), (_, inner) = caller, workflow
    assert "concurrency" not in outer and "concurrency" not in inner
    assert [name for name, job in outer["jobs"].items() if "concurrency" in job] == ["discover", "demo"]
    assert not [name for name, job in inner["jobs"].items() if "concurrency" in job], "the call holds it, not one job"
    assert outer["jobs"]["demo"]["concurrency"] == {
        "group": "kit-${{ matrix.demo }}-${{ github.event_name }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}"}, \
        "a newer push cancels a pull request's build alone, never a publishing run in progress"
    assert "needs" not in outer["jobs"]["discover"] and outer["jobs"]["demo"]["needs"] == "discover"
    assert outer["jobs"]["discover"]["concurrency"] == {
        "group": "kit-${{ github.event_name }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}"}, \
        "the run's lock, as the whole run held it before: a merge's discover is never cancelled in progress"
    assert "kit" in inner["jobs"]["release"]["needs"] and inner["jobs"]["kit"]["outputs"]["version"] == \
        "${{ steps.version.outputs.version }}", "the version is counted inside the serialised call"


PUSH = re.compile(r"docker\s+(image\s+)?push|docker\s+login|buildx[^\n]*--push|type=registry|push=true", re.I)


def recipe(makefile: str, target: str) -> str:
    """A rule's recipe, its tab-indented lines after the target's line."""
    lines = makefile.splitlines()
    at = next(i for i, line in enumerate(lines) if re.match(rf"{target}::?(\s|$)", line))
    return "\n".join(itertools.takewhile(lambda line: line.startswith("\t"), lines[at + 1:]))


def test_the_kit_workflow_holds_no_secret_and_reaches_no_cloud(caller, workflow):
    for text, wf in (caller, workflow):
        assert not re.search(r"secrets\s*[.\[:]", text), "no secret, none passed to the call"
        assert "id-token" not in text, "no identity token, so no cloud sign-in"
        cloud = re.compile(r"gcloud|google-github-actions", re.I)
        for name, step in steps(wf):
            ran = f"{step.get('uses', '')}\n{step.get('run', '')}"
            assert not cloud.search(ran) and not PUSH.search(ran), (name, step)
            assert not re.search(r"LOCAL_ONLY|BUILD_FLAGS|IMAGES", yaml.safe_dump(step)), "the kit as make kit builds it"
        assert all("env" not in job for job in wf["jobs"].values())
    for wrong in ("docker push kge:1", "docker image push kge:1", "docker buildx build --push -t kge:1 .",
                  "docker build --output type=registry,name=kge:1 .", "docker login -u x", "--output=image,push=true"):
        assert PUSH.search(wrong), wrong
    kits = [d for d in sorted(DEMO.parent.iterdir()) if (d / "kit" / "VERSION").is_file() and (d / "Makefile").is_file()
            and re.search(r"^kit::?(\s|$)", (d / "Makefile").read_text(), re.M)]
    assert DEMO in kits
    for demo in kits:
        for name, ran in [("Makefile's kit rule", recipe((demo / "Makefile").read_text(), "kit")),
                          *((script.name, script.read_text()) for script in sorted((demo / "kit").glob("*.sh")))]:
            assert ran.strip() and not PUSH.search(ran), (demo.name, name)


def test_the_kit_workflow_runs_make_kit_and_uploads_the_kit_folder(workflow):
    text, wf = workflow
    kit = wf["jobs"]["kit"]
    assert kit["runs-on"] == "ubuntu-26.04", "pinned: the runner's Docker decides what docker save writes"
    assert kit["steps"][0] == {"uses": "actions/checkout@v7", "with": {"persist-credentials": False}}, \
        "git's credentials not left in the workspace while the demo's make kit runs"
    runs = [s.get("run", "") for s in kit["steps"]]
    make = [i for i, run in enumerate(runs) if re.search(r"^make kit VERSION=", run, re.M)]
    assert len(make) == 1 and kit["steps"][make[0]]["working-directory"] == "demos/${{ inputs.demo }}"
    store = [i for i, run in enumerate(runs) if "io.containerd.snapshotter.v1" in run]
    assert len(store) == 1 and store[0] < make[0], "Docker held to its containerd image store before make kit"
    sums = [i for i, run in enumerate(runs) if "sha256sum -c SHA256SUMS" in run]
    upload = [i for i, s in enumerate(kit["steps"]) if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(sums) == 1 and len(upload) == 1 and make[0] < sums[0] < upload[0]
    assert kit["steps"][sums[0]]["working-directory"] == "demos/${{ inputs.demo }}"
    assert kit["steps"][sums[0]]["env"] == {"DEMO": "${{ inputs.demo }}", "VERSION": "${{ steps.version.outputs.version }}"}
    assert runs[sums[0]] == 'cd "build/kit/$DEMO-$VERSION" && sha256sum -c SHA256SUMS'
    version = "${{ steps.version.outputs.version }}"
    given = kit["steps"][upload[0]]["with"]
    assert given["name"] == f"${{{{ inputs.demo }}}}-kit-{version}"
    assert given["path"].rstrip("/") == f"demos/${{{{ inputs.demo }}}}/build/kit/${{{{ inputs.demo }}}}-{version}"
    assert given["retention-days"] == "${{ github.event_name == 'push' && 1 || 30 }}", "a tag's kit is its release's"
    assert given["if-no-files-found"] == "error"
    release = wf["jobs"]["release"]["steps"]
    download = [s for s in release if s.get("uses", "").startswith("actions/download-artifact@")]
    assert len(download) == 1 and download[0]["with"]["name"] == "${{ inputs.demo }}-kit-${{ needs.kit.outputs.version }}"
    published = "\n".join(s.get("run", "") for s in release)
    assert "gh release view" in published and 'gh release create "$TAG" "${files[@]}"' in published
    assert "--latest=false" in published, "the repository's latest release stays the release train's"


RUNNER_TOOLS = {
    "docker": '#!/bin/sh\necho "docker $*" >> "$KGE_STAND_IN_LOG"\necho "$KGE_STORE"\n',
    "sudo": '#!/bin/sh\necho "sudo $*" >> "$KGE_STAND_IN_LOG"\n[ "$1" != cat ] || exit 1\n',
}
CONTAINERD = '[["driver-type","io.containerd.snapshotter.v1"]]'
CLASSIC = '[["Backing Filesystem","extfs"],["Supports d_type","true"],["Using metacopy","false"]]'


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("store, code", [(CONTAINERD, 0), (CLASSIC, 1)])
def test_the_store_step_turns_the_containerd_store_on_and_stops_without_it(workflow, tmp_path, store, code):
    text, wf = workflow
    step = next(s for s in wf["jobs"]["kit"]["steps"] if "io.containerd.snapshotter.v1" in s.get("run", ""))
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    for name, script in RUNNER_TOOLS.items():
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path), "KGE_STORE": store,
           "KGE_STAND_IN_LOG": str(log)}
    done = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == code, done.stdout + done.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "docker info --format {{json .DriverStatus}}"
    if store == CONTAINERD:
        assert len(calls) == 1, "on the containerd store already: nothing changed"
        return
    assert json.loads((tmp_path / "daemon.json").read_text()) == {"features": {"containerd-snapshotter": True}}
    assert f"sudo install -m 644 {tmp_path}/daemon.json /etc/docker/daemon.json" in calls
    assert "sudo systemctl restart docker" in calls and calls[-1].startswith("docker info"), "checked again after"
    assert "::error::Docker is not on its containerd image store" in done.stdout


STAND_IN_GH_PUBLISH = r"""#!@PYTHON@
import json, os, sys
args = sys.argv[1:]
demo = os.environ["DEMO"]
with open(os.environ["KGE_STAND_IN_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
with open(os.environ["KGE_GH_FIXTURE"]) as f:
    fixture = json.load(f)
if args == ["api", "--paginate", f"repos/acme/kge/git/matching-refs/tags/{demo}-kit-v", "--jq", ".[].ref"]:
    if fixture.get("refs") is None:
        print("gh: Server Error (HTTP 502)", file=sys.stderr)
        sys.exit(1)
    for ref in fixture["refs"]:
        print(ref)
elif len(args) == 4 and args[0] == "api" and args[1].startswith(f"repos/acme/kge/compare/{demo}-kit-v") \
        and args[1].endswith("..." + os.environ["GITHUB_SHA"]) and args[2:] == ["--jq", ".status"]:
    print(fixture["status"])
elif args[:2] == ["release", "view"] and args[3:] == ["--repo", "acme/kge"]:
    sys.exit(0 if args[2] in fixture.get("releases", []) else 1)
elif len(args) == 2 and args[0] == "api" and args[1].startswith("repos/acme/kge/git/ref/tags/"):
    tag = args[1].removeprefix("repos/acme/kge/git/ref/tags/")
    if tag not in fixture.get("tags", []):
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"ref": "refs/tags/" + tag}))
elif args[:2] != ["release", "create"]:
    print("stand-in gh: unexpected call: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
"""
SHA = "4f2c9e0b7d1a3c5e8f6b2d4a9c7e1f3b5d8a0c2e"


def listing(demo: str) -> list[str]:
    return ["api", "--paginate", f"repos/acme/kge/git/matching-refs/tags/{demo}-kit-v", "--jq", ".[].ref"]


def stand_in_gh(tmp_path: Path, fixture: dict, demo: str = "kge") -> tuple[dict, Path]:
    bin_dir, log, data = tmp_path / "bin", tmp_path / "gh.log", tmp_path / "gh.json"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(STAND_IN_GH_PUBLISH.replace("@PYTHON@", sys.executable))
    (bin_dir / "gh").chmod(0o755)
    data.write_text(json.dumps(fixture))
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "GH_TOKEN": "stand-in", "REPO": "acme/kge",
            "DEMO": demo, "KGE_STAND_IN_LOG": str(log), "KGE_GH_FIXTURE": str(data)}, log


def gh_calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def run_version(wf: dict, tmp_path: Path, event: str, ref: str, line: str | None = "0.6\n", refs=(),
                status: str = "ahead", demo: str = "kge", **given: str
                ) -> tuple[subprocess.CompletedProcess, str | None, list[list[str]]]:
    """The version step for the demo, run at SHA from a checkout holding its kit/VERSION as given (none when None) and
    another demo's line beside it, with a stand-in gh whose compare of a tag with SHA gives the status given."""
    step = next(s for s in wf["jobs"]["kit"]["steps"] if s.get("id") == "version")
    assert set(step["env"]) == {"DEMO", "EVENT", "REF", "BASE", "TAG", "PR", "RUN", "INPUT", "GH_TOKEN", "REPO"}, \
        step["env"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}" and step["env"]["REF"] == "${{ github.ref }}"
    assert step["env"]["BASE"] == "${{ github.base_ref }}"
    assert step["env"]["DEMO"] == "${{ inputs.demo }}" and step["env"]["INPUT"] == "${{ inputs.version }}"
    work = tmp_path / "work"
    (work / "demos" / demo / "kit").mkdir(parents=True)
    if line is not None:
        (work / "demos" / demo / "kit" / "VERSION").write_text(line)
    other = "kge-lite" if demo == "kge" else "kge"
    (work / "demos" / other / "kit").mkdir(parents=True)
    (work / "demos" / other / "kit" / "VERSION").write_text("9.9\n")
    env, log = stand_in_gh(tmp_path, {"refs": None if refs is None else list(refs), "status": status}, demo)
    output = tmp_path / "output"
    env.update({"GITHUB_OUTPUT": str(output), "GITHUB_SHA": SHA, "EVENT": event, "REF": ref, **given})
    done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, env=env, capture_output=True, text=True,
                          timeout=30)
    return done, output.read_text() if output.exists() else None, gh_calls(log)


def build_sh_accepts(version: str) -> bool:
    rule = re.search(r"grep -Eqx '([^']+)'", (KIT / "build.sh").read_text()).group(1)
    return re.fullmatch(rule, version) is not None


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("event, ref, given, version", [
    ("pull_request", "refs/pull/61/merge", {"PR": "61", "RUN": "12"}, "0.61.12"),
    ("push", "refs/tags/{d}-kit-v1.2.3", {"TAG": "{d}-kit-v1.2.3"}, "1.2.3"),
    ("workflow_dispatch", "refs/heads/develop", {"INPUT": "0.0.12"}, "0.0.12"),
])
def test_the_kit_workflow_names_a_version_build_sh_accepts(workflow, tmp_path, demo, event, ref, given, version):
    text, wf = workflow
    given = {k: v.format(d=demo) for k, v in given.items()}
    done, output, calls = run_version(wf, tmp_path, event, ref.format(d=demo), refs=[f"refs/tags/{demo}-kit-v0.6.0"],
                                      demo=demo, **given)
    assert done.returncode == 0, done.stdout + done.stderr
    assert output == f"version={version}\n"
    assert calls == ([listing(demo)] if event == "pull_request" else []), \
        "a pull request, a tag and a run by hand keep their versions; a pull request lists the tags to check its line"
    assert build_sh_accepts(version)


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("refs, version, newest", [
    ([], "0.6.0", None),
    (["refs/tags/{d}-kit-v0.6.0", "refs/tags/{d}-kit-v0.6.1"], "0.6.2", "0.6.1"),
    (["refs/tags/{d}-kit-v0.6.2", "refs/tags/{d}-kit-v0.6.10", "refs/tags/{d}-kit-v0.6.9"], "0.6.11", "0.6.10"),
    (["refs/tags/{d}-kit-v0.5.0", "refs/tags/{d}-kit-v0.5.7", "refs/tags/{d}-kit-v0.0.60",
      "refs/tags/{d}-kit-v0.6.4-rc1", "refs/tags/{d}-kit-v0.6",
      "refs/tags/my-{d}-kit-v0.6.5", "refs/heads/{d}-kit-v0.6.6"], "0.6.0", "0.5.7"),
])
def test_a_merge_into_develop_publishes_the_next_patch_of_the_line_in_kit_version(workflow, tmp_path, demo, refs, version,
                                                                                 newest):
    text, wf = workflow
    refs = [r.format(d=demo) for r in refs]
    done, output, calls = run_version(wf, tmp_path, "push", "refs/heads/develop", refs=refs, demo=demo, TAG="develop")
    assert done.returncode == 0, done.stdout + done.stderr
    assert output == f"version={version}\n"
    compare = [["api", f"repos/acme/kge/compare/{demo}-kit-v{newest}...{SHA}", "--jq", ".status"]] if newest else []
    assert calls == [listing(demo), *compare], \
        "the kit tags listed once, and the demo's highest compared with the merge, with reads: no write, no other call"
    assert build_sh_accepts(version)


@pytest.mark.parametrize("demo, refs, version", [
    ("kge", ["refs/tags/kge-lite-kit-v0.6.9", "refs/tags/ledger-ops-kit-v0.6.3", "refs/tags/kge-kit-v0.6.1"], "0.6.2"),
    ("kge", ["refs/tags/kge-lite-kit-v0.6.9", "refs/tags/kge-kit-lite-v0.6.4"], "0.6.0"),
    ("kge-lite", ["refs/tags/kge-kit-v0.6.5", "refs/tags/kge-lite-kit-v0.6.0"], "0.6.1"),
    ("ledger-ops", ["refs/tags/kge-kit-v0.6.5", "refs/tags/ops-kit-v0.6.7", "refs/tags/ledger-kit-v0.6.2"], "0.6.0"),
])
def test_a_merge_counts_its_patch_from_its_own_demos_tags_alone(workflow, tmp_path, demo, refs, version):
    text, wf = workflow
    done, output, calls = run_version(wf, tmp_path, "push", "refs/heads/develop", refs=refs, demo=demo, TAG="develop")
    assert done.returncode == 0 and output == f"version={version}\n", done.stdout + done.stderr
    assert calls[0] == listing(demo), "the demo's own tags listed"
    if version.endswith(".0"):
        assert calls == [listing(demo)], "no other demo's tag is compared"
    else:
        assert calls[1] == ["api", f"repos/acme/kge/compare/{demo}-kit-v0.6.{int(version[-1]) - 1}...{SHA}", "--jq",
                            ".status"]


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("status, published", [
    ("ahead", True),
    ("diverged", True),
    ("behind", False),
    ("identical", False),
])
def test_a_rerun_of_an_older_merges_run_publishes_nothing_its_lines_newest_kit_carries(workflow, tmp_path, demo, status,
                                                                                      published):
    """A merge diverged from the newest kit when that kit was tagged by hand off develop, so it does not carry the merge;
    a merge behind it is a re-run of an older merge's run after a later merge published, so it carries the merge."""
    text, wf = workflow
    refs = [f"refs/tags/{demo}-kit-v0.6.0", f"refs/tags/{demo}-kit-v0.6.1", "refs/tags/kge-lite-kit-v0.6.7"]
    done, output, calls = run_version(wf, tmp_path, "push", "refs/heads/develop", refs=refs, status=status, demo=demo,
                                      TAG="develop")
    assert calls == [listing(demo), ["api", f"repos/acme/kge/compare/{demo}-kit-v0.6.1...{SHA}", "--jq", ".status"]], \
        "the newest of the demo's own tags on the line is compared"
    if published:
        assert done.returncode == 0 and output == "version=0.6.2\n", done.stdout + done.stderr
    else:
        assert done.returncode == 1 and output is None, "no version, so no build and no release"
        assert f"::error::{demo}-kit-v0.6.1 is at {SHA} or after it" in done.stdout, done.stdout


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("status, published", [
    ("ahead", True),
    ("diverged", True),
    ("behind", False),
    ("identical", False),
])
def test_a_rerun_after_the_line_moved_publishes_nothing_the_demos_highest_kit_in_any_line_carries(workflow, tmp_path,
                                                                                                  demo, status, published):
    """The merge is compared with the demo's highest kit by number, in whichever line, not only its own line's: a line
    moved to 0.7 publishes 0.7.0 after 0.6's newest, unless that kit carries the merge; and once 0.7.0 is published, a
    re-run of an older merge's run, whose checkout still reads 0.6, publishes nothing."""
    text, wf = workflow
    refs = [f"refs/tags/{demo}-kit-v0.6.0", f"refs/tags/{demo}-kit-v0.6.10", f"refs/tags/{demo}-kit-v0.6.9",
            "refs/tags/kge-lite-kit-v0.9.0"]
    done, output, calls = run_version(wf, tmp_path / "moved", "push", "refs/heads/develop", line="0.7\n", refs=refs,
                                      status=status, demo=demo, TAG="develop")
    assert calls == [listing(demo), ["api", f"repos/acme/kge/compare/{demo}-kit-v0.6.10...{SHA}", "--jq", ".status"]], \
        "the demo's highest kit, of the line below, is compared"
    if published:
        assert done.returncode == 0 and output == "version=0.7.0\n", done.stdout + done.stderr
    else:
        assert done.returncode == 1 and output is None, "no version, so no build and no release"
        assert f"::error::{demo}-kit-v0.6.10 is at {SHA} or after it" in done.stdout, done.stdout
    done, output, calls = run_version(wf, tmp_path / "rerun", "push", "refs/heads/develop", line="0.6\n",
                                      refs=[*refs, f"refs/tags/{demo}-kit-v0.7.0"], status="behind", demo=demo,
                                      TAG="develop")
    assert done.returncode == 1 and output is None, done.stdout + done.stderr


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("event, ref, given", [
    ("pull_request", "refs/pull/61/merge", {"PR": "61", "RUN": "12", "BASE": "develop"}),
    ("push", "refs/heads/develop", {"TAG": "develop"}),
])
@pytest.mark.parametrize("line, published, refused, version", [
    ("0.5", ["0.5.0", "0.6.0"], "0.6", None),
    ("0.6", ["0.6.1", "0.60.0"], "0.60", None),
    ("0.6", ["0.6.1", "10.6.3"], "10.6", None),
    ("0.9", ["0.10.0"], "0.10", None),
    ("0.6", ["0.5.7", "0.6.1"], None, "0.6.2"),
    ("0.7", ["0.5.7", "0.6.1"], None, "0.7.0"),
    ("1.0", ["0.6.1"], None, "1.0.0"),
    ("0.6", [], None, "0.6.0"),
])
def test_a_kit_line_below_the_demos_highest_published_line_is_refused_on_a_pull_request_and_a_merge(
        workflow, tmp_path, demo, event, ref, given, line, published, refused, version):
    """A line only moves up: one below the line of the demo's highest kit fails the pull request that brings it, and a
    merge's publish; another demo's higher line does not count."""
    text, wf = workflow
    refs = [f"refs/tags/{demo}-kit-v{v}" for v in published] + ["refs/tags/kge-lite-kit-v9.9.0"]
    done, output, calls = run_version(wf, tmp_path, event, ref, line=line + "\n", refs=refs, demo=demo, **given)
    assert calls[0] == listing(demo), "the demo's tags listed with the run's read-only token"
    if refused:
        assert done.returncode == 1 and output is None, done.stdout + done.stderr
        assert f"::error::demos/{demo}/kit/VERSION holds {line}, below {refused}, the highest line {demo} has " \
               f"published" in done.stdout and "a kit line only moves up" in done.stdout, done.stdout
        assert calls == [listing(demo)], "refused before anything is compared"
    else:
        assert done.returncode == 0, done.stdout + done.stderr
        assert output == f"version={'0.61.12' if event == 'pull_request' else version}\n", output


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("line", ["0.5", "0.6"])
def test_a_pull_request_into_main_is_let_through_below_the_demos_highest_published_line(workflow, tmp_path, demo, line):
    """main's line catches up with develop's only when a docs release is promoted, so a hotfix into main that touches
    the demo, its line behind develop's, still builds its test kit."""
    text, wf = workflow
    refs = [f"refs/tags/{demo}-kit-v0.6.3", f"refs/tags/{demo}-kit-v0.7.0"]
    done, output, calls = run_version(wf, tmp_path, "pull_request", "refs/pull/81/merge", line=line + "\n", refs=refs,
                                      demo=demo, PR="81", RUN="5", BASE="main")
    assert done.returncode == 0 and output == "version=0.81.5\n", done.stdout + done.stderr
    assert calls == [listing(demo)]


MALFORMED_LINES = ["0.6.0\n", "v0.6\n", "0.6 \n", "0.6\r\n", "0.6\n0.7\n", "six\n", "00.06\n", "0.06\n", "0.0\n",
                   "\n", "", None]


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("line", MALFORMED_LINES)
def test_a_malformed_release_line_is_refused(workflow, tmp_path, demo, line):
    text, wf = workflow
    done, output, calls = run_version(wf, tmp_path, "push", "refs/heads/develop", line=line, demo=demo, TAG="develop")
    assert done.returncode != 0 and output is None, done.stdout + done.stderr
    if line is not None:
        assert f"::error::demos/{demo}/kit/VERSION" in done.stdout and "MAJOR.MINOR" in done.stdout, done.stdout
    assert calls == [], "refused before any tag is listed"


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("line", MALFORMED_LINES)
def test_a_pull_request_refuses_the_malformed_release_line_its_merge_would(workflow, tmp_path, demo, line):
    """A kit/VERSION that is not MAJOR.MINOR fails the pull request that brings it, before its merge fails to publish."""
    text, wf = workflow
    done, output, calls = run_version(wf, tmp_path, "pull_request", "refs/pull/61/merge", line=line, demo=demo,
                                      PR="61", RUN="12")
    assert done.returncode != 0 and output is None, done.stdout + done.stderr
    if line is not None:
        assert f"::error::demos/{demo}/kit/VERSION" in done.stdout and "MAJOR.MINOR" in done.stdout, done.stdout
    assert calls == [], "refused before any tag is listed"


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("line", ["0.6\n", "0.6", "1.0\n", "12.34\n"])
def test_a_pull_requests_version_is_its_build_number_whatever_its_line(workflow, tmp_path, demo, line):
    text, wf = workflow
    done, output, calls = run_version(wf, tmp_path, "pull_request", "refs/pull/61/merge", line=line, demo=demo,
                                      PR="61", RUN="12")
    assert done.returncode == 0 and output == "version=0.61.12\n", done.stdout + done.stderr
    assert calls == [listing(demo)], "the tags listed to check the line, and nothing else"


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("event, ref, given, version", [
    ("push", "refs/tags/{d}-kit-v1.2.3", {"TAG": "{d}-kit-v1.2.3"}, "1.2.3"),
    ("workflow_dispatch", "refs/heads/develop", {"INPUT": "0.0.3"}, "0.0.3"),
])
def test_a_kit_tag_and_a_run_by_hand_keep_their_versions_whatever_the_line(workflow, tmp_path, demo, event, ref, given,
                                                                          version):
    """The line is not theirs: a tag names its version and a run by hand is given one, so neither reads kit/VERSION."""
    text, wf = workflow
    given = {k: v.format(d=demo) for k, v in given.items()}
    for line in ("v0.6\n", None):
        done, output, calls = run_version(wf, tmp_path / str(line is None), event, ref.format(d=demo), line=line,
                                          demo=demo, **given)
        assert done.returncode == 0 and output == f"version={version}\n", done.stdout + done.stderr
        assert calls == []


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("given", ["0.6.1", "1.0.0", "0.1.0", "0.0.03", "00.0.3", "0.0", "0.0.3.1", "0.0.x", ""])
def test_a_run_by_hand_refuses_a_version_inside_a_kit_line(workflow, tmp_path, demo, given):
    """A run by hand takes 0.0.<n>, outside every kit line, so that its artifact never shares a kit's number."""
    text, wf = workflow
    done, output, calls = run_version(wf, tmp_path, "workflow_dispatch", "refs/heads/develop", demo=demo, INPUT=given)
    assert done.returncode == 1 and output is None, done.stdout + done.stderr
    assert f"::error::A run by hand was given '{given}': its version is 0.0.<n>, outside every kit line" in done.stdout, \
        done.stdout
    assert calls == []


@pytest.mark.parametrize("demo", DEMOS)
def test_a_merge_whose_tags_cannot_be_listed_gets_no_version(workflow, tmp_path, demo):
    text, wf = workflow
    done, output, calls = run_version(wf, tmp_path, "push", "refs/heads/develop", refs=None, demo=demo, TAG="develop")
    assert done.returncode != 0 and output is None, "never a guessed patch"
    assert calls == [listing(demo)]


def test_the_release_line_is_two_numbers_and_stays_out_of_the_kit(kit):
    line = (KIT / "VERSION").read_text()
    assert re.fullmatch(r"\d+\.\d+\n", line), line
    assert build_sh_accepts(line.strip() + ".0")
    assert not [n for n in names(kit) if n.startswith("kit/")] and (kit / "SHA256SUMS").exists(), \
        "the zip's VERSION is the kit's own version; kit/VERSION is the repository's"


def run_release(wf: dict, tmp_path: Path, ref: str, version: str, releases=(), tags=(), demo: str = "kge"
                ) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    """The release step for the demo, on a downloaded kit of two files, with a stand-in gh."""
    (step,) = [s for s in wf["jobs"]["release"]["steps"] if "run" in s]
    assert step["env"] == {"DEMO": "${{ inputs.demo }}", "GH_TOKEN": "${{ github.token }}",
                           "REPO": "${{ github.repository }}", "REF": "${{ github.ref }}",
                           "TAG": "${{ inputs.demo }}-kit-v${{ needs.kit.outputs.version }}",
                           "VERSION": "${{ needs.kit.outputs.version }}"}, step["env"]
    kit = tmp_path / "work" / "kit"
    (kit / "docs").mkdir(parents=True)
    (kit / f"{demo}-{version}.zip").write_bytes(b"the zip")
    (kit / "docs" / "INSTALL.md").write_text("the guide\n")
    (kit / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((kit / n).read_bytes()).hexdigest()}  {n}\n"
                                            for n in ("docs/INSTALL.md", f"{demo}-{version}.zip")))
    env, log = stand_in_gh(tmp_path, {"releases": list(releases), "tags": list(tags)}, demo)
    env.update({"REF": ref, "TAG": f"{demo}-kit-v{version}", "VERSION": version, "GITHUB_SHA": SHA,
                "RUNNER_TEMP": str(tmp_path)})
    done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path / "work", env=env, capture_output=True,
                          text=True, timeout=30)
    return done, gh_calls(log)


def created(calls: list[list[str]]) -> list[str]:
    (create,) = [c for c in calls if c[:2] == ["release", "create"]]
    return create


@pytest.mark.parametrize("demo", DEMOS)
def test_a_merges_release_is_tagged_by_the_workflow_at_the_merge_it_built(workflow, tmp_path, demo):
    text, wf = workflow
    done, calls = run_release(wf, tmp_path, "refs/heads/develop", "0.6.1", demo=demo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls[:2] == [["release", "view", f"{demo}-kit-v0.6.1", "--repo", "acme/kge"],
                         ["api", f"repos/acme/kge/git/ref/tags/{demo}-kit-v0.6.1"]], "no release, and no tag, before"
    create = created(calls)
    assert create[:6] == ["release", "create", f"{demo}-kit-v0.6.1", "SHA256SUMS", "docs/INSTALL.md", f"{demo}-0.6.1.zip"]
    assert create[create.index("--target") + 1] == SHA and "--verify-tag" not in create, "tagged at the merge built"
    assert "--latest=false" in create and create[create.index("--title") + 1] == f"{TITLE[demo]} release kit 0.6.1"
    notes = Path(create[create.index("--notes-file") + 1]).read_text()
    assert notes.startswith(f"The {TITLE[demo]} release kit 0.6.1, built from {SHA} by the Release kit workflow.")
    assert f"cd {demo}-0.6.1 && sha256sum -c SHA256SUMS" in notes
    assert f"mkdir -p {demo}-0.6.1/docs && mv INSTALL.md {demo}-0.6.1/docs/INSTALL.md" in notes


def test_kges_release_is_titled_as_before(workflow, tmp_path):
    text, wf = workflow
    done, calls = run_release(wf, tmp_path, "refs/heads/develop", "0.6.1")
    create = created(calls)
    assert create[2] == "kge-kit-v0.6.1" and create[create.index("--title") + 1] == "KGE release kit 0.6.1"
    notes = Path(create[create.index("--notes-file") + 1]).read_text()
    assert notes.startswith("The KGE release kit 0.6.1, built from") and "cd kge-0.6.1 && sha256sum -c SHA256SUMS" in notes


@pytest.mark.parametrize("demo", DEMOS)
def test_a_tags_release_is_of_the_tag_pushed_as_today(workflow, tmp_path, demo):
    text, wf = workflow
    done, calls = run_release(wf, tmp_path, f"refs/tags/{demo}-kit-v1.2.3", "1.2.3", tags=[f"{demo}-kit-v1.2.3"],
                              demo=demo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not [c for c in calls if c[0] == "api"], "the tag is the one pushed: not looked up, not made"
    create = created(calls)
    assert create[2] == f"{demo}-kit-v1.2.3" and "--verify-tag" in create and "--target" not in create
    assert "--latest=false" in create


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize("ref, version, releases, tags", [
    ("refs/heads/develop", "0.6.1", ["{d}-kit-v0.6.1"], ["{d}-kit-v0.6.1"]),
    ("refs/tags/{d}-kit-v1.2.3", "1.2.3", ["{d}-kit-v1.2.3"], ["{d}-kit-v1.2.3"]),
    ("refs/heads/develop", "0.6.1", [], ["{d}-kit-v0.6.1"]),
])
def test_a_version_published_already_or_tagged_meanwhile_is_refused(workflow, tmp_path, demo, ref, version, releases,
                                                                    tags):
    text, wf = workflow
    done, calls = run_release(wf, tmp_path, ref.format(d=demo), version, releases=[r.format(d=demo) for r in releases],
                              tags=[t.format(d=demo) for t in tags], demo=demo)
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"::error::{demo}-kit-v{version} " in done.stdout and "published once" in done.stdout, done.stdout
    assert not [c for c in calls if c[:2] == ["release", "create"]], "nothing created"


def test_the_prune_job_checks_out_nothing_and_never_deletes_this_runs_kit(workflow):
    text, wf = workflow
    prune = wf["jobs"]["prune"]
    assert set(prune["needs"]) == {"kit", "release"}, "after the kit's upload, and on a merge or a tag the release job"
    condition = " ".join(prune["if"].split())
    assert condition == ("${{ !cancelled() && needs.kit.result == 'success' && (github.event_name != 'pull_request' || "
                         "(github.event.pull_request.head.repo.full_name == github.repository && "
                         "github.actor != 'dependabot[bot]')) }}"), \
        "once the kit is uploaded, whether a release ran or not, on every merge, tag and run by hand; skipped only on a " \
        "pull request where GitHub keeps the token read-only"
    assert prune["runs-on"] == "ubuntu-latest" and prune["timeout-minutes"] <= 15
    (step,) = prune["steps"]
    assert "uses" not in step and "working-directory" not in step, "no checkout, no action, no file of the repository"
    assert step["env"] == {"DEMO": "${{ inputs.demo }}", "GH_TOKEN": "${{ github.token }}",
                           "REPO": "${{ github.repository }}", "RUN_ID": "${{ github.run_id }}"}, \
        "the run's own token, and no secret"
    run = step["run"]
    assert not re.search(r"secrets|\bmake\b|python|\bsh\b|\./|demos/|tools/|\$GITHUB_WORKSPACE|\$\{\{", run), \
        "no code of the repository runs, and nothing reaches the shell by interpolation"
    calls = re.findall(r"\bgh api (?:-X DELETE )?\"([^\"]+)\"", run)
    assert calls and all(c.startswith("repos/$REPO/actions/") for c in calls), calls
    assert len(re.findall(r"\bgh\b", run)) == len(calls), "gh api alone, no other gh command"
    assert '--arg prefix "$DEMO-kit-"' in run and "(.name | startswith($prefix))" in run, "the demo's own kits alone"
    assert ".workflow_run.id < $current" in run, "earlier runs' kits alone"


STAND_IN_GH = '''#!{python}
import json, os, re, sys
args = sys.argv[1:]
with open(os.environ["KGE_STAND_IN_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")
with open(os.environ["KGE_GH_FIXTURE"]) as f:
    fixture = json.load(f)
call = " ".join(args)
if m := re.fullmatch(r"api repos/acme/kge/actions/artifacts\\?per_page=100&page=(\\d+)", call):
    page = int(m.group(1))
    pages = fixture["pages"]
    print(json.dumps({{"total_count": sum(map(len, pages)), "artifacts": pages[page - 1] if page <= len(pages) else []}}))
elif m := re.fullmatch(r"api repos/acme/kge/actions/runs/(\\d+)", call):
    print(json.dumps({{"id": int(m.group(1)), **fixture["runs"][m.group(1)]}}))
elif m := re.fullmatch(r"api -X DELETE repos/acme/kge/actions/artifacts/(\\d+)", call):
    code = fixture.get("refuse", {{}}).get(m.group(1))
    if code:
        print(json.dumps({{"message": "Refused"}}))
        print(f"gh: Refused (HTTP {{code}})", file=sys.stderr)
        sys.exit(1)
else:
    print(f"stand-in gh: unexpected call: {{call}}", file=sys.stderr)
    sys.exit(2)
'''


def artifact(id_: int, name: str, run: int, expired: bool = False) -> dict:
    return {"id": id_, "name": name, "size_in_bytes": 1024, "expired": expired,
            "workflow_run": {"id": run, "head_branch": "feature/x"}}


def run_(status: str, event: str = "pull_request", conclusion: str | None = "success") -> dict:
    return {"status": status, "event": event, "conclusion": conclusion if status == "completed" else None}


OTHER_KITS = {"kge": ["kge-lite-kit-0.1.0", "ledger-ops-kit-0.2.0"], "ledger-ops": ["ledger-ops-lite-kit-0.1.0", "kge-kit-0.6.3"]}


def pages(demo: str) -> list[list[dict]]:
    """This run is 900. Page one is full, a hundred artifacts, so page two is read; page two is short, so page three is
    not."""
    return [
        [artifact(1, f"{demo}-kit-0.65.30", 900),
         artifact(2, f"{demo}-kit-0.65.29", 899),
         artifact(3, f"{demo}-kit-0.64.12", 850),
         artifact(4, "coverage", 880),
         artifact(5, f"{demo}-kit-0.63.1", 700, True),
         artifact(6, f"{demo}-kit-1.0.0", 895),
         artifact(7, f"{demo}-kit-1.0.1", 890),
         artifact(8, f"{demo}-kit-0.66.3", 950),
         artifact(9, f"{demo}-kit-0.67.1", 960),
         artifact(10, OTHER_KITS[demo][0], 899),
         artifact(11, OTHER_KITS[demo][1], 850),
         *[artifact(i, f"lint-{i}", 100 + i) for i in range(12, 101)]],
        [artifact(101, f"{demo}-kit-0.61.4", 600),
         artifact(102, f"{demo}-kit-0.62.2", 650),
         artifact(103, f"my-{demo}-kit-0.1.1", 640),
         artifact(104, f"{demo}-kit", 630),
         artifact(105, f"{demo}-kit-0.9.0", 620)],
    ]


RUNS = {"899": run_("completed"), "850": run_("completed"), "895": run_("in_progress", "push"),
        "890": run_("completed", "push", "failure"), "950": run_("completed"), "960": run_("in_progress"),
        "600": run_("completed", conclusion="failure"), "650": run_("completed"), "620": run_("completed", "push")}


def run_prune(workflow: dict, tmp_path: Path, refuse: dict[str, int], demo: str = "kge"
              ) -> tuple[subprocess.CompletedProcess, list[str]]:
    (step,) = workflow["jobs"]["prune"]["steps"]
    bin_dir, log, fixture = tmp_path / "bin", tmp_path / "calls.log", tmp_path / "fixture.json"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(STAND_IN_GH.format(python=sys.executable))
    (bin_dir / "gh").chmod(0o755)
    fixture.write_text(json.dumps({"pages": pages(demo), "runs": RUNS, "refuse": refuse}))
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path), "GH_TOKEN": "stand-in",
           "REPO": "acme/kge", "RUN_ID": "900", "DEMO": demo, "KGE_STAND_IN_LOG": str(log),
           "KGE_GH_FIXTURE": str(fixture)}
    done = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=60)
    return done, log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("demo", DEMOS)
def test_the_prune_step_deletes_the_older_kits_across_pages_and_keeps_the_current_one(workflow, tmp_path, demo):
    text, wf = workflow
    done, calls = run_prune(wf, tmp_path, refuse={"102": 404}, demo=demo)
    assert done.returncode == 0, done.stdout + done.stderr
    listed = [c for c in calls if "/actions/artifacts?" in c]
    assert listed == [f"api repos/acme/kge/actions/artifacts?per_page=100&page={n}" for n in (1, 2)], "paging followed"
    deletes = [c for c in calls if c.startswith("api -X DELETE ")]
    assert calls.index(deletes[0]) > calls.index(listed[-1]), "every page read before the first delete"
    assert {int(c.rsplit("/", 1)[1]) for c in deletes} == {2, 3, 101, 102, 105}, "the demo's older kits alone"
    looked_up = {c.rsplit("/", 1)[1] for c in calls if "/actions/runs/" in c}
    assert looked_up == set(RUNS) - {"950", "960"}, \
        "an earlier kit's run is looked up before its kit is deleted; this run's and a later run's never"
    assert not [name for name in OTHER_KITS[demo] if name in done.stdout], "another demo's kit is never even considered"
    assert f"Kept {demo}-kit-1.0.0: its run, 895, is in_progress" in done.stdout
    assert f"Kept {demo}-kit-1.0.1: its run, 890, a merge's or a tag's, ended failure" in done.stdout
    assert f"{demo}-kit-0.62.2 was already gone." in done.stdout
    assert "Older kits deleted: 4. This run's kit (run 900) is kept." in done.stdout


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("demo", DEMOS)
def test_the_prune_step_stops_on_a_delete_github_refuses(workflow, tmp_path, demo):
    text, wf = workflow
    done, calls = run_prune(wf, tmp_path, refuse={"3": 403}, demo=demo)
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"::error::GitHub refused to delete {demo}-kit-0.64.12 (artifact 3)." in done.stdout
    assert "gh: Refused (HTTP 403)" in done.stdout, "gh's own words, so the run says why"


SHELLS = (["bash", "-e", "-c"], ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"])
GUIDE = "".join(f"Line {n} of a guide long enough for git to see a move of it as a rename.\n" for n in range(40))
DEMO_TREE = {
    "demos/README.md": "The demos.\n",
    "demos/kge/Makefile": "PY ?= python3\n\n.PHONY: kit\n\nkit:\n\tsh kit/build.sh\n\nkit-notices:\n\t$(PY) x.py --check\n",
    "demos/kge/kit/VERSION": "0.6\n",
    "demos/kge/guide.md": GUIDE,
    "demos/kge-lite/Makefile": "kit: lite\n\t@echo lite\n",
    "demos/kge-lite/kit/VERSION": "1.0\n",
    "demos/ledger-ops/Makefile": "kit::\n\t@echo ops\n",
    "demos/ledger-ops/kit/VERSION": "0.2\n",
    "demos/notes/README.md": "Notes, with no kit.\n",
    "demos/half/Makefile": "kit-notices:\n\t@echo no kit rule\nkits:\n\t@echo nor is this\n#kit:\n",
    "demos/half/kit/VERSION": "0.1\n",
    "demos/only-rule/Makefile": "kit:\n\t@echo no line\n",
    "demos/vector/Makefile": "kit:\n\t@echo v\n",
    "demos/vector/kit/VERSION": "0.1\n",
    "demos/kge-kit-x/Makefile": "kit:\n\t@echo x\n",
    "demos/kge-kit-x/kit/VERSION": "0.1\n",
    ".github/workflows/kit.yml": "name: Release kit\n",
    ".github/workflows/kit-demo.yml": "name: Release kit, one demo\n",
    "docs/readme.md": "Docs.\n",
}
KITS = ["kge", "kge-lite", "ledger-ops"]
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], env={**os.environ, **GIT_ENV}, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(repo: Path, files: dict[str, str | None], message: str = "a change") -> str:
    """Writes the files (None deletes one; a (path) value moves the file to it) and commits them; the commit."""
    for name, content in files.items():
        if content is None:
            git(repo, "rm", "-q", name)
        elif isinstance(content, tuple):
            (repo / content[0]).parent.mkdir(parents=True, exist_ok=True)
            git(repo, "mv", name, content[0])
        else:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def demo_repo(tmp_path: Path, tree: dict[str, str] = DEMO_TREE) -> Path:
    """A scratch repository on develop holding the tree, by default DEMO_TREE's demos.

    In DEMO_TREE, vector and kge-kit-x have kits under names the convention refuses: a v first is the release train's
    tags', and a kit word in a demo's name makes another demo's tags and artifacts look like kge's."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "develop")
    commit(repo, tree, "the demos")
    return repo


def pull_request(repo: Path, change: dict) -> None:
    """Leaves the repository at a pull request's merge ref, as GitHub checks it out: develop, moved on since the branch
    left it (a change to ledger-ops), merged with the branch's change."""
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, change, "the pull request's change")
    git(repo, "checkout", "-q", "develop")
    commit(repo, {"demos/ledger-ops/since.md": "develop moved on\n"}, "develop moved on")
    git(repo, "checkout", "-q", "--detach", "develop")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "feature")


def discover_step(caller: tuple[str, dict]) -> dict:
    return next(s for s in caller[1]["jobs"]["discover"]["steps"] if s.get("id") == "demos")


def discover(caller, repo: Path, tmp_path: Path, shell: list[str], event: str, ref: str, before: str = "",
             given: str = "", sha: str | None = None) -> tuple[subprocess.CompletedProcess, list[str] | None]:
    """The step's result and the demos it chose (None when it wrote none)."""
    output = tmp_path / "output"
    output.unlink(missing_ok=True)
    temp = tmp_path / "runner-temp"
    temp.mkdir(exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), **GIT_ENV, "GITHUB_OUTPUT": str(output),
           "GITHUB_SHA": sha or git(repo, "rev-parse", "HEAD"), "RUNNER_TEMP": str(temp), "EVENT": event, "REF": ref,
           "BEFORE": before, "INPUT": given}
    done = subprocess.run([*shell, discover_step(caller)["run"]], cwd=repo, env=env, capture_output=True, text=True,
                          timeout=60)
    lines = output.read_text().splitlines() if output.exists() else []
    found = [json.loads(line.removeprefix("demos=")) for line in lines if line.startswith("demos=")]
    assert len(found) <= 1, lines
    return done, found[0] if found else None


def warned(done: subprocess.CompletedProcess) -> set[str]:
    return set(re.findall(r"^::warning::demos/([^ :]+)", done.stdout, re.M))


MANY = {f"docs/many/{n}.md": f"{n}\n" for n in range(3000)}


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("shell", SHELLS, ids=["bash -e", "pipefail"])
@pytest.mark.parametrize("change, chosen", [
    ({"demos/kge/README.md": "kge\n"}, ["kge"]),
    ({"demos/kge-lite/README.md": "lite\n"}, ["kge-lite"]),
    ({"demos/kge/kit/VERSION": "0.7\n", "demos/ledger-ops/x.md": "x\n"}, ["kge", "ledger-ops"]),
    ({"demos/kge/guide.md": None}, ["kge"]),
    ({"demos/kge/guide.md": ("docs/guide.md",)}, ["kge"]),
    ({"demos/kge/café.md": "x\n"}, ["kge"]),
    ({'demos/kge/say "hi".md': "x\n"}, ["kge"]),
    ({".github/workflows/kit-demo.yml": "name: changed\n"}, KITS),
    ({".github/workflows/kit.yml": "name: changed\n", **MANY}, KITS),
    ({".github/workflows/checks.yml": "x\n", "demos/notes/README.md": "y\n"}, []),
    ({"demos/notes/README.md": "more notes\n"}, []),
    ({"demos/half/README.md": "x\n", "demos/only-rule/x.md": "x\n", "demos/vector/x.md": "x\n",
      "demos/kge-kit-x/x.md": "x\n"}, []),
    ({"docs/readme.md": "Docs, changed.\n", "demos/README.md": "Changed.\n"}, []),
])
def test_a_pull_request_builds_the_kits_of_the_demos_it_changes(caller, tmp_path, shell, change, chosen):
    repo = demo_repo(tmp_path)
    pull_request(repo, change)
    done, demos = discover(caller, repo, tmp_path, shell, "pull_request", "refs/pull/7/merge")
    assert done.returncode == 0, done.stdout + done.stderr
    assert demos == chosen, "the pull request's own changes, as GitHub's filter sees them: not develop's since"
    assert warned(done) == {"half", "only-rule", "vector", "kge-kit-x"}, done.stdout
    assert ("nothing to build" in done.stdout) is (chosen == [])


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("shell", SHELLS, ids=["bash -e", "pipefail"])
def test_a_merge_builds_the_kits_of_the_demos_its_push_changes(caller, tmp_path, shell):
    repo = demo_repo(tmp_path)
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"demos/kge-lite/a.md": "a\n"})
    sha = commit(repo, {"demos/ledger-ops/b.md": "b\n"})
    done, demos = discover(caller, repo, tmp_path, shell, "push", "refs/heads/develop", before=before)
    assert done.returncode == 0 and demos == ["kge-lite", "ledger-ops"], done.stdout + done.stderr
    done, demos = discover(caller, repo, tmp_path, shell, "push", "refs/heads/develop", before=git(repo, "rev-parse", "HEAD^"))
    assert done.returncode == 0 and demos == ["ledger-ops"], "from the push's previous commit to its own"
    commit(repo, {".github/workflows/kit.yml": "name: changed\n"})
    done, demos = discover(caller, repo, tmp_path, shell, "push", "refs/heads/develop", before=sha)
    assert done.returncode == 0 and demos == KITS, done.stdout + done.stderr
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"demos/kge/guide.md": ("docs/guide.md",)})
    done, demos = discover(caller, repo, tmp_path, shell, "push", "refs/heads/develop", before=before)
    assert done.returncode == 0 and demos == ["kge"], "a file moved out of a demo changes it: " + done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("shell", SHELLS, ids=["bash -e", "pipefail"])
@pytest.mark.parametrize("before", ["0" * 40, "", "1234abcd" * 5, "develop^{tree}"])
def test_a_merge_whose_previous_commit_is_not_here_builds_nothing(caller, tmp_path, shell, before):
    repo = demo_repo(tmp_path)
    commit(repo, {"demos/kge/a.md": "a\n"})
    done, demos = discover(caller, repo, tmp_path, shell, "push", "refs/heads/develop", before=before)
    assert done.returncode != 0 and demos is None, done.stdout + done.stderr
    assert "::error::" in done.stdout and "no demo is guessed" in done.stdout, done.stdout


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("shell", SHELLS, ids=["bash -e", "pipefail"])
@pytest.mark.parametrize("tag, chosen, error", [
    ("kge-kit-v0.6.1", ["kge"], None),
    ("ledger-ops-kit-v1.2.3", ["ledger-ops"], None),
    ("kge-lite-kit-v1.0.0", ["kge-lite"], None),
    ("notes-kit-v1.0.0", None, "notes-kit-v1.0.0 names no demo with a kit"),
    ("half-kit-v0.1.0", None, "half-kit-v0.1.0 names no demo with a kit"),
    ("vector-kit-v0.1.0", None, "vector-kit-v0.1.0 names no demo with a kit"),
    ("kge-kit-x-kit-v0.1.0", None, "kge-kit-x-kit-v0.1.0 names no demo with a kit"),
    ("kge-kit-v1.2", None, "kge-kit-v1.2 is not <demo>-kit-vX.Y.Z"),
    ("kge-kit-v1.2.3-rc1", None, "kge-kit-v1.2.3-rc1 is not <demo>-kit-vX.Y.Z"),
    ("-kit-v1.2.3", None, "-kit-v1.2.3 is not <demo>-kit-vX.Y.Z"),
])
def test_a_kit_tag_builds_the_demo_it_names_at_the_commit_it_tags(caller, tmp_path, shell, tag, chosen, error):
    repo = demo_repo(tmp_path)
    done, demos = discover(caller, repo, tmp_path, shell, "push", f"refs/tags/{tag}")
    assert demos == chosen, done.stdout + done.stderr
    if error:
        assert done.returncode != 0 and f"::error::{error}" in done.stdout, done.stdout
    else:
        assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("shell", SHELLS, ids=["bash -e", "pipefail"])
@pytest.mark.parametrize("given, chosen", [
    ("ledger-ops", ["ledger-ops"]),
    ("kge", ["kge"]),
    ("notes", None),
    ("half", None),
    ("kge/../ledger-ops", None),
    ("", None),
])
def test_a_run_by_hand_builds_the_demo_named(caller, tmp_path, shell, given, chosen):
    repo = demo_repo(tmp_path)
    done, demos = discover(caller, repo, tmp_path, shell, "workflow_dispatch", "refs/heads/develop", given=given)
    assert demos == chosen, done.stdout + done.stderr
    if chosen is None:
        assert done.returncode != 0 and "::error::" in done.stdout, done.stdout
        assert "kge kge-lite ledger-ops" in done.stdout, "the demos with a kit, named"
    else:
        assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="no jq on the PATH")
@pytest.mark.parametrize("shell", SHELLS, ids=["bash -e", "pipefail"])
def test_in_this_repository_kge_is_the_one_demo_with_a_kit(caller, tmp_path, shell):
    """Today's demos, their Makefiles and kit lines as they are: a change to the workflow builds and publishes KGE's kit
    alone, and a run by hand with a version alone builds it, as before."""
    tree = {f"demos/{d.name}/{name}": (d / name).read_text() for d in sorted(DEMO.parent.iterdir()) if d.is_dir()
            for name in ("Makefile", "kit/VERSION") if (d / name).is_file()}
    tree.update({f: (DEMO.parents[1] / f).read_text() for f in WORKFLOW_FILES})
    repo = demo_repo(tmp_path, tree)
    done, demos = discover(caller, repo, tmp_path, shell, "workflow_dispatch", "refs/heads/develop")
    assert done.returncode == 0 and demos == ["kge"], done.stdout + done.stderr
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {".github/workflows/kit-demo.yml": tree[".github/workflows/kit-demo.yml"] + "# changed\n"})
    done, demos = discover(caller, repo, tmp_path, shell, "push", "refs/heads/develop", before=before)
    assert done.returncode == 0 and demos == ["kge"], done.stdout + done.stderr
    assert "::warning::" not in done.stdout, "every demo here is a kit's or has none of one"


def test_the_kit_rules_are_found_as_the_convention_says(caller, workflow):
    """One form for both rules, a target at the start of a line with one colon or two; KGE's Makefile has each once."""
    assert f"'{KIT_RULE}'" in discover_step(caller)["run"]
    assert f"'{NOTICES_RULE}'" in notices_step(workflow)["run"]
    assert NOTICES_RULE == KIT_RULE.replace("^kit:", "^kit-notices:")
    for rule, target in ((KIT_RULE, "kit"), (NOTICES_RULE, "kit-notices")):
        found = subprocess.run(["grep", "-Ec", rule, str(DEMO / "Makefile")], capture_output=True, text=True)
        assert found.stdout.strip() == "1", f"KGE's Makefile has one {target} rule"


def notices_step(workflow: tuple[str, dict]) -> dict:
    return next(s for s in workflow[1]["jobs"]["notices"]["steps"] if s.get("id") == "has")


def test_the_notices_check_runs_for_a_demo_that_has_one_and_its_release_waits_for_it(workflow):
    text, wf = workflow
    notices = wf["jobs"]["notices"]
    assert notices["name"] == "Third-party notices are current" and "permissions" not in notices
    assert notices["runs-on"] == "ubuntu-26.04" and notices["timeout-minutes"] == 10
    checkout, has, python, node, check = notices["steps"]
    assert checkout == {"uses": "actions/checkout@v7", "with": {"persist-credentials": False}}, \
        "git's credentials not left in the workspace while the demo's rule installs packages and runs its code"
    assert has["name"] == "Whether the demo has a notices check" and has["env"] == {"DEMO": "${{ inputs.demo }}"}
    assert python == {"uses": "actions/setup-python@v7", "if": "steps.has.outputs.notices == 'yes'",
                      "with": {"python-version": "3.12"}}
    assert node == {"uses": "actions/setup-node@v7", "if": "steps.has.outputs.notices == 'yes'",
                    "with": {"node-version": "22"}}, "the portal's npm notices are checked with node"
    assert check == {"name": "The image's dependencies, then the notices against them",
                     "if": "steps.has.outputs.notices == 'yes'", "working-directory": "demos/${{ inputs.demo }}",
                     "run": "make kit-notices PY=python"}
    assert wf["jobs"]["release"]["needs"] == ["notices", "kit", "full", "full-coverage"], \
        "a demo's release waits for its notices check, and for its full check: every leg and the coverage gate"
    assert not [name for name, job in wf["jobs"].items() if "continue-on-error" in job], \
        "a failed notices check (or build) stops the release: no job's failure is let through"


def test_a_kit_publishes_only_after_its_demos_full_check_which_runs_on_the_publishing_pushes_alone(workflow):
    text, wf = workflow
    jobs = wf["jobs"]
    full, gate, release = jobs["full"], jobs["full-coverage"], jobs["release"]
    assert full["name"] == "The full check, ${{ matrix.leg }}" and "permissions" not in full and "needs" not in full, \
        "its legs read with the run's token, beside the kit's build"
    assert gate["name"] == "The full check's coverage" and "permissions" not in gate and gate["needs"] == "full", \
        "the coverage is combined once every leg has passed, with the run's read-only token"
    for job in (full, gate):
        assert " ".join(job["if"].split()) == " ".join(release["if"].split()), \
            "on the pushes that publish, the demo's kit tag or a merge into develop, and never on a pull request's run"
    for job in (gate, release):
        assert not re.search(r"success\(\)|always\(\)|cancelled\(\)|failure\(\)", job["if"]), \
            "no status function, so a failed or skipped leg or coverage gate stops the publish"
    assert full["strategy"] == {"fail-fast": False, "matrix": {"leg": FULL_LEGS}}, \
        "every leg of the demo's full check, each on a runner of its own, run to its end"
    assert full["runs-on"] == "ubuntu-24.04" and 30 <= full["timeout-minutes"] <= 90
    assert gate["runs-on"] == "ubuntu-latest" and gate["timeout-minutes"] <= 30
    checkout, has, python, deps, check, upload = full["steps"]
    assert checkout == {"uses": "actions/checkout@v7", "with": {"persist-credentials": False}}
    assert has["id"] == "has" and has["env"] == {"DEMO": "${{ inputs.demo }}"} and "${{" not in has["run"]
    assert python == {"uses": "actions/setup-python@v7", "if": "steps.has.outputs.full == 'yes'",
                      "with": {"python-version": "3.12"}}
    assert deps["if"] == check["if"] == "steps.has.outputs.full == 'yes'"
    assert check["working-directory"] == "demos/${{ inputs.demo }}"
    assert check["run"] == 'make full-check-leg PY=python LEG="$LEG"'
    assert check["env"] == {"MODEL_PROVIDER": "none", "LEG": "${{ matrix.leg }}"}, \
        "no model is called, and the leg reaches the shell through the environment"
    assert upload["uses"] == "actions/upload-artifact@v7" and upload["with"]["overwrite"] is True, \
        "a re-run of a failed leg replaces its own data, so the retry can publish"
    assert upload["with"]["include-hidden-files"] is True
    g_checkout, g_has, g_python, g_deps, g_download, g_combine, g_upload = gate["steps"]
    assert g_checkout == checkout and g_has["id"] == "has" and g_has["env"] == has["env"]
    assert g_python == python and "${{" not in g_has["run"]
    assert all(step["if"] == "steps.has.outputs.full == 'yes'" for step in (g_deps, g_download, g_combine))
    assert g_download["uses"] == "actions/download-artifact@v8"
    assert g_download["with"]["pattern"] == "${{ inputs.demo }}-full-coverage-*"
    assert upload["with"]["name"] == "${{ inputs.demo }}-full-coverage-${{ strategy.job-index }}"
    assert g_combine["env"]["LEGS"] == "${{ needs.full.result }}" and '[ "$LEGS" = success ]' in g_combine["run"], \
        "the gate says again that every leg passed"
    assert g_combine["run"].rstrip().endswith("make coverage-check PY=python MIN=70"), "held to 70%"
    assert len([leg for leg in FULL_LEGS if leg != "parity-margin"]) == 14 and '[ "$legs" -eq 14 ]' in g_combine["run"], \
        "every measured leg, all but parity and the margin, left data, as Checks' KGE full check asks"
    assert g_upload["uses"] == "actions/upload-artifact@v7" and g_upload["with"]["overwrite"] is True
    for name in (upload["with"]["name"], g_upload["with"]["name"]):
        assert not fnmatch.fnmatchcase(name.replace("${{ inputs.demo }}", "kge"), "kge-kit-*"), \
            "no coverage data or report is taken for a kit, nor deleted as one by the prune job"
    found = subprocess.run(["grep", "-Ec", r"^full-check::?([[:space:]]|$)", str(DEMO / "Makefile")],
                           capture_output=True, text=True)
    assert found.stdout.strip() == "1", "KGE's Makefile has one full-check rule"


@pytest.mark.parametrize("legs, passes", [(14, True), (13, False), (0, False)])
def test_the_kit_coverage_gate_fails_when_a_measured_leg_left_no_data(workflow, tmp_path, legs, passes):
    """The full-coverage job's combine step, run with a stand-in make: fourteen legs' data combine; one missing fails it
    before the coverage is judged, as an expired artifact would otherwise let a part pass for the whole."""
    combine = next(s for s in workflow[1]["jobs"]["full-coverage"]["steps"] if "coverage-check" in s.get("run", ""))
    data, work, bin_dir = tmp_path / "data", tmp_path / "work", tmp_path / "bin"
    for path in (work, bin_dir):
        path.mkdir()
    for i in range(legs):
        (data / f"kge-full-coverage-{i}").mkdir(parents=True)
        (data / f"kge-full-coverage-{i}" / ".coverage").write_text("x")
    (bin_dir / "make").write_text("#!/bin/sh\necho made\n")
    (bin_dir / "make").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "DATA": str(data), "LEGS": "success"}
    done = subprocess.run(["bash", "-e", "-c", combine["run"]], cwd=work, env=env, capture_output=True, text=True,
                          timeout=30)
    assert (done.returncode == 0) is passes, done.stdout + done.stderr
    assert ("made" in done.stdout) is passes, "the gate never judges a part"


def test_no_checkout_in_checks_or_the_kit_workflow_keeps_gits_credentials(workflow):
    """Nothing in either workflow pushes with git, so no checkout leaves its token in the workspace."""
    for wf in (workflow[1], yaml.safe_load((WORKFLOWS / "checks.yml").read_text())):
        checkouts = [step for _, step in steps(wf) if step.get("uses", "").startswith("actions/checkout@")]
        assert checkouts and all(step["with"]["persist-credentials"] is False for step in checkouts), checkouts


def test_the_walkthroughs_parts_are_the_whole_walkthrough_each_step_once_one_part_to_an_e2e_leg():
    """The full check's e2e legs, make full-check-leg LEG=e2e-i/N, each run one part of the Demo script
    (tests/e2e/demo_script.cjs's PARTS): together every one of its steps, step 14 as its two halves, and the portal
    alone, none dropped or run twice, each part in the README's order, and as many parts as e2e legs; the lane's tests
    run with part 1 alone. In a row step 14 runs both halves, one after the other."""
    src = (DEMO / "tests" / "e2e" / "demo_script.cjs").read_text()
    steps = re.search(r"^const STEPS = \[\n(.*?)^\];", src, re.M | re.S).group(1)
    count = len(re.findall(r"^  \['", steps, re.M))
    assert count == 15
    block = re.search(r"^const PARTS = \[\n(.*?)^\];", src, re.M | re.S).group(1)
    parts = [[int(x) if x.isdigit() else x.strip("'") for x in re.split(r",\s*", row)]
             for row in re.findall(r"^  \[([^\]]+)\],$", block, re.M)]
    assert "const HALVES = { 14: [['14a', semanticOsi], ['14b', semanticMarkdown]] };" in src
    whole = src.split("async function semanticBusinessUnit() {", 1)[1].split("\n}", 1)[0]
    assert whole.split() == ["await", "semanticOsi();", "await", "semanticMarkdown();"], "in a row, both halves"
    pieces = [*range(1, 14), "14a", "14b", 15, "portal"]
    dealt = [x for part in parts for x in part]
    assert sorted(dealt, key=str) == sorted(pieces, key=str) and len(dealt) == len(pieces), \
        "every step, each half of step 14 and the portal in exactly one part"
    for part in parts:
        assert [pieces.index(x) for x in part] == sorted(pieces.index(x) for x in part), \
            "a part takes its steps in the README's order"
    assert {10, 11, 12, 13} <= set(next(p for p in parts if 10 in p)), "steps 12 and 13 ask what step 10 onboards"
    legs = [leg for leg in FULL_LEGS if leg.startswith("e2e")]
    assert legs == [f"e2e-{i}/{len(parts)}" for i in range(1, len(parts) + 1)], "one e2e leg to a part"
    make = (DEMO / "Makefile").read_text()
    test_e2e = make.split("\ntest-e2e:", 1)[1].split("\n\n", 1)[0]
    assert "ifeq ($(filter-out 1/%,$(PART)),)\n\t$(PY) -m pytest tests/e2e" in test_e2e and "demo-script PART=$(PART)" in test_e2e
    assert "PART=$(PART) node tests/e2e/demo_script.cjs" in make.split("\ndemo-script:", 1)[1].split("\n\n", 1)[0]
    leg = make.split("\nfull-check-leg:", 1)[1].split("\n\n", 1)[0]
    assert "test-e2e PART=$(patsubst e2e-%,%,$(LEG))" in leg


def test_a_ready_pull_request_runs_the_full_check_in_checks_and_checks_passed_requires_it(workflow):
    """Checks runs KGE's full check on a pull request into develop that is not a draft and changes the demo or its
    workflows: the unit shards and the other legs side by side, one job combining their coverage at 70%, and Checks
    passed failing unless it succeeded wherever it was due. The full-check label's workflow is gone."""
    assert FULL_LEGS == ["unit-1/4", "unit-2/4", "unit-3/4", "unit-4/4", "integration-1/6", "integration-2/6",
                         "integration-3/6", "integration-4/6", "integration-5/6", "integration-6/6", "parity-margin",
                         "e2e-1/4", "e2e-2/4", "e2e-3/4", "e2e-4/4"]
    assert not (WORKFLOWS / "full-check.yml").exists()
    text = (WORKFLOWS / "checks.yml").read_text()
    wf = yaml.safe_load(text)
    on = triggers(wf)
    assert on["pull_request"]["types"] == ["opened", "synchronize", "reopened", "ready_for_review"]
    assert "paths" not in on["pull_request"], "no paths filter: a workflow never started leaves Checks passed waiting"
    assert wf["permissions"] == {"contents": "read"} and "env" not in wf
    assert "id-token" not in text
    jobs = wf["jobs"]
    scope, legs, full, unit, gate = (jobs[n] for n in ("kge-full-scope", "kge-full-legs", "kge-full", "kge-unit", "gate"))
    assert not re.search(r"secrets\s*[.\[]|token", yaml.safe_dump([scope, legs, full])), "no secret and no token"
    assert " ".join(scope["if"].split()) == ("${{ github.event_name == 'pull_request' && github.base_ref == 'develop' "
                                             "&& github.event.pull_request.draft == false }}"), "a ready pull request"
    assert scope["steps"][0] == {"uses": "actions/checkout@v7", "with": {"fetch-depth": 2, "persist-credentials": False}}
    pattern = re.search(r"grep -Eq '([^']+)'", scope["steps"][1]["run"]).group(1)
    for path, due in (("demos/kge/kge/app.py", True), ("demos/kge/Makefile", True), (".github/workflows/checks.yml", True),
                      (".github/workflows/kit.yml", True), (".github/workflows/kit-demo.yml", True),
                      (".github/workflows/release.yml", False), ("demos/kge-lite/x", False), ("docs/a.md", False)):
        assert bool(re.search(pattern, path)) is due, path
    assert unit["strategy"]["matrix"]["shard"] == [1, 2, 3, 4] and \
        [f"unit-{i}/4" for i in unit["strategy"]["matrix"]["shard"]] == [leg for leg in FULL_LEGS if leg.startswith("unit-")]
    assert "make test-unit PY=python SHARD=${{ matrix.shard }}/4" in [s.get("run") for s in unit["steps"]], \
        "the unit legs are the KGE unit tests shards"
    assert legs["needs"] == "kge-full-scope" and legs["if"] == "${{ needs.kge-full-scope.outputs.run == 'yes' }}"
    assert legs["strategy"] == {"fail-fast": False,
                                "matrix": {"leg": [leg for leg in FULL_LEGS if not leg.startswith("unit-")]}}
    assert [s["run"] for s in legs["steps"] if "full-check-leg" in s.get("run", "")] == \
        ['make full-check-leg PY=python LEG="$LEG"']
    assert full["needs"] == ["kge-full-scope", "kge-unit", "kge-full-legs"] and full["name"] == "KGE full check"
    assert full["if"] == "${{ !cancelled() && needs.kge-full-scope.outputs.run == 'yes' }}"
    combine = next(s for s in full["steps"] if "make coverage-check PY=python MIN=70" in s.get("run", ""))
    assert '[ "$shards" -eq 4 ] && [ "$legs" -eq 10 ]' in combine["run"], "every unit shard and measured leg left data"
    last = full["steps"][-1]
    assert last["env"] == {"UNIT": "${{ needs.kge-unit.result }}", "LEGS": "${{ needs.kge-full-legs.result }}"}
    assert '[ "$UNIT" = success ] && [ "$LEGS" = success ]' in last["run"], "a shard or leg that did not pass fails it"
    for job in (scope, legs, full):
        assert "permissions" not in job and "env" not in job and "continue-on-error" not in job and job["timeout-minutes"]
        assert job["steps"][0]["with"]["persist-credentials"] is False
        assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", s["uses"]) for s in job["steps"] if "uses" in s)
    assert {"kge-full-scope", "kge-full"} <= set(gate["needs"]) and gate["if"] == "${{ always() }}"
    step = gate["steps"][0]
    assert step["env"]["KGE_FULL"] == "${{ needs.kge-full.result }}"
    assert step["env"]["KGE_FULL_DUE"] == "${{ needs.kge-full-scope.outputs.run }}"
    assert step["env"]["KGE_FULL_SCOPE"] == "${{ needs.kge-full-scope.result }}"
    shell = shutil.which("bash")
    for scope_result, due, result, passes in (("success", "yes", "success", True), ("success", "yes", "failure", False),
                                              ("success", "yes", "skipped", False), ("success", "yes", "cancelled", False),
                                              ("success", "no", "skipped", True), ("skipped", "", "skipped", True),
                                              ("failure", "", "skipped", False)):
        env = {**{k: "success" for k in step["env"]}, "SOURCE": "skipped", "KGE_FULL_SCOPE": scope_result,
               "KGE_FULL_DUE": due, "KGE_FULL": result, "PATH": os.environ["PATH"]}
        done = subprocess.run([shell, "-e", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=30)
        assert (done.returncode == 0) is passes, (scope_result, due, result, done.stdout)


@pytest.mark.parametrize("makefile, has", [
    ("kit-notices:\n\t$(PY) x.py --check\n", True),
    ("PY ?= python3\nkit:\n\tsh b.sh\nkit-notices: deps\n\t$(PY) x.py --check\n", True),
    ("kit-notices::\n\t$(PY) x.py --check\n", True),
    ("kit:\n\tsh b.sh\n", False),
    ("kit-notices-all:\n\t$(PY) x.py\n#kit-notices:\n.PHONY: kit-notices\n", False),
    (None, True),
])
def test_the_notices_check_is_found_in_the_demos_makefile_and_its_absence_said(workflow, tmp_path, makefile, has):
    work = tmp_path / "work"
    (work / "demos" / "toy").mkdir(parents=True)
    if makefile is None:
        shutil.copy(DEMO / "Makefile", work / "demos" / "toy" / "Makefile")
    else:
        (work / "demos" / "toy" / "Makefile").write_text(makefile)
    output = tmp_path / "output"
    done = subprocess.run(["bash", "-e", "-c", notices_step(workflow)["run"]], cwd=work, capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], "DEMO": "toy", "GITHUB_OUTPUT": str(output)}, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (output.read_text() if output.exists() else "") == ("notices=yes\n" if has else "")
    assert ("::warning::toy has no kit-notices target: nothing to check" in done.stdout) is not has, done.stdout


def test_kges_notices_check_covers_both_images(tmp_path):
    """KGE's kit-notices rule: the images' dependencies installed, then both notices checked against them, as the notices
    job ran them before the workflow served every demo; make kit-notices PY=python runs them with the runner's Python."""
    lines = (DEMO / "Makefile").read_text().splitlines()
    at = next(i for i, line in enumerate(lines) if re.match(r"kit-notices::?(\s|$)", line))
    recipe = []
    for line in lines[at + 1:]:
        if not line.startswith("\t"):
            break
        recipe.append(line[1:])
    assert recipe == ["$(PY) -m pip install --quiet -r requirements.txt -c constraints.txt",
                      "$(PY) kit/generate_third_party_notices.py --check",
                      "node kit/generate_npm_notices.mjs --check"]
    phony = next(line for line in lines if line.startswith(".PHONY:")).split()
    assert "kit-notices" in phony and "kit" in phony
    if shutil.which("make"):
        env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
        done = subprocess.run(["make", "-n", "kit-notices", "PY=python"], cwd=DEMO, capture_output=True, text=True,
                              timeout=60, env={**env, "PROFILE": ""})
        assert done.returncode == 0, done.stderr
        assert done.stdout.splitlines()[-3:] == [
            "python -m pip install --quiet -r requirements.txt -c constraints.txt",
            "python kit/generate_third_party_notices.py --check",
            "node kit/generate_npm_notices.mjs --check"], done.stdout


@pytest.mark.skipif(not shutil.which("make"), reason="make is not installed")
@pytest.mark.parametrize("version, found", [(None, "none"), ("v22.5.1", "22.5.1"), ("v20.19.0", "20.19.0")])
def test_make_portal_refuses_a_node_before_22_12_and_installs_nothing(tmp_path, version, found):
    """make portal, with a stand-in node and npm first on the PATH: a node that is missing (it answers nothing) or older
    than 22.12 is refused, naming what it found, before npm is ever run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "npm-calls"
    node = f'#!/bin/sh\necho "{version}"\n' if version else "#!/bin/sh\nexit 127\n"
    for name, body in (("node", node), ("npm", f'#!/bin/sh\necho "$@" >> "{calls}"\n')):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
    done = subprocess.run(["make", "portal", f"PY={sys.executable}"], cwd=DEMO, capture_output=True, text=True, timeout=60,
                          env={**env, "PROFILE": "", "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"})
    assert done.returncode == 2, done.stdout + done.stderr
    assert f"make portal needs Node.js 22.12 or later (found: {found})" in done.stderr
    assert not calls.exists(), "nothing installed"


def started(monkeypatch, *argv: str, needs: str | None = None, **env: str) -> tuple[int, list, list]:
    """kge <argv> (what make <argv> now runs) from the demo's folder, in this process, with the servers it would start
    recorded and not started, the portal's build recorded and not run, and each Python step a stand-in that records it:
    (its exit code, each server's (args, folder, environment), the steps and builds it ran). needs, where given, stands
    in for what the portal lacks here; else the node first on the PATH answers."""
    from kge import cli
    from kge.cli import servers
    monkeypatch.chdir(DEMO)
    for name in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS", "PORT", "HOST", "PORTAL_PORT", "KGE_URL", "PORTAL_SYNC_TOKEN", "SIGNIN",
                 "DEMO_MODE", "HASH", "API"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROFILE", "")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    specs, ran = [], []
    real = subprocess.run

    def run(args, **kwargs):
        if list(args[:2]) == [sys.executable, "-m"]:
            ran.append(list(args[2:]))
            return subprocess.CompletedProcess(args, 0, "")
        return real(args, **kwargs)

    monkeypatch.setattr(servers, "supervise", lambda given: specs.extend(given) or 0)
    monkeypatch.setattr(servers, "build", lambda commands: ran.append(["portal-build"]))
    if needs is not None:
        monkeypatch.setattr(servers, "needs", lambda: needs)
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", run)
        code = cli.main(list(argv))
    return code, specs, ran


def test_make_portal_serves_the_built_ui_on_portal_port_against_kge_url_with_sqlite_under_build(monkeypatch):
    """make portal is a phony target whose last step starts the portal's server, as its own child, on PORTAL_PORT
    (90 above PORT, 8090 beside 8000) calling KGE_URL (the page make serve runs on PORT), its UI the one it built and its SQLite under build/portal/."""
    from kge.cli import servers
    lines = (DEMO / "Makefile").read_text().splitlines()
    assert "portal" in next(line for line in lines if line.startswith(".PHONY:")).split()
    assert "PORTAL_PORT ?= $(shell expr $(PORT) + 90 2>/dev/null)" in lines and "KGE_URL ?= http://127.0.0.1:$(PORT)" in lines
    files = []
    monkeypatch.setattr(servers, "token", lambda commands: files.append(commands.get("PORTAL_SYNC_FILE")) or "from-the-file")
    code, specs, ran = started(monkeypatch, "portal", "PORT=8123")
    assert code == 0 and ran == [["portal-build"]]
    (args, folder, env), = specs
    assert len(files) == 1 and re.fullmatch(re.escape(str(DEMO)) + r"/build/(portal|stores/[a-z][a-z0-9-]*)/run/portal_sync_token",
                                            str(DEMO / files[0])), files
    assert Path(args[0]).name in ("node", "node.exe") and args[1:] == ["--experimental-sqlite", "src/index.js"], args
    assert folder == Path("portal") / "server"
    assert {name: env[name] for name in ("PORTAL_SYNC_TOKEN", "PORT", "KGE_URL", "PORTAL_BACKEND", "PORTAL_UI_PATH",
                                         "PORTAL_SQLITE_PATH")} == {
        "PORTAL_SYNC_TOKEN": "from-the-file", "PORT": "8213", "KGE_URL": "http://127.0.0.1:8123", "PORTAL_BACKEND": "sqlite",
        "PORTAL_UI_PATH": "../ui/build", "PORTAL_SQLITE_PATH": f"{DEMO}/build/portal/portal.db"}


@pytest.mark.parametrize("target", ["serve", "portal"])
def test_make_serve_and_portal_keep_an_environment_sync_token_without_reading_or_creating_a_file(monkeypatch, tmp_path, target):
    token_file = tmp_path / "portal_sync_token"
    code, specs, _ = started(monkeypatch, target, f"PORTAL_SYNC_FILE={token_file}", PORTAL_SYNC_TOKEN="environment-token")
    assert code == 0
    (args, _, env), = specs
    assert env["PORTAL_SYNC_TOKEN"] == "environment-token"
    assert not any(str(token_file) in part for part in args)
    assert not token_file.exists()


def test_entrypoint_serve_passes_an_environment_sync_token_to_kge_unchanged(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen"
    stand_in = bin_dir / "kge"
    stand_in.write_text(f'#!/bin/sh\nprintf "%s\\n" "$PORTAL_SYNC_TOKEN" > "{seen}"\n')
    stand_in.chmod(0o755)
    token = "entrypoint-environment-token-unchanged"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PORTAL_SYNC_TOKEN": token}
    done = subprocess.run(["sh", str(DEMO / "entrypoint.sh"), "serve"], cwd=DEMO, env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert seen.read_text() == f"{token}\n"


def _stand_ins(tmp_path, node_version):
    """A bin folder of stand-ins, first on the PATH: python (as PY) and npm record each call, node answers --version."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    record = f'#!/bin/sh\necho "$(basename "$0") SIGNIN=$SIGNIN DEMO_MODE=$DEMO_MODE $*" >> "{calls}"\n'
    node = f'#!/bin/sh\necho "{node_version}"\n' if node_version else "#!/bin/sh\nexit 127\n"
    for name, body in (("python", record), ("npm", record), ("node", node)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
    return bin_dir, calls, {**env, "PROFILE": "", "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}


@pytest.mark.parametrize("version, found", [(None, "none"), ("v22.5.1", "22.5.1")])
def test_make_demo_without_node_22_12_serves_kge_alone_and_says_the_portal_was_skipped(monkeypatch, capsys, tmp_path, version, found):
    """make demo with no Node, or one before 22.12 (stand-ins on the PATH, the build already made): one line says the
    portal was skipped and why, and the page is served alone, with password sign-in beside demo mode; npm never runs."""
    bin_dir, calls, env = _stand_ins(tmp_path, version)
    code, specs, ran = started(monkeypatch, "demo", "GRAPH=README.md", "PASSWORDS_FILE=", "DEMO_MODE=1", PATH=env["PATH"])
    assert code == 0
    skipped = [line for line in capsys.readouterr().err.splitlines() if "portal" in line]
    assert skipped == [f"make demo: KGE alone, the discovery portal skipped: it needs Node.js 22.12 or later (found: {found}): "
                       "https://nodejs.org"], skipped
    assert not calls.exists() and ["portal-build"] not in ran, "npm never runs"
    (args, _, served), = specs
    assert args[1:4] == ["-m", "uvicorn", "kge.app:app"] and served["SIGNIN"] == "passwords" and served["DEMO_MODE"] == "1", specs


@pytest.mark.parametrize("args, page, portal", [((), 8000, 8090), (("PROFILE=thornbury", "PORT=8001"), 8001, 8091)])
def test_make_demo_with_node_runs_the_page_and_the_portal_side_by_side(monkeypatch, capsys, args, page, portal):
    """make demo, Node 22.12 or later found: the portal built, then the page (password sign-in on, beside demo mode) and
    the portal run side by side, the portal 90 above the page's port, so a second profile's demo beside the first has its
    own."""
    code, specs, ran = started(monkeypatch, "demo", "GRAPH=README.md", "PASSWORDS_FILE=", *args, needs="")
    assert code == 0 and ["portal-build"] in ran
    assert (f"KGE on http://127.0.0.1:{page}, the discovery portal on http://127.0.0.1:{portal}; Ctrl-C stops both"
            in capsys.readouterr().out)
    (kge, _, kge_env), (node, folder, portal_env) = specs
    assert kge[1:4] == ["-m", "uvicorn", "kge.app:app"] and kge[-2:] == ["--port", str(page)] and kge_env["SIGNIN"] == "passwords"
    assert node[1:] == ["--experimental-sqlite", "src/index.js"] and folder == Path("portal") / "server"
    assert portal_env["PORT"] == str(portal) and portal_env["KGE_URL"] == f"http://127.0.0.1:{page}"


def test_side_by_side_stops_the_other_command_when_one_ends_and_both_on_a_signal(tmp_path):
    """scripts/side_by_side.py: the first command to end stops the other, its status the run's; SIGTERM stops both."""
    script = str(DEMO / "scripts" / "side_by_side.py")
    marker = tmp_path / "late"
    done = subprocess.run([sys.executable, script, "exit 3", f"sleep 30; touch {marker}"], capture_output=True, timeout=40)
    assert done.returncode == 3 and not marker.exists()
    proc = subprocess.Popen([sys.executable, script, "sleep 30", "sleep 30"])
    time.sleep(1)
    proc.send_signal(15)
    assert proc.wait(timeout=40) == 128 + 15


def test_every_action_is_pinned_to_a_major_version_tag(caller, workflow):
    uses = [step["uses"] for _, wf in (caller, workflow) for _, step in steps(wf) if "uses" in step]
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", u) for u in uses), uses
    checkout = re.search(r"actions/checkout@v\d+", (WORKFLOWS / "checks.yml").read_text()).group(0)
    assert checkout in uses, "the checkout checks.yml uses"
    calls = [job["uses"] for job in caller[1]["jobs"].values() if "uses" in job]
    assert calls == ["./.github/workflows/kit-demo.yml"], "the one call, of this repository's own file at the run's commit"


@pytest.mark.skipif(shutil.which("docker") is None, reason="no Docker on the PATH")
def test_docker_compose_validates_the_zips_compose_file(unzipped):
    if subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=60).returncode:
        pytest.skip("no Docker Compose v2")
    env = {**os.environ, "SESSION_SECRET": "x" * 48, "PORTAL_SYNC_TOKEN": "y" * 48}
    done = subprocess.run(["docker", "compose", "config", "-q"], cwd=unzipped / "compose", env=env, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    unset = subprocess.run(["docker", "compose", "config", "-q"], cwd=unzipped / "compose",
                           env={**{k: v for k, v in os.environ.items() if k not in {"SESSION_SECRET", "PORTAL_SYNC_TOKEN"}},
                                "PORTAL_SYNC_TOKEN": "y" * 48}, capture_output=True,
                           text=True, timeout=60)
    assert unset.returncode == 0, "standalone portal config does not require a KGE session key"
    unset_sync = subprocess.run(["docker", "compose", "config", "-q"], cwd=unzipped / "compose",
                                env={**{k: v for k, v in os.environ.items() if k not in {"SESSION_SECRET", "PORTAL_SYNC_TOKEN"}},
                                     "SESSION_SECRET": "x" * 48}, capture_output=True, text=True, timeout=60)
    assert unset_sync.returncode == 0, "standalone portal config does not require a KGE sync token"


def terraform(*args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["terraform", *args, "-no-color"], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


@pytest.mark.skipif(shutil.which("terraform") is None, reason="no Terraform on the PATH")
def test_terraform_fmt_validate_and_test_pass_from_the_zip_alone(unzipped, tmp_path):
    root = unzipped / "terraform"
    env = {**os.environ, "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1"}
    fmt = subprocess.run(["terraform", "fmt", "-check", "-recursive", "-diff", "-no-color"], cwd=root, env=env,
                         capture_output=True, text=True, timeout=120)
    assert fmt.returncode == 0, fmt.stdout + fmt.stderr
    for config, tfvars in (("pilot", ("example.tfvars",)),):
        env["TF_DATA_DIR"] = str(tmp_path / config)
        init = terraform("init", "-backend=false", "-input=false", "-lockfile=readonly", cwd=root / config, env=env)
        if init.returncode:
            pytest.skip(f"terraform init could not install the providers here: {init.stderr.strip()[-400:]}")
        validate = terraform("validate", cwd=root / config, env=env)
        assert validate.returncode == 0, validate.stdout + validate.stderr
        for name in tfvars:
            test = terraform("test", f"-var-file={name}", cwd=root / config, env=env)
            assert test.returncode == 0, f"{config} {name}:\n{test.stdout}{test.stderr}"
            assert re.search(r"Success! \d+ passed, 0 failed", test.stdout), test.stdout


def test_the_dataset_prefix_row_names_each_dataset_the_pilot_makes():
    """Grok n7 on #97: the settings row for BIGQUERY_DATASET_PREFIX names the warehouses, the graph and, on the pilot,
    the candidates dataset, as the guide's BigQuery section says the pilot makes three."""
    row = next(line for line in (KIT / "INSTALL.md").read_text().splitlines()
               if line.startswith("| `BIGQUERY_PROJECT`, `BIGQUERY_DATASET_PREFIX`"))
    for dataset in ("`<prefix>_<client>_warehouses`", "`<prefix>_<client>_graph`", "`<prefix>_<client>_candidates`"):
        assert dataset in row, dataset


def test_rolling_back_to_0_7_seeds_the_warehouses_before_its_onboard():
    """Verifier V3 on #97: after the upgrade the work holds no warehouse file but keeps the estate's summary, so 0.7's
    onboard (its Makefile's $(LANDED): | $(SUMMARY)) builds no estate warehouse; the rollback runs the job's seed, which
    builds them again, before that onboard."""
    guide = " ".join((KIT / "INSTALL.md").read_text().split())
    rollback = guide.split("To roll back, first, if the app state goes back to files with it", 1)[1].split("- **", 1)[0]
    after = rollback.split("apply the 0.7 kit's Terraform", 1)[1]
    assert "`seed`" in after and after.index("`seed`") < after.index("`onboard`"), after[:400]


def test_the_embeddings_note_names_each_load_s_chunks_table_not_one_table():
    """Verifier V4 on #97: each load writes its chunks to a table of its own, chunks_<hash>_<generation>, so the upgrade's
    embeddings note never says the vectors live in one <prefix>_<client>_graph.chunks table."""
    guide = " ".join((KIT / "INSTALL.md").read_text().split())
    note = guide.split("**Embeddings, after the upgrade.**", 1)[1].split("- **", 1)[0]
    assert "_graph.chunks`" not in note and "`chunks_<first 16 characters of the hash>_<8 characters>`" in note, note[:400]


def test_the_pilots_first_setup_names_no_baseline_and_records_its_own_after_the_first_release():
    """GCP agent's live run on #100: on a fresh pilot the example's Ladybug baselines cannot be put beside a BigQuery run,
    and baseline needs the client's current, which only release makes. So the guide's Google Cloud sequence runs its first
    setup-client with no BASELINE= (the post-load check then holds the candidate to the pack's gates alone), and runs
    baseline, then tells the operator to run its printed setup-client command, only after release; Compose keeps issue-40's."""
    guide = (KIT / "INSTALL.md").read_text()
    section = guide.split("### Set the client up and release\n", 1)[1].split("\n### ", 1)[0]
    runs = [line for line in section.splitlines() if line.startswith('"${JOB[@]}" --args=')]
    order = [re.match(r'"\$\{JOB\[@\]\}" --args="?([a-z-]+)', line).group(1) for line in runs]
    assert order == ["setup-client", "seed", "onboard", "sign-off", "release", "passwords", "baseline"], order
    first = runs[0]
    assert "BASELINE=" not in first and "ASKER=analyst_enterprise" in first and "PEOPLE=" in first
    assert "complete `setup-client` command" in section and "current asker when there is one" in section
    assert "passing its command and assignments as comma-separated `--args` values" in section
    assert "pack's own gates alone" in section and "issue-40" not in section
    own = guide.split("**The pilot's own baseline.**", 1)[1].split("\n### ", 1)[0]
    assert "once a release exists" in own and "no `BASELINE`" in own
    upgrade = guide.split("- **The baseline, wherever `GRAPH_STORE` is `bigquery`.**", 1)[1].split("\n- ", 1)[0]
    assert "no `BASELINE`" in upgrade and upgrade.index("`release`") < upgrade.index("run the job's `baseline`")
    assert "docker compose run --rm job setup-client CLIENT=meridian ASKER=analyst_enterprise BASELINE=baselines/issue-40.json" in guide


def test_install_sample_setup_people_match_test_drive():
    config = yaml.safe_load((DEMO / "kge/services/onboard/test_drive.yaml").read_text())
    expected = {tuple(person.split(":")) for person in config["people"]}
    setups = []
    for line in (KIT / "INSTALL.md").read_text().splitlines():
        if "setup-client" not in line or "PEOPLE=" not in line:
            continue
        people = re.search(r'PEOPLE=([^"\n]+)', line).group(1)
        if people.startswith("<"):
            continue
        setups.append(line)
        assert {tuple(person.split(":")) for person in people.split()} == expected, line
    assert len(setups) >= 3


def test_portal_can_run_without_app():
    compose = yaml.safe_load((DEMO / "kit/compose/compose.yaml").read_text())
    portal = compose["services"]["portal"]
    assert "app" not in portal.get("depends_on", [])
    assert "PORTAL_CATALOGUE_FILE" in portal["environment"]
    assert any("/catalogue/catalogue.yaml:ro" in volume for volume in portal["volumes"])
    assert "PORTAL_IDENTITIES_FILE" in portal["environment"]
    assert "PORTAL_PASSWORDS_FILE" in portal["environment"]
    assert "PORTAL_SESSION_SECRET" in portal["environment"]
    assert not any(":?" in str(value) for service in compose["services"].values() for value in service.get("environment", {}).values())


@pytest.mark.parametrize("job, leg, ago, warned", [
    ("kge-full-legs", "integration-2/6", 700, True), ("kge-full-legs", "integration-2/6", 30, False),
    ("kge-full-legs", "e2e-2/4", 700, True), ("kge-full-legs", "e2e-4/4", 30, False),
    ("kge-full-legs", "parity-margin", 30, False),
    ("kge-unit", "unit-3/4", 700, True), ("kge-unit", "unit-3/4", 30, False)])
def test_a_full_check_leg_over_10_minutes_warns_naming_the_leg_and_its_files(tmp_path, job, leg, ago, warned):
    """Checks' step after a full check leg (make full-check-leg, or a unit shard's make test-unit), run as the job runs
    it: past 10 minutes a warning names the leg and, for a shard, the files tests/lanes.py deals it, so a leg drifting
    towards the timeout is seen; it never fails the leg."""
    legs = yaml.safe_load((WORKFLOWS / "checks.yml").read_text())["jobs"][job]
    timed = next(s for s in legs["steps"] if s.get("name") == "The leg's time, warned past 10 minutes")
    start = next(s for s in legs["steps"] if s.get("name") == "The leg's start")
    work = "The full check's leg" if job == "kge-full-legs" else "Unit tests, shard ${{ matrix.shard }} of 4, with coverage"
    leg_env = "${{ matrix.leg }}" if job == "kge-full-legs" else "unit-${{ matrix.shard }}/4"
    assert timed["if"] == "${{ !cancelled() }}" and timed["env"] == {"LEG": leg_env}
    assert start["run"] == 'echo "LEG_START=$(date +%s)" >> "$GITHUB_ENV"'
    names = [s.get("name") for s in legs["steps"]]
    assert names.index("The leg's start") < names.index(work) < names.index(timed["name"])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "LEG": leg, "LEG_START": str(int(time.time()) - ago)}
    done = subprocess.run(["bash", "-e", "-c", timed["run"]], cwd=DEMO, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    warning = [line for line in done.stdout.splitlines() if line.startswith("::warning")]
    assert bool(warning) is warned, done.stdout
    if warned:
        assert f"The {leg} leg took 11 min" in warning[0]
        lane, _, shard = leg.partition("-")
        files = subprocess.run([sys.executable, "tests/lanes.py", lane, "--shard", shard], cwd=DEMO, capture_output=True,
                               text=True).stdout.split() if lane in ("integration", "unit") else []
        assert files or lane not in ("integration", "unit")
        assert all(f in warning[0] for f in files) and ("Its files:" in warning[0]) is bool(files)
        assert ("PARTS in tests/e2e/demo_script.cjs" in warning[0]) is (lane == "e2e"), "an e2e leg's cure is its parts"
        assert ("make lane-times" in warning[0]) is (lane != "e2e")
