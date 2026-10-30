output "services" {
  description = "Per client, its page's address."
  value       = { for id, r in module.run : id => r.service_uri }
}

output "portals" {
  description = "Per client, its discovery portal's address."
  value       = { for id, r in module.portal : id => r.service_uri }
}

output "jobs" {
  description = "Per client, its job: gcloud run jobs execute <job> --region=<region> --args=<command>."
  value       = { for id, r in module.run : id => r.job_name }
}

output "store_bucket" {
  description = "The store the service and the jobs share (STORE_URL): the packs, the registry and people, the job's work, the releases and the passwords' hashes."
  value       = module.storage.store_bucket
}

output "tmp_bucket" {
  description = "The tmp bucket every client's service and job share for scratch files (TMP_URL), each under tmp/<client>/; objects are deleted after 7 days."
  value       = module.storage.tmp_bucket
}

output "landing_buckets" {
  description = "Per client, its landing bucket."
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

output "deployer" {
  description = "The account GitHub Actions deploys as."
  value       = module.identity.deployer.email
}

output "workload_identity_provider" {
  description = "What GitHub Actions authenticates through."
  value       = module.identity.workload_identity_provider
}
