"""The pilot's Terraform (issue #48 item 6): infra/, its shared modules, the pilot, and QA/PROD.

    .venv/bin/python -m pytest tests/unit/test_infra.py -q

A live pilot exists, but these tests never plan or apply against it. They read the files: the three parts and the
modules each calls, the versions pinned, the clients a plain variable (meridian by default) and no file read from outside
its folder (issue #56: the kit's Terraform stands alone), the store and the landing reached by URL with nothing mounted,
the image version left to deployments, the registry's cleanup, PROD's refusal without an organization, and no real project,
billing account, organization, domain or host anywhere, only placeholders. With Terraform on the PATH (and its providers
reachable: the registry, or a mirror TF_CLI_CONFIG_FILE names), the last test runs what infra/README.md says to run
here: terraform fmt -check, then validate and terraform test, against stand-in providers, for the pilot and for QA and
PROD; its working files go to a scratch folder, never into infra/. Each configuration's tests run once per tfvars
file, which gives the values every run starts from.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEMO = Path(__file__).resolve().parents[2]
INFRA = DEMO / "infra"
MODULES = ("project", "identity", "storage", "registry", "bigquery", "run", "secrets", "observability", "portal")
SHARED_MODULES = set(MODULES) - {"portal"}
PILOT, PROD = INFRA / "pilot", INFRA / "prod"
RUNS = {PILOT: ("example.tfvars",), PROD: ("qa.tfvars", "prod.tfvars")}


def tf(folder: Path) -> str:
    """Every .tf file of a folder, joined."""
    return "\n".join(p.read_text() for p in sorted(folder.glob("*.tf")))


def tracked() -> list[Path]:
    """infra/'s files as git sees them: committed or to be, never what git ignores."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "infra"], cwd=DEMO,
                         capture_output=True, text=True, check=True).stdout
    return [DEMO / line for line in out.splitlines()]


def test_infra_holds_the_modules_the_pilot_and_qa_prod():
    for name in MODULES:
        for part in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
            assert (INFRA / "modules" / name / part).is_file(), f"modules/{name}/{part}"
    for config in (PILOT, PROD):
        for part in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
            assert (config / part).is_file(), f"{config.name}/{part}"
        assert list((config / "tests").glob("*.tftest.hcl")), f"{config.name}: its terraform tests"
    assert (PILOT / "example.tfvars").is_file()
    assert (PROD / "qa.tfvars").is_file() and (PROD / "prod.tfvars").is_file()
    assert (INFRA / "README.md").is_file()


def test_configurations_call_their_modules():
    """The pilot alone calls the portal module; both roots call every other shared module, and modules call no other."""
    for config, expected in ((PILOT, set(MODULES)), (PROD, SHARED_MODULES)):
        called = set(re.findall(r'source\s*=\s*"\.\./modules/([a-z_-]+)"', tf(config)))
        assert called == expected, (config.name, sorted(called))
    for name in MODULES:
        assert not re.search(r"^\s*module\s+\"", tf(INFRA / "modules" / name), re.M), f"modules/{name} calls a module"


def test_cloud_run_modules_never_declare_reserved_container_environment():
    """Cloud Run sets PORT and the K_ names itself and refuses a container env that names them. Check literal env names
    and map keys, including locals feeding dynamic env blocks, in the Cloud Run modules and in the configurations and
    their tfvars, which pass env maps into them."""
    reserved = r"(?:PORT|K_[A-Z0-9_]+)"
    pattern = re.compile(rf'\b{reserved}"?\s*=|\bname\s*=\s*"{reserved}"')
    run_modules = [f for f in sorted((INFRA / "modules").iterdir())
                   if re.search(r'resource\s+"google_cloud_run(?:_v2)?_(?:service|job)"', tf(f))]
    assert {f.name for f in run_modules} >= {"portal", "run"}
    for folder in run_modules + [INFRA / "pilot", INFRA / "prod"]:
        sources = [p.read_text() for p in sorted(folder.glob("*.tf")) + sorted(folder.glob("*.tfvars"))]
        assert not pattern.search("\n".join(sources)), folder.name


