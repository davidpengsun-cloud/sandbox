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
variable "egress" {
  description = "The network the portal sends all its outbound traffic through (direct VPC egress), or null for Cloud Run's own egress."
  type = object({
    network    = string
    subnetwork = string
  })
  default = null
}
variable "tracing_mode" {
  description = "When Cloud Trace is not enabled, store keeps the service's question traces or off disables tracing."
  type        = string
  default     = "store"
  validation {
    condition     = contains(["store", "off"], var.tracing_mode)
    error_message = "tracing_mode must be store or off."
  }
}

variable "tracing" {
  description = "Whether the portal exports its traces to Cloud Trace, as its service account (TRACING = cloudtrace), or uses tracing_mode (store by default, or off)."
  type        = bool
  default     = false
  nullable    = false
}

variable "portal_client_name" {
  description = "The client's display name for the portal's product and audience. Trimmed; empty uses Client, independently of the profile id."
  type        = string
  default     = "Client"
  nullable    = false
}

variable "portal_product_name" {
  description = "The product's short name, as in <client> ALTA, ALTA home and Ask ALTA, for the portal and KGE's pages. Trimmed; empty uses ALTA."
  type        = string
  default     = "ALTA"
  nullable    = false
}

variable "portal_product_long_name" {
  description = "The product's long form, shown as the portal's name. Trimmed; empty uses the ALTA default."
  type        = string
  default     = "ALTA: AI-powered Library for Trusted Assets"
  nullable    = false
}

variable "portal_product_subtitle" {
  description = "The Discover home subtitle. Trimmed; unset or blank uses the default wording."
  type        = string
  default     = "Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it."
  nullable    = false
}

variable "portal_context_ask_title" {
  description = "The Context Ask header's title. Trimmed; unset or blank uses the default wording."
  type        = string
  default     = "Ask a governed question"
  nullable    = false
}

variable "portal_context_ask_subtitle" {
  description = "The Context Ask header's subtitle. Trimmed; unset or blank uses the default wording."
  type        = string
  default     = "Certified definitions and figures, answered only from data you may read."
  nullable    = false
}

variable "portal_logo_title" {
  description = "Show the product name beside a configured logo. on or off, read when the portal starts."
  type        = string
  default     = "off"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_logo_title)
    error_message = "portal_logo_title must be on or off."
  }
}

variable "portal_knowledge_home" {
  description = "The ALTA home, its Docs and navigation between apps. on or off, read when the portal starts."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_knowledge_home)
    error_message = "portal_knowledge_home must be on or off."
  }
}

variable "portal_app_certify" {
  description = "The Certify stub and its external link. on or off, read when the portal starts."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_app_certify)
    error_message = "portal_app_certify must be on or off."
  }
}

variable "portal_app_context" {
  description = "The Context app inside the portal. on or off, read when the portal starts."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_app_context)
    error_message = "portal_app_context must be on or off."
  }
}

variable "portal_app_discover" {
  description = "The Discover app inside the portal. on or off, read when the portal starts."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_app_discover)
    error_message = "portal_app_discover must be on or off."
  }
}

variable "portal_app_cerebro" {
  description = "The Cerebro app inside the portal: chat, graph and curation over KGE. on or off, read when the portal starts. With portal_knowledge_home off, exactly one app may be on."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_app_cerebro)
    error_message = "portal_app_cerebro must be on or off."
  }
}

variable "portal_cerebro_name" {
  description = "The Cerebro app's display name in the portal. Trimmed; empty uses Cerebro."
  type        = string
  default     = "Cerebro"
  nullable    = false
}

variable "portal_table_query" {
  description = "The Run Query tab on table pages and its query endpoint. on or off, read when the portal starts."
  type        = string
  default     = "off"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_table_query)
    error_message = "portal_table_query must be on or off."
  }
}

variable "portal_table_preview" {
  description = "The Preview tab on table pages and its preview endpoints. on or off, read when the portal starts."
  type        = string
  default     = "off"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_table_preview)
    error_message = "portal_table_preview must be on or off."
  }
}

variable "portal_dq_filters" {
  description = "Show the four Data quality filter buttons. off always shows All; on or off, read when the portal starts."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_dq_filters)
    error_message = "portal_dq_filters must be on or off."
  }
}

