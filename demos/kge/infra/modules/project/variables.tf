variable "project_id" {
  description = "The project, made beforehand (the kit's INSTALL.md)."
  type        = string
}

variable "name" {
  description = "The budget's display name."
  type        = string
}

variable "services" {
  description = "The APIs the configuration uses, enabled here."
  type        = list(string)
}

variable "billing_account" {
  description = "The billing account the project is linked to, XXXXXX-XXXXXX-XXXXXX."
  type        = string
  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account))
    error_message = "A billing account id is three groups of six hexadecimal digits, XXXXXX-XXXXXX-XXXXXX."
  }
}

variable "budget_amount" {
  description = "The monthly budget, in whole units of the billing account's currency."
  type        = number
  validation {
    condition     = var.budget_amount > 0 && floor(var.budget_amount) == var.budget_amount
    error_message = "A whole, positive amount."
  }
}

variable "budget_thresholds" {
  description = "The fractions of the amount actually spent at which the budget alerts."
  type        = list(number)
  default     = [0.5, 0.9, 1.0]
}

variable "alert_channels" {
  description = "The Cloud Monitoring notification channels the budget alerts (the observability module's)."
  type        = list(string)
  default     = []
}
