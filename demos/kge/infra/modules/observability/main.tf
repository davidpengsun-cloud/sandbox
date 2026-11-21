# The budget alert and the uptime check. One email channel carries both: the project module's budget tells it at each
# threshold of its amount, and an alert policy tells it when a client's page fails its uptime check. The check asks
# for the page itself, over HTTPS, from Google's checker regions, every 15 minutes (each check wakes the service, which
# otherwise scales to none).

resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "${var.name} alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_uptime_check_config" "page" {
  for_each     = var.pages
  project      = var.project_id
  display_name = "${var.name} ${each.key} page"
  timeout      = "10s"
  period       = "900s"

  http_check {
    path         = var.path
    port         = 443
    use_ssl      = true
    validate_ssl = true
    dynamic "accepted_response_status_codes" {
      for_each = var.accepted_status_classes
      content {
        status_class = accepted_response_status_codes.value
      }
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimsuffix(trimprefix(each.value, "https://"), "/")
    }
  }
}

resource "google_monitoring_alert_policy" "page_down" {
  for_each              = var.pages
  project               = var.project_id
  display_name          = "${var.name} ${each.key} page down"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "${each.key}'s uptime check fails"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.page[each.key].uptime_check_id}\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
      trigger {
        count = 1
      }
    }
  }
}
