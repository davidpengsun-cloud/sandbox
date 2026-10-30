# What runs the jobs in QA and PROD, where the pilot has someone execute them. A release lands in a client's landing
# bucket (Power Automate, from the client's SharePoint); the bucket tells its Pub/Sub topic of every object written
# under releases/; Eventarc hands each message to the client's workflow, which starts the client's job when the object
# is a release's manifest.json, written last, so the workflow overrides the job's page-request default with onboard.
# Cloud Scheduler runs each client's eval job on eval_schedule, so the A/B is measured on what is served.

resource "google_service_account" "events" {
  project      = module.project.project_id
  account_id   = "${local.name}-events"
  display_name = "${local.name} events (Eventarc, Workflows, Cloud Scheduler)"
}

resource "google_project_iam_member" "events" {
  for_each = toset(["roles/eventarc.eventReceiver", "roles/workflows.invoker"])
  project  = module.project.project_id
  role     = each.value
  member   = google_service_account.events.member
}

# It starts each client's job with the onboard or eval override, and may do nothing else to it.
resource "google_cloud_run_v2_job_iam_member" "events" {
  for_each = module.run
  project  = module.project.project_id
  location = var.region
  name     = each.value.job_name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = google_service_account.events.member
}

resource "google_pubsub_topic" "landed" {
  for_each                   = toset(local.clients)
  project                    = module.project.project_id
  name                       = "${local.name}-${each.key}-landed"
  kms_key_name               = google_kms_crypto_key.data.id
  message_retention_duration = "86400s"
  labels                     = merge(local.labels, { client = each.key })
  depends_on                 = [google_kms_crypto_key_iam_member.agents]
}

resource "google_pubsub_topic_iam_member" "storage_publishes" {
  for_each = toset(local.clients)
  project  = module.project.project_id
  topic    = google_pubsub_topic.landed[each.key].id
  role     = "roles/pubsub.publisher"
  member   = data.google_storage_project_service_account.this.member
}

resource "google_storage_notification" "landed" {
  for_each           = toset(local.clients)
  bucket             = module.storage.landing_buckets[each.key]
  topic              = google_pubsub_topic.landed[each.key].id
  payload_format     = "JSON_API_V1"
  event_types        = ["OBJECT_FINALIZE"]
  object_name_prefix = "releases/"
  depends_on         = [google_pubsub_topic_iam_member.storage_publishes]
}

resource "google_workflows_workflow" "onboard" {
  for_each            = toset(local.clients)
  project             = module.project.project_id
  name                = "${local.name}-${each.key}-onboard"
  region              = var.region
  description         = "Onboards ${each.key}'s release when its manifest lands."
  service_account     = google_service_account.events.email
  source_contents     = file("${path.module}/onboard.workflow.yaml")
  user_env_vars       = { JOB = "projects/${var.project_id}/locations/${var.region}/jobs/${module.run[each.key].job_name}" }
  deletion_protection = true
  labels              = merge(local.labels, { client = each.key })
}

resource "google_eventarc_trigger" "landed" {
  for_each        = toset(local.clients)
  project         = module.project.project_id
  name            = "${local.name}-${each.key}-landed"
  location        = var.region
  service_account = google_service_account.events.email
  labels          = merge(local.labels, { client = each.key })

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = google_pubsub_topic.landed[each.key].id
    }
  }

  destination {
    workflow = google_workflows_workflow.onboard[each.key].id
  }

  depends_on = [google_project_iam_member.events]
}

resource "google_cloud_scheduler_job" "eval" {
  for_each    = toset(local.clients)
  project     = module.project.project_id
  name        = "${local.name}-${each.key}-eval"
  region      = var.region
  description = "Runs ${each.key}'s eval job: the A/B on what is served."
  schedule    = var.eval_schedule
  time_zone   = var.time_zone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${module.run[each.key].job_name}:run"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ args = ["eval"] }] } }))
    oauth_token {
      service_account_email = google_service_account.events.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.events]
}

resource "google_cloud_run_v2_job" "improve" {
  for_each            = toset(local.clients)
  project             = module.project.project_id
  location            = var.region
  name                = "${local.name}-${each.key}-improve"
  deletion_protection = true
  labels              = merge(local.labels, { client = each.key })

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account       = module.identity.service_accounts[each.key].improve
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      encryption_key        = google_kms_crypto_key.data.id
      timeout               = "3600s"
      max_retries           = 0

      containers {
        image = coalesce(var.image, "us-docker.pkg.dev/cloudrun/container/job")
        args  = ["improve"]
        resources {
          limits = { cpu = "2", memory = "4Gi" }
        }
        dynamic "env" {
          for_each = merge({ PROFILE = each.key, STORE_URL = "gs://${module.storage.store_bucket}", LANDING_URL = "gs://${module.storage.landing_buckets[each.key]}" }, local.env)
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_kms_crypto_key_iam_member.agents]
}

resource "google_cloud_run_v2_job_iam_member" "events_improve" {
  for_each = google_cloud_run_v2_job.improve
  project  = module.project.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = google_service_account.events.member
}

resource "google_cloud_scheduler_job" "improve" {
  for_each    = toset(local.clients)
  project     = module.project.project_id
  name        = "${local.name}-${each.key}-improve"
  region      = var.region
  description = "Runs ${each.key}'s improve job: proposes pack candidates from misses and unit content."
  schedule    = var.improve_schedule
  time_zone   = var.time_zone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.improve[each.key].name}:run"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(jsonencode({}))
    oauth_token {
      service_account_email = google_service_account.events.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.events_improve]
}

