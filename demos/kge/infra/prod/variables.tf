variable "environment" {
  description = "Which instance of this configuration: qa or prod, each with its own project, tfvars and state."
  type        = string
  validation {
    condition     = contains(["qa", "prod"], var.environment)
    error_message = "qa or prod."
  }
}

variable "organization_id" {
  description = "The Google Cloud organization the project is in, or null while there is none: its parts (VPC Service Controls, organization policies, group-based IAP, workforce federation) wait for one, and PROD refuses to apply without it."
  type        = string
  default     = null
  validation {
    condition     = var.organization_id != null || var.environment != "prod"
    error_message = "PROD needs a Google Cloud organization: set organization_id. Its VPC Service Controls, organization policies, group-based IAP and workforce federation cannot exist without one."
  }
  validation {
    condition     = var.organization_id == null || can(regex("^[0-9]+$", var.organization_id))
    error_message = "An organization id is its number."
  }
}

variable "project_id" {
  description = "The environment's project, made at bootstrap (the kit's INSTALL.md)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "A project id: 6 to 30 lower-case letters, digits and hyphens, starting with a letter."
  }
}

variable "billing_account" {
  description = "The billing account the project is linked to, for its budget."
  type        = string
}

variable "region" {
  description = "Where everything runs and is held, the environment's key among it."
  type        = string
}

variable "clients" {
  description = "The clients the environment deploys, one Cloud Run service and one job each, and a landing bucket each. Each is a slug, the client's id, and its profile's id in the store's deploy/profiles.yaml (the job's setup-client writes it)."
  type        = list(string)
  default     = ["meridian"]
  validation {
    condition     = length(var.clients) > 0
    error_message = "At least one client."
  }
  validation {
    condition     = alltrue([for c in var.clients : can(regex("^[a-z][a-z0-9-]{0,15}$", c))])
    error_message = "a client id is a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (kge-<client>-svc, -job and -improve) fits Google's 30 characters"
  }
  validation {
    condition     = length(distinct(var.clients)) == length(var.clients)
    error_message = "Each client is named once."
  }
}

variable "github_repository" {
  description = "The repository whose GitHub Actions may push the image and deploy it, owner/name."
  type        = string
}

variable "alert_email" {
  description = "Where the budget and uptime alerts go."
  type        = string
}

variable "uptime_path" {
  description = "The liveness path checked for each service."
  type        = string
  default     = "/health"
}

variable "budget_amount" {
  description = "The monthly budget, in whole units of the billing account's currency."
  type        = number
}

variable "bigquery_max_slots" {
  description = "The most slots BigQuery's reservation autoscales to, from 0."
  type        = number
}

variable "model_location" {
  description = "The Vertex AI location the model is called in."
  type        = string
  default     = "global"
}

variable "app_env" {
  description = "More of the image's settings for the services and the jobs: the model's (issue #48 item 3)."
  type        = map(string)
  default     = {}
}

variable "image" {
  description = "The image the services and the jobs start with, or null for Google's sample until the first deployment; Terraform never moves it after."
  type        = string
  default     = null
}

variable "iap_members" {
  description = "Who IAP lets through to every page, one by one, organization or not: user:<email> or serviceAccount:<email>."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for m in var.iap_members : can(regex("^(user|serviceAccount):[^@\\s]+@[^@\\s]+$", m))])
    error_message = "user:<email> or serviceAccount:<email>; groups go in iap_groups."
  }
}

variable "iap_groups" {
  description = "The groups IAP lets through to every page, by email: an organization's (Cloud Identity), so left out while organization_id is null."
  type        = list(string)
  default     = []
}

variable "allowed_customer_ids" {
  description = "The organization's directory customer ids that IAM may grant to (domain-restricted sharing); empty sets no such policy."
  type        = list(string)
  default     = []
}

variable "workforce_oidc" {
  description = "The client's identity provider (its Microsoft Entra ID tenant) for workforce federation, or null: {issuer_uri, client_id}."
  type        = object({ issuer_uri = string, client_id = string })
  default     = null
}

variable "eval_schedule" {
  description = "When Cloud Scheduler runs each client's eval job (cron, in time_zone): weekly by default."
  type        = string
  default     = "0 3 * * 1"
}

variable "improve_schedule" {
  description = "When Cloud Scheduler runs each client's improve job (cron, in time_zone): weekly by default."
  type        = string
  default     = "0 4 * * 1"
}

variable "time_zone" {
  description = "The time zone eval_schedule and improve_schedule are read in."
  type        = string
  default     = "Etc/UTC"
}
