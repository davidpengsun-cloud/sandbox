output "ids" {
  description = "Per client, its secrets' ids, by secret: {client => {passwords = <id>, session-key = <id>}}."
  value       = { for c in var.clients : c => { for s in var.secrets : s => google_secret_manager_secret.this["${c}/${s}"].secret_id } }
}

output "deletion_protection" {
  description = "Per secret, whether Terraform refuses to destroy it."
  value       = { for k, s in google_secret_manager_secret.this : s.secret_id => s.deletion_protection }
}
