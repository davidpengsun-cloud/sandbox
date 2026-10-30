# The pilot, planned and applied against stand-in providers: no account, no network, nothing created.
#
#   terraform init -backend=false && terraform test -var-file=example.tfvars
#
# example.tfvars gives the placeholders every run starts from; a run's own variables override them. A stand-in makes up
# what Google would compute; where the provider checks a value's form (an IAM member, a service account's name), the
# stand-in gives one of that form.

mock_provider "google" {
  mock_resource "google_service_account" {
    defaults = {
      email  = "stand-in@your-pilot-project-id.iam.gserviceaccount.com"
      member = "serviceAccount:stand-in@your-pilot-project-id.iam.gserviceaccount.com"
      name   = "projects/your-pilot-project-id/serviceAccounts/stand-in@your-pilot-project-id.iam.gserviceaccount.com"
    }
  }
}

run "the_pilot_deploys_kge_portal_and_one_job_for_meridian" {
  assert {
    condition     = keys(module.run) == ["meridian"]
    error_message = "One client by default, meridian: got ${jsonencode(keys(module.run))}."
  }
  assert {
    condition     = module.run["meridian"].service_name == "kge-meridian" && module.run["meridian"].job_name == "kge-meridian"
    error_message = "Meridian's service and job are kge-meridian."
  }

  assert {
    condition     = module.portal["meridian"].service_name == "kge-portal-meridian" && module.portal["meridian"].service_account == module.identity.service_accounts["meridian"].service && module.portal["meridian"].container_port == 8080
    error_message = "Meridian's portal is a separate service on port 8080, running as the client's KGE service account."
  }

  assert {
    condition     = module.portal["meridian"].env.PORTAL_BACKEND == "bigquery" && module.portal["meridian"].env.KGE_URL == module.run["meridian"].service_uri && module.portal["meridian"].env.KGE_AUDIENCE == module.run["meridian"].service_uri && module.portal["meridian"].env.BIGQUERY_DATASET_PREFIX == "kge" && length(module.portal["meridian"].kge_invoker) == 0
    error_message = "The portal uses the client's warehouse dataset and reaches KGE server to server."
  }
  assert {
    condition     = module.run["meridian"].env.TRUSTED_CALLER_EMAIL == module.identity.service_accounts["meridian"].service && module.run["meridian"].env.TRUSTED_CALLER_AUDIENCE == format("%s://kge-meridian-%s.europe-west2.run.app", "https", module.project.number) && module.portal["meridian"].env.KGE_CALLER_AUDIENCE == module.run["meridian"].env.TRUSTED_CALLER_AUDIENCE && !contains(keys(module.run["meridian"].job_env), "TRUSTED_CALLER_EMAIL")
    error_message = "KGE trusts the portal's service account's ID token for its own address, the audience the portal mints its caller token for; the job trusts no caller: ${jsonencode([module.run["meridian"].env, module.portal["meridian"].env])}."
  }
  assert {
    condition     = module.run["meridian"].profile == "meridian" && module.run["meridian"].env.PROFILE == "meridian" && module.run["meridian"].job_env.PROFILE == "meridian"
    error_message = "The service and the job serve the meridian profile, the client's id."
  }
  assert {
    condition     = module.run["meridian"].env.STORE_URL == "gs://your-pilot-project-id-kge-store" && module.run["meridian"].env.SIGNIN == "passwords" && !contains(keys(module.run["meridian"].env), "LANDING_URL")
    error_message = "The service reads the store, signs people in with passwords, and never reads the landing: ${jsonencode(module.run["meridian"].env)}."
  }
  assert {
    condition     = module.run["meridian"].job_env.STORE_URL == "gs://your-pilot-project-id-kge-store" && module.run["meridian"].job_env.LANDING_URL == "gs://your-pilot-project-id-kge-meridian-landing" && !contains(keys(module.run["meridian"].job_env), "SIGNIN")
    error_message = "The job reads and writes the store and meridian's landing bucket: ${jsonencode(module.run["meridian"].job_env)}."
  }
  assert {
    condition     = toset(keys(module.run["meridian"].env)) == toset(concat(["PROFILE", "SIGNIN", "STORE_URL", "TMP_URL", "TRUSTED_CALLER_EMAIL", "TRUSTED_CALLER_AUDIENCE", "VERTEX_PROJECT", "VERTEX_REGION", "RUN_REGION", "BIGQUERY_PROJECT", "BIGQUERY_DATASET_PREFIX"], ["MODEL_PROVIDER", "MODEL_NAME", "MODEL_HIGH_PROVIDER", "MODEL_HIGH_NAME", "MODEL_MEDIUM_PROVIDER", "MODEL_MEDIUM_NAME", "MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "TAGGER_PROVIDER", "TAGGER_MODEL_TIER", "EMBEDDING_PROVIDER", "VERTEX_EMBEDDING_MODEL", "EMBEDDING_DIM", "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE"])) && toset(keys(module.run["meridian"].job_env)) == toset(concat(["PROFILE", "STORE_URL", "LANDING_URL", "TMP_URL", "VERTEX_PROJECT", "VERTEX_REGION", "RUN_REGION", "BIGQUERY_PROJECT", "BIGQUERY_DATASET_PREFIX"], ["MODEL_PROVIDER", "MODEL_NAME", "MODEL_HIGH_PROVIDER", "MODEL_HIGH_NAME", "MODEL_MEDIUM_PROVIDER", "MODEL_MEDIUM_NAME", "MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "TAGGER_PROVIDER", "TAGGER_MODEL_TIER", "EMBEDDING_PROVIDER", "VERTEX_EMBEDDING_MODEL", "EMBEDDING_DIM", "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE"]))
    error_message = "The service's and the job's settings are these and the pilot's GCP-native ones, and no others (beside the service's secrets)."
  }
  assert {
    condition     = module.run["meridian"].env.TMP_URL == "gs://your-pilot-project-id-kge-tmp" && module.run["meridian"].job_env.TMP_URL == "gs://your-pilot-project-id-kge-tmp"
    error_message = "The service and the job write scratch files to the tmp bucket: ${jsonencode([module.run["meridian"].env, module.run["meridian"].job_env])}."
  }
  assert {
    condition     = tolist(module.run["meridian"].job_args) == tolist(["onboard-request"])
    error_message = "The job always validates and runs the request in the store."
  }
  assert {
    condition     = tolist(module.run["meridian"].invokers) == tolist(["allUsers"]) && module.run["meridian"].env.SIGNIN == "passwords"
    error_message = "The pilot's page is public by default, reached by anyone and signed in to by its own usernames and passwords."
  }
  assert {
    condition     = tolist(module.observability.uptime_accepts["meridian"]) == tolist(["STATUS_CLASS_2XX"])
    error_message = "A public page is up when it serves: a 2xx alone."
  }
  assert {
    condition     = module.observability.uptime_paths["meridian"] == "/health"
    error_message = "The uptime check uses the service's liveness endpoint."
  }
  assert {
    condition     = module.bigquery.default_table_expiration_ms["kge_meridian_candidates"] == 7 * 24 * 60 * 60 * 1000 && module.bigquery.default_table_expiration_ms["kge_meridian_graph"] == null && module.bigquery.default_table_expiration_ms["kge_meridian_warehouses"] == null
    error_message = "A table in the candidates dataset expires after 7 days, the backstop for an improvement run that crashes before it drops its candidate; the graph and the warehouses never expire."
  }
  assert {
    condition     = module.bigquery.descriptions == { kge_meridian_warehouses = "meridian's warehouses.", kge_meridian_graph = "meridian's graph and chunk vectors.", kge_meridian_candidates = "meridian's candidates: a candidate's graph and chunks, which nothing serves." }
    error_message = "Each dataset's description names its own kind: the candidates dataset is never described as warehouses."
  }
}

