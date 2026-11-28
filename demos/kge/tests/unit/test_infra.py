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

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.terraform_runs import run, terraform

DEMO = Path(__file__).resolve().parents[2]
INFRA = DEMO / "infra"
MODULES = ("project", "identity", "storage", "registry", "bigquery", "run", "secrets", "observability", "portal", "network")
SHARED_MODULES = set(MODULES) - {"portal", "network"}
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
    """The pilot alone calls the portal and network modules; both roots call every other shared module, and modules call no
    other."""
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
        assert pins and len(set(pins.values())) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", next(iter(pins.values()))), (config.name, pins)
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
    for folder, name in ((INFRA / "modules" / "bigquery", "create_reservation"), (PILOT, "bigquery_create_reservation")):
        block = (folder / "variables.tf").read_text().split('variable "' + name + '"', 1)[1].split("\n}\n", 1)[0]
        assert re.search(r"default\s*=\s*false", block)
    assert bq.count("count         = var.create_reservation ? 1 : 0") == 1
    assert re.search(r'resource "google_bigquery_reservation_assignment" "queries"\s*\{\s*count\s*=\s*var.create_reservation \? 1 : 0', bq)
    assert "create_reservation  = var.bigquery_create_reservation" in (PILOT / "main.tf").read_text()


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
    service = re.search(r"service_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, local\.trusted_env, local\.job_name_env, local\.limits_env, local\.question_env, var\.env, local\.egress_env\)", run).group(1)
    job_name_env = re.search(r"job_name_env\s*=\s*var\.job_name != null && var\.job_name != var\.name \? \{([^\n]*)\} : \{\}", run).group(1)
    assert set(re.findall(r"([A-Z_]+)\s*=", job_name_env)) == {"RUN_JOB"}
    trusted = re.search(r"trusted_env\s*=\s*var\.trusted_caller == null \? \{\} : \{([^\n]*)\}", run).group(1)
    job = re.search(r'job_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, local\.limits_env, var\.env, local\.egress_env, \{ TRACING = "off" \}\)', run).group(1)
    limits = re.search(r"limits_env\s*=\s*\{ for name, seconds in \{([^\n]*)\} : name => tostring\(seconds\) if seconds != null \}", run).group(1)
    question = re.search(r"question_env\s*=\s*\{ for name, seconds in \{([^\n]*)\} : name => tostring\(seconds\) if seconds != null \}", run).group(1)
    egress = re.search(r"egress_env\s*=\s*var\.egress == null \? \{\} : \{([^\n]*)\}", run).group(1)
    tmp = re.search(r"tmp_env\s*=\s*var\.tmp_bucket == null \? \{\} : \{([^\n]*)\}", run).group(1)
    assert set(re.findall(r"([A-Z_]+)\s*=", service)) == {"PROFILE", "CLIENT_NAME", "PRODUCT_NAME", "SIGNIN", "STORE_URL", "TRACING"}, service
    assert set(re.findall(r"([A-Z_]+)\s*=", job)) == {"PROFILE", "STORE_URL", "LANDING_URL"}, job
    assert set(re.findall(r"([A-Z_]+)\s*=", tmp)) == {"TMP_URL"}, tmp
    assert set(re.findall(r"([A-Z_]+)\s*=", trusted)) == {"TRUSTED_CALLER_EMAIL", "TRUSTED_CALLER_AUDIENCE"}, trusted
    assert set(re.findall(r"([A-Z_]+)\s*=", egress)) == {"EGRESS_ALLOWED"}, egress
    assert set(re.findall(r"([A-Z_]+)\s*=", limits)) == {"MODEL_TIMEOUT", "MODEL_TOTAL_TIMEOUT", "MODEL_REQUEST_TIMEOUT", "BIGQUERY_JOB_TIMEOUT"}, limits
    assert set(re.findall(r"([A-Z_]+)\s*=", question)) == {"QUESTION_RUN_TIMEOUT", "QUESTION_POLL_SECONDS", "QUESTION_START_WAIT"}, question
    for name in re.findall(r"([A-Z_]+)\s*=", secret + env + service + job + tmp + trusted + job_name_env + egress + limits + question):
        assert f'"{name}"' in code, f"{name}: no setting the image reads"
        assert f'    {name}:' in settings_code and f'"{name}"' in settings_code, f"{name}: absent from the settings module"


def test_tracing_is_one_pilot_switch_for_the_service_and_the_portal_and_the_accounts_may_write_traces():
    """The service and portal default to stored traces, can opt out, or use the existing Cloud Trace switch; jobs stay untraced."""
    on_off = 'TRACING = var.tracing ? "cloudtrace" : var.tracing_mode'
    run = (INFRA / "modules" / "run" / "main.tf").read_text()
    service = re.search(r"service_env\s*=\s*merge\(\{([^\n]*)\}", run).group(1)
    job = re.search(r'job_env\s*=\s*merge\(\{([^\n]*)\}, local\.tmp_env, local\.limits_env, var\.env, local\.egress_env, \{ TRACING = "off" \}\)', run).group(1)
    assert on_off in service and "TRACING" not in job and 'var.env, local.egress_env, { TRACING = "off" }' in run
    portal = (INFRA / "modules" / "portal" / "main.tf").read_text()
    assert re.search(r'^\s*TRACING\s*=\s*var\.tracing \? "cloudtrace" : var\.tracing_mode$', portal, re.M)
    assert re.search(r"^\s*TRACE_PROPAGATE_TO\s*=\s*var\.kge_url$", portal, re.M), "the portal sends trace context to its KGE alone"
    assert "TRACE_PROPAGATE_TO" not in run, "KGE sends trace context to nobody"
    for folder in (INFRA / "modules" / "run", INFRA / "modules" / "portal", PILOT):
        block = re.search(r'variable "tracing" \{(.*?)\n\}', (folder / "variables.tf").read_text(), re.S).group(1)
        assert re.search(r"type\s*=\s*bool", block) and re.search(r"default\s*=\s*false", block), folder
        mode = re.search(r'variable "tracing_mode" \{(.*?)\n\}', (folder / "variables.tf").read_text(), re.S).group(1)
        assert re.search(r'default\s*=\s*"store"', mode) and '["store", "off"]' in mode, folder
    pilot = (PILOT / "main.tf").read_text()
    assert len(re.findall(r"^\s*tracing\s*=\s*var\.tracing$", pilot, re.M)) == 2, "the pilot passes tracing to run and portal"
    assert "tracing" not in tf(PROD)
    services = pilot.split("services = [", 1)[1].split("]", 1)[0]
    assert '"cloudtrace.googleapis.com"' in services and '"telemetry.googleapis.com"' in services
    roles = re.search(r'variable "runtime_roles" \{.*?default = \[(.*?)\]', (INFRA / "modules" / "identity" / "variables.tf").read_text(), re.S).group(1)
    assert '"roles/cloudtrace.agent"' in roles
    assert "runtime_roles" not in tf(PILOT) + tf(PROD), "both roots keep the identity module's default roles"
    settings_code = (DEMO / "kge" / "pipeline" / "settings.py").read_text()
    for name in ("TRACING", "TRACING_ENDPOINT", "TRACE_PROPAGATE_TO"):
        assert f"    {name}:" in settings_code and f'"{name}"' in settings_code, name


