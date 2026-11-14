variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "name" {
  description = "The prefix of every secret's id: <name>-<client>-<secret>."
  type        = string
}

variable "location" {
  description = "The region the secrets are held in: the services'."
  type        = string
}

variable "clients" {
  description = "The profiles deployed, one per client: each gets its own secrets."
  type        = list(string)
}

variable "secrets" {
  description = "The secrets each client has: the pilot sign-in's hashed passwords and its session key."
  type        = list(string)
  default     = ["passwords", "session-key"]
}

variable "accessors" {
  description = "Per client, who may read its secrets: {client => {key => member}}, each key known before apply."
  type        = map(map(string))
  default     = {}
}

variable "kms_key_name" {
  description = "The Cloud KMS key the secrets are encrypted with (CMEK), or null for Google's."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Whether Terraform refuses to destroy a secret."
  type        = bool
}

variable "labels" {
  description = "Labels on every secret."
  type        = map(string)
  default     = {}
}
