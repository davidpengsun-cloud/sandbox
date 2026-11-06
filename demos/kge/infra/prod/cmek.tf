# The environment's own key (CMEK): the buckets, the datasets, the secrets, the image repository, the services' and
# jobs' revisions and the Pub/Sub topics are encrypted with it, so turning it off makes all of them unreadable at once.
# Each service's Google-managed agent is granted the key before anything it encrypts is made. A key is never destroyed:
# Cloud KMS keeps it, and Terraform refuses to try.

resource "google_kms_key_ring" "this" {
  project  = module.project.project_id
  name     = "${local.name}-${var.environment}"
  location = var.region
}

resource "google_kms_crypto_key" "data" {
  name            = "${local.name}-data"
  key_ring        = google_kms_key_ring.this.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days
  deletion_policy = "PREVENT"

  lifecycle {
    prevent_destroy = true
  }
}

# The agents Google makes on demand, made now so they can be granted the key first.
resource "google_project_service_identity" "agents" {
  provider = google-beta
  for_each = toset(["artifactregistry.googleapis.com", "pubsub.googleapis.com", "secretmanager.googleapis.com"])
  project  = module.project.project_id
  service  = each.value
}

data "google_storage_project_service_account" "this" {
  project = module.project.project_id
}

data "google_bigquery_default_service_account" "this" {
  project = module.project.project_id
}

locals {
  cmek_agents = {
    bigquery = data.google_bigquery_default_service_account.this.member
    pubsub   = google_project_service_identity.agents["pubsub.googleapis.com"].member
    registry = google_project_service_identity.agents["artifactregistry.googleapis.com"].member
    run      = "serviceAccount:service-${module.project.number}@serverless-robot-prod.iam.gserviceaccount.com"
    secrets  = google_project_service_identity.agents["secretmanager.googleapis.com"].member
    storage  = data.google_storage_project_service_account.this.member
  }
}

resource "google_kms_crypto_key_iam_member" "agents" {
  for_each      = local.cmek_agents
  crypto_key_id = google_kms_crypto_key.data.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}
