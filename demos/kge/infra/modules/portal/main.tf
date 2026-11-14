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
    timeout               = "1200s"
    scaling {
      min_instance_count = 1
      max_instance_count = 2
    }
    containers {
      image = var.image
      ports { container_port = 8080 }
      resources {
        limits            = { cpu = "2", memory = "4Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = merge({
          PROFILE                     = var.profile
          PORTAL_BACKEND              = "bigquery"
          KGE_URL                     = var.kge_url
          KGE_AUDIENCE                = var.kge_url
          KGE_CALLER_AUDIENCE         = var.kge_caller_audience
          BIGQUERY_PROJECT            = var.project_id
          BIGQUERY_DATASET_PREFIX     = var.dataset_prefix
          PORTAL_CLIENT_NAME          = var.portal_client_name
          PORTAL_PRODUCT_NAME         = var.portal_product_name
          PORTAL_PRODUCT_LONG_NAME    = var.portal_product_long_name
          PORTAL_PRODUCT_SUBTITLE     = var.portal_product_subtitle
          PORTAL_CONTEXT_ASK_TITLE    = var.portal_context_ask_title
          PORTAL_CONTEXT_ASK_SUBTITLE = var.portal_context_ask_subtitle
          PORTAL_LOGO_TITLE           = var.portal_logo_title
          PORTAL_KNOWLEDGE_HOME       = var.portal_knowledge_home
          PORTAL_APP_CERTIFY          = var.portal_app_certify
          PORTAL_APP_CONTEXT          = var.portal_app_context
          PORTAL_APP_DISCOVER         = var.portal_app_discover
          PORTAL_TABLE_QUERY          = var.portal_table_query
          PORTAL_TABLE_PREVIEW        = var.portal_table_preview
          PORTAL_DQ_FILTERS           = var.portal_dq_filters
          PORTAL_ASK_PROVIDER         = var.portal_ask_provider
          PORTAL_ASK_MODEL            = var.portal_ask_model
          PORTAL_ASK_MODEL_PROVIDER   = var.portal_ask_model_provider
        }, var.portal_certify_url == null || var.portal_certify_url == "" ? {} : { PORTAL_CERTIFY_URL = var.portal_certify_url })
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