def test_terraform_and_the_providers_are_pinned():
    """The configurations pin Terraform's release line and each provider's exact version, the one their lock files hold;
    a module asks only for the line it was written against."""
    for config in (PILOT, PROD):
        versions = (config / "versions.tf").read_text()
        assert re.search(r'required_version\s*=\s*"~> 1\.16"', versions), config.name
        pins = dict(re.findall(r'(google(?:-beta)?)\s*=\s*\{\s*source\s*=\s*"hashicorp/google(?:-beta)?"\s*,?\s*version\s*=\s*"([^"]+)"', versions))
        assert pins and set(pins.values()) == {"8.8.0"}, (config.name, pins)
        lock = (config / ".terraform.lock.hcl").read_text()
        locked = dict(re.findall(r'provider "registry\.terraform\.io/hashicorp/(google(?:-beta)?)" \{\s*version\s*=\s*"([^"]+)"', lock))
        assert locked == pins, (config.name, locked, pins)
    pilot = re.search(r'"hashicorp/google"\s*,?\s*version\s*=\s*"([^"]+)"', (PILOT / "versions.tf").read_text()).group(1)
    guide = (DEMO / "kit" / "INSTALL.md").read_text()
    assert f"`hashicorp/google` {pilot}, which `terraform/pilot/.terraform.lock.hcl` holds" in guide, pilot
    for name in MODULES:
        versions = (INFRA / "modules" / name / "versions.tf").read_text()
        assert re.search(r'version\s*=\s*">= 8\.5\.0, < 9\.0\.0"', versions), name


def test_the_guide_installs_the_tested_terraform_which_the_configurations_allow():
    """The owner on #100: the install guide's bootstrap installs Terraform 1.17.0, the version tested, from HashiCorp's
    release and its SHA256SUMS, and says the configurations require ~> 1.16, which 1.17.0 meets."""
    guide = (DEMO / "kit" / "INSTALL.md").read_text()
    step = guide.split("1. Install Terraform ", 1)[1].split("\n2. ", 1)[0]
    assert step.startswith("1.17.0, checked against HashiCorp's sums.")
    assert "1.17.0 is the tested version; the configurations require `~> 1.16`." in step
    assert set(re.findall(r"releases\.hashicorp\.com/terraform/([0-9.]+)/terraform_([0-9.]+)_", step)) == {("1.17.0", "1.17.0")}
    assert set(re.findall(r"terraform_([0-9.]+)_(?:linux_amd64\.zip|SHA256SUMS)", step)) == {"1.17.0"}
    assert "sha256sum -c" in step
    assert "1.16.5" not in guide and "1.16.5" not in (INFRA / "README.md").read_text()


def test_the_clients_are_a_plain_variable_meridian_by_default():
    """Issue #56: the client list is a variable of its own, slugs, meridian alone by default; nothing reads the
    repository's deploy/profiles.yaml, which the store's registry (the job's setup-client) replaces."""
    for config in (PILOT, PROD):
        block = (config / "variables.tf").read_text().split('variable "clients"', 1)[1].split("\n}\n", 1)[0]
        assert re.search(r'default\s*=\s*\["meridian"\]', block), f"{config.name}: meridian alone by default"
        assert "can(regex(" in block and "distinct(var.clients)" in block, f"{config.name}: slugs, each named once"
        assert re.search(r"clients\s*=\s*sort\(var\.clients\)", tf(config)), config.name
    registry = yaml.safe_load((DEMO / "deploy" / "profiles.yaml").read_text())
    assert "meridian" in registry["profiles"], "the demo's own registry still names meridian, as the default"


def test_no_tf_file_reads_a_file_outside_its_own_folder():
    """The kit's terraform/ is infra/ alone: no file(), templatefile() or fileset() climbs out of its folder."""
    for path in sorted(INFRA.rglob("*.tf")):
        if ".terraform" in path.parts:
            continue
        for call in re.findall(r"\b(?:file[a-z0-9]*|templatefile)\([^)]*\)", path.read_text()):
            assert ".." not in call, f"{path.relative_to(INFRA)}: {call}"


def test_the_store_and_the_landing_are_reached_by_url_with_nothing_mounted():
    """The service and the job read the buckets through the image's store (STORE_URL, and the job's LANDING_URL), never a
    gcsfuse mount; the store bucket replaces the config bucket, and the service is granted nothing on the landing."""
    run = tf(INFRA / "modules" / "run")
    assert not re.search(r"\b(volumes|volume_mounts|mount_options)\b", run), "nothing is mounted"
    assert re.search(r'service_env\s*=\s*merge\(\{[^\n]*STORE_URL\s*=\s*"gs://\$\{var\.store_bucket\}"', run)
    assert re.search(r'job_env\s*=\s*merge\(\{[^\n]*LANDING_URL\s*=\s*"gs://\$\{var\.landing_bucket\}"', run)
    storage = tf(INFRA / "modules" / "storage")
    assert '"${var.project_id}-${var.name}-store"' in storage and "config" not in storage
    for config in (PILOT, PROD):
        main = (config / "main.tf").read_text()
        landing = main.split("landing_iam = ", 1)[1].split("} }", 1)[0]
        assert "service_member" not in landing and "objectAdmin" in landing, f"{config.name}: the landing is never the service's"
        store = main.split("store_iam = ", 1)[1].split("}]...)", 1)[0]
        assert re.search(r'-service"\s*=\s*\{\s*role\s*=\s*"roles/storage\.objectViewer"', store), config.name
        assert re.search(r'-job"\s*=\s*\{\s*role\s*=\s*"roles/storage\.objectAdmin"', store), config.name
        if config == PROD:
            assert re.search(r'improve\s*=\s*\{\s*role\s*=\s*"roles/storage\.objectViewer"', landing), "improve reads landing"
            assert "work/" not in store, "improve may not write work/<client>.tar.gz"


