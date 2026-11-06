# Installing the KGE pilot, kit @VERSION@

This guide installs the KGE pilot from its release kit alone: the zip, the image tarballs and this guide. Nothing else is needed, and nothing here is fetched from anywhere but the places named. The pilot serves a governed knowledge graph to a page and an API: people sign in with a username and a password, ask questions, and get figures that cite the certified sources they rest on.

It installs two ways, which hold the same parts:

| Mode | Services | Data features |
|---|---|---|
| Context service alone | `docker compose up -d app gcs` | APIs, MCP and guides; standalone page only with `CONTEXT_PAGE=on` |
| Portal alone | `KGE_URL= PORTAL_CATALOGUE_FILE=/catalogue/catalogue.yaml docker compose up -d portal` | catalogue browse, search, catalogue Ask and portal-owned activity; preview, export and query are off |
| Together | `docker compose up -d` | KGE and portal features; table queries require `PORTAL_TABLE_QUERY=on`, previews require `PORTAL_TABLE_PREVIEW=on` |

The portal hosts the client’s ALTA home. Set `PORTAL_CLIENT_NAME` to the client’s display name alone, for example `Meridian` (not `Meridian ALTA`), to show “Meridian ALTA” and “Everyone at Meridian”. Unset or blank uses `Client`; surrounding whitespace is removed. The same value names KGE’s role guides, standalone Context page and developer API reference. This setting does not change the client’s profile id. `PORTAL_PRODUCT_NAME` (default `ALTA`, also supplying KGE’s `PRODUCT_NAME`) and `PORTAL_PRODUCT_LONG_NAME` (default `ALTA: AI-powered Library for Trusted Assets`) set the product’s short name and long form when starting the containers. `PORTAL_PRODUCT_SUBTITLE` sets the Discover home subtitle; trimmed, unset or blank uses “Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.” With the default settings, sign-in opens the ALTA home and its three apps: Certify is a stub that links out to the Power App, Context is the workspace inside the portal, and Discover offers browsing, search, questions and access requests. The common bar switches apps. Context needs the KGE service; Discover can run with its own catalogue and identity as below.

The header hides the product name beside a logo by default. To show it, set `PORTAL_LOGO_TITLE=on` in the Compose `.env` and recreate the portal, or set `portal_logo_title = "on"` in the pilot tfvars and apply the Terraform change. The setting takes `on` or `off`; an invalid value stops the portal and names it. When no logo is available the name remains visible, and the home link retains its accessible name in either setting.

Open the portal and choose **Context** in the app switcher, or open `/context` at the portal’s address and sign in with your email and password. Context’s Docs opens its end-user playbooks and connection help. The portal’s `/api/context/` backend serves onboarding, job status, review, quality, questions, maps and versions. The SQL check remains available through the API. KGE makes every permission decision as that person; the portal never supplies a principal in a request body. Apps and agents still use KGE’s own API and MCP with their own tokens.

The standalone Context page is off by default: `CONTEXT_PAGE=off`. The service still offers its APIs, MCP, `/onboarding-guide` and `/docs`. To restore the fallback on Compose, set `CONTEXT_PAGE=on` in the Compose `.env` and run `docker compose up -d --force-recreate app`; open `http://127.0.0.1:${KGE_PORT}` and use the same credentials. On Google Cloud, add `CONTEXT_PAGE = "on"` to the existing `app_env` and apply the installation’s usual revision change. No image rebuild is needed. Set `off` and recreate or restart to disable it again. An unknown value refuses startup.


`PORTAL_KNOWLEDGE_HOME`, `PORTAL_APP_CERTIFY`, `PORTAL_APP_CONTEXT` and `PORTAL_APP_DISCOVER` default to `on`. Each takes `on` or `off`; a different value stops startup and names the setting. With the ALTA home off, exactly one app must be on or startup refuses and names the flags to change. Sign-in then opens that app, without the ALTA home or app switcher. An app switched off is shown as “not set up here” and its pages and API are unavailable. With no KGE service configured, Context is also treated as off: its page and backend are unavailable and settings return no Context MCP address. `PORTAL_CERTIFY_URL` sets the external Certify address; unset or empty leaves `[Power App link]`.

Compose passes these twelve settings from the shell or its `.env`; recreate the portal container after changing them, and the app container when changing the display or product name. The pilot's tfvars take `portal_client_name` (default `Client`), `portal_product_name` (default `ALTA`), `portal_product_long_name` (default `ALTA: AI-powered Library for Trusted Assets`), `portal_product_subtitle` (default `Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.`), `portal_logo_title`, `portal_knowledge_home`, `portal_app_certify`, `portal_app_context`, `portal_app_discover`, `portal_certify_url`, `portal_table_preview` and `portal_table_query`; the app flags default to `on`, the logo title and table flags to `off`, and a null or empty URL is not passed. Applying those settings creates a portal revision; changing the display or product name also creates a KGE service revision. Neither installation needs an image rebuild. The portal's Cloud Run timeout is 300 seconds, above Context's 285-second proxy budget.

For portal-alone mode, the default mounted catalogue is `examples/meridian-portal/catalogue.yaml`, a complete export of Meridian’s compiled catalogue; the pack’s `catalogue.yaml` is only enrichment and is not a standalone provider file. `PORTAL_CATALOGUE_FILE_HOST` selects another host file. Before starting, create `identity/principals.json` beside `compose/`, using KGE's compiled principal format:

```json
[{"principal":"analyst_retail.meridian@example.com","role":"Reader","reads":["retail"],"processes":["querying"]}]
```

List explicit readable units, never `all`. `querying` or `certification` marks a person who may sign in. The display role does not grant portal actions: `_portal_users` holds those roles, defaulting to viewer; no name grants a portal role; portal admins assign them explicitly. Portal roles never widen unit access. This file can be written directly without running KGE.

Create the `identity/` directory first, then save the JSON above as `identity/principals.json`. From `compose/`, generate credentials using just the portal image (loaded as described below), then configure the portal:

```sh
chmod 700 ../identity
docker run --rm --user "$(id -u):$(id -g)" --entrypoint node \
  -v "$PWD/../identity:/identity" kge-portal:@VERSION@ src/passwords.js /identity
export PORTAL_IDENTITIES_FILE=/identity/principals.json
export PORTAL_PASSWORDS_FILE=/identity/passwords.json
export PORTAL_SESSION_SECRET="$(cat ../identity/session_secret)"
KGE_URL= PORTAL_CATALOGUE_FILE=/catalogue/catalogue.yaml docker compose up -d portal
```

The generator writes `passwords.json` (salted scrypt hashes, the same format as KGE's), `passwords.txt` (each person's password, handed over privately and then deleted), and `session_secret`, all mode 600; it prints no credentials and refuses to overwrite existing files. Keep the exported settings for later starts, or store them in the private Compose `.env`. `PORTAL_IDENTITY_DIR_HOST` can select a different host directory, mounted read-only at `/identity`. The portal image's user must be able to read the mounted files; on Linux, assign that directory and its files to UID 1000, retaining their private modes.

Use the same sign-in page as in the combined mode. The standalone portal requires its own secret of at least 32 random characters, limits attempts by address and username (10/minute; five failures in 15 minutes lock for five minutes), and uses a 12-hour HttpOnly, SameSite=Lax session. Logout, restart, expiry or changing a password invalidates the session. Run one instance in standalone mode. Principal and password files are reread on each request; removing a person or narrowing their unit list takes effect immediately. Cookies default to Secure; this loopback-only Compose service sets `PORTAL_COOKIE_SECURE=false` for HTTP. Set it to `true` when serving HTTPS. No header establishes identity. With KGE present, sign-in remains KGE's and these standalone credential settings are unused.

| Part | On your machine (Docker Compose) | On Google Cloud (Terraform) |
|---|---|---|
| KGE's API, MCP and optional fallback page | service `app` | a Cloud Run service per client, `kge-<client>` |
| The ALTA portal and its API | service `portal` | a Cloud Run service per client, `kge-portal-<client>` |
| The job, one command per run | service `job` | a Cloud Run job per client, `kge-<client>` |
| The store: packs, people, the job's work, releases, passwords' hashes | bucket `kge-store` in a Cloud Storage emulator | bucket `<project>-kge-store` |
| The client's landing: certified releases of its sources | bucket `kge-<client>-landing` in the emulator | bucket `<project>-kge-<client>-landing` |
| Scratch files: large BigQuery loads, staged and then deleted | a folder in the container's temporary folder (`TMP_URL` unset; nothing loads into BigQuery here) | bucket `<project>-kge-tmp`, shared, each client's under `tmp/<client>/`, every object deleted 7 days after it was made |

The kit carries no client's own content. A client is set up once, after installation, by a job command, and its pack (what the graph is built from) goes up to the store. The kit holds invented example content for a fictional bank, Meridian, to upload as a client's pack and try the whole journey; it is optional.

The kit carries the installation playbook at `guides/operator.md` and its runner, `kge playbook operator`. Read it before running its steps. People working in Context find their own playbooks in Context’s Docs; developer team playbooks are not part of the kit. `docs/ONBOARDING.md` is the installation playbook index.

## What the kit holds

```
kge-@VERSION@/
  kge-@VERSION@.zip                       VERSION; terraform/ (the pilot and its modules only);
                                          compose/ (compose.yaml, .env.example); examples/ (Meridian's invented pack and generated fixture);
                                          skills/ (four knowledge-engineer skills, versioned with this kit);
                                          scripts/ (kge helper and playbook runner); guides/ (installation playbook)
  images/kge-@VERSION@.tar                the image, linux/amd64: the page, the API and every job command
  images/kge-@VERSION@.digest             its image id
  images/kge-@VERSION@.sbom.cdx.json      its software bill of materials (CycloneDX 1.5)
  images/kge-portal-@VERSION@.tar         the ALTA portal image, linux/amd64
  images/kge-portal-@VERSION@.digest      its image id
  images/kge-portal-@VERSION@.sbom.cdx.json its software bill of materials (CycloneDX 1.5)
  images/fake-gcs-server-1.56.1.tar       the Cloud Storage emulator for the Compose install, with its .digest
  docs/INSTALL.md                         this guide
  docs/ONBOARDING.md                      the installation playbook index
  SHA256SUMS                              every file above
```

## Prerequisites

- Docker, with Compose v2 (`docker compose version`), on linux/amd64.
- Bash 4.4 or later (the `kge` helper requires it), Python 3 (standard library only), `unzip`, `curl` and `sha256sum`. The system Bash on macOS may be older; put a current Bash on PATH.
- For Google Cloud, also: the Google Cloud CLI (`gcloud`), signed in as the project's owner (Cloud Shell has it), and Terraform 1.17.0.
- A browser.

When building a kit on an arm64 machine, pass `BUILD_FLAGS=--platform=linux/amd64`; the kit requires a linux/amd64 image.

## Install on your machine with Docker Compose

Every command runs in a POSIX shell (bash or zsh). The test drive brands the Meridian sample as your installed client and leaves a released starting baseline. The manual steps below also show the unchanged Meridian example, including a second release and rollback. Step 4 is the platform team's, once per client; everything after it runs through the product. `docs/ONBOARDING.md` is the installation playbook index; Context’s Docs explains source preparation, business review and acceptance for each person’s roles. The kit ships `examples/leasing/schema.sql`, `examples/leasing/questions.json` and `examples/leasing/heldout.json` for that journey.

1. From the kit's folder, check the files and load the images:

   ```sh
   sha256sum -c SHA256SUMS
   docker load -i images/kge-@VERSION@.tar
   docker load -i images/kge-portal-@VERSION@.tar
   docker load -i images/fake-gcs-server-1.56.1.tar
   ```

   With Docker's containerd image store, `docker image inspect --format '{{.Id}}' kge:@VERSION@` then prints the id that `images/kge-@VERSION@.digest` holds, as does `docker image inspect --format '{{.Id}}' fsouza/fake-gcs-server:1.56.1` for `images/fake-gcs-server-1.56.1.digest`. These files hold image ids. Check the emulator's repository digest separately with `docker images --digests fsouza/fake-gcs-server`: its DIGEST column must match the `sha256:` digest pinned in `compose/compose.yaml` after unzipping in step 2. If it shows `<none>`, follow the emulator troubleshooting entry below before starting Compose.

2. Unzip the kit, and create the settings file from its example, with a new session key and portal sync token (48 random characters each, never printed):

   ```sh
   unzip kge-@VERSION@.zip -d kge && cd kge/compose && cp .env.example .env
   { grep -v '^SESSION_SECRET=' .env || true; } > .env.new && mv .env.new .env
   printf 'SESSION_SECRET=%s\n' "$(head -c 36 /dev/urandom | base64)" >> .env
   { grep -v '^PORTAL_SYNC_TOKEN=' .env || true; } > .env.new && mv .env.new .env
   printf 'PORTAL_SYNC_TOKEN=%s\n' "$(head -c 36 /dev/urandom | base64)" >> .env
   chmod 600 .env
   . ./.env
   export PATH="$PWD/../scripts:$PATH"
   kge add compose "$PWD"
   ```

   `.env` names the client (`CLIENT=meridian` by default; set your own client id, for example `CLIENT=aster-bank`, before reading the file), KGE's port (`KGE_PORT=8000`), the discovery portal's (`PORTAL_PORT=8090`) and the emulator's (`GCS_PORT=4443`); `. ./.env` reads them into this shell for the commands below. Every later command runs in `kge/compose`. Compose defaults to project name `kge`, so two installs on one host collide unless each uses a different `COMPOSE_PROJECT_NAME` and different ports. Before starting either stack, put e.g. `COMPOSE_PROJECT_NAME=kge-meridian-scratch` in that install’s `.env`; use a different value in the other install. Compose and `kge` read it from the same folder. Two installs of the same client also need a separate helper state directory each, because both otherwise use `compose/meridian` and its passwords pointer. Before `kge add compose` in each install, run `export KGE_STATE="$PWD/.kge/state"` from that install’s compose folder, then register it again. Repeat that export in each new shell before any `kge` command; tokens and private password deliveries stay under that install’s `.kge/` directory.

