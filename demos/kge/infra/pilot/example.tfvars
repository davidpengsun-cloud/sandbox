# The pilot's settings, with placeholders where an account's own values go. Give those on the command line from Cloud
# Shell (the kit's INSTALL.md), after this file, so the real ones are never committed:
#   terraform plan -var-file=example.tfvars -var="project_id=$PROJECT" -var="billing_account=$BILLING" ...

project_id        = "your-pilot-project-id"
billing_account   = "000000-000000-000000"
github_repository = "your-org/your-repo"
alert_email       = "alerts@example.com"

region              = "europe-west2"
budget_amount       = 100
bigquery_max_slots  = 50
model_location      = "global"
session_key_version = "1"

# The clients deployed are meridian alone (the default), each with its own service, job and landing bucket; to name more:
# clients = ["meridian", "thornbury"]

# Each page is public (the default) and signs people in itself. Where the organisation's domain-restricted sharing refuses
# allUsers, keep it private and name who may invoke it:
# public   = false
# invokers = ["domain:example.com"]

# The pilot runs GCP-native by default (pilot_env in variables.tf): Gemini on Vertex AI by tier, the tagger on the medium
# tier, Vertex AI embeddings and BigQuery for the warehouses, retrieval and app state. app_env overrides any one of those
# settings, as MODEL_NAME = "<another model>"; the models must be enabled for the project.
app_env = {}

# The product's name in the portal and on KGE's pages: its short name (as in <client> ALTA) and its long form.
portal_product_name      = "ALTA"
portal_product_long_name = "ALTA: AI-powered Library for Trusted Assets"
portal_product_subtitle  = "Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it."

# The Context Ask header's title and subtitle.
portal_context_ask_title    = "Ask a governed question"
portal_context_ask_subtitle = "Certified definitions and figures, answered only from data you may read."