def test_the_pilot_creates_no_pubsub_eventarc_scheduler_or_iap():
    """Read from the files as terraform test reads them (infra/tests/declared): the pilot and the modules declare none."""
    text = tf(PILOT) + "".join(tf(INFRA / "modules" / name) for name in MODULES)
    types = set(re.findall(r'^\s*resource\s+"([a-z0-9_]+)"', text, re.M))
    assert not {t for t in types if re.match(r"google_(pubsub|eventarc|cloud_scheduler|workflows|iap)_", t)}, sorted(types)
    assert not re.search(r"^\s*iap_enabled\s*=\s*true\b", text, re.M)
    assert not re.search(r"(pubsub|eventarc|cloudscheduler|workflows|iap)\.googleapis\.com", tf(PILOT))


def test_the_landing_workflow_overrides_the_page_request_command_with_onboard():
    workflow = (PROD / "onboard.workflow.yaml").read_text()
    onboard = workflow.split("    - onboard:", 1)[1].split("    - started:", 1)[0]
    assert re.search(r'body:\s+overrides:\s+containerOverrides:\s+- args: \["onboard"\]', onboard)
    assert 'role     = "roles/run.jobsExecutorWithOverrides"' in (PROD / "events.tf").read_text()


def test_the_image_version_is_left_to_deployments():
    """Terraform makes the service and the job and never moves their image: a deployment (gcloud, GitHub Actions) does."""
    run = tf(INFRA / "modules" / "run")

    def ignored(kind: str) -> list[str]:
        """One resource's ignore_changes entries, each whole: the job's entry never stands for the service's."""
        block = run.split(f'resource "{kind}" "this"', 1)[1].split('\nresource "', 1)[0]
        lists = re.findall(r"^\s*ignore_changes\s*=\s*\[(.*)\]\s*$", block, re.M)
        return [entry.strip() for entries in lists for entry in entries.split(",")]

    assert "template[0].containers[0].image" in ignored("google_cloud_run_v2_service"), "the service's image"
    assert "template[0].template[0].containers[0].image" in ignored("google_cloud_run_v2_job"), "the job's image"


def test_the_registry_keeps_the_last_ten_tagged_images_and_drops_untagged_ones_after_seven_days():
    registry = tf(INFRA / "modules" / "registry")
    assert re.search(r'format\s*=\s*"DOCKER"', registry)
    assert re.search(r'action\s*=\s*"KEEP"[^}]*most_recent_versions\s*\{[^}]*keep_count\s*=\s*10', registry, re.S)
    assert re.search(r'action\s*=\s*"DELETE"\s*condition\s*\{[^}]*tag_state\s*=\s*"UNTAGGED"[^}]*older_than\s*=\s*"604800s"', registry, re.S)
    assert re.search(r'action\s*=\s*"DELETE"\s*condition\s*\{\s*tag_state\s*=\s*"TAGGED"\s*\}', registry), \
        "tagged images beyond the last 10 are deleted"


def test_the_bigquery_reservation_is_enterprise_autoscaling_from_zero_to_a_cap():
    bq = tf(INFRA / "modules" / "bigquery")
    assert re.search(r'edition\s*=\s*"ENTERPRISE"', bq)
    assert re.search(r"slot_capacity\s*=\s*0\b", bq), "baseline 0"
    assert re.search(r"autoscale\s*\{\s*max_slots\s*=\s*var\.max_slots", bq), "a capped maximum"


def test_the_service_and_the_job_may_write_the_graph_dataset_where_app_state_is_kept():
    """Issue #93: APP_STATE_BACKEND=bigquery keeps its tables in the graph dataset, which both runtimes write."""
    for config in (PILOT, PROD):
        main = (config / "main.tf").read_text()
        for member in ("service_member", "job_member"):
            assert re.search(r'role\s*=\s*"roles/bigquery\.dataEditor",\s*member\s*=\s*module\.identity\.service_accounts\[c\]\.'
                             + member + r',\s*kinds\s*=\s*\[[^\]]*"graph"', main), (config.name, member)