def test_the_pilot_s_egress_reaches_the_listed_google_apis_alone_and_qa_and_prod_keep_theirs():
    """Issue #478: the pilot sends every client's service, job and portal through one network (direct VPC egress, all
    traffic) with no default internet route, no NAT and no external address, whose firewall allows HTTPS to
    private.googleapis.com's range alone and denies the rest, logged, and whose private zones resolve only the Google
    APIs egress_allowed lists (with the regional Vertex AI host and, while tracing is on, Cloud Trace's endpoint) and
    each client's KGE and portal. The list is one variable, documented in the install guide. QA and PROD keep Cloud
    Run's own egress: they name no network and pass no egress."""
    network = tf(INFRA / "modules" / "network")
    assert "delete_default_routes_on_create = true" in network and "private_ip_google_access = true" in network
    assert re.search(r'dest_range\s*=\s*local\.vip_range', network) and 'vip_range = "199.36.153.8/30"' in network
    assert not re.search(r'resource\s+"google_compute_(router|router_nat|address|global_address|network_peering)"', tf(PILOT) + network)
    allow = re.search(r'resource "google_compute_firewall" "google_apis" \{(.*?)\n\}', network, re.S).group(1)
    deny = re.search(r'resource "google_compute_firewall" "deny_all" \{(.*?)\n\}', network, re.S).group(1)
    assert '"EGRESS"' in allow and "[local.vip_range]" in allow and 'ports    = ["443"]' in allow and "priority           = 1000" in allow
    assert '"EGRESS"' in deny and '["0.0.0.0/0"]' in deny and 'protocol = "all"' in deny and "priority           = 65534" in deny and "log_config" in deny
    assert 'dns_name    = "googleapis.com."' in network and 'dns_name    = "run.app."' in network
    assert network.count('visibility  = "private"') == 2 and "*." not in network.replace("# ", "")
    for module in ("run", "portal"):
        main = (INFRA / "modules" / module / "main.tf").read_text()
        assert main.count('dynamic "vpc_access"') == (2 if module == "run" else 1) and main.count('egress = "ALL_TRAFFIC"') == main.count('dynamic "vpc_access"'), module
    pilot = tf(PILOT)
    assert re.search(r"^\s*egress\s*=\s*\{ network = module\.network\.network, subnetwork = module\.network\.subnetwork, allowed = local\.egress_allowed \}$", pilot, re.M)
    assert re.search(r"^\s*egress\s*=\s*\{ network = module\.network\.network, subnetwork = module\.network\.subnetwork \}$", pilot, re.M)
    assert '"compute.googleapis.com"' in pilot and '"dns.googleapis.com"' in pilot
    block = re.search(r'variable "egress_allowed" \{.*?default = \[(.*?)\]', (PILOT / "variables.tf").read_text(), re.S).group(1)
    assert re.findall(r'"([^"]+)"', block) == ["aiplatform.googleapis.com", "bigquery.googleapis.com", "run.googleapis.com",
                                               "secretmanager.googleapis.com", "storage.googleapis.com", "www.googleapis.com"]
    assert 'var.tracing ? ["telemetry.googleapis.com"] : []' in pilot
    assert '"${var.model_location}-aiplatform.googleapis.com"' in pilot
    assert "egress" not in tf(PROD), "QA and PROD keep develop's egress"
    guide = (DEMO / "kit" / "INSTALL.md").read_text()
    assert "`egress_allowed`" in guide and "`EGRESS_ALLOWED`" in guide
    for host in re.findall(r'"([^"]+)"', block) + ["telemetry.googleapis.com"]:
        assert f"`{host}`" in guide, host


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


def test_no_cloud_run_job_retries_a_failed_task():
    """Issue #192: a retried onboard builds a new graph generation and embeds every chunk again, so every Cloud Run job
    Terraform declares, the run module's onboarding job and QA and PROD's improve job, sets max_retries to 0."""
    declared = {}
    for path in sorted(p for p in INFRA.rglob("*.tf") if ".terraform" not in p.parts):
        text = path.read_text()
        for match in re.finditer(r'^resource "google_cloud_run_v2_job" "([a-z_]+)" \{\n(.*?)^\}', text, re.M | re.S):
            declared[f"{path.relative_to(INFRA).as_posix()}:{match.group(1)}"] = match.group(2)
    assert set(declared) == {"modules/run/main.tf:this", "prod/events.tf:improve"}, sorted(declared)
    for name, block in declared.items():
        assert re.findall(r"^\s*max_retries\s*=\s*(\S+)\s*$", block, re.M) == ["0"], name


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




@pytest.mark.skipif(shutil.which("terraform") is None, reason="no Terraform on the PATH: infra/README.md says how to run these")
def test_terraform_fmt_validate_and_test_pass_against_stand_in_providers(tmp_path):
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    fmt = run(["terraform", "fmt", "-check", "-recursive", "-diff", "-no-color"], cwd=INFRA, env=env, timeout=120)
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
        "TAGGER_PROVIDER": "model", "TAGGER_MODEL_TIER": "medium",
        "EMBEDDING_PROVIDER": "vertex", "VERTEX_EMBEDDING_MODEL": "gemini-embedding-001", "EMBEDDING_DIM": "768",
        "WAREHOUSE_BACKEND": "bigquery", "RETRIEVAL_BACKEND": "bigquery", "APP_STATE_BACKEND": "bigquery",
        "GRAPH_STORE": "bigquery", "DOCUMENT_READER": "gemini",
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


