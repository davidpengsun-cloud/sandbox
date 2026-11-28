output "project_id" {
  description = "The project, once its APIs are on: every other module takes it from here."
  value       = var.project_id
  depends_on  = [google_project_service.enabled]
}

output "number" {
  description = "The project's number, which its service agents and its perimeter are named by."
  value       = data.google_project.this.number
}

output "services" {
  description = "The APIs enabled."
  value       = sort(keys(google_project_service.enabled))
}

output "budget" {
  description = "The budget's resource name."
  value       = google_billing_budget.this.name
}