def test_the_service_may_write_the_warehouses_dataset_where_the_page_onboards_a_unit():
    """Issue #95: with WAREHOUSE_BACKEND=bigquery the page's onboarding steps, which the service runs, make a unit's
    tables in the warehouses dataset (kge/adapters/warehouse/bigquery_estate.py), so the pilot's service may write it.
    QA and PROD are placeholders this pull request leaves as develop has them: their service only reads it."""
    grant = (r'service\s*=\s*\{\s*role\s*=\s*"roles/bigquery\.%s",\s*member\s*=\s*module\.identity\.'
             r'service_accounts\[c\]\.service_member,\s*kinds\s*=\s*\["warehouses"\]')
    assert re.search(grant % "dataEditor", (PILOT / "main.tf").read_text())
    assert re.search(grant % "dataViewer", (PROD / "main.tf").read_text())


def test_only_the_pilots_job_may_write_the_candidates_dataset_and_qa_and_prod_have_none():
    """Review of #97 (M2): the improvement job builds and checks its candidates in the client's candidates dataset
    (GRAPH_DATASET=candidates), which nothing serves. Only a root that asks for it (candidates = true) gets one: the
    pilot, whose job runs the page's improvements and writes it beside the others. QA and PROD, placeholders, ask for
    none and grant nothing on it; no service is granted it anywhere."""
    bq = tf(INFRA / "modules" / "bigquery")
    assert 'concat(["warehouses", "graph"], var.candidates ? ["candidates"] : [])' in bq
    pilot = (PILOT / "main.tf").read_text()
    assert re.search(r'module "bigquery"\s*\{[^}]*candidates\s*=\s*true', pilot)
    assert re.search(r'job\s*=\s*\{[^}]*job_member,\s*kinds\s*=\s*\["warehouses",\s*"graph",\s*"candidates"\]', pilot)
    prod = (PROD / "main.tf").read_text()
    assert not re.search(r'^\s*candidates\s*=', prod, re.M) and '"candidates"' not in prod
    for main in (prod, pilot):
        for grant in re.findall(r'^.*service_member.*$', main, re.M):
            assert "candidates" not in grant


def test_secrets_are_declared_with_no_values():
    text = "".join(tf(c) for c in (PILOT, PROD)) + "".join(tf(INFRA / "modules" / n) for n in MODULES)
    assert "google_secret_manager_secret_version" not in text, "a value would sit in the Terraform state"


def test_every_setting_the_configurations_give_the_image_is_one_it_reads():
    """The service's and the job's settings, the secrets' among them, are named as the image reads them: the pilot
    sign-in's session key and hashed passwords (kge/services/identity/service.py) and the model's project and region on Vertex AI
    (kge/adapters/model/providers.py), never names the code does not know."""
    settings_code = (DEMO / "kge" / "pipeline" / "settings.py").read_text()
    code = "".join(p.read_text() for d in ("app.py", "services", "semantics", "pipeline") for p in sorted((DEMO / "kge").glob(f"{d}") if d.endswith(".py") else (DEMO / "kge" / d).rglob("*.py")))
    for config in (PILOT, PROD):
        main = (config / "main.tf").read_text()
        secret = re.search(r"^\s*secret_env\s*=\s*\{([^}]*)\}", main, re.M).group(1)
        env = re.search(r"^\s*env\s*=\s*merge\(\{([^}]*)\}", main, re.M).group(1)
        assert set(re.findall(r"([A-Z_]+)\s*=", secret)) == {"SESSION_SECRET", "SIGNIN_PASSWORDS"}, (config.name, secret)
        assert set(re.findall(r"([A-Z_]+)\s*=", env)) == {"VERTEX_PROJECT", "VERTEX_REGION", "RUN_REGION", "BIGQUERY_PROJECT", "BIGQUERY_DATASET_PREFIX"}, (config.name, env)
    run = tf(INFRA / "modules" / "run")
    service = re.search(r"service_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, local\.trusted_env, var\.env\)", run).group(1)
    trusted = re.search(r"trusted_env\s*=\s*var\.trusted_caller == null \? \{\} : \{([^\n]*)\}", run).group(1)
    job = re.search(r"job_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, var\.env\)", run).group(1)
    tmp = re.search(r"tmp_env\s*=\s*var\.tmp_bucket == null \? \{\} : \{([^\n]*)\}", run).group(1)
    assert set(re.findall(r"([A-Z_]+)\s*=", service)) == {"PROFILE", "SIGNIN", "STORE_URL"}, service
    assert set(re.findall(r"([A-Z_]+)\s*=", job)) == {"PROFILE", "STORE_URL", "LANDING_URL"}, job
    assert set(re.findall(r"([A-Z_]+)\s*=", tmp)) == {"TMP_URL"}, tmp
    assert set(re.findall(r"([A-Z_]+)\s*=", trusted)) == {"TRUSTED_CALLER_EMAIL", "TRUSTED_CALLER_AUDIENCE"}, trusted
    for name in re.findall(r"([A-Z_]+)\s*=", secret + env + service + job + tmp + trusted):
        assert f'"{name}"' in code, f"{name}: no setting the image reads"
        assert f'    {name}:' in settings_code and f'"{name}"' in settings_code, f"{name}: absent from the settings module"


