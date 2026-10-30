# The PROD instance of this configuration, with placeholders where an account's own values go. Give those on the
# command line from Cloud Shell (the kit's INSTALL.md), after this file, so the real ones are never committed:
#   terraform plan -var-file=prod.tfvars -var="project_id=$PROD_PROJECT" -var="billing_account=$BILLING" ...

environment       = "prod"
project_id        = "your-prod-project-id"
billing_account   = "000000-000000-000000"
github_repository = "your-org/your-repo"
alert_email       = "alerts@example.com"

# No Google Cloud organization yet: its parts wait, and PROD refuses to apply until this is set.
organization_id = null

region = "europe-west2"

# PROD carries the clients released to it.
clients            = ["meridian"]
budget_amount      = 500
bigquery_max_slots = 100
model_location     = "global"
app_env            = {}

# IAP lets these through one by one (user:<email>); groups wait for the organization.
iap_members          = []
iap_groups           = []
allowed_customer_ids = []
workforce_oidc       = null

eval_schedule = "0 3 * * 1"
time_zone     = "Etc/UTC"
