output "service_name" { value = google_cloud_run_v2_service.this.name }
output "service_uri" { value = google_cloud_run_v2_service.this.uri }
output "service_account" { value = google_cloud_run_v2_service.this.template[0].service_account }
output "image" { value = google_cloud_run_v2_service.this.template[0].containers[0].image }
output "container_port" { value = google_cloud_run_v2_service.this.template[0].containers[0].ports[0].container_port }
output "env" { value = { for item in google_cloud_run_v2_service.this.template[0].containers[0].env : item.name => item.value } }
output "kge_invoker" { value = [for m in google_cloud_run_v2_service_iam_member.reaches_kge : { service = m.name, role = m.role, member = m.member }] }
