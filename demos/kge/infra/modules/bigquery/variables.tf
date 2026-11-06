variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "name" {
  description = "The reservation's name and the prefix of every dataset's."
  type        = string
}

variable "location" {
  description = "Where the reservation and the datasets are: the region the services run in."
  type        = string
}

variable "clients" {
  description = "The profiles deployed, one per client: each gets its warehouses and its graph dataset."
  type        = list(string)
}

variable "max_slots" {
  description = "The most slots the reservation autoscales to, from its baseline of 0: the cap on what BigQuery can cost."
  type        = number
  validation {
    condition     = var.max_slots >= 50 && var.max_slots % 50 == 0
    error_message = "Autoscaling adds slots 50 at a time: a multiple of 50, at least 50."
  }
}

variable "candidates" {
  description = "Whether each client also gets a candidates dataset, where the improvement job builds and checks a candidate's graph and chunks."
  type        = bool
  default     = false
}

variable "dataset_iam" {
  description = "Per client, the members granted on its datasets: {client => {key => {role, member, kinds}}}, kinds among warehouses, graph and candidates, each key known before apply."
  type        = map(map(object({ role = string, member = string, kinds = list(string) })))
  default     = {}
}

variable "kms_key_name" {
  description = "The Cloud KMS key the datasets are encrypted with (CMEK), or null for Google's."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Whether Terraform refuses to destroy the reservation or a dataset (and never empties one to destroy it)."
  type        = bool
}

variable "labels" {
  description = "Labels on every dataset."
  type        = map(string)
  default     = {}
}
