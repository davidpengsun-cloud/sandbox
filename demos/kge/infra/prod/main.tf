# QA and PROD: one configuration, two instances (qa.tfvars and prod.tfvars), each in its own project with its own
# state. The same modules as the pilot, and what a pilot does without: Pub/Sub, Eventarc and Cloud Scheduler run the
# jobs (events.tf), IAP stands in front of every page (iap.tf), the environment's own key encrypts what it holds
# (CMEK, cmek.tf), and nothing can be destroyed by Terraform, the project included. The parts that need a Google Cloud
# organization (VPC Service Controls, organization policies, group-based IAP, workforce federation) wait behind
# organization_id (organization.tf), and PROD refuses to apply while it is null.

locals {
  name = "kge"
  # Each client's profile has the client's id: the job's setup-client writes it into the store's deploy/profiles.yaml.
  clients = sort(var.clients)
  services = concat([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "bigqueryreservation.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "eventarc.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ], local.organization ? ["accesscontextmanager.googleapis.com", "orgpolicy.googleapis.com"] : [])
  secret_env = { SESSION_SECRET = "session-key", SIGNIN_PASSWORDS = "passwords" } # gitleaks:allow: the names of Secret Manager secrets, not their values
  env = merge({
    VERTEX_PROJECT          = var.project_id
    VERTEX_REGION           = var.model_location
    RUN_REGION              = var.region
    BIGQUERY_PROJECT        = var.project_id
    BIGQUERY_DATASET_PREFIX = local.name
  }, var.app_env)
  labels = { app = local.name, environment = var.environment }
}

module "project" {
  source          = "../modules/project"
  project_id      = var.project_id
  name            = "${local.name} ${var.environment}"
  services        = local.services
  billing_account = var.billing_account
  budget_amount   = var.budget_amount
  alert_channels  = module.observability.alert_channels
}

# The project itself cannot be deleted while this lien holds; removing it is a deliberate act of its own.
resource "google_resource_manager_lien" "project" {
  parent       = "projects/${module.project.number}"
  restrictions = ["resourcemanager.projects.delete"]
  origin       = "${local.name}-terraform"
  reason       = "The ${var.environment} environment of the KGE platform: remove this lien to delete the project."
}

module "identity" {
  source            = "../modules/identity"
  project_id        = module.project.project_id
  name              = local.name
  clients           = local.clients
  github_repository = var.github_repository
  improve           = true
}

module "storage" {
  source              = "../modules/storage"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  clients             = local.clients
  kms_key_name        = google_kms_crypto_key.data.id
  deletion_protection = true
  labels              = local.labels
  landing_iam = { for c in local.clients : c => {
    job     = { role = "roles/storage.objectAdmin", member = module.identity.service_accounts[c].job_member }
    improve = { role = "roles/storage.objectViewer", member = module.identity.service_accounts[c].improve_member }
  } }
  store_iam = merge([for c in local.clients : {
    "${c}-service" = { role = "roles/storage.objectViewer", member = module.identity.service_accounts[c].service_member }
    "${c}-service-pack" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-page-writes"
        description = "The ${c} service may change only its own unit drafts and onboarding markers."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/clients/${c}/units/\") || resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/clients/${c}/onboard/\")"
      }
    }
    "${c}-job"     = { role = "roles/storage.objectAdmin", member = module.identity.service_accounts[c].job_member }
    "${c}-improve" = { role = "roles/storage.objectViewer", member = module.identity.service_accounts[c].improve_member }
    "${c}-improve-proposals" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].improve_member
      condition = {
        title       = "${c}-improve-writes"
        description = "The ${c} improve job may change only its own proposals."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/clients/${c}/proposals/\")"
      }
    }
  }]...)
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

module "registry" {
  source              = "../modules/registry"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  writers             = { deployer = module.identity.deployer.member }
  kms_key_name        = google_kms_crypto_key.data.id
  deletion_protection = true
  labels              = local.labels
  depends_on          = [google_kms_crypto_key_iam_member.agents]
}

module "bigquery" {
  source              = "../modules/bigquery"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  clients             = local.clients
  max_slots           = var.bigquery_max_slots
  kms_key_name        = google_kms_crypto_key.data.id
  deletion_protection = true
  labels              = local.labels
  dataset_iam = { for c in local.clients : c => {
    service       = { role = "roles/bigquery.dataViewer", member = module.identity.service_accounts[c].service_member, kinds = ["warehouses"] }
    service_graph = { role = "roles/bigquery.dataEditor", member = module.identity.service_accounts[c].service_member, kinds = ["graph"] }
    job           = { role = "roles/bigquery.dataEditor", member = module.identity.service_accounts[c].job_member, kinds = ["warehouses", "graph"] }
    improve       = { role = "roles/bigquery.dataViewer", member = module.identity.service_accounts[c].improve_member, kinds = ["warehouses", "graph"] }
  } }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

module "secrets" {
  source              = "../modules/secrets"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  clients             = local.clients
  kms_key_name        = google_kms_crypto_key.data.id
  deletion_protection = true
  labels              = local.labels
  accessors           = { for c in local.clients : c => { service = module.identity.service_accounts[c].service_member } }
  depends_on          = [google_kms_crypto_key_iam_member.agents]
}

module "run" {
  source              = "../modules/run"
  for_each            = toset(local.clients)
  project_id          = module.project.project_id
  location            = var.region
  name                = "${local.name}-${each.key}"
  profile             = each.key
  client              = each.key
  image               = var.image
  job_image           = var.image
  service_account     = module.identity.service_accounts[each.key].service
  job_service_account = module.identity.service_accounts[each.key].job
  store_bucket        = module.storage.store_bucket
  landing_bucket      = module.storage.landing_buckets[each.key]
  env                 = local.env
  secret_env          = { for name, secret in local.secret_env : name => module.secrets.ids[each.key][secret] }
  public              = false
  iap_enabled         = true
  kms_key_name        = google_kms_crypto_key.data.id
  deletion_protection = true
  labels              = local.labels
  depends_on          = [google_kms_crypto_key_iam_member.agents]
}

module "observability" {
  source      = "../modules/observability"
  project_id  = module.project.project_id
  name        = "${local.name} ${var.environment}"
  alert_email = var.alert_email
  pages       = { for id, r in module.run : id => r.service_uri }
  path        = var.uptime_path
  # IAP answers a check that has not signed in with its redirect to sign-in: the front door is up.
  accepted_status_classes = ["STATUS_CLASS_2XX", "STATUS_CLASS_3XX"]
}
