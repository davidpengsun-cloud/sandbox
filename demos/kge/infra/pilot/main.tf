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
    "cloudtrace.googleapis.com", # Cloud Trace, which tracing = true exports to
    "compute.googleapis.com",    # the egress network, its routes and firewall rules
    "dns.googleapis.com",        # the egress network's private zones
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "telemetry.googleapis.com", # the OTLP endpoint Cloud Trace takes traces through
  ]
  # The service reads the pinned session key and the latest passwords as settings.
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
  # The Google APIs the services, jobs and portals may reach (var.egress_allowed), with the regional Vertex AI host where
  # the model is called in a region, Cloud Trace's OTLP endpoint while tracing is on, and Dialogflow CX's global and
  # regional endpoints while agent_studio is on.
  egress_allowed = sort(distinct(concat(
    var.egress_allowed,
    var.model_location == "global" ? [] : ["${var.model_location}-aiplatform.googleapis.com"],
    var.tracing ? ["telemetry.googleapis.com"] : [],
    var.telemetry_cloud_read ? ["cloudtrace.googleapis.com", "monitoring.googleapis.com"] : [],
    var.agent_studio ? ["dialogflow.googleapis.com", "${var.region}-dialogflow.googleapis.com"] : [],
  )))
}

module "project" {
  source          = "../modules/project"
  project_id      = var.project_id
  name            = "${local.name} pilot"
  services        = distinct(concat(local.services, var.agent_studio ? ["dialogflow.googleapis.com", "discoveryengine.googleapis.com"] : []))
  billing_account = var.billing_account
  budget_amount   = var.budget_amount
  alert_channels  = module.observability.alert_channels
}

module "identity" {
  source               = "../modules/identity"
  project_id           = module.project.project_id
  name                 = local.name
  clients              = local.clients
  github_repository    = var.github_repository
  telemetry_cloud_read = var.telemetry_cloud_read
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
  # Its proposal reviews also write: a business owner's accept or a steward's certify rewrites the proposal under
  # proposals/, the certify that completes it lands its pack.yaml, and reading a review record written before kit
  # 0.9.23 writes the upgrade report, review-upgrade.json. The pack and the report are granted as exact objects, never
  # the client's prefix.
  # The feedback log is append-only: the service may add records under feedback/ (objectCreator), never change one.
  # So is the role audit: one entry for each call the role matrix checks, added under access/<client>/role-audit/.
  # The shadow log is append-only too: one observation per compared question, added under shadow/<client>/.
  # The portal runs as the service's account and keeps each person's Cerebro conversations, one object a conversation,
  # under portal/<client>/conversations/: it creates, rewrites (each write conditional on the generation it read) and
  # deletes them there (objectAdmin, conditioned on that prefix), and lists them through the store-wide objectViewer.
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
    "${c}-service-proposals" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-proposal-reviews"
        description = "The ${c} service may change only its improvement proposals, the pack a certified proposal lands, and the review-upgrade report."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/clients/${c}/proposals/\") || resource.name == \"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/clients/${c}/pack.yaml\" || resource.name == \"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/clients/${c}/review-upgrade.json\""
      }
    }
    "${c}-service-questions" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-question-runs"
        description = "The ${c} service may resume only its own question runs."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/questions/${c}/runs/\")"
      }
    }
    "${c}-service-model-quota" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-model-quota"
        description = "The ${c} service may update only its own model quota counters."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/model-quotas/${c}/\")"
      }
    }
    "${c}-service-traces" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-traces"
        description = "The ${c} service may update only its own telemetry records."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/traces/${c}/\")"
      }
    }
    "${c}-service-model-usage" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-model-usage"
        description = "The ${c} service may update only its own model usage records."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/model-usage/${c}/\")"
      }
    }
    "${c}-service-feedback" = {
      role   = "roles/storage.objectCreator"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-feedback"
        description = "The ${c} service may only add feedback records under feedback/, never change or delete one."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/feedback/\")"
      }
    }
    "${c}-service-role-audit" = {
      role   = "roles/storage.objectCreator"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-role-audit"
        description = "The ${c} service may only add role-audit entries under its own role-audit prefix, never change or delete one."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/access/${c}/role-audit/\")"
      }
    }
    "${c}-service-shadow-log" = {
      role   = "roles/storage.objectCreator"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-shadow-log"
        description = "The ${c} service may only add shadow-log observations under its own shadow prefix, never change or delete one."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/shadow/${c}/\")"
      }
    }
    "${c}-service-conversations" = {
      role   = "roles/storage.objectAdmin"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-portal-conversations"
        description = "The ${c} portal, which runs as the service's account, may keep only its people's Cerebro conversations, under portal/${c}/conversations/."
        expression  = "resource.name.startsWith(\"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/portal/${c}/conversations/\")"
      }
    }
    "${c}-service-session" = {
      role   = "roles/storage.objectUser"
      member = module.identity.service_accounts[c].service_member
      condition = {
        title       = "${c}-session-state"
        description = "The ${c} service may merge only its own session state."
        expression  = "resource.name == \"projects/_/buckets/${module.project.project_id}-${local.name}-store/objects/access/${c}/identity/session-state.json\""
      }
    }
    "${c}-job" = { role = "roles/storage.objectAdmin", member = module.identity.service_accounts[c].job_member }
  }]...)
  # One tmp bucket for scratch files, shared as the store is: each client's service and job may read, write and delete
  # only under tmp/<client>/, and an object is deleted 7 days after it was made. The module also grants each member here
  # its storage.buckets.get role on the bucket, which a BigQuery load from it needs. Vertex AI's service agent may read
  # objects under tmp/, where the document reader puts a file too large to send inline (DOCUMENT_READER=gemini).
  tmp               = true
  tmp_model_readers = { model-reader = google_project_service_identity.aiplatform.member }
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

