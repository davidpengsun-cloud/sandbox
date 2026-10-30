output "alert_channels" {
  description = "The notification channels the budget alerts: the email channel."
  value       = [google_monitoring_notification_channel.email.id]
}

output "uptime_accepts" {
  description = "Per profile, the answers its page's uptime check counts as up."
  value       = { for k, c in google_monitoring_uptime_check_config.page : k => [for a in c.http_check[0].accepted_response_status_codes : a.status_class] }
}

output "uptime_checks" {
  description = "Per profile, its page's uptime check id."
  value       = { for k, c in google_monitoring_uptime_check_config.page : k => c.uptime_check_id }
}

output "uptime_paths" {
  description = "Per profile, the HTTP path its uptime check requests."
  value       = { for k, c in google_monitoring_uptime_check_config.page : k => c.http_check[0].path }
}
