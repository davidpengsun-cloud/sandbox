variable "project_id" {
  description = "The project (the project module's output); bucket names start with it, so they are unique."
  type        = string
}

variable "name" {
  description = "The prefix after the project id in every bucket's name."
  type        = string
}

variable "location" {
  description = "Where the buckets are: the region the services run in."
  type        = string
}

variable "clients" {
  description = "The profiles deployed, one per client: each gets a landing bucket."
  type        = list(string)
}

variable "landing_iam" {
  description = "Per client, the members granted on its landing bucket: {client => {key => {role, member}}}, each key known before apply."
  type        = map(map(object({ role = string, member = string })))
  default     = {}
  validation {
    condition     = alltrue(flatten([for c, gs in var.landing_iam : [for k, g in gs : !contains(["allUsers", "allAuthenticatedUsers"], g.member)]]))
    error_message = "No bucket is public: allUsers and allAuthenticatedUsers are never granted."
  }
}

variable "store_iam" {
  description = "The members granted on the store bucket: {key => {role, member, condition?}}, each key known before apply."
  type = map(object({
    role   = string
    member = string
    condition = optional(object({
      title       = string
      description = string
      expression  = string
    }))
  }))
  default = {}
  validation {
    condition     = alltrue([for k, g in var.store_iam : !contains(["allUsers", "allAuthenticatedUsers"], g.member)])
    error_message = "No bucket is public: allUsers and allAuthenticatedUsers are never granted."
  }
}

variable "tmp" {
  description = "Whether the module creates the tmp bucket, <project>-<name>-tmp, which every client's service and job share for scratch files (TMP_URL). Off unless a root asks for it."
  type        = bool
  default     = false
}

variable "tmp_days" {
  description = "How many days after it was made an object in the tmp bucket is deleted."
  type        = number
  default     = 7
  validation {
    condition     = var.tmp_days >= 1 && floor(var.tmp_days) == var.tmp_days
    error_message = "tmp_days is a whole number of days, at least 1."
  }
}

variable "tmp_iam" {
  description = "The members granted on the tmp bucket, with var.tmp: {key => {role, member, condition?}}, each key known before apply; a client's grant is conditioned on its own tmp/<client>/ prefix. Each member named also gets the module's storage.buckets.get role on the bucket, unconditioned."
  type = map(object({
    role   = string
    member = string
    condition = optional(object({
      title       = string
      description = string
      expression  = string
    }))
  }))
  default = {}
  validation {
    condition     = alltrue([for k, g in var.tmp_iam : !contains(["allUsers", "allAuthenticatedUsers"], g.member)])
    error_message = "No bucket is public: allUsers and allAuthenticatedUsers are never granted."
  }
}

variable "kms_key_name" {
  description = "The Cloud KMS key the buckets are encrypted with (CMEK), or null for Google's."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Whether Terraform refuses to destroy a bucket (and never empties one to destroy it)."
  type        = bool
}

variable "labels" {
  description = "Labels on every bucket."
  type        = map(string)
  default     = {}
}
