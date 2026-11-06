output "service_accounts" {
  description = "Per client, its service's, its job's and (in QA and PROD) its improve job's account: email and IAM member."
  value = { for c in var.clients : c => {
    service        = google_service_account.client["${c}-svc"].email
    service_member = google_service_account.client["${c}-svc"].member
    job            = google_service_account.client["${c}-job"].email
    job_member     = google_service_account.client["${c}-job"].member
    improve        = var.improve ? google_service_account.client["${c}-improve"].email : null
    improve_member = var.improve ? google_service_account.client["${c}-improve"].member : null
  } }
}

output "deployer" {
  description = "The account GitHub Actions deploys as: email and IAM member."
  value = {
    email  = google_service_account.deployer.email
    member = google_service_account.deployer.member
  }
}

output "client_project_grants" {
  description = "The project-level roles held by each runtime account, for least-privilege verification."
  value = { for c in var.clients : c => {
    service = sort([for g in google_project_iam_member.client : g.role if g.member == google_service_account.client["${c}-svc"].member])
    job     = sort([for g in google_project_iam_member.client : g.role if g.member == google_service_account.client["${c}-job"].member])
    improve = var.improve ? sort([for g in google_project_iam_member.client : g.role if g.member == google_service_account.client["${c}-improve"].member]) : []
  } }
}

output "workload_identity_provider" {
  description = "What a workflow's google-github-actions/auth step names as its workload_identity_provider."
  value       = google_iam_workload_identity_pool_provider.github.name
}
