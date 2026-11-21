# The project's services and its budget. The project itself is made once, by hand, from Cloud Shell (the kit's INSTALL.md):
# with no Google Cloud organization there is nothing for Terraform to create it under, and the Terraform state lives in a
# bucket inside it. Every other module takes project_id from this one, so nothing is made before its API is on.

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "enabled" {
  for_each = toset(var.services)
  project  = var.project_id
  service  = each.value
  # A destroy leaves the APIs on: switching one off breaks whatever else in the project uses it.
  disable_on_destroy = false
}

# The budget watches the project's spend on the billing account and tells the alert channels (the observability
# module's) and the billing account's admins at each threshold of the amount, and when the month's forecast reaches it.
# A budget alerts; it never stops spending.
resource "google_billing_budget" "this" {
  billing_account = var.billing_account
  display_name    = var.name

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      units = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.budget_thresholds
    content {
      threshold_percent = threshold_rules.value
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = var.alert_channels
    disable_default_iam_recipients   = false
  }

  depends_on = [google_project_service.enabled]
}
