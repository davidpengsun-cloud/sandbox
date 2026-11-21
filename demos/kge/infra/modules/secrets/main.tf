# Secret Manager entries, with no values: Terraform declares each secret and who may read it, and never holds a value,
# which would sit in its state. Each client's are the pilot sign-in's: its principals' generated passwords, stored
# hashed, and the key its session cookies are signed with, so a session survives a restart. Their values are added
# from Cloud Shell after the first apply (the kit's INSTALL.md); the service reads the latest version when it starts.

locals {
  secrets = merge([for c in var.clients : { for s in var.secrets : "${c}/${s}" => { client = c, secret = s } }]...)
  # Every grant, keyed by names known before apply: "<client>/<secret>/<grant key>".
  grants = merge([for k, s in local.secrets : {
    for g, member in lookup(var.accessors, s.client, {}) : "${k}/${g}" => { secret = k, member = member }
  }]...)
}

resource "google_secret_manager_secret" "this" {
  for_each            = local.secrets
  project             = var.project_id
  secret_id           = "${var.name}-${each.value.client}-${each.value.secret}"
  deletion_protection = var.deletion_protection
  labels              = merge(var.labels, { client = each.value.client })

  # Held in the services' region alone, with the environment's key where there is one.
  replication {
    user_managed {
      replicas {
        location = var.location
        dynamic "customer_managed_encryption" {
          for_each = var.kms_key_name == null ? [] : [var.kms_key_name]
          content {
            kms_key_name = customer_managed_encryption.value
          }
        }
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "accessors" {
  for_each  = local.grants
  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}