def test_vertex_ais_service_agent_reads_the_tmp_prefix_alone_for_the_document_reader():
    """Issue #271: the Gemini reader sends a file over Vertex AI's inline limit by its gs:// address in the client's
    scratch area, which Vertex AI reads as its own service agent. Google often makes that agent only on first use, so
    the pilot makes it with google-beta's service identity once its API is on, as prod/ makes the agents its key is
    granted to, and the storage module grants it roles/storage.objectViewer on the tmp bucket conditioned on tmp/,
    behind its own input, keyed before apply (default {}); the pilot, whose pilot_env selects the Gemini reader, sets it, and PROD, which
    makes no tmp bucket, does not."""
    storage = (INFRA / "modules" / "storage")
    block = re.search(r'variable "tmp_model_readers" \{(.*?)\n\}', (storage / "variables.tf").read_text(), re.S).group(1)
    assert re.search(r"^\s*default\s*=\s*\{\}\s*$", block, re.M), block
    main = (storage / "main.tf").read_text()
    grant = main.split('var.tmp ? { for k, m in var.tmp_model_readers : "_tmp/${k}" => {', 1)[1].split("} } : {},", 1)[0]
    assert 'role   = "roles/storage.objectViewer"' in grant and "member = m" in grant
    assert 'expression  = "resource.name.startsWith(\\"projects/_/buckets/${local.buckets["_tmp"]}/objects/tmp/\\")"' in grant
    pilot = (PILOT / "main.tf").read_text()
    assert re.search(r"^\s*tmp_model_readers\s*=\s*\{\s*model-reader\s*=\s*google_project_service_identity\.aiplatform\.member\s*\}\s*$", pilot, re.M)
    identity = re.search(r'resource "google_project_service_identity" "aiplatform" \{(.*?)\n\}', pilot, re.S).group(1)
    assert re.search(r"^\s*provider\s*=\s*google-beta\s*$", identity, re.M) and 'service  = "aiplatform.googleapis.com"' in identity
    assert "project  = module.project.project_id" in identity, "made once the project module has turned its API on"
    versions = (PILOT / "versions.tf").read_text()
    assert 'provider "google-beta" {' in versions and 'source  = "hashicorp/google-beta"' in versions
    assert "tmp_model_readers" not in (PROD / "main.tf").read_text()
    assert 'DOCUMENT_READER        = "gemini"' in (PILOT / "variables.tf").read_text()


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


PORTAL_TELEMETRY_OFF = dict(re.findall(r'^\s+([A-Z0-9_]+): "([^"]*)",$', (DEMO / "portal" / "server" / "src" / "cerebro" / "telemetry-off.js").read_text(), re.M))


def test_portal_third_party_telemetry_is_off_in_the_kit_and_terraform():
    """LangChain's and LangSmith's switches, as the portal server sets them, are fixed values in both: never settings."""
    assert PORTAL_TELEMETRY_OFF["LANGSMITH_TRACING"] == "false" and len(PORTAL_TELEMETRY_OFF) == 8
    compose = yaml.safe_load((DEMO / "kit" / "compose" / "compose.yaml").read_text())["services"]["portal"]["environment"]
    main = (INFRA / "modules" / "portal" / "main.tf").read_text()
    for name, value in PORTAL_TELEMETRY_OFF.items():
        assert compose[name] == value, name
        assert re.search(rf'^\s*{name}\s*=\s*"{value}"$', main, re.M), name


def test_portal_runtime_settings_reach_the_image_and_are_documented():
    """The same image receives all app flags; an unset Certify URL stays absent."""
    names = {"PORTAL_TABLE_QUERY", "PORTAL_TABLE_PREVIEW", "PORTAL_DQ_FILTERS", "PORTAL_ASK_GUIDE", "PORTAL_ASK_PROVIDER", "PORTAL_ASK_MODEL", "PORTAL_ASK_MODEL_PROVIDER", "PORTAL_LOGO_TITLE", "PORTAL_KNOWLEDGE_HOME", "PORTAL_APP_CERTIFY", "PORTAL_APP_CONTEXT", "PORTAL_APP_DISCOVER", "PORTAL_APP_CEREBRO", "PORTAL_CEREBRO_NAME"}
    defaults = {"PORTAL_CERTIFY_URL": None, "PORTAL_CLIENT_NAME": "Client", "PORTAL_CEREBRO_NAME": "Cerebro", "PORTAL_TABLE_QUERY": "off",
                "PORTAL_TABLE_PREVIEW": "off", "PORTAL_LOGO_TITLE": "off", "PORTAL_ASK_PROVIDER": "kge",
                "PORTAL_ASK_MODEL": "", "PORTAL_ASK_MODEL_PROVIDER": "vertex"}
    module = INFRA / "modules" / "portal"
    main = (module / "main.tf").read_text()
    passed = set(re.findall(r"^\s*([A-Z0-9_]+)\s*=", main, re.M))
    assert passed == names | {"PORTAL_ASK_TIMEOUT", "PORTAL_KGE_TIMEOUT", "PORTAL_SEARCH_TIMEOUT", "PORTAL_CLIENT_NAME", "PORTAL_PRODUCT_NAME", "PORTAL_PRODUCT_LONG_NAME", "PORTAL_PRODUCT_SUBTITLE", "PORTAL_CONTEXT_ASK_TITLE", "PORTAL_CONTEXT_ASK_SUBTITLE", "PROFILE", "PORTAL_BACKEND", "KGE_URL", "KGE_AUDIENCE", "KGE_CALLER_AUDIENCE", "BIGQUERY_PROJECT", "BIGQUERY_DATASET_PREFIX", "TRACING", "TRACE_CLIENT_ID", "TRACE_PROPAGATE_TO"} | set(PORTAL_TELEMETRY_OFF)
    assert 'var.portal_certify_url == null || var.portal_certify_url == "" ? {} : { PORTAL_CERTIFY_URL = var.portal_certify_url }' in main
    assert int(re.search(r'timeout\s*=\s*"(\d+)s"', main).group(1)) >= 300
    compose = yaml.safe_load((DEMO / "kit" / "compose" / "compose.yaml").read_text())["services"]["portal"]["environment"]
    server = "\n".join((DEMO / "portal" / "server" / "src" / name).read_text() for name in ("apps.js", "providers.js"))
    assert re.search(r'client_name\s*=\s*var\.portal_client_name\b', (PILOT / "main.tf").read_text())
    run = INFRA / "modules" / "run"
    assert "CLIENT_NAME = var.client_name" in (run / "main.tf").read_text()
    block = re.search(r'variable "client_name" \{(.*?)\n\}', (run / "variables.tf").read_text(), re.S).group(1)
    assert re.search(r'default\s*=\s*"Client"', block)
    app = yaml.safe_load((DEMO / "kit" / "compose" / "compose.yaml").read_text())["services"]["app"]["environment"]
    assert app["CLIENT_NAME"] == compose["PORTAL_CLIENT_NAME"]
    for name in sorted(names | {"PORTAL_CERTIFY_URL", "PORTAL_CLIENT_NAME"}):
        variable = name.lower()
        for folder in (module, PILOT):
            block = re.search(rf'variable "{variable}" \{{(.*?)\n\}}', (folder / "variables.tf").read_text(), re.S).group(1)
            default = defaults.get(name, "on")
            assert re.search(r'default\s*=\s*' + ('null' if default is None else re.escape(f'"{default}"')), block)
        assert re.search(rf'{variable}\s*=\s*var\.{variable}\b', (PILOT / "main.tf").read_text())
        assert name in server
        for example in (DEMO / ".env.example", DEMO / "kit" / "compose" / ".env.example"):
            assert re.search(rf"^{name}=", example.read_text(), re.M)
        compose_default = "" if name in {"PORTAL_CERTIFY_URL", "PORTAL_ASK_PROVIDER"} else defaults.get(name, "on")
        assert compose[name] == "${" + name + ":-" + compose_default + "}"
        for guide in (DEMO / "README.md", DEMO / "kit" / "INSTALL.md"):
            assert f"`{name}`" in guide.read_text()
            assert f"`{variable}`" in guide.read_text()


