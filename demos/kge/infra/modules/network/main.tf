# The pilot's egress: one VPC network that each client's KGE service, job and portal send all their outbound traffic
# through (Cloud Run's direct VPC egress, ALL_TRAFFIC), and that reaches nothing but Google's APIs, by three layers.
#
# Routes: the network keeps no default route to the internet (delete_default_routes_on_create) and has no Cloud NAT and
# no external address. Its one route out leads to private.googleapis.com's range, 199.36.153.8/30, which Private Google
# Access serves on the subnet.
#
# Firewall: one egress rule allows TCP 443 to that range, and one below it denies every other destination, logged, so a
# refused call shows in Cloud Logging. The metadata server, where the runtime's tokens and identity come from, is not
# subject to firewall rules.
#
# DNS: two private zones, visible to this network alone, answer for googleapis.com and run.app. Only the names in
# var.apis, and each client's KGE and portal addresses in var.peers, resolve, each to the range above; every other name
# in either domain does not resolve. The firewall cannot tell one Google API from another, since they share the range,
# so the per-name list holds by DNS alone: a process that dials the range directly with another name still reaches
# Google's front end. Only VPC Service Controls, which needs an organization, would close that.

locals {
  # private.googleapis.com: Private Google Access's addresses for Google's APIs and run.app.
  vip       = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
  vip_range = "199.36.153.8/30"
}

resource "google_compute_network" "this" {
  project                         = var.project_id
  name                            = var.name
  description                     = "The egress of the KGE services, jobs and portals: Google's APIs alone."
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = true
}

resource "google_compute_subnetwork" "this" {
  project                  = var.project_id
  name                     = var.name
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.cidr
  private_ip_google_access = true
}

resource "google_compute_route" "google_apis" {
  project          = var.project_id
  name             = "${var.name}-google-apis"
  description      = "The one route out: private.googleapis.com's range."
  network          = google_compute_network.this.id
  dest_range       = local.vip_range
  next_hop_gateway = "default-internet-gateway"
  priority         = 1000
}

resource "google_compute_firewall" "google_apis" {
  project            = var.project_id
  name               = "${var.name}-allow-google-apis"
  description        = "Egress to private.googleapis.com's range, HTTPS alone."
  network            = google_compute_network.this.id
  direction          = "EGRESS"
  priority           = 1000
  destination_ranges = [local.vip_range]
  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}

resource "google_compute_firewall" "deny_all" {
  project            = var.project_id
  name               = "${var.name}-deny-all"
  description        = "Every other egress is refused, and logged."
  network            = google_compute_network.this.id
  direction          = "EGRESS"
  priority           = 65534
  destination_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "all"
  }
  log_config {
    metadata = "EXCLUDE_ALL_METADATA"
  }
}

resource "google_dns_managed_zone" "googleapis" {
  project     = var.project_id
  name        = "${var.name}-googleapis"
  dns_name    = "googleapis.com."
  description = "The Google APIs this network may reach; no other googleapis.com name resolves."
  visibility  = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "private" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = local.vip
}

resource "google_dns_record_set" "apis" {
  for_each     = toset(var.apis)
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "${each.key}."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = [google_dns_record_set.private.name]
}

resource "google_dns_managed_zone" "run_app" {
  project     = var.project_id
  name        = "${var.name}-run-app"
  dns_name    = "run.app."
  description = "The KGE and portal services this network may reach; no other run.app name resolves."
  visibility  = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "peers" {
  for_each     = var.peers
  project      = var.project_id
  managed_zone = google_dns_managed_zone.run_app.name
  name         = "${each.value}."
  type         = "A"
  ttl          = 300
  rrdatas      = local.vip
}