# Issue #69: an organisation whose domain-restricted sharing (constraints/iam.allowedPolicyMemberDomains) refuses allUsers
# keeps the page private and names who may invoke it.
run "the_pilot_is_gcp_native_by_default" {
  assert {
    condition = alltrue([for env in [module.run["meridian"].env, module.run["meridian"].job_env] : (
      env.VERTEX_REGION == "global" &&
      env.MODEL_PROVIDER == "vertex-gemini" && env.MODEL_NAME == "gemini-3.8-flash" &&
      env.MODEL_HIGH_PROVIDER == "vertex-gemini" && env.MODEL_HIGH_NAME == "gemini-3.1-pro-preview" &&
      env.MODEL_MEDIUM_PROVIDER == "vertex-gemini" && env.MODEL_MEDIUM_NAME == "gemini-3.8-flash" &&
      env.MODEL_LOW_PROVIDER == "vertex-gemini" && env.MODEL_LOW_NAME == "gemini-3.5-flash-lite" &&
      env.TAGGER_PROVIDER == "model" && env.TAGGER_MODEL_TIER == "low" &&
      env.EMBEDDING_PROVIDER == "vertex" && env.VERTEX_EMBEDDING_MODEL == "gemini-embedding-001" && env.EMBEDDING_DIM == "768" &&
      env.WAREHOUSE_BACKEND == "bigquery" && env.RETRIEVAL_BACKEND == "bigquery" && env.APP_STATE_BACKEND == "bigquery" &&
      env.GRAPH_STORE == "bigquery"
    )])
    error_message = "The service and the job run the validated GCP-native configuration by default (issue #95)."
  }
}