def test_the_time_limits_are_variables_with_the_images_defaults_and_reach_the_containers_that_read_them():
    """Issue #630 requirement 5: each time limit is a pilot variable defaulting to the image's own value (null for
    bigquery_job_timeout, passed only when set), handed to the run module's time_limits (the model's and BigQuery's to the
    service and the job, the question's to the service alone) or to the portal module, whose own variables keep the same
    defaults and pass each under the name the portal reads (portal/server/src/kge.js). QA and PROD pass none, so the
    image's defaults hold there. The pilot's tests check each value in its container (mocked terraform test)."""
    variables, main = (PILOT / "variables.tf").read_text(), (PILOT / "main.tf").read_text()
    kge = {"model_timeout": "180", "model_total_timeout": "270", "model_request_timeout": "270", "bigquery_job_timeout": "null",
           "question_run_timeout": "900", "question_poll_seconds": "2", "question_start_wait": "5"}
    portal = {"portal_ask_timeout": "285", "portal_kge_timeout": "30", "portal_search_timeout": "30"}
    block = re.search(r"time_limits = \{(.*?)\n  \}", main, re.S).group(1)
    assert dict(re.findall(r"^\s*(\w+)\s*=\s*var\.(\w+)$", block, re.M)) == {name: name for name in kge}
    module = (INFRA / "modules" / "portal" / "variables.tf").read_text()
    portal_main = (INFRA / "modules" / "portal" / "main.tf").read_text()
    server = (DEMO / "portal" / "server" / "src" / "kge.js").read_text()
    for name, default in {**kge, **portal}.items():
        found = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.S).group(1)
        assert re.search(r"type\s*=\s*number\n", found) and re.search(rf"default\s*=\s*{default}\n", found), name
        assert "validation" in found and ("nullable    = false" in found) == (default != "null"), name
    for name, default in portal.items():
        assert re.search(rf"^\s*{name}\s*=\s*var\.{name}$", main, re.M), name
        found = re.search(rf'variable "{name}" \{{(.*?)\n\}}', module, re.S).group(1)
        assert re.search(rf"default\s*=\s*{default}\n", found), name
        setting = name.upper()
        assert re.search(rf"^\s*{setting}\s*=\s*tostring\(var\.{name}\)$", portal_main, re.M), setting
        assert f'timeout("{setting}", {default})' in server, f"{setting}: the portal reads it with default {default}"
    run = (INFRA / "modules" / "run" / "variables.tf").read_text()
    assert re.search(r'variable "time_limits" \{.*?default\s*=\s*\{\}', run, re.S), "the run module passes no limit unless given one"
    assert "time_limits" not in tf(PROD) and "_timeout" not in tf(PROD), "QA and PROD keep the image's defaults"


def test_cerebro_conversations_are_kept_under_the_clients_own_store_prefix_and_documented():
    """Issue #625: the pilot gives each client's portal PORTAL_CONVERSATIONS_URL, gs://<store bucket>/portal/<client>/
    conversations, through the portal module's portal_conversations_url (null by default, which passes nothing), and the
    client's service account, which the portal runs as, objectAdmin on the store conditioned on that prefix alone; the
    store-wide objectViewer it already holds lists it. Compose keeps them on the portal-data volume. The portal's server
    reads the setting (a name under portal/server/src), and the install guide, the demo README and the infra README name
    it. QA and PROD run no portal and are given neither."""
    module = INFRA / "modules" / "portal"
    main = (module / "main.tf").read_text()
    assert ('conversations_env = var.portal_conversations_url == null || var.portal_conversations_url == "" ? {} : '
            '{ PORTAL_CONVERSATIONS_URL = var.portal_conversations_url }') in main
    assert "}, local.conversations_env)" in main
    block = re.search(r'variable "portal_conversations_url" \{(.*?)\n\}', (module / "variables.tf").read_text(), re.S).group(1)
    assert re.search(r"default\s*=\s*null\b", block)
    pilot = (PILOT / "main.tf").read_text()
    assert re.search(r'^\s*portal_conversations_url\s*=\s*"gs://\$\{module\.storage\.store_bucket\}/portal/\$\{each\.key\}/conversations"$',
                     pilot, re.M)
    grants = {name: (role, expression) for name, role, expression in _service_store_grants(PILOT)}
    assert grants["service-conversations"] == (
        "roles/storage.objectAdmin",
        f'resource.name.startsWith("projects/_/buckets/{STORE_BUCKET}/objects/portal/meridian/conversations/")')
    assert re.search(r'"\$\{c\}-service"\s*=\s*\{\s*role\s*=\s*"roles/storage\.objectViewer",\s*member\s*=\s*module\.identity\.service_accounts\[c\]\.service_member\s*\}',
                     pilot), "the store-wide read that lists the conversations"
    assert "conversations" not in tf(PROD)
    compose = yaml.safe_load((DEMO / "kit" / "compose" / "compose.yaml").read_text())["services"]["portal"]
    assert compose["environment"]["PORTAL_CONVERSATIONS_URL"] == "/data/conversations"
    assert "portal-data:/data" in compose["volumes"]
    server = "\n".join(p.read_text() for p in sorted((DEMO / "portal" / "server" / "src").rglob("*.js")))
    assert "PORTAL_CONVERSATIONS_URL" in server, "the portal's server reads PORTAL_CONVERSATIONS_URL"
    for guide in (DEMO / "kit" / "INSTALL.md", DEMO / "README.md", INFRA / "README.md"):
        assert "`PORTAL_CONVERSATIONS_URL`" in guide.read_text(), guide
    assert "`<client>-portal-conversations`, the pilot only" in (INFRA / "README.md").read_text()
    assert "#### Cerebro keeps each person's conversations" in (DEMO / "kit" / "INSTALL.md").read_text()


