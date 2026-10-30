# The buckets: each client's landing bucket, where certified releases land (the landing contract: releases/<release_id>/
# and current, a text object naming it), and one store bucket the services and the jobs share (STORE_URL): clients/ (the
# packs, the locks, the baselines), deploy/ (the profiles registry and each client's people), work/ (a job's build/
# between executions), releases/ (the released graphs, with current naming the one served) and secrets/ (the passwords'
# hashes, and the passwords until fetched). The image reads and writes them through the storage JSON API; nothing mounts
# them. No bucket is public: public access prevention is enforced and access is IAM alone, granted here and nowhere else.
# A root that asks for it (var.tmp) also gets one tmp bucket every client's service and job share for scratch files
# (TMP_URL), each client's under tmp/<client>/ alone: unversioned, and every object deleted 7 days after it was made.
# A BigQuery load from Cloud Storage needs storage.buckets.get on the bucket as well as storage.objects.get on the file,
# and a grant conditioned on an object prefix cannot meet a check made against the bucket. So with var.tmp the module
# also grants the predefined roles/storage.bucketViewer (storage.buckets.get and storage.buckets.list, no object
# permission) unconditionally on the tmp bucket to each member var.tmp_iam names: it reads the bucket's metadata, never
# lists its objects. It is predefined, not a custom role, because Cloud Storage does not know a custom role made in the
# same apply for some seconds, and the grant naming it would fail the first apply.

locals {
  landing = { for c in var.clients : c => "${var.project_id}-${var.name}-${c}-landing" }
  # The tmp bucket's key, "_tmp", is no client's: a client is a slug, which starts with a letter.
  tmp     = var.tmp ? { _tmp = "${var.project_id}-${var.name}-tmp" } : {}
  buckets = merge({ store = "${var.project_id}-${var.name}-store" }, local.landing, local.tmp)
  labels  = merge({ store = { purpose = "store" }, _tmp = { purpose = "tmp" } }, { for c in var.clients : c => { purpose = "landing", client = c } })
  # Scratch files are kept for days, never versioned: an object is deleted tmp_days after it was made. The store and the
  # landing buckets keep the versions a mistaken overwrite or delete replaced, for 30 days.
  versioned = { for k in keys(local.buckets) : k => k != "_tmp" }
  # Every grant, keyed by names known before apply: "<bucket key>/<grant key>".
  grants = merge(
    { for k, g in var.store_iam : "store/${k}" => merge(g, { bucket = "store" }) },
    merge([for c, gs in var.landing_iam : { for k, g in gs : "${c}/${k}" => merge(g, { bucket = c }) }]...),
    var.tmp ? { for k, g in var.tmp_iam : "_tmp/${k}" => merge(g, { bucket = "_tmp" }) } : {},
    var.tmp ? { for k, g in var.tmp_iam : "_tmp/${k}-bucket-viewer" => { role = "roles/storage.bucketViewer", member = g.member, bucket = "_tmp" } } : {},
  )
}

resource "google_storage_bucket" "this" {
  for_each                    = local.buckets
  project                     = var.project_id
  name                        = each.value
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  deletion_policy             = var.deletion_protection ? "PREVENT" : "DELETE"
  labels                      = merge(var.labels, local.labels[each.key])

  # A landed or published release never changes; versions keep what a mistaken overwrite or delete replaced, for 30 days.
  versioning {
    enabled = local.versioned[each.key]
  }

  dynamic "lifecycle_rule" {
    for_each = local.versioned[each.key] ? [30] : []
    content {
      condition {
        days_since_noncurrent_time = lifecycle_rule.value
        with_state                 = "ARCHIVED"
      }
      action {
        type = "Delete"
      }
    }
  }

  dynamic "lifecycle_rule" {
    for_each = local.versioned[each.key] ? [] : [var.tmp_days]
    content {
      condition {
        age = lifecycle_rule.value
      }
      action {
        type = "Delete"
      }
    }
  }

  dynamic "encryption" {
    for_each = var.kms_key_name == null ? [] : [var.kms_key_name]
    content {
      default_kms_key_name = encryption.value
    }
  }

  lifecycle {
    precondition {
      condition     = length(each.value) <= 63
      error_message = "${each.value}: a bucket name has at most 63 characters."
    }
  }
}

resource "google_storage_bucket_iam_member" "this" {
  for_each = local.grants
  bucket   = google_storage_bucket.this[each.value.bucket].name
  role     = each.value.role
  member   = each.value.member

  dynamic "condition" {
    for_each = try(each.value.condition, null) == null ? [] : [each.value.condition]
    content {
      title       = condition.value.title
      description = condition.value.description
      expression  = condition.value.expression
    }
  }
}
