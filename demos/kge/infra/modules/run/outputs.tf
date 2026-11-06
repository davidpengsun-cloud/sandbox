output "service_name" {
  description = "The service's name."
  value       = google_cloud_run_v2_service.this.name
}

output "service_uri" {
  description = "The service's address."
  value       = google_cloud_run_v2_service.this.uri
}

output "job_name" {
  description = "The job's name: gcloud run jobs execute <name> --args=<command>."
  value       = google_cloud_run_v2_job.this.name
}

output "job_executor" {
  description = "The service's job-level grant to execute this job without overrides."
  value = {
    name   = google_cloud_run_v2_job_iam_member.service_executes.name
    role   = google_cloud_run_v2_job_iam_member.service_executes.role
    member = google_cloud_run_v2_job_iam_member.service_executes.member
  }
}

output "profile" {
  description = "The profile both serve."
  value       = var.profile
}

output "env" {
  description = "The service's settings, beside its secrets."
  value       = local.service_env
}

output "job_env" {
  description = "The job's settings."
  value       = local.job_env
}

output "job_args" {
  description = "The job's fixed command, which validates the request in the store."
  value       = google_cloud_run_v2_job.this.template[0].template[0].containers[0].args
}

output "invokers" {
  description = "Who is granted roles/run.invoker here: allUsers on a public pilot, the invokers named on a private one, no one on QA and PROD."
  value       = [for m in google_cloud_run_v2_service_iam_member.public : m.member]
}

output "iap_enabled" {
  description = "Whether IAP stands in front of the service."
  value       = google_cloud_run_v2_service.this.iap_enabled == true
}

output "deletion_protection" {
  description = "Whether Terraform refuses to destroy the service, and the job."
  value = {
    service = google_cloud_run_v2_service.this.deletion_protection
    job     = google_cloud_run_v2_job.this.deletion_protection
  }
}