def test_product_name_settings_reach_both_services_and_are_documented():
    """Issue #303: the product's short name and long form are settings with the ALTA values as defaults, everywhere."""
    long_name = "ALTA: AI-powered Library for Trusted Assets"
    defaults = {"PORTAL_PRODUCT_NAME": "ALTA", "PORTAL_PRODUCT_LONG_NAME": long_name, "PORTAL_PRODUCT_SUBTITLE": "Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it."}
    module = INFRA / "modules" / "portal"
    run = INFRA / "modules" / "run"
    assert "PRODUCT_NAME = var.product_name" in (run / "main.tf").read_text()
    block = re.search(r'variable "product_name" \{(.*?)\n\}', (run / "variables.tf").read_text(), re.S).group(1)
    assert re.search(r'default\s*=\s*"ALTA"', block)
    assert re.search(r'product_name\s*=\s*var\.portal_product_name\b', (PILOT / "main.tf").read_text())
    compose = yaml.safe_load((DEMO / "kit" / "compose" / "compose.yaml").read_text())["services"]
    assert compose["app"]["environment"]["PRODUCT_NAME"] == compose["portal"]["environment"]["PORTAL_PRODUCT_NAME"]
    server = (DEMO / "portal" / "server" / "src" / "brand.js").read_text()
    settings_code = (DEMO / "kge" / "pipeline" / "settings.py").read_text()
    assert '"PRODUCT_NAME"' in settings_code and 'DEFAULT_PRODUCT_NAME = "ALTA"' in settings_code
    assert re.search(r"^PRODUCT_NAME=ALTA\b", (DEMO / ".env.example").read_text(), re.M)
    tfvars = (PILOT / "example.tfvars").read_text()
    for name, default in defaults.items():
        variable = name.lower()
        for folder in (module, PILOT):
            block = re.search(rf'variable "{variable}" \{{(.*?)\n\}}', (folder / "variables.tf").read_text(), re.S).group(1)
            assert f'default     = "{default}"' in block, (folder.name, name)
        assert re.search(rf"^\s*{name}\s*=\s*var\.{variable}\b", (module / "main.tf").read_text(), re.M)
        assert re.search(rf'^\s*{variable}\s*=\s*var\.{variable}\b', (PILOT / "main.tf").read_text(), re.M)
        assert re.search(rf'^{variable}\s*=\s*"{re.escape(default)}"$', tfvars, re.M)
        assert name in server
        for example in (DEMO / ".env.example", DEMO / "kit" / "compose" / ".env.example"):
            assert re.search(rf"^{name}={re.escape(default)}(\s|$)", example.read_text(), re.M), (example, name)
        assert compose["portal"]["environment"][name] == "${" + name + ":-" + default + "}"
        for guide in (DEMO / "README.md", DEMO / "kit" / "INSTALL.md"):
            assert f"`{name}`" in guide.read_text() and f"`{variable}`" in guide.read_text(), (guide.name, name)
    assert "`PRODUCT_NAME`" in (DEMO / "kit" / "INSTALL.md").read_text()


def test_context_ask_header_settings_reach_the_portal_and_are_documented():
    """Issue #440: the Context Ask header's title and subtitle are portal settings with today's text as defaults, everywhere."""
    defaults = {"PORTAL_CONTEXT_ASK_TITLE": "Ask a governed question",
                "PORTAL_CONTEXT_ASK_SUBTITLE": "Certified definitions and figures, answered only from data you may read."}
    module = INFRA / "modules" / "portal"
    compose = yaml.safe_load((DEMO / "kit" / "compose" / "compose.yaml").read_text())["services"]
    server = (DEMO / "portal" / "server" / "src" / "apps.js").read_text()
    tfvars = (PILOT / "example.tfvars").read_text()
    for name, default in defaults.items():
        variable = name.lower()
        for folder in (module, PILOT):
            block = re.search(rf'variable "{variable}" \{{(.*?)\n\}}', (folder / "variables.tf").read_text(), re.S).group(1)
            assert f'default     = "{default}"' in block and "nullable    = false" in block, (folder.name, name)
        assert re.search(rf"^\s*{name}\s*=\s*var\.{variable}\b", (module / "main.tf").read_text(), re.M)
        assert re.search(rf'^\s*{variable}\s*=\s*var\.{variable}\b', (PILOT / "main.tf").read_text(), re.M)
        assert re.search(rf'^{variable}\s*=\s*"{re.escape(default)}"$', tfvars, re.M)
        assert f'"{default}"' in server and f"env.{name}?.trim()" in server
        for example in (DEMO / ".env.example", DEMO / "kit" / "compose" / ".env.example"):
            assert re.search(rf"^{name}={re.escape(default)}$", example.read_text(), re.M), (example, name)
        assert compose["portal"]["environment"][name] == "${" + name + ":-" + default + "}"
        assert name not in compose["app"]["environment"]
        for guide in (DEMO / "README.md", DEMO / "kit" / "INSTALL.md"):
            assert f"| `{name}` | `{default}` | `{variable}`, default as shown |" in guide.read_text(), (guide.name, name)


def test_cloud_run_services_keep_one_warm_instance_with_two_vcpu_and_four_gib():
    """Issue #291: pilot Cloud Run services keep one warm instance and run on 2 vCPU / 4 GiB; QA/PROD stay at module defaults."""
    portal = (INFRA / "modules" / "portal" / "main.tf").read_text()
    pilot = (PILOT / "main.tf").read_text()
    prod = (PROD / "main.tf").read_text()
    run_vars = (INFRA / "modules" / "run" / "variables.tf").read_text()
    assert re.search(r"min_instance_count\s*=\s*1\b", portal)
    assert re.search(r"max_instance_count\s*=\s*2\b", portal)
    assert re.search(r'limits\s*=\s*\{\s*cpu\s*=\s*"2",\s*memory\s*=\s*"4Gi"\s*\}', portal)
    assert re.search(r"min_instances\s*=\s*var\.min_instances\b", pilot)
    assert re.search(r"max_instances\s*=\s*var\.max_instances\b", pilot)
    pilot_vars = (PILOT / "variables.tf").read_text()
    assert re.search(r'variable "min_instances"[^}]*default\s*=\s*1\b', pilot_vars)
    assert re.search(r'variable "max_instances"[^}]*default\s*=\s*10\b', pilot_vars)
    assert re.search(r'cpu\s*=\s*"2"', pilot)
    assert re.search(r'memory\s*=\s*"4Gi"', pilot)
    assert not re.search(r"\b(min_instances|cpu|memory)\s*=", prod)
    assert re.search(r'variable "min_instances"[^}]*default\s*=\s*0\b', run_vars)
    assert re.search(r'variable "cpu"[^}]*default\s*=\s*"1"', run_vars)
    assert re.search(r'variable "memory"[^}]*default\s*=\s*"2Gi"', run_vars)


def test_pilot_session_writes_are_limited_to_the_clients_single_object():
    main = (PILOT / "main.tf").read_text()
    grant = main.split('"${c}-service-session" = {', 1)[1].split('    "${c}-job"', 1)[0]
    assert 'role   = "roles/storage.objectUser"' in grant
    assert 'member = module.identity.service_accounts[c].service_member' in grant
    assert 'expression  = "resource.name == \\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/access/${c}/identity/session-state.json\\""' in grant
    assert "startsWith" not in grant


def test_pilot_feedback_writes_are_create_only_under_the_feedback_prefix():
    """The feedback log (kge/adapters/feedbacklog/objstore.py) puts one object a record under feedback/<release>/ in the
    store, only where none exists: the service may create objects there and nothing else, so a record is never changed
    or deleted, and Cloud Storage's 403 no longer turns POST /ask/feedback into a 503."""
    from kge.adapters.feedbacklog.objstore import PREFIX
    main = (PILOT / "main.tf").read_text()
    grant = main.split('"${c}-service-feedback" = {', 1)[1].split("\n    }\n", 1)[0]
    assert 'role   = "roles/storage.objectCreator"' in grant
    assert "member = module.identity.service_accounts[c].service_member" in grant
    assert (f'expression  = "resource.name.startsWith(\\"projects/_/buckets/${{module.project.project_id}}-${{local.name}}-store'
            f'/objects/{PREFIX}/\\")"') in grant
    assert "objectUser" not in grant and "objectAdmin" not in grant