# Vertex AI's service agent, which Google often makes only on first use, made now (once its API is on) so the tmp
# bucket's grant to it applies on a fresh install.
resource "google_project_service_identity" "aiplatform" {
  provider = google-beta
  project  = module.project.project_id
  service  = "aiplatform.googleapis.com"
}

# Conversational Agents (Dialogflow CX)'s service agent, made when agent_studio is on so its Cloud Run invoker grant
# applies in the same run.
resource "google_project_service_identity" "dialogflow" {
  count    = var.agent_studio ? 1 : 0
  provider = google-beta
  project  = module.project.project_id
  service  = "dialogflow.googleapis.com"
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
  create_reservation  = var.bigquery_create_reservation
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
  secrets             = ["passwords", "session-key", "model-usage-key"]
  deletion_protection = false
  labels              = local.labels
  accessors           = { for c in local.clients : c => { service = module.identity.service_accounts[c].service_member } }
}

# The egress: every client's service, job and portal sends all its outbound traffic through one network that reaches
# the Google APIs local.egress_allowed names, and each client's KGE and portal, and refuses every other address.
module "network" {
  source     = "../modules/network"
  project_id = module.project.project_id
  name       = "${local.name}-egress"
  region     = var.region
  apis       = local.egress_allowed
  peers = merge(
    { for c in local.clients : "${c}-kge" => trimprefix(module.run[c].service_uri, "https://") },
    { for c in local.clients : "${c}-portal" => trimprefix(module.portal[c].service_uri, "https://") },
  )
}

