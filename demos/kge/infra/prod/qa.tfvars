# The QA instance of this configuration, with placeholders where an account's own values go. Give those on the
# command line from Cloud Shell (the kit's INSTALL.md), after this file, so the real ones are never committed:
#   terraform plan -var-file=qa.tfvars -var="project_id=$QA_PROJECT" -var="billing_account=$BILLING" ...

environment       = "qa"
project_id        = "your-qa-project-id"
billing_account   = "000000-000000-000000"
github_repository = "your-org/your-repo"
alert_email       = "alerts@example.com"

# No Google Cloud organization yet: its parts wait, and QA applies without them.
organization_id = null

region = "europe-west2"

# QA carries every client before PROD does: each is onboarded and checked here first.
clients            = ["meridian", "thornbury", "fellmoor"]
budget_amount      = 200
bigquery_max_slots = 50
model_location     = "global"
app_env            = {}

# IAP lets these through one by one (user:<email>); groups wait for the organization.
iap_members          = []
iap_groups           = []
allowed_customer_ids = []
workforce_oidc       = null

eval_schedule = "0 3 * * 1"
time_zone     = "Etc/UTC"
