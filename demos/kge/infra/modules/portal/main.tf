resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  location            = var.location
  name                = var.name
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = merge(var.labels, { client = var.client })

  template {
    service_account       = var.service_account
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    timeout               = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = var.image
      ports { container_port = 8080 }
      resources {
        limits            = { cpu = "1", memory = "1Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = {
          PROFILE                 = var.profile
          PORTAL_BACKEND          = "bigquery"
          KGE_URL                 = var.kge_url
          KGE_AUDIENCE            = var.kge_url
          KGE_CALLER_AUDIENCE     = var.kge_caller_audience
          BIGQUERY_PROJECT        = var.project_id
          BIGQUERY_DATASET_PREFIX = var.dataset_prefix
        }
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = var.public ? toset(["allUsers"]) : toset(var.invokers)
  project  = var.project_id
  location = var.location
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}

# A private KGE page lets in only its invokers: the portal, which calls it server to server with its service account's
# Google ID token (X-Serverless-Authorization), is granted that on its paired KGE service alone.
resource "google_cloud_run_v2_service_iam_member" "reaches_kge" {
  count    = var.public ? 0 : 1
  project  = var.project_id
  location = var.location
  name     = var.kge_service_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_account}"
}
