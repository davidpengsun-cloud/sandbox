output "store_bucket" {
  description = "The store bucket's name: the image's STORE_URL is gs://<it>."
  value       = google_storage_bucket.this["store"].name
}

output "landing_buckets" {
  description = "Per client, its landing bucket's name."
  value       = { for c in var.clients : c => google_storage_bucket.this[c].name }
}

output "tmp_bucket" {
  description = "The tmp bucket's name, with var.tmp (the image's TMP_URL is gs://<it>), else null."
  value       = var.tmp ? google_storage_bucket.this["_tmp"].name : null
}

output "versioning" {
  description = "Per bucket, whether it keeps the versions an overwrite or delete replaced: every bucket but the tmp one."
  value       = { for k, b in google_storage_bucket.this : b.name => b.versioning[0].enabled }
}

output "lifecycle_rules" {
  description = "Per bucket, its lifecycle rules: the action and the condition's age or days since an object became noncurrent."
  value = { for k, b in google_storage_bucket.this : b.name => [for r in b.lifecycle_rule : {
    action                     = one(r.action).type
    age                        = try(one(r.condition).age, null)
    days_since_noncurrent_time = try(one(r.condition).days_since_noncurrent_time, null)
  }] }
}

output "public_access_prevention" {
  description = "Per bucket, its public access prevention: enforced on each."
  value       = { for k, b in google_storage_bucket.this : b.name => b.public_access_prevention }
}

output "uniform_bucket_level_access" {
  description = "Per bucket, whether access is IAM alone (no object ACLs)."
  value       = { for k, b in google_storage_bucket.this : b.name => b.uniform_bucket_level_access }
}

output "members" {
  description = "Every member granted anything on any bucket."
  value       = sort(distinct([for g in google_storage_bucket_iam_member.this : g.member]))
}

output "grants" {
  description = "Every bucket grant, including its optional IAM condition, for verification."
  value = { for k, g in google_storage_bucket_iam_member.this : k => {
    bucket     = g.bucket
    role       = g.role
    member     = g.member
    conditions = [for c in g.condition : { title = c.title, expression = c.expression }]
  } }
}

output "kms_key_names" {
  description = "Per bucket, the key it is encrypted with, or null."
  value       = { for k, b in google_storage_bucket.this : b.name => try(b.encryption[0].default_kms_key_name, null) }
}

output "deletion_policies" {
  description = "Per bucket, its deletion policy: PREVENT where Terraform refuses to destroy it."
  value       = { for k, b in google_storage_bucket.this : b.name => b.deletion_policy }
}

output "force_destroy" {
  description = "Per bucket, whether a destroy empties it first."
  value       = { for k, b in google_storage_bucket.this : b.name => b.force_destroy }
}