variable "portal_ask_guide" {
  description = "Show the Guidelines section in Discover's Ask; on or off, read when the portal starts."
  type        = string
  default     = "on"
  nullable    = false
  validation {
    condition     = contains(["on", "off"], var.portal_ask_guide)
    error_message = "portal_ask_guide must be on or off."
  }
}

variable "portal_ask_provider" {
  description = "Where Ask starts in the kge to portal to none fallback chain. none uses catalogue rules only."
  type        = string
  default     = "kge"
  nullable    = false
  validation {
    condition     = contains(["kge", "portal", "none"], var.portal_ask_provider)
    error_message = "portal_ask_provider must be kge, portal or none."
  }
}

variable "portal_ask_model" {
  description = "The portal catalogue agent's model id. Empty uses catalogue rules only."
  type        = string
  default     = ""
  nullable    = false
}

variable "portal_ask_model_provider" {
  description = "The portal catalogue agent's model provider: vertex, or mock for offline tests. No call is made without portal_ask_model."
  type        = string
  default     = "vertex"
  nullable    = false
  validation {
    condition     = contains(["vertex", "mock"], var.portal_ask_model_provider)
    error_message = "portal_ask_model_provider must be vertex or mock."
  }
}

variable "portal_certify_url" {
  description = "Certify's external http or https address. Null or empty leaves the Power App link placeholder."
  type        = string
  default     = null
  validation {
    condition     = var.portal_certify_url == null || var.portal_certify_url == "" ? true : can(regex("^(?i)https?://[^/?#\\s]+", var.portal_certify_url))
    error_message = "portal_certify_url must be an http or https address with a host, or null or empty."
  }
}

variable "portal_conversations_url" {
  description = "Where Cerebro keeps each person's conversations (PORTAL_CONVERSATIONS_URL): gs://<bucket>/<prefix>, a file:// URL or an absolute folder. The pilot gives gs://<store bucket>/portal/<client>/conversations. Null or empty passes nothing: on Cloud Run (the BigQuery backend) the portal then keeps them in its process's memory alone, lost on a restart."
  type        = string
  default     = null
  validation {
    condition     = var.portal_conversations_url == null || var.portal_conversations_url == "" ? true : can(regex("^(gs://[a-z0-9][-a-z0-9_.]*[a-z0-9]/[^/\\s][^\\s]*|file:///[^\\s]*|/[^\\s]*)$", var.portal_conversations_url))
    error_message = "portal_conversations_url must be gs://<bucket>/<prefix> (a bucket alone is refused: the portal sweeps under the prefix), a file:// URL or an absolute folder, or null or empty."
  }
}

variable "portal_ask_timeout" {
  description = "The portal's time limit on a KGE Ask call, response body included (PORTAL_ASK_TIMEOUT), in whole seconds from 1 to 299; the image's default is 285, above KGE's MODEL_TOTAL_TIMEOUT and below Cloud Run's 300."
  type        = number
  default     = 285
  nullable    = false
  validation {
    condition     = var.portal_ask_timeout >= 1 && var.portal_ask_timeout <= 299 && floor(var.portal_ask_timeout) == var.portal_ask_timeout
    error_message = "portal_ask_timeout must be a whole number of seconds from 1 to 299."
  }
}

variable "portal_kge_timeout" {
  description = "The portal's time limit on a KGE identity, session, catalogue or data call, response body included (PORTAL_KGE_TIMEOUT), in whole seconds from 1 to 299; the image's default is 30."
  type        = number
  default     = 30
  nullable    = false
  validation {
    condition     = var.portal_kge_timeout >= 1 && var.portal_kge_timeout <= 299 && floor(var.portal_kge_timeout) == var.portal_kge_timeout
    error_message = "portal_kge_timeout must be a whole number of seconds from 1 to 299."
  }
}

variable "portal_search_timeout" {
  description = "The portal's time limit on a KGE Search call, response body included (PORTAL_SEARCH_TIMEOUT), in whole seconds from 1 to 299; the image's default is 30."
  type        = number
  default     = 30
  nullable    = false
  validation {
    condition     = var.portal_search_timeout >= 1 && var.portal_search_timeout <= 299 && floor(var.portal_search_timeout) == var.portal_search_timeout
    error_message = "portal_search_timeout must be a whole number of seconds from 1 to 299."
  }
}
