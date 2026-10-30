# One client's Cloud Run service and Cloud Run job, both running the one image: the service serves the page and the API
# (serve), and the job runs whichever job command an execution names (setup-client, put-example, seed, onboard,
# sign-off, release, passwords, releases, eval), or validates the page's request when it names none. Both serve the
# client's profile.
#
# Terraform makes them and never moves their image: a deployment does (gcloud run deploy, gcloud run jobs update, or
# GitHub Actions as the deployer), so the image starts as Google's public sample until the first deployment, and an
# apply after one leaves its image alone.
#
# Nothing is mounted. The image reads and writes the buckets itself, through the storage JSON API as its own account:
# STORE_URL, the store both share (the job's commands write it, and the service fetches the release current names from
# it when it starts), and LANDING_URL, the job's alone, the client's landing bucket (seed lands a release there). Given a
# tmp bucket (var.tmp_bucket), both also get TMP_URL, where each writes scratch files under tmp/<client>/: none else.
# build/ is each instance's own: a job keeps its work in the store between executions, and a release reaches the
# service through the store, served from its next start.

locals {
  tmp_env     = var.tmp_bucket == null ? {} : { TMP_URL = "gs://${var.tmp_bucket}" }
  trusted_env = var.trusted_caller == null ? {} : { TRUSTED_CALLER_EMAIL = var.trusted_caller.email, TRUSTED_CALLER_AUDIENCE = var.trusted_caller.audience }
  service_env = merge({ PROFILE = var.profile, SIGNIN = "passwords", STORE_URL = "gs://${var.store_bucket}" }, local.tmp_env, local.trusted_env, var.env)
  job_env     = merge({ PROFILE = var.profile, STORE_URL = "gs://${var.store_bucket}", LANDING_URL = "gs://${var.landing_bucket}" }, local.tmp_env, var.env)
}

resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  location            = var.location
  name                = var.name
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = var.iap_enabled
  deletion_protection = var.deletion_protection
  labels              = merge(var.labels, { client = var.client })

  template {
    service_account       = var.service_account
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    encryption_key        = var.kms_key_name
    timeout               = "300s"

    # One instance at most: the API holds its graph's file lock, as on a laptop. None while no one asks.
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image
      ports {
        container_port = 8000
      }
      resources {
        limits            = { cpu = "1", memory = "2Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.service_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
}

# Who may invoke the service. The pilot's page is public by default, reached by anyone and signed in to with its own
# usernames and passwords; where an organisation's domain-restricted sharing refuses allUsers, the pilot keeps it private
# and grants the invokers it names instead (issue #69). QA and PROD put IAP in front, and name no one here.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = var.public ? toset(["allUsers"]) : toset(var.invokers)
  project  = var.project_id
  location = var.location
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_job" "this" {
  project             = var.project_id
  location            = var.location
  name                = var.name
  deletion_protection = var.deletion_protection
  labels              = merge(var.labels, { client = var.client })

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account       = var.job_service_account
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      encryption_key        = var.kms_key_name
      timeout               = var.job_timeout
      max_retries           = 0

      containers {
        image = var.job_image
        args  = ["onboard-request"]
        resources {
          limits = { cpu = "2", memory = "4Gi" }
        }
        dynamic "env" {
          for_each = local.job_env
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
}

# The page may start only its paired job, without changing its command or environment.
resource "google_cloud_run_v2_job_iam_member" "service_executes" {
  project  = var.project_id
  location = var.location
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_account}"
}