run "app_env_overrides_one_pilot_setting_and_keeps_the_rest" {
  variables {
    app_env = { MODEL_NAME = "another-model" }
  }
  assert {
    condition     = module.run["meridian"].env.MODEL_NAME == "another-model" && module.run["meridian"].job_env.MODEL_NAME == "another-model" && module.run["meridian"].env.WAREHOUSE_BACKEND == "bigquery"
    error_message = "app_env wins over pilot_env setting by setting, and the others stay."
  }
}

run "an_empty_pilot_env_leaves_only_the_shared_settings" {
  variables {
    pilot_env = {}
  }
  assert {
    condition     = toset(keys(module.run["meridian"].env)) == toset(["PROFILE", "SIGNIN", "STORE_URL", "TMP_URL", "TRUSTED_CALLER_EMAIL", "TRUSTED_CALLER_AUDIENCE", "VERTEX_PROJECT", "VERTEX_REGION", "RUN_REGION", "BIGQUERY_PROJECT", "BIGQUERY_DATASET_PREFIX"]) && toset(keys(module.run["meridian"].job_env)) == toset(["PROFILE", "STORE_URL", "LANDING_URL", "TMP_URL", "VERTEX_PROJECT", "VERTEX_REGION", "RUN_REGION", "BIGQUERY_PROJECT", "BIGQUERY_DATASET_PREFIX"])
    error_message = "With pilot_env = {} the image runs on its own defaults."
  }
}

run "a_private_page_is_invoked_by_the_members_named_alone" {
  variables {
    public   = false
    invokers = ["domain:example.com", "group:pilot@example.com"]
  }
  assert {
    condition     = toset(module.run["meridian"].invokers) == toset(["domain:example.com", "group:pilot@example.com"])
    error_message = "A private page grants roles/run.invoker to the invokers named, never allUsers: got ${jsonencode(module.run["meridian"].invokers)}."
  }
  assert {
    condition     = module.run["meridian"].env.SIGNIN == "passwords"
    error_message = "Behind Cloud Run's IAM the page still signs people in itself."
  }
  assert {
    condition     = toset(module.observability.uptime_accepts["meridian"]) == toset(["STATUS_CLASS_2XX", "STATUS_CLASS_4XX"])
    error_message = "Google's uptime checkers are not invokers: a private page's 403 to them counts as up, not as the page down."
  }
}

run "a_private_page_with_no_invokers_grants_no_one" {
  command = plan
  variables {
    public = false
  }
  assert {
    condition     = module.portal["meridian"].kge_invoker == [{ service = "kge-meridian", role = "roles/run.invoker", member = "serviceAccount:${module.identity.service_accounts["meridian"].service}" }]
    error_message = "With a private page the portal, as the client's service account, may invoke its paired KGE service alone."
  }
  assert {
    condition     = length(module.run["meridian"].invokers) == 0
    error_message = "No invoker named, no one is granted roles/run.invoker on the service."
  }
}

run "an_invoker_that_is_not_an_iam_member_is_refused" {
  command = plan
  variables {
    public   = false
    invokers = ["allUsers"]
  }
  expect_failures = [var.invokers]
}

run "invokers_on_a_public_page_are_refused" {
  command = plan
  variables {
    invokers = ["domain:example.com"]
  }
  expect_failures = [var.invokers]
}

run "the_pilot_declares_two_services_and_one_job" {
  command = plan
  module {
    source = "../tests/declared"
  }
  variables {
    config = "pilot"
  }
  assert {
    condition     = output.declarations["google_cloud_run_v2_service"] == 2 && output.declarations["google_cloud_run_v2_job"] == 1
    error_message = "KGE and portal Cloud Run services and one KGE job are declared once per client."
  }
}

