# The pilot: one project, the clients var.clients names (meridian alone by default), each with one Cloud Run service
# and one Cloud Run job running the one image, and one store bucket they share: the packs, the deployment's registry and
# people, the job's work between executions and the released graphs, written by the job's commands (setup-client,
# put-example, release) after installation; and one tmp bucket they share for scratch files, each client's under a
# prefix of its own. No Pub/Sub, Eventarc, Cloud Scheduler or IAP: a job runs when someone executes it, and the page
# signs people in itself, a username and a password per person. QA and PROD add those, in a configuration of their own
# that the release kit does not carry.
# The page is public unless var.public is false, for an organisation that refuses allUsers: then var.invokers alone may
# invoke it.

locals {
  name = "kge"
  # Each client's profile has the client's id: the job's setup-client writes it into the store's deploy/profiles.yaml.
  clients = sort(var.clients)
  services = [
    "aiplatform.googleapis.com", # the model, on Vertex AI
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "bigqueryreservation.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
  # The service reads each secret's latest version as a setting: the pilot sign-in's (issue #48 item 4).
  secret_env = { SESSION_SECRET = "session-key", SIGNIN_PASSWORDS = "passwords" } # gitleaks:allow: the names of Secret Manager secrets, not their values
  env = merge({
    VERTEX_PROJECT          = var.project_id
    VERTEX_REGION           = var.model_location
    RUN_REGION              = var.region
    BIGQUERY_PROJECT        = var.project_id
    BIGQUERY_DATASET_PREFIX = local.name
  }, var.pilot_env, var.app_env)
  labels = { app = local.name, environment = "pilot" }
  # Each client's KGE address, as Cloud Run makes it from the service's name, the project's number and the region: the
  # audience of the caller token the portal sends KGE and KGE verifies (the service cannot be handed its own uri).
  kge_address = { for client in local.clients : client => "https://${local.name}-${client}-${module.project.number}.${var.region}.run.app" }
}

module "project" {
  source          = "../modules/project"
  project_id      = var.project_id
  name            = "${local.name} pilot"
  services        = local.services
  billing_account = var.billing_account
  budget_amount   = var.budget_amount
  alert_channels  = module.observability.alert_channels
}

module "identity" {
  source            = "../modules/identity"
  project_id        = module.project.project_id
  name              = local.name
  clients           = local.clients
  github_repository = var.github_repository
}

module "storage" {
  source              = "../modules/storage"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  clients             = local.clients
  deletion_protection = false
  labels              = local.labels
  # The job writes its landing bucket and the whole store. The service reads the store, and may change only the unit
  # drafts and request markers its page owns. objectUser includes create and overwrite (update/delete) of objects.
  landing_iam = { for c in local.clients : c => {
    job = { role = "roles/storage.objectAdmin", member = module.identity.service_accounts[c].job_member }
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
    "${c}-job" = { role = "roles/storage.objectAdmin", member = module.identity.service_accounts[c].job_member }
  }]...)
  # One tmp bucket for scratch files, shared as the store is: each client's service and job may read, write and delete
  # only under tmp/<client>/, and an object is deleted 7 days after it was made. The module also grants each member here
  # its storage.buckets.get role on the bucket, which a BigQuery load from it needs.
  tmp = true
  tmp_iam = merge([for c in local.clients : { for who, member in {
    service = module.identity.service_accounts[c].service_member
    job     = module.identity.service_accounts[c].job_member
    } : "${c}-${who}" => {
    role   = "roles/storage.objectUser"
    member = member
    condition = {
      title       = "${c}-${who}-tmp"
      description = "The ${c} ${who} may use only its client's own scratch prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-tmp/objects/tmp/${c}/\")"
    }
  } }]...)
}

module "registry" {
  source              = "../modules/registry"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  writers             = { deployer = module.identity.deployer.member }
  deletion_protection = false
  labels              = local.labels
}

module "bigquery" {
  source              = "../modules/bigquery"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  clients             = local.clients
  max_slots           = var.bigquery_max_slots
  deletion_protection = false
  candidates          = true
  labels              = local.labels
  dataset_iam = { for c in local.clients : c => {
    service       = { role = "roles/bigquery.dataEditor", member = module.identity.service_accounts[c].service_member, kinds = ["warehouses"] }
    service_graph = { role = "roles/bigquery.dataEditor", member = module.identity.service_accounts[c].service_member, kinds = ["graph"] }
    job           = { role = "roles/bigquery.dataEditor", member = module.identity.service_accounts[c].job_member, kinds = ["warehouses", "graph", "candidates"] }
  } }
}

module "secrets" {
  source              = "../modules/secrets"
  project_id          = module.project.project_id
  name                = local.name
  location            = var.region
  clients             = local.clients
  deletion_protection = false
  labels              = local.labels
  accessors           = { for c in local.clients : c => { service = module.identity.service_accounts[c].service_member } }
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
  tmp_bucket          = module.storage.tmp_bucket
  env                 = local.env
  secret_env          = { for name, secret in local.secret_env : name => module.secrets.ids[each.key][secret] }
  public              = var.public
  invokers            = var.invokers
  trusted_caller      = { email = module.identity.service_accounts[each.key].service, audience = local.kge_address[each.key] }
  iap_enabled         = false
  deletion_protection = false
  labels              = local.labels
}

module "portal" {
  source              = "../modules/portal"
  for_each            = toset(local.clients)
  project_id          = module.project.project_id
  location            = var.region
  name                = "${local.name}-portal-${each.key}"
  client              = each.key
  profile             = each.key
  image               = var.portal_image
  service_account     = module.identity.service_accounts[each.key].service
  kge_url             = module.run[each.key].service_uri
  kge_service_name    = module.run[each.key].service_name
  kge_caller_audience = local.kge_address[each.key]
  dataset_prefix      = local.name
  public              = var.public
  invokers            = var.invokers
  labels              = local.labels
}

module "observability" {
  source      = "../modules/observability"
  project_id  = module.project.project_id
  name        = "${local.name} pilot"
  alert_email = var.alert_email
  pages       = { for id, r in module.run : id => r.service_uri }
  path        = var.uptime_path
  # A private page answers Google's uptime checkers, which are not its invokers, with a 403 from Cloud Run, before any
  # instance is reached. Counted as up, the check then proves only that Cloud Run answers at the page's address, not
  # that the page runs.
  accepted_status_classes = var.public ? ["STATUS_CLASS_2XX"] : ["STATUS_CLASS_2XX", "STATUS_CLASS_4XX"]
}
