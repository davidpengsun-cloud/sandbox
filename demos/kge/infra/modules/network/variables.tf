variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "name" {
  description = "The network's and its subnet's name, and the prefix of its routes, rules and zones."
  type        = string
}

variable "region" {
  description = "The region of the subnet, the one the services and jobs run in."
  type        = string
}

variable "cidr" {
  description = "The subnet's range, from which Cloud Run gives each instance its addresses: a /24 gives room for some 120 instances. The network is peered with nothing, so it need not avoid another network's ranges."
  type        = string
  default     = "10.8.0.0/24"
}

variable "apis" {
  description = "The Google API host names the network resolves, each under googleapis.com: none else does."
  type        = list(string)
  validation {
    condition     = alltrue([for h in var.apis : can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?\\.googleapis\\.com$", h))]) && length(distinct(var.apis)) == length(var.apis)
    error_message = "Each API is one host name under googleapis.com, no wildcard, each named once."
  }
}

variable "peers" {
  description = "The run.app host names the network resolves, {key => host}: each client's KGE and portal services, so that they reach each other. No other run.app name resolves."
  type        = map(string)
  default     = {}
}
