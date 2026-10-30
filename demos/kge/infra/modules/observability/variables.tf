variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "name" {
  description = "The prefix of every display name."
  type        = string
}

variable "alert_email" {
  description = "Where the budget alert and the page-down alert are sent."
  type        = string
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "An email address."
  }
}

variable "pages" {
  description = "The pages checked: {profile => the service's https address}."
  type        = map(string)
}

variable "path" {
  description = "The HTTP path checked on every page."
  type        = string
  default     = "/healthz"
}

variable "accepted_status_classes" {
  description = "The answers that count as up: a page's 2xx; behind IAP, its redirect to sign-in (3xx) too; on a private page, its refusal (4xx), which proves only that Cloud Run answers at the page's address, not that the page runs."
  type        = list(string)
  default     = ["STATUS_CLASS_2XX"]
}
