# The machines' identities. IAM is for machines only: people sign in to the page with its own usernames and passwords
# (the pilot) or through IAP (QA and PROD), never as one of these. Each client has two: its service's, which serves the
# page and reads, and its job's, which runs the job commands and writes; neither can act as another client's. The
# deployer is GitHub Actions', reached through Workload Identity Federation, so no key is ever made or stored.

locals {
  kinds = var.improve ? ["svc", "job", "improve"] : ["svc", "job"]
  # Each client gets a service and a job account; QA and PROD also give the improve job its own account.
  accounts = merge([for c in var.clients : { for k in local.kinds : "${c}-${k}" => { client = c, kind = k == "svc" ? "service" : k } }]...)
  # What every client account may do in the project: call the model on Vertex AI, run BigQuery jobs (its datasets are
  # granted one by one, in the bigquery module), and write its logs and metrics.
  grants = merge([for a in keys(local.accounts) : { for r in var.runtime_roles : "${a}/${r}" => { account = a, role = r } }]...)
}

resource "google_service_account" "client" {
  for_each     = local.accounts
  project      = var.project_id
  account_id   = "${var.name}-${each.key}"
  display_name = "${var.name} ${each.value.client} ${each.value.kind}"
}

resource "google_project_iam_member" "client" {
  for_each = local.grants
  project  = var.project_id
  role     = each.value.role
  member   = google_service_account.client[each.value.account].member
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "${var.name}-deployer"
  display_name = "${var.name} deployer (GitHub Actions)"
}

# The deployer rolls out new revisions (gcloud run deploy, gcloud run jobs update): it acts as each client's accounts
# while doing so, and pushes images (granted on the repository, in the registry module).
resource "google_project_iam_member" "deployer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each           = local.accounts
  service_account_id = google_service_account.client[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name}-github"
  display_name              = "GitHub Actions"
}

# GitHub's OIDC tokens, from this repository's workflows alone: the condition refuses every other repository's.
resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deploys" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
