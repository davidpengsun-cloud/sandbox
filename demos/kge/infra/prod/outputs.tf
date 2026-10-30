output "services" {
  description = "Per client, its page's address, behind IAP."
  value       = { for id, r in module.run : id => r.service_uri }
}

output "jobs" {
  description = "Per client, its job: started by its landing bucket's events (onboard) and by Cloud Scheduler (eval)."
  value       = { for id, r in module.run : id => r.job_name }
}

output "improve_jobs" {
  description = "Per client, its improve job: started weekly by Cloud Scheduler under its own proposal-only account."
  value       = { for id, j in google_cloud_run_v2_job.improve : id => j.name }
}

output "store_bucket" {
  description = "The store the service and the jobs share (STORE_URL): the packs, the registry and people, the job's work, the releases and the passwords' hashes."
  value       = module.storage.store_bucket
}

output "landing_buckets" {
  description = "Per client, its landing bucket: what Power Automate lands releases in."
  value       = module.storage.landing_buckets
}

output "repository" {
  description = "Where the image is pushed."
  value       = module.registry.repository
}

output "secrets" {
  description = "Per client, its secrets' ids: their values are added after the first apply."
  value       = module.secrets.ids
}

output "kms_key" {
  description = "The environment's key (CMEK)."
  value       = google_kms_crypto_key.data.id
}

output "deployer" {
  description = "The account GitHub Actions deploys as."
  value       = module.identity.deployer.email
}

output "workload_identity_provider" {
  description = "What GitHub Actions authenticates through."
  value       = module.identity.workload_identity_provider
}

output "organization_parts" {
  description = "Whether the organization's parts are made: false until organization_id is set."
  value       = local.organization
}