def test_qa_and_prod_are_two_instances_of_one_configuration_each_with_its_own_state():
    for env in ("qa", "prod"):
        tfvars = (PROD / f"{env}.tfvars").read_text()
        assert re.search(rf'^environment\s*=\s*"{env}"', tfvars, re.M)
        assert re.search(r"^organization_id\s*=\s*null", tfvars, re.M), "no organization yet"
        backend = (PROD / f"{env}.gcs.tfbackend").read_text()
        assert re.search(rf'^prefix\s*=\s*"kge/{env}"', backend, re.M), f"{env}'s own state"
    assert re.search(r'backend\s+"gcs"\s*\{\s*\}', (PROD / "versions.tf").read_text()), "a partial backend: one per environment"


def test_prod_refuses_a_null_organization_and_the_organizations_parts_wait_for_one():
    variables = (PROD / "variables.tf").read_text()
    block = variables.split('variable "organization_id"', 1)[1].split("\n}\n", 1)[0]
    assert re.search(r"default\s*=\s*null", block)
    assert re.search(r'var\.organization_id\s*!=\s*null\s*\|\|\s*var\.environment\s*!=\s*"prod"', block)
    org = (PROD / "organization.tf").read_text()
    for kind in ("google_access_context_manager_service_perimeter", "google_org_policy_policy",
                 "google_iap_web_cloud_run_service_iam_member", "google_iam_workforce_pool"):
        assert re.search(rf'resource "{kind}"', org), kind
    gated = re.findall(r'^resource "[a-z_]+" "[a-z_]+" \{\n\s*(?:count|for_each)\s*=\s*([^\n]+)', org, re.M)
    assert gated and all("local.organization" in g for g in gated), gated


def test_qa_and_prod_improve_job_has_its_own_proposal_only_account():
    main = (PROD / "main.tf").read_text()
    events = (PROD / "events.tf").read_text()
    assert re.search(r"improve\s*=\s*true", main)
    assert re.search(r'"\$\{c\}-improve"\s*=\s*\{\s*role\s*=\s*"roles/storage\.objectViewer"', main)
    assert re.search(r'"\$\{c\}-improve-proposals".*?roles/storage\.objectUser.*?clients/\$\{c\}/proposals/', main, re.S)
    assert 'writers             = { deployer = module.identity.deployer.member }' in main
    assert 'resource "google_cloud_run_v2_job" "improve"' in events
    assert 'args  = ["improve"]' in events
    assert 'resource "google_cloud_scheduler_job" "improve"' in events


EMAIL = re.compile(r"[A-Za-z0-9._%+${}-]+@([A-Za-z0-9.${}-]+\.[A-Za-z]{2,})")
GOOGLE = re.compile(r"(?:[a-z0-9-]+\.)*(?:googleapis\.com|gserviceaccount\.com|pkg\.dev|github\.com|githubusercontent\.com|hashicorp\.com|terraform\.io|google\.com)$")


def test_no_real_project_billing_account_organization_domain_or_host():
    """Variables only: the tfvars and the backends hold obvious placeholders, an address is example.com's or a Google
    service agent's, and a host is a public Google, GitHub or HashiCorp endpoint, never one of the client's."""
    for path in tracked():
        if path.suffix not in (".tf", ".tfvars", ".tfbackend", ".hcl", ".md", ".yaml"):
            continue
        text = path.read_text()
        for m in re.finditer(r"\b[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\b", text):
            assert m.group(0) == "000000-000000-000000", f"{path.name}: a billing account"
        for m in EMAIL.finditer(text):
            assert m.group(1) == "example.com" or GOOGLE.search(m.group(1).replace("${", "").replace("}", "")), f"{path.name}: {m.group(0)}"
        for m in re.finditer(r"https://([A-Za-z0-9.-]+)", text):
            assert m.group(1).endswith(("example.com",)) or GOOGLE.search(m.group(1)), f"{path.name}: {m.group(1)}"
    for path in (PILOT / "example.tfvars", PROD / "qa.tfvars", PROD / "prod.tfvars"):
        text = path.read_text()
        for project in re.findall(r'^project_id\s*=\s*"([^"]+)"', text, re.M):
            assert project.startswith("your-"), f"{path.name}: {project}"
        assert re.search(r'^github_repository\s*=\s*"your-org/your-repo"', text, re.M), path.name
        for org in re.findall(r'^organization_id\s*=\s*"([^"]+)"', text, re.M):
            assert set(org) == {"0"}, f"{path.name}: {org}"