3. Start the Cloud Storage emulator:

   ```sh
   docker compose up -d gcs
   ```

   Choose one setup path after starting the emulator. **Test-drive path (fresh install):** run the two commands below, then skip steps 4–6 and continue at step 7. **Manual Meridian path:** skip the test-drive commands and continue at step 4, completing steps 4–6 once before step 7. The test drive performs the sample setup and release covered by steps 4–6, checks the released graph's golden questions, prints its configuration report and saves the same report in the store. BigQuery, the tmp bucket and an unconfigured model are reported as not applicable here. A Compose pass does not test the pilot's real buckets because the emulator creates a missing bucket on first write.

   ```sh
   kge setup-client CLIENT="$CLIENT" ASKER=analyst_enterprise
   docker compose run --rm job test-drive CONFIRM=test-drive
   ```

   Add `"NAME=Aster Bank"` to override the display name. Omit `NAME` to derive the display name from the client id (`aster-bank` becomes `Aster Bank`; `meridian` keeps `Meridian Union Bank`). The test drive takes the client id from the install, never a command parameter: Compose supplies `PROFILE` from `.env`, matching `setup-client`. It refuses a client that already holds a pack, work, landing data or a release; a profile and identities alone are allowed only with the sample’s people, roles and asker. Existing setup is kept unchanged; different or unreadable identities are refused before any write. Read the report, then continue at step 7 to generate passwords, start the app and smoke-test the page while signed in as an example person. Keep `clients/<client>/` and the released graph in the store: this is your editable starting baseline, a fixture client. Download `clients/<client>/` from the store to start editing and use only the routine loop in [A client from its own fixture](#a-client-from-its-own-fixture), with the invented people, roles, units, tables and figures intact. Edit that pack, then run `onboard`, `sign-off PRINCIPAL=steward_glossary.<client>@example.com` (substitute the installed client id) and `release` again; `onboard` rebuilds changed fixture inputs and refreshes the lock. A second test drive is only for starting over: follow [Before a fresh test drive](#before-a-fresh-test-drive). The manual steps below use Meridian and remain the reference for individual operations.

4. **Manual path only.** Set the client profile. `setup-client` sets `CLIENT`, `ASKER` and `BASELINE`; it does not change people. The asker is the role the job's checks ask as, and the baseline is the A/B baseline the post-load check holds the graph to, a file of the client's pack. The example's baselines were recorded on Ladybug, the local stack's graph store; with `GRAPH_STORE=bigquery` the post-load check will not use one recorded on another store (it stops, not checked, and says so), so the pilot starts without one and records its own automatically at each release ([BigQuery](#bigquery)):

   ```sh
   kge setup-client CLIENT=meridian ASKER=analyst_enterprise BASELINE=baselines/issue-40.json
   ```

5. **Manual path only.** Upload the example content, Meridian's pack, its lock and its baselines, to the store:

   ```sh
   kge put-example
   kge admins access_admin.meridian@example.com BY=operator.meridian@example.com
   for ROLE in analyst_retail analyst_enterprise finance_reporting steward_glossary business_team knowledge_engineer advisor_wealth admin steward pipeline_tagger; do
     kge access "GRANT=$ROLE.meridian@example.com:$ROLE" BY=access_admin.meridian@example.com
   done
   ```

   This manual path grants the sample people against the stored pack before its first release. The test drive does that setup itself. Ordinary grants preserve the reserved Access admin grant.

6. **Manual path only.** Seed, onboard, sign off and release:

   ```sh
   kge seed                                      # about 5 minutes
   kge onboard                                   # about 3 minutes
   docker compose run --rm job sign-off PRINCIPAL=steward_glossary.meridian@example.com
   kge release
   ```

   `seed` builds Meridian's invented estate and lands its certified release in the landing bucket only on success. A failed seed publishes no work or landing and releases its client lease. If the container or job is killed before cleanup, wait until the reported lease expiry (up to 10 minutes after its last renewal) before retrying; do not delete the lease or add retry loops. `onboard` checks the pack, compiles it into an artifact named by its hash, loads it into a graph of its own and runs the post-load check on it; note the hash on its `GATE:` line (`GATE: /app/build/stores/meridian/<hash>/GATE: passed`), H1. If that check fails, its log prints the saved `report.md` path. Run `onboard` again after addressing the cause. A candidate created by the failed run is discarded and rebuilt; one already loaded before that run can have just its margin rerun. The failed run's diagnostic report is saved under `runs/<client>/onboard/`. `sign-off` is the steward's sign-off on the check that passed, and `release` publishes the graph to the store as the one to serve.

7. Set the Access admin and operator login before creating passwords. The canonical scratch admin list is only `access_admin.meridian@example.com`; INSTALL and the operator playbook use that same list. `kge admins` replaces the whole list, so retain every admin when adapting it to your own install. On a test-drive install, substitute its client id for `meridian` in both example addresses. This gives the portal an administrator from installation; the sample's `admin` content role alone does not.

   ```sh
   kge admins access_admin.meridian@example.com BY=operator.meridian@example.com
   kge access GRANT=operator.meridian@example.com:analyst_enterprise NAME=Operator BY=access_admin.meridian@example.com
   ```

   Generate each person's first password with `ROTATE=all`. Later runs omit `ROTATE` to keep unchanged hashes and issue passwords only to added people. The object URLs below name Meridian; after a test drive for another installed client, substitute that client id for `meridian` in both URLs. The job keeps their hashes in the store and the passwords once, beside them; download the passwords, then delete that object from the store (keep `passwords.json`, which the page signs people in with):

   ```sh
   kge passwords ROTATE=all
   ```

   The helper prints the private delivery file path and keeps `~/.config/kge/passwords/compose/meridian-latest` pointing to it (under the state directory when `KGE_STATE` is set). Copy that private file to `passwords.txt` for the smoke checks below, without printing it:

   ```sh
   cp "$(dirname "${KGE_STATE:-$HOME/.config/kge/state}")/passwords/compose/$CLIENT-latest" passwords.txt
   chmod 600 passwords.txt
   ```

   The direct job fallback, if the helper is unavailable, downloads and removes the plaintext object as follows:

   ```sh
   docker compose run --rm job passwords ROTATE=all
   curl -fsS -o passwords.txt "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/secrets%2Fmeridian%2Fpasswords.txt?alt=media"
   curl -fsS -o /dev/null -X DELETE "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/secrets%2Fmeridian%2Fpasswords.txt"
   chmod 600 passwords.txt
   ```

   `passwords.txt` holds one line per new or rotated person, a username and its password. Hand each person theirs; `pipeline_tagger.meridian@example.com`, a machine, has none.

8. Start KGE and the discovery portal:

   ```sh
   docker compose up -d app portal
   ```

   Open `http://127.0.0.1:${PORTAL_PORT}`. Sign in with a KGE username and password, browse the products in that person's readable units, ask a question and sign out. A product's Data preview tab, for a table landed in the current release, shows its first governed rows and offers a capped CSV export; Run a query accepts read-only SQL for that product's unit. All three go through KGE's access rule, SQL gate and row caps. Row selectors follow Ask's context scope: a selector applies to reads within its context's unit and joins, not to every read of the table it names. For example, Meridian's `banks_with_retail` belongs to a Wealth context; it does not filter a Retail analyst's direct read of `retail.customer`. The portal forwards the host-only KGE session cookie server to server; it has no session key of its own. It syncs the released catalogue when it starts and every five minutes (`PORTAL_CATALOGUE_SYNC_MS`, in milliseconds); compatible existing records are preserved; an incompatible table shape requires an explicit migration and is never dropped automatically.

   Open `http://127.0.0.1:${PORTAL_PORT}/context`. Sign in as `analyst_enterprise.meridian@example.com` with its password from `passwords.txt`. The header names the profile, `meridian`, and the artifact it serves, the first 12 characters of H1. Ask *Total retail revenue for June 2025* with the Arm set to `graph`: it answers **13,287.67**. Ask it again on each of the reference agent's two arms, `agent control, graph off (rules)` and `agent treatment, graph on (rules)`: each answers 13,287.67.

9. A second release, without rebuilding anything. Upload the example's second version of the pack, which reads *takings* as revenue, and take it through the same steps:

   ```sh
   kge put-example EXAMPLE=meridian-takings
   kge onboard                                   # note its hash, H2
   docker compose run --rm job sign-off PRINCIPAL=steward_glossary.meridian@example.com
   kge release
   kge restart
   ```

   Reload the page and sign in again if asked. The header names H2, and *Total retail takings for June 2025* on the `graph` arm answers 13,287.67.

10. Roll back. List the releases, release H1 again (a release made before needs no new sign-off), and restart the page:

    ```sh
    kge releases
    kge release HASH=<H1>
    kge restart
    ```

    `releases` lists H1 and H2, marking the current one. After the restart the header names H1, and *Total retail takings for June 2025* abstains on the `graph` arm and on `agent treatment, graph on (rules)`: the pack of H1 knows no such term. (`agent control, graph off (rules)` reads no graph, so the rollback does not change its answer.) Rollback also restores the earlier inputs, keeping live access unchanged, so the next onboard starts from H1. No extra pack upload is needed.

11. Check the discovery portal. Open `http://127.0.0.1:${PORTAL_PORT}` and sign in as `analyst_retail.meridian@example.com` with its password from `passwords.txt`. Choose Discover from the ALTA home. Browse: the products listed are the retail unit's ten tables, one product per table, including *Account*, *Branch* and *Monthly Revenue*, and nothing of SME, Corporate or Wealth. Open *Monthly Revenue*, then ask a question about retail in the portal's ask panel, *Total retail revenue for June 2025*: KGE answers it (**13,287.67**), as the page answers `analyst_retail.meridian@example.com` within that product's scope (asking it on *Account* abstains, naming the scope). Sign out: the portal returns to its sign-in page. The same check from the shell:

    ```sh
    PORTAL=http://127.0.0.1:${PORTAL_PORT}
    curl -fsS -c portal.cookies -H 'content-type: application/json' \
      -d "{\"username\":\"analyst_retail.meridian@example.com\",\"password\":\"$(awk '$1=="analyst_retail.meridian@example.com"{print $2}' passwords.txt)\"}" "$PORTAL/session"
    curl -fsS -b portal.cookies "$PORTAL/api/products" | python3 -c 'import json,sys; products=json.load(sys.stdin); assert len(products) == 10 and {"retail.account", "retail.branch"} <= {p["id"] for p in products} and {p["unit_id"] for p in products} == {"retail"}'
    curl -s -o /dev/null -w '%{http_code}\n' -b portal.cookies "$PORTAL/api/products/wealth.HSHLD_DIM_1"
    curl -fsS -b portal.cookies -X DELETE "$PORTAL/session" && rm portal.cookies
    ```

    The sign-in answers with `analyst_retail.meridian@example.com`'s identity, `/api/products` passes the ten-retail-product check, another unit's product answers `404`, and the sign-out ends the session.

`docker compose down` stops everything and keeps the store; `docker compose down -v` deletes the store too.


### Run the installation playbook

The kit includes `guides/operator.md`, the installation playbook. Read the whole guide before using its runner. Each step identifies its changes and any human decision. It covers connection, operator access setup, password delivery, complete input uploads, teardown preview and release inspection. Cloud job execution is operator power and does not grant a Context role.

```sh
kge playbook operator --dry-run
```

The playbook restarts Compose after password changes only; page releases are followed on the status poll. The preview executes no commands. Run selected steps only after reading their prerequisites and deciding whether they apply to your installation. Context’s Docs contains the page steps for knowledge engineers, business teams and stewards, and the guide for developers connecting apps and agents.

`kge releases` on Compose/GCP inventories the retained store bundles; `/versions` (the page's history and local `kge releases`) reads the history carried in the served bundle. A byte-for-byte rollback restores the earlier history too, so `/versions` can show only H1 while the store still retains H1 and H2. This is intentional: `/versions` is historical evidence, not the authoritative inventory of restorable bundles. Use the Compose/GCP `kge releases` command to inventory those bundles. Restoring still checks that every required part is retained.

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
MODEL_ENV="VERTEX_PROJECT=$PROJECT,VERTEX_REGION=global,RUN_REGION=$REGION,MODEL_PROVIDER=vertex-gemini,MODEL_NAME=gemini-3.8-flash,MODEL_HIGH_PROVIDER=vertex-gemini,MODEL_HIGH_NAME=gemini-3.1-pro-preview,MODEL_MEDIUM_PROVIDER=vertex-gemini,MODEL_MEDIUM_NAME=gemini-3.8-flash,MODEL_LOW_PROVIDER=vertex-gemini,MODEL_LOW_NAME=gemini-3.5-flash-lite,TAGGER_PROVIDER=model,TAGGER_MODEL_TIER=medium,EMBEDDING_PROVIDER=vertex,VERTEX_EMBEDDING_MODEL=gemini-embedding-001,EMBEDDING_DIM=768"
gcloud run jobs update "$JOB_NAME" --region="$REGION" --service-account="$JOB_SA" --update-env-vars="$RESOURCE_ENV,$MODEL_ENV"
gcloud run services update "$SERVICE_NAME" --region="$REGION" --service-account="$SERVICE_SA" --update-env-vars="$RESOURCE_ENV,$MODEL_ENV"
```

When the embedding model’s region differs from the models’ region, append `,VERTEX_EMBEDDING_REGION=<region>` to `MODEL_ENV` before running the two commands above, replacing `<region>` with the embedding location. For an existing pilot, set `VERTEX_EMBEDDING_REGION=<region>` with `--update-env-vars` on both the service and the job. Record `VERTEX_EMBEDDING_REGION = "<region>"` in Terraform’s `app_env` so a later apply retains it; Terraform supplies no default for this setting.

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
docker tag kge:@VERSION@ $IMAGE:@VERSION@ && docker push $IMAGE:@VERSION@
docker tag kge-portal:@VERSION@ $PORTAL_IMAGE:@VERSION@ && docker push $PORTAL_IMAGE:@VERSION@
DIGEST=$(gcloud artifacts docker images describe $IMAGE:@VERSION@ --format='value(image_summary.digest)')
PORTAL_DIGEST=$(gcloud artifacts docker images describe $PORTAL_IMAGE:@VERSION@ --format='value(image_summary.digest)')
gcloud run jobs update kge-meridian --region=$REGION --image=$IMAGE@$DIGEST
```

A custom installation ([Custom resource names and settings](#custom-resource-names-and-settings)) points its own job at the image here, in place of `kge-meridian`: `gcloud run jobs update "$JOB_NAME" --region="$REGION" --image="$IMAGE@$DIGEST"`. Terraform never moves either image after this: deployments do. The portal's image is deployed once the page serves a release, in *Passwords and the service* below.

### Set the client up and release

Register the installed environment with the kit helper after deploying the job image. From the unzipped kit, add `scripts/` to PATH, then run `kge sync` and `kge use gcp/meridian` (substitute your client). Discovery reads the existing resources. Use `kge setup-client CLIENT=meridian ASKER=analyst_enterprise` for a new profile, `kge onboard` for subsequent inputs, and `kge passwords` for the password handover. The initial test-drive and seed commands and raw fallback sequence follow.

After pointing the job at the pushed image, test the fresh installation before creating passwords or deploying the service. Copy the single sample to the shared source prefix, select the job for a client in Terraform’s `clients` list, and execute it with a timeout long enough for the complete run. That job already has its client id in `PROFILE`; do not pass a client id to `test-drive`. Its report is printed in the execution log and saved under `runs/<client>/test-drive/`:

```sh
gcloud storage rsync -r --delete-unmatched-destination-objects "$KIT/examples/meridian" "$STORE/examples/meridian"
gcloud run jobs execute kge-aster-bank --region=$REGION --wait --task-timeout=10800s --args="test-drive,CONFIRM=test-drive,NAME=Aster Bank"
```

Use your installed job’s name in place of `kge-aster-bank`. `NAME` is optional and otherwise derived from the client id, as under Compose. The test drive refuses a client that already holds data, while allowing its setup-only profile and identities only with the sample’s people, roles and asker. Existing setup is kept unchanged; different or unreadable identities are refused before any write. It checks the configured buckets, BigQuery datasets, models, embeddings and versions, then performs the example setup, seed, onboarding (including the schema lock refresh), sign-off, release and golden check. It then checks that every example table is in the released catalogue with its governed metadata, and checks data preview, a small query and cross-unit refusal through the data service. After a pass, the client is a fixture client. Keep the branded pack and fixture in `clients/<client>/` and its release as the client’s starting baseline. Set `CLIENT` to the installed client id and `STORE` to its store bucket URL (for example `gs://<project>-kge-store`), then run `mkdir -p "clients/$CLIENT" && gcloud storage rsync --recursive "$STORE/clients/$CLIENT/" "clients/$CLIENT/"` in Cloud Shell to download the starting pack. Then follow only [A client from its own fixture](#a-client-from-its-own-fixture): upload, `onboard`, sign-off, release and restart. To discard that baseline for a fresh test drive, follow [Before a fresh test drive](#before-a-fresh-test-drive): `teardown CONFIRM=<client> PEOPLE=remove`. Once it passes, the client is set up and released: set its Access admins with `access ADMINS=<admin emails> BY=<operator email>`, then run `passwords` (`gcloud run jobs execute kge-aster-bank --region=$REGION --wait --args=passwords,ROTATE=all`), then follow [Passwords and the service](#passwords-and-the-service) to deploy the service, and smoke-test the page signed in as one of the example's people. The manual sequence below does the same one command at a time, and stays the reference for each step.

The same commands as on your machine, each one execution of the job, with one difference: the first `setup-client` names no `BASELINE`. The example's baselines were recorded on Ladybug, and the pilot's graph is in BigQuery, so the post-load check would refuse to put a run beside them; with no baseline it checks the candidate against the pack's own gates alone (the no-fall check against a baseline skipped) and writes its gate, so the first candidate can be signed off and released. The example content goes up with `gcloud storage rsync`. This manual route then grants the sample people against the stored pack before its first release; `setup-client` alone creates no people:

```sh
JOB=(gcloud run jobs execute kge-meridian --region=$REGION --wait)
"${JOB[@]}" --args="setup-client,CLIENT=meridian,ASKER=analyst_enterprise"
gcloud storage rsync -r $KIT/examples/meridian $STORE
kge admins access_admin.meridian@example.com BY=operator.meridian@example.com
kge access GRANT=operator.meridian@example.com:analyst_enterprise NAME=Operator BY=access_admin.meridian@example.com
for ROLE in analyst_retail analyst_enterprise finance_reporting steward_glossary business_team knowledge_engineer advisor_wealth admin steward pipeline_tagger; do
  kge access "GRANT=$ROLE.meridian@example.com:$ROLE" BY=access_admin.meridian@example.com
done
kge seed
"${JOB[@]}" --args=onboard
"${JOB[@]}" --args=sign-off,PRINCIPAL=steward_glossary.meridian@example.com
"${JOB[@]}" --args=release
"${JOB[@]}" --args=passwords,ROTATE=all
```

A failed seed publishes no work or landing and releases its client lease. If the job is killed before cleanup, wait until the lease expiry shown in the refusal (up to 10 minutes after its last renewal), then retry after fixing the cause.

The release job records the pilot's baseline automatically and updates the profile, keeping its people and asker. Every later release records it again, including rollback, after automatic eval post passes. To repair a failed recording, run `"${JOB[@]}" --args=baseline`; no printed `setup-client` command follows. See [Release, rollback and baseline](#release-rollback-and-baseline).

An execution's output is in Cloud Logging: `gcloud run jobs executions list --job=kge-meridian --region=$REGION` names each, and the console's job page shows its logs (onboard's names the hash).

### A client from its own fixture

Jobs own the lock; people own the pack. No person runs `lock` or edits `pack.lock` on the pilot, and no person runs `seed` for a fixture client. `onboard` checks the inputs, rebuilds a changed fixture, refreshes the schema lock and shows the changes before sign-off. The `lock` command remains for local development. The kit's Meridian sample carries a generated fixture and follows this same loop; the test drive builds that fixture and records its digest. The next unchanged `onboard` reports `current already`.

After provisioning the client and setting up its profile, keep a complete input folder:

```text
clients/<client>/
  pack.yaml
  glossary.yaml
  fixture/
    schema.yaml
    data/<warehouse>/<table>.csv
    docs/<shelf>/<id>.md
    golden.json
    heldout.json
```

`schema.yaml` names the warehouses, tables, columns, types and keys, with parent tables before children. Every column needs a description; BigQuery allows at most 1,024 characters. Each CSV's header names its columns in schema order; empty fields are NULL. Documents require `title` and `kind` in front matter. Optional keys are `unit`, `owner`, `version`, `published_at`, `reviewed_at`, `superseded_by`, `certification` and `planted`. A unit, if supplied, must be a schema warehouse. The sample's uncertified documents carry only `title`, `kind` and `planted`; `planted` is a list of strings holding the sample's test tags and may be kept or removed. Golden v2 uses an object with `version: v2`, `changes_from` and an `items` list; a bare list is v1 and cannot pass the margin gate. An optional `document_order` list in `schema.yaml` names every `docs/<shelf>/<id>.md` path exactly once; the generated sample uses it to preserve its landed release identity. Keep the list in step when adding or removing documents, or omit it to use shelf and filename order. Start with the downloaded sample fixture or supply the client's own files.

In `pack.yaml`, declare the inputs under `manifest`, beside the existing include list:

```yaml
manifest:
  includes: [glossary.yaml]
  inputs:
    estate: {fixture: fixture}
    catalogue: null
    sets: [golden, heldout]
```

Keep only the includes your pack uses. `estate` names exactly one relative fixture folder inside the client's folder, with no `..`, or `{generator: sample}` for the kit's sample alone. A catalogue is optional: use `catalogue: catalogue.yaml` when supplying that enrichment; absent or null means none is read, even if the file is there. `sets` is a non-empty list of distinct file stems. Each declared set is read from the fixture, for example `fixture/golden.json`. Each `tests.sets` entry must be declared: golden v2 uses `golden`, while golden_wealth v1 uses `golden_wealth_v1`. Further sets are `<stem>.json` in the fixture, with that stem in `manifest.inputs.sets`; Meridian declares `golden_wealth_v1`, `sage_v1`, `golden_figures_v1`, `golden_documents_v1` and `heldout_documents_v1` this way. Sets belonging to live page-onboarded units are included implicitly. Roles remain in `access.roles`.

A pack written before these declarations still reads its fixture if `fixture/schema.yaml` exists, its catalogue if present and the estate's sets as before. `onboard` prints `onboard: pack.yaml declares no inputs; add this to its manifest:` followed by the proposed block. Copy that block into the pack's existing `manifest`, check every named file, and upload the complete folder again. Adding the declarations does not change the pack hash. Once declared, other client files and estate sets are listed as `onboard: ignored: <path>` and are not used by the gate. A declared fixture that disappears refuses with `<client>'s pack declares a fixture and none was uploaded: upload it, or run teardown to start over`.

Your own pack takes its own `manifest.version`; choose it for your pack, independently of the sample's version. Changing fixture rows alone needs no version bump and reloads the same model hash, with fresh checks and sign-off.

#### Upload the pack inputs

People are managed separately in the access table. Do not include `people.yaml` in a content upload. `setup-client` sets only the profile's `CLIENT`, `ASKER=<role>` and `BASELINE`; omitted asker and baseline values are kept, and an explicit empty value clears one. A registry without a `profiles` mapping is refused and must be repaired. Access admins grant roles against the served pack, or the stored pack before its first release. People changes do not reload a candidate or move a model hash.

With the kit helper, `kge upload <folder>` copies the complete input folder to a fresh upload prefix, checks for `pack.yaml` and runs `onboard FROM=`. The raw command fallback follows.

On the pilot, use `onboard FROM=`. Run these commands in Cloud Shell; on locked-down Windows, compress the complete input folder into a zip, upload it through Cloud Shell's file upload and unzip it there first. Set `CLIENT`, `PROJECT`, `REGION`, `STORE`, `JOB_NAME` and `SERVICE_NAME` from the installed resources, and `INPUT` to the folder holding `pack.yaml`. `STORE` is the full URL, such as `gs://<project>-kge-store`. Copy into a fresh UTC id each time; never overwrite an upload a job may be reading:

```sh
INPUT="clients/$CLIENT"
UPLOAD_ID="$(date -u +%Y%m%dT%H%M%SZ)"
UPLOAD="$STORE/uploads/$CLIENT/$UPLOAD_ID"
JOB=(gcloud run jobs execute "$JOB_NAME" --project="$PROJECT" --region="$REGION" --wait)
gcloud storage cp -r "$INPUT"/* "$UPLOAD/"
gcloud storage ls "$UPLOAD/pack.yaml"
"${JOB[@]}" --args="onboard,FROM=$UPLOAD/"
```

Wait for the copy to finish and verify that `$UPLOAD/pack.yaml` exists before executing the job. A second upload within the same second needs a different id. `FROM` must be a `gs://` or `file://` prefix under this store's `uploads/<client>/`; without a store it takes an absolute local folder. The job refuses a missing `pack.yaml`, another writing job or an active page onboarding request.

The job compares file contents, not times or sizes, and stages an exact mirror of the people-owned files. Files absent from the upload are removed on success, including stale catalogues and includes. `pack.lock`, `baselines/`, `versions/`, `proposals/`, `units/` and `onboard/` are job- or page-owned and left alone; uploaded copies are ignored. Dotfiles are ignored too, including Mac `._*.md` files already in the live fixture; a local store may hide them from the ignored list. The upload digest is recorded with the run. Successful publication writes fixture files, includes and catalogue before `pack.yaml`; Cloud Storage has no transaction across objects, so this is an ordered publication, not a multi-object atomic write. A failure before publication restores the staged inputs and publishes only its diagnostic record.

For local development, a filesystem `rsync` mirror remains available. With the local API stopped, set `INPUT` and `LOCAL_CLIENT` to different source and destination folders and run:

```sh
rsync -rc --delete --exclude='.*' --exclude='pack.lock' --exclude='baselines/' --exclude='versions/' --exclude='proposals/' --exclude='units/' --exclude='onboard/' "$INPUT/" "$LOCAL_CLIENT/"
```

This direct local copy is outside the job's restore boundary. Use `FROM` when the job must apply and restore the inputs together. The pilot route above leaves the live inputs alone until the job succeeds.

#### Read the up-front check

Onboarding reports ten steps: inputs, sweep, check, fixture, units, certify, lock, compile, load and margin. The up-front check lists every finding as `onboard: refused: <rule> <file>:<entry>: <message>`, then `onboard: refused, <n> finding(s): nothing was written`, with exit 2. Fix all listed entries in the input folder and submit a new upload.

| Rule | What to fix |
|---|---|
| `I00` | malformed `manifest.inputs` |
| `I01` | a declared file is missing |
| `I02` | the declared fixture has no uploaded schema |
| `I03` | a `tests.sets` entry is not declared |
| `F01` | a harvested source has no schema warehouse, or the warehouse schema is malformed |
| `F02` | the catalogue names something the pack does not declare |
| `F03` | a pack reference, table, column or key does not match the fixture |
| `F04` | a column type is refused by the warehouse |
| `F05` | a column description is missing or exceeds the warehouse's limit |
| `F06` | a foreign key names an unknown or later parent, or mismatched columns |
| `F07` | a CSV is missing, unreadable or has the wrong header or field count |
| `F08` | document front matter is invalid, or its unit is not a schema warehouse |
| `F09` | a question set has the wrong shape or version |
| `F10` | a changed set lacks a valid `changes_from` declaration |
| `A01` | an access-table row holds a role the pack does not define |
| `B01` | the role the baseline asked as no longer exists |
| `B02` | the baseline cannot be read, uses another graph store, or has no comparable declared set |

Pack syntax findings may also be listed. Both baseline findings name the remedy: `fix: release records a new baseline on the released graph (or run baseline); or clear it with setup-client BASELINE=`. Clearing a baseline removes that comparison, so review the reason before doing it; the pack's gates still apply. To clear it without changing access:

```sh
"${JOB[@]}" --args="setup-client,CLIENT=$CLIENT,BASELINE="
```

On the page, read **Candidate changes** and the A/B report before sign-off (the page shows all of these; the job's log prints only the candidate hash, ignored files, missing declarations and whether checks are repeated): schema changes, estate rebuild reason, **Reloaded the same model**, ignored files, declarations to add, upload digest, checks repeated. A failed gate is checked again on the next onboard. Only unchanged inputs with a passing gate give `onboard: current already` or `onboard: candidate already loaded and checked=<hash>`.

Set `HASH` to the checked hash and `STEWARD` to the client's steward. The steward normally signs off and releases on the page; the job operator's break-glass equivalent is:

```sh
"${JOB[@]}" --args="sign-off,HASH=$HASH,PRINCIPAL=$STEWARD"
"${JOB[@]}" --args="release,HASH=$HASH"
gcloud run services update "${SERVICE_NAME:-kge-$CLIENT}" --project="$PROJECT" --region="$REGION" --revision-suffix="release-$(date +%s)"
```

A changed CSV row or document in the fixture rebuilds and reloads with the model hash unchanged; it needs a fresh margin check and sign-off. The fixture digest covers sorted relative paths and bytes, excluding dotfiles, and is recorded only on success. The fixture estate loses sample-only sets, glossary, mapping, measure and document files; page-owned unit sets and retained warehouse versions remain. On BigQuery the candidate's rows have their own version in the existing warehouse dataset while the service reads the released rows. On local DuckDB/Ladybug, stop the API before reloading the served hash: a same-hash reload writes in place, so the durable release stays unchanged but local files can change after a failed run. Correct or restore the fixture files and run `onboard` again. Any fixture data replaced by the failed run is rebuilt and checked again, even when the inputs match the release. No manual estate rebuild is needed.

For units onboarded through the page, changing only rows or documents can still give `onboard: current already` when the compiled model hash is unchanged and the existing gate passed. Fixture rebuilds trigger automatic reloads at the same hash; people changes do not; page row and document edits alone do not.

#### Failed jobs and the lease

A failed onboard saves no new work, lock, landing, input or fixture digest. It drops the graph generations, property graphs, chunks tables and warehouse versions it created, while protecting releases. A candidate already loaded before the run keeps its earlier gate when only its margin was rerun. The next attempt after fixing the cause rebuilds as needed. Its record is `runs/<client>/onboard/<UTC>.json`; when margin ran, its report is `runs/<client>/onboard/<UTC>/report.md`. A failed page checks job has no report link; its message names that stored report key for the operator.

At the next onboard's start, the sweep drops and lists objects nothing names, including leftovers from a killed run. It preserves pointers, release records, the release ledger and kept snapshots, loaded candidates, live estate versions, artifact-named warehouse versions and version 0. Cleanup may therefore run before an up-front refusal. It is not a promise to bound storage from every successful historical load.

With `STORE_URL`, writing jobs use `jobs/<client>/lease.json`: one writer per client, renewed every two minutes, expiring ten minutes after its last renewal. A busy refusal names the command, start time and expiry. After a killed job, wait until that expiry before rerunning. A corrupt lease refuses jobs with `the job lease cannot be read: nothing was changed`. Once the operator has confirmed no job is running, delete just that corrupt key, then rerun:

```sh
gcloud storage rm "$STORE/jobs/$CLIENT/lease.json"
```

There is no lease without a store. Terraform sets task retries to zero. Set the same on a custom job:

```sh
gcloud run jobs update "$JOB_NAME" --project="$PROJECT" --region="$REGION" --max-retries=0
```

`onboard`, `release`, `sign-off`, `passwords`, `seed`, `teardown` and `onboard-request` refuse a `CLOUD_RUN_TASK_ATTEMPT` above 0, exit 2: `an automatic retry cannot fix a failed onboard: fix the cause and run it again` (with that command's name). A value that is not a whole number at least 0 is refused too. Fix the cause and start a new execution; do not raise retries.

#### Release, rollback and baseline

A release refuses inputs that moved after the candidate was built, naming the files: onboard the new inputs or upload the inputs that candidate used. Uploading a fresh staged prefix alone does not move the live inputs.

Every new release gets its own id, even at the same model hash. Its tarball is `releases/<client>/<hash>/<release>.tar.gz`; `identity.json` selects it, and `<hash>.tar.gz` remains the latest copy for older readers. The ledger retains two release identities and their snapshots under `clients/<client>/versions/<hash>/<release>/`. Rollback uses the stored tarball byte for byte and restores the pack, lock, includes, declared catalogue, fixture files and digest, unit inputs, estate summary and live versions, and the asker role recorded with that release. It prints restored keys. Access follows the live table; rollback never restores people or passwords. If an older snapshot did not keep the fixture's files, it names the fixture digest to upload again. Missing snapshots, tarballs, graphs, chunks or warehouse versions cause a refusal naming the part.

Use the steward's **Roll back to the previous release** action, or:

```sh
"${JOB[@]}" --args="release,PREVIOUS=yes"
gcloud run services update "${SERVICE_NAME:-kge-$CLIENT}" --project="$PROJECT" --region="$REGION" --revision-suffix="release-$(date +%s)"
```

This also restores the previous release at the same hash. `release HASH=<hash>` can select a retained release; it cannot rebuild a pruned snapshot. A rollback keeps the live access table and passwords unchanged.

After every successful job release, rollback included, the job records a baseline only after automatic eval post passes, on the released graph using rules, with no comparison to the old baseline. It saves `baselines/<graph store>-<UTC time>.json`, updates the profile and prints `release: baseline re-recorded: baselines/<file>, from run <id>; the profile names it, people and asker kept`. No `setup-client` step follows. If recording fails, the release has already succeeded: the command exits nonzero and says `release: <hash> is released; recording its baseline failed: <reason>: run baseline`. Fix the cause and run the `baseline` job; it records and updates the profile in the same way.

A set changed since that baseline must advance its version and declare the old file's version and sha256, every changed or removed item, and why. For example, the following fragment belongs beside `version` and `items` in the new set; substitute the recorded file digest and actual ids:

```yaml
changes_from:
  version: v2
  sha256: <the baseline's 64-character file sha256>
  items: [question_changed]
  removed: [question_removed]
  why: Corrected the expected answer and retired the obsolete question.
```

Write it as JSON in a `.json` set and update the corresponding `tests.sets` version; for this example the new version must be later than v2. Removed ids must no longer be in `items`; the change must leave comparable unchanged questions. The gate compares every margin score on the unchanged questions, including citations, leaving removed questions out. An older baseline without per-item scores compares only pass rate and says `pass rate only: baseline recorded before per-item scores`; full per-item comparisons start with the first release-time re-record.

### Starting a client over

An installed client built by the older sample generator can start over with this teardown, then the test drive, or take its own pack and fixture through [A client from its own fixture](#a-client-from-its-own-fixture). Upgrading the image alone does not add a fixture to that client.

The job operator runs `teardown`; it has no page control or MCP tool. `CONFIRM` must equal the installed `PROFILE` client. Terraform and Compose give the job a landing of that client's own. A custom job's `LANDING_URL` must be its own bucket or prefix too: teardown empties that whole landing store, so it must never name a shared landing root.

From Cloud Shell, set `JOB_NAME`, `CLIENT`, `PROJECT` and `REGION` to the installed values. Preview first, read every listed name, then execute:

```sh
JOB=(gcloud run jobs execute "$JOB_NAME" --project="$PROJECT" --region="$REGION" --wait)
"${JOB[@]}" --args="teardown,CONFIRM=$CLIENT,PREVIEW=yes"
"${JOB[@]}" --args="teardown,CONFIRM=$CLIENT"
```

Under Compose, from `compose/`, use:

```sh
docker compose stop app
docker compose run --rm job teardown CONFIRM="$CLIENT" PREVIEW=yes
docker compose run --rm job teardown CONFIRM="$CLIENT"
```

| Teardown result | What it includes |
|---|---|
| Preview | every store key and counts by prefix, every landing object and count, every BigQuery table, view and property graph by name; nothing removed |
| Removed | `clients/<client>/`, `work/<client>.tar.gz`, `releases/<client>/`, `secrets/<client>/`, `tmp/<client>/` in the store, `uploads/<client>/`, the client's landing objects and objects in its warehouses, graph and candidates datasets |
| Kept | datasets, portal `_portal_*` tables, live access tables, Secret Manager, `runs/<client>/` and backups; deployment setup and profile by default |
| `PEOPLE=remove` | also removes `deploy/<client>/` and the profile entry |
| Backup | store objects first, under `backups/<client>/<UTC>/`, including `passwords.json` but never `passwords.txt`; landing objects and BigQuery tables are not copied |
| Record | `runs/<client>/teardown/<UTC>.json` and `.md`, names and counts, never contents |

The tmp bucket's separate `tmp/<client>/` cannot be listed by the job and remains subject to its seven-day lifecycle. Versioned buckets retain deleted noncurrent versions for 30 days; the record says both. Teardown releases its own lease when finished. A repeated teardown succeeds with `teardown: nothing left for <client>`. A service with no release starts with `nothing released for <client>: release first`; health and the page remain available, while data calls return 503. A running service keeps serving the old release from memory until it restarts, so restart it after teardown; after a teardown that also removed deployment setup, it cannot start until `setup-client` (or the test drive) has written the client's profile again.

#### Before a fresh test drive

To discard the test drive's starting baseline and begin again, preview and run teardown with deployment setup removal. The live access table is kept:

```sh
"${JOB[@]}" --args="teardown,CONFIRM=$CLIENT,PEOPLE=remove,PREVIEW=yes"
"${JOB[@]}" --args="teardown,CONFIRM=$CLIENT,PEOPLE=remove"
"${JOB[@]}" --args="test-drive,CONFIRM=test-drive"
```

Under Compose the corresponding command is `docker compose run --rm job teardown CONFIRM="$CLIENT" PEOPLE=remove`, preceded by the same command with `PREVIEW=yes`; then rerun `test-drive CONFIRM=test-drive`. The test-drive report points to this procedure. Keep the deployment setup instead when moving to a new fixture with compatible roles, then upload and onboard it as above. Live access, portal favourites and feedback survive either choice.

### Passwords and the service

`passwords` reads the live access table, including Access admins, independently of release and rollback. By default it keeps hashes for unchanged people, generates passwords for added people and drops removed people. The plaintext file contains only newly issued passwords. `ROTATE=all` replaces everyone's password; use it on a first run with no earlier hashes, or for an intended rotation.

On Google Cloud, use the kit helper with the installed environment selected:

```sh
kge sync
kge use gcp/meridian
kge passwords
```

For the first generation without a current secret version use `kge passwords ROTATE=all`. The helper copies the latest hashes into the store as the merge base, runs the job, adds the merged hashes to the **existing** password secret with the operator's own login, downloads the plaintext file with mode 0600 and deletes both store objects, including their old versions. It prints the file path, never the passwords. No new secret, job grant, service account or Terraform change is needed. The job itself does not write Secret Manager: it stages the two files in the store, which the helper collects and removes.

The helper also writes the secret's full resource name to `deploy/<client>/passwords-secret` in the store. This pointer is plain text, not a credential. The service uses its existing Secret Manager accessor to read the latest version and caches successful reads for about a minute. Without that pointer it falls back to `SIGNIN_PASSWORDS` at startup. `kge passwords REVISION=yes` forces a new service revision when needed; the current service needs none for a password change. Compose keeps the merged hash file in its store and restarts `app` to read it.

The following raw commands are the fallback when the helper is unavailable. On a first install, deploy KGE and the portal after the secret is populated; subsequent password updates need only the pointer and its refresh interval.

On Google Cloud, before each later raw `passwords` run, copy the secret version the service currently uses back into the store as the merge base. Set `PASSWORDS_SECRET` to that secret's id and `PASSWORDS_VERSION` to the active version (`latest` only when that is what the service uses). With `CLIENT`, `STORE` and `JOB` set for this installation:

```sh
gcloud secrets versions access "$PASSWORDS_VERSION" --secret="$PASSWORDS_SECRET" --out-file=passwords-current.json
chmod 600 passwords-current.json
gcloud storage cp passwords-current.json "$STORE/secrets/$CLIENT/passwords.json"
rm passwords-current.json
"${JOB[@]}" --args=passwords
```

If the merge base is missing, the job refuses with `passwords: no earlier hashes in <path>: copy the secret's current version there (the guide), or run passwords ROTATE=all`. Compose keeps `secrets/<client>/passwords.json` in its store, so later runs merge against it directly. After a pilot run, follow the steps below with the installed client and secret names. Distribute only the new or rotated passwords, keeping unchanged people's existing passwords private.

Copy the hashes into a new version of the passwords secret, download the passwords, then delete both objects from the store with every version of them (the store keeps a deleted object's versions for 30 days); then write the live-reload pointer. The deploy commands below are for installation or an image upgrade:

```sh
gcloud storage cat $STORE/secrets/meridian/passwords.json | gcloud secrets versions add kge-meridian-passwords --data-file=-
gcloud storage cp $STORE/secrets/meridian/passwords.txt passwords.txt && chmod 600 passwords.txt
gcloud storage rm --all-versions $STORE/secrets/meridian/passwords.json $STORE/secrets/meridian/passwords.txt
printf '%s\n' "projects/$PROJECT/secrets/kge-meridian-passwords" > passwords-secret
gcloud storage cp passwords-secret "$STORE/deploy/meridian/passwords-secret"
rm passwords-secret
gcloud run deploy kge-meridian --region=$REGION --image=$IMAGE@$DIGEST
gcloud run deploy kge-portal-meridian --region=$REGION --image=$PORTAL_IMAGE@$PORTAL_DIGEST
```

Check the portal from Cloud Shell. Its health check needs no sign-in, and a sign-in through it is KGE's own: the portal forwards it to `kge-meridian`, server to server, and hands back KGE's session cookie under its own address. The portal holds no session key:

```sh
PORTAL=$(gcloud run services describe kge-portal-meridian --region=$REGION --format='value(status.url)')
curl -fsS "$PORTAL/health"
curl -fsS -c portal.cookies -H 'content-type: application/json' \
  -d "{\"username\":\"analyst_retail.meridian@example.com\",\"password\":\"$(awk '$1=="analyst_retail.meridian@example.com"{print $2}' passwords.txt)\"}" "$PORTAL/session"
curl -fsS -b portal.cookies "$PORTAL/readable-units"
curl -fsS -b portal.cookies -X DELETE "$PORTAL/session" && rm portal.cookies
```

`/health` answers `{"status":"ok"}`, the sign-in answers with `analyst_retail.meridian@example.com`'s identity and `/readable-units` with `{"units":["retail"]}`. Open `$PORTAL` in a browser to browse that person's products and ask a question. The portal calls `kge-meridian` server to server. On a private page (`public=false`) Terraform grants the client's service account, which the portal runs as, `roles/run.invoker` on its paired `kge-<client>` service alone, and the portal sends its own Google identity token, minted by Cloud Run's metadata server for the audience `KGE_AUDIENCE` (the KGE service's address), in `X-Serverless-Authorization`, separate from the person's KGE session cookie, which KGE still checks. The portal itself is then private too: open it, and the commands above, through a proxy that sends your Google identity token, as for the page.

Set the service’s portal address for the guide’s Context / Sign in link and the fallback’s product switcher and logo lookup. Add `PORTAL_URL = "<the portal's https URL, the value of $PORTAL>"` to `app_env` in the tfvars (`example.tfvars`, or yours), keeping what `app_env` holds already, and apply again from `pilot`: `terraform plan "${VARS[@]}"` shows the service and the job gaining `PORTAL_URL` and nothing else, then `terraform apply "${VARS[@]}"`. The fallback links to the portal as **Discover** in its bar and, with no `KGE_LOGO` of its own, takes the portal's `PORTAL_LOGO` as it starts (that logo is a file on a volume you add to the portal's service yourself, outside Terraform: the kit's Terraform mounts no logo); the portal serves **Context** itself and reaches the API through its server using `KGE_URL`. With `public = false` the portal is private too: KGE's call for its logo carries no Google identity token and is refused, so the page shows the client's name; set `KGE_LOGO` as well to show the logo there: the same file as `PORTAL_LOGO`, on a volume you add to the `kge-<client>` service yourself, outside Terraform, as for the portal (the kit's Terraform mounts no logo on either).

Open the portal’s URL with `/context` and sign in as on your machine. `gcloud run services describe kge-meridian --region=$REGION --format='value(status.url)'` gives the API and MCP address; it has a page only when the fallback is enabled. On a private page, open it through a proxy that sends your Google identity token in `X-Serverless-Authorization` ([A private page](#bootstrap-the-project-once)). Each instance of the service serves the release the store names current when the instance starts, so after a `release` any instance that starts serves the new one while those already running serve the old. A new revision restarts them all, so that every instance serves it; the command below creates one with a fresh revision suffix. This also restarts a row-only release whose model hash is unchanged; it adds no environment setting:

```sh
gcloud run services update kge-meridian --region=$REGION --revision-suffix="release-$(date +%s)"
```

### Roles in onboarding and improvement

Roles belong to the pack (`access.roles`); the live access table binds each person to one or more role ids. Processes are platform-declared capabilities: `drafting`, `building`, `improving` and `accepting`, alongside `querying` and `certification`. A pack grants those processes to roles; a role label alone grants nothing. The knowledge engineer uploads and drafts, the business team accepts, the steward certifies, the knowledge engineer runs checks, and the steward signs off and releases. The platform engineer installs the service and assigns people; installation grants no KGE role.

Four people take part: the knowledge engineer, the business team, the steward and the end user. The page checks each step against the role of the person signed in, using the person's live access-table grants:

| Step | Who does it | Role the page checks | Page route | Job command | Role source |
|---|---|---|---|---|---|
| Land a unit: its tables, its sources, its rows | Knowledge engineer | `drafting` | `POST /onboard/tables`, `POST /onboard/source`, `POST /onboard/rows` | page only | live access table against the served pack's roles |
| Draft | Knowledge engineer | `drafting` | `POST /onboard/draft` | page only | live access table against the served pack's roles |
| Accept drafted items, answer open requests | Business team | `business` | `POST /onboard/accept` | page only | live access table against the served pack's roles |
| Certify | Steward | `steward` | `POST /onboard/certify` | page only | live access table against the served pack's roles |
| Build and gate | Knowledge engineer | `building` | `POST /onboard/run`, step `checks` | `onboard` | live access table against the served pack's roles |
| Read the margin check | Knowledge engineer | `building` | `GET /onboard/margin` | API | live access table against the served pack's roles |
| Improve | Knowledge engineer | `improving` | `POST /onboard/run`, step `improve` | `improve` | live access table against the served pack's roles |
| Sign off, release | Steward | `steward` | `POST /onboard/run`, steps `sign-off`, `release` | `sign-off PRINCIPAL=<steward>`, `release` (knowledge engineer's break-glass access) | live access table against the candidate's `access/roles.json` |
| Roll back to the previous release | Steward | `steward` | `POST /onboard/rollback` | `release PREVIOUS=yes` | live access table against the current released roles |
| Roll back through the run control | Steward | `steward` | `POST /onboard/run`, step `rollback` | `release PREVIOUS=yes` | live access table against the current released roles |
| Ask | End user | `needs("querying")` | `POST /ask`, `POST /answer`, `POST /agent`, `POST /context` | page or API only | live access table against the served graph roles |

- `business`: a person whose role has `accepting`, or, for compatibility, writes `terms` and `mappings` without `certification`; in the example, `business_team`.
- `steward`: a person whose role performs `certification`; in the example, `steward_glossary`, `steward` and `admin`. The existing steward roles have no `accepting` grant, keeping accepting and certifying apart.
- `drafting`, `building`, `improving`: the corresponding process in the pack role. `knowledge_engineer` has all three and `querying`, with no acceptance writes or `certification`. The business team reads, accepts and answers requests; it has none of these automated processes. Stewards certify, sign off and release; they do not run checks or improvements.
- `participant`: the business team, a steward, or a role with any of those three processes, which opens the page's onboarding panel.
- `needs("querying")`: a role with `querying`, which answers within the units it reads.

Sign-off and release of a candidate check the live access table against that candidate's `access/roles.json`, on the page and in the job alike. A later pack edit cannot supply its steward permission. Rollback checks the live access table against the current released roles; stored onboard and improve requests use the same live table and released-role checks.

The business team answers open requests with the knowledge engineer, who uploads any revised sources; a request also closes when its item is certified. Of the job commands, `sign-off` checks `PRINCIPAL`; `onboard`, `improve` and `release` name no person, so whoever may execute the job may run them: on Google Cloud, whoever IAM lets execute it; under Compose, whoever runs `docker compose`.

Whoever runs the KGE job on Google Cloud, or `docker compose run job` locally, is a knowledge engineer. Running the job by hand can sign off with `sign-off PRINCIPAL=<steward>` and `release` without the page. This is the knowledge engineers' break-glass power, recorded in Cloud Run's execution log. The page remains the governed route: its engineer principal cannot take these human steps. Signed page requests that remove the job operator's power are later hardening, outside this change.

The knowledge engineer uses its own principal, `knowledge_engineer.meridian@example.com` in the invented Meridian people. HTTP and MCP apply the same process checks. Accept, certify, sign-off and release require their human roles and have no MCP tools. The platform engineer owns installation through Google Cloud IAM and holds no KGE role by default; neither role implies the other. Older packs still compile and serve; their roles need explicit `drafting`, `building` and `improving` grants to use those automated endpoints.

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

The access admin grants an existing pack role without rebuilding content. If `knowledge_engineer` is missing, add it with the intended readable units and `processes: [drafting, building, improving, querying]`, then onboard, sign off and release that content change. Preserve a customised pack. Before the first release, a grant is checked against the stored pack's roles.

Using the kit helper, with the installation selected and the access admin signed in:

```sh
kge connect
kge grant knowledge_engineer.meridian@example.com ROLES=knowledge_engineer
kge passwords
```

The last command is the operator's. It keeps unchanged passwords and issues one for the added engineer. On Compose, restart `app` after updating hashes. On Google Cloud, the service reads the latest password secret through the live-reload pointer within a minute. The new person signs in as `knowledge_engineer.meridian@example.com`; acceptance and steward controls remain unavailable. No model hash moves when people change.

### Checks without an account

The pilot's configuration plans and applies against stand-in providers, creating nothing, from the zip alone, with Terraform 1.17.0 (`terraform init` fetches `hashicorp/google` 8.9.0, which `terraform/pilot/.terraform.lock.hcl` holds, from the registry or from a mirror that `TF_CLI_CONFIG_FILE` names):

```sh
cd $KIT/terraform && terraform fmt -check -recursive
(cd pilot && terraform init -backend=false && terraform validate && terraform test -var-file=example.tfvars)
```

### Check the discovery portal

Last, check the portal end to end, as on your machine. Open `$PORTAL` (the portal's address, from *Passwords and the service*; on a private page, through the proxy) and sign in as `analyst_retail.meridian@example.com` with its password from `passwords.txt`. Choose Discover from the ALTA home. Browse: the products listed are Retail's ten tables, including *Account*, *Branch* and *Monthly Revenue*, and nothing of SME, Corporate or Wealth. Open *Monthly Revenue*, then ask *Total retail revenue for June 2025* in the portal's ask panel: `kge-meridian` answers it (**13,287.67**), as the page answers `analyst_retail.meridian@example.com` within that product's scope (asking it on *Account* abstains, naming the scope). Sign out: the portal returns to its sign-in page. The same check from Cloud Shell:

```sh
curl -fsS -c portal.cookies -H 'content-type: application/json' \
  -d "{\"username\":\"analyst_retail.meridian@example.com\",\"password\":\"$(awk '$1=="analyst_retail.meridian@example.com"{print $2}' passwords.txt)\"}" "$PORTAL/session"
curl -fsS -b portal.cookies "$PORTAL/api/products"
curl -s -o /dev/null -w '%{http_code}\n' -b portal.cookies "$PORTAL/api/products/wealth.HSHLD_DIM_1"
curl -fsS -b portal.cookies -X DELETE "$PORTAL/session" && rm portal.cookies
```

The sign-in answers with `analyst_retail.meridian@example.com`'s identity, `/api/products` lists ten products with `unit_id` equal to `retail`, including `retail.account` and `retail.branch`, another unit's product answers `404`, and the sign-out ends the session.

### Browse, search, Ask and read rows

Use the same walkthrough on Compose and Google Cloud. As `analyst_retail.meridian@example.com`, open Discover and search for **Account**, open it, and choose **Data preview**. **Export CSV** downloads the allowed rows, with a default cap of 10,000. In **Run a query**, run the suggested statement or `SELECT account.account_id FROM account LIMIT 5`; the result is capped at 1,000 rows. KGE checks readable units, allowed columns, context-scoped row selectors and the served release's landed-table record before reading. Tables absent from that record are refused. If an older release says to release again, follow *Upgrade and roll back*, including `reload` when onboarding skips an unchanged hash. Badges do not certify a table or grant access.

Open **Monthly Revenue** and in **Ask**, use *Total retail revenue for June 2025*: the sample answers **13,287.67** from its certified Retail revenue column (asking it on **Account** abstains because the figure comes from `retail.monthly_revenue`). Sign out when finished. For the portal alone, use the `analyst_retail.meridian@example.com` credentials created at the start of this guide: browse and search for Account, then ask *Who owns Account?*. It answers from catalogue metadata, with no data figures. Preview, export and query require KGE.

The portal owns its product and field descriptions, favourites, requests, ratings, feedback and publishing state in `_portal_*` tables: SQLite in its Compose volume, BigQuery in the client's existing warehouse dataset on the pilot. Onboarding derives a product for every table, with optional catalogue enrichment. Refreshes seed missing records and keep activity. A value changed in `catalogue.yaml` reaches an existing card on the next refresh after its release, unless a portal edit changed that value since the last refresh; a product absent from the release is hidden, not deleted, and returns with its records. KGE's released tables, columns, row counts, terms, lineage, quality and certification are stored separately and overlaid in joined mode. A failed sync keeps the last good catalogue.

Search and Ask select KGE by default when it is configured, otherwise the portal's own tables. `PORTAL_SEARCH_PROVIDER=portal` and `PORTAL_ASK_PROVIDER=portal` select the portal explicitly; `none` is an older alias for `portal`. KGE Search uses certified terms, synonyms, lineage and governed document passages, without a generative model call. Portal Search searches its own catalogue, and portal Ask describes owners and fields but abstains on financial figures. A timeout, network failure or server error may fall back with a visible notice. Authentication or access refusals never trigger fallback. Previously verified units remain usable for at most five minutes during an outage; no warehouse read uses that cache.

### API audiences

OpenAPI and MCP tool metadata label capabilities `external: knowledge` (context, retrieve, term mapping, catalogue and search), `external: query` (Ask, preview, export and query), or `external: identity` (sign-in, agent token, identity and readable units). Internal capabilities are tagged `internal: onboarding`, `internal: governance`, `internal: quality` or `internal: platform`. A tag explains the audience; it does not grant access. Human accept, certify, sign-off and release actions remain HTTP-only. The unauthenticated KGE `/health` and `/healthz` checks support GET and HEAD, report status and image version, and open neither graph nor warehouse. Use `/health` for uptime checks.

### Sample people

The test drive substitutes the installed client id for `sample` in its seed emails. For Meridian the resulting people are listed below, in test-drive order. These are invented accounts; the `admin` pack role does not confer Access admin. `pipeline_tagger` is a machine role and receives no password. Local development seeds the table only when it is empty. An installed manual setup grants the people explicitly, as above; the test drive supplies its own seed. Do not run a new load ahead of migration on an existing install.

| Username | Pack role |
|---|---|
| `analyst_retail.meridian@example.com` | `analyst_retail` |
| `analyst_enterprise.meridian@example.com` | `analyst_enterprise` |
| `finance_reporting.meridian@example.com` | `finance_reporting` |
| `steward_glossary.meridian@example.com` | `steward_glossary` |
| `business_team.meridian@example.com` | `business_team` |
| `knowledge_engineer.meridian@example.com` | `knowledge_engineer` |
| `advisor_wealth.meridian@example.com` | `advisor_wealth` |
| `admin.meridian@example.com` | `admin` |
| `steward.meridian@example.com` | `steward` |
| `pipeline_tagger.meridian@example.com` | `pipeline_tagger` |

The installation also creates `operator.meridian@example.com` with the `analyst_enterprise` reader role and `access_admin.meridian@example.com` with reserved `access_admin` membership (Access admin). The canonical admin list contains only the latter, exactly as the operator playbook sets it.

### Access and portal roles

The pack defines roles, processes and readable units. Catalogue owners and stewards are titles, offices or teams. Acceptance and certification put role ids in drafts and the acting person in release history and audit, so removing a person cannot block onboarding. The live access table maps each username to a display name and one or more roles: BigQuery app state on the pilot, files locally and under Compose. Display names are looked up when shown. The page, HTTP and MCP resolve the signed-in person through the identity service and the table; a username that happens to match a role id gains nothing from that name. Access changes need no release, move no model hash and take effect within a minute. Removing all grants stops sign-in and existing sessions and tokens at their next request once refreshed. Rollback and teardown keep this table.

The sign-in throttle (5 failures, 300 s lockout) can lock out a person who retries repeatedly in the minute after a regrant; wait for the refresh before retrying.

**Access admin** is the reserved internal role `access_admin`. No pack may define it, and ordinary API or CLI grants cannot assign it. The operator sets the complete stored admin list with `access ADMINS=<emails> BY=<operator email>`. `ACCESS_ADMINS` remains a bootstrap setting, unioned with the stored list, so changing the stored list cannot remove an administrator still named by that setting. A pack role named `admin` is an ordinary content role, not Access admin. Installation privileges alone grant no KGE content role.

For an existing install, migrate before setting ADMINS; see the upgrade order below. For a new install, the sample load seeds its people once when the table is empty; a custom client uses explicit grants. After the profile and pack exist, an operator can set the admin list and grant against that pack before its first release:

```sh
kge access ADMINS=access_admin.meridian@example.com BY=operator.meridian@example.com
kge access GRANT=knowledge_engineer.meridian@example.com:knowledge_engineer BY=access_admin.meridian@example.com
kge access LIST=yes
kge access AUDIT=yes
```

`kge admins access_admin.meridian@example.com BY=operator.meridian@example.com` is the admin-list shortcut. On Google Cloud it can take BY from the operator's gcloud account; local and Compose calls must supply BY. Ordinary grant and revoke calls require BY to be an Access admin. ADMINS and MIGRATE are operator job actions, never HTTP or MCP tools. The list is replaced, while each person's other roles are kept.

A signed-in Access admin uses `kge grant <username> ROLES=<role>[,<role>] NAME="<display name>"`, `kge revoke <username>`, `kge list` and `kge audit`. A grant replaces that person's content-role list. Ordinary grant and revoke preserve Access admin; removing that power requires the operator's ADMINS list change and, if applicable, removing the bootstrap entry. Unknown roles, an empty role list and grants of `access_admin` are refused. Roles are checked against the served pack, or the stored pack before the first release. Each change records who, when, the before and after values and its API, job or migration origin. The access listing reports `changed_outside` when the grants differ from the audited state; inspect the audit and reconcile the change through KGE.

The API offers `GET /access`, `PUT /access/{email}`, `DELETE /access/{email}` and `GET /access/audit`; the PUT body is `{"roles":["knowledge_engineer"],"name":"Knowledge engineer"}`. The path identifies the person whose grant changes; the actor comes from authentication, never the body. `/mcp/access` exposes `list_access`, `grant_access`, `revoke_access` and `access_audit` through the same service and checks. The path parameter is called `email` for compatibility; usernames are otherwise free-form, normalised with strip, NFKC and casefold. Whitespace, control characters, more than 254 characters, `access_admin` and the `role:` prefix are refused. Sample people use fake `<role>.<client>@example.com` emails.

The portal shares KGE sign-in and display names, and keeps its own action roles. Portal roles never widen readable-unit access. Every KGE Access admin is automatically a portal admin, unioned with the portal's own admin grants; the pack's `admin` role alone does not grant that power. Nobody becomes a portal admin or approver because of their name.

| Portal role | Allows |
|---|---|
| `viewer` | Default browsing and permitted reads |
| `publisher` | Publish products within readable units |
| `approver` | Decide access requests within readable units |
| `admin` | Manage portal roles and portal administration |

In the portal's **Admin → User Management**, an admin lists people, grants or removes `publisher`, `approver` or `admin`, and reads the role audit. Unknown roles and non-admin changes are refused. Each change records who, when and before/after values in the portal's own store and takes effect at the next request. Removing a portal admin grant does not remove the admin power inherited from KGE. The migration map preserves favourites, views, cart, requests, decisions and feedback when a legacy id becomes an email. Standalone portal sign-in does not receive KGE admin inheritance.

### The environment helper

The kit's `scripts/kge` runs on the operator's laptop or Cloud Shell. Put its `scripts/` directory on PATH, keeping `playbook.py` beside it. Environments are named `<target>/<client>`; every command prints the selected environment first. State is in `~/.config/kge/state` (`KGE_STATE` overrides it), with resource names and settings, never passwords. Personal bearer tokens are separate files with mode 0600, one per environment.

| Target | Register or discover | Operator commands run through |
|---|---|---|
| `local` | `kge add local <demo-folder>` | The development command runner in that folder |
| `compose` | `kge add compose <kit-compose-folder>` | `docker compose run --rm job` |
| `gcp` | `kge sync` | The installed Cloud Run job |

`kge sync` discovers existing services, jobs, regions, clients, URLs, buckets and the password secret reference. It is read-only on Google Cloud, reports new or changed saved settings and is safe to repeat. `kge setup` adds prompts for missing setup information. `kge use gcp/meridian` selects an environment; `ENV=compose/meridian` overrides it for one command. `kge config` shows settings and `kge config NAME=value` changes one.

```sh
kge sync
kge use gcp/meridian
kge connect
kge checks
kge wait
```

On GCP, `kge connect` starts `gcloud run services proxy` and signs in with the person's KGE username and password when no valid token is saved. The Google account needs `roles/run.invoker`; the KGE account supplies the content permissions. API calls use the local proxy. Local and Compose environments need no proxy. `kge proxy`, `kge proxy stop`, `kge login` and `kge logout` are separate controls; `kge disconnect` stops the proxy and forgets the token. API commands auto-connect by default; `kge config AUTO=off` disables that behaviour. `kge --dry-run <command>` (or `DRY_RUN=yes`) prints every `gcloud`, `docker`, runner and HTTP call the command would make, on any target, and runs none of them: it starts no proxy, signs nobody in, adds no secret version and writes no state.

`checks`, `improve`, `sign-off`, `publish` and `unpublish` use the signed-in person's HTTP permissions: `publish` releases and `unpublish` rolls back. `grant`, `revoke`, `list` and `audit` use the access API. No actor is passed in a request body. Operator commands include `setup-client`, `access`, `admins`, `upload`, `passwords`, `teardown`, `release` and `rollback`; `rollback` invokes the release job with `PREVIOUS=yes`. The job operator's authority is broader than a signed-in person's.

Context’s **Docs** contains the signed-in person’s end-user playbooks. The kit carries only the installation playbook, `guides/operator.md`, and its runner. `kge playbook operator` runs its command blocks and checks their expected outcomes; `--safe` runs only read-only steps after an existing connection, and `--dry-run` previews the steps. A failed step stops the playbook. Human decisions remain human steps.

#### Evaluation before and after release

`kge eval pre` runs the candidate's margin check; its report is available at sign-off. The pre-release margin gate keeps its existing thresholds. Every release, including rollback, runs **eval post automatically** against the served graph, using the same questions, sets, asker role and scoring as that candidate's own pre-release results. There is no manual post command or page button. Anyone allowed to read releases can read the report saved with the release; it records that release's latest check.

| Comparison | Post-release rule |
|---|---|
| Retrieved items and order, SQL, figures, citations, withheld items, access rights and rules-path answers | Zero tolerance: any deterministic difference fails |
| Must-pass question set | Every required result must pass |
| Model-graded score | Report the difference and 95% bootstrap interval over questions, reusing three repeats; information only, never a release failure |

A failed post check fails the job loudly and raises a page banner saying **run kge rollback**. It never rolls back automatically. The release has already been served; investigate the report or run the rollback, which is checked too. Baseline re-recording starts only after eval post passes.

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
| `LANDING_URL` | the job: `gs://kge-<client>-landing` | the job: `gs://<project>-kge-<client>-landing` | the client's landing, where `seed` and successful fixture onboarding land certified inputs; must be the client's own bucket or prefix because teardown empties it whole; unset, it is `landing/<client>/` in the store |
| `TMP_URL` | none: a folder of the client's in the container's temporary folder | the service and the job: `gs://<project>-kge-tmp` | the scratch area, `tmp/<client>/` in that bucket, where each client's service and job alone may write: a BigQuery load larger than `BIGQUERY_LOAD_INLINE_MAX` is staged there as a file, loaded from it and deleted (the bucket deletes any file left after 7 days). The image never lists the bucket. Unset, the area is a local folder, which no BigQuery load job can read; a planned large BigQuery load refuses before onboarding writes and names `TMP_URL` |
| `BIGQUERY_LOAD_INLINE_MAX` | none | none | the most bytes of newline-delimited JSON a BigQuery load sends inline, in its request; a larger one is staged in the scratch area. Unset, 5242880 (5 MiB), a planning default; anything but a whole number above 0 stops the load, naming the setting |
| `FROM` | command argument | command argument | for `onboard`, a fresh upload prefix under the same store's `uploads/<client>/`; without a store, an absolute local folder; `put-example` and `put-baseline` use it for their source |
| `CONFIRM` | command argument | command argument | teardown requires the installed client id; test drive requires `test-drive` |
| `PREVIEW` | command argument | command argument | `yes` makes teardown list names and counts without removing anything |
| `PEOPLE` | command argument | command argument | `remove` also removes deployment setup during teardown; the live access table is kept |
| `ROTATE` | command argument | command argument | `all` rotates every sign-in password; unset merges with earlier hashes, which must exist |
| `PREVIOUS` | command argument | command argument | `yes` makes `release` restore the previous release |
| `ASKER` | command argument | command argument | for `setup-client`, the querying role; kept unless set, and an empty value clears it |
| `BASELINE` | command argument | command argument | for `setup-client`, the baseline path under `clients/<id>/`; kept unless set, and an empty value clears it |
| `CLOUD_RUN_TASK_ATTEMPT` | unset | supplied by Cloud Run | whole number at least 0; the writing commands listed above refuse automatic attempts after 0 |
| `STORAGE_EMULATOR_HOST` | `http://gcs:4443` | none | a Cloud Storage emulator, called with no credentials; unset, the image calls Cloud Storage as its service account |
| `PROFILE` | `CLIENT` | the client | the profile served and built |
| `NAME` | unset | unset | optional test-drive display name; otherwise the title-cased client id, with Meridian’s original sample name retained |
| `SIGNIN` | the page: `passwords` | the service: `passwords` | sign-in by username and password |
| `SESSION_SECRET` | from `.env` | the secret `kge-<client>-session-key` | as above |
| `ACCESS_ADMINS` | unset | optional bootstrap | comma-separated admin usernames, unioned with stored Access admins |
| `ADMINS`, `GRANT`, `REVOKE`, `LIST`, `AUDIT`, `MIGRATE`, `EMAILS`, `BY` | command arguments | command arguments | the access job operations, migration mapping and audited actor |
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
| `READER_MODEL_TIER` | none | optional through `app_env` | The document reader’s own model tier: `high`, `medium`, `low` or `default`; unset, it prefers `medium` (`gemini-3.8-flash` on the pilot). All three pilot tiers read plain text, PDF and images. Unless a `MODEL_*_MAX_TOKENS` setting is given, a reading may use up to 65,536 reply tokens, enough for a whole document's Markdown; a reading cut off at its budget stops onboarding and names the setting to raise. A file larger than Vertex AI's 20 MB inline request limit is sent through the client's scratch area in the tmp bucket (`TMP_URL`) and deleted after the call; without a Cloud Storage scratch area it is refused, naming the limit. |
| `DOCUMENT_READER` | none | `text`; the pilot: `gemini` | Document conversion: `text`, without a model, or `gemini` on Vertex AI using the reader model and existing Vertex settings. The pilot selects `gemini`; local and Compose use `text`. Unknown values are refused. |
| `GRAPH_STORE` | none | the pilot: `bigquery` (`pilot_env`) | where the client's graph is: unset or `ladybug`, a file in the image's store; `bigquery`, node and edge tables and a property graph in each client's existing `<prefix>_<client>_graph` dataset, queried with GQL, and no graph file built or released; any other value stops the image, naming the setting. It needs no Terraform change ([BigQuery](#bigquery)) |
| `EMBEDDING_PROVIDER`, `VERTEX_EMBEDDING_REGION`, `VERTEX_EMBEDDING_MODEL` or `EMBEDDING_MODEL`, `EMBEDDING_DIM` | none | the pilot: `vertex`, `gemini-embedding-001` and `768` (`pilot_env`); `VERTEX_EMBEDDING_REGION` only through `app_env` | the embedder: unset, the hashing one; `vertex`, Vertex AI's `gemini-embedding-001` unless `VERTEX_EMBEDDING_MODEL` or `EMBEDDING_MODEL` names another, 768 wide unless `EMBEDDING_DIM` says otherwise where the chunk vectors are in BigQuery, and 128 wide on the image's own files ([BigQuery](#bigquery)); `VERTEX_EMBEDDING_REGION` sets the embedding location when it differs from the models’, otherwise `VERTEX_REGION` then `global` |
| `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND` | none | the pilot: `bigquery` (`pilot_env`) | `WAREHOUSE_BACKEND=bigquery` moves the warehouses into those datasets and, unless `RETRIEVAL_BACKEND` says otherwise, the chunk vectors too; `RETRIEVAL_BACKEND=duckdb` keeps searching the image's own files ([BigQuery](#bigquery)). Unset, both stay in the image's own files |
| `PORTAL_CLIENT_NAME` | `Client` | `portal_client_name`, default `Client` | The client’s display name for the product `<client> <product>` and audience `Everyone at <client>`; trimmed, unset or blank uses `Client`. Also supplies KGE’s `CLIENT_NAME`; independent of the profile id; read when the services start |
| `PORTAL_PRODUCT_NAME` | `ALTA` | `portal_product_name`, default `ALTA` | The product’s short name, for example `ALTA`, used in `<client> ALTA`, `Ask ALTA`, `ALTA home` and `ALTA docs`; trimmed, unset or blank uses `ALTA`. Also supplies KGE’s `PRODUCT_NAME`; read when the services start |
| `PORTAL_PRODUCT_LONG_NAME` | `ALTA: AI-powered Library for Trusted Assets` | `portal_product_long_name`, default `ALTA: AI-powered Library for Trusted Assets` | The product’s long form, returned by `/brand` (`product_long_name`) and `/getConfig` (`Application_Title`); trimmed, unset or blank uses `ALTA: AI-powered Library for Trusted Assets`; read when the portal starts |
| `PORTAL_PRODUCT_SUBTITLE` | `Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.` | `portal_product_subtitle`, default as shown | The Discover home subtitle, returned by `/getConfig` as `Application_Desc`. Trimmed; unset or blank uses the default; read when the portal starts |
| `PORTAL_LOGO_TITLE` | `off` | `portal_logo_title`, default `off` | `on` shows the product name beside a configured logo; `off` hides it. Without a logo the name remains visible. The home link retains an accessible name either way. Any other value stops portal startup; read when the portal starts |
| `PORTAL_KNOWLEDGE_HOME` | `on` | `portal_knowledge_home`, default `on` | The ALTA home, home link and app switcher; `on` or `off`, read when the portal starts |
| `PORTAL_APP_CERTIFY` | `on` | `portal_app_certify`, default `on` | The Certify stub, linking out to the Power App; `on` or `off`, read when the portal starts |
| `PORTAL_APP_CONTEXT` | `on` | `portal_app_context`, default `on` | The Context workspace inside the portal, using KGE through the portal; `on` or `off`, read when the portal starts |
| `PORTAL_APP_DISCOVER` | `on` | `portal_app_discover`, default `on` | Discover: browsing, search, questions and access requests; `on` or `off`, read when the portal starts |
| `PORTAL_TABLE_QUERY` | `off` | `portal_table_query`, default `off` | The Run Query tab on table pages and its server endpoint; `on` or `off`, read when the portal starts. `off` hides the tab and refuses queries; `on` enables them when KGE data reads are available. Any other value stops startup, naming the setting |
| `PORTAL_TABLE_PREVIEW` | `off` | `portal_table_preview`, default `off` | The Preview tab on table pages, its server endpoints and CSV exports; `on` or `off`, read when the portal starts. `off` hides the tab and refuses previews; `on` enables them when KGE data reads are available. Any other value stops startup, naming the setting |
| `PORTAL_CERTIFY_URL` | empty | `portal_certify_url`, default `null` (not passed); empty also omitted | Certify's external `http` or `https` address; unset or empty shows `[Power App link]` in its door and the switcher |
| `PORTAL_BACKEND`, `PORTAL_SQLITE_PATH` | `sqlite`, `/data/portal.db` | `bigquery`, unset | the portal's repository adapter and, locally, its SQLite database; BigQuery uses `_portal_*` tables in the client's existing warehouse dataset |
| `PORTAL_ASK` | `on` | unset: `on`; the portal module does not pass it | `on` or `off`. `off` clears the legacy `kge_url` field in `/brand`; `on` supplies `KGE_PUBLIC_URL` or `KGE_URL` where KGE is configured. GlobalBar does not read that field, so this setting has no effect on navigation. Ask buttons, panels and routes (`/ask`, `/api/ask`, `/ask-data-product`, `/ask-domain`, `/feedback-add`) stay available. To hide an app, use `PORTAL_APP_CONTEXT`, `PORTAL_APP_CERTIFY` or `PORTAL_APP_DISCOVER` instead. Any other value stops startup, naming the setting |
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
| `CLIENT_NAME` | `Client`, supplied from `PORTAL_CLIENT_NAME` | `portal_client_name`, default `Client` | KGE’s client display name for `<client> <product>` (default `Client ALTA`) in role guides, the standalone Context page and OpenAPI title; trimmed, unset or blank uses `Client`. Set the portal display name once for both services; independent of the profile id and pack title |
| `PRODUCT_NAME` | `ALTA`, supplied from `PORTAL_PRODUCT_NAME` | `portal_product_name`, default `ALTA` | KGE’s product short name in `<client> <product>` (default `Client ALTA`) for role guides, the standalone Context page and OpenAPI title; trimmed, unset or blank uses `ALTA` |
| `CONTEXT_PAGE` | `off` | `off`, set through `app_env` | `on` enables the standalone Context page at `/`; `off` leaves APIs, MCP and guides available. Use the portal’s `/context` by default; invalid values stop startup |
| `KGE_LOGO` | unset | unset | the page's logo, the same contract as `PORTAL_LOGO`: the path in KGE's container of the image, checked as KGE starts (a bad one stops it, naming the setting) and served at `/brand/logo` from memory, so a new file takes effect on restart, with no rebuild; on Compose, mount the file into the app service, and on Cloud Run add a volume holding it to the service yourself, outside Terraform (the kit's Terraform mounts no logo). Unset with `PORTAL_URL` set, KGE asks the portal for its logo as it starts and keeps it, taking it only when the portal was given one; neither, the page shows the client's name beside its initial. Set one of them; when joined, KGE takes the portal's |
| `PORTAL_URL` | the portal's published address, `http://127.0.0.1:<PORTAL_PORT>`; KGE's start-up call for the portal's logo cannot reach that address from inside its container, so the page shows the client's name | set through `app_env`: the portal's `https` address ([Passwords and the service](#passwords-and-the-service)) | the discovery portal's address as a browser reaches it, `http` or `https`: the page's product switcher links to it as **Discover**, and KGE asks it for its logo as it starts. Unset, the fallback page shows Context alone |
| `KGE_PUBLIC_URL` | KGE's published address, `http://127.0.0.1:<KGE_PORT>` | unset: `KGE_URL`, which is public on Cloud Run | portal container environment: set to the address people reach KGE at. When `KGE_URL` is set, `/api/config` builds `contextMcpUrl` by appending `/mcp` to `KGE_PUBLIC_URL` (or `KGE_URL` if unset or empty), after removing trailing slashes. This is the MCP address shown in Context's MCP help. When `PORTAL_APP_CONTEXT` is on, a non-empty `KGE_URL` marks Context set up even if `KGE_PUBLIC_URL` is empty; `KGE_PUBLIC_URL` alone does not set up Context. On Compose, leaving it unset or empty gives `http://app:8000/mcp`, which a browser or agent outside Compose cannot reach. With `KGE_URL` empty or `PORTAL_APP_CONTEXT=off`, `contextMcpUrl` is null; `PORTAL_ASK=off` does not affect it. The address also supplies `/brand`'s legacy `kge_url` field, which GlobalBar ignores and which is null when `KGE_URL` is empty or `PORTAL_ASK=off`. Each address must be `http` or `https` with a host and no query: anything else stops the portal, naming the setting |
| `KGE_URL` | `http://app:8000` | the client's `kge-<client>` service URI | KGE's server-to-server address for session, identity, readable units and questions |
| `KGE_CALLER_AUDIENCE` | none | the page's own address, as `TRUSTED_CALLER_AUDIENCE` | on Cloud Run, the audience of the Google identity token the portal sends KGE as its caller token, `X-Caller-Token`, with each sign-in beside the browser's address; unset, none is sent |
| `PORTAL_SYNC_TOKEN` | none | Compose only; not set by the pilot | off Cloud Run, the shared secret sent only from the portal to KGE for whole-catalogue sync; the pilot uses the verified Google identity token instead |
| `SEARCH_PASSAGE_MIN_SIMILARITY` | `0.2` | `0.2` | search discovery floor on raw passage similarity, 0–1; zero-score hits always excluded |
| `SUGGESTED_MENTION_MIN_SIMILARITY` | `0.55` (hash) | `0.70` (real models) | Optional passage-to-term suggestion threshold in `[0, 1]`; blank defaults to `0.55` for hash embeddings and `0.70` for real models. Suggestions need steward acceptance to create links. |
| `SUGGESTED_MENTION_CAP` | `20` | `20` | Maximum suggested passage-to-term links per onboarding, highest similarity first; default `20`, nonnegative integer (`0` disables). |
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
| `TAGGER_PROVIDER`, `TAGGER_MODEL_TIER` | `model`, `medium` |
| `DOCUMENT_READER` | `gemini` |
| `EMBEDDING_PROVIDER`, `VERTEX_EMBEDDING_MODEL`, `EMBEDDING_DIM` | `vertex`, `gemini-embedding-001`, `768` |
| `VERTEX_EMBEDDING_REGION` | none: `VERTEX_REGION` unless set through `app_env` |
| `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND`, `APP_STATE_BACKEND`, `GRAPH_STORE` | `bigquery` each ([BigQuery](#bigquery)) |

`app_env` overrides any one of them, such as `app_env = { MODEL_NAME = "<another model>" }`, and `pilot_env = {}` leaves the image on its own defaults. Each model must be available to the project in Vertex AI. With any BigQuery backend on, a model call that fails, or a reply that is not JSON, stops its step on every route, and the page shows the error, instead of falling back to rules, a template or the tagger's rules; with none on, as in Compose, the steps fall back as before.

The Vertex AI embedder uses `VERTEX_EMBEDDING_REGION` when its model is served in a different region from the models. Unset, it uses `VERTEX_REGION`, then `global`. Set it for both the service and the job as [Custom resource names and settings](#custom-resource-names-and-settings) describes, and record it in `app_env`; the Gemini and Claude providers keep using `VERTEX_REGION`.

The image reads `MODEL_PROVIDER` (default `none`: no model is called and the image answers on rules), `MODEL_NAME`, `VERTEX_PROJECT` and `VERTEX_REGION` (Terraform sets these two, from `project_id` and `model_location`, default `global`) and `MODEL_SEED` (default `7`). Both Vertex AI providers call the model as the service's or the job's own service account, which holds `roles/aiplatform.user`, never with a key:

| `MODEL_PROVIDER` | `MODEL_NAME` | On a new project |
|---|---|---|
| `vertex-gemini` | required: a Gemini model as Vertex AI names it, such as `gemini-2.5-flash` or `gemini-2.5-flash-lite` | works with the project's default quota |
| `vertex-claude` | `claude-haiku-5-5` unless set | Claude is a third-party model, enabled in Model Garden, and its quota on a new project is 0: every call fails with 429 (quota exceeded for the Claude base model) until a quota increase is granted, in the console's Quotas page |

`MODEL_HIGH_PROVIDER` and `MODEL_HIGH_NAME`, and the corresponding `MEDIUM` and `LOW` settings, define optional tiers in `app_env`; a tier with no provider falls back to the default provider and name together. Onboarding and enhancement prefer high, answers and the document reader prefer medium, and context, tagging and assist prefer low when those tiers exist. `ONBOARD_MODEL_TIER`, `ENHANCE_MODEL_TIER`, `ANSWER_MODEL_TIER`, `CONTEXT_MODEL_TIER`, `TAGGER_MODEL_TIER`, `ASSIST_MODEL_TIER` and `READER_MODEL_TIER` can instead select `high`, `medium`, `low` or `default`. Add `MODEL_<TIER>_PRICE_IN` and `MODEL_<TIER>_PRICE_OUT` where the A/B report should cost a tier; a run pins its answer model and prices. A tier names its model in `MODEL_<TIER>_NAME` (only `vertex-claude` has a default), and calls its provider with its own `MODEL_<TIER>_API_KEY` and `MODEL_<TIER>_BASE_URL`. It uses `MODEL_API_KEY` and `MODEL_BASE_URL` only when its provider is the default's, so no provider is sent another's key or endpoint; the Vertex AI providers read no key at all. A selected tier with no provider falls back to the default model and logs why, and a price that does not parse is logged and left unknown. `MODEL_MAX_TOKENS` sets the reply budget of every model call, in place of the budget each step asks for (200 to 1,800 tokens), and `MODEL_HIGH_MAX_TOKENS`, `MODEL_MEDIUM_MAX_TOKENS` and `MODEL_LOW_MAX_TOKENS` set a tier's, which wins over it; unset, the image supplies 8192 tokens on every tier. The image's 8192-token default leaves room for thinking before the reply. Its attempt timeout is 180 seconds (`MODEL_TIMEOUT`), with one retry on transient errors. `MODEL_TOTAL_TIMEOUT` defaults to 270 seconds for both attempts together; each attempt is capped at the remaining time, and no retry starts after the deadline. The total must be positive and below Cloud Run’s 300-second request limit. `MODEL_REQUEST_TIMEOUT` defaults to 270 seconds for the whole answering request, including its model calls and graph and warehouse queries, in `/ask` (HTTP or MCP) and the reference agent. Its clock starts at service entry and includes time spent between calls. Each attempt receives the lesser of its own timeout, its call’s remaining total and the request’s remaining time; no call or retry starts with less than 0.05 seconds left. Exhaustion returns HTTP 503 with “The answer took too long. Please try a simpler question.”, or the same detail prefixed by `503: ` on MCP, never a partial answer. The request budget must be positive and below 300 seconds and ships in the image; an override is optional. The test-drive connectivity probe always requests 256 tokens, regardless of the configured budget. A value that is not a whole number above 0 is refused: the service does not start on one, and its log names the setting. `ONBOARD_MODEL_RETRIES` (0 to 5, default 2) is how often onboarding's drafting steps send a refused model reply back to the model before they fall back to rules, or, with a BigQuery backend on, stop. Model budgets and the timeout are defaults in the image, so a kit upgrade can change them. An operator may override them through `app_env`. Existing pilots need a one-time apply of this kit’s Terraform to remove the old budget variables from the service and job; any explicit `app_env` override still wins.

A smaller model can abstain where a larger one corrects itself. When the SQL gate refuses a model's first query and says why, `gemini-2.5-flash` rewrote it and passed in the pilot's trial, where `gemini-2.5-flash-lite` did not, and the turn abstained: no answer, never a wrong one.

The pilot’s medium tagger default follows the owner’s recorded Meridian results
(2 November 2026): medium meets the agreed targets and low does not.

### Tagger evaluation on the pilot

After deploying this image to the installed job, the owner can evaluate the
shipped invented passages and run the answering A/B against the served graph.
The job uses the installation's low, medium and high model tiers through the
same model selection as its other commands. It keeps the configured answering
and context tiers for the A/B. It does not retag the graph. The pilot defaults
to medium because the owner’s 2 November 2026 Meridian run found 93.5% of
reference mentions with no wrong mentions or candidates; low failed the agreed
recall and wrong-find targets. Mock counts cannot choose a model tier.

Set these values to the installation's project, region, client, job and store:

```sh
PROJECT=your-project-id
REGION=us-central1
CLIENT=meridian
JOB_NAME=kge-$CLIENT
STORE=gs://$PROJECT-kge-store
```

First obtain the evaluation set and its digest with a mock scoring run. This
reads and writes the configured store but calls no model or embedding service:

```sh
gcloud run jobs execute "$JOB_NAME" --project="$PROJECT" --region="$REGION" \
  --wait --args="tagger-eval,REPEATS=1"
```

The execution log prints `Results: gs://.../runs/<client>/tagger-eval/<run>/`.
Use that exact prefix, including the run id, for `RESULTS` below:

```sh
RESULTS=$STORE/runs/$CLIENT/tagger-eval/REPLACE_WITH_RUN_ID
gcloud storage cat "$RESULTS/report.md"
gcloud storage cp "$RESULTS/result.json" tagger-result.json
```

The report applies the owner’s agreed baseline and model targets and records
each arm’s pass or fail in `status.json`, even in a mock run; mock verdicts
check the plumbing only.

Review the passages and expected labels in `tagger-result.json`. Agree minimum
found and maximum wrong counts for candidates and mentions separately, the
repeat count, and the served pack's V01–V04 gates before the live run. Counts
are unique passage-label pairs per repeat, ignoring case and whitespace only;
the set has five expected candidate pairs and eight expected mention pairs.
Create `targets.json` in Cloud Shell with the actual agreement:

```json
{
  "agreed_by": "owner's agreed identifier",
  "agreed_at": "date of agreement",
  "set_sha256": "copy set_sha256 from tagger-result.json",
  "repeats": 1,
  "candidates": {"min_found": 0, "max_wrong": 0},
  "mentions": {"min_found": 0, "max_wrong": 0}
}
```

The zeroes illustrate the format; replace them with the agreed counts, totaled
across the agreed repeats. These counts are this run's own; the report judges
each tier against the agreed rate targets the image ships. Upload the
agreement to a fresh prefix, then execute the live evaluation and A/B. Change
`REPEATS=1` to match the agreement if needed:

```sh
TARGETS=uploads/$CLIENT/tagger-eval/$(date -u +%Y%m%dT%H%M%SZ)/targets.json
gcloud storage cp targets.json "$STORE/$TARGETS"
gcloud run jobs execute "$JOB_NAME" --project="$PROJECT" --region="$REGION" \
  --wait --task-timeout=10800s \
  --args="tagger-eval,LIVE=yes,AB=yes,REPEATS=1,TARGETS=$TARGETS"
```

`TARGETS` is a key under this client's `uploads/` prefix in the configured
store. The job checks the set digest and repeat count before calling a model.
`LIVE=yes` uses the installed Vertex AI settings and service account; omitting
it selects mock models for this process only. `AB=yes` adds the rules and tiered
answering runs, each with graph off and on, on the same served graph and all
its declared question sets. V03 is reported only. This compares answering on
that graph; record which tagger built it with the evidence. The invented
passages are scored separately and never loaded into the graph.

Read the new execution's `Results:` prefix even when the execution fails:

```sh
RESULTS=$STORE/runs/$CLIENT/tagger-eval/REPLACE_WITH_LIVE_RUN_ID
gcloud storage cat "$RESULTS/status.json"
gcloud storage cat "$RESULTS/report.md"
mkdir -p tagger-evaluation-results
gcloud storage rsync --recursive "$RESULTS/" tagger-evaluation-results/
```

`result.json` holds the set, agreement, tier identities, proposals, per-passage
matches, counts and the A/B gates and margins. `report.md` summarises them;
`ab/rules/` and `ab/tiered/` retain each harness run's manifest, records and
reports. Paths inside the summary are relative to this results prefix.
`status.json` records the evaluation's exit code: 0 means the supplied targets and
measured gates passed; 1 means a completed evaluation failed a target or gate;
2 means it could not complete and gives no quality verdict. An unagreed mock
run gives no quality verdict even with exit 0. An incomplete run may hold only
its status and any partial A/B records. A missing status means publication did
not finish; inspect the execution log. The execution can also fail after a
completed evaluation if saving the job's work fails; the evaluation results
remain readable at their printed prefix. Every execution has its own prefix, so
earlier results cannot stand in for a failed run. Preserve the downloaded
folder and the graph provenance with the agreed targets and live findings.

### BigQuery

Before the first BigQuery onboarding run, set `TMP_URL=gs://<project>-kge-tmp` on the job and service and grant access to bucket metadata and to read, write and delete objects under `tmp/<client>/`. Onboarding estimates load sizes before writing the fixture or building a candidate. A planned load above `BIGQUERY_LOAD_INLINE_MAX` (5 MiB by default) requires readable staging: an unset URL, a local folder or inaccessible bucket fails early. With 768-dimensional embeddings, roughly 280 chunks can already need staging; this is a planning example, not a fixed chunk limit. Text, metadata, vector values and JSON encoding change the byte count. The final load decision uses serialised bytes; smaller loads remain inline. The test drive probes the configured tmp bucket's metadata and writes, reads and deletes one small object before seed. A local Compose pass does not prove these cloud permissions.

With `GRAPH_STORE=bigquery`, the test drive also creates a uniquely named scratch vertex table and property graph in the configured client's graph dataset (`GRAPH_DATASET=candidates` selects the candidates dataset). It waits for each job and drops the graph before its table after a completed create or a terminal failure. If cancellation cannot establish that a job has stopped, cleanup is deferred: the report names the job and both scratch objects. Confirm that job is terminal before removing the graph and then its table, so late DDL cannot recreate an object after cleanup. Dataset listing and dry-run success alone are not treated as proof. A failed capability check stops before seed and onboarding; its report names the original BigQuery error and distinguishes permissions, dataset/location, unsupported configuration and temporary failures. If cleanup fails, the report names the scratch objects for an administrator to inspect before retrying. The job needs graph create/delete and table create/read/delete permissions, as well as permission to run queries.

[BigQuery editions](https://docs.cloud.google.com/bigquery/docs/editions-intro) differ: Standard does not support Graph; Enterprise and Enterprise Plus do. On-demand supports graph creation, `GRAPH_EXPAND` and measures, but not GQL. Passing the creation probe proves creation only, not that every later graph query is supported. Check the actual job's billing assignment and dataset location when the service reports an unsupported configuration; the diagnostic does not change reservations, editions or locations. [Graph permissions](https://docs.cloud.google.com/bigquery/docs/graph-create) are separate from reading dataset metadata.

The pilot's Terraform makes each client's three BigQuery datasets: its warehouses, its graph and its candidates, where the improvement job builds and checks a candidate's graph and chunks and which nothing serves. An improvement run drops its candidate's graph generation and chunks table once the candidate's check ends, passed or failed. The next onboard sweeps unnamed crash leftovers, property graphs included; the candidates dataset's seven-day table expiry is a backstop. It grants the job `roles/bigquery.dataEditor` on the warehouses and the graph (and on the candidates, since the pilot's job runs the page's improvements), and the service `roles/bigquery.dataEditor` on both: on the warehouses, where the page's onboarding steps build a unit's tables (with `WAREHOUSE_BACKEND = "bigquery"`), and on the graph dataset, where it records a steward's decisions on chunks. The pilot's `pilot_env` switches it on: `WAREHOUSE_BACKEND`, `RETRIEVAL_BACKEND`, `APP_STATE_BACKEND` and `GRAPH_STORE` are `bigquery`. With `pilot_env = {}` the pilot stays on the image's own files until you switch it: give `app_env = { WAREHOUSE_BACKEND = "bigquery" }`. That moves the warehouses and, unless `RETRIEVAL_BACKEND` says otherwise, the search over the chunk vectors too; add `RETRIEVAL_BACKEND = "duckdb"` to keep searching the image's own files. `app_env` reaches the service and the job alike. With it, the job makes the warehouses in BigQuery itself and keeps none of its own: `seed` for a test drive or a fresh fixture client builds `<unit>_v1_<table>` in `<prefix>_<client>_warehouses` and records version 1 in `_kge_catalogue`; only an older generated client's `seed` builds `<unit>_<table>`. Onboarding (the page's steps and the job's `onboard`) builds each version of a unit as `<unit>_v<N>_<table>`, its rows checked before they load and counted after, with the dataset's `_kge_catalogue` table recording each warehouse's tables and the inputs its rows were made from; neither the job's work nor a release holds a warehouse file. The job's `onboard` drops the tables of a unit since reset; it also writes the candidate's chunks and their vectors to a table of their own in `<prefix>_<client>_graph`, `chunks_<first 16 characters of the hash>_<8 characters>`, embedding each chunk that may be embedded (never an `embed-prohibited` one) straight into that table: the image's own graph keeps no vector on this path. The candidate's graph names its table in `chunks.bigquery`, which its release carries, so the margin check reads the candidate's chunks, the service only those its release names, and a candidate never touches what is served; retention keeps chunks tables referenced by the ledger's two release identities, the current pointer and the newest completed candidate, and drops tables outside that set only after successful sync and save. Tables made before this kit, `chunks` among them, are not read; delete them by hand. With `EMBEDDING_PROVIDER = "vertex"` the vectors are `gemini-embedding-001`'s unless `EMBEDDING_MODEL` names another, and 768 wide unless `EMBEDDING_DIM` says otherwise; on the image's own files they stay 128 wide, and any other `EMBEDDING_DIM` is refused there. Tables made before this kit carry no label and are never dropped by it; delete them by hand if you want them gone.

`GRAPH_STORE=bigquery`, the pilot's default and in `app_env` elsewhere, keeps the client's graph in BigQuery: the job's `onboard` loads each candidate into node and edge tables of its own in `<prefix>_<client>_graph`, a generation named by its model hash and the load (`g<first 16 characters of the hash>_<8 characters>_Unit`, `..._HAS_TABLE__Unit__Tbl` and so on), with a property graph over them, `..._graph`, so a load, even of the hash being served, never replaces what the service reads, and each released hash stays there to roll back to; tagging and a steward's decisions write to them, and the service queries them. A release then carries `graph.bigquery`, a small file naming those tables, in place of the image's own graph, and `release.json`, what the release named, its graph and its chunks in one file written whole, which the service opens (a release made before this kit, with `graph.bigquery.released` and `chunks.bigquery.released`, is still served, and the next release replaces them): a later `onboard` of the same pack loads a new generation for its check, but the service, even restarted from the job's work, serves the released one until that candidate is signed off and released. Nothing is copied from an existing graph: a client's graph moves to BigQuery when the job's `onboard` runs with the setting and its candidate is signed off and released, and until that release there is no graph on the selected store to serve. A client with no release starts with the release-first message; a retained release with missing backend parts still needs repair. Retention keeps graph generations and chunks tables referenced by the ledger's two release identities, the current pointer and the newest completed candidate. Older release files and abandoned candidates do not keep extra generations. A failed onboard drops the new generations and chunks it created while protecting retained releases; replacing an unreleased candidate at the same hash removes its superseded generation once nothing retained references it. A release of the same pack again, at a new generation, counts as a new release: the service moves to it on its next look at the onboarding page's status, or when it restarts, so it reads the tables of one of those two releases as long as it does so before the release after next. This path has been tested against stand-ins for BigQuery only: check the first `onboard` and answers on a real project.

`APP_STATE_BACKEND=bigquery`, the pilot's default and in `app_env` elsewhere, keeps unit manifests, history, status inputs, open requests and onboarding job state in app-created tables in each client's existing `<prefix>_<client>_graph` dataset. Before changing an existing pilot, run `onboard-status` and record every listed unit. The two state backends do not migrate records automatically: reset each listed unit while the files backend is still selected, apply the BigQuery setting, then upload and onboard those units again. A pilot with no listed units can switch directly. Do not switch with a unit still listed, because it would disappear from the page while its files remained in the store. Switching back from BigQuery to files keeps the same order the other way: run `onboard-status` and record every listed unit, reset each one while `APP_STATE_BACKEND = "bigquery"` is still selected, so its manifest and history leave BigQuery with it, then revert the setting and apply, then upload and onboard those units again. A unit reset after the revert leaves its BigQuery records behind, and it would come back without its inputs on a later switch to BigQuery.

The apply rolls out a new revision of the service that reads BigQuery at once, but the datasets stay empty until the job has run `onboard` with the setting, and until then the service's questions find no tables (with `GRAPH_STORE=bigquery`, it cannot answer until that `onboard`'s candidate is released). So, on a pilot already running, run the job's `onboard` straight after the apply, then sign off and release its candidate as usual ([Set the client up and release](#set-the-client-up-and-release)). To switch back, first, where the app state is in BigQuery and `onboard-status` lists a unit, record and reset each listed unit while BigQuery is still selected, as the paragraph above says. Then remove the setting (on the pilot, override it in `app_env`: `WAREHOUSE_BACKEND = "duckdb"`, `RETRIEVAL_BACKEND = "duckdb"` and `APP_STATE_BACKEND = "files"`), and bring `EMBEDDING_DIM` back to 128, since any other width is refused once the vectors are back in the image's own graph, which is 128 wide: on the pilot set `EMBEDDING_DIM = "128"` in `app_env`, because removing it from `app_env` leaves `pilot_env`'s `768`; elsewhere remove it or set it to `"128"`; and apply; then run the job's `onboard` and sign off and release its candidate, and upload and onboard again each unit you reset: the image's own files are still there, but a graph built while the vectors were in BigQuery holds none, so until that `onboard` the service's answers cite no documents. This path has been tested against stand-ins for BigQuery only, so check the first answers on a real project.

**The pilot's own baseline.** A baseline is specific to its graph store and asker role. A fresh pilot starts with no `BASELINE`, so its first candidate is checked against the pack's own gates. If an existing profile names a baseline from another store, clear it explicitly with `setup-client BASELINE=` without changing people; omitting `BASELINE` keeps it. Each job release, rollback included, records `baselines/bigquery-<UTC time>.json` on rules and points the profile at it. The separate `baseline` job repairs a failed recording without changing people or asker. See [Release, rollback and baseline](#release-rollback-and-baseline) for set changes and failure handling.

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

A job command takes its own settings after it, `NAME=value`: `PRINCIPAL` (sign-off), `HASH` (sign-off and release of a hash other than the pack's), `PREVIOUS` (release), `ASKER` and `BASELINE` (setup-client), `ROTATE` (passwords), `CONFIRM`, `PREVIEW` and `PEOPLE` (teardown), `EXAMPLE` and `FROM` (put-example: the folder, or the store location such as `$STORE/examples`, that holds the example) and `CONFIRM` plus optional `NAME` (test-drive display name). Test-drive and put-baseline reject `CLIENT`, `PROFILE` and `EXAMPLE` command parameters: both use the install’s client and the Meridian sample.

| Command | What it does |
|---|---|
| `test-drive` | with `CONFIRM=test-drive`, on a fresh install only: checks the job's settings, buckets, BigQuery, models, embeddings and versions, then sets up, seeds, onboards (refreshing the schema lock), signs off and releases the Meridian sample branded as the installed client, retained as its editable starting baseline; checks its golden set, its derived catalogue for every table, and governed preview, query and cross-unit refusal through the data service; prints its report and saves it under `runs/<client>/test-drive/` |
| `access` | lists grants or audit, grants/revokes roles, migrates identities, or replaces ADMINS; every write names `BY` |
| `setup-client` | sets the client profile and keeps `ASKER` (a role) and `BASELINE` unless explicitly changed or cleared; never changes the access table |
| `put-baseline` | copies the Meridian sample as the install’s `PROFILE`, branding its id and optional `NAME` display name, preserving YAML formatting; accepts `FROM` and refuses an existing client pack |
| `put-example` | uploads an example's pack from the kit to the store (`EXAMPLE`, default `meridian`) |
| `seed` | builds and lands the initial estate during the test drive; the generator remains available for development, while subsequent fixture changes use `onboard` |
| `lock` | retained for local development; no person runs it on the pilot, where `onboard` refreshes the schema lock |
| `onboard` | optionally stages `FROM`, sweeps unnamed leftovers, checks declared inputs, rebuilds a changed fixture, certifies, refreshes the lock, compiles, loads and gates; publishes only on success; changed people reload even at the same hash; a failed gate is checked again |
| `reload` | reloads the current hash from its compiled artifact and landed catalogue, records the tables landed in the release and runs the candidate check; clears the previous check, sign-off and release marker, and publishes nothing |
| `onboard-request` | validates and runs the page's stored `onboard`, `improve`, `sign-off` or `release` request; the job's fixed default |
| `improve` | runs the improvement job, checks its edits against the guards and the A/B margin, and writes a governed proposal |
| `sign-off` | a steward's sign-off on a candidate whose check passed |
| `release` | publishes the signed-off candidate, or restores a retained `HASH`; `PREVIOUS=yes` restores the previous release including its inputs and warehouse versions, keeping live access unchanged at the same hash too; records a new baseline; restart the service afterwards |
| `releases` | lists the client's releases, marking the current one |
| `passwords` | merges hashes for the live access table; only added people get new passwords, removed people lose hashes; `ROTATE=all` rotates everyone or starts with no merge base |
| `eval` | the A/B run, both arms on the question sets |
| `tagger-eval` | passage scoring for RuleTagger and all three tiers; `LIVE=yes` uses configured models and requires `TARGETS`, `AB=yes` adds the served graph’s answering A/B; saves `runs/<client>/tagger-eval/<run>/` ([Tagger evaluation on the pilot](#tagger-evaluation-on-the-pilot)) |
| `baseline` | records the released graph on rules as `baselines/<graph store>-<UTC time>.json` and updates the profile, keeping people and asker; also runs after every successful release |
| `teardown` | `CONFIRM=<client>` removes client data after a store backup; `PREVIEW=yes` lists it only; `PEOPLE=remove` also removes deployment identities and profile; no page or MCP control |

The page persists the short source, draft, acceptance and certification edits under its own `clients/<client>/` prefix. Those inputs stay in `clients/`; generated warehouses and graph state stay in the job's `work/`, and releases stay in `releases/`. On Google Cloud, Run checks, sign-off and release write a validated request and start the client's fixed `onboard-request` job without overrides. The page reads the job's store marker whenever the page polls. Under Compose there is no Cloud Run API: the page accepts the request and returns HTTP 202 with the exact `docker compose run --rm job onboard-request` command for the operator; the request is validated in the same way. One request may run for a client at a time. A `running` marker older than 185 minutes—beyond the job's three-hour timeout—becomes `stale` and may be superseded, so an unrun command or a terminated job does not block the page indefinitely. Compose and Cloud Run pages follow a completed page/API release on the next status poll: with `STORE_URL` set, each process fetches and switches to the store's current graph. The pilot has at most one instance. The helper's `publish` and `unpublish` use this path; `kge wait` polls it. A direct operator `release` command does not write that page job marker, so restart the service after it to ensure every process follows.

## Building the images on your own base

The kit's images are built on public bases from public package indexes: KGE's on Python's Debian slim image, the portal's on Red Hat's signed Universal Base Image 9 Node.js 22 minimal image, which runs on RHEL 9. A team that must build on its own base, from its own index and behind its own certificate authority, builds both images from their source folders, the KGE source folder and the portal source folder, with these build arguments. Tag them `kge:@VERSION@` and `kge-portal:@VERSION@` and the steps above use them in place of the kit's tarballs: skip those two `docker load` commands.

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
docker build --platform linux/amd64 -t kge:@VERSION@ \
  --build-arg BASE_IMAGE=<your Python 3.12 base> \
  --build-arg UV_DEFAULT_INDEX=https://<your index>/simple \
  --build-arg UV_SYSTEM_CERTS=true \
  --secret id=netrc,src="$HOME/.netrc" \
  <the KGE source folder>
docker build --platform linux/amd64 -t kge-portal:@VERSION@ \
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
docker load -i images/kge-@VERSION@.tar
docker load -i images/kge-portal-@VERSION@.tar
docker tag kge:@VERSION@ $REGISTRY/kge:@VERSION@ && docker push $REGISTRY/kge:@VERSION@
docker tag kge-portal:@VERSION@ $REGISTRY/kge-portal:@VERSION@ && docker push $REGISTRY/kge-portal:@VERSION@
cosign sign --key <your key or KMS URI> $REGISTRY/kge@sha256:<the digest kge's push reported>
cosign sign --key <your key or KMS URI> $REGISTRY/kge-portal@sha256:<the digest kge-portal's push reported>
cosign verify --key <your public key or KMS URI> $REGISTRY/kge-portal@sha256:<that digest>
```

- **The digest.** `docker push` prints it on its last line, `digest: sha256:<64 hex characters>`. Deploy by that digest too, so what runs is what was signed.
- **The key.** A key file (`cosign generate-key-pair`) or a key-management URI (`gcpkms://`, `awskms://`, `azurekms://`, `hashivault://`), as your platform team's policy names it. The policy is given the public key.
- **A registry with no route to the internet.** cosign records each signature in Sigstore's public transparency log by default. Where the registry cannot reach it, or the image names must not be published, add `--tlog-upload=false` to `cosign sign`, and `--insecure-ignore-tlog` to `cosign verify`.
- **On Google Cloud.** The same, after [Push the images](#push-the-images), against the project's repository, where a policy of yours asks for a signature.

## Upgrade and roll back

### Access migration for an installed client

Back up the installed deployment seed and profile, content and review records, and the live access state if it already exists. Update the job and service image, then follow this order **before loading or onboarding**:

1. Run `access MIGRATE=yes BY=<operator email>` first. Supply `EMAILS="<old id>=<email> ..."` for known addresses. Migration requires an empty table, preserves unmapped legacy usernames until an email is supplied, and saves the asker as a role. It reports rows written and people still missing email mappings. If it reports already migrated, inspect the existing table rather than deleting it to force a rerun.
2. Run `access ADMINS=<admin emails> BY=<operator email>` after migration. Setting ADMINS first creates rows and prevents the initial migration. The operator job authorises this bootstrap; `ACCESS_ADMINS` is also available as a bootstrap setting. Use the migration email map so portal histories follow the same person.
3. Read the existing onboarding and proposal records. Legacy accepted/certified records are upgraded on read: roles enter the draft, while the original person and dates are preserved in history and `review-upgrade.json`. Unmapped reviews are reported explicitly and kept as legacy evidence; they do not grant current access. Keep the old deployment seed until this review is complete.
4. Onboard the content, inspect the candidate and pre-release check, sign off and release. Removing people from compiled output changes the model hash once; later people changes do not. Unchanged pack inputs retain their pack hash, which is distinct from the compiled model hash. Automatic eval post must pass before the baseline is re-recorded.
5. Run `kge passwords` to merge passwords for the live table and install the live-reload pointer. For a legacy id retained through migration, keep using it until its email mapping and account handover are complete.

For example, with the helper pointed at the installed Meridian environment:

```sh
kge access MIGRATE=yes BY=operator.meridian@example.com "EMAILS=analyst_enterprise=analyst_enterprise.meridian@example.com steward_glossary=steward_glossary.meridian@example.com"
kge access ADMINS=access_admin.meridian@example.com BY=operator.meridian@example.com
kge access LIST=yes
kge access AUDIT=yes
```

Map every legacy person you can; the two mappings above only illustrate the syntax. On Compose, the equivalent prefix is `docker compose run --rm job access`; on Cloud Run use the access job arguments. A content rollback leaves the current access table, password secret and portal roles in place.


This kit adds per-release ids and tarballs at `releases/<client>/<hash>/<release>.tar.gz`, with the chosen id in `identity.json` and the release ledger. Two releases at the same model hash can now be distinguished and rolled back faithfully. New releases keep their declared fixture files and inputs with those two retained snapshots; access remains live. Old releases remain restorable only as published; a previous same-hash release made before release ids is refused because its original tarball was not kept. `release HASH=<hash>` older than the ledger's two releases now refuses, naming the missing snapshot; it no longer rebuilds a release from a legacy pack snapshot. Adding `manifest.inputs` leaves hashes, locks and existing baseline records unchanged.

After the access migration above, add input declarations as [A client from its own fixture](#a-client-from-its-own-fixture) describes, use a fresh `onboard FROM=` upload for each change, and set custom jobs to `--max-retries=0`. The first release-time baseline re-record enables all per-item score comparisons; older baselines compare pass rate only. Before the next password merge, copy the current secret version into the store; first local generation needs `ROTATE=all`.

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

- **An earlier test-drive install.** Kits 0.8.4 to 0.9.29 seeded `<role>.sample@example.com`. Updating images does not rename those people: use their existing usernames instead of the guide’s `<role>.<client>@example.com`, or start the scratch client over. For a pilot test-driven before the fixture, live access table and client-named people were introduced (for example, 0.9.19 to 0.9.32), use this order: deploy the new images, review and apply the new kit’s Terraform on the existing backend, then follow [Before a fresh test drive](#before-a-fresh-test-drive). Preview `teardown CONFIRM=<client> PEOPLE=remove PREVIEW=yes`, inspect its inventory and backup limits, then run `teardown CONFIRM=<client> PEOPLE=remove`. Run `test-drive CONFIRM=test-drive` and wait for its successful release before running `passwords ROTATE=all`. This route discards the scratch content and deployment setup; save anything you need first. Teardown keeps an existing live access table, so inspect its grants and retained usernames after the test drive; it does not rename existing people. Keep a working client’s content and accounts by following [Access migration for an installed client](#access-migration-for-an-installed-client) instead. An installation moving from 0.9 also follows [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal) for the portal changes, then [Upgrading from 0.10](#upgrading-from-010-context-docs-and-playbooks) for the Docs and playbook changes.

- **ALTA and table queries.** The portal now shows `<client> ALTA` (default “Client ALTA”), configurable with `PORTAL_PRODUCT_NAME` (`portal_product_name`, default `ALTA`) and `PORTAL_PRODUCT_LONG_NAME` (`portal_product_long_name`, default `ALTA: AI-powered Library for Trusted Assets`), and uses “curated dataset”. Keep `PORTAL_CLIENT_NAME` as the client’s name alone; `Everyone at <client>` stays the same. The portal names itself “ALTA: AI-powered Library for Trusted Assets”. `PORTAL_PRODUCT_SUBTITLE` (`portal_product_subtitle`) sets the Discover home subtitle; unset or blank uses “Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.” Run Query on table pages and its server endpoint now default to off. To enable them, set `portal_table_query = "on"` in the pilot tfvars and apply the reviewed Terraform change, or set `PORTAL_TABLE_QUERY=on` in the Compose `.env` and run `docker compose up -d --force-recreate portal`. Preview and its endpoints also default to off. To enable them, set `portal_table_preview = "on"` in the pilot tfvars and apply the reviewed Terraform change, or set `PORTAL_TABLE_PREVIEW=on` in the Compose `.env` and recreate the portal as above. Both settings take effect when the portal starts; `off` hides the corresponding tab and refuses its endpoints. With both off, table pages make no warehouse queries. Run Query uses catalogue fields for its column list and works with Preview off.

On BigQuery, the first onboard or release after this upgrade prunes graph generations, chunks tables and warehouse versions, including old sample v0 tables, that neither of the two retained release identities, the current pointer nor the newest completed candidate references. Historical release files do not extend retention. Rollback with `release PREVIOUS=yes` uses the previous retained identity and its original warehouse versions; an older pruned release cannot be rebuilt by selecting its hash.

Before an onboard that needs staging, set `TMP_URL=gs://<project>-kge-tmp` and grant bucket metadata access and read, write and delete access under `tmp/<client>/`. At 768 embedding dimensions, roughly 280 chunks can require staging; actual text, metadata and vector values determine the size. A planned load above the inline limit is refused at the start if staging is unset or inaccessible, before warehouse writes. See [The image’s settings](#the-images-settings) for the byte limit and checks.

For a fixture client, upload changed pack and fixture files as in [A client from its own fixture](#a-client-from-its-own-fixture), then run `onboard`, review its changes, sign off, release and restart the service. Do not run `lock` or `seed`: onboarding owns the schema refresh and fixture rebuild. The schema-only lock drops old row-count and storage fields once, so the first upgrade can change the model hash. Later row-only changes reload the same hash and still require a fresh check and sign-off. An image or identity-only upgrade that reports `current already` still needs `reload` where the steps below require it.

A kit's first two numbers are its line: 0.6.0, 0.6.1 and 0.6.2 are one line. A later kit of the installed kit's line upgrades it as follows; neither way touches a client's pack, people, units or releases.

- **On Google Cloud.** Follow steps 1 to 4 of *Upgrading from 0.5.0* below, with the installed kit wherever they name 0.5.0: check and load the new kit, push its image, initialise its Terraform on the same backend with the same variables, save each client's digest, current release and data, and deploy the new image to its job and service, then review `terraform plan` and apply it if it shows changes. Step 5 is 0.5.0's alone. To roll back, return each client's job and service to its saved digest and, if the apply changed anything, apply the installed kit's Terraform again.
- **On your machine.** Run the first commands of *Other upgrade and rollback operations* below, with `OLD_KIT` the installed kit's folder: they back up the store, copy `.env`, load the new image and start `gcs` from the new kit's `compose/`. Then run `docker compose up -d app` there, and nothing else from that section. To roll back, run `docker compose up -d app` from the installed kit's `compose/`.
- **To a kit with the tagger on medium.** The pilot’s `TAGGER_MODEL_TIER` default changes from `low` to `medium`. Follow the Google Cloud steps above and review `terraform plan`: unless `app_env` or your own `pilot_env` overrides the tier, it shows that environment setting changing on each client’s service and job. Apply this kit’s Terraform to take the new default; deploying the image alone keeps the old tier. To roll back, apply the installed kit’s Terraform again.
- **To a kit with the providers at 8.9.0.** The pilot's Terraform now requires `hashicorp/google` 8.9.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes.
- **To a kit on Python 3.12 with the providers at 8.8.0.** KGE's image is rebuilt on Python 3.12 (`python:3.12.15-slim-bookworm`), with its Python dependencies moved together, and the portal's with its npm dependencies updated; a client's pack, people, units and releases are read as before. The pilot's Terraform now requires `hashicorp/google` 8.8.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`, which would move the lock), as the steps above do, then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes. Upgrade the portal too, which the steps above do not: load, tag and push the new kit's portal image and set `PORTAL_IMAGE` and `PORTAL_DIGEST` as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does, then deploy it to each client's `kge-portal-<client>` as that section's step 3 does: its catalogue sync on BigQuery now sends each column's type, so a client whose first product scores 0.0 syncs (an install where `GET /api/products` returned nothing after a release syncs on the new revision's start, or within five minutes). To roll back, as above, and deploy each portal's previous image again: note it first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`.
- **To a kit whose portal and KGE pages show the client's display name.** Set the client's name once with `PORTAL_CLIENT_NAME`: Compose passes it to the portal and to KGE as `CLIENT_NAME`. It names the ALTA product, role guides, standalone Context page and developer API reference. An install that upgrades without setting it shows "Client ALTA" and "Everyone at Client". On Google Cloud, set `portal_client_name` in the tfvars, for example `portal_client_name = "Meridian"`, review `terraform plan` and apply it: this makes portal and KGE service revisions. On your machine, set `PORTAL_CLIENT_NAME` in the Compose `.env` and run `docker compose up -d --force-recreate app portal`. To roll back, unset it the same way.
- **To a kit whose portal is built on Red Hat's UBI base (0.9.19 on).** The portal's image moves from Node's Debian slim base to Red Hat's signed UBI 9 Node.js 22 minimal base and runs as the same uid 1000, group 0, so its `portal-data` volume and its `_portal_*` tables are read as before. Upgrade the portal as well as KGE. On Google Cloud, note each client's portal image first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`, then load, tag and push the new kit's portal image as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does and deploy it to each client's `kge-portal-<client>` as its step 3 does; to roll back, deploy the noted image again. On your machine, load the new kit's portal image beside KGE's (`docker load -i images/kge-portal-@VERSION@.tar`) and run `docker compose up -d portal` with `app`; to roll back, run it from the installed kit's `compose/`. Where your registry admits only signed images, sign both images there before deploying them: [Pushing the images to your registry and signing them](#pushing-the-images-to-your-registry-and-signing-them).
- **To a kit whose job may run three hours.** Each client's job now has a three-hour timeout (`10800s`), up from one hour, so `onboard` and `test-drive` on a full pack finish; the page holds a request's `running` marker for 185 minutes before marking it `stale`. On Google Cloud the timeout reaches an installed job only through this kit's Terraform: `terraform plan` shows each `kge-<client>` job's timeout changing, so apply it. Until then the job keeps its one-hour limit and stops before such a run ends.
- **To a kit that reads documents in any format.** On Google Cloud, `terraform plan` shows `DOCUMENT_READER=gemini` added to each client's service and job, Vertex AI's service agent (`service-<project number>@gcp-sa-aiplatform.iam.gserviceaccount.com`) made through `hashicorp/google-beta`, which the pilot's `.terraform.lock.hcl` now holds too, and one new grant on the tmp bucket: `roles/storage.objectViewer` for that agent, conditioned on `tmp/`, so Gemini can read a document too large to send inline. Initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then apply it. Until then the image reads documents with the text reader, and PDFs and images onboard without passages. To roll back, apply the installed kit's Terraform again.
- **Model settings, before either.** A `TAGGER_PROVIDER` that names a provider other than `MODEL_PROVIDER` now needs its own model in `TAGGER_NAME` and, where that provider takes a key, its own key in `TAGGER_API_KEY` (`TAGGER_BASE_URL` for its endpoint): the tagger refuses to start without its model, naming the setting, and is never sent `MODEL_NAME` or `MODEL_API_KEY`, which are `MODEL_PROVIDER`'s. A `MODEL_<TIER>_PROVIDER` naming no known provider (a mistype) now stops each agent on that tier, naming it, instead of quietly falling back to rules; `none` there is kept as the switch that turns that tier's model off. With the settings the kit ships, which name no model, nothing changes.

### Upgrading from 0.10: Context Docs and playbooks

A 0.11 kit upgrades a 0.10 install. It preserves the client's pack, people, units, releases and Terraform state. Apply any relevant changes in *Upgrading within a line* above for the installed kit, then upgrade KGE's images by those steps and the portal's image by [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal) step 2. Upgrade both services so the portal's Docs entry and the served guides agree. The Docs and playbook changes require no additional Terraform change or new setting.

Context's global bar now opens its own Docs: page-only guides for knowledge engineers, business teams and stewards, plus the official API and MCP connection guide for developers and agent builders. The guides follow the person's existing permissions. Tools and the SQL check no longer appear in Context; the SQL check remains available through the API.

The kit carries only the installation playbook, `guides/operator.md`, and its runner. Use `docs/ONBOARDING.md` to find it. End users follow Context's Docs for their daily work. Executable development playbooks remain in the repository and are no longer shipped in the kit.

Keep the installed 0.10 kit and the previous image references until the upgrade is checked. To roll back, restore KGE's images as *Upgrading within a line* describes and the portal's image by [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal)'s rollback steps. On your machine, use the installed kit's Compose folder as those steps describe.

### Upgrading from 0.9: Context in the portal

A 0.10 kit upgrades a 0.9 install. It doesn't touch a client's pack, people, units, releases or Terraform state, and it needs no Terraform change. Upgrade the portal as well as KGE: Context's page now lives in the portal, so people reach it only through a 0.10 portal.

1. **KGE.** Follow *Upgrading within a line* above with the 0.9 kit as the installed one.
2. **The portal.** On Google Cloud, note each client's portal image first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`. Then load, tag and push the new kit's portal image as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does, and deploy it to each client's `kge-portal-<client>` as its step 3 does. On your machine, load the new kit's portal image beside KGE's (`docker load -i images/kge-portal-@VERSION@.tar`) and run `docker compose up -d app portal` from the new kit's `compose/`. Where your registry admits only signed images, sign both images before deploying them: [Pushing the images to your registry and signing them](#pushing-the-images-to-your-registry-and-signing-them).
3. **Where people find Context.** People open Context from the portal, at its `/context` address, after signing in to the portal. The service's own page at its address is off by default (`CONTEXT_PAGE=off`): it answers 404, while its APIs, MCP, `/onboarding-guide` and `/docs` stay available. Tell people the portal address before the upgrade.
4. **Keeping the service's own page during a changeover.** Set `CONTEXT_PAGE=on`. On Google Cloud, add `CONTEXT_PAGE = "on"` to `app_env` and apply. On your machine, set it in the Compose `.env` and run `docker compose up -d --force-recreate app`.

To roll back on Google Cloud, deploy KGE's previous images as *Upgrading within a line* describes, and each client's noted portal image to its `kge-portal-<client>`. On your machine, run `docker compose up -d app portal` from the installed kit's `compose/`. The service's own page then serves Context at its address, as before.

### Catalogue attributes on an existing portal

The portal adds `asset_type`, `usage_purpose`, `dataset_type`, `APM_ID`, `project`, `product_owner_email` and `business_owner_email` columns to its products table and `classification` to its fields table on startup, idempotently in SQLite and BigQuery. Existing rows and portal activity are preserved; no manual table recreation is needed. Older rows show blank values until the catalogue is synced again.

Set `product_type` to `Table`, `View`, `API/MCP` or `Documents`, `asset_type` to `Structured` or `Unstructured`, and `usage_purpose` to `Analytical` or `Operational` on products in `catalogue.yaml`. `dataset_type` and `project` are optional free text (`project` is shown on the Overview only where set). `product_owner_email` and `business_owner_email` are optional contact addresses for the product and business owners, editable in the portal and preserved across refreshes by the same rule as `product_owner` and `business_owner`. On each field in `fields`, optional `classification` accepts `Public`, `Internal`, `Confidential`, `Highly Confidential` or `Restricted` (in any letter case; `Restricted` is shown on the Fields tab as `Highly Confidential`). Asset-level classification is a badge (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `HIGHLY CONFIDENTIAL`). Source identifiers belong in the same file's optional `sources` list, for example:

```yaml
sources:
- id: retail
  APM_ID: APM-MER-RETAIL-001
```

`APM_ID` is optional free text and is inherited by every product of that source. A product may also give its own `APM_ID`, `path`, `data_format` and `schema` as non-empty text; each replaces the value KGE derives (the source's `APM_ID`, the product id, the table kind and the unit id), and a product without one keeps the derived value. On each field, `quality` may be a list of rules when a column has several, and a rule's `fail_percent` may be left blank; the portal shows a blank fail % as blank. On startup the portal adds a `schema` column to its products table and a `rule_ordinal` column to its quality rules table, and lets `fail_percent` be empty: SQLite rebuilds the quality rules table, keeping its rows as each column's first rule, and BigQuery drops the column's `NOT NULL` in the same script that adds the columns. Older spellings such as `TABLE` and `VIEW` are still accepted, in any letter case, and become `Table` and `View` at the next onboard. Onboard, sign off and release the updated pack, then restart KGE as usual. A re-sync is required to populate the new columns: the portal picks up the new release on its next catalogue check; restart the portal to sync immediately. Deploy both upgraded images, KGE's and the portal's, before doing so: an older portal refuses a newer KGE's catalogue and keeps its last one. No Terraform change or new setting is required.

### Upgrading from 0.8: the discovery portal

A 0.9 kit upgrades a 0.8 install, and adds the discovery portal to it. It is written for an install on the newest 0.8 kit: an install on an earlier one also takes that kit's changes on the way, so read *Upgrading from 0.8.0 within the 0.8 line* below first (a pilot on 0.8.0 also gains the tmp bucket in step 2's plan), or upgrade to the newest 0.8 kit before this. It does not touch a client's pack, people, units, releases, Terraform state or passwords, and KGE answers as before. On the pilot it adds one Cloud Run service per client, `kge-portal-<client>`, running as the `kge-<client>` service's account, and the image repository gains a second image, `kge-portal`. No account, dataset or grant is added. The portal creates its `_portal_*` tables in the client's existing warehouse dataset when it first starts.

An existing install gets a catalogue by running onboard, sign-off and release: KGE derives every product from the pack, lock and model roles, and the portal works from onboarding alone. Uploading `catalogue.yaml` beside `pack.yaml` and `pack.lock` is optional; it enriches descriptive fields and is validated against the governed tables, columns and roles. The portal syncs the release without replacing favourites, requests, feedback, roles or publish status.

1. Follow *Upgrading within a line* above with the 0.8 kit as the installed one, through deploying KGE's new image to each client's job and service. Load the portal's image with KGE's, and push it beside KGE's:

   ```sh
   docker load -i "$BUNDLE/images/kge-portal-@VERSION@.tar"
   PORTAL_IMAGE=$REGION-docker.pkg.dev/$PROJECT/kge/kge-portal
   docker tag kge-portal:@VERSION@ $PORTAL_IMAGE:@VERSION@ && docker push $PORTAL_IMAGE:@VERSION@
   PORTAL_DIGEST=$(gcloud artifacts docker images describe $PORTAL_IMAGE:@VERSION@ --format='value(image_summary.digest)')
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
  export BUNDLE=<absolute path to the @VERSION@ kit folder holding images/>
  export KIT=<absolute path to the newly unzipped @VERSION@ folder>
  cd "$OLD_KIT/compose" && . ./.env
  curl -fsS -o "$KIT/compose/pack.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.yaml?alt=media"
  curl -fsS -o "$KIT/compose/pack.lock.$CLIENT.before-@VERSION@" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.lock?alt=media"
  curl -fsS -o "$KIT/compose/identities.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/deploy%2F${CLIENT}%2Fidentities.yaml?alt=media"
  curl -fsS -o "$KIT/compose/work.$CLIENT.before-@VERSION@.tar.gz" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/work%2F${CLIENT}.tar.gz?alt=media"
  curl -fsS -o "$KIT/compose/current.$CLIENT.before-@VERSION@" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/releases%2F${CLIENT}%2Fcurrent?alt=media"
  cp "$OLD_KIT/compose/.env" "$KIT/compose/.env"
  cp "$OLD_KIT/compose/passwords.txt" "$KIT/compose/passwords.txt"
  docker load -i "$BUNDLE/images/kge-@VERSION@.tar"
  docker load -i "$BUNDLE/images/kge-portal-@VERSION@.tar"
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
  docker compose run --rm job sign-off PRINCIPAL=steward_glossary.meridian@example.com
  docker compose run --rm job release
  docker compose restart app
  ```

  Compose KGE reads the release when `app` starts, so the restart is required. The portal sees it on its next five-minute catalogue check (`PORTAL_CATALOGUE_SYNC_MS`, in milliseconds); run `docker compose restart portal` to sync now instead of waiting. Its `portal-data` volume keeps portal records and activity. Incompatible old table shapes require an explicit migration.

  To roll back, stop the portal, restore every saved live input and the pre-upgrade release, then start the 0.8 app:

  ```sh
  docker compose stop portal
  curl -fsS -o /dev/null -X POST --data-binary @"pack.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"pack.lock.$CLIENT.before-@VERSION@" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.lock"
  curl -fsS -o /dev/null -X POST --data-binary @"identities.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=deploy%2F${CLIENT}%2Fidentities.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"work.$CLIENT.before-@VERSION@.tar.gz" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=work%2F${CLIENT}.tar.gz"
  docker compose run --rm job release HASH=$(cat "current.$CLIENT.before-@VERSION@")
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

- **On Google Cloud, the pilot.** Before anything, check that each model `pilot_env` names is available to the project in Vertex AI, and run `onboard-status` and record every listed unit. If the pilot keeps its app state in files (its `app_env` has no `APP_STATE_BACKEND = "bigquery"`) and a unit is listed, reset each listed unit while 0.7 is still deployed, as [BigQuery](#bigquery) says: records do not move from files to BigQuery. Then follow *Upgrading within a line* above, with the 0.7 kit as the installed one: check and load the new kit, push its image, save each client's digest, current release and data, deploy the new image to each client's job and service, and apply the new kit's Terraform. The apply is needed: review `terraform plan` first, which shows the service and the job taking `pilot_env`'s settings, less any your `app_env` already sets, since `app_env` wins setting by setting. Straight after the apply, run `onboard` for a fixture client, or the job's `seed` and then `onboard` for the generated sample (*The warehouses* below says why), then sign off and release its candidate as usual ([Set the client up and release](#set-the-client-up-and-release)), and upload and onboard again any unit you reset; until that `onboard` the service finds no tables in BigQuery and its answers cite no documents, and, its graph now in BigQuery (`GRAPH_STORE`), the service does not start until that candidate is released. To keep 0.7's configuration instead, give `pilot_env = {}` (and your `app_env` as before): then nothing but the image changes. To roll back, first, if the app state goes back to files with it (your `app_env` has no `APP_STATE_BACKEND = "bigquery"`) and `onboard-status` lists a unit, record and reset each listed unit while 0.8 still runs, its app state still in BigQuery, as [BigQuery](#bigquery) says for switching back; then return each client's job and service to its saved digest and apply the 0.7 kit's Terraform, which has no `pilot_env` (an `EMBEDDING_DIM` of yours in `app_env` must go or be `"128"` once the vectors are back in the image's own graph, as [BigQuery](#bigquery) says; removing it is enough here, since 0.7's Terraform supplies no `EMBEDDING_DIM` of its own), then run the job's `seed` and then its `onboard`, sign off and release its candidate, and upload and onboard again each unit you reset. The `seed` comes first because the work 0.8 saved holds no warehouse file, while 0.7 keeps its warehouses as files and its `onboard` does not build the estate's while the estate's summary is there. A release made with the graph in BigQuery holds no graph the 0.7 image can open, so its service does not start until the candidate is released.
- **The warehouses, wherever `WAREHOUSE_BACKEND` is `bigquery`.** The job now makes the warehouses in BigQuery itself, recorded in the dataset's `_kge_catalogue` table, rather than copying files it built ([BigQuery](#bigquery)). The tables 0.7 copied have no record there, and the first `onboard` drops them. So, straight after the apply, run `onboard` for a fixture client (it builds an absent or changed estate), or the job's `seed` then `onboard` for a generated sample. This builds the estate's warehouses in BigQuery and each onboarded unit's live version there from its uploaded files. A version only an older release reads is not built again, so rolling back to a release made before the upgrade needs that unit uploaded and onboarded again. A work or release saved by 0.7 that holds warehouse files is read without them. The apply also lets each client's service write its warehouses dataset (`roles/bigquery.dataEditor`, where it could only read it), since the page's onboarding steps now build a unit's tables there; review it in `terraform plan`.
- **The baseline, wherever `GRAPH_STORE` is `bigquery`.** The client's baseline was recorded on Ladybug, and the post-load check no longer puts a BigQuery run beside it: it stops, not checked, so no candidate is released while the profile names it. Before the first `onboard` on BigQuery, run `setup-client` with `BASELINE=` to clear it (omitting it keeps it), without changing people, so the check holds the candidate to the pack's own gates alone; then `onboard`, `sign-off` and `release`. That release records its baseline and updates the profile automatically; if recording fails, run the job's `baseline` ([BigQuery](#bigquery), *The pilot's own baseline*).
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

This kit upgrades the pilot alone; QA and PROD are not part of it. Use only the @VERSION@ kit while upgrading, with the same backend, tfvars and variable values used for the pilot's 0.5.0 apply. Start by naming the pilot's project and the newly unzipped kit; do not leave `KIT` pointing at the 0.5.0 folder:

```sh
export PROJECT=<the pilot's project id> REGION=<the pilot's region>
export BUNDLE=<the @VERSION@ kit folder holding images/ and kge-@VERSION@.zip>
export KIT=<the newly unzipped @VERSION@ folder>
IMAGE=$REGION-docker.pkg.dev/$PROJECT/kge/kge
STORE=gs://$PROJECT-kge-store
```

These are the example's names. A custom installation sets `IMAGE` and `STORE`, and uses its own job and service names wherever the steps below say `kge-<client>`, from its Terraform state, as [Custom resource names and settings](#custom-resource-names-and-settings) reads them.

1. Check the kit and load its new image tarballs as in step 1 of *Install on your machine*, including `$BUNDLE/images/kge-@VERSION@.tar`. Unzip `$BUNDLE/kge-@VERSION@.zip` into the new folder named by `KIT`; do not copy Terraform state or configuration from the old kit. Push the image, without yet updating any job:

   ```sh
   (cd "$BUNDLE" && sha256sum -c SHA256SUMS)
   docker load -i "$BUNDLE/images/kge-@VERSION@.tar"
   mkdir -p "$KIT" && unzip -o "$BUNDLE/kge-@VERSION@.zip" -d "$KIT"
   gcloud auth configure-docker $REGION-docker.pkg.dev
   docker tag kge:@VERSION@ $IMAGE:@VERSION@ && docker push $IMAGE:@VERSION@
   DIGEST=$(gcloud artifacts docker images describe $IMAGE:@VERSION@ --format='value(image_summary.digest)')
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
     gcloud storage cat "$STORE/releases/$CLIENT/current" > "current.$CLIENT.before-@VERSION@"
     gcloud storage cp "$STORE/clients/$CLIENT/pack.yaml" "pack.$CLIENT.before-@VERSION@.yaml"
     gcloud storage cp "$STORE/clients/$CLIENT/pack.lock" "pack.lock.$CLIENT.before-@VERSION@"
     gcloud storage cp "$STORE/deploy/$CLIENT/identities.yaml" "identities.$CLIENT.before-@VERSION@.yaml"
     gcloud storage cp "$STORE/work/$CLIENT.tar.gz" "work.$CLIENT.before-@VERSION@.tar.gz"
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

   A released 0.5.0 Meridian pack has no `business_team` or `knowledge_engineer` role. The example upload replaces the live Meridian pack, including any customisation; for a customised pack, add the new role to that pack and upload it instead. Before anyone onboards from the page, upload the pack and run `setup-client` without changing people. Then onboard, sign off, release and generate passwords. After release, grant the new people through the access table before generating passwords. Set `ACCESS_ADMIN` to an existing Access admin from the migration above:

   ```sh
   gcloud storage rsync -r "$KIT/examples/meridian" "$STORE"
   JOB=(gcloud run jobs execute kge-$CLIENT --region=$REGION --wait)
   "${JOB[@]}" --args="setup-client,CLIENT=$CLIENT,ASKER=<existing asker>,BASELINE=<existing baseline>"
   "${JOB[@]}" --args=onboard
   "${JOB[@]}" --args=sign-off,PRINCIPAL=steward_glossary.meridian@example.com
   "${JOB[@]}" --args=release
   "${JOB[@]}" --args="access,GRANT=business_team.$CLIENT@example.com:business_team,BY=$ACCESS_ADMIN"
   "${JOB[@]}" --args="access,GRANT=knowledge_engineer.$CLIENT@example.com:knowledge_engineer,BY=$ACCESS_ADMIN"
   "${JOB[@]}" --args=passwords
   ```

   Before this upgrade sequence reaches `passwords`, copy the active secret version back to the store as [Passwords and the service](#passwords-and-the-service) describes.

   Preserve the profile's asker role and baseline; `setup-client` does not change people. Grant the new business-team and engineering accounts through the access table after their roles exist in the served pack. Use the password helper above to merge the current hashes without rotating unchanged accounts.

The existing remote state, store objects, released graphs and password secrets otherwise carry over unchanged. To roll back, remove the added unit objects one object at a time, restore every saved lock, restore each pre-upgrade `current` release, and then return the job and service to that client's saved 0.5.0 digest. This uses the same per-object delete shape as the emulator rollback below:

```sh
for CLIENT in $CLIENTS; do
  for OBJECT in $(gcloud storage ls "$STORE/clients/$CLIENT/units/**"); do
    gcloud storage rm "$OBJECT"
  done
  gcloud storage cp "pack.$CLIENT.before-@VERSION@.yaml" "$STORE/clients/$CLIENT/pack.yaml"
  gcloud storage cp "pack.lock.$CLIENT.before-@VERSION@" "$STORE/clients/$CLIENT/pack.lock"
  gcloud storage cp "identities.$CLIENT.before-@VERSION@.yaml" "$STORE/deploy/$CLIENT/identities.yaml"
  gcloud storage cp "work.$CLIENT.before-@VERSION@.tar.gz" "$STORE/work/$CLIENT.tar.gz"
  OLD_DIGEST=$(cat "old-digest.$CLIENT")
  JOB=(gcloud run jobs execute kge-$CLIENT --region=$REGION --wait)
  "${JOB[@]}" --args="release,HASH=$(cat "current.$CLIENT.before-@VERSION@")"
  gcloud run jobs update kge-$CLIENT --region=$REGION --image=$IMAGE@$OLD_DIGEST
  gcloud run deploy kge-$CLIENT --region=$REGION --image=$IMAGE@$OLD_DIGEST
done
```

Then initialise the 0.5.0 kit's Terraform with the same backend command above, and run its `terraform plan "${VARS[@]}"` and `terraform apply "${VARS[@]}"`. The explicit `release` command above restores the release that was current before the upgrade; reverting the image and Terraform alone would not move that pointer.

### Other upgrade and rollback operations

- **On your machine.** Set `OLD_KIT` to the installed kit folder and `KIT` to the newly unzipped @VERSION@ folder. Both Compose files use project `kge`, so they address the same emulator volume. Back up the pack, lock, identities, work and current release before loading or starting the new image:

  ```sh
  export OLD_KIT=<absolute path to the installed kit folder>
  export BUNDLE=<absolute path to the @VERSION@ kit folder holding images/>
  export KIT=<absolute path to the newly unzipped @VERSION@ folder>
  cd "$OLD_KIT/compose" && . ./.env
  curl -fsS -o "$KIT/compose/pack.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.yaml?alt=media"
  curl -fsS -o "$KIT/compose/pack.lock.$CLIENT.before-@VERSION@" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/clients%2F${CLIENT}%2Fpack.lock?alt=media"
  curl -fsS -o "$KIT/compose/identities.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/deploy%2F${CLIENT}%2Fidentities.yaml?alt=media"
  curl -fsS -o "$KIT/compose/work.$CLIENT.before-@VERSION@.tar.gz" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/work%2F${CLIENT}.tar.gz?alt=media"
  curl -fsS -o "$KIT/compose/current.$CLIENT.before-@VERSION@" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/releases%2F${CLIENT}%2Fcurrent?alt=media"
  cp .env "$KIT/compose/.env"
  docker load -i "$BUNDLE/images/kge-@VERSION@.tar"
  cd "$KIT/compose" && . ./.env
  docker compose up -d gcs
  ```

  Follow the access migration above, preserving the saved identities as migration evidence. Preserve the profile's asker role and baseline. Update a customised pack in place; the sample upload replaces it. After the content is released, grant new people through the access table and generate their passwords. Set `ACCESS_ADMIN` to an existing Access admin. Start new app and portal containers from the new Compose file:

  ```sh
  docker compose run --rm job put-example
  docker compose run --rm job setup-client CLIENT=$CLIENT ASKER=<existing asker> BASELINE=<existing baseline>
  docker compose run --rm job onboard
  docker compose run --rm job sign-off PRINCIPAL=steward_glossary.meridian@example.com
  docker compose run --rm job release
  docker compose run --rm job access "GRANT=business_team.$CLIENT@example.com:business_team" BY="$ACCESS_ADMIN"
  docker compose run --rm job access "GRANT=knowledge_engineer.$CLIENT@example.com:knowledge_engineer" BY="$ACCESS_ADMIN"
  docker compose run --rm job passwords
  docker compose up -d app
  ```

  To roll back, still in the new kit's `compose/`, delete every added unit object individually, restore the saved pack, lock, identities and work, and restore the pre-upgrade release. Restoring work removes the unit's derived warehouse and golden set. The emulator has no recursive delete:

  ```sh
  curl -fsS "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o?prefix=clients%2F${CLIENT}%2Funits%2F" | python3 -c 'import json,sys,urllib.parse; [print(urllib.parse.quote(x["name"], safe="")) for x in json.load(sys.stdin).get("items", [])]' | while IFS= read -r OBJECT; do
    curl -fsS -o /dev/null -X DELETE "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/$OBJECT"
  done
  curl -fsS -o /dev/null -X POST --data-binary @"pack.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"pack.lock.$CLIENT.before-@VERSION@" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=clients%2F${CLIENT}%2Fpack.lock"
  curl -fsS -o /dev/null -X POST --data-binary @"identities.$CLIENT.before-@VERSION@.yaml" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=deploy%2F${CLIENT}%2Fidentities.yaml"
  curl -fsS -o /dev/null -X POST --data-binary @"work.$CLIENT.before-@VERSION@.tar.gz" "http://127.0.0.1:${GCS_PORT}/upload/storage/v1/b/kge-store/o?uploadType=media&name=work%2F${CLIENT}.tar.gz"
  docker compose run --rm job release HASH=$(cat "current.$CLIENT.before-@VERSION@")
  cd "$OLD_KIT/compose"
  docker compose up -d app
  ```

  This restores the pre-upgrade `current` release as well as the installed app image. Repeat the backup, upgrade and rollback commands for every client in the Compose store.
- **On Google Cloud.** Follow the section for the line you upgraded from, *Upgrading from 0.6* or *Upgrading from 0.5.0* above, for every client the pilot names. Each client has its own saved old digest; update and deploy both `kge-$CLIENT` runtimes with that digest to roll it back. Terraform never moves the image.
- **A release.** `release PREVIOUS=yes` restores the previous ledger identity, including the old warehouse version for a same-hash reload. Restart the service afterwards. `release HASH=<an earlier hash>` serves that hash again from the page's next start, with no new sign-off; `releases` lists them. A steward's decision made on the page is made on that page's own copy of the graph, never in the store: it is not carried into the next release or a rollback, nor into a new container or instance of the service (an upgrade on your machine; another or a replaced instance on Google Cloud); it is decided again there, or put in the pack.

## Troubleshooting

- **The page says "nothing released for meridian: release first".** The service starts and health answers, while data calls return 503. Older images said "nothing released for meridian: run release" and did not start. Run `onboard`, sign-off and release; use `seed` first only for the generated sample, then restart the service. If the profile is absent, run setup-client first.
- **A job command stops with "run setup-client".** The store has no profiles registry yet, or none for the client: run `setup-client` (step 4). A job command that exits 3 names the store's object and address it could not reach; check that `gcs` is running (`docker compose ps`).
- **`docker compose up -d gcs` tries to pull `fsouza/fake-gcs-server` and fails offline.** Some Docker engines drop an image's digest on `docker load`, and Compose then looks for it by digest in the registry. `docker images --digests fsouza/fake-gcs-server` shows `<none>` for its digest then. Where Docker Hub is reachable, `docker pull fsouza/fake-gcs-server:1.56.1@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef` fetches exactly that image; on a machine with no access, use one whose engine keeps it (Docker Engine 25 or later with the containerd image store).
- **A build stops with "failed to compute cache key: short read".** The registry served an empty layer of the base: use another base, or report it to the registry's owner.
- **A port is taken.** Set `KGE_PORT` or `GCS_PORT` in `.env`, run `. ./.env` again, and `docker compose up -d` again; the page is then at that port.
- **Sign-in is refused.** A password works for the person `passwords.txt` names it for. `passwords` keeps unchanged hashes; `ROTATE=all` deliberately replaces them all: on your machine, download and delete `passwords.txt` as in step 7, then `docker compose restart app`, which reads the new hashes; on Google Cloud, run `kge passwords`; the live-reload pointer avoids a new revision.
- **Sign-in answers 429, "too many sign-in attempts".** Failed sign-ins are counted per address and principal pair. Five failures in 15 minutes throttle further failures for five minutes; `Retry-After` gives the wait. Wait for the lockout to expire even with a correct password; a successful sign-in then clears that pair’s failures. Another principal behind the same address is unaffected.
- **A second release does not show.** After a page/API release or rollback, keep the onboarding page open or run `kge wait`: Compose and Cloud Run follow the completed job on the status poll. After a direct operator release, use `docker compose restart app`, or a new revision on Google Cloud, which restarts every instance.

## Documents and the reader

Documents passed in must contain no personal or sensitive data. Ensuring this is the client's responsibility,
before uploading any document. The model reader reads every supported file. Certification only attests the
converted document, exactly as it attests a Markdown document.

Put documents on the `certified` or `uncertified` shelf. Markdown keeps its inline front matter. For every
other file, put the same front matter in a small YAML file beside it, named `<filename>.yaml` (including the
original extension), without `---` delimiters. Keep document ids (the filename without its extension) unique
across both shelves. If `document_order` is used, it lists the original document paths, excluding sidecars.

The selected reader declares the supported formats. Supported content becomes Markdown before the existing
certify, split, tag, embed, review and release steps. The kept Markdown marks each page's start and whether
it was read from the text layer or by the model; tables remain tables and pictures and charts have descriptions.
An unsupported file is retained as supplied with its metadata and no passages. Search can find its title and
front matter; Ask and Context cannot cite it. Certification records `content not read: <format> isn't supported
by <reader>`, and the onboarding report lists it.

A reading is kept by content hash in the client's store, so unchanged files reuse it across onboards, even
when another fixture input changes. Choosing another reader deliberately rereads the files and rebuilds the
candidate. On the pilot, a failed model reading stops onboarding; it never silently uses text extraction.