def test_pilot_shadow_log_writes_are_create_only_under_the_client_shadow_prefix():
    """Issue #516: the shadow log (kge/adapters/shadowlog/objstore.py) puts one object an observation under
    shadow/<client>/<release>/ in the store, only where none exists: the service may create objects under its own
    client's shadow prefix and nothing else, so an observation is never changed or deleted by the service."""
    from kge.adapters.shadowlog.objstore import PREFIX
    main = (PILOT / "main.tf").read_text()
    grant = main.split('"${c}-service-shadow-log" = {', 1)[1].split("\n    }\n", 1)[0]
    assert 'role   = "roles/storage.objectCreator"' in grant
    assert "member = module.identity.service_accounts[c].service_member" in grant
    assert f'/objects/{PREFIX}/${{c}}/\\")"' in grant
    assert "objectUser" not in grant and "objectAdmin" not in grant


STORE_BUCKET = "your-pilot-project-id-kge-store"
CREATES = {"roles/storage.objectCreator", "roles/storage.objectUser", "roles/storage.objectAdmin"}
CHANGES = {"roles/storage.objectUser", "roles/storage.objectAdmin"}


class _Bucket:
    """The store bucket in memory, keeping for each object name the service writes whether it only ever created the
    object (objectCreator allows that) or also replaced or deleted one (objectUser)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.writes: dict[str, bool] = {}

    def open(self, url: str, *_, **__):
        assert url == f"gs://{STORE_BUCKET}" or url.startswith(f"gs://{STORE_BUCKET}/"), url
        return _Store(self, url, url[len(f"gs://{STORE_BUCKET}"):].strip("/"))


class _Store:
    def __init__(self, bucket: _Bucket, url: str, base: str):
        self.bucket, self.url, self.base = bucket, url, base

    def _name(self, key: str) -> str:
        return f"{self.base}/{key}" if self.base else key

    def get(self, key):
        return self.get_version(key)[0]

    def get_version(self, key):
        return self.bucket.objects.get(self._name(key), (None, "0"))

    def put(self, key, data):
        name = self._name(key)
        self.bucket.writes[name] = self.bucket.writes.get(name, False) or name in self.bucket.objects
        generation = str(int(self.get_version(key)[1]) + 1)
        self.bucket.objects[name] = (data, generation)
        return generation

    def put_version(self, key, data, version, *, retry_ambiguous=True):
        from kge.ports import StoreConflict
        if version != self.get_version(key)[1]:
            raise StoreConflict(f"{key} changed concurrently")
        return self.put(key, data)

    def list(self, prefix=""):
        lead = self._name(prefix) if prefix else (f"{self.base}/" if self.base else "")
        return sorted(name[len(self.base) + 1 if self.base else 0:] for name in self.bucket.objects if name.startswith(lead))

    def delete(self, key):
        self.bucket.writes[self._name(key)] = True
        self.bucket.objects.pop(self._name(key), None)


def _service_store_grants(config: Path) -> list[tuple[str, str, str]]:
    """(name, role, expression) of each conditioned store grant to the client's service in a configuration's main.tf,
    for the client meridian on the stand-in project, as Cloud Storage would see it."""
    main = (config / "main.tf").read_text()
    store = main.split("store_iam = merge([for c in local.clients : {", 1)[1].split("}]...)", 1)[0]
    grants = []
    for name, body in re.findall(r'^    "\$\{c\}-([a-z-]+)" += \{(.*?)(?=^    "\$\{c\}-|\Z)', store, re.M | re.S):
        if "service_accounts[c].service_member" not in body:
            continue
        role = re.search(r'role\s*=\s*"([^"]+)"', body).group(1)
        expression = re.search(r'^\s*expression\s*=\s*"(.*)"\s*$', body, re.M)
        if expression is None:
            assert role not in CREATES, f"{config.name} {name}: a write grant to the service must be conditioned"
            continue
        text = (expression.group(1).replace('\\"', '"')
                .replace("${module.project.project_id}-${local.name}-store", STORE_BUCKET).replace("${c}", "meridian"))
        assert "${" not in text, f"{config.name} {name}: {text}"
        grants.append((name, role, text))
    return grants


def _app_state_backend(config: Path) -> str:
    """The APP_STATE_BACKEND a configuration gives the service: the pilot's pilot_env sets it; with none set, the
    image's default, files."""
    found = re.findall(r'^\s*APP_STATE_BACKEND\s*=\s*"([^"]+)"', tf(config), re.M)
    assert len(found) <= 1, (config.name, found)
    return found[0] if found else "files"


def _allows(expression: str, name: str) -> bool:
    """Whether a store grant's condition, the startsWith and == forms the configurations use joined by ||, admits an
    object."""
    admitted = False
    for term in expression.split(" || "):
        prefix = re.fullmatch(r'resource\.name\.startsWith\("([^"]+)"\)', term)
        exact = re.fullmatch(r'resource\.name == "([^"]+)"', term)
        assert prefix or exact, f"a condition form this test does not read: {term}"
        admitted |= name.startswith(prefix.group(1)) if prefix else name == exact.group(1)
    return admitted


def _improvement_proposal(bucket: _Bucket, prof, base_hash: str) -> None:
    """Put a kept improvement proposal, as the improve job leaves it, and its candidate pack in the bucket: one alias
    for the retail unit, drafted, which a business owner accepts and a steward certifies."""
    candidate = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())
    candidate["manifest"]["version"] = "1.3.0"
    candidate.setdefault("terminology", {}).setdefault("aliases", []).append({"phrase": "top-line turnover", "term": "revenue"})
    access = prof.artifacts / prof.client / ("b" * 64) / "access"
    access.mkdir(parents=True)
    (access / "roles.json").write_text(json.dumps(candidate["access"]))
    item = {"id": "alias:top-line turnover", "kind": "alias", "unit": "retail", "section": "terminology.aliases",
            "phrase": "top-line turnover", "term": "revenue", "proposer": "rules", "why": "A comment names it.",
            "evidence": "the comment on retail.revenue.amount", "state": "drafted", "accepted_as": None,
            "accepted_on": None, "certified_as": None, "certified_on": None}
    record = {"format": 1, "id": "open", "base_hash": base_hash, "served_hash": "a" * 64, "base_version": "1.2.0",
              "next_version": "1.3.0", "candidate_hash": "b" * 64, "created_at": "2026-11-18T09:00:00Z",
              "proposer": "rules", "state": "drafted", "kept": True, "after_run_id": "20261118T090000Z-bbbbbb",
              "items": [item]}
    bucket.objects["clients/meridian/proposals/open/proposal.yaml"] = (yaml.safe_dump(record, sort_keys=False).encode(), "1")
    bucket.objects["clients/meridian/proposals/open/meridian/pack.yaml"] = (
        yaml.safe_dump(candidate, sort_keys=False).encode(), "1")