def test_state_plans_and_provider_caches_are_never_committed():
    ignore = (INFRA / ".gitignore").read_text().split()
    for pattern in (".terraform/", "*.tfstate", "*.tfstate.*", "*.tfplan"):
        assert pattern in ignore, pattern
    names = [p.relative_to(INFRA).as_posix() for p in tracked()]
    assert not [n for n in names if ".terraform/" in n or ".tfstate" in n or n.endswith(".tfplan")], names


def test_the_readme_says_what_each_configuration_creates_what_waits_and_how_to_bootstrap():
    readme = (INFRA / "README.md").read_text()
    for heading in ("## What each configuration creates", "## What waits for the account", "## Bootstrap, once, from Cloud Shell",
                    "## Checks without an account"):
        assert heading in readme, heading
    for command in ("terraform fmt -check -recursive", "terraform test -var-file=example.tfvars",
                    "terraform test -var-file=qa.tfvars", "terraform test -var-file=prod.tfvars", "-backend-config=qa.gcs.tfbackend"):
        assert command in readme, command


def test_the_docs_describe_the_pilots_native_stores_and_no_deferred_graph():
    """The pilot keeps every store in BigQuery (#95): the infra README, the demo README and the install guide say so, with
    seed before onboard, the graph.bigquery and chunks.bigquery pointers a release carries and the candidates dataset, and
    none still says the BigQuery graph is a later stage or that an instance keeps the graph or DuckDB files (a Copilot
    finding on #97)."""
    readme = " ".join((INFRA / "README.md").read_text().split())
    for said in ("`seed`, before the first `onboard`", "`graph.bigquery`", "`chunks.bigquery`", "`<prefix>_<client>_candidates`"):
        assert said in readme, said
    stale = ("not yet on BigQuery Graph", "a later stage", "DuckDB files the sync loads from", "keeps the graph (and")
    for doc in (INFRA / "README.md", DEMO / "README.md", DEMO / "kit/INSTALL.md", DEMO / "CONTRIBUTING.md"):
        text = " ".join(doc.read_text().split())
        assert not [s for s in stale if s in text], doc


