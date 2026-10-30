# One Docker repository, for the one image the services and the jobs all run (Dockerfile). GitHub Actions pushes to it
# (the deployer); Cloud Run pulls from it as its own service agent, which reads a repository of its project unasked.
#
# Cleanup keeps the last 10 tagged images and deletes untagged ones after 7 days. A version kept by a KEEP policy is
# never deleted, so: every tagged version is deleted unless it is among the 10 most recent, and an untagged version
# (a tag moved off it) once it is 7 days old. A revision Cloud Run serves holds its own copy of its image, so cleanup
# never takes a running service down.

resource "google_artifact_registry_repository" "this" {
  project                = var.project_id
  location               = var.location
  repository_id          = var.name
  description            = "The KGE demo's image, for the app and the jobs."
  format                 = "DOCKER"
  kms_key_name           = var.kms_key_name
  cleanup_policy_dry_run = false
  deletion_policy        = var.deletion_protection ? "PREVENT" : "DELETE"
  labels                 = var.labels

  cleanup_policies {
    id     = "keep-last-10"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-older-tagged"
    action = "DELETE"
    condition {
      tag_state = "TAGGED"
    }
  }

  cleanup_policies {
    id     = "delete-untagged-after-7-days"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }
}

resource "google_artifact_registry_repository_iam_member" "writers" {
  for_each   = var.writers
  project    = var.project_id
  location   = google_artifact_registry_repository.this.location
  repository = google_artifact_registry_repository.this.name
  role       = "roles/artifactregistry.writer"
  member     = each.value
}
