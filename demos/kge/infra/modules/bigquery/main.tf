# BigQuery, ready for the stage that moves the warehouses, the graph (BigQuery Graph) and the chunk vectors into it:
# an Enterprise reservation that autoscales from a baseline of 0 slots to a capped maximum, so it costs nothing while
# idle and never more than the cap when busy, assigned to the project's queries; and two or three datasets per client, its
# warehouses (written by its service's onboarding steps and its job, kge/adapters/warehouse/bigquery_estate.py), its
# graph with the chunk vectors (kge/estate/load_bigquery.py), each load and sync in tables of its own, and, where `candidates` is set, its
# candidates, where the improvement job builds and checks a candidate's graph and chunks: nothing serves it.

locals {
  candidate_expiration_ms = 7 * 24 * 60 * 60 * 1000
  described = {
    warehouses = "warehouses"
    graph      = "graph and chunk vectors"
    candidates = "candidates: a candidate's graph and chunks, which nothing serves"
  }
  datasets = merge([for c in var.clients : {
    for kind in concat(["warehouses", "graph"], var.candidates ? ["candidates"] : []) : "${c}/${kind}" => {
      client = c
      kind   = kind
      id     = "${var.name}_${replace(c, "-", "_")}_${kind}"
    }
  }]...)
  # Every grant, keyed by names known before apply: "<client>/<kind>/<grant key>".
  grants = merge([for d, ds in local.datasets : {
    for k, g in lookup(var.dataset_iam, ds.client, {}) : "${d}/${k}" => merge(g, { dataset = d }) if contains(g.kinds, ds.kind)
  }]...)
}

resource "google_bigquery_reservation" "this" {
  project       = var.project_id
  name          = var.name
  location      = var.location
  edition       = "ENTERPRISE"
  slot_capacity = 0
  autoscale {
    max_slots = var.max_slots
  }
  deletion_policy = var.deletion_protection ? "PREVENT" : "DELETE"
}

resource "google_bigquery_reservation_assignment" "queries" {
  project     = var.project_id
  location    = var.location
  reservation = google_bigquery_reservation.this.id
  assignee    = "projects/${var.project_id}"
  job_type    = "QUERY"
}

resource "google_bigquery_dataset" "this" {
  for_each                   = local.datasets
  project                    = var.project_id
  dataset_id                 = each.value.id
  location                   = var.location
  description                = "${each.value.client}'s ${local.described[each.value.kind]}."
  delete_contents_on_destroy = !var.deletion_protection
  # A candidate's tables, which the improvement job drops once its check ends, expire after this if a run crashes
  # first. A property graph is no table and does not expire: the next run's own drop, or a hand, removes one.
  default_table_expiration_ms = each.value.kind == "candidates" ? local.candidate_expiration_ms : null
  deletion_policy             = var.deletion_protection ? "PREVENT" : "DELETE"
  labels                      = merge(var.labels, { client = each.value.client, kind = each.value.kind })

  dynamic "default_encryption_configuration" {
    for_each = var.kms_key_name == null ? [] : [var.kms_key_name]
    content {
      kms_key_name = default_encryption_configuration.value
    }
  }
}

resource "google_bigquery_dataset_iam_member" "this" {
  for_each   = local.grants
  project    = var.project_id
  dataset_id = google_bigquery_dataset.this[each.value.dataset].dataset_id
  role       = each.value.role
  member     = each.value.member
}
