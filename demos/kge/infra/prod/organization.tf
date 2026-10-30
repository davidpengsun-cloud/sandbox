# The parts that need a Google Cloud organization, each made only once organization_id is set; with none (today) they
# are left out, and PROD refuses to apply (variables.tf). Each is scoped to the environment's project, never the whole
# organization:
#   - VPC Service Controls: a perimeter round the project's data services, in dry run first, so what it would refuse
#     is logged before anything is refused;
#   - organization policies on the project: no service account keys, no public buckets, IAM-only buckets, and
#     domain-restricted sharing where the directory's customer ids are given;
#   - group-based IAP: the groups of iap_groups let through to every page;
#   - workforce federation: the client's people signing in with its own identity provider (its Entra ID tenant).

locals {
  organization = var.organization_id != null
  enforced_constraints = [
    "iam.disableServiceAccountKeyCreation",
    "storage.publicAccessPrevention",
    "storage.uniformBucketLevelAccess",
  ]
  perimeter_services = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudkms.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_access_context_manager_access_policy" "this" {
  count  = local.organization ? 1 : 0
  parent = "organizations/${var.organization_id}"
  title  = "${local.name}-${var.environment}"
  scopes = ["projects/${module.project.number}"]
}

resource "google_access_context_manager_service_perimeter" "this" {
  count                     = local.organization ? 1 : 0
  parent                    = "accessPolicies/${google_access_context_manager_access_policy.this[0].name}"
  name                      = "accessPolicies/${google_access_context_manager_access_policy.this[0].name}/servicePerimeters/${local.name}_${var.environment}"
  title                     = "${local.name}-${var.environment}"
  perimeter_type            = "PERIMETER_TYPE_REGULAR"
  use_explicit_dry_run_spec = true

  spec {
    resources           = ["projects/${module.project.number}"]
    restricted_services = local.perimeter_services
  }
}

resource "google_org_policy_policy" "enforced" {
  for_each = local.organization ? toset(local.enforced_constraints) : toset([])
  name     = "projects/${module.project.project_id}/policies/${each.value}"
  parent   = "projects/${module.project.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "member_domains" {
  count  = local.organization && length(var.allowed_customer_ids) > 0 ? 1 : 0
  name   = "projects/${module.project.project_id}/policies/iam.allowedPolicyMemberDomains"
  parent = "projects/${module.project.project_id}"

  spec {
    rules {
      values {
        allowed_values = var.allowed_customer_ids
      }
    }
  }
}

resource "google_iap_web_cloud_run_service_iam_member" "groups" {
  for_each               = local.organization ? merge([for id, r in module.run : { for g in var.iap_groups : "${id}/${g}" => { service = r.service_name, group = g } }]...) : {}
  project                = module.project.project_id
  location               = var.region
  cloud_run_service_name = each.value.service
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "group:${each.value.group}"
}

resource "google_iam_workforce_pool" "this" {
  count             = local.organization ? 1 : 0
  workforce_pool_id = "${local.name}-${var.environment}"
  parent            = "organizations/${var.organization_id}"
  location          = "global"
  display_name      = "${local.name} ${var.environment}"
  session_duration  = "3600s"
}

resource "google_iam_workforce_pool_provider" "client" {
  count             = local.organization && var.workforce_oidc != null ? 1 : 0
  workforce_pool_id = google_iam_workforce_pool.this[0].workforce_pool_id
  location          = "global"
  provider_id       = "client-idp"
  display_name      = "The client's identity provider"
  attribute_mapping = {
    "google.subject"      = "assertion.sub"
    "google.groups"       = "assertion.groups"
    "google.display_name" = "assertion.name"
  }
  oidc {
    issuer_uri = var.workforce_oidc.issuer_uri
    client_id  = var.workforce_oidc.client_id
    web_sso_config {
      response_type             = "ID_TOKEN"
      assertion_claims_behavior = "ONLY_ID_TOKEN_CLAIMS"
    }
  }
}
