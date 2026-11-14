output "repository" {
  description = "Where the image is pushed: <region>-docker.pkg.dev/<project>/<repository>, then /kge-demo:<tag>."
  value       = "${google_artifact_registry_repository.this.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}

output "deletion_policy" {
  description = "The repository's deletion policy: PREVENT where Terraform refuses to destroy it."
  value       = google_artifact_registry_repository.this.deletion_policy
}