run "no_bucket_is_public" {
  assert {
    condition     = module.storage.store_bucket == "your-pilot-project-id-kge-store" && module.storage.landing_buckets["meridian"] == "your-pilot-project-id-kge-meridian-landing" && module.storage.tmp_bucket == "your-pilot-project-id-kge-tmp" && output.tmp_bucket == module.storage.tmp_bucket
    error_message = "Three buckets: the store, meridian's landing bucket and the tmp bucket."
  }
  assert {
    condition     = length(module.storage.public_access_prevention) == 3 && alltrue([for b, p in module.storage.public_access_prevention : p == "enforced"])
    error_message = "Every bucket, the store, meridian's landing bucket and the tmp bucket, enforces public access prevention."
  }
  assert {
    condition     = alltrue(values(module.storage.uniform_bucket_level_access))
    error_message = "Every bucket is IAM only: no object ACL can open an object to the public."
  }
  assert {
    condition     = length(setintersection(toset(module.storage.members), ["allUsers", "allAuthenticatedUsers"])) == 0
    error_message = "No bucket grants anything to allUsers or allAuthenticatedUsers."
  }
  assert {
    condition     = module.storage.grants["store/meridian-service-pack"].role == "roles/storage.objectUser" && module.storage.grants["store/meridian-service-pack"].conditions[0].expression == "resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-store/objects/clients/meridian/units/\") || resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-store/objects/clients/meridian/onboard/\")"
    error_message = "Meridian's service may overwrite only its unit drafts and onboarding markers."
  }
  assert {
    condition     = module.storage.grants["store/meridian-job"].role == "roles/storage.objectAdmin" && length(module.storage.grants["store/meridian-job"].conditions) == 0 && !contains(keys(module.storage.grants), "store/meridian-job-registry")
    error_message = "The job retains one unconditional, store-wide objectAdmin grant."
  }
  assert {
    condition     = module.run["meridian"].job_executor.role == "roles/run.invoker" && module.run["meridian"].job_executor.name == module.run["meridian"].job_name
    error_message = "Meridian's service may invoke only Meridian's job, without overrides."
  }
  assert {
    condition     = length(setintersection(toset(module.identity.client_project_grants["meridian"].service), ["roles/storage.objectUser", "roles/run.invoker"])) == 0
    error_message = "Neither scoped service grant is made at project level."
  }
}

run "the_tmp_bucket_deletes_after_seven_days_and_each_client_reaches_its_own_prefix_alone" {
  assert {
    condition     = module.storage.public_access_prevention["your-pilot-project-id-kge-tmp"] == "enforced" && module.storage.uniform_bucket_level_access["your-pilot-project-id-kge-tmp"]
    error_message = "The tmp bucket enforces public access prevention and is IAM only, with no ACLs."
  }
  assert {
    condition     = length(module.storage.lifecycle_rules["your-pilot-project-id-kge-tmp"]) == 1 && module.storage.lifecycle_rules["your-pilot-project-id-kge-tmp"][0].action == "Delete" && module.storage.lifecycle_rules["your-pilot-project-id-kge-tmp"][0].age == 7 && module.storage.lifecycle_rules["your-pilot-project-id-kge-tmp"][0].days_since_noncurrent_time == null && !module.storage.versioning["your-pilot-project-id-kge-tmp"]
    error_message = "The tmp bucket deletes each object 7 days after it was made, and keeps no versions: ${jsonencode(module.storage.lifecycle_rules)}."
  }
  assert {
    condition     = module.storage.versioning["your-pilot-project-id-kge-store"] && length(module.storage.lifecycle_rules["your-pilot-project-id-kge-store"]) == 1 && module.storage.lifecycle_rules["your-pilot-project-id-kge-store"][0].days_since_noncurrent_time == 30 && module.storage.lifecycle_rules["your-pilot-project-id-kge-store"][0].age == null
    error_message = "The store keeps its versions for 30 days, as before."
  }
  assert {
    condition     = toset([for k, g in module.storage.grants : k if g.bucket == "your-pilot-project-id-kge-tmp"]) == toset(["_tmp/meridian-service", "_tmp/meridian-job", "_tmp/meridian-service-bucket-viewer", "_tmp/meridian-job-bucket-viewer"])
    error_message = "Only meridian's service and job hold a grant on the tmp bucket: ${jsonencode(module.storage.grants)}."
  }
  assert {
    condition     = toset([for k, g in module.storage.grants : { role = g.role, member = g.member, conditioned = length(g.conditions) > 0 } if g.bucket == "your-pilot-project-id-kge-tmp"]) == toset(flatten([for who in ["service", "job"] : [{ role = "roles/storage.objectUser", member = module.storage.grants["_tmp/meridian-${who}"].member, conditioned = true }, { role = "roles/storage.bucketViewer", member = module.storage.grants["_tmp/meridian-${who}"].member, conditioned = false }]]))
    error_message = "The tmp bucket's grants are, for each of meridian's service and job, objectUser conditioned and bucketViewer unconditioned, and nothing else: never a custom role, a conditioned bucketViewer, or a role that lists its objects (legacyBucketReader, objectViewer): ${jsonencode(module.storage.grants)}."
  }
  assert {
    condition     = alltrue([for who in ["service", "job"] : module.storage.grants["_tmp/meridian-${who}"].role == "roles/storage.objectUser" && module.storage.grants["_tmp/meridian-${who}"].conditions == [{ title = "meridian-${who}-tmp", expression = "resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-tmp/objects/tmp/meridian/\")" }]])
    error_message = "Meridian's service and job hold objectUser on tmp/meridian/ alone."
  }
  assert {
    condition     = module.storage.grants["_tmp/meridian-service"].member == module.identity.service_accounts["meridian"].service_member && module.storage.grants["_tmp/meridian-job"].member == module.identity.service_accounts["meridian"].job_member
    error_message = "The tmp grants are meridian's service's and job's own accounts."
  }
}