def terraform(*args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["terraform", *args, "-no-color"], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


@pytest.mark.skipif(shutil.which("terraform") is None, reason="no Terraform on the PATH: infra/README.md says how to run these")
def test_terraform_fmt_validate_and_test_pass_against_stand_in_providers(tmp_path):
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    fmt = subprocess.run(["terraform", "fmt", "-check", "-recursive", "-diff", "-no-color"], cwd=INFRA, env=env,
                         capture_output=True, text=True, timeout=120)
    assert fmt.returncode == 0, fmt.stdout + fmt.stderr
    for config, tfvars in RUNS.items():
        env["TF_DATA_DIR"] = str(tmp_path / config.name)
        init = terraform("init", "-backend=false", "-input=false", "-lockfile=readonly", cwd=config, env=env)
        if init.returncode:
            pytest.skip(f"terraform init could not install the providers here: {init.stderr.strip()[-400:]}")
        validate = terraform("validate", cwd=config, env=env)
        assert validate.returncode == 0, validate.stdout + validate.stderr
        for name in tfvars:
            test = terraform("test", f"-var-file={name}", cwd=config, env=env)
            assert test.returncode == 0, f"{config.name} {name}:\n{test.stdout}{test.stderr}"
            assert re.search(r"Success! \d+ passed, 0 failed", test.stdout), test.stdout


def test_the_pilots_gcp_native_settings_are_the_issues_and_each_one_the_image_reads():
    """pilot_env, the pilot's default configuration (#95), holds exactly the live-validated GCP-native values; each name
    is documented in .env.example and read by the code: by name, or, for a tier's or an agent's, by the pattern the model
    adapter builds it from (MODEL_<TIER>_PROVIDER and _NAME, <AGENT>_MODEL_TIER). main.tf merges it between the shared settings and
    app_env, so app_env overrides any one of them."""
    variables = (PILOT / "variables.tf").read_text()
    block = re.search(r'variable "pilot_env" \{.*?default = \{(.*?)\n  \}', variables, re.S).group(1)
    pilot = dict(re.findall(r'^\s*([A-Z_]+)\s*=\s*"([^"]*)"', block, re.M))
    assert pilot == {
        "MODEL_PROVIDER": "vertex-gemini", "MODEL_NAME": "gemini-3.8-flash",
        "MODEL_HIGH_PROVIDER": "vertex-gemini", "MODEL_HIGH_NAME": "gemini-3.1-pro-preview",
        "MODEL_MEDIUM_PROVIDER": "vertex-gemini", "MODEL_MEDIUM_NAME": "gemini-3.8-flash",
        "MODEL_LOW_PROVIDER": "vertex-gemini", "MODEL_LOW_NAME": "gemini-3.5-flash-lite",
        "TAGGER_PROVIDER": "model", "TAGGER_MODEL_TIER": "low",
        "EMBEDDING_PROVIDER": "vertex", "VERTEX_EMBEDDING_MODEL": "gemini-embedding-001", "EMBEDDING_DIM": "768",
        "WAREHOUSE_BACKEND": "bigquery", "RETRIEVAL_BACKEND": "bigquery", "APP_STATE_BACKEND": "bigquery",
        "GRAPH_STORE": "bigquery",
    }
    assert re.search(r'model_location"\s*\{[^}]*default\s*=\s*"global"', variables), "VERTEX_REGION is global by default"
    assert "}, var.pilot_env, var.app_env)" in (PILOT / "main.tf").read_text()
    example = (DEMO / ".env.example").read_text()
    code = "".join(p.read_text() for p in sorted((DEMO / "kge").rglob("*.py")))
    providers = (DEMO / "kge" / "adapters" / "model" / "providers.py").read_text()
    for name in pilot:
        assert re.search(rf"^#? ?{name}=", example, re.M), f"{name}: not in .env.example"
        model = re.fullmatch(r"MODEL_(?:(?:HIGH|MEDIUM|LOW)_)?(PROVIDER|NAME)", name)
        if model:
            assert 'f"MODEL_{wanted.upper()}"' in providers and f'f"{{prefix}}_{model.group(1)}"' in providers, name
        elif name.endswith("_MODEL_TIER"):
            assert 'f"{agent.upper()}_MODEL_TIER"' in providers, name
        else:
            assert f'"{name}"' in code, f"{name}: no setting the image reads"


def test_the_bigquery_module_gives_no_candidates_dataset_unless_a_root_asks():
    """Grok m1 on #97: the bigquery module's candidates variable defaults to false, so QA and PROD, whose roots never set
    it (and stay as develop has them), get no candidates dataset; only the pilot's root sets it."""
    variables = (INFRA / "modules" / "bigquery" / "variables.tf").read_text()
    block = re.search(r'variable "candidates" \{(.*?)\n\}', variables, re.S).group(1)
    assert re.search(r"^\s*default\s*=\s*false\s*$", block, re.M), block
    assert "candidates" not in (PROD / "main.tf").read_text().split('module "bigquery"', 1)[1].split("\n}", 1)[0]
    assert re.search(r"^\s*candidates\s*=\s*true\s*$", (PILOT / "main.tf").read_text(), re.M)

def test_qa_and_prod_s_improve_account_only_reads_the_warehouses_and_the_graph():
    """Grok n6 on #97: QA and PROD's improve account holds roles/bigquery.dataViewer on the warehouses and graph
    datasets, never a role that writes them."""
    main = (PROD / "main.tf").read_text()
    improve = re.findall(r"^\s*improve\s*=\s*\{[^}]*\}", main.split("dataset_iam", 1)[1].split("depends_on", 1)[0], re.M)
    assert improve == ['    improve       = { role = "roles/bigquery.dataViewer", member = module.identity.service_accounts[c].improve_member, kinds = ["warehouses", "graph"] }'], improve


def test_the_tmp_bucket_is_the_pilots_alone_and_each_client_reaches_its_own_prefix():
    """Issue #99: the storage module makes the tmp bucket, <project>-kge-tmp, only for a root that asks (tmp = true), and
    the run module passes TMP_URL only where it is given one (tmp_bucket, default null), as the bigquery module's
    candidates is opt-in: QA and PROD, whose roots name neither and stay as develop has them, get no bucket and no
    setting. The pilot asks for both, and grants each client's service and job roles/storage.objectUser on its own
    tmp/<client>/ prefix alone, conditioned as the store's page grant is; the bucket's objects are deleted after 7 days.
    A BigQuery load from the bucket also needs storage.buckets.get, which no object-prefix condition can give, so with
    tmp the module grants the predefined roles/storage.bucketViewer (storage.buckets.get and storage.buckets.list, no
    object permission), unconditioned, to each member tmp_iam names: a predefined role binds at once, where a custom one
    made in the same apply is not yet known to Cloud Storage. No custom role, and no role that lists the bucket's
    objects."""
    storage, run = (INFRA / "modules" / "storage"), (INFRA / "modules" / "run")
    variables = (storage / "variables.tf").read_text()
    assert re.search(r"^\s*default\s*=\s*false\s*$", re.search(r'variable "tmp" \{(.*?)\n\}', variables, re.S).group(1), re.M)
    assert re.search(r"^\s*default\s*=\s*7\s*$", re.search(r'variable "tmp_days" \{(.*?)\n\}', variables, re.S).group(1), re.M)
    block = re.search(r'variable "tmp_bucket" \{(.*?)\n\}', (run / "variables.tf").read_text(), re.S).group(1)
    assert re.search(r"^\s*default\s*=\s*null\s*$", block, re.M), block
    main = (storage / "main.tf").read_text()
    assert 'var.tmp ? { _tmp = "${var.project_id}-${var.name}-tmp" } : {}' in main
    assert re.search(r"condition \{\s*age = lifecycle_rule\.value\s*\}\s*action \{\s*type = \"Delete\"", main)
    assert "google_project_iam_custom_role" not in main, "no custom role: a predefined one binds in the same apply"
    assert ('var.tmp ? { for k, g in var.tmp_iam : "_tmp/${k}-bucket-viewer" => '
            '{ role = "roles/storage.bucketViewer", member = g.member, bucket = "_tmp" } } : {}') in main, "unconditioned, per tmp_iam member"
    for tree in (INFRA / "modules", PILOT, PROD):
        for tf in (t for t in tree.rglob("*.tf") if ".terraform" not in t.parts):
            assert "google_project_iam_custom_role" not in tf.read_text(), tf
    prod = (PROD / "main.tf").read_text()
    assert not re.search(r"^\s*tmp(_iam|_bucket|_days)?\s*=", prod, re.M) and "TMP_URL" not in prod
    pilot = (PILOT / "main.tf").read_text()
    assert re.search(r"^\s*tmp\s*=\s*true\s*$", pilot, re.M)
    assert re.search(r"^\s*tmp_bucket\s*=\s*module\.storage\.tmp_bucket\s*$", pilot, re.M)
    tmp_iam = pilot.split("tmp_iam = ", 1)[1].split("}]...)", 1)[0]
    assert re.findall(r'role\s*=\s*"([^"]+)"', tmp_iam) == ["roles/storage.objectUser"]
    assert re.findall(r"(\w+)\s*=\s*module\.identity\.service_accounts\[c\]\.(\w+)", tmp_iam) == [
        ("service", "service_member"), ("job", "job_member")]
    store = re.search(r'expression\s*=\s*"resource\.name\.startsWith\(\\"projects/_/buckets/(\$\{[^}]+\}-\$\{local\.name\})-store/',
                      pilot).group(1)
    assert re.findall(r'expression\s*=\s*"([^\n]*)"\n', tmp_iam) == [
        f'resource.name.startsWith(\\"projects/_/buckets/{store}-tmp/objects/tmp/${{c}}/\\")'], "the store's condition, on tmp/<client>/"


def test_the_guide_lists_the_tmp_bucket_and_its_setting_and_imports_nothing():
    """Issue #99: the pilot's Terraform makes the tmp bucket itself; the install guide lists it and TMP_URL, and asks
    for no import of a bucket made outside the release."""
    guide = (DEMO / "kit" / "INSTALL.md").read_text()
    assert "terraform import" not in guide
    storage = (INFRA / "modules" / "storage" / "main.tf").read_text()
    assert '_tmp = "${var.project_id}-${var.name}-tmp"' in storage
    assert guide.index("### Upgrading from 0.8.0") < guide.index("### Upgrading from 0.7")
    assert "| `TMP_URL` |" in guide and "bucket `<project>-kge-tmp`" in guide
    assert "kge_tmp_bucket_get" not in guide and "No custom role is created" in guide
    assert "`roles/storage.bucketViewer`" in guide and "`storage.buckets.get`" in guide and "BETA" in guide


def test_the_job_runs_three_hours_and_the_page_holds_its_marker_until_just_past_that():
    """Onboard and test-drive on a full pack run about 100 minutes on live models, so the run module gives each job
    execution three hours; the onboarding page turns a running request stale only once that timeout has passed, so a
    live job is never superseded and a terminated one does not block the page for long."""
    from kge.services.onboard.service import JOB_STALE_SECONDS

    block = re.search(r'variable "job_timeout" \{(.*?)\n\}', (INFRA / "modules" / "run" / "variables.tf").read_text(), re.S).group(1)
    timeout = int(re.search(r'default\s*=\s*"(\d+)s"', block).group(1))
    assert timeout == 10800
    assert timeout < JOB_STALE_SECONDS <= timeout + 10 * 60
    for root in (PILOT, PROD):
        assert not re.search(r"^\s*job_timeout\s*=", (root / "main.tf").read_text(), re.M), root
