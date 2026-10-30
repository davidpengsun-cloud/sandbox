variable "project_id" {
  description = "The project (the project module's output)."
  type        = string
}

variable "location" {
  description = "The region the service and the job run in."
  type        = string
}

variable "name" {
  description = "The service's name, and the job's: kge-<client>."
  type        = string
}

variable "profile" {
  description = "The profile both serve (PROFILE), in the store's deploy/profiles.yaml."
  type        = string
}

variable "client" {
  description = "The profile's client, whose landing bucket the job reads and writes (LANDING_URL)."
  type        = string
}

variable "image" {
  description = "The service's first image, until a deployment replaces it: Google's public sample."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
  nullable    = false
}

variable "job_image" {
  description = "The job's first image, until a deployment replaces it: Google's public sample."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job"
  nullable    = false
}

variable "service_account" {
  description = "The account the service runs as (the identity module's)."
  type        = string
}

variable "job_service_account" {
  description = "The account the job runs as (the identity module's)."
  type        = string
}

variable "store_bucket" {
  description = "The store bucket both read (STORE_URL): the job writes it, and the service fetches the release it serves from it."
  type        = string
}

variable "landing_bucket" {
  description = "The client's landing bucket, the job's alone (LANDING_URL)."
  type        = string
}

variable "tmp_bucket" {
  description = "The tmp bucket both write scratch files to (TMP_URL), under tmp/<client>/, or null for none: then the image keeps its scratch files in a local folder."
  type        = string
  default     = null
}

variable "env" {
  description = "More of the image's settings, beside PROFILE, STORE_URL, the service's SIGNIN and the job's LANDING_URL: the model's, say."
  type        = map(string)
  default     = {}
}

variable "trusted_caller" {
  description = "Who may name the address a sign-in came from, or null for no one: the service account whose Google ID token, for KGE's own address (audience), KGE verifies itself, and so counts sign-in's per-address limit by the browser's address that caller names (the discovery portal's). The service's TRUSTED_CALLER_EMAIL and TRUSTED_CALLER_AUDIENCE."
  type = object({
    email    = string
    audience = string
  })
  default = null
}

variable "secret_env" {
  description = "Settings the service reads from Secret Manager, at their latest version: {name => secret id}."
  type        = map(string)
  default     = {}
}

variable "public" {
  description = "Whether anyone may reach the service (the pilot, which signs people in itself)."
  type        = bool
}

variable "invokers" {
  description = "Who may invoke the service when it is not public (roles/run.invoker): IAM members, such as domain:example.com. None on QA and PROD, where IAP's own agent invokes it."
  type        = list(string)
  default     = []
  nullable    = false
}

variable "iap_enabled" {
  description = "Whether IAP stands in front of the service (QA and PROD)."
  type        = bool
}

variable "max_instances" {
  description = "The most instances the service scales to."
  type        = number
  default     = 1
}

variable "job_timeout" {
  description = "How long one job execution may run: three hours, so onboard and test-drive on a full pack, three repeats of the margin on live models, finish."
  type        = string
  default     = "10800s"
}

variable "kms_key_name" {
  description = "The Cloud KMS key the service's and the job's revisions are encrypted with (CMEK), or null for Google's."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Whether Terraform refuses to destroy the service or the job."
  type        = bool
}

variable "labels" {
  description = "Labels on the service and the job."
  type        = map(string)
  default     = {}
}