run "each_client_reaches_its_own_tmp_prefix_and_no_other" {
  command = plan
  variables {
    clients = ["thornbury", "meridian"]
  }
  assert {
    condition     = toset([for k, g in module.storage.grants : k if g.bucket == "your-pilot-project-id-kge-tmp"]) == toset(flatten([for c in ["meridian", "thornbury"] : [for who in ["service", "job"] : ["_tmp/${c}-${who}", "_tmp/${c}-${who}-bucket-viewer"]]]))
    error_message = "Each client's service and job, and no one else, hold a grant on the tmp bucket."
  }
  assert {
    condition     = toset([for k, g in module.storage.grants : { grant = k, role = g.role, conditioned = length(g.conditions) > 0 } if g.bucket == "your-pilot-project-id-kge-tmp"]) == toset(flatten([for c in ["meridian", "thornbury"] : [for who in ["service", "job"] : [{ grant = "_tmp/${c}-${who}", role = "roles/storage.objectUser", conditioned = true }, { grant = "_tmp/${c}-${who}-bucket-viewer", role = "roles/storage.bucketViewer", conditioned = false }]]]))
    error_message = "Each client's service and job hold objectUser conditioned and bucketViewer unconditioned on the tmp bucket, and no other role there."
  }
  assert {
    condition     = alltrue(flatten([for c in ["meridian", "thornbury"] : [for who in ["service", "job"] : module.storage.grants["_tmp/${c}-${who}"].role == "roles/storage.objectUser" && length(module.storage.grants["_tmp/${c}-${who}"].conditions) == 1 && module.storage.grants["_tmp/${c}-${who}"].conditions[0].expression == "resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-tmp/objects/tmp/${c}/\")"]]))
    error_message = "Each client's grant is conditioned on its own tmp/<client>/ prefix alone."
  }
  assert {
    condition     = module.run["thornbury"].env.TMP_URL == module.run["meridian"].env.TMP_URL && module.run["thornbury"].job_env.TMP_URL == "gs://your-pilot-project-id-kge-tmp"
    error_message = "One tmp bucket for every client, as the store is."
  }
}

run "no_bucket_is_public_whatever_the_variables" {
  command = plan
  module {
    source = "../tests/declared"
  }
  variables {
    config = "pilot"
  }
  assert {
    condition     = length([for t in output.resource_types : t if can(regex("^google_storage_(.*_acl|.*_access_control)$", t))]) == 0
    error_message = "No bucket or object ACL is declared: bucket access is IAM, granted in the storage module alone."
  }
  assert {
    condition     = contains(keys(output.declared_in), "google_storage_bucket_iam_member") && alltrue([for t, files in output.declared_in : toset(files) == toset(["modules/storage/main.tf"]) if can(regex("^google_storage_bucket_iam_", t))])
    error_message = "Bucket IAM is declared in the storage module alone, whose members the run above checks."
  }
  assert {
    condition     = !contains(output.resource_types, "google_project_iam_custom_role")
    error_message = "The pilot declares no custom role: one made in the same apply is not yet known to Cloud Storage when a bucket grant names it, so the tmp bucket's grants use predefined roles."
  }
}

