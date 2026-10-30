variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "name" {
  description = "The repository's id."
  type        = string
}

variable "location" {
  description = "The region the repository is in: the services'."
  type        = string
}

variable "writers" {
  description = "Who may push images: {key => member}, each key known before apply."
  type        = map(string)
  default     = {}
}

variable "kms_key_name" {
  description = "The Cloud KMS key the repository is encrypted with (CMEK), or null for Google's."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Whether Terraform refuses to destroy the repository."
  type        = bool
}

variable "labels" {
  description = "Labels on the repository."
  type        = map(string)
  default     = {}
}
