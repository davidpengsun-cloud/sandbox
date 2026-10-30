# Installing the KGE pilot, kit 0.9.19

This guide installs the KGE pilot from its release kit alone: the zip, the image tarballs and this guide. Nothing else is needed, and nothing here is fetched from anywhere but the places named. The pilot serves a governed knowledge graph to a page and an API: people sign in with a username and a password, ask questions, and get figures that cite the certified sources they rest on.

It installs two ways, which hold the same parts:

| Mode | Services | Data features |
|---|---|---|
| KGE alone | `docker compose up -d app gcs` | KGE page and APIs |
| Portal alone | `KGE_URL= PORTAL_CATALOGUE_FILE=/catalogue/catalogue.yaml docker compose up -d portal` | catalogue browse, search, catalogue Ask and portal-owned activity; preview, export and query are off |
| Together | `docker compose up -d` | all KGE and portal features |

For portal-alone mode, the default mounted catalogue is `examples/meridian-portal/catalogue.yaml`, a complete export of Meridian’s compiled catalogue; the pack’s `catalogue.yaml` is only enrichment and is not a standalone provider file. `PORTAL_CATALOGUE_FILE_HOST` selects another host file. Before starting, create `identity/principals.json` beside `compose/`, using KGE's compiled principal format:

```json
[{"principal":"reader","role":"Reader","reads":["retail"],"processes":["querying"]}]
```

List explicit readable units, never `all`. `querying` or `certification` marks a person who may sign in. The display role does not grant portal actions: `_portal_users` holds those roles, defaulting to viewer; the existing admin and steward rows grant admin and approver actions only. Portal roles never widen unit access. This file can be written directly without running KGE.

Create the `identity/` directory first, then save the JSON above as `identity/principals.json`. From `compose/`, generate credentials using just the portal image (loaded as described below), then configure the portal:

```sh
chmod 700 ../identity
docker run --rm --user "$(id -u):$(id -g)" --entrypoint node \
  -v "$PWD/../identity:/identity" kge-portal:0.9.19 src/passwords.js /identity
export PORTAL_IDENTITIES_FILE=/identity/principals.json
export PORTAL_PASSWORDS_FILE=/identity/passwords.json
export PORTAL_SESSION_SECRET="$(cat ../identity/session_secret)"
KGE_URL= PORTAL_CATALOGUE_FILE=/catalogue/catalogue.yaml docker compose up -d portal
```

The generator writes `passwords.json` (salted scrypt hashes, the same format as KGE's), `passwords.txt` (each person's password, handed over privately and then deleted), and `session_secret`, all mode 600; it prints no credentials and refuses to overwrite existing files. Keep the exported settings for later starts, or store them in the private Compose `.env`. `PORTAL_IDENTITY_DIR_HOST` can select a different host directory, mounted read-only at `/identity`. The portal image's user must be able to read the mounted files; on Linux, assign that directory and its files to UID 1000, retaining their private modes.

Use the same sign-in page as in the combined mode. The standalone portal requires its own secret of at least 32 random characters, limits attempts by address and username (10/minute; five failures in 15 minutes lock for five minutes), and uses a 12-hour HttpOnly, SameSite=Lax session. Logout, restart, expiry or changing a password invalidates the session. Run one instance in standalone mode. Principal and password files are reread on each request; removing a person or narrowing their unit list takes effect immediately. Cookies default to Secure; this loopback-only Compose service sets `PORTAL_COOKIE_SECURE=false` for HTTP. Set it to `true` when serving HTTPS. No header establishes identity. With KGE present, sign-in remains KGE's and these standalone credential settings are unused.

| Part | On your machine (Docker Compose) | On Google Cloud (Terraform) |
|---|---|---|
| KGE's operator page and API | service `app` | a Cloud Run service per client, `kge-<client>` |
| The discovery portal and its API | service `portal` | a Cloud Run service per client, `kge-portal-<client>` |
| The job, one command per run | service `job` | a Cloud Run job per client, `kge-<client>` |
| The store: packs, people, the job's work, releases, passwords' hashes | bucket `kge-store` in a Cloud Storage emulator | bucket `<project>-kge-store` |
| The client's landing: certified releases of its sources | bucket `kge-<client>-landing` in the emulator | bucket `<project>-kge-<client>-landing` |
| Scratch files: large BigQuery loads, staged and then deleted | a folder in the container's temporary folder (`TMP_URL` unset; nothing loads into BigQuery here) | bucket `<project>-kge-tmp`, shared, each client's under `tmp/<client>/`, every object deleted 7 days after it was made |

The kit carries no client's own content. A client is set up once, after installation, by a job command, and its pack (what the graph is built from) goes up to the store. The kit holds invented example content for a fictional bank, Meridian, to upload as a client's pack and try the whole journey; it is optional.

## What the kit holds

```
kge-0.9.19/
  kge-0.9.19.zip                       VERSION; terraform/ (the pilot and its modules only);
                                          compose/ (compose.yaml, .env.example); examples/ (Meridian's invented pack);
                                          skills/ (four knowledge-engineer skills, versioned with this kit)
  images/kge-0.9.19.tar                the image, linux/amd64: the page, the API and every job command
  images/kge-0.9.19.digest             its image id
  images/kge-0.9.19.sbom.cdx.json      its software bill of materials (CycloneDX 1.5)
  images/kge-portal-0.9.19.tar         the discovery portal image, linux/amd64
  images/kge-portal-0.9.19.digest      its image id
  images/kge-portal-0.9.19.sbom.cdx.json its software bill of materials (CycloneDX 1.5)
  images/fake-gcs-server-1.56.1.tar       the Cloud Storage emulator for the Compose install, with its .digest
  docs/INSTALL.md                         this guide
  docs/ONBOARDING.md                      the business onboarding journey
  SHA256SUMS                              every file above
```

## Prerequisites

- Docker, with Compose v2 (`docker compose version`), on linux/amd64.
- `unzip`, `curl` and `sha256sum`.
- For Google Cloud, also: the Google Cloud CLI (`gcloud`), signed in as the project's owner (Cloud Shell has it), and Terraform 1.17.0.
- A browser.

When building a kit on an arm64 machine, pass `BUILD_FLAGS=--platform=linux/amd64`; the kit requires a linux/amd64 image.

## Install on your machine with Docker Compose

Every command runs in a POSIX shell (bash or zsh). The test drive brands the Meridian sample as your installed client and leaves a released starting baseline. The manual steps below also show the unchanged Meridian example, including a second release and rollback. Step 4 is the platform team's, once per client; everything after it runs through the product. `docs/ONBOARDING.md` explains the business onboarding journey.

1. From the kit's folder, check the files and load the images:

   ```sh
   sha256sum -c SHA256SUMS
   docker load -i images/kge-0.9.19.tar
   docker load -i images/kge-portal-0.9.19.tar
   docker load -i images/fake-gcs-server-1.56.1.tar
   ```

   With Docker's containerd image store, `docker image inspect --format '{{.Id}}' kge:0.9.19` then prints the id that `images/kge-0.9.19.digest` holds, and the emulator's is the digest `compose.yaml` pins.

2. Unzip the kit, and create the settings file from its example, with a new session key and portal sync token (48 random characters each, never printed):

   ```sh
   unzip kge-0.9.19.zip -d kge && cd kge/compose && cp .env.example .env
   { grep -v '^SESSION_SECRET=' .env || true; } > .env.new && mv .env.new .env
   printf 'SESSION_SECRET=%s\n' "$(head -c 36 /dev/urandom | base64)" >> .env
   { grep -v '^PORTAL_SYNC_TOKEN=' .env || true; } > .env.new && mv .env.new .env
   printf 'PORTAL_SYNC_TOKEN=%s\n' "$(head -c 36 /dev/urandom | base64)" >> .env
   chmod 600 .env
   . ./.env
   ```

   `.env` names the client (`CLIENT=meridian` by default; set your own client id, for example `CLIENT=aster-bank`, before reading the file), KGE's port (`KGE_PORT=8000`), the discovery portal's (`PORTAL_PORT=8090`) and the emulator's (`GCS_PORT=4443`); `. ./.env` reads them into this shell for the commands below. Every later command runs in `kge/compose`.

3. Start the Cloud Storage emulator:

   ```sh
   docker compose up -d gcs
   ```

   On a fresh installation, set up the client from `.env`, then run the test drive. It performs steps 5–6, checks the released graph's golden questions, prints its configuration report and saves the same report in the store. BigQuery, the tmp bucket and an unconfigured model are reported as not applicable here. A Compose pass does not test the pilot's real buckets because the emulator creates a missing bucket on first write.

   ```sh
   docker compose run --rm job setup-client CLIENT="$CLIENT" ASKER=analyst_enterprise "PEOPLE=analyst_retail:analyst_retail analyst_enterprise:analyst_enterprise finance_reporting:finance_reporting steward_glossary:steward_glossary business_team:business_team knowledge_engineer:knowledge_engineer advisor_wealth:advisor_wealth admin:admin steward:steward pipeline_tagger:pipeline_tagger"
   docker compose run --rm job test-drive CONFIRM=test-drive
   ```

   Add `"NAME=Aster Bank"` to override the display name. Omit `NAME` to derive the display name from the client id (`aster-bank` becomes `Aster Bank`; `meridian` keeps `Meridian Union Bank`). The test drive takes the client id from the install, never a command parameter: Compose supplies `PROFILE` from `.env`, matching `setup-client`. It refuses a client that already holds a pack, work, landing data or a release; a profile and identities alone are allowed only with the sample’s people, roles and asker. Existing setup is kept unchanged; different or unreadable identities are refused before any write. Read the report, then continue at step 7 to generate passwords, start the app and smoke-test the page while signed in as an example person. Keep `clients/<client>/` and the released graph in the store: this is your editable starting baseline, with the invented people, roles, units, tables and figures intact. Edit that pack, then run `onboard`, `sign-off PRINCIPAL=steward_glossary` and `release` again; use `seed` then `lock` first if changing the estate. A second test drive is only for starting over and requires removing the data locations in its report. The manual steps below use Meridian and remain the reference for individual operations.

4. Set the client up: its profile and its people, each with a role. The asker is who the job's checks ask as, and the baseline is the A/B baseline the post-load check holds the graph to, a file of the client's pack. The example's baselines were recorded on Ladybug, the local stack's graph store; with `GRAPH_STORE=bigquery` the post-load check will not use one recorded on another store (it stops, not checked, and says so), so the pilot records its own once, with the job's `baseline` command ([BigQuery](#bigquery)):

   ```sh
   docker compose run --rm job setup-client CLIENT=meridian ASKER=analyst_enterprise BASELINE=baselines/issue-40.json "PEOPLE=analyst_retail:analyst_retail analyst_enterprise:analyst_enterprise finance_reporting:finance_reporting steward_glossary:steward_glossary business_team:business_team knowledge_engineer:knowledge_engineer advisor_wealth:advisor_wealth admin:admin steward:steward pipeline_tagger:pipeline_tagger"
   ```

5. Upload the example content, Meridian's pack, its lock and its baselines, to the store:

   ```sh
   docker compose run --rm job put-example
   ```

6. Seed, onboard, sign off and release:

   ```sh
   docker compose run --rm job seed                                      # about 5 minutes
   docker compose run --rm job onboard                                   # about 3 minutes
   docker compose run --rm job sign-off PRINCIPAL=steward_glossary
   docker compose run --rm job release
   ```

   `seed` builds Meridian's invented estate and lands its certified release in the landing bucket. `onboard` checks the pack, compiles it into an artifact named by its hash, loads it into a graph of its own and runs the post-load check on it; note the hash on its `GATE:` line (`GATE: /app/build/stores/meridian/<hash>/GATE: passed`), H1. If that check fails, its log prints the saved `report.md` path. Run `onboard` again after addressing the cause: the same pack's loaded candidate is restored from the store and only the margin is rerun; a changed pack loads a new candidate. `sign-off` is the steward's sign-off on the check that passed, and `release` publishes the graph to the store as the one to serve.

7. Generate each person's password. The object URLs below name Meridian; after a test drive for another installed client, substitute that client id for `meridian` in both URLs. The job keeps their hashes in the store and the passwords once, beside them; download the passwords, then delete that object from the store (keep `passwords.json`, which the page signs people in with):

   ```sh
   docker compose run --rm job passwords
   curl -fsS -o passwords.txt "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/secrets%2Fmeridian%2Fpasswords.txt?alt=media"
   curl -fsS -o /dev/null -X DELETE "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/secrets%2Fmeridian%2Fpasswords.txt"
   chmod 600 passwords.txt
   ```

   `passwords.txt` holds one line per person who signs in, a username and its password. Hand each person theirs; `pipeline_tagger`, a machine, has none.

8. Start KGE and the discovery portal:

   ```sh
   docker compose up -d app portal
   ```

   Open `http://127.0.0.1:${PORTAL_PORT}`. Sign in with a KGE username and password, browse the products in that person's readable units, ask a question and sign out. A product's Data preview tab, for a table landed in the current release, shows its first governed rows and offers a capped CSV export; Run a query accepts read-only SQL for that product's unit. All three go through KGE's access rule, SQL gate and row caps. Row selectors follow Ask's context scope: a selector applies to reads within its context's unit and joins, not to every read of the table it names. For example, Meridian's `banks_with_retail` belongs to a Wealth context; it does not filter a Retail analyst's direct read of `retail.customer`. The portal forwards the host-only KGE session cookie server to server; it has no session key of its own. It syncs the released catalogue when it starts and every five minutes (`PORTAL_CATALOGUE_SYNC_MS`, in milliseconds); compatible existing records are preserved; an incompatible table shape requires an explicit migration and is never dropped automatically.

   Open http://127.0.0.1:8000. Sign in as `analyst_enterprise` with its password from `passwords.txt`. The header names the profile, `meridian`, and the artifact it serves, the first 12 characters of H1. Ask *Total retail revenue for June 2025* with the Arm set to `graph`: it answers **13,287.67**. Ask it again on each of the reference agent's two arms, `agent control, graph off (rules)` and `agent treatment, graph on (rules)`: each answers 13,287.67.

9. A second release, without rebuilding anything. Upload the example's second version of the pack, which reads *takings* as revenue, and take it through the same steps:

   ```sh
   docker compose run --rm job put-example EXAMPLE=meridian-takings
   docker compose run --rm job onboard                                   # note its hash, H2
   docker compose run --rm job sign-off PRINCIPAL=steward_glossary
   docker compose run --rm job release
   docker compose restart app
   ```

   Reload the page and sign in again if asked. The header names H2, and *Total retail takings for June 2025* on the `graph` arm answers 13,287.67.

10. Roll back. List the releases, release H1 again (a release made before needs no new sign-off), and restart the page:

    ```sh
    docker compose run --rm job releases
    docker compose run --rm job release HASH=<H1>
    docker compose restart app
    ```

    `releases` lists H1 and H2, marking the current one. After the restart the header names H1, and *Total retail takings for June 2025* abstains on the `graph` arm and on `agent treatment, graph on (rules)`: the pack of H1 knows no such term. (`agent control, graph off (rules)` reads no graph, so the rollback does not change its answer.) Put the example's first pack back in the store, so that the next onboard builds from it:

    ```sh
    docker compose run --rm job put-example
    ```

11. Check the discovery portal. Open `http://127.0.0.1:${PORTAL_PORT}` and sign in as `analyst_retail` with its password from `passwords.txt`. Browse: the products listed are the retail unit's ten tables, one product per table, including *Account* and *Branch*, and nothing of SME, Corporate or Wealth. Open *Account*, then ask a question about retail in the portal's ask panel, *Total retail revenue for June 2025*: KGE answers it, as the page answers `analyst_retail`. Sign out: the portal returns to its sign-in page. The same check from the shell:

    ```sh
    PORTAL=http://127.0.0.1:${PORTAL_PORT}
    curl -fsS -c portal.cookies -H 'content-type: application/json' \
      -d "{\"username\":\"analyst_retail\",\"password\":\"$(awk '$1=="analyst_retail"{print $2}' passwords.txt)\"}" "$PORTAL/session"
    curl -fsS -b portal.cookies "$PORTAL/api/products" | python3 -c 'import json,sys; products=json.load(sys.stdin); assert len(products) == 10 and {"retail.account", "retail.branch"} <= {p["id"] for p in products} and {p["unit_id"] for p in products} == {"retail"}'
    curl -s -o /dev/null -w '%{http_code}\n' -b portal.cookies "$PORTAL/api/products/wealth.HSHLD_DIM_1"
    curl -fsS -b portal.cookies -X DELETE "$PORTAL/session" && rm portal.cookies
    ```

    The sign-in answers with `analyst_retail`'s identity, `/api/products` passes the ten-retail-product check, another unit's product answers `404`, and the sign-out ends the session.