run "the_pilot_has_no_pubsub_eventarc_scheduler_or_iap" {
  command = plan
  module {
    source = "../tests/declared"
  }
  variables {
    config = "pilot"
  }
  assert {
    condition     = length([for t in output.resource_types : t if can(regex("^google_(pubsub|eventarc|cloud_scheduler|workflows|iap)_", t))]) == 0
    error_message = "The pilot and its modules declare no Pub/Sub, Eventarc, Cloud Scheduler, Workflows or IAP resource."
  }
  assert {
    condition     = !output.iap_switched_on
    error_message = "Nothing in the pilot or its modules switches IAP on."
  }
  assert {
    condition     = contains(output.resource_types, "google_cloud_run_v2_service") && length(output.files) > 20
    error_message = "The modules the pilot calls were read, not the pilot's own files alone."
  }
}

run "the_pilot_enables_no_pubsub_eventarc_scheduler_or_iap" {
  command = plan
  assert {
    condition     = length([for s in module.project.services : s if contains(["pubsub.googleapis.com", "eventarc.googleapis.com", "cloudscheduler.googleapis.com", "workflows.googleapis.com", "iap.googleapis.com"], s)]) == 0
    error_message = "The pilot enables none of their APIs: ${jsonencode(module.project.services)}."
  }
  assert {
    condition     = alltrue([for id, r in module.run : !r.iap_enabled])
    error_message = "IAP is off on every service."
  }
}

run "no_file_is_read_from_outside_its_folder" {
  command = plan
  module {
    source = "../tests/declared"
  }
  variables {
    config = "pilot"
  }
  assert {
    condition     = length(output.reads_outside) == 0
    error_message = "The pilot and its modules read no file outside their own folders: ${jsonencode(output.reads_outside)}."
  }
}

run "a_client_that_is_not_a_slug_is_refused" {
  command = plan
  variables {
    clients = ["meridian", "Not_A_Slug"]
  }
  expect_failures = [var.clients]
}

run "a_sixteen_character_client_is_accepted" {
  command = plan
  variables {
    clients = ["abbbbbbbbbbbbbbb"]
  }
}

run "a_seventeen_character_client_is_refused_at_the_root" {
  command = plan
  variables {
    clients = ["abbbbbbbbbbbbbbbb"]
  }
  expect_failures = [var.clients]
}

run "a_client_named_twice_is_refused" {
  command = plan
  variables {
    clients = ["meridian", "meridian"]
  }
  expect_failures = [var.clients]
}

run "each_client_named_has_its_own_service_job_and_landing_bucket" {
  command = plan
  variables {
    clients = ["thornbury", "meridian"]
  }
  assert {
    condition     = keys(module.run) == ["meridian", "thornbury"] && module.run["thornbury"].env.PROFILE == "thornbury" && module.run["thornbury"].job_env.LANDING_URL == "gs://your-pilot-project-id-kge-thornbury-landing"
    error_message = "Each client named is deployed as its own service and job, its profile the client's id, with its own landing bucket."
  }
  assert {
    condition     = length(module.storage.public_access_prevention) == 4 && module.run["thornbury"].env.STORE_URL == module.run["meridian"].env.STORE_URL
    error_message = "One store bucket and one tmp bucket for every client, and a landing bucket each."
  }
  assert {
    condition     = module.storage.grants["store/thornbury-service-pack"].conditions[0].expression == "resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-store/objects/clients/thornbury/units/\") || resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-store/objects/clients/thornbury/onboard/\")" && module.storage.grants["store/meridian-service-pack"].conditions[0].expression == "resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-store/objects/clients/meridian/units/\") || resource.name.startsWith(\"projects/_/buckets/your-pilot-project-id-kge-store/objects/clients/meridian/onboard/\")" && module.run["thornbury"].job_executor.name == "kge-thornbury" && module.run["meridian"].job_executor.name == "kge-meridian"
    error_message = "Each service's pack and job grants are scoped to that same client."
  }
}
