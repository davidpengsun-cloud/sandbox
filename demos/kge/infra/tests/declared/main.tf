# What a configuration declares, read from its files: a helper for terraform test, never applied. A run block that names
# this module reads config's *.tf and those of every local module they call, and lists the resource types declared,
# where, and whether any file switches IAP on, so a test can say what a configuration can never create, whatever its
# variables. config is a folder of infra/, this folder's grandparent: pilot or prod.

variable "config" {
  description = "The configuration to read: a folder of infra/, pilot or prod."
  type        = string
}

locals {
  root  = "${dirname(dirname(abspath(path.module)))}/${var.config}"
  files = [for f in fileset(local.root, "*.tf") : "${local.root}/${f}"]
  # The local modules the configuration calls (source = "../modules/<name>"), each read whole; none calls another.
  modules = distinct(flatten([for f in local.files : [for m in regexall("(?m)^\\s*source\\s*=\\s*\"(\\.\\.?/[^\"]+)\"", file(f)) : "${local.root}/${m[0]}"]]))
  read    = concat(local.files, flatten([for m in local.modules : [for f in fileset(m, "*.tf") : "${m}/${f}"]]))
  # Each declaration, its type and the file (relative to the configuration's parent folder: pilot/main.tf,
  # modules/run/main.tf) that declares it.
  declared = flatten([for f in local.read : [
    for m in regexall("(?m)^\\s*resource\\s+\"([a-z0-9_]+)\"", file(f)) : { type = m[0], file = trimprefix(abspath(f), "${abspath("${local.root}/..")}/") }
  ]])
}

output "files" {
  description = "Every file read: the configuration's and its modules'."
  value       = local.read
}

output "resource_types" {
  description = "Every resource type declared, in the configuration or a module it calls."
  value       = toset([for d in local.declared : d.type])
}

output "declarations" {
  description = "How many times each resource type is declared."
  value       = { for t in toset([for d in local.declared : d.type]) : t => length([for d in local.declared : d if d.type == t]) }
}

output "declared_in" {
  description = "The files that declare each resource type."
  value       = { for t in toset([for d in local.declared : d.type]) : t => sort(distinct([for d in local.declared : d.file if d.type == t])) }
}

output "reads_outside" {
  description = "Every call of file(), templatefile(), fileset() and the like whose path climbs out of its file's folder (holds ..)."
  value = flatten([for f in local.read : [
    for m in regexall("\\b((?:file[a-z0-9]*|templatefile)\\([^)]*\\))", file(f)) : "${trimprefix(abspath(f), "${abspath("${local.root}/..")}/")}: ${m[0]}" if strcontains(m[0], "..")
  ]])
}

output "iap_switched_on" {
  description = "Whether any file sets iap_enabled to a literal true."
  value       = length(regexall("(?m)^\\s*iap_enabled\\s*=\\s*true\\b", join("\n", [for f in local.read : file(f)]))) > 0
}
