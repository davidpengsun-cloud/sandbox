# The pilot's settings, with placeholders where an account's own values go. Give those on the command line from Cloud
# Shell (the kit's INSTALL.md), after this file, so the real ones are never committed:
#   terraform plan -var-file=example.tfvars -var="project_id=$PROJECT" -var="billing_account=$BILLING" ...

project_id        = "your-pilot-project-id"
billing_account   = "000000-000000-000000"
github_repository = "your-org/your-repo"
alert_email       = "alerts@example.com"

region             = "europe-west2"
budget_amount      = 100
bigquery_max_slots = 50
model_location     = "global"

# The clients deployed are meridian alone (the default), each with its own service, job and landing bucket; to name more:
# clients = ["meridian", "thornbury"]

# Each page is public (the default) and signs people in itself. Where the organisation's domain-restricted sharing refuses
# allUsers, keep it private and name who may invoke it:
# public   = false
# invokers = ["domain:example.com"]

# The pilot runs GCP-native by default (pilot_env in variables.tf): Gemini on Vertex AI by tier, the tagger on the low
# tier, Vertex AI embeddings and BigQuery for the warehouses, retrieval and app state. app_env overrides any one of those
# settings, as MODEL_NAME = "<another model>"; the models must be enabled for the project.
app_env = {}