def test_every_store_prefix_the_service_writes_has_a_grant(monkeypatch, tmp_path):
    """Issues #541 and #551: each object listed below that the KGE service writes to the store bucket is admitted, on
    the pilot (infra/pilot/main.tf) and on QA and PROD (infra/prod/main.tf), by one of the service's conditioned store
    grants, with a role that allows the write: objectCreator where it only adds objects, objectUser where it replaces or
    deletes one. Every store the code opens on the store's URL is this in-memory bucket, so each write the drive below
    reaches is seen, whichever module makes it: the role audit, the feedback log, the shadow log, question runs, session
    state, unit drafts, and the pack-store writes the service's routes reach: a business owner's accept and a steward's
    certify of an improvement proposal (POST /onboard/accept and /onboard/certify), which rewrite the proposal and land
    its pack.yaml, and the first read of a review record written before kit 0.9.23, which writes review-upgrade.json.
    The access table is written as an admin's change in Access writes it; a configuration that keeps app state in
    BigQuery (APP_STATE_BACKEND, the pilot's pilot_env) keeps it there, not in the store, and one that leaves the
    image's files backend (QA and PROD) needs a grant for it. The onboarding markers are read from the f-strings
    kge/services/onboard/jobs.py puts, not written, so a changed marker fails the exact-set check. The service's private
    state stores are the adapters that open the store with private=True, the shadow log among them: a new one fails here
    until this test writes through it, and a grant admits it. A write the drive does not reach is not caught;
    portal-metadata/ and catalogue.yaml are written only by python -m kge.estate.onboard.metadata, which no route or job
    calls, and are not covered; a release's audit upgrades a portal-metadata review record it reads, but a release runs
    as the job, under the job's store-wide grant, not as the service."""
    import time
    from types import SimpleNamespace

    import kge.adapters.feedbacklog as feedbacklog
    import kge.adapters.modelusage as modelusage
    import kge.adapters.objstore as objstore
    import kge.adapters.questionstate as questionstate
    import kge.adapters.roleaudit as roleaudit
    import kge.adapters.sessionstate as sessionstate
    import kge.adapters.shadowlog as shadowlog
    import kge.adapters.telemetry.store as telemetry_store
    import kge.packstore as packstore
    from kge.adapters import appstate
    from kge.adapters.appstate.files import FilesState
    from kge.compiler import pack as packs
    from kge.estate import onboard
    from kge.pipeline import review_upgrade
    from kge.pipeline.profile import Profile
    from kge.ports import FeedbackRecord, RoleAuditEntry, ShadowObservation
    from tests.access_fixture import person, seed_access

    private = {path.relative_to(DEMO / "kge" / "adapters").parts[0] for path in (DEMO / "kge" / "adapters").rglob("*.py")
               if re.search(r"open_store\([^\n]*private=True", path.read_text())}
    assert private == {"feedbacklog", "modelusage", "questionstate", "roleaudit", "sessionstate", "shadowlog", "telemetry"}, private
    assert {config.name: _app_state_backend(config) for config in (PILOT, PROD)} == {"pilot": "bigquery", "prod": "files"}

    bucket = _Bucket()
    monkeypatch.setenv("STORE_URL", f"gs://{STORE_BUCKET}")
    monkeypatch.delenv("TMP_URL", raising=False)
    for name in ("SESSION_STATE_BACKEND", "QUESTION_STATE_BACKEND", "APP_STATE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(objstore, "GcsStore", bucket.open)

    clients = tmp_path / "clients"
    (clients / "meridian").mkdir(parents=True)
    for name in ("pack.yaml", "pack.lock"):
        shutil.copy(DEMO / "clients" / "meridian" / name, clients / "meridian" / name)
        bucket.objects[f"clients/meridian/{name}"] = ((clients / "meridian" / name).read_bytes(), "1")
    served = tmp_path / "artifacts" / "meridian" / ("a" * 64) / "access"
    served.mkdir(parents=True)
    (served / "roles.json").write_text(json.dumps(yaml.safe_load((clients / "meridian" / "pack.yaml").read_text())["access"]))
    (tmp_path / "artifacts" / "meridian" / "current").symlink_to("a" * 64)
    prof = seed_access(Profile("meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores",
                               tmp_path / "landing", tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml",
                               None, tmp_path / "profiles.yaml"))
    _improvement_proposal(bucket, prof, packs.read(clients, "meridian", lock=True).hash)
    legacy = {"format": 1, "id": "legacy", "candidate_hash": "c" * 64, "items": [
        {"id": "alias:turnover", "kind": "alias", "unit": "retail", "accepted_by": "old_business",
         "accepted_on": "2026-01-01"}]}
    bucket.objects["clients/meridian/proposals/legacy/proposal.yaml"] = (yaml.safe_dump(legacy).encode(), "1")
    bucket.writes.clear()

    roleaudit.role_audit_log(tmp_path, "meridian").append(
        RoleAuditEntry("2026-11-18T09:00:00+00:00", "engineer@example.com", ("engineer",), "ask", "/ask", "allowed"))
    feedbacklog.feedback_log(tmp_path).append(
        FeedbackRecord("r1", "2026-11-18T09:00:00+00:00", "confirmed", "certified", "rules", {}, "0" * 16))
    shadowlog.shadow_log(tmp_path, "meridian").append(
        ShadowObservation("0" * 32, "2026-11-18T09:00:00.000+00:00", "r1", (), (), (), "abstain", "abstain", "agree",
                          "coded", False, False, False, None))
    runs, run_id = questionstate.for_profile(SimpleNamespace(client="meridian")), "0" * 32
    record = {"id": run_id, "state": "running", "expires_at": time.time() + 60}
    runs.put(run_id, record, runs.put(run_id, record, None))
    import base64
    import uuid
    from datetime import datetime, timezone
    from kge.adapters.model.quota import Caller, admit, quota_caller
    monkeypatch.setenv("MODEL_USAGE_KEY", base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="))
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default": 5}')
    ledger = modelusage.for_profile(prof, modelusage.Actor("meridian", "person", "analyst", "cerebro"), str(uuid.uuid4()))
    question, invocation = str(uuid.uuid4()), str(uuid.uuid4())
    ledger.open_question(question)
    ledger.start_invocation(question, invocation)
    with quota_caller(Caller("meridian", "person", "analyst")):
        admit("test-model", "test")
        admit("test-model", "test")
    session = sessionstate.for_profile(prof)
    session.identity_sign_out("one", int(time.time()) + 60)
    session.identity_sign_out("two", int(time.time()) + 60)
    instant = datetime.now(timezone.utc).isoformat()
    trace_session = str(uuid.uuid4())
    trace_row = {"id": "a" * 16, "trace_id": "b" * 32, "parent_id": None,
                 "name": "kge.model.attempt", "started_at": instant, "ended_at": instant,
                 "attributes": {"kge.agent": "cerebro", "gen_ai.conversation.id": trace_session}}
    telemetry_store.for_profile(prof).append([trace_row])
    telemetry_store.for_profile(prof).append([trace_row])
    units = FilesState(packstore.for_profile(prof))
    units.commit("sales", {"unit": "sales"}, [{"step": "draft"}])
    units.commit("sales", {"unit": "sales"}, [{"step": "draft"}])
    packstore.for_profile(prof).delete("units/sales/")
    appstate.access_state(prof).access_put({"username": person("analyst_retail"), "name": "Retail analyst",
                                            "roles": ["analyst_retail"]}, by=person("admin"), via="api")
    onboard.accept(prof, proposal="open", item="all", as_=person("business_team"), on="2026-11-18")
    onboard.certify(prof, proposal="open", item="all", as_=person("steward_glossary"), on="2026-11-18")
    store = packstore.for_profile(prof)
    review_upgrade.read(store, "proposals/legacy/proposal.yaml", legacy, prof=prof)

    jobs = (DEMO / "kge" / "services" / "onboard" / "jobs.py").read_text()
    markers = set(re.findall(r'f"clients/\{app\.PROFILE\.client\}/([^"]+)"', jobs))
    assert markers == {"onboard/job.json", "onboard/request.json"}, markers
    bucket.writes.update({f"clients/meridian/{marker}": True for marker in markers})

    written = {name.split("/", 1)[0] for name in bucket.writes}
    assert written == {"access", "clients", "feedback", "model-quotas", "model-usage", "questions", "shadow", "traces"}, written
    assert any(name.startswith("access/meridian/role-audit/") for name in bucket.writes)
    assert any(name.startswith("shadow/meridian/") for name in bucket.writes)
    for prefix in ("model-quotas/meridian/", "model-usage/meridian/model-usage/"):
        assert any(name.startswith(prefix) and changed for name, changed in bucket.writes.items()), prefix
    assert "model-usage/meridian/key-fingerprint" in bucket.writes
    assert not any(changed for name, changed in bucket.writes.items()
                   if name.startswith(("feedback/", "access/meridian/role-audit/", "shadow/meridian/"))), (
        "the three logs only add objects")
    access_table = {"access/meridian/access-grants.json", "access/meridian/access-audit.jsonl"}
    reviews = {"clients/meridian/proposals/open/proposal.yaml", "clients/meridian/pack.yaml",
               "clients/meridian/proposals/legacy/proposal.yaml", "clients/meridian/review-upgrade.json"}
    assert access_table | reviews <= set(bucket.writes), sorted(bucket.writes)
    for config in (PILOT, PROD):
        grants = _service_store_grants(config)
        for namespace in ("model-quotas", "model-usage", "traces"):
            own = f"projects/_/buckets/{STORE_BUCKET}/objects/{namespace}/meridian/record"
            other = f"projects/_/buckets/{STORE_BUCKET}/objects/{namespace}/thornbury/record"
            matching = [(role, expression) for _, role, expression in grants if _allows(expression, own) and role in CHANGES]
            assert matching and all(not _allows(expression, other) for _, expression in matching), (config.name, namespace)
        in_bigquery = access_table if _app_state_backend(config) == "bigquery" else set()
        for name, changed in sorted(bucket.writes.items()):
            if name in in_bigquery:
                continue
            full = f"projects/_/buckets/{STORE_BUCKET}/objects/{name}"
            allowed = [grant for grant, role, expression in grants
                       if _allows(expression, full) and role in (CHANGES if changed else CREATES)]
            assert allowed, (f"{config.name}: the service writes {name} ({'replace or delete' if changed else 'create'}) "
                             "with no grant")


def test_agent_studio_is_an_opt_in_pilot_switch_for_dialogflow_discovery_engine_egress_and_private_invokers():
    """Issue #483: agent_studio (false by default) enables dialogflow.googleapis.com and discoveryengine.googleapis.com,
    creates the Dialogflow service agent via google_project_service_identity, adds dialogflow.googleapis.com and
    <region>-dialogflow.googleapis.com to egress_allowed, and when public = false grants roles/run.invoker on each
    client's KGE service to the Vertex AI and Dialogflow service agents."""
    variables = (PILOT / "variables.tf").read_text()
    block = re.search(r'variable "agent_studio" \{(.*?)\n\}', variables, re.S).group(1)
    assert re.search(r"type\s*=\s*bool", block) and re.search(r"default\s*=\s*false", block) and re.search(r"nullable\s*=\s*false", block)
    pilot = (PILOT / "main.tf").read_text()
    assert 'var.agent_studio ? ["dialogflow.googleapis.com", "discoveryengine.googleapis.com"] : []' in pilot
    assert 'var.agent_studio ? ["dialogflow.googleapis.com", "${var.region}-dialogflow.googleapis.com"] : []' in pilot
    identity = re.search(r'resource "google_project_service_identity" "dialogflow" \{(.*?)\n\}', pilot, re.S).group(1)
    assert "count    = var.agent_studio ? 1 : 0" in identity and "provider = google-beta" in identity
    assert 'service  = "dialogflow.googleapis.com"' in identity and "project  = module.project.project_id" in identity
    assert 'invokers               = var.agent_studio ? [for m in var.invokers : m if m != "serviceAccount:service-${module.project.number}@gcp-sa-dialogflow.iam.gserviceaccount.com" && m != "serviceAccount:service-${module.project.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"] : var.invokers' in pilot
    assert "service_agent_invokers = var.agent_studio ? { aiplatform = google_project_service_identity.aiplatform.member, dialogflow = google_project_service_identity.dialogflow[0].member } : {}" in pilot
    assert "agent_studio" not in tf(PROD), "QA and PROD keep their own configuration"


def test_model_usage_key_has_two_phase_secret_mount_and_compose_forwarding():
    """Shared installs create the key container before opting into mounting its seeded value."""
    for environment in ("pilot", "prod"):
        main = (DEMO / "infra" / environment / "main.tf").read_text()
        variables = (DEMO / "infra" / environment / "variables.tf").read_text()
        assert '"model-usage-key"' in main
        assert 'var.model_usage_secret_enabled ? { MODEL_USAGE_KEY = module.secrets.ids[each.key]["model-usage-key"] } : {}' in main
        assert re.search(r'variable "model_usage_secret_enabled"\s*\{[^}]*default\s*=\s*false', variables, re.S)
    compose = (DEMO / "kit" / "compose" / "compose.yaml").read_text()
    assert 'MODEL_USAGE_KEY: ${MODEL_USAGE_KEY:-}' in compose
