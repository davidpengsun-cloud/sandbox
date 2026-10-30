# The pilot on Google Cloud: Terraform

Two configurations share one set of modules. [`pilot/`](pilot/main.tf) is the pilot: one project, Meridian only. [`prod/`](prod/main.tf) is QA and PROD: one configuration applied twice, through [`qa.tfvars`](prod/qa.tfvars) and [`prod.tfvars`](prod/prod.tfvars), each in its own project with its own state. Both run the demo's one image ([`Dockerfile`](../Dockerfile)) and deploy the clients their `clients` variable names, each client's profile its own id. QA and PROD are placeholders: they are not yet aligned with the GCP-native pilot baseline and are not supported for this release. The pilot is the only supported install. Nothing here reads a file outside its own folder. The release kit's `terraform/` ([the kit](../kit/INSTALL.md)), which an installer runs with no repository, is the pilot's alone: [`pilot/`](pilot/main.tf), `modules/` and [`tests/`](tests/declared/main.tf), the helper the pilot's tests read. `prod/` is not part of the kit.

The pilot configuration has been applied once, by the owner, from release kit 0.5.0 on a pilot project (#69). In this repository it is formatted, validated and tested against stand-in providers only ([checks](#checks-without-an-account)). No file holds a real project, billing account, organization, domain or host: the tfvars files hold obvious placeholders, and an account's own values are given on the command line.

## What each configuration creates

| Module | Creates |
|---|---|
| [`project`](modules/project/main.tf) | the APIs each configuration uses, and a monthly budget that alerts at 50%, 90% and 100% of its amount, and when the month's forecast reaches it |
| [`identity`](modules/identity/main.tf) | per client, a service account for its service and one for its job; a deployer account that GitHub Actions reaches through Workload Identity Federation, from one repository only, with no key |
| [`storage`](modules/storage/main.tf) | per client, a landing bucket (the landing contract's `releases/<release_id>/`), the job's alone; one store bucket, `<project>-kge-store`, that the services read and the jobs write: the packs, the registry and people, a job's work between executions, the released graphs and the passwords' hashes; with `tmp = true` (the pilot alone), one tmp bucket, `<project>-kge-tmp`, every client's service and job share for scratch files (`TMP_URL`), unversioned, each object deleted 7 days after it was made (`tmp_days`), each `tmp_iam` member holding the predefined `roles/storage.bucketViewer` on that bucket; public access prevention enforced, IAM only |
| [`registry`](modules/registry/main.tf) | one Docker repository, keeping the last 10 tagged images and deleting untagged ones after 7 days |
| [`bigquery`](modules/bigquery/main.tf) | an Enterprise reservation autoscaling from a baseline of 0 slots to a capped maximum, assigned to the project's queries; per client, a warehouses dataset, a graph dataset and a candidates dataset (the improvement job's, which nothing serves), empty |
| [`run`](modules/run/main.tf) | per client, one Cloud Run service and one fixed `onboard-request` job, with nothing mounted: the service reads the store (`STORE_URL`), and the job the store and its landing bucket (`LANDING_URL`); given a `tmp_bucket` (the pilot's), both write scratch files to it (`TMP_URL`); the service has `roles/run.invoker` on that job alone and cannot override its command or environment; the image is left to deployments, never moved by Terraform |
| [`secrets`](modules/secrets/main.tf) | per client, the pilot sign-in's Secret Manager entries, its hashed passwords and its session key, with no values |
| [`observability`](modules/observability/main.tf) | one email channel for the budget alert and a page-down alert, and an uptime check on each client's page (on a private page, the 403 Google's checkers get counts as up, which shows only that Cloud Run answers at the page's address, not that the page runs) |

The service has unconditional `roles/storage.objectViewer` on the store, including the bucket-level list permission, and
a conditioned `roles/storage.objectUser` grant only under `clients/<client>/`. The job retains the unconditional,
store-wide `roles/storage.objectAdmin` grant used by release kit 0.5.0. This is a residual risk if the job account itself
is compromised; object-prefix conditions cannot replace it because every job command lists `clients/` and `deploy/`,
and Cloud Storage authorizes `storage.objects.list` against the bucket rather than an object name. The service can invoke
only its own fixed job and cannot override its command or environment.

On the pilot's tmp bucket each client's service and job hold `roles/storage.objectUser` conditioned on their own
`tmp/<client>/` prefix, as the service's grant on the store is, and, unconditioned, the predefined
`roles/storage.bucketViewer`, which holds `storage.buckets.get` and `storage.buckets.list` and no object permission (a
BETA-stage role in Google's list): a BigQuery load from Cloud Storage needs `storage.buckets.get` on the bucket as well
as `storage.objects.get` on the file, and a condition on an object prefix cannot meet a check made against the bucket.
It is a predefined role, not a custom one, because Cloud Storage does not know a custom role made in the same apply for
some seconds, which would fail a first apply. Nothing else is granted on it: a client cannot read or write another's scratch files, nor list their names. The
condition does not cover `storage.objects.list`, which is checked against the bucket, and no grant there holds it, so
the image never lists the tmp bucket: it writes a file, has a load job read it by its exact name, and deletes it.

The job authorizes each page request against the current release's roles and the client's deployment identities, but it
trusts the principal name that the service writes into that request. The service authenticates the person and is therefore
the trust anchor: compromising it could let an attacker name a real steward and submit a steward action. A later hardening
step is a signed request whose authenticated principal and action the job verifies independently.

**The pilot** calls every module once, for `clients` (default `["meridian"]`; each a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters, named once): one service and one job. It has no Pub/Sub, Eventarc, Cloud Scheduler or IAP. A job runs when someone executes it, and the page is public and signs people in itself, a username and a password per demo principal. Where an organisation's domain-restricted sharing refuses `allUsers`, `public = false` keeps the page private and grants `roles/run.invoker` to the members `invokers` names instead (such as `domain:example.com`); a proxy then sends its Google identity token in `X-Serverless-Authorization`, leaving `Authorization` and the cookie to the page's own sign-in, which answers 401 to a Google token in `Authorization`. Nothing is protected from `terraform destroy`.

**QA and PROD**, placeholders not supported yet (above), call the same modules and add:

- Pub/Sub, Eventarc and Workflows: each landing bucket tells a topic of every object written under `releases/`, and a release's `manifest.json` starts the client's job with an explicit `onboard` override ([`events.tf`](prod/events.tf));
- Cloud Scheduler: each client's eval job (`eval_schedule`) and its improve job (`improve_schedule`, running under its own proposal-only service account), both weekly by default ([`events.tf`](prod/events.tf));
- IAP in front of every page, letting through the members `iap_members` names ([`iap.tf`](prod/iap.tf));
- CMEK: the environment's own Cloud KMS key on every bucket, dataset, secret, the repository, the revisions and the topics ([`cmek.tf`](prod/cmek.tf));
- deletion protection on every service, job, bucket, dataset, secret, the repository and the key, and a lien on the project;
- behind `organization_id`, null by default: VPC Service Controls (a perimeter in dry run), organization policies on the project, group-based IAP and workforce federation for the client's identity provider ([`organization.tf`](prod/organization.tf)). PROD refuses to apply while `organization_id` is null; QA applies without them.

## What waits for the account

- `terraform plan` and `apply`, for the pilot and for each of QA and PROD.
- The image pushed to the repository and deployed; a GitHub Actions workflow that does so through the deployer.
- The secrets' values: the session key, and the hashed passwords the job's `passwords` command writes to the store (`passwords.json`), which the service reads as `SESSION_SECRET` and `SIGNIN_PASSWORDS`.
- The models available to the project in Vertex AI. The pilot runs GCP-native by default: [`pilot/variables.tf`](pilot/variables.tf)'s `pilot_env`, validated live (#95), gives the service and the job Gemini on Vertex AI by tier (`gemini-3.8-flash` default and medium, `gemini-3.1-pro-preview` high, `gemini-3.5-flash-lite` low; reply budgets and timeout come from the image), the tagger on the low tier, Vertex AI's `gemini-embedding-001` embeddings 768 wide, and BigQuery for the warehouses, retrieval, app state and graph (`GRAPH_STORE=bigquery`: the graph's node and edge tables and property graph in `<prefix>_<client>_graph`, under a prefix per load of a model hash, made by the job's `onboard` with the `roles/bigquery.dataEditor` it has there); `app_env` overrides any one of them. The image's local defaults (DuckDB, its own Ladybug graph, rules and the hashing embedder) are for development and tests only. QA and PROD have no `pilot_env`: their model and BigQuery settings go in `app_env`. The service reads the project and the region as `VERTEX_PROJECT` and `VERTEX_REGION`, from `project_id` and `model_location` (`global`).
- BigQuery's datasets filled by the job: on the pilot every store the client has is BigQuery's, and no Cloud Run instance keeps a graph, a vector or a warehouse file of its own. `seed`, before the first `onboard`, builds the estate's warehouses as tables of `<prefix>_<client>_warehouses` (`<unit>_<table>`, recorded in its `_kge_catalogue`), which onboarding adds each unit's versions to; `onboard` loads the candidate's graph as node and edge tables and a property graph of its own generation in `<prefix>_<client>_graph`, and its chunks and their vectors as a chunks table of their own there, and the app state lives in the same dataset. A release carries the pointers, `graph.bigquery` and `chunks.bigquery`, naming those tables, with the artifact and the estate's own files, and no graph, chunk or warehouse file; the service reads the tables the release it serves names. The improvement job builds and checks its candidates in `<prefix>_<client>_candidates`, which nothing serves. An improvement run drops its candidate's graph generation and chunks table once the candidate's check ends, passed or failed; a table a crashed run leaves expires after 7 days, the candidates dataset's default table expiration (a property graph does not expire: delete one by hand if a crash leaves it).
- QA and PROD's organization: `organization_id`, the groups for IAP, the directory's customer ids and the client's identity provider for workforce federation.

## Bootstrap, once, from Cloud Shell

The installer bootstraps from the release kit alone, with no clone of this repository: its [`INSTALL.md`](../kit/INSTALL.md#install-on-google-cloud-with-terraform), section *Install on Google Cloud with Terraform*, carries every step, from the kit's `terraform/` folder. In short:

1. Install Terraform 1.17.0, checked against HashiCorp's sums, and make the project, its billing link and its state bucket.
2. `terraform init -backend-config="bucket=$PROJECT-tfstate" -backend-config="prefix=kge/pilot"` in `terraform/pilot`, then `terraform apply` of `module.secrets` alone, then the session key and a placeholder `{}` for the passwords as the secrets' first versions, then `terraform apply` of the rest.
3. Load the image tarball, push it to the repository and point the service and the job at it by digest (`gcloud run jobs update --image=...@sha256:<digest>`, `gcloud run deploy`); after this, Terraform leaves the image alone.
4. Set the client up and upload the example content through the job (`gcloud run jobs execute kge-meridian --wait --args=setup-client,...`, and `gcloud storage rsync` for the example), then `seed`, `onboard`, `sign-off`, `release` and `passwords`; copy `passwords.json` into the passwords secret, download `passwords.txt`, and delete both from the store with every version, since the bucket keeps a deleted object's versions for 30 days (`gcloud storage rm --all-versions $STORE/secrets/meridian/passwords.json $STORE/secrets/meridian/passwords.txt`); then deploy the image to the service. An instance that starts after a `release` serves it; a new revision restarts them all, so that every instance does.

QA and PROD are bootstrapped the same way, once per environment, from `prod/`, with their own project, state bucket and tfvars: `terraform init -backend-config=qa.gcs.tfbackend -backend-config="bucket=$PROJECT-tfstate"`, then `-var-file=qa.tfvars` (and `prod` for PROD, which needs `organization_id` first).

## Checks without an account

From this folder, with Terraform 1.17.0 on the PATH:

```sh
terraform fmt -check -recursive
(cd pilot && terraform init -backend=false && terraform validate && terraform test -var-file=example.tfvars)
(cd prod && terraform init -backend=false && terraform validate && terraform test -var-file=qa.tfvars && terraform test -var-file=prod.tfvars)
```

The tests ([`pilot/tests/`](pilot/tests/pilot.tftest.hcl), [`prod/tests/`](prod/tests/prod.tftest.hcl)) plan and apply against stand-in providers, creating nothing, and check that no bucket is public, two buckets in the pilot; that the pilot deploys exactly one service and one job, for meridian, the service reading the store and the job the store and its landing; that each client id must be a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters, and a client named twice is refused; that 16 characters are accepted and 17 are refused at the root variable; that a private page grants only the invokers it names, refuses one that is not an IAM member, and counts a 403 to the uptime check as up; that no file is read from outside its folder; and that the pilot has no Pub/Sub, Eventarc, Scheduler or IAP ([`tests/declared/`](tests/declared/main.tf) reads what each configuration declares); that PROD refuses a null `organization_id` and has deletion protection; and that QA applies without an organization. `terraform init` fetches the providers (`hashicorp/google` 8.8.0, and in `prod/` `hashicorp/google-beta` 8.8.0 too, held by each folder's `.terraform.lock.hcl`) from the registry, or from a filesystem mirror that `TF_CLI_CONFIG_FILE` names where the registry is out of reach. `.venv/bin/python -m pytest tests/unit/test_infra.py -q`, from the demo folder, reads the files and, with Terraform on the PATH, runs these checks too; `tests/unit/test_kit.py` runs them again from the kit's zip, outside the repository.