`docker compose down` stops everything and keeps the store; `docker compose down -v` deletes the store too.

## Install on Google Cloud with Terraform

The kit carries the pilot's Terraform only: `terraform/pilot` (one project, the clients the `clients` variable names, `["meridian"]` by default), the modules it calls (`terraform/modules`) and a helper its tests read (`terraform/tests`). QA and PROD are not part of this kit. The steps below are the pilot's; they run in Cloud Shell, from the folder the kit's zip was unzipped into (`kge/`), with the kit's images loaded into Cloud Shell's Docker (step 1 above). The variables in capitals hold your account's own values.

### Bootstrap the project, once

For an installation with custom names, use [Custom resource names and settings](#custom-resource-names-and-settings) when substituting the example values below.

1. Install Terraform 1.17.0, checked against HashiCorp's sums. 1.17.0 is the tested version; the configurations require `~> 1.16`.

   ```sh
   cd ~ && curl -fsSLO https://releases.hashicorp.com/terraform/1.17.0/terraform_1.17.0_linux_amd64.zip \
     && curl -fsSLO https://releases.hashicorp.com/terraform/1.17.0/terraform_1.17.0_SHA256SUMS \
     && grep linux_amd64 terraform_1.17.0_SHA256SUMS | sha256sum -c - \
     && mkdir -p ~/bin && unzip -o terraform_1.17.0_linux_amd64.zip terraform -d ~/bin && export PATH=~/bin:$PATH
   ```

2. Name your values. `REPO` is a GitHub repository your organisation owns, `owner/name`: its Actions alone may deploy the image (the pilot needs none to). `KIT` is the folder the zip was unzipped into:

   ```sh
   export PROJECT=<pilot project id> BILLING=<billing account> REGION=europe-west2 REPO=<owner>/<repository> ALERTS=<alert email>
   export KIT=<the folder holding terraform/ and examples/>
   ```

3. Create the project, link it to the billing account, turn on what Terraform's first calls need, and create the state's bucket, private and versioned:

   ```sh
   gcloud projects create $PROJECT && gcloud config set project $PROJECT
   gcloud billing projects link $PROJECT --billing-account=$BILLING
   gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com storage.googleapis.com billingbudgets.googleapis.com
   gcloud storage buckets create gs://$PROJECT-tfstate --location=$REGION --uniform-bucket-level-access --public-access-prevention
   gcloud storage buckets update gs://$PROJECT-tfstate --versioning
   ```

4. Apply the secrets first, add their first versions, then apply the rest: a service's revision needs its secrets' values. The session key is 48 random characters; the passwords' first version is a placeholder, `{}`, until the job generates them:

   ```sh
   cd $KIT/terraform/pilot
   terraform init -backend-config="bucket=$PROJECT-tfstate" -backend-config="prefix=kge/pilot"
   VARS=(-var-file=example.tfvars -var="project_id=$PROJECT" -var="billing_account=$BILLING" -var="region=$REGION" -var="github_repository=$REPO" -var="alert_email=$ALERTS")
   terraform apply "${VARS[@]}" -target=module.secrets
   head -c 36 /dev/urandom | base64 | tr -d '\n' | gcloud secrets versions add kge-meridian-session-key --data-file=-
   printf '{}' | gcloud secrets versions add kge-meridian-passwords --data-file=-
   terraform apply "${VARS[@]}"
   ```

   This creates, for Meridian: the store bucket, its landing bucket and the tmp bucket (all private), the tmp bucket's grants, among them the predefined `roles/storage.bucketViewer` (`storage.buckets.get` and `storage.buckets.list`, no object permission; a BETA-stage role in Google's list), which a BigQuery load from the tmp bucket needs, a Cloud Run service and a Cloud Run job, `kge-meridian`, each with its own service account, the discovery portal's Cloud Run service, `kge-portal-meridian`, running as the `kge-meridian` service's account (it adds no account, dataset or grant; it makes its own `_portal_*` tables in the client's existing warehouse dataset when it first starts), the image repository `kge`, BigQuery's reservation and datasets (empty for now), the budget and its alerts. The page is public and signs people in itself.

   **A private page.** Where your organisation's domain-restricted sharing (`constraints/iam.allowedPolicyMemberDomains`) refuses `allUsers`, the apply's grant of the page to `allUsers` fails. Add `-var="public=false"` and the members who may reach the page, such as `-var='invokers=["domain:example.com"]'`, to `VARS` and apply again: Cloud Run then lets in only those members, each with a Google identity token, and the page still signs people in itself. A proxy that calls the private page sends the Google identity token (`gcloud auth print-identity-token`) in `X-Serverless-Authorization: Bearer <token>`, which Cloud Run checks, and leaves `Authorization` and `Cookie` to the page's own sign-in: the page reads `Authorization: Bearer` as its own sign-in token, ahead of the session cookie, so a Google token there is an unknown token, answered 401. The uptime check then counts the 403 Cloud Run gives Google's checkers as up, which proves only that Cloud Run answers at the page's address, not that the page runs: Cloud Run refuses the checkers before any instance is reached.

### Custom resource names and settings

The commands below use the example's names. For another installation, read its own Terraform state in the initialised `terraform/pilot` folder, using the same backend and workspace as its apply. After the secrets-only apply, read `terraform output -json secrets` for the client's `session-key` and `passwords` ids before adding their first versions. After the full apply, read the remaining outputs:

```sh
terraform output -raw store_bucket
terraform output -json landing_buckets
terraform output -raw tmp_bucket
terraform output -raw repository
terraform output -json jobs
terraform output -json services
terraform output -json portals
terraform output -json secrets
STORE=gs://$(terraform output -raw store_bucket)
IMAGE=$(terraform output -raw repository)/kge
PORTAL_IMAGE=$(terraform output -raw repository)/kge-portal
```

`landing_buckets`, `jobs`, `services`, `portals` and `secrets` are maps keyed by client id. Select the same client in each. Use its job name wherever the examples say `kge-meridian` for a job, and its passwords secret id wherever they say `kge-meridian-passwords`. `services` and `portals` hold URLs, not service names: match those URLs to the installed Cloud Run services to obtain their names for deploy and update commands, replacing `kge-meridian` and `kge-portal-meridian`. Read the runtime service accounts from those services and the job, rather than inferring their emails from a prefix. For resources provisioned separately, use the platform team's actual bucket names, repository, service and job names, URLs, secret ids and service accounts. Keep those names when following [Push the images](#push-the-images) and [Passwords and the service](#passwords-and-the-service); use the chosen client id in store paths in place of `meridian`. For a fresh custom client, follow [Set the client up and release](#set-the-client-up-and-release)'s test drive.

BigQuery dataset ids must follow `<BIGQUERY_DATASET_PREFIX>_<PROFILE with underscores>_{warehouses,graph,candidates}`: replace each hyphen in `PROFILE` with an underscore. For example, prefix `pilot` and profile `aster-bank` give `pilot_aster_bank_warehouses`, `pilot_aster_bank_graph` and `pilot_aster_bank_candidates`. KGE resolves all three this way; the portal resolves the same `warehouses` dataset for its `_portal_*` tables. Set the same prefix and profile on both. A custom dataset with an unrelated name is not selected by these settings; provision the matching names and grants described in [BigQuery](#bigquery).

Neither image carries an installation's prefix, client, buckets, service accounts or Vertex AI settings. KGE's local defaults and the pilot's Gemini 3+ tiers and embedding settings are listed in [The model](#the-model); the portal's settings are in [The image's settings](#the-images-settings). Both the KGE service's and job's runtime accounts need `roles/aiplatform.user` on `VERTEX_PROJECT`, with `aiplatform.googleapis.com` enabled there, for keyless Application Default Credentials (`google.auth.default`). The supplied Terraform grants that role to both accounts and enables the API in its project; if Vertex AI uses another project, arrange the grants and API there too.

If custom Terraform did not inject `pilot_env`, supply its model and backend settings when updating the images. Set `CLIENT`, `PREFIX`, `LANDING_BUCKET`, `TMP_BUCKET`, `JOB_NAME`, `SERVICE_NAME` and `PORTAL_NAME` to the actual values above; set `JOB_SA` and `SERVICE_SA` to their runtime account emails. With `STORE` from the step above:

```sh
RESOURCE_ENV="PROFILE=$CLIENT,BIGQUERY_PROJECT=$PROJECT,BIGQUERY_DATASET_PREFIX=$PREFIX,STORE_URL=$STORE,LANDING_URL=gs://$LANDING_BUCKET,TMP_URL=gs://$TMP_BUCKET,WAREHOUSE_BACKEND=bigquery,RETRIEVAL_BACKEND=bigquery,APP_STATE_BACKEND=bigquery,GRAPH_STORE=bigquery"
MODEL_ENV="VERTEX_PROJECT=$PROJECT,VERTEX_REGION=global,RUN_REGION=$REGION,MODEL_PROVIDER=vertex-gemini,MODEL_NAME=gemini-3.8-flash,MODEL_HIGH_PROVIDER=vertex-gemini,MODEL_HIGH_NAME=gemini-3.1-pro-preview,MODEL_MEDIUM_PROVIDER=vertex-gemini,MODEL_MEDIUM_NAME=gemini-3.8-flash,MODEL_LOW_PROVIDER=vertex-gemini,MODEL_LOW_NAME=gemini-3.5-flash-lite,TAGGER_PROVIDER=model,TAGGER_MODEL_TIER=low,EMBEDDING_PROVIDER=vertex,VERTEX_EMBEDDING_MODEL=gemini-embedding-001,EMBEDDING_DIM=768"
gcloud run jobs update "$JOB_NAME" --region="$REGION" --service-account="$JOB_SA" --update-env-vars="$RESOURCE_ENV,$MODEL_ENV"
gcloud run services update "$SERVICE_NAME" --region="$REGION" --service-account="$SERVICE_SA" --update-env-vars="$RESOURCE_ENV,$MODEL_ENV"
```

Use the models available to the Vertex AI project, as [The model](#the-model) describes. Keep `RUN_REGION` as the Cloud Run region even when `VERTEX_REGION` is `global`. Change `VERTEX_PROJECT` in `MODEL_ENV` if the model project differs from the data project. Use `--update-env-vars` for settings changes, including with `gcloud run deploy`; never use `--set-env-vars`, which removes the other environment variables. Preserve the existing secret mounts ([Mounts and secrets](#mounts-and-secrets)), and record the same settings in Terraform so a later apply retains them.

Set `KGE_URL` to the KGE address the portal calls, `KGE_RUN_URL` to the KGE service's canonical Cloud Run URL, and `PORTAL_SA` to the portal's runtime account email. Configure the trusted caller before deploying the portal image in [Passwords and the service](#passwords-and-the-service):

```sh
gcloud run services update "$SERVICE_NAME" --region="$REGION" --update-env-vars="SIGNIN=passwords,TRUSTED_CALLER_EMAIL=$PORTAL_SA,TRUSTED_CALLER_AUDIENCE=$KGE_RUN_URL"
gcloud run services update "$PORTAL_NAME" --region="$REGION" --service-account="$PORTAL_SA" --update-env-vars="PROFILE=$CLIENT,PORTAL_BACKEND=bigquery,BIGQUERY_PROJECT=$PROJECT,BIGQUERY_DATASET_PREFIX=$PREFIX,KGE_URL=$KGE_URL,KGE_AUDIENCE=$KGE_URL,KGE_CALLER_AUDIENCE=$KGE_RUN_URL"
```

### Push the images

Push the two loaded images to the project's repository, and point the job at KGE's by the digest the push reports:

```sh
STORE=gs://$(terraform output -raw store_bucket)
IMAGE=$(terraform output -raw repository)/kge
PORTAL_IMAGE=$(terraform output -raw repository)/kge-portal
gcloud auth configure-docker $REGION-docker.pkg.dev
docker tag kge:0.9.19 $IMAGE:0.9.19 && docker push $IMAGE:0.9.19
docker tag kge-portal:0.9.19 $PORTAL_IMAGE:0.9.19 && docker push $PORTAL_IMAGE:0.9.19
DIGEST=$(gcloud artifacts docker images describe $IMAGE:0.9.19 --format='value(image_summary.digest)')
PORTAL_DIGEST=$(gcloud artifacts docker images describe $PORTAL_IMAGE:0.9.19 --format='value(image_summary.digest)')
gcloud run jobs update kge-meridian --region=$REGION --image=$IMAGE@$DIGEST
```

A custom installation ([Custom resource names and settings](#custom-resource-names-and-settings)) points its own job at the image here, in place of `kge-meridian`: `gcloud run jobs update "$JOB_NAME" --region="$REGION" --image="$IMAGE@$DIGEST"`. Terraform never moves either image after this: deployments do. The portal's image is deployed once the page serves a release, in *Passwords and the service* below.

### Set the client up and release

After pointing the job at the pushed image, test the fresh installation before creating passwords or deploying the service. Copy the single sample to the shared source prefix, select the job for a client in Terraform’s `clients` list, and execute it with a timeout long enough for the complete run. That job already has its client id in `PROFILE`; do not pass a client id to `test-drive`. Its report is printed in the execution log and saved under `runs/<client>/test-drive/`:

```sh
gcloud storage rsync -r $KIT/examples/meridian $STORE/examples/meridian
gcloud run jobs execute kge-aster-bank --region=$REGION --wait --task-timeout=10800s --args="test-drive,CONFIRM=test-drive,NAME=Aster Bank"
```

Use your installed job’s name in place of `kge-aster-bank`. `NAME` is optional and otherwise derived from the client id, as under Compose. The test drive refuses a client that already holds data, while allowing its setup-only profile and identities only with the sample’s people, roles and asker. Existing setup is kept unchanged; different or unreadable identities are refused before any write. It checks the configured buckets, BigQuery datasets, models, embeddings and versions, then performs the example setup, seed, catalogue lock, onboarding, sign-off, release and golden check. It then checks that every example table is in the released catalogue with its governed metadata, and checks data preview, a small query and cross-unit refusal through the data service. After a pass, keep the branded pack in `clients/<client>/` and its release as the client’s starting baseline. Edit the pack and run `onboard`, `sign-off` and `release` on the same job to evolve it. Remove the report’s data locations only to discard that baseline for a fresh test drive. Once it passes, the client is set up and released: run `passwords` (`gcloud run jobs execute kge-aster-bank --region=$REGION --wait --args=passwords`), then follow [Passwords and the service](#passwords-and-the-service) to deploy the service, and smoke-test the page signed in as one of the example's people. The manual sequence below does the same one command at a time, and stays the reference for each step.

The same commands as on your machine, each one execution of the job, with one difference: the first `setup-client` names no `BASELINE`. The example's baselines were recorded on Ladybug, and the pilot's graph is in BigQuery, so the post-load check would refuse to put a run beside them; with no baseline it checks the candidate against the pack's own gates alone (the no-fall check against a baseline skipped) and writes its gate, so the first candidate can be signed off and released. The example content goes up with `gcloud storage rsync`:

```sh
JOB=(gcloud run jobs execute kge-meridian --region=$REGION --wait)
"${JOB[@]}" --args="setup-client,CLIENT=meridian,ASKER=analyst_enterprise,PEOPLE=analyst_retail:analyst_retail analyst_enterprise:analyst_enterprise finance_reporting:finance_reporting steward_glossary:steward_glossary business_team:business_team knowledge_engineer:knowledge_engineer advisor_wealth:advisor_wealth admin:admin steward:steward pipeline_tagger:pipeline_tagger"
gcloud storage rsync -r $KIT/examples/meridian $STORE
"${JOB[@]}" --args=seed
"${JOB[@]}" --args=onboard
"${JOB[@]}" --args=sign-off,PRINCIPAL=steward_glossary
"${JOB[@]}" --args=release
"${JOB[@]}" --args=passwords
```

Once the first release is made, record the pilot's own baseline and point the client at it, so every later `onboard` and `improve` is checked beside it as well as against the gates. `baseline` runs the A/B run on the released graph, which needs the client's `current`, made by `release`, and prints a complete `setup-client` command with the new baseline, every current person in file order and the current asker when there is one:

```sh
"${JOB[@]}" --args="baseline"
```

Run the printed command through the same job, passing its command and assignments as comma-separated `--args` values as in the first `setup-client` execution above. Its `PEOPLE` value remains one argument even though it contains spaces. If the deployment identities cannot be read, the output says why and prints the placeholder command instead; recover the current values before running it.

An execution's output is in Cloud Logging: `gcloud run jobs executions list --job=kge-meridian --region=$REGION` names each, and the console's job page shows its logs (onboard's names the hash).

### Passwords and the service

Copy the hashes into a new version of the passwords secret, download the passwords, then delete both objects from the store with every version of them (the store keeps a deleted object's versions for 30 days); then deploy the image to the service:

```sh
gcloud storage cat $STORE/secrets/meridian/passwords.json | gcloud secrets versions add kge-meridian-passwords --data-file=-
gcloud storage cp $STORE/secrets/meridian/passwords.txt passwords.txt && chmod 600 passwords.txt
gcloud storage rm --all-versions $STORE/secrets/meridian/passwords.json $STORE/secrets/meridian/passwords.txt
gcloud run deploy kge-meridian --region=$REGION --image=$IMAGE@$DIGEST
gcloud run deploy kge-portal-meridian --region=$REGION --image=$PORTAL_IMAGE@$PORTAL_DIGEST
```

Check the portal from Cloud Shell. Its health check needs no sign-in, and a sign-in through it is KGE's own: the portal forwards it to `kge-meridian`, server to server, and hands back KGE's session cookie under its own address. The portal holds no session key:

```sh
PORTAL=$(gcloud run services describe kge-portal-meridian --region=$REGION --format='value(status.url)')
curl -fsS "$PORTAL/health"
curl -fsS -c portal.cookies -H 'content-type: application/json' \
  -d "{\"username\":\"analyst_retail\",\"password\":\"$(awk '$1=="analyst_retail"{print $2}' passwords.txt)\"}" "$PORTAL/session"
curl -fsS -b portal.cookies "$PORTAL/readable-units"
curl -fsS -b portal.cookies -X DELETE "$PORTAL/session" && rm portal.cookies
```

`/health` answers `{"status":"ok"}`, the sign-in answers with `analyst_retail`'s identity and `/readable-units` with `{"units":["retail"]}`. Open `$PORTAL` in a browser to browse that person's products and ask a question. The portal calls `kge-meridian` server to server. On a private page (`public=false`) Terraform grants the client's service account, which the portal runs as, `roles/run.invoker` on its paired `kge-<client>` service alone, and the portal sends its own Google identity token, minted by Cloud Run's metadata server for the audience `KGE_AUDIENCE` (the KGE service's address), in `X-Serverless-Authorization`, separate from the person's KGE session cookie, which KGE still checks. The portal itself is then private too: open it, and the commands above, through a proxy that sends your Google identity token, as for the page.

Then join the page to the portal: on Cloud Run both addresses are public, so the portal's one address serves the page's link to it and KGE's call for its logo. Add `PORTAL_URL = "<the portal's https URL, the value of $PORTAL>"` to `app_env` in the tfvars (`example.tfvars`, or yours), keeping what `app_env` holds already, and apply again from `pilot`: `terraform plan "${VARS[@]}"` shows the service and the job gaining `PORTAL_URL` and nothing else, then `terraform apply "${VARS[@]}"`. The new revision links to the portal as **Discover** in its bar and, with no `KGE_LOGO` of its own, takes the portal's `PORTAL_LOGO` as it starts (that logo is a file on a volume you add to the portal's service yourself, outside Terraform: the kit's Terraform mounts no logo); the portal already links back to the page as **Ask** through its `KGE_URL`. With `public = false` the portal is private too: KGE's call for its logo carries no Google identity token and is refused, so the page shows the client's name; set `KGE_LOGO` as well to show the logo there: the same file as `PORTAL_LOGO`, on a volume you add to the `kge-<client>` service yourself, outside Terraform, as for the portal (the kit's Terraform mounts no logo on either).

`gcloud run services describe kge-meridian --region=$REGION --format='value(status.url)'` gives the page's address; sign in as on your machine. On a private page, open it through a proxy that sends your Google identity token in `X-Serverless-Authorization` ([A private page](#bootstrap-the-project-once)). Each instance of the service serves the release the store names current when the instance starts, so after a `release` any instance that starts serves the new one while those already running serve the old. A new revision restarts them all, so that every instance serves it; the command below makes one, by changing a setting that nothing reads:

```sh
gcloud run services update kge-meridian --region=$REGION --update-env-vars=KGE_RELEASE=<hash>
```

### Roles in onboarding and improvement

Roles belong to the pack (`access.roles`); people are deployment identities bound to those role ids by `setup-client`, whose `PEOPLE` argument lists `<principal>:<role>` bindings. Processes are platform-declared capabilities: `drafting`, `building`, `improving` and `accepting`, alongside `querying` and `certification`. A pack grants those processes to roles; a role label alone grants nothing. The knowledge engineer uploads and drafts, the business team accepts, the steward certifies, the knowledge engineer runs checks, and the steward signs off and releases. The platform engineer installs the service and assigns people; installation grants no KGE role.

Four people take part: the knowledge engineer, the business team, the steward and the end user. The page checks each step against the role of the person signed in, as `PEOPLE` in `setup-client` binds each person to one of the pack's roles:

| Step | Who does it | Role the page checks | Page route | Job command |
|---|---|---|---|---|
| Land a unit: its tables, its sources, its rows | Knowledge engineer | `drafting` | `POST /onboard/tables`, `POST /onboard/source`, `POST /onboard/rows` | page only |
| Draft | Knowledge engineer | `drafting` | `POST /onboard/draft` | page only |
| Accept drafted items, answer open requests | Business team | `business` | `POST /onboard/accept` | page only |
| Certify | Steward | `steward` | `POST /onboard/certify` | page only |
| Build and gate | Knowledge engineer | `building` | `POST /onboard/run`, step `checks` | `onboard` |
| Read the margin check | Knowledge engineer | `building` | `GET /onboard/margin` | API |
| Improve | Knowledge engineer | `improving` | `POST /onboard/run`, step `improve` | `improve` |
| Sign off, release | Steward | `steward` | `POST /onboard/run`, steps `sign-off`, `release` | `sign-off PRINCIPAL=<steward>`, `release` (knowledge engineer's break-glass access) |
| Ask | End user | `needs("querying")` | `POST /ask`, `POST /answer`, `POST /agent`, `POST /context` | page or API only |

- `business`: a person whose role has `accepting`, or, for compatibility, writes `terms` and `mappings` without `certification`; in the example, `business_team`.
- `steward`: a person whose role performs `certification`; in the example, `steward_glossary`, `steward` and `admin`. The existing steward roles have no `accepting` grant, keeping accepting and certifying apart.
- `drafting`, `building`, `improving`: the corresponding process in the pack role. `knowledge_engineer` has all three and `querying`, with no acceptance writes or `certification`. The business team reads, accepts and answers requests; it has none of these automated processes. Stewards certify, sign off and release; they do not run checks or improvements.
- `participant`: the business team, a steward, or a role with any of those three processes, which opens the page's onboarding panel.
- `needs("querying")`: a role with `querying`, which answers within the units it reads.

The business team answers open requests with the knowledge engineer, who uploads any revised sources; a request also closes when its item is certified. Of the job commands, `sign-off` checks `PRINCIPAL`; `onboard`, `improve` and `release` name no person, so whoever may execute the job may run them: on Google Cloud, whoever IAM lets execute it; under Compose, whoever runs `docker compose`.

Whoever runs the KGE job on Google Cloud, or `docker compose run job` locally, is a knowledge engineer. Running the job by hand can sign off with `sign-off PRINCIPAL=<steward>` and `release` without the page. This is the knowledge engineers' break-glass power, recorded in Cloud Run's execution log. The page remains the governed route: its engineer principal cannot take these human steps. Signed page requests that remove the job operator's power are later hardening, outside this change.

The knowledge engineer uses its own principal, `knowledge_engineer` in the invented Meridian people. HTTP and MCP apply the same process checks. Accept, certify, sign-off and release require their human roles and have no MCP tools. The platform engineer owns installation through Google Cloud IAM and holds no KGE role by default; neither role implies the other. Older packs still compile and serve; their roles need explicit `drafting`, `building` and `improving` grants to use those automated endpoints.

Improvement is a workflow of its own, not a step of onboarding: it proposes (`improve`), the business team accepts the proposal, the steward certifies it, its candidate is built and checked, and the steward signs it off and releases it, under the same roles as the table's.

### Manual and assisted routes

The manual commands in the roles table remain the reference route. After unzipping the kit, `skills/` contains four folders, each with a `SKILL.md`: `land-and-draft`, `build-and-gate`, `improve` and `ask`. Load the chosen skill into the assistant connected to this installation over HTTP or MCP. Keep the skills with the kit version they came from. They require no additional server installation.

The folders follow the Agent Skills open standard. Copy each chosen folder from the kit's `skills/` into one of these locations; keep each folder whole and its name unchanged. Locations verified against the vendors' documentation on 2026-10-25:

| Agent | In a repository | Personal |
|---|---|---|
| GitHub Copilot (CLI, VS Code agent mode, cloud agent) | `.github/skills/`, `.claude/skills/` or `.agents/skills/` | `~/.copilot/skills/` or `~/.agents/skills/` |
| OpenAI Codex (CLI, IDE extension, app) | `.agents/skills/` | `~/.agents/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |

The `.agents/skills/` location serves Copilot and Codex alike. For example, copying `ask` there gives `.agents/skills/ask/SKILL.md`.

Connect the agent to this installation's streamable HTTP MCP servers: all four skills use `/mcp/onboard`; `ask` also uses `/mcp/answer`, `/mcp` and `/mcp/identity`. Use the person's own bearer token from `POST /session/token`, following [Tokens for an agent](#tokens-for-an-agent). Each agent has its own MCP configuration format; supply the server URL and header, for example:

```text
Server URL: https://<kge-host>/mcp/onboard
Authorization: Bearer <token>
```

For agents without MCP, use the HTTP endpoints listed in each skill with the same bearer token, role and unit permissions. The steps work the same on either transport.

The assisted route starts with a unit-state check and runs as the person's own authenticated principal on either transport. Land and draft previews and uploads sources and rows, drafts items, and hands them to the business team and steward on the page. After acceptance and certification, build and gate runs checks and reports the candidate hash and margin evidence; the steward signs off and releases on the page. Skills never perform human decisions or use job-operator break-glass powers.

After release, improve is a separate workflow that proposes changes for the same human review, followed by build and gate. Runs are client-wide by default. Select one unit with the job command `improve UNIT=<id>` or the HTTP/MCP `unit` argument. Only that unit supplies evidence and proposed edits. An unknown unit is refused, and so is one outside the person's improving grant and readable units: on HTTP and MCP always, on the job command when it names `PRINCIPAL=<person>`. Candidate regression checks still protect the whole client. For an improvement job, `GET /onboard` and MCP `onboard_status` report its recorded scope in `job.unit`: a unit id means that unit, absent or null means client-wide. Ask tests golden questions through each person's own connection and compares returned answers before and after a pack change is served. It never computes or alters figures and treats refusals and abstentions as answers. Neither engineering route grants installation privileges.

### Add a knowledge engineer to an existing install

Roles are in the live pack; identities are in the store's `deploy/<client>/identities.yaml`. If the pack lacks `knowledge_engineer`, add that role with the intended readable units and `processes: [drafting, building, improving, querying]`, then upload the edited pack to its existing location. Do not replace a client's pack with the sample merely to add a role.

For the unchanged Meridian example, from `compose/`, bind the engineer while retaining all sample people and the existing asker and baseline. Set `BASELINE` to the current profile's value (empty if none); for the manual Compose setup above it is `baselines/issue-40.json`. For a customised install, replace the sample `PEOPLE` list with every existing binding plus the new engineer, and retain its current asker.

```sh
BASELINE=baselines/issue-40.json
docker compose stop app
docker compose run --rm job setup-client CLIENT=meridian ASKER=analyst_enterprise BASELINE="$BASELINE" "PEOPLE=analyst_retail:analyst_retail analyst_enterprise:analyst_enterprise finance_reporting:finance_reporting steward_glossary:steward_glossary business_team:business_team knowledge_engineer:knowledge_engineer advisor_wealth:advisor_wealth admin:admin steward:steward pipeline_tagger:pipeline_tagger"
docker compose run --rm job onboard
```

If it reports `onboard: current already`, the pack hash is unchanged: run `docker compose run --rm job reload` before continuing. People are bound when the graph is loaded, so do not rely on a skipped load to bind a new person. Adding a person normally changes the derived catalogue and its compiled hash; the conditional reload also covers an unchanged hash. Wait for its check to pass, then:

```sh
docker compose run --rm job sign-off PRINCIPAL=steward_glossary
docker compose run --rm job release
docker compose run --rm job passwords
```

Wait for each command to succeed. Passwords come from the released artifact's principals: generating them immediately after `setup-client` would still omit the new engineer. Download the new `passwords.txt` and delete that object as in installation step 7, then start `app` and `portal`. This rotates every person's password; distribute each privately. Sign in as `knowledge_engineer`: it can upload, draft, run checks and improve, while acceptance and steward controls remain unavailable.

On Google Cloud, run the same commands through `gcloud run jobs execute kge-meridian --region=$REGION --wait --args=...`, with comma-separated arguments and the current pilot baseline, not the Compose baseline above. After `passwords`, repeat *Passwords and the service*: copy the new hashes into `kge-meridian-passwords`, retrieve and remove the plaintext password object, and deploy a new service revision so it reads the updated secret. The job operator's manual sign-off and release here use the break-glass authority described above; ordinary onboarding uses the page's separated roles.

### Checks without an account

The pilot's configuration plans and applies against stand-in providers, creating nothing, from the zip alone, with Terraform 1.17.0 (`terraform init` fetches `hashicorp/google` 8.8.0, which `terraform/pilot/.terraform.lock.hcl` holds, from the registry or from a mirror that `TF_CLI_CONFIG_FILE` names):

```sh
cd $KIT/terraform && terraform fmt -check -recursive
(cd pilot && terraform init -backend=false && terraform validate && terraform test -var-file=example.tfvars)
```

### Check the discovery portal

Last, check the portal end to end, as on your machine. Open `$PORTAL` (the portal's address, from *Passwords and the service*; on a private page, through the proxy) and sign in as `analyst_retail` with its password from `passwords.txt`. Browse: the products listed are Retail's ten tables, including *Account* and *Branch*, and nothing of SME, Corporate or Wealth. Open *Account*, then ask *Total retail revenue for June 2025* in the portal's ask panel: `kge-meridian` answers it, as the page answers `analyst_retail`. Sign out: the portal returns to its sign-in page. The same check from Cloud Shell:

```sh
curl -fsS -c portal.cookies -H 'content-type: application/json' \
  -d "{\"username\":\"analyst_retail\",\"password\":\"$(awk '$1=="analyst_retail"{print $2}' passwords.txt)\"}" "$PORTAL/session"
curl -fsS -b portal.cookies "$PORTAL/api/products"
curl -s -o /dev/null -w '%{http_code}\n' -b portal.cookies "$PORTAL/api/products/wealth.HSHLD_DIM_1"
curl -fsS -b portal.cookies -X DELETE "$PORTAL/session" && rm portal.cookies
```

The sign-in answers with `analyst_retail`'s identity, `/api/products` lists ten products with `unit_id` equal to `retail`, including `retail.account` and `retail.branch`, another unit's product answers `404`, and the sign-out ends the session.

### Browse, search, Ask and read rows

Use the same walkthrough on Compose and Google Cloud. As `analyst_retail`, search for **Account**, open it, and choose **Data preview**. **Export CSV** downloads the allowed rows, with a default cap of 10,000. In **Run a query**, run the suggested statement or `SELECT account.account_id FROM account LIMIT 5`; the result is capped at 1,000 rows. KGE checks readable units, allowed columns, context-scoped row selectors and the served release's landed-table record before reading. Tables absent from that record are refused. If an older release says to release again, follow *Upgrade and roll back*, including `reload` when onboarding skips an unchanged hash. Badges do not certify a table or grant access.

In **Ask**, use *Total retail revenue for June 2025*: the sample answers **13,287.67** from its certified Retail revenue column. Sign out when finished. For the portal alone, use the `reader` credentials created at the start of this guide: browse and search for Account, then ask *Who owns Account?*. It answers from catalogue metadata, with no data figures. Preview, export and query require KGE.

The portal owns its product and field descriptions, favourites, requests, ratings, feedback and publishing state in `_portal_*` tables: SQLite in its Compose volume, BigQuery in the client's existing warehouse dataset on the pilot. Onboarding derives a product for every table, with optional catalogue enrichment. Refreshes seed missing records and preserve portal edits and activity. KGE's released tables, columns, row counts, terms, lineage, quality and certification are stored separately and overlaid in joined mode. A failed sync keeps the last good catalogue.

Search and Ask select KGE by default when it is configured, otherwise the portal's own tables. `PORTAL_SEARCH_PROVIDER=portal` and `PORTAL_ASK_PROVIDER=portal` select the portal explicitly; `none` is an older alias for `portal`. KGE Search uses certified terms, synonyms, lineage and governed document passages, without a generative model call. Portal Search searches its own catalogue, and portal Ask describes owners and fields but abstains on financial figures. A timeout, network failure or server error may fall back with a visible notice. Authentication or access refusals never trigger fallback. Previously verified units remain usable for at most five minutes during an outage; no warehouse read uses that cache.

### API audiences

OpenAPI and MCP tool metadata label capabilities `external: knowledge` (context, retrieve, term mapping, catalogue and search), `external: query` (Ask, preview, export and query), or `external: identity` (sign-in, agent token, identity and readable units). Internal capabilities are tagged `internal: onboarding`, `internal: governance`, `internal: quality` or `internal: platform`. A tag explains the audience; it does not grant access. Human accept, certify, sign-off and release actions remain HTTP-only. The unauthenticated KGE `/health` and `/healthz` checks support GET and HEAD, report status and image version, and open neither graph nor warehouse. Use `/health` for uptime checks.

## Configure

### The Compose install's `.env`

| Setting | Default | What it is |
|---|---|---|
| `CLIENT` | `meridian` | the client id: a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters; its profile and landing bucket take the same id |
| `KGE_PORT`, `PORTAL_PORT`, `GCS_PORT` | `8000`, `8090`, `4443` | KGE's, the discovery portal's and the emulator's ports on this machine, on 127.0.0.1 alone |
| `LADYBUG_MAX_DB_MB` | `128` | the most megabytes any one graph may take, a positive whole number; not in `.env.example`: add it only for a graph over 128 MB |
| `SESSION_SECRET` | none; set for KGE | the key KGE's sessions are signed with, 32 characters or more; when unset KGE generates a private key in its run state; the standalone portal does not use it |
| `PORTAL_SYNC_TOKEN` | none: required off Cloud Run | the shared secret KGE and the portal use for whole-catalogue sync, 32 characters or more; the portal refuses to start in KGE mode without it; standalone mode does not need a KGE sync secret |

### Tokens for an agent

With `SIGNIN=passwords`, a person obtains an agent's bearer token by sending `POST /session/token` to the KGE service over HTTPS, with JSON `{"username":"<username>","password":"<password>"}` and `Content-Type: application/json`. Use that person's assigned sign-in details. This exchange shares the page sign-in's attempt limits: a refusal is 401, and a locked sign-in is 429 with `Retry-After` seconds.

The response is `{"access_token":"<token>","token_type":"Bearer","expires_in":900}`. Give the agent only `access_token`, and send it on every request to `/mcp`, `/mcp/answer`, `/mcp/onboard`, `/mcp/catalogue`, `/mcp/data`, `/mcp/search` or `/mcp/identity` as `Authorization: Bearer <token>` (or `X-KGE-Authorization: Bearer <token>`). On a private Cloud Run service, also send its Google ID token in `X-Serverless-Authorization: Bearer <Google ID token>`; that does not replace KGE sign-in.

The bearer token works on the REST API as well as MCP, using the same `Authorization: Bearer <token>` header as local tokens. It cannot open a browser session through `POST /session`; use password sign-in for that. The bearer token contains the principal and expiry, never the password. It works on instances serving the same profile with the same session secret for 15 minutes. Expired or changed tokens are refused. Exchange credentials again when it expires; browser sign-out does not revoke this separate token. Keep tokens private and out of logs and committed agent configuration. Browser cookies are not MCP credentials; existing local bearer tokens still work. Agents retain the person's existing role and unit permissions.

### The image's settings

Compose and Terraform set these; they are listed so that you can read a service's or a job's configuration.

| Setting | Set by Compose | Set by Terraform | What it is |
|---|---|---|---|
| `STORE_URL` | `gs://kge-store` | `gs://<project>-kge-store` | the store: the client's profile and people, its pack, the job's work between runs, its releases, its passwords' hashes. `/abs/path` (a local folder) and `gs://bucket[/prefix]` are accepted; `s3://` is refused, not supported yet |
| `LADYBUG_MAX_DB_MB` | the service and the job: `.env`'s, `128` by default | none: `128` by default | the maximum size in MB of every Ladybug database. This keeps Compose usable on Apple Silicon when the image runs under emulation; raise it only for a graph over 128 MB. It must be a positive whole number |
| `LANDING_URL` | the job: `gs://kge-<client>-landing` | the job: `gs://<project>-kge-<client>-landing` | the client's landing, where `seed` lands its certified release; unset, it is `landing/<client>/` in the store |
| `TMP_URL` | none: a folder of the client's in the container's temporary folder | the service and the job: `gs://<project>-kge-tmp` | the scratch area, `tmp/<client>/` in that bucket, where each client's service and job alone may write: a BigQuery load larger than `BIGQUERY_LOAD_INLINE_MAX` is staged there as a file, loaded from it and deleted (the bucket deletes any file left after 7 days). The image never lists the bucket. Unset, the area is a local folder, which no load job can read, so every load is sent inline |
| `BIGQUERY_LOAD_INLINE_MAX` | none | none | the most bytes of newline-delimited JSON a BigQuery load sends inline, in its request; a larger one is staged in the scratch area. Unset, 5242880 (5 MiB), a planning default; anything but a whole number above 0 stops the load, naming the setting |
| `STORAGE_EMULATOR_HOST` | `http://gcs:4443` | none | a Cloud Storage emulator, called with no credentials; unset, the image calls Cloud Storage as its service account |
| `PROFILE` | `CLIENT` | the client | the profile served and built |
| `NAME` | unset | unset | optional test-drive display name; otherwise the title-cased client id, with Meridian’s original sample name retained |
| `SIGNIN` | the page: `passwords` | the service: `passwords` | sign-in by username and password |
| `SESSION_SECRET` | from `.env` | the secret `kge-<client>-session-key` | as above |
| `SIGNIN_PASSWORDS` | none: the page reads `passwords.json` from the store | the secret `kge-<client>-passwords` | the passwords' hashes |
| `TRUSTED_CALLER_EMAIL`, `TRUSTED_CALLER_AUDIENCE` | none | the service: the client's service account, and the page's own address, `https://kge-<client>-<project number>.<region>.run.app` | the caller whose Google ID token (`X-Caller-Token`) the page verifies itself, signature, expiry, issuer, audience and e-mail, before it counts a sign-in against the browser's address that caller names (`X-Client-Address`): the discovery portal's. Set together or not at all; unset, every sign-in counts against its caller's own address |
| `API_URL`, `PORT` | `API_URL=http://127.0.0.1:<KGE_PORT>`; `PORT` unset, so 8000 | Cloud Run sets `PORT` | the address the API names itself by, and the port it listens on |
| `VERTEX_PROJECT`, `VERTEX_REGION`, `MODEL_*`, `*_MODEL_TIER` | none | the project, `model_location` (`global`), and the pilot's `pilot_env` (Gemini on Vertex AI, [The model](#the-model)) | the model's default, optional tiers and per-agent selection ([The model](#the-model)); with no `MODEL_PROVIDER` or configured tier the image answers on rules, as Compose's does: for local development and tests only |
| `MODEL_MAX_TOKENS`, `MODEL_HIGH_MAX_TOKENS`, `MODEL_MEDIUM_MAX_TOKENS`, `MODEL_LOW_MAX_TOKENS` | none | none | image default: 8192 tokens on every tier, including thinking; a tier's override wins over the global override. The connectivity probe always uses 256 tokens |
| `MODEL_TIMEOUT` | none | none | image default: 180 seconds per attempt, capped at the remaining total budget; one retry on transient errors |
| `MODEL_TOTAL_TIMEOUT` | none | none | image default: 270 seconds for attempt and retry together; a positive number below 300 seconds overrides it |
| `MODEL_REQUEST_TIMEOUT` | none | none | image default: 270 seconds for the whole answering request, including graph and warehouse queries; a positive number below 300 seconds overrides it |
| `EVAL_CONCURRENCY` | none | none | image default: 8 questions at once in the job's margin check, A/B runs and test drive; a positive whole number overrides it, `1` asks one at a time. Results are the same at any value; lower it if Vertex AI or BigQuery quotas are tight |
| `RUN_REGION` | none | `region` | the Cloud Run location used to start the client's job; separate from the model location |
| `BIGQUERY_PROJECT`, `BIGQUERY_DATASET_PREFIX` | none | the project, and `kge` | where the client's BigQuery datasets are: `<prefix>_<client>_warehouses` and `<prefix>_<client>_graph`, and on the pilot `<prefix>_<client>_candidates`, with any `-` in the client's id written `_` |
| `APP_STATE_BACKEND` | `files` | the pilot: `bigquery` (`pilot_env`) | `bigquery` keeps unit manifests, history, status inputs, open requests and onboarding job state in tables in each client's existing `<prefix>_<client>_graph` dataset; it needs no Terraform change |
| `GRAPH_STORE` | none | the pilot: `bigquery` (`pilot_env`) | where the client's graph is: unset or `ladybug`, a file in the image's store; `bigquery`, node and edge tables and a property graph in each client's existing `<prefix>_<client>_graph` dataset, queried with GQL, and no graph file built or released; any other value stops the image, naming the setting. It needs no Terraform change ([BigQuery](#bigquery)) |
| `EMBEDDING_PROVIDER`, `VERTEX_EMBEDDING_MODEL` or `EMBEDDING_MODEL`, `EMBEDDING_DIM` | none | the pilot: `vertex`, `gemini-embedding-001` and `768` (`pilot_env`) | the embedder: unset, the hashing one; `vertex`, Vertex AI's `gemini-embedding-001` unless `VERTEX_EMBEDDING_MODEL` or `EMBEDDING_MODEL` names another, 768 wide unless `EMBEDDING_DIM` says otherwise where the chunk vectors are in BigQuery, and 128 wide on the image's own files ([BigQuery](#bigquery)) |
| `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND` | none | the pilot: `bigquery` (`pilot_env`) | `WAREHOUSE_BACKEND=bigquery` moves the warehouses into those datasets and, unless `RETRIEVAL_BACKEND` says otherwise, the chunk vectors too; `RETRIEVAL_BACKEND=duckdb` keeps searching the image's own files ([BigQuery](#bigquery)). Unset, both stay in the image's own files |
| `PORTAL_BACKEND`, `PORTAL_SQLITE_PATH` | `sqlite`, `/data/portal.db` | `bigquery`, unset | the portal's repository adapter and, locally, its SQLite database; BigQuery uses `_portal_*` tables in the client's existing warehouse dataset |
| `PORTAL_ASK` | `on`; set `off` in `.env` | unset: `on`. The portal module does not pass it: to turn Ask off, set it on the portal service yourself, outside Terraform, and again after each `terraform apply`, which puts the service's settings back | `on` or `off`. `off` hides the Ask tab (the link to KGE) in the portal's header; the Ask button and panel stay, and the portal's Ask routes (`/ask`, `/api/ask`, `/ask-data-product`, `/ask-domain`, `/feedback-add`) still answer. With `on`, the tab shows where `KGE_URL` is set. Any other value than on or off stops the portal as it starts, naming the setting |
| `PORTAL_SEARCH_PROVIDER`, `PORTAL_ASK_PROVIDER` | `kge` with a KGE client; `portal` alone | same | `portal` searches and answers about its own catalogue tables, without data or figures; `none` is a legacy alias for `portal`. An unreachable KGE falls back with a page notice; existing verified sessions keep their units for at most five minutes |
| `PORTAL_DATA_PROVIDER` | `kge` with a KGE client; `none` alone | same | preview, export and query require KGE |
| `PORTAL_KGE_TIMEOUT` | 30 seconds | 30 seconds | portal container environment: timeout for KGE identity, session, catalogue and data calls, including the response body; whole seconds from 1 to 299 |
| `PORTAL_SEARCH_TIMEOUT` | 30 seconds | 30 seconds | portal container environment: timeout for KGE Search, including the response body; whole seconds from 1 to 299 |
| `PORTAL_ASK_TIMEOUT` | 285 seconds | 285 seconds | portal container environment: timeout for KGE Ask, including the response body; whole seconds from 1 to 299. Keep above KGE's model-call total (`MODEL_TOTAL_TIMEOUT`, 270 seconds by default) and below Cloud Run's 300-second limit |
| `PORTAL_CATALOGUE_FILE` | empty; `/catalogue/catalogue.yaml` for standalone | unset | the catalogue file when `KGE_URL` is empty; its host file is selected by `PORTAL_CATALOGUE_FILE_HOST` |
| `PORTAL_IDENTITIES_FILE`, `PORTAL_PASSWORDS_FILE` | empty; `/identity/principals.json` and `/identity/passwords.json` for standalone | unset | administrator-managed people and readable units, and KGE-format salted scrypt hashes; required without KGE |
| `PORTAL_SESSION_SECRET` | from the environment or `.env` for standalone | unset | at least 32 random characters; required without KGE; independent of KGE's session secret |
| `PORTAL_COOKIE_SECURE` | `false` for local HTTP | unset: `true` | Secure cookies for standalone sessions; turn on when serving HTTPS |
| `PORTAL_LOGO` | unset: the portal's built-in logo, in its header and as its tab icon | as Compose | the path in the portal's container of an SVG, PNG, JPG, JPEG or ICO logo image of 256 KB or less (a plain-drawing SVG: no doctype, script, event handler, animation, style sheet, embedded image, character reference or external link; the portal refuses to start on any other, naming the setting), served at `/brand/logo`, `/brand/logo.png` and `/brand/logo.svg` and shown in its header, tab icon and app manifest; on Compose, mount the file into the portal service and set this path, and on Cloud Run add a volume holding the file to the portal's service yourself, outside Terraform (the kit's Terraform mounts no logo), and set this path. It is read once as the portal starts, so a new file takes effect on restart (on Cloud Run, a new revision). Set one of `PORTAL_LOGO` and `KGE_LOGO`; when joined, KGE takes the portal's. The bar is navy in light and dark mode alike, so one version suffices: a light mark on a transparent background |
| `KGE_LOGO` | unset | unset | the page's logo, the same contract as `PORTAL_LOGO`: the path in KGE's container of the image, checked as KGE starts (a bad one stops it, naming the setting) and served at `/brand/logo` from memory, so a new file takes effect on restart, with no rebuild; on Compose, mount the file into the app service, and on Cloud Run add a volume holding it to the service yourself, outside Terraform (the kit's Terraform mounts no logo). Unset with `PORTAL_URL` set, KGE asks the portal for its logo as it starts and keeps it, taking it only when the portal was given one; neither, the page shows the client's name beside its initial. Set one of them; when joined, KGE takes the portal's |
| `PORTAL_URL` | the portal's published address, `http://127.0.0.1:<PORTAL_PORT>`; KGE's start-up call for the portal's logo cannot reach that address from inside its container, so the page shows the client's name | set through `app_env`: the portal's `https` address ([Passwords and the service](#passwords-and-the-service)) | the discovery portal's address as a browser reaches it, `http` or `https`: the page's product switcher links to it as **Discover**, and KGE asks it for its logo as it starts. Unset, the page shows KGE alone |
| `KGE_PUBLIC_URL` | KGE's published address, `http://127.0.0.1:<KGE_PORT>` | unset: `KGE_URL`, which is public on Cloud Run | portal container environment: KGE's address as a browser reaches it, used only for the portal's **Ask** link; unset, the link is `KGE_URL`. Each must be an `http` or `https` address with a host and no query: anything else stops the portal, naming the setting. With `KGE_URL` empty there is no link |
| `KGE_URL` | `http://app:8000` | the client's `kge-<client>` service URI | KGE's server-to-server address for session, identity, readable units and questions |
| `KGE_CALLER_AUDIENCE` | none | the page's own address, as `TRUSTED_CALLER_AUDIENCE` | on Cloud Run, the audience of the Google identity token the portal sends KGE as its caller token, `X-Caller-Token`, with each sign-in beside the browser's address; unset, none is sent |
| `PORTAL_SYNC_TOKEN` | none | Compose only; not set by the pilot | off Cloud Run, the shared secret sent only from the portal to KGE for whole-catalogue sync; the pilot uses the verified Google identity token instead |
| `SEARCH_PASSAGE_MIN_SIMILARITY` | `0.2` | `0.2` | search discovery floor on raw passage similarity, 0–1; zero-score hits always excluded |
| `DATA_EXPORT_ROW_CAP` | `10000` | `10000` | maximum rows in one governed product CSV export; a positive whole number |
| `KGE_AUDIENCE` | none | the client's `kge-<client>` service URI | on Cloud Run, the audience of the Google identity token the portal sends KGE in `X-Serverless-Authorization`; unset, `KGE_URL`'s address. Off Cloud Run no token is sent |

### The model

The pilot's Terraform gives the service and the job its GCP-native configuration by default, the variable `pilot_env`, validated live on Vertex AI and BigQuery; the image's own defaults (DuckDB, the image's graph, rules and the hashing embedder, `MODEL_PROVIDER` `none`) are for local development and tests only, as Compose runs them:

| Setting | The pilot's default |
|---|---|
| `VERTEX_REGION` | `global` (`model_location`) |
| `MODEL_PROVIDER`, `MODEL_NAME` | `vertex-gemini`, `gemini-3.8-flash` |
| `MODEL_HIGH_PROVIDER`, `MODEL_HIGH_NAME` | `vertex-gemini`, `gemini-3.1-pro-preview` |
| `MODEL_MEDIUM_PROVIDER`, `MODEL_MEDIUM_NAME` | `vertex-gemini`, `gemini-3.8-flash` |
| `MODEL_LOW_PROVIDER`, `MODEL_LOW_NAME` | `vertex-gemini`, `gemini-3.5-flash-lite` |
| `TAGGER_PROVIDER`, `TAGGER_MODEL_TIER` | `model`, `low` |
| `EMBEDDING_PROVIDER`, `VERTEX_EMBEDDING_MODEL`, `EMBEDDING_DIM` | `vertex`, `gemini-embedding-001`, `768` |
| `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND`, `APP_STATE_BACKEND`, `GRAPH_STORE` | `bigquery` each ([BigQuery](#bigquery)) |

`app_env` overrides any one of them, such as `app_env = { MODEL_NAME = "<another model>" }`, and `pilot_env = {}` leaves the image on its own defaults. Each model must be available to the project in Vertex AI. With any BigQuery backend on, a model call that fails, or a reply that is not JSON, stops its step on every route, and the page shows the error, instead of falling back to rules, a template or the tagger's rules; with none on, as in Compose, the steps fall back as before.

The image reads `MODEL_PROVIDER` (default `none`: no model is called and the image answers on rules), `MODEL_NAME`, `VERTEX_PROJECT` and `VERTEX_REGION` (Terraform sets these two, from `project_id` and `model_location`, default `global`) and `MODEL_SEED` (default `7`). Both Vertex AI providers call the model as the service's or the job's own service account, which holds `roles/aiplatform.user`, never with a key:

| `MODEL_PROVIDER` | `MODEL_NAME` | On a new project |
|---|---|---|
| `vertex-gemini` | required: a Gemini model as Vertex AI names it, such as `gemini-2.5-flash` or `gemini-2.5-flash-lite` | works with the project's default quota |
| `vertex-claude` | `claude-haiku-5-5` unless set | Claude is a third-party model, enabled in Model Garden, and its quota on a new project is 0: every call fails with 429 (quota exceeded for the Claude base model) until a quota increase is granted, in the console's Quotas page |

`MODEL_HIGH_PROVIDER` and `MODEL_HIGH_NAME`, and the corresponding `MEDIUM` and `LOW` settings, define optional tiers in `app_env`; a tier with no provider falls back to the default provider and name together. Onboarding and enhancement prefer high, answers prefer medium, and context and tagging prefer low when those tiers exist. `ONBOARD_MODEL_TIER`, `ENHANCE_MODEL_TIER`, `ANSWER_MODEL_TIER`, `CONTEXT_MODEL_TIER` and `TAGGER_MODEL_TIER` can instead select `high`, `medium`, `low` or `default`. Add `MODEL_<TIER>_PRICE_IN` and `MODEL_<TIER>_PRICE_OUT` where the A/B report should cost a tier; a run pins its answer model and prices. A tier names its model in `MODEL_<TIER>_NAME` (only `vertex-claude` has a default), and calls its provider with its own `MODEL_<TIER>_API_KEY` and `MODEL_<TIER>_BASE_URL`. It uses `MODEL_API_KEY` and `MODEL_BASE_URL` only when its provider is the default's, so no provider is sent another's key or endpoint; the Vertex AI providers read no key at all. A selected tier with no provider falls back to the default model and logs why, and a price that does not parse is logged and left unknown. `MODEL_MAX_TOKENS` sets the reply budget of every model call, in place of the budget each step asks for (200 to 1,800 tokens), and `MODEL_HIGH_MAX_TOKENS`, `MODEL_MEDIUM_MAX_TOKENS` and `MODEL_LOW_MAX_TOKENS` set a tier's, which wins over it; unset, the image supplies 8192 tokens on every tier. The image's 8192-token default leaves room for thinking before the reply. Its attempt timeout is 180 seconds (`MODEL_TIMEOUT`), with one retry on transient errors. `MODEL_TOTAL_TIMEOUT` defaults to 270 seconds for both attempts together; each attempt is capped at the remaining time, and no retry starts after the deadline. The total must be positive and below Cloud Run’s 300-second request limit. `MODEL_REQUEST_TIMEOUT` defaults to 270 seconds for the whole answering request, including its model calls and graph and warehouse queries, in `/ask` (HTTP or MCP) and the reference agent. Its clock starts at service entry and includes time spent between calls. Each attempt receives the lesser of its own timeout, its call’s remaining total and the request’s remaining time; no call or retry starts with less than 0.05 seconds left. Exhaustion returns HTTP 503 with “The answer took too long. Please try a simpler question.”, or the same detail prefixed by `503: ` on MCP, never a partial answer. The request budget must be positive and below 300 seconds and ships in the image; an override is optional. The test-drive connectivity probe always requests 256 tokens, regardless of the configured budget. A value that is not a whole number above 0 is refused: the service does not start on one, and its log names the setting. `ONBOARD_MODEL_RETRIES` (0 to 5, default 2) is how often onboarding's drafting steps send a refused model reply back to the model before they fall back to rules, or, with a BigQuery backend on, stop. Model budgets and the timeout are defaults in the image, so a kit upgrade can change them. An operator may override them through `app_env`. Existing pilots need a one-time apply of this kit’s Terraform to remove the old budget variables from the service and job; any explicit `app_env` override still wins.

A smaller model can abstain where a larger one corrects itself. When the SQL gate refuses a model's first query and says why, `gemini-2.5-flash` rewrote it and passed in the pilot's trial, where `gemini-2.5-flash-lite` did not, and the turn abstained: no answer, never a wrong one.

### BigQuery

The pilot's Terraform makes each client's three BigQuery datasets: its warehouses, its graph and its candidates, where the improvement job builds and checks a candidate's graph and chunks and which nothing serves. An improvement run drops its candidate's graph generation and chunks table once the candidate's check ends, passed or failed; a table a crashed run leaves expires after 7 days, the candidates dataset's default table expiration (a property graph does not expire: delete one by hand if a crash leaves it). It grants the job `roles/bigquery.dataEditor` on the warehouses and the graph (and on the candidates, since the pilot's job runs the page's improvements), and the service `roles/bigquery.dataEditor` on both: on the warehouses, where the page's onboarding steps build a unit's tables (with `WAREHOUSE_BACKEND = "bigquery"`), and on the graph dataset, where it records a steward's decisions on chunks. The pilot's `pilot_env` switches it on: `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND`, `APP_STATE_BACKEND` and `GRAPH_STORE` are `bigquery`. With `pilot_env = {}` the pilot stays on the image's own files until you switch it: give `app_env = { WAREHOUSE_BACKEND = "bigquery" }`. That moves the warehouses and, unless `RETRIEVAL_BACKEND` says otherwise, the search over the chunk vectors too; add `RETRIEVAL_BACKEND = "duckdb"` to keep searching the image's own files. `app_env` reaches the service and the job alike. With it, the job makes the warehouses in BigQuery itself and keeps none of its own: `seed` builds the estate's as `<unit>_<table>` in `<prefix>_<client>_warehouses`, and onboarding (the page's steps and the job's `onboard`) builds each version of a unit as `<unit>_v<N>_<table>`, its rows checked before they load and counted after, with the dataset's `_kge_catalogue` table recording each warehouse's tables and the inputs its rows were made from; neither the job's work nor a release holds a warehouse file. The job's `onboard` drops the tables of a unit since reset; it also writes the candidate's chunks and their vectors to a table of their own in `<prefix>_<client>_graph`, `chunks_<first 16 characters of the hash>_<8 characters>`, embedding each chunk that may be embedded (never an `embed-prohibited` one) straight into that table: the image's own graph keeps no vector on this path. The candidate's graph names its table in `chunks.bigquery`, which its release carries, so the margin check reads the candidate's chunks, the service only those its release names, and a candidate never touches what is served; each release drops the chunks tables nothing names any more, except the one released before, which a service not yet restarted may still read. Tables made before this kit, `chunks` among them, are not read; delete them by hand. With `EMBEDDING_PROVIDER = "vertex"` the vectors are `gemini-embedding-001`'s unless `EMBEDDING_MODEL` names another, and 768 wide unless `EMBEDDING_DIM` says otherwise; on the image's own files they stay 128 wide, and any other `EMBEDDING_DIM` is refused there. Tables made before this kit carry no label and are never dropped by it; delete them by hand if you want them gone.

`GRAPH_STORE=bigquery`, the pilot's default and in `app_env` elsewhere, keeps the client's graph in BigQuery: the job's `onboard` loads each candidate into node and edge tables of its own in `<prefix>_<client>_graph`, a generation named by its model hash and the load (`g<first 16 characters of the hash>_<8 characters>_Unit`, `..._HAS_TABLE__Unit__Tbl` and so on), with a property graph over them, `..._graph`, so a load, even of the hash being served, never replaces what the service reads, and each released hash stays there to roll back to; tagging and a steward's decisions write to them, and the service queries them. A release then carries `graph.bigquery`, a small file naming those tables, in place of the image's own graph, and `release.json`, what the release named, its graph and its chunks in one file written whole, which the service opens (a release made before this kit, with `graph.bigquery.released` and `chunks.bigquery.released`, is still served, and the next release replaces them): a later `onboard` of the same pack loads a new generation for its check, but the service, even restarted from the job's work, serves the released one until that candidate is signed off and released. Nothing is copied from an existing graph: a client's graph moves to BigQuery when the job's `onboard` runs with the setting and its candidate is signed off and released, and until that release the service, which serves the release `current` names, has no graph it can open and does not start. Each release drops the generations nothing can read any more: those no hash in the job's work names, other than the one just released and the one released before it. A release of the same pack again, at a new generation, counts as a new release: the service moves to it on its next look at the onboarding page's status, or when it restarts, so it reads the tables of one of those two releases as long as it does so before the release after next. This path has been tested against stand-ins for BigQuery only: check the first `onboard` and answers on a real project.

`APP_STATE_BACKEND=bigquery`, the pilot's default and in `app_env` elsewhere, keeps unit manifests, history, status inputs, open requests and onboarding job state in app-created tables in each client's existing `<prefix>_<client>_graph` dataset. Before changing an existing pilot, run `onboard-status` and record every listed unit. The two state backends do not migrate records automatically: reset each listed unit while the files backend is still selected, apply the BigQuery setting, then upload and onboard those units again. A pilot with no listed units can switch directly. Do not switch with a unit still listed, because it would disappear from the page while its files remained in the store. Switching back from BigQuery to files keeps the same order the other way: run `onboard-status` and record every listed unit, reset each one while `APP_STATE_BACKEND = "bigquery"` is still selected, so its manifest and history leave BigQuery with it, then revert the setting and apply, then upload and onboard those units again. A unit reset after the revert leaves its BigQuery records behind, and it would come back without its inputs on a later switch to BigQuery.

The apply rolls out a new revision of the service that reads BigQuery at once, but the datasets stay empty until the job has run `onboard` with the setting, and until then the service's questions find no tables (with `GRAPH_STORE=bigquery`, it finds no graph and does not start until that `onboard`'s candidate is released). So, on a pilot already running, run the job's `onboard` straight after the apply, then sign off and release its candidate as usual ([Set the client up and release](#set-the-client-up-and-release)). To switch back, first, where the app state is in BigQuery and `onboard-status` lists a unit, record and reset each listed unit while BigQuery is still selected, as the paragraph above says. Then remove the setting (on the pilot, override it in `app_env`: `WAREHOUSE_BACKEND = "duckdb"`, `RETRIEVAL_BACKEND = "duckdb"` and `APP_STATE_BACKEND = "files"`), and bring `EMBEDDING_DIM` back to 128, since any other width is refused once the vectors are back in the image's own graph, which is 128 wide: on the pilot set `EMBEDDING_DIM = "128"` in `app_env`, because removing it from `app_env` leaves `pilot_env`'s `768`; elsewhere remove it or set it to `"128"`; and apply; then run the job's `onboard` and sign off and release its candidate, and upload and onboard again each unit you reset: the image's own files are still there, but a graph built while the vectors were in BigQuery holds none, so until that `onboard` the service's answers cite no documents. This path has been tested against stand-ins for BigQuery only, so check the first answers on a real project.

**The pilot's own baseline.** The post-load check holds a candidate to the client's baseline, and a baseline is kept per graph store: one recorded on Ladybug, as the example's are, is not put beside a run on BigQuery, and the check then says so and does not pass (not checked, no gate). So a pilot whose graph is in BigQuery is first set up with no `BASELINE` (`setup-client` without it): the check then holds a candidate to the pack's own gates alone (the no-fall check against a baseline is skipped) and writes its gate. Record the pilot's own baseline once a release exists, since `baseline` runs the A/B run on the released graph and needs the client's `current`, which only `release` makes:

```sh
"${JOB[@]}" --args="baseline"
```

It runs the A/B run on the pilot, records it as `baselines/bigquery-<UTC time>.json` in the store's `clients/<client>/`, and prints the complete next command: `setup-client` with `BASELINE=baselines/<file>`, every existing person in file order and the asker when there is one. Run that command; every later `onboard` and `improve` is then checked beside the pilot's own baseline as well as against the gates. If the identities cannot be read, it says why and falls back to the placeholder command instead.

### Mounts and secrets

- **Mounts.** None on Google Cloud. In Compose, the job alone mounts the kit's `examples/` read only at `/kit/examples`, for `put-example`, and the emulator keeps its buckets in the volume `gcs-data`. Each run of the job starts with an empty `/app/build` (`run --rm`, and a new execution on Cloud Run); what a job command builds goes back to the store when it succeeds.
- **Secrets.** Two per client, in Secret Manager on Google Cloud: `kge-<client>-session-key` (the session key) and `kge-<client>-passwords` (the hashes, `passwords.json`). Terraform creates them with no value; you add each version (bootstrap step 4, and Passwords and the service). In Compose the session key is `.env`'s and the hashes stay in the store. The image holds no secret, token or client content.
- **Credentials.** On Google Cloud the service and the job each run as their own service account; nothing else is needed. In Compose none are used.

### Terraform's variables

`terraform/pilot`, given in `VARS` (bootstrap step 4) or in a tfvars file:

| Variable | Default | What it is |
|---|---|---|
| `project_id` | none | the pilot's project |
| `billing_account` | none | the billing account the project is linked to, for its budget |
| `region` | none | where everything runs and is held, e.g. `europe-west2` |
| `clients` | `["meridian"]` | the client ids deployed, each a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters; each is named once |
| `github_repository` | none | `owner/name`: the repository whose GitHub Actions may push and deploy the image |
| `alert_email` | none | where the budget and uptime alerts go |
| `budget_amount` | `100` | the monthly budget, in whole units of the billing account's currency |
| `bigquery_max_slots` | `50` | the most slots BigQuery's reservation autoscales to, from 0 |
| `model_location` | `global` | the Vertex AI location the model is called in |
| `pilot_env` | the GCP-native configuration ([The model](#the-model)) | the pilot's settings for the service and the job: Gemini on Vertex AI by tier, Vertex AI embeddings, BigQuery for the warehouses, retrieval and app state; `{}` leaves the image on its local defaults |
| `app_env` | `{}` | more of the image's settings for the service and the job, each over `pilot_env`'s of the same name: the model's ([The model](#the-model)) and BigQuery's ([BigQuery](#bigquery)) |
| `public` | `true` | whether anyone may reach each client's page; `false` where the organisation refuses `allUsers` ([A private page](#bootstrap-the-project-once)) |
| `invokers` | `[]` | with `public = false`, who may reach each page: IAM members, such as `domain:example.com`, `group:<email>` or `user:<email>` |
| `image` | `null` | the image the service and the job start with; `null` starts them on Google's sample until your first deployment. Terraform never moves it after |

### Job commands

A job command takes its own settings after it, `NAME=value`: `PRINCIPAL` (sign-off), `HASH` (sign-off and release of a hash other than the pack's), `EXAMPLE` and `FROM` (put-example: the folder, or the store location such as `$STORE/examples`, that holds the example) and `CONFIRM` plus optional `NAME` (test-drive display name). Test-drive and put-baseline reject `CLIENT`, `PROFILE` and `EXAMPLE` command parameters: both use the install’s client and the Meridian sample.

| Command | What it does |
|---|---|
| `test-drive` | with `CONFIRM=test-drive`, on a fresh install only: checks the job's settings, buckets, BigQuery, models, embeddings and versions, then sets up, seeds, locks the branded catalogue, onboards, signs off and releases the Meridian sample branded as the installed client, retained as its editable starting baseline; checks its golden set, its derived catalogue for every table, and governed preview, query and cross-unit refusal through the data service; prints its report and saves it under `runs/<client>/test-drive/` |
| `setup-client` | writes the client's profile and people to the store; running it again replaces them |
| `put-baseline` | copies the Meridian sample as the install’s `PROFILE`, branding its id and optional `NAME` display name, preserving YAML formatting; accepts `FROM` and refuses an existing client pack |
| `put-example` | uploads an example's pack from the kit to the store (`EXAMPLE`, default `meridian`) |
| `seed` | builds the invented estate and lands its certified release in the landing bucket |
| `lock` | freezes the landed catalogue in the client’s `pack.lock` and saves it in the store; the test drive runs this after seeding because branding changes text sizes |
| `onboard` | checks and compiles the pack, loads it as a candidate graph and checks it; where the pack compiles to the current release and its graph is loaded, it says `onboard: current already` and loads and checks nothing |
| `reload` | reloads the current hash from its compiled artifact and landed catalogue, records the tables landed in the release and runs the candidate check; clears the previous check, sign-off and release marker, and publishes nothing |
| `onboard-request` | validates and runs the page's stored `onboard`, `improve`, `sign-off` or `release` request; the job's fixed default |
| `improve` | runs the improvement job, checks its edits against the guards and the A/B margin, and writes a governed proposal |
| `sign-off` | a steward's sign-off on a candidate whose check passed |
| `release` | publishes the candidate, or `HASH`'s, as the release the page serves from its next start |
| `releases` | lists the client's releases, marking the current one |
| `passwords` | a new password for each person, its hash and the password in the store |
| `eval` | the A/B run, both arms on the question sets |
| `baseline` | the A/B run, recorded as the client's baseline on this setup, `baselines/<graph store>-<UTC time>.json`, put in the store beside the pack; it prints the `setup-client` line that points the profile at it |

The page persists the short source, draft, acceptance and certification edits under its own `clients/<client>/` prefix. Those inputs stay in `clients/`; generated warehouses and graph state stay in the job's `work/`, and releases stay in `releases/`. On Google Cloud, Run checks, sign-off and release write a validated request and start the client's fixed `onboard-request` job without overrides. The page reads the job's store marker whenever the page polls. Under Compose there is no Cloud Run API: the page accepts the request and returns HTTP 202 with the exact `docker compose run --rm job onboard-request` command for the operator; the request is validated in the same way. One request may run for a client at a time. A `running` marker older than 185 minutes—beyond the job's three-hour timeout—becomes `stale` and may be superseded, so an unrun command or a terminated job does not block the page indefinitely. When a Cloud Run page polls a completed release, it fetches and switches to the store's current graph in that process; the pilot has at most one instance. After a Compose job performs the release, run `docker compose restart app` so the page reads it.

## Building the images on your own base

The kit's images are built on public bases from public package indexes: KGE's on Python's Debian slim image, the portal's on Red Hat's signed Universal Base Image 9 Node.js 22 minimal image, which runs on RHEL 9. A team that must build on its own base, from its own index and behind its own certificate authority, builds both images from their source folders, the KGE source folder and the portal source folder, with these build arguments. Tag them `kge:0.9.19` and `kge-portal:0.9.19` and the steps above use them in place of the kit's tarballs: skip those two `docker load` commands.

| Image | Build argument | Default | What it sets |
|---|---|---|---|
| KGE | `BASE_IMAGE` | `python:3.12.15-slim-bookworm`, by digest | the base: any image with Python 3.12 as `python3`, and `sh` |
| KGE | `UV_IMAGE` | `ghcr.io/astral-sh/uv:0.12.24`, by digest | where the pinned uv comes from; used during the build only, never copied into the image |
| KGE | `UV_BIN` | empty | the path of a uv the base already has, used in place of `UV_IMAGE` |
| KGE | `UV_DEFAULT_INDEX` | empty: PyPI | the Python package index |
| KGE | `UV_SYSTEM_CERTS` | empty | `true`: uv trusts the base's certificate store |
| KGE | `CA_BUNDLE` | empty | the path, inside the base, of a certificate-authority bundle, trusted during the install steps only |
| Portal | `BASE_IMAGE` | Red Hat's signed `registry.access.redhat.com/ubi9/nodejs-22-minimal:9.8`, by digest | the base: any image with Node 22.12 or later, `npm` and `sh` |
| Portal | `npm_config_registry` | empty: the public npm registry | the npm registry |
| Portal | `CA_BUNDLE` | empty | as KGE's, for the `npm ci` steps only |

A credential for the index or the registry is never a build argument, nor part of its URL, both of which the image's history keeps: it is a BuildKit secret, a netrc file for KGE (`--secret id=netrc,src=<file>`) and an `.npmrc` file for the portal (`--secret id=npmrc,src=<file>`), present during the install steps alone.

```sh
docker build --platform linux/amd64 -t kge:0.9.19 \
  --build-arg BASE_IMAGE=<your Python 3.12 base> \
  --build-arg UV_DEFAULT_INDEX=https://<your index>/simple \
  --build-arg UV_SYSTEM_CERTS=true \
  --secret id=netrc,src="$HOME/.netrc" \
  <the KGE source folder>
docker build --platform linux/amd64 -t kge-portal:0.9.19 \
  --build-arg BASE_IMAGE=<your Node 22.12 base> \
  --build-arg npm_config_registry=https://<your registry>/ \
  --secret id=npmrc,src="$HOME/.npmrc" \
  <the portal source folder>
```

- **What KGE's base needs.** Besides Python 3.12 and `sh`, the pinned packages load `libz.so.1`, `libssl.so.3`, `libcrypto.so.3`, `libstdc++.so.6` and `libgcc_s.so.1`. The build's last step opens every shared library in the image's environment and fails naming a missing one and the package that needs it. The remedy is a base from your platform team that has it, else copying the library in from an image of the same operating-system release.
- **Users.** KGE runs as uid 10001 and the portal as uid 1000, both in group 0, so a base needs no named user and no `useradd`.
- **A Windows checkout builds.** The shell and Docker files keep LF line endings in any checkout, and the build rewrites KGE's entry script to LF in any case.

## Pushing the images to your registry and signing them

A registry whose policy admits only signed images refuses the kit's images as they come: Harbor's *Prevent unsigned images* on a project, for example, or an admission policy that verifies a signature before a container starts. The signature such a policy checks is yours, made in your registry with your own key, not one the kit carries. The portal's base, Red Hat's UBI 9 Node.js 22 minimal image, is signed by Red Hat, and a policy on bases can check that signature; the two images built on the bases are signed by you. Load, tag and push both images, then sign each by the digest its push reports, never by a tag, which can be moved to another image:

```sh
REGISTRY=<your registry>/<your project>
docker load -i images/kge-0.9.19.tar
docker load -i images/kge-portal-0.9.19.tar
docker tag kge:0.9.19 $REGISTRY/kge:0.9.19 && docker push $REGISTRY/kge:0.9.19
docker tag kge-portal:0.9.19 $REGISTRY/kge-portal:0.9.19 && docker push $REGISTRY/kge-portal:0.9.19
cosign sign --key <your key or KMS URI> $REGISTRY/kge@sha256:<the digest kge's push reported>
cosign sign --key <your key or KMS URI> $REGISTRY/kge-portal@sha256:<the digest kge-portal's push reported>
cosign verify --key <your public key or KMS URI> $REGISTRY/kge-portal@sha256:<that digest>
```

- **The digest.** `docker push` prints it on its last line, `digest: sha256:<64 hex characters>`. Deploy by that digest too, so what runs is what was signed.
- **The key.** A key file (`cosign generate-key-pair`) or a key-management URI (`gcpkms://`, `awskms://`, `azurekms://`, `hashivault://`), as your platform team's policy names it. The policy is given the public key.
- **A registry with no route to the internet.** cosign records each signature in Sigstore's public transparency log by default. Where the registry cannot reach it, or the image names must not be published, add `--tlog-upload=false` to `cosign sign`, and `--insecure-ignore-tlog` to `cosign verify`.
- **On Google Cloud.** The same, after [Push the images](#push-the-images), against the project's repository, where a policy of yours asks for a signature.

## Upgrade and roll back

After upgrading, every client needs a new release (or a re-release rebuilt by the upgraded job) to carry the landed-tables record. Run `onboard`, sign-off and release, then restart the service as usual. If `onboard` reports `onboard: current already`, the unchanged compiled hash has skipped loading: run `reload` with the upgraded job, then sign off and release that same hash as shown below. Changing the image or restoring an old release alone does not add this data. Until then, catalogue sync and its enrichment badges still work, and data reads refuse with “this release predates the landed-tables record; release again”. The estate's certify step lands each warehouse's catalogue; the platform does not grade tables. The job records tables from that landed catalogue; the service reads it from the release bundle and needs no access to landing.

When the hash is unchanged, record the current hash from `releases` as `HASH` and the client's steward as `STEWARD`. On Google Cloud, with `CLIENT` and `REGION` set for that client:

```sh
gcloud run jobs execute kge-$CLIENT --region=$REGION --wait --args=reload
gcloud run jobs execute kge-$CLIENT --region=$REGION --wait --args=sign-off,HASH=$HASH,PRINCIPAL=$STEWARD
gcloud run jobs execute kge-$CLIENT --region=$REGION --wait --args=release,HASH=$HASH
```

Wait for each command to succeed before running the next. If the reload's check fails, resolve its findings and rerun `reload`; never skip the check or sign-off. Then restart the service as usual. The previously published bundle remains served until release.

On your machine, from the upgraded kit's `compose/`, stop the app before reloading its current graph:

```sh
docker compose stop app
docker compose run --rm job reload
docker compose run --rm job sign-off HASH="$HASH" PRINCIPAL="$STEWARD"
docker compose run --rm job release HASH="$HASH"
docker compose up -d app
```

Use the same success requirement for each command. `reload` keeps the pack unchanged and records the tables in the current landed catalogue; it removes the old check and sign-off, so the new release needs both again.

### Upgrading within a line

A kit's first two numbers are its line: 0.6.0, 0.6.1 and 0.6.2 are one line. A later kit of the installed kit's line upgrades it as follows; neither way touches a client's pack, people, units or releases.

- **On Google Cloud.** Follow steps 1 to 4 of *Upgrading from 0.5.0* below, with the installed kit wherever they name 0.5.0: check and load the new kit, push its image, initialise its Terraform on the same backend with the same variables, save each client's digest, current release and data, and deploy the new image to its job and service, then review `terraform plan` and apply it if it shows changes. Step 5 is 0.5.0's alone. To roll back, return each client's job and service to its saved digest and, if the apply changed anything, apply the installed kit's Terraform again.
- **On your machine.** Run the first commands of *Other upgrade and rollback operations* below, with `OLD_KIT` the installed kit's folder: they back up the store, copy `.env`, load the new image and start `gcs` from the new kit's `compose/`. Then run `docker compose up -d app` there, and nothing else from that section. To roll back, run `docker compose up -d app` from the installed kit's `compose/`.
- **To a kit on Python 3.12 with the providers at 8.8.0.** KGE's image is rebuilt on Python 3.12 (`python:3.12.15-slim-bookworm`), with its Python dependencies moved together, and the portal's with its npm dependencies updated; a client's pack, people, units and releases are read as before. The pilot's Terraform now requires `hashicorp/google` 8.8.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`, which would move the lock), as the steps above do, then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes. Upgrade the portal too, which the steps above do not: load, tag and push the new kit's portal image and set `PORTAL_IMAGE` and `PORTAL_DIGEST` as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does, then deploy it to each client's `kge-portal-<client>` as that section's step 3 does: its catalogue sync on BigQuery now sends each column's type, so a client whose first product scores 0.0 syncs (an install where `GET /api/products` returned nothing after a release syncs on the new revision's start, or within five minutes). To roll back, as above, and deploy each portal's previous image again: note it first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`.
- **To a kit whose portal is built on Red Hat's UBI base (0.9.19 on).** The portal's image moves from Node's Debian slim base to Red Hat's signed UBI 9 Node.js 22 minimal base and runs as the same uid 1000, group 0, so its `portal-data` volume and its `_portal_*` tables are read as before. Upgrade the portal as well as KGE. On Google Cloud, note each client's portal image first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`, then load, tag and push the new kit's portal image as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does and deploy it to each client's `kge-portal-<client>` as its step 3 does; to roll back, deploy the noted image again. On your machine, load the new kit's portal image beside KGE's (`docker load -i images/kge-portal-0.9.19.tar`) and run `docker compose up -d portal` with `app`; to roll back, run it from the installed kit's `compose/`. Where your registry admits only signed images, sign both images there before deploying them: [Pushing the images to your registry and signing them](#pushing-the-images-to-your-registry-and-signing-them).
- **To a kit whose job may run three hours.** Each client's job now has a three-hour timeout (`10800s`), up from one hour, so `onboard` and `test-drive` on a full pack finish; the page holds a request's `running` marker for 185 minutes before marking it `stale`. On Google Cloud the timeout reaches an installed job only through this kit's Terraform: `terraform plan` shows each `kge-<client>` job's timeout changing, so apply it. Until then the job keeps its one-hour limit and stops before such a run ends.
- **Model settings, before either.** A `TAGGER_PROVIDER` that names a provider other than `MODEL_PROVIDER` now needs its own model in `TAGGER_NAME` and, where that provider takes a key, its own key in `TAGGER_API_KEY` (`TAGGER_BASE_URL` for its endpoint): the tagger refuses to start without its model, naming the setting, and is never sent `MODEL_NAME` or `MODEL_API_KEY`, which are `MODEL_PROVIDER`'s. A `MODEL_<TIER>_PROVIDER` naming no known provider (a mistype) now stops each agent on that tier, naming it, instead of quietly falling back to rules; `none` there is kept as the switch that turns that tier's model off. With the settings the kit ships, which name no model, nothing changes.

### Upgrading from 0.8: the discovery portal

A 0.9 kit upgrades a 0.8 install, and adds the discovery portal to it. It is written for an install on the newest 0.8 kit: an install on an earlier one also takes that kit's changes on the way, so read *Upgrading from 0.8.0 within the 0.8 line* below first (a pilot on 0.8.0 also gains the tmp bucket in step 2's plan), or upgrade to the newest 0.8 kit before this. It does not touch a client's pack, people, units, releases, Terraform state or passwords, and KGE answers as before. On the pilot it adds one Cloud Run service per client, `kge-portal-<client>`, running as the `kge-<client>` service's account, and the image repository gains a second image, `kge-portal`. No account, dataset or grant is added. The portal creates its `_portal_*` tables in the client's existing warehouse dataset when it first starts.

An existing install gets a catalogue by running onboard, sign-off and release: KGE derives every product from the pack, lock, model and deployment identities, and the portal works from onboarding alone. Uploading `catalogue.yaml` beside `pack.yaml` and `pack.lock` is optional; it enriches descriptive fields and is validated against the governed tables, columns and principals. The portal syncs the release without replacing favourites, requests, feedback, roles or publish status.

1. Follow *Upgrading within a line* above with the 0.8 kit as the installed one, through deploying KGE's new image to each client's job and service. Load the portal's image with KGE's, and push it beside KGE's:

   ```sh
   docker load -i "$BUNDLE/images/kge-portal-0.9.19.tar"
   PORTAL_IMAGE=$REGION-docker.pkg.dev/$PROJECT/kge/kge-portal
   docker tag kge-portal:0.9.19 $PORTAL_IMAGE:0.9.19 && docker push $PORTAL_IMAGE:0.9.19
   PORTAL_DIGEST=$(gcloud artifacts docker images describe $PORTAL_IMAGE:0.9.19 --format='value(image_summary.digest)')
   ```

   `PORTAL_IMAGE` is the example's; a custom installation reads it, and its portals' names, from its Terraform state ([Custom resource names and settings](#custom-resource-names-and-settings)).

2. Review `terraform plan "${VARS[@]}"`: for each client it adds `google_cloud_run_v2_service` `kge-portal-<client>` (port 8080, `PORTAL_BACKEND=bigquery`, `KGE_URL` and `KGE_AUDIENCE` the client's `kge-<client>` address) and its `roles/run.invoker` grant, the same members as the page's; on a private page (`public=false`) also `roles/run.invoker` on `kge-<client>` for the client's service account, so the portal may call it. It also changes each client's `kge-<client>` service: two settings, `TRUSTED_CALLER_EMAIL` and `TRUSTED_CALLER_AUDIENCE`, so that the page counts a sign-in through the portal against the browser's address, and a new revision of the service rolls out. It changes nothing else. Apply it.
3. The new service starts with Google's sample image, as a new KGE service does. Deploy the portal's image to each client's portal and check it as *Passwords and the service* above does:

   ```sh
   for CLIENT in $CLIENTS; do
     gcloud run deploy kge-portal-$CLIENT --region=$REGION --image=$PORTAL_IMAGE@$PORTAL_DIGEST
   done
   ```

To roll back, return each client's job and service to its saved digest and apply the 0.8 kit's Terraform again: it deletes each `kge-portal-<client>` service. The `_portal_*` tables stay in the warehouse dataset, and KGE's catalogue, read by unit prefix, never reads them; delete them by hand if they are not wanted.

- **On your machine.** Set `OLD_KIT` to the unzipped 0.8 folder and `BUNDLE` and `KIT` to the new kit's image folder and newly unzipped folder. Back up the live pack, lock, identities, work and current release, then carry the settings and passwords forward, load both application images and start the emulator from the new Compose file:

  ```sh
  export OLD_KIT=<absolute path to the unzipped 0.8 folder>
  export BUNDLE=<absolute path to the 0.9.19 kit folder holding images/>
  export KIT=<absolute path to the newly unzipped 0.9.19 folder>
  cd "$OLD_KIT/compose" && . ./.env
  curl -fsS -o "$KIT/compose/pack.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.yaml?alt=media"
  curl -fsS -o "$KIT/compose/pack.lock.$CLIENT.before-0.9.19" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.lock?alt=media"
  curl -fsS -o "$KIT/compose/identities.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/deploy%2F${CLIENT}%2Fidentities.yaml?alt=media"
  curl -fsS -o "$KIT/compose/work.$CLIENT.before-0.9.19.tar.gz" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/work%2F${CLIENT}.tar.gz?alt=media"
  curl -fsS -o "$KIT/compose/current.$CLIENT.before-0.9.19" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/releases%2F${CLIENT}%2Fcurrent?alt=media"
  cp "$OLD_KIT/compose/.env" "$KIT/compose/.env"
  cp "$OLD_KIT/compose/passwords.txt" "$KIT/compose/passwords.txt"
  docker load -i "$BUNDLE/images/kge-0.9.19.tar"
  docker load -i "$BUNDLE/images/kge-portal-0.9.19.tar"
  cd "$KIT/compose"
  grep -q '^PORTAL_PORT=' .env || printf 'PORTAL_PORT=8090\n' >> .env
  grep -q '^PORTAL_SYNC_TOKEN=' .env || printf 'PORTAL_SYNC_TOKEN=%s\n' "$(head -c 36 /dev/urandom | base64)" >> .env
  chmod 600 .env passwords.txt
  . ./.env
  docker compose up -d gcs app portal
  ```

  A `catalogue.yaml` is optional descriptive enrichment. If one is wanted, put the kit's example or the client's own enrichment beside its live `pack.yaml` and `pack.lock`; then onboard, sign off and release it. Without the file, onboarding alone derives the portal catalogue:

  ```sh
  docker compose run --rm job onboard
  docker compose run --rm job sign-off PRINCIPAL=steward_glossary
  docker compose run --rm job release
  docker compose restart app
  ```

  Compose KGE reads the release when `app` starts, so the restart is required. The portal sees it on its next five-minute catalogue check (`PORTAL_CATALOGUE_SYNC_MS`, in milliseconds); run `docker compose restart portal` to sync now instead of waiting. Its `portal-data` volume keeps portal records and activity. Incompatible old table shapes require an explicit migration.

  To roll back, stop the portal, restore every saved live input and the pre-upgrade release, then start the 0.8 app:

  ```sh
  docker compose stop portal
  curl -fsS -o /dev/null -X POST --data-binary @"pack.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"pack.lock.$CLIENT.before-0.9.19" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.lock"
  curl -fsS -o /dev/null -X POST --data-binary @"identities.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=deploy%2F${CLIENT}%2Fidentities.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"work.$CLIENT.before-0.9.19.tar.gz" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=work%2F${CLIENT}.tar.gz"
  docker compose run --rm job release HASH=$(cat "current.$CLIENT.before-0.9.19")
  cd "$OLD_KIT/compose"
  docker compose up -d app
  ```

### Upgrading from 0.8.0 within the 0.8 line

A kit with the tmp bucket upgrades a 0.8 install. It does not touch a client's pack, people, units, releases, Terraform state or passwords, and answers do not change. On the pilot it adds one bucket, `<project>-kge-tmp`, which every client's service and job share for scratch files, each client's under `tmp/<client>/` alone, every object deleted 7 days after it was made; and one setting, `TMP_URL`, which Terraform gives the service and the job ([The image's settings](#the-images-settings)). The image then stages a BigQuery load larger than `BIGQUERY_LOAD_INLINE_MAX` (5 MiB unless set) as a file there and loads it from the bucket, rather than sending its rows in the request; smaller loads are sent as before.

- **On Google Cloud, the pilot.** Follow *Upgrading within a line* above, with the 0.8 kit as the installed one: check and load the new kit, push its image, initialise its Terraform on the same backend with the same variables (`VARS`), save each client's digest, current release and data, and deploy the new image to each client's job and service.

  Review `terraform plan "${VARS[@]}"`: it shows the tmp bucket created (uniform bucket-level access, public access prevention enforced, no versioning, the 7-day delete rule); the grants on the bucket, for each client's service and job `roles/storage.objectUser` on `tmp/<client>/` and, unconditioned, the predefined `roles/storage.bucketViewer` (`storage.buckets.get` and `storage.buckets.list`, no object permission; a BETA-stage role in Google's list), which a BigQuery load from the bucket needs; and `TMP_URL` added to each service and job. No custom role is created. Apply it: one apply is enough. To roll back, return each client's job and service to its saved digest and apply the 0.8 kit's Terraform: that removes `TMP_URL` and the grants, and deletes the tmp bucket, with whatever scratch files are in it.
- **On your machine.** As *Upgrading within a line* above, with `OLD_KIT` the 0.8 folder; roll back the same way. Compose sets no `TMP_URL` and makes no BigQuery load, so nothing changes.
- **The image's settings.** New: `TMP_URL`, set by Terraform, and `BIGQUERY_LOAD_INLINE_MAX`, optional ([The image's settings](#the-images-settings)). No other setting is new or changed.

### Upgrading from 0.7

A 0.8 kit upgrades a 0.7 install. It does not touch a client's pack, people, releases, Terraform state or passwords. On the pilot it changes what the service and the job run with: the pilot's Terraform now gives them its GCP-native configuration by default, `pilot_env` ([The model](#the-model)): Gemini on Vertex AI by tier, the tagger on the low tier, Vertex AI embeddings 768 wide, and the warehouses, retrieval, app state and graph in BigQuery ([BigQuery](#bigquery)). The image's own defaults, which the 0.7 pilot ran on unless its `app_env` said otherwise, are now for local development and tests only.

- **On Google Cloud, the pilot.** Before anything, check that each model `pilot_env` names is available to the project in Vertex AI, and run `onboard-status` and record every listed unit. If the pilot keeps its app state in files (its `app_env` has no `APP_STATE_BACKEND = "bigquery"`) and a unit is listed, reset each listed unit while 0.7 is still deployed, as [BigQuery](#bigquery) says: records do not move from files to BigQuery. Then follow *Upgrading within a line* above, with the 0.7 kit as the installed one: check and load the new kit, push its image, save each client's digest, current release and data, deploy the new image to each client's job and service, and apply the new kit's Terraform. The apply is needed: review `terraform plan` first, which shows the service and the job taking `pilot_env`'s settings, less any your `app_env` already sets, since `app_env` wins setting by setting. Straight after the apply, run the job's `seed` and then its `onboard` (*The warehouses* below says why), then sign off and release its candidate as usual ([Set the client up and release](#set-the-client-up-and-release)), and upload and onboard again any unit you reset; until that `onboard` the service finds no tables in BigQuery and its answers cite no documents, and, its graph now in BigQuery (`GRAPH_STORE`), the service does not start until that candidate is released. To keep 0.7's configuration instead, give `pilot_env = {}` (and your `app_env` as before): then nothing but the image changes. To roll back, first, if the app state goes back to files with it (your `app_env` has no `APP_STATE_BACKEND = "bigquery"`) and `onboard-status` lists a unit, record and reset each listed unit while 0.8 still runs, its app state still in BigQuery, as [BigQuery](#bigquery) says for switching back; then return each client's job and service to its saved digest and apply the 0.7 kit's Terraform, which has no `pilot_env` (an `EMBEDDING_DIM` of yours in `app_env` must go or be `"128"` once the vectors are back in the image's own graph, as [BigQuery](#bigquery) says; removing it is enough here, since 0.7's Terraform supplies no `EMBEDDING_DIM` of its own), then run the job's `seed` and then its `onboard`, sign off and release its candidate, and upload and onboard again each unit you reset. The `seed` comes first because the work 0.8 saved holds no warehouse file, while 0.7 keeps its warehouses as files and its `onboard` does not build the estate's while the estate's summary is there. A release made with the graph in BigQuery holds no graph the 0.7 image can open, so its service does not start until the candidate is released.
- **The warehouses, wherever `WAREHOUSE_BACKEND` is `bigquery`.** The job now makes the warehouses in BigQuery itself, recorded in the dataset's `_kge_catalogue` table, rather than copying files it built ([BigQuery](#bigquery)). The tables 0.7 copied have no record there, and the first `onboard` drops them. So, straight after the apply and before that `onboard`, run the job's `seed`: it builds the estate's warehouses in BigQuery. Then the `onboard` builds each onboarded unit's live version there from its uploaded files. A version only an older release reads is not built again, so rolling back to a release made before the upgrade needs that unit uploaded and onboarded again. A work or release saved by 0.7 that holds warehouse files is read without them. The apply also lets each client's service write its warehouses dataset (`roles/bigquery.dataEditor`, where it could only read it), since the page's onboarding steps now build a unit's tables there; review it in `terraform plan`.
- **The baseline, wherever `GRAPH_STORE` is `bigquery`.** The client's baseline was recorded on Ladybug, and the post-load check no longer puts a BigQuery run beside it: it stops, not checked, so no candidate is released while the profile names it. Before the first `onboard` on BigQuery, run `setup-client` with every existing person and the asker and no `BASELINE`, so the check holds the candidate to the pack's own gates alone; then `onboard`, `sign-off` and `release`. Once that release exists, run the job's `baseline`, which needs the client's `current`, and the `setup-client` line it prints ([BigQuery](#bigquery), *The pilot's own baseline*).
- **On your machine.** As *Upgrading within a line* above, with `OLD_KIT` the 0.7 folder; roll back the same way. Compose sets no model or BigQuery setting, so the local pilot answers as 0.7's did.
- **The image's settings.** New: `GRAPH_STORE`, read again, now `ladybug` (unset) or `bigquery`, the graph in BigQuery ([The image's settings](#the-images-settings), [BigQuery](#bigquery)); a `GRAPH_STORE` left from before 0.7 (`kuzu`, say) now stops the image, naming it: remove it, or set `ladybug`. A 0.8 install made before this setting moves its graph the same way: run the job's `onboard`, then sign off and release its candidate, straight after deploying the image. New and optional: `MODEL_MAX_TOKENS` and `MODEL_<TIER>_MAX_TOKENS`, a model call's reply budget, and `EMBEDDING_DIM`, the Vertex AI embedder's width ([The model](#the-model), [The image's settings](#the-images-settings)). Changed: with `EMBEDDING_PROVIDER = "vertex"` the default model is now `gemini-embedding-001` (`text-embedding-004` is retired); with any of `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND`, `APP_STATE_BACKEND` or `GRAPH_STORE` set to `bigquery`, a model call that fails, or a reply that is not JSON, now stops its step, an error on the page or a failed job, instead of falling back to rules, and `TAGGER_PROVIDER = "model"` with no model for the tagger, or only the offline `mock`, is refused; the job's BigQuery sync now runs as each candidate is loaded and as a release is made, before the candidate's margin check, rather than after it, and with `WAREHOUSE_BACKEND` set to `bigquery` copies no warehouse, since the job made them there; and a service that may not create the app state's tables in BigQuery still reads them. With none of those set, as in Compose, answers are unchanged.
- **Embeddings, after the upgrade.** Wherever `EMBEDDING_PROVIDER = "vertex"` was set already in `app_env`, and `WAREHOUSE_BACKEND` or `RETRIEVAL_BACKEND` is `bigquery`, the vectors are now 768 wide (`EMBEDDING_DIM`) and live only in BigQuery, in a chunks table of each load's own in `<prefix>_<client>_graph`, `chunks_<first 16 characters of the hash>_<8 characters>` ([BigQuery](#bigquery)). The service asks with the new model at once, and finds none of the vectors made before, so run the job's `onboard` straight after the upgrade, then sign off and release its candidate as usual ([Set the client up and release](#set-the-client-up-and-release)); until then its answers cite no documents. The pilot's step above already runs that `onboard`. With `EMBEDDING_PROVIDER` unset, nothing changes.

### Upgrading from 0.6

A 0.7 kit upgrades a 0.6 install. To go from 0.6 to a later line, use the new kit throughout and keep this order: first the steps *Upgrading from 0.7* above puts before anything (on the pilot, the models checked, `onboard-status` recorded and any listed unit reset while 0.6 still runs), then this section, whose apply also brings in the later line's settings (review its plan as *Upgrading from 0.7* says), then the rest of *Upgrading from 0.7*: the `onboard`, sign-off and release straight after the apply, and its other bullets. It does not touch a client's pack, people, units, releases, Terraform state or passwords, and every route 0.6 had answers as it did. Two routes are new: `GET /healthz` (and `HEAD /healthz`), the health check, which answers `200` with the kit's version and needs no sign-in, and `POST /sql/check`, which checks a query against the SQL gate as the signed-in principal, one whose role may query, and never runs it.

- **On Google Cloud.** Follow *Upgrading within a line* above, with the 0.6 kit as the installed one: check and load the new kit, push its image, save each client's digest, current release and data, deploy the new image to each client's job and service, then apply the new kit's Terraform. The apply is needed this time: the uptime check now requests `/health` instead of `/` (Terraform's `uptime_path`, default `/health`); `/healthz` remains the local container check, and Monitoring uses the new path only once it is applied. Review `terraform plan` first: it shows each page's uptime check moving to the new path. To roll back, as within a line: return each client's job and service to its saved digest and apply the 0.6 kit's Terraform again. From a later line whose app state is in BigQuery, as the pilot's `pilot_env` puts it, first record and reset each unit `onboard-status` lists while that line still runs, as *Upgrading from 0.7* does for its rollback, since 0.6 keeps the app state in files; upload and onboard them again after the rollback's `onboard`.
- **On your machine.** As *Upgrading within a line* above says, with `OLD_KIT` the 0.6 folder; roll back the same way.
- **The image's settings.** `GRAPH_STORE` is gone in 0.7: Ladybug is its only graph store, and a `GRAPH_STORE` left in `.env` or a service's environment is ignored; remove it. From 0.8 it is read again, `ladybug` or `bigquery`, and any other value stops the image (*Upgrading from 0.7*). `KGE_VERSION` is new and is set in the image by the kit, which `/healthz` and the page report: there is nothing to set. No other setting is new or changed.

### Upgrading from 0.5.0

Since 0.5.0, the kit adds page-led unit onboarding. Terraform gives each service `roles/run.invoker` on its own job, a conditioned `roles/storage.objectUser` grant under `clients/<client>/`, and `RUN_REGION`; it changes the job's default argument from `onboard` to `onboard-request`. The service cannot override the job's command or environment. The job keeps the same unconditional, store-wide `roles/storage.objectAdmin` grant as 0.5.0. It does not replace Terraform state, the store, releases or passwords. Check the model settings first, as *Model settings, before either* under *Upgrading within a line* above says.

This kit upgrades the pilot alone; QA and PROD are not part of it. Use only the 0.9.19 kit while upgrading, with the same backend, tfvars and variable values used for the pilot's 0.5.0 apply. Start by naming the pilot's project and the newly unzipped kit; do not leave `KIT` pointing at the 0.5.0 folder:

```sh
export PROJECT=<the pilot's project id> REGION=<the pilot's region>
export BUNDLE=<the 0.9.19 kit folder holding images/ and kge-0.9.19.zip>
export KIT=<the newly unzipped 0.9.19 folder>
IMAGE=$REGION-docker.pkg.dev/$PROJECT/kge/kge
STORE=gs://$PROJECT-kge-store
```

These are the example's names. A custom installation sets `IMAGE` and `STORE`, and uses its own job and service names wherever the steps below say `kge-<client>`, from its Terraform state, as [Custom resource names and settings](#custom-resource-names-and-settings) reads them.

1. Check the kit and load its new image tarballs as in step 1 of *Install on your machine*, including `$BUNDLE/images/kge-0.9.19.tar`. Unzip `$BUNDLE/kge-0.9.19.zip` into the new folder named by `KIT`; do not copy Terraform state or configuration from the old kit. Push the image, without yet updating any job:

   ```sh
   (cd "$BUNDLE" && sha256sum -c SHA256SUMS)
   docker load -i "$BUNDLE/images/kge-0.9.19.tar"
   mkdir -p "$KIT" && unzip -o "$BUNDLE/kge-0.9.19.zip" -d "$KIT"
   gcloud auth configure-docker $REGION-docker.pkg.dev
   docker tag kge:0.9.19 $IMAGE:0.9.19 && docker push $IMAGE:0.9.19
   DIGEST=$(gcloud artifacts docker images describe $IMAGE:0.9.19 --format='value(image_summary.digest)')
   ```

   Here `IMAGE`, `STORE` and the new kit's `KIT` are defined before step 1, and `DIGEST` is the new image's digest.
2. In the new kit, initialise the same backend and set `VARS` to the same tfvars and command-line variables used for 0.5.0:

   ```sh
   cd "$KIT/terraform/pilot"
   terraform init -backend-config="bucket=$PROJECT-tfstate" -backend-config="prefix=kge/pilot"
   VARS=(-var-file=example.tfvars -var="project_id=$PROJECT" -var="billing_account=$BILLING" -var="region=$REGION" -var="github_repository=$REPO" -var="alert_email=$ALERTS")
   ```

   `PROJECT`, `BILLING`, `REGION`, `REPO` and `ALERTS` are the account values used for the pilot's 0.5.0 apply. Optional overrides such as `public`, `invokers` or `app_env` must likewise be copied from that apply; the remaining values come from `example.tfvars`.

3. Read the clients from those variables. For each client, record that job's 0.5.0 image digest and current release, and back up its pack, lock, identities and work locally. Deploy the new image to its job and service **before** Terraform changes the job's default argument. The image and argument therefore move in a safe order:

   ```sh
   CLIENTS=$(terraform console "${VARS[@]}" <<<'join(" ", var.clients)' | tr -d '"')
   for CLIENT in $CLIENTS; do
     gcloud run jobs describe kge-$CLIENT --region=$REGION --format='value(template.template.containers[0].image)' | sed 's|.*@||' > "old-digest.$CLIENT"
     gcloud storage cat "$STORE/releases/$CLIENT/current" > "current.$CLIENT.before-0.9.19"
     gcloud storage cp "$STORE/clients/$CLIENT/pack.yaml" "pack.$CLIENT.before-0.9.19.yaml"
     gcloud storage cp "$STORE/clients/$CLIENT/pack.lock" "pack.lock.$CLIENT.before-0.9.19"
     gcloud storage cp "$STORE/deploy/$CLIENT/identities.yaml" "identities.$CLIENT.before-0.9.19.yaml"
     gcloud storage cp "$STORE/work/$CLIENT.tar.gz" "work.$CLIENT.before-0.9.19.tar.gz"
     gcloud run jobs update kge-$CLIENT --region=$REGION --image=$IMAGE@$DIGEST
     gcloud run deploy kge-$CLIENT --region=$REGION --image=$IMAGE@$DIGEST
   done
   ```

4. Run `terraform plan "${VARS[@]}"`. The plan adds the service's job `roles/run.invoker`, its conditioned store `roles/storage.objectUser`, and `RUN_REGION`, and changes the job arguments to `onboard-request`. The job's store-wide `roles/storage.objectAdmin` grant is unchanged from 0.5.0. Review the plan, then run `terraform apply "${VARS[@]}"`.
5. For each client, first read the pack and both records that `setup-client` would replace:

   ```sh
   gcloud storage cat "$STORE/clients/$CLIENT/pack.yaml"
   gcloud storage cat "$STORE/deploy/$CLIENT/identities.yaml"
   gcloud storage cat "$STORE/deploy/profiles.yaml"
   ```

   A released 0.5.0 Meridian pack has no `business_team` or `knowledge_engineer` role. The example upload replaces the live Meridian pack, including any customisation; for a customised pack, add the new role to that pack and upload it instead. Before anyone onboards from the page, upload the pack and run `setup-client` with the complete people list. Then onboard, sign off, release and generate passwords. This puts the new person in the work and released principals before passwords are made:

   ```sh
   gcloud storage rsync -r "$KIT/examples/meridian" "$STORE"
   JOB=(gcloud run jobs execute kge-$CLIENT --region=$REGION --wait)
   "${JOB[@]}" --args="setup-client,CLIENT=$CLIENT,ASKER=<existing asker>,BASELINE=<existing baseline>,PEOPLE=<all existing principal:role pairs plus the new business-team person if needed>"
   "${JOB[@]}" --args=onboard
   "${JOB[@]}" --args=sign-off,PRINCIPAL=steward_glossary
   "${JOB[@]}" --args=release
   "${JOB[@]}" --args=passwords
   ```

   Preserve every existing `principal:role` pair in `PEOPLE`, and preserve that client's existing `asker` and `baseline` as `ASKER` and `BASELINE`. Running `setup-client` replaces the whole people list and those settings; omitting one loses it. Add a new person only if the uploaded pack has a role whose `writes` includes both `terms` and `mappings` and whose `processes` does not include `certification`. Add the new `passwords.json` as a version of `kge-$CLIENT-passwords`, download `passwords.txt`, and delete both store objects with all versions, exactly as in *Passwords and the service*.

The existing remote state, store objects, released graphs and password secrets otherwise carry over unchanged. To roll back, remove the added unit objects one object at a time, restore every saved lock, restore each pre-upgrade `current` release, and then return the job and service to that client's saved 0.5.0 digest. This uses the same per-object delete shape as the emulator rollback below:

```sh
for CLIENT in $CLIENTS; do
  for OBJECT in $(gcloud storage ls "$STORE/clients/$CLIENT/units/**"); do
    gcloud storage rm "$OBJECT"
  done
  gcloud storage cp "pack.$CLIENT.before-0.9.19.yaml" "$STORE/clients/$CLIENT/pack.yaml"
  gcloud storage cp "pack.lock.$CLIENT.before-0.9.19" "$STORE/clients/$CLIENT/pack.lock"
  gcloud storage cp "identities.$CLIENT.before-0.9.19.yaml" "$STORE/deploy/$CLIENT/identities.yaml"
  gcloud storage cp "work.$CLIENT.before-0.9.19.tar.gz" "$STORE/work/$CLIENT.tar.gz"
  OLD_DIGEST=$(cat "old-digest.$CLIENT")
  JOB=(gcloud run jobs execute kge-$CLIENT --region=$REGION --wait)
  "${JOB[@]}" --args="release,HASH=$(cat "current.$CLIENT.before-0.9.19")"
  gcloud run jobs update kge-$CLIENT --region=$REGION --image=$IMAGE@$OLD_DIGEST
  gcloud run deploy kge-$CLIENT --region=$REGION --image=$IMAGE@$OLD_DIGEST
done
```

Then initialise the 0.5.0 kit's Terraform with the same backend command above, and run its `terraform plan "${VARS[@]}"` and `terraform apply "${VARS[@]}"`. The explicit `release` command above restores the release that was current before the upgrade; reverting the image and Terraform alone would not move that pointer.

### Other upgrade and rollback operations

- **On your machine.** Set `OLD_KIT` to the installed kit folder and `KIT` to the newly unzipped 0.9.19 folder. Both Compose files use project `kge`, so they address the same emulator volume. Back up the pack, lock, identities, work and current release before loading or starting the new image:

  ```sh
  export OLD_KIT=<absolute path to the installed kit folder>
  export BUNDLE=<absolute path to the 0.9.19 kit folder holding images/>
  export KIT=<absolute path to the newly unzipped 0.9.19 folder>
  cd "$OLD_KIT/compose" && . ./.env
  curl -fsS -o "$KIT/compose/pack.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.yaml?alt=media"
  curl -fsS -o "$KIT/compose/pack.lock.$CLIENT.before-0.9.19" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.lock?alt=media"
  curl -fsS -o "$KIT/compose/identities.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/deploy%2F${CLIENT}%2Fidentities.yaml?alt=media"
  curl -fsS -o "$KIT/compose/work.$CLIENT.before-0.9.19.tar.gz" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/work%2F${CLIENT}.tar.gz?alt=media"
  curl -fsS -o "$KIT/compose/current.$CLIENT.before-0.9.19" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/releases%2F${CLIENT}%2Fcurrent?alt=media"
  cp .env "$KIT/compose/.env"
  docker load -i "$BUNDLE/images/kge-0.9.19.tar"
  cd "$KIT/compose" && . ./.env
  docker compose up -d gcs
  ```

  Read the saved identities and preserve every existing person, asker and baseline when running `setup-client`. `put-example` replaces the live pack, including customisations; for a customised pack, add the role to that pack and upload it instead. For Meridian, run `put-example`, then `setup-client` with the complete existing values plus `business_team:business_team` and `knowledge_engineer:knowledge_engineer`, and only then onboard, sign off, release and generate passwords. Start a new app container from the new Compose file—`restart` would retain the 0.5.0 container and image:

  ```sh
  docker compose run --rm job put-example
  docker compose run --rm job setup-client CLIENT=$CLIENT ASKER=<existing asker> BASELINE=<existing baseline> "PEOPLE=<all existing principal:role pairs plus business_team:business_team knowledge_engineer:knowledge_engineer>"
  docker compose run --rm job onboard
  docker compose run --rm job sign-off PRINCIPAL=steward_glossary
  docker compose run --rm job release
  docker compose run --rm job passwords
  docker compose up -d app
  ```

  To roll back, still in the new kit's `compose/`, delete every added unit object individually, restore the saved pack, lock, identities and work, and restore the pre-upgrade release. Restoring work removes the unit's derived warehouse and golden set. The emulator has no recursive delete:

  ```sh
  curl -fsS "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o?prefix=clients%2F${CLIENT}%2Funits%2F" | python3 -c 'import json,sys,urllib.parse; [print(urllib.parse.quote(x["name"], safe="")) for x in json.load(sys.stdin).get("items", [])]' | while IFS= read -r OBJECT; do
    curl -fsS -o /dev/null -X DELETE "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/$OBJECT"
  done
  curl -fsS -o /dev/null -X POST --data-binary @"pack.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"pack.lock.$CLIENT.before-0.9.19" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.lock"
  curl -fsS -o /dev/null -X POST --data-binary @"identities.$CLIENT.before-0.9.19.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=deploy%2F${CLIENT}%2Fidentities.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"work.$CLIENT.before-0.9.19.tar.gz" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=work%2F${CLIENT}.tar.gz"
  docker compose run --rm job release HASH=$(cat "current.$CLIENT.before-0.9.19")
  cd "$OLD_KIT/compose"
  docker compose up -d app
  ```

  This restores the pre-upgrade `current` release as well as the installed app image. Repeat the backup, upgrade and rollback commands for every client in the Compose store.
- **On Google Cloud.** Follow the section for the line you upgraded from, *Upgrading from 0.6* or *Upgrading from 0.5.0* above, for every client the pilot names. Each client has its own saved old digest; update and deploy both `kge-$CLIENT` runtimes with that digest to roll it back. Terraform never moves the image.
- **A release.** `release HASH=<an earlier hash>` serves that hash again from the page's next start, with no new sign-off; `releases` lists them. A steward's decision made on the page is made on that page's own copy of the graph, never in the store: it is not carried into the next release or a rollback, nor into a new container or instance of the service (an upgrade on your machine; another or a replaced instance on Google Cloud); it is decided again there, or put in the pack.

## Troubleshooting

- **The page does not come up, and its log says "nothing released for meridian: run release".** `docker compose logs app` shows it. The store holds no release for the client yet: run `seed`, `onboard`, `sign-off` and `release` (step 6), then `docker compose up -d app`.
- **A job command stops with "run setup-client".** The store has no profiles registry yet, or none for the client: run `setup-client` (step 4). A job command that exits 3 names the store's object and address it could not reach; check that `gcs` is running (`docker compose ps`).
- **`docker compose up -d gcs` tries to pull `fsouza/fake-gcs-server` and fails offline.** Some Docker engines drop an image's digest on `docker load`, and Compose then looks for it by digest in the registry. `docker images --digests fsouza/fake-gcs-server` shows `<none>` for its digest then. Where Docker Hub is reachable, `docker pull fsouza/fake-gcs-server:1.56.1@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef` fetches exactly that image; on a machine with no access, use one whose engine keeps it (Docker Engine 25 or later with the containerd image store).
- **A build stops with "failed to compute cache key: short read".** The registry served an empty layer of the base: use another base, or report it to the registry's owner.
- **A port is taken.** Set `KGE_PORT` or `GCS_PORT` in `.env`, run `. ./.env` again, and `docker compose up -d` again; the page is then at that port.
- **Sign-in is refused.** A password works for the person `passwords.txt` names it for. `passwords` again makes new ones for everyone and puts them in the store again: on your machine, download and delete `passwords.txt` as in step 7, then `docker compose restart app`, which reads the new hashes; on Google Cloud, run Passwords and the service again (the secret, the download, the delete with every version, a new revision).
- **Sign-in answers 429, "too many sign-in attempts".** Failed sign-ins are counted per address and principal pair. Five failures in 15 minutes throttle further failures for five minutes; `Retry-After` gives the wait. Wait for the lockout to expire even with a correct password; a successful sign-in then clears that pair’s failures. Another principal behind the same address is unaffected.
- **A second release does not show.** The page reads the release when it starts: `docker compose restart app`, or a new revision on Google Cloud, which restarts every instance.
