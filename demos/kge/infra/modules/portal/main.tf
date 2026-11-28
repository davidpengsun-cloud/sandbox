locals {
  # Where Cerebro keeps each person's conversations, passed only when given: unset, the portal keeps them in memory.
  conversations_env = var.portal_conversations_url == null || var.portal_conversations_url == "" ? {} : { PORTAL_CONVERSATIONS_URL = var.portal_conversations_url }
}

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
    # Given var.egress (the pilot), all outbound traffic leaves through that network, which reaches only the Google
    # APIs it lists and the client's KGE.
    dynamic "vpc_access" {
      for_each = var.egress == null ? [] : [var.egress]
      content {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = vpc_access.value.network
          subnetwork = vpc_access.value.subnetwork
        }
      }
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
          PORTAL_APP_CEREBRO          = var.portal_app_cerebro
          PORTAL_CEREBRO_NAME         = var.portal_cerebro_name
          PORTAL_TABLE_QUERY          = var.portal_table_query
          PORTAL_TABLE_PREVIEW        = var.portal_table_preview
          PORTAL_DQ_FILTERS           = var.portal_dq_filters
          PORTAL_ASK_GUIDE            = var.portal_ask_guide
          PORTAL_ASK_PROVIDER         = var.portal_ask_provider
          PORTAL_ASK_MODEL            = var.portal_ask_model
          PORTAL_ASK_MODEL_PROVIDER   = var.portal_ask_model_provider
          PORTAL_ASK_TIMEOUT          = tostring(var.portal_ask_timeout)
          PORTAL_KGE_TIMEOUT          = tostring(var.portal_kge_timeout)
          PORTAL_SEARCH_TIMEOUT       = tostring(var.portal_search_timeout)
          # Third-party telemetry off, no exception: fixed, not variables (portal/server/src/cerebro/telemetry-off.js).
          LANGSMITH_TRACING      = "false"
          LANGSMITH_TRACING_V2   = "false"
          LANGCHAIN_TRACING      = "false"
          LANGCHAIN_TRACING_V2   = "false"
          LANGSMITH_OTEL_ENABLED = "false"
          LANGCHAIN_OTEL_ENABLED = "false"
          LANGSMITH_GATEWAY      = "false"
          DO_NOT_TRACK           = "1"
          TRACING                = var.tracing ? "cloudtrace" : var.tracing_mode
          TRACE_CLIENT_ID        = var.client
          TRACE_PROPAGATE_TO     = var.kge_url
        }, var.portal_certify_url == null || var.portal_certify_url == "" ? {} : { PORTAL_CERTIFY_URL = var.portal_certify_url }, local.conversations_env)
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
