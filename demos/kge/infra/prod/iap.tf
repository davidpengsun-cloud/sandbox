# IAP in front of every page (the run module's iap_enabled): IAP's own agent invokes the service, and IAP lets through
# only the members named here, one by one (iap_members). Groups wait for an organization (organization.tf). The page's
# own sign-in is unchanged behind it.

resource "google_project_service_identity" "iap" {
  provider = google-beta
  project  = module.project.project_id
  service  = "iap.googleapis.com"
}

resource "google_cloud_run_v2_service_iam_member" "iap_invokes" {
  for_each = module.run
  project  = module.project.project_id
  location = var.region
  name     = each.value.service_name
  role     = "roles/run.invoker"
  member   = google_project_service_identity.iap.member
}

resource "google_iap_web_cloud_run_service_iam_member" "members" {
  for_each               = merge([for id, r in module.run : { for m in var.iap_members : "${id}/${m}" => { service = r.service_name, member = m } }]...)
  project                = module.project.project_id
  location               = var.region
  cloud_run_service_name = each.value.service
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value.member
}
