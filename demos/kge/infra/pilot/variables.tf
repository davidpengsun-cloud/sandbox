variable "project_id" {
  description = "The pilot's one project, made at bootstrap (the kit's INSTALL.md)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "A project id: 6 to 30 lower-case letters, digits and hyphens, starting with a letter."
  }
}

variable "billing_account" {
  description = "The billing account the project is linked to, for its budget."
  type        = string
}

variable "region" {
  description = "Where everything runs and is held."
  type        = string
}

variable "clients" {
  description = "The clients the pilot deploys, one Cloud Run service and one job each, and a landing bucket each: meridian alone. Each is a slug, the client's id, and its profile's id in the store's deploy/profiles.yaml (the job's setup-client writes it)."
  type        = list(string)
  default     = ["meridian"]
  validation {
    condition     = length(var.clients) > 0
    error_message = "At least one client."
  }
  validation {
    condition     = alltrue([for c in var.clients : can(regex("^[a-z][a-z0-9-]{0,15}$", c))])
    error_message = "a client id is a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (kge-<client>-svc, -job and -improve) fits Google's 30 characters"
  }
  validation {
    condition     = length(distinct(var.clients)) == length(var.clients)
    error_message = "Each client is named once."
  }
}

variable "public" {
  description = "Whether anyone may reach each client's page (allUsers may invoke it); the page signs people in itself either way. False where the organisation's domain-restricted sharing (constraints/iam.allowedPolicyMemberDomains) refuses allUsers: then only the invokers named may reach it."
  type        = bool
  default     = true
  nullable    = false
}

variable "invokers" {
  description = "With public = false, who may invoke each client's page (roles/run.invoker): IAM members, such as domain:example.com, group:pilot@example.com or user:alex@example.com."
  type        = list(string)
  default     = []
  nullable    = false
  validation {
    condition     = alltrue([for m in var.invokers : can(regex("^(user|group|serviceAccount|domain):[^\\s:]+$", m))])
    error_message = "Each invoker is an IAM member: user:, group: or serviceAccount: and an email, or domain: and a domain; never allUsers or allAuthenticatedUsers."
  }
  validation {
    condition     = !var.public || length(var.invokers) == 0
    error_message = "Invokers are for a private page (public = false): a public page lets anyone invoke it."
  }
}

variable "github_repository" {
  description = "The repository whose GitHub Actions may push the image and deploy it, owner/name."
  type        = string
}

variable "alert_email" {
  description = "Where the budget and uptime alerts go."
  type        = string
}

variable "uptime_path" {
  description = "The liveness path checked for each service. With public = false, the pilot also counts a 4xx response as up because Cloud Run rejects the checker before the service runs."
  type        = string
  default     = "/health"
}

variable "budget_amount" {
  description = "The monthly budget, in whole units of the billing account's currency."
  type        = number
  default     = 100
}

variable "bigquery_max_slots" {
  description = "The most slots BigQuery's reservation autoscales to, from 0."
  type        = number
  default     = 50
}

variable "model_location" {
  description = "The Vertex AI location the model is called in."
  type        = string
  default     = "global"
}

variable "pilot_env" {
  description = "The pilot's configuration, given to the service and the job: GCP-native, as validated live (issue #95). The models on Vertex AI (Gemini 3.x, by tier), the tagger on the medium tier, Vertex AI embeddings 768 wide, and the warehouses, retrieval, app state and graph in BigQuery. Model budgets and the timeout are image defaults that app_env can override. app_env overrides any one of them; {} leaves the image on its local defaults, which are for development and tests only."
  type        = map(string)
  default = {
    MODEL_PROVIDER         = "vertex-gemini"
    MODEL_NAME             = "gemini-3.8-flash"
    MODEL_HIGH_PROVIDER    = "vertex-gemini"
    MODEL_HIGH_NAME        = "gemini-3.1-pro-preview"
    MODEL_MEDIUM_PROVIDER  = "vertex-gemini"
    MODEL_MEDIUM_NAME      = "gemini-3.8-flash"
    MODEL_LOW_PROVIDER     = "vertex-gemini"
    MODEL_LOW_NAME         = "gemini-3.5-flash-lite"
    TAGGER_PROVIDER        = "model"
    TAGGER_MODEL_TIER      = "medium"
    EMBEDDING_PROVIDER     = "vertex"
    VERTEX_EMBEDDING_MODEL = "gemini-embedding-001"
    EMBEDDING_DIM          = "768"
    WAREHOUSE_BACKEND      = "bigquery"
    RETRIEVAL_BACKEND      = "bigquery"
    APP_STATE_BACKEND      = "bigquery"
    GRAPH_STORE            = "bigquery"
    DOCUMENT_READER        = "gemini"
  }
}

variable "app_env" {
  description = "More of the image's settings for the service and the job, each over pilot_env's of the same name (issue #48 item 3)."
  type        = map(string)
  default     = {}
}

variable "image" {
  description = "The image the service and the job start with, or null for Google's sample until the first deployment; Terraform never moves it after."
  type        = string
  default     = null
}

variable "portal_image" {
  description = "The portal image the portal service starts with, or null for Google's sample until the first deployment; Terraform never moves it after."
  type        = string
  default     = null
}

variable "portal_client_name" {
  description = "The client's display name for the portal's product and audience and KGE's pages. Trimmed; empty uses Client, independently of the profile id."
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

variable "portal_logo_title" {
  description = "Show the product name beside a configured portal logo. on or off, read when the portal starts."
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

variable "portal_certify_url" {
  description = "Certify's external http or https address. Null or empty leaves the Power App link placeholder."
  type        = string
  default     = null
  validation {
    condition     = var.portal_certify_url == null || var.portal_certify_url == "" ? true : can(regex("^(?i)https?://[^/?#\\s]+", var.portal_certify_url))
    error_message = "portal_certify_url must be an http or https address with a host, or null or empty."
  }
}
