variable "project_id" { type = string }
variable "location" { type = string }
variable "name" { type = string }
variable "client" { type = string }
variable "profile" { type = string }
variable "image" {
  type     = string
  default  = "us-docker.pkg.dev/cloudrun/container/hello"
  nullable = false
}
variable "service_account" { type = string }
variable "kge_url" { type = string }
variable "kge_service_name" { type = string }
variable "kge_caller_audience" {
  description = "KGE's own address, the audience of the Google ID token the portal sends KGE as its caller token (KGE_CALLER_AUDIENCE), which KGE verifies itself before it counts sign-ins by the browser's address the portal names."
  type        = string
}
variable "dataset_prefix" { type = string }
variable "public" { type = bool }
variable "invokers" {
  type    = list(string)
  default = []
}
variable "labels" {
  type    = map(string)
  default = {}
}
