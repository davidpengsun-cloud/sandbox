variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "name" {
  description = "The prefix every account is named with."
  type        = string
}

variable "clients" {
  description = "The profiles deployed, one per client: each gets a service account and a job account."
  type        = list(string)
  validation {
    condition     = alltrue([for c in var.clients : can(regex("^[a-z][a-z0-9-]{0,15}$", c))])
    error_message = "a client id is a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (kge-<client>-svc, -job and -improve) fits Google's 30 characters"
  }
}

variable "github_repository" {
  description = "The GitHub repository whose workflows may deploy, owner/name."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "owner/name."
  }
}

variable "runtime_roles" {
  description = "The project roles every client account holds."
  type        = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/bigquery.jobUser",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ]
}

variable "improve" {
  description = "Whether each client also gets a dedicated improve job account (QA and PROD)."
  type        = bool
  default     = false
}
