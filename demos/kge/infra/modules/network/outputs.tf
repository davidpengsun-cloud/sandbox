output "network" {
  description = "The network's id, which Cloud Run's direct VPC egress names."
  value       = google_compute_network.this.id
}

output "subnetwork" {
  description = "The subnet's id, where the services and jobs take their addresses."
  value       = google_compute_subnetwork.this.id
}

output "private_google_access" {
  description = "Whether the subnet reaches Google's APIs privately."
  value       = google_compute_subnetwork.this.private_ip_google_access
}

output "routes" {
  description = "The network's routes out: private.googleapis.com's range alone; its default internet route is deleted on create."
  value = {
    default_routes_deleted = google_compute_network.this.delete_default_routes_on_create
    out                    = [google_compute_route.google_apis.dest_range]
  }
}

output "firewall" {
  description = "The egress rules, by name: direction, priority, destinations, what each allows or denies, and whether it is logged."
  value = { for r in [google_compute_firewall.google_apis, google_compute_firewall.deny_all] : r.name => {
    direction    = r.direction
    priority     = r.priority
    destinations = r.destination_ranges
    allow        = [for a in r.allow : { protocol = a.protocol, ports = a.ports == null ? [] : a.ports }]
    deny         = [for d in r.deny : { protocol = d.protocol, ports = d.ports == null ? [] : d.ports }]
    logged       = length(r.log_config) > 0
  } }
}

output "resolves" {
  description = "Every name the network's private zones answer, each with what it answers: the APIs and peers given, and private.googleapis.com."
  value = merge(
    { (google_dns_record_set.private.name) = google_dns_record_set.private.rrdatas },
    { for k, r in google_dns_record_set.apis : r.name => r.rrdatas },
    { for k, r in google_dns_record_set.peers : r.name => r.rrdatas },
  )
}

output "zones" {
  description = "The private zones and the networks that see them."
  value = { for z in [google_dns_managed_zone.googleapis, google_dns_managed_zone.run_app] : z.dns_name => {
    visibility = z.visibility
    networks   = [for n in z.private_visibility_config[0].networks : n.network_url]
  } }
}