module "run" {
  source                 = "../modules/run"
  for_each               = toset(local.clients)
  project_id             = module.project.project_id
  location               = var.region
  name                   = "${local.name}-${each.key}"
  job_name               = lookup(var.job_names, each.key, null)
  profile                = each.key
  client_name            = var.portal_client_name
  product_name           = var.portal_product_name
  client                 = each.key
  image                  = var.image
  job_image              = var.image
  service_account        = module.identity.service_accounts[each.key].service
  job_service_account    = module.identity.service_accounts[each.key].job
  store_bucket           = module.storage.store_bucket
  landing_bucket         = module.storage.landing_buckets[each.key]
  tmp_bucket             = module.storage.tmp_bucket
  env                    = local.env
  secret_env             = merge({ for name, secret in local.secret_env : name => module.secrets.ids[each.key][secret] }, var.model_usage_secret_enabled ? { MODEL_USAGE_KEY = module.secrets.ids[each.key]["model-usage-key"] } : {})
  session_key_version    = var.session_key_version
  public                 = var.public
  invokers               = var.agent_studio ? [for m in var.invokers : m if m != "serviceAccount:service-${module.project.number}@gcp-sa-dialogflow.iam.gserviceaccount.com" && m != "serviceAccount:service-${module.project.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"] : var.invokers
  service_agent_invokers = var.agent_studio ? { aiplatform = google_project_service_identity.aiplatform.member, dialogflow = google_project_service_identity.dialogflow[0].member } : {}
  trusted_caller         = { email = module.identity.service_accounts[each.key].service, audience = local.kge_address[each.key] }
  iap_enabled            = false
  requests_per_instance  = var.requests_per_instance
  tracing                = var.tracing
  tracing_mode           = var.tracing_mode
  egress                 = { network = module.network.network, subnetwork = module.network.subnetwork, allowed = local.egress_allowed }
  min_instances          = var.min_instances
  max_instances          = var.max_instances
  cpu                    = "2"
  memory                 = "4Gi"
  deletion_protection    = false
  labels                 = local.labels
  # The time limits, each variable defaulting to the image's own value; bigquery_job_timeout, null by default, passes nothing.
  time_limits = {
    model_timeout         = var.model_timeout
    model_total_timeout   = var.model_total_timeout
    model_request_timeout = var.model_request_timeout
    bigquery_job_timeout  = var.bigquery_job_timeout
    question_run_timeout  = var.question_run_timeout
    question_poll_seconds = var.question_poll_seconds
    question_start_wait   = var.question_start_wait
  }
}

module "portal" {
  source                      = "../modules/portal"
  for_each                    = toset(local.clients)
  project_id                  = module.project.project_id
  location                    = var.region
  name                        = "${local.name}-portal-${each.key}"
  client                      = each.key
  profile                     = each.key
  image                       = var.portal_image
  portal_client_name          = var.portal_client_name
  portal_product_name         = var.portal_product_name
  portal_product_long_name    = var.portal_product_long_name
  portal_product_subtitle     = var.portal_product_subtitle
  portal_context_ask_title    = var.portal_context_ask_title
  portal_context_ask_subtitle = var.portal_context_ask_subtitle
  portal_logo_title           = var.portal_logo_title
  portal_knowledge_home       = var.portal_knowledge_home
  portal_app_certify          = var.portal_app_certify
  portal_app_context          = var.portal_app_context
  portal_app_discover         = var.portal_app_discover
  portal_app_cerebro          = var.portal_app_cerebro
  portal_cerebro_name         = var.portal_cerebro_name
  portal_table_query          = var.portal_table_query
  portal_table_preview        = var.portal_table_preview
  portal_dq_filters           = var.portal_dq_filters
  portal_ask_guide            = var.portal_ask_guide
  portal_ask_provider         = var.portal_ask_provider
  portal_ask_model            = var.portal_ask_model
  portal_ask_model_provider   = var.portal_ask_model_provider
  portal_certify_url          = var.portal_certify_url
  portal_ask_timeout          = var.portal_ask_timeout
  portal_kge_timeout          = var.portal_kge_timeout
  portal_search_timeout       = var.portal_search_timeout
  portal_conversations_url    = "gs://${module.storage.store_bucket}/portal/${each.key}/conversations"
  service_account             = module.identity.service_accounts[each.key].service
  kge_url                     = module.run[each.key].service_uri
  kge_service_name            = module.run[each.key].service_name
  kge_caller_audience         = local.kge_address[each.key]
  dataset_prefix              = local.name
  public                      = var.public
  invokers                    = var.invokers
  tracing                     = var.tracing
  tracing_mode                = var.tracing_mode
  egress                      = { network = module.network.network, subnetwork = module.network.subnetwork }
  labels                      = local.labels
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
