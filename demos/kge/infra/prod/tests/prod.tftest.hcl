# QA and PROD, planned and applied against stand-in providers: no account, no organization, no network, nothing created.
#
#   terraform init -backend=false
#   terraform test -var-file=qa.tfvars
#   terraform test -var-file=prod.tfvars
#
# Each tfvars file gives the values every run starts from, so both are read and type-checked; each run names the
# environment and the organization it is about, which override the file's. A stand-in makes up what Google would
# compute; where the provider checks a value's form (an IAM member, a service account's name), the stand-in gives one
# of that form.

mock_provider "google" {
  mock_resource "google_service_account" {
    defaults = {
      email  = "stand-in@your-project-id.iam.gserviceaccount.com"
      member = "serviceAccount:stand-in@your-project-id.iam.gserviceaccount.com"
      name   = "projects/your-project-id/serviceAccounts/stand-in@your-project-id.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_kms_crypto_key" {
    defaults = {
      id = "projects/your-project-id/locations/europe-west2/keyRings/kge/cryptoKeys/kge-data"
    }
  }
  mock_data "google_storage_project_service_account" {
    defaults = {
      email_address = "service-0@gs-project-accounts.iam.gserviceaccount.com"
      member        = "serviceAccount:service-0@gs-project-accounts.iam.gserviceaccount.com"
    }
  }
  mock_data "google_bigquery_default_service_account" {
    defaults = {
      email  = "bq-0@bigquery-encryption.iam.gserviceaccount.com"
      member = "serviceAccount:bq-0@bigquery-encryption.iam.gserviceaccount.com"
    }
  }
}

mock_provider "google-beta" {
  mock_resource "google_project_service_identity" {
    defaults = {
      email  = "service-0@gcp-sa-stand-in.iam.gserviceaccount.com"
      member = "serviceAccount:service-0@gcp-sa-stand-in.iam.gserviceaccount.com"
    }
  }
}

run "qa_applies_without_an_organization" {
  variables {
    environment     = "qa"
    organization_id = null
  }
  assert {
    condition     = length(google_access_context_manager_service_perimeter.this) == 0 && length(google_org_policy_policy.enforced) == 0 && length(google_iam_workforce_pool.this) == 0 && length(google_iap_web_cloud_run_service_iam_member.groups) == 0
    error_message = "With no organization, its parts (VPC Service Controls, organization policies, workforce federation, group-based IAP) are left out."
  }
  assert {
    condition     = length(google_pubsub_topic.landed) == length(module.run) && length(google_eventarc_trigger.landed) == length(module.run) && length(google_cloud_scheduler_job.eval) == length(module.run) && length(google_cloud_run_v2_job.improve) == length(module.run) && length(google_cloud_scheduler_job.improve) == length(module.run)
    error_message = "Each client's landing bucket notifies a Pub/Sub topic, Eventarc starts its onboarding from it, and Cloud Scheduler runs its eval and improve jobs."
  }
  assert {
    condition     = alltrue([for id, r in module.run : r.iap_enabled && length(r.invokers) == 0])
    error_message = "Every service is behind IAP, and no one invokes it unauthenticated."
  }
  assert {
    condition     = alltrue([for path in values(module.observability.uptime_paths) : path == "/health"])
    error_message = "Every uptime check uses the service's liveness endpoint."
  }
  assert {
    condition     = alltrue([for b, k in module.storage.kms_key_names : k == google_kms_crypto_key.data.id])
    error_message = "Every bucket is encrypted with the environment's own key (CMEK)."
  }
}

run "prod_refuses_a_null_organization" {
  command = plan
  variables {
    environment     = "prod"
    organization_id = null
  }
  expect_failures = [var.organization_id]
}

run "prod_has_deletion_protection" {
  variables {
    environment     = "prod"
    organization_id = "000000000000"
  }
  assert {
    condition     = alltrue([for id, r in module.run : r.deletion_protection.service && r.deletion_protection.job]) && alltrue([for id, j in google_cloud_run_v2_job.improve : j.deletion_protection])
    error_message = "No Cloud Run service or job can be destroyed."
  }
  assert {
    condition     = alltrue([for b, p in module.storage.deletion_policies : p == "PREVENT"]) && !anytrue(values(module.storage.force_destroy))
    error_message = "No bucket can be destroyed, nor emptied by a destroy."
  }
  assert {
    condition     = alltrue([for d, p in module.bigquery.deletion_policies : p == "PREVENT"]) && !anytrue(values(module.bigquery.delete_contents_on_destroy))
    error_message = "No dataset can be destroyed, nor emptied by a destroy."
  }
  assert {
    condition     = alltrue(values(module.secrets.deletion_protection)) && module.registry.deletion_policy == "PREVENT" && google_kms_crypto_key.data.deletion_policy == "PREVENT"
    error_message = "No secret, image repository or key can be destroyed."
  }
  assert {
    condition     = tolist(google_resource_manager_lien.project.restrictions) == tolist(["resourcemanager.projects.delete"])
    error_message = "A lien keeps the project itself from being deleted."
  }
  assert {
    condition     = length(google_access_context_manager_service_perimeter.this) == 1 && length(google_org_policy_policy.enforced) > 0 && length(google_iam_workforce_pool.this) == 1
    error_message = "With an organization, its parts are created."
  }
}

run "no_bucket_is_public" {
  variables {
    environment     = "prod"
    organization_id = "000000000000"
  }
  assert {
    condition     = alltrue([for b, p in module.storage.public_access_prevention : p == "enforced"]) && alltrue(values(module.storage.uniform_bucket_level_access))
    error_message = "Every bucket enforces public access prevention and is IAM only."
  }
  assert {
    condition     = length(setintersection(toset(module.storage.members), ["allUsers", "allAuthenticatedUsers"])) == 0
    error_message = "No bucket grants anything to allUsers or allAuthenticatedUsers."
  }
  assert {
    condition     = alltrue([for c in var.clients : module.storage.grants["store/${c}-service-pack"].role == "roles/storage.objectUser" && module.storage.grants["store/${c}-service-pack"].conditions[0].expression == "resource.name.startsWith(\"projects/_/buckets/${var.project_id}-kge-store/objects/clients/${c}/units/\") || resource.name.startsWith(\"projects/_/buckets/${var.project_id}-kge-store/objects/clients/${c}/onboard/\")"])
    error_message = "Each service may overwrite only its unit drafts and onboarding markers."
  }
  assert {
    condition     = alltrue([for c in var.clients : module.storage.grants["store/${c}-job"].role == "roles/storage.objectAdmin" && length(module.storage.grants["store/${c}-job"].conditions) == 0 && !contains(keys(module.storage.grants), "store/${c}-job-registry")])
    error_message = "Every job retains one unconditional, store-wide objectAdmin grant."
  }
  assert {
    condition     = alltrue([for c in var.clients : module.storage.grants["store/${c}-improve"].role == "roles/storage.objectViewer" && length(module.storage.grants["store/${c}-improve"].conditions) == 0 && module.storage.grants["${c}/improve"].role == "roles/storage.objectViewer" && length(module.storage.grants["${c}/improve"].conditions) == 0 && module.storage.grants["store/${c}-improve-proposals"].role == "roles/storage.objectUser" && module.storage.grants["store/${c}-improve-proposals"].conditions[0].expression == "resource.name.startsWith(\"projects/_/buckets/${var.project_id}-kge-store/objects/clients/${c}/proposals/\")" && !contains([for k, g in module.storage.grants : g.role if startswith(k, "store/${c}-improve") || startswith(k, "${c}/improve")], "roles/storage.objectAdmin")])
    error_message = "Each improve job account has objectViewer on store and landing and a proposal-scoped objectUser grant, never objectAdmin."
  }
  assert {
    condition     = alltrue([for c in var.clients : tolist(google_cloud_run_v2_job.improve[c].template[0].template[0].containers[0].args) == tolist(["improve"]) && contains([for e in google_cloud_run_v2_job.improve[c].template[0].template[0].containers[0].env : e.name], "LANDING_URL") && !contains([for e in google_cloud_run_v2_job.improve[c].template[0].template[0].containers[0].env : e.name], "RELEASE_URL") && length(setintersection(toset(module.identity.client_project_grants[c].improve), ["roles/storage.objectAdmin", "roles/artifactregistry.writer", "roles/artifactregistry.admin", "roles/run.developer", "roles/run.admin"])) == 0])
    error_message = "The improve job runs improve with STORE_URL and LANDING_URL, and its account has no objectAdmin, registry or release access."
  }
  assert {
    condition     = alltrue([for c in var.clients : module.run[c].job_executor.role == "roles/run.invoker" && module.run[c].job_executor.name == module.run[c].job_name])
    error_message = "Each service may invoke only its own job, without overrides."
  }
  assert {
    condition     = alltrue([for c in var.clients : length(setintersection(toset(module.identity.client_project_grants[c].service), ["roles/storage.objectUser", "roles/run.invoker"])) == 0])
    error_message = "Neither scoped service grant is made at project level."
  }
}

run "no_bucket_is_public_whatever_the_variables" {
  command = plan
  module {
    source = "../tests/declared"
  }
  variables {
    config = "prod"
  }
  assert {
    condition     = length([for t in output.resource_types : t if can(regex("^google_storage_(.*_acl|.*_access_control)$", t))]) == 0
    error_message = "No bucket or object ACL is declared: bucket access is IAM, granted in the storage module alone."
  }
  assert {
    condition     = contains(keys(output.declared_in), "google_storage_bucket_iam_member") && alltrue([for t, files in output.declared_in : toset(files) == toset(["modules/storage/main.tf"]) if can(regex("^google_storage_bucket_iam_", t))])
    error_message = "Bucket IAM is declared in the storage module alone, whose members the run above checks."
  }
}

run "no_file_is_read_from_outside_its_folder" {
  command = plan
  module {
    source = "../tests/declared"
  }
  variables {
    config = "prod"
  }
  assert {
    condition     = length(output.reads_outside) == 0
    error_message = "QA/PROD and its modules read no file outside their own folders: ${jsonencode(output.reads_outside)}."
  }
}

run "a_client_that_is_not_a_slug_is_refused" {
  command = plan
  variables {
    environment     = "qa"
    organization_id = null
    clients         = ["meridian", "Not_A_Slug"]
  }
  expect_failures = [var.clients]
}

run "a_sixteen_character_client_is_accepted" {
  command = plan
  variables {
    environment     = "qa"
    organization_id = null
    clients         = ["abbbbbbbbbbbbbbb"]
  }
}

run "a_seventeen_character_client_is_refused_at_the_root" {
  command = plan
  variables {
    environment     = "qa"
    organization_id = null
    clients         = ["abbbbbbbbbbbbbbbb"]
  }
  expect_failures = [var.clients]
}

run "an_environment_is_qa_or_prod" {
  command = plan
  variables {
    environment     = "staging"
    organization_id = null
  }
  expect_failures = [var.environment]
}
