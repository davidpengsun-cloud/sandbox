output "service_name" { value = google_cloud_run_v2_service.this.name }
output "service_uri" { value = google_cloud_run_v2_service.this.uri }
output "service_account" { value = google_cloud_run_v2_service.this.template[0].service_account }
output "image" { value = google_cloud_run_v2_service.this.template[0].containers[0].image }
output "container_port" { value = google_cloud_run_v2_service.this.template[0].containers[0].ports[0].container_port }
output "env" { value = { for item in google_cloud_run_v2_service.this.template[0].containers[0].env : item.name => item.value } }
output "kge_invoker" { value = [for m in google_cloud_run_v2_service_iam_member.reaches_kge : { service = m.name, role = m.role, member = m.member }] }

output "request_timeout" {
  description = "The Context request limit, above the supported question lifetime."
  value       = google_cloud_run_v2_service.this.template[0].timeout
}

output "egress" {
  description = "The portal's direct VPC egress: what traffic takes it, and the network and subnet; empty where it has none."
  value       = [for v in google_cloud_run_v2_service.this.template[0].vpc_access : { egress = v.egress, network = v.network_interfaces[0].network, subnetwork = v.network_interfaces[0].subnetwork }]
}
