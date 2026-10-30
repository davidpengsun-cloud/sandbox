output "reservation" {
  description = "The reservation's id."
  value       = google_bigquery_reservation.this.id
}

output "datasets" {
  description = "Per client, its datasets' ids: warehouses, graph and candidates (null where the root sets no `candidates`)."
  value = { for c in var.clients : c => {
    warehouses = google_bigquery_dataset.this["${c}/warehouses"].dataset_id
    graph      = google_bigquery_dataset.this["${c}/graph"].dataset_id
    candidates = try(google_bigquery_dataset.this["${c}/candidates"].dataset_id, null)
  } }
}

output "deletion_policies" {
  description = "Per dataset, and the reservation, its deletion policy: PREVENT where Terraform refuses to destroy it."
  value = merge(
    { for k, d in google_bigquery_dataset.this : d.dataset_id => d.deletion_policy },
    { (var.name) = google_bigquery_reservation.this.deletion_policy },
  )
}

output "delete_contents_on_destroy" {
  description = "Per dataset, whether a destroy empties it first."
  value       = { for k, d in google_bigquery_dataset.this : d.dataset_id => d.delete_contents_on_destroy }
}

output "default_table_expiration_ms" {
  description = "Per dataset, the default expiration of a table made in it, in milliseconds; null where tables never expire."
  value       = { for k, d in google_bigquery_dataset.this : d.dataset_id => d.default_table_expiration_ms }
}

output "descriptions" {
  description = "Per dataset, its description."
  value       = { for k, d in google_bigquery_dataset.this : d.dataset_id => d.description }
}
